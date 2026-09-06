"""Donation advisor.

Major NPCs level up by receiving a specific *type* of cat. Each day the
player decides which cats to send. This module inspects the live roster and,
for every donation NPC, lists the cats that currently qualify — ranked from
"give away first" to "keep" — so the daily cull is a few clicks instead of
manual scanning.

Detection uses what the save exposes:

  * Tink          — kittens: age 1 (born that day)
  * Tracy         — seniors: age >= 5
  * Dr. Beanies   — cats carrying mutations, birth defects, disorders
                    (parasites are not parsed from the save yet)
  * Frank         — retired: survived an adventure (MBM's heuristic: strong
                    ability count + stat growth from level-ups)

Not yet decodable from the save: injuries (Baby Jack), chapter progress
(Butch), parasite data, and per-NPC donation counters. Those NPCs are listed
with "unsupported" so the tab stays honest about coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from mewgenics_overlay.core.recommend import W_AVG, W_RISK, W_SEVENS
from mewgenics_overlay.core.session import display_location
from mewgenics_overlay.core.stimulation import STIMULATION_DEFAULT
from mewgenics_overlay.vendor.breeding import pair_projection

# Adult/breedable threshold: 1-day-olds (and younger) can't breed yet.
KITTEN_MAX_AGE = 1
TINK_MAX_AGE = 1          # 1-day-old kittens (Tink)
TRACY_MIN_AGE = 5         # minimum age Tracy accepts


# breeding-value signal: a cat is a 'keeper' when its best pairing score
# beats this percentile of the whole roster
KEEPER_PERCENTILE = 0.75

# give-away ranking weights (higher score = donate later)
W_INBRED = 20.0
W_AGE = 0.5
W_CONDITION = 3.0
W_LOVER_KEEP = 6.0
# retired (Frank) detection: abilities gained + stat growth
ADV_ABILITIES_MIN = 3
# Organ Grinder: only list deaths recent enough to plausibly still
# await collection (the game keeps tombstones for the whole history)
DEAD_RECENT_DAYS = 3


@dataclass(frozen=True)
class NpcProfile:
    """What a donation NPC wants, when they unlock, and their save-token stem."""
    wants: str
    unlock_note: str
    slug: str


NPC_PROFILES: dict = {
    "Tink": NpcProfile("1-day-old kittens",
                       "unlocked after the tutorial", "tink"),
    "Dr. Beanies": NpcProfile("cats with mutations, birth defects, disorders",
                              "unlocked after Caves + Boneyard", "beanies"),
    "Frank": NpcProfile("cats that survived an adventure",
                        "unlocked after Alley + next day", "frank"),
    "Tracy": NpcProfile("cats aged 5 or older",
                        "unlocked after Sewers + next day", "tracy"),
    "Baby Jack": NpcProfile("cats with an injury",
                            "unlocked after getting a piece of furniture",
                            "jack"),
    "Organ Grinder": NpcProfile("cats that have died",
                                 "after losing an adventure (Frank unlocked)",
                                 "organ"),
}
NPC_ORDER = list(NPC_PROFILES)
# NPCs whose requirements the save format can't express yet.
UNSUPPORTED = [
    ("Butch", "cats that reached far chapters",
     "per-cat chapter progress is not stored in the save — only an "
     "adventure heuristic exists"),
]

def _has_any_mutation_or_condition(cat) -> bool:
    entries = getattr(cat, "visual_mutation_entries", None) or []
    if any(e and e.get("is_defect") for e in entries):
        return True
    if any(e and not e.get("is_defect") for e in entries):
        return True
    if getattr(cat, "disorders", None):
        return True
    return bool(getattr(cat, "defects", None))


def _visible_dead(cat) -> bool:
    """Only dead cats that are still present count for the Organ Grinder.
    'Gone' cats have already been donated/collected and are not visible."""
    return (getattr(cat, "status", "") or "") != "Gone"


def _recent_death(cat, current_day) -> bool:
    """True when a dead cat died within the last DEAD_RECENT_DAYS (or we have
    no current day to compare against, in which case we keep them all)."""
    death = getattr(cat, "death_day", None)
    if death is None or current_day is None:
        return True
    return 0 <= int(current_day) - int(death) <= DEAD_RECENT_DAYS


def _is_retired(cat) -> bool:
    """Likely went on an adventure: stat growth from level-ups PLUS enough
    abilities. Explicit overrides win first; 4+ (MBM strict) OR the looser
    3+ threshold both count so genuine veterans aren't missed."""
    if getattr(cat, "not_adventured_override", False):
        return False
    fn = getattr(cat, "has_adventured", None)
    if callable(fn):
        try:
            if fn():
                return True
        except Exception:
            pass
    elif fn is True:
        return True
    gains = [int(x) for x in (getattr(cat, "stat_mod", None) or [])]
    if not any(g > 0 for g in gains):
        return False
    return len(getattr(cat, "abilities", None) or []) >= ADV_ABILITIES_MIN


