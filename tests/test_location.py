"""Location labels + gender-rule checks against the vendored can_breed.

Same-sex rule (Mewgenics 1.1, per wiki): same-sex pairs *mate* but never
produce a kitten — they raise the Gay-Stray chance instead. can_breed in the
vendored parser rejects every male-male / female-female pair for that reason;
only pairs with a neutral ('?') cat or opposite sexes can yield kittens.
"""

from types import SimpleNamespace

from mewgenics_overlay.core.session import display_location
from mewgenics_overlay.vendor.save_parser import can_breed


def cat(status, room=None):
    return SimpleNamespace(status=status, room=room)


def gcat(name, gender, sexuality_raw):
    return SimpleNamespace(name=name, gender=gender,
                           sexuality_raw=sexuality_raw)


def test_roomed_house_cat_shows_room():
    assert display_location(cat("In House", "Attic")) == "Attic"


def test_unroomed_house_cat_is_outside():
    assert display_location(cat("In House", "")) == "Outside house"
    assert display_location(cat("In House", None)) == "Outside house"
    assert display_location(cat("In House", "  ")) == "Outside house"


def test_same_sex_never_produces_kittens_even_when_both_bi_or_gay():
    """The old 'bi/gay same-sex can breed' reading is retired: same-sex pairs
    mate but produce no kitten (they raise the Gay-Stray chance)."""
    gay = gcat("Lady", "female", 0.93)
    bi = gcat("Baby", "female", 0.37)
    ok, reason = can_breed(gay, bi)
    assert not ok
    assert "no kitten" in reason or "Gay Stray" in reason
    gay_m = gcat("Sir", "male", 0.93)
    ok2, _ = can_breed(gay_m, gcat("M", "male", 0.95))
    assert not ok2


def test_opposite_sex_and_neutral_pairs_can_breed():
    straight = gcat("Nat", "female", 0.05)
    ok, _ = can_breed(straight, gcat("Bert", "male", 0.01))
    assert ok
    # Neutral '?' cats are not same-sex and still produce kittens normally.
    ok2, _ = can_breed(gcat("X", "?", 0.0), straight)
    assert ok2


def test_adventure_and_gone():
    assert display_location(cat("Adventure")) == "Adventure"
    assert display_location(cat("Gone")) == "Gone"
    assert display_location(cat("", "")) == "Gone"
