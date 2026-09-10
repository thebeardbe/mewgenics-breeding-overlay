"""``ui/settings_tab.py``: the Global hotkey card.

The card is three modifier checkboxes plus a one-letter field. It syncs from
the persisted combo without firing the apply action, validates the widgets
(at least one modifier + one A-Z letter) before calling the injected
``set_hotkey`` action, shows the canonical combo or the returned error, and
colours the hint with the active theme.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace  # noqa: E402

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mewgenics_overlay.ui import shortcutworker  # noqa: E402
from mewgenics_overlay.ui import theme as _theme  # noqa: E402
from mewgenics_overlay.ui.settings_tab import SettingsTab  # noqa: E402


class FakeSavePanel(QWidget):
    def __init__(self):
        super().__init__()
        self.restyles = 0

    def restyle(self):
        self.restyles += 1


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
def make_tab(qapp):
    tabs = []

    def _make(result=(True, ""), with_action=True):
        applied = []

        def set_hotkey(text):
            applied.append(text)
            return result

        actions = {
            "set_theme": lambda key: None,
            "zoom_in": lambda: None,
            "zoom_out": lambda: None,
            "zoom_reset": lambda: None,
            "set_check_updates": lambda on: None,
            "about": lambda: None,
            "report": lambda: None,
        }
        if with_action:
            actions["set_hotkey"] = set_hotkey
        panel = FakeSavePanel()
        tab = SettingsTab(actions, panel,
                          titles={k: v["title"] for k, v in _theme.THEMES.items()})
        tabs.append(tab)
        return tab, applied

    yield _make
    for tab in tabs:
        tab.hide()
        tab.close()
        tab.deleteLater()
    # Flush the deferred deletes now; left pending, a later test's event loop
    # destroys these widgets while its own hooks are patched.
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _state(tab):
    return (tab._hotkey_mods["ctrl"].isChecked(),
            tab._hotkey_mods["alt"].isChecked(),
            tab._hotkey_mods["shift"].isChecked(),
            tab._hotkey_key.text())


# ── 1. initial sync / hint ─────────────────────────────────────────────────
def test_card_starts_with_the_default_hotkey_hint(make_tab):
    tab, applied = make_tab()

    assert tab._hotkey_hint.text() == "Global hotkey: Ctrl+Shift+B"
    assert tab._hotkey_hint_error is False
    assert applied == []


def test_set_hotkey_syncs_the_widgets_without_firing_the_action(make_tab):
    tab, applied = make_tab()

    tab.set_hotkey("Ctrl+Alt+K")

    assert _state(tab) == (True, True, False, "K")
    assert applied == []
    assert tab._hotkey_hint.text() == "Global hotkey: Ctrl+Alt+K"
    assert tab._hotkey_hint_error is False


@pytest.mark.parametrize("text,expected", [
    ("Ctrl+Shift+B", (True, False, True, "B")),
    ("Alt+A", (False, True, False, "A")),
    ("Ctrl+Alt+Shift+Z", (True, True, True, "Z")),
    ("control+z", (True, False, False, "Z")),      # Control alias
])
def test_set_hotkey_accepts_aliases_and_canonicalises(make_tab, text,
                                                      expected):
    tab, applied = make_tab()

    tab.set_hotkey(text)

    assert _state(tab) == expected
    assert applied == []


def test_set_hotkey_ignores_invalid_text(make_tab):
    tab, applied = make_tab()

    tab.set_hotkey("no modifier here")

    assert _state(tab) == (False, False, False, "")
    assert applied == []


# ── 2. valid change applies through the injected action ────────────────────
def test_typing_a_letter_applies_the_combo(make_tab):
    tab, applied = make_tab()
    tab.set_hotkey("Ctrl+Shift+B")

    tab._hotkey_key.setText("k")        # a real keystroke path

    assert applied == ["Ctrl+Shift+K"]
    assert _state(tab) == (True, False, True, "K")
    assert tab._hotkey_hint.text() == "Global hotkey: Ctrl+Shift+K"


def test_toggling_a_modifier_applies_the_combo(make_tab):
    tab, applied = make_tab()
    tab.set_hotkey("Ctrl+B")

    tab._hotkey_mods["shift"].setChecked(True)

    assert applied[-1] == "Ctrl+Shift+B"
    assert tab._hotkey_hint.text() == "Global hotkey: Ctrl+Shift+B"


def test_lowercase_letter_is_stored_uppercase(make_tab):
    tab, applied = make_tab()
    tab.set_hotkey("Ctrl+B")

    tab._hotkey_key.setText("q")

    assert tab._hotkey_key.text() == "Q"
    assert applied == ["Ctrl+Q"]


def test_modifiers_are_applied_in_canonical_order(make_tab):
    tab, applied = make_tab()
    tab.set_hotkey("B")                 # unparseable sync: ignored

    tab._hotkey_mods["shift"].setChecked(True)
    tab._hotkey_mods["ctrl"].setChecked(True)
    tab._hotkey_mods["alt"].setChecked(True)
    tab._hotkey_key.setText("K")

    assert applied[-1] == "Ctrl+Alt+Shift+K"


# ── 3. invalid widget states never reach the action ────────────────────────
def test_no_modifier_input_is_rejected(make_tab):
    tab, applied = make_tab()

    tab._hotkey_key.setText("B")

    assert applied == []
    assert tab._hotkey_hint_error is True
    assert "modifier" in tab._hotkey_hint.text().lower()
    assert "letter" in tab._hotkey_hint.text().lower()


def test_non_letter_input_is_rejected(make_tab):
    tab, applied = make_tab()
    tab._hotkey_mods["ctrl"].setChecked(True)   # empty key -> invalid, ignored

    tab._hotkey_key.setText("1")

    assert applied == []
    assert tab._hotkey_hint_error is True
    assert _state(tab)[3] == "1"                # the field keeps the typed char


def test_symbol_input_is_rejected(make_tab):
    tab, applied = make_tab()
    tab.set_hotkey("Ctrl+B")

    tab._hotkey_key.setText("?")

    assert applied == []
    assert tab._hotkey_hint_error is True


def test_key_field_holds_a_single_character(make_tab):
    tab, applied = make_tab()
    tab.set_hotkey("Ctrl+Z")

    tab._hotkey_key.setText("BC")             # max length 1 drops the C

    assert len(tab._hotkey_key.text()) == 1
    assert tab._hotkey_key.text() in ("B", "C")


def test_modifier_only_change_does_not_apply_without_a_key(make_tab):
    tab, applied = make_tab()

    tab._hotkey_mods["ctrl"].setChecked(True)

    assert applied == []
    assert tab._hotkey_hint_error is True


# ── 4. platform failure surfaces the error text ────────────────────────────
def test_failed_apply_shows_the_returned_error(make_tab):
    tab, applied = make_tab(result=(False, "that combination is taken"))
    tab.set_hotkey("Ctrl+Shift+B")

    tab._hotkey_key.setText("k")

    assert applied == ["Ctrl+Shift+K"]
    assert tab._hotkey_hint.text() == "that combination is taken"
    assert tab._hotkey_hint_error is True


def test_failed_apply_without_a_reason_uses_a_generic_message(make_tab):
    tab, applied = make_tab(result=(False, ""))
    tab.set_hotkey("Ctrl+Shift+B")

    tab._hotkey_key.setText("k")

    assert tab._hotkey_hint_error is True
    assert tab._hotkey_hint.text()          # not empty


def test_missing_action_does_not_raise(make_tab):
    tab, applied = make_tab(with_action=False)
    tab.set_hotkey("Ctrl+Shift+B")

    tab._hotkey_key.setText("k")            # valid widget state, no action

    assert applied == []
    assert tab._hotkey_key.text() == "K"


# ── 5. theme-aware hint colour ─────────────────────────────────────────────
def test_error_hint_uses_the_risk_colour(make_tab):
    tab, applied = make_tab()

    tab._hotkey_key.setText("B")            # invalid: no modifier

    assert _theme.RISK_HIGH in tab._hotkey_hint.styleSheet()
    assert _theme.C_MUTED not in tab._hotkey_hint.styleSheet()


def test_valid_hint_uses_the_muted_colour(make_tab):
    tab, applied = make_tab()

    tab.set_hotkey("Ctrl+B")

    assert _theme.C_MUTED in tab._hotkey_hint.styleSheet()
    assert _theme.RISK_HIGH not in tab._hotkey_hint.styleSheet()


def test_theme_switch_restyles_an_error_hint(make_tab):
    tab, applied = make_tab()
    tab._hotkey_key.setText("B")            # leave an error showing
    other = next(k for k in _theme.THEMES if k != _theme.active_theme())

    tab.set_active_theme(other)

    assert _theme.RISK_HIGH in tab._hotkey_hint.styleSheet()
    # The card frame and the save panel restyle too.
    assert tab._save_panel.restyles >= 1


# ── 6. desktop shortcut (Linux-only) ───────────────────────────────────────
# On Linux the desktop environment owns the key, so the card offers Set up /
# Remove buttons; on Windows they do not exist and the combo widgets remain.
# The window owns the real commands, so the tab only calls injected actions.
# The actions now run on a background ShortcutWorker; these tests run the
# worker's thread inline and pump the QTimer hop, so the delivery (and the
# transient busy label) is deterministic with no sleeps.
class _InlineThread:
    """Run a worker target inline so its queued UI callback is predictable."""

    def __init__(self, target=None, name=None, daemon=None, **kwargs):
        self._target = target
        self.name = name
        self.daemon = daemon

    def start(self):
        if self._target is not None:
            self._target()


@pytest.fixture
def inline_shortcut_threads(monkeypatch):
    monkeypatch.setattr(shortcutworker, "threading",
                        SimpleNamespace(Thread=_InlineThread))


def _drain(qapp):
    """Deliver the shortcut worker's queued UI-thread callback."""
    qapp.processEvents()


