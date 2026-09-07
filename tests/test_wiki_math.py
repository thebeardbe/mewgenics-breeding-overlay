"""Wiki-pinned formula tests for the vendored breeding math (no save needed).

Every numeric claim here is pinned against the public game-code documentation
so a future vendor re-sync cannot silently drift the formulas:

  * Mewgenics breeding calculator (mewgenicswiki.org/tools/breeding-calculator)
    — built on SciresM's reverse engineering (glaiel::CatData::breed).
  * SciresM "Mewgenics breeding notes" gist (95a9dbba22937420e75d4da617af1397).
  * mewgenics.wiki.gg/wiki/Breeding (datamined tables, desktop v1.1.21039).

The vendored engine may change; if one of these starts failing, that is a
FORMULA DRIFT ALARM, not just a broken test — check the upstream fork sync
(vendor/_VENDORED.md) before "fixing" the test.
"""

import math
from types import SimpleNamespace

import pytest

from mewgenics_overlay.vendor.breeding import (
    _sexuality_mult,
    ability_inheritance_chances,
    breeding_success_chance,
    game_compatibility,
)
from mewgenics_overlay.vendor.save_parser import (
    _malady_breakdown,
    _stimulation_inheritance_weight,
    kinship_coi,
)
from mewgenics_overlay.core.maladies import (
    _ordinary_inherit_pct,
    defect_inheritance_rows,
)


def _cat(name="c", gender="female", sexuality_raw=0.05, libido=1.0, cha=10,
         lovers=None):
    """Stub Cat for compatibility math (mirrors test_breeding_model.bcat)."""
    return SimpleNamespace(
        name=name, gender=gender, sexuality_raw=sexuality_raw, libido=libido,
        lovers=lovers or [],
        total_stats={"CHA": cha, "STR": 5, "DEX": 5, "CON": 5, "INT": 5,
                     "SPD": 5, "LCK": 5},
    )


# ── stat inheritance ────────────────────────────────────────────────────────
# Calculator FAQ / wiki.gg: P(better stat) = (100 + S) / (200 + |S|).
# Reference points taken from the calculator's own table.
def test_stat_inheritance_matches_wiki_curve():
    ref = {0: 50.0, 25: 55.6, 50: 60.0, 95: 66.1, 100: 66.7, 200: 75.0}
    for stim, want in ref.items():
        got = 100.0 * _stimulation_inheritance_weight(stim)
        assert got == pytest.approx(want, abs=0.15)


def test_stat_inheritance_is_the_wiki_formula():
    for stim in (0, 25, 50, 100, 200):
        got = _stimulation_inheritance_weight(stim)
        want = (100.0 + stim) / (200.0 + abs(stim))
        assert got == pytest.approx(want)


# ── abilities (SciresM gist step 4-5; calculator "Spell & passive") ─────────
def test_ability_inheritance_matches_gist_formulas():
    for stim in (0.0, 12.5, 32.0, 196.0, 300.0):
        chances = ability_inheritance_chances(stim)
        assert chances["first_active"] == pytest.approx(
            min(1.0, 0.20 + 0.025 * stim))
        assert chances["second_active"] == pytest.approx(
            min(1.0, 0.02 + 0.005 * stim))
        assert chances["passive"] == pytest.approx(
            min(1.0, 0.05 + 0.01 * stim))


def test_ability_inheritance_breakpoints():
    # 32+ Stim → first spell guaranteed; 196+ → second; 95+ → passive.
    assert ability_inheritance_chances(32.0)["first_active"] == 1.0
    assert ability_inheritance_chances(196.0)["second_active"] == 1.0
    assert ability_inheritance_chances(95.0)["passive"] == 1.0
    assert ability_inheritance_chances(31.0)["first_active"] < 1.0


# ── inbreeding rolls (gist step 7-8; calculator disorder/parts) ─────────────
def test_birth_defect_disorder_formula_matches_gist():
    # 0.02 + 0.4 * clamp(IC - 0.2, 0, 1): 2% flat below IC 0.2, 42% cap at 1.2+.
    for ic in (0.0, 0.1, 0.2, 0.3, 0.5, 1.0, 1.2, 1.5):
        disorder, _, _ = _malady_breakdown(ic)
        want = 0.02 + 0.4 * max(0.0, min(1.0, ic - 0.20))
        assert disorder == pytest.approx(want)


def test_birth_defect_parts_formula_matches_gist():
    # parts = 1.5*IC, impossible at IC <= 0.05; wiki: ~75% at IC 0.5.
    _, defect_parts, _ = _malady_breakdown(0.5)
    assert defect_parts == pytest.approx(0.75)
    assert _malady_breakdown(0.05)[1] == 0.0
    assert _malady_breakdown(0.0)[1] == 0.0
    assert _malady_breakdown(0.9)[1] == pytest.approx(1.0)  # capped


# ── appearance / parent-carried defect inheritance (wiki.gg table) ──────────
def test_ordinary_vs_defect_inheritance_formula_matches_wiki():
    # p(ordinary) = 50 + 50*(S - 2*Inbreed%) / (200 + |S - 2*Inbreed%|)
    for stim, inbred in ((0, 0), (50, 0), (100, 0), (50, 10), (0, 30)):
        got = _ordinary_inherit_pct(stim, inbred)
        t = stim - 2.0 * inbred
        want = 50.0 + 50.0 * t / (200.0 + abs(t))
        assert got == pytest.approx(want)


