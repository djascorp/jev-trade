"""Renvoie le VRAI solde Deriv (source de vérité) : BAL|NPOS|FLOATING
Utilisé par la reconciliation periodique. Silencieux sur les logs, print uniquement."""
import asyncio, sys, os
sys.path.insert(0, '/home/djasnive/PROJECTS/Python/bloc-trade')
os.chdir('/home/djasnive/PROJECTS/Python/bloc-trade')
from dotenv import load_dotenv
load_dotenv('.env')
from core.deriv_api import DerivAPI


async def main():
    api = DerivAPI(ws_url='', api_token='',
                   pat_token=os.getenv('DERIV_PAT_TOKEN', ''),
                   app_id=os.getenv('DERIV_PAT_APP_ID', ''),
                   account_id=os.getenv('DERIV_PAT_ACCOUNT_ID', ''))
    await api.connect()
    bal = await api.get_balance()
    b = bal.get('balance', {})
    bal_val = float(b.get('balance', 0))
    try:
        port = await api.send({'portfolio': 1})
        pos = port.get('portfolio', {}).get('contracts', []) or []
    except Exception:
        pos = []
    floating = sum(float(p.get('profit', 0)) for p in pos)
    print(f"{bal_val:.2f}|{len(pos)}|{floating:+.2f}")
    loop = asyncio.get_event_loop()
    loop.stop()


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
