"""Local API + dashboard. The collector runs in a background thread while this is up."""
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import collector, db, gains, planner, training
from .xp import SKILLS, TARGETS

STATIC = Path(__file__).resolve().parent / "static"
STATIC_ASSETS = {"app.css", "app.js", "theme.js", "logo.svg"}
# Revalidate every load (cheap, via ETag) so an updated app.js never runs against a stale cache.
NO_CACHE = {"Cache-Control": "no-cache"}
# Sent with every response: no framing, no MIME sniffing, no referrer leaks.
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                    "Content-Security-Policy": "frame-ancestors 'none'", "Referrer-Policy": "no-referrer"}
# Names this server answers to. Anything else is refused, which blocks DNS-rebinding attacks
# (a web page pointing its own domain at 127.0.0.1 to read or drive this API).
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_PLAYERS = int(os.environ.get("RSXP_MAX_PLAYERS") or 25)   # each one costs Jagex an hourly request


def allowed_hosts() -> set[str]:
    """LOCAL_HOSTS plus RSXP_HOSTS (comma-separated), for opening the page from other devices."""
    extra = {h.strip().lower().strip("[]") for h in os.environ.get("RSXP_HOSTS", "").split(",") if h.strip()}
    return LOCAL_HOSTS | extra


class NewPlayer(BaseModel):
    name: str = Field(max_length=40)


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

    # No /docs or /openapi.json: nothing here needs them, and they'd pull scripts from a CDN.
    app = FastAPI(title="RS3 XP Planner", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    hosts = allowed_hosts()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if urlsplit("//" + host).hostname not in hosts:
            return PlainTextResponse("Unknown host. Add it to RSXP_HOSTS to allow it.", 421)
        # Changes only from this page: a cross-site form or script can't add players or poll Jagex.
        if request.method not in ("GET", "HEAD") and request.headers.get("origin") not in (None, f"http://{host}", f"https://{host}"):
            return PlainTextResponse("Cross-site request refused.", 403)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    def open_db():
        return closing(db.connect(db_path))

    def player_or_404(conn, pid):
        p = db.get_player(conn, pid)
        if not p:
            raise HTTPException(404, "No such player")
        return p

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers=NO_CACHE)

    @app.get("/{asset}", include_in_schema=False)
    def asset(asset: str):
        # Only the page's own files; nothing else in the package is reachable.
        if asset not in STATIC_ASSETS:
            raise HTTPException(404, "Not found")
        return FileResponse(STATIC / asset, headers=NO_CACHE)

    @app.get("/api/players")
    def list_players():
        with open_db() as conn:
            return [dict(p) for p in db.players(conn)]

    @app.post("/api/players")
    def add_player(body: NewPlayer):
        with open_db() as conn:
            if not db.get_player(conn, body.name) and len(db.players(conn)) >= MAX_PLAYERS:
                raise HTTPException(409, f"Already tracking {MAX_PLAYERS} players (RSXP_MAX_PLAYERS).")
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
                    "monthly": gains.monthly(pts, int(time.time())), "daily": gains.daily(pts, int(time.time()))}

    @app.get("/api/training/creatures")
    def training_creatures():
        c = training.load()["creatures"]
        return {"skills": c["skills"], "list": c["list"]}

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
                                "ORDER BY date DESC LIMIT ?", (pid, max(1, min(limit, 200)))).fetchall()
            return [dict(r) for r in rows]

    return app
