"""XP-over-time queries. The only API called is the RuneScape Platform API.

Each skill's history is a list of (time, xp) points from up to three sources:
  * Hiscores snapshots (hourly while the planner runs): exact.
  * Adventurer's Log events: every 2M XP milestone ("70000000XP in Construction") and
    level-up ("I am now level 116") is an XP point at that moment. These reach back
    before you started tracking, but only as far as the log does, and level-ups can be
    batched, so they're treated as approximate.
  * Imported monthly history already saved in the database (month-start totals from
    RuneMetrics, saved before it closed on 10 Nov 2026). Nothing new is ever fetched
    for it; it's read from the local file, and is approximate inside each month.
XP between points is linearly interpolated.
"""
import bisect
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .xp import INVENTION, SKILLS, XP_TABLE

DAY = 86400
# Adventurer's Log uses the in-game names for these two
EVENT_ALIASES = {"Constitution": "Hitpoints", "Runecrafting": "Runecraft"}
_MILESTONE = re.compile(r"^\s*(\d+)\s*XP in (.+?)\.?\s*$", re.I)
_LEVELUP = re.compile(r"levelled my (.+?) skill, I am now level (\d+)", re.I)


def event_points(conn, pid: int) -> dict[int, list[tuple[int, int]]]:
    """XP points read from stored Adventurer's Log events."""
    index = {n: i for i, n in enumerate(SKILLS)}
    out = defaultdict(list)
    for date, text, details in conn.execute(
            "SELECT date, text, details FROM events WHERE player_id=?", (pid,)):
        m = _MILESTONE.match(text or "")
        if m:
            name, xp = m.group(2).strip(), int(m.group(1))
        else:
            m = _LEVELUP.search(details or "")
            if not m:
                continue
            name, level = m.group(1).strip(), int(m.group(2))
            if index.get(EVENT_ALIASES.get(name, name)) == INVENTION or not 1 < level < len(XP_TABLE):
                continue   # Invention uses the elite curve, which isn't tabulated
            xp = XP_TABLE[level]
        sid = index.get(EVENT_ALIASES.get(name, name))
        if sid:
            out[sid].append((date, xp))
    return out


def load_series(conn, pid: int) -> dict[int, list[tuple[int, int]]]:
    series = defaultdict(list)
    for taken, seen, sid, xp in conn.execute(
            "SELECT s.taken_at, s.seen_until, k.skill_id, k.xp FROM snapshots s "
            "JOIN snapshot_skills k ON k.snapshot_id = s.id "
            "WHERE s.player_id=? AND s.source IN ('hiscores', 'runemetrics') AND k.xp >= 0 "
            "ORDER BY s.taken_at, s.id", (pid,)):
        series[sid].append((taken, xp))
        if seen > taken:
            series[sid].append((seen, xp))
    for sid, pts in event_points(conn, pid).items():
        series[sid].extend(pts)
    for sid, pts in series.items():
        pts.sort()
        # XP never goes down. A batched level-up can be dated after a snapshot that
        # already shows more XP; drop points like that instead of drawing a dip.
        kept, top = [], -1
        for t, xp in pts:
            if xp >= top:
                kept.append((t, xp))
                top = xp
        series[sid] = kept
    return series


def xp_at(pts: list[tuple[int, int]], t: int) -> float | None:
    """XP at time t, or None if t is before the first point. After the last point
    XP is assumed unchanged."""
    if not pts or t < pts[0][0]:
        return None
    if t >= pts[-1][0]:
        return pts[-1][1]
    i = bisect.bisect_right(pts, (t, float("inf")))
    (t0, x0), (t1, x1) = pts[i - 1], pts[i]
    return x0 if t1 == t0 else x0 + (x1 - x0) * (t - t0) / (t1 - t0)


def gain(pts, start: int, end: int) -> tuple[int, bool] | tuple[None, bool]:
    """(XP gained in [start, end], partial). partial=True when history starts after
    `start`, so the figure only covers part of the window."""
    if not pts or end < pts[0][0]:
        return None, True
    partial = start < pts[0][0]
    a = xp_at(pts, max(start, pts[0][0]))
    b = xp_at(pts, end)
    return round(b - a), partial


def rate_per_day(pts, now: int, days: int) -> float | None:
    if not pts:
        return None
    start = max(now - days * DAY, pts[0][0])
    span = (now - start) / DAY
    if span < 1:
        return None
    g, _ = gain(pts, start, now)
    return g / span


def period_starts(now: int) -> dict[str, int]:
    """Calendar periods in UTC, matching RuneScape's resets: daily at 00:00 UTC,
    weekly on Wednesday 00:00 UTC."""
    d = datetime.fromtimestamp(now, tz=timezone.utc)
    day = d.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=(day.weekday() - 2) % 7)
    return {"day": int(day.timestamp()), "week": int(week.timestamp()),
            "month": int(day.replace(day=1).timestamp()),
            "year": int(day.replace(month=1, day=1).timestamp())}


def monthly(pts, now: int) -> list[dict]:
    """Calendar-month gains from the first point's month to now."""
    if not pts:
        return []
    first = datetime.fromtimestamp(pts[0][0], tz=timezone.utc)
    y, m = first.year, first.month
    out = []
    while True:
        start = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp())
        if start > now:
            break
        y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
        end = min(int(datetime(y2, m2, 1, tzinfo=timezone.utc).timestamp()), now)
        g, partial = gain(pts, start, end)
        out.append({"month": f"{y:04d}-{m:02d}", "gain": g,
                    "partial": partial or end == now})
        y, m = y2, m2
    return out


def daily(pts, now: int, days: int = 30) -> list[dict]:
    """UTC-day gains for the last `days` days that have history."""
    if not pts:
        return []
    today = int(datetime.fromtimestamp(now, tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    first_day = int(datetime.fromtimestamp(pts[0][0], tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    out = []
    for start in range(max(first_day, today - (days - 1) * DAY), today + 1, DAY):
        end = min(start + DAY, now)
        g, partial = gain(pts, start, end)
        out.append({"day": datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "gain": g, "partial": partial or end == now})
    return out


def downsample(pts, limit: int = 400) -> list[tuple[int, int]]:
    if len(pts) <= limit:
        return pts
    step = len(pts) / limit
    out = [pts[int(i * step)] for i in range(limit)]
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out
