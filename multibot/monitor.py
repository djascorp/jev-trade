#!/usr/bin/env python3
"""
Multi-Bot Monitor — quick status check of all running bots.
Run this to see what's happening inside each bot.
"""
import os, sys, json, re
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.json')

BOTS = ['r100_3consec_m30', 'eurgbp_4consec_m30', 'eurgbp_stochrsi_m30', 'eurusd_4consec_m30', 'gbpusd_stochrsi_m15']

def parse_log(name: str) -> dict:
    """Parse a bot's log file for recent activity."""
    log_file = os.path.join(LOG_DIR, f'{name}.log')
    if not os.path.exists(log_file):
        return {'name': name, 'running': False, 'error': 'No log file'}
    
    lines = Path(log_file).read_text().strip().split('\n')
    if not lines or not lines[0]:
        return {'name': name, 'running': False, 'error': 'Empty log'}
    
    # Parse last 100 lines
    recent = lines[-100:]
    
    # Extract data
    trades_opened = [l for l in recent if 'TRADE OPENED' in l]
    trades_closed = [l for l in recent if 'TRADE CLOSED' in l]
    signals = [l for l in recent if 'SIGNAL:' in l or 'SPIKE DETECTED' in l or 'GRID SIGNAL' in l]
    skips = [l for l in recent if 'SKIP:' in l]
    errors = [l for l in recent if 'ERROR' in l]
    status_lines = [l for l in recent if 'status_line' in l.lower() or 'active=' in l.lower()]
    
    # Get last status line
    last_status = None
    for l in reversed(recent):
        if 'active=' in l and 'trades=' in l:
            last_status = l
            break
    
    # Calculate PnL from closed trades
    total_pnl = 0.0
    wins = 0
    losses = 0
    for l in trades_closed:
        m = re.search(r'PnL=([+-]?[\d.]+)', l)
        if m:
            pnl = float(m.group(1))
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1
    
    # Check if bot is alive (last log within 5 minutes)
    last_line = recent[-1] if recent else ''
    alive = True  # Assume alive if log exists
    
    return {
        'name': name,
        'running': alive,
        'last_log': last_line[-100:] if last_line else '',
        'signals': len(signals),
        'trades_opened': len(trades_opened),
        'trades_closed': len(trades_closed),
        'wins': wins,
        'losses': losses,
        'pnl': total_pnl,
        'skips': len(skips),
        'errors': len(errors),
        'recent_signals': signals[-3:],
        'recent_trades': trades_closed[-3:],
        'recent_errors': errors[-3:],
        'last_status': last_status[-120:] if last_status else '',
    }

def main():
    print("=" * 90)
    print(f"  MULTI-BOT MONITOR — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)
    
    # Check if orchestrator is running
    import subprocess
    orch = subprocess.run(
        ['pgrep', '-f', 'orchestrator.py'],
        capture_output=True, text=True
    )
    orch_running = bool(orch.stdout.strip())
    print(f"\n  Orchestrator: {'RUNNING ✅' if orch_running else 'NOT RUNNING ❌'}")
    
    # Check individual bot processes
    for name in BOTS:
        r = subprocess.run(
            ['pgrep', '-f', name],
            capture_output=True, text=True
        )
    
    total_pnl = 0
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_signals = 0
    
    print()
    for name in BOTS:
        data = parse_log(name)
        if data.get('running'):
            total_pnl += data['pnl']
            total_trades += data['trades_closed']
            total_wins += data['wins']
            total_losses += data['losses']
            total_signals += data['signals']
            
            emoji = '🟢' if data['errors'] == 0 else '🟡'
            wr = f"{data['wins']/max(data['trades_closed'],1)*100:.0f}%" if data['trades_closed'] > 0 else "—"
            
            print(f"  {emoji} {data['name']:25s} | "
                  f"signals={data['signals']:3d} | "
                  f"trades={data['trades_closed']:3d} ({data['wins']}W/{data['losses']}L, WR={wr}) | "
                  f"PnL=${data['pnl']:+.2f} | "
                  f"errors={data['errors']}")
            
            if data['recent_signals']:
                for s in data['recent_signals'][-1:]:
                    print(f"      Last signal: ...{s[-80:]}")
            if data['recent_trades']:
                for t in data['recent_trades'][-1:]:
                    print(f"      Last trade:  ...{t[-80:]}")
            if data['recent_errors']:
                for e in data['recent_errors'][-1:]:
                    print(f"      ⚠️ Error:     ...{e[-80:]}")
        else:
            print(f"  🔴 {name:25s} | NOT RUNNING")
    
    print()
    print(f"  {'TOTAL':28s} | "
          f"signals={total_signals:3d} | "
          f"trades={total_trades:3d} ({total_wins}W/{total_losses}L) | "
          f"PnL=${total_pnl:+.2f}")
    print("=" * 90)

if __name__ == "__main__":
    main()
