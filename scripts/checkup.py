"""Weekly checkup for your roster: injuries, empty slots, games this week, adds, drops, trending. Read-only.
Usage:  .venv/bin/python scripts/checkup.py                 # this week (Mon to Sun)
        .venv/bin/python scripts/checkup.py --week 2026-10-12 --skip-file skip.txt
        .venv/bin/python scripts/checkup.py --json
Run scripts/refresh.py first so the roster, injuries and ownership are current.
MOVES_LEFT=2 in .env prints how many adds you have left (you lower it by hand after each move).
"""
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fhdb import checkup, db, espn

ap = argparse.ArgumentParser()
ap.add_argument("--week", help="any date in the week to report (YYYY-MM-DD)")
ap.add_argument("--season", type=int)
ap.add_argument("--skip-file", metavar="PATH", help="one player name per line, never suggested as an add")
ap.add_argument("--json", action="store_true")
a = ap.parse_args()
espn.load_env()
season = a.season or int(os.environ.get("ESPN_SEASON", 2027))
moves = os.environ.get("MOVES_LEFT")
rep = checkup.build(db.connect(), start=a.week, season=season, skip_file=a.skip_file,
                    moves_left=int(moves) if moves not in (None, "") else None)
print(json.dumps(rep, indent=1) if a.json else checkup.render(rep))
