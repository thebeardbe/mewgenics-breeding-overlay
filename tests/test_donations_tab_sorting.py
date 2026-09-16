"""Regression: donations row actions must resolve the cat from the row's key.

The Donations table has sorting enabled. A view row is a position in the
*sorted view*, not an index into ``slot.candidates``. The pre-fix code used
``slot.candidates[index.row()]``, so after any header click the pin action and
"Show in game" acted on a different cat than the one on screen. Each rendered
row now stores its cat's ``db_key`` (``_CAT_KEY_ROLE``) and
``DonationsTab._cat_for_item`` resolves the cat from that key, falling back to
the row number only for rows built outside ``_render_slot``.

Sorting also moved off the display text: a pinned cat's Cat cell reads
``📌 Name`` but carries the bare lower-cased name as its raw sort value
(``_SORT_ROLE``), so the marker can no longer push it out of alphabetical
order. The action tests below therefore locate a cat by finding the row that
actually holds it, never by assuming a row number the marker might change.

These tests build a real offscreen ``DonationsTab``, render real rows through
``_on_npc_selected`` -> ``_render_slot``, sort the real table, and drive the
row menu with a recording ``QMenu`` replacement (the real ``exec`` blocks), so
no save, report or ``PaletteWindow`` is needed.
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QTableWidgetItem,
    QWidget,
)

from mewgenics_overlay.ui import donations_tab as _dt  # noqa: E402
from mewgenics_overlay.ui.donations_tab import DonationsTab  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeMenu:
    """Scriptable QMenu stand-in: records action texts, returns a choice."""

    def __init__(self, controller, parent=None):
        self.controller = controller
        self.parent = parent
        self.actions = []
        controller.menus.append(self)

    def addAction(self, text):
        action = SimpleNamespace(text=text)
        self.actions.append(action)
        return action

    def exec(self, global_pos):                       # noqa: A003 (Qt API)
        self.controller.last_pos = global_pos
        if isinstance(self.controller.choice, int) and self.actions:
            return self.actions[self.controller.choice]
        return None


@pytest.fixture
def fake_menu(monkeypatch):
    """Replace the tab module's QMenu with a recording, non-blocking fake."""
    controller = SimpleNamespace(menus=[], choice=None, last_pos=None)

    def factory(parent=None):
        return _FakeMenu(controller, parent)

    monkeypatch.setattr(_dt, "QMenu", factory)
    return controller


class _FakePalette:
    """The row-menu surface the tab reads from ``palette``."""

    def __init__(self, available=True):
        self.in_game_available = available
        self.pins = []
        self.shown = []
        self._session = None

    def set_pinned(self, cat, on):
        self.pins.append((cat, on))

    def show_in_game(self, key):
        self.shown.append(key)
        return True


#: Widgets built by a test, destroyed before the next one so Qt never has to
#: tear a shown table down at interpreter shutdown.
_WIDGETS: list[QWidget] = []


@pytest.fixture(autouse=True)
def _destroy_widgets(qapp):
    yield
    while _WIDGETS:
        widget = _WIDGETS.pop()
        widget.hide()
        widget.close()
        widget.deleteLater()
    qapp.processEvents()


# ── fixtures / helpers ─────────────────────────────────────────────────────
def make_cat(name, db_key, *, age=3):
    base = {s: 4 for s in ("STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK")}
    return SimpleNamespace(
        name=name, db_key=db_key, is_pinned=False, must_breed=False, age=age,
        base_stats=dict(base), total_stats=dict(base), status="In House",
        room="Attic", gender="female", abilities=[], stat_mod=[],
        defects=[], disorders=[], visual_mutation_entries=[], lovers=[],
        children=[], has_adventured=lambda: False,
    )


def make_slot(cats, npc="Tink"):
    advice = [SimpleNamespace(give=[], keep=[], keep_for_breeding=False)
              for _ in cats]
    return SimpleNamespace(npc=npc, wants="kittens", count=len(cats),
                           candidates=list(cats), advice=advice,
                           supported=True, active=True)


def _three_cats():
    """Candidate (report) order deliberately differs from alphabetical order."""
    return [make_cat("Charlie", 30, age=3),
            make_cat("Alpha", 10, age=1),
            make_cat("Bravo", 20, age=2)]


def _new_tab(qapp, palette):
    tab = DonationsTab(palette=palette)
    _WIDGETS.append(tab)
    tab.resize(900, 400)
    tab.show()
    qapp.processEvents()
    return tab


