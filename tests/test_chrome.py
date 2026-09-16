"""TopBar chrome: the header row extracted from PaletteWindow (split step 3).

``mewgenics_overlay.ui.chrome`` owns the frameless drag grip, the title and
status labels and the two icon buttons (click-through / hide). Keep-on-top is
now a persisted tray setting, so the header's pin button and its
state/helpers are gone; a regression test asserts they stay gone. The initial
state and the actions arrive as plain values and callables, so the bar can be
driven here without constructing a ``PaletteWindow`` (no save, watcher or
timers) and without a real window (offscreen platform).

The widget is self-contained; the only shared state it touches is the
``theme.ZOOM`` global read by ``restyle``, so an autouse fixture restores it.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from mewgenics_overlay.ui import theme as _theme  # noqa: E402
from mewgenics_overlay.ui.chrome import (  # noqa: E402
    _BTN_BASE_H,
    _BTN_BASE_W,
    _BTN_MIN_H,
    _BTN_MIN_W,
    TopBar,
    _DragLabel,
)


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_theme_zoom(monkeypatch):
    # ``restyle`` writes the module-global ZOOM via theme.set_zoom(); keep that
    # from leaking into other test files (or reading a stale value).
    monkeypatch.setattr(_theme, "ZOOM", 1.0)


@pytest.fixture
def make_bar(qapp):
    """Build isolated TopBars; tear them down so no widget survives a test."""
    bars = []

    def _make(**kwargs):
        bar = TopBar(**kwargs)
        bars.append(bar)
        return bar

    yield _make
    for bar in bars:
        bar.hide()
        bar.close()
        bar.deleteLater()
    qapp.processEvents()


def fixed_size(btn):
    return (btn.minimumWidth(), btn.minimumHeight(),
            btn.maximumWidth(), btn.maximumHeight())


# ── 1. constructor seeds button state ──────────────────────────────────────
def test_constructor_seeds_click_through_and_close_defaults(make_bar):
    bar = make_bar()

    assert bar._btn_ct.isCheckable()
    assert bar._btn_ct.isChecked() is False
    assert bar._btn_close.isCheckable() is False


def test_header_has_no_pin_button_any_more(make_bar):
    # Regression: keep-on-top moved to a persisted tray setting, so the
    # header lost its pin button, its state and its callback wiring.
    bar = make_bar()

    assert not hasattr(bar, "_btn_pin")
    assert not hasattr(bar, "set_pinned")
    assert not hasattr(bar, "_on_pin")
    buttons = bar.findChildren(QPushButton)
    assert len(buttons) == 2           # click-through + hide only
    for btn in buttons:
        assert "pin" not in btn.toolTip().lower()


@pytest.mark.parametrize("click_through", [True, False])
def test_constructor_seeds_click_through_from_the_argument(make_bar,
                                                           click_through):
    bar = make_bar(click_through=click_through)

    assert bar._btn_ct.isChecked() is click_through


# ── 2. button clicks fire the matching callback exactly once ───────────────
def test_click_through_click_fires_on_click_through_once_with_new_state(
        make_bar):
    seen = []
    bar = make_bar(click_through=False, on_click_through=seen.append)

    bar._btn_ct.click()

    assert seen == [True]
    assert bar._btn_ct.isChecked() is True

    bar._btn_ct.click()
    assert seen == [True, False]


def test_close_click_fires_on_hide_once(make_bar):
    seen = []
    bar = make_bar(on_hide=lambda: seen.append("hide"))

    bar._btn_close.click()
    bar._btn_close.click()

    assert seen == ["hide", "hide"]
    assert bar._btn_close.isCheckable() is False


def test_clicks_without_host_callbacks_do_not_raise(make_bar):
    # The host may wire only some actions; unconnected buttons must be inert.
    bar = make_bar(on_click_through=None, on_hide=None)

    bar._btn_ct.click()
    bar._btn_close.click()

    assert bar._btn_ct.isChecked() is True    # started click-through=False


# ── 3. programmatic sync must not loop back into the host ──────────────────
def test_set_click_through_syncs_button_without_firing_the_callback(make_bar):
    seen = []
    bar = make_bar(click_through=False, on_click_through=seen.append)

    bar.set_click_through(True)

    assert bar._btn_ct.isChecked() is True
    assert seen == []

    bar._btn_ct.click()
    assert seen == [False]


def test_programmatic_sync_accepts_truthy_and_falsy_values(make_bar):
    seen = []
    bar = make_bar(on_click_through=seen.append)

    bar.set_click_through(1)
    bar.set_click_through(0)

    assert bar._btn_ct.isChecked() is False
    assert seen == []


# ── 4. title / status labels ───────────────────────────────────────────────
def test_set_title_and_status_update_the_labels(make_bar):
    bar = make_bar()

    bar.set_title("🐈 Overlay - steamcampaign01.sav")
    bar.set_status("19 cats, day 42")

    assert bar.title_label.text() == "🐈 Overlay - steamcampaign01.sav"
    assert bar.status_label.text() == "19 cats, day 42"
    assert bar._title.text() == "🐈 Overlay - steamcampaign01.sav"
    assert bar._status.text() == "19 cats, day 42"


def test_status_starts_empty_and_accepts_unicode_and_clearing(make_bar):
    bar = make_bar()

    assert bar.status_label.text() == ""

    bar.set_status("Mîlø 😺 · 3 partners")
    assert bar.status_label.text() == "Mîlø 😺 · 3 partners"

    bar.set_status("")
    assert bar.status_label.text() == ""


def test_title_property_is_the_live_label(make_bar):
    bar = make_bar()

    bar.set_title("first")
    assert bar.title_label.text() == "first"

    # Mutating the exposed label is seen through the property.
    bar.title_label.setText("second")
    assert bar._title.text() == "second"


# ── 5. scale_buttons sizing maths (old header formulas) ────────────────────
@pytest.mark.parametrize("zoom,exp_w,exp_h", [
    (1.0, _BTN_BASE_W, _BTN_BASE_H),                       # 46 x 26
    (1.25, int(_BTN_BASE_W * 1.25), int(_BTN_BASE_H * 1.25)),
    (2.0, _BTN_BASE_W * 2, _BTN_BASE_H * 2),               # 92 x 52
    (3.0, _BTN_BASE_W * 3, _BTN_BASE_H * 3),
    (0.75, max(_BTN_MIN_W, int(_BTN_BASE_W * 0.75)),
     max(_BTN_MIN_H, int(_BTN_BASE_H * 0.75))),
    (0.5, _BTN_MIN_W, _BTN_MIN_H),                         # clamped to 38 x 22
])
def test_scale_buttons_uses_the_old_header_maths(make_bar, zoom, exp_w,
                                                 exp_h):
    bar = make_bar()

    bar.scale_buttons(zoom)

    for btn in (bar._btn_ct, bar._btn_close):
        assert fixed_size(btn) == (exp_w, exp_h, exp_w, exp_h), \
            f"zoom {zoom}: expected {exp_w}x{exp_h}"


def test_scale_buttons_never_goes_below_the_unchanged_minimums(make_bar):
    bar = make_bar()

    bar.scale_buttons(0.01)            # absurd zoom-out still clamps

    for btn in (bar._btn_ct, bar._btn_close):
        assert fixed_size(btn) == (_BTN_MIN_W, _BTN_MIN_H,
                                   _BTN_MIN_W, _BTN_MIN_H)


# ── 6. restyle applies the zoomed font sizes ───────────────────────────────
@pytest.mark.parametrize("zoom,grip_px,title_px,status_px", [
    (1.0, 13, 14, 11),
    (1.5, 20, 21, 16),                 # 19.5 / 21.0 / 16.5
    (2.0, 26, 28, 22),
])
def test_restyle_sets_zoomed_font_sizes_on_grip_title_status(
        make_bar, zoom, grip_px, title_px, status_px):
    bar = make_bar()

    bar.restyle(zoom)                  # must not raise

    assert f"font-size:{grip_px}px" in bar._grip.styleSheet()
    assert f"font-size:{title_px}px" in bar._title.styleSheet()
    assert f"font-size:{status_px}px" in bar._status.styleSheet()


def test_restyle_clamps_absurd_zoom_and_keeps_all_three_styles(make_bar):
    bar = make_bar()

    bar.restyle(0.0)                   # theme clamps to 0.5

    for label in (bar._grip, bar._title, bar._status):
        assert "font-size:" in label.styleSheet()


def test_restyle_updates_the_shared_theme_zoom(make_bar):
    bar = make_bar()

    bar.restyle(2.0)

    assert _theme.ZOOM == 2.0
    assert _theme.zoom_px(10) == 20


# ── drag grip (the other half of the extracted module) ─────────────────────
def test_grip_and_title_are_drag_labels_with_open_hand_cursor(make_bar):
    bar = make_bar()

    assert isinstance(bar._grip, _DragLabel)
    assert isinstance(bar._title, _DragLabel)
    for label in (bar._grip, bar._title):
        assert label.cursor().shape() == Qt.CursorShape.OpenHandCursor


# ── hotkey tooltip follows the configured combo (spec: chrome.py) ──────────
# The click-through and hide tooltips name the summon shortcut. With a
# configurable hotkey they must follow the live description instead of the
# shipped Ctrl+Shift+B default. ``TopBar`` should expose a ``set_hotkey``
# (or ``set_hotkey_tooltip``) method for the window to call after a rebind.
def test_tooltips_follow_the_live_hotkey_description(make_bar):
    bar = make_bar()
    setter = (getattr(bar, "set_hotkey", None)
              or getattr(bar, "set_hotkey_tooltip", None))

    assert setter is not None, (
        "TopBar has no method to update its tooltips from the live hotkey "
        "description; click-through/hide still hardcode Ctrl+Shift+B")

    setter("Ctrl+Alt+K")

    for btn in (bar._btn_ct, bar._btn_close):
        assert "Ctrl+Alt+K" in btn.toolTip()
        assert "Ctrl+Shift+B" not in btn.toolTip()
