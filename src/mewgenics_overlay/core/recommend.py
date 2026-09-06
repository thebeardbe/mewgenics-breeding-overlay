"""Best-partner recommendation.

Picks the single strongest breeding candidate for the focused cat by
combining the three signals that matter:

  * Risk — the pair's birth-defect risk % (new-defect roll from inbreeding;
    lower is better).
  * Existing birth defects — a penalty whenever the kitten would inherit a
    defect the parents already carry. Shared defects (both parents carry the
    same one → guaranteed) cost the most, a defect carried only by the
    partner costs less, one carried only by the focused cat costs least.
  * ≥7 stats — how many of the kitten's stats are expected to land on 7
    (higher is better), with the expected stat average as a tie-breaker.

Additive score, shown in the breakdown so the user can see *why* a candidate
won or lost:

    score = 4×≥7stats + 2×expected_avg − risk% − defect_points

Only compatible (non-family, breedable) partners are considered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from mewgenics_overlay.core.maladies import defect_inheritance_rows

_BOTH_CARRY_PENALTY = 35.0   # points per shared (guaranteed) defect
_PARTNER_FACTOR = 0.5        # × chance% for a partner-only defect (~20–29)
_FOCUS_FACTOR = 0.25         # × chance% for a focused-only defect (~10–15)


def _short(name: str) -> str:
    return name.replace(" Birth Defect", "") or name


@dataclass
class Recommendation:
    row: object                       # winning PartnerRow (or None)
    score: float = 0.0
    breakdown: List[str] = field(default_factory=list)


def recommend(rows, focused) -> Recommendation:
    """Return the best compatible partner (empty Recommendation when none)."""
    best = None
    best_score = float("-inf")
    best_breakdown: List[str] = []

    for row in rows:
        if not getattr(row, "compatible", False):
            continue
        factors = getattr(row, "pair_factors", None)
        if factors is None:
            continue
        partner = row.partner
        partner_side = "b" if factors.cat_b is partner else "a"
        defect_rows = defect_inheritance_rows(
            factors.cat_a, factors.cat_b, row.coi)

        risk = float(row.risk_pct)
        sevens = float(row.seven_plus_total)
        exp_avg = float(row.expected_avg)

        defect_pts = 0.0
        for d in defect_rows:
            if len(d.carriers) == 2:
                defect_pts += _BOTH_CARRY_PENALTY * d.chance_pct / 100.0
            elif partner_side in d.carriers:
                defect_pts += _PARTNER_FACTOR * d.chance_pct
            else:
                defect_pts += _FOCUS_FACTOR * d.chance_pct

        score = 4.0 * sevens + 2.0 * exp_avg - risk - defect_pts
        if score <= best_score:
            continue

        best, best_score = row, score
        shared = [d for d in defect_rows if len(d.carriers) == 2]
        partner_only = [d for d in defect_rows
                        if partner_side in d.carriers and len(d.carriers) == 1]
        focused_only = [d for d in defect_rows
                        if partner_side not in d.carriers
                        and len(d.carriers) == 1]
        bits = []
        if shared:
            bits.append(f"{len(shared)} shared → guaranteed")
        if partner_only:
            bits.append("partner: " + ", ".join(_short(d.name)
                                               for d in partner_only))
        if focused_only:
            bits.append("focused: " + ", ".join(_short(d.name)
                                                for d in focused_only))
        best_breakdown = [
            f"≥7 stats: {sevens:.1f} (+{4 * sevens:.0f})",
            f"expected avg: {exp_avg:.2f} (+{2 * exp_avg:.1f})",
            f"risk: {risk:.1f}% (−{risk:.1f})",
            f"defects: {', '.join(bits) or 'none'} (−{defect_pts:.0f})",
            f"score: {score:.1f}",
        ]

    return Recommendation(row=best, score=best_score,
                          breakdown=best_breakdown)
