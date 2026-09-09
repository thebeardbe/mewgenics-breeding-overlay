"""Donation advisor.

Major NPCs level up by receiving a specific *type* of cat. Each day the
player decides which cats to send. This module inspects the live roster and,
for every donation NPC, lists the cats that currently qualify - ranked from
"give away first" to "keep" - so the daily cull is a few clicks instead of
manual scanning.

Detection uses what the save exposes:

  * Tink          - kittens: age 1 (born that day)
  * Tracy         - seniors: age >= 5
  * Dr. Beanies   - cats carrying mutations, birth defects, disorders
                    (parasites are not parsed from the save yet)
  * Frank         - retired: survived an adventure (MBM's heuristic: strong
                    ability count + stat growth from level-ups)

Not yet decodable from the save: injuries (Baby Jack), chapter progress
(Butch), parasite data, and per-NPC donation counters. Those NPCs are listed
with "unsupported" so the tab stays honest about coverage.
"""

from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger("mewgenics_overlay.donations")


from mewgenics_overlay.core.recommend import (
    W_AVG,
    W_RISK,
    W_SEVENS,
    effect_stat_net,
)
from mewgenics_overlay.vendor.save_parser import get_all_ancestors, risk_percent
from mewgenics_overlay.core.session import ALIVE_STATUSES, display_location
from mewgenics_overlay.core.stimulation import STIMULATION_DEFAULT
from mewgenics_overlay.vendor.breeding import pair_projection

# Adult/breedable threshold: 1-day-olds (and younger) can't breed yet.
KITTEN_MAX_AGE = 1
TINK_MAX_AGE = 1          # 1-day-old kittens (Tink)
TRACY_MIN_AGE = 5         # minimum age Tracy accepts


# breeding-value signal: a cat is a 'keeper' when its best pairing score
# beats this percentile of the whole roster
KEEPER_PERCENTILE = 0.75

# donation matrix weights (positive = keep, negative = donate)
# Rationale: each weight multiplies a roster-relative or per-cat signal and
# was tuned against real saves - the full reasoning lives in git history
# (commit subjects like 'weight nightly chance strongly') and SCORING notes;
# change numbers deliberately, never ad-hoc.
W_STRENGTH = 2.0      # roster-relative strength (bell curve)
W_INBRED = 2.0        # per COI point -> donate
W_OFFSPRING = 0.35    # per living offspring -> donate (line continues)
W_NO_OFFSPRING = 0.3  # no living offspring -> keep (line would end)
W_LINE = 1.2          # roster-relative recent-line strength -> keep
W_DEFECT_SIGN = 0.8   # per signed defect (positive = keep)
W_LOVER_KEEP = 0.5
# ALIVE_STATUSES is imported from core.session (single source of truth).
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
     "per-cat chapter progress is not stored in the save - only an "
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


def _pair_value(a, b, kinship_memo: dict) -> float:
    """Breeding value of pairing a with b: 7s + stats, minus a safety
    penalty for the pair's birth-defect risk (low-risk pairings are worth
    more). ``kinship_memo`` is shared across all pairs so the COI walks are
    amortized instead of restarted per pair."""
    proj = pair_projection(a, b, stimulation=STIMULATION_DEFAULT)
    risk = float(risk_percent(a, b, kinship_memo))
    return (W_SEVENS * proj.seven_plus_total
            + W_AVG * proj.avg_expected
            - W_RISK * risk)


def _breeding_keepers(cats) -> set:
    """Cats that are a top-tier mate for someone - donating them hurts the
    breeding pool. Kept deliberately simple: their best pairing score must
    beat the 75th percentile of the roster's scores."""
    scores: list = []
    memo: dict = {}
    skipped = 0
    for a in cats:
        best = 0.0
        for b in cats:
            if b is a:
                continue
            try:
                best = max(best, _pair_value(a, b, memo))
            except Exception:
                # One broken pair must never silently poison advice for the
                # whole roster: count it, log the first few loudly, and move
                # on scoring the next pair.
                skipped += 1
                if skipped <= 3:
                    log.exception("keeper scoring failed for %s x %s",
                                  getattr(a, "name", a), getattr(b, "name", b))
        scores.append((a, best))
    if skipped:
        log.warning("keeper scoring skipped %d pair(s) after errors", skipped)
    if len(scores) < 4:
        return set()
    ordered = sorted(v for _, v in scores)
    threshold = ordered[int(KEEPER_PERCENTILE * (len(ordered) - 1))]
    return {id(a) for a, v in scores if v > threshold}


@dataclass(slots=True)
class DonationAdvice:
    """Why one candidate ranks where it does, parallel to the slot's
    ``candidates`` list (advice[i] explains candidates[i]).

    Computed fresh on every ``donation_report`` call and never stored on the
    cats themselves - no hidden/stale state left behind on parser objects.
    """

    give: List[str]            # reasons to donate (shown first / ranked weak)
    keep: List[str]            # reasons to keep
    keep_for_breeding: bool    # top breeding mate for another cat
    score: float = 0.0         # the sort score (lower = give away first)


