"""WindowController: OS-level window behaviour extracted from PaletteWindow.

``mewgenics_overlay.ui.windowstate.WindowController`` owns framing, geometry
restore/save, pinning/always-on-top, click-through, summon/hide and the Qt
event hooks the palette calls from its thin ``showEvent`` / ``hideEvent`` /
``changeEvent`` overrides.

These tests drive it with an offscreen ``QWidget`` and a recording stand-in
for the header bar, so no ``PaletteWindow`` (and therefore no save, watcher or
timers) is ever constructed. The native Windows topmost path cannot run on
Linux; the test for it asserts the failure is swallowed *and logged* rather
than raised.
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QRect, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mewgenics_overlay.ui import windowstate as ws  # noqa: E402
from mewgenics_overlay.ui.windowstate import (  # noqa: E402
    CLICK_THROUGH_DEFAULT,
    PIN_DEFAULT,
    WindowController,
)


# ── fixtures / helpers ─────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeChrome:
    """Records the state syncs WindowController pushes to the header bar."""

    def __init__(self):
        self.pinned = []
        self.click_through = []

    def set_pinned(self, value):
        self.pinned.append(bool(value))

    def set_click_through(self, value):
        self.click_through.append(bool(value))


@pytest.fixture
def make_ctl(qapp):
    """Build (controller, window, chrome, settings, saves) triples.

    Windows are torn down after each test; the controller is parented to its
    window, so deleting the window takes it with it.
    """
    windows = []

    def _make(settings=None):
        window = QWidget()
        windows.append(window)
        chrome = FakeChrome()
        settings = settings if settings is not None else {}
        saves = []
        ctl = WindowController(window, chrome, settings,
                               lambda: saves.append(dict(settings)))
        return SimpleNamespace(ctl=ctl, window=window, chrome=chrome,
                               settings=settings, saves=saves)

    yield _make

    for window in windows:
        window.hide()
        window.close()
        window.deleteLater()
    qapp.processEvents()


def _on_screen_rect(margin=10, width=400, height=300) -> QRect:
    """A rect guaranteed to intersect the (offscreen) primary screen."""
    geo = QGuiApplication.screens()[0].availableGeometry()
    return QRect(geo.x() + margin, geo.y() + margin,
                 min(width, geo.width()), min(height, geo.height()))


# ── 1. framing flags ───────────────────────────────────────────────────────
def test_configure_frame_is_frameless_and_topmost_away_from_hyprland(
        make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl()

    h.ctl.configure_frame()

    flags = h.window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint


def test_configure_frame_is_frameless_without_the_qt_topmost_flag_on_hyprland(
        make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "1")
    h = make_ctl()

    h.ctl.configure_frame()

    flags = h.window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint)


def test_configure_frame_never_sets_the_qt_topmost_flag_on_windows(
        make_ctl, monkeypatch):
    # On Windows the Qt flag re-creates the HWND (the "lost overlay" bug), so
    # topmost is done natively instead.
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl()

    h.ctl.configure_frame()

    flags = h.window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint)


# ── 2. geometry round-trip ─────────────────────────────────────────────────
def test_geometry_save_then_restore_round_trips_through_settings(make_ctl):
    rect = _on_screen_rect()
    first = make_ctl()
    first.window.setGeometry(rect)

    first.ctl.save_geometry()

    assert first.settings["window_rect"] == [
        rect.x(), rect.y(), rect.width(), rect.height()]
    assert len(first.saves) == 1

    # A fresh window with the same settings comes back at the same place.
    second = make_ctl(settings=first.settings)
    second.ctl.restore_geometry()

    assert second.window.geometry() == rect


def test_save_geometry_calls_the_persist_callback_once_per_save(make_ctl):
    h = make_ctl()
    h.window.setGeometry(QRect(11, 22, 333, 222))

    h.ctl.save_geometry()
    h.ctl.on_hide()

    assert len(h.saves) == 2
    assert h.settings["window_rect"] == [11, 22, 333, 222]


@pytest.mark.parametrize("rect", [
    None,
    {},
    [1, 2, 3],
    [1, 2, 3, 4, 5],
    "0,0,800,600",
    42,
])
def test_restore_geometry_ignores_a_malformed_stored_rect(make_ctl, rect):
    h = make_ctl(settings={"window_rect": rect})
    before = h.window.geometry()

    h.ctl.restore_geometry()

    assert h.window.geometry() == before


def test_restore_geometry_ignores_a_rect_off_every_screen(make_ctl):
    h = make_ctl(settings={"window_rect": [-100000, -100000, 200, 150]})
    before = h.window.geometry()

    h.ctl.restore_geometry()

    assert h.window.geometry() == before


def test_restore_geometry_is_a_noop_without_a_stored_rect(make_ctl):
    h = make_ctl()               # settings carries no window_rect
    before = h.window.geometry()

    h.ctl.restore_geometry()

    assert h.window.geometry() == before
    assert "window_rect" not in h.settings


# ── 3. click-through ───────────────────────────────────────────────────────
def test_set_click_through_sets_the_attribute_and_syncs_the_chrome(make_ctl):
    h = make_ctl()

    h.ctl.set_click_through(True)

    assert h.ctl.click_through is True
    assert h.window.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    assert h.chrome.click_through == [True]

    h.ctl.set_click_through(False)

    assert h.ctl.click_through is False
    assert h.window.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    assert h.chrome.click_through == [True, False]


def test_click_through_defaults_to_interactive(make_ctl):
    h = make_ctl()

    assert h.ctl.click_through is CLICK_THROUGH_DEFAULT
    assert h.window.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    assert h.chrome.click_through == []


def test_header_click_delegates_to_set_click_through(make_ctl):
    h = make_ctl()

    h.ctl.on_click_through_clicked(True)
    h.ctl.on_click_through_clicked(False)

    assert h.chrome.click_through == [True, False]


def test_set_click_through_accepts_truthy_and_falsy_values(make_ctl):
    h = make_ctl()

    h.ctl.set_click_through(1)
    assert h.ctl.click_through is True

    h.ctl.set_click_through(0)
    assert h.ctl.click_through is False


# ── 4. pinning / always-on-top ─────────────────────────────────────────────
def test_pin_defaults_to_true_and_is_reported_through_the_property(make_ctl):
    h = make_ctl()

    assert h.ctl.pinned is PIN_DEFAULT
    assert h.chrome.pinned == []


def test_toggle_pin_syncs_the_chrome_and_never_raises_off_windows(
        make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl()

    h.ctl.toggle_pin(False)
    assert h.ctl.pinned is False
    assert h.chrome.pinned == [False]

    h.ctl.toggle_pin(True)
    assert h.ctl.pinned is True
    assert h.chrome.pinned == [False, True]


def test_toggle_pin_on_hyprland_does_not_try_to_show_or_flag(make_ctl,
                                                            monkeypatch):
    # Hyprland stacking is handled by compositor rules; the controller must
    # only update its own state and the button.
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "1")
    h = make_ctl()
    calls = []
    monkeypatch.setattr(h.ctl, "_set_topmost_win32",
                        lambda on: calls.append(on))

    h.ctl.toggle_pin(False)

    assert h.ctl.pinned is False
    assert h.chrome.pinned == [False]
    assert calls == []                 # no native call on Hyprland
    assert h.window.isVisible() is False   # and no forced show


def test_toggle_pin_uses_the_native_path_on_windows_without_raising(
        make_ctl, monkeypatch, caplog):
    """The native SetWindowPos path is unavailable under Linux.

    It must degrade to a logged warning, never an exception that would take
    the overlay down for a cosmetic pin.
    """
    monkeypatch.setattr(ws.sys, "platform", "win32")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl()
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="mewgenics_overlay.ui"):
        h.ctl.toggle_pin(True)         # must not raise

    assert h.ctl.pinned is True
    assert h.chrome.pinned == [True]
    assert any("native topmost toggle failed" in r.message
               for r in caplog.records)


# ── 5. summon / hide ───────────────────────────────────────────────────────
def test_engage_shows_and_makes_the_window_interactive(make_ctl, qapp):
    h = make_ctl()
    h.ctl.set_click_through(True)

    h.ctl.engage()
    qapp.processEvents()

    assert h.window.isVisible() is True
    assert h.ctl.click_through is False
    assert h.chrome.click_through[-1] is False


def test_toggle_activate_cycles_hidden_passive_active(make_ctl, qapp):
    h = make_ctl()
    assert h.window.isVisible() is False

    h.ctl.toggle_activate()            # hidden -> engage
    qapp.processEvents()
    assert h.window.isVisible() is True
    assert h.ctl.click_through is False

    h.ctl.set_click_through(True)      # passive (click-through) -> engage
    h.ctl.toggle_activate()
    qapp.processEvents()
    assert h.window.isVisible() is True
    assert h.ctl.click_through is False

    h.ctl.toggle_activate()            # active -> hide
    qapp.processEvents()
    assert h.window.isVisible() is False


# ── 6. Qt event hooks ──────────────────────────────────────────────────────
def test_on_show_sets_topmost_natively_on_windows(make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl()
    seen = []
    monkeypatch.setattr(h.ctl, "_set_topmost_win32", seen.append)

    h.ctl.on_show()

    assert seen == [h.ctl.pinned]


def test_on_show_is_inert_off_windows(make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    h = make_ctl()
    seen = []
    monkeypatch.setattr(h.ctl, "_set_topmost_win32", seen.append)
    flags = h.window.windowFlags()

    h.ctl.on_show()

    assert seen == []
    assert h.window.windowFlags() == flags


def test_on_hide_saves_the_geometry(make_ctl):
    h = make_ctl()
    h.window.setGeometry(QRect(7, 8, 400, 300))

    h.ctl.on_hide()

    assert h.settings["window_rect"] == [7, 8, 400, 300]
    assert len(h.saves) == 1


def test_window_deactivate_engages_click_through(make_ctl, qapp):
    h = make_ctl()
    h.window.show()
    qapp.processEvents()
    assert h.ctl.click_through is False

    h.ctl.on_change(QEvent(QEvent.Type.WindowDeactivate))

    assert h.ctl.click_through is True
    assert h.chrome.click_through[-1] is True


def test_window_deactivate_is_ignored_while_hidden(make_ctl):
    h = make_ctl()
    assert h.window.isVisible() is False

    h.ctl.on_change(QEvent(QEvent.Type.WindowDeactivate))

    assert h.ctl.click_through is False
    assert h.chrome.click_through == []


def test_window_deactivate_is_skipped_while_a_dialog_is_open(make_ctl, qapp):
    h = make_ctl()
    h.window.show()
    qapp.processEvents()
    h.ctl.dialog_open = True

    h.ctl.on_change(QEvent(QEvent.Type.WindowDeactivate))

    assert h.ctl.click_through is False
    assert h.chrome.click_through == []


def test_window_deactivate_is_a_noop_when_already_click_through(make_ctl,
                                                               qapp):
    h = make_ctl()
    h.window.show()
    qapp.processEvents()
    h.ctl.set_click_through(True)
    h.chrome.click_through.clear()

    h.ctl.on_change(QEvent(QEvent.Type.WindowDeactivate))

    assert h.ctl.click_through is True
    assert h.chrome.click_through == []


def test_non_deactivate_events_are_ignored(make_ctl, qapp):
    h = make_ctl()
    h.window.show()
    qapp.processEvents()

    h.ctl.on_change(QEvent(QEvent.Type.WindowActivate))

    assert h.ctl.click_through is False
    assert h.chrome.click_through == []


def test_dialog_open_coerces_truthy_and_falsy_values(make_ctl):
    h = make_ctl()

    h.ctl.dialog_open = 1
    assert h.ctl.dialog_open is True
    h.ctl.dialog_open = 0
    assert h.ctl.dialog_open is False
    h.ctl.dialog_open = "yes"
    assert h.ctl.dialog_open is True


# ── 7. clean shutdown ──────────────────────────────────────────────────────
def test_teardown_after_engage_saves_nothing_extra_and_is_clean(make_ctl,
                                                                qapp):
    """There is no controller shutdown(); the host owns the lifecycle.

    Closing the window must therefore leave the controller usable and
    destructible without a Qt error, and must not spuriously re-persist the
    geometry (the palette's hideEvent is the only save trigger).
    """
    h = make_ctl()
    h.ctl.configure_frame()
    h.ctl.engage()
    qapp.processEvents()

    h.window.hide()
    h.window.close()
    qapp.processEvents()

    assert h.saves == []               # no hidden extra save
    h.ctl.on_hide()                    # still callable after close
    assert len(h.saves) == 1
