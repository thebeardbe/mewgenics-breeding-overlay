"""Unit tests for parent malady (disorder/defect) inheritance helpers."""

from types import SimpleNamespace

import pytest

from mewgenics_overlay.core.maladies import (
    defect_inheritance_rows,
    defect_lines,
    disorder_summary,
    has_maladies,
)


def cat(disorders=None, defects=None):
    return SimpleNamespace(disorders=disorders, defects=defects)


def test_disorder_summary_clean():
    s = disorder_summary(cat(), cat())
    assert s["a"] == [] and s["b"] == []
    assert s["a_pct"] == s["b_pct"] == s["any_pct"] == 0.0


def test_disorder_summary_one_carrier():
    a = cat(disorders=["Sleepy"])
    s = disorder_summary(a, cat())
    assert s["a_pct"] == 15.0
    assert s["b_pct"] == 0.0
    assert s["any_pct"] == pytest.approx(15.0)


def test_disorder_summary_both_carriers():
    s = disorder_summary(cat(disorders=["X"]), cat(disorders=["Y"]))
    assert s["a_pct"] == s["b_pct"] == 15.0
    assert s["any_pct"] == pytest.approx(100.0 * (1 - 0.85 * 0.85))  # 27.75


def test_defect_lines_deduplicates_and_order():
    c = cat(defects=["Leg Birth Defect", "Leg Birth Defect", "Head Birth Defect"])
    assert defect_lines(c) == ["Leg Birth Defect", "Head Birth Defect"]
    assert defect_lines(cat()) == []


def test_has_maladies():
    assert not has_maladies(cat())
    assert has_maladies(cat(defects=["X"]))
    assert has_maladies(cat(disorders=["X"]))


def _row(rows, name):
    return next(r for r in rows if r.name == name)


def test_defect_shared_by_both_parents_is_guaranteed():
    rows = defect_inheritance_rows(
        cat(defects=["Arm Birth Defect"]),
        cat(defects=["Arm Birth Defect", "Mouth Birth Defect"]),
        coi=0.0,
    )
    arm = _row(rows, "Arm Birth Defect")
    assert arm.carriers == ("a", "b")
    assert arm.chance_pct == 100.0
    # mouth is only on b
    mouth = _row(rows, "Mouth Birth Defect")
    assert mouth.carriers == ("b",)
    assert mouth.chance_pct == pytest.approx(40.0, abs=0.1)


def test_single_carrier_clean_partner_at_50_stim():
    rows = defect_inheritance_rows(cat(defects=["Leg Birth Defect"]), cat(), 0.0)
    r = _row(rows, "Leg Birth Defect")
    assert r.carriers == ("a",)
    assert r.chance_pct == pytest.approx(40.0, abs=0.1)


def test_inbreeding_raises_single_carrier_chance():
    # Natalie x Brian Earwig style: high COI shifts odds toward the defect.
    rows = defect_inheritance_rows(cat(defects=["Mouth Birth Defect"]), cat(), 0.438)
    r = _row(rows, "Mouth Birth Defect")
    assert r.chance_pct == pytest.approx(57.9, abs=0.3)


def test_no_defects_no_rows():
    assert defect_inheritance_rows(cat(), cat(), 0.5) == []
    assert defect_inheritance_rows(cat(), cat(), 0.5, stimulation=120.0) == []
