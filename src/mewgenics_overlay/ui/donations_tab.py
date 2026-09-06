"""Donations tab: pick an NPC from a dropdown, see its candidates + why."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.core.donations import donation_report, recommendation_lines
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt

_COLS = ["Cat", "Age", "Stats", "Why donate"]


class DonationsTab(QWidget):
    """Pick which NPC you're feeding, see today's candidates with reasons."""

    def __init__(self, palette=None):
        super().__init__()
        self._palette = palette
        self._slots = []
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

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for i, w in enumerate([170, 50, 80, 380]):
            self._table.setColumnWidth(i, w)
        root.addWidget(self._table, 1)

        self._hint = QLabel("")
        self._hint.setObjectName("muted")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

    # ── data ───────────────────────────────────────────────────────────────
    def refresh(self, session) -> None:
        cats = session.alive if session is not None else []
        flags = getattr(session, "npc_progress_flags", set()) \
            if session is not None else set()
        self._slots = donation_report(cats, active=flags) if cats else []
        self._rebuild_combo()

    def _rebuild_combo(self) -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        for slot in self._slots:
            label = slot.npc
            if slot.supported:
                label += f"  ({slot.count} now)"
                if not slot.active:
                    label += " · locked"
            else:
                label += "  (unsupported)"
            self._combo.addItem(label)
            self._combo.setItemData(self._combo.count() - 1, slot, 0x0100)
        self._combo.blockSignals(False)
        if self._slots:
            self._combo.setCurrentIndex(0)
            self._on_npc_selected(0)
        else:
            self._summary.setText("No save loaded yet.")
            self._table.setRowCount(0)

    def _current_slot(self):
        i = self._combo.currentIndex()
        return self._slots[i] if 0 <= i < len(self._slots) else None

    def _on_npc_selected(self, index: int) -> None:
        slot = self._current_slot()
        if slot is None:
            self._table.setRowCount(0)
            self._hint.setText("")
            return
        if not slot.supported:
            self._table.setRowCount(0)
            self._hint.setText(f"{slot.npc}: {slot.unlock_note}")
            return
        status = "this NPC is active" if slot.active \
            else "locked — not unlocked in your game yet"
        self._summary.setText(f"{slot.wants} · {slot.count} qualifying · {status}")
        self._render_slot(slot)

    def _render_slot(self, slot) -> None:
        self._table.setRowCount(0)
        cats = slot.candidates
        self._table.setRowCount(len(cats))
        for r_i, cat in enumerate(cats):
            base = sum(getattr(cat, "base_stats", {}).values())
            inj = sum(1 for s, v in (getattr(cat, "base_stats", {}) or {}).items()
                      if (getattr(cat, "total_stats", {}) or {}).get(s, v) < v)
            why = " · ".join(recommendation_lines(cat))
            cells = [cat.name, str(getattr(cat, "age", "?")), str(base), why]
            for c_i, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setToolTip(self._row_tip(cat, slot, base, inj))
                self._table.setItem(r_i, c_i, it)
        self._hint.setText(
            "Ordered weakest → strongest (the game takes any of them). "
            "Pinned and must-breed cats are pushed to the bottom."
        )

    @staticmethod
    def _row_tip(cat, slot, base, injured) -> str:
        lines = [f"{cat.name}  ({getattr(cat, 'gender', '?')})",
                 f"Stats: {base} · age {getattr(cat, 'age', '?')}",
                 f"Suitable for: {slot.npc} ({slot.wants})"]
        why = recommendation_lines(cat)
        if why:
            lines.append("Why:")
            lines += [f"  • {line}" for line in why]
        if injured:
            lines.append(f"  • {injured} stat(s) with an injury penalty")
        room = getattr(cat, "room", "") or getattr(cat, "status", "")
        lines.append(f"Where: {room or '?'}")
        return _wt("\n".join(lines))
