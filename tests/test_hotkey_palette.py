"""``PaletteWindow._set_hotkey``: card, config and header stay in agreement.

Building a full ``PaletteWindow`` is heavy (save controller, watcher, asset
loader, threads), so this file drives the real ``PaletteWindow._set_hotkey``
method through the smallest stand-in that carries the three collaborators it
touches: the hotkey controller, the header chrome and the Settings tab. The
build-time wiring that injects the tab's ``set_hotkey`` action
(``layout.build`` -> ``window._set_hotkey``) is covered by ``test_layout.py``.

Gap: a stand-in cannot prove that a *real* PaletteWindow build keeps card,
config and header in agreement after a live failed rebind, only the method's
own contract. It also cannot exercise the real platform registration (see
``test_hotkey.py``).
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mewgenics_overlay.ui import hotkeybinding  # noqa: E402
from mewgenics_overlay.ui import theme as _theme  # noqa: E402
from mewgenics_overlay.ui.chrome import TopBar  # noqa: E402
from mewgenics_overlay.ui.hotkeyctl import HotkeyController  # noqa: E402
from mewgenics_overlay.ui.palette import PaletteWindow  # noqa: E402
from mewgenics_overlay.ui.settings_tab import SettingsTab  # noqa: E402


# ── fakes ──────────────────────────────────────────────────────────────────
class _FakeHotkey:
    """Platform handle stand-in: ``rebind`` honours ok/error and can stay
    inactive (nothing registered) or active (the restore succeeded)."""

    def __init__(self, binding, active=True, ok=True, error=""):
        self._binding = binding
        self._active = active
        self.ok = ok
        self.error = error
        self.rebind_calls = []

    @property
    def binding(self):
        return self._binding

    @property
    def active(self):
        return self._active

    @property
    def description(self):
        return self._binding.format()

    def rebind(self, binding):
        self.rebind_calls.append(binding)
        if not self.ok:
            return False, self.error
        self._binding = binding
        return True, ""

    def uninstall(self):
        self._active = False


class _FakeSavePanel(QWidget):
    def restyle(self):
        pass


class _Host:
    """Smallest stand-in for the PaletteWindow surface ``_set_hotkey`` uses."""

    def __init__(self, settings, ctl, chrome, tab=None):
        self._settings = settings
        self._hotkey_ctl = ctl
        self._chrome = chrome
        if tab is not None:
            self._settings_tab = tab


def _actions():
    return {
        "set_theme": lambda key: None,
        "zoom_in": lambda: None,
        "zoom_out": lambda: None,
        "zoom_reset": lambda: None,
        "set_check_updates": lambda on: None,
        "about": lambda: None,
        "report": lambda: None,
    }


def _state(tab):
    return (tab._hotkey_mods["ctrl"].isChecked(),
            tab._hotkey_mods["alt"].isChecked(),
            tab._hotkey_mods["shift"].isChecked(),
            tab._hotkey_key.text())


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_theme():
    original = _theme.active_theme()
    _theme.set_theme(_theme.DEFAULT_THEME)
    yield
    _theme.set_theme(original)


@pytest.fixture
def make_case(qapp):
    made = []

    def _make(previous_text="Ctrl+Shift+B", active=True, ok=True, error="",
              with_tab=True):
        previous = hotkeybinding.parse(previous_text)
        settings = {"hotkey": previous.format()}
        saves = []
        status = []
        parent = QWidget()
        ctl = HotkeyController(settings, lambda: saves.append(True),
                               status.append, lambda: None, parent=parent)
        ctl._hotkey = _FakeHotkey(binding=previous, active=active, ok=ok,
                                  error=error)
        chrome = TopBar()
        chrome.set_hotkey(previous.format())
        tab = None
        if with_tab:
            tab = SettingsTab(
                _actions(), _FakeSavePanel(),
                titles={k: v["title"] for k, v in _theme.THEMES.items()})
            tab.set_hotkey(previous.format())
        made.append((parent, chrome, tab))
        return _Host(settings, ctl, chrome, tab), settings, saves, status

    yield _make
    for parent, chrome, tab in made:
        for widget in (tab, chrome, parent):
            if widget is not None:
                widget.hide()
                widget.close()
                widget.deleteLater()
    qapp.processEvents()


# ── failed rebind: the card snaps back to the combo still in force ─────────
def test_failed_rebind_snaps_the_card_back_to_the_combo_in_force(make_case):
    host, settings, saves, status = make_case(
        previous_text="Ctrl+Shift+B", active=True, ok=False,
        error="already registered")
    # What the user left on screen: the rejected combination.
    host._settings_tab.set_hotkey("Ctrl+Alt+K")

    ok, error = PaletteWindow._set_hotkey(host, "Ctrl+Alt+K")

    assert (ok, error) == (False, "already registered")
    assert settings["hotkey"] == "Ctrl+Shift+B"      # config unchanged
    assert saves == []                                # nothing persisted
    assert _state(host._settings_tab) == (True, False, True, "B")
    assert host._settings_tab._hotkey_hint.text() == \
        "Global hotkey: Ctrl+Shift+B"
    assert host._settings_tab._hotkey_hint_error is False


def test_failed_rebind_keeps_the_header_tooltips_on_the_combo_in_force(
        make_case):
    host, settings, saves, status = make_case(ok=False, error="taken")
    host._settings_tab.set_hotkey("Ctrl+Alt+K")

    PaletteWindow._set_hotkey(host, "Ctrl+Alt+K")

    for btn in (host._chrome._btn_ct, host._chrome._btn_close):
        assert "Ctrl+Shift+B" in btn.toolTip()
        assert "Ctrl+Alt+K" not in btn.toolTip()


def test_failed_rebind_with_no_grab_names_the_tray_fallback(make_case):
    host, settings, saves, status = make_case(active=False, ok=False,
                                              error="taken")
    host._settings_tab.set_hotkey("Ctrl+Alt+K")

    PaletteWindow._set_hotkey(host, "Ctrl+Alt+K")

    assert status[-1] == (
        "global hotkey unavailable: only the overlay window responds; "
        "use the tray icon")
    assert settings["hotkey"] == "Ctrl+Shift+B"
    assert _state(host._settings_tab) == (True, False, True, "B")


def test_failed_rebind_without_a_settings_tab_does_not_crash(make_case):
    host, settings, saves, status = make_case(with_tab=False, ok=False,
                                              error="taken")

    ok, error = PaletteWindow._set_hotkey(host, "Ctrl+Alt+K")

    assert ok is False
    assert settings["hotkey"] == "Ctrl+Shift+B"


# ── successful rebind: all three agree on the new combo ────────────────────
def test_successful_rebind_keeps_card_config_and_header_in_agreement(
        make_case):
    host, settings, saves, status = make_case(ok=True)
    host._settings_tab.set_hotkey("Ctrl+Alt+K")

    ok, error = PaletteWindow._set_hotkey(host, "Ctrl+Alt+K")

    assert (ok, error) == (True, "")
    assert settings["hotkey"] == "Ctrl+Alt+K"
    assert saves == [True]
    assert _state(host._settings_tab) == (True, True, False, "K")
    assert host._settings_tab._hotkey_hint.text() == \
        "Global hotkey: Ctrl+Alt+K"
    assert "Ctrl+Alt+K" in host._chrome._btn_ct.toolTip()
