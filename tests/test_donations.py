"""Unit tests for the donation advisor."""

from types import SimpleNamespace

import pytest

from mewgenics_overlay.core.donations import (
    donation_report,
    recommendation_lines,
)


def cat(name="c", age=None, defects=None, disorders=None, entries=None,
        adventured=False, must_breed=False, inbredness=0.0, lovers=None,
        base=None, status="In House", room="", abilities=None, stat_mod=None,
        death_day=None):
    return SimpleNamespace(
        name=name, age=age, defects=defects or [], disorders=disorders or [],
        visual_mutation_entries=entries or [], lovers=lovers or [],
        must_breed=must_breed, inbredness=inbredness, status=status, room=room,
        base_stats=base if base is not None else
        {"STR": 5, "DEX": 5, "CON": 5, "INT": 5,
         "SPD": 5, "CHA": 5, "LCK": 5},
        abilities=abilities or [], stat_mod=stat_mod or [],
        death_day=death_day,
        has_adventured=lambda: adventured,
    )


def _slot(report, npc):
    return next(s for s in report if s.npc == npc)


def test_tink_takes_kittens_only():
    report = donation_report([
        cat("Kit", age=1),
        cat("Teen", age=2),
        cat("Old", age=40),
    ])
    slot = _slot(report, "Tink")
    assert [c.name for c in slot.candidates] == ["Kit"]


def test_tracy_takes_seniors():
    report = donation_report([cat("Yng", age=4), cat("Old", age=5)])
    assert [c.name for c in _slot(report, "Tracy").candidates] == ["Old"]


def test_beanies_takes_mutants_and_conditions():
    report = donation_report([
        cat("Clean"),
        cat("Blemished", defects=["Arm Birth Defect"]),
        cat("Odd", entries=[{"is_defect": False, "name": "Spots"}]),
        cat("Moody", disorders=["Sleepy"]),
    ])
    names = [c.name for c in _slot(report, "Dr. Beanies").candidates]
    assert set(names) == {"Blemished", "Odd", "Moody"}


def test_frank_takes_retired():
    report = donation_report([
        cat("New"),
        cat("Veteran", adventured=True),
    ])
    assert [c.name for c in _slot(report, "Frank").candidates] == ["Veteran"]


def test_weakest_ranked_first_and_must_breed_last():
    report = donation_report([
        cat("Strong", age=1, base={k: 7 for k in
                                  ("STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK")}),
        cat("Weak", age=1, base={k: 1 for k in
                                 ("STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK")}),
        cat("KeepMe", age=1, must_breed=True),
    ])
    names = [c.name for c in _slot(report, "Tink").candidates]
    assert names[0] == "Weak"
    assert names[-1] == "KeepMe"


def test_recommendation_lines_explain_why():
    c = cat(age=1, defects=["Leg Birth Defect"])
    text = "\n".join(recommendation_lines(c))
    assert "Tink" in text and "Dr. Beanies" in text


def test_baby_jack_takes_injured_cats():
    hurt = cat("Hurt", base={k: 5 for k in
                           ("STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK")})
    hurt.total_stats = {k: (3 if k == "SPD" else 5) for k in
                        ("STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK")}
    healthy = cat("Fine")
    healthy.total_stats = dict(healthy.base_stats)
    report = donation_report([hurt, healthy])
    slot = _slot(report, "Baby Jack")
    assert [c.name for c in slot.candidates] == ["Hurt"]


def test_organ_grinder_takes_only_recent_dead():
    fresh = cat("Fresh RIP", age=40, death_day=99)   # status In House
    fresh.is_dead = True
    gone = cat("Gone RIP", age=400, death_day=98, status="Gone")
    gone.is_dead = True
    old = cat("Old RIP", age=400, death_day=10)
    old.is_dead = True
    report = donation_report([], dead=(fresh, gone, old), current_day=100)
    slot = _slot(report, "Organ Grinder")
    # Gone (already collected/donated) and too-old deaths are excluded
    assert [c.name for c in slot.candidates] == ["Fresh RIP"]
    assert _slot(report, "Tracy").count == 0
    butch = _slot(report, "Butch")
    assert not butch.supported and butch.count == 0


def test_retired_loosened_threshold_catches_veterans():
    # strict 4+ would miss these: 3 abilities but clear stat gains from runs
    vet = cat("Vet", age=30, abilities=["a", "b", "c"],
              stat_mod=[2, 0, 0, 0, 0, 0, 0])
    weak = cat("Weak", age=30, abilities=["a", "b"],
               stat_mod=[2, 0, 0, 0, 0, 0, 0])
    report = donation_report([vet, weak])
    slot = _slot(report, "Frank")
    assert [c.name for c in slot.candidates] == ["Vet"]


def test_unsupported_npcs_listed_but_empty():
    report = donation_report([cat(age=1)])
    for slot in report:
        if not slot.supported:
            assert slot.count == 0
            assert slot.candidates == []
