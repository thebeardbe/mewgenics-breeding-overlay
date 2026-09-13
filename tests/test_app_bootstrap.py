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

import logging
import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtCore import QObject, QTimer  # noqa: E402
from PySide6.QtGui import QShortcut  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.core import bridge  # noqa: E402
from mewgenics_overlay.core import livesave  # noqa: E402
from mewgenics_overlay.ui import app  # noqa: E402
from mewgenics_overlay.ui import desktopshortcut  # noqa: E402
from mewgenics_overlay.ui import hotkeybinding  # noqa: E402
from mewgenics_overlay.ui import palette  # noqa: E402
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


class _FakePalette(QObject):
    """Records constructions; every method ``main`` calls is a no-op.

    A real QObject because ``main`` parents a QShortcut to it.
    """

    instances: list["_FakePalette"] = []

    def __init__(self):
        super().__init__()
        _FakePalette.instances.append(self)
        self.hotkey_active = False
        self.listener = None
        self.shown = False
        self.shutdown_called = False
        self.uninstalled = False
        # Bridge wiring reads the live session and focuses a cat key.
        self._session = SimpleNamespace(
            by_key={341: SimpleNamespace(db_key=341)}, cats=[])
        self.focused = []
        self.engaged = False
        self._focus = None
        # Save-follow wiring (app.main): the policy hook for a *user*-chosen
        # save, and the recorded load calls. ``current_save_path`` is what the
        # policy compares a detected save against.
        self.on_manual_open = None
        self.statuses = []
        self.opened = []                     # every load, automatic or manual
        self.manual_opens = []               # only user-chosen ones
        self._current_save = None
        # Outbound "show in game" path, as main() drives it: the shortcut calls
        # the shared focused-cat guard, which delegates to show_in_game. The
        # guard is bound from the real PaletteWindow so app.py's wiring is
        # exercised against the production entry point.
        self.bridges = []
        self.showed_in_game = []

    show_focused_in_game = palette.PaletteWindow.show_focused_in_game

    def attach_bridge(self, bridge_ctl):
        self.bridges.append(bridge_ctl)

    def show_in_game(self, db_key):
        self.showed_in_game.append(db_key)
        return True

    def set_focus_key(self, db_key):
        self.focused.append(db_key)

    def _engage(self):
        self.engaged = True

    def toggle_activate(self):
        pass

    def install_hotkey(self, app, on_change=None):
        self.hotkey_active = False

    def uninstall_hotkey(self):
        self.uninstalled = True

    def _save_geometry(self):
        pass

    @property
    def current_save_path(self):
        return self._current_save

    def open_save(self, path):
        self.opened.append(path)
        self._current_save = path

    def open_save_manual(self, path):
        # Mirrors PaletteWindow.open_save_manual: the user's choice goes
        # through the hook (pin) and is then loaded like any other save.
        self.manual_opens.append(path)
        if self.on_manual_open is not None:
            self.on_manual_open(path)
        self.open_save(path)

    def _set_status(self, text):
        self.statuses.append(text)

    def show(self):
        self.shown = True

    def shutdown(self):
        self.shutdown_called = True


class _FakeBridgeController:
    """Records construction and lifecycle; delivers requests on demand."""

    instances: list["_FakeBridgeController"] = []
    #: Scripted result of :meth:`start`; tests flip it to simulate a busy port.
    start_result = True

    def __init__(self, port=0, parent=None):
        self.port = port
        self.focus_requested = _FakeSignal()
        self.save_reported = _FakeSignal()
        self.game_online = _FakeSignal()
        self.started = False
        self.stopped = False
        self.sent = []
        _FakeBridgeController.instances.append(self)

    def start(self):
        self.started = True
        return self.start_result

    def stop(self):
        self.stopped = True

    def send_select(self, key):
        self.sent.append(key)
        return 1


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
    _FakeBridgeController.start_result = True
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
    monkeypatch.setattr(app.ui_config, "load",
                        lambda: {"bridge_enabled": False})
    monkeypatch.setattr(
        app.ui_config, "config_dir",
        lambda: (_ for _ in ()).throw(OSError("no config dir in tests")))

    yield SimpleNamespace(app=fake_app, palette=_FakePalette,
                          created=created, install_instance=install_instance)
    # A palette carries a live 5 s follow timer; if one outlives its test it
    # fires during another module's processEvents() and spins real /proc scans.
    # Stop every timer and drop the palettes before the next test begins.
    for palette in list(_FakePalette.instances):
        for timer in palette.findChildren(QTimer):
            timer.stop()
    _FakePalette.instances = []
    _theme.set_theme(original_theme)


