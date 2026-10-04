"""Weekly checkup: the user's roster this week, from data refresh.py already stored.
Read-only. Points this week = season projection under league scoring / 82 x games this week.
Adds and drops rank by value over replacement (rank.build), never by hunch; the user makes every move on ESPN."""
import datetime as dt
import json

from . import espn, rank

STARTING_SLOTS = {k: v for k, v in espn.SLOTS.items() if v not in ("BE", "IR")}
GAMES_PER_SEASON = 82
TOP_ADDS, TOP_DROPS, TOP_TRENDING = 5, 2, 10


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


def ownership(con):
    """espn_id -> (pct_owned now, pct_owned at the previous league pull or None)."""
    runs = [r["run_id"] for r in con.execute(
        "SELECT run_id FROM fetch_runs WHERE source_id='espn_league' AND endpoint='players' AND ok=1 ORDER BY run_id DESC LIMIT 2")]
    if not runs:
        return {}
    now = {r["espn_id"]: r["pct_owned"] for r in con.execute("SELECT espn_id, pct_owned FROM player_snapshots WHERE run_id=?", (runs[0],))}
    prev = {r["espn_id"]: r["pct_owned"] for r in con.execute("SELECT espn_id, pct_owned FROM player_snapshots WHERE run_id=?", (runs[1],))} if len(runs) > 1 else {}
    return {pid: (pct, prev.get(pid)) for pid, pct in now.items()}


def read_skip(path, players):
    """skip.txt: one player name per line. Returns the espn_ids it names (case-insensitive, whole name)."""
    if not path:
        return set()
    try:
        names = {line.strip().lower() for line in open(path, encoding="utf-8") if line.strip() and not line.startswith("#")}
    except FileNotFoundError:
        return set()
    return {p["espn_id"] for p in players if p["full_name"].lower() in names}


def my_team(con, league_run):
    row = con.execute("SELECT team_id, name FROM fantasy_teams WHERE run_id=? AND is_mine=1", (league_run,)).fetchone()
    if row is None:
        raise SystemExit("No team marked as yours in the latest league pull. Set MY_TEAM_ID in .env and run scripts/refresh.py.")
    return row["team_id"], row["name"]


def _week_pts(pts, games):
    return (pts / GAMES_PER_SEASON * games) if pts is not None else None


