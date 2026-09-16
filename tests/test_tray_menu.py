"""The tray menu: non-empty, parented actions, and the Keep on top choice.

``app._build_tray`` builds the system-tray menu. It carries four window
actions (Show overlay, Toggle click-through, Keep on top, Choose save…) and a
separated Quit. Two things about it are load-bearing:

* Every action is constructed with the menu as its parent. Under this
  PySide6 a ``QAction`` with no owner is garbage-collected once
  ``_build_tray`` returns and the menu silently comes out empty; the
  parenting is what keeps the entries alive, so it is a regression test.
* "Keep on top" is checkable and seeded from ``palette._keep_on_top``, and
  its ``toggled`` signal is wired to ``palette.set_keep_on_top`` so the
  choice applies immediately and is persisted by the window controller.

The tests drive the real ``_build_tray`` with a fake ``QSystemTrayIcon``
recording the menu, an offscreen ``QApplication`` and an isolated (in-memory)
settings dict, so no real tray, save, watcher or config file is touched.
"""

from __future__ import annotations

import gc
import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mewgenics_overlay.ui import app as overlay_app  # noqa: E402
from mewgenics_overlay.ui import windowstate as ws  # noqa: E402
from mewgenics_overlay.ui.windowstate import WindowController  # noqa: E402

ACTION_TEXTS = ["Show overlay", "Toggle click-through", "Keep on top",
                "Choose save…", "Quit"]
#: The menu order, with the separator that precedes Quit.
MENU_TEXTS = ACTION_TEXTS[:4] + ["", "Quit"]


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeSignal:
    def __init__(self):
        self.slot = None

    def connect(self, slot):
        self.slot = slot

    def emit(self, *args):
        if self.slot is not None:
            self.slot(*args)


class _FakeTray:
    def __init__(self, icon, parent):
        self.icon = icon
        self.parent = parent
        self.context_menu = None
        self.tooltips = []
        self.shown = False
        self.activated = _FakeSignal()

    def setToolTip(self, text):
        self.tooltips.append(text)

    def setContextMenu(self, menu):
        self.context_menu = menu

    def show(self):
        self.shown = True


class _FakeTrayClass:
    """Stands in for ``QSystemTrayIcon`` and records the built tray."""

    instances: list[_FakeTray] = []
    available = True

    @classmethod
    def isSystemTrayAvailable(cls):
        return cls.available

    def __new__(cls, *args, **kwargs):
        tray = _FakeTray(*args, **kwargs)
        cls.instances.append(tray)
        return tray


@pytest.fixture
def fake_tray(monkeypatch):
    _FakeTrayClass.instances = []
    monkeypatch.setattr(overlay_app, "QSystemTrayIcon", _FakeTrayClass)
    yield _FakeTrayClass
    _FakeTrayClass.instances = []


class _FakePalette:
    """The palette surface ``_build_tray`` reads and connects."""

    def __init__(self, keep_on_top=True):
        self._keep = bool(keep_on_top)
        self.engaged = 0
        self.picked = 0
        self.toggled = 0
        self._click_through = False
        self.set_keep_calls = []

    def _engage(self):
        self.engaged += 1

    def set_click_through(self, on):
        self._click_through = bool(on)

    def _pick_save(self):
        self.picked += 1

    def toggle_activate(self):
        self.toggled += 1

    @property
    def _keep_on_top(self):
        return self._keep

    def set_keep_on_top(self, on):
        self._keep = bool(on)
        self.set_keep_calls.append(bool(on))


def _build(qapp, palette):
    tray = overlay_app._build_tray(qapp, palette)
    assert tray is not None
    return tray


def _menu(tray):
    return tray.context_menu


def _action(menu, text):
    return next(a for a in menu.actions() if a.text() == text)


# ── the menu is populated and can never come out empty ─────────────────────
def test_tray_menu_contains_every_action_with_quit_after_a_separator(qapp,
                                                                     fake_tray):
    tray = _build(qapp, _FakePalette())
    menu = _menu(tray)

    assert [a.text() for a in menu.actions()] == MENU_TEXTS
    assert _action(menu, ACTION_TEXTS[-1]).isSeparator() is False
    assert [(a.isSeparator(), a.text()) for a in menu.actions()] == [
        (False, "Show overlay"),
        (False, "Toggle click-through"),
        (False, "Keep on top"),
        (False, "Choose save…"),
        (True, ""),
        (False, "Quit"),
    ]


