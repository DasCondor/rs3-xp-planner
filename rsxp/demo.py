"""The GitHub Pages demo.

The repo keeps one small fixture (demo/fixture.json): the raw rows for each player, with
the names swapped for "Demo Player 1", "Demo Player 2"... CI turns it into the static site;
nothing generated is committed.

    python -m rsxp demo-fixture "Name" ["Other name" ...]   # refresh the fixture
    python -m rsxp build-demo [--out _site]                   # build the site

The built page is static/index.html with window.RSXP_STATIC set, so it reads data/*.json
instead of calling the local API (per-player files are prefixed p<id>-). Those files are computed by the same code the server
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
FIXTURE_VERSION = 2


# ---------- fixture: raw rows in, raw rows out ----------

def _dump_player(conn, p, display_as: str) -> dict:
    pid = p["id"]
    snaps = []
    for s in conn.execute("SELECT * FROM snapshots WHERE player_id=? ORDER BY taken_at, id", (pid,)):
        rows = conn.execute("SELECT skill_id, xp, level, rank FROM snapshot_skills WHERE snapshot_id=? "
                            "ORDER BY skill_id", (s["id"],)).fetchall()
        snaps.append({"taken_at": s["taken_at"], "seen_until": s["seen_until"], "source": s["source"],
                      "skills": [list(r) for r in rows]})
    return {
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


def dump_fixture(conn, names: list[str], path: Path = FIXTURE) -> dict:
    """Write the players' stored rows to a JSON fixture, renamed "Demo Player" (or
    "Demo Player 1", "Demo Player 2", ... when there are several)."""
    players = []
    for n, name in enumerate(names, 1):
        p = db.get_player(conn, name)
        if not p:
            raise SystemExit(f"{name} isn't tracked.")
        players.append(_dump_player(conn, p, f"Demo Player {n}" if len(names) > 1 else "Demo Player"))
    now = max((s["seen_until"] for p in players for s in p["snapshots"]), default=int(time.time()))
    fixture = {"version": FIXTURE_VERSION, "now": now, "players": players}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture, separators=(",", ":")), encoding="utf-8")
    return fixture


def load_fixture(path: Path = FIXTURE):
    """Load a fixture into an in-memory database. Returns (conn, now)."""
    f = json.loads(Path(path).read_text(encoding="utf-8"))
    if f.get("version") != FIXTURE_VERSION:
        raise SystemExit(f"{path}: unsupported fixture version {f.get('version')!r}")
    conn = db.connect(":memory:")
    for d in f["players"]:
        p = d["player"]
        # inserted directly: "Demo Player 1" is longer than a real RuneScape name may be
        pid = conn.execute(
            "INSERT INTO players(display_name, name_key, added_at, status, last_checked_at, last_events_at, "
            "last_quests_at, qp, qp_max) VALUES (?,?,?,?,?,?,?,?,?)",
            (p["display_name"], db.name_key(p["display_name"]), p["added_at"], p["status"], p["last_checked_at"],
             p["last_events_at"], p["last_quests_at"], p["qp"], p["qp_max"])).lastrowid
        for s in d["snapshots"]:
            cur = conn.execute("INSERT INTO snapshots(player_id, taken_at, seen_until, source) VALUES (?,?,?,?)",
                               (pid, s["taken_at"], s["seen_until"], s["source"]))
            conn.executemany("INSERT INTO snapshot_skills VALUES (?,?,?,?,?)",
                             [(cur.lastrowid, *row) for row in s["skills"]])
        conn.executemany("INSERT INTO events(player_id, date, type, text, details) VALUES (?,?,?,?,?)",
                         [(pid, *e) for e in d["events"]])
        conn.executemany("INSERT INTO activity_history VALUES (?,?,?,?,?)", [(pid, *a) for a in d["activities"]])
        conn.executemany("INSERT INTO quests VALUES (?,?,?,?,?,?,?,?,?)", [(pid, *q) for q in d["quests"]])
    conn.commit()
    return conn, f["now"]


# ---------- site ----------

def build_site(conn, out: Path, repo: str | None = "auto", now: int | None = None) -> int:
    """Write the static demo for every tracked player to `out`. Returns the number of data files."""
    now = now or int(time.time())
    out = Path(out)
    data_dir = out / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    files = 0

    def write(name, payload):
        nonlocal files
        (data_dir / f"{name}.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        files += 1

    players = [dict(p, next_check_at=None) for p in db.players(conn)]
    write("players", players)
    doc = training.load()
    write("boosts", {"boosts": doc["boosts"]})
    write("creatures", {"skills": doc["creatures"]["skills"], "list": doc["creatures"]["list"]})
    for player in players:
        pid, pre = player["id"], f"p{player['id']}-"
        for w in planner.RATE_WINDOWS:
            write(f"{pre}summary-{w}", {"player": player, **planner.summary(conn, pid, now, w)})
        series = gains.load_series(conn, pid)
        for sid in range(len(SKILLS)):
            pts = series.get(sid, [])
            write(f"{pre}skill-{sid}", {"skill": SKILLS[sid], "history": gains.downsample(pts),
                                        "monthly": gains.monthly(pts, now), "daily": gains.daily(pts, now)})
        write(f"{pre}events", [dict(r) for r in conn.execute(
            "SELECT date, type, text, details FROM events WHERE player_id=? ORDER BY date DESC LIMIT 30", (pid,))])
        write(f"{pre}activities", db.activities(conn, pid, gains.period_starts(now)))
        write(f"{pre}quests", db.quests(conn, pid))
        latest = {sid: dict(r) for sid, r in db.latest_skills(conn, pid).items()}
        for target in TARGETS:
            for mode in training.MODES:
                for expensive, tag in ((False, "std"), (True, "all")):
                    write(f"{pre}training-{target}-{mode}-{tag}",
                          training.plan(latest, target, mode, {}, expensive, doc))

    for name in ASSETS:
        shutil.copyfile(STATIC / name, out / name)
    # a file, not an inline <script>: the page's CSP only runs scripts from files
    repo = None if repo in (None, "", "none") else repo
    (out / "config.js").write_text(f"window.RSXP_STATIC = {json.dumps({'base': 'data/', 'repo': repo})};\n",
                                   encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    if "</title>" not in html:
        raise SystemExit("static/index.html has no <title>; can't inject the demo config")
    html = html.replace("</title>", ' (demo)</title>\n<script src="config.js"></script>', 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    return files