def _age(cat) -> Optional[int]:
    try:
        return int(getattr(cat, "age", None))
    except (TypeError, ValueError):
        return None


def cat_status(cat) -> str:
    """kitten (can't breed yet) / retired (went on an adventure) / normal."""
    age = _age(cat)
    if age is not None and age <= KITTEN_MAX_AGE:
        return "kitten"
    if _is_retired(cat):
        return "retired"
    return "normal"


def _pair_value(a, b) -> float:
    """Breeding value of pairing a with b: 7s + stats, minus a safety
    penalty for the pair's birth-defect risk (low-risk pairings are worth
    more)."""
    proj = pair_projection(a, b, stimulation=STIMULATION_DEFAULT)
    risk = 0.0
    try:
        from mewgenics_overlay.vendor.save_parser import risk_percent
        risk = float(risk_percent(a, b))
    except Exception:
        risk = 0.0
    return (W_SEVENS * proj.seven_plus_total
            + W_AVG * proj.avg_expected
            - W_RISK * risk)


def _breeding_keepers(cats) -> set:
    """Cats that are a top-tier mate for someone — donating them hurts the
    breeding pool. Kept deliberately simple: their best pairing score must
    beat the 75th percentile of the roster's scores."""
    scores: list = []
    for a in cats:
        best = 0.0
        for b in cats:
            if b is a:
                continue
            try:
                best = max(best, _pair_value(a, b))
            except Exception:
                continue
        scores.append((a, best))
    if len(scores) < 4:
        return set()
    ordered = sorted(v for _, v in scores)
    threshold = ordered[int(KEEPER_PERCENTILE * (len(ordered) - 1))]
    return {id(a) for a, v in scores if v > threshold}


@dataclass
class DonationSlot:
    """Qualified cats for one NPC, ranked worst-kept first."""

    npc: str
    wants: str
    unlock_note: str = ""
    supported: bool = True
    active: bool = True          # NPC has started taking cats (save flags)
    candidates: List[object] = field(default_factory=list)
    ranks: List[int] = field(default_factory=list)   # parallel: quality rank

    @property
    def count(self) -> int:
        return len(self.candidates)


def _injured_stat_count(cat) -> int:
    """How many stats carry a penalty (total < base). The save keeps injuries
    as stat penalties on the cat; MBM uses the same signal."""
    base = getattr(cat, "base_stats", None) or {}
    total = getattr(cat, "total_stats", None)
    if not base or total is None:
        return 0
    return sum(1 for s, v in base.items()
               if total.get(s, v) < v)


def _qualifies(cat, npc: str) -> bool:
    if npc == "Tink":
        return _age(cat) == TINK_MAX_AGE
    if npc == "Tracy":
        age = _age(cat)
        return age is not None and age >= TRACY_MIN_AGE
    if npc == "Dr. Beanies":
        return _has_any_mutation_or_condition(cat)
    if npc == "Frank":
        return _is_retired(cat)
    if npc == "Baby Jack":
        return _injured_stat_count(cat) >= 1
    if npc == "Organ Grinder":
        return bool(getattr(cat, "is_dead", False))
    return False


