#!/usr/bin/env python3
"""Test the correct proposal format for new Deriv PAT API."""
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
    
    # First: get all available symbols
    print("\n=== AVAILABLE SYMBOLS ===")
    r = await api.send({"active_symbols": "brief"})
    syms = r.get("active_symbols", [])
    # Show first 20
    for s in syms[:30]:
        print(f"  {s.get('symbol',''):20s} | {s.get('display_name',''):30s} | pip={s.get('pip','')} | market={s.get('market','')}")
    print(f"  ... ({len(syms)} total)")
    
    # Try proposal with underlying_symbol
    print("\n=== TEST A: underlying_symbol (MULTUP R_100) ===")
    try:
        r = await api.send({
            "proposal": 1,
            "amount": 4,
            "basis": "stake",
            "contract_type": "MULTUP",
            "currency": "USD",
            "underlying_symbol": "R_100",
            "multiplier": 100,
        })
        if "error" in r:
            print(f"  FAILED: {r['error']['message']}")
        else:
            print(f"  SUCCESS: id={r.get('proposal',{}).get('id','?')}")
            print(f"  Response keys: {list(r.get('proposal', {}).keys())}")
    except Exception as e:
        print(f"  ERROR: {e}")
    
    print("\n=== TEST B: underlying_symbol with different field names ===")
    # Try different format variations
    for field in ["underlying_symbol", "underlying", "market", "instrument", "asset"]:
        try:
            r = await api.send({
                "proposal": 1,
                "amount": 4,
                "basis": "stake",
                "contract_type": "CALL",
                "currency": "USD",
                field: "R_100",
                "duration": 5,
                "duration_unit": "m",
            })
            if "error" in r:
                err = r['error']['message']
                if "not allowed" in err:
                    print(f"  {field}: NOT ALLOWED")
                else:
                    print(f"  {field}: {err}")
            else:
                print(f"  {field}: SUCCESS! payout={r.get('proposal',{}).get('payout','?')}")
                break
        except Exception as e:
            print(f"  {field}: {str(e)[:80]}")
    
    # Try a minimal buy to see what fields are required
    print("\n=== TEST C: Minimal proposal ===")
    try:
        r = await api.send({
            "proposal": 1,
            "amount": 4,
            "basis": "stake",
            "contract_type": "CALL",
            "currency": "USD",
            "underlying_symbol": "R_100",
            "duration": 5,
            "duration_unit": "m",
        })
        if "error" in r:
            print(f"  FAILED: {r['error']['message']}")
            # Check if there are more details
            print(f"  Full error: {json.dumps(r['error'])[:300]}")
        else:
            prop = r.get('proposal', {})
            print(f"  SUCCESS!")
            print(f"  payout: {prop.get('payout')}")
            print(f"  spot: {prop.get('spot')}")
            print(f"  id: {prop.get('id')}")
            print(f"  All keys: {list(prop.keys())}")
    except Exception as e:
        print(f"  ERROR: {e}")
    
    # Check what the first symbol in active_symbols looks like
    print("\n=== FIRST SYMBOL DETAILS ===")
    if syms:
        print(f"  {json.dumps(syms[0], indent=2)}")
    
    await api.disconnect()

asyncio.run(test())