def test_every_tray_action_is_parented_to_the_menu(qapp, fake_tray):
    tray = _build(qapp, _FakePalette())
    menu = _menu(tray)

    # Parenting to the menu is what stops PySide6 from collecting the actions
    # once ``_build_tray`` returns (the empty-menu bug).
    for action in menu.actions():
        if not action.isSeparator():
            assert action.parent() is menu


def test_tray_menu_stays_populated_after_garbage_collection(qapp, fake_tray):
    tray = _build(qapp, _FakePalette())
    menu = _menu(tray)

    gc.collect()

    assert [a.text() for a in menu.actions()] == MENU_TEXTS


# ── the Keep on top action reflects and drives the choice ──────────────────
def test_keep_on_top_action_is_checkable_and_seeded_on(qapp, fake_tray):
    tray = _build(qapp, _FakePalette(keep_on_top=True))

    action = _action(_menu(tray), "Keep on top")
    assert action.isCheckable() is True
    assert action.isChecked() is True


def test_keep_on_top_action_is_seeded_from_a_stored_off_choice(qapp,
                                                               fake_tray):
    tray = _build(qapp, _FakePalette(keep_on_top=False))

    assert _action(_menu(tray), "Keep on top").isChecked() is False


def test_toggling_the_action_applies_the_choice_live(qapp, fake_tray):
    palette = _FakePalette(keep_on_top=True)
    tray = _build(qapp, palette)
    action = _action(_menu(tray), "Keep on top")

    action.setChecked(False)

    assert palette.set_keep_calls == [False]
    assert palette._keep_on_top is False

    action.setChecked(True)

    assert palette.set_keep_calls == [False, True]
    assert palette._keep_on_top is True


class _FakeApp:
    def __init__(self):
        self.quits = 0

    def quit(self):
        self.quits += 1


def test_show_save_and_quit_actions_reach_their_targets(qapp, fake_tray):
    palette = _FakePalette()
    fake_app = _FakeApp()
    tray = overlay_app._build_tray(fake_app, palette)
    menu = _menu(tray)

    _action(menu, "Show overlay").trigger()
    _action(menu, "Toggle click-through").trigger()
    _action(menu, "Choose save…").trigger()
    _action(menu, "Quit").trigger()

    assert palette.engaged == 1
    assert palette._click_through is True
    assert palette.picked == 1
    assert fake_app.quits == 1


# ── the tray choice clears/restores the real window topmost state ──────────
class _FakeChrome:
    def set_click_through(self, value):
        pass


class _DelegatingPalette:
    """A palette surface whose keep-on-top delegates to a real controller."""

    def __init__(self, ctl):
        self._ctl = ctl
        self._click_through = False

    def _engage(self):
        pass

    def set_click_through(self, on):
        self._click_through = bool(on)

    def _pick_save(self):
        pass

    def toggle_activate(self):
        pass

    @property
    def _keep_on_top(self):
        return self._ctl.keep_on_top

    def set_keep_on_top(self, on):
        self._ctl.set_keep_on_top(on)


def test_toggling_the_tray_action_clears_and_restores_topmost(qapp, fake_tray,
                                                              monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    window = QWidget()
    settings = {}
    saves = []
    ctl = WindowController(window, _FakeChrome(), settings,
                           lambda: saves.append(dict(settings)))
    ctl.configure_frame()
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    tray = _build(qapp, _DelegatingPalette(ctl))
    action = _action(_menu(tray), "Keep on top")
    assert action.isChecked() is True

    action.setChecked(False)

    assert not (window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert settings["keep_on_top"] is False
    assert saves[-1]["keep_on_top"] is False

    action.setChecked(True)

    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert settings["keep_on_top"] is True

    window.deleteLater()
    qapp.processEvents()
