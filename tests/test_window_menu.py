"""The overlay's right-click is a no-op and the shared close/hide path.

``ui/palette.py`` no longer catches context-menu events: the overlay-wide
right-click menu (Hide overlay / Quit) was removed, because the window actions
live on the tray icon whose menu already offers Show overlay, Toggle
click-through, Keep on top, Choose save and Quit. Right-clicking the header or
the empty body therefore does nothing again, while the partner/donation row
menus and the search field's standard menu are unaffected (covered by
``test_partneractions.py``, ``test_donations_tab_menu.py`` and
``test_layout.py``).

The header close button still runs ``_on_close_clicked``: it hides the overlay
only when it can actually be summoned back, which requires a tray icon
(``tray_available``) or a live global hotkey (``hotkey_active``). With neither,
hiding would strand a running process with no way back, so the app quits
instead and logs why. The tray's Quit action is separate and always leaves the
application, whatever the summon options are.

These tests bind the real ``PaletteWindow`` methods onto a plain ``QWidget``
and replace the palette module's ``QApplication`` with a recording fake, so no
real ``PaletteWindow`` (save, watcher, asset loader, threads) is built and
nothing is actually quit.
"""

from __future__ import annotations

import logging
import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget  # noqa: E402

from mewgenics_overlay.ui import palette  # noqa: E402
from mewgenics_overlay.ui.chrome import TopBar  # noqa: E402
from mewgenics_overlay.ui.palette import PaletteWindow  # noqa: E402

LOG = "mewgenics_overlay.ui"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeApp:
    """Records ``quit()`` without tearing down the test process."""

    def __init__(self):
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1


class _FakeApplicationClass:
    """Stand-in for the ``QApplication`` class in the palette module."""

    def __init__(self, app):
        self._app = app

    def instance(self):
        return self._app


class WindowHost(QWidget):
    """A real widget carrying the real ``PaletteWindow`` close methods.

    ``tray_available`` / ``hotkey_active`` mirror the two facts the real close
    path reads. On the real palette they are set by ``app.main`` (tray built)
    and exposed as a property (global grab live); here they are plain class
    attributes the tests set to enumerate every summon-option combination.
    """

    _on_close_clicked = PaletteWindow._on_close_clicked
    _quit = PaletteWindow._quit

    tray_available = False
    hotkey_active = False


@pytest.fixture
def make_host(qapp):
    hosts = []

    def _make():
        host = WindowHost()
        host.resize(240, 120)
        hosts.append(host)
        return host

    yield _make
    for host in hosts:
        host.hide()
        host.close()
        host.deleteLater()
    qapp.processEvents()


@pytest.fixture
def fake_app(monkeypatch):
    """Replace the palette module's ``QApplication`` with a recorder."""
    fake = _FakeApp()
    monkeypatch.setattr(palette, "QApplication", _FakeApplicationClass(fake))
    return fake


@pytest.fixture
def close_invoker(make_host, qapp):
    """The header close button, wired to the host's real close path.

    ``layout.build`` wires the header's close button to the host's
    ``_on_close_clicked``, so clicking it must take the branch under test.
    """
    host = make_host()
    bar = TopBar(on_hide=host._on_close_clicked, parent=host)
    host._chrome = bar

    def invoke():
        bar._btn_close.click()

    return host, invoke


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


# ── the overlay menu is gone; right-click is a no-op ───────────────────────
def test_the_overlay_no_longer_defines_a_context_menu_handler():
    # Regression: the window-wide Hide/Quit menu was removed in favour of the
    # tray menu, so the palette must not define its own handler or menu.
    assert "contextMenuEvent" not in PaletteWindow.__dict__
    assert "_window_menu" not in PaletteWindow.__dict__


def test_right_click_on_the_overlay_is_ignored(make_host, qapp):
    host = make_host()
    host.show()
    qapp.processEvents()
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                              QPoint(4, 5), QPoint(44, 55))

    qapp.sendEvent(host, event)

    # No handler accepts it, so nothing opens (Qt's default ignores it).
    assert event.isAccepted() is False


class _RecordingField(QLineEdit):
    """A search-field stand-in that records its own context menu handling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.context_events = 0

    def contextMenuEvent(self, event):  # noqa: N802 (Qt API)
        self.context_events += 1
        event.accept()


def test_a_field_context_menu_still_reaches_the_field(make_host, qapp):
    # Removing the window-wide handler must not swallow the child widgets'
    # own menus: the search field keeps its standard context menu.
    host = make_host()
    field = _RecordingField(host)
    field.setText("Meeko")
    host.show()
    qapp.processEvents()
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                              QPoint(2, 2), QPoint(2, 2))

    qapp.sendEvent(field, event)

    assert field.context_events == 1
    assert event.isAccepted() is True


# ── close button: hide when there is a way back ────────────────────────────
@pytest.mark.parametrize("tray,hotkey,expected", [
    (True, False, "tray=True, global hotkey=False"),
    (False, True, "tray=False, global hotkey=True"),
    (True, True, "tray=True, global hotkey=True"),
])
def test_close_hides_and_stays_alive_when_it_can_be_summoned(
        close_invoker, fake_app, qapp, caplog, tray, hotkey, expected):
    host, invoke = close_invoker
    host.tray_available = tray
    host.hotkey_active = hotkey
    host.show()
    qapp.processEvents()

    with caplog.at_level(logging.INFO, logger=LOG):
        invoke()
    qapp.processEvents()

    assert host.isVisible() is False          # hidden, not destroyed
    assert fake_app.quit_calls == 0           # the process is still alive
    # The decision and the summon path that justified it are logged.
    assert any("close: hiding the overlay" in m and expected in m
               for m in _messages(caplog)), _messages(caplog)


# ── close button: quit when there is no way back ───────────────────────────
def test_close_quits_when_neither_tray_nor_hotkey_is_available(
        close_invoker, fake_app, qapp, caplog):
    host, invoke = close_invoker
    host.tray_available = False
    host.hotkey_active = False
    host.show()
    qapp.processEvents()

    with caplog.at_level(logging.INFO, logger=LOG):
        invoke()
    qapp.processEvents()

    assert fake_app.quit_calls == 1
    # Hiding a window with no way to summon it would strand the process, so
    # the branch and its reason must be visible in the log.
    joined = "\n".join(_messages(caplog))
    assert "close: quitting the app" in joined
    assert "no tray icon" in joined
    assert "no global hotkey" in joined


# ── the explicit Quit path always quits ────────────────────────────────────
@pytest.mark.parametrize("tray,hotkey", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_quit_always_leaves_the_application(make_host, fake_app, qapp,
                                            tray, hotkey):
    host = make_host()
    host.tray_available = tray
    host.hotkey_active = hotkey
    host.show()
    qapp.processEvents()

    host._quit()
    qapp.processEvents()

    assert fake_app.quit_calls == 1


def test_quit_exits_even_without_a_tray_icon(make_host, fake_app, qapp):
    # Regression: the old close path hid only when a system tray existed and
    # otherwise quit; the palette no longer consults QSystemTrayIcon at all,
    # and the explicit Quit path always leaves the application.
    host = make_host()

    host._quit()

    assert fake_app.quit_calls == 1
    assert not hasattr(palette, "QSystemTrayIcon")


def test_quit_without_a_running_qapplication_is_a_noop(monkeypatch, qapp):
    class _NoApp:
        @staticmethod
        def instance():
            return None

    monkeypatch.setattr(palette, "QApplication", _NoApp)
    host = WindowHost()

    host._quit()               # must not raise
