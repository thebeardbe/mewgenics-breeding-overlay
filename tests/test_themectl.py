"""ThemeController: active theme, toggle order, persistence, switch sequence.

``mewgenics_overlay.ui.themectl.ThemeController`` was extracted from
``PaletteWindow`` (god-file split). It owns the active theme key, the toggle
cycle, the settings key and the exact order of a switch: validate -> activate
-> persist -> notify (active/styles/refresh). The registry itself (keys, order,
titles, stylesheets) stays in ``ui/theme.py``.

These tests drive it with a *fresh* settings dict and recording callbacks, so
no real user config is ever read or written. Theme registry globals are
restored after every test.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui import theme as _theme  # noqa: E402
from mewgenics_overlay.ui.themectl import ThemeController  # noqa: E402


# ── fixtures / helpers ─────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_theme(monkeypatch):
    """Start each test on the module default and restore the globals after.

    ``apply_theme``/``toggle_theme`` mutate ``theme._ACTIVE``, the live colour
    globals and ``theme.STYLESHEET``; put them back so nothing leaks into
    another test file. ``theme.ZOOM`` is pinned to 1.0 so ``theme.stylesheet()``
    returns the raw registry css regardless of what another test file left
    behind (monkeypatch restores it).
    """
    original = _theme.active_theme()
    monkeypatch.setattr(_theme, "ZOOM", 1.0)
    _theme.set_theme(_theme.DEFAULT_THEME)
    yield
    _theme.set_theme(original)


@pytest.fixture
def make_ctl(qapp):
    """Build a controller over a fresh dict with recording host callbacks.

    ``settings`` is per-test (never the real config); ``save_settings`` only
    records, so ``core/config`` is never touched.
    """

    def _make(settings=None, with_active=True):
        settings = {} if settings is None else settings
        events: list = []
        controller = ThemeController(
            settings,
            lambda: events.append(("save", None)),
            lambda css: events.append(("styles", css)),
            lambda: events.append(("refresh", None)),
            on_active=(lambda key: events.append(("active", key)))
            if with_active else None,
        )
        return SimpleNamespace(
            ctl=controller, settings=settings, events=events)

    return _make


def kinds(events):
    return [kind for kind, _ in events]


def event_value(events, kind):
    return [value for k, value in events if k == kind]


def startup_theme(settings):
    """The documented host startup contract (PaletteWindow/app.py):

    read the stored key, fall back to the registry default, reject unknown
    keys, then apply. The controller stays free of config knowledge.
    """
    key = settings.get("theme", _theme.DEFAULT_THEME)
    if key not in _theme.THEMES:
        key = _theme.DEFAULT_THEME
    return key


# ── 1. initial state ───────────────────────────────────────────────────────
def test_starts_on_the_module_default(make_ctl):
    handle = make_ctl()

    assert _theme.DEFAULT_THEME == "noir"
    assert handle.ctl.active_theme() == _theme.DEFAULT_THEME


def test_construction_does_not_touch_the_settings_dict(make_ctl):
    handle = make_ctl()

    assert handle.settings == {}
    assert handle.events == []


def test_active_theme_is_a_static_view_of_theme_py(make_ctl):
    handle = make_ctl()

    assert ThemeController.active_theme() == _theme.active_theme()
    _theme.set_theme("film")
    assert handle.ctl.active_theme() == "film"
    assert ThemeController.active_theme() == "film"


# ── 2. startup from the injected settings value ────────────────────────────
def test_startup_applies_the_stored_settings_theme(make_ctl):
    handle = make_ctl({"theme": "film"})

    handle.ctl.apply_theme(startup_theme(handle.settings))

    assert handle.ctl.active_theme() == "film"
    assert handle.settings["theme"] == "film"          # re-persisted unchanged
    assert event_value(handle.events, "styles") == \
        [_theme.THEMES["film"]["css"]]


def test_startup_with_absent_theme_falls_back_to_default(make_ctl):
    handle = make_ctl({})

    assert startup_theme(handle.settings) == _theme.DEFAULT_THEME
    handle.ctl.apply_theme(startup_theme(handle.settings))

    assert handle.ctl.active_theme() == _theme.DEFAULT_THEME


def test_startup_with_unknown_theme_falls_back_to_default(make_ctl):
    handle = make_ctl({"theme": "neon"})             # stale/renamed key

    assert startup_theme(handle.settings) == _theme.DEFAULT_THEME
    handle.ctl.apply_theme(startup_theme(handle.settings))

    assert handle.ctl.active_theme() == _theme.DEFAULT_THEME
    assert handle.settings["theme"] == _theme.DEFAULT_THEME


# ── 3. apply: direct switch, validation, persistence ───────────────────────
def test_apply_switches_directly_to_the_requested_key(make_ctl):
    handle = make_ctl()

    handle.ctl.apply_theme("film")

    assert handle.ctl.active_theme() == "film"
    assert handle.settings["theme"] == "film"


def test_apply_does_not_route_through_toggle(make_ctl, monkeypatch):
    handle = make_ctl()

    def boom():
        raise AssertionError("apply_theme must not call toggle_theme")

    monkeypatch.setattr(handle.ctl, "toggle_theme", boom)

    handle.ctl.apply_theme("film")

    assert handle.ctl.active_theme() == "film"


def test_apply_the_current_key_keeps_it_and_still_notifies(make_ctl):
    handle = make_ctl()
    assert handle.ctl.active_theme() == _theme.DEFAULT_THEME

    handle.ctl.apply_theme(_theme.DEFAULT_THEME)     # no early return

    assert handle.ctl.active_theme() == _theme.DEFAULT_THEME
    assert kinds(handle.events) == ["save", "active", "styles", "refresh"]


@pytest.mark.parametrize("bad", ["neon", "", "FILM", None, 0])
def test_apply_rejects_any_key_not_in_the_registry(make_ctl, bad):
    handle = make_ctl()

    handle.ctl.apply_theme(bad)

    assert handle.ctl.active_theme() == _theme.DEFAULT_THEME
    assert handle.settings == {}                     # nothing persisted
    assert handle.events == []                       # no callback at all


def test_default_theme_is_a_valid_registry_key(make_ctl):
    handle = make_ctl()

    handle.ctl.apply_theme(_theme.DEFAULT_THEME)

    assert _theme.DEFAULT_THEME in _theme.THEMES
    assert handle.settings["theme"] == _theme.DEFAULT_THEME


# ── 4. host callback order on change, silence on rejection ─────────────────
def test_change_fires_active_styles_refresh_in_order(make_ctl):
    handle = make_ctl()

    handle.ctl.apply_theme("film")

    assert kinds(handle.events) == ["save", "active", "styles", "refresh"]
    assert event_value(handle.events, "active") == ["film"]


def test_styles_callback_receives_the_active_stylesheet_string(make_ctl):
    handle = make_ctl()

    handle.ctl.apply_theme("film")
    assert event_value(handle.events, "styles") == [_theme.stylesheet()]
    assert event_value(handle.events, "styles") == \
        [_theme.THEMES["film"]["css"]]

    handle.events.clear()
    handle.ctl.apply_theme("noir")
    assert event_value(handle.events, "styles") == [_theme.stylesheet()]
    assert event_value(handle.events, "styles") == \
        [_theme.THEMES["noir"]["css"]]


def test_rejected_key_fires_no_host_callbacks(make_ctl):
    handle = make_ctl()
    handle.ctl.apply_theme("film")
    handle.events.clear()

    handle.ctl.apply_theme("does-not-exist")

    assert handle.events == []
    assert handle.ctl.active_theme() == "film"       # still on the last good one


def test_on_active_is_optional(make_ctl):
    handle = make_ctl(with_active=False)

    handle.ctl.apply_theme("film")

    assert "active" not in kinds(handle.events)
    assert kinds(handle.events) == ["save", "styles", "refresh"]


# ── 5. toggle: order, wrapping, persistence ────────────────────────────────
@pytest.mark.parametrize("start,expected", [
    ("film", "noir"),
    ("noir", "film"),                                # wraps around
])
def test_toggle_advances_in_registry_order_and_wraps(make_ctl, start,
                                                     expected):
    _theme.set_theme(start)
    handle = make_ctl()

    handle.ctl.toggle_theme()

    assert handle.ctl.active_theme() == expected
    assert handle.settings["theme"] == expected


def test_toggle_uses_the_registry_order_not_a_hardcoded_one(make_ctl):
    keys = list(_theme.THEMES)
    assert len(keys) >= 2

    for i, start in enumerate(keys):
        _theme.set_theme(start)
        handle = make_ctl()
        handle.ctl.toggle_theme()
        assert handle.ctl.active_theme() == keys[(i + 1) % len(keys)]


def test_toggle_twice_returns_to_the_start(make_ctl):
    handle = make_ctl()
    start = handle.ctl.active_theme()

    handle.ctl.toggle_theme()
    handle.ctl.toggle_theme()

    assert handle.ctl.active_theme() == start


def test_toggle_persists_and_notifies_like_apply(make_ctl):
    _theme.set_theme("film")
    handle = make_ctl()

    handle.ctl.toggle_theme()

    assert handle.settings is handle.settings          # same injected dict
    assert handle.settings["theme"] == "noir"
    assert kinds(handle.events) == ["save", "active", "styles", "refresh"]
    assert event_value(handle.events, "active") == ["noir"]


def test_toggle_never_reads_or_writes_a_real_config(make_ctl):
    # The only persistence channel is the injected save_settings callback.
    handle = make_ctl({"theme": "film"})
    _theme.set_theme("film")

    handle.ctl.toggle_theme()

    assert kinds(handle.events).count("save") == 1
    assert handle.settings["theme"] == "noir"


def test_full_cycle_visits_every_theme_once(make_ctl):
    keys = list(_theme.THEMES)
    _theme.set_theme(keys[0])
    handle = make_ctl()
    seen = [_theme.active_theme()]

    for _ in range(len(keys)):
        handle.ctl.toggle_theme()
        seen.append(handle.ctl.active_theme())

    assert seen == keys + [keys[0]]
