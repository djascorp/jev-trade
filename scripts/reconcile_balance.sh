#!/bin/bash
# RAPPROCHEMENT DU VRAI SOLDE — appelé à chaque vérification périodique (cron */5)
# Compare le solde réel Deriv (source de vérité) avec le dernier snapshot.
# Journal: scalper/logs/reconciliation.log | Alerte si le solde bouge.
cd /home/djasnive/PROJECTS/Python/bloc-trade

LOG=scalper/logs/reconciliation.log
STATE=scalper/logs/balance_state.txt
TS=$(date -u '+%F %T')

OUT=$(.venv/bin/python scripts/balance_check.py 2>/dev/null)
if [ -z "$OUT" ]; then
    echo "$TS | ERROR: API Deriv injoignable — pas de rapprochement possible" >> "$LOG"
    echo "ERROR API"
    exit 1
fi

BAL=$(echo "$OUT"  | cut -d'|' -f1)
NPOS=$(echo "$OUT" | cut -d'|' -f2)
FLOAT=$(echo "$OUT"| cut -d'|' -f3)

PREV=$(head -1 "$STATE" 2>/dev/null)
echo "$BAL" > "$STATE"

if [ -z "$PREV" ]; then
    DELTA="N/A (premiere mesure)"
elif [ "$PREV" = "$BAL" ]; then
    DELTA="+0.00"
else
    DELTA=$(.venv/bin/python -c "print(f'{$BAL - $PREV:+.2f}')")
fi

echo "$TS | real=\$$BAL | prev=\$$PREV | delta=$DELTA | positions=$NPOS | floating=$FLOAT" >> "$LOG"

# Alerte + stdout si le solde a bougé depuis la derniere verification
if [ -n "$PREV" ] && [ "$PREV" != "$BAL" ]; then
    MSG="$TS | ⚠️ RAPPROCHEMENT: solde reel derive $PREV -> $BAL ($DELTA) | positions=$NPOS | flottant=$FLOAT"
    echo "$MSG" >> multibot/logs/watchdog.log
    echo "$MSG"
fi