def _render(tab, slot):
    """Install ``slot`` and render it the way the real tab does."""
    tab._slots = [slot]
    tab._combo.blockSignals(True)
    tab._combo.clear()
    tab._combo.addItem(slot.npc)
    tab._combo.setCurrentIndex(0)
    tab._combo.blockSignals(False)
    tab._on_npc_selected(0)


def _action_on_sorted_row(qapp, fake_menu, candidates, order, view_row, action,
                          available=True):
    """Render + sort, then invoke a row action on the item at ``view_row``.

    Returns ``(shown_name, palette, slot, item)`` where ``shown_name`` is the
    cat name the user sees in that row and ``palette`` recorded what the
    action did.
    """
    palette = _FakePalette(available=available)
    tab = _new_tab(qapp, palette)
    slot = make_slot(candidates)
    _render(tab, slot)
    tab._table.sortItems(0, order)
    qapp.processEvents()
    item = tab._table.item(view_row, 0)
    assert item is not None, f"no item at view row {view_row}"
    shown = _strip_pin(item.text())                  # drop the pin marker
    fake_menu.choice = 0 if action == "pin" else 1
    tab._show_row_menu(tab._table.visualItemRect(item).center())
    return shown, palette, slot, item


def _strip_pin(text):
    """Display text with the pinned-cat marker removed."""
    return text.replace("\U0001f4cc ", "")


def _view_texts(tab):
    """Raw Cat-cell display text of every row, top to bottom as shown."""
    return [tab._table.item(r, 0).text()
            for r in range(tab._table.rowCount())]


def _row_holding(tab, name):
    """The view row currently showing ``name`` (marker-insensitive).

    Sorting defines where a row ends up, so a test that cares about a cat must
    ask the real view which row holds it instead of assuming an index.
    """
    for row, text in enumerate(_view_texts(tab)):
        if _strip_pin(text) == name:
            return row
    raise AssertionError(f"{name!r} is not in the view: {_view_texts(tab)}")


# ── core regression: the action follows the row the user clicked ───────────
def test_view_row_differs_from_candidate_index_once_sorted(qapp, fake_menu):
    """The fixture really reorders: sorting makes the old lookup wrong."""
    shown, palette, slot, item = _action_on_sorted_row(
        qapp, fake_menu, _three_cats(), Qt.SortOrder.AscendingOrder, 0, "show")
    assert shown == "Alpha"
    # Pre-fix resolution ``slot.candidates[item.row()]`` names a different cat.
    assert item.row() == 0
    assert slot.candidates[0].name == "Charlie"


def test_pin_targets_shown_cat_after_ascending_sort(qapp, fake_menu):
    shown, palette, slot, item = _action_on_sorted_row(
        qapp, fake_menu, _three_cats(), Qt.SortOrder.AscendingOrder, 0, "pin")
    assert shown == "Alpha"
    expected = next(c for c in slot.candidates if c.name == "Alpha")
    assert palette.pins == [(expected, True)]
    assert palette.shown == []


def test_show_in_game_targets_shown_cat_after_ascending_sort(qapp, fake_menu):
    shown, palette, slot, item = _action_on_sorted_row(
        qapp, fake_menu, _three_cats(), Qt.SortOrder.AscendingOrder, 0, "show")
    assert shown == "Alpha"
    expected = next(c for c in slot.candidates if c.name == "Alpha")
    assert palette.shown == [expected.db_key]
    assert palette.pins == []


def test_show_in_game_targets_shown_cat_after_descending_sort(qapp, fake_menu):
    # Descending order is Charlie, Bravo, Alpha; view row 2 is Alpha, whose
    # candidate index is 1 (Alpha) but old lookup would use candidates[2]
    # (Bravo) - a cat that moved.
    shown, palette, slot, item = _action_on_sorted_row(
        qapp, fake_menu, _three_cats(), Qt.SortOrder.DescendingOrder, 2, "show")
    assert shown == "Alpha"
    assert slot.candidates[item.row()].name == "Bravo"
    expected = next(c for c in slot.candidates if c.name == "Alpha")
    assert palette.shown == [expected.db_key]


def test_pin_targets_shown_cat_after_descending_sort(qapp, fake_menu):
    shown, palette, slot, item = _action_on_sorted_row(
        qapp, fake_menu, _three_cats(), Qt.SortOrder.DescendingOrder, 1, "pin")
    assert shown == "Bravo"
    expected = next(c for c in slot.candidates if c.name == "Bravo")
    assert palette.pins == [(expected, True)]


