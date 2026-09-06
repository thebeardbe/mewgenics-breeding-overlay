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

from PySide6.QtCore import Qt, QEvent, QRect, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.core.session import (
    ALIVE_STATUSES,
    Cat,
    PartnerRow,
    Session,
    display_location,
)
from mewgenics_overlay.core.watcher import SaveWatcher, safe_read_save
from mewgenics_overlay.vendor.breeding import tracked_offspring

from . import config as cfg
from .theme import STYLESHEET, gender_badge, risk_color, wrap_tooltip as _wt
from mewgenics_overlay.core.maladies import (
    ASYMMETRIC_GROUPS,
    _side_text,
    defect_inheritance_rows,
    defect_lines,
    disorder_summary,
    sexuality_label,
)
from mewgenics_overlay.core.gameassets import GameAssets, locate_gpak
from mewgenics_overlay.core.stimulation import (
    STIMULATION_DEFAULT,
    room_env_map,
)
from mewgenics_overlay.core.recommend import recommend as recommend_best

log = logging.getLogger("mewgenics_overlay.ui")

STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]

_COLS = ["Cat", "Family", "GenΔ", "Room", "Risk", "Night", "Exp/stat", "≥7", "Defects", "Note"]

# Column explanations shown as tooltips when hovering each header.
# Column explanation tooltips, written as short human paragraphs so each
# idea, legend line or note starts on its own line.
_COL_TIP_PARAS = [
    # Cat
    [
        "The partner cat being compared.",
        "Rows marked ✗ can't breed with the cat you picked — the Note "
        "column says why.",
        "Double-click a row to look at things from that cat's side instead.",
        "Tip: click any header to sort, click again to reverse, and a third "
        "click brings back the default order.",
    ],
    # Family
    [
        "How the two cats are related, found by tracing shared ancestors "
        "up to 9 generations back.",
        "Shown as simple names: parent/child, sibling, aunt/uncle, "
        "1st cousin, … or 'unrelated'.",
        "Shared family history is what drives inbreeding (the Risk column).",
        "Very distant shared ancestors barely count — each generation back "
        "halves the effect, so ~5 generations back is effectively nothing.",
    ],
    # Gen delta
    [
        "How far apart the cats sit in the family tree: the generation of "
        "the cat you picked minus theirs.",
        "A positive gap means the partner comes from an older line, a "
        "negative one a younger line.",
        "Read it together with Family: a big gap with 'unrelated' is often "
        "the safest pairing in a deep colony.",
    ],
    # Room
    [
        "Where this cat is right now.",
        "In a named room: can take part in overnight breeding.",
        "On Adventure: away from the house until the next day.",
        "Outside house: standing on screen, not in a room or adventure box.",
    ],
    # Risk
    [
        "How likely the kitten is to be born with a problem (a disorder or "
        "a birth defect), as a percentage.",
        "Two strangers sit near the base ~2%.",
        "The closer the parents are related, the higher it climbs.",
        "Colour key — green: low (≤ 5%) · orange: medium (5–12%) · "
        "red: high (> 12%).",
    ],
    # Chance (Nightly)
    [
        "How likely the pair is to breed on a given night — one number that "
        "already folds in the game's two nightly rolls.",
        "A higher room Comfort nudges it up a little.",
        "Below 5% the game won't even attempt the pair.",
        "Colour key — green: above the line · orange: below it.",
    ],
    # Exp/stat
    [
        "The size each kitten stat is likely to end up at, on a 0–7 scale "
        "(the average across all seven stats).",
        "Higher room Stimulation makes kittens inherit the better parent's "
        "stat more often.",
        "Click a row to see each stat's possible range in detail below.",
    ],
    # >=7
    [
        "How many of the kitten's stats should come out as a perfect 7.",
        "A stat where both parents are already 7 is a guaranteed one and "
        "counts fully; others count by how reachable they are.",
    ],
    # Defects
    [
        "The birth defects these parents already carry, and whether the "
        "kitten will inherit them.",
        "✓ = both parents carry it — the kitten will get it.",
        "A % = one parent carries it — that's the kitten's chance at the "
        "selected room's Stimulation.",
        "Hover a cell to see which side/part it affects, whether it comes "
        "from one shared family line, and what the defect actually does.",
    ],
    # Note
    [
        "Extra notes per row.",
        "♥ = in love with the cat you picked · ♥♥ = mutual lovers.",
        "'hates you' = the two dislike each other.",
        "Blocked rows explain why breeding can't happen.",
        "'kittens' = how many this pair has produced, and how many are "
        "still around (not dead or donated).",
    ],
]
_COL_TIPS = ["\n".join(paras) for paras in _COL_TIP_PARAS]


