"""Per-skill plans toward 99 / 120 / 200m and totals across all skills."""
from . import db, gains
from .xp import SKILLS, SKILL_IDS, TARGETS, target_xp, virtual_level

RATE_WINDOWS = (7, 30, 90, 365)


def skill_targets(skill_id: int, xp: int, rate: float | None) -> dict:
    out = {}
    for t in TARGETS:
        goal = target_xp(skill_id, t)
        left = max(goal - xp, 0)
        out[t] = {
            "target_xp": goal,
            "remaining": left,
            "pct": round(min(xp / goal, 1.0) * 100, 2),
            "eta_days": 0 if left == 0 else (left / rate if rate and rate > 0 else None),
        }
    return out


def totals(skills: list[dict]) -> dict:
    """For each target: XP left across all skills, skills unfinished, and the ETA of the
    slowest unfinished skill at its current pace (None if any has no recent XP)."""
    out = {}
    for t in TARGETS:
        open_ = [s for s in skills if s["targets"] and s["targets"][t]["remaining"] > 0]
        etas = [s["targets"][t]["eta_days"] for s in open_]
        out[t] = {
            "remaining": sum(s["targets"][t]["remaining"] for s in open_),
            "skills_left": len(open_),
            "no_rate": sum(e is None for e in etas),
            "eta_days": 0 if not open_ else (None if None in etas else max(etas)),
        }
    return out


def summary(conn, pid: int, now: int, window: int = 30) -> dict:
    series = gains.load_series(conn, pid)
    latest = db.latest_skills(conn, pid)
    starts = gains.period_starts(now)
    skills = []
    for sid in [0, *SKILL_IDS]:
        row = latest.get(sid)
        pts = series.get(sid, [])
        xp = row["xp"] if row else (pts[-1][1] if pts else None)
        has_xp = xp is not None and xp >= 0
        rate = gains.rate_per_day(pts, now, window)
        skills.append({
            "id": sid,
            "name": SKILLS[sid],
            "xp": xp,
            "level": row["level"] if row else None,
            "virtual_level": virtual_level(sid, xp, row["level"] if row else None) if has_xp else None,
            "rank": row["rank"] if row else None,
            "gains": {p: gains.gain(pts, s, now)[0] for p, s in starts.items()},
            "rate_per_day": rate,
            "targets": skill_targets(sid, xp, rate) if sid and has_xp else None,
        })
    first = min((pts[0][0] for pts in series.values() if pts), default=None)
    tracked_from = conn.execute("SELECT MIN(taken_at) FROM snapshots WHERE player_id=? AND source='hiscores'",
                                (pid,)).fetchone()[0]
    return {
        # periods that start before hourly tracking lean on Adventurer's Log points: approximate
        "estimated": {p: tracked_from is None or s < tracked_from for p, s in starts.items()},
        "tracked_from": tracked_from,
        "now": now,
        "window_days": window,
        "history_starts": first,
        "period_starts": starts,
        "partial": {p: first is None or s < first for p, s in starts.items()},
        "skills": skills,
        "totals": totals([s for s in skills if s["id"]]),
    }