@pytest.fixture(scope="session")
def qapp():
    """A real QApplication: ``main`` installs the Ctrl+G QShortcut only when
    one exists (``_QtGuiApplication.instance() is not None``)."""
    app_ = QApplication.instance() or QApplication([])
    yield app_


def _ctrl_g_shortcut(palette):
    """The Ctrl+G shortcut ``main`` parents to the palette, or None."""
    for shortcut in palette.findChildren(QShortcut):
        if shortcut.key().toString() == "Ctrl+G":
            return shortcut
    return None


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


# ── 5. in-game bridge wiring ──────────────────────────────────────────────
def test_bridge_wiring_focuses_the_requested_cat(bootstrap, monkeypatch):
    bootstrap.install_instance([True], toggle_ok=True)
    monkeypatch.setattr(app.ui_config, "load",
                        lambda: {"bridge_enabled": True, "bridge_port": 45699})
    _FakeBridgeController.instances = []
    monkeypatch.setattr(app.bridgectl, "BridgeController", _FakeBridgeController)

    assert app.main(["--no-tray"]) == 0

    ctl = _FakeBridgeController.instances[-1]
    assert ctl.started is True
    assert ctl.port == 45699
    assert ctl.stopped is True           # listener closed on quit

    palette = bootstrap.palette.instances[-1]
    # The palette gets the controller, so its outbound path can use it.
    assert palette.bridges == [ctl]
    ctl.focus_requested.emit(bridge.FocusRequest(key=341))
    assert palette.focused == [341]
    assert palette.engaged is True


def test_ctrl_g_shortcut_asks_the_palette_to_show_the_focused_cat(
        bootstrap, qapp, monkeypatch):
    bootstrap.install_instance([True], toggle_ok=True)
    monkeypatch.setattr(app.ui_config, "load",
                        lambda: {"bridge_enabled": True, "bridge_port": 45699})
    _FakeBridgeController.instances = []
    monkeypatch.setattr(app.bridgectl, "BridgeController",
                        _FakeBridgeController)

    assert app.main(["--no-tray"]) == 0

    palette = bootstrap.palette.instances[-1]
    palette._focus = SimpleNamespace(db_key=777)
    shortcut = _ctrl_g_shortcut(palette)
    assert shortcut is not None, "main() installed no Ctrl+G shortcut"

    shortcut.activated.emit()

    # The shortcut must route through the palette's single outbound path.
    assert palette.showed_in_game == [777]


def test_ctrl_g_shortcut_without_a_focused_cat_asks_for_nothing(
        bootstrap, qapp, monkeypatch):
    bootstrap.install_instance([True], toggle_ok=True)
    monkeypatch.setattr(app.ui_config, "load",
                        lambda: {"bridge_enabled": True, "bridge_port": 45699})
    _FakeBridgeController.instances = []
    monkeypatch.setattr(app.bridgectl, "BridgeController",
                        _FakeBridgeController)

    assert app.main(["--no-tray"]) == 0

    palette = bootstrap.palette.instances[-1]
    palette._focus = None
    shortcut = _ctrl_g_shortcut(palette)
    assert shortcut is not None

    shortcut.activated.emit()

    assert palette.showed_in_game == []


def test_bridge_does_not_start_when_disabled(bootstrap, monkeypatch):
    bootstrap.install_instance([True], toggle_ok=True)
    _FakeBridgeController.instances = []
    monkeypatch.setattr(app.bridgectl, "BridgeController", _FakeBridgeController)

    assert app.main(["--no-tray"]) == 0
    assert _FakeBridgeController.instances == []
    # No controller -> nothing is attached to the palette's outbound path.
    assert bootstrap.palette.instances[-1].bridges == []


def test_bridge_that_fails_to_start_is_not_attached(bootstrap, monkeypatch,
                                                    caplog):
    # A busy port must not leave the palette advertising "Show in game"
    # through a controller that is not listening.
    bootstrap.install_instance([True], toggle_ok=True)
    monkeypatch.setattr(app.ui_config, "load",
                        lambda: {"bridge_enabled": True, "bridge_port": 45699})
    _FakeBridgeController.instances = []
    monkeypatch.setattr(app.bridgectl, "BridgeController",
                        _FakeBridgeController)
    monkeypatch.setattr(_FakeBridgeController, "start_result", False)

    with caplog.at_level(logging.WARNING):
        assert app.main(["--no-tray"]) == 0

    ctl = _FakeBridgeController.instances[-1]
    assert ctl.started is True
    assert ctl.stopped is True                 # teardown still runs
    assert bootstrap.palette.instances[-1].bridges == []
    assert any("not listening" in r.getMessage() for r in caplog.records)


