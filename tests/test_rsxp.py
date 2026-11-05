import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest

from rsxp import client, collector, db, gains, planner
from rsxp.xp import INVENTION, MAX_XP, SKILL_IDS, XP_TABLE, target_xp, virtual_level


def ts(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp())


def hiscores_payload(xp_by_id):
    skills = [{"id": sid, "name": str(sid), "rank": 100, "level": 99, "xp": xp} for sid, xp in xp_by_id.items()]
    return {"name": "Test", "skills": skills, "activities": []}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


# --- XP curve -------------------------------------------------------------

def test_xp_thresholds():
    assert XP_TABLE[99] == 13_034_431
    assert XP_TABLE[110] == 38_737_661
    assert XP_TABLE[120] == 104_273_167
    assert target_xp(INVENTION, "99") == 36_073_511
    assert target_xp(INVENTION, "120") == 80_618_654
    assert target_xp(1, "200m") == MAX_XP


def test_virtual_level():
    assert virtual_level(1, 13_034_431) == 99
    assert virtual_level(1, 13_034_430) == 98
    assert virtual_level(1, 0) == 1
    assert virtual_level(1, MAX_XP) == 126
    assert virtual_level(INVENTION, 50_000_000, 107) == 107


# --- storage --------------------------------------------------------------

def test_name_key_and_validation(conn):
    a = db.add_player(conn, "Mod_Example")
    assert db.add_player(conn, "mod example") == a
    assert db.get_player(conn, "MOD_EXAMPLE")["display_name"] == "Mod Example"
    with pytest.raises(ValueError):
        db.add_player(conn, "this name is too long")


