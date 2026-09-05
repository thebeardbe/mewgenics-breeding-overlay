"""Parent malady (disorder / birth-defect) inheritance helpers.

Game rules (mewgenics.wiki.gg/wiki/Breeding, kitten birth process):

  * Disorders — the kitten makes one roll per parent: 15 % to inherit one
    random disorder from that parent. Independent per parent, unaffected by
    furniture or Stimulation.
  * Visual birth defects — inherited as *appearance*, per body part (birth
    step 7). One parent's version of each part is chosen, with a bias that
    depends on Stimulation and the kitten's inbreeding:

        ordinary vs defect:  p(ordinary) = 50 + 50 * (Stim − 2·Inbreed%) /
                                           (200 + |Stim − 2·Inbreed%|)

    So the single-carrier pass chance is 100 % − p(ordinary). If BOTH parents
    carry the same defect, whichever parent's part is inherited is that
    defect → it passes (only the separate 20 % single-part rerandomisation
    step can remove it). Mutation-vs-defect and per-slot L/R details are not
    recoverable from the save parser yet, so single-sided odds assume the
    other parent's matching part is normal and use 50 Stimulation.

This module only covers traits parents ALREADY carry. The pair's brand-new
defect/disorder roll from inbreeding is the Risk % column (exact, derived
from COI by the vendored engine).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


def disorders_of(cat) -> List[str]:
    return list(getattr(cat, "disorders", None) or [])


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


@dataclass(frozen=True)
class DefectRow:
    """Pass-chance for one visual birth defect present on the parents."""

    name: str
    carriers: tuple[str, ...]   # 'a' and/or 'b' (a = focused cat)
    chance_pct: float           # 100.0 when both parents carry it
    note: str = ""


def _ordinary_inherit_pct(stimulation: float, inbred_pct: float) -> float:
    """p(ordinary part is inherited) for a defect-vs-ordinary slot pair."""
    t = stimulation - 2.0 * inbred_pct
    return 50.0 + 50.0 * t / (200.0 + abs(t))


def defect_inheritance_rows(
    a, b, coi: float, stimulation: float = 50.0
) -> List[DefectRow]:
    """Per-defect pass chances for a pair.

    * Same defect on both parents -> 100 % (the only way to lose it is the
      20 % single-part rerandomisation of the birth process).
    * Single parent carrier -> 100 % − p(ordinary), which rises with the
      pair's inbreeding and falls with Stimulation.
    """
    def_a = defect_lines(a)
    def_b = defect_lines(b)
    inbred_pct = max(0.0, coi * 100.0)
    rows: List[DefectRow] = []
    for name in list(dict.fromkeys(def_a + def_b)):
        sides = tuple(c for c in "ab" if name in (def_a if c == "a" else def_b))
        if len(sides) == 2:
            rows.append(DefectRow(name=name, carriers=("a", "b"), chance_pct=100.0,
                                  note="both parents carry it"))
        else:
            pct = 100.0 - _ordinary_inherit_pct(stimulation, inbred_pct)
            rows.append(DefectRow(name=name, carriers=sides,
                                  chance_pct=max(0.0, min(100.0, pct))))
    return rows
