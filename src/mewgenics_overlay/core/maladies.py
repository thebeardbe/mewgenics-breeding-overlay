"""Parent malady (disorder / birth-defect) inheritance helpers.

Game rules (mewgenics.wiki.gg/wiki/Breeding, kitten birth process):

  * Disorders - the kitten makes one roll per parent: 15 % to inherit one
    random disorder from that parent. Independent per parent.
  * Visual birth defects - inherited as *appearance*, per body part (birth
    step 7). Each part (with left/right slots for legs, arms, eyes,
    eyebrows, ears) inherits one parent's version; a bias depends on
    Stimulation and the kitten's inbreeding:
        ordinary vs defect: p(ordinary) = 50 + 50*(Stim−2·Inbreed%)/
                                           (200+|Stim−2·Inbreed%|)
    If BOTH parents carry the same defect:
        * same ancestral line (a shared ancestor also carries it) - the
          kitten gets it on the same part/side (~100 %).
        * different lines - we cannot pin the side: the kitten gets it on
          the same side as either parent's or the opposite side (~100 %).
    Single-carrier odds use the formula above (other side assumed normal).

The pair's brand-new defect/disorder roll from inbreeding is the Risk %
column (exact, derived from COI by the vendored engine).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from mewgenics_overlay.vendor.save_parser import get_all_ancestors

# Part groups with a left/right slot pair (asymmetric in the game).
ASYMMETRIC_GROUPS = {"legs", "arms", "eyes", "eyebrows", "ears"}
_SLOT_SIDE_LABEL = {
    "fur": "fur", "body": "body", "head": "head", "tail": "tail",
    "mouth": "mouth",
    "leg_L": "left leg", "leg_R": "right leg",
    "arm_L": "left arm", "arm_R": "right arm",
    "eye_L": "left eye", "eye_R": "right eye",
    "eyebrow_L": "left eyebrow", "eyebrow_R": "right eyebrow",
    "ear_L": "left ear", "ear_R": "right ear",
}

LINEAGE_DEPTH = 9  # how far back we look for a shared defect-carrying ancestor


def disorders_of(cat) -> List[str]:
    return list(getattr(cat, "disorders", None) or [])


def sexuality_label(raw) -> str:
    """Game categories from the hidden sexuality value 0..1: <10 % straight,
    10–90 % bi, >90 % gay. Unknown/None -> 'straight' (game default)."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return "straight"
    if v < 0.1:
        return "straight"
    if v > 0.9:
        return "gay"
    return "bi"


def defects_of(cat) -> List[str]:
    return list(getattr(cat, "defects", None) or [])


def defect_lines(cat) -> List[str]:
    """Deduplicated defect display names of one cat, in save order."""
    seen: set[str] = set()
    out: List[str] = []
    for name in defects_of(cat):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def has_maladies(cat) -> bool:
    return bool(disorders_of(cat) or defects_of(cat))


def disorder_summary(a, b) -> dict:
    """Per-parent + combined disorder inheritance odds (exact game rule)."""
    dis_a, dis_b = disorders_of(a), disorders_of(b)
    pa = 0.15 if dis_a else 0.0
    pb = 0.15 if dis_b else 0.0
    return {
        "a": dis_a,
        "b": dis_b,
        "a_pct": pa * 100.0,
        "b_pct": pb * 100.0,
        "any_pct": (1.0 - (1.0 - pa) * (1.0 - pb)) * 100.0,
    }


# ── birth defects: slots, sides, ancestry ──────────────────────────────────


def defect_map(cat) -> Dict[str, dict]:
    """name -> {group, slots} for the defects this cat carries (entries give
    the exact body slot, e.g. arm_L / arm_R). When a Cat has only the legacy
    name list (no per-slot entries), slots are empty and the group is inferred
    from the display name."""
    out: Dict[str, dict] = {}
    for e in (getattr(cat, "visual_mutation_entries", None) or []):
        if not e or not e.get("is_defect"):
            continue
        name = e.get("name")
        if not name:
            continue
        entry = out.setdefault(name, {"group": e.get("group_key"), "slots": set()})
        slot = e.get("slot_key")
        if slot:
            entry["slots"].add(slot)
        g = e.get("group_key")
        if g:
            entry["group"] = g
    if not out:
        for name in defects_of(cat):
            out.setdefault(name, {"group": _infer_group(name), "slots": set()})
    return out


