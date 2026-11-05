"""RS3 skill list, XP curves and level targets."""
import math

# Index Lite skill ids (0 = Overall). RuneMetrics ids are these minus 1.
SKILLS = [
    "Overall", "Attack", "Defence", "Strength", "Hitpoints", "Ranged", "Prayer", "Magic",
    "Cooking", "Woodcutting", "Fletching", "Fishing", "Firemaking", "Crafting", "Smithing",
    "Mining", "Herblore", "Agility", "Thieving", "Slayer", "Farming", "Runecraft", "Hunter",
    "Construction", "Summoning", "Dungeoneering", "Divination", "Invention", "Archaeology",
    "Necromancy",
]
SKILL_IDS = range(1, len(SKILLS))
INVENTION = 27
MAX_XP = 200_000_000
MAX_VIRTUAL = 126  # highest standard level below 200m XP

# XP_TABLE[L] = XP needed for level L on the standard curve.
XP_TABLE = [0, 0]
for _lvl in range(2, MAX_VIRTUAL + 1):
    XP_TABLE.append(sum(math.floor(n + 300 * 2 ** (n / 7)) for n in range(1, _lvl)) // 4)

TARGETS = ("99", "120", "200m")
_STANDARD = {"99": XP_TABLE[99], "120": XP_TABLE[120], "200m": MAX_XP}
_ELITE = {"99": 36_073_511, "120": 80_618_654, "200m": MAX_XP}  # Invention's elite curve


def target_xp(skill_id: int, target: str) -> int:
    return (_ELITE if skill_id == INVENTION else _STANDARD)[target]


def virtual_level(skill_id: int, xp: int, hiscore_level: int | None = None) -> int | None:
    """Level from XP, uncapped up to 126. Invention uses the elite curve, which we
    don't tabulate, so it falls back to the Hiscores level."""
    if skill_id == INVENTION or skill_id == 0 or xp < 0:
        return hiscore_level
    lvl = 1
    for lvl in range(MAX_VIRTUAL, 0, -1):
        if XP_TABLE[lvl] <= xp:
            break
    return lvl
