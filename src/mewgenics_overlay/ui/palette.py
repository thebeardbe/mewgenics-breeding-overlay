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

from PySide6.QtCore import Qt, QRect, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.core.session import Cat, PartnerRow, Session
from mewgenics_overlay.core.watcher import SaveWatcher, safe_read_save
from mewgenics_overlay.vendor.breeding import tracked_offspring

from . import config as cfg
from .theme import STYLESHEET, gender_badge, risk_color

log = logging.getLogger("mewgenics_overlay.ui")

STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]

_COLS = ["Cat", "Family", "GenΔ", "Room", "Risk", "Compat", "Exp/stat", "≥7", "Note"]

# Column explanations shown as tooltips when hovering each header.
_COL_TIPS = [
    # Cat
    "Candidate partner. Rows marked ✗ cannot breed with the selected cat "
    "(the reason is in the Note column). Double-click a name to analyse "
    "breeding from that cat instead. Click a header to sort; click again "
    "to reverse; a third click returns to the default safe-first order.",
    # Family
    "Family link: how the partner is related to the focused cat, traced "
    "through shared ancestry up to 9 generations — parent/child, "
    "grandparent/grandchild, full/half sibling, aunt/uncle ↔ niece/nephew, "
    "cousins ('once removed' when generations differ), or 'unrelated'.\n\n"
    "The tooltip on each cell adds this pair's inbreeding coefficient (COI): "
    "the distance-weighted shared ancestry that actually drives extra birth "
    "defects. Each shared ancestor contributes 0.5^(gens_a + gens_b + 1), so "
    "a shared ancestor ~5 generations back on both sides adds only ~0.05% — "
    "effectively nothing. COI 0% means no shared ancestry that matters.",
    # Gen delta
    "Generation gap = focused cat's generation minus this partner's. "
    "Negative means the partner is from a deeper line, positive the reverse. "
    "Useful alongside Family: a big gap with 'unrelated' is often the "
    "safest kind of pairing in a deep colony.",
    # Room
    "Where the cat currently is: a house room (breeding happens in-house) "
    "or Adventure (away until the next day).",
    # Risk
    "Birth-defect risk for a kitten from this pair (0–100%), derived from "
    "the pair's inbreeding coefficient (COI — see the Family header). "
    "COI 0% leaves only the ~2% baseline. "
    "Green ≤ 5% (safe), amber 5–12% (caution), red > 12% (likely defect).",
    # Compat
    "The game's own compatibility score: 0.15 × charisma × libido × "
    "lover bonus × sexuality multiplier. Above 0.05 the game will attempt "
    "the breed; higher = more successful rounds. Green = passes, "
    "amber = below the pass line.",
    # Exp/stat
    "Expected value of each of the kitten's 7 base stats (0–7 scale), "
    "using the better parent's stat with ~50% inheritance weight. "
    "Per-stat ranges show in the strip below when you select the row.",
    # >=7
    "Expected number of the kitten's stats that land on 7 or higher "
    "(Perfect-7 planning). A stat both parents have at 7 counts 1.0; "
    "a stat that can reach 7 counts fractionally.",
    # Note
    "Relationship flags and blockers. ♥ = the focused cat is in love "
    "with them, ♥♥ = mutual lovers, 'hates you' = hater conflict. "
    "Blocked rows show the rejection reason (direct family, straight "
    "same-sex, …). 'n kitten(s)' = kittens this pair already produced.",
]


def _note_text(row, kids: list[str]) -> str:
    """The human-readable Note cell contents for a partner row."""
    parts = []
    if row.mutual_lover:
        parts.append("♥♥")
    elif row.is_lover:
        parts.append("♥")
    if row.is_hater:
        parts.append("hates you")
    if not row.compatible and row.reason:
        parts.append(row.reason)
    if kids:
        parts.append(f"{len(kids)} kitten(s)")
    return "  ".join(parts)


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


def _fmt_compat(v: float) -> str:
    return f"{v:.3f}"


