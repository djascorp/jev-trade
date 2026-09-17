#!/usr/bin/env python3
"""Test different proposal formats on the new Deriv PAT API."""
import sys, os, asyncio
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
    
    # Test 1: Standard proposal with symbol (legacy format)
    print("\n=== TEST 1: Standard proposal (MULTUP + symbol) ===")
    try:
        r = await api.send({
            "proposal": 1,
            "amount": 4,
            "basis": "stake",
            "contract_type": "MULTUP",
            "currency": "USD",
            "symbol": "R_25",
            "multiplier": 100,
        })
        if "error" in r:
            print(f"  FAILED: {r['error']['message']}")
        else:
            print(f"  SUCCESS: id={r.get('proposal',{}).get('id','?')} payout={r.get('proposal',{}).get('payout','?')}")
    except Exception as e:
        print(f"  ERROR: {e}")
    
    # Test 2: Proposal without symbol
    print("\n=== TEST 2: Proposal WITHOUT symbol (MULTUP) ===")
    try:
        r = await api.send({
            "proposal": 1,
            "amount": 4,
            "basis": "stake",
            "contract_type": "MULTUP",
            "currency": "USD",
            "multiplier": 100,
        })
        if "error" in r:
            print(f"  FAILED: {r['error']['message']}")
        else:
            print(f"  SUCCESS: id={r.get('proposal',{}).get('id','?')} payout={r.get('proposal',{}).get('payout','?')}")
    except Exception as e:
        print(f"  ERROR: {e}")
    
    # Test 3: CALL/PUT with symbol + duration
    print("\n=== TEST 3: CALL with symbol + duration ===")
    try:
        r = await api.send({
            "proposal": 1,
            "amount": 4,
            "basis": "stake",
            "contract_type": "CALL",
            "currency": "USD",
            "symbol": "CRASH500",
            "duration": 5,
            "duration_unit": "m",
        })
        if "error" in r:
            print(f"  FAILED: {r['error']['message']}")
        else:
            print(f"  SUCCESS: id={r.get('proposal',{}).get('id','?')} payout={r.get('proposal',{}).get('payout','?')}")
    except Exception as e:
        print(f"  ERROR: {e}")
    
    # Test 4: Try with R_100 (known working symbol from existing bot)
    print("\n=== TEST 4: R_100 MULTUP with symbol ===")
    try:
        r = await api.send({
            "proposal": 1,
            "amount": 4,
            "basis": "stake",
            "contract_type": "MULTUP",
            "currency": "USD",
            "symbol": "R_100",
            "multiplier": 100,
        })
        if "error" in r:
            print(f"  FAILED: {r['error']['message']}")
        else:
            print(f"  SUCCESS: id={r.get('proposal',{}).get('id','?')} payout={r.get('proposal',{}).get('payout','?')}")
    except Exception as e:
        print(f"  ERROR: {e}")
    
    # Test 5: Try buy_contract method (uses different format)
    print("\n=== TEST 5: buy_contract method (CALL) ===")
    try:
        r = await api.buy_contract("CALL", "CRASH500", 4, "USD", 5, "m")
        print(f"  SUCCESS: {r.get('buy',{}).get('contract_id','?')}")
    except Exception as e:
        print(f"  ERROR: {str(e)[:200]}")
    
    # Test 6: What does the API respond for proposal_open_contract
    print("\n=== TEST 6: Active symbols (to find correct names) ===")
    try:
        r = await api.send({"active_symbols": "brief"})
        syms = r.get("active_symbols", [])
        crash_syms = [s for s in syms if 'CRASH' in s.get('symbol', '').upper() or 'BOOM' in s.get('symbol', '').upper()]
        vol_syms = [s for s in syms if s.get('symbol', '').startswith('R_')]
        print(f"  Total symbols: {len(syms)}")
        print(f"  Volatility: {[(s['symbol'], s.get('display_name','')) for s in vol_syms[:5]]}")
        print(f"  Crash/Boom: {[(s['symbol'], s.get('display_name','')) for s in crash_syms]}")
    except Exception as e:
        print(f"  ERROR: {str(e)[:200]}")
    
    await api.disconnect()

asyncio.run(test())