def test_no_ctrl_g_shortcut_when_the_bridge_is_disabled(bootstrap, qapp,
                                                       monkeypatch):
    bootstrap.install_instance([True], toggle_ok=True)

    assert app.main(["--no-tray"]) == 0

    assert _ctrl_g_shortcut(bootstrap.palette.instances[-1]) is None


# ── 6. following the save the game is actually playing ─────────────────────
class _Detection:
    """Records ``find_live_save`` calls and returns a scripted path."""

    def __init__(self, path=None):
        self.path = path
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.path


class _FakeScanner(QObject):
    """Synchronous stand-in for ``app.LiveSaveScanner``.

    The real scanner runs on a worker thread and delivers through a queued Qt
    signal, which no test can wait on without an event loop. This fake keeps
    ``main``'s wiring intact but runs the scan and the handler on the calling
    (UI) thread, so the policy and palette logic is deterministic. The real
    scanner's off-thread hop is covered by its own tests below.
    """

    instances: list["_FakeScanner"] = []
    #: ``() -> (live_save_or_None, game_present)``, set per test.
    scan = staticmethod(lambda: (None, False))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.handler = None
        self.requests = 0
        _FakeScanner.instances.append(self)

    def set_handler(self, handler):
        self.handler = handler

    def request(self):
        self.requests += 1
        if self.handler is not None:
            found, present = _FakeScanner.scan()
            self.handler(found, present)


def enable_follow(monkeypatch, bootstrap, *, detected=None, follow_enabled=True,
                  saves=None, present=None, detector=True):
    """Enable the bridge + follow feature with every seam scripted.

    Returns the detection recorder, the presence flag holder, and the real
    (but recorded) ``SaveFollowPolicy`` instances ``main`` builds, so tests can
    assert on the policy state, not only on the resulting loads. *present*
    overrides the reported process presence (which otherwise mirrors whether a
    save was detected); *detector* says whether the host exposes a process
    table.
    """
    bootstrap.install_instance([True], toggle_ok=True)
    monkeypatch.setattr(app.ui_config, "load", lambda: {
        "bridge_enabled": True,
        "bridge_port": 45699,
        "follow_game_save": follow_enabled,
    })
    _FakeBridgeController.instances = []
    monkeypatch.setattr(app.bridgectl, "BridgeController", _FakeBridgeController)

    detection = _Detection(detected)
    monkeypatch.setattr(app.livesave, "find_live_save", detection)
    presence = {"value": present if present is not None else bool(detected)}
    monkeypatch.setattr(app.livesave, "game_process_running",
                        lambda: presence["value"])
    monkeypatch.setattr(app.livesave, "detector_available", lambda: detector)

    def _scan():
        found = detection()
        return found, bool(found) or presence["value"]

    _FakeScanner.instances = []
    _FakeScanner.scan = staticmethod(_scan)
    monkeypatch.setattr(app, "LiveSaveScanner", _FakeScanner)

    policies = []
    real_policy = livesave.SaveFollowPolicy

    def recording_policy(enabled=True, **kwargs):
        policy = real_policy(enabled=enabled, **kwargs)
        policies.append(policy)
        return policy

    monkeypatch.setattr(app.livesave, "SaveFollowPolicy", recording_policy)

    if saves is not None:
        monkeypatch.setattr(app.livesave.discovery, "find_all_saves",
                            lambda: list(saves))
    return SimpleNamespace(detect=detection, policies=policies,
                           present=presence, scanner=_FakeScanner)


def _follow_timer(palette):
    """The one 5-second follow timer ``main`` parents to the palette."""
    timers = [t for t in palette.findChildren(QTimer)
              if t.interval() == app.FOLLOW_POLL_MS]
    assert len(timers) == 1, "main() did not create exactly one follow timer"
    return timers[0]


def test_the_game_connecting_marks_it_online_and_scans_once(bootstrap, qapp,
                                                            monkeypatch):
    helper = enable_follow(monkeypatch, bootstrap,
                           detected="/steam/root/game.sav")
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]

    ctl.game_online.emit(True)

    assert "game connected" in palette.statuses
    assert helper.detect.calls == 1
    assert palette.opened == ["/steam/root/game.sav"]
    assert helper.policies[-1].online is True


