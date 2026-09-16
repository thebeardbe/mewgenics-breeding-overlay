"""``app.main`` reports whether a tray icon exists to the palette.

The overlay's close path reads ``palette.tray_available`` to decide between
hiding (there is a way back) and quitting (there is not), so the value must be
``True`` exactly when a tray icon was actually built. ``--no-tray`` and a
tray-less desktop both leave it ``False``; a normal launch with a tray sets it
``True``.

``main`` builds a real ``QApplication`` and ``PaletteWindow``, so this module
stubs both at the app seam (plus the single-instance channel) and drives the
real control flow with fakes. Nothing here touches the user's config, and
``_build_tray`` is always stubbed, so no system tray is ever touched. Keeping
this in its own file also lets ``test_app_bootstrap.py`` stay under the
1000-line budget.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtCore import QObject, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui import app  # noqa: E402
from mewgenics_overlay.ui import theme as _theme  # noqa: E402


# ── fakes ──────────────────────────────────────────────────────────────────
class _FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in self.slots:
            slot(*args)


class _FakeApp:
    """Minimal QApplication stand-in: no event loop ever runs."""

    def __init__(self):
        self.aboutToQuit = _FakeSignal()
        self.exec_calls = 0

    def setApplicationName(self, name):
        pass

    def setStyleSheet(self, sheet):
        pass

    def exec(self):
        self.exec_calls += 1
        return 0


class _FakePalette(QObject):
    """The palette surface ``main`` touches on a no-bridge, no-follow run."""

    instances: list["_FakePalette"] = []

    def __init__(self):
        super().__init__()
        _FakePalette.instances.append(self)
        self.hotkey_active = False
        # Sentinel: ``main`` must set this from the tray it built; a missing
        # assignment would leave it None and fail every assertion below.
        self.tray_available = None
        self._current_save = None

    def toggle_activate(self):
        pass

    def install_hotkey(self, app_, on_change=None):
        pass

    def uninstall_hotkey(self):
        pass

    def _save_geometry(self):
        pass

    @property
    def current_save_path(self):
        return self._current_save

    def open_save(self, path):
        self._current_save = path

    def open_save_manual(self, path):
        self.open_save(path)

    def _set_status(self, text):
        pass

    def show(self):
        pass

    def shutdown(self):
        pass


class _FakeInstance:
    """First-launch single-instance channel: we always acquire it."""

    def __init__(self):
        self.listener = None

    def try_acquire(self):
        return True

    def send_toggle(self):
        return True

    def listen(self, callback):
        self.listener = callback

    def close(self):
        pass


class _FakeTray:
    """Records the tray lifecycle calls ``main`` makes."""

    def __init__(self):
        self.tooltips = []
        self.hidden = False

    def setToolTip(self, text):
        self.tooltips.append(text)

    def hide(self):
        self.hidden = True


# ── fixture ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app_ = QApplication.instance() or QApplication([])
    yield app_


@pytest.fixture
def run_main(monkeypatch, qapp):
    """Patch ``app``'s heavy seams; return a recorder around ``app.main``."""
    fake_app = _FakeApp()
    _FakePalette.instances = []
    original_theme = _theme.active_theme()

    monkeypatch.setattr(app, "QApplication", lambda *a, **k: fake_app)
    monkeypatch.setattr(app, "PaletteWindow", _FakePalette)
    monkeypatch.setattr(app.singleton, "SingleInstance", lambda: _FakeInstance())
    # No bridge, no save-follow: the tray state is the only thing under test.
    monkeypatch.setattr(app.ui_config, "load", lambda: {
        "bridge_enabled": False, "follow_game_save": False})
    monkeypatch.setattr(
        app.ui_config, "config_dir",
        lambda: (_ for _ in ()).throw(OSError("no config dir in tests")))

    yield SimpleNamespace(app=fake_app, palette=_FakePalette,
                          run=lambda argv: app.main(argv))

    # Palettes carry Qt timers; stop them before the next test's event loop.
    for palette in list(_FakePalette.instances):
        for timer in palette.findChildren(QTimer):
            timer.stop()
    _FakePalette.instances = []
    _theme.set_theme(original_theme)


# ── tray state reported to the palette ─────────────────────────────────────
def test_a_launch_with_the_tray_disabled_is_treated_as_no_tray(
        run_main, monkeypatch):
    built = []
    monkeypatch.setattr(app, "_build_tray",
                        lambda *a, **k: built.append(True))

    assert run_main.run(["--no-tray"]) == 0

    assert run_main.palette.instances[-1].tray_available is False
    assert built == []              # --no-tray never even attempts a tray


def test_a_launch_with_a_tray_reports_it_to_the_palette(run_main, monkeypatch):
    tray = _FakeTray()
    monkeypatch.setattr(app, "_build_tray", lambda *a, **k: tray)

    assert run_main.run([]) == 0

    assert run_main.palette.instances[-1].tray_available is True
    assert tray.hidden is True      # normal teardown still hides the icon


def test_a_desktop_without_a_tray_is_treated_as_no_tray(run_main, monkeypatch):
    # ``_build_tray`` returns None when ``isSystemTrayAvailable()`` is False.
    monkeypatch.setattr(app, "_build_tray", lambda *a, **k: None)

    assert run_main.run([]) == 0

    assert run_main.palette.instances[-1].tray_available is False
