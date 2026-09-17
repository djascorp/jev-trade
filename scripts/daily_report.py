"""Rapport quotidien de rapprochement — imprime un resume texte (livre via cron Hermes).
- Vrai solde Deriv (source de verite) vs dernier snapshot 5-min
- Trades des 24h (profit_table) vs delta de solde reel 24h
- Fraicheur des feeds de chaque bot (dernier prix en log)
Sortie vide = rien a signaler de bloque; le rapport part toujours (cron quotidien)."""
import asyncio, sys, os, json, subprocess, re
from datetime import datetime, timezone, timedelta

BASE = '/home/djasnive/PROJECTS/Python/bloc-trade'
sys.path.insert(0, BASE)
os.chdir(BASE)
from dotenv import load_dotenv
load_dotenv('.env')
from core.deriv_api import DerivAPI


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ''


def feed_freshness():
    """Fraicheur REELLE = variation de prix, pas la fraicheur des lignes de log.
    Les lignes de statut s'impriment meme quand le feed est mort (zombie 18-30/08)."""
    lines = []
    checks = [
        # (nom, log, filtre de lignes "status pures", pattern prix, nb mesures)
        ('Gold Scalper', 'scalper/logs/gold_scalper.log', 'STATUS', r'Price=\$[0-9.]+', 12),
        ('R_25 MACD M15', 'multibot/logs/r25_macd_m15.log', 'active=', r'price=[0-9.]+', 15),
        ('R_10 MACD v2 H1', 'multibot/logs/r10_macd_v2_h1.log', 'active=', r'price=[0-9.]+', 15),
        ('EUR/GBP StochRSI', 'multibot/logs/eurgbp_stochrsi_h1.log', 'active=', r'price=[0-9.]+', 15),
    ]
    now = datetime.now()  # logs en heure locale du serveur
    for name, path, line_filter, price_pat, n in checks:
        full = os.path.join(BASE, path)
        if not os.path.exists(full):
            lines.append(f"| {name} | ❌ pas de log |")
            continue
        uniq = sh(f"grep '{line_filter}' {full} | grep -oE '{price_pat}' | tail -{n} | sort -u | wc -l")
        last_ts = sh(f"grep -E '^[0-9]{{4}}-' {full} | tail -1 | cut -c1-19")
        try:
            last = datetime.strptime(last_ts[:19], '%Y-%m-%d %H:%M:%S')  # heure locale serveur
            age_min = int((now - last).total_seconds() / 60)
            age_txt = f"{age_min//60}h{age_min%60:02d}" if age_min >= 60 else f"{age_min}min"
            if uniq.strip() == '1':
                lines.append(f"| {name} | 🧟 FIGÉ (prix unique sur {n} statuts, dernier log il y a {age_txt}) |")
            else:
                lines.append(f"| {name} | ✅ vivant (dernier log il y a {age_txt}) |")
        except Exception:
            lines.append(f"| {name} | ⚠️ log illisible |")
    return lines


async def main():
    api = DerivAPI(ws_url='', api_token='',
                   pat_token=os.getenv('DERIV_PAT_TOKEN', ''),
                   app_id=os.getenv('DERIV_PAT_APP_ID', ''),
                   account_id=os.getenv('DERIV_PAT_ACCOUNT_ID', ''))
    await api.connect()
    b = (await api.get_balance()).get('balance', {})
    real_bal = float(b.get('balance', 0))
    try:
        port = await api.send({'portfolio': 1})
        pos = port.get('portfolio', {}).get('contracts', []) or []
    except Exception:
        pos = []

    # trades 24h depuis profit_table
    since = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())
    resp = await api.send({'profit_table': 1, 'description': 1, 'limit': 200, 'sort': 'DESC'})
    tx = resp.get('profit_table', {}).get('transactions', [])
    trades24 = [t for t in tx if int(t.get('sell_time', 0)) >= since]
    pnl24 = sum(float(t.get('sell_price', 0)) - float(t.get('buy_price', 0)) for t in trades24)

    # delta solde 24h depuis le journal de reconciliation
    recon = sh(f"grep -E '^' {BASE}/scalper/logs/reconciliation.log | grep '{datetime.now(timezone.utc):%F}' | head -1")
    m = re.search(r'real=\$([0-9.]+)', recon)
    bal_24h_ago = float(m.group(1)) if m else None
    drift = (real_bal - bal_24h_ago) if bal_24h_ago else None

    print("📊 RAPPROCHEMENT QUOTIDIEN — " + datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC'))
    print()
    print(f"💰 Vrai solde Deriv : ${real_bal:,.2f} | positions: {len(pos)}")
    if drift is not None:
        print(f"   vs il y a 24h: ${bal_24h_ago:,.2f} → dérive réelle {drift:+.2f}$")
    print(f"📈 Trades 24h: {len(trades24)} | PnL réalisé 24h: {pnl24:+.2f}$")
    if pos:
        fl = sum(float(p.get('profit', 0)) for p in pos)
        print(f"   ⚠️ {len(pos)} position(s) ouverte(s), flottant {fl:+.2f}$")
    # coherence PnL vs derive
    if drift is not None and trades24:
        gap = drift - pnl24
        flag = '✅ cohérent' if abs(gap) < 1 else f'⚠️ écart {gap:+.2f}$ (à investiguer)'
        print(f"   Rapprochement: dérive {drift:+.2f}$ vs PnL trades {pnl24:+.2f}$ → {flag}")
    print()
    print("🔄 Fraicheur des bots:")
    for l in feed_freshness():
        print(l)
    loop = asyncio.get_event_loop()
    loop.stop()


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
