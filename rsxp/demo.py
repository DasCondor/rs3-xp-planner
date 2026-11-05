"""Build the static GitHub Pages demo: the dashboard plus a JSON snapshot of one player.

    python -m rsxp export-demo "Display Name" [--out docs] [--as "Demo Player"] [--repo URL]

The page is static/index.html with window.RSXP_STATIC set, so it reads data/*.json
instead of calling the local API. Everything is computed by the same code the server
uses; only boosts are applied in the browser (see applyBoosts in index.html).
"""
import json
import time
from pathlib import Path

from . import db, gains, planner, training
from .xp import SKILLS, TARGETS

STATIC = Path(__file__).resolve().parent / "static" / "index.html"


def _write(folder: Path, name: str, data) -> None:
    (folder / f"{name}.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def export(conn, player_name: str, out: Path, display_as: str | None = "Demo Player",
           repo: str | None = None, now: int | None = None) -> int:
    p = db.get_player(conn, player_name)
    if not p:
        raise SystemExit(f"{player_name} isn't tracked. Run: python -m rsxp add \"{player_name}\"")
    pid, now = p["id"], now or int(time.time())
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for old in data_dir.glob("*.json"):
        old.unlink()

    player = dict(p)
    shown = display_as or p["display_name"]
    player.update(id=1, display_name=shown, name_key=db.name_key(shown), next_check_at=None)
    files = 0

    def write(name, payload):
        nonlocal files
        _write(data_dir, name, payload)
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
    write("boosts", {"boosts": doc["boosts"], "combat_skills": doc["combat_skills"]})
    latest = {sid: dict(r) for sid, r in db.latest_skills(conn, pid).items()}
    for target in TARGETS:
        for mode in training.MODES:
            for expensive, tag in ((False, "std"), (True, "all")):
                write(f"training-{target}-{mode}-{tag}", training.plan(latest, target, mode, {}, expensive, doc))

    config = json.dumps({"base": "data/", "repo": repo})
    html = STATIC.read_text(encoding="utf-8")
    if "</title>" not in html:
        raise SystemExit("static/index.html has no <title>; can't inject the demo config")
    html = html.replace("</title>", f" (demo)</title>\n<script>window.RSXP_STATIC = {config};</script>", 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return files