def _pump(qapp, predicate, attempts=400):
    """Process events until *predicate* holds (bounded, never sleeps)."""
    for _ in range(attempts):
        if predicate():
            return True
        qapp.processEvents()
    return predicate()


def _idle(qapp, tab):
    """Pump the worker until its result label leaves the busy state."""
    return _pump(qapp, lambda: tab._shortcut_result.text()
                 not in ("Setting up\u2026", "Removing\u2026"))


@pytest.fixture
def make_shortcut_tab(qapp, inline_shortcut_threads):
    tabs = []

    def _make(result=(True, "done"), with_setup=True, with_remove=True,
              setup_result=None, remove_result=None):
        calls = []

        def setup():
            calls.append("setup")
            return result if setup_result is None else setup_result

        def remove():
            calls.append("remove")
            return result if remove_result is None else remove_result

        actions = {
            "set_theme": lambda key: None,
            "zoom_in": lambda: None,
            "zoom_out": lambda: None,
            "zoom_reset": lambda: None,
            "set_check_updates": lambda on: None,
            "about": lambda: None,
            "report": lambda: None,
        }
        if with_setup:
            actions["setup_desktop_shortcut"] = setup
        if with_remove:
            actions["remove_desktop_shortcut"] = remove
        tab = SettingsTab(
            actions, FakeSavePanel(),
            titles={k: v["title"] for k, v in _theme.THEMES.items()})
        tabs.append(tab)
        return tab, calls

    yield _make
    for tab in tabs:
        # A test may have destroyed a tab (to exercise cancel-on-destroy), so
        # teardown must tolerate an already-deleted C++ object.
        try:
            tab.hide()
            tab.close()
            tab.deleteLater()
        except RuntimeError:
            pass
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_linux_shows_the_desktop_shortcut_controls(make_shortcut_tab):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")

    tab, calls = make_shortcut_tab()

    assert tab._shortcut_set is not None
    assert tab._shortcut_remove is not None
    assert tab._shortcut_result is not None
    assert not tab._shortcut_set.isHidden()
    assert not tab._shortcut_remove.isHidden()


