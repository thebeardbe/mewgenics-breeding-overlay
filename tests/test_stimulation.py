"""Stimulation should affect inheritance math (stat weighting + defect odds)."""

from types import SimpleNamespace

import pytest

from mewgenics_overlay.vendor.breeding import pair_projection
from mewgenics_overlay.core.maladies import defect_inheritance_rows
from mewgenics_overlay.core.stimulation import room_stimulation_map


def cat(name="c", base=None, defects=None, room="Floor1_Large"):
    stats = base if base is not None else {
        "STR": 5, "DEX": 5, "CON": 5, "INT": 5,
        "SPD": 5, "CHA": 5, "LCK": 5}
    return SimpleNamespace(name=name, base_stats=stats, room=room,
                           defects=defects or [], disorders=[],
                           parent_a=None, parent_b=None,
                           visual_mutation_entries=[])


def test_high_stimulation_raises_expected_stats():
    a = cat(base={"STR": 7, "DEX": 7, "CON": 7, "INT": 7,
                  "SPD": 7, "CHA": 7, "LCK": 7})
    b = cat(base={"STR": 1, "DEX": 1, "CON": 1, "INT": 1,
                  "SPD": 1, "CHA": 1, "LCK": 1})
    low = pair_projection(a, b, stimulation=0.0).avg_expected
    high = pair_projection(a, b, stimulation=200.0).avg_expected
    assert high > low
    assert high == pytest.approx(5.5, abs=0.05)   # 50/50 at 0 stim, biased up at 200
    assert low == pytest.approx(4.0, abs=0.01)


def test_stimulation_lowers_single_carrier_defect_chance():
    carrier = cat(defects=["Arm Birth Defect"])
    clean = cat()
    rows0 = defect_inheritance_rows(carrier, clean, 0.0, stimulation=0.0)
    rows200 = defect_inheritance_rows(carrier, clean, 0.0, stimulation=200.0)
    r0 = next(r for r in rows0 if r.name == "Arm Birth Defect")
    r200 = next(r for r in rows200 if r.name == "Arm Birth Defect")
    assert r0.chance_pct > r200.chance_pct


def test_room_stimulation_map_sums_furniture():
    item = SimpleNamespace(item_name="toy", room="Floor1_Large", is_rare=False)
    defs = {"toy": SimpleNamespace(effects={"Stimulation": 40.0,
                                            "Appeal": 5.0})}
    out = room_stimulation_map({"Floor1_Large": [item]}, defs)
    assert out["Floor1_Large"] == pytest.approx(40.0)
    # rooms without furniture defs still appear with their raw sum (0)
    out2 = room_stimulation_map({"Attic": [SimpleNamespace(item_name="x",
                                                           room="Attic",
                                                           is_rare=False)]},
                                {})
    assert out2["Attic"] == pytest.approx(0.0)
