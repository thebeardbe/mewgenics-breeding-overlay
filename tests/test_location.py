"""Location labels: 'Outside house' for cats on screen without a room/box."""

from types import SimpleNamespace

from mewgenics_overlay.core.session import display_location, same_sex_straight_block


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


def test_same_sex_requires_both_bi_or_gay():
    straight = gcat("Nat", "female", 0.05)
    bi = gcat("Baby", "female", 0.37)
    gay = gcat("Lady", "female", 0.93)
    assert same_sex_straight_block(straight, bi) != ""
    assert same_sex_straight_block(bi, gay) == ""
    # opposite sex is always fine
    assert same_sex_straight_block(straight, gcat("Bert", "male", 0.01)) == ""
    # ? gender exempt
    assert same_sex_straight_block(gcat("X", "?", 0.0), bi) == ""


def test_adventure_and_gone():
    assert display_location(cat("Adventure")) == "Adventure"
    assert display_location(cat("Gone")) == "Gone"
    assert display_location(cat("", "")) == "Gone"
