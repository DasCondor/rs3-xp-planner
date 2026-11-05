"""Local API + dashboard. The collector runs in a background thread while this is up."""
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import collector, db, gains, planner, training
from .xp import SKILLS, TARGETS

STATIC = Path(__file__).resolve().parent / "static"


class NewPlayer(BaseModel):
    name: str


def seed_players(conn, names: str | None) -> list[str]:
    """Track every name in a comma-separated list (the RSXP_PLAYERS setting). Names that
    are already tracked are left alone; invalid ones are logged and skipped."""
    added = []
    for name in (names or "").split(","):
        if not name.strip():
            continue
        try:
            pid = db.add_player(conn, name)
            added.append(db.get_player(conn, pid)["display_name"])
        except ValueError as exc:
            logging.getLogger("rsxp").warning("RSXP_PLAYERS: skipping %r: %s", name.strip(), exc)
    return added


def create_app(db_path=db.DEFAULT_DB, collect: bool = True) -> FastAPI:
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        with closing(db.connect(db_path)) as conn:
            seed_players(conn, os.environ.get("RSXP_PLAYERS"))
        if collect:
            threading.Thread(target=collector.loop, args=(db_path, stop), daemon=True,
                             name="collector").start()
        yield
        stop.set()

    app = FastAPI(title="RS3 XP Planner", lifespan=lifespan)

    def open_db():
        return closing(db.connect(db_path))

    def player_or_404(conn, pid):
        p = db.get_player(conn, pid)
        if not p:
            raise HTTPException(404, "No such player")
        return p

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/players")
    def list_players():
        with open_db() as conn:
            return [dict(p) for p in db.players(conn)]

    @app.post("/api/players")
    def add_player(body: NewPlayer):
        with open_db() as conn:
            try:
                pid = db.add_player(conn, body.name)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            p = db.get_player(conn, pid)
            if p["last_checked_at"] is None:
                collector.poll(conn, p)
            return dict(db.get_player(conn, pid))

    @app.post("/api/players/{pid}/refresh")
    def refresh(pid: int):
        with open_db() as conn:
            player_or_404(conn, pid)
            result = collector.refresh(conn, pid)
            return {**result, "player": dict(db.get_player(conn, pid))}

    @app.get("/api/players/{pid}/summary")
    def summary(pid: int, window: int = 30):
        if window not in planner.RATE_WINDOWS:
            raise HTTPException(422, f"window must be one of {planner.RATE_WINDOWS}")
        with open_db() as conn:
            p = player_or_404(conn, pid)
            return {"player": dict(p), **planner.summary(conn, pid, int(time.time()), window)}

    @app.get("/api/players/{pid}/skills/{sid}")
    def skill_history(pid: int, sid: int):
        if not 0 <= sid < len(SKILLS):
            raise HTTPException(404, "No such skill")
        with open_db() as conn:
            player_or_404(conn, pid)
            pts = gains.load_series(conn, pid).get(sid, [])
            return {"skill": SKILLS[sid], "history": gains.downsample(pts),
                    "monthly": gains.monthly(pts, int(time.time()))}

    @app.get("/api/training/boosts")
    def training_boosts():
        return training.load()["boosts"]

    @app.get("/api/players/{pid}/training")
    def training_plan(pid: int, target: str = "120", mode: str = "fastest", avatar: int = 0,
                      outfits: str = "", portables: str = "", double: bool = False,
                      expensive: bool = False):
        if target not in TARGETS or mode not in training.MODES or avatar not in (0, 3, 6):
            raise HTTPException(422, f"target must be one of {TARGETS}, mode one of {training.MODES}, avatar 0, 3 or 6")
        with open_db() as conn:
            player_or_404(conn, pid)
            latest = {sid: dict(r) for sid, r in db.latest_skills(conn, pid).items()}
        names = lambda csv: [n.strip() for n in csv.split(",") if n.strip()]
        boosts = {"avatar": avatar, "outfits": names(outfits), "portables": names(portables), "double": double}
        return training.plan(latest, target, mode, boosts, expensive)

    @app.get("/api/players/{pid}/activities")
    def activities(pid: int):
        with open_db() as conn:
            player_or_404(conn, pid)
            return db.activities(conn, pid, gains.period_starts(int(time.time())))

    @app.get("/api/players/{pid}/quests")
    def quests(pid: int):
        with open_db() as conn:
            player_or_404(conn, pid)
            return db.quests(conn, pid)

    @app.get("/api/players/{pid}/events")
    def events(pid: int, limit: int = 30):
        with open_db() as conn:
            player_or_404(conn, pid)
            rows = conn.execute("SELECT date, type, text, details FROM events WHERE player_id=? "
                                "ORDER BY date DESC LIMIT ?", (pid, min(limit, 200))).fetchall()
            return [dict(r) for r in rows]

    return app
