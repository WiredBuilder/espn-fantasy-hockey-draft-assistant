"""Weekly checkup: the user's roster this week, from data refresh.py already stored.
Read-only. Points this week = season projection under league scoring / 82 x games this week."""
import datetime as dt
import json

from . import espn, rank

STARTING_SLOTS = {k: v for k, v in espn.SLOTS.items() if v not in ("BE", "IR")}
GAMES_PER_SEASON = 82


def week_window(today=None, start=None):
    """Monday to Sunday (local dates) that contains `today`, or the week starting at `start`."""
    if start:
        first = dt.date.fromisoformat(start) if isinstance(start, str) else start
        first -= dt.timedelta(days=first.weekday())
    else:
        today = today or dt.date.today()
        first = today - dt.timedelta(days=today.weekday())
    return first, first + dt.timedelta(days=6)


def games_by_team(con, first, last):
    """pro_team_id -> number of games whose local date falls inside [first, last]."""
    out = {}
    for r in con.execute("SELECT pro_team_id, bye_or_games_json FROM pro_teams"):
        n = 0
        for games in json.loads(r["bye_or_games_json"] or "{}").values():
            for g in games:
                ms = g.get("date")
                if ms is None:
                    continue
                d = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).astimezone().date()
                if first <= d <= last:
                    n += 1
        out[r["pro_team_id"]] = n
    return out


def my_team(con, league_run):
    row = con.execute("SELECT team_id, name FROM fantasy_teams WHERE run_id=? AND is_mine=1", (league_run,)).fetchone()
    if row is None:
        raise SystemExit("No team marked as yours in the latest league pull. Set MY_TEAM_ID in .env and run scripts/refresh.py.")
    return row["team_id"], row["name"]


def build(con, today=None, start=None, season=2027):
    """Everything the report prints, as plain data (so --json and the tests see the same thing)."""
    players, meta = rank.build(con, season)
    rank.check(meta)
    by_id = {p["espn_id"]: p for p in players}
    lg = rank.latest(con, "espn_league", "league")
    team_id, team_name = my_team(con, lg["run_id"])
    first, last = week_window(today, start)
    games = games_by_team(con, first, last)
    slot_counts = {r["slot_id"]: r["count"] for r in con.execute(
        "SELECT slot_id, count FROM league_roster_slots WHERE run_id=? AND count>0", (lg["run_id"],))}
    team_abbrev = {r["pro_team_id"]: r["abbrev"] for r in con.execute("SELECT pro_team_id, abbrev FROM pro_teams")}

    rows, filled = [], {}
    q = """SELECT e.lineup_slot_id, e.espn_id, p.full_name, p.default_position pos, s.pro_team_id, s.injury_status
           FROM roster_entries e JOIN players p USING (espn_id)
           LEFT JOIN player_snapshots s ON s.espn_id=e.espn_id AND s.run_id=(
               SELECT MAX(run_id) FROM player_snapshots WHERE espn_id=e.espn_id)
           WHERE e.run_id=? AND e.team_id=? ORDER BY e.lineup_slot_id, p.full_name"""
    for r in con.execute(q, (lg["run_id"], team_id)):
        x = by_id.get(r["espn_id"], {})
        slot = espn.SLOTS.get(r["lineup_slot_id"], str(r["lineup_slot_id"]))
        filled[r["lineup_slot_id"]] = filled.get(r["lineup_slot_id"], 0) + 1
        g = games.get(r["pro_team_id"], 0)
        pts = x.get("pts")
        status = r["injury_status"] or x.get("injury_status") or "ACTIVE"
        rows.append({"slot": slot, "starting": r["lineup_slot_id"] in STARTING_SLOTS, "name": r["full_name"],
                     "pos": r["pos"], "team": team_abbrev.get(r["pro_team_id"], "?"), "status": status,
                     "games": g, "season_pts": pts, "week_pts": (pts / GAMES_PER_SEASON * g) if pts is not None else None,
                     "healthy": status == "ACTIVE"})

    alerts = []
    for x in rows:
        if x["starting"] and not x["healthy"]:
            alerts.append(f"!!! {x['status']} in a starting slot: {x['name']} ({x['pos']}, {x['team']})")
    for slot_id, n in sorted(slot_counts.items()):
        if slot_id in STARTING_SLOTS and filled.get(slot_id, 0) < n:
            alerts.append(f"!!! Empty slot: {espn.SLOTS[slot_id]} ({filled.get(slot_id, 0)} of {n} filled)")

    starters = [x for x in rows if x["starting"]]
    possible = sum(x["games"] for x in starters)
    playing = sum(x["games"] for x in starters if x["healthy"])
    return {"team": team_name, "week": [first.isoformat(), last.isoformat()], "league_pulled": meta["league_pulled"],
            "roster": rows, "alerts": alerts,
            "summary": {"starters": len(starters), "games_playing": playing, "games_possible": possible,
                        "week_pts": round(sum(x["week_pts"] or 0 for x in starters if x["healthy"]), 1)}}


def render(rep):
    first, last = rep["week"]
    out = [f"Weekly checkup: {rep['team']} | week {first} to {last} | league data from {rep['league_pulled'][:16]}", ""]
    out.append(f"{'Slot':<5}{'Player':<26}{'Pos':<4}{'Team':<5}{'Status':<12}{'GP wk':>6}{'Pts wk':>8}{'Season':>8}")
    for x in rep["roster"]:
        wk = f"{x['week_pts']:.1f}" if x["week_pts"] is not None else "-"
        se = f"{x['season_pts']:.0f}" if x["season_pts"] is not None else "no proj"
        out.append(f"{x['slot']:<5}{x['name'][:25]:<26}{x['pos']:<4}{x['team']:<5}{x['status']:<12}{x['games']:>6}{wk:>8}{se:>8}")
    out.append("")
    out.extend(rep["alerts"] or ["No lineup alerts."])
    s = rep["summary"]
    out.append("")
    out.append(f"Starters playing {s['games_playing']} of {s['games_possible']} possible games this week, "
               f"about {s['week_pts']} points under league scoring.")
    return "\n".join(out)