def _give_away_score(cat) -> float:
    """Lower = give away first. We want to donate cats we'd least miss:
    weak stats, inbred, older, carrying conditions, no lover ties."""
    base = float(sum(getattr(cat, "base_stats", {}).values()))
    score = base
    score += (getattr(cat, "inbredness", 0.0) or 0.0) * W_INBRED
    score += float(_age(cat) or 0) * W_AGE
    if getattr(cat, "defects", None) or getattr(cat, "disorders", None):
        score += W_CONDITION
    if getattr(cat, "lovers", None):
        score -= W_LOVER_KEEP      # keep cats who are in love
    if getattr(cat, "must_breed", False):
        score -= 100.0        # never recommend a marked must-breed
    if getattr(cat, "is_pinned", False):
        score -= 100.0
    return score


def donation_report(cats, active: Optional[set] = None,
                    dead: tuple = (),
                    current_day: Optional[int] = None) -> List[DonationSlot]:
    """Rank every donation NPC's qualifying cats for the current roster.

    ``cats`` are the alive cats; ``dead`` (optional) supplies the cats that
    have died, which the Organ Grinder takes. ``active`` is an optional set
    of flag names from the save's npc_progress.
    """
    flags = set(active or ())
    slots: List[DonationSlot] = []
    keepers = _breeding_keepers(cats)

    for npc in NPC_ORDER:
        profile = NPC_PROFILES[npc]
        pool = dead if npc == "Organ Grinder" else cats
        slot = DonationSlot(npc=npc, wants=profile.wants,
                            unlock_note=profile.unlock_note,
                            active=any(flag.startswith(profile.slug)
                                       for flag in flags))
        slot.candidates = [c for c in pool
                         if _qualifies(c, npc)
                         and (npc != "Organ Grinder"
                              or _recent_death(c, current_day))
                         and (npc != "Organ Grinder" or _visible_dead(c))]
        if npc != "Organ Grinder":
            for c in slot.candidates:
                if id(c) in keepers:
                    c._donate_keep_for_breeding = True
        # protect the breeding pool: cats that are a top mate for someone
        # sort below expendable cats (but above pinned/must-breed).
        def _protected(c):
            return bool(getattr(c, "must_breed", False)
                        or getattr(c, "is_pinned", False))

        def _keeper(c):
            return bool(getattr(c, "_donate_keep_for_breeding", False))
        slot.candidates.sort(key=lambda c: (int(_protected(c)),
                                            int(_keeper(c)),
                                            _give_away_score(c)))
        slot.ranks = list(range(1, len(slot.candidates) + 1))
        slots.append(slot)

    for name, wants, why in UNSUPPORTED:
        slots.append(DonationSlot(npc=name, wants=wants, supported=False,
                                   unlock_note=why))
    return slots


def recommendation_lines(cat) -> List[str]:
    """Short human lines explaining why a cat is a candidate."""
    lines: List[str] = []
    age = _age(cat)
    if age is not None and age <= TINK_MAX_AGE:
        lines.append("born today — Tink wants kittens")
    if age is not None and age >= TRACY_MIN_AGE:
        lines.append("senior cat — Tracy will take them")
    if _is_retired(cat):
        lines.append("survived an adventure — Frank will take them")
    if _has_any_mutation_or_condition(cat):
        bits = []
        if getattr(cat, "defects", None):
            bits.append("birth defects")
        if getattr(cat, "disorders", None):
            bits.append("disorders")
        if any(e and not e.get("is_defect")
               for e in (getattr(cat, "visual_mutation_entries", None) or [])):
            bits.append("mutations")
        lines.append("carries " + ", ".join(bits) + " — Dr. Beanies wants these")
    if _injured_stat_count(cat) >= 1:
        lines.append("stat penalties suggest an injury — Baby Jack will take "
                     "them")
    if getattr(cat, "_donate_keep_for_breeding", False):
        lines.append("valuable for breeding (a top mate for someone) — "
                     "donate only if you really need to")
    if getattr(cat, "is_dead", False):
        lines.append("deceased — the Organ Grinder takes the dead")
    if not lines:
        lines.append(f"currently {display_location(cat)}")
    return lines
