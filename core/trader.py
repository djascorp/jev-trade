"""Trade execution and position management with custom SL/TP monitoring."""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from core.deriv_api import DerivAPI
from core.logger import get_logger
from core.strategy import TradeSignal, SignalType

logger = get_logger("trader")


class PositionStatus(Enum):
    OPEN = "OPEN"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_EXPIRY = "CLOSED_EXPIRY"
    CLOSED_ERROR = "CLOSED_ERROR"


@dataclass
class Position:
    contract_id: Optional[int] = None
    signal: Optional[TradeSignal] = None
    stake_amount: float = 0.0
    buy_price: float = 0.0
    payout: float = 0.0
    status: PositionStatus = PositionStatus.OPEN
    open_time: float = field(default_factory=time.time)
    close_time: Optional[float] = None
    profit: float = 0.0
    current_price: float = 0.0
    # Effective SL/TP used for monitoring (may differ from signal in binary mode)
    effective_sl_price: Optional[float] = None
    effective_tp_price: Optional[float] = None


class Trader:
    """Handles trade execution and custom SL/TP monitoring on Deriv.
    
    For multiplier contracts, Deriv limit_order uses cent amounts for stop_loss/take_profit.
    The monitoring loop also checks SL/TP via price as a fallback.
    """

    def __init__(self, api: DerivAPI, stake_amount: float = 1.0, currency: str = "USD",
                 contract_duration: int = 5, contract_duration_unit: str = "m",
                 contract_mode: str = "binary", multiplier_leverage: int = 100,
                 max_positions: int = 7, symbol: str = None,
                 binary_sl_multiplier: float = 1.0, binary_tp_multiplier: float = 1.0):
        self.api = api
        self.stake_amount = stake_amount
        self.currency = currency
        self.contract_duration = contract_duration
        self.contract_duration_unit = contract_duration_unit
        self.contract_mode = contract_mode  # "binary" or "multiplier"
        self.multiplier_leverage = multiplier_leverage
        self.max_positions = max_positions
        self.symbol = symbol
        self.binary_sl_multiplier = binary_sl_multiplier
        self.binary_tp_multiplier = binary_tp_multiplier
        self.active_positions: list[Position] = []
        self.closed_positions: list[Position] = []
        self._monitor_tasks: dict[int, asyncio.Task] = {}
        self.on_position_closed = None

    def _price_to_dollars(self, entry_price: float, target_price: float, direction: str) -> float:
        """Convert a price target to Deriv dollar amount for limit_order.

        For Deriv multiplier contracts, limit_order stop_loss/take_profit
        are in dollars (cannot exceed stake amount for stop_loss).
        Formula: |target_price - entry_price| / entry_price * multiplier * stake
        
        Args:
            entry_price: The entry price of the trade
            target_price: The SL or TP price level
            direction: "LONG" or "SHORT"
            
        Returns:
            float: Amount in dollars
        """
        if entry_price <= 0:
            return 0.0
        
        # Calculate price difference
        price_diff = target_price - entry_price
        
        # For LONG: profit when price goes up, loss when price goes down
        # For SHORT: profit when price goes down, loss when price goes up
        if direction == "LONG":
            pl_ratio = price_diff / entry_price
        else:
            pl_ratio = -price_diff / entry_price
        
        # P/L in dollars = pl_ratio * multiplier * stake
        pl_amount = pl_ratio * self.multiplier_leverage * self.stake_amount
        return round(pl_amount, 2)

    def _compute_binary_sl_tp(self, signal: TradeSignal) -> tuple[float, float]:
        """Compute effective SL/TP for binary mode using dedicated multipliers.
        
        Returns the adjusted (sl_price, tp_price) to use for monitoring.
        The original signal is NOT mutated - this allows the bot's trailing stop
        to work on strategy-level prices without conflicting with binary adjustments.
        """
        sl = signal.sl_price
        tp = signal.tp_price

        if self.binary_sl_multiplier != 1.0 or self.binary_tp_multiplier != 1.0:
            entry = signal.entry_price
            sl_dist = abs(entry - signal.sl_price)

            if sl_dist > 0:
                new_sl_dist = sl_dist * self.binary_sl_multiplier
                tp_dist = abs(signal.tp_price - entry)
                new_tp_dist = tp_dist * self.binary_tp_multiplier

                if signal.signal_type == SignalType.LONG:
                    sl = entry - new_sl_dist
                    tp = entry + new_tp_dist
                else:
                    sl = entry + new_sl_dist
                    tp = entry - new_tp_dist

                logger.info(
                    f"Binary SL/TP adjusted | SL={signal.sl_price:.5f}->{sl:.5f} "
                    f"(x{self.binary_sl_multiplier}) TP={signal.tp_price:.5f}->{tp:.5f} "
                    f"(x{self.binary_tp_multiplier})"
                )

        return sl, tp

    async def execute_signal(self, signal: TradeSignal) -> Optional[Position]:
        """Execute a trade based on a strategy signal."""
        if not self._can_open_more():
            logger.warning(f"Max positions ({self.max_positions}) reached, skipping signal")
            return None

        # CRITICAL: Check if market is open before opening any trade
        symbol = self._get_deriv_symbol()
        try:
            market_open = await self.api.is_market_open(symbol)
            if not market_open:
                logger.warning(f"Market {symbol} is CLOSED - skipping trade signal")
                return None
        except Exception as e:
            logger.error(f"Error checking market status: {e} - skipping trade signal for safety")
            return None

        if self.contract_mode == "multiplier":
            contract_type = "MULTUP" if signal.signal_type == SignalType.LONG else "MULTDOWN"
        else:
            contract_type = "CALL" if signal.signal_type == SignalType.LONG else "PUT"

        position = Position(signal=signal, stake_amount=self.stake_amount)

        # In binary mode, compute effective SL/TP with binary multipliers for monitoring
        if self.contract_mode == "binary":
            eff_sl, eff_tp = self._compute_binary_sl_tp(signal)
            position.effective_sl_price = eff_sl
            position.effective_tp_price = eff_tp
        self.active_positions.append(position)

        try:
            # Step 1: Get proposal
            proposal_req = {
                "proposal": 1,
                "amount": self.stake_amount,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": self.currency,
                "symbol": self._get_deriv_symbol(),
            }

            if self.contract_mode == "multiplier":
                proposal_req["multiplier"] = self.multiplier_leverage
                # Set SL/TP via limit_order for multiplier contracts
                # Deriv expects dollar amounts for stop_loss/take_profit
                limit_order = {}
                if signal.sl_price and signal.entry_price:
                    sl_dollars = self._price_to_dollars(
                        signal.entry_price, signal.sl_price,
                        signal.signal_type.value
                    )
                    # stop_loss must be positive (max loss amount in dollars)
                    sl_dollars = abs(sl_dollars)
                    # Enforce Deriv minimum: stop_loss cannot be below $0.35
                    if sl_dollars < 0.35:
                        sl_dollars = 0.35
                        logger.warning(
                            f"SL too small (${abs(sl_dollars):.4f}), enforcing minimum $0.35"
                        )
                    limit_order["stop_loss"] = sl_dollars
                if signal.tp_price and signal.entry_price:
                    tp_dollars = self._price_to_dollars(
                        signal.entry_price, signal.tp_price,
                        signal.signal_type.value
                    )
                    # take_profit must be positive (target profit amount)
                    limit_order["take_profit"] = abs(tp_dollars)
                if limit_order:
                    proposal_req["limit_order"] = limit_order
                    logger.info(
                        f"SL/TP in $ | stop_loss={limit_order['stop_loss']:.2f} "
                        f"take_profit={limit_order['take_profit']:.2f} "
                        f"(from SL price={signal.sl_price:.5f} TP price={signal.tp_price:.5f})"
                    )
            else:
                proposal_req["duration"] = self.contract_duration
                proposal_req["duration_unit"] = self.contract_duration_unit

            proposal = await self.api.send(proposal_req)

            if "error" in proposal:
                logger.error(f"Proposal error: {proposal['error']['message']}")
                position.status = PositionStatus.CLOSED_ERROR
                self._close_position(position)
                return None

            proposal_id = proposal.get("proposal", {}).get("id")
            ask_price = proposal.get("proposal", {}).get("ask_price", self.stake_amount)
            payout = proposal.get("proposal", {}).get("payout", 0)

            if not proposal_id:
                logger.error("Proposal returned no ID - cannot execute buy")
                position.status = PositionStatus.CLOSED_ERROR
                self._close_position(position)
                return None

            mode_label = f"{self.multiplier_leverage}x" if self.contract_mode == "multiplier" else "BINARY"
            logger.info(
                f"Proposal received | Mode={mode_label} Type={contract_type} Ask={ask_price} "
                f"Payout={payout} | SL={signal.sl_price:.5f} TP={signal.tp_price:.5f}"
            )

            # Step 2: Buy contract
            buy_req = {
                "buy": proposal_id,
                "price": ask_price,  # Use actual ask price instead of stake * 5
            }
            buy_response = await self.api.send(buy_req)

            if "error" in buy_response:
                logger.error(f"Buy error: {buy_response['error']['message']}")
                position.status = PositionStatus.CLOSED_ERROR
                self._close_position(position)
                return None

            buy_data = buy_response.get("buy", {})
            position.contract_id = buy_data.get("contract_id")
            position.buy_price = buy_data.get("buy_price", self.stake_amount)
            position.payout = buy_data.get("payout", payout)

            logger.info(
                f"Contract opened [{mode_label}] | ID={position.contract_id} "
                f"BuyPrice={position.buy_price} Payout={position.payout}"
            )

            # Step 3: Start SL/TP monitor
            task = asyncio.create_task(self._monitor_position(position))
            self._monitor_tasks[position.contract_id] = task

            return position

        except Exception as e:
            logger.error(f"Trade execution error: {e}")
            position.status = PositionStatus.CLOSED_ERROR
            self._close_position(position)
            return None

    async def _monitor_position(self, position: Position):
        """Monitor an open position for SL/TP hit.
        
        For multiplier contracts, Deriv handles SL/TP server-side via limit_order.
        This monitoring loop serves as a fallback and logs price updates.
        For binary contracts, SL/TP is checked manually via current_spot.
        """
        if not position.contract_id or not position.signal:
            return

        signal = position.signal
        contract_id = position.contract_id
        check_interval = 1.0
        max_consecutive_errors = 10

        # Use effective SL/TP (binary-adjusted) if available, fallback to signal
        mon_sl = position.effective_sl_price if position.effective_sl_price is not None else signal.sl_price
        mon_tp = position.effective_tp_price if position.effective_tp_price is not None else signal.tp_price
        mon_sl_label = f"{mon_sl:.5f}" if mon_sl is not None else "N/A"
        mon_tp_label = f"{mon_tp:.5f}" if mon_tp is not None else "N/A"

        logger.info(
            f"Monitoring position {contract_id} | "
            f"SL={mon_sl_label} TP={mon_tp_label}"
        )

        # Wait a moment for the contract to propagate in Deriv's system
        await asyncio.sleep(0.5)

        try:
            consecutive_errors = 0
            while position.status == PositionStatus.OPEN:
                try:
                    poc = await self.api.send({
                        "proposal_open_contract": 1,
                        "contract_id": contract_id,
                    })

                    if "error" in poc:
                        consecutive_errors += 1
                        err_msg = poc.get("error", {}).get("message", "unknown")
                        err_code = poc.get("error", {}).get("code", "unknown")
                        if consecutive_errors >= max_consecutive_errors:
                            logger.warning(
                                f"Contract {contract_id} not found after {max_consecutive_errors} attempts "
                                f"(last error: {err_code}: {err_msg}), likely expired"
                            )
                            position.status = PositionStatus.CLOSED_EXPIRY
                            break
                        logger.debug(
                            f"Contract {contract_id} POC error: {err_code}: {err_msg} "
                            f"(attempt {consecutive_errors}/{max_consecutive_errors}), retrying..."
                        )
                        await asyncio.sleep(1)
                        continue

                    # Reset error counter on success
                    consecutive_errors = 0

                    contract = poc.get("proposal_open_contract", {})

                    # Check if Deriv closed the contract (SL/TP hit server-side for multiplier)
                    if contract.get("is_sold") or contract.get("is_expired"):
                        raw_profit = contract.get("profit")
                        position.profit = float(raw_profit) if raw_profit is not None else 0.0
                        position.close_time = time.time()
                        
                        # Determine close reason from contract status
                        if contract.get("is_sold"):
                            # Sold = SL or TP hit by Deriv server-side
                            if position.profit < 0:
                                position.status = PositionStatus.CLOSED_SL
                            elif position.profit > 0:
                                position.status = PositionStatus.CLOSED_TP
                            else:
                                position.status = PositionStatus.CLOSED_EXPIRY
                        else:
                            position.status = PositionStatus.CLOSED_EXPIRY
                        
                        reason = position.status.value
                        logger.info(
                            f"Contract {contract_id} closed by Deriv | "
                            f"Reason={reason} Profit={position.profit:.2f}"
                        )
                        break

                    current_spot_raw = contract.get("current_spot")
                    if current_spot_raw is None:
                        logger.debug(f"Contract {contract_id} has no current_spot yet, skipping check")
                        await asyncio.sleep(check_interval)
                        continue

                    current_spot = float(current_spot_raw)
                    position.current_price = current_spot

                    # Check SL/TP by price level
                    # For binary: primary enforcement (no server-side limit_order)
                    # For multiplier: safety fallback (server handles via limit_order,
                    #   but trailing updates are async and can be delayed/rejected)
                    # Use live signal.sl_price/tp_price which may be updated by trailing stop
                    check_sl = signal.sl_price if self.contract_mode == "multiplier" else mon_sl
                    check_tp = signal.tp_price if self.contract_mode == "multiplier" else mon_tp

                    if signal.signal_type == SignalType.LONG:
                        if check_sl is not None and current_spot <= check_sl:
                            logger.warning(
                                f"SL HIT for {contract_id} | "
                                f"Price={current_spot:.5f} <= SL={check_sl:.5f}"
                            )
                            await self._sell_contract(position)
                            position.status = PositionStatus.CLOSED_SL
                            break
                        if check_tp is not None and current_spot >= check_tp:
                            logger.info(
                                f"TP HIT for {contract_id} | "
                                f"Price={current_spot:.5f} >= TP={check_tp:.5f}"
                            )
                            await self._sell_contract(position)
                            position.status = PositionStatus.CLOSED_TP
                            break
                    elif signal.signal_type == SignalType.SHORT:
                        if check_sl is not None and current_spot >= check_sl:
                            logger.warning(
                                f"SL HIT for {contract_id} | "
                                f"Price={current_spot:.5f} >= SL={check_sl:.5f}"
                            )
                            await self._sell_contract(position)
                            position.status = PositionStatus.CLOSED_SL
                            break
                        if check_tp is not None and current_spot <= check_tp:
                            logger.info(
                                f"TP HIT for {contract_id} | "
                                f"Price={current_spot:.5f} <= TP={check_tp:.5f}"
                            )
                            await self._sell_contract(position)
                            position.status = PositionStatus.CLOSED_TP
                            break

                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Monitor check error: {e} (attempt {consecutive_errors}/{max_consecutive_errors})")
                    if consecutive_errors >= max_consecutive_errors:
                        position.status = PositionStatus.CLOSED_ERROR
                        break

                await asyncio.sleep(check_interval)

        except asyncio.CancelledError:
            logger.info(f"Monitor cancelled for contract {contract_id}")
        finally:
            position.close_time = time.time()
            self._close_position(position)
            self._monitor_tasks.pop(contract_id, None)

    async def _sell_contract(self, position: Position):
        """Attempt to sell (cash out) a contract early."""
        if not position.contract_id:
            return
        try:
            sell_response = await self.api.send({
                "sell": position.contract_id,
                "price": 0,
            })
            if "error" in sell_response:
                logger.warning(f"Sell error: {sell_response['error']['message']}")
                position.profit = None
            else:
                sell_data = sell_response.get("sell", {})
                sell_price = float(sell_data.get("sell_price", 0) or 0)
                position.profit = sell_price - position.buy_price
                logger.info(
                    f"Contract {position.contract_id} sold | "
                    f"SellPrice={sell_price} Profit={position.profit:.2f}"
                )
        except Exception as e:
            logger.error(f"Sell execution error: {e}")

    def _close_position(self, position: Position):
        try:
            self.active_positions.remove(position)
        except ValueError:
            pass
        self.closed_positions.append(position)
        # Prevent unbounded memory growth: keep only last 100 closed positions
        if len(self.closed_positions) > 100:
            self.closed_positions = self.closed_positions[-100:]
        self._log_position_summary(position)
        if self.on_position_closed:
            try:
                self.on_position_closed(position)
            except Exception as e:
                logger.error(f"on_position_closed callback error: {e}")

    def _can_open_more(self) -> bool:
        """Check if we can open more positions based on max_positions limit."""
        return len(self.active_positions) < self.max_positions

    async def update_limit_order(self, contract_id: int, stop_loss_price: float = None, take_profit_price: float = None):
        """Update SL/TP for an open multiplier contract via Deriv API.

        Converts price levels to dollar amounts before sending to Deriv.
        
        Args:
            contract_id: The Deriv contract ID
            stop_loss_price: New SL price level (absolute price), or None to skip
            take_profit_price: New TP price level (absolute price), or None to skip
        """
        if not contract_id:
            return

        position = None
        for p in self.active_positions:
            if p.contract_id == contract_id and p.signal:
                position = p
                break
        
        if not position or not position.signal:
            return

        signal = position.signal
        limit_order = {}

        if stop_loss_price is not None:
            sl_dollars = self._price_to_dollars(
                signal.entry_price, stop_loss_price,
                signal.signal_type.value
            )
            limit_order["stop_loss"] = abs(sl_dollars)

        if take_profit_price is not None:
            tp_dollars = self._price_to_dollars(
                signal.entry_price, take_profit_price,
                signal.signal_type.value
            )
            limit_order["take_profit"] = abs(tp_dollars)

        if not limit_order:
            return

        try:
            response = await self.api.send({
                "contract_update": 1,
                "contract_id": contract_id,
                "limit_order": limit_order,
            })
            if "error" in response:
                logger.warning(
                    f"contract_update error for {contract_id}: "
                    f"{response['error'].get('message', 'unknown')}"
                )
            else:
                sl_label = f"{stop_loss_price:.5f}" if stop_loss_price is not None else "N/A"
                tp_label = f"{take_profit_price:.5f}" if take_profit_price is not None else "N/A"
                logger.info(
                    f"contract_update OK for {contract_id} | "
                    f"SL={sl_label} (${limit_order.get('stop_loss', 0):.2f}) "
                    f"TP={tp_label} (${limit_order.get('take_profit', 0):.2f})"
                )
        except Exception as e:
            logger.error(f"contract_update failed for {contract_id}: {e}")

    def _log_position_summary(self, position: Position):
        signal = position.signal
        if not signal:
            return
        direction = signal.signal_type.value
        profit_str = f"{position.profit:.2f}" if position.profit is not None else "N/A"
        profit_tag = "+" + profit_str if position.profit and position.profit > 0 else profit_str
        duration = ""
        if position.open_time and position.close_time:
            secs = position.close_time - position.open_time
            if secs > 60:
                duration = f" | Duration={int(secs//60)}m{int(secs%60):02d}s"
            else:
                duration = f" | Duration={secs:.0f}s"
        logger.info(
            f"TRADE CLOSED {direction} #{position.contract_id} | "
            f"{position.status.value}{duration} | "
            f"P/L={profit_tag} | Entry={signal.entry_price:.5f} | "
            f"SL={signal.sl_price:.5f} TP={signal.tp_price:.5f}"
        )

    def _get_deriv_symbol(self) -> str:
        """Get the Deriv symbol for this trader instance."""
        if self.symbol:
            return self.symbol
        from config import SYMBOL
        return SYMBOL

    def get_stats(self) -> dict:
        total = len(self.closed_positions)
        wins = sum(1 for p in self.closed_positions if p.profit and p.profit > 0)
        losses = sum(1 for p in self.closed_positions if p.profit and p.profit <= 0)
        total_profit = sum(p.profit for p in self.closed_positions if p.profit)
        win_rate = (wins / total * 100) if total > 0 else 0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_profit": total_profit,
            "active": len(self.active_positions),
        }
