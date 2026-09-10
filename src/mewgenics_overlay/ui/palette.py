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
import os
import threading
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
import mewgenics_overlay.ui.theme as _theme
from PySide6.QtGui import (
    QFont,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizeGrip,
    QListWidget,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.core.session import (
    STAT_NAMES,
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
    _COL_TIPS,
    _better_stat_expectation,
    PartnerTableWidget,
)

# self-contained panels split out of this window (god-file steps 4 and 5)
from .bestmatch import BestMatchBar
from .updatenotice import UpdateNotice

from . import config as cfg
from .theme import wrap_tooltip as _wt
from mewgenics_overlay import __version__
from mewgenics_overlay.core.gameassets import GameAssets, locate_gpak

log = logging.getLogger("mewgenics_overlay.ui")

_ROOT_SPACING = 6   # shared by the palette root layout and the SearchBox

class PaletteWindow(QWidget):
    """The overlay palette. Owns the save session, watcher and worker."""

    # The save watcher fires from its own thread; a signal is the Qt-safe way
    # to hand that notification back to the UI thread (connected in _wire_ui).
    _save_changed = Signal()

    def __init__(self):
        super().__init__()
        self._settings = cfg.load()
        self._zoom_base_font = QFont(
            QApplication.instance().font()) if QApplication.instance() else None
        _theme.set_zoom(float(self._settings.get("zoom", 1.0) or 1.0))
        self._focus: Optional[Cat] = None
        self._save = SaveController()   # session state + background queue + watcher
        self._asset_lock = threading.Lock()
        self._table: Optional[PartnerTableWidget] = None  # built in _build_ui
        self._ui_busy = False
        self._flag_applied = False        # non-Windows fallback guard
        self._ga: Optional[GameAssets] = None   # gpak effect tables (async)
        self._assets_started = False
        self._asset_result: Optional[GameAssets] = None

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
        self._poll = QTimer(self)
        self._poll.setInterval(120)
        self._poll.timeout.connect(self._on_poll)
        self._poll.start()
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

        self._zoom_render(float(self._settings.get("zoom", 1.0) or 1.0))
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
            return

        def work():
            try:
                ga = GameAssets(path)
            except Exception:
                ga = None
            with self._asset_lock:
                self._asset_result = ga

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
                self._table._pair_malady_lines(row, stim, effect)),
            effect_of=self._effect_for_name,
            safe_risk_cap=lambda: self._to_float(
                self._settings.get("safe_risk_cap", 15.0), 15.0,
                floor=1.0, ceil=100.0),
            spacing=_ROOT_SPACING,
        )
        root.addWidget(self._best_bar)

        # partners
        self._table = PartnerTableWidget(self)
        self._table.set_header_tooltips(_COL_TIPS, _wt)
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

        # ⚙ Settings tab (save / appearance / zoom / help)
        from mewgenics_overlay.ui.settings_tab import SettingsTab
        self._settings_tab = SettingsTab(
            {
                "open_save": self._pick_save,
                "load_slot": self._on_load_slot,
                "set_theme": self.apply_theme,
                "zoom_in": self._zoom_inc,
                "zoom_out": self._zoom_dec,
                "zoom_reset": self._zoom_default,
                "about": self._show_about,
                "report": self._open_report,
                "set_check_updates": self._set_update_check,
            },
            titles={k: _theme.THEMES[k]["title"] for k in _theme.THEMES},
        )
        tabs.addTab(self._settings_tab, "⚙ Settings")
        # resize handle in the bottom-right corner (frameless window)
        size_row = QHBoxLayout()
        size_row.setContentsMargins(6, 0, 6, 4)
        size_row.addStretch(1)
        grip_w = QSizeGrip(self)
        grip_w.setFixedSize(22, 22)
        grip_w.setToolTip("Drag the corner to resize the window")
        size_row.addWidget(grip_w)
        outer.addLayout(size_row)

        self._refresh_save_slots()

        self._tabs = tabs

    def _wire_ui(self) -> None:
        self._table.itemSelectionChanged.connect(self._on_partner_selected)
        self._table.itemDoubleClicked.connect(self._on_partner_double)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_breeding_menu)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._btn_swap.toggled.connect(self._recompute_partners)
        # watcher thread -> UI thread (queued automatically by the signal)
        self._save_changed.connect(self._on_save_changed)

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

    def _zoom_step(self, delta: float) -> None:
        self._set_zoom(float(self._settings.get("zoom", 1.0) or 1.0) + delta)

    def _zoom_inc(self) -> None:
        self._zoom_step(0.25)

    def _zoom_dec(self) -> None:
        self._zoom_step(-0.25)

    def _zoom_default(self) -> None:
        self._set_zoom(1.0)

    def _cycle_zoom(self) -> None:
        cur = float(self._settings.get("zoom", 1.0) or 1.0)
        nxt = next((c for c in (1.0, 1.5, 2.0, 3.0) if c > cur + 0.001), 1.0)
        self._set_zoom(nxt)

    def _zoom_render(self, z: float) -> None:
        """Re-zoom every visual: app font (tables/labels), header buttons,
        column widths and the theme stylesheet font sizes."""
        _theme.set_zoom(z)
        app = QApplication.instance()
        base = getattr(self, "_zoom_base_font", None) or (app.font() if app else None)
        if app is not None and base is not None:
            nf = QFont(base)
            if base.pixelSize() > 0:
                nf.setPixelSize(max(6, int(round(base.pixelSize() * z))))
            else:
                nf.setPointSizeF(max(4.0, base.pointSizeF() * z))
            app.setFont(nf)
            for w in app.allWidgets():
                w.setFont(nf)
        self._chrome.scale_buttons(z)
        if self._table is not None:
            self._table.scale_columns(z)
        self.apply_theme(str(self._settings.get("theme", "noir")))
        self._chrome.restyle(z)
        if getattr(self, "_focus_panel", None) is not None:
            self._focus_panel.restyle(z)

    def _set_zoom(self, z: float) -> None:
        """Persist and apply a new zoom level."""
        z = round(min(4.0, max(0.75, float(z))), 2)
        self._settings["zoom"] = z
        cfg.save(self._settings)
        if getattr(self, "_settings_tab", None) is not None:
            self._settings_tab.set_zoom(int(round(z * 100)))
        self._zoom_render(z)

    def wheelEvent(self, event):  # noqa: N802 (Qt API)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self._zoom_step(0.25 if delta > 0 else -0.25)
            event.accept()
            return
        super().wheelEvent(event)

    def _toggle_theme(self) -> None:
        keys = list(_theme.THEMES)
        current = _theme.active_theme()
        nxt = keys[(keys.index(current) + 1) % len(keys)]
        self.apply_theme(nxt)

    def apply_theme(self, key: str) -> None:
        """Switch the active theme, restyle the app and re-render colours."""
        if key not in _theme.THEMES:
            return
        _theme.set_theme(key)
        self._settings["theme"] = key
        cfg.save(self._settings)
        if getattr(self, "_settings_tab", None) is not None:
            self._settings_tab.set_active_theme(key)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(_theme.stylesheet())
        # The palette carries its own stylesheet (it shadows the app-wide
        # one), so it must be refreshed too or nothing visually changes.
        self.setStyleSheet(_theme.stylesheet())
        from mewgenics_overlay.ui.comboarrow import style_combo
        room_bar = getattr(self, "_room_bar", None)
        if room_bar is not None:
            room_bar.restyle()
        donations = getattr(self, "_donations_tab", None)
        npc_combo = getattr(donations, "_combo", None)
        if npc_combo is not None:
            style_combo(npc_combo, _theme.C_MUTED)
        self._refresh_theme()

    def _refresh_theme(self) -> None:
        """Repaint everything that cached theme colours."""
        if self._rows:
            self._redraw_table()
        if self._focus is not None:
            self._show_focus(self._focus)
        donations = getattr(self, "_donations_tab", None)
        if donations is not None:
            donations.refresh(self._session)

    # ── pinning (keep-list) ────────────────────────────────────────────────
    def _pinned_store(self) -> list:
        store = self._settings.setdefault("pinned", {})
        key = self._settings.get("save_path") or ""
        return store.setdefault(key, [])

    def _sync_pins(self) -> None:
        """Apply saved pins and forget any cat that is gone from the save."""
        if self._session is None:
            return
        store = self._pinned_store()
        present = {c.unique_id for c in self._session.cats
                   if getattr(c, "status", "") != "Gone"}
        fresh = [uid for uid in store if uid in present]
        if len(fresh) != len(store):
            store[:] = fresh
            cfg.save(self._settings)
        for c in self._session.cats:
            c.is_pinned = c.unique_id in store

    def set_pinned(self, cat, on: bool) -> None:
        """Pin/unpin a cat (kept as a breeder; Gone cats are pruned)."""
        store = self._pinned_store()
        uid = cat.unique_id
        if on and uid not in store:
            store.append(uid)
        elif not on and uid in store:
            store.remove(uid)
        cfg.save(self._settings)
        cat.is_pinned = on
        self._refresh_theme()

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

    def _show_about(self) -> None:
        """Credits dialog: who built it and whose research it stands on."""
        text = (
            f"<h3>Mewgenics Breeding Overlay</h3>"
            f"<p>Version {__version__}</p>"
            f"<p>Built by <b>TheBeardBE</b>, with help from an LLM "
            f"through <b>pi.dev</b>.</p>"
            f"<p><b>Credits</b></p>"
            f"<ul>"
            f"<li>Save parser &amp; genetics engine: "
            f"<a href='https://github.com/frankieg33/MewgenicsBreedingManager'>"
            f"MewgenicsBreedingManager</a> (MIT, © 2026 frankieg33) - "
            f"vendored; provenance in <code>vendor/_VENDORED.md</code></li>"
            f"<li>1.1 breeding-model sync: "
            f"<a href='https://github.com/whyayala/MewgenicsBreedingManager'>"
            f"whyayala's maintained fork</a> (v5.9.5) - same-sex rule, "
            f"gender-role compat gate, neutral-sexuality fix</li>"
            f"<li>Save-format research: "
            f"<a href='https://github.com/pzx521521/mewgenics-save-editor'>"
            f"pzx521521/mewgenics-save-editor</a> and the community</li>"
            f"<li>Breeding formulas (datamined from the game): "
            f"<a href='https://mewgenicswiki.org/tools/breeding-calculator'>"
            f"Mewgenics breeding calculator</a> (mewgenicswiki.org) + "
            f"<a href='https://gist.github.com/SciresM/95a9dbba22937420e75d4da617af1397'>"
            f"SciresM's game-code analysis</a>, cross-checked against "
            f"<a href='https://mewgenics.wiki.gg/wiki/Breeding'>wiki.gg's "
            f"datamined tables</a> - pinned by tests/test_wiki_math.py</li>"
            f"<li>Game mechanics reference: "
            f"<a href='https://mewgenics.wiki.gg/wiki/Mewgenics'>"
            f"Mewgenics Wiki</a></li>"
            f"</ul>"
            f"<p>Licensed MIT. Saves are read-only - this tool never "
            f"modifies them.</p>"
            f"<p>Update check: on start the app asks GitHub for the newest "
            f"release and shows a download button if one exists - no data is "
            f"sent. Disable it in Settings.</p>"
            f"<p>Found a problem? Use <b>🐞 Report a problem</b> below - "
            f"no account needed.</p>"
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("About")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setOpenExternalLinks(True)
        layout.addWidget(label)
        ok = QPushButton("OK")
        ok.clicked.connect(dialog.accept)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        report = QPushButton("🐞 Report a problem")
        report.setToolTip("Open the bug-report form in your browser - no account needed.")
        report.clicked.connect(self._open_report)
        copy_info = QPushButton("📋 Copy debug info")
        copy_info.setToolTip("Copies version + save + theme to the clipboard so a "
                             "bug report needs no file hunting. Paste it into the form.")
        copy_info.clicked.connect(self._copy_debug)
        row.addWidget(report)
        row.addWidget(copy_info)
        row.addStretch()
        layout.addWidget(actions)
        layout.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)
        dialog.resize(560, 480)
        dialog.exec()

    def _report_url(self) -> str:
        """Where the About-box report button points (see config.report_url)."""
        url = (self._settings.get("report_url")
               or "https://github.com/thebeardbe/mewgenics-breeding-overlay/issues")
        # Only ever hand an http(s) URL to the OS browser - never a custom
        # scheme from a config file (file:, or registered protocol handlers).
        if isinstance(url, str) and url.lower().startswith(("http://",
                                                           "https://")):
            return url
        return "https://github.com/thebeardbe/mewgenics-breeding-overlay/issues"

    def _open_report(self) -> None:
        import webbrowser
        webbrowser.open(self._report_url())

    def _copy_debug(self) -> None:
        """Copy a compact debug block to the clipboard for bug reports."""
        import platform
        try:
            qt_ver = __import__("PySide6").__version__
        except Exception:
            qt_ver = "?"
        try:
            py_ver = platform.python_version()
        except Exception:
            py_ver = "?"
        save = self._settings.get("save_path") or ""
        text = "\n".join([
            f"Mewgenics Breeding Overlay v{__version__}",
            f"OS: {platform.system()} {platform.release()}",
            f"Python: {py_ver} · Qt: {qt_ver}",
            f"Theme: {self._settings.get('theme')}",
            f"Save: {save or '(none loaded)'}",
        ])
        QApplication.clipboard().setText(text)
        self._set_status("debug info copied - paste it into a bug report")

    # ── save loading ───────────────────────────────────────────────────────
    def _load_last_save(self) -> None:
        path = self._settings.get("save_path")
        if not path or not self._file_exists(path):
            from mewgenics_overlay.core.discovery import newest_save
            found = newest_save()
            path = found["path"] if found else None
        if path:
            self.open_save(path)
        else:
            self._set_status("no save found - use 📁 to locate one")

    @staticmethod
    def _file_exists(path: str) -> bool:
        import os
        return bool(path) and os.path.exists(path)

    def _discover_slot_paths(self) -> list:
        """steamcampaign01..03 saves under the discovered saves folder."""
        try:
            from mewgenics_overlay.core.discovery import find_all_saves
            records = find_all_saves()
        except Exception:
            records = []
        by_num = {}
        for r in records:
            raw = str(r.get("path", "") or "")
            base = raw.split("/")[-1].split(chr(92))[-1]
            if base.startswith("steamcampaign") and base.endswith(".sav"):
                try:
                    n = int(base[len("steamcampaign"):-4])
                except ValueError:
                    continue
                by_num[n] = raw
        return [by_num.get(n) for n in (1, 2, 3)]

    def _refresh_save_slots(self) -> None:
        paths = self._discover_slot_paths()
        self._slot_paths = paths
        if getattr(self, "_settings_tab", None) is not None:
            self._settings_tab.set_slots(
                [(f"Slot {i + 1}", p) for i, p in enumerate(paths)])
            self._settings_tab.set_current(self._settings.get("save_path"))

    def _on_load_slot(self, index: int) -> None:
        paths = getattr(self, "_slot_paths", None)
        if not paths:
            return
        if 0 <= index < len(paths) and paths[index]:
            self.open_save(paths[index])

    def _pick_save(self) -> None:
        """Open a file picker and load the chosen save.

        Uses Qt's own dialog (not the OS-native one) - the native dialog is
        the usual suspect for platform crashes here. The dialog is modal, so
        auto click-through is suspended while it is open.
        """
        self._dialog_open = True
        try:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Locate Mewgenics save",
                os.path.dirname(self._settings.get("save_path") or ""),
                "Mewgenics saves (*.sav)",
                options=QFileDialog.Option.DontUseNativeDialog,
            )
        finally:
            self._dialog_open = False
        if path:
            self.open_save(path)

    def open_save(self, path: str) -> None:
        """(Re)load a save file; safe to call repeatedly / from any thread."""
        # watch on the *copied* path isn't needed: watch the real file.
        self._settings["save_path"] = path
        cfg.save(self._settings)
        base = path.split('/')[-1].split(chr(92))[-1]
        self._chrome.set_title(f"🐈 Overlay - {base}")
        if getattr(self, "_settings_tab", None) is not None:
            self._settings_tab.set_current(path)
        self._start_watcher(path)
        self._set_status("loading save…")
        self._schedule_reload()

    def _start_watcher(self, path: str) -> None:
        self._save.start_watcher(path, on_change=self._save_changed.emit)

    def _on_save_changed(self) -> None:
        """Run on the UI thread via the ``_save_changed`` signal - the watcher
        thread only emits, it never touches Qt widgets."""
        try:
            self._set_status("save changed - reloading…")
            self._schedule_reload()
        except Exception:
            log.exception("save-change handler failed")
            self._set_status("⚠ save changed but reload failed - see the log")

    def _set_status(self, text: str) -> None:
        self._chrome.set_status(text)

    def shutdown(self) -> None:
        """Stop background threads before the app exits."""
        self._poll.stop()
        self._save.stop_watcher()

    # ── background work ────────────────────────────────────────────────────
    def _schedule_reload(self) -> None:
        path = self._settings.get("save_path")
        if path:
            self._save.schedule_reload(path)

    def _schedule_partners(self) -> None:
        focus = self._focus
        if self._save.session is None or focus is None:
            return
        self._save.schedule_partners(
            focus.db_key,
            int(self._settings.get("max_partners", 100)),
            0 if self._btn_swap.isChecked() else None,
            bool(self._settings.get("include_adventure", True)),
            str(self._settings.get("order", "risk")),
            self._stim_value(),
        )

    def _on_poll(self) -> None:
        """Drain completed background jobs on the UI thread (token-guarded)."""
        items = self._save.drain()
        with self._asset_lock:
            assets = self._asset_result
            self._asset_result = None
        if assets is not None:
            self._ga = assets if assets.ok else None
            self._room_bar.refresh()          # room Stimulation now available
            if self._rows:
                self._redraw_table()          # effects now available in tooltips
            if self._focus is not None:
                self._show_focus(self._focus)  # refresh health/effect tooltip
        token_now = self._save.token
        for token, kind, result in items:
            if kind == "session":
                if token >= token_now:
                    self._adopt_session(result)
            elif kind == "session_error":
                if token >= token_now:
                    # Keep the previous roster; never leave the UI hanging on
                    # a corrupt/hostile save.
                    self._set_status("⚠ could not read save - keeping the "
                                     "previous cats")
                    log.warning("save reload failed: %s", result)
            elif kind == "partners":
                cat_key, rows = result
                if token >= token_now and self._focus is not None \
                        and self._focus.db_key == cat_key:
                    self._render_partners(rows)
            elif kind == "partners_error":
                # raw exception already logged in the worker; show a
                # friendly line, never a Python traceback in the UI.
                self._set_status("⚠ partner scoring failed - see the log; "
                                 "try selecting another cat")

    def _adopt_session(self, sess: Optional[Session]) -> None:
        self._session = sess
        count = len(sess.alive) if sess else 0
        self._set_status(f"{count} cats in house/on adventures" if sess else "no save loaded")
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
        item = self._table.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        row, kids = data
        rel = row.relation
        head = (f"{row.partner.name}: {rel.label} · Δgen {rel.gen_gap:+d}"
                f" · COI {row.coi * 100:.1f}%")
        if row.compatible:
            proj = row.pair_factors.projection
            text = (
                head + "\n"
                + "Kitten stats per parent range: "
                + "  ".join(
                    f"{s} {proj.stat_ranges[s][0]}–{proj.stat_ranges[s][1]}"
                    for s in STAT_NAMES
                )
            )
            better = _better_stat_expectation(row, self._stim_value())
            if better:
                text += (f"\nThe kitten takes the higher of the two parents' "
                         f"values in ≈{better[0]:.1f} of "
                         f"{better[1]} differing stats")
            if kids:
                text += f"   ·   existing kittens: {', '.join(kids)}"
        else:
            text = head + f"\nCan't breed: {row.reason or 'blocked'}"
        malady = self._table._pair_malady_lines(
            row, self._stim_value(), self._effect_for_name)
        if malady:
            text += "\n" + "\n".join(malady)
        self._detail.setText(text)

    def _show_breeding_menu(self, pos) -> None:
        """Right-click a partner row to pin/unpin them as a keeper."""
        item = self._table.itemAt(pos)
        if item is None:
            return
        name_item = self._table.item(item.row(), 0)
        data = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
        if not data:
            return
        partner = data[0].partner
        menu = QMenu(self)
        label = ("Unpin - allow donation" if getattr(partner, "is_pinned", False)
                 else "Pin for breeding")
        action = menu.addAction(label)
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is action:
            self.set_pinned(partner, not getattr(partner, "is_pinned", False))

    def _on_partner_double(self, item: QTableWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            row, _ = data
            self.set_focus(row.partner)
