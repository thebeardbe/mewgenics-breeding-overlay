"""Unit tests for parent malady (disorder/defect) inheritance helpers."""

from types import SimpleNamespace

import pytest

from mewgenics_overlay.core.maladies import (
    DEFECT_PASS_APPROX_PCT,
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
    assert 0 < DEFECT_PASS_APPROX_PCT <= 100
