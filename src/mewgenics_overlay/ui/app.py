"""Bootstrap the overlay application.

Run with:

    PYTHONPATH=src python -m mewgenics_overlay            (GUI)
    PYTHONPATH=src python -m mewgenics_overlay.cli …      (headless)

Behaviour:
  * Creates a palette window, a system-tray toggle (when a tray exists) and,
    on Windows, a global Ctrl+Shift+B hotkey.
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
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from mewgenics_overlay import __version__
from mewgenics_overlay.ui import hotkey as hotkey_mod
from mewgenics_overlay.ui.palette import PaletteWindow
from mewgenics_overlay.ui.theme import STYLESHEET, apply_casual_font


def _make_tray_icon() -> QIcon:
    """Draw a small cat-ear tile so the tray icon is never blank."""
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#453a7a"))
    p.setPen(Qt.PenStyle.NoPen)
    # rounded tile
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    # cat ears
    ear = QColor("#8a7bf0")
    p.setBrush(ear)
    p.drawPolygon([QPoint(18, 24), QPoint(22, 4), QPoint(36, 18)])
    p.drawPolygon([QPoint(46, 24), QPoint(42, 4), QPoint(28, 18)])
    p.end()
    return QIcon(pm)


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
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The vendored parser logs per-cat defect detection at INFO — too chatty
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

    # On Hyprland run under XWayland: the always-on-top flag is honoured
    # reliably there, and Hyprland window rules (pin/float) can keep the
    # palette above a fullscreen game. Wayland-native can't guarantee that.
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and \
            "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    # QT_QPA_PLATFORMTHEME=gtk3 (common on NixOS/Hyprland) makes Qt's file
    # dialog initialise GTK/GIO, which aborts with 'No GSettings schemas are
    # installed on the system' when the schemas aren't in XDG_DATA_DIRS — a
    # hard crash the moment the 📁 picker opens. Fall back to the generic
    # theme for this app so dialogs stay pure Qt.
    if sys.platform.startswith("linux") and os.environ.get(
            "QT_QPA_PLATFORMTHEME", "").lower() == "gtk3":
        os.environ["QT_QPA_PLATFORMTHEME"] = "generic"
        logging.info("QT_QPA_PLATFORMTHEME=gtk3 would crash file dialogs "
                     "without GSettings schemas — using generic")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("mewgenics-overlay")
    apply_casual_font(app)
    app.setStyleSheet(STYLESHEET)

    palette = PaletteWindow()
    hotkey = hotkey_mod.install(app, palette.toggle_activate)
    if not hotkey.active:
        logging.info("global hotkey unavailable; use the tray icon to toggle")
    tray = None
    if not args.no_tray:
        tray = _build_tray(app, palette)
    app.aboutToQuit.connect(palette._save_geometry)

    if args.save:
        palette.open_save(args.save)
    if not args.hidden:
        palette.show()

    code = app.exec()

    palette.shutdown()
    hotkey.uninstall()
    if tray is not None:
        tray.hide()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
