"""Unit tests for the best-partner recommender."""

from types import SimpleNamespace

import pytest

from mewgenics_overlay.core.recommend import recommend


def cat(name, defects=None):
    return SimpleNamespace(name=name, defects=defects or [],
                           disorders=[], parent_a=None, parent_b=None,
                           visual_mutation_entries=[])


def row(partner, focused, *, risk=2.0, sevens=2.0, exp_avg=5.0, coi=0.0,
        compatible=True):
    return SimpleNamespace(
        partner=partner, compatible=compatible,
        risk_pct=risk, seven_plus_total=sevens, expected_avg=exp_avg,
        coi=coi,
        pair_factors=SimpleNamespace(cat_a=focused, cat_b=partner),
    )


def test_prefers_clean_low_risk_partner():
    focus = cat("Focus", defects=["Arm Birth Defect"])
    clean = cat("Clean")
    shared = cat("Shared", defects=["Arm Birth Defect"])  # both carry -> guaranteed
    rows = [
        row(shared, focus, risk=5.0, sevens=4.0, exp_avg=5.5),
        row(clean, focus, risk=2.0, sevens=2.5, exp_avg=5.0),
    ]
    rec = recommend(rows, focus)
    assert rec.row.partner is clean
    assert rec.breakdown and any("defects" in b for b in rec.breakdown)


def test_risk_beats_extra_sevens():
    focus = cat("Focus")
    safe = cat("Safe")
    risky = cat("Risky")
    rows = [
        row(risky, focus, risk=60.0, sevens=6.0, exp_avg=6.2),
        row(safe, focus, risk=2.0, sevens=2.0, exp_avg=5.0),
    ]
    assert recommend(rows, focus).row.partner is safe


def test_shared_defect_drops_candidate():
    focus = cat("Focus", defects=["Leg Birth Defect"])
    other = cat("Other", defects=["Leg Birth Defect"])
    stranger = cat("Stranger")
    rows = [
        row(other, focus, risk=2.0, sevens=3.0),
        row(stranger, focus, risk=2.0, sevens=1.0),
    ]
    assert recommend(rows, focus).row.partner is stranger


def test_incompatible_ignored_and_none_case():
    focus = cat("Focus")
    blocked = cat("Blocked")
    rows = [row(blocked, focus, compatible=False)]
    rec = recommend(rows, focus)
    assert rec.row is None
    assert recommend([], focus).row is None
