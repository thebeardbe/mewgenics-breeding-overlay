"""``PaletteWindow.show_in_game`` / ``attach_bridge``: the outbound path.

"Show in game" asks the companion mod to select a cat. The palette is the one
outbound path: with no bridge attached it must log (INFO), post a status line
and report failure; with a bridge that reaches no client it must warn, post a
status line and report failure; and when a client receives the select it must
report success and post a status line. ``bridge_available`` gates the menu item
on a bridge that is attached *and* running.

Building a full ``PaletteWindow`` is heavy (save controller, watcher, asset
loader, threads), so these tests call the real methods on the smallest
stand-in that carries the attributes they read (``_bridge`` and
``_set_status``), the same convention as ``test_palette_shortcut.py``. The
bridge controller is a recording fake, so no socket is opened.

Gap: a stand-in proves the method's contract, not that a live
``PaletteWindow`` build wires the context-menu item and Ctrl+G to it; those
wiring paths are covered by ``test_layout.py`` and ``test_app_bootstrap.py``.
"""

from __future__ import annotations

import os
import logging

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from mewgenics_overlay.ui import palette  # noqa: E402

LOG_NAME = "mewgenics_overlay.ui"


class _FakeBridge:
    """Records every select and returns a scripted delivery count."""

    def __init__(self, delivered=1, running=True):
        self.delivered = delivered
        self.running = running
        self.keys = []

    def send_select(self, key):
        self.keys.append(key)
        return self.delivered


def _host(bridge=None, statuses=None):
    # PaletteWindow.__init__ always sets _bridge (to None until attach_bridge
    # runs) and carries a _set_status(status) method; the stand-in carries the
    # same surface, recording every status line the outbound path posts.
    # attach_bridge also re-gates the card's "Show in game" button through
    # _refresh_show_in_game, so the stand-in records that call too (the button
    # gate itself is covered by test_show_in_game_button.py).
    host = SimpleNamespace(_bridge=bridge)
    host.statuses = [] if statuses is None else statuses
    host._set_status = host.statuses.append
    host.refreshes = []
    host._refresh_show_in_game = lambda: host.refreshes.append(True)
    return host


def _records(caplog, level):
    return [r for r in caplog.records if r.name == LOG_NAME
            and r.levelno == level]


# ── no bridge attached ─────────────────────────────────────────────────────
def test_no_bridge_returns_false_and_logs_at_info(caplog):
    host = _host()
    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        result = palette.PaletteWindow.show_in_game(host, 341)

    assert result is False
    infos = _records(caplog, logging.INFO)
    assert len(infos) == 1
    assert "not attached" in infos[0].getMessage()
    assert "341" in infos[0].getMessage()
    # A missing bridge is not an error condition.
    assert _records(caplog, logging.WARNING) == []
    assert host.statuses == ["in-game bridge is off"]


def test_an_explicit_none_bridge_is_the_same_silent_failure(caplog):
    # Unset really means None, not a missing attribute: the palette initialises
    # ``_bridge = None`` and only attach_bridge replaces it.
    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        assert palette.PaletteWindow.show_in_game(_host(None), 1) is False
    assert len(_records(caplog, logging.INFO)) == 1


# ── availability ───────────────────────────────────────────────────────────
def _available(host):
    return palette.PaletteWindow.bridge_available.fget(host)


def test_bridge_available_is_false_without_a_bridge():
    assert _available(_host()) is False
    assert _available(_host(None)) is False


def test_bridge_available_tracks_the_controller_running_state():
    assert _available(_host(_FakeBridge(running=True))) is True
    assert _available(_host(_FakeBridge(running=False))) is False


def test_attach_bridge_makes_availability_follow_the_controller():
    running = _FakeBridge(running=True)
    stopped = _FakeBridge(running=False)
    host = _host()

    palette.PaletteWindow.attach_bridge(host, running)
    assert _available(host) is True
    assert host.refreshes == [True]

    palette.PaletteWindow.attach_bridge(host, stopped)
    assert _available(host) is False
    assert host.refreshes == [True, True]