def test_action_targets_shown_cat_when_sort_leaves_order_unchanged(
        qapp, fake_menu):
    # Already-alphabetical candidates: sorting cannot move a row, so the old
    # lookup happened to be right; the keyed path must stay right too.
    cats = [make_cat("Alpha", 10, age=1),
            make_cat("Bravo", 20, age=2),
            make_cat("Charlie", 30, age=3)]
    shown, palette, slot, item = _action_on_sorted_row(
        qapp, fake_menu, cats, Qt.SortOrder.AscendingOrder, 2, "show")
    assert shown == "Charlie"
    assert slot.candidates[item.row()].name == "Charlie"   # unchanged
    expected = next(c for c in slot.candidates if c.name == "Charlie")
    assert palette.shown == [expected.db_key]


def test_emulated_pre_fix_lookup_acts_on_the_wrong_cat(qapp, fake_menu,
                                                       monkeypatch):
    """Reproduce the reported bug in-test with the pre-fix resolution.

    Resolving by view row number on a sorted table must target the wrong cat;
    this is exactly the failure the keyed lookup fixes.
    """

    def _pre_fix_lookup(slot, item):
        row = item.row()
        return slot.candidates[row] if row < len(slot.candidates) else None

    monkeypatch.setattr(DonationsTab, "_cat_for_item",
                        staticmethod(_pre_fix_lookup))

    shown, palette, slot, item = _action_on_sorted_row(
        qapp, fake_menu, _three_cats(), Qt.SortOrder.AscendingOrder, 0, "show")
    assert shown == "Alpha"
    # The pre-fix code sent Charlie's key (candidate 0), not Alpha's.
    assert palette.shown == [30]
    assert palette.shown != [10]


# ── documented fallback for rows built outside _render_slot ────────────────
def _keyless_rows(tab, slot):
    """Install ``slot`` and hand-build rows with no stored cat key."""
    tab._slots = [slot]
    tab._combo.blockSignals(True)
    tab._combo.clear()
    tab._combo.addItem(slot.npc)
    tab._combo.setCurrentIndex(0)
    tab._combo.blockSignals(False)
    tab._table.setSortingEnabled(False)
    tab._table.setRowCount(len(slot.candidates) + 1)   # +1 out-of-range row
    for r, cat in enumerate(slot.candidates):
        tab._table.setItem(r, 0, QTableWidgetItem(cat.name))
    tab._table.setItem(len(slot.candidates), 0, QTableWidgetItem("Ghost"))
    tab.show()
    tab._table.show()
    return tab


def test_keyless_row_falls_back_to_its_position_and_logs(qapp, fake_menu,
                                                         caplog):
    cats = [make_cat("One", 1, age=1), make_cat("Two", 2, age=2)]
    slot = make_slot(cats)
    palette = _FakePalette()
    tab = _new_tab(qapp, palette)
    _keyless_rows(tab, slot)
    qapp.processEvents()
    item = tab._table.item(1, 0)
    assert item.data(_dt._CAT_KEY_ROLE) is None

    fake_menu.choice = 0
    with caplog.at_level(logging.WARNING, logger="mewgenics_overlay.ui"):
        tab._show_row_menu(tab._table.visualItemRect(item).center())

    assert palette.pins == [(cats[1], True)]        # resolved by row number
    assert any("falling back" in r.getMessage() for r in caplog.records)


def test_keyless_row_out_of_range_resolves_to_none(qapp, fake_menu):
    cats = [make_cat("One", 1, age=1), make_cat("Two", 2, age=2)]
    slot = make_slot(cats)
    palette = _FakePalette()
    tab = _new_tab(qapp, palette)
    _keyless_rows(tab, slot)
    qapp.processEvents()
    ghost = tab._table.item(len(cats), 0)

    assert DonationsTab._cat_for_item(slot, ghost) is None

    fake_menu.choice = 0
    tab._show_row_menu(tab._table.visualItemRect(ghost).center())

    assert palette.pins == []
    assert palette.shown == []


# ── "Show in game" gating stays tied to the connection ─────────────────────
def test_show_in_game_offered_when_a_game_is_connected(qapp, fake_menu):
    palette = _FakePalette(available=True)
    tab = _new_tab(qapp, palette)
    _render(tab, make_slot(_three_cats()))
    qapp.processEvents()
    item = tab._table.item(0, 0)

    tab._show_row_menu(tab._table.visualItemRect(item).center())

    assert [a.text for a in fake_menu.menus[0].actions] == [
        "Pin for breeding", "Show in game"]


def test_show_in_game_omitted_when_no_game_is_connected(qapp, fake_menu):
    palette = _FakePalette(available=False)
    tab = _new_tab(qapp, palette)
    _render(tab, make_slot(_three_cats()))
    qapp.processEvents()
    item = tab._table.item(0, 0)

    tab._show_row_menu(tab._table.visualItemRect(item).center())

    assert [a.text for a in fake_menu.menus[0].actions] == ["Pin for breeding"]


