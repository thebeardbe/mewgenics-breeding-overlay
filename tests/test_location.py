"""Location labels: 'Outside house' for cats on screen without a room/box."""

from types import SimpleNamespace

from mewgenics_overlay.core.session import display_location


def cat(status, room=None):
    return SimpleNamespace(status=status, room=room)


def test_roomed_house_cat_shows_room():
    assert display_location(cat("In House", "Attic")) == "Attic"


def test_unroomed_house_cat_is_outside():
    assert display_location(cat("In House", "")) == "Outside house"
    assert display_location(cat("In House", None)) == "Outside house"
    assert display_location(cat("In House", "  ")) == "Outside house"


def test_adventure_and_gone():
    assert display_location(cat("Adventure")) == "Adventure"
    assert display_location(cat("Gone")) == "Gone"
    assert display_location(cat("", "")) == "Gone"
