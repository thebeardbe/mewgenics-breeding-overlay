"""Best-partner recommendation.

Picks the single strongest breeding candidate for the focused cat from three
signals:

  * ≥7 stats — the *highest-weighted* factor: how many kitten stats are
    expected to land on 7 (each counts 6 points, so a full litter of 7s
    dominates everything else).
  * Existing birth defects — each defect's in-game effect is read from
    resources.gpak and parsed for stat deltas. Defects that grant **+stats
    are a positive effect** and add to the score; defects that cost stats
    subtract. Defects with no numeric effect (pure appearance/flavour) get a
    small penalty, and shared (both parents carry it → guaranteed) defects
    weigh more than single-carrier ones.
  * Risk — the pair's birth-defect risk % (new-defect roll from inbreeding),
    subtracted.

Additive and transparent:

    score = W_SEVENS×≥7stats + W_AVG×expected_avg − W_RISK×risk% ± defects

≥7 stats dominate the score; risk and inherited defects act as
safety/quality tie-breakers so that equal-7 candidates are decided by them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from mewgenics_overlay.core.maladies import defect_inheritance_rows

W_SEVENS = 14.0           # per expected ≥7 stat (dominates the score)
W_AVG = 2.0               # per point of expected stat average
W_RISK = 0.3              # subtracted per risk % (safety tie-breaker)
W_NIGHT = 25.0            # per unit nightly-attempt probability (chance counts)

W_STAT_EFFECT = 3.0       # per net stat point granted/cost by a defect
UNQUANT_BOTH = 20.0       # flavour defect carried by both parents
UNQUANT_PARTNER = 8.0     # flavour defect only on the partner
UNQUANT_FOCUS = 4.0       # flavour defect only on the focused cat

_STAT_TOKEN = re.compile(r"([+-]?\d+)\s+(STR|DEX|CON|INT|SPD|CHA|LCK)",
                         re.IGNORECASE)
_STAT_CODES = {"STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"}


def effect_stat_net(effect: str) -> int:
    """Net stat change implied by an effect string, e.g. '+1 CON, -2 INT' -> -1.
    Only the seven core stats count; flavour text without stat deltas -> 0."""
    total = 0
    for match in _STAT_TOKEN.finditer(effect or ""):
        value = int(match.group(1))
        if match.group(2).upper() in _STAT_CODES:
            total += value
    return total


def _short(name: str) -> str:
    return name.replace(" Birth Defect", "") or name


@dataclass
class Recommendation:
    row: object                       # winning PartnerRow (or None)
    score: float = 0.0
    breakdown: List[str] = field(default_factory=list)


def _defect_contribution(d, partner_side: str, effect_of, a, b):
    """Signed score contribution for one inherited defect, plus a short label."""
    both = len(d.carriers) == 2
    partner_only = not both and partner_side in d.carriers
    factor = 1.0 if both else d.chance_pct / 100.0
    effect = effect_of(a, b, d.name) if effect_of else ""
    net = effect_stat_net(effect)

    if net > 0:
        pts = W_STAT_EFFECT * net * factor
        label = f"[{effect or f'+{net} stats'}] bonus"
        return pts, label
    if net < 0:
        pts = -W_STAT_EFFECT * abs(net) * factor
        label = f"[{effect or f'{net} stats'}] penalty"
        return pts, label

    # no numeric stat effect -> appearance/flavour; modest penalty
    if both:
        pts = -UNQUANT_BOTH
    elif partner_only:
        pts = -UNQUANT_PARTNER
    else:
        pts = -UNQUANT_FOCUS
    label = f"[{effect or 'no stat effect'}] appearance"
    return pts, label


def recommend(
    rows, focused, effect_of: Optional[Callable] = None,
    stimulation: float = 50.0,
    comfort: float = 0.0,
) -> Recommendation:
    """Return the best compatible partner (empty Recommendation when none).

    ``effect_of(a, b, defect_name) -> str`` supplies each defect's in-game
    effect text ('' when unknown); the palette feeds it from resources.gpak.
    ``stimulation`` is the breeding room's Stimulation (single-carrier defect
    odds depend on it); ``comfort`` scales the nightly attempt chance
    (compat × √(1+0.1×Comfort), both rolls must succeed).
    """
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
            factors.cat_a, factors.cat_b, row.coi, stimulation=stimulation)

        sevens = float(row.seven_plus_total)
        exp_avg = float(row.expected_avg)
        risk = float(row.risk_pct)
        # Nightly attempt chance: two rolls per night must both succeed, each
        # at compat × √(1+0.1×Comfort). Low compat = many wasted nights.
        roll = max(0.0, min(1.0,
                            float(getattr(row, "game_compat", 0.4))
                            * (1.0 + 0.1 * max(0.0, comfort)) ** 0.5))
        night = roll * roll

        defect_pts = 0.0
        defect_lines: List[str] = []
        for d in defect_rows:
            pts, label = _defect_contribution(d, partner_side, effect_of,
                                              factors.cat_a, factors.cat_b)
            defect_pts += pts
            if pts:
                defect_lines.append(
                    f"  {_short(d.name)}: {label} → {pts:+.0f}")

        score = (W_SEVENS * sevens + W_AVG * exp_avg - W_RISK * risk
                 + W_NIGHT * night + defect_pts)
        if score <= best_score:
            continue

        best, best_score = row, score
        bits = defect_lines if defect_lines else ["  none"]
        best_breakdown = [
            f"≥7 stats: {sevens:.1f} ×{W_SEVENS:.0f} (+{W_SEVENS * sevens:.1f})",
            f"expected avg: {exp_avg:.2f} ×{W_AVG:.0f} (+{W_AVG * exp_avg:.1f})",
            f"nightly attempt: {night * 100:.0f}% (+{W_NIGHT * night:.1f})",
            f"risk: {risk:.1f}% (−{W_RISK * risk:.1f})",
            "defect effects:",
            *bits,
            f"score: {score:.1f}",
        ]

    return Recommendation(row=best, score=best_score,
                          breakdown=best_breakdown)