# ── column indexes (keep in sync with _COLS) ─────────────────────
(COL_CAT, COL_FAMILY, COL_GEN_DELTA, COL_ROOM, COL_RISK, COL_CHANCE,
 COL_EXP, COL_SEVEN, COL_DEFECTS, COL_NOTE) = range(10)
assert len(_COLS) == 10

# ── palette colours (single source of truth) ─────────────────────
C_TEXT = "#e8e6ee"        # normal text
C_MUTED = "#8a849f"       # blocked rows / low emphasis
C_FAMILY = "#c9a0e8"      # related but breedable
C_GOOD = "#7fe08a"        # pass / high stat
C_WARN = "#e0a63a"        # caution / inherited defect
C_STAT_LOW = "#c0b9d8"    # low-ish stat chip
C_GRIP = "#6a6390"        # drag grip
C_STATUS = "#9a94b8"      # header status text

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
    kittens = _kittens_label(row)
    if kittens:
        parts.append(kittens)
    return "  ".join(parts)


def _kittens_label(row) -> str:
    """e.g. '1 kitten', '3 kittens', '3 kittens, 1 available',
    '3 kittens, none available' — 'available' means still in the house /
    on adventures (dead or donated/gone kittens are excluded)."""
    total = int(getattr(row, "kitty_total", 0) or 0)
    if total <= 0:
        return ""
    noun = "1 kitten" if total == 1 else f"{total} kittens"
    available = int(getattr(row, "kitty_available", total) or 0)
    if available < total:
        noun += ", none available" if available == 0 \
            else f", {available} available"
    return noun


def _defect_short(name: str) -> str:
    return name.replace(" Birth Defect", "") or name


def _defects_summary(row, stimulation: float = 50.0) -> str:
    """Compact Defects cell text: shared defects as '✓', single-carrier as %."""
    factors = row.pair_factors
    if factors is None:
        return ""
    parts = []
    for d in defect_inheritance_rows(factors.cat_a, factors.cat_b, row.coi,
                                     stimulation=stimulation):
        short = _defect_short(d.name)
        parts.append(short + (" ✓" if len(d.carriers) == 2
                              else f" ≈{d.chance_pct:.0f}%"))
    return "; ".join(parts)


def _any_defect_guaranteed(row, stimulation: float = 50.0) -> bool:
    factors = row.pair_factors
    if factors is None:
        return False
    return any(len(d.carriers) == 2
               for d in defect_inheritance_rows(factors.cat_a, factors.cat_b,
                                                row.coi,
                                                stimulation=stimulation))


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


def _fmt_chance(v: float, comfort: float = 0.0) -> str:
    """Nightly breeding chance % — both of the game's rolls folded into one."""
    return f"{max(0.0, min(100.0, _night_chance(v, comfort) * 100.0)):.0f}%"


def _roll_chance(v: float, comfort: float = 0.0) -> float:
    """Per-roll success chance (0..1): compat × √(1 + 0.1×Comfort)."""
    roll = v * (1.0 + 0.1 * max(0.0, comfort)) ** 0.5
    return max(0.0, min(1.0, roll))


def _night_chance(v: float, comfort: float = 0.0) -> float:
    """Chance the pair breeds on a given night: both of the game's two nightly
    rolls must succeed, so it is the per-roll chance squared."""
    roll = _roll_chance(v, comfort)
    return roll * roll


