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
    assert virtual_level(1, MAX_XP) == 120 and virtual_level(1, 104_273_167) == 120
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


def test_pull_history_once_from_monthly_gains(conn, monkeypatch):
    pid = db.add_player(conn, "Test")
    conn.execute("UPDATE players SET status='ok' WHERE id=?", (pid,))
    asked = []
    def xp_monthly(name, skill):
        asked.append(skill)
        months = [{"xpGain": 1000, "timestamp": ts(2026, 10, 5) * 1000},
                  {"xpGain": 500, "timestamp": ts(2026, 11, 3) * 1000}]
        return {"monthlyXpGain": [{"totalXp": 10_000, "monthData": months}]}
    monkeypatch.setattr(client, "xp_monthly", xp_monthly)
    monkeypatch.setattr(collector, "poll", lambda *a, **k: "ok")
    profile = {"error": "PROFILE_PRIVATE"}
    monkeypatch.setattr(client, "profile", lambda name: profile)
    collector.run_due(conn, ts(2026, 11, 6))           # private: nothing fetched, noted for the page
    assert asked == [] and db.get_player(conn, pid)["history_error"] == "private"
    collector.run_due(conn, ts(2026, 11, 6, 0, 30))    # not retried within the hour
    profile = {"name": "Test"}                         # made public
    collector.run_due(conn, ts(2026, 11, 6, 1, 1))
    collector.run_due(conn, ts(2026, 11, 6, 2))       # already pulled
    assert asked == [sid - 1 for sid in SKILL_IDS] and db.get_player(conn, pid)["history_error"] is None
    rows = dict(conn.execute("SELECT s.taken_at, k.xp FROM snapshots s JOIN snapshot_skills k "
                             "ON k.snapshot_id=s.id WHERE k.skill_id=1").fetchall())
    assert rows == {ts(2026, 10, 1): 8_500, ts(2026, 11, 1): 9_500, ts(2026, 11, 3): 10_000}
    # an empty answer later must not wipe what was imported
    assert db.save_monthly(conn, pid, {1: {"totalXp": 10_000, "monthData": []}}) == 0
    assert conn.execute("SELECT COUNT(*) FROM snapshots WHERE source='runemetrics'").fetchone()[0] == 3


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
        for skill, v in doc["boosts"][table].items():
            assert skill in SKILLS and 0 < v["percent"] <= 25 and v["item"], (table, skill)


# --- static demo export -------------------------------------------------------

def test_demo_fixture_round_trip_and_site(conn, tmp_path):
    from rsxp import demo
    pid = db.add_player(conn, "Secret Name")
    db.save_hiscores(conn, pid, hiscores_payload({sid: 1_000_000 for sid in range(30)}), now=ts(2026, 11, 1))
    db.save_hiscores(conn, pid, hiscores_payload({sid: 1_500_000 for sid in range(30)}), now=ts(2026, 11, 4))
    db.save_events(conn, pid, {"events": [{"date": "2026-11-02T10:00:00Z", "type": "rs_plog_xp_milestone",
                                           "text": "2000000XP in Attack", "details": ""}]})
    db.save_quests(conn, pid, {"qp": 3, "qp_max": 5, "quests": {"1": {"title": "Cold Front", "difficulty": 0,
        "members": False, "qp": 1, "eligible": True, "status": "COMPLETED"}}}, now=ts(2026, 11, 4))

    other = db.add_player(conn, "Other Secret")
    db.save_hiscores(conn, other, hiscores_payload({sid: 2_000 for sid in range(30)}), now=ts(2026, 11, 3))

    fx = tmp_path / "fixture.json"
    demo.dump_fixture(conn, ["Secret Name", "Other Secret"], fx)
    assert "secret" not in fx.read_text(encoding="utf-8").lower()

    # the fixture rebuilds the same numbers as the real database
    conn2, now = demo.load_fixture(fx)
    assert [p["display_name"] for p in db.players(conn2)] == ["Demo Player 1", "Demo Player 2"]
    assert now == ts(2026, 11, 4)
    pid2 = db.get_player(conn2, "Demo Player 1")["id"]
    a = planner.summary(conn, pid, now, 30)["skills"]
    b = planner.summary(conn2, pid2, now, 30)["skills"]
    assert [(s["xp"], s["gains"]) for s in a] == [(s["xp"], s["gains"]) for s in b]

    site = tmp_path / "site"
    n = demo.build_site(conn2, site, now=now)
    assert n == len(list((site / "data").glob("*.json"))) == 3 + 2 * 49   # shared + per player
    assert json.loads((site / "data" / "p2-summary-30.json").read_text())["skills"][1]["xp"] == 2_000
    for asset in ("index.html", "app.css", "app.js", "theme.js", "logo.svg"):
        assert (site / asset).exists(), asset
    assert '<script src="config.js">' in (site / "index.html").read_text(encoding="utf-8")
    assert "window.RSXP_STATIC" in (site / "config.js").read_text(encoding="utf-8")
    for f in site.rglob("*.*"):
        assert "secret" not in f.read_text(encoding="utf-8").lower(), f.name


