"""Donations tab: pick an NPC from a dropdown, see its candidates + why."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.core.donations import (
    cat_status,
    donation_report,
    rating_why,
)
import mewgenics_overlay.ui.theme as _theme
from PySide6.QtGui import QColor
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt

_COLS = ["Cat", "Status", "Age", "Stats", "Donate?", "Why donate"]
_COL_TIPS = [
    "The cat's name. 📌 = pinned (kept for breeding). Hover a row for the full story.",
    "kitten — born today, can't breed yet · retired — went on an adventure "
    "· normal — a regular adult.",
    "Age in days. Kittens (1) go to Tink; seniors (5+) to Tracy.",
    "Just its strength: the sum of the 7 birth stats (0–49). It is NOT the "
    "donation advice — use the 'Donate?' column for that.",
    "Give-away order, weakest first: Donate = safe to part with · Maybe = "
    "borderline · Keep = worth holding on to (breeding-valuable, pinned or "
    "must-breed). Colours: green = donate · amber = maybe · grey = keep. "
    "The Why column explains each rating.",
    "Why this cat qualifies for this NPC. Cats valuable for breeding (or "
    "pinned / must-breed) sink to the bottom — donate them only if you must.",
]


class DonationsTab(QWidget):
    """Pick which NPC you're feeding, see today's candidates with reasons."""

    def __init__(self, palette=None):
        super().__init__()
        self._palette = palette
        self._slots = []
        self._active_npc = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        head = QHBoxLayout()
        lab = QLabel("Donating to:")
        self._combo = QComboBox()
        self._combo.setMinimumWidth(240)
        self._combo.currentIndexChanged.connect(self._on_npc_selected)
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        head.addWidget(lab)
        head.addWidget(self._combo)
        head.addWidget(self._summary, 1)
        root.addLayout(head)

        self._butch_note = QLabel("")
        self._butch_note.setWordWrap(True)
        self._butch_note.setStyleSheet(
            f"color:{_theme.C_MUTED}; padding:4px 8px; "
            f"border:1px dashed {_theme.C_GRIP}; border-radius:6px;")
        self._butch_note.setVisible(False)
        root.addWidget(self._butch_note)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        # explain each column (plain words)
        for i, tip in enumerate(_COL_TIPS):
            item = self._table.horizontalHeaderItem(i)
            if item is not None:
                item.setToolTip(_wt(tip))
        self._table.setWordWrap(True)
        self._table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for i, w in enumerate([130, 70, 44, 58, 96]):
            self._table.setColumnWidth(i, w)
        root.addWidget(self._table, 1)

        self._hint = QLabel("")
        self._hint.setObjectName("muted")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_row_menu)

    # ── data ───────────────────────────────────────────────────────────────
    def refresh(self, session) -> None:
        previous = getattr(self, "_active_npc", None)
        palette = getattr(self, "_palette", None)
        self._effect_of_cat = getattr(palette, "defect_text_of", None) \
            if palette is not None else None
        cats = session.alive if session is not None else []
        dead = getattr(session, "dead_cats", []) if session is not None else []
        flags = getattr(session, "npc_progress_flags", set()) \
            if session is not None else set()
        butch_unlocked = any(f.startswith("butch") for f in flags)
        if butch_unlocked:
            self._butch_note.setText(
                "Butch is unlocked — we can't yet tell which cats he'd take: "
                "the save doesn't record per-cat adventure/chapter progress."
            )
            self._butch_note.setVisible(True)
        else:
            self._butch_note.setVisible(False)
        report = donation_report(
            cats, active=flags, dead=tuple(dead),
            current_day=getattr(session, "current_day", None),
            effect_of_cat=getattr(self, "_effect_of_cat", None)) \
            if (cats or dead) else []
        # Spoiler guard: locked (or unsupported/undetectable) NPCs must never
        # appear — that would give away who exists and what they want.
        self._slots = [s for s in report if s.supported and s.active]
        self._rebuild_combo()
        self._restore_npc(previous)

    def _restore_npc(self, npc) -> None:
        """Keep the dropdown on the NPC the user was viewing after a refresh
        (e.g. after pinning a cat) instead of snapping back to the first."""
        if not npc or not self._slots:
            return
        for i, slot in enumerate(self._slots):
            if slot.npc == npc:
                self._combo.blockSignals(True)
                self._combo.setCurrentIndex(i)
                self._combo.blockSignals(False)
                self._on_npc_selected(i)
                break

    def _rebuild_combo(self) -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        for slot in self._slots:
            self._combo.addItem(f"{slot.npc}  ({slot.count} now)")
            self._combo.setItemData(self._combo.count() - 1, slot, 0x0100)
        self._combo.blockSignals(False)
        if self._slots:
            self._combo.setCurrentIndex(0)
            self._on_npc_selected(0)
        else:
            self._summary.setText(
                "No donation NPCs unlocked yet — the list fills in as you "
                "meet them."
            )
            self._hint.setText("")
            self._table.setRowCount(0)

    def _current_slot(self):
        i = self._combo.currentIndex()
        return self._slots[i] if 0 <= i < len(self._slots) else None

    def _on_npc_selected(self, index: int) -> None:
        slot = self._current_slot()
        if slot is None:
            self._active_npc = None
            self._table.setRowCount(0)
            self._hint.setText("")
            return
        self._active_npc = slot.npc
        if not slot.supported:
            self._table.setRowCount(0)
            self._hint.setText("")
            return
        self._summary.setText(f"{slot.wants} · {slot.count} qualifying")
        self._render_slot(slot)

    @staticmethod
    def _donate_rating(cat, index: int, total: int,
                       keep_for_breeding: bool = False) -> str:
        """Donate? label: Donate / Maybe / Keep (weakest first ordering)."""
        if getattr(cat, "must_breed", False) or getattr(cat, "is_pinned", False):
            return "Keep"
        if keep_for_breeding:
            return "Keep"
        if total <= 1:
            return "Donate"
        fraction = index / (total - 1)
        if fraction < 0.4:
            return "Donate"
        if fraction < 0.75:
            return "Maybe"
        return "Keep"


    @staticmethod
    def _group_reasons(give, keep) -> list:
        lines = []
        if give:
            lines.append("Reasons to donate:")
            lines += ["  • " + line for line in give]
        if keep:
            lines.append("Reasons to keep:")
            lines += ["  • " + line for line in keep]
        if not lines:
            lines.append("No strong reasons either way")
        return lines

    def _show_row_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        slot = self._current_slot()
        palette = getattr(self, "_palette", None)
        if not index.isValid() or slot is None or palette is None:
            return
        cats = slot.candidates
        if index.row() >= len(cats):
            return
        cat = cats[index.row()]
        menu = QMenu(self)
        label = ("Unpin — allow donation" if getattr(cat, "is_pinned", False)
                 else "Pin for breeding")
        action = menu.addAction(label)
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is action:
            palette.set_pinned(cat, not getattr(cat, "is_pinned", False))
            self.refresh(palette._session)

    def _render_slot(self, slot) -> None:
        self._table.setRowCount(0)
        cats = slot.candidates
        total = len(cats)
        self._table.setRowCount(total)
        rating_colour = {
            "Donate": _theme.C_GOOD,
            "Maybe": _theme.C_WARN,
            "Keep": _theme.C_MUTED,
        }
        for r_i, (cat, advice) in enumerate(zip(cats, slot.advice)):
            base = sum(getattr(cat, "base_stats", {}).values())
            inj = sum(1 for s, v in (getattr(cat, "base_stats", {}) or {}).items()
                      if (getattr(cat, "total_stats", {}) or {}).get(s, v) < v)
            rating = self._donate_rating(cat, r_i, total,
                                         keep_for_breeding=advice.keep_for_breeding)
            give = list(advice.give)
            keep = list(advice.keep)
            if not give and not keep:   # no specific reasons -> generic copy
                generic = rating_why(rating)
                if rating == "Keep":
                    keep = generic
                else:
                    give = generic
            why = "\n".join(self._group_reasons(give, keep))
            _pin = "📌 " if getattr(cat, "is_pinned", False) else ""
            cells = [f"{_pin}{cat.name}", cat_status(cat), str(getattr(cat, "age", "?")),
                     str(base), rating, why]
            for c_i, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setToolTip(self._row_tip(cat, slot, base, inj, rating, advice))
                if c_i == 4:
                    it.setForeground(QColor(rating_colour[rating]))
                self._table.setItem(r_i, c_i, it)
        self._hint.setText(
            "Ordered weakest → strongest. Pinned, must-breed and breeding-"
            "valuable cats are marked Keep — donate them only if you must."
        )

    @staticmethod
    def _row_tip(cat, slot, base, injured, rating="", advice=None) -> str:
        lines = [f"{cat.name}  ({getattr(cat, 'gender', '?')})",
                 f"Stats: {base} · age {getattr(cat, 'age', '?')}"]
        if rating:
            lines.append(f"Donate? → {rating}")
        give, keep = [], []
        if advice is not None:
            give, keep = list(advice.give), list(advice.keep)
        if not give and not keep:
            (keep if rating == "Keep" else give).extend(rating_why(rating))
        lines += ["  " + line for line in DonationsTab._group_reasons(give, keep)]
        aggression = getattr(cat, "aggression", None)
        if aggression is not None:
            lines.append(f"Aggression: {float(aggression) * 100:.0f}% "
                         "(marked for fighters — not a donation factor)")
        if injured:
            lines.append(f"{injured} stat(s) with an injury penalty")
        lines.append(f"Suitable for: {slot.npc} ({slot.wants})")
        room = getattr(cat, "room", "") or getattr(cat, "status", "")
        lines.append(f"Where: {room or '?'}")
        return _wt("\n".join(lines))
