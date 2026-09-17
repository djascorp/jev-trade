#!/usr/bin/env python3
"""Find correct underlying_symbol names for all Deriv markets."""
import sys, os, asyncio, json
sys.path.insert(0, '/home/djasnive/PROJECTS/Python/bloc-trade')
from dotenv import load_dotenv
load_dotenv('/home/djasnive/PROJECTS/Python/bloc-trade/.env')

from core.deriv_api import DerivAPI

async def test():
    pat = os.getenv('DERIV_PAT_TOKEN', '')
    app_id = os.getenv('DERIV_PAT_APP_ID', '')
    acct = os.getenv('DERIV_PAT_ACCOUNT_ID', '')
    
    api = DerivAPI(ws_url='', api_token='', pat_token=pat, app_id=app_id, account_id=acct)
    await api.connect()
    
    # Get ALL symbols
    r = await api.send({"active_symbols": "brief"})
    syms = r.get("active_symbols", [])
    
    print(f"=== ALL {len(syms)} SYMBOLS ===\n")
    print(f"{'underlying_symbol':>20} | {'name':>45} | {'market':>20} | {'pip':>8} | {'open':>5}")
    print("-" * 110)
    
    for s in sorted(syms, key=lambda x: x.get('underlying_symbol', '')):
        us = s.get('underlying_symbol', '')
        name = s.get('underlying_symbol_name', '')
        market = s.get('market', '')
        pip = s.get('pip_size', '')
        is_open = s.get('exchange_is_open', 0)
        print(f"{us:>20} | {name:>45} | {market:>20} | {pip:>8} | {'✅' if is_open else '❌'}")
    
    # Now test proposal for key symbols
    print("\n=== PROPOSAL TESTS ===")
    test_symbols = ['R_25', 'R_100', 'R_10', 'stpRNG', 'CRASH500', 'BOOM500']
    for sym in test_symbols:
        r = await api.send({
            "proposal": 1,
            "amount": 4,
            "basis": "stake",
            "contract_type": "CALL",
            "currency": "USD",
            "underlying_symbol": sym,
            "duration": 5,
            "duration_unit": "m",
        })
        if "error" in r:
            print(f"  {sym:15s}: FAILED — {r['error']['message']}")
        else:
            print(f"  {sym:15s}: ✅ payout=${r.get('proposal',{}).get('payout','?')}")
    
    # Test with MULTUP
    print("\n=== MULTUP TESTS ===")
    for sym in ['R_25', 'R_100', 'stpRNG']:
        r = await api.send({
            "proposal": 1,
            "amount": 4,
            "basis": "stake",
            "contract_type": "MULTUP",
            "currency": "USD",
            "underlying_symbol": sym,
            "multiplier": 100,
        })
        if "error" in r:
            print(f"  {sym:15s}: FAILED — {r['error']['message']}")
        else:
            p = r.get('proposal', {})
            print(f"  {sym:15s}: ✅ spot={p.get('spot','?')} payout={p.get('payout','?')}")
    
    await api.disconnect()

asyncio.run(test())
