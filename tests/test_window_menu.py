"""The overlay's right-click window menu and its shared close/hide path.

``ui/palette.py`` catches context-menu events that reach the window (the
header, the empty body) and offers Hide overlay / Quit. The header's close
button and the window menu's "Hide overlay" action both run the *same*
``_on_close_clicked`` method (``layout.build`` wires ``on_hide`` to it), so
every decision test here drives both surfaces through the parametrised
``hide_source`` fixture and asserts they take the same branch.

That branch is not "hide, unless a system tray exists": the overlay hides
only when it can actually be summoned back, which requires a tray icon
(``tray_available``) or a live global hotkey (``hotkey_active``). With
neither, hiding would strand a running process with no way back, so the app
quits instead and logs why. The explicit Quit action always leaves the
application, whatever the summon options are.

These tests bind the real ``PaletteWindow`` methods onto a plain ``QWidget``
and replace the palette module's ``QApplication`` with a recording fake, so
no real ``PaletteWindow`` (save, watcher, asset loader, threads) is built and
nothing is actually quit.
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402

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
    """A real widget carrying the real ``PaletteWindow`` menu methods.

    ``tray_available`` / ``hotkey_active`` mirror the two facts the real close
    path reads. On the real palette they are set by ``app.main`` (tray built)
    and exposed as a property (global grab live); here they are plain class
    attributes the tests set to enumerate every summon-option combination.
    """

    contextMenuEvent = PaletteWindow.contextMenuEvent
    _window_menu = PaletteWindow._window_menu
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


@pytest.fixture(params=["menu", "header"])
def hide_source(request, make_host, qapp):
    """The two surfaces that share the close path, as an invoker.

    ``layout.build`` wires the header's close button to the host's
    ``_on_close_clicked`` and ``_window_menu`` connects the "Hide overlay"
    action to the same method, so both must take the same branch in every
    state. Parametrising here proves that across all the decision tests.
    """
    host = make_host()
    if request.param == "menu":
        def invoke():
            action = next(a for a in host._window_menu().actions()
                          if a.text() == "Hide overlay")
            action.trigger()
    else:
        bar = TopBar(on_hide=host._on_close_clicked, parent=host)
        host._chrome = bar

        def invoke():
            bar._btn_close.click()
    return host, invoke


def _action_texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _quit_action(menu):
    return next(a for a in menu.actions() if a.text() == "Quit")


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


# ── menu contents ──────────────────────────────────────────────────────────
def test_window_menu_offers_hide_then_a_separated_quit(make_host):
    host = make_host()

    menu = host._window_menu()

    assert isinstance(menu, QMenu)
    assert _action_texts(menu) == ["Hide overlay", "Quit"]
    assert [(a.isSeparator(), a.text()) for a in menu.actions()] == [
        (False, "Hide overlay"), (True, ""), (False, "Quit")]


def test_window_menu_is_parented_to_the_window(make_host):
    host = make_host()

    assert host._window_menu().parent() is host


# ── Hide overlay: hide when there is a way back ────────────────────────────
@pytest.mark.parametrize("tray,hotkey,expected", [
    (True, False, "tray=True, global hotkey=False"),
    (False, True, "tray=False, global hotkey=True"),
    (True, True, "tray=True, global hotkey=True"),
])
def test_close_hides_and_stays_alive_when_it_can_be_summoned(
        hide_source, fake_app, qapp, caplog, tray, hotkey, expected):
    host, invoke = hide_source
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


# ── Hide overlay: quit when there is no way back ───────────────────────────
def test_close_quits_when_neither_tray_nor_hotkey_is_available(
        hide_source, fake_app, qapp, caplog):
    host, invoke = hide_source
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


# ── Quit action always quits ───────────────────────────────────────────────
@pytest.mark.parametrize("tray,hotkey", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_quit_action_quits_regardless_of_the_summon_options(
        make_host, fake_app, qapp, tray, hotkey):
    host = make_host()
    host.tray_available = tray
    host.hotkey_active = hotkey
    host.show()
    qapp.processEvents()

    _quit_action(host._window_menu()).trigger()
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


# ── contextMenuEvent wiring ────────────────────────────────────────────────
def test_context_menu_event_execs_the_window_menu_at_the_global_pos(
        make_host, monkeypatch, qapp):
    host = make_host()
    host.show()
    qapp.processEvents()
    seen = []
    monkeypatch.setattr(host, "_window_menu",
                        lambda: SimpleNamespace(exec=seen.append))
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                              QPoint(4, 5), QPoint(44, 55))

    host.contextMenuEvent(event)

    assert seen == [event.globalPos()]
    assert event.isAccepted()
