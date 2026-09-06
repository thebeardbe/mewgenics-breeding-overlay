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

from mewgenics_overlay.core.session import display_location

NPC_ORDER = ["Tink", "Dr. Beanies", "Frank", "Tracy"]
# NPCs whose requirements the save format can't express yet.
UNSUPPORTED = [
    ("Baby Jack", "cats with injuries", "injuries are not in the save data"),
    ("Butch", "cats far from home", "chapter progress is not in the save data"),
    ("Organ Grinder", "dead cats", "collected automatically in-game"),
]

TRACY_MIN_AGE = 5     # days/years of age before Tracy will take a cat
TINK_MAX_AGE = 1


def _has_any_mutation_or_condition(cat) -> bool:
    entries = getattr(cat, "visual_mutation_entries", None) or []
    if any(e and e.get("is_defect") for e in entries):
        return True
    if any(e and not e.get("is_defect") for e in entries):
        return True
    if getattr(cat, "disorders", None):
        return True
    return bool(getattr(cat, "defects", None))


def _is_retired(cat) -> bool:
    fn = getattr(cat, "has_adventured", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            return False
    return bool(getattr(cat, "has_adventured_override", False))


def _age(cat) -> Optional[int]:
    try:
        return int(getattr(cat, "age", None))
    except (TypeError, ValueError):
        return None


@dataclass
class DonationSlot:
    """Qualified cats for one NPC, ranked worst-kept first."""

    npc: str
    wants: str
    unlock_note: str = ""
    supported: bool = True
    candidates: List[object] = field(default_factory=list)
    ranks: List[int] = field(default_factory=list)   # parallel: quality rank

    @property
    def count(self) -> int:
        return len(self.candidates)


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
    return False


def _give_away_score(cat) -> float:
    """Lower = give away first. We want to donate cats we'd least miss:
    weak stats, inbred, older, carrying conditions, no lover ties."""
    base = float(sum(getattr(cat, "base_stats", {}).values()))
    score = base
    score += (getattr(cat, "inbredness", 0.0) or 0.0) * 20.0
    score += float(_age(cat) or 0) * 0.5
    if getattr(cat, "defects", None) or getattr(cat, "disorders", None):
        score += 3.0
    if getattr(cat, "lovers", None):
        score -= 6.0          # keep cats who are in love
    if getattr(cat, "must_breed", False):
        score -= 100.0        # never recommend a marked must-breed
    if getattr(cat, "is_pinned", False):
        score -= 100.0
    return score


def donation_report(cats) -> List[DonationSlot]:
    """Rank every donation NPC's qualifying cats for the current roster."""
    slots: List[DonationSlot] = []

    def info(npc):
        return {
            "Tink": ("1-day-old kittens", "unlocked after the tutorial"),
            "Dr. Beanies": ("cats with mutations, birth defects, disorders",
                            "unlocked after Caves + Boneyard"),
            "Frank": ("cats that survived an adventure",
                      "unlocked after Alley + next day"),
            "Tracy": ("cats aged 5 or older",
                      "unlocked after Sewers + next day"),
        }[npc]

    for npc in NPC_ORDER:
        wants, unlock = info(npc)
        slot = DonationSlot(npc=npc, wants=wants, unlock_note=unlock)
        slot.candidates = [c for c in cats if _qualifies(c, npc)]
        # keep protected cats out of the giveaway list entirely-ish: any
        # must-breed / pinned cat sorts after everything else.
        def _protected(c):
            return bool(getattr(c, "must_breed", False)
                        or getattr(c, "is_pinned", False))
        slot.candidates.sort(key=lambda c: (int(_protected(c)),
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
    if not lines:
        lines.append(f"currently {display_location(cat)}")
    return lines
