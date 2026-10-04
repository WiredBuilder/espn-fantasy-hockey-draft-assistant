"""Weekly checkup: roster health, empty slots and games this week, on a small fixture league."""
import datetime as dt
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fhdb import checkup, db

SEASON = 2027
MON = dt.date(2026, 10, 5)  # a Monday


def _ms(day, hour=19):
    """Local evening game on `day` as ESPN's epoch milliseconds."""
    local = dt.datetime.combine(day, dt.time(hour)).astimezone()
    return int(local.timestamp() * 1000)


def _con(tmp_path):
    con = db.connect(tmp_path / "t.sqlite")
    lg = db.start_run(con, "espn_league", "league", SEASON)
    pl = db.start_run(con, "espn_league", "players", SEASON)
    con.execute("INSERT INTO league_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                (lg, 1, SEASON, "Fixture", 2, "TOTAL_SEASON_POINTS", "SNAKE", None, 1, "{}"))
    con.execute("INSERT INTO league_scoring VALUES (?,?,?,?)", (lg, 13, 1.0, 0))  # goals = 1 point
    for slot_id, name, n in [(3, "F", 2), (4, "D", 1), (5, "G", 1)]:
        con.execute("INSERT INTO league_roster_slots VALUES (?,?,?,?)", (lg, slot_id, name, n))
    con.execute("INSERT INTO fantasy_teams VALUES (?,?,?,?,?,?,?)", (lg, 7, "ME", "My Team", "Me", 1, 1))
    con.execute("INSERT INTO fantasy_teams VALUES (?,?,?,?,?,?,?)", (lg, 8, "YOU", "Other", "You", 2, 0))
    # Pro teams: 10 plays 4 games this week, 20 plays 2, 30 plays 0.
    sched = {
        10: {str(i + 1): [{"date": _ms(MON + dt.timedelta(days=d)), "scoringPeriodId": i + 1}] for i, d in enumerate((0, 2, 4, 6))},
        20: {"1": [{"date": _ms(MON)}], "2": [{"date": _ms(MON + dt.timedelta(days=3))}],
             "9": [{"date": _ms(MON + dt.timedelta(days=8))}]},  # next week, must not count
        30: {},
    }
    for tid, abbrev in [(10, "AAA"), (20, "BBB"), (30, "CCC")]:
        con.execute("INSERT INTO pro_teams VALUES (?,?,?,?,?,?)", (tid, abbrev, abbrev, abbrev, json.dumps(sched[tid]), lg))
    # espn_id, name, pos, pro team, status, projected goals, roster slot (None = free agent)
    people = [
        (1, "Healthy Forward", "C", 10, "ACTIVE", 41, 3),
        (2, "Hurt Forward", "LW", 20, "OUT", 82, 3),
        (3, "Lone Defender", "D", 20, "ACTIVE", 20, 4),
        (4, "Free Agent", "RW", 10, "ACTIVE", 30, None),
        (5, "Bench Guy", "G", 30, "ACTIVE", 10, 7),
    ]
    for pid, name, pos, team, status, goals, slot in people:
        con.execute("INSERT INTO players (espn_id, full_name, default_position, pro_team_id, active) VALUES (?,?,?,?,1)",
                    (pid, name, pos, team))
        con.execute("INSERT INTO player_snapshots (run_id, espn_id, pro_team_id, injury_status, injured, on_team_id, roster_status) "
                    "VALUES (?,?,?,?,?,?,?)", (pl, pid, team, status, 0 if status == "ACTIVE" else 1, 7 if slot else 0,
                                               "ONTEAM" if slot else "FREEAGENT"))
        con.execute("INSERT INTO stat_lines VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (pl, pid, f"10{SEASON}", SEASON, "projection", 1, "league", float(goals), None,
                     json.dumps({"13": goals, "34": 82})))
        if slot is not None:
            con.execute("INSERT INTO roster_entries VALUES (?,?,?,?,?)", (lg, 7, pid, slot, "DRAFT"))
    db.finish_run(con, lg, True, 200, 2)
    db.finish_run(con, pl, True, 200, len(people))
    return con


def test_week_window_is_monday_to_sunday():
    assert checkup.week_window(dt.date(2026, 10, 8)) == (dt.date(2026, 10, 5), dt.date(2026, 10, 11))
    assert checkup.week_window(start="2026-10-14") == (dt.date(2026, 10, 12), dt.date(2026, 10, 18))


def test_games_this_week_counts_only_dates_inside_the_window(tmp_path):
    con = _con(tmp_path)
    assert checkup.games_by_team(con, MON, MON + dt.timedelta(days=6)) == {10: 4, 20: 2, 30: 0}


def test_report_flags_out_starter_and_empty_slot_and_counts_games(tmp_path):
    rep = checkup.build(_con(tmp_path), today=MON + dt.timedelta(days=2), season=SEASON)
    assert rep["team"] == "My Team" and rep["week"] == ["2026-10-05", "2026-10-11"]
    by = {x["name"]: x for x in rep["roster"]}
    assert by["Healthy Forward"]["games"] == 4 and by["Healthy Forward"]["week_pts"] == 2.0  # 41/82*4
    assert by["Hurt Forward"]["status"] == "OUT" and by["Hurt Forward"]["starting"]
    assert by["Bench Guy"]["slot"] == "BE" and not by["Bench Guy"]["starting"]
    assert "Free Agent" not in by
    assert rep["alerts"] == ["!!! OUT in a starting slot: Hurt Forward (LW, BBB)",
                             "!!! Empty slot: G (0 of 1 filled)"]
    s = rep["summary"]
    assert s["starters"] == 3 and s["games_possible"] == 8 and s["games_playing"] == 6
    text = checkup.render(rep)
    assert "Hurt Forward" in text and "!!! Empty slot: G" in text and "6 of 8 possible games" in text