_PART_GROUP = {
    "fur": "fur", "body": "body", "head": "head", "tail": "tail",
    "mouth": "mouth", "leg": "legs", "legs": "legs", "arm": "arms",
    "arms": "arms", "ear": "ears", "ears": "ears", "eye": "eyes",
    "eyes": "eyes", "eyebrow": "eyebrows", "eyebrows": "eyebrows",
}


def _infer_group(name: str) -> Optional[str]:
    part = name.lower().split(" birth defect")[0].strip()
    if not part:
        return None
    return _PART_GROUP.get(part)


def _groups_carried(cat) -> Set[str]:
    return {info["group"] for info in defect_map(cat).values() if info["group"]}


def shared_defect_line(a, b, group: str, max_depth: int = LINEAGE_DEPTH) -> bool:
    """True when a single common ancestor of *a* and *b* carries a defect of
    this part group - i.e. the defect reaches both parents down the same line."""
    ancestors = get_all_ancestors(a, depth=max_depth) \
        & get_all_ancestors(b, depth=max_depth)
    return any(group in _groups_carried(anc) for anc in ancestors)


def side_label(slot: str) -> str:
    return _SLOT_SIDE_LABEL.get(slot, slot)


def side_text(slots: FrozenSet[str]) -> str:
    labels = sorted(side_label(s) for s in slots if s)
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return " and ".join(labels)


@dataclass(frozen=True)
class DefectRow:
    """Pass chance for one visual birth defect present on the parents."""

    name: str
    group: Optional[str]          # part group, e.g. 'arms' (None if unknown)
    carriers: Tuple[str, ...]     # 'a' and/or 'b' (a = focused cat)
    slots_a: FrozenSet[str] = field(default_factory=frozenset)
    slots_b: FrozenSet[str] = field(default_factory=frozenset)
    same_line: Optional[bool] = None  # shared ancestor also carries it
    chance_pct: float = 0.0


def _ordinary_inherit_pct(stimulation: float, inbred_pct: float) -> float:
    """p(ordinary part is inherited) for a defect-vs-ordinary slot pair."""
    t = stimulation - 2.0 * inbred_pct
    return 50.0 + 50.0 * t / (200.0 + abs(t))


def defect_inheritance_rows(
    a, b, coi: float, stimulation: float = 50.0
) -> List[DefectRow]:
    """Per-defect pass information for a pair.

    * Same defect on both parents - guaranteed (~100 %). Whether the side is
      deterministic depends on `same_line` (see module docstring).
    * Single parent carrier - 100 % − p(ordinary), which rises with the
      pair's inbreeding and falls with Stimulation.
    """
    map_a, map_b = defect_map(a), defect_map(b)
    inbred_pct = max(0.0, coi * 100.0)
    rows: List[DefectRow] = []
    for name in list(dict.fromkeys(list(map_a) + list(map_b))):
        info_a, info_b = map_a.get(name), map_b.get(name)
        slots_a = frozenset(info_a["slots"] if info_a else ())
        slots_b = frozenset(info_b["slots"] if info_b else ())
        group = (info_a or info_b or {}).get("group")
        sides = tuple(c for c in "ab" if (info_a if c == "a" else info_b))
        if len(sides) == 2:
            same_line = None
            if group in ASYMMETRIC_GROUPS:
                same_line = shared_defect_line(a, b, group)
            rows.append(DefectRow(name=name, group=group, carriers=("a", "b"),
                                  slots_a=slots_a, slots_b=slots_b,
                                  same_line=same_line, chance_pct=100.0))
        else:
            pct = 100.0 - _ordinary_inherit_pct(stimulation, inbred_pct)
            rows.append(DefectRow(name=name, group=group, carriers=sides,
                                  slots_a=slots_a, slots_b=slots_b,
                                  chance_pct=max(0.0, min(100.0, pct))))
    return rows
