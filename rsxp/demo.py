"""The GitHub Pages demo.

The repo keeps one small fixture (demo/fixture.json): the raw rows for one player, with
the name swapped out. CI turns it into the static site; nothing generated is committed.

    python -m rsxp demo-fixture "Display Name" [--as "Demo Player"]   # refresh the fixture
    python -m rsxp build-demo [--out _site]                           # build the site

The built page is static/index.html with window.RSXP_STATIC set, so it reads data/*.json
instead of calling the local API. Those files are computed by the same code the server
uses; only boosts are applied in the browser (applyBoosts in app.js).
"""
import json
import shutil
import time
from pathlib import Path

from . import db, gains, planner, training
from .xp import SKILLS, TARGETS

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = ("app.css", "app.js", "theme.js", "logo.svg")
FIXTURE = Path(__file__).resolve().parent.parent / "demo" / "fixture.json"
FIXTURE_VERSION = 1


# ---------- fixture: raw rows in, raw rows out ----------

def dump_fixture(conn, player_name: str, path: Path = FIXTURE, display_as: str = "Demo Player") -> dict:
    """Write one player's stored rows to a JSON fixture, renamed to `display_as`."""
    p = db.get_player(conn, player_name)
    if not p:
        raise SystemExit(f"{player_name} isn't tracked.")
    pid = p["id"]
    snaps = []
    for s in conn.execute("SELECT * FROM snapshots WHERE player_id=? ORDER BY taken_at, id", (pid,)):
        rows = conn.execute("SELECT skill_id, xp, level, rank FROM snapshot_skills WHERE snapshot_id=? "
                            "ORDER BY skill_id", (s["id"],)).fetchall()
        snaps.append({"taken_at": s["taken_at"], "seen_until": s["seen_until"], "source": s["source"],
                      "skills": [list(r) for r in rows]})
    now = max((s["seen_until"] for s in snaps), default=int(time.time()))
    fixture = {
        "version": FIXTURE_VERSION,
        "now": now,
        "player": {"display_name": display_as, "added_at": p["added_at"], "status": p["status"],
                   "last_checked_at": p["last_checked_at"], "last_events_at": p["last_events_at"],
                   "last_quests_at": p["last_quests_at"], "qp": p["qp"], "qp_max": p["qp_max"]},
        "snapshots": snaps,
        "events": [list(r) for r in conn.execute(
            "SELECT date, type, text, details FROM events WHERE player_id=? ORDER BY date", (pid,))],
        "activities": [list(r) for r in conn.execute(
            "SELECT name, taken_at, score, rank FROM activity_history WHERE player_id=? ORDER BY name, taken_at", (pid,))],
        "quests": [list(r) for r in conn.execute(
            "SELECT quest_id, title, difficulty, members, qp, eligible, status, completed_seen_at "
            "FROM quests WHERE player_id=? ORDER BY quest_id", (pid,))],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture, separators=(",", ":")), encoding="utf-8")
    return fixture


def load_fixture(path: Path = FIXTURE):
    """Load a fixture into an in-memory database. Returns (conn, display_name, now)."""
    f = json.loads(Path(path).read_text(encoding="utf-8"))
    if f.get("version") != FIXTURE_VERSION:
        raise SystemExit(f"{path}: unsupported fixture version {f.get('version')!r}")
    conn = db.connect(":memory:")
    p = f["player"]
    pid = db.add_player(conn, p["display_name"], now=p["added_at"])
    conn.execute("UPDATE players SET status=?, last_checked_at=?, last_events_at=?, last_quests_at=?, qp=?, qp_max=? "
                 "WHERE id=?", (p["status"], p["last_checked_at"], p["last_events_at"], p["last_quests_at"],
                                p["qp"], p["qp_max"], pid))
    for s in f["snapshots"]:
        cur = conn.execute("INSERT INTO snapshots(player_id, taken_at, seen_until, source) VALUES (?,?,?,?)",
                           (pid, s["taken_at"], s["seen_until"], s["source"]))
        conn.executemany("INSERT INTO snapshot_skills VALUES (?,?,?,?,?)",
                         [(cur.lastrowid, *row) for row in s["skills"]])
    conn.executemany("INSERT INTO events(player_id, date, type, text, details) VALUES (?,?,?,?,?)",
                     [(pid, *e) for e in f["events"]])
    conn.executemany("INSERT INTO activity_history VALUES (?,?,?,?,?)", [(pid, *a) for a in f["activities"]])
    conn.executemany("INSERT INTO quests VALUES (?,?,?,?,?,?,?,?,?)", [(pid, *q) for q in f["quests"]])
    conn.commit()
    return conn, p["display_name"], f["now"]


# ---------- site ----------

def build_site(conn, player_name: str, out: Path, repo: str | None = "auto", now: int | None = None) -> int:
    """Write the static demo for one tracked player to `out`. Returns the number of data files."""
    p = db.get_player(conn, player_name)
    if not p:
        raise SystemExit(f"{player_name} isn't tracked.")
    pid, now = p["id"], now or int(time.time())
    out = Path(out)
    data_dir = out / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)

    player = dict(p)
    player.update(id=1, next_check_at=None)
    files = 0

    def write(name, payload):
        nonlocal files
        (data_dir / f"{name}.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        files += 1

    write("players", [player])
    for w in planner.RATE_WINDOWS:
        write(f"summary-{w}", {"player": player, **planner.summary(conn, pid, now, w)})
    series = gains.load_series(conn, pid)
    for sid in range(len(SKILLS)):
        pts = series.get(sid, [])
        write(f"skill-{sid}", {"skill": SKILLS[sid], "history": gains.downsample(pts),
                               "monthly": gains.monthly(pts, now), "daily": gains.daily(pts, now)})
    write("events", [dict(r) for r in conn.execute(
        "SELECT date, type, text, details FROM events WHERE player_id=? ORDER BY date DESC LIMIT 30", (pid,))])
    write("activities", db.activities(conn, pid, gains.period_starts(now)))
    write("quests", db.quests(conn, pid))

    doc = training.load()
    write("boosts", {"boosts": doc["boosts"]})
    write("creatures", {"skills": doc["creatures"]["skills"], "list": doc["creatures"]["list"]})
    latest = {sid: dict(r) for sid, r in db.latest_skills(conn, pid).items()}
    for target in TARGETS:
        for mode in training.MODES:
            for expensive, tag in ((False, "std"), (True, "all")):
                write(f"training-{target}-{mode}-{tag}", training.plan(latest, target, mode, {}, expensive, doc))

    for name in ASSETS:
        shutil.copyfile(STATIC / name, out / name)
    config = json.dumps({"base": "data/", "repo": repo})
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    if "</title>" not in html:
        raise SystemExit("static/index.html has no <title>; can't inject the demo config")
    html = html.replace("</title>", f" (demo)</title>\n<script>window.RSXP_STATIC = {config};</script>", 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    return files
