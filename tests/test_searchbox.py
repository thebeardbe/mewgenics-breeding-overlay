"""SearchBox: dropdown contents, choose/clear callbacks and focus behaviour.

``mewgenics_overlay.ui.searchbox.SearchBox`` was extracted out of
``PaletteWindow`` (god-file split, step 1) and now takes the live session plus
the "chosen" / "cleared" actions as plain callables. These tests drive the
widget with a small fake session (only ``.alive`` and ``.search``) so they do
not need a real save.

Focus notes
-----------
The PySide6 build this repo resolves through nixpkgs (6.11.x) exposes the
focus reasons on ``Qt.FocusReason`` (there is no ``QEvent.FocusReason``), and
production reads them there. A FocusIn carrying an explicit reason (mouse,
Tab or shortcut) opens the dropdown; window activation alone does not.
``test_focus_in_with_explicit_reason_opens_the_dropdown`` drives that path.

Host-side clear guard
---------------------
``PaletteWindow._on_search_cleared`` only resets the table when a cat is
focused or rows are still on screen. The SearchBox-level tests cover the
widget half; ``test_host_clear_guard_resets_only_when_focus_or_rows`` covers
the guard itself with a light stand-in for the host, so no ``PaletteWindow``
is ever constructed here.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QFocusEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui.palette import PaletteWindow  # noqa: E402
from mewgenics_overlay.ui.searchbox import (  # noqa: E402
    _RESULTS_SHOWN,
    _SEARCH_LIMIT,
    SearchBox,
)

_DB_KEY = Qt.ItemDataRole.UserRole


# ── fakes / helpers ────────────────────────────────────────────────────────
def make_cat(name, db_key, gender="female", status="In House", room="Attic",
             stats=None):
    """Minimal duck-typed stand-in for a parsed Cat (fields the widget reads)."""
    return SimpleNamespace(
        name=name,
        db_key=db_key,
        gender=gender,
        status=status,
        room=room,
        base_stats=stats if stats is not None else {
            "STR": 1, "DEX": 2, "CON": 3, "INT": 4,
            "SPD": 5, "CHA": 6, "LCK": 7},
    )


class FakeSession:
    """Only the two members SearchBox uses: ``alive`` and ``search``."""

    def __init__(self, alive=(), search_results=None):
        self.alive = list(alive)
        self._search_results = search_results
        self.search_calls = []

    def search(self, text, limit=40):
        self.search_calls.append((text, limit))
        if self._search_results is not None:
            return list(self._search_results)[:limit]
        needle = text.strip().lower()
        hits = [c for c in self.alive if needle in c.name.lower()]
        return hits[:limit]


def arrow_action(box):
    """The trailing 'Show all cats' QAction added to the line edit."""
    for action in box.edit.actions():
        if action.toolTip() == "Show all cats":
            return action
    raise AssertionError("dropdown arrow action not found on the search edit")


def focus_edit(box, qapp):
    """Give the edit an explicit-reason FocusIn (active-window focus is not
    'explicit' for the widget, so the auto-open path would not run)."""
    box.edit.clearFocus()
    qapp.processEvents()
    box.edit.setFocus(Qt.FocusReason.MouseFocusReason)
    qapp.processEvents()


def visible_to(box):
    """True when the list was told to show, even if the box itself is hidden."""
    return box.list.isVisibleTo(box)


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_box(qapp):
    """Build an isolated SearchBox; returns (box, chosen, cleared) recorders."""
    widgets = []

    def _make(session):
        chosen = []
        cleared = []
        box = SearchBox(lambda: session, chosen.append,
                        lambda: cleared.append(True))
        widgets.append(box)
        return box, chosen, cleared

    yield _make
    for box in widgets:
        box.edit.clearFocus()          # drop focus before the widget dies
        box.hide()
        box.close()
        box.deleteLater()
    qapp.processEvents()


# ── 1. empty query lists every alive cat ───────────────────────────────────
def test_dropdown_lists_every_alive_cat_sorted_by_name(make_box):
    cats = [make_cat("Zoe", 3), make_cat("alpha", 1), make_cat("Meeko", 2)]
    session = FakeSession(cats)
    box, chosen, cleared = make_box(session)

    arrow_action(box).trigger()

    assert visible_to(box)
    assert box.list.count() == 3
    keys = [box.list.item(i).data(_DB_KEY) for i in range(box.list.count())]
    assert keys == [1, 2, 3]              # alpha, Meeko, Zoe (case-insensitive)
    assert session.search_calls == []     # empty query is not a search
    assert chosen == [] and cleared == []


def test_dropdown_row_renders_location_gender_and_stat_sum(make_box):
    cat = make_cat("Meeko", 7, gender="male", status="In House", room="Attic")
    box, _, _ = make_box(FakeSession([cat]))

    arrow_action(box).trigger()

    text = box.list.item(0).text()
    assert text.startswith("Meeko")
    assert "Attic" in text
    assert "male" in text
    assert "sum 28" in text           # 1+2+3+4+5+6+7


def test_unicode_name_is_rendered_verbatim(make_box):
    box, _, _ = make_box(FakeSession([make_cat("Mîlø 😺", 9)]))

    arrow_action(box).trigger()

    assert box.list.item(0).text().startswith("Mîlø 😺")
    assert box.list.item(0).data(_DB_KEY) == 9


# ── 1b. choosing a result fires on_choose exactly once ─────────────────────
@pytest.mark.parametrize("signal_name", ["itemClicked", "itemActivated"])
def test_picking_a_result_calls_on_choose_once_with_db_key(make_box,
                                                           signal_name):
    cats = [make_cat("Meeko", 11), make_cat("Bo", 22)]
    box, chosen, cleared = make_box(FakeSession(cats))
    arrow_action(box).trigger()

    getattr(box.list, signal_name).emit(box.list.item(0))   # "Bo"

    assert chosen == [22]
    assert cleared == []
    assert not visible_to(box)         # pick closes the dropdown


def test_return_key_picks_the_first_result(make_box):
    cats = [make_cat("Meeko", 11), make_cat("Bo", 22)]
    box, chosen, cleared = make_box(FakeSession(cats))
    arrow_action(box).trigger()

    box.edit.returnPressed.emit()

    assert chosen == [22]
    assert cleared == []
    assert not visible_to(box)


# ── 2. typing goes through session.search and truncates ────────────────────
def test_typing_filters_through_session_search_and_adds_more_row(make_box):
    hits = [make_cat(f"Cat{i:03d}", i) for i in range(_RESULTS_SHOWN + 5)]
    session = FakeSession(search_results=hits)
    box, chosen, cleared = make_box(session)

    box.edit.setText("at")

    assert session.search_calls == [("at", _SEARCH_LIMIT)]
    assert visible_to(box)
    assert box.list.count() == _RESULTS_SHOWN + 1
    last = box.list.item(_RESULTS_SHOWN)
    assert "more matches" in last.text()
    assert last.flags() == Qt.ItemFlag.NoItemFlags
    # the first (_RESULTS_SHOWN) rows are real, keyed results
    assert box.list.item(_RESULTS_SHOWN - 1).data(_DB_KEY) == _RESULTS_SHOWN - 1
    assert chosen == [] and cleared == []


def test_exactly_the_row_cap_has_no_more_row(make_box):
    hits = [make_cat(f"Cat{i:03d}", i) for i in range(_RESULTS_SHOWN)]
    box, _, _ = make_box(FakeSession(search_results=hits))

    box.edit.setText("cat")

    assert box.list.count() == _RESULTS_SHOWN
    assert "more matches" not in box.list.item(_RESULTS_SHOWN - 1).text()
    assert box.list.item(_RESULTS_SHOWN - 1).flags() != Qt.ItemFlag.NoItemFlags


def test_empty_query_overflow_also_shows_more_row(make_box):
    cats = [make_cat(f"Cat{i:03d}", i) for i in range(_RESULTS_SHOWN + 3)]
    session = FakeSession(cats)
    box, _, _ = make_box(session)

    arrow_action(box).trigger()        # empty query -> uses session.alive

    assert box.list.count() == _RESULTS_SHOWN + 1
    assert "more matches" in box.list.item(_RESULTS_SHOWN).text()
    assert session.search_calls == []


def test_empty_query_never_calls_session_search(make_box):
    session = FakeSession([make_cat("Meeko", 1)])
    box, _, _ = make_box(session)

    box.edit.setText("x")              # non-empty -> search
    box.edit.setText("")               # back to empty -> roster, not search

    assert [call[0] for call in session.search_calls] == ["x"]


# ── 3. clear state machine ─────────────────────────────────────────────────
def test_manual_clear_while_focused_calls_on_clear_once(make_box, qapp):
    box, chosen, cleared = make_box(FakeSession([make_cat("Meeko", 1)]))
    box.show()
    qapp.processEvents()
    focus_edit(box, qapp)
    assert box.edit.hasFocus()

    box.edit.setText("Meeko")          # non-empty, must not clear
    assert cleared == []

    box.edit.clear()                   # manual clear while focused

    assert cleared == [True]
    assert chosen == []


def test_set_text_silently_does_not_call_on_clear_and_keeps_working(
        make_box, qapp):
    box, chosen, cleared = make_box(FakeSession([make_cat("Meeko", 1)]))
    box.show()
    qapp.processEvents()
    focus_edit(box, qapp)

    box.edit.setText("Meeko")
    box.set_text_silently("")

    assert cleared == []               # silent path never clears the table
    assert box.edit.text() == ""

    # suppression must be released again: a later manual clear still fires
    box.edit.setText("Bo")
    box.edit.clear()
    assert cleared == [True]


def test_manual_clear_when_not_focused_does_not_call_on_clear(make_box):
    box, _, cleared = make_box(FakeSession([make_cat("Meeko", 1)]))
    assert not box.edit.hasFocus()

    box.edit.setText("Meeko")
    box.edit.setText("")

    assert cleared == []


# ── 4. clear_focus ─────────────────────────────────────────────────────────
def test_clear_focus_removes_focus_from_the_edit(make_box, qapp):
    box, _, _ = make_box(FakeSession([make_cat("Meeko", 1)]))
    box.show()
    qapp.processEvents()
    focus_edit(box, qapp)
    assert box.edit.hasFocus()

    box.clear_focus()
    qapp.processEvents()

    assert not box.edit.hasFocus()


# ── 5. arrow action toggles, never clears ──────────────────────────────────
def test_arrow_toggles_dropdown_and_never_calls_on_clear(make_box, qapp):
    cats = [make_cat("Meeko", 1), make_cat("Bo", 2)]
    box, chosen, cleared = make_box(FakeSession(cats))
    box.show()                         # the toggle reads QWidget.isVisible()
    qapp.processEvents()

    arrow_action(box).trigger()
    assert visible_to(box)
    assert box.list.count() == 2

    arrow_action(box).trigger()
    assert not visible_to(box)

    arrow_action(box).trigger()
    assert visible_to(box)             # toggles back open

    assert chosen == [] and cleared == []


def test_focus_open_then_first_arrow_keeps_open_second_closes(
        make_box, qapp):
    cats = [make_cat("Meeko", 1), make_cat("Bo", 2)]
    box, chosen, cleared = make_box(FakeSession(cats))
    box.show()
    qapp.processEvents()
    focus_edit(box, qapp)              # explicit focus opened the list
    assert visible_to(box)

    arrow_action(box).trigger()        # a click that focused the box must not
    assert visible_to(box)             # immediately close it again

    arrow_action(box).trigger()
    assert not visible_to(box)

    assert chosen == [] and cleared == []


# ── negative paths ─────────────────────────────────────────────────────────
def test_arrow_with_empty_roster_does_not_open_an_empty_dropdown(make_box):
    # ``_fill_dropdown`` sets visibility from the result, so an empty roster
    # leaves the list hidden and the arrow handler must not force it open.
    box, _, cleared = make_box(FakeSession([]))

    arrow_action(box).trigger()

    assert not visible_to(box)
    assert box.list.count() == 0
    assert cleared == []


def test_typed_query_with_no_hits_hides_the_dropdown(make_box):
    session = FakeSession([make_cat("Meeko", 1)], search_results=[])
    box, _, _ = make_box(session)

    box.edit.setText("zzz")            # session.search finds nothing

    assert session.search_calls == [("zzz", _SEARCH_LIMIT)]
    assert not visible_to(box)
    assert box.list.count() == 0


def test_no_session_keeps_the_dropdown_hidden_and_does_not_raise(make_box):
    box, chosen, cleared = make_box(None)

    arrow_action(box).trigger()
    assert not visible_to(box)
    assert box.list.count() == 0

    box.edit.setText("anything")       # typing with no session must be safe
    assert not visible_to(box)
    assert chosen == [] and cleared == []


# ── regression: real focus path ────────────────────────────────────────────
def test_focus_in_with_explicit_reason_opens_the_dropdown(make_box):
    """Regression guard for the FocusIn event filter.

    A mouse/click focus must open the dropdown, reading ``Qt.FocusReason``
    (the enum the installed PySide6 actually exposes). The event is sent
    directly so the test never has to show (and activate) a window.
    """
    cats = [make_cat("Meeko", 1), make_cat("Bo", 2)]
    box, chosen, cleared = make_box(FakeSession(cats))

    QApplication.sendEvent(
        box.edit,
        QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason),
    )

    assert visible_to(box)
    assert box.list.count() == len(cats)
    assert chosen == [] and cleared == []


# ── 6. host-side clear guard ───────────────────────────────────────────────
class _FakePaletteHost:
    """The only state ``PaletteWindow._on_search_cleared`` reads."""

    def __init__(self, focus=None, rows=0):
        self._focus = focus
        self._table = SimpleNamespace(rowCount=lambda: rows)
        self.clears = 0

    def _clear_focus(self):
        self.clears += 1


@pytest.mark.parametrize("focus,rows,expected", [
    (None, 0, 0),        # nothing to reset -> table left alone
    (object(), 0, 1),    # a cat is focused -> reset
    (None, 3, 1),        # stale rows on screen -> reset
])
def test_host_clear_guard_resets_only_when_focus_or_rows(focus, rows,
                                                         expected):
    """The thin host guard on top of SearchBox's on_clear callback.

    Called unbound against a stand-in host: the method touches only
    ``_focus``, ``_table.rowCount()`` and ``_clear_focus()``, so this needs
    no PaletteWindow (and thus no save, watcher or timers).
    """
    host = _FakePaletteHost(focus=focus, rows=rows)

    PaletteWindow._on_search_cleared(host)

    assert host.clears == expected