def test_connecting_without_a_detectable_save_marks_the_policy_online(
        bootstrap, qapp, monkeypatch):
    # `note_game_online()` runs before the scan, so a connect that finds no
    # save still leaves the policy online: a user open from now on pins.
    helper = enable_follow(monkeypatch, bootstrap, detected=None)
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]

    ctl.game_online.emit(True)

    assert helper.detect.calls == 1
    assert "game connected" in palette.statuses
    assert helper.policies[-1].online is True
    assert palette.opened == []


def test_a_manual_open_after_a_save_less_connect_pins_and_stays(bootstrap,
                                                                qapp, monkeypatch):
    # The full changed window end to end: connect with nothing detectable,
    # then a user-chosen save pins; a later detection must not yank it away.
    helper = enable_follow(monkeypatch, bootstrap, detected=None)
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]
    ctl.game_online.emit(True)
    assert helper.policies[-1].online is True

    palette.open_save_manual("/steam/root/user.sav")

    assert helper.policies[-1].pinned is True
    assert palette.opened == ["/steam/root/user.sav"]

    # The game's own save is now detectable; the pin still wins.
    helper.detect.path = "/steam/root/game.sav"
    _follow_timer(palette).timeout.emit()

    assert helper.policies[-1].pinned is True
    assert palette.opened == ["/steam/root/user.sav"]


def test_a_different_game_save_lifts_the_pin_set_after_a_save_less_connect(
        bootstrap, qapp, monkeypatch):
    # The window's pin survives the game's *first* detected save (there is no
    # earlier game save to differ from), but a later different save means the
    # game itself switched campaign, so the pin lifts and the overlay follows.
    helper = enable_follow(monkeypatch, bootstrap, detected=None)
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]
    ctl.game_online.emit(True)
    palette.open_save_manual("/steam/root/user.sav")

    helper.detect.path = "/steam/root/game.sav"
    _follow_timer(palette).timeout.emit()
    assert helper.policies[-1].pinned is True

    helper.detect.path = "/steam/root/other.sav"
    _follow_timer(palette).timeout.emit()

    assert helper.policies[-1].pinned is False
    assert palette.opened == ["/steam/root/user.sav", "/steam/root/other.sav"]


def test_a_disconnect_alone_keeps_the_game_online_with_a_detector(
        bootstrap, qapp, monkeypatch):
    # With a process table the scan is the authority: a mod disconnect is not
    # proof the game quit, so the online flag (and any pin) survives.
    helper = enable_follow(monkeypatch, bootstrap, detector=True)
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]
    ctl.game_online.emit(True)
    palette.open_save_manual("/steam/root/user.sav")
    assert helper.policies[-1].pinned is True

    ctl.game_online.emit(False)

    assert helper.policies[-1].online is True
    assert helper.policies[-1].pinned is True


def test_a_disconnect_clears_the_online_state_without_a_detector(
        bootstrap, qapp, monkeypatch):
    # No /proc detector: the bridge is the only signal, so a disconnect keeps
    # its old meaning and clears the game's online state.
    helper = enable_follow(monkeypatch, bootstrap, detector=False)
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]

    ctl.game_online.emit(True)
    ctl.game_online.emit(False)

    assert helper.policies[-1].online is False


def test_a_reported_save_is_resolved_and_loaded(bootstrap, qapp, monkeypatch):
    helper = enable_follow(
        monkeypatch, bootstrap,
        saves=[{"path": "/steam/root/steamcampaign02.sav", "root": "/steam/root",
                "mtime": 1.0}])
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]

    ctl.save_reported.emit(bridge.SaveRequest(file="steamcampaign02.sav"))

    assert palette.opened == ["/steam/root/steamcampaign02.sav"]
    # The mod's report alone puts the game online (no connect signal needed).
    assert helper.policies[-1].online is True


def test_a_reported_save_that_is_not_on_disk_is_ignored(bootstrap, qapp,
                                                        monkeypatch):
    helper = enable_follow(monkeypatch, bootstrap, saves=[])
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]

    ctl.save_reported.emit(bridge.SaveRequest(file="steamcampaign02.sav"))

    assert palette.opened == []
    assert helper.policies[-1].online is False


