"""Donations tab: which cats to send to each NPC today."""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.core.donations import donation_report
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt

_COLS = ["NPC", "Wants", "Qualifying now", "Suggested first"]


class DonationsTab(QWidget):
    """Lists every donation NPC and today's qualifying cats, ranked by who
    to give away first (weakest/most expendable first)."""

    def __init__(self, palette=None):
        super().__init__()
        self._palette = palette
        self._slots = []
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        head = QHBoxLayout()
        self._summary = QLabel("Loading…")
        self._summary.setWordWrap(True)
        self._summary.setToolTip(_wt(
            "Who wants what, and how many of your cats currently qualify.\n"
            "'Suggested first' lists the easiest cats to part with — weak "
            "stats, inbred, older, carrying conditions. In-love, pinned and "
            "must-breed cats are kept out of the top of the list.\n"
            "Unsupported NPCs are shown for completeness; the save format "
            "doesn't tell us their requirements yet."
        ))
        head.addWidget(self._summary, 1)
        root.addLayout(head)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for i, w in enumerate([130, 220, 90, 260]):
            self._table.setColumnWidth(i, w)
        root.addWidget(self._table, 1)

        self._note = QLabel("Refresh happens automatically when the save updates.")
        self._note.setObjectName("muted")
        root.addWidget(self._note)

    # ── data ───────────────────────────────────────────────────────────────
    def refresh(self, session) -> None:
        cats = session.alive if session is not None else []
        self._slots = donation_report(cats) if cats else []
        self._render()

    def _render(self) -> None:
        if not self._slots:
            self._summary.setText("No save loaded — no donation advice yet.")
        else:
            total = sum(s.count for s in self._slots if s.supported)
            self._summary.setText(
                f"Each NPC levels up when you send them their type of cat. "
                f"Right now {total} of your cats qualify for a donation NPC."
            )
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._slots))
        for r_i, slot in enumerate(self._slots):
            cells = [slot.npc, slot.wants,
                     str(slot.count) if slot.supported else "—",
                     self._suggested_text(slot)]
            for c_i, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setToolTip(self._cell_tip(slot, c_i))
                if not slot.supported and c_i == 0:
                    it.setForeground(QColor("#8a849f"))
                self._table.setItem(r_i, c_i, it)

    @staticmethod
    def _suggested_text(slot) -> str:
        if not slot.supported or not slot.candidates:
            return "—"
        names = [c.name for c in slot.candidates[:5]]
        if slot.count > 5:
            names.append(f"+{slot.count - 5} more")
        return ", ".join(names)

    def _cell_tip(self, slot, c_i: int) -> str:
        if not slot.supported:
            return _wt(f"{slot.npc}\n{slot.unlock_note}")
        lines = [f"{slot.npc} — {slot.wants}",
                 f"Unlock: {slot.unlock_note}",
                 f"Qualifying now: {slot.count}"]
        if slot.candidates:
            lines.append("Give away first (weakest → strongest):")
            for cat in slot.candidates[:10]:
                base = sum(getattr(cat, "base_stats", {}).values())
                age = getattr(cat, "age", "?")
                lines.append(f"  • {cat.name}  (stats {base}, age {age})")
        return _wt("\n".join(lines))
