"""PartnerActions - what a partner row does when you click it.

Extracted from ``PaletteWindow`` (god-file split): selection (the inheritance
detail strip under the table), the right-click pin/unpin menu and the
double-click "analyse from this cat instead" action.

Window-agnostic: the table widget and the detail label arrive as objects, and
everything else - the active room Stimulation, the pair's malady lines, the
gpak effect lookup, pinning and focusing a cat - arrives as a callable from
the host. Rendering the table stays in ``partnertable.py``; this module only
reacts to row interaction. No reference to ``PaletteWindow``.

Selection copy, menu wording and behaviour are unchanged.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QLabel, QMenu

from mewgenics_overlay.core.recommend import better_stat_expectation
from mewgenics_overlay.core.session import STAT_NAMES
from .partnertable import PartnerTableWidget

# Callback signatures, for readability at the call sites.
StimValue = Callable[[], float]
MaladyLines = Callable[[object, float, Callable[[object, object, str], str]],
                       list]
EffectOf = Callable[[object, object, str], str]
PinCat = Callable[[object, bool], None]
FocusCat = Callable[[object], None]


def _pin_action_label(cat) -> str:
    """Right-click menu text for a partner's current pin state."""
    return ("Unpin - allow donation" if getattr(cat, "is_pinned", False)
            else "Pin for breeding")


def _partner_detail_text(row, kids, stimulation: float,
                         malady_lines: MaladyLines,
                         effect_of: EffectOf) -> str:
    """The inheritance detail strip for one selected partner row.

    Pure text building (no widgets) so the wording stays in one place: the
    per-stat ranges, the "takes the higher parent" expectation, existing
    kittens, the block reason and the pair's malady inheritance lines.
    """
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
        better = better_stat_expectation(row, stimulation)
        # Only show the expectation when the parents actually differ somewhere:
        # with every range flat the count is 0 and the line would read
        # "≈0.0 of 0 differing stats".
        if better and better[1]:
            text += (f"\nThe kitten takes the higher of the two parents' "
                     f"values in ≈{better[0]:.1f} of "
                     f"{better[1]} differing stats")
        if kids:
            text += f"   ·   existing kittens: {', '.join(kids)}"
    else:
        text = head + f"\nCan't breed: {row.reason or 'blocked'}"
    malady = malady_lines(row, stimulation, effect_of)
    if malady:
        text += "\n" + "\n".join(malady)
    return text


class PartnerActions(QObject):
    """Row-interaction handlers for the partners table.

    ``table`` and ``detail`` are the widgets to read/write; ``stim_value``,
    ``malady_lines`` and ``effect_of`` supply the live game data, while
    ``on_pin`` and ``on_focus`` are the host actions this unit triggers.
    """

    def __init__(
        self,
        table: PartnerTableWidget,
        detail: QLabel,
        stim_value: StimValue,
        malady_lines: MaladyLines,
        effect_of: EffectOf,
        on_pin: PinCat,
        on_focus: FocusCat,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._table = table
        self._detail = detail
        self._stim_value = stim_value
        self._malady_lines = malady_lines
        self._effect_of = effect_of
        self._on_pin = on_pin
        self._on_focus = on_focus

    def on_partner_selected(self) -> None:
        """Selection changed: show the inheritance detail for that row."""
        item = self._table.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        row, kids = data
        self._detail.setText(_partner_detail_text(
            row, kids, self._stim_value(), self._malady_lines,
            self._effect_of))

    def show_breeding_menu(self, pos) -> None:
        """Right-click a partner row to pin/unpin them as a keeper."""
        item = self._table.itemAt(pos)
        if item is None:
            return
        name_item = self._table.item(item.row(), 0)
        data = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
        if not data:
            return
        partner = data[0].partner
        menu = QMenu(self._table)
        action = menu.addAction(_pin_action_label(partner))
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is action:
            self._on_pin(partner, not getattr(partner, "is_pinned", False))

    def on_partner_double(self, item) -> None:
        """Double-click: analyse breeding from the partner's side instead."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            row, _ = data
            self._on_focus(row.partner)
