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
import sys
import threading
from typing import Optional

from PySide6.QtCore import Qt, QEvent, QRect, QTimer, Signal
import mewgenics_overlay.ui.theme as _theme
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
    display_location,
)
from mewgenics_overlay.ui.savecontroller import SaveController
from mewgenics_overlay.ui.focuspanel import FocusedCatPanel

# Partner-table pure core (columns, header tips, cell formatters) — moved to
# ui/partnertable.py so the interactive widget can stay Qt-focused (step 2b).
from .partnertable import (
    _COL_TIPS,
    _better_stat_expectation,
    _night_chance,
    PartnerTableWidget,
)

from . import config as cfg
from .theme import wrap_tooltip as _wt
from mewgenics_overlay import __version__
from mewgenics_overlay.core.gameassets import GameAssets, locate_gpak
from mewgenics_overlay.core.stimulation import (
    STIMULATION_DEFAULT,
    room_env_map,
)
from mewgenics_overlay.core.recommend import recommend as recommend_best

log = logging.getLogger("mewgenics_overlay.ui")

class _DragLabel(QLabel):
    """Header grip that starts an OS window move on left-drag."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            wh = self.window().windowHandle()
            if wh is not None and wh.startSystemMove():
                self._dragging = False
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)


class PaletteWindow(QWidget):
    """The overlay palette. Owns the save session, watcher and worker."""

    # The save watcher fires from its own thread; a signal is the Qt-safe way
    # to hand that notification back to the UI thread (connected in _wire_ui).
    _save_changed = Signal()

    def __init__(self):
        super().__init__()
        self._settings = cfg.load()
        self._focus: Optional[Cat] = None
        self._save = SaveController()   # session state + background queue + watcher
        self._asset_lock = threading.Lock()
        self._table: Optional[PartnerTableWidget] = None  # built in _build_ui
        self._ui_busy = False
        self._pinned = True               # mirror of the 📌 button state
        self._click_through = False       # mouse passes through to the game
        self._dialog_open = False         # modal dialog (file picker) open
        self._flag_applied = False        # non-Windows fallback guard
        self._ga: Optional[GameAssets] = None   # gpak effect tables (async)
        self._assets_started = False
        self._asset_result: Optional[GameAssets] = None
        self._stim = STIMULATION_DEFAULT     # active breeding Stimulation
        self._comfort = 0.0                  # active room Comfort (roll chance)
        self._room_items: list = []          # combo entries (room, stim, comf)

        self.setWindowTitle("Mewgenics Breeding Overlay")
        flags = Qt.WindowType.FramelessWindowHint
        # Pinning is done natively on Windows (SetWindowPos) and via compositor
        # rules on Hyprland; only generic X11/Wayland keep the Qt flag, which
        # re-creates the native window when toggled (Windows hides it -> the
        # "can't find it anymore" bug).
        if sys.platform != "win32" and not os.environ.get(
                "HYPRLAND_INSTANCE_SIGNATURE"):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(880, 600)
        self._restore_geometry()
        self.setStyleSheet(_theme.stylesheet())
        self._build_ui()
        self._wire_ui()
        self._poll = QTimer(self)
        self._poll.setInterval(120)
        self._poll.timeout.connect(self._on_poll)
        self._poll.start()

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
        root.setSpacing(6)
        tabs.addTab(self._page_main, "Breeding")

        # header
        head = QHBoxLayout()
        grip = _DragLabel("⠿")
        grip.setStyleSheet(f"color:{_theme.C_GRIP}; font-size:13px;")
        grip.setToolTip("Drag to move the overlay")
        self._title = _DragLabel("🐈 Breeding Overlay")
        self._title.setStyleSheet("font-weight:700; font-size:14px;")
        self._title.setCursor(Qt.CursorShape.OpenHandCursor)
        self._status = QLabel("")
        self._status.setObjectName("muted")
        self._status.setStyleSheet(f"color:{_theme.C_STATUS}; font-size:11px;")
        pin = QPushButton("📌")
        pin.setCheckable(True)
        pin.setChecked(self._pinned)
        pin.setFixedWidth(34)
        pin.setToolTip("Keep above the game (native pin on Windows, "
                       "Hyprland rules on Linux)")
        pin.clicked.connect(self._toggle_pin)
        self._btn_pin = pin
        ct = QPushButton("🧿")
        ct.setCheckable(True)
        ct.setChecked(self._click_through)
        ct.setFixedWidth(34)
        ct.setToolTip("Click-through: let mouse clicks reach Mewgenics. "
                      "Press Ctrl+Shift+B (Windows) / tray to interact again.")
        ct.clicked.connect(self._on_ct_clicked)
        self._btn_ct = ct
        open_save = QPushButton("📁")
        open_save.setFixedWidth(34)
        open_save.setToolTip("Choose a different save file")
        open_save.clicked.connect(self._pick_save)
        about = QPushButton("ℹ️")
        about.setFixedWidth(34)
        about.setToolTip("About — version and credits")
        about.clicked.connect(self._show_about)
        self._btn_theme = QPushButton("◐")
        self._btn_theme.setFixedWidth(34)
        self._btn_theme.setToolTip("Switch theme (Bleached Film ↔ Noir Ink)")
        self._btn_theme.clicked.connect(self._toggle_theme)
        close = QPushButton("✕")
        close.setFixedWidth(34)
        close.setToolTip("Hide (Ctrl+Shift+B / tray) — quits when no tray is available")
        close.clicked.connect(self._on_close_clicked)
        # header emoji buttons: bigger glyphs, uniform width
        for _b in (pin, ct, open_save, about, self._btn_theme, close):
            _b.setObjectName("iconbtn")
            _b.setFixedWidth(40)
        head.addWidget(grip)
        head.addWidget(self._title)
        head.addWidget(self._status, 1)
        head.addWidget(pin)
        head.addWidget(ct)
        head.addWidget(open_save)
        head.addWidget(about)
        head.addWidget(self._btn_theme)
        head.addWidget(close)
        # header lives ABOVE the tabs (insertLayout(0)) so the chrome stays
        # visible on every tab — it used to live inside the Breeding page
        # and vanished on Donations.
        outer.insertLayout(0, head)

        # search
        self._search = QLineEdit()
        self._search.setPlaceholderText("Click a cat in-game, then type its name here…")
        self._search.setClearButtonEnabled(True)
        self._search.setToolTip(_wt(
            "Find a cat by typing part of its name — the list comes from "
            "your latest save and refreshes on its own whenever the game "
            "saves.\n"
            "Then pick who to analyse for breeding."
        ))
        self._results = QListWidget()
        self._results.setVisible(False)
        self._results.setMaximumHeight(170)
        root.addWidget(self._search)
        root.addWidget(self._results)

        # focused cat summary
        self._focus_panel = FocusedCatPanel()
        root.addWidget(self._focus_panel)
        row2 = QHBoxLayout()
        room_lbl = QLabel("Breed room:")
        room_lbl.setToolTip(_wt(
            "The room where you plan to breed.\n"
            "Its furniture changes two things in the numbers:\n"
            "• Stimulation — decides how often kittens inherit the better "
            "stat, and how likely a lone defect is to pass.\n"
            "• Comfort — decides how often a breeding attempt actually "
            "succeeds each night.\n"
            "Until a room is chosen, a neutral Stimulation of 50 is assumed."
        ))
        self._room_combo = QComboBox()
        self._room_combo.setToolTip(room_lbl.toolTip())
        self._room_combo.setMinimumWidth(170)
        self._room_combo.setEnabled(False)
        self._room_combo.currentIndexChanged.connect(self._on_room_changed)
        self._btn_swap = QPushButton("Hide blocked rows")
        self._btn_swap.setCheckable(True)
        self._btn_swap.setToolTip(_wt(
            "When checked, pairs that cannot breed (direct family, hater, "
            "sexuality blocks) are hidden instead of listed below."
        ))
        row2.addWidget(room_lbl)
        row2.addWidget(self._room_combo)
        row2.addStretch(1)
        row2.addWidget(self._btn_swap)
        _row_holder = QWidget()
        _row_holder.setLayout(row2)
        self._focus_panel.append_row(_row_holder)


        # best-match banner (+ safe-mode switch)
        best_row = QHBoxLayout()
        self._btn_best = QPushButton("⭐ Best match")
        self._btn_best.setObjectName("best")
        self._btn_best.setToolTip("")
        self._btn_best.setVisible(False)
        self._best_row = None
        self._safe_mode = False
        self._btn_safe = QPushButton("🛡 Safe ≤ 15% risk")
        self._btn_safe.setCheckable(True)
        self._btn_safe.setToolTip(_wt(
            "Limit the ⭐ Best match to partners that are low risk (15% or "
            "less), so you only breed pairs that are unlikely to produce a "
            "defective kitten.\n"
            "If no partner is that safe, the normal 7s-first pick is shown "
            "instead — clearly labelled."
        ))
        self._btn_safe.setVisible(False)
        best_row.addWidget(self._btn_best, 1)
        best_row.addWidget(self._btn_safe)
        root.addLayout(best_row)

        # partners
        self._table = PartnerTableWidget(self)
        self._table.set_header_tooltips(_COL_TIPS, _wt)
        root.addWidget(self._table, 1)

        # detail strip
        self._detail = QLabel("Select a partner row for inheritance detail.")
        self._detail.setWordWrap(True)
        self._detail.setObjectName("muted")
        # can embed partner names (save-derived) — never auto-rich-text them
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
        self._donations_tab = DonationsTab(palette=self)
        tabs.addTab(self._donations_tab, "Donations")
        self._tabs = tabs

    def _wire_ui(self) -> None:
        self._search.textChanged.connect(self._on_search_text)
        self._search.returnPressed.connect(self._on_search_enter)
        self._results.itemClicked.connect(self._on_result_clicked)
        self._results.itemActivated.connect(self._on_result_clicked)
        self._table.itemSelectionChanged.connect(self._on_partner_selected)
        self._table.itemDoubleClicked.connect(self._on_partner_double)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_breeding_menu)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._btn_swap.toggled.connect(self._recompute_partners)
        self._btn_best.clicked.connect(self._on_best_clicked)
        self._btn_safe.toggled.connect(self._on_safe_toggled)
        # watcher thread -> UI thread (queued automatically by the signal)
        self._save_changed.connect(self._on_save_changed)

    # ── window behaviour: pin, click-through, summoning ────────────────────
    def _toggle_pin(self, checked: bool) -> None:
        """Pin toggle. Never re-creates the native window on Windows."""
        self._pinned = bool(checked)
        if sys.platform == "win32":
            self._set_topmost_win32(self._pinned)
        elif not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            # generic X11/Wayland: Qt flag fallback (may flash once)
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,
                               self._pinned)
            self.show()
        # Hyprland: stacking is controlled by compositor rules — visual only.

    def _set_topmost_win32(self, on: bool) -> None:
        """Set/unset always-on-top without touching window flags (no HWND
        re-creation -> the overlay can't get 'lost')."""
        try:
            import ctypes
            hwnd = int(self.winId())
            HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            ctypes.windll.user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST if on else HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            pass

    def _on_ct_clicked(self, checked: bool) -> None:
        self.set_click_through(checked)

    def set_click_through(self, on: bool) -> None:
        """When ON, mouse events pass through to the game underneath."""
        self._click_through = bool(on)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                          self._click_through)
        btn = getattr(self, "_btn_ct", None)
        if btn is not None:
            btn.blockSignals(True)
            btn.setChecked(self._click_through)
            btn.blockSignals(False)

    def _engage(self) -> None:
        """Show the palette and make it interactive (hotkey/tray summon)."""
        self.set_click_through(False)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def toggle_activate(self) -> None:
        """Hotkey/tray cycle: hidden -> engage; passive -> engage; active -> hide."""
        if not self.isVisible():
            self._engage()
        elif self._click_through:
            self._engage()
        else:
            self.hide()

    def showEvent(self, event):  # noqa: N802 (Qt API)
        super().showEvent(event)
        if sys.platform == "win32":
            self._set_topmost_win32(self._pinned)

    def hideEvent(self, event):  # noqa: N802 (Qt API)
        self._save_geometry()
        super().hideEvent(event)

    def changeEvent(self, event):  # noqa: N802 (Qt API)
        """The moment the palette loses focus (user clicks the game), stop
        intercepting mouse input: switch to click-through automatically so the
        game always receives clicks in this area. Summon it again with
        Ctrl+Shift+B / tray to interact. Skipped while a modal dialog is open."""
        if (event.type() == QEvent.Type.WindowDeactivate
                and not self._dialog_open
                and self.isVisible() and not self._click_through):
            self.set_click_through(True)
        super().changeEvent(event)

    def _restore_geometry(self) -> None:
        """Restore the last window rect, clamped to a visible screen."""
        rect = self._settings.get("window_rect")
        if not (isinstance(rect, list) and len(rect) == 4):
            return
        r = QRect(*rect)
        screens = QGuiApplication.screens()
        if any(r.intersects(s.availableGeometry()) for s in screens):
            self.setGeometry(r)

    def _save_geometry(self) -> None:
        g = self.geometry()
        self._settings["window_rect"] = [g.x(), g.y(), g.width(), g.height()]
        cfg.save(self._settings)

    def _on_close_clicked(self) -> None:
        """Hide when a tray icon can bring us back; otherwise quit."""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        else:
            self.shutdown()
            QApplication.instance().quit()

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
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(_theme.stylesheet())
        # The palette carries its own stylesheet (it shadows the app-wide
        # one), so it must be refreshed too or nothing visually changes.
        self.setStyleSheet(_theme.stylesheet())
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
            f"MewgenicsBreedingManager</a> (MIT, © 2026 frankieg33) — "
            f"vendored; provenance in <code>vendor/_VENDORED.md</code></li>"
            f"<li>1.1 breeding-model sync: "
            f"<a href='https://github.com/whyayala/MewgenicsBreedingManager'>"
            f"whyayala's maintained fork</a> (v5.9.5) — same-sex rule, "
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
            f"datamined tables</a> — pinned by tests/test_wiki_math.py</li>"
            f"<li>Game mechanics reference: "
            f"<a href='https://mewgenics.wiki.gg/wiki/Mewgenics'>"
            f"Mewgenics Wiki</a></li>"
            f"</ul>"
            f"<p>Licensed MIT. Saves are read-only — this tool never "
            f"modifies them.</p>"
            f"<p>Found a problem? Use <b>🐞 Report a problem</b> below — "
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
        report.setToolTip("Open the bug-report form in your browser — no account needed.")
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
        # Only ever hand an http(s) URL to the OS browser — never a custom
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
        self._set_status("debug info copied — paste it into a bug report")

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
            self._set_status("no save found — use 📁 to locate one")

    @staticmethod
    def _file_exists(path: str) -> bool:
        import os
        return bool(path) and os.path.exists(path)

    def _pick_save(self) -> None:
        """Open a file picker and load the chosen save.

        Uses Qt's own dialog (not the OS-native one) — the native dialog is
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
        self._title.setText(f"🐈 Overlay — {path.split('/')[-1].split(chr(92))[-1]}")
        self._start_watcher(path)
        self._set_status("loading save…")
        self._schedule_reload()

    def _start_watcher(self, path: str) -> None:
        self._save.start_watcher(path, on_change=self._save_changed.emit)

    def _on_save_changed(self) -> None:
        """Run on the UI thread via the ``_save_changed`` signal — the watcher
        thread only emits, it never touches Qt widgets."""
        try:
            self._set_status("save changed — reloading…")
            self._schedule_reload()
        except Exception:
            log.exception("save-change handler failed")
            self._set_status("⚠ save changed but reload failed — see the log")

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

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
            self._refresh_room_combo()        # room Stimulation now available
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
                    self._set_status("⚠ could not read save — keeping the "
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
                self._set_status("⚠ partner scoring failed — see the log; "
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
        self._refresh_room_combo()
        self._sync_pins()
        self._donations_tab.refresh(self._session)

    # ── breeding-room Stimulation ──────────────────────────────────────────
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
        return self._to_float(self._stim, STIMULATION_DEFAULT)

    def _comfort_value(self) -> float:
        return self._to_float(self._comfort, 0.0, floor=0.0)

    def _selected_room(self):
        idx = self._room_combo.currentIndex()
        if 0 <= idx < len(self._room_items):
            entry = self._room_items[idx]
            return entry[0] if entry else None
        return None

    def _refresh_room_combo(self) -> None:
        """Rebuild the room list from furniture (needs resources.gpak defs)."""
        prev_room = self._selected_room()
        rooms_env: dict = {}
        if self._session is not None and self._session.data is not None \
                and self._ga is not None:
            fb = self._session.data.furniture_by_room or {}
            if fb and self._ga.furniture_data:
                rooms_env = room_env_map(fb, self._ga.furniture_data)
        self._room_combo.blockSignals(True)
        self._room_combo.clear()
        self._room_items = []
        self._room_combo.addItem("— Stim 50 (no room)")
        self._room_items.append(None)
        for room in sorted(rooms_env, key=lambda r: -rooms_env[r][0]):
            stim, comfort = float(rooms_env[room][0]), float(rooms_env[room][1])
            label = f"{room} — Stim {stim:g}"
            if comfort:
                label += f", Comf {comfort:g}"
            self._room_combo.addItem(label)
            self._room_items.append((room, stim, comfort))
        self._room_combo.setEnabled(bool(rooms_env))
        # prefer the previous pick, else the focused cat's room
        target = None
        if prev_room is not None and prev_room in rooms_env:
            target = prev_room
        elif self._focus is not None and self._focus.room in rooms_env:
            target = self._focus.room
        idx = 0
        for i, entry in enumerate(self._room_items):
            if entry is not None and entry[0] == target:
                idx = i
                break
        old_stim = self._stim_value()
        old_comf = self._comfort_value()
        self._room_combo.setCurrentIndex(idx)
        self._room_combo.blockSignals(False)
        self._apply_room_selection()
        if self._focus is not None and (self._stim_value() != old_stim
                                        or self._comfort_value() != old_comf):
            self._schedule_partners()   # numbers change with Stim/Comfort

    def _on_room_changed(self, index: int) -> None:
        self._apply_room_selection()
        if self._focus is not None:
            self._schedule_partners()

    def _apply_room_selection(self) -> None:
        entry = None
        idx = self._room_combo.currentIndex()
        if 0 <= idx < len(self._room_items):
            entry = self._room_items[idx]
        if entry is None:
            self._stim = STIMULATION_DEFAULT
            self._comfort = 0.0
        else:
            self._stim = float(entry[1])
            self._comfort = float(entry[2])

    def set_focus_key(self, db_key: int) -> None:
        """Programmatic focus (used by the future in-game bridge)."""
        if self._session is None:
            return
        cat = self._session.by_key.get(db_key)
        if cat is not None:
            self.set_focus(cat)

    def set_focus(self, cat: Cat) -> None:
        self._focus = cat
        self._search.setText("")
        self._search.clearFocus()
        self._show_focus(cat)
        self._schedule_partners()

    def _clear_focus(self) -> None:
        self._focus = None
        self._focus_panel.clear()
        self._best_row = None
        self._btn_best.setVisible(False)
        self._btn_safe.setVisible(False)
        self._table.setRowCount(0)
        self._detail.setText("Select a partner row for inheritance detail.")

    def _show_focus(self, cat: Cat) -> None:
        def _gpak_effect(group_key, mutation_id):
            return self._ga.effect_for(group_key, mutation_id) \
                if self._ga is not None else ""
        self._focus_panel.show_cat(cat, effect_for=_gpak_effect)

    def _on_search_text(self, text: str) -> None:
        if not text.strip():
            self._results.setVisible(False)
            return
        if self._session is None:
            return
        hits = self._session.search(text, limit=100)
        truncated = len(hits) > 60
        if truncated:
            hits = hits[:60]
        self._results.clear()
        for c in hits:
            item = QListWidgetItem(
                f"{c.name}   · {display_location(c)}   · {c.gender}   · "
                f"sum {sum(c.base_stats.values())}"
            )
            item.setData(Qt.ItemDataRole.UserRole, c.db_key)
            self._results.addItem(item)
        if truncated:
            more = QListWidgetItem("… more matches — type more of the name")
            more.setFlags(Qt.ItemFlag.NoItemFlags)
            self._results.addItem(more)
        self._results.setVisible(True)

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if self._session is not None:
            cat = self._session.by_key.get(key)
            if cat is not None:
                self.set_focus(cat)
        self._results.setVisible(False)

    def _on_search_enter(self) -> None:
        if self._results.count():
            self._on_result_clicked(self._results.item(0))

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

    # ── sorting ────────────────────────────────────────────────────────────
    def _on_safe_toggled(self, checked: bool) -> None:
        self._safe_mode = bool(checked)
        self._update_best()

    def _update_best(self) -> None:
        """Recompute and show the ⭐ best-match banner for the focused cat."""
        if not self._rows or self._focus is None:
            self._best_row = None
            self._btn_best.setVisible(False)
            self._btn_safe.setVisible(False)
            return
        compat = [r for r, _ in self._rows if r.compatible]
        if not compat:
            self._best_row = None
            self._btn_best.setVisible(False)
            self._btn_safe.setVisible(False)
            return
        effect = self._effect_for_name
        overall = recommend_best(compat, self._focus, effect_of=effect,
                                 stimulation=self._stim_value(),
                                 comfort=self._comfort_value())
        chosen = overall
        fallback = False
        if self._safe_mode:
            cap = self._to_float(self._settings.get("safe_risk_cap", 15.0),
                                 15.0, floor=1.0, ceil=100.0)
            safe_rows = [r for r in compat if r.risk_pct <= cap]
            safe_rec = (recommend_best(safe_rows, self._focus,
                                       effect_of=effect,
                                       stimulation=self._stim_value(),
                                       comfort=self._comfort_value())
                        if safe_rows else recommend_best([], self._focus))
            if safe_rec.row is not None:
                chosen = safe_rec
            elif overall.row is not None:
                chosen = overall            # fall back to the 7s-first pick
                fallback = True
        self._best_row = chosen.row
        self._btn_safe.setVisible(True)
        if chosen.row is None:
            self._btn_best.setVisible(False)
            return
        partner = chosen.row.partner
        prefix = "⭐ Best match"
        if self._safe_mode and not fallback:
            prefix = "🛡 Safe best"
        elif fallback:
            prefix = "⭐ Best (no ≤15% risk partner)"
        text = (
            f"{prefix}: {partner.name} — Risk {chosen.row.risk_pct:.1f}% · "
            f"≥7 ≈{chosen.row.seven_plus_total:.1f} · "
            f"COI {chosen.row.coi * 100:.1f}%"
        )
        if chosen.row.risk_pct > 35:
            text += "   ⚠ high risk"
        if _night_chance(chosen.row.game_compat,
                         self._comfort_value()) < 0.10:
            text += "   ⚠ breeds rarely"
        self._btn_best.setText(text)
        tool = "Why this pick:\n" + "\n".join(chosen.breakdown)
        malady = self._table._pair_malady_lines(
            chosen.row, self._stim_value(), self._effect_for_name)
        if malady:
            tool += "\n\n" + "\n".join(malady)
        tool += "\n\nClick to select this partner."
        self._btn_best.setToolTip(_wt(tool))
        self._btn_best.setVisible(True)

    def _on_best_clicked(self) -> None:
        if self._best_row is None:
            return
        target = self._best_row.partner.db_key
        for ri in range(self._table.rowCount()):
            it = self._table.item(ri, 0)
            data = it.data(Qt.ItemDataRole.UserRole) if it else None
            if data and data[0].partner.db_key == target:
                self._table.setCurrentCell(ri, 0)
                self._table.scrollToItem(it)
                self._on_partner_selected()
                return

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
        label = ("Unpin — allow donation" if getattr(partner, "is_pinned", False)
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
