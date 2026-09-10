"""``ui/settings_tab.py``: the Global hotkey card.

The card is three modifier checkboxes plus a one-letter field. It syncs from
the persisted combo without firing the apply action, validates the widgets
(at least one modifier + one A-Z letter) before calling the injected
``set_hotkey`` action, shows the canonical combo or the returned error, and
colours the hint with the active theme.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

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