def test_server_serves_only_page_assets():
    from fastapi.testclient import TestClient
    from rsxp.server import create_app
    c = TestClient(create_app(":memory:", collect=False), base_url="http://127.0.0.1:8765")
    assert c.get("/").status_code == 200
    for asset in ("", "app.css", "app.js", "theme.js", "logo.svg"):
        r = c.get(f"/{asset}")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", asset
    assert c.get("/server.py").status_code == 404
    assert c.get("/methods.json").status_code == 404
    # scripts only from this server and the one pinned Chart.js file: no inline code, no other CDN files
    import re
    script_src = re.search(r"script-src ([^;]*);", c.get("/").text).group(1).split()
    assert script_src == ["'self'", "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"]


def test_server_refuses_other_hosts_and_cross_site_changes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from rsxp import server
    monkeypatch.setattr(collector, "poll", lambda conn, p, *a, **k: "ok")
    monkeypatch.setattr(server, "MAX_PLAYERS", 2)
    app = server.create_app(tmp_path / "t.sqlite3", collect=False)
    local = TestClient(app, base_url="http://127.0.0.1:8765")
    r = local.get("/api/players")
    assert r.status_code == 200 and r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    # DNS rebinding: an outside name pointed at this machine
    assert TestClient(app, base_url="http://evil.example").get("/api/players").status_code == 421
    # cross-site POST (CSRF) is refused; the page's own origin and tools without Origin are fine
    assert local.post("/api/players/1/refresh", headers={"Origin": "http://evil.example"}).status_code == 403
    assert local.post("/api/players", json={"name": "Zezima"},
                      headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200
    assert local.post("/api/players", json={"name": "Second"}).status_code == 200
    assert local.post("/api/players", json={"name": "Third"}).status_code == 409   # cap
    assert local.post("/api/players", json={"name": "zezima"}).status_code == 200  # already tracked: fine
    assert local.post("/api/players", json={"name": "x" * 41}).status_code == 422
    assert local.get("/docs").status_code == 404 and local.get("/openapi.json").status_code == 404
    monkeypatch.setenv("RSXP_HOSTS", "mypc.lan")
    lan = TestClient(server.create_app(tmp_path / "t.sqlite3", collect=False), base_url="http://mypc.lan:8765")
    assert lan.get("/api/players").status_code == 200


def test_bad_api_answers_are_stored_safely_and_not_retried_every_minute(conn, monkeypatch):
    pid = db.add_player(conn, "Test")
    # junk where numbers belong is dropped, never stored as text
    db.save_quests(conn, pid, {"qp": "<img src=x onerror=alert(1)>", "qp_max": 5, "quests": {"1": {
        "title": "<b>Quest</b>", "difficulty": "x", "members": True, "qp": "1; DROP", "eligible": True,
        "status": "COMPLETED"}}}, now=ts(2026, 11, 4))
    q = db.quests(conn, pid)
    assert q["qp"] is None and q["qp_max"] == 5 and q["recent"] == [] and q["total"] == 1
    assert conn.execute("SELECT qp, difficulty FROM quests").fetchone()[:] == (None, None)
    payload = hiscores_payload({0: 100, 1: 100})
    payload["skills"] += [{"id": 999, "xp": 5, "level": 1, "rank": 1}, {"id": 2, "xp": "lots", "level": 1, "rank": 1}]
    payload["skills"][1]["level"] = "<script>"
    payload["activities"] = [{"name": "Clues", "score": "9", "rank": 1}, {"name": "RuneScore", "score": 10, "rank": "x"}]
    db.save_hiscores(conn, pid, payload, now=ts(2026, 11, 4))
    assert {sid: (r["xp"], r["level"]) for sid, r in db.latest_skills(conn, pid).items()} == {0: (100, 99), 1: (100, None)}
    assert [(a["name"], a["score"], a["rank"]) for a in db.activities(conn, pid, {})] == [("RuneScore", 10, None)]
    with pytest.raises(ValueError):
        db.save_hiscores(conn, pid, {"skills": [{"id": "0", "xp": 1}]}, now=ts(2026, 11, 5))

    # an answer the code can't read: logged, and the player waits the normal hour
    conn.execute("UPDATE players SET status='ok', next_check_at=0, history_at=1 WHERE id=?", (pid,))
    conn.commit()
    calls = []
    monkeypatch.setattr(client, "hiscores", lambda name: calls.append(name) or {"skills": "nonsense"})
    now = ts(2026, 11, 6)
    collector.run_due(conn, now)
    collector.run_due(conn, now + 60)
    assert calls == ["Test"] and db.get_player(conn, pid)["next_check_at"] == now + collector.POLL_S

    # same for the history import: one try, then hourly, not every tick
    conn.execute("UPDATE players SET history_at=NULL WHERE id=?", (pid,))
    conn.commit()
    monkeypatch.setattr(client, "profile", lambda name: {})
    monkeypatch.setattr(client, "xp_monthly", lambda name, skill: calls.append(skill) or
                        {"monthlyXpGain": [{"totalXp": "x", "monthData": [{"timestamp": 1, "xpGain": None}]}]})
    collector.run_due(conn, now + 120)
    collector.run_due(conn, now + 180)
    assert calls.count(0) == 1 and db.get_player(conn, pid)["history_at"] == now + 120
    # an error nobody planned for: caught by the collector, marked failed, retried hourly
    conn.execute("UPDATE players SET history_at=NULL WHERE id=?", (pid,))
    conn.commit()
    monkeypatch.setattr(client, "xp_monthly", lambda name, skill: calls.append(skill) or
                        {"monthlyXpGain": [{"totalXp": "x", "monthData": [{"timestamp": 1000, "xpGain": 1}]}]})
    collector.run_due(conn, now + 240)
    collector.run_due(conn, now + 300)
    assert calls.count(0) == 2 and db.get_player(conn, pid)["history_error"] == "failed"
    collector.run_due(conn, now + 240 + collector.POLL_S + 1)
    assert calls.count(0) == 3


def test_client_refuses_oversized_answers(monkeypatch):
    big = io.BytesIO(b"[" + b"0," * client.MAX_BODY + b"0]")
    class Resp:
        def __enter__(self): return big
        def __exit__(self, *a): return False
    monkeypatch.setattr(client._opener, "open", lambda req, timeout: Resp())
    monkeypatch.setattr(client, "_last_call", 0.0)
    monkeypatch.setattr(client, "_paused_until", 0.0)
    with pytest.raises(client.Unavailable, match="over"):
        client.get_json("https://example.invalid/")


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


def test_slayer_creatures_need_the_slayer_level():
    doc = {"boosts": {"outfits": {}, "portables": {}, "double": {"percent": 100}}, "skills": {"Attack": [
        {"level": 1, "name": "basic", "xp_hr": 40_000, "effort": "low"},
        {"level": 1, "name": "scorpions", "xp_hr": 500_000, "effort": "afk", "slayer": 88},
        {"level": 1, "name": "demons", "xp_hr": None, "effort": "low", "slayer": 85}]}}
    def attack_plan(slayer_level):
        latest = {1: {"xp": 0, "level": 1}, 19: {"xp": 0, "level": slayer_level}}
        return next(s for s in training.plan(latest, "99", "fastest", doc=doc)["skills"] if s["name"] == "Attack")
    low = attack_plan(80)
    assert low["now"]["name"] == "basic"
    assert {o["name"]: o.get("locked") for o in low["other_methods"]} == {
        "scorpions": "needs 88 Slayer (you have 80)", "demons": "needs 85 Slayer (you have 80)"}
    high = attack_plan(90)
    assert high["now"]["name"] == "scorpions" and high["segments"][0]["requires"] == "88 Slayer"
    assert [o["name"] for o in high["other_methods"]] == ["demons"]          # no rate, still listed


def test_compact_rows_expand():
    doc = training.load()
    m = next(m for m in doc["skills"]["Attack"] if m["name"].startswith("Corrupted scorpions"))
    assert m == {"level": 80, "name": "Corrupted scorpions (Sophanem)", "xp_hr": 500000, "effort": "afk",
                 "slayer": 88, "requires": "tier 90 gear"}
    assert doc["boosts"]["portables"]["Cooking"] == {"item": "Portable range", "percent": 21}


def test_creatures_table():
    c = training.load()["creatures"]
    assert {"Attack", "Ranged", "Magic", "Necromancy", "Hitpoints"} <= set(c["skills"])
    strigoi = next(x for x in c["list"] if x["name"] == "Ravenous strigoi")
    assert strigoi == {"level": 90, "name": "Ravenous strigoi", "kill": 5160, "slayer": 104,
                       "requires": "Heralds of Crimson (partial)"}
    assert all(x["kill"] > 0 and x["name"] for x in c["list"])
