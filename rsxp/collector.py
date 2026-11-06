"""Scheduled polling. Hiscores only change on logout or world hop, so hourly is plenty."""
import logging
import threading
import time

from . import client, db
from .xp import SKILL_IDS

POLL_S = 3600            # normal Hiscores poll interval per player
NOT_FOUND_S = 86400      # retry a missing player once a day
EVENTS_S = 1800          # Player Events: endpoint caches 10 min, we ask every 30
QUESTS_S = 6 * 3600      # Player Quests: changes rarely
REFRESH_MIN_S = 300      # "Update now" at most once per 5 minutes per player
TICK_S = 60
RUNEMETRICS_CLOSES = 1794268800   # 10 Nov 2026 00:00 UTC; no point asking after that

log = logging.getLogger("rsxp.collector")


def poll(conn, player, now: int | None = None, with_events: bool = True) -> str:
    """Fetch Hiscores (and Events if due) for one player. Returns the new status."""
    now = now or int(time.time())
    pid, name = player["id"], player["display_name"]
    try:
        data = client.hiscores(name)
    except client.NotFound:
        conn.execute("UPDATE players SET status='not_found', last_checked_at=?, next_check_at=? WHERE id=?",
                     (now, now + NOT_FOUND_S, pid))
        conn.commit()
        return "not_found"
    except client.Unavailable as exc:
        log.warning("hiscores unavailable for %s: %s", name, exc)
        conn.execute("UPDATE players SET next_check_at=? WHERE id=?",
                     (now + max(POLL_S // 4, int(client.paused_for())), pid))
        conn.commit()
        return "unavailable"

    changed = db.save_hiscores(conn, pid, data, now)
    conn.execute("UPDATE players SET status='ok', last_checked_at=?, next_check_at=? WHERE id=?",
                 (now, now + POLL_S, pid))
    conn.commit()
    log.info("%s: %s", name, "new snapshot" if changed else "no change")

    if with_events:
        _extra(conn, player, now, "last_events_at", EVENTS_S, client.events, db.save_events)
        _extra(conn, player, now, "last_quests_at", QUESTS_S, client.quests, db.save_quests)
    return "ok"


def _extra(conn, player, now, column, interval, fetch, save) -> None:
    """Adventurer's Log endpoints, each on its own slower schedule."""
    if player[column] is not None and player[column] + interval > now:
        return
    try:
        save(conn, player["id"], fetch(player["display_name"]))
    except (client.NotFound, client.NotMember):
        pass
    except client.Unavailable as exc:
        log.warning("%s unavailable for %s: %s", fetch.__name__, player["display_name"], exc)
    conn.execute(f"UPDATE players SET {column}=? WHERE id=?", (now, player["id"]))
    conn.commit()


def pull_history(conn, player, now: int | None = None) -> int:
    """Once per player: a year of monthly XP from RuneMetrics (a privacy check, then 29
    requests at the polite pace). Returns snapshots saved. Retried next tick if the API was
    down, hourly while the profile is private."""
    now = now or int(time.time())
    n, error = 0, None
    if now < RUNEMETRICS_CLOSES:
        log.info("%s: fetching a year of monthly XP from RuneMetrics", player["display_name"])
        try:
            if client.profile(player["display_name"]).get("error") == "PROFILE_PRIVATE":
                error = "private"   # asked again hourly, in case they make it public
            else:
                n = db.save_monthly(conn, player["id"], {
                    sid: client.xp_monthly(player["display_name"], sid - 1)["monthlyXpGain"][0] for sid in SKILL_IDS})
        except client.Unavailable as exc:
            log.warning("history unavailable for %s: %s", player["display_name"], exc)
            return 0
        except (client.NotFound, client.NotMember, KeyError, IndexError, TypeError):
            pass   # no RuneMetrics profile for this name: nothing to import
    conn.execute("UPDATE players SET history_at=?, history_error=? WHERE id=?", (now, error, player["id"]))
    conn.commit()
    log.info("%s: %s", player["display_name"],
             "RuneMetrics profile is private" if error else f"{n} monthly history points")
    return n


def run_due(conn, now: int | None = None) -> int:
    now = now or int(time.time())
    due = conn.execute("SELECT * FROM players WHERE next_check_at <= ? ORDER BY next_check_at",
                       (now,)).fetchall()
    for p in due:
        poll(conn, p)
    new = conn.execute("SELECT * FROM players WHERE status='ok' AND (history_at IS NULL OR "
                       "(history_error='private' AND history_at < ?)) LIMIT 1", (now - POLL_S,)).fetchone()
    if new:
        pull_history(conn, new, now)
    return len(due)


def refresh(conn, pid: int, now: int | None = None) -> dict:
    """Manual update, throttled. Returns whether a request was made."""
    now = now or int(time.time())
    p = db.get_player(conn, pid)
    wait = (p["last_checked_at"] or 0) + REFRESH_MIN_S - now
    if wait > 0:
        return {"polled": False, "retry_in": wait}
    return {"polled": True, "status": poll(conn, p, now)}


def loop(db_path, stop: threading.Event) -> None:
    conn = db.connect(db_path)
    while not stop.is_set():
        try:
            run_due(conn)
        except Exception:
            log.exception("collector tick failed")
        stop.wait(TICK_S)
    conn.close()