def test_the_periodic_scan_is_wired_to_the_detector(bootstrap, qapp,
                                                    monkeypatch):
    helper = enable_follow(monkeypatch, bootstrap,
                           detected="/steam/root/steamcampaign03.sav")
    assert app.main(["--no-tray"]) == 0
    palette = bootstrap.palette.instances[-1]
    timer = _follow_timer(palette)
    assert timer.isActive() is True

    timer.timeout.emit()

    assert helper.detect.calls == 1
    assert palette.opened == ["/steam/root/steamcampaign03.sav"]


def test_a_periodic_scan_that_finds_nothing_changes_nothing(bootstrap, qapp,
                                                            monkeypatch):
    helper = enable_follow(monkeypatch, bootstrap, detected=None)
    assert app.main(["--no-tray"]) == 0
    palette = bootstrap.palette.instances[-1]

    _follow_timer(palette).timeout.emit()

    assert helper.detect.calls == 1
    assert palette.opened == []


def test_an_auto_followed_save_does_not_pin(bootstrap, qapp, monkeypatch):
    helper = enable_follow(monkeypatch, bootstrap,
                           detected="/steam/root/steamcampaign03.sav")
    assert app.main(["--no-tray"]) == 0
    palette = bootstrap.palette.instances[-1]

    _follow_timer(palette).timeout.emit()

    assert palette.opened == ["/steam/root/steamcampaign03.sav"]
    assert palette.manual_opens == []
    assert helper.policies[-1].pinned is False


def test_a_user_chosen_save_pins_while_the_game_is_online(bootstrap, qapp,
                                                          monkeypatch):
    helper = enable_follow(monkeypatch, bootstrap,
                           detected="/steam/root/game.sav")
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]
    ctl.game_online.emit(True)

    palette.open_save_manual("/steam/root/user.sav")

    assert helper.policies[-1].pinned is True
    # The connect scan auto-loaded the game's save first, then the user picked.
    assert palette.opened == ["/steam/root/game.sav", "/steam/root/user.sav"]

    # An automatic follow must not yank the view away from the user's choice.
    _follow_timer(palette).timeout.emit()

    assert palette.opened == ["/steam/root/game.sav", "/steam/root/user.sav"]


def test_a_user_chosen_save_while_offline_does_not_pin(bootstrap, qapp,
                                                       monkeypatch):
    # The game goes offline through the grace period, not through a disconnect
    # (which a present detector ignores). Only then does a manual open stop
    # pinning.
    helper = enable_follow(monkeypatch, bootstrap, detector=True,
                           detected=None, present=False)
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]
    ctl.game_online.emit(True)
    ctl.game_online.emit(False)
    assert helper.policies[-1].online is True

    for _ in range(helper.policies[-1].offline_grace_scans):
        _follow_timer(palette).timeout.emit()
    assert helper.policies[-1].online is False

    palette.open_save_manual("/steam/root/user.sav")

    assert helper.policies[-1].pinned is False
    assert palette.opened == ["/steam/root/user.sav"]


def test_the_game_switching_campaign_lifts_the_user_pin(bootstrap, qapp,
                                                        monkeypatch):
    helper = enable_follow(
        monkeypatch, bootstrap, detected="/steam/root/old.sav",
        saves=[{"path": "/steam/root/new.sav", "root": "/steam/root",
                "mtime": 2.0}])
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]
    ctl.game_online.emit(True)
    _follow_timer(palette).timeout.emit()               # game on old.sav
    palette.open_save_manual("/steam/root/user.sav")     # user pins
    assert helper.policies[-1].pinned is True

    ctl.save_reported.emit(bridge.SaveRequest(file="new.sav"))

    assert palette.opened == ["/steam/root/old.sav", "/steam/root/user.sav",
                              "/steam/root/new.sav"]
    assert helper.policies[-1].pinned is False


def test_follow_disabled_means_no_scan_and_no_switch(bootstrap, qapp,
                                                     monkeypatch):
    helper = enable_follow(monkeypatch, bootstrap,
                           detected="/steam/root/game.sav",
                           follow_enabled=False)
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]

    assert helper.policies[-1].enabled is False
    assert _follow_timer(palette).isActive() is False

    _follow_timer(palette).timeout.emit()
    ctl.game_online.emit(True)

    # The disabled policy short-circuits the scan (and never loads).
    assert helper.detect.calls == 0
    assert palette.opened == []


