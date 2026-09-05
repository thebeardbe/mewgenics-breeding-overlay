"""Parent malady (disorder / birth-defect) inheritance helpers.

Game rules (mewgenics.wiki.gg/wiki/Breeding, kitten birth process):

  * Disorders  — the kitten makes one roll per parent: 15 % to inherit one
    random disorder from that parent. Independent per parent. Not affected
    by furniture or Stimulation.
  * Visual birth defects — inherited as appearance, per body part (step 7 of
    the birth process). The exact chance depends on Stimulation, the kitten's
    inbreeding and what the *other* parent has on that body part (normal /
    mutation / defect). Without a full body-part map we surface an
    approximation: each carried defect passes ~40 % when the partner's
    matching part is normal at 50 Stimulation (defect kept 40 %, then a 20 %
    part-reroll can replace it). Always labelled as approximate in the UI.

The pair's *new* defect/disorder roll from inbreeding is the Risk % column
(derived from COI via the vendored engine) — this module is only about traits
already carried by the parents.
"""

from __future__ import annotations

from typing import Optional


def disorders_of(cat) -> list[str]:
    return list(getattr(cat, "disorders", None) or [])


def defects_of(cat) -> list[str]:
    return list(getattr(cat, "defects", None) or [])


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


# Approximate per-defect pass chance (see module docstring for assumptions).
DEFECT_PASS_APPROX_PCT = 40.0


def defect_lines(cat) -> list[str]:
    """Deduplicated defect display names of one cat, in save order."""
    seen: set[str] = set()
    out: list[str] = []
    for name in defects_of(cat):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out
