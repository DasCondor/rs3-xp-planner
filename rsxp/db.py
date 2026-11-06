"""SQLite storage: players, XP snapshots, activities, quests and Adventurer's Log events."""
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .xp import SKILL_IDS, SKILLS

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "rsxp.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS players(
  id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  name_key TEXT NOT NULL UNIQUE,
  added_at INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',          -- new | ok | not_found
  last_checked_at INTEGER,
  next_check_at INTEGER NOT NULL DEFAULT 0,
  last_events_at INTEGER
);
-- One row per distinct XP state. seen_until moves forward while polls find no change,
-- so a later jump in XP is known to have happened after seen_until.
CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY,
  player_id INTEGER NOT NULL REFERENCES players(id),
  taken_at INTEGER NOT NULL,
  seen_until INTEGER NOT NULL,
  source TEXT NOT NULL                          -- hiscores, or 'runemetrics' for monthly
                                                -- history imported before RuneMetrics closed
);
CREATE INDEX IF NOT EXISTS snapshots_player_time ON snapshots(player_id, taken_at);
CREATE TABLE IF NOT EXISTS snapshot_skills(
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  skill_id INTEGER NOT NULL,                    -- Index Lite id, 0 = Overall
  xp INTEGER NOT NULL,                          -- -1 = no data
  level INTEGER,
  rank INTEGER,
  PRIMARY KEY(snapshot_id, skill_id)
);
-- Hiscores activities (clues, RuneScore, minigames...). Activity ids aren't stable, so
-- they're keyed by name. A row is added only when the score changes.
CREATE TABLE IF NOT EXISTS activity_history(
  player_id INTEGER NOT NULL REFERENCES players(id),
  name TEXT NOT NULL,
  taken_at INTEGER NOT NULL,
  score INTEGER NOT NULL,
  rank INTEGER,
  PRIMARY KEY(player_id, name, taken_at)
);
CREATE TABLE IF NOT EXISTS quests(
  player_id INTEGER NOT NULL REFERENCES players(id),
  quest_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  difficulty INTEGER,
  members INTEGER,
  qp INTEGER,
  eligible INTEGER,
  status TEXT NOT NULL,
  completed_seen_at INTEGER,                    -- first poll that saw it COMPLETED (NULL if done before tracking)
  PRIMARY KEY(player_id, quest_id)
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  player_id INTEGER NOT NULL REFERENCES players(id),
  date INTEGER NOT NULL,
  type TEXT NOT NULL,
  text TEXT NOT NULL,
  details TEXT NOT NULL,
  UNIQUE(player_id, date, type, text)
);
"""

NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,12}$")


def _int(v) -> int | None:
    """Numbers from the API, checked: anything that isn't a whole number is stored as NULL,
    so a malformed answer can never put text where the page expects a number."""
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _text(v, limit: int = 500) -> str:
    return str(v)[:limit] if v is not None else ""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(players)")}
    for col in ("qp", "qp_max", "last_quests_at", "history_at", "history_error"):
        if col not in have:
            conn.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER")
    conn.commit()
    return conn


def name_key(name: str) -> str:
    """Names are case-insensitive and treat spaces and underscores as equal."""
    return re.sub(r"[ _]+", " ", name.strip()).lower()


def add_player(conn, name: str, now: int | None = None) -> int:
    name = re.sub(r"[ _]+", " ", name.strip())
    if not NAME_RE.match(name):
        raise ValueError("Display names are 1-12 letters, numbers, spaces, hyphens or underscores.")
    row = conn.execute("SELECT id FROM players WHERE name_key=?", (name_key(name),)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO players(display_name, name_key, added_at) VALUES (?,?,?)",
                       (name, name_key(name), now or int(time.time())))
    conn.commit()
    return cur.lastrowid


def get_player(conn, ident) -> sqlite3.Row | None:
    if isinstance(ident, int) or str(ident).isdigit():
        return conn.execute("SELECT * FROM players WHERE id=?", (int(ident),)).fetchone()
    return conn.execute("SELECT * FROM players WHERE name_key=?", (name_key(ident),)).fetchone()


def players(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM players ORDER BY display_name").fetchall()


def save_hiscores(conn, pid: int, data: dict, now: int | None = None) -> bool:
    """Store an Index Lite response. Returns True if XP changed (new snapshot row)."""
    now = now or int(time.time())
    save_activities(conn, pid, data.get("activities", []), now)
    skills = {s["id"]: {"xp": s["xp"], "level": _int(s.get("level")), "rank": _int(s.get("rank"))}
              for s in data["skills"]
              if _int(s.get("id")) is not None and 0 <= s["id"] < len(SKILLS) and _int(s.get("xp")) is not None}
    if not skills:
        raise ValueError("Hiscores answer has no skills")
    latest = conn.execute(
        "SELECT id FROM snapshots WHERE player_id=? AND source='hiscores' "
        "ORDER BY taken_at DESC, id DESC LIMIT 1", (pid,)).fetchone()
    if latest:
        old = {r["skill_id"]: r["xp"] for r in conn.execute(
            "SELECT skill_id, xp FROM snapshot_skills WHERE snapshot_id=?", (latest["id"],))}
        if old == {sid: s["xp"] for sid, s in skills.items()}:
            conn.execute("UPDATE snapshots SET seen_until=? WHERE id=?", (now, latest["id"]))
            conn.executemany("UPDATE snapshot_skills SET rank=?, level=? WHERE snapshot_id=? AND skill_id=?",
                             [(s["rank"], s["level"], latest["id"], sid) for sid, s in skills.items()])
            conn.commit()
            return False
    cur = conn.execute("INSERT INTO snapshots(player_id, taken_at, seen_until, source) VALUES (?,?,?,'hiscores')",
                       (pid, now, now))
    conn.executemany("INSERT INTO snapshot_skills VALUES (?,?,?,?,?)",
                     [(cur.lastrowid, sid, s["xp"], s["level"], s["rank"]) for sid, s in skills.items()])
    conn.commit()
    return True


def save_activities(conn, pid: int, activities: list[dict], now: int) -> None:
    latest = {r["name"]: r for r in conn.execute(
        "SELECT a.* FROM activity_history a JOIN (SELECT name, MAX(taken_at) t FROM activity_history "
        "WHERE player_id=? GROUP BY name) m ON a.name=m.name AND a.taken_at=m.t WHERE a.player_id=?",
        (pid, pid))}
    for a in activities:
        if _int(a.get("score")) is None or a["score"] < 0 or not isinstance(a.get("name"), str):
            continue                # -1 = no data for this record
        a = {"name": _text(a["name"], 100), "score": a["score"], "rank": _int(a.get("rank"))}
        old = latest.get(a["name"])
        if old and old["score"] == a["score"]:
            if old["rank"] != a["rank"]:
                conn.execute("UPDATE activity_history SET rank=? WHERE player_id=? AND name=? AND taken_at=?",
                             (a["rank"], pid, a["name"], old["taken_at"]))
            continue
        conn.execute("INSERT OR REPLACE INTO activity_history VALUES (?,?,?,?,?)",
                     (pid, a["name"], now, a["score"], a["rank"]))


def activities(conn, pid: int, starts: dict[str, int]) -> list[dict]:
    """Current score and rank per activity, plus score gained since each period start.
    Activities that have never had a score or rank are left out."""
    hist = {}
    for r in conn.execute("SELECT name, taken_at, score, rank FROM activity_history "
                          "WHERE player_id=? ORDER BY taken_at", (pid,)):
        hist.setdefault(r["name"], []).append(r)
    out = []
    for name, rows in hist.items():
        cur = rows[-1]
        if cur["score"] <= 0 and (cur["rank"] or -1) <= 0:
            continue
        gains = {}
        for p, s in starts.items():
            before = [r for r in rows if r["taken_at"] <= s]
            gains[p] = cur["score"] - (before[-1] if before else rows[0])["score"]
        out.append({"name": name, "score": cur["score"], "rank": cur["rank"],
                    "since": rows[0]["taken_at"], "gains": gains})
    return sorted(out, key=lambda a: a["name"])


def save_quests(conn, pid: int, data: dict, now: int | None = None) -> None:
    now = now or int(time.time())
    first_import = conn.execute("SELECT COUNT(*) FROM quests WHERE player_id=?", (pid,)).fetchone()[0] == 0
    old = {r["quest_id"]: r for r in conn.execute("SELECT * FROM quests WHERE player_id=?", (pid,))}
    for qid, q in data.get("quests", {}).items():
        qid = int(qid)
        prev = old.get(qid)
        seen = prev["completed_seen_at"] if prev else None
        if q["status"] == "COMPLETED" and seen is None and not first_import and \
                (prev is None or prev["status"] != "COMPLETED"):
            seen = now
        conn.execute("INSERT OR REPLACE INTO quests VALUES (?,?,?,?,?,?,?,?,?)",
                     (pid, qid, _text(q["title"], 200), _int(q.get("difficulty")), int(bool(q.get("members"))),
                      _int(q.get("qp")), int(bool(q.get("eligible"))), _text(q["status"], 20), seen))
    conn.execute("UPDATE players SET qp=?, qp_max=? WHERE id=?", (_int(data.get("qp")), _int(data.get("qp_max")), pid))
    conn.commit()


def quests(conn, pid: int) -> dict:
    p = conn.execute("SELECT qp, qp_max, last_quests_at FROM players WHERE id=?", (pid,)).fetchone()
    rows = [dict(r) for r in conn.execute(
        "SELECT quest_id, title, difficulty, members, qp, eligible, status, completed_seen_at "
        "FROM quests WHERE player_id=? ORDER BY title", (pid,))]
    counts = {s: sum(r["status"] == s for r in rows) for s in ("COMPLETED", "STARTED", "NOT_STARTED")}
    return {"qp": p["qp"], "qp_max": p["qp_max"], "checked_at": p["last_quests_at"],
            "counts": counts, "total": len(rows),
            "open": [r for r in rows if r["status"] != "COMPLETED"],
            "recent": sorted((r for r in rows if r["completed_seen_at"]),
                             key=lambda r: -r["completed_seen_at"])[:10]}


def latest_skills(conn, pid: int) -> dict[int, sqlite3.Row]:
    snap = conn.execute(
        "SELECT id FROM snapshots WHERE player_id=? AND source='hiscores' "
        "ORDER BY taken_at DESC, id DESC LIMIT 1", (pid,)).fetchone()
    if not snap:
        return {}
    return {r["skill_id"]: r for r in conn.execute(
        "SELECT * FROM snapshot_skills WHERE snapshot_id=?", (snap["id"],))}


def save_events(conn, pid: int, data: dict) -> int:
    rows = []
    for e in data.get("events", []):
        ts = int(datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp())
        rows.append((pid, ts, _text(e.get("type"), 100), _text(e.get("text")), _text(e.get("details"), 1000)))
    cur = conn.executemany(
        "INSERT OR IGNORE INTO events(player_id, date, type, text, details) VALUES (?,?,?,?,?)", rows)
    conn.commit()
    return cur.rowcount


def save_monthly(conn, pid: int, entries: dict[int, dict]) -> int:
    """Turn RuneMetrics xp-monthly answers ({skill id: monthlyXpGain[0]}) into cumulative-XP
    snapshots at each month start: XP at month m = current total - gains of months >= m.
    Replaces any earlier import. Returns snapshots written."""
    per_skill, fetched_at = {}, 0
    for sid, entry in entries.items():
        gains = {}
        for m in entry.get("monthData", []):
            ts = int(m["timestamp"] / 1000)
            fetched_at = max(fetched_at, ts)
            d = datetime.fromtimestamp(ts, tz=timezone.utc)
            month = int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp())
            gains[month] = gains.get(month, 0) + int(m["xpGain"])
        per_skill[sid] = (int(entry["totalXp"]), gains)
    points = {fetched_at: {sid: total for sid, (total, _) in per_skill.items()}} if fetched_at else {}
    for m in sorted({m for _, g in per_skill.values() for m in g}):
        points[m] = {sid: total - sum(v for k, v in g.items() if k >= m) for sid, (total, g) in per_skill.items()}
    if len(points) < 2:
        return 0   # no months came back: keep whatever was imported before

    conn.execute("DELETE FROM snapshot_skills WHERE snapshot_id IN "
                 "(SELECT id FROM snapshots WHERE player_id=? AND source='runemetrics')", (pid,))
    conn.execute("DELETE FROM snapshots WHERE player_id=? AND source='runemetrics'", (pid,))
    for ts, xps in sorted(points.items()):
        cur = conn.execute("INSERT INTO snapshots(player_id, taken_at, seen_until, source) "
                           "VALUES (?,?,?,'runemetrics')", (pid, ts, ts))
        rows = [(cur.lastrowid, sid, xp, None, None) for sid, xp in xps.items()]
        rows.append((cur.lastrowid, 0, sum(xps.values()), None, None))
        conn.executemany("INSERT INTO snapshot_skills VALUES (?,?,?,?,?)", rows)
    conn.commit()
    return len(points)
