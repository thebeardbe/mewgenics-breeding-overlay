"""ZoomController: zoom clamping, stepping, cycling, persistence, Ctrl+wheel.

``mewgenics_overlay.ui.zoom.ZoomController`` was extracted from
``PaletteWindow`` (god-file split, final step). It owns the zoom value in the
injected settings dict, the step/cycle rules, the app-font rescale and the
Ctrl+wheel detection; the window-specific re-render arrives as ``on_zoom``.

The fake ``QApplication`` below isolates the app-font rescale so these tests
never mutate the real, session-wide application font.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QFont, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui import theme as _theme  # noqa: E402
from mewgenics_overlay.ui import zoom as _zoom  # noqa: E402
from mewgenics_overlay.ui.zoom import (  # noqa: E402
    ZOOM_CYCLE,
    ZOOM_DEFAULT,
    ZOOM_MAX,
    ZOOM_MIN,
    ZOOM_STEP,
    ZoomController,
)


# ── fakes / fixtures ───────────────────────────────────────────────────────
class _FakeApp:
    """Stand-in for the QApplication used by ``render`` to rescale fonts."""

    def __init__(self, font):
        self._font = font
        self.all_widgets = []

    def font(self):
        return self._font

    def setFont(self, font):
        self._font = font

    def allWidgets(self):
        return list(self.all_widgets)


class _FakeWidget:
    def __init__(self):
        self.fonts = []

    def setFont(self, font):
        self.fonts.append(font)


class _WheelEvent:
    """Minimal event exposing only what ``handle_wheel`` touches."""

    def __init__(self, modifiers, delta_y):
        self._modifiers = modifiers
        self._delta_y = delta_y
        self.accepted = False

    def modifiers(self):
        return self._modifiers

    def angleDelta(self):
        return QPoint(0, self._delta_y)

    def accept(self):
        self.accepted = True


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_app(qapp, monkeypatch):
    """Swap the app font target and restore the shared theme zoom."""
    monkeypatch.setattr(_theme, "ZOOM", 1.0)
    base = QFont()
    base.setPixelSize(10)
    app = _FakeApp(base)
    monkeypatch.setattr(_zoom, "QApplication",
                        SimpleNamespace(instance=lambda: app))
    return app


@pytest.fixture
def make_ctl(isolated_app):
    def _make(zoom=1.0):
        settings = {} if zoom is None else {"zoom": zoom}
        saves, calls, labels = [], [], []
        ctl = ZoomController(
            settings,
            lambda: saves.append(dict(settings)),
            on_zoom=calls.append,
            on_label=labels.append,
        )
        return SimpleNamespace(ctl=ctl, settings=settings, saves=saves,
                               calls=calls, labels=labels, app=isolated_app)

    return _make


# ── 1. clamping ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    (0.0, ZOOM_MIN),
    (0.1, ZOOM_MIN),
    (0.75, 0.75),
    (1.0, 1.0),
    (1.234, 1.23),          # rounded to 2 decimals
    (2.0, 2.0),
    (3.999, ZOOM_MAX),      # 4.0
    (99.0, ZOOM_MAX),
    (-5.0, ZOOM_MIN),
])
def test_clamp_rounds_to_two_decimals_and_holds_the_range(raw, expected):
    assert ZoomController.clamp(raw) == expected


def test_zoom_bounds_are_sane():
    assert 0.0 < ZOOM_MIN < ZOOM_DEFAULT < ZOOM_MAX
    assert ZOOM_MIN <= min(ZOOM_CYCLE) and max(ZOOM_CYCLE) <= ZOOM_MAX
    assert ZOOM_STEP > 0


# ── 2. stored zoom ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("settings", [
    {},
    {"zoom": None},
    {"zoom": 0},
])
def test_missing_or_falsy_stored_zoom_reads_as_the_default(make_ctl,
                                                           settings):
    saves, calls, labels = [], [], []
    ctl = ZoomController(settings, lambda: saves.append(1),
                         on_zoom=calls.append, on_label=labels.append)

    assert ctl.zoom == ZOOM_DEFAULT


def test_stored_zoom_is_read_back_verbatim(make_ctl):
    ctl = make_ctl(2.0).ctl

    assert ctl.zoom == 2.0


# ── 3. step / in / out / reset ─────────────────────────────────────────────
def test_zoom_in_steps_up_and_persists(make_ctl):
    h = make_ctl(1.0)

    h.ctl.zoom_in()

    assert h.settings["zoom"] == pytest.approx(1.0 + ZOOM_STEP)
    assert h.ctl.zoom == pytest.approx(1.25)
    assert h.saves == [{"zoom": 1.25}]     # persisted exactly once
    assert h.calls == [1.25]               # host re-render
    assert h.labels == [125]               # host readout, percent


def test_zoom_out_steps_down_and_persists(make_ctl):
    h = make_ctl(1.5)

    h.ctl.zoom_out()

    assert h.ctl.zoom == pytest.approx(1.25)
    assert h.saves == [{"zoom": 1.25}]
    assert h.labels == [125]


def test_reset_returns_to_one_hundred_percent(make_ctl):
    h = make_ctl(3.0)

    h.ctl.reset()

    assert h.ctl.zoom == ZOOM_DEFAULT
    assert h.settings["zoom"] == ZOOM_DEFAULT
    assert h.labels == [100]
    assert h.calls == [ZOOM_DEFAULT]


def test_step_accepts_an_explicit_delta(make_ctl):
    h = make_ctl(1.0)

    h.ctl.step(-0.5)

    assert h.ctl.zoom == pytest.approx(0.75)


def test_step_and_in_out_clamp_at_the_bounds(make_ctl):
    high = make_ctl(ZOOM_MAX)
    low = make_ctl(ZOOM_MIN)

    high.ctl.zoom_in()
    low.ctl.zoom_out()

    assert high.ctl.zoom == ZOOM_MAX
    assert low.ctl.zoom == ZOOM_MIN
    assert high.calls == [ZOOM_MAX]        # host still told the clamped value
    assert low.calls == [ZOOM_MIN]


def test_repeated_zoom_out_stops_at_the_minimum(make_ctl):
    h = make_ctl(1.0)

    for _ in range(10):
        h.ctl.zoom_out()

    assert h.ctl.zoom == ZOOM_MIN
    assert h.settings["zoom"] == ZOOM_MIN


# ── 4. cycle order ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("start,expected", [
    (1.0, 1.5),
    (1.5, 2.0),
    (2.0, 3.0),
    (3.0, 1.0),          # wraps back to 100%
    (1.25, 1.5),
    (2.5, 3.0),
    (0.75, 1.0),         # below the first preset -> first preset
    (ZOOM_MAX, 1.0),     # at/above the top -> wrap
])
def test_cycle_advances_through_the_preset_order(make_ctl, start, expected):
    h = make_ctl(start)

    h.ctl.cycle()

    assert h.ctl.zoom == pytest.approx(expected)
    assert h.settings["zoom"] == pytest.approx(expected)
    assert h.saves == [{"zoom": expected}]


def test_cycle_from_the_top_visits_every_preset(make_ctl):
    h = make_ctl(1.0)
    seen = []
    for _ in range(len(ZOOM_CYCLE)):
        h.ctl.cycle()
        seen.append(h.ctl.zoom)

    assert seen == [1.5, 2.0, 3.0, 1.0]    # one full lap, back to the start


# ── 5. set_zoom / apply ────────────────────────────────────────────────────
def test_set_zoom_persists_and_fires_both_callbacks_with_the_clamped_value(
        make_ctl):
    h = make_ctl(1.0)

    h.ctl.set_zoom(99.0)

    assert h.settings["zoom"] == ZOOM_MAX
    assert len(h.saves) == 1
    assert h.calls == [ZOOM_MAX]
    assert h.labels == [400]


def test_set_zoom_clamps_a_too_small_value(make_ctl):
    h = make_ctl(1.0)

    h.ctl.set_zoom(0.01)

    assert h.settings["zoom"] == ZOOM_MIN
    assert h.calls == [ZOOM_MIN]
    assert h.labels == [75]


def test_set_zoom_rounds_to_two_decimals(make_ctl):
    h = make_ctl(1.0)

    h.ctl.set_zoom(1.239)

    assert h.settings["zoom"] == 1.24
    assert h.calls == [1.24]
    assert h.labels == [124]


def test_apply_renders_the_stored_zoom_without_writing_settings(make_ctl):
    h = make_ctl(2.0)

    h.ctl.apply()

    assert h.saves == []                   # startup must not dirty the config
    assert h.settings == {"zoom": 2.0}
    assert h.calls == [2.0]
    assert h.labels == []                  # the readout is not updated here


def test_zoom_and_label_callbacks_are_optional(make_ctl):
    settings = {"zoom": 1.0}
    saves = []
    ctl = ZoomController(settings, lambda: saves.append(1))

    ctl.zoom_in()                          # must not raise

    assert settings["zoom"] == pytest.approx(1.25)
    assert saves == [1]                    # the persist callback still ran


# ── 6. Ctrl+wheel ──────────────────────────────────────────────────────────
def test_handle_wheel_without_ctrl_is_declined(make_ctl):
    h = make_ctl(1.0)
    event = _WheelEvent(Qt.KeyboardModifier.NoModifier, 120)

    handled = h.ctl.handle_wheel(event)

    assert handled is False                # host must pass the event on
    assert event.accepted is False
    assert h.ctl.zoom == 1.0
    assert h.saves == []


def test_handle_wheel_with_ctrl_zooms_in_and_consumes(make_ctl):
    h = make_ctl(1.0)
    event = _WheelEvent(Qt.KeyboardModifier.ControlModifier, 120)

    handled = h.ctl.handle_wheel(event)

    assert handled is True
    assert event.accepted is True
    assert h.ctl.zoom == pytest.approx(1.25)
    assert h.saves == [{"zoom": 1.25}]


def test_handle_wheel_with_ctrl_and_negative_delta_zooms_out(make_ctl):
    h = make_ctl(1.5)
    event = _WheelEvent(Qt.KeyboardModifier.ControlModifier, -120)

    assert h.ctl.handle_wheel(event) is True

    assert event.accepted is True
    assert h.ctl.zoom == pytest.approx(1.25)


def test_handle_wheel_with_ctrl_and_zero_delta_consumes_without_zooming(
        make_ctl):
    h = make_ctl(1.0)
    event = _WheelEvent(Qt.KeyboardModifier.ControlModifier, 0)

    handled = h.ctl.handle_wheel(event)

    assert handled is True
    assert event.accepted is True
    assert h.ctl.zoom == 1.0               # no magnitude -> no change
    assert h.saves == []


@pytest.mark.parametrize("modifiers,expected", [
    (Qt.KeyboardModifier.AltModifier, False),
    (Qt.KeyboardModifier.ShiftModifier, False),
    (Qt.KeyboardModifier.ControlModifier, True),
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
     True),
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
     True),
])
def test_handle_wheel_reacts_exactly_to_the_ctrl_modifier(make_ctl,
                                                          modifiers,
                                                          expected):
    h = make_ctl(1.0)
    event = _WheelEvent(modifiers, 120)

    assert h.ctl.handle_wheel(event) is expected
    assert (h.ctl.zoom == 1.25) is expected


def test_handle_wheel_accepts_a_real_ctrl_wheel_event(make_ctl):
    h = make_ctl(1.0)
    event = QWheelEvent(
        QPointF(1, 1), QPointF(1, 1), QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase, False)

    assert h.ctl.handle_wheel(event) is True

    assert h.ctl.zoom == pytest.approx(1.25)


# ── 7. render / font rescale ───────────────────────────────────────────────
def test_render_updates_the_shared_theme_zoom(make_ctl):
    h = make_ctl(1.0)

    h.ctl.render(2.0)

    assert _theme.ZOOM == 2.0
    assert _theme.zoom_px(10) == 20


def test_render_scales_the_pixel_font_from_the_captured_baseline(make_ctl):
    h = make_ctl(1.0)                       # baseline pixel size 10

    h.ctl.render(2.0)

    assert h.app.font().pixelSize() == 20


def test_render_floors_a_scaled_pixel_font_at_six(make_ctl):
    h = make_ctl(1.0)
    h.ctl._base_font = QFont()
    h.ctl._base_font.setPixelSize(4)

    h.ctl.render(ZOOM_MIN)                  # round(4 * 0.75) = 3 -> floor 6

    assert h.app.font().pixelSize() == 6


def test_render_scales_a_point_font(make_ctl):
    h = make_ctl(1.0)
    h.ctl._base_font = QFont()
    h.ctl._base_font.setPointSizeF(12.0)

    h.ctl.render(2.0)

    assert h.app.font().pointSizeF() == pytest.approx(24.0)


def test_render_floors_a_scaled_point_font_at_four(make_ctl):
    h = make_ctl(1.0)
    h.ctl._base_font = QFont()
    h.ctl._base_font.setPointSizeF(1.0)

    h.ctl.render(ZOOM_MIN)

    assert h.app.font().pointSizeF() == pytest.approx(4.0)


def test_render_applies_the_scaled_font_to_every_widget(make_ctl):
    h = make_ctl(1.0)
    widgets = [_FakeWidget(), _FakeWidget()]
    h.app.all_widgets.extend(widgets)

    h.ctl.render(1.5)

    for widget in widgets:
        assert len(widget.fonts) == 1
        assert widget.fonts[0].pixelSize() == 15
