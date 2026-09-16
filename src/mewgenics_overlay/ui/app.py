"""Bootstrap the overlay application.

Run with:

    PYTHONPATH=src python -m mewgenics_overlay            (GUI)
    PYTHONPATH=src python -m mewgenics_overlay.cli …      (headless)

Behaviour:
  * Creates a palette window, a system-tray toggle (when a tray exists) and,
    on Windows, the user-configured global hotkey (default Ctrl+Shift+B).
  * Watches the live save and re-parses on change (background thread).
  * `--save <path>` opens a specific save; otherwise the most recent one is
    used; the first-run flow lets you browse if none is found.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading

from PySide6.QtCore import Qt, QPoint, QObject, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtGui import QGuiApplication as _QtGuiApplication  # real class, not test-stubbed
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from mewgenics_overlay import __version__
from mewgenics_overlay.core import bridge
from mewgenics_overlay.core import livesave
from mewgenics_overlay.ui import bridgectl
from mewgenics_overlay.ui import config as ui_config
from mewgenics_overlay.ui import singleton
from mewgenics_overlay.ui import theme as _theme
from mewgenics_overlay.ui.palette import PaletteWindow

log = logging.getLogger("mewgenics_overlay.app")

#: How often an overlay with no mod connected scans for the game's open save.
FOLLOW_POLL_MS = 5000


class LiveSaveScanner(QObject):
    """Run one process scan off the UI thread and hand the result back.

    A full :func:`~mewgenics_overlay.core.livesave.find_live_save` pass walks
    every process's ``/proc`` entry and measured about 8 ms with a few hundred
    processes, enough to stall the UI thread on the 5 s follow timer. The scan
    runs on a short-lived daemon thread and its result is emitted from there;
    Qt queues that emission to this object's thread (the UI thread), so the
    policy and the palette are only ever touched on the UI thread. A request
    while a scan is in flight is dropped, so a slow scan cannot pile up.
    """

    #: Emitted per finished scan with ``(live_save_or_None, game_present)``.
    scanned = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._busy = False
        self._handler = None
        # A method of this QObject is the receiver, so Qt sees the worker
        # thread's emission as cross-thread and queues it to this object's
        # thread (the UI thread). A plain Python callable would not be safe.
        self.scanned.connect(self._deliver)

    def set_handler(self, handler) -> None:
        """Set the callable that receives ``(save, present)`` on the UI thread."""
        self._handler = handler

    def request(self) -> None:
        """Start one scan unless a previous one is still running."""
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._run, name="livesave-scan",
                         daemon=True).start()

    def _deliver(self, result) -> None:
        """Mark the scan done and hand the result on (UI thread)."""
        self._busy = False
        if self._handler is not None:
            self._handler(*result)

    def _run(self) -> None:
        found, present = None, False
        try:
            try:
                found = livesave.find_live_save()
                present = bool(found) or livesave.game_process_running()
            except Exception:
                # find_live_save never raises by design, but a worker thread
                # that died silently would stop the feature; log and carry on.
                log.exception("follow: live-save scan failed")
            # The emission shares this guarded region: if this object's C++
            # side was deleted while the scan was in flight, emit raises
            # RuntimeError, which is logged and swallowed like any other
            # worker failure instead of killing the thread.
            self.scanned.emit((found, present))
        except Exception:
            log.exception("follow: live-save result emission failed")
            # _deliver normally clears the flag; a failed emission means it
            # never ran, so clear it here or every later scan is skipped.
            self._busy = False


def _make_tray_icon() -> QIcon:
    """Draw a small cat-ear tile so the tray icon is never blank."""
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(_theme.C_TRAY_BASE))
    p.setPen(Qt.PenStyle.NoPen)
    # rounded tile
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    # cat ears
    ear = QColor(_theme.C_TRAY_EAR)
    p.setBrush(ear)
    p.drawPolygon([QPoint(18, 24), QPoint(22, 4), QPoint(36, 18)])
    p.drawPolygon([QPoint(46, 24), QPoint(42, 4), QPoint(28, 18)])
    p.end()
    return QIcon(pm)


def _tray_tooltip(description: str, active: bool) -> str:
    """Tray tooltip naming the live hotkey, or the fallback wording."""
    if active:
        return f"Mewgenics Breeding Overlay - global hotkey {description}"
    return ("Mewgenics Breeding Overlay - use the tray icon to toggle "
            "(hotkey works while the overlay is focused)")


def _build_tray(app: QApplication, palette: PaletteWindow):
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None

    tray = QSystemTrayIcon(_make_tray_icon(), app)
    tray.setToolTip("Mewgenics Breeding Overlay")
    menu = QMenu()
    act_show = QAction("Show overlay", None)
    act_show.triggered.connect(palette._engage)
    act_ct = QAction("Toggle click-through", None)
    act_ct.triggered.connect(
        lambda: palette.set_click_through(not palette._click_through))
    act_save = QAction("Choose save…", None)
    act_save.triggered.connect(palette._pick_save)
    act_quit = QAction("Quit", None)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_show)
    menu.addAction(act_ct)
    menu.addAction(act_save)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: palette.toggle_activate()
        if reason == QSystemTrayIcon.ActivationReason.Trigger else None
    )
    tray.show()
    return tray


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mewgenics breeding overlay")
    parser.add_argument("--save", help="path to a .sav file")
    parser.add_argument("--no-tray", action="store_true", help="disable the tray icon")
    parser.add_argument("--hidden", action="store_true",
                        help="start hidden (summon with tray/hotkey)")
    parser.add_argument("--toggle", action="store_true",
                        help="toggle a running overlay and exit; starts the "
                             "overlay when none is running (used by a desktop "
                             "shortcut)")
    parser.add_argument("--setup-shortcut", action="store_true",
                        help="install a desktop shortcut for the configured "
                             "hotkey, then exit")
    parser.add_argument("--remove-shortcut", action="store_true",
                        help="remove the desktop shortcut this app installed, "
                             "then exit")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    # Headless/systemd Linux runs (no desktop env) may lack the CA bundle the
    # update check needs. Point OpenSSL at the first cert file that exists.
    for _env in ("NIX_SSL_CERT_FILE", "SSL_CERT_FILE"):
        if os.environ.get(_env):
            break
    else:
        for _cand in ("/etc/ssl/certs/ca-certificates.crt",
                      "/etc/pki/tls/certs/ca-bundle.crt"):
            if os.path.exists(_cand):
                os.environ.setdefault("SSL_CERT_FILE", _cand)
                break


    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The vendored parser logs per-cat defect detection at INFO - too chatty
    # for an overlay. Keep our own loggers at INFO, vendors at WARNING.
    for noisy in ("mewgenics.parser", "mewgenics.breeding"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # The frozen Windows build has no console: also log to a file so a crash
    # or uncaught exception is never invisible.
    try:
        from mewgenics_overlay.ui import config as ui_cfg
        _log_dir = ui_cfg.config_dir()
        _fh = logging.FileHandler(str(_log_dir / "overlay.log"),
                                  encoding="utf-8")
        _fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(_fh)

        def _excepthook(exc_type, exc, tb):
            logging.getLogger("mewgenics_overlay.crash").critical(
                "Unhandled exception", exc_info=(exc_type, exc, tb))
        sys.excepthook = _excepthook
    except Exception:
        pass

    # Headless desktop-shortcut commands (used by the Settings buttons and
    # by a copy-pasteable script): print the manager's message and exit.
    if args.setup_shortcut or args.remove_shortcut:
        return _shortcut_cli(install=bool(args.setup_shortcut))

    # On Hyprland run under XWayland: the always-on-top flag is honoured
    # reliably there, and Hyprland window rules (pin/float) can keep the
    # palette above a fullscreen game. Wayland-native can't guarantee that.
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and \
            "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    # QT_QPA_PLATFORMTHEME=gtk3 (common on NixOS/Hyprland) makes Qt's file
    # dialog initialise GTK/GIO, which aborts with 'No GSettings schemas are
    # installed on the system' when the schemas aren't in XDG_DATA_DIRS - a
    # hard crash the moment the 📁 picker opens. Fall back to the generic
    # theme for this app so dialogs stay pure Qt.
    if sys.platform.startswith("linux") and os.environ.get(
            "QT_QPA_PLATFORMTHEME", "").lower() == "gtk3":
        os.environ["QT_QPA_PLATFORMTHEME"] = "generic"
        logging.info("QT_QPA_PLATFORMTHEME=gtk3 would crash file dialogs "
                     "without GSettings schemas - using generic")

    # Respect fractional monitor scales (125/150%): Qt6 rounds up to 1
    # unless told to pass through, which makes the overlay tiny on HiDPI.
    from PySide6.QtCore import Qt
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("mewgenics-overlay")

    # Single instance: a second launch, or the desktop shortcut's --toggle,
    # forwards a toggle to the running overlay and exits without a window.
    # Nothing is printed; the hand-off is logged at INFO.
    instance = singleton.SingleInstance()
    if not instance.try_acquire():
        # Either a live overlay owns the channel (forward the toggle) or the
        # channel was unavailable. Exit only when the hand-off actually
        # landed; otherwise start normally so a peer that died between the
        # probe and the write never leaves a launch doing nothing.
        if instance.send_toggle():
            logging.info("another overlay is running; forwarded the toggle")
            return 0
        logging.warning("no running overlay answered the toggle; "
                        "starting a new instance")
        instance.try_acquire()
    # start with the saved theme (Bleached Film / Noir Ink)
    settings = ui_config.load()
    _theme_key = settings.get("theme", _theme.DEFAULT_THEME)
    if _theme_key not in _theme.THEMES:
        _theme_key = _theme.DEFAULT_THEME
    _theme.set_theme(_theme_key)
    app.setStyleSheet(_theme.stylesheet())

    palette = PaletteWindow()
    instance.listen(palette.toggle_activate)
    tray = None
    if not args.no_tray:
        tray = _build_tray(app, palette)

    # The palette owns the hotkey (config -> registration + focused-window
    # shortcut); the tray tooltip follows every accepted change.
    def _on_hotkey_changed(description: str) -> None:
        if tray is not None:
            tray.setToolTip(_tray_tooltip(description,
                                         palette.hotkey_active))

    palette.install_hotkey(app, on_change=_on_hotkey_changed)
    if not palette.hotkey_active:
        logging.info("global hotkey unavailable; use the tray icon to toggle")
    app.aboutToQuit.connect(palette._save_geometry)

    # In-game bridge: the companion mod sends focus requests over loopback TCP.
    # The controller re-emits them on the UI thread; the palette resolves the
    # reported key against its own live save and selects the cat silently, so
    # an in-game click does not yank focus out of the game.
    #
    # Before the bridge, set up following the save the game is actually
    # playing (core/livesave.py). The policy only decides; loading stays with
    # palette.open_save. Detection runs from a timer so an overlay-only install
    # with no mod (nothing to report) still follows.
    # The grace is counted in scans (``livesave.DEFAULT_OFFLINE_GRACE_SCANS``);
    # at FOLLOW_POLL_MS it is about 15 s before the game is judged gone.
    follow = livesave.SaveFollowPolicy(
        enabled=bool(settings.get("follow_game_save", True)))
    palette.on_manual_open = follow.note_manual_open

    # The detector can only vouch for the game on hosts with a process table
    # (/proc). Elsewhere the bridge is the only signal, and a mod disconnect
    # keeps its old meaning: the game went away. Computed once - the root does
    # not move at runtime.
    detector_available = livesave.detector_available()
    mod_connected = False

    def _feed_game_save(path: str) -> None:
        """Record a detected/reported save and switch if the policy says so.

        The current save is resolved before the comparison because one file
        can be spelled through a symlinked library path; without this a scan
        and a user choice of the same save would read as a campaign switch.
        """
        current = livesave.canonical_path(palette.current_save_path)
        target = follow.note_game_save(path, current)
        if target:
            logging.info("follow: game is on %s; loading it", target)
            palette.open_save(target)

    scanner = LiveSaveScanner(palette)

    def _on_scan_result(found, present: bool) -> None:
        """Adopt one live-save scan, back on the UI thread."""
        if not follow.enabled:
            return
        if found:
            _feed_game_save(found)
            return
        if present or mod_connected:
            # The process is there (menus, between campaigns) or the mod is
            # still connected: the game is online, so the pin stays.
            follow.note_game_process(True)
            return
        if follow.note_game_process(False):
            logging.info("follow: game process absent for %d scans; offline",
                         follow.offline_grace_scans)

    scanner.set_handler(_on_scan_result)

    def _detect_live_save() -> None:
        """Ask the scanner for one scan; the scan runs off the UI thread.

        A full pass measured about 8 ms with a few hundred processes, so it is
        not run inline on the UI thread. :class:`LiveSaveScanner` drops a
        request while a scan is in flight, so a slow scan cannot pile ticks
        up. The early ``enabled`` check makes a disabled overlay pay nothing.
        """
        if not follow.enabled or not detector_available:
            return
        scanner.request()

    follow_timer = QTimer(palette)
    follow_timer.setInterval(FOLLOW_POLL_MS)
    follow_timer.timeout.connect(_detect_live_save)
    if follow.enabled and detector_available:
        follow_timer.start()

    def _on_bridge_save(request) -> None:
        """The mod told us which save the game is playing."""
        path = livesave.resolve_save_path(request.file)
        if path is None:
            logging.info("follow: mod reported save %r but it is not on disk",
                         request.file)
            return
        _feed_game_save(path)

    def _on_game_online(online: bool) -> None:
        """The mod connected or left: update the connection status.

        A disconnect on its own is not proof the game quit: with a detector
        available only the process scan decides that (after the grace period).
        Where there is no detector (no /proc) the disconnect keeps its old
        meaning and clears the pin.
        """
        nonlocal mod_connected
        mod_connected = online
        if not online:
            logging.info("follow: game disconnected")
        else:
            palette._set_status("game connected")
            logging.info("follow: game connected; scanning for its save")
        # Connecting marks the game online even before a save is known, so a
        # manual open from now on pins. A disconnect only clears the pin where
        # no process detector exists to confirm the game has really gone.
        follow.note_bridge_connection(online, detector_available)
        if online:
            _detect_live_save()

    bridge_ctl = None
    if settings.get("bridge_enabled", True):
        # The configured port, before the bind: ``bridge_ctl.port`` is None
        # until the socket is actually listening, so logging it would read
        # "port None busy?".
        bridge_port = int(settings.get("bridge_port", bridge.DEFAULT_PORT))
        bridge_ctl = bridgectl.BridgeController(port=bridge_port)

        def _on_bridge_focus(request) -> None:
            palette.select_reported_cat(request)

        def _on_bridge_raise(request) -> None:
            palette.raise_reported_cat(request)

        bridge_ctl.focus_requested.connect(_on_bridge_focus)
        bridge_ctl.raise_requested.connect(_on_bridge_raise)
        bridge_ctl.save_reported.connect(_on_bridge_save)
        bridge_ctl.game_online.connect(_on_game_online)
        if bridge_ctl.start():
            # Only attach a running bridge; the palette's in_game_available
            # gate then hides "Show in game" while it is off or no game is
            # connected.
            palette.attach_bridge(bridge_ctl)
        else:
            logging.warning("bridge: not listening this session (port %d busy?)",
                            bridge_port)
            palette._set_status(
                f"⚠ bridge port {bridge_port} is in use - another instance or "
                "program appears to own it; the game cannot connect")

        def _show_focused_cat_in_game() -> None:
            palette.show_focused_in_game()

        # Ctrl+G: show the cat the overlay is focused on back in the game.
        # Guarded: a QShortcut needs a live QApplication (tests stub it out).
        if _QtGuiApplication.instance() is not None:
            show_cat = QShortcut(QKeySequence("Ctrl+G"), palette)
            show_cat.activated.connect(_show_focused_cat_in_game)

    if args.save:
        # Apply the explicit startup choice before the queued "game
        # connected" notification arrives, so an already-running game is
        # marked online first and the choice pins instead of being overridden
        # by the live save. With no game running the detector sees nothing and
        # the choice stays unpinned.
        if detector_available and livesave.game_process_running():
            follow.note_game_online()
        palette.open_save_manual(args.save)
    if not args.hidden:
        palette.show()

    code = app.exec()

    if bridge_ctl is not None:
        bridge_ctl.stop()
    palette.shutdown()
    palette.uninstall_hotkey()
    if tray is not None:
        tray.hide()
    instance.close()
    return code


def _shortcut_cli(install: bool) -> int:
    """Run the desktop-shortcut manager headlessly for the CLI flags.

    Prints the manager's message to stdout (including manual instructions on
    failure) and returns 0 on success, 1 when the action did not complete.
    """
    from mewgenics_overlay.ui import desktopshortcut, hotkeybinding

    if install:
        binding = hotkeybinding.binding_or_default(
            ui_config.load().get("hotkey"))
        ok, message = desktopshortcut.install(binding)
    else:
        ok, message = desktopshortcut.remove()
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
