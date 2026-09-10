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

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from mewgenics_overlay import __version__
from mewgenics_overlay.ui import config as ui_config
from mewgenics_overlay.ui import singleton
from mewgenics_overlay.ui import theme as _theme
from mewgenics_overlay.ui.palette import PaletteWindow


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
    _theme_key = ui_config.load().get("theme", _theme.DEFAULT_THEME)
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

    if args.save:
        palette.open_save(args.save)
    if not args.hidden:
        palette.show()

    code = app.exec()

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