def test_snapshot_dedupe_moves_seen_until(conn):
    pid = db.add_player(conn, "Test")
    assert db.save_hiscores(conn, pid, hiscores_payload({0: 10, 1: 10}), now=1000)
    assert not db.save_hiscores(conn, pid, hiscores_payload({0: 10, 1: 10}), now=4600)
    assert db.save_hiscores(conn, pid, hiscores_payload({0: 20, 1: 20}), now=8200)
    rows = conn.execute("SELECT taken_at, seen_until FROM snapshots ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(1000, 4600), (8200, 8200)]
    # the jump happened after 4600, so XP at 4600 is still the old value
    pts = gains.load_series(conn, pid)[1]
    assert gains.xp_at(pts, 4600) == 10
    assert gains.xp_at(pts, 6400) == 15


def test_history_from_adventurers_log(conn):
    pid = db.add_player(conn, "Test")
    db.save_events(conn, pid, {"events": [
        {"date": "2026-10-25T23:23:22Z", "type": "rs_plog_xp_milestone", "text": "78000000XP in Ranged",
         "details": "I now have at least 78000000 experience points in the Ranged skill."},
        {"date": "2026-11-01T13:50:36Z", "type": "rs_plog_xp_milestone", "text": "80000000XP in Ranged", "details": ""},
        {"date": "2026-11-03T23:47:59Z", "type": "rs_plog_levelup_count", "text": "Levelled up Construction.",
         "details": "I levelled my Construction skill, I am now level 116."},
        {"date": "2026-11-05T14:25:12+00:00", "type": "rs_plog_xp_milestone", "text": "144000000XP in Constitution", "details": ""},
        {"date": "2026-11-04T10:00:00Z", "type": "rs_plog_quest", "text": "Quest complete: Cold Front", "details": ""},
    ]})
    s = gains.load_series(conn, pid)
    assert s[5] == [(ts(2026, 10, 25, 23, 23, 22), 78_000_000), (ts(2026, 11, 1, 13, 50, 36), 80_000_000)]
    assert s[23] == [(ts(2026, 11, 3, 23, 47, 59), XP_TABLE[116])]          # level-up -> XP threshold
    assert s[4][0][1] == 144_000_000                                          # Constitution -> Hitpoints
    assert gains.gain(s[5], ts(2026, 10, 26), ts(2026, 11, 2))[0] > 0


def test_history_uses_imported_monthly_rows_and_never_dips(conn):
    pid = db.add_player(conn, "Test")
    # a month-start total saved from RuneMetrics before it closed
    conn.execute("INSERT INTO snapshots(player_id, taken_at, seen_until, source) VALUES (?, ?, ?, 'runemetrics')",
                 (pid, ts(2026, 10, 1), ts(2026, 10, 1)))
    conn.execute("INSERT INTO snapshot_skills VALUES (last_insert_rowid(), 1, 40000000, NULL, NULL)")
    db.save_hiscores(conn, pid, hiscores_payload({0: 50_000_000, 1: 50_000_000}), now=ts(2026, 11, 5))
    # a batched level-up dated after the snapshot, with less XP than the snapshot shows
    db.save_events(conn, pid, {"events": [{"date": "2026-11-06T00:00:00Z", "type": "rs_plog_levelup_count",
        "text": "Levelled up Attack.", "details": "I levelled my Attack skill, I am now level 99."}]})
    pts = gains.load_series(conn, pid)[1]
    assert pts == [(ts(2026, 10, 1), 40_000_000), (ts(2026, 11, 5), 50_000_000)]
    s = planner.summary(conn, pid, ts(2026, 11, 5, 12), 30)
    assert s["estimated"]["month"] is True and s["history_starts"] == ts(2026, 10, 1)


# --- gains ----------------------------------------------------------------

def test_gain_and_partial():
    pts = [(100, 0), (200, 1000)]
    assert gains.gain(pts, 150, 200) == (500, False)
    assert gains.gain(pts, 0, 200) == (1000, True)
    assert gains.gain(pts, 0, 50) == (None, True)
    assert gains.xp_at(pts, 500) == 1000


def test_period_starts_use_rs_resets():
    now = ts(2026, 11, 5, 15)              # Thursday
    p = gains.period_starts(now)
    assert p["day"] == ts(2026, 11, 5)
    assert p["week"] == ts(2026, 11, 4)    # Wednesday reset
    assert p["month"] == ts(2026, 11, 1)
    assert p["year"] == ts(2026, 1, 1)
    assert gains.period_starts(ts(2026, 11, 4, 0, 30))["week"] == ts(2026, 11, 4)


def test_rate_and_monthly():
    pts = [(ts(2026, 9, 1), 0), (ts(2026, 10, 1), 3000), (ts(2026, 11, 1), 3000)]
    now = ts(2026, 11, 1)
    assert gains.rate_per_day(pts, now, 30) == 0
    assert gains.rate_per_day(pts, now, 365) == pytest.approx(3000 / 61)
    months = gains.monthly(pts, now)
    assert [(m["month"], m["gain"]) for m in months] == [("2026-09", 3000), ("2026-10", 0), ("2026-11", 0)]


# --- planner --------------------------------------------------------------

def test_skill_targets():
    t = planner.skill_targets(1, 67_444_161, 1_108_187 / 30.44)
    assert t["99"] == {"target_xp": XP_TABLE[99], "remaining": 0, "pct": 100.0, "eta_days": 0}
    assert t["120"]["remaining"] == 36_829_006
    assert t["120"]["eta_days"] == pytest.approx(36_829_006 / (1_108_187 / 30.44))
    assert planner.skill_targets(1, 0, None)["99"]["eta_days"] is None


def test_totals_slowest_and_missing_rate():
    a = {"targets": planner.skill_targets(1, 0, 1000)}
    b = {"targets": planner.skill_targets(2, MAX_XP, None)}
    tot = planner.totals([a, b])
    assert tot["99"]["skills_left"] == 1 and tot["99"]["eta_days"] == pytest.approx(XP_TABLE[99] / 1000)
    c = {"targets": planner.skill_targets(3, 0, None)}
    assert planner.totals([a, c])["99"]["eta_days"] is None


# --- client load safety ---------------------------------------------------

@pytest.fixture
def fake_api(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(client, "MIN_GAP_S", 0)
    monkeypatch.setattr(client, "JITTER_S", 0)
    monkeypatch.setattr(client, "_fails", 0)
    monkeypatch.setattr(client, "_paused_until", 0.0)
    monkeypatch.setattr(client.time, "sleep", sleeps.append)
    responses = []

    def send(url):
        calls.append(url)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(client, "_send", send)
    return responses, calls, sleeps


def http_error(code, body=b"", headers=None):
    return urllib.error.HTTPError("u", code, "x", headers or {}, io.BytesIO(body))


def test_redirect_is_not_found_without_retry(fake_api):
    responses, calls, _ = fake_api
    responses.append(http_error(302, headers={"Location": "https://www.runescape.com/unavailable"}))
    with pytest.raises(client.NotFound):
        client.hiscores("x")
    assert len(calls) == 1


def test_retries_5xx_and_honours_retry_after(fake_api):
    responses, calls, sleeps = fake_api
    responses += [http_error(429, headers={"Retry-After": "42"}), http_error(503), b'{"ok": 1}']
    assert client.hiscores("x") == {"ok": 1}
    assert len(calls) == 3 and sleeps[0] == 42


def test_client_errors_not_retried(fake_api):
    responses, calls, _ = fake_api
    responses.append(http_error(400))
    with pytest.raises(client.Unavailable):
        client.hiscores("x")
    responses.append(http_error(404, b'{"error":"PLAYER_NOT_MEMBER"}'))
    with pytest.raises(client.NotMember):
        client.events("x")
    assert len(calls) == 2


def test_circuit_breaker_stops_calls(fake_api):
    responses, calls, _ = fake_api
    responses += [http_error(500)] * (client.MAX_ATTEMPTS * client.BREAKER_FAILS)
    for _ in range(client.BREAKER_FAILS):
        with pytest.raises(client.Unavailable):
            client.hiscores("x")
    n = len(calls)
    with pytest.raises(client.Unavailable, match="paused"):
        client.hiscores("x")
    assert len(calls) == n


# --- collector ------------------------------------------------------------

def test_refresh_is_throttled(conn, fake_api):
    responses, calls, _ = fake_api
    pid = db.add_player(conn, "Test")
    responses += [json.dumps(hiscores_payload({0: 5, 1: 5})).encode(), b'{"name":"Test","events":[]}',
                  b'{"name":"Test","qp":1,"qp_max":2,"quests":{}}']
    assert collector.refresh(conn, pid, now=10_000)["polled"]
    r = collector.refresh(conn, pid, now=10_100)
    assert not r["polled"] and r["retry_in"] == 200
    assert len(calls) == 3   # hiscores + events + quests, nothing for the throttled refresh


def test_activities_history_and_gains(conn):
    pid = db.add_player(conn, "Test")
    def payload(score):
        p = hiscores_payload({0: 5, 1: 5})
        p["activities"] = [{"id": 0, "name": "Clue Scrolls (hard)", "rank": 10, "score": score},
                           {"id": 1, "name": "Never Played", "rank": -1, "score": 0},
                           {"id": 2, "name": "No Data", "rank": -1, "score": -1}]
        return p
    db.save_hiscores(conn, pid, payload(50), now=ts(2026, 11, 1))
    db.save_hiscores(conn, pid, payload(50), now=ts(2026, 11, 2))   # unchanged: no new row
    db.save_hiscores(conn, pid, payload(54), now=ts(2026, 11, 5, 12))
    assert conn.execute("SELECT COUNT(*) FROM activity_history WHERE name='Clue Scrolls (hard)'").fetchone()[0] == 2
    acts = db.activities(conn, pid, gains.period_starts(ts(2026, 11, 5, 13)))
    assert [a["name"] for a in acts] == ["Clue Scrolls (hard)"]
    assert acts[0]["score"] == 54 and acts[0]["gains"]["day"] == 4 and acts[0]["gains"]["month"] == 4


def test_quests_track_new_completions(conn):
    pid = db.add_player(conn, "Test")
    def payload(status):
        return {"qp": 3, "qp_max": 5, "quests": {
            "1": {"title": "Old One", "difficulty": 0, "members": False, "qp": 1, "eligible": True, "status": "COMPLETED"},
            "2": {"title": "New One", "difficulty": 4, "members": True, "qp": 2, "eligible": True, "status": status}}}
    db.save_quests(conn, pid, payload("STARTED"), now=100)
    db.save_quests(conn, pid, payload("COMPLETED"), now=200)
    q = db.quests(conn, pid)
    assert q["qp"] == 3 and q["counts"]["COMPLETED"] == 2
    assert [(r["title"], r["completed_seen_at"]) for r in q["recent"]] == [("New One", 200)]


def test_not_found_backs_off_a_day(conn, fake_api):
    responses, _, _ = fake_api
    pid = db.add_player(conn, "Ghost")
    responses.append(http_error(404))
    assert collector.poll(conn, db.get_player(conn, pid), now=500) == "not_found"
    assert db.get_player(conn, pid)["next_check_at"] == 500 + collector.NOT_FOUND_S


# --- training routes --------------------------------------------------------

from rsxp import training

METHODS = [
    {"level": 1, "name": "slow afk", "xp_hr": 10_000, "effort": "afk"},
    {"level": 1, "name": "basic", "xp_hr": 20_000, "effort": "low"},
    {"level": 50, "name": "fast", "xp_hr": 100_000, "effort": "high"},
    {"level": 70, "name": "good afk", "xp_hr": 50_000, "effort": "afk"},
]


def test_best_method_by_mode():
    assert training.best_method(METHODS, 60, "fastest")["name"] == "fast"
    assert training.best_method(METHODS, 60, "easiest")["name"] == "slow afk"
    assert training.best_method(METHODS, 80, "easiest")["name"] == "good afk"
    assert training.best_method(METHODS[2:], 10, "fastest") is None


def test_route_splits_at_unlock_levels():
    r = training.route(1, XP_TABLE[40], XP_TABLE[60], METHODS, "fastest")
    assert [(s["method"], s["from_level"], s["to_level"]) for s in r["segments"]] == [("basic", 40, 50), ("fast", 50, None)]
    first, second = r["segments"]
    assert first["xp"] == XP_TABLE[50] - XP_TABLE[40] and second["xp"] == XP_TABLE[60] - XP_TABLE[50]
    assert r["hours"] == pytest.approx(first["xp"] / 20_000 + second["xp"] / 100_000)
    easy = training.route(1, XP_TABLE[60], XP_TABLE[80], METHODS, "easiest")
    assert [s["method"] for s in easy["segments"]] == ["slow afk", "good afk"]


def test_route_done_and_invention():
    assert training.route(1, MAX_XP, MAX_XP, METHODS, "fastest") == {"segments": [], "hours": 0}
    inv = training.route(INVENTION, 0, 1_000_000, METHODS, "fastest", level=55)
    assert [s["method"] for s in inv["segments"]] == ["fast"] and inv["hours"] == pytest.approx(10)


def test_methods_file_covers_every_skill():
    from rsxp.xp import SKILLS
    doc = training.load()
    for name in SKILLS[1:]:
        methods = doc["skills"][name]
        assert training.best_method(methods, 5, "fastest"), f"{name} needs a cheap starting method with a rate"
        assert all(m["effort"] in training.EFFORT_ORDER for m in methods), name
        assert all(m.get("source", "wiki") in ("wiki", "guide", "estimate") for m in methods), name


def test_expensive_and_unrated_methods_are_skipped():
    methods = METHODS + [{"level": 1, "name": "gold sink", "xp_hr": 900_000, "effort": "low", "expensive": True},
                         {"level": 1, "name": "no rate", "xp_hr": None, "effort": "afk"}]
    assert training.best_method(methods, 10, "fastest")["name"] == "basic"
    assert training.best_method(methods, 10, "fastest", allow_expensive=True)["name"] == "gold sink"
    assert training.best_method(methods, 10, "easiest")["name"] == "slow afk"


def test_boost_multiplier():
    doc = training.load()
    assert training.multiplier("Cooking", {}, doc) == 1
    # outfit and portable only count for the skills you pick, at that skill's own rate
    b = {"avatar": 6, "outfits": ["Cooking", "Woodcutting"], "portables": ["Cooking"]}
    assert training.multiplier("Cooking", b, doc) == pytest.approx(1.06 * 1.06 * 1.21)
    assert training.multiplier("Woodcutting", b, doc) == pytest.approx(1.06 * 1.05)
    assert training.multiplier("Fishing", b, doc) == pytest.approx(1.06)
    # Attack has no outfit and Smithing no portable, so asking for them changes nothing
    assert training.multiplier("Attack", {"outfits": ["Attack"]}, doc) == 1
    assert training.multiplier("Smithing", {"portables": ["Smithing"]}, doc) == 1
    assert training.multiplier("Mining", {"double": True}, doc) == pytest.approx(2)
    r = training.route(1, 0, XP_TABLE[40], METHODS, "fastest", mult=2)   # "basic" 20k/hr, doubled
    assert r["hours"] == pytest.approx(XP_TABLE[40] / 40_000) and r["segments"][0]["xp_hr"] == 40_000


def test_boost_tables_name_real_skills():
    from rsxp.xp import SKILLS
    doc = training.load()
    for table in ("outfits", "portables"):
        for skill, v in training.per_skill(doc["boosts"][table]).items():
            assert skill in SKILLS and 0 < v["percent"] <= 25 and v["item"], (table, skill)


# --- static demo export -------------------------------------------------------

def test_export_demo_hides_real_name(conn, tmp_path):
    from rsxp import demo
    pid = db.add_player(conn, "Secret Name")
    db.save_hiscores(conn, pid, hiscores_payload({sid: 1_000_000 for sid in range(30)}), now=ts(2026, 11, 1))
    db.save_hiscores(conn, pid, hiscores_payload({sid: 1_500_000 for sid in range(30)}), now=ts(2026, 11, 4))
    n = demo.export(conn, "Secret Name", tmp_path, "Demo Player", now=ts(2026, 11, 5))
    files = list((tmp_path / "data").glob("*.json"))
    assert n == len(files) == 51
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "window.RSXP_STATIC" in html
    for f in files + [tmp_path / "index.html"]:
        assert "secret" not in f.read_text(encoding="utf-8").lower(), f.name
    summary = json.loads((tmp_path / "data" / "summary-30.json").read_text())
    assert summary["player"]["display_name"] == "Demo Player" and summary["skills"][1]["xp"] == 1_500_000


# --- docker / env settings ------------------------------------------------------

def test_seed_players_from_env(conn):
    from rsxp.server import seed_players
    assert seed_players(conn, "Zezima, mod_example ,,this name is far too long") == ["Zezima", "mod example"]
    assert seed_players(conn, "zezima") == ["Zezima"]                # already tracked: no duplicate
    assert len(db.players(conn)) == 2
    assert seed_players(conn, None) == []


def test_db_path_from_env(monkeypatch, tmp_path):
    from rsxp import __main__ as cli
    target = tmp_path / "x" / "planner.sqlite3"
    monkeypatch.setenv("RSXP_DB", str(target))
    cli.main(["add", "Zezima"])
    assert target.exists()