def build(con, today=None, start=None, season=2027, skip_file=None, moves_left=None):
    """Everything the report prints, as plain data (so --json and the tests see the same thing)."""
    players, meta = rank.build(con, season)
    rank.check(meta)
    by_id = {p["espn_id"]: p for p in players}
    lg = rank.latest(con, "espn_league", "league")
    team_id, team_name = my_team(con, lg["run_id"])
    first, last = week_window(today, start)
    games = games_by_team(con, first, last)
    own = ownership(con)
    skip = read_skip(skip_file, players)
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
        rows.append({"espn_id": r["espn_id"], "slot": slot, "model_slot": rank.slot_of(r["pos"]),
                     "starting": r["lineup_slot_id"] in STARTING_SLOTS, "name": r["full_name"],
                     "pos": r["pos"], "team": team_abbrev.get(r["pro_team_id"], "?"), "status": status,
                     "games": g, "season_pts": pts, "vor": x.get("vor"), "week_pts": _week_pts(pts, g),
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

    # Who each free agent would replace: the lowest-VOR rostered player at the same model slot.
    # A hurt starter counts as replaceable at VOR 0, so the gain shows what the empty nights cost.
    weakest = {}
    for x in rows:
        if x["vor"] is None:
            continue
        v = x["vor"] if x["healthy"] else min(x["vor"], 0.0)
        if x["model_slot"] not in weakest or v < weakest[x["model_slot"]]["vor_eff"]:
            weakest[x["model_slot"]] = {"name": x["name"], "vor_eff": v}
    for s in meta["starters"]:
        if s not in weakest and slot_counts:
            weakest[s] = {"name": "(empty slot)", "vor_eff": 0.0}

    team_of = {r["espn_id"]: r["pro_team_id"] for r in con.execute("SELECT espn_id, pro_team_id FROM players")}
    adds = []
    for p in players:
        if p.get("on_team_id") not in (0, None) or p["espn_id"] in skip:
            continue
        if p.get("injury_status") not in (None, "ACTIVE"):
            continue
        w = weakest.get(p["slot"])
        if w is None:
            continue
        gain = p["vor"] - w["vor_eff"]
        if gain <= 0:
            continue
        g = games.get(team_of.get(p["espn_id"]), 0)
        pct, prev = own.get(p["espn_id"], (None, None))
        adds.append({"name": p["full_name"], "pos": p["pos"], "team": p["team"] or "?", "slot": p["slot"], "games": g,
                     "season_pts": p["pts"], "vor": p["vor"], "gain": gain, "replaces": w["name"],
                     "pct_owned": pct, "pct_change": (pct - prev) if pct is not None and prev is not None else None})
    adds.sort(key=lambda a: (-a["gain"], -a["games"]))
    adds = adds[:TOP_ADDS]

    g_required = meta["starters"].get("G", 0)
    g_rostered = sum(1 for x in rows if x["model_slot"] == "G")
    drops = [x for x in rows if x["vor"] is not None and not (x["model_slot"] == "G" and g_rostered <= g_required)]
    drops.sort(key=lambda x: (x["healthy"], x["vor"], x["games"]))
    drops = [{"name": x["name"], "pos": x["pos"], "team": x["team"], "status": x["status"], "games": x["games"],
              "season_pts": x["season_pts"], "vor": x["vor"]} for x in drops[:TOP_DROPS]]

    trending = []
    for p in players:
        if p.get("on_team_id") not in (0, None):
            continue
        pct, prev = own.get(p["espn_id"], (None, None))
        if pct is None or prev is None or pct <= prev:
            continue
        trending.append({"name": p["full_name"], "pos": p["pos"], "team": p["team"] or "?", "pct_owned": pct,
                         "pct_change": pct - prev, "season_pts": p["pts"], "status": p.get("injury_status") or "ACTIVE"})
    trending.sort(key=lambda t: -t["pct_change"])
    trending = trending[:TOP_TRENDING]

    return {"team": team_name, "week": [first.isoformat(), last.isoformat()], "league_pulled": meta["league_pulled"],
            "roster": rows, "alerts": alerts,
            "summary": {"starters": len(starters), "games_playing": playing, "games_possible": possible,
                        "week_pts": round(sum(x["week_pts"] or 0 for x in starters if x["healthy"]), 1)},
            "adds": adds, "drops": drops, "trending": trending, "moves_left": moves_left,
            "trend_basis": "since the previous league pull" if any(v[1] is not None for v in own.values()) else None}


def _pct(a):
    if a["pct_owned"] is None:
        return "-"
    s = f"{a['pct_owned']:.0f}%"
    if a.get("pct_change") is not None:
        s += f" ({a['pct_change']:+.1f})"
    return s


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

    out += ["", "## Adds (free agents who beat your weakest player at their slot, rest of season)"]
    if rep["adds"]:
        for a in rep["adds"]:
            out.append(f"  {a['name']} ({a['pos']}, {a['team']}): +{a['gain']:.0f} over {a['replaces']}, "
                       f"{a['games']} games this week, {a['season_pts']:.0f} season pts, rostered {_pct(a)}")
    else:
        out.append("  None: no healthy free agent beats your weakest player at any slot.")
    out += ["", "## Drops (lowest value on your roster; hurt players first)"]
    for d in rep["drops"]:
        out.append(f"  {d['name']} ({d['pos']}, {d['team']}, {d['status']}): VOR {d['vor']:.0f}, {d['games']} games this week, {d['season_pts']:.0f} season pts")
    rookies = [d["name"] for d in rep["drops"] if d["season_pts"] is not None and d["season_pts"] < 10]
    if rookies:
        out.append(f"  Note: ESPN projects most rookies near zero, so {', '.join(rookies)} may be a projection gap, not a bad player. Judge the role yourself.")
    out += ["", f"## Trending (free agents being picked up league-wide{', ' + rep['trend_basis'] if rep['trend_basis'] else ''})"]
    if rep["trending"]:
        for t in rep["trending"]:
            out.append(f"  {t['name']} ({t['pos']}, {t['team']}): rostered {_pct(t)}, {t['season_pts']:.0f} season pts{'' if t['status'] == 'ACTIVE' else ', ' + t['status']}")
    else:
        out.append("  No trend yet: needs two league pulls (run scripts/refresh.py again another day).")
    if rep["moves_left"] is not None:
        out.append("")
        out.append(f"Moves left before Christmas: {rep['moves_left']}")
        if rep["moves_left"] <= 1:
            out.append(f"!!! Only {rep['moves_left']} move{'s' if rep['moves_left'] != 1 else ''} left")
    return "\n".join(out)