@dataclass(slots=True)
class DonationSlot:
    """Qualified cats for one NPC, ranked worst-kept first.

    ``candidates`` keeps the raw cat objects (ordering is the ranking);
    ``advice`` holds the matching per-cat analysis, parallel by index.
    """

    npc: str
    wants: str
    unlock_note: str = ""
    supported: bool = True
    active: bool = True          # NPC has started taking cats (save flags)
    candidates: List[object] = field(default_factory=list)
    advice: List[DonationAdvice] = field(default_factory=list)
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


def _base_sum(cat) -> float:
    return float(sum(getattr(cat, "base_stats", {}).values()))


def _roster_ctx(cats) -> list:
    """Ascending base-stat sums of the living roster (for percentiles)."""
    return sorted(_base_sum(c) for c in cats)


def _rank_fraction(base_sum: float, sums: list) -> float:
    """0..1 rank of a base-sum within the living roster (1 = strongest)."""
    if not sums:
        return 0.5
    return bisect.bisect_right(sums, base_sum) / len(sums)


def _living_children(cat) -> list:
    out = []
    for child in getattr(cat, "children", None) or []:
        if getattr(child, "status", "") in ALIVE_STATUSES:
            out.append(child)
    return out


def _line_pool(cat) -> list:
    """Self + living offspring + recent ancestors (<=2 gens)."""
    pool = [cat]
    pool += _living_children(cat)
    seen = {id(cat)}
    for anc in get_all_ancestors(cat, depth=3):
        if id(anc) not in seen:
            seen.add(id(anc))
            pool.append(anc)
    return pool


def _line_strength(cat, sums) -> float:
    pool = _line_pool(cat)
    if not pool:
        return 0.5
    return sum(_rank_fraction(_base_sum(c), sums) for c in pool) / len(pool)


def _defect_bias(cat, effect_of_cat) -> float:
    """Signed defect influence: +stats => keep, -stats => donate, neutral => 0."""
    if effect_of_cat is None:
        return 0.0
    bias = 0.0
    seen = set()
    for entry in (getattr(cat, "visual_mutation_entries", None) or []):
        if not entry or not entry.get("is_defect"):
            continue
        name = entry.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        net = effect_stat_net(effect_of_cat(cat, name))
        if net > 0:
            bias += 1.0
        elif net < 0:
            bias -= 1.0
    return bias


def _assess(cat, sums, effect_of_cat=None):
    """One pass over a cat: (score, reasons_to_donate, reasons_to_keep).

    LOWER score = give away first. Merges what used to be two separate
    functions (score + reason builder) so the expensive shared inputs -
    roster percentile, living offspring, line strength, defect sign - are
    computed ONCE per cat per report instead of twice.
    """
    base = _base_sum(cat)
    rel = _rank_fraction(base, sums)
    coi = max(0.0, min(1.0, float(getattr(cat, "inbredness", 0.0) or 0.0)))
    living = len(_living_children(cat))
    line = _line_strength(cat, sums)
    bias = _defect_bias(cat, effect_of_cat)

    give: list = []
    keep: list = []
    n = len(sums) or 1
    below = bisect.bisect_left(sums, base) / n      # strictly stronger share
    above = (len(sums) - bisect.bisect_right(sums, base)) / n  # strictly weaker
    if below >= 0.6:
        keep.append(f"Stronger than {below * 100:.0f}% of your living cats")
    elif above >= 0.6:
        give.append(f"Weaker than {above * 100:.0f}% of your living cats")
    if coi > 0.1:
        give.append(f"Inbred (COI {coi * 100:.0f}%)")
    if living:
        give.append(f"Has {living} direct child(ren) still living (from any "
                    f"mate) - its line already continues")
    else:
        keep.append("No direct children left living - donating would end "
                    "its line")
    if line >= 0.6:
        keep.append("Comes from a strong recent line")
    elif line <= 0.4:
        give.append("Comes from a weak recent line")
    if bias > 0:
        keep.append("Carries positive-stats birth defects")
    elif bias < 0:
        give.append("Carries negative-stats birth defects")
    if getattr(cat, "lovers", None):
        keep.append("Is in love - keep with their partner")
    if getattr(cat, "must_breed", False):
        keep.append("Marked must-breed")
    if getattr(cat, "is_pinned", False):
        keep.append("Pinned by you")

    score = 0.0
    score += (rel - 0.5) * W_STRENGTH          # vs. living average (bell)
    score -= coi * W_INBRED                    # inbred -> donate
    score += (line - 0.5) * W_LINE             # good line -> keep
    if living:
        score -= min(1.5, living) * W_OFFSPRING   # line continues -> donate
    else:
        score += W_NO_OFFSPRING                   # line would end -> keep
    score += bias * W_DEFECT_SIGN
    if getattr(cat, "lovers", None):
        score += W_LOVER_KEEP
    if getattr(cat, "must_breed", False) or getattr(cat, "is_pinned", False):
        score -= 100.0
    return score, give, keep


