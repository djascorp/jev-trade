#!/usr/bin/env python3
"""Find correct durations for CRASH500 and correct multipliers."""
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
    
    # Test CRASH500 with various durations
    print("=== CRASH500 Duration Tests (CALL) ===")
    for dur, unit in [(15, "m"), (30, "m"), (1, "h"), (2, "h"), (1, "d"), (10, "m"), (3, "m")]:
        r = await api.send({
            "proposal": 1, "amount": 4, "basis": "stake",
            "contract_type": "CALL", "currency": "USD",
            "underlying_symbol": "CRASH500",
            "duration": dur, "duration_unit": unit,
        })
        if "error" in r:
            print(f"  {dur}{unit}: FAILED — {r['error']['message'][:80]}")
        else:
            print(f"  {dur}{unit}: ✅ payout=${r.get('proposal',{}).get('payout','?')}")
    
    # Test CRASH500 PUT
    print("\n=== CRASH500 PUT Tests ===")
    for dur, unit in [(15, "m"), (30, "m"), (1, "h")]:
        r = await api.send({
            "proposal": 1, "amount": 4, "basis": "stake",
            "contract_type": "PUT", "currency": "USD",
            "underlying_symbol": "CRASH500",
            "duration": dur, "duration_unit": unit,
        })
        if "error" in r:
            print(f"  PUT {dur}{unit}: FAILED — {r['error']['message'][:80]}")
        else:
            print(f"  PUT {dur}{unit}: ✅ payout=${r.get('proposal',{}).get('payout','?')}")
    
    # Test R_25 multipliers
    print("\n=== R_25 Multiplier Tests ===")
    for mult in [160, 400, 800, 1200, 1600]:
        r = await api.send({
            "proposal": 1, "amount": 4, "basis": "stake",
            "contract_type": "MULTUP", "currency": "USD",
            "underlying_symbol": "R_25", "multiplier": mult,
        })
        if "error" in r:
            print(f"  MULTUP x{mult}: FAILED — {r['error']['message'][:80]}")
        else:
            p = r.get('proposal', {})
            print(f"  MULTUP x{mult}: ✅ spot={p.get('spot','?')} commission={p.get('commission','?')}")
    
    # Test stpRNG multipliers
    print("\n=== stpRNG Multiplier Tests ===")
    for mult in [750, 2000, 3500, 5500, 7500]:
        r = await api.send({
            "proposal": 1, "amount": 4, "basis": "stake",
            "contract_type": "MULTUP", "currency": "USD",
            "underlying_symbol": "stpRNG", "multiplier": mult,
        })
        if "error" in r:
            print(f"  MULTUP x{mult}: FAILED — {r['error']['message'][:80]}")
        else:
            p = r.get('proposal', {})
            print(f"  MULTUP x{mult}: ✅ spot={p.get('spot','?')}")
    
    # Buy test: actually buy a $4 CALL on R_25 to verify full flow
    print("\n=== ACTUAL BUY TEST: R_25 CALL 5min ===")
    r = await api.send({
        "proposal": 1, "amount": 4, "basis": "stake",
        "contract_type": "CALL", "currency": "USD",
        "underlying_symbol": "R_25",
        "duration": 5, "duration_unit": "m",
    })
    if "error" in r:
        print(f"  Proposal failed: {r['error']['message']}")
    else:
        prop_id = r['proposal']['id']
        payout = r['proposal']['payout']
        spot = r['proposal'].get('spot', '?')
        print(f"  Proposal OK: id={prop_id[:20]} payout=${payout} spot={spot}")
        
        # Buy it
        buy_r = await api.send({"buy": prop_id, "price": 4})
        if "error" in buy_r:
            print(f"  Buy failed: {buy_r['error']['message']}")
        else:
            contract_id = buy_r['buy']['contract_id']
            buy_price = buy_r['buy']['buy_price']
            print(f"  ✅ BOUGHT: contract_id={contract_id} buy_price=${buy_price}")
            
            # Subscribe to contract updates
            print(f"  Subscribing to contract updates...")
            try:
                sub_r = await api.send({
                    "proposal_open_contract": 1,
                    "contract_id": contract_id,
                    "subscribe": 1,
                })
                if "error" in sub_r:
                    print(f"  Subscribe failed: {sub_r['error']['message']}")
                else:
                    print(f"  ✅ Subscribed to contract updates")
                    poc = sub_r.get('proposal_open_contract', {})
                    print(f"  Status: {poc.get('status', '?')} | is_sold={poc.get('is_sold', '?')}")
            except Exception as e:
                print(f"  Subscribe error: {str(e)[:100]}")
    
    await api.disconnect()

asyncio.run(test())
