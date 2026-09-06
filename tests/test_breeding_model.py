"""1.1 breeding-model regression tests against the vendored (fork-synced) math.

Covers the three changes we ported from whyayala/MewgenicsBreedingManager v5.9.5:
  * opposite-sex compatibility is gated by the FEMALE's sexuality
  * neutral ('?') cats ignore both orientations (multiplier 1.0 on both sides)
  * negative-Stimulation inheritance weight uses |stim| + a [0,1] clamp
"""

from types import SimpleNamespace

import pytest

from mewgenics_overlay.vendor.breeding import game_compatibility
from mewgenics_overlay.vendor.save_parser import (
    _stimulation_inheritance_weight,
    _defect_inheritance_weight,
)


def bcat(name, gender, sexuality_raw, cha=10, libido=1.0):
    """Minimal cat stub for game_compatibility (total_stats + traits)."""
    return SimpleNamespace(
        name=name, gender=gender, sexuality_raw=sexuality_raw,
        total_stats={"CHA": cha, "STR": 5, "DEX": 5, "CON": 5, "INT": 5,
                     "SPD": 5, "LCK": 5},
        libido=libido, lovers=None,
    )


def test_opposite_sex_gated_by_female_sexuality():
    gay_m = bcat("Guy", "male", 0.95)
    gay_f = bcat("Galia", "female", 0.95)
    straight_f = bcat("Stella", "female", 0.05)
    straight_m = bcat("Stan", "male", 0.05)
    # Gay male × straight female: female sexuality ~ cos(0) = 1 → healthy compat.
    c_gm = game_compatibility(gay_m, straight_f)
    # Straight male × gay female: the female (mother) sexuality ≈ 0.08 → tiny.
    c_gf = game_compatibility(straight_m, gay_f)
    assert c_gm > 1.0
    assert c_gf < 0.2
    assert c_gf < 0.2 * c_gm   # gated hard by the female's orientation


def test_neutral_cat_ignores_both_orientations():
    n = bcat("Neut", "?", 0.0)
    gay = bcat("Gay", "female", 0.95)
    straight = bcat("Het", "female", 0.05)
    # Wiki: with a neutral cat either side, the multiplier is 1 for BOTH cats,
    # so partner orientation must not matter.
    assert game_compatibility(n, gay) == pytest.approx(
        game_compatibility(n, straight))
    # …and both equal the plain formula 0.15 * CHA * libido.
    assert game_compatibility(n, gay) == pytest.approx(0.15 * 10 * 1.0)


def test_negative_stimulation_weight_is_clamped():
    # Below -200 the old formula (no abs) flipped past 100 %.
    assert 0.0 <= _stimulation_inheritance_weight(-500) <= 1.0
    assert _stimulation_inheritance_weight(-500) == pytest.approx(0.0, abs=1e-9)
    assert _stimulation_inheritance_weight(0.0) == pytest.approx(0.5)
    assert _stimulation_inheritance_weight(200.0) == pytest.approx(0.75)


def test_defect_inheritance_penalised_by_inbreeding():
    # The fork helper returns the ORDINARY-part favour weight; a kitten
    # inherits a parent's defect when the ordinary side LOSES the roll
    # (chance = 1 - weight). Inbreeding lowers the ordinary weight via
    # effective stim = stim - 2 x inbreeding%, so inbred kittens inherit
    # parental defects far more often.
    clean = _defect_inheritance_weight(50.0, 0.0)      # ord weight 0.6
    inbred = _defect_inheritance_weight(50.0, 0.3)     # ord weight ~0.43
    assert (1.0 - inbred) > (1.0 - clean)