# ── pinned cats sort by name, not by the marker (the reported bug) ─────────
def test_pinned_cat_keeps_its_alphabetical_place_ascending(qapp):
    """A 📌 marker must not move a cat in the ascending order."""
    cats = _three_cats()
    next(c for c in cats if c.name == "Bravo").is_pinned = True
    tab = _new_tab(qapp, _FakePalette())
    _render(tab, make_slot(cats))

    tab._table.sortItems(0, Qt.SortOrder.AscendingOrder)
    qapp.processEvents()

    texts = _view_texts(tab)
    assert [_strip_pin(t) for t in texts] == ["Alpha", "Bravo", "Charlie"]
    # The pinned cat still shows its marker in its alphabetical place.
    assert texts[1].startswith("\U0001f4cc ")
    assert not texts[0].startswith("\U0001f4cc ")
    assert not texts[2].startswith("\U0001f4cc ")


def test_pinned_cat_keeps_its_alphabetical_place_descending(qapp):
    """Descending order reverses the names, still ignoring the marker."""
    cats = _three_cats()
    next(c for c in cats if c.name == "Bravo").is_pinned = True
    tab = _new_tab(qapp, _FakePalette())
    _render(tab, make_slot(cats))

    tab._table.sortItems(0, Qt.SortOrder.DescendingOrder)
    qapp.processEvents()

    texts = _view_texts(tab)
    assert [_strip_pin(t) for t in texts] == ["Charlie", "Bravo", "Alpha"]
    assert texts[1].startswith("\U0001f4cc ")


def test_age_column_sorts_by_its_raw_value_not_the_display_text(qapp):
    """Age compares numerically; an unknown age ('?') sorts last ascending.

    Text order would put "10" before "2"; the raw value keeps 2 first. This
    pins the other-column half of the fix.
    """
    cats = [make_cat("Alpha", 10, age=10),
            make_cat("Bravo", 20, age="?"),
            make_cat("Charlie", 30, age=2)]
    tab = _new_tab(qapp, _FakePalette())
    _render(tab, make_slot(cats))

    tab._table.sortItems(2, Qt.SortOrder.AscendingOrder)
    qapp.processEvents()

    assert [_strip_pin(t) for t in _view_texts(tab)] == [
        "Charlie", "Alpha", "Bravo"]


# ── row actions follow the pinned cat wherever sorting put it ─────────────
def test_show_in_game_on_sorted_row_still_sends_after_pin_state_check(
        qapp, fake_menu):
    """A pinned cat's label flips, but "Show in game" keeps its index/key.

    The row is located by name rather than assumed: the assertion is about
    which cat the row action resolves, not about how a marker collates.
    """
    cats = _three_cats()
    pinned = next(c for c in cats if c.name == "Alpha")
    pinned.is_pinned = True
    palette = _FakePalette()
    tab = _new_tab(qapp, palette)
    slot = make_slot(cats)
    _render(tab, slot)
    tab._table.sortItems(0, Qt.SortOrder.AscendingOrder)
    qapp.processEvents()

    row = _row_holding(tab, "Alpha")
    item = tab._table.item(row, 0)
    assert item.text().startswith("\U0001f4cc ")   # display keeps the marker
    fake_menu.choice = 1
    tab._show_row_menu(tab._table.visualItemRect(item).center())

    assert [a.text for a in fake_menu.menus[0].actions] == [
        "Unpin - allow donation", "Show in game"]
    assert palette.shown == [pinned.db_key]


def test_pin_action_targets_the_pinned_cat_in_its_sorted_row(qapp, fake_menu):
    """Unpinning targets the cat whose marked cell was clicked."""
    cats = _three_cats()
    pinned = next(c for c in cats if c.name == "Bravo")
    pinned.is_pinned = True
    palette = _FakePalette()
    tab = _new_tab(qapp, palette)
    slot = make_slot(cats)
    _render(tab, slot)
    tab._table.sortItems(0, Qt.SortOrder.DescendingOrder)
    qapp.processEvents()

    row = _row_holding(tab, "Bravo")
    item = tab._table.item(row, 0)
    fake_menu.choice = 0
    tab._show_row_menu(tab._table.visualItemRect(item).center())

    assert [a.text for a in fake_menu.menus[0].actions] == [
        "Unpin - allow donation", "Show in game"]
    assert palette.pins == [(pinned, False)]
    assert palette.shown == []
