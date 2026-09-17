"""Test de connexion Deriv pour jev-trade (read-only : aucun trade).

Utilise le flux PAT comme en production (cf. multibot/base_bot.py).
"""
import asyncio
import os
import sys

sys.path.insert(0, "/home/djasnive/PROJECTS/Python/jev-trade")

from dotenv import load_dotenv

load_dotenv("/home/djasnive/PROJECTS/Python/jev-trade/.env")

from core.deriv_api import DerivAPI


async def main():
    pat = os.getenv("DERIV_PAT_TOKEN", "")
    app_id = os.getenv("DERIV_PAT_APP_ID", "")
    account = os.getenv("DERIV_PAT_ACCOUNT_ID", "")

    api = DerivAPI(
        ws_url="", api_token="",
        pat_token=pat, app_id=app_id, account_id=account,
    )
    await api.connect()
    await api.authorize()
    print("[1/3] connect PAT + authorize OK")

    bal = await api.get_balance()
    b = bal.get("balance", {})
    print(f"[2/3] balance: {b.get('balance')} {b.get('currency')} (compte {account})")

    candles = await api.get_candles("R_100", granularity=60, count=5)
    print(f"[3/3] candles R_100: {len(candles)} recues, derniere close={candles[-1].get('close', '?')}")

    await api.disconnect()
    print("=== JEV-TRADE : drivers Deriv operationnels ===")


asyncio.run(main())