def test_single_carrier_defect_pass_chance():
    # One parent carries the defect: pass chance = 100% - p(ordinary).
    # At 50 Stim / 0 inbred: p(ordinary)=60% → 40% defect pass.
    def defect_cat(name):
        return SimpleNamespace(
            name=name, gender="female", sexuality_raw=0.05, libido=1.0,
            lovers=[],
            total_stats={"CHA": 10, "STR": 5, "DEX": 5, "CON": 5,
                         "INT": 5, "SPD": 5, "LCK": 5},
            visual_mutation_entries=[
                {"is_defect": True, "name": "Leg Birth Defect"}],
        )
    carrier = defect_cat("Mom")
    clean = defect_cat("Dad")
    clean.visual_mutation_entries = []
    rows = defect_inheritance_rows(carrier, clean, coi=0.0, stimulation=50.0)
    assert len(rows) == 1
    assert rows[0].chance_pct == pytest.approx(40.0)


# ── sexuality multiplier (wiki.gg Breeding § Sexuality) ─────────────────────
def _avg_over(fn, lo, hi, n=300000):
    """Deterministic average of fn(x) over x in [lo, hi)."""
    s = 0.0
    for i in range(n):
        x = lo + (hi - lo) * (i + 0.5) / n
        s += fn(x)
    return s / n


def test_sexuality_multiplier_averages_match_wiki():
    # wiki.gg: table = average of cos(pi/2 x) [opposite sex] or sin(pi/2 x)
    # [same sex] over each range: straight 99.59/7.84, bi 66.15/66.15,
    # gay 7.84/99.59 (%). We average the actual vendored multiplier.
    def opp(x):
        return _sexuality_mult(SimpleNamespace(sexuality_raw=x), False)

    def same(x):
        return _sexuality_mult(SimpleNamespace(sexuality_raw=x), True)

    cases = [
        # (range, opposite-sex wiki %, same-sex wiki %)
        ((0.0, 0.1), 99.59, 7.84),
        ((0.1, 0.9), 66.15, 66.15),
        ((0.9, 1.0), 7.84, 99.59),
    ]
    for (lo, hi), want_opp, want_same in cases:
        assert 100.0 * _avg_over(opp, lo, hi) == pytest.approx(want_opp, abs=0.15)
        assert 100.0 * _avg_over(same, lo, hi) == pytest.approx(want_same, abs=0.15)


def test_neutral_partner_multiplier_is_one():
    # wiki.gg: pairing with a Neutral (?) cat → multiplier 100% regardless of
    # orientation (the gate lives in game_compatibility, not _sexuality_mult).
    m = _cat("Dad", gender="male", sexuality_raw=0.05)
    f = _cat("Mom", gender="female", sexuality_raw=0.05)
    n = _cat("Neut", gender="?")
    # Straight male × straight female is attenuated by cos(pi/2 * 0.05);
    # involving the neutral cat removes that attenuation entirely.
    assert game_compatibility(m, f) < 1.5
    assert game_compatibility(n, m) == pytest.approx(1.5)
    assert game_compatibility(m, n) == pytest.approx(1.5)


# ── compatibility + nightly success (wiki.gg Pairing) ───────────────────────
def test_compatibility_formula_matches_wiki():
    # compat = 15% × initiator_CHA × partner_libido × lover_mult × sex_mult.
    # No lovers → lover_mult 1; female (partner) straight coeff 0.05:
    # sex_mult = cos(pi/2 * 0.05).
    male = _cat("Dad", gender="male", sexuality_raw=0.05)
    female = _cat("Mom", gender="female", sexuality_raw=0.05)
    sex = math.cos(0.5 * math.pi * 0.05)
    got = game_compatibility(male, female)  # father initiator → CHA=10
    assert got == pytest.approx(0.15 * 10.0 * 1.0 * 1.0 * sex)


def test_nightly_success_is_two_rolls_of_compat_times_comfort():
    # wiki.gg: 2 rolls each compat × sqrt(1 + 0.1×Comfort); ≤ -10 auto-fail.
    assert breeding_success_chance(0.5, 0.0) == pytest.approx(0.25)
    assert breeding_success_chance(0.5, 10.0) == pytest.approx(0.5)  # ×2 factor
    assert breeding_success_chance(0.9, 0.0) == pytest.approx(0.81)
    assert breeding_success_chance(0.5, -10.0) == 0.0
    assert breeding_success_chance(0.5, -20.0) == 0.0


# ── kinship recursion (wiki.gg § Inbreeding) ────────────────────────────────
def test_kinship_matches_wiki_inbreeding_escalation():
    # wiki.gg: breeding a cat with its younger parent each generation from two
    # unrelated strays gives 0 → 25 → 37.5 → 50 → 59.4 → ... (and >90% by
    # the 12th generation). This exercises the vendored recursion (self =
    # (1+inbreeding)/2, younger/older = average over parents).
    s1 = SimpleNamespace(generation=0, parent_a=None, parent_b=None)
    s2 = SimpleNamespace(generation=0, parent_a=None, parent_b=None)

    def breed(a, b):
        return SimpleNamespace(
            generation=max(a.generation, b.generation) + 1, parent_a=a,
            parent_b=b)

    want = [0, 25, 37.5, 50, 59.4, 67.2, 73.4, 78.5, 82.6, 85.9, 88.5,
            90.7, 92.5]
    c1 = breed(s1, s2)
    c2 = breed(c1, s1)
    series = [0.0, 100.0 * kinship_coi(s1, c1)]
    prev2, prev1 = c1, c2
    for _ in range(2, len(want)):
        new = breed(prev1, prev2)
        series.append(100.0 * kinship_coi(prev1, prev2))
        prev2, prev1 = prev1, new
    for got, exp in zip(series, want):
        assert got == pytest.approx(exp, abs=0.3)
    assert series[11] > 90.0  # "after 12, inbreeding exceeds 90%"