def _stats_html(cat: Cat) -> str:
    parts = []
    for s in STAT_NAMES:
        v = cat.base_stats[s]
        color = "#7fe08a" if v >= 7 else ("#e8e6ee" if v >= 4 else "#c0b9d8")
        parts.append(f'<span style="color:{color}"><b>{s}</b> {v}</span>')
    return "   ".join(parts)


class PaletteWindow(QWidget):
    """The overlay palette. Owns the save session, watcher and worker."""

    def __init__(self):
        super().__init__()
        self._settings = cfg.load()
        self._session: Optional[Session] = None
        self._focus: Optional[Cat] = None
        self._history: list[int] = []
        self._watcher: Optional[SaveWatcher] = None
        self._lock = threading.Lock()
        self._pending: list[tuple] = []   # (token, kind, result)
        self._token = 0
        self._ui_busy = False
        self._pinned = True               # mirror of the 📌 button state
        self._click_through = False       # mouse passes through to the game
        self._flag_applied = False        # non-Windows fallback guard

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
        self.resize(760, 560)
        self._restore_geometry()
        self.setStyleSheet(STYLESHEET)
        self._build_ui()
        self._wire_ui()
        self._poll = QTimer(self)
        self._poll.setInterval(120)
        self._poll.timeout.connect(self._on_poll)
        self._poll.start()

        self._adopt_session(None)
        self._load_last_save()

    # ── UI construction ───────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)

        # header
        head = QHBoxLayout()
        grip = _DragLabel("⠿")
        grip.setStyleSheet("color:#6a6390; font-size:13px;")
        grip.setToolTip("Drag to move the overlay")
        self._title = _DragLabel("🐈 Breeding Overlay")
        self._title.setStyleSheet("font-weight:700; font-size:14px;")
        self._title.setCursor(Qt.CursorShape.OpenHandCursor)
        self._status = QLabel("")
        self._status.setObjectName("muted")
        self._status.setStyleSheet("color:#9a94b8; font-size:11px;")
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
        close = QPushButton("✕")
        close.setFixedWidth(34)
        close.setToolTip("Hide (Ctrl+Shift+B / tray) — quits when no tray is available")
        close.clicked.connect(self._on_close_clicked)
        head.addWidget(grip)
        head.addWidget(self._title)
        head.addWidget(self._status, 1)
        head.addWidget(pin)
        head.addWidget(ct)
        head.addWidget(open_save)
        head.addWidget(close)
        root.addLayout(head)

        # search
        self._search = QLineEdit()
        self._search.setPlaceholderText("Click a cat in-game, then type its name here…")
        self._search.setClearButtonEnabled(True)
        self._search.setToolTip(
            "Type part of a cat's name to pick who to analyse. All alive cats "
            "come from the live save; the roster refreshes automatically when "
            "the game saves."
        )
        self._results = QListWidget()
        self._results.setVisible(False)
        self._results.setMaximumHeight(170)
        root.addWidget(self._search)
        root.addWidget(self._results)

        # focused cat summary
        self._cat_box = QWidget()
        cat_l = QVBoxLayout(self._cat_box)
        cat_l.setContentsMargins(0, 0, 0, 0)
        cat_l.setSpacing(3)
        self._cat_name = QLabel("No cat selected")
        self._cat_name.setObjectName("headerName")
        self._cat_meta = QLabel("")
        self._cat_meta.setObjectName("muted")
        self._cat_stats = QLabel("")
        self._cat_stats.setTextFormat(Qt.TextFormat.RichText)
        self._cat_lovers = QLabel("")
        self._cat_lovers.setObjectName("muted")
        row2 = QHBoxLayout()
        self._btn_swap = QPushButton("Hide blocked rows")
        self._btn_swap.setCheckable(True)
        self._btn_swap.setToolTip(
            "When checked, pairs that cannot breed (direct family, hater, "
            "sexuality blocks) are hidden instead of listed below."
        )
        row2.addWidget(self._btn_swap, 0, Qt.AlignmentFlag.AlignRight)
        cat_l.addWidget(self._cat_name)
        cat_l.addWidget(self._cat_meta)
        cat_l.addWidget(self._cat_stats)
        cat_l.addWidget(self._cat_lovers)
        cat_l.addLayout(row2)
        root.addWidget(self._cat_box)

        # partners
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        # Per-section explanation tooltips on the header items — QHeaderView
        # natively shows these on hover (works on every platform).
        for i, tip in enumerate(_COL_TIPS):
            item = self._table.horizontalHeaderItem(i)
            if item is not None:
                item.setToolTip(tip)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for i, w in enumerate([150, 118, 46, 84, 56, 60, 58, 40]):
            self._table.setColumnWidth(i, w)
        # manual sorting (headers clickable; tri-state per column)
        self._table.setSortingEnabled(False)
        self._rows: list = []          # currently rendered (row, kids) pairs
        self._sort_col: Optional[int] = None
        self._sort_dir = "asc"
        root.addWidget(self._table, 1)

        # detail strip
        self._detail = QLabel("Select a partner row for inheritance detail.")
        self._detail.setWordWrap(True)
        self._detail.setObjectName("muted")
        self._detail.setToolTip(
            "Details for the highlighted partner: per-stat inheritance ranges "
            "for the kitten (min of the parents → max of the parents per stat), "
            "and any kittens this pair has already produced."
        )
        root.addWidget(self._detail)

    def _wire_ui(self) -> None:
        self._search.textChanged.connect(self._on_search_text)
        self._search.returnPressed.connect(self._on_search_enter)
        self._results.itemClicked.connect(self._on_result_clicked)
        self._results.itemActivated.connect(self._on_result_clicked)
        self._table.itemSelectionChanged.connect(self._on_partner_selected)
        self._table.itemDoubleClicked.connect(self._on_partner_double)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._btn_swap.toggled.connect(self._recompute_partners)

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
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate Mewgenics save", "", "Mewgenics saves (*.sav)"
        )
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
        if self._watcher is not None:
            self._watcher.stop()
        self._watcher = SaveWatcher(path, on_change=self._on_save_changed)
        self._watcher.start()

    def _on_save_changed(self) -> None:
        self._set_status("save changed — reloading…")
        self._schedule_reload()

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def shutdown(self) -> None:
        """Stop background threads before the app exits."""
        self._poll.stop()
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    # ── background work ────────────────────────────────────────────────────
    def _schedule_reload(self) -> None:
        with self._lock:
            self._token += 1
            token = self._token
        path = self._settings.get("save_path")
        if not path:
            return

        def work():
            tmp = safe_read_save(path)
            if tmp is None:
                return
            try:
                sess = Session(tmp)
                with self._lock:
                    self._pending.append((token, "session", sess))
            finally:
                import os
                os.unlink(tmp)

        threading.Thread(target=work, name="save-reload", daemon=True).start()

    def _schedule_partners(self) -> None:
        with self._lock:
            self._token += 1
            token = self._token
            session = self._session
            focus = self._focus
        if session is None or focus is None:
            return
        cat_key = focus.db_key
        max_rows = int(self._settings.get("max_partners", 30))
        show_blocked = 0 if self._btn_swap.isChecked() else int(
            self._settings.get("show_blocked", 3)
        )
        include_adv = bool(self._settings.get("include_adventure", True))
        order = str(self._settings.get("order", "risk"))

        def work():
            try:
                sess = session
                cat = sess.by_key.get(cat_key)
                if cat is None:
                    return
                rows = sess.rank_partners(
                    cat,
                    max_partners=max_rows,
                    include_adventure=include_adv,
                    show_blocked=show_blocked,
                    order=order,
                )
                enriched = []
                for r in rows:
                    kids = tracked_offspring(cat, r.partner)
                    enriched.append((r, [k.name for k in kids]))
                with self._lock:
                    self._pending.append((token, "partners", (cat_key, enriched)))
            except Exception as exc:  # keep UI alive on parser surprises
                log.exception("partner scoring failed")
                with self._lock:
                    self._pending.append((token, "partners_error", str(exc)))

        threading.Thread(target=work, name="partner-score", daemon=True).start()

    def _on_poll(self) -> None:
        """Drain completed background jobs on the UI thread (token-guarded)."""
        with self._lock:
            items = self._pending
            self._pending = []
        for token, kind, result in items:
            if kind == "session":
                if token >= self._token:
                    self._adopt_session(result)
            elif kind == "partners":
                cat_key, rows = result
                if token >= self._token and self._focus is not None \
                        and self._focus.db_key == cat_key:
                    self._render_partners(rows)
            elif kind == "partners_error":
                self._set_status(f"scoring failed: {result}")

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

    # ── selection & search ─────────────────────────────────────────────────
    def set_focus_key(self, db_key: int) -> None:
        """Programmatic focus (used by the future in-game bridge)."""
        if self._session is None:
            return
        cat = self._session.by_key.get(db_key)
        if cat is not None:
            self.set_focus(cat)

    def set_focus(self, cat: Cat) -> None:
        self._history.append(cat.db_key)
        self._focus = cat
        self._search.setText("")
        self._search.clearFocus()
        self._show_focus(cat)
        self._schedule_partners()

    def _clear_focus(self) -> None:
        self._focus = None
        self._cat_name.setText("No cat selected")
        self._cat_meta.setText("")
        self._cat_stats.setText("")
        self._cat_lovers.setText("")
        self._table.setRowCount(0)
        self._detail.setText("Select a partner row for inheritance detail.")

    def _show_focus(self, cat: Cat) -> None:
        gen = "stray" if cat.generation == 0 else f"gen {cat.generation}"
        meta = f"{gender_badge(cat.gender)} {cat.gender} · {cat.room or cat.status} · {gen}"
        if cat.age is not None:
            meta += f" · {cat.age}d"
        if cat.inbredness > 0.03:
            meta += f" · inbred {cat.inbredness * 100:.0f}%"
        self._cat_name.setText(cat.name)
        self._cat_name.setToolTip(
            f"{cat.name}  (save id {cat.db_key})\n"
            "The cat you are analysing. Double-click a partner to switch "
            "the analysis to them."
        )
        self._cat_meta.setText(meta)
        self._cat_meta.setToolTip(
            f"{cat.gender} · {cat.room or cat.status} · "
            f"generation {cat.generation} (0 = stray, each generation adds depth "
            "and shared ancestry)"
            + (f" · age {cat.age} days" if cat.age is not None else "")
            + (f"\nInbreeding coefficient {cat.inbredness * 100:.1f}% = kinship "
               "of this cat's parents — flagged above 3%."
               if cat.inbredness > 0.03 else "")
        )
        self._cat_stats.setText(_stats_html(cat))
        self._cat_stats.setToolTip(
            "Base stats (STR DEX CON INT SPD CHA LCK, 0–7) — the birth stats "
            "kittens inherit from. Green = 7. These drive breeding math; "
            "gear/mod bonuses are not shown here."
        )
        lover_txt = ", ".join(l.name for l in getattr(cat, "lovers", []))
        self._cat_lovers.setText(
            f"♥ in love with: {lover_txt}" if lover_txt else "no lovers"
        )
        self._cat_lovers.setToolTip(
            "Current in-game love relationships. Lovers get a compatibility "
            "bonus; lover conflicts are handled at room-assignment level, "
            "not as a hard pair block." if lover_txt
            else "This cat has no in-game lovers right now."
        )

    # ── search results ─────────────────────────────────────────────────────
    def _on_search_text(self, text: str) -> None:
        if not text.strip():
            self._results.setVisible(False)
            return
        if self._session is None:
            return
        hits = self._session.search(text, limit=8)
        self._results.clear()
        for c in hits:
            item = QListWidgetItem(
                f"{c.name}   · {c.room or c.status}   · {c.gender}   · "
                f"sum {sum(c.base_stats.values())}"
            )
            item.setData(Qt.ItemDataRole.UserRole, c.db_key)
            self._results.addItem(item)
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

    # ── sorting ────────────────────────────────────────────────────────────
    def _col_key(self, col: int, row: PartnerRow, kids: list[str]):
        """Sort key for a column (text columns sort as strings, rest numeric)."""
        p = row.partner
        if col == 0:
            return p.name.lower()
        if col == 1:
            return row.relation.label.lower()
        if col == 2:
            return row.relation.gen_gap
        if col == 3:
            return (p.room or p.status).lower()
        if col == 4:
            return row.risk_pct
        if col == 5:
            return row.game_compat
        if col == 6:
            return row.expected_avg
        if col == 7:
            return row.seven_plus_total
        return _note_text(row, kids).lower()

    def _order_rows(self) -> list:
        """Compatible partners first (per active column), blocked rows after."""
        if self._sort_col is None:
            return list(self._rows)          # default safe-first order from engine
        rev = self._sort_dir == "desc"
        good = [e for e in self._rows if e[0].compatible]
        blocked = [e for e in self._rows if not e[0].compatible]
        good.sort(key=lambda e: self._col_key(self._sort_col, e[0], e[1]), reverse=rev)
        # blocked rows keep a readable fixed order (text columns only)
        if self._sort_col in (0, 1, 8):
            blocked.sort(key=lambda e: self._col_key(self._sort_col, e[0], e[1]),
                         reverse=rev)
        return good + blocked

    def _on_header_clicked(self, col: int) -> None:
        """Tri-state sort: asc -> desc -> back to default order."""
        if self._sort_col == col:
            if self._sort_dir == "asc":
                self._sort_dir = "desc"
            else:
                self._sort_col = None       # third click: default order
        else:
            self._sort_col = col
            self._sort_dir = "asc"
        self._redraw_table()

    def _update_sort_indicator(self) -> None:
        hdr = self._table.horizontalHeader()
        if self._sort_col is None:
            hdr.setSortIndicatorShown(False)
            return
        order = (Qt.SortOrder.AscendingOrder if self._sort_dir == "asc"
                 else Qt.SortOrder.DescendingOrder)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(self._sort_col, order)

    # ── table rendering ────────────────────────────────────────────────────
    def _redraw_table(self) -> None:
        ordered = self._order_rows()
        self._table.setRowCount(0)
        self._table.setRowCount(len(ordered))
        for r_i, (row, kids) in enumerate(ordered):
            p = row.partner
            ok = row.compatible
            name = p.name if ok else f"{p.name}  (✗)"
            rel = row.relation

            it_name = QTableWidgetItem(name)
            it_name.setData(Qt.ItemDataRole.UserRole, (row, kids))
            it_name.setToolTip(self._partner_tooltip(row, kids))

            it_family = QTableWidgetItem(rel.label)
            it_family.setToolTip(self._family_tooltip(row))
            it_gap = QTableWidgetItem(f"{rel.gen_gap:+d}" if rel.gen_gap else "0")
            it_gap.setToolTip(
                f"Generation gap for {p.name}: focused gen − partner gen "
                f"= {rel.gen_gap:+d}."
            )

            it_room = QTableWidgetItem(p.room or p.status)
            it_risk = QTableWidgetItem(f"{row.risk_pct:.1f}%" if ok else "—")
            it_comp = QTableWidgetItem(_fmt_compat(row.game_compat) if ok else "—")
            it_exp = QTableWidgetItem(f"{row.expected_avg:.2f}" if ok else "—")
            it_7 = QTableWidgetItem(f"{row.seven_plus_total:.1f}" if ok else "—")
            it_note = QTableWidgetItem(_note_text(row, kids))

            if ok:
                it_risk.setToolTip(
                    f"Birth-defect risk for this pair: {row.risk_pct:.1f}%."
                )
                it_comp.setToolTip(
                    f"Game compatibility for this pair: {row.game_compat:.3f} — "
                    + ("passes the 0.05 line."
                       if row.game_compat > 0.05
                       else "below the 0.05 line.")
                )
                proj = row.pair_factors.projection
                it_exp.setToolTip(
                    f"Expected offspring stat average: {row.expected_avg:.2f} / 7.\n"
                    "Per-stat inheritance ranges for this pair:\n"
                    + "\n".join(
                        f"  {s}: {proj.stat_ranges[s][0]}–{proj.stat_ranges[s][1]}"
                        for s in STAT_NAMES
                    )
                )
                it_7.setToolTip(
                    f"Expected stats at 7 or higher: "
                    f"{row.seven_plus_total:.1f} of 7.\n"
                    "Locked 7s for this pair (both parents at 7): "
                    + (", ".join(proj.locked_stats) or "none")
                )
            else:
                for it in (it_risk, it_comp, it_exp, it_7):
                    it.setToolTip("No values — this pair cannot breed. "
                                  "Reason is in the Note column.")
            it_room.setToolTip(f"Current location of {p.name}.")
            it_note.setToolTip(
                (row.reason if (not ok and row.reason) else "")
                + (f"This pair already produced {len(kids)} kitten(s) together"
                   if kids else "")
            )

            color = "#8a849f" if not ok else "#e8e6ee"
            cells = [it_name, it_family, it_gap, it_room, it_risk, it_comp,
                     it_exp, it_7, it_note]
            for col, it in enumerate(cells):
                it.setForeground(QColor(color))
                if ok and col == 1 and rel.is_family:
                    it.setForeground(QColor("#c9a0e8"))   # related, breedable
                if ok and col == 4:
                    it.setForeground(QColor(risk_color(row.risk_pct)))
                if ok and col == 5:
                    it.setForeground(QColor("#7fe08a" if row.game_compat > 0.05
                                           else "#e0a63a"))
                self._table.setItem(r_i, col, it)
        self._update_sort_indicator()

    @staticmethod
    def _family_tooltip(row: PartnerRow) -> str:
        """Row-specific facts only; the COI weighting explanation lives in the
        Family column header tooltip."""
        rel = row.relation
        lines = [f"{rel.label} · COI {row.coi * 100:.1f}%"]
        if rel.is_family:
            lines.append(
                f"{rel.shared_recent} shared ancestor(s) within 4 generations "
                "of both cats"
                + (f" (of {rel.shared_ancestors} total within 9)"
                   if rel.shared_ancestors > rel.shared_recent else "")
            )
            if row.direct_family:
                lines.append("Direct family — breeding is blocked (see Note).")
            else:
                lines.append("Related but breedable — this shared ancestry is "
                             "what pushes the Risk % up.")
        else:
            lines.append("No shared ancestry within the range that matters — "
                         "the safest kind of pairing.")
        return "\n".join(lines)

    @staticmethod
    def _partner_tooltip(row: PartnerRow, kids: list[str]) -> str:
        lines = [f"{row.partner.name}  ({row.partner.gender}, {row.partner.room})"]
        rel = row.relation
        lines.append(f"Family: {rel.label} · Δgen {rel.gen_gap:+d} "
                     f"· COI {row.coi * 100:.1f}%")
        lines.append(f"Birth-defect risk: {row.risk_pct:.1f}%")
        lines.append(f"Game compatibility: {row.game_compat:.3f} "
                     f"(needs > 0.05)")
        if row.compatible:
            proj = row.pair_factors.projection
            ranges = "  ".join(
                f"{s} {proj.stat_ranges[s][0]}–{proj.stat_ranges[s][1]}"
                for s in STAT_NAMES
            )
            lines.append(f"Expected kitten stats: {ranges}")
            lines.append(f"Expected ≥7 stats: {row.seven_plus_total:.1f}")
        if row.is_lover or row.mutual_lover:
            lines.append("They are lovers" if row.mutual_lover else "They like you")
        if kids:
            lines.append("Existing kittens together: " + ", ".join(kids))
        lines.append("Double-click to analyse breeding from this cat.")
        return "\n".join(lines)

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
            if kids:
                text += f"   ·   existing kittens: {', '.join(kids)}"
        else:
            text = head + f"\nCan't breed: {row.reason or 'blocked'}"
        self._detail.setText(text)

    def _on_partner_double(self, item: QTableWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            row, _ = data
            self.set_focus(row.partner)
