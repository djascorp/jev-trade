#!/usr/bin/env python3
"""
Multi-Bot Orchestrator — runs all trading bots in parallel.
Each bot has its own DerivAPI connection and operates independently.
"""
import asyncio
import json
import os
import sys
import time
import signal as sig_module
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_DIR, '.env'))

from multibot.base_bot import BaseBot
from multibot.mean_reversion_bot import MeanReversionBot
from multibot.spike_bot import MomentumBot
from multibot.grid_bot import GridTraderBot
from multibot.bb_bounce_bot import BounceBot
from multibot.consecutive_bot import ConsecutiveReversalBot
from multibot.bmr_bot import BigMoveReversalBot
from multibot.macd_bot import MACDBot
from multibot.macd_v2_bot import MACDBotV2
from multibot.fibonacci_bot import FibonacciBot
from multibot.supp_res_bot import SupportResistanceBot

class Orchestrator:
    """Manages multiple trading bots running in parallel."""
    
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(SCRIPT_DIR, 'config.json')
        
        with open(config_path) as f:
            self.config = json.load(f)
        
        self.bots: list[BaseBot] = []
        self.tasks: list[asyncio.Task] = []
        self.running = False
    
    def _create_bot(self, bot_config: dict) -> BaseBot:
        """Create a bot instance based on strategy type."""
        strategy = bot_config.get("strategy", "")
        
        if strategy == "mean_reversion":
            return MeanReversionBot(bot_config)
        elif strategy == "momentum":
            return MomentumBot(bot_config)
        elif strategy == "bbounce":
            return BounceBot(bot_config)
        elif strategy == "consecutive":
            return ConsecutiveReversalBot(bot_config)
        elif strategy == "bmr":
            return BigMoveReversalBot(bot_config)
        elif strategy == "grid":
            return GridTraderBot(bot_config)
        elif strategy == "macd":
            return MACDBot(bot_config)
        elif strategy == "macd_v2":
            return MACDBotV2(bot_config)
        elif strategy == "fibonacci":
            return FibonacciBot(bot_config)
        elif strategy == "supp_res":
            return SupportResistanceBot(bot_config)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")
    
    async def start(self):
        """Start all enabled bots."""
        print("=" * 70)
        print("  BLOCSLAND MULTI-BOT ORCHESTRATOR")
        print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)
        
        enabled = [b for b in self.config["bots"] if b.get("enabled", True)]
        print(f"\n  {len(enabled)} bot(s) enabled:")
        for b in enabled:
            print(f"    → {b['name']:25s} | {b['symbol']:10s} | ${b['stake']}/trade | {b['strategy']}")
        print()
        
        # Create and start bots
        for bot_config in enabled:
            try:
                bot = self._create_bot(bot_config)
                self.bots.append(bot)
                # Start bot as async task with crash protection
                task = asyncio.create_task(self._run_bot_safe(bot))
                self.tasks.append(task)
                print(f"  ✅ {bot.name} started")
                # Stagger startup to avoid connection issues
                await asyncio.sleep(3)
            except Exception as e:
                print(f"  ❌ Failed to start {bot_config['name']}: {e}")
        
        self.running = True
        
        # Start status reporter
        asyncio.create_task(self._status_reporter())
        
        print(f"\n  All bots running. Monitoring...\n")
        
        # Keepalive loop — restart dead bots, never exit
        try:
            while self.running:
                # Check for dead tasks and restart them
                alive = 0
                for idx, task in enumerate(self.tasks):
                    if task.done():
                        bot = self.bots[idx]
                        if not bot.running:
                            continue
                        # Restart the bot's safe wrapper
                        print(f"\n  ⚠️  {bot.name} task died — restarting...")
                        bot.api = None
                        bot.candles = []
                        self.tasks[idx] = asyncio.create_task(self._run_bot_safe(bot))
                        print(f"  ✅ {bot.name} restarted")
                    else:
                        alive += 1
                
                await asyncio.sleep(30)  # check every 30s
        except asyncio.CancelledError:
            pass
    
    async def _run_bot_safe(self, bot: BaseBot):
        """Run a bot with automatic crash recovery."""
        max_restarts = 10
        for attempt in range(1, max_restarts + 1):
            try:
                await bot.run()
                break  # Normal exit
            except Exception as e:
                print(f"\n  ⚠️  {bot.name} crashed (attempt {attempt}/{max_restarts}): {e}")
                if attempt < max_restarts:
                    print(f"     Restarting {bot.name} in 30 seconds...")
                    await asyncio.sleep(30)
                    bot.running = False
                    # Reconnect
                    try:
                        if bot.api:
                            await bot.api.disconnect()
                    except:
                        pass
                    bot.api = None
                    bot.candles = []
                    bot.running = True
                else:
                    print(f"  ❌ {bot.name} exhausted restart attempts — disabled")
    
    async def _status_reporter(self):
        """Print combined status every 5 minutes."""
        while self.running:
            await asyncio.sleep(300)  # 5 minutes
            
            print("\n" + "=" * 80)
            print(f"  STATUS REPORT — {datetime.now().strftime('%H:%M:%S')}")
            print("=" * 80)
            
            total_pnl = 0
            total_trades = 0
            total_active = 0
            
            for bot in self.bots:
                print(f"  {bot.status_line()}")
                total_pnl += bot.total_pnl
                total_trades += bot.trades_today
                total_active += len(bot.active_trades)
            
            print(f"\n  TOTAL | Active={total_active} | Trades={total_trades} | PnL=${total_pnl:+.2f}")
            print("=" * 80 + "\n")
    
    async def stop(self):
        """Stop all bots gracefully."""
        print("\n  Stopping all bots...")
        self.running = False
        
        for bot in self.bots:
            await bot.stop()
        
        for task in self.tasks:
            task.cancel()
        
        print("  All bots stopped.")


def main():
    orch = Orchestrator()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(orch.start())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        print(f"\n  ❌ Orchestrator error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
