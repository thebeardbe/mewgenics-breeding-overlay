"""TableCoordinator - focus, partner rows, sorting and the best-match banner.

Extracted from ``PaletteWindow`` (god-file split): owns the focused cat, the
partner-table rendering and its tri-state sort, the focus panel refresh, the
search-box focus action and the ⭐ best-match updates.

Window-agnostic: the widgets it drives (table, focus panel, search box,
best-match bar, room bar, detail strip) arrive from the host, as do the live
session and gpak-asset getters, a ``schedule()`` callback (queue partner
ranking for the focused cat) and an ``on_selected()`` callback (a row was
selected, refresh the detail strip). Nothing here reaches into
``PaletteWindow``.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QLabel

from mewgenics_overlay.core.maladies import defect_effect_text
from mewgenics_overlay.core.session import Cat, Session
from mewgenics_overlay.ui.bestmatch import BestMatchBar
from mewgenics_overlay.ui.focuspanel import FocusedCatPanel
from mewgenics_overlay.ui.partnertable import PartnerTableWidget
from mewgenics_overlay.ui.roombar import RoomBar
from mewgenics_overlay.ui.searchbox import SearchBox


class TableCoordinator(QObject):
    """Focus + partner-table behaviour for the overlay window.

    ``schedule`` queues the partner ranking for the focused cat (the host owns
    the view settings it reads) and ``on_selected`` refreshes the inheritance
    detail strip after a row is picked.
    """

    def __init__(
        self,
        table: PartnerTableWidget,
        focus_panel: FocusedCatPanel,
        searchbox: SearchBox,
        best_bar: BestMatchBar,
        room_bar: RoomBar,
        detail: QLabel,
        session_getter: Callable[[], Optional[Session]],
        assets_getter: Callable[[], object],
        schedule: Callable[[], None],
        on_selected: Callable[[], None],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._table = table
        self._focus_panel = focus_panel
        self._searchbox = searchbox
        self._best_bar = best_bar
        self._room_bar = room_bar
        self._detail = detail
        self._session_getter = session_getter
        self._assets_getter = assets_getter
        self._schedule = schedule
        self._on_selected = on_selected
        self._focus: Optional[Cat] = None

    # ── state (read-only, for the host's own wiring) ───────────────────────
    @property
    def focus(self) -> Optional[Cat]:
        return self._focus

    @property
    def rows(self) -> list:
        return self._table.rows

    @property
    def sort_col(self) -> Optional[int]:
        return self._table.sort_col

    @property
    def sort_dir(self) -> str:
        return self._table.sort_dir

    # ── focus ──────────────────────────────────────────────────────────────
    def set_focus_key(self, db_key: int) -> None:
        """Programmatic focus (used by the future in-game bridge)."""
        session = self._session_getter()
        if session is None:
            return
        cat = session.by_key.get(db_key)
        if cat is not None:
            self.set_focus(cat)

    def set_focus(self, cat: Cat) -> None:
        self._focus = cat
        self._searchbox.set_text_silently("")
        self._searchbox.clear_focus()
        self.show_focus(cat)
        self._schedule()

    def clear_focus(self) -> None:
        self._focus = None
        self._focus_panel.clear()
        self._best_bar.clear()
        # Drop the stored rows too: the table is emptied here, and a later
        # refresh_theme() must not redraw stale partners for a cleared focus.
        self._table.set_rows([])
        # No sort state (or its header arrow) survives a cleared view: the
        # table never redraws while empty, so a theme switch would otherwise
        # keep showing a stale sort indicator.
        self._table.reset_sort()
        self._table.show_sort_indicator()
        self._table.setRowCount(0)
        self._searchbox.set_text_silently("")
        self._detail.setText("Select a partner row for inheritance detail.")

    def has_content(self) -> bool:
        """True when a cat is focused or partner rows are rendered."""
        return self._focus is not None or self._table.rowCount() > 0

    def clear_if_content(self) -> None:
        """Clear the focus/table only when there is something to clear."""
        if self.has_content():
            self.clear_focus()

    def show_focus(self, cat: Cat) -> None:
        """Fill the focused-cat card, with gpak effect text when available."""
        assets = self._assets_getter()

        def _gpak_effect(group_key, mutation_id):
            return assets.effect_for(group_key, mutation_id) \
                if assets is not None else ""
        self._focus_panel.show_cat(cat, effect_for=_gpak_effect)

    def adopt_session(self, sess: Optional[Session]) -> None:
        """Keep focus on the same cat when the session is rebuilt.

        An unloaded session (``None``) clears the focus: a stale cat key must
        not keep driving partner scheduling.
        """
        focus = None
        if sess is not None and self._focus is not None:
            focus = sess.by_key.get(self._focus.db_key)
        self._focus = focus
        if self._focus is None:
            self.clear_focus()
        else:
            self.show_focus(self._focus)
            self._schedule()

    # ── search-box actions ─────────────────────────────────────────────────
    def on_search_chosen(self, db_key: int) -> None:
        """A dropdown result was picked: focus that cat."""
        session = self._session_getter()
        if session is None:
            return
        cat = session.by_key.get(db_key)
        if cat is not None:
            self.set_focus(cat)

    # ── partners table ─────────────────────────────────────────────────────
    def recompute_partners(self) -> None:
        if self._focus is not None:
            self._schedule()

    def render_partners(self, rows: list) -> None:
        """Store computed partner rows and redraw in the current sort order."""
        self._table.set_rows(list(rows))
        self._table.reset_sort()   # new data -> engine's safe-first order
        self.redraw_table()
        self.update_best()

    def redraw_table(self) -> None:
        """Render the partner rows via PartnerTableWidget."""
        self._table.redraw(
            stimulation=self._room_bar.stim_value(),
            comfort=self._room_bar.comfort_value(),
            effect_of=self.effect_for_name
            if self._assets_getter() is not None else None)

    def on_header_clicked(self, col: int) -> None:
        """Tri-state sort: asc -> desc -> back to default order."""
        self._table.toggle_sort(col)
        self.redraw_table()

    def effect_for_name(self, a, b, name: str) -> str:
        """Look up the first known gpak effect for a defect carried by a/b."""
        assets = self._assets_getter()
        for cat in (a, b):
            text = defect_effect_text(assets, cat, name)
            if text:
                return text
        return ""

    # ── best match ─────────────────────────────────────────────────────────
    def update_best(self) -> None:
        """Delegate the ⭐ banner to BestMatchBar - it reads the live rows,
        focus and room Stimulation/Comfort through its wired callables."""
        self._best_bar.update_best()

    def on_best_selected(self, db_key: int) -> None:
        """The ⭐ pick was clicked: select and reveal that partner row."""
        for ri in range(self._table.rowCount()):
            it = self._table.item(ri, 0)
            data = it.data(Qt.ItemDataRole.UserRole) if it else None
            if data and data[0].partner.db_key == db_key:
                self._table.setCurrentCell(ri, 0)
                self._table.scrollToItem(it)
                self._on_selected()
                return

    # ── theme re-render ────────────────────────────────────────────────────
    def refresh_theme(self) -> None:
        """Repaint the rows and the focus card for the active theme."""
        if self.rows:
            self.redraw_table()
        if self._focus is not None:
            self.show_focus(self._focus)