def donation_report(cats, active: Optional[set] = None,
                    dead: tuple = (), current_day: Optional[int] = None,
                    effect_of_cat=None) -> List[DonationSlot]:
    """Rank every donation NPC's qualifying cats for the current roster.

    ``cats`` are the alive cats; ``dead`` (optional) supplies the cats that
    have died, which the Organ Grinder takes. ``active`` is an optional set
    of flag names from the save's npc_progress.

    Results are fully self-contained: each slot carries its cats AND its
    analysis (``advice``), so nothing is written onto the cat objects.
    """
    flags = set(active or ())
    slots: List[DonationSlot] = []
    keepers = _breeding_keepers(cats)
    sums = _roster_ctx(cats)
    # A cat can qualify for several NPCs, but its score/reasons depend only on
    # the roster - assess each cat once per report and reuse the result.
    assessed: dict = {}

    def _assessment_of(c) -> tuple:
        cached = assessed.get(id(c))
        if cached is None:
            cached = _assess(c, sums, effect_of_cat)
            assessed[id(c)] = cached
        return cached

    for npc in NPC_ORDER:
        profile = NPC_PROFILES[npc]
        pool = dead if npc == "Organ Grinder" else cats
        slot = DonationSlot(npc=npc, wants=profile.wants,
                            unlock_note=profile.unlock_note,
                            active=any(flag.startswith(profile.slug)
                                       for flag in flags))
        qualified = [c for c in pool
                     if _qualifies(c, npc)
                     and (npc != "Organ Grinder"
                          or _recent_death(c, current_day))
                     and (npc != "Organ Grinder" or _visible_dead(c))]
        # protect the breeding pool: cats that are a top mate for someone
        # sort below expendable cats (but above pinned/must-breed).
        scored = []
        for c in qualified:
            keeper = id(c) in keepers
            protected = bool(getattr(c, "must_breed", False)
                             or getattr(c, "is_pinned", False))
            score, give, keep = _assessment_of(c)
            scored.append((protected, keeper, score, give, keep, c))
        scored.sort(key=lambda t: (int(t[0]) * 2 + int(t[1]), t[2]))
        slot.candidates = [t[5] for t in scored]
        # Single source of the keeper-note copy lives HERE (not in the UI):
        # every reason string a player sees originates from core/donations.
        slot.advice = []
        for t in scored:
            give = list(t[3])
            keep = list(t[4])
            if t[1] and not any("Top breeding mate" in line for line in keep):
                keep.append("Top breeding mate for another cat")
            slot.advice.append(DonationAdvice(give=give, keep=keep,
                                              keep_for_breeding=t[1],
                                              score=t[2]))
        slot.ranks = list(range(1, len(scored) + 1))
        slots.append(slot)

    for name, wants, why in UNSUPPORTED:
        slots.append(DonationSlot(npc=name, wants=wants, supported=False,
                                   unlock_note=why))
    return slots


def rating_why(rating: str) -> List[str]:
    """Generic rating copy shown when a cat has no specific give/keep reasons.

    Single source for the Donate/Maybe/Keep boilerplate so the UI never forks
    this wording; pinned/must-breed cats always carry real reasons from
    ``_assess``, so they never hit this path.
    """
    if rating == "Keep":
        return ["Among the strongest here - more valuable kept for breeding."]
    if rating == "Maybe":
        return ["Not clearly expendable nor essential - donate if you "
                "need the space."]
    return ["One of the weakest / least useful here - a fine donation."]


def recommendation_lines(cat, keep_for_breeding: bool = False) -> List[str]:
    """Short human lines explaining why a cat is a candidate.

    ``keep_for_breeding`` comes from the matching ``DonationAdvice`` - the
    caller knows it, a bare cat never carries it.
    """
    lines: List[str] = []
    age = _age(cat)
    if age is not None and age <= TINK_MAX_AGE:
        lines.append("born today - Tink wants kittens")
    if age is not None and age >= TRACY_MIN_AGE:
        lines.append("senior cat - Tracy will take them")
    if _is_retired(cat):
        lines.append("survived an adventure - Frank will take them")
    if _has_any_mutation_or_condition(cat):
        bits = []
        if getattr(cat, "defects", None):
            bits.append("birth defects")
        if getattr(cat, "disorders", None):
            bits.append("disorders")
        if any(e and not e.get("is_defect")
               for e in (getattr(cat, "visual_mutation_entries", None) or [])):
            bits.append("mutations")
        lines.append("carries " + ", ".join(bits) + " - Dr. Beanies wants these")
    if _injured_stat_count(cat) >= 1:
        lines.append("stat penalties suggest an injury - Baby Jack will take "
                     "them")
    if keep_for_breeding:
        lines.append("valuable for breeding (a top mate for someone) - "
                     "donate only if you really need to")
    if getattr(cat, "is_dead", False):
        lines.append("deceased - the Organ Grinder takes the dead")
    if not lines:
        lines.append(f"currently {display_location(cat)}")
    return lines
