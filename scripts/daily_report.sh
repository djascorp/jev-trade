#!/bin/bash
# Wrapper rapport quotidien — utilisé par le cron Hermes (19h heure locale / 16h UTC env)
cd /home/djasnive/PROJECTS/Python/bloc-trade
.venv/bin/python scripts/daily_report.py 2>/dev/null