def _stats_html(cat: Cat) -> str:
    parts = []
    for s in STAT_NAMES:
        v = cat.base_stats[s]
        color = C_GOOD if v >= 7 else (C_TEXT if v >= 4 else C_STAT_LOW)
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
        self._maybe_start_assets()

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
            with self._lock:
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
        grip.setStyleSheet(f"color:{C_GRIP}; font-size:13px;")
        grip.setToolTip("Drag to move the overlay")
        self._title = _DragLabel("🐈 Breeding Overlay")
        self._title.setStyleSheet("font-weight:700; font-size:14px;")
        self._title.setCursor(Qt.CursorShape.OpenHandCursor)
        self._status = QLabel("")
        self._status.setObjectName("muted")
        self._status.setStyleSheet(f"color:{C_STATUS}; font-size:11px;")
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
        self._cat_health = QLabel("")
        self._cat_health.setWordWrap(True)
        self._cat_health.setStyleSheet(f"color:{C_WARN}; font-size:11px;")
        self._cat_health.setToolTip(_wt(
            "Things this cat carries that can be passed on to kittens.\n"
            "• Disorders — a 15% chance per parent that carries one of "
            "passing a random disorder to the kitten.\n"
            "• Birth defects — kittens inherit these per body part; pick a "
            "partner to see the exact odds for that pairing."
        ))
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
        cat_l.addWidget(self._cat_name)
        cat_l.addWidget(self._cat_meta)
        cat_l.addWidget(self._cat_stats)
        cat_l.addWidget(self._cat_lovers)
        cat_l.addWidget(self._cat_health)
        cat_l.addLayout(row2)
        root.addWidget(self._cat_box)

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
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        # Per-section explanation tooltips on the header items — QHeaderView
        # natively shows these on hover (works on every platform).
        for i, tip in enumerate(_COL_TIPS):
            item = self._table.horizontalHeaderItem(i)
            if item is not None:
                item.setToolTip(_wt(tip))
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for i, w in enumerate([140, 106, 42, 74, 54, 58, 54, 36, 128]):
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
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._btn_swap.toggled.connect(self._recompute_partners)
        self._btn_best.clicked.connect(self._on_best_clicked)
        self._btn_safe.toggled.connect(self._on_safe_toggled)

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
        max_rows = int(self._settings.get("max_partners", 100))
        show_blocked = 0 if self._btn_swap.isChecked() else None
        include_adv = bool(self._settings.get("include_adventure", True))
        order = str(self._settings.get("order", "risk"))
        stimulation = self._stim_value()

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
                    stimulation=stimulation,
                )
                enriched = []
                for r in rows:
                    kids = tracked_offspring(cat, r.partner)
                    r.kitty_total = len(kids)
                    r.kitty_available = sum(
                        1 for k in kids
                        if getattr(k, "status", "") in ALIVE_STATUSES)
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
            assets = self._asset_result
            self._asset_result = None
        if assets is not None:
            self._ga = assets if assets.ok else None
            self._refresh_room_combo()        # room Stimulation now available
            if self._rows:
                self._redraw_table()          # effects now available in tooltips
            if self._focus is not None:
                self._show_focus(self._focus)  # refresh health/effect tooltip
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
        self._refresh_room_combo()
        self._donations_tab.refresh(self._session)

    # ── breeding-room Stimulation ──────────────────────────────────────────
    @staticmethod
    def _to_float(raw, default: float, floor: Optional[float] = None) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
        return max(value, floor) if floor is not None else value

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
        self._cat_health.setText("")
        self._best_row = None
        self._btn_best.setVisible(False)
        self._btn_safe.setVisible(False)
        self._table.setRowCount(0)
        self._detail.setText("Select a partner row for inheritance detail.")

    def _show_focus(self, cat: Cat) -> None:
        gen = "stray" if cat.generation == 0 else f"gen {cat.generation}"
        _gender = (cat.gender or "?").lower()
        _sex = sexuality_label(getattr(cat, "sexuality_raw", None)) \
            if _gender in ("male", "female") else None
        meta = f"{gender_badge(cat.gender)} {cat.gender}" + \
            (f" · {_sex}" if _sex else "") + \
            f" · {display_location(cat)} · {gen}"
        if cat.age is not None:
            meta += f" · {cat.age}d"
        if cat.inbredness > 0.03:
            meta += f" · inbred {cat.inbredness * 100:.0f}%"
        self._cat_name.setText(cat.name)
        self._cat_name.setToolTip(_wt(
            f"{cat.name}  (save id {cat.db_key})\n"
            "The cat you are analysing. Double-click a partner to switch "
            "the analysis to them."
        ))
        self._cat_meta.setText(meta)
        self._cat_meta.setToolTip(_wt(
            f"{cat.gender} · {display_location(cat)} · "
            f"generation {cat.generation} (0 = stray, each generation adds "
            f"depth and shared ancestry)"
            + (f"\nSexuality: {_sex}. "
               "Bi/gay cats can breed with the same sex." if _sex else "")
            + (f" · age {cat.age} days" if cat.age is not None else "")
            + (f"\nInbreeding coefficient {cat.inbredness * 100:.1f}% = kinship "
               "of this cat's parents — flagged above 3%."
               if cat.inbredness > 0.03 else "")
        ))
        self._cat_stats.setText(_stats_html(cat))
        self._cat_stats.setToolTip(_wt(
            "Base stats (STR DEX CON INT SPD CHA LCK, 0–7) — the birth stats "
            "kittens inherit from. Green = 7. These drive breeding math; "
            "gear/mod bonuses are not shown here."
        ))
        lover_txt = ", ".join(l.name for l in getattr(cat, "lovers", []))
        self._cat_lovers.setText(
            f"♥ in love with: {lover_txt}" if lover_txt else "no lovers"
        )
        self._cat_lovers.setToolTip(_wt(
            "In-game relationships.\n"
            "Being lovers gives the pair a bonus when breeding.\n"
            "If a cat already loves someone else, picking a different "
            "partner can complicate things later." if lover_txt
            else "This cat is not in love with anyone right now."
        ))
        # traits this cat already carries (defects / disorders)
        disorders = list(getattr(cat, "disorders", None) or [])
        own_defects = defect_lines(cat)
        health_bits = []
        if disorders:
            health_bits.append("disorders: " + ", ".join(disorders))
        if own_defects:
            health_bits.append("birth defects: " + ", ".join(own_defects))
        self._cat_health.setText(
            "⚠ " + " · ".join(health_bits) if health_bits else ""
        )
        self._cat_health.setToolTip(_wt(self._health_tooltip(
            cat, disorders, own_defects)))

    def _health_tooltip(self, cat, disorders, own_defects) -> str:
        """Hover text for the ⚠ health line: carried traits + in-game effects
        (effects come from resources.gpak when available)."""
        if not (disorders or own_defects):
            return "No birth defects or disorders."
        lines = [f"{cat.name} carries:"]
        if disorders:
            lines.append("• disorders: " + ", ".join(disorders)
                         + " — each parent with a disorder has a 15% chance "
                           "to pass one")
        if own_defects:
            lines.append("• birth defects (odds depend on the partner — "
                         "select one to see them):")
            seen: set = set()
            for e in (getattr(cat, "visual_mutation_entries", None) or []):
                if not e.get("is_defect"):
                    continue
                name = e.get("name")
                if not name or name in seen:
                    continue
                seen.add(name)
                eff = (self._ga.effect_for(e.get("group_key"),
                                           e.get("mutation_id"))
                       if self._ga is not None else "")
                lines.append("    · " + name + (f" — {eff}" if eff else ""))
        return "\n".join(lines)

    # ── search results ─────────────────────────────────────────────────────
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
            cap = float(self._settings.get("safe_risk_cap", 15.0))
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
        malady = self._pair_malady_lines(chosen.row, self._stim_value())
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

    def _col_key(self, col: int, row: PartnerRow, kids: list[str]):
        """Sort key for a column (text columns sort as strings, rest numeric)."""
        p = row.partner
        if col == COL_CAT:
            return p.name.lower()
        if col == COL_FAMILY:
            return row.relation.label.lower()
        if col == COL_GEN_DELTA:
            return row.relation.gen_gap
        if col == COL_ROOM:
            return display_location(p).lower()
        if col == COL_RISK:
            return row.risk_pct
        if col == COL_CHANCE:
            return row.game_compat
        if col == COL_EXP:
            return row.expected_avg
        if col == COL_SEVEN:
            return row.seven_plus_total
        if col == COL_DEFECTS:
            return _defects_summary(row, self._stim_value()).lower()
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
        if self._sort_col in (COL_CAT, COL_FAMILY, COL_DEFECTS, COL_NOTE):
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

            it_room = QTableWidgetItem(display_location(p))
            it_risk = QTableWidgetItem(f"{row.risk_pct:.1f}%" if ok else "—")
            it_comp = QTableWidgetItem(
                _fmt_chance(row.game_compat, self._comfort_value())
                if ok else "—")
            it_exp = QTableWidgetItem(f"{row.expected_avg:.2f}" if ok else "—")
            it_7 = QTableWidgetItem(f"{row.seven_plus_total:.1f}" if ok else "—")
            defects_text = _defects_summary(row, self._stim_value())
            it_defects = QTableWidgetItem(defects_text)
            it_defects.setToolTip(
                _wt("\n".join(self._pair_malady_lines(row, self._stim_value()))
                   or "Both parents clean.")
            )
            it_note = QTableWidgetItem(_note_text(row, kids))

            if ok:
                it_risk.setToolTip(
                    f"Birth-defect risk for this pair: {row.risk_pct:.1f}%."
                )
                _comfort = self._comfort_value()
                it_comp.setToolTip(_wt(
                    f"Nightly breeding chance: "
                    f"{_fmt_chance(row.game_compat, _comfort)}.\n"
                    "That's the answer to 'will they breed tonight?' — the "
                    "game's two rolls are already folded in."
                    + ("\nThe pair is above the 5% line, so the game will "
                       "try it." if row.game_compat > 0.05
                       else "\nBelow the 5% line — the game won't attempt it.")
                ))
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
                + (f"Existing kittens: {_kittens_label(row)}" if kids else "")
            )

            cells = [it_name, it_family, it_gap, it_room, it_risk, it_comp,
                     it_exp, it_7, it_defects, it_note]
            specials: dict = {}
            if ok:
                if rel.is_family:
                    specials[COL_FAMILY] = C_FAMILY      # related, breedable
                specials[COL_RISK] = risk_color(row.risk_pct)
                specials[COL_CHANCE] = (C_GOOD if row.game_compat > 0.05
                                        else C_WARN)
                if _any_defect_guaranteed(row, self._stim_value()):
                    specials[COL_DEFECTS] = C_WARN       # inherited defects
            base_color = C_MUTED if not ok else C_TEXT
            for col, it in enumerate(cells):
                it.setForeground(QColor(specials.get(col, base_color)))
                self._table.setItem(r_i, col, it)
        self._update_sort_indicator()

    def _same_sex_note(self, row: PartnerRow) -> str:
        """Explain same-sex pairs (only possible with bi/gay cats)."""
        factors = row.pair_factors
        if factors is None:
            return ""
        a, b = factors.cat_a, factors.cat_b
        ga = (getattr(a, "gender", "?") or "?").lower()
        gb = (getattr(b, "gender", "?") or "?").lower()
        if ga == "?" or gb == "?" or ga != gb:
            return ""
        la = sexuality_label(getattr(a, "sexuality_raw", None))
        lb = sexuality_label(getattr(b, "sexuality_raw", None))
        nonstraight = [n for n, l in ((a.name, la), (b.name, lb)) if l != "straight"]
        if not nonstraight:
            return ""   # both straight same-sex pairs are blocked already
        if la == lb:
            who = f"both are {la}"
        else:
            who = f"{nonstraight[0]} is " + \
                  (la if nonstraight[0] == a.name else lb)
        return (f"Same-sex pair — this works: {who}. "
                "(In-game, roles are picked at random and bi/gay cats can "
                "breed same-sex.)")

    @staticmethod
    def _family_tooltip(row: PartnerRow) -> str:
        """Row-specific facts only; the COI weighting explanation lives in the
        Family column header tooltip."""
        rel = row.relation
        lines = [f"Relationship: {rel.label}",
                 f"Shared family history (COI): {row.coi * 100:.1f}%"]
        if rel.is_family:
            close = f"{rel.shared_recent} shared ancestor(s) close enough " \
                    "to matter"
            if rel.shared_ancestors > rel.shared_recent:
                close += f" (of {rel.shared_ancestors} in total)"
            lines.append(close)
            if row.direct_family:
                lines.append("Direct family — the game stops this pairing.")
            else:
                lines.append("Related, but allowed — this shared history "
                             "is what raises the Risk %.")
        else:
            lines.append("No shared family history that matters — the "
                         "safest kind of pairing.")
        lines.append("Longer explanation: hover the Family heading above.")
        return _wt("\n".join(lines))

    def _partner_tooltip(self, row: PartnerRow, kids: list[str]) -> str:
        p = row.partner
        _pg = (p.gender or "?").lower()
        _pl = sexuality_label(getattr(p, "sexuality_raw", None)) \
            if _pg in ("male", "female") else None
        lines = [f"{p.name}  ({p.gender}"
                 + (f" · {_pl}" if _pl else "")
                 + f", {display_location(p)})"]
        rel = row.relation
        lines.append(f"Family: {rel.label} · Δgen {rel.gen_gap:+d} "
                     f"· COI {row.coi * 100:.1f}%")
        lines.append(f"Birth-defect risk: {row.risk_pct:.1f}%")
        lines.append(
            f"Breed attempt/night: {_night_chance(row.game_compat, self._comfort_value()) * 100:.0f}% "
            f"(compat {row.game_compat:.3f} > 0.05)"
        )
        if row.compatible:
            proj = row.pair_factors.projection
            ranges = "  ".join(
                f"{s} {proj.stat_ranges[s][0]}–{proj.stat_ranges[s][1]}"
                for s in STAT_NAMES
            )
            lines.append(f"Expected kitten stats: {ranges}")
            lines.append(f"Expected ≥7 stats: {row.seven_plus_total:.1f}")
        note = self._same_sex_note(row)
        if note:
            lines.append(note)
        malady = self._pair_malady_lines(row, self._stim_value())
        if malady:
            lines.append("")
            lines.extend(malady)
        if row.is_lover or row.mutual_lover:
            lines.append("They are lovers" if row.mutual_lover else "They like you")
        if kids:
            names = ", ".join(kids)
            label = _kittens_label(row)
            lines.append(f"Existing kittens together: {names}")
            if label:
                lines.append(f"   ({label})")
        lines.append("Double-click to analyse breeding from this cat.")
        return _wt("\n".join(lines))

    def _pair_malady_lines(self, row: PartnerRow,
                           stimulation: float = 50.0) -> list[str]:
        """Inheritance of traits the parents ALREADY carry (disorders exact,
        visual birth defects per body part, effects when the gpak is present).
        Empty when both parents are clean."""
        if row.pair_factors is None:
            return []
        a = row.pair_factors.cat_a          # focused cat
        b = row.pair_factors.cat_b          # partner
        dis = disorder_summary(a, b)
        lines: list[str] = []
        if dis["a"]:
            lines.append(f"⚠ {a.name} carries disorder(s): "
                         + ", ".join(dis["a"]))
        if dis["b"]:
            lines.append(f"⚠ {b.name} carries disorder(s): "
                         + ", ".join(dis["b"]))
        if dis["a"] or dis["b"]:
            lines.append(f"→ Kitten inherits ≥1 parent disorder: "
                         f"{dis['any_pct']:.0f}% "
                         f"(15% per parent that carries one)")
        rows = defect_inheritance_rows(a, b, row.coi,
                                       stimulation=stimulation)
        for drow in rows:
            asym = drow.group in ASYMMETRIC_GROUPS
            if len(drow.carriers) == 2:
                if not asym:
                    lines.append(f"→ {drow.name}: both parents carry it — "
                                 f"the kitten gets it (≈100%)")
                elif drow.same_line:
                    lines.append(
                        f"→ {drow.name}: both parents carry it from the SAME "
                        f"line → the kitten gets it on the same part/side "
                        f"(≈100%)"
                    )
                else:
                    lines.append(
                        f"→ {drow.name}: both parents carry it from DIFFERENT "
                        f"lines → the kitten gets it on the same side as one "
                        f"parent OR the opposite side (≈100%)"
                    )
                if asym:
                    sa = _side_text(drow.slots_a)
                    sb = _side_text(drow.slots_b)
                    if sa and sb:
                        lines.append(f"    ({a.name}: {sa} · {b.name}: {sb})")
            else:
                who = a.name if drow.carriers[0] == "a" else b.name
                where = _side_text(drow.slots_a or drow.slots_b)
                loc = f", on {where}" if where else ""
                lines.append(
                    f"→ {drow.name} (carried by {who} only{loc}): "
                    f"≈{drow.chance_pct:.0f}% to pass at "
                    f"{stimulation:g} Stimulation"
                )
            effect = self._effect_for_name(a, b, drow.name)
            if effect:
                lines.append(f"    effect: {effect}")
        if rows and any(len(r.carriers) == 1 for r in rows):
            lines.append("(single-sided odds assume the other parent's matching "
                         "body part is normal; a 20% part-reroll can still "
                         "change one part)")
        return lines

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
        same_sex = self._same_sex_note(row)
        if same_sex:
            head += "\n" + same_sex
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
        malady = self._pair_malady_lines(row, self._stim_value())
        if malady:
            text += "\n" + "\n".join(malady)
        self._detail.setText(text)

    def _on_partner_double(self, item: QTableWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            row, _ = data
            self.set_focus(row.partner)
