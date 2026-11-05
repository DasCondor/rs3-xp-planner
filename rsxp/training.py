"""Training routes: which method to use at each level, and how many hours to a goal.

fastest = highest XP/hr unlocked at each level.
easiest = the most relaxed effort available (afk, then low, then high), and the
          highest XP/hr within that effort.
Rates come from methods.json (base XP/hr, checked against the RuneScape Wiki). Boosts
multiply them; methods marked expensive are skipped unless allowed; methods with no
published rate (xp_hr null) are listed but never used for time estimates.
"""
import json
from pathlib import Path

from .xp import INVENTION, SKILLS, XP_TABLE, target_xp, virtual_level

METHODS_FILE = Path(__file__).resolve().parent / "methods.json"
MODES = ("fastest", "easiest")
EFFORT_ORDER = {"afk": 0, "low": 1, "high": 2}


def load(path: Path = METHODS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_methods(path: Path = METHODS_FILE) -> dict[str, list[dict]]:
    return load(path)["skills"]


def multiplier(skill: str, boosts: dict, doc: dict) -> float:
    """Avatar and portables multiply; Double XP doubles; outfit only for non-combat skills."""
    cfg = doc.get("boosts", {})
    mult = 1 + (boosts.get("avatar") or 0) / 100
    if boosts.get("outfit") and skill not in doc.get("combat_skills", []):
        mult *= 1 + cfg["outfit"]["percent"] / 100
    if boosts.get("portable") and skill in cfg["portable"]["applies"]:
        mult *= 1 + cfg["portable"]["percent"] / 100
    if boosts.get("double"):
        mult *= 1 + cfg["double"]["percent"] / 100
    return mult


def usable(methods: list[dict], allow_expensive: bool = False) -> list[dict]:
    return [m for m in methods if m.get("xp_hr") and (allow_expensive or not m.get("expensive"))]


def best_method(methods: list[dict], level: int, mode: str, allow_expensive: bool = False) -> dict | None:
    pool = [m for m in usable(methods, allow_expensive) if m["level"] <= level]
    if not pool:
        return None
    if mode == "fastest":
        return max(pool, key=lambda m: m["xp_hr"])
    return min(pool, key=lambda m: (EFFORT_ORDER.get(m["effort"], 3), -m["xp_hr"]))


def route(skill_id: int, xp: int, goal_xp: int, methods: list[dict], mode: str,
          level: int | None = None, mult: float = 1.0, allow_expensive: bool = False) -> dict:
    """Split [xp, goal_xp] at the levels where new methods unlock and pick the best
    method for each stretch. Invention's elite curve isn't tabulated, so it uses the
    methods available at its current level for the whole way."""
    pool = usable(methods, allow_expensive)
    segments = []
    cur = xp
    while cur < goal_xp:
        lvl = level if skill_id == INVENTION else virtual_level(skill_id, cur)
        m = best_method(pool, lvl, mode, True)
        if not m:
            return {"segments": segments, "hours": None, "missing_method_at": lvl}
        end = goal_xp
        if skill_id != INVENTION:
            unlocks = [XP_TABLE[u["level"]] for u in pool if lvl < u["level"] < len(XP_TABLE)]
            unlocks = [u for u in unlocks if cur < u < goal_xp]
            if unlocks:
                end = min(unlocks)
        gained = end - cur
        rate = m["xp_hr"] * mult
        if segments and segments[-1]["method"] == m["name"] and segments[-1]["base_xp_hr"] == m["xp_hr"]:
            seg = segments[-1]
            seg["xp"] += gained
            seg["hours"] += gained / rate
        else:
            segments.append({"method": m["name"], "effort": m["effort"], "base_xp_hr": m["xp_hr"],
                             "xp_hr": round(rate), "source": m.get("source", "wiki"),
                             "requires": m.get("requires"), "note": m.get("note"),
                             "expensive": bool(m.get("expensive")),
                             "from_level": lvl, "xp": gained, "hours": gained / rate})
        cur = end
    for seg, nxt in zip(segments, segments[1:] + [None]):
        seg["to_level"] = nxt["from_level"] if nxt else None   # None = runs to the goal
    return {"segments": segments, "hours": sum(s["hours"] for s in segments)}


def plan(latest: dict[int, dict], target: str, mode: str, boosts: dict | None = None,
         allow_expensive: bool = False, doc: dict | None = None) -> dict:
    """Routes for every skill from its current XP to `target`. `latest` maps skill id
    to a row with xp and level (Hiscores)."""
    doc = doc or load()
    boosts = boosts or {}
    skills = []
    for sid in range(1, len(SKILLS)):
        row = latest.get(sid)
        if not row or row["xp"] < 0:
            continue
        name = SKILLS[sid]
        methods = doc["skills"].get(name, [])
        mult = multiplier(name, boosts, doc)
        goal = target_xp(sid, target)
        lvl_now = row["level"] if sid == INVENTION else virtual_level(sid, row["xp"])
        r = route(sid, row["xp"], goal, methods, mode, row["level"], mult, allow_expensive)
        now = best_method(methods, lvl_now, mode, allow_expensive)
        others = [m for m in methods if not m.get("xp_hr")
                  or (m.get("expensive") and not allow_expensive)]
        skills.append({"id": sid, "name": name, "xp": row["xp"], "goal_xp": goal,
                       "remaining": max(goal - row["xp"], 0), "multiplier": round(mult, 4),
                       "now": now and {**now, "boosted_xp_hr": round(now["xp_hr"] * mult)},
                       "other_methods": others, **r})
    open_ = [s for s in skills if s["remaining"] > 0]
    return {"target": target, "mode": mode, "boosts": boosts, "allow_expensive": allow_expensive,
            "skills": skills, "skills_left": len(open_),
            "total_hours": None if any(s["hours"] is None for s in open_) else sum(s["hours"] for s in open_)}
