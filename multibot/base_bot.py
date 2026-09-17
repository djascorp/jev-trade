#!/usr/bin/env python3
"""
Base trading bot — shared infrastructure for all strategies.
Each bot has its own DerivAPI connection and runs independently.
"""
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_DIR, '.env'))

from core.deriv_api import DerivAPI

try:
    from loguru import logger as loguru_logger
except ImportError:
    import logging
    loguru_logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Represents a single trade position."""
    bot_name: str
    symbol: str
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    sl_price: float
    tp_price: float
    stake: float
    contract_id: Optional[int] = None
    open_time: float = field(default_factory=time.time)
    close_time: Optional[float] = None
    exit_price: Optional[float] = None
    result: Optional[str] = None    # "WIN", "LOSS", "CLOSED"
    pnl: float = 0.0
    reason: str = ""                # Why trade was opened
    close_reason: str = ""          # Why trade was closed


class BaseBot:
    """Base class for all trading bots."""
    
    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.symbol = config["symbol"]
        self.stake = config.get("stake", 4.0)
        self.multiplier = config.get("multiplier", 100)
        self.max_positions = config.get("max_positions", 2)
        self.granularity = config.get("granularity", 300)  # M5 default
        self.entry_delay = config.get("entry_delay_seconds", 0)  # delay before entering trade
        
        # Session filter: only trade during good hours, skip bad hours (UTC)
        self.good_hours = config.get("good_hours", None)  # list of UTC hours, or None = all
        self.bad_hours = config.get("bad_hours", None)   # list of UTC hours to skip, or None
        
        # State
        self.candles: list[dict] = []
        self.current_price: float = 0.0
        self.active_trades: list[Trade] = []
        self.closed_trades: list[Trade] = []
        self.signals_today: int = 0
        self.trades_today: int = 0
        self.skipped_today: int = 0
        self.total_pnl: float = 0.0
        self.running: bool = False
        self._trade_lock = asyncio.Lock()  # Prevent concurrent trade openings
        self._last_signal_epoch: int = 0   # Prevent duplicate signals on same candle
        
        # API
        self.api: Optional[DerivAPI] = None
        self._reconnect_count = 0
        
        # Logging
        self.log_dir = os.path.join(PROJECT_DIR, 'multibot', 'logs')
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file = os.path.join(self.log_dir, f'{name}.log')
        
        # Stats
        self.start_time: Optional[float] = None
        self.last_signal_time: Optional[float] = None
        self.last_trade_time: Optional[float] = None
    
    def log(self, msg: str, level: str = "INFO"):
        """Log to both stdout and file."""
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"{ts} | [{self.name.upper()}] {msg}"
        print(line, flush=True)
        try:
            with open(self.log_file, 'a') as f:
                f.write(line + '\n')
        except:
            pass
    
    def status_line(self) -> str:
        """Return a compact one-line status for monitoring."""
        wins = sum(1 for t in self.closed_trades if t.result == "WIN")
        losses = sum(1 for t in self.closed_trades if t.result == "LOSS")
        active = len(self.active_trades)
        last_price = f"{self.current_price:.2f}" if self.current_price else "N/A"
        return (
            f"[{self.name.upper()}] {self.symbol} "
            f"price={last_price} "
            f"active={active} "
            f"trades={self.trades_today}({wins}W/{losses}L) "
            f"pnl={self.total_pnl:+.2f} "
            f"signals={self.signals_today} skipped={self.skipped_today}"
        )
    
    async def connect(self):
        """Connect to Deriv API via PAT flow."""
        pat = os.getenv('DERIV_PAT_TOKEN', '')
        app_id = os.getenv('DERIV_PAT_APP_ID', '')
        account = os.getenv('DERIV_PAT_ACCOUNT_ID', '')
        
        self.api = DerivAPI(
            ws_url='', api_token='',
            pat_token=pat, app_id=app_id, account_id=account
        )
        await self.api.connect()
        
        # Get balance
        try:
            bal = await self.api.get_balance()
            balance = bal.get('balance', {}).get('balance', 0)
            currency = bal.get('balance', {}).get('currency', 'USD')
            self.log(f"Connected | Balance: {balance} {currency} | Stake: ${self.stake}")
        except Exception as e:
            self.log(f"Connected (balance check failed: {e})")
    
    async def warmup(self, count: int = 200):
        """Load historical candles for warm-up."""
        try:
            candles = await self.api.get_candles(self.symbol, count=count, granularity=self.granularity)
            self.candles = candles
            self.current_price = float(candles[-1]['close']) if candles else 0
            self.log(f"Warm-up: {len(candles)} candles loaded | Last: {self.current_price:.2f}")
        except Exception as e:
            self.log(f"Warm-up failed: {e}", "ERROR")
    
    async def execute_signal(self, direction: str, entry_price: float,
                             sl_price: float, tp_price: float, reason: str = ""):
        """
        Execute a trade signal, optionally with entry delay and session filter.
        Prevents duplicate signals on the same candle via _last_signal_epoch.
        """
        # Direction filter (config: "allowed_directions": ["CALL"] / ["PUT"] / ["LONG"] / ["SHORT"])
        allowed = self.config.get("allowed_directions")
        if allowed:
            norm = {"LONG": "CALL", "SHORT": "PUT"}
            dir_n = norm.get(direction, direction).upper()
            allowed_n = {norm.get(a, a).upper() for a in allowed}
            if dir_n not in allowed_n:
                self.log(f"SKIP {direction} | direction not in allowed_directions={sorted(allowed_n)}")
                return None
        # Deduplicate: one signal per candle close + cooldown between trades
        current_epoch = int(self.candles[-1]['epoch']) if self.candles else 0
        if current_epoch == self._last_signal_epoch:
            return None  # Already processed this candle
        # Cooldown: don't open new trade if last one was < contract_duration ago
        if self.last_trade_time:
            cooldown = self.config.get("contract_duration", 5) * 60  # seconds
            elapsed = time.time() - self.last_trade_time
            if elapsed < cooldown:
                remaining = int(cooldown - elapsed)
                self.log(f"SKIP: Cooldown ({remaining}s remaining until next trade allowed)")
                self.skipped_today += 1
                return None
        self._last_signal_epoch = current_epoch

        # Session filter check
        if self.good_hours or self.bad_hours:
            from datetime import datetime, timezone
            hour = datetime.now(timezone.utc).hour
            if self.bad_hours and hour in self.bad_hours:
                self.log(f"SKIP: Bad trading hour {hour:02d}:00 UTC")
                self.skipped_today += 1
                return None
            if self.good_hours and hour not in self.good_hours:
                self.log(f"SKIP: Outside good hours ({hour:02d}:00 UTC not in {self.good_hours})")
                self.skipped_today += 1
                return None

        if self.entry_delay <= 0:
            return await self.open_trade(direction, self.current_price, sl_price, tp_price, reason)

        delay = self.entry_delay
        self.log(f"Signal queued — entering in {delay}s | {reason}")

        async def _delayed():
            await asyncio.sleep(delay)
            if not self.running:
                return
            if len(self.active_trades) >= self.max_positions:
                self.log(f"SKIP after {delay}s delay: max positions reached")
                return
            await self.open_trade(direction, self.current_price, sl_price, tp_price, reason)

        asyncio.create_task(_delayed())

    async def open_trade(self, direction: str, entry_price: float, sl_price: float,
                         tp_price: float, reason: str = "") -> Optional[Trade]:
        """Open a trade on Deriv. Lock prevents duplicate concurrent opens."""
        async with self._trade_lock:
            if len(self.active_trades) >= self.max_positions:
                self.skipped_today += 1
                self.log(f"SKIP: Max positions ({self.max_positions}) reached")
                return None
            
            # Check market open (skip for synthetic indices — they're 24/7)
            synthetic_symbols = ['R_10', 'R_25', 'R_50', 'R_75', 'R_100',
                                'BOOM500', 'BOOM1000', 'CRASH500', 'CRASH1000',
                                'BOOM300N', 'CRASH300N',
                                'JD10', 'JD25', 'JD50', 'JD100',
                                'stpRNG', 'stpRNG5', 'stpRNG10', 'stpRNG20', 'stpRNG30',
                                'stpRNG50', 'stpRNG100']
            if self.symbol not in synthetic_symbols:
                try:
                    market_open = await self.api.is_market_open(self.symbol)
                    if not market_open:
                        self.log(f"SKIP: Market {self.symbol} is closed")
                        return None
                except Exception as e:
                    self.log(f"SKIP: Market check failed: {e}")
                    return None
            
            use_binary = self.config.get("contract_mode", "multiplier") == "binary"
            contract_duration = self.config.get("contract_duration", 5)
            contract_duration_unit = self.config.get("contract_duration_unit", "m")
            
            if use_binary:
                contract_type = "CALL" if direction == "LONG" else "PUT"
            else:
                contract_type = "MULTUP" if direction == "LONG" else "MULTDOWN"
            
            trade = Trade(
                bot_name=self.name,
                symbol=self.symbol,
                direction=direction,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
                stake=self.stake,
                reason=reason,
            )
            
            try:
                # Step 1: Proposal
                proposal_req = {
                    "proposal": 1,
                    "amount": self.stake,
                    "basis": "stake",
                    "contract_type": contract_type,
                    "currency": "USD",
                }
                
                if use_binary:
                    proposal_req["underlying_symbol"] = self.symbol
                    proposal_req["duration"] = contract_duration
                    proposal_req["duration_unit"] = contract_duration_unit
                else:
                    proposal_req["underlying_symbol"] = self.symbol
                    proposal_req["multiplier"] = self.multiplier
                    sl_dist_pct = abs(entry_price - sl_price) / entry_price if entry_price > 0 else 0
                    sl_dollars = max(abs(sl_dist_pct * self.multiplier * self.stake), 0.50)
                    tp_dist_pct = abs(tp_price - entry_price) / entry_price if entry_price > 0 else 0
                    tp_dollars = tp_dist_pct * self.multiplier * self.stake
                    limit_order = {"stop_loss": round(sl_dollars, 2)}
                    if tp_dollars > 0:
                        limit_order["take_profit"] = round(tp_dollars, 2)
                    proposal_req["limit_order"] = limit_order
                
                proposal_resp = await self.api.send(proposal_req)
                if "error" in proposal_resp:
                    self.log(f"Proposal failed: {proposal_resp['error']['message']}", "ERROR")
                    return None
                
                proposal_id = proposal_resp.get("proposal", {}).get("id")
                if not proposal_id:
                    self.log(f"No proposal ID returned", "ERROR")
                    return None
                
                # Step 2: Buy
                buy_resp = await self.api.send({
                    "buy": proposal_id,
                    "price": self.stake,
                })
                if "error" in buy_resp:
                    self.log(f"Buy failed: {buy_resp['error']['message']}", "ERROR")
                    return None
                
                contract_id = buy_resp.get("buy", {}).get("contract_id")
                trade.contract_id = contract_id
                
                self.active_trades.append(trade)
                self.trades_today += 1
                self.last_trade_time = time.time()
                
                self.log(
                    f"TRADE OPENED | {direction} {self.symbol} "
                    f"entry={entry_price:.4f} "
                    f"contract={contract_type}({contract_duration}{contract_duration_unit}) "
                    f"id={contract_id} | {reason}"
                )
                return trade
                
            except Exception as e:
                self.log(f"Trade execution failed: {e}", "ERROR")
                return None
    
    async def close_trade(self, trade: Trade, reason: str = "manual"):
        """Close a trade by selling the contract."""
        if not trade.contract_id:
            return
        
        try:
            resp = await self.api.send({
                "sell": trade.contract_id,
                "price": 0,  # market sell
            })
            if "error" in resp:
                self.log(f"Sell failed: {resp['error']['message']}", "ERROR")
                return
            
            sell_price = resp.get("sell", {}).get("sold_for", 0)
            pnl = sell_price - self.stake
            trade.pnl = pnl
            trade.close_time = time.time()
            trade.close_reason = reason
            trade.result = "WIN" if pnl > 0 else "LOSS"
            
            self.total_pnl += pnl
            self.active_trades.remove(trade)
            self.closed_trades.append(trade)
            
            self.log(
                f"TRADE CLOSED | {trade.direction} {self.symbol} "
                f"PnL={pnl:+.2f} ({trade.result}) | {reason} | "
                f"Total PnL: {self.total_pnl:+.2f}"
            )
        except Exception as e:
            self.log(f"Close trade failed: {e}", "ERROR")
    
    async def check_positions(self):
        """Check active positions.
        For BINARY contracts (CALL/PUT): just check if expired (won/lost).
        For MULTIPLIER contracts: monitor SL/TP."""
        for trade in list(self.active_trades):
            if not trade.contract_id:
                continue
            
            try:
                resp = await self.api.send({
                    "proposal_open_contract": 1,
                    "contract_id": trade.contract_id,
                })
                if "error" in resp:
                    continue
                
                poc = resp.get("proposal_open_contract", {})
                
                # Check if contract has expired/been sold
                if poc.get("is_sold") == 1 or poc.get("status") in ("won", "lost", "sold"):
                    sell_price = float(poc.get("sell_price", 0))
                    pnl = sell_price - self.stake
                    trade.pnl = pnl
                    trade.close_time = time.time()
                    trade.exit_price = float(poc.get("exit_spot", 0))
                    trade.result = "WIN" if poc.get("status") == "won" else "LOSS"
                    trade.close_reason = f"contract {poc.get('status', 'expired')}"
                    
                    self.total_pnl += pnl
                    if trade in self.active_trades:
                        self.active_trades.remove(trade)
                    self.closed_trades.append(trade)
                    
                    self.log(
                        f"TRADE CLOSED | {trade.direction} {self.symbol} "
                        f"PnL={pnl:+.2f} ({trade.result}) | {trade.close_reason} | "
                        f"Total PnL: {self.total_pnl:+.2f}"
                    )
                    continue
                
                # Update current price from contract data
                if poc.get("current_spot"):
                    self.current_price = float(poc["current_spot"])
                
                # SL/TP monitoring ONLY for multiplier contracts (not binary)
                use_binary = self.config.get("contract_mode", "multiplier") == "binary"
                if use_binary:
                    continue  # Binary contracts: just wait for expiry
                
                current = self.current_price
                if current and poc.get("status") == "open":
                    hit_sl = False
                    hit_tp = False
                    
                    if trade.direction == "LONG":
                        if current <= trade.sl_price:
                            hit_sl = True
                        elif current >= trade.tp_price:
                            hit_tp = True
                    else:
                        if current >= trade.sl_price:
                            hit_sl = True
                        elif current <= trade.tp_price:
                            hit_tp = True
                    
                    if hit_sl:
                        await self.close_trade(trade, f"SL hit @ {current:.4f}")
                    elif hit_tp:
                        await self.close_trade(trade, f"TP hit @ {current:.4f}")
                    elif trade.open_time and (time.time() - trade.open_time) > 3600:
                        await self.close_trade(trade, "timeout (1h)")
                        
            except Exception as e:
                # Fallback: only SL/TP for non-binary
                use_binary = self.config.get("contract_mode", "multiplier") == "binary"
                if use_binary:
                    continue
                if self.current_price:
                    hit_sl = False
                    hit_tp = False
                    if trade.direction == "LONG":
                        if self.current_price <= trade.sl_price:
                            hit_sl = True
                        elif self.current_price >= trade.tp_price:
                            hit_tp = True
                    else:
                        if self.current_price >= trade.sl_price:
                            hit_sl = True
                        elif self.current_price <= trade.tp_price:
                            hit_tp = True
                    if hit_sl:
                        await self.close_trade(trade, f"SL hit @ {self.current_price:.4f}")
                    elif hit_tp:
                        await self.close_trade(trade, f"TP hit @ {self.current_price:.4f}")
    
    async def _on_candle(self, data: dict):
        """Called when a new candle update arrives."""
        ohlc = data.get("ohlc", {})
        if not ohlc:
            return
        
        candle = {
            'epoch': int(ohlc.get('open_time', 0)),
            'open': float(ohlc.get('open', 0)),
            'high': float(ohlc.get('high', 0)),
            'low': float(ohlc.get('low', 0)),
            'close': float(ohlc.get('close', 0)),
        }
        
        # Update candle buffer
        if self.candles:
            last = self.candles[-1]
            if last.get('epoch') == candle['epoch']:
                self.candles[-1] = candle  # update current
            else:
                self.candles.append(candle)  # new candle
                if len(self.candles) > 1000:
                    self.candles.pop(0)
        
        self.current_price = candle['close']
        
        # Check positions on every tick
        await self.check_positions()
        
        # Strategy evaluation (only on candle close, not intra-candle)
        if self.candles and self.candles[-1]['epoch'] == candle['epoch']:
            # This is an update of the current candle, check if it's a close
            pass
    
    async def _on_candle_close(self, candle: dict):
        """Called when a new candle closes. Override in subclass."""
        self.current_price = candle['close']
        await self.check_positions()
        # Subclass implements strategy logic here
    
    async def subscribe_data(self):
        """Subscribe to live candle data."""
        # Set up candle handler that detects candle closes
        last_epoch = None
        
        async def candle_handler(data: dict):
            nonlocal last_epoch
            ohlc = data.get("ohlc", {})
            if not ohlc:
                return
            
            epoch = int(ohlc.get('open_time', 0))
            candle = {
                'epoch': epoch,
                'open': float(ohlc.get('open', 0)),
                'high': float(ohlc.get('high', 0)),
                'low': float(ohlc.get('low', 0)),
                'close': float(ohlc.get('close', 0)),
            }
            
            self.current_price = candle['close']
            
            # Update buffer
            if self.candles and self.candles[-1]['epoch'] == epoch:
                self.candles[-1] = candle
            elif self.candles and self.candles[-1]['epoch'] < epoch:
                # New candle — old one closed
                closed_candle = self.candles[-1]
                self.candles.append(candle)
                if len(self.candles) > 1000:
                    self.candles.pop(0)
                # Call strategy on candle close
                await self._on_candle_close(closed_candle)
            else:
                self.candles.append(candle)
            
            # Check positions on every update
            await self.check_positions()
        
        self.api.on_subscription("ohlc", candle_handler)
        await self.api.subscribe_candles(self.symbol, self.granularity, candle_handler)
    
    async def _status_loop(self):
        """Periodically log status."""
        while self.running:
            await asyncio.sleep(120)  # Every 2 minutes
            self.log(self.status_line())
    
    async def run(self):
        """Main bot loop. Override _on_candle_close in subclass.
        Handles forex market closed gracefully — retries every 5 min.
        Reconnects WebSocket on each retry to prevent stale connections."""
        self.running = True
        self.start_time = time.time()
        
        self.log(f"=== {self.name.upper()} STARTING ===")
        self.log(f"Symbol: {self.symbol} | Stake: ${self.stake} | Mult: {self.multiplier}x | Gran: {self.granularity}s")
        
        # Retry connect + warmup + subscription until success (handles market closed)
        max_init_retries = 200  # ~16+ hours max at 5-min intervals
        for attempt in range(1, max_init_retries + 1):
            if not self.running:
                return
            try:
                self.log(f"Init attempt {attempt}/{max_init_retries}...")
                
                # Reconnect if needed (WS dies during 5-min sleep)
                if not self.api or not self.api.is_connected:
                    await self.connect()
                
                await self.warmup(200)
                if not self.candles:
                    raise RuntimeError("No candles returned (market may be closed)")
                await self.subscribe_data()
                self.log(f"Bot ready — waiting for signals...")
                break
            except Exception as e:
                self.log(f"Init failed (attempt {attempt}): {e}", "WARN")
                # Disconnect stale connection
                if self.api:
                    try:
                        await self.api.disconnect()
                    except:
                        pass
                    self.api = None
                if attempt < max_init_retries:
                    self.log(f"Retrying in 5 minutes...")
                    await asyncio.sleep(300)
                else:
                    self.log(f"Max init retries reached — giving up", "ERROR")
                    return
        
        # Start status loop
        asyncio.create_task(self._status_loop())
        
        # Keep running
        while self.running:
            await asyncio.sleep(1)
    
    async def stop(self):
        """Stop the bot gracefully."""
        self.running = False
        self.log("Stopping...")
        # Close all active trades
        for trade in list(self.active_trades):
            await self.close_trade(trade, "bot shutdown")
        if self.api:
            await self.api.disconnect()
        self.log("Stopped.")