# ── a bridge that delivers to nobody ───────────────────────────────────────
def test_undelivered_select_returns_false_and_warns_naming_the_key(caplog):
    bridge = _FakeBridge(delivered=0)
    host = _host(bridge)
    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        result = palette.PaletteWindow.show_in_game(host, 900)

    assert result is False
    warnings = _records(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "900" in warnings[0].getMessage()
    # The attempt still went out exactly once, and the status line explains.
    assert bridge.keys == [900]
    assert host.statuses == ["no game connected - is the mod running?"]


def test_a_negative_delivery_count_is_a_failure(caplog):
    # Only a positive count means the game answered.
    bridge = _FakeBridge(delivered=-1)
    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        result = palette.PaletteWindow.show_in_game(_host(bridge), 7)

    assert result is False
    assert len(_records(caplog, logging.WARNING)) == 1
    assert bridge.keys == [7]


# ── a bridge that delivers ─────────────────────────────────────────────────
def test_delivered_select_returns_true_and_sends_the_key_once(caplog):
    bridge = _FakeBridge(delivered=1)
    host = _host(bridge)
    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        result = palette.PaletteWindow.show_in_game(host, 423)

    assert result is True
    assert bridge.keys == [423]
    infos = _records(caplog, logging.INFO)
    assert len(infos) == 1
    assert "423" in infos[0].getMessage()
    assert _records(caplog, logging.WARNING) == []
    assert host.statuses == ["asked the game to select this cat"]


def test_delivery_to_several_clients_still_reports_success():
    bridge = _FakeBridge(delivered=3)
    assert palette.PaletteWindow.show_in_game(_host(bridge), 1) is True


@pytest.mark.parametrize("key", [0, 1, 2 ** 63 - 1])
def test_the_key_is_forwarded_unchanged(key):
    bridge = _FakeBridge()
    palette.PaletteWindow.show_in_game(_host(bridge), key)
    assert bridge.keys == [key]


# ── attaching ──────────────────────────────────────────────────────────────
def test_attach_bridge_stores_the_controller_and_makes_it_reachable():
    ctl = _FakeBridge(delivered=1)
    host = _host()

    palette.PaletteWindow.attach_bridge(host, ctl)

    assert host._bridge is ctl
    assert host.refreshes == [True]
    assert palette.PaletteWindow.show_in_game(host, 12) is True
    assert ctl.keys == [12]


def test_attach_bridge_replaces_a_previous_controller():
    first, second = _FakeBridge(delivered=0), _FakeBridge(delivered=1)
    host = _host()

    palette.PaletteWindow.attach_bridge(host, first)
    palette.PaletteWindow.attach_bridge(host, second)

    assert host._bridge is second
    assert host.refreshes == [True, True]
    assert palette.PaletteWindow.show_in_game(host, 55) is True
    assert first.keys == []
    assert second.keys == [55]


# ── the focused-cat guard shared by the button and the shortcut ────────────
def _focused_host(cat, bridge=None):
    # The guard reads _focus and delegates to show_in_game; bind both the way a
    # real PaletteWindow carries them so the stand-in can exercise the guard.
    host = _host(bridge)
    host._focus = cat
    host.show_in_game = lambda key: palette.PaletteWindow.show_in_game(host, key)
    return host


def test_show_focused_in_game_forwards_the_focused_key():
    bridge = _FakeBridge(delivered=1)
    host = _focused_host(SimpleNamespace(db_key=341), bridge)

    assert palette.PaletteWindow.show_focused_in_game(host) is True
    assert bridge.keys == [341]
    assert host.statuses == ["asked the game to select this cat"]


def test_show_focused_in_game_with_no_cat_logs_and_sends_nothing(caplog):
    bridge = _FakeBridge(delivered=1)
    host = _focused_host(None, bridge)

    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        assert palette.PaletteWindow.show_focused_in_game(host) is False

    assert bridge.keys == []
    infos = _records(caplog, logging.INFO)
    assert len(infos) == 1
    assert "nothing focused" in infos[0].getMessage()
    assert host.statuses == []
