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
inject a cat key the same way `set_focus_key()` does.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, QTimer
import mewgenics_overlay.ui.theme as _theme
from PySide6.QtGui import (
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizeGrip,
    QListWidget,
    QPushButton,
    QSystemTrayIcon,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.core.session import (
    Cat,
    Session,
)
from mewgenics_overlay.ui.savecontroller import SaveController
from mewgenics_overlay.ui.chrome import TopBar
from mewgenics_overlay.ui.focuspanel import FocusedCatPanel
from mewgenics_overlay.ui.roombar import RoomBar
from mewgenics_overlay.ui.searchbox import SearchBox
from mewgenics_overlay.ui import windowstate as _win_state
from mewgenics_overlay.ui.windowstate import WindowController

# Partner-table pure core (columns, header tips, cell formatters) - moved to
# ui/partnertable.py so the interactive widget can stay Qt-focused (step 2b).
from .partnertable import (
    COL_TIPS,
    PartnerTableWidget,
)

# self-contained panels split out of this window (god-file steps 4 and 5)
from .bestmatch import BestMatchBar, SAFE_CAP_DEFAULT
from .partneractions import PartnerActions
from .themectl import ThemeController
from .updatenotice import UpdateNotice

# About/report/debug block and the user-zoom machinery (final split steps)
from .aboutdialog import (
    open_report,
    show_about,
)
from .zoom import ZoomController

from . import config as cfg
from .theme import wrap_tooltip as _wt
from .pinning import PinningStore
from .savepanel import SavePanel
from .reloader import ReloadCoordinator
from mewgenics_overlay.core.gameassets import GameAssets, locate_gpak

_ROOT_SPACING = 6   # shared by the palette root layout and the SearchBox

log = logging.getLogger("mewgenics_overlay.ui")


@dataclass
class _AssetLoad:
    """One finished background gpak load: the assets plus any parse error.

    ``_drain_assets`` must tell "no resources.gpak was found" (no load is
    started, so no result ever arrives) from "the gpak exists but failed to
    parse" (a result with ``error`` set). The first is a normal optional
    feature being absent; the second is a compute failure worth a status line.
    """

    assets: Optional[GameAssets] = None
    error: Optional[Exception] = None

class PaletteWindow(QWidget):
    """The overlay palette: the window, its views and the user's selection.

    Save watching, background parsing/ranking and the drain pump live in
    :class:`~mewgenics_overlay.ui.reloader.ReloadCoordinator`; this window
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
        self._focus: Optional[Cat] = None
        self._save = SaveController()   # session state + background queue + watcher
        self._asset_lock = threading.Lock()
        self._table: Optional[PartnerTableWidget] = None  # built in _build_ui
        self._ga: Optional[GameAssets] = None   # gpak effect tables (async)
        self._assets_started = False
        self._asset_result: Optional[_AssetLoad] = None

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(880, 600)
        self.setMinimumSize(620, 460)
        self._build_ui()
        # framing + geometry pose no native window until the first show
        self._win.configure_frame()
        self._win.restore_geometry()
        self.setWindowTitle("Mewgenics Breeding Overlay")
        self.setStyleSheet(_theme.stylesheet())
        self._wire_ui()
        # Watcher -> debounced reload -> background parse -> drain onto the UI
        # thread, plus partner-job scheduling (ui/reloader.py owns that path).
        # Built here, exactly where the poll timer it replaces used to start:
        # the views and their callbacks must exist before the first tick.
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
        self._maybe_start_assets()

    # ── save/session state (delegated to SaveController) ───────────────────
    @property
    def _session(self) -> Optional[Session]:
        return self._save.session

    @_session.setter
    def _session(self, sess: Optional[Session]) -> None:
        self._save.session = sess

    # ── partner-table data + sort state (delegated to PartnerTableWidget) ──
    @property
    def _rows(self) -> list:
        return self._table.rows if self._table is not None else []

    @_rows.setter
    def _rows(self, rows) -> None:
        if self._table is not None:
            self._table.set_rows(rows)

    @property
    def _sort_col(self) -> Optional[int]:
        return self._table.sort_col if self._table is not None else None

    @_sort_col.setter
    def _sort_col(self, col: Optional[int]) -> None:
        if self._table is not None:
            self._table.sort_col = col

    @property
    def _sort_dir(self) -> str:
        return self._table.sort_dir if self._table is not None else "asc"

    @_sort_dir.setter
    def _sort_dir(self, d: str) -> None:
        if self._table is not None:
            self._table.sort_dir = d

    # ── header chrome (delegated to TopBar) ───────────────────────────────
    @property
    def _title(self) -> QLabel:
        """Read-only access to the header title label."""
        return self._chrome.title_label

    @property
    def _status(self) -> QLabel:
        """Read-only access to the header status label."""
        return self._chrome.status_label

    # ── update availability check (delegated to UpdateNotice) ─────────────
    def _set_update_check(self, on: bool) -> None:
        self._settings["check_for_updates"] = bool(on)
        cfg.save(self._settings)

    def _maybe_start_assets(self) -> None:
        """Load resources.gpak effect tables off the UI thread (once)."""
        if self._assets_started:
            return
        self._assets_started = True
        path = locate_gpak()
        if not path:
            # No gpak on this machine: the optional feature is simply absent.
            log.info("no resources.gpak found - defect effect text disabled")
            return

        def work():
            result = _AssetLoad()
            try:
                result.assets = GameAssets(path)
            except Exception as exc:
                # The gpak exists but failed to parse: log the reason and pass
                # the failure on so the drain can surface it (compute-failed,
                # distinct from "no gpak found").
                log.exception("failed to read resources.gpak %s", path)
                result.error = exc
            with self._asset_lock:
                self._asset_result = result

        threading.Thread(target=work, name="gpak-assets", daemon=True).start()

    # ── UI construction ───────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # tabbed layout: Breeding (main) + Donations
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        outer.addWidget(tabs)

        self._page_main = QWidget()
        root = QVBoxLayout(self._page_main)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(_ROOT_SPACING)
        tabs.addTab(self._page_main, "Breeding")

        # header: drag grip + title + status + pin/click-through/hide. The row
        # is a self-contained widget (ui/chrome.py); the actions it triggers
        # stay here. Extra actions live in the ⚙ Settings tab / tab corner.
        self._chrome = TopBar(
            pinned=_win_state.PIN_DEFAULT,
            click_through=_win_state.CLICK_THROUGH_DEFAULT,
            on_pin=self._toggle_pin,
            on_click_through=self._on_ct_clicked,
            on_hide=self._on_close_clicked,
        )
        self._win = WindowController(
            self, self._chrome, self._settings,
            lambda: cfg.save(self._settings))
        outer.insertWidget(0, self._chrome)

        # search (owns its own line edit + results dropdown)
        self._searchbox = SearchBox(
            lambda: self._session,
            self._on_search_chosen,
            self._on_search_cleared,
            spacing=_ROOT_SPACING,
        )
        root.addWidget(self._searchbox)

        # focused cat summary
        self._focus_panel = FocusedCatPanel()
        root.addWidget(self._focus_panel)
        row2 = QHBoxLayout()
        self._room_bar = RoomBar(
            session_getter=lambda: self._session,
            assets_getter=lambda: self._ga,
            focus_getter=lambda: self._focus,
            on_change=self._on_room_change,
        )
        self._btn_swap = QPushButton("Hide blocked rows")
        self._btn_swap.setCheckable(True)
        self._btn_swap.setToolTip(_wt(
            "When checked, pairs that cannot breed (direct family, hater, "
            "sexuality blocks) are hidden instead of listed below."
        ))
        row2.addWidget(self._room_bar)
        row2.addStretch(1)
        row2.addWidget(self._btn_swap)
        _row_holder = QWidget()
        _row_holder.setLayout(row2)
        self._focus_panel.append_row(_row_holder)

        # best-match banner (+ safe-mode switch) - self-contained widget that
        # reads the live rows/focus/room values through these callables.
        self._best_bar = BestMatchBar(
            on_select=self._on_best_selected,
            rows_getter=lambda: self._rows,
            focus_getter=lambda: self._focus,
            stim_getter=self._stim_value,
            comfort_getter=self._comfort_value,
            malady_lines=lambda row, stim, effect: (
                self._table.pair_malady_lines(row, stim, effect)),
            effect_of=self._effect_for_name,
            safe_risk_cap=lambda: self._to_float(
                self._settings.get("safe_risk_cap", SAFE_CAP_DEFAULT),
                SAFE_CAP_DEFAULT, floor=1.0, ceil=100.0),
            spacing=_ROOT_SPACING,
        )
        root.addWidget(self._best_bar)

        # partners
        self._table = PartnerTableWidget(self)
        self._table.set_header_tooltips(COL_TIPS, _wt)
        root.addWidget(self._table, 1)

        # detail strip
        self._detail = QLabel("Select a partner row for inheritance detail.")
        self._detail.setWordWrap(True)
        self._detail.setObjectName("muted")
        # can embed partner names (save-derived) - never auto-rich-text them
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._detail.setToolTip(_wt(
            "Information about the row you have highlighted:\n"
            "• What each kitten stat could come out as (the possible range "
            "per parent).\n"
            "• Kittens this pair has already produced together."
        ))
        root.addWidget(self._detail)

        # partner-row interaction: selection detail, right-click pin menu
        # and double-click re-focus (ui/partneractions.py).
        self._actions = PartnerActions(
            self._table,
            self._detail,
            self._stim_value,
            lambda row, stim, effect: self._table.pair_malady_lines(
                row, stim, effect),
            self._effect_for_name,
            on_pin=self.set_pinned,
            on_focus=self.set_focus,
            parent=self,
        )

        # Donations tab (last, so it exists before sessions arrive)
        from mewgenics_overlay.ui.donations_tab import DonationsTab

        self._tabs = tabs
        self._donations_tab = DonationsTab(palette=self)
        tabs.addTab(self._donations_tab, "Donations")
        # the update notice sits on the right of the tab row
        self._update_notice = UpdateNotice(
            self._settings, lambda: cfg.save(self._settings))

        corner = QWidget()
        cl = QHBoxLayout(corner)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(2)
        cl.addWidget(self._update_notice)
        tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        # ⚙ Settings tab (save / appearance / zoom / help). The save-slot
        # cards and the picker are their own widget (ui/savepanel.py), built
        # here where those controls used to live in this window.
        from mewgenics_overlay.ui.settings_tab import SettingsTab
        self._save_panel = SavePanel(
            self._settings, on_open=self.open_save)
        self._settings_tab = SettingsTab(
            {
                "set_theme": self.apply_theme,
                "zoom_in": self._zoom_inc,
                "zoom_out": self._zoom_dec,
                "zoom_reset": self._zoom_default,
                "about": self._show_about,
                "report": self._open_report,
                "set_check_updates": self._set_update_check,
            },
            self._save_panel,
            titles={k: _theme.THEMES[k]["title"] for k in _theme.THEMES},
        )
        tabs.addTab(self._settings_tab, "⚙ Settings")
        # Sync the checkbox with the persisted setting (default on) so the UI
        # matches the config at startup. The setter blocks its own signal, so
        # this never writes the unchanged value back to disk.
        self._settings_tab.set_check_updates(
            bool(self._settings.get("check_for_updates", True)))
        # resize handle in the bottom-right corner (frameless window)
        size_row = QHBoxLayout()
        size_row.setContentsMargins(6, 0, 6, 4)
        size_row.addStretch(1)
        grip_w = QSizeGrip(self)
        grip_w.setFixedSize(22, 22)
        grip_w.setToolTip("Drag the corner to resize the window")
        size_row.addWidget(grip_w)
        outer.addLayout(size_row)

        self._save_panel.refresh()

        self._tabs = tabs

    def _wire_ui(self) -> None:
        self._table.itemSelectionChanged.connect(self._on_partner_selected)
        self._table.itemDoubleClicked.connect(self._on_partner_double)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_breeding_menu)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._btn_swap.toggled.connect(self._recompute_partners)

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
    def _zoom_step(self, delta: float) -> None:
        self._zoom_ctl.step(delta)

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

    def _set_zoom(self, z: float) -> None:
        """Persist and apply a new zoom level."""
        self._zoom_ctl.set_zoom(z)

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
        if self._rows:
            self._redraw_table()
        if self._focus is not None:
            self._show_focus(self._focus)
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
        if self._ga is None:
            return ""
        for entry in (getattr(cat, "visual_mutation_entries", None) or []):
            if entry.get("is_defect") and entry.get("name") == name:
                text = self._ga.effect_for(entry.get("group_key"),
                                           entry.get("mutation_id"))
                if text:
                    return text
        return ""

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
        base = path.split('/')[-1].split(chr(92))[-1]
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
        focus = self._focus
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
        with self._asset_lock:
            result = self._asset_result
            self._asset_result = None
        if result is None:
            return
        if result.error is not None:
            # The gpak was found but unreadable: say so instead of silently
            # showing no effect text ("no data" vs "compute failed").
            self._set_status("⚠ could not read resources.gpak - defect "
                             "effect text unavailable, see the log")
            return
        assets = result.assets
        if assets is None:
            return
        self._ga = assets if assets.ok else None
        self._room_bar.refresh()          # room Stimulation now available
        if self._rows:
            self._redraw_table()          # effects now available in tooltips
        if self._focus is not None:
            self._show_focus(self._focus)  # refresh health/effect tooltip

    def _on_partner_rows(self, cat_key: int, rows: list) -> None:
        """Rows for *cat_key* arrived (token already checked): show them if
        that cat is still the focused one - the selection is ours to know."""
        if self._focus is not None and self._focus.db_key == cat_key:
            self._render_partners(rows)

    def _adopt_session(self, sess: Optional[Session]) -> None:
        """Adopt a parsed session (or ``None``) through the coordinator."""
        self._reloader.adopt_session(sess)

    def _on_session_adopted(self, sess: Optional[Session]) -> None:
        """Rebuild the views for a newly adopted session.

        Called by the coordinator once the session and the status line are in
        place; the ordering (focus, room bar, pins, donations) is unchanged
        from the pre-extraction window.
        """
        # keep focus if the cat still exists
        if sess is not None and self._focus is not None:
            cat = sess.by_key.get(self._focus.db_key)
            self._focus = cat or None
        if self._focus is None:
            self._clear_focus()
        else:
            self._show_focus(self._focus)
            self._schedule_partners()
        self._room_bar.refresh()
        self._sync_pins()
        self._donations_tab.refresh(self._session)

    # ── breeding-room Stimulation (delegated to RoomBar) ───────────────────
    @staticmethod
    def _to_float(raw, default: float, floor: Optional[float] = None,
                  ceil: Optional[float] = None) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
        if floor is not None:
            value = max(value, floor)
        if ceil is not None:
            value = min(value, ceil)
        return value

    def _stim_value(self) -> float:
        """Active Stimulation for pair math (selected room's furniture value
        or the default 50 when no room is chosen)."""
        return self._room_bar.stim_value()

    def _comfort_value(self) -> float:
        return self._room_bar.comfort_value()

    def _on_room_change(self) -> None:
        """RoomBar selection/refresh changed the numbers: recompute rows."""
        if self._focus is not None:
            self._schedule_partners()

    def set_focus_key(self, db_key: int) -> None:
        """Programmatic focus (used by the future in-game bridge)."""
        if self._session is None:
            return
        cat = self._session.by_key.get(db_key)
        if cat is not None:
            self.set_focus(cat)

    def set_focus(self, cat: Cat) -> None:
        self._focus = cat
        self._searchbox.set_text_silently("")
        self._searchbox.clear_focus()
        self._show_focus(cat)
        self._schedule_partners()

    def _on_search_cleared(self) -> None:
        """Manual clear in the search box: reset the table only when there
        is something to reset (pre-extraction behaviour)."""
        if self._focus is not None or self._table.rowCount() > 0:
            self._clear_focus()

    def _on_search_chosen(self, db_key: int) -> None:
        """A dropdown result was picked: focus that cat."""
        if self._session is None:
            return
        cat = self._session.by_key.get(db_key)
        if cat is not None:
            self.set_focus(cat)

    # ── search box (delegated to SearchBox) ────────────────────────────────
    @property
    def _search(self) -> QLineEdit:
        return self._searchbox.edit

    @property
    def _results(self) -> QListWidget:
        return self._searchbox.list

    def _clear_focus(self) -> None:
        self._focus = None
        self._focus_panel.clear()
        self._best_bar.clear()
        self._table.setRowCount(0)
        self._detail.setText("Select a partner row for inheritance detail.")

    def _show_focus(self, cat: Cat) -> None:
        def _gpak_effect(group_key, mutation_id):
            return self._ga.effect_for(group_key, mutation_id) \
                if self._ga is not None else ""
        self._focus_panel.show_cat(cat, effect_for=_gpak_effect)

    # ── partners table ─────────────────────────────────────────────────────
    def _recompute_partners(self) -> None:
        if self._focus is not None:
            self._schedule_partners()

    def _render_partners(self, rows: list) -> None:
        """Store computed partner rows and redraw in the current sort order."""
        self._rows = list(rows)
        self._sort_col = None        # new data -> back to engine's safe-first order
        self._redraw_table()
        self._update_best()

    # ── best match (delegated to BestMatchBar) ─────────────────────────────
    def _update_best(self) -> None:
        """Delegate the ⭐ banner to BestMatchBar - it reads the live rows,
        focus and room Stimulation/Comfort through the callables wired in
        ``_build_ui``."""
        self._best_bar.update_best()

    def _on_best_selected(self, db_key: int) -> None:
        """The ⭐ pick was clicked: select and reveal that partner row."""
        for ri in range(self._table.rowCount()):
            it = self._table.item(ri, 0)
            data = it.data(Qt.ItemDataRole.UserRole) if it else None
            if data and data[0].partner.db_key == db_key:
                self._table.setCurrentCell(ri, 0)
                self._table.scrollToItem(it)
                self._on_partner_selected()
                return

    # ── sorting ────────────────────────────────────────────────────────────
    def _on_header_clicked(self, col: int) -> None:
        """Tri-state sort: asc -> desc -> back to default order."""
        self._table.toggle_sort(col)
        self._redraw_table()

    # ── table rendering ────────────────────────────────────────────────────
    def _redraw_table(self) -> None:
        """Render the partner rows via PartnerTableWidget."""
        self._table.redraw(
            stimulation=self._stim_value(),
            comfort=self._comfort_value(),
            effect_of=self._effect_for_name
            if self._ga is not None else None)
    def _effect_for_name(self, a, b, name: str) -> str:
        """Look up the first known gpak effect for a defect carried by a/b."""
        if self._ga is None:
            return ""
        for cat in (a, b):
            for e in (getattr(cat, "visual_mutation_entries", None) or []):
                if e.get("is_defect") and e.get("name") == name:
                    text = self._ga.effect_for(e.get("group_key"),
                                               e.get("mutation_id"))
                    if text:
                        return text
        return ""

    def _on_partner_selected(self) -> None:
        """Selection changed: show that row's inheritance detail."""
        self._actions.on_partner_selected()

    def _show_breeding_menu(self, pos) -> None:
        """Right-click a partner row to pin/unpin them as a keeper."""
        self._actions.show_breeding_menu(pos)

    def _on_partner_double(self, item: QTableWidgetItem) -> None:
        """Double-click: analyse breeding from the partner's side instead."""
        self._actions.on_partner_double(item)
