"""Weekly checkup for your roster: injuries, empty slots, games this week. Read-only.
Usage:  .venv/bin/python scripts/checkup.py                 # this week (Mon to Sun)
        .venv/bin/python scripts/checkup.py --week 2026-10-12
        .venv/bin/python scripts/checkup.py --json
Run scripts/refresh.py first so the roster and injury statuses are current.
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fhdb import checkup, db, espn

ap = argparse.ArgumentParser()
ap.add_argument("--week", help="any date in the week to report (YYYY-MM-DD)")
ap.add_argument("--season", type=int)
ap.add_argument("--json", action="store_true")
a = ap.parse_args()
espn.load_env()
import os
season = a.season or int(os.environ.get("ESPN_SEASON", 2027))
rep = checkup.build(db.connect(), start=a.week, season=season)
print(json.dumps(rep, indent=1) if a.json else checkup.render(rep))
