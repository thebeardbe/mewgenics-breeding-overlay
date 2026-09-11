"""Overlay wording for a blocked partner row (``blocked_reason``, pure).

The vendored ``can_breed`` rejects every same-gender pair with one generic
message before the game's compatibility gate is considered, so the overlay
re-describes straight same-sex pairs from their real compatibility: below the
gate the game never attempts the mating, at or above it the pair mates but
yields no kitten (raising the Gay Stray chance). Everything else keeps the
vendored reason.
"""

import math
from types import SimpleNamespace

import pytest

from mewgenics_overlay.core.session import (
    FAMILY_BLOCK_REASON,
    GAME_COMPATIBILITY_MIN,
    blocked_reason,
)
from mewgenics_overlay.vendor.breeding import game_compatibility

EM_DASH = "\u2014"


def _cat(gender, cha=5, libido=0.5, sexuality=0.5, lovers=None):
    """Minimal stub with exactly what ``game_compatibility`` reads."""
    return SimpleNamespace(
        gender=gender,
        sexuality_raw=sexuality,
        total_stats={"CHA": cha},
        libido=libido,
        lovers=lovers or [],
    )


def test_direct_family_returns_family_label():
    a, b = _cat("male"), _cat("female")
    assert blocked_reason(a, b, "some vendored reason", direct_family=True) \
        == FAMILY_BLOCK_REASON


def test_straight_same_gender_below_gate_says_will_not_mate():
    # Low charisma, low libido, nearly straight: compatibility is far below
    # the game's line.
    a = _cat("female", cha=1, libido=0.1, sexuality=0.1)
    b = _cat("female", cha=1, libido=0.1, sexuality=0.1)
    compat = game_compatibility(a, b)
    assert compat < GAME_COMPATIBILITY_MIN

    reason = blocked_reason(a, b, "vendored")
    assert "will not mate" in reason
    assert f"{compat:.3f}" in reason
    assert f"{GAME_COMPATIBILITY_MIN:.2f}" in reason
    assert "no kitten" not in reason
    assert "Gay Stray" not in reason


def test_same_gender_at_or_above_gate_describes_gay_stray():
    a = _cat("female", cha=5, libido=1.0, sexuality=1.0)
    b = _cat("female", cha=5, libido=1.0, sexuality=1.0)
    compat = game_compatibility(a, b)
    assert compat >= GAME_COMPATIBILITY_MIN

    reason = blocked_reason(a, b, "vendored")
    assert "mate but produce no kitten" in reason
    assert "Gay Stray" in reason
    assert f"{compat:.3f}" in reason


def test_exactly_at_the_gate_counts_as_above(monkeypatch):
    monkeypatch.setattr(
        "mewgenics_overlay.core.session.game_compatibility",
        lambda a, b: GAME_COMPATIBILITY_MIN,
    )
    reason = blocked_reason(_cat("male"), _cat("male"), "vendored")
    assert "mate but produce no kitten" in reason
    assert "Gay Stray" in reason


def test_just_below_the_gate_counts_as_below(monkeypatch):
    monkeypatch.setattr(
        "mewgenics_overlay.core.session.game_compatibility",
        lambda a, b: math.nextafter(GAME_COMPATIBILITY_MIN, 0.0),
    )
    reason = blocked_reason(_cat("male"), _cat("male"), "vendored")
    assert "will not mate" in reason
    assert "Gay Stray" not in reason


def test_neutral_gender_returns_vendored_reason_unchanged():
    vendored = "Some other vendored block"
    a = _cat("?")
    b = _cat("female")
    assert blocked_reason(a, b, vendored) == vendored
    assert blocked_reason(b, a, vendored) == vendored


def test_opposite_sex_returns_vendored_reason_unchanged():
    vendored = "These cats hate each other"
    assert blocked_reason(_cat("male"), _cat("female"), vendored) == vendored
    assert blocked_reason(_cat("female"), _cat("male"), vendored) == vendored


@pytest.mark.parametrize("reason", [
    FAMILY_BLOCK_REASON,
    blocked_reason(_cat("female", 1, 0.1, 0.1),
                   _cat("female", 1, 0.1, 0.1), "v"),
    blocked_reason(_cat("female", 5, 1.0, 1.0),
                   _cat("female", 5, 1.0, 1.0), "v"),
    blocked_reason(_cat("?"), _cat("female"), "vendored"),
    blocked_reason(_cat("male"), _cat("female"), "vendored"),
])
def test_no_returned_string_contains_an_em_dash(reason):
    assert EM_DASH not in reason
