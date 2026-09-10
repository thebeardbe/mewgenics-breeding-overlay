"""The overlay palette: pick a cat, see ranked breeding partners.

Layout (compact, always-summonable):

    [header: title · save · status · pin · close]
    [cat search: QLineEdit + results popup list]
    [focused cat: name, chips (gender/room/gen/age), stats, lovers, open-in-MBM]
    [partners table: name | room | risk% | compat | exp/stat | >=7 | note]
    [detail strip for the selected partner]

Data flows through a single background worker + a generation token so the UI
thread never parses or scores. Selection input is pluggable: v1 is search on
top of the live save; an in-game hook bridge (see core/bridge notes) can
inject a cat key the same way ``set_focus_key()`` does.

This window is a coordinator only: widget construction lives in
``ui/layout.py``, the gpak asset load in ``ui/assets.py``, the focus/table
behaviour in ``ui/tablectl.py``, and save watching/parsing in the
``ui/reloader.py`` + ``ui/savecontroller.py`` pair. Thin delegations remain
for the tray, the global hotkey and ``scripts/gui_smoke.py``.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
import mewgenics_overlay.ui.theme as _theme
from PySide6.QtGui import (
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QListWidget,
    QSystemTrayIcon,
    QTableWidgetItem,
    QWidget,
)

from mewgenics_overlay.core.maladies import defect_effect_text
from mewgenics_overlay.core.session import (
    Cat,
    Session,
)
from mewgenics_overlay.ui import layout as _layout
from mewgenics_overlay.ui.assets import AssetLoader
from mewgenics_overlay.ui.savecontroller import SaveController
from mewgenics_overlay.ui.savepanel import base_name

# self-contained panels split out of this window (god-file steps 4 and 5)
from .themectl import ThemeController

# About/report/debug block and the user-zoom machinery (final split steps)
from .aboutdialog import (
    open_report,
    show_about,
)
from .zoom import ZoomController

from . import config as cfg
from .pinning import PinningStore
from .reloader import ReloadCoordinator


class PaletteWindow(QWidget):
    """The overlay palette: the window, its views and the user's selection.

    Save watching, background parsing/ranking and the drain pump live in
    :class:`~mewgenics_overlay.ui.reloader.ReloadCoordinator`; the gpak load
    lives in :class:`~mewgenics_overlay.ui.assets.AssetLoader`; the focus and
    partner-table behaviour in
    :class:`~mewgenics_overlay.ui.tablectl.TableCoordinator`. This window
    keeps thin delegations (``open_save``, ``shutdown``, …) for the tray
    menu, the global hotkey and ``scripts/gui_smoke.py``.
    """

    def __init__(self):
        super().__init__()
        self._settings = cfg.load()
        # Zoom state, clamping and persistence live in the controller; it
        # rescales the app font itself and calls _zoom_render for the
        # window-specific visuals (header buttons, columns, stylesheets).
        self._zoom_ctl = ZoomController(
            self._settings,
            lambda: cfg.save(self._settings),
            on_zoom=self._zoom_render,
            on_label=self._on_zoom_label,
            parent=self,
        )
        _theme.set_zoom(self._zoom_ctl.zoom)
        # The keep-list (per-save pinned cats) persists through the settings
        # dict; on_change re-renders the tables that show the 📌 marker.
        self._pins = PinningStore(
            self._settings,
            lambda: cfg.save(self._settings),
            self._refresh_theme,
        )
        # Theme choice, persistence and the switch sequence live in the
        # controller (ui/themectl.py); the callbacks cover the widget-level
        # restyle and the re-render of what cached theme colours.
        self._themes = ThemeController(
            self._settings,
            lambda: cfg.save(self._settings),
            self._apply_theme_styles,
            self._refresh_theme,
            on_active=self._on_theme_active,
            parent=self,
        )
        self._save = SaveController()   # session state + background queue + watcher
        # The resources.gpak effect tables load once, off the UI thread; the
        # drain runs first on every reloader poll tick (ui/assets.py).
        self._assets = AssetLoader(
            on_status=self._set_status,
            on_ready=self._on_assets_ready,
            parent=self,
        )

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(880, 600)
        self.setMinimumSize(620, 460)
        self._build_ui()
        # framing + geometry pose no native window until the first show
        self._win.configure_frame()
        self._win.restore_geometry()
        self.setWindowTitle("Mewgenics Breeding Overlay")
        self.setStyleSheet(_theme.stylesheet())
        # Watcher -> debounced reload -> background parse -> drain onto the UI
        # thread, plus partner-job scheduling (ui/reloader.py owns that path).
        self._reloader = ReloadCoordinator(
            self._save,
            on_session=self._on_session_adopted,
            on_partners=self._on_partner_rows,
            on_status=self._set_status,
            on_tick=self._drain_assets,
            parent=self,
        )
        QShortcut(QKeySequence("Ctrl++"), self, activated=self._zoom_inc)
        QShortcut(QKeySequence("Ctrl+-"), self, activated=self._zoom_dec)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self._zoom_default)
        QTimer.singleShot(2500, self._update_notice.start_check)
        # also re-check periodically while the overlay stays open (still
        # interval-gated, so it only hits GitHub when it should).
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(60 * 60 * 1000)
        self._update_timer.timeout.connect(self._update_notice.start_check)
        self._update_timer.start()

        self._zoom_ctl.apply()
        self._adopt_session(None)
        self._load_last_save()
        self._assets.start()

    # ── save/session state (delegated to SaveController) ───────────────────
    @property
    def _session(self) -> Optional[Session]:
        return self._save.session

    # ── partner-table data + sort state (delegated to TableCoordinator) ────
    @property
    def _sort_col(self) -> Optional[int]:
        return self._tablectl.sort_col

    @property
    def _focus(self) -> Optional[Cat]:
        """The cat being analysed (delegated to TableCoordinator)."""
        return self._tablectl.focus

    # ── update availability check (delegated to UpdateNotice) ─────────────
    def _set_update_check(self, on: bool) -> None:
        self._settings["check_for_updates"] = bool(on)
        cfg.save(self._settings)

    # ── UI construction ───────────────────────────────────────────────────
    def _build_ui(self) -> None:
        """Build the whole widget tree (ui/layout.py owns the construction)."""
        _layout.build(self)

    # ── window behaviour (delegated to WindowController) ───────────────────
    @property
    def _pinned(self) -> bool:
        return self._win.pinned

    @property
    def _click_through(self) -> bool:
        return self._win.click_through

    @property
    def _dialog_open(self) -> bool:
        return self._win.dialog_open

    @_dialog_open.setter
    def _dialog_open(self, on: bool) -> None:
        self._win.dialog_open = on

    def _toggle_pin(self, checked: bool) -> None:
        self._win.toggle_pin(checked)

    def _on_ct_clicked(self, checked: bool) -> None:
        self._win.on_click_through_clicked(checked)

    def set_click_through(self, on: bool) -> None:
        self._win.set_click_through(on)

    def _engage(self) -> None:
        self._win.engage()

    def toggle_activate(self) -> None:
        self._win.toggle_activate()

    def showEvent(self, event):  # noqa: N802 (Qt API)
        super().showEvent(event)
        self._win.on_show()

    def hideEvent(self, event):  # noqa: N802 (Qt API)
        self._win.on_hide()
        super().hideEvent(event)

    def changeEvent(self, event):  # noqa: N802 (Qt API)
        self._win.on_change(event)
        super().changeEvent(event)

    def _save_geometry(self) -> None:
        self._win.save_geometry()

    def _on_close_clicked(self) -> None:
        """Hide when a tray icon can bring us back; otherwise quit."""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        else:
            self.shutdown()
            QApplication.instance().quit()

    # ── zoom (delegated to ZoomController) ─────────────────────────────────
    def _zoom_inc(self) -> None:
        self._zoom_ctl.zoom_in()

    def _zoom_dec(self) -> None:
        self._zoom_ctl.zoom_out()

    def _zoom_default(self) -> None:
        self._zoom_ctl.reset()

    def _on_zoom_label(self, pct: int) -> None:
        """Zoom readout in the Settings tab (absent before _build_ui)."""
        if getattr(self, "_settings_tab", None) is not None:
            self._settings_tab.set_zoom(pct)

    def _zoom_render(self, z: float) -> None:
        """Re-zoom the window-specific visuals the controller cannot see:
        header buttons, table column widths and theme stylesheet font sizes.
        The app font and ``theme.set_zoom`` are handled by the controller."""
        self._chrome.scale_buttons(z)
        if self._table is not None:
            self._table.scale_columns(z)
        self.apply_theme(str(self._settings.get("theme", "noir")))
        self._chrome.restyle(z)
        if getattr(self, "_focus_panel", None) is not None:
            self._focus_panel.restyle(z)

    def wheelEvent(self, event):  # noqa: N802 (Qt API)
        if self._zoom_ctl.handle_wheel(event):
            return
        super().wheelEvent(event)

    # ── theme (delegated to ThemeController) ───────────────────────────────
    def apply_theme(self, key: str) -> None:
        """Switch the active theme, restyle the app and re-render colours."""
        self._themes.apply_theme(key)

    def _on_theme_active(self, key: str) -> None:
        """Theme readout in the Settings tab (absent before _build_ui)."""
        if getattr(self, "_settings_tab", None) is not None:
            self._settings_tab.set_active_theme(key)

    def _apply_theme_styles(self, css: str) -> None:
        """Apply *css* app-wide, to the palette (whose own stylesheet shadows
        the app-wide one) and to the widgets that carry their own colours."""
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(css)
        self.setStyleSheet(css)
        from mewgenics_overlay.ui.comboarrow import style_combo
        room_bar = getattr(self, "_room_bar", None)
        if room_bar is not None:
            room_bar.restyle()
        donations = getattr(self, "_donations_tab", None)
        npc_combo = getattr(donations, "_combo", None)
        if npc_combo is not None:
            style_combo(npc_combo, _theme.C_MUTED)

    def _refresh_theme(self) -> None:
        """Repaint everything that cached theme colours."""
        self._tablectl.refresh_theme()
        donations = getattr(self, "_donations_tab", None)
        if donations is not None:
            donations.refresh(self._session)

    # ── pinning (keep-list, delegated to PinningStore) ─────────────────────
    def _sync_pins(self) -> None:
        """Apply the saved keep-list to the live session."""
        self._pins.sync(self._session)

    def set_pinned(self, cat, on: bool) -> None:
        """Pin/unpin a cat (kept as a breeder; Gone cats are pruned)."""
        self._pins.set_pinned(cat, on)

    def defect_text_of(self, cat, name: str) -> str:
        """Effect text for one of *cat*'s defects (for the donation matrix)."""
        return defect_effect_text(self._assets.assets, cat, name)

    # ── about / report / debug (delegated to ui/aboutdialog.py) ────────────
    def _show_about(self) -> None:
        """Credits dialog: who built it and whose research it stands on."""
        show_about(self, self._settings, self._set_status)

    def _open_report(self) -> None:
        open_report(self._settings.get("report_url"))

    # ── save loading (slot/file UI delegated to SavePanel) ─────────────────
    def _load_last_save(self) -> None:
        """Startup: reopen the remembered save, else the newest on disk."""
        if self._save_panel.load_last() is None:
            self._set_status("no save found - use 📁 to locate one")

    def _pick_save(self) -> None:
        """Open the save picker (tray menu / Settings tab).

        The dialog is modal, so auto click-through is suspended while it is
        open; the picker itself lives in SavePanel.
        """
        self._dialog_open = True
        try:
            self._save_panel.pick()
        finally:
            self._dialog_open = False

    def open_save(self, path: str) -> None:
        """(Re)load a save file; safe to call repeatedly / from any thread."""
        # watch on the *copied* path isn't needed: watch the real file.
        self._settings["save_path"] = path
        cfg.save(self._settings)
        base = base_name(path)
        self._chrome.set_title(f"🐈 Overlay - {base}")
        self._save_panel.set_current(path)
        self._reloader.start(path)
        self._set_status("loading save…")
        self._reloader.request_reload()

    def _on_save_changed(self) -> None:
        """The watched save was rewritten: reload it (UI thread).

        Delegated to the coordinator, which owns the watcher signal; kept as
        a named method because ``scripts/gui_smoke.py`` drives this path.
        """
        self._reloader.on_save_changed()

    def _set_status(self, text: str) -> None:
        self._chrome.set_status(text)

    def shutdown(self) -> None:
        """Stop background threads before the app exits."""
        self._reloader.stop()

    # ── background work (delegated to ReloadCoordinator) ───────────────────
    def _schedule_partners(self) -> None:
        """Gather the current view settings and queue a partner ranking."""
        focus = self._tablectl.focus
        if focus is None:
            return
        self._reloader.schedule_partners(
            focus.db_key,
            int(self._settings.get("max_partners", 100)),
            0 if self._btn_swap.isChecked() else None,
            bool(self._settings.get("include_adventure", True)),
            str(self._settings.get("order", "risk")),
            self._stim_value(),
        )

    def _drain_assets(self) -> None:
        """Collect a finished gpak load (called by the coordinator's poll)."""
        self._assets.drain()

    def _on_assets_ready(self) -> None:
        """Useable gpak effect tables arrived: refresh the views that want
        them (room Stimulation, table tooltips, focus card health text)."""
        self._room_bar.refresh()
        self._tablectl.refresh_theme()

    def _on_partner_rows(self, cat_key: int, rows: list) -> None:
        """Rows for *cat_key* arrived (token already checked): show them if
        that cat is still the focused one - the selection is ours to know."""
        focus = self._tablectl.focus
        if focus is not None and focus.db_key == cat_key:
            self._tablectl.render_partners(rows)

    def _adopt_session(self, sess: Optional[Session]) -> None:
        """Adopt a parsed session (or ``None``) through the coordinator."""
        self._reloader.adopt_session(sess)

    def _on_session_adopted(self, sess: Optional[Session]) -> None:
        """Rebuild the views for a newly adopted session.

        Called by the coordinator once the session and the status line are in
        place; the ordering (focus, room bar, pins, donations) is unchanged
        from the pre-extraction window.
        """
        self._tablectl.adopt_session(sess)
        self._room_bar.refresh()
        self._sync_pins()
        self._donations_tab.refresh(self._session)

    # ── breeding-room Stimulation (delegated to RoomBar) ───────────────────
    def _stim_value(self) -> float:
        """Active Stimulation for pair math (selected room's furniture value
        or the default 50 when no room is chosen)."""
        return self._room_bar.stim_value()

    def _comfort_value(self) -> float:
        return self._room_bar.comfort_value()

    def _on_room_change(self) -> None:
        """RoomBar selection/refresh changed the numbers: recompute rows."""
        if self._tablectl.focus is not None:
            self._schedule_partners()

    # ── focus / table (delegated to TableCoordinator) ──────────────────────
    def set_focus_key(self, db_key: int) -> None:
        """Programmatic focus (used by the future in-game bridge)."""
        self._tablectl.set_focus_key(db_key)

    def set_focus(self, cat: Cat) -> None:
        self._tablectl.set_focus(cat)

    def _clear_focus(self) -> None:
        """Drop the focused cat and empty the table (coordinator behaviour)."""
        self._tablectl.clear_focus()

    def _on_search_cleared(self) -> None:
        """Manual clear in the search box: reset the table only when there
        is something to reset (the coordinator owns that decision)."""
        self._tablectl.clear_if_content()

    def _on_header_clicked(self, col: int) -> None:
        """Tri-state sort: asc -> desc -> back to default order."""
        self._tablectl.on_header_clicked(col)

    # ── search box (delegated to SearchBox) ────────────────────────────────
    @property
    def _search(self) -> QLineEdit:
        return self._searchbox.edit

    @property
    def _results(self) -> QListWidget:
        return self._searchbox.list

    # ── partner-row actions (delegated to PartnerActions) ──────────────────
    def _on_partner_selected(self) -> None:
        """Selection changed: show that row's inheritance detail."""
        self._actions.on_partner_selected()

    def _show_breeding_menu(self, pos) -> None:
        """Right-click a partner row to pin/unpin them as a keeper."""
        self._actions.show_breeding_menu(pos)

    def _on_partner_double(self, item: QTableWidgetItem) -> None:
        """Double-click: analyse breeding from the partner's side instead."""
        self._actions.on_partner_double(item)
