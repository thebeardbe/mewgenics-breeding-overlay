"""Pure tests for the update checker (no network in tests)."""

from mewgenics_overlay.ui.update_check import (
    CHECK_INTERVAL,
    due,
    is_newer,
    parse_version,
)


def test_parse_version_variants():
    assert parse_version("v0.1.47") == (0, 1, 47)
    assert parse_version("0.1.47") == (0, 1, 47)
    assert parse_version("1.2.3-beta") == (1, 2, 3)
    assert parse_version("0.2.0") == (0, 2, 0)
    assert parse_version("") == (0,)
    assert parse_version(None) == (0,)


def test_is_newer():
    assert is_newer("0.1.47", "0.1.48") is True
    assert is_newer("0.1.47", "0.2.0") is True
    assert is_newer("0.1.47", "0.1.47") is False
    assert is_newer("0.1.48", "0.1.47") is False
    assert is_newer("0.1.47", "0.1.47-beta") is False


def test_due_respects_interval():
    now = 1000000.0
    assert due(None, now) is True
    assert due(now - CHECK_INTERVAL, now) is True
    assert due(now - CHECK_INTERVAL + 10, now) is False
