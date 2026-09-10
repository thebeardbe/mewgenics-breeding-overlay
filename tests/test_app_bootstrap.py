"""``ui/app.py`` single-instance startup: forward a toggle, or start normally.

The overlay's startup path owns the single-instance hand-off: a second launch
(or the desktop shortcut's ``--toggle``) asks the running instance to toggle
and exits, but when the forward fails it must fall through to a normal start
instead of exiting silently.

``main`` builds a real ``QApplication`` and ``PaletteWindow`` (watcher,
threads, timers), so these tests stub both at the module seam and drive the
real control flow with fakes. Nothing touches the user's config: the config
loader and directory are stubbed and no tray is built (``--no-tray``).
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from mewgenics_overlay.ui import app  # noqa: E402
from mewgenics_overlay.ui import desktopshortcut  # noqa: E402
from mewgenics_overlay.ui import hotkeybinding  # noqa: E402
from mewgenics_overlay.ui import theme as _theme  # noqa: E402


# ── fakes ──────────────────────────────────────────────────────────────────
class _FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _FakeApp:
    """Minimal QApplication stand-in: no event loop ever runs."""

    def __init__(self):
        self.aboutToQuit = _FakeSignal()
        self.names = []
        self.stylesheets = []
        self.exec_calls = 0

    def setApplicationName(self, name):
        self.names.append(name)

    def setStyleSheet(self, sheet):
        self.stylesheets.append(sheet)

    def exec(self):
        self.exec_calls += 1
        return 0


class _FakePalette:
    """Records constructions; every method ``main`` calls is a no-op."""

    instances: list["_FakePalette"] = []

    def __init__(self):
        _FakePalette.instances.append(self)
        self.hotkey_active = False
        self.listener = None
        self.shown = False
        self.shutdown_called = False
        self.uninstalled = False

    def toggle_activate(self):
        pass

    def install_hotkey(self, app, on_change=None):
        self.hotkey_active = False

    def uninstall_hotkey(self):
        self.uninstalled = True

    def _save_geometry(self):
        pass

    def open_save(self, path):
        pass

    def show(self):
        self.shown = True

    def shutdown(self):
        self.shutdown_called = True


class _FakeInstance:
    """Scripted single-instance stand-in: acquisition results and toggle."""

    def __init__(self, acquire_results, toggle_ok):
        self._acquire_results = list(acquire_results)
        self._toggle_ok = toggle_ok
        self.acquire_calls = 0
        self.toggle_calls = 0
        self.listener = None
        self.closed = False

    def try_acquire(self):
        self.acquire_calls += 1
        if self._acquire_results:
            return self._acquire_results.pop(0)
        return True

    def send_toggle(self):
        self.toggle_calls += 1
        return self._toggle_ok

    def listen(self, callback):
        self.listener = callback

    def close(self):
        self.closed = True


# ── fixture ────────────────────────────────────────────────────────────────
@pytest.fixture
def bootstrap(monkeypatch, tmp_path):
    """Patch ``app``'s heavy seams; returns the recorded state and a factory."""
    fake_app = _FakeApp()
    _FakePalette.instances = []
    created: dict = {}
    original_theme = _theme.active_theme()

    def install_instance(acquire_results, toggle_ok):
        def factory(*args, **kwargs):
            instance = _FakeInstance(acquire_results, toggle_ok)
            created["instance"] = instance
            return instance

        monkeypatch.setattr(app.singleton, "SingleInstance", factory)
        return factory

    monkeypatch.setattr(app, "QApplication", lambda *a, **k: fake_app)
    monkeypatch.setattr(
        app, "QGuiApplication",
        SimpleNamespace(setHighDpiScaleFactorRoundingPolicy=staticmethod(
            lambda policy: None)))
    monkeypatch.setattr(app, "PaletteWindow", _FakePalette)
    monkeypatch.setattr(app.ui_config, "load", lambda: {})
    monkeypatch.setattr(
        app.ui_config, "config_dir",
        lambda: (_ for _ in ()).throw(OSError("no config dir in tests")))

    yield SimpleNamespace(app=fake_app, palette=_FakePalette,
                          created=created, install_instance=install_instance)
    _theme.set_theme(original_theme)


# ── 1. hand-off lands: exit without a window ──────────────────────────────
def test_successful_toggle_forward_exits_without_starting(bootstrap):
    bootstrap.install_instance([False], toggle_ok=True)

    code = app.main(["--toggle", "--no-tray"])

    assert code == 0
    assert bootstrap.palette.instances == []
    assert bootstrap.app.exec_calls == 0
    instance = bootstrap.created["instance"]
    assert instance.toggle_calls == 1
    assert instance.acquire_calls == 1
    assert instance.closed is False        # early return, no teardown needed


# ── 2. hand-off fails: start normally instead of exiting silently ─────────
def test_failed_toggle_forward_falls_through_to_a_start(bootstrap):
    bootstrap.install_instance([False, True], toggle_ok=False)

    code = app.main(["--toggle", "--no-tray"])

    assert code == 0
    instance = bootstrap.created["instance"]
    assert instance.toggle_calls == 1
    # The failed forward is followed by one retry that wins the channel.
    assert instance.acquire_calls == 2
    assert len(bootstrap.palette.instances) == 1
    assert bootstrap.app.exec_calls == 1
    assert instance.closed is True


def test_a_started_instance_listens_for_toggles(bootstrap):
    bootstrap.install_instance([False, True], toggle_ok=False)

    app.main(["--toggle", "--no-tray"])

    instance = bootstrap.created["instance"]
    palette = bootstrap.palette.instances[0]
    assert instance.listener == palette.toggle_activate


# ── 3. first launch: no peer, no forward ──────────────────────────────────
def test_first_instance_starts_without_forwarding(bootstrap):
    bootstrap.install_instance([True], toggle_ok=True)

    code = app.main(["--no-tray"])

    assert code == 0
    instance = bootstrap.created["instance"]
    assert instance.acquire_calls == 1
    assert instance.toggle_calls == 0
    assert len(bootstrap.palette.instances) == 1
    assert bootstrap.app.exec_calls == 1
    assert instance.closed is True
    assert bootstrap.palette.instances[0].shutdown_called is True
    assert bootstrap.palette.instances[0].uninstalled is True


# ── 4. headless shortcut CLI ──────────────────────────────────────────────
def test_shortcut_cli_install_prints_the_message_and_exits_zero(
        monkeypatch, capsys):
    seen = {}

    def fake_install(binding):
        seen["binding"] = binding
        return True, "GNOME shortcut installed"

    monkeypatch.setattr(desktopshortcut, "install", fake_install)

    assert app._shortcut_cli(install=True) == 0
    assert "GNOME shortcut installed" in capsys.readouterr().out
    # No usable config hotkey -> the shipped default binding is installed.
    assert seen["binding"].format() == hotkeybinding.DEFAULT_TEXT


def test_shortcut_cli_remove_failure_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(desktopshortcut, "remove",
                        lambda: (False, "could not remove"))

    assert app._shortcut_cli(install=False) == 1
    assert "could not remove" in capsys.readouterr().out