def test_the_periodic_scan_offlines_the_game_after_the_grace_period(
        bootstrap, qapp, monkeypatch):
    # The grace period is driven by the periodic process scan: a lone bridge
    # disconnect does not end the game, but enough absent scans do.
    helper = enable_follow(monkeypatch, bootstrap, detector=True,
                           detected=None, present=False)
    assert app.main(["--no-tray"]) == 0
    ctl = _FakeBridgeController.instances[-1]
    palette = bootstrap.palette.instances[-1]
    ctl.game_online.emit(True)              # connect scans once
    ctl.game_online.emit(False)             # ...then disconnects
    assert helper.policies[-1].online is True

    grace = helper.policies[-1].offline_grace_scans
    timer = _follow_timer(palette)
    for _ in range(grace - 1):
        timer.timeout.emit()
        assert helper.policies[-1].online is True

    timer.timeout.emit()                    # the grace-th absent scan
    assert helper.policies[-1].online is False
    # One scan on connect, then one per timer tick.
    assert helper.scanner.instances[-1].requests == grace + 1


def test_a_present_process_without_a_save_keeps_the_game_online(
        bootstrap, qapp, monkeypatch):
    # A running game with no save open (menu, between campaigns) is present,
    # not gone, and must not be reported as offline.
    helper = enable_follow(monkeypatch, bootstrap, detector=True,
                           detected=None, present=True)
    assert app.main(["--no-tray"]) == 0
    palette = bootstrap.palette.instances[-1]

    timer = _follow_timer(palette)
    for _ in range(helper.policies[-1].offline_grace_scans + 1):
        timer.timeout.emit()

    assert helper.policies[-1].online is True
    assert palette.opened == []


def test_a_present_process_resets_the_apps_grace_period(bootstrap, qapp,
                                                       monkeypatch):
    helper = enable_follow(monkeypatch, bootstrap, detector=True,
                           detected=None, present=False)
    assert app.main(["--no-tray"]) == 0
    palette = bootstrap.palette.instances[-1]
    timer = _follow_timer(palette)

    # One detected save puts the game online...
    helper.detect.path = "/steam/root/game.sav"
    timer.timeout.emit()
    assert helper.policies[-1].online is True

    # ...then the save disappears and two absent scans accumulate.
    helper.detect.path = None
    timer.timeout.emit()
    timer.timeout.emit()
    assert helper.policies[-1].online is True

    # A present process resets that count, so the grace starts over.
    helper.present["value"] = True
    timer.timeout.emit()
    helper.present["value"] = False
    timer.timeout.emit()
    timer.timeout.emit()
    assert helper.policies[-1].online is True

    timer.timeout.emit()
    assert helper.policies[-1].online is False


# ── 7. startup --save and the game already running ─────────────────────────
def test_startup_save_pins_when_the_game_is_already_running(bootstrap, qapp,
                                                            monkeypatch):
    # The explicit startup choice must be applied *after* the game is known to
    # be online, so it pins instead of being overridden by the live save.
    helper = enable_follow(monkeypatch, bootstrap, detector=True,
                           detected=None, present=True)

    assert app.main(["--save", "/steam/root/chosen.sav", "--no-tray"]) == 0

    palette = bootstrap.palette.instances[-1]
    policy = helper.policies[-1]
    assert policy.online is True
    assert policy.pinned is True
    assert policy.pinned_path == "/steam/root/chosen.sav"
    assert palette.manual_opens == ["/steam/root/chosen.sav"]
    assert palette.opened == ["/steam/root/chosen.sav"]


def test_startup_save_does_not_pin_when_the_game_is_not_running(
        bootstrap, qapp, monkeypatch):
    helper = enable_follow(monkeypatch, bootstrap, detector=True,
                           detected=None, present=False)

    assert app.main(["--save", "/steam/root/chosen.sav", "--no-tray"]) == 0

    palette = bootstrap.palette.instances[-1]
    policy = helper.policies[-1]
    assert policy.online is False
    assert policy.pinned is False
    assert palette.opened == ["/steam/root/chosen.sav"]


def test_startup_save_does_not_pin_without_a_detector(bootstrap, qapp,
                                                      monkeypatch):
    # No process table: the overlay cannot confirm the game is online at
    # startup, so the choice is loaded but not pinned.
    helper = enable_follow(monkeypatch, bootstrap, detector=False,
                           detected=None, present=True)

    assert app.main(["--save", "/steam/root/chosen.sav", "--no-tray"]) == 0

    palette = bootstrap.palette.instances[-1]
    assert helper.policies[-1].pinned is False
    assert palette.opened == ["/steam/root/chosen.sav"]


