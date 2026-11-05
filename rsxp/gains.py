"""XP-over-time queries built on snapshots.

Each skill's history is a list of (time, xp) points. XP between points is linearly
interpolated: exact for hourly snapshots, an estimate inside RuneMetrics months.
"""
import bisect
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DAY = 86400


def load_series(conn, pid: int) -> dict[int, list[tuple[int, int]]]:
    series = defaultdict(list)
    for taken, seen, sid, xp in conn.execute(
            "SELECT s.taken_at, s.seen_until, k.skill_id, k.xp FROM snapshots s "
            "JOIN snapshot_skills k ON k.snapshot_id = s.id "
            "WHERE s.player_id=? AND k.xp >= 0 ORDER BY s.taken_at, s.id", (pid,)):
        series[sid].append((taken, xp))
        if seen > taken:
            series[sid].append((seen, xp))
    for pts in series.values():
        pts.sort()
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


def downsample(pts, limit: int = 400) -> list[tuple[int, int]]:
    if len(pts) <= limit:
        return pts
    step = len(pts) / limit
    out = [pts[int(i * step)] for i in range(limit)]
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out