def test_setup_button_invokes_the_action_and_shows_the_message(
        make_shortcut_tab, qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    tab, calls = make_shortcut_tab(result=(True, "GNOME shortcut installed"))

    tab._shortcut_set.click()

    assert calls == ["setup"]
    # While the worker runs the label shows a transient busy line.
    assert tab._shortcut_result.text() == "Setting up\u2026"
    _drain(qapp)
    assert tab._shortcut_result.text() == "GNOME shortcut installed"
    assert tab._shortcut_result_error is False
    assert _theme.C_MUTED in tab._shortcut_result.styleSheet()
    assert _theme.RISK_HIGH not in tab._shortcut_result.styleSheet()


def test_failed_setup_shows_the_reason_in_the_risk_colour(
        make_shortcut_tab, qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    tab, calls = make_shortcut_tab(result=(False, "schema is not installed"))

    tab._shortcut_set.click()
    _drain(qapp)

    assert calls == ["setup"]
    assert tab._shortcut_result.text() == "schema is not installed"
    assert tab._shortcut_result_error is True
    assert _theme.RISK_HIGH in tab._shortcut_result.styleSheet()
    assert _theme.C_MUTED not in tab._shortcut_result.styleSheet()


def test_remove_button_invokes_the_remove_action(make_shortcut_tab, qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    tab, calls = make_shortcut_tab(result=(True, "removed"))

    tab._shortcut_remove.click()

    assert calls == ["remove"]
    assert tab._shortcut_result.text() == "Removing\u2026"      # busy line
    _drain(qapp)
    assert tab._shortcut_result.text() == "removed"
    assert tab._shortcut_result_error is False


def test_empty_message_falls_back_to_a_generic_result(make_shortcut_tab,
                                                      qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    tab, calls = make_shortcut_tab(result=(False, ""))

    tab._shortcut_set.click()
    _drain(qapp)

    assert tab._shortcut_result_error is True
    assert tab._shortcut_result.text()          # never blank


def test_successful_empty_message_falls_back_to_done(make_shortcut_tab,
                                                     qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    tab, calls = make_shortcut_tab(result=(True, ""))

    tab._shortcut_set.click()
    _drain(qapp)

    assert tab._shortcut_result_error is False
    assert tab._shortcut_result.text() == "Done."


def test_a_rapid_setup_then_remove_shows_the_remove_result(
        make_shortcut_tab, qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    tab, calls = make_shortcut_tab(setup_result=(True, "setup done"),
                                   remove_result=(True, "remove done"))

    tab._shortcut_set.click()          # request 1 (starts running)
    tab._shortcut_remove.click()       # request 2 (newest pending)
    assert calls == ["setup"]          # the second request is queued, not run
    assert tab._shortcut_result.text() == "Removing\u2026"

    assert _idle(qapp, tab) is True

    # Both actions run in order (serialized), and the final label is the
    # Remove result, not the superseded Set up result.
    assert calls == ["setup", "remove"]
    assert tab._shortcut_result.text() == "remove done"
    assert tab._shortcut_result_error is False


def test_destroying_the_tab_cancels_in_flight_delivery(
        make_shortcut_tab, qapp, monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    # Spy on cancel() *before* the tab is built so the destroyed connection
    # stores the patched bound method: this proves the tab really cancels its
    # worker on destroy, not merely that a deleted receiver stops receiving.
    cancelled = []
    original_cancel = shortcutworker.ShortcutWorker.cancel

    def spy_cancel(self):
        cancelled.append(self)
        original_cancel(self)

    monkeypatch.setattr(shortcutworker.ShortcutWorker, "cancel", spy_cancel)
    # Settle any deferred deletes from earlier tests first, so only this tab's
    # destroy is observed below.
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    tab, calls = make_shortcut_tab(result=(True, "too late"))
    worker = tab._shortcut_worker
    delivered = []
    tab._on_shortcut_done = lambda ok, msg: delivered.append((ok, msg))

    tab._shortcut_set.click()          # action runs, result queued
    assert calls == ["setup"]

    # Destroy the tab: the destroyed hook cancels the worker, so the queued
    # result must never reach the (gone) UI.
    tab.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _drain(qapp)
    assert _pump(qapp, lambda: bool(delivered)) is False

    assert any(w is worker for w in cancelled)
    assert delivered == []


def test_shortcut_buttons_are_safe_without_the_actions(make_shortcut_tab):
    tab, calls = make_shortcut_tab(with_setup=False, with_remove=False)
    if sys.platform.startswith("linux"):
        tab._shortcut_set.click()
        tab._shortcut_remove.click()

    assert calls == []
    assert tab._shortcut_result_text == ""


def test_theme_switch_restyles_a_failed_shortcut_result(
        make_shortcut_tab, qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    tab, calls = make_shortcut_tab(result=(False, "failed"))
    tab._shortcut_set.click()
    _drain(qapp)
    other = next(k for k in _theme.THEMES if k != _theme.active_theme())

    tab.set_active_theme(other)

    assert _theme.RISK_HIGH in tab._shortcut_result.styleSheet()


def test_windows_hides_the_controls_and_keeps_the_combo(
        monkeypatch, make_shortcut_tab):
    monkeypatch.setattr(sys, "platform", "win32")
    tab, calls = make_shortcut_tab()

    assert tab._shortcut_set is None
    assert tab._shortcut_remove is None
    assert tab._shortcut_result is None
    # The Windows path keeps the global-grab combo widgets.
    assert set(tab._hotkey_mods) == {"ctrl", "alt", "shift"}
    assert tab._hotkey_key is not None
    # Restyling must tolerate the missing Linux widgets.
    tab._restyle()
    assert tab._hotkey_hint is not None
