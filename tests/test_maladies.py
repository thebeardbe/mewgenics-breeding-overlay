"""Unit tests for parent malady (disorder/defect) inheritance helpers."""

from types import SimpleNamespace

import pytest

from mewgenics_overlay.core.maladies import (
    defect_effect_text,
    defect_inheritance_rows,
    defect_lines,
    disorder_summary,
    has_maladies,
    sexuality_label,
)


def cat(disorders=None, defects=None):
    return SimpleNamespace(disorders=disorders, defects=defects,
                           parent_a=None, parent_b=None)


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


def test_sexuality_label_thresholds():
    assert sexuality_label(0.05) == "straight"
    assert sexuality_label(0.5) == "bi"
    assert sexuality_label(0.95) == "gay"
    assert sexuality_label(None) == "straight"


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


# ── lineage (shared ancestor) checks ────────────────────────────────────────
from dataclasses import dataclass, field  # noqa: E402


@dataclass(eq=False)
class Node:
    name: str
    parent_a: object = field(default=None, repr=False)
    parent_b: object = field(default=None, repr=False)
    visual_mutation_entries: list = field(default_factory=list)
    defects: list = field(default_factory=list)
    disorders: list = field(default_factory=list)


def _arm_defect(slots):
    return [{"is_defect": True, "name": "Arm Birth Defect",
             "group_key": "arms", "slot_key": s} for s in slots]


def test_same_line_when_shared_ancestor_carries_defect():
    founder = Node("f", visual_mutation_entries=_arm_defect(["arm_L", "arm_R"]),
                   defects=["Arm Birth Defect"])
    mate = Node("m")
    # two unrelated mates, both bred through the founder => cousins
    a = Node("a", parent_a=founder, parent_b=mate,
             visual_mutation_entries=_arm_defect(["arm_L", "arm_R"]),
             defects=["Arm Birth Defect"])
    b = Node("b", parent_a=founder, parent_b=mate,
             visual_mutation_entries=_arm_defect(["arm_L", "arm_R"]),
             defects=["Arm Birth Defect"])
    rows = defect_inheritance_rows(a, b, coi=0.2)
    r = _row(rows, "Arm Birth Defect")
    assert r.carriers == ("a", "b")
    assert r.chance_pct == 100.0
    assert r.same_line is True


def test_different_lines_when_no_shared_carrier_ancestor():
    # founder A-line and a completely separate founder B-line both carry the
    # defect; the pair shares no defect-carrying ancestor.
    f1 = Node("f1", visual_mutation_entries=_arm_defect(["arm_L"]),
              defects=["Arm Birth Defect"])
    f2 = Node("f2", visual_mutation_entries=_arm_defect(["arm_R"]),
              defects=["Arm Birth Defect"])
    m1, m2 = Node("m1"), Node("m2")
    a = Node("a", parent_a=f1, parent_b=m1,
             visual_mutation_entries=_arm_defect(["arm_L"]),
             defects=["Arm Birth Defect"])
    b = Node("b", parent_a=f2, parent_b=m2,
             visual_mutation_entries=_arm_defect(["arm_R"]),
             defects=["Arm Birth Defect"])
    rows = defect_inheritance_rows(a, b, coi=0.0)
    r = _row(rows, "Arm Birth Defect")
    assert r.chance_pct == 100.0
    assert r.same_line is False
    assert r.slots_a == frozenset({"arm_L"})
    assert r.slots_b == frozenset({"arm_R"})


# ── shared gpak defect-effect lookup ──────────────────────────────────────
class _Assets:
    """Minimal GameAssets stand-in: ``effect_for`` over a (group, id) map."""

    def __init__(self, effects=None):
        self._effects = dict(effects or {})

    def effect_for(self, group, mutation_id):
        return self._effects.get((group, mutation_id), "")


def _defect_cat(entries):
    return SimpleNamespace(visual_mutation_entries=entries)


def test_defect_effect_text_returns_the_matching_defect_effect():
    assets = _Assets({("tail", 3): "short and stumpy"})
    c = _defect_cat([
        {"is_defect": False, "name": "Bobtail", "group_key": "tail",
         "mutation_id": 9},        # decoration, not a defect
        {"is_defect": True, "name": "Bobtail", "group_key": "tail",
         "mutation_id": 3},
    ])

    assert defect_effect_text(assets, c, "Bobtail") == "short and stumpy"


def test_defect_effect_text_skips_other_names_and_non_defects():
    assets = _Assets({("tail", 3): "text"})
    c = _defect_cat([
        {"is_defect": False, "name": "Bobtail", "group_key": "tail",
         "mutation_id": 3},
    ])

    assert defect_effect_text(assets, c, "Bobtail") == ""
    assert defect_effect_text(assets, c, "Other") == ""


def test_defect_effect_text_without_assets_or_entries_is_empty():
    c = _defect_cat([
        {"is_defect": True, "name": "Bobtail", "group_key": "tail",
         "mutation_id": 3},
    ])

    assert defect_effect_text(None, c, "Bobtail") == ""
    assert defect_effect_text(_Assets({}), _defect_cat([]), "Bobtail") == ""


def test_defect_effect_text_returns_the_first_non_empty_effect():
    assets = _Assets({("tail", 4): "second"})
    c = _defect_cat([
        {"is_defect": True, "name": "Bobtail", "group_key": "tail",
         "mutation_id": 3},     # known entry but no effect table text
        {"is_defect": True, "name": "Bobtail", "group_key": "tail",
         "mutation_id": 4},
    ])

    assert defect_effect_text(assets, c, "Bobtail") == "second"
