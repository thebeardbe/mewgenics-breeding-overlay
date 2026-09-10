"""``ui/tablectl.py`` TableCoordinator: focus, rows, sorting, best match.

The coordinator is window-agnostic: it drives real widgets (partner table,
focused-cat panel, search box, best-match bar, room bar, detail strip) and
reads the session and gpak assets through getters. These tests build those
real widgets offscreen and feed duck-typed cats plus fully-formed
``PartnerRow`` objects (with a hand-built projection and empty pre-computed
defect rows), so no save file and no gpak is needed.

``clear_focus`` note: it resets the focused cat, the focus card, the banner,
the detail strip, the search box and the tri-state table sort (column,
direction and header arrow), and it also drops the *stored* partner rows.
Dropping the stored rows matters because ``refresh_theme`` redraws whenever
``rows`` is non-empty, so a cleared focus must not leave stale partners for a
later theme switch to resurrect. ``adopt_session(None)`` clears the focus for
the same reason: an unloaded session must not keep a stale cat key driving
partner scheduling.

The search-clear guard lives here too now: ``clear_if_content`` (on top of
``has_content``) is the single entry point the SearchBox's on-clear callback
reaches through ``PaletteWindow._on_search_cleared``.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from mewgenics_overlay.core.kinship import Relation  # noqa: E402
from mewgenics_overlay.core.session import (  # noqa: E402
    STAT_NAMES,
    PartnerRow,
)
from mewgenics_overlay.ui.bestmatch import BestMatchBar  # noqa: E402
from mewgenics_overlay.ui.focuspanel import FocusedCatPanel  # noqa: E402
from mewgenics_overlay.ui.partnertable import (  # noqa: E402
    COL_CHANCE,
    COL_RISK,
    PartnerTableWidget,
)
from mewgenics_overlay.ui.roombar import RoomBar  # noqa: E402
from mewgenics_overlay.ui import palette as _palette  # noqa: E402
from mewgenics_overlay.ui import tablectl as _tablectl  # noqa: E402
from mewgenics_overlay.ui.searchbox import SearchBox  # noqa: E402
from mewgenics_overlay.ui.tablectl import TableCoordinator  # noqa: E402
from mewgenics_overlay.vendor.breeding import (  # noqa: E402
    PairFactors,
    PairProjection,
)

_USER_ROLE = Qt.ItemDataRole.UserRole


# ── fakes / helpers ────────────────────────────────────────────────────────
def make_cat(name, db_key, *, room="Attic", status="In House", generation=1,
             stats=None):
    """Duck-typed Cat with every field the widgets and row builder read."""
    base = dict(stats or {s: 4 for s in STAT_NAMES})
    return SimpleNamespace(
        name=name,
        db_key=db_key,
        gender="female",
        status=status,
        room=room,
        generation=generation,
        age=5,
        inbredness=0.0,
        base_stats=base,
        total_stats=dict(base),
        sexuality_raw=0.0,
        lovers=[],
        haters=[],
        disorders=[],
        defects=[],
        visual_mutation_entries=[],
        is_pinned=False,
        is_dead=False,
        must_breed=False,
    )


def _projection(sevens=2.0):
    return PairProjection(
        expected_stats={s: 4.0 for s in STAT_NAMES},
        stat_ranges={s: (3, 5) for s in STAT_NAMES},
        locked_stats=(),
        reachable_stats=tuple(STAT_NAMES),
        missing_stats=(),
        sum_range=(21, 35),
        avg_expected=4.0,
        seven_plus_total=sevens,
        distance_total=0.0,
    )


def make_row(partner, focus, *, risk=2.0, compatible=True, reason="",
             sevens=2.0, expected=4.0, coi=0.05, game_compat=0.5):
    """A fully-formed PartnerRow; ``defect_rows=[]`` skips ancestry walking."""
    factors = PairFactors(
        cat_a=focus,
        cat_b=partner,
        compatible=compatible,
        reason=reason,
        risk=risk,
        projection=_projection(sevens),
        complementarity_bonus=0.0,
        variance_penalty=0.0,
        personality_bonus=0.0,
        trait_bonus=0.0,
        must_breed_bonus=0.0,
        lover_bonus=0.0,
        quality=0.0,
        game_compat=game_compat,
    )
    return PartnerRow(
        partner=partner,
        compatible=compatible,
        reason=reason,
        risk_pct=risk,
        game_compat=game_compat,
        expected_avg=expected,
        stat_sum_range=(21, 35),
        seven_plus_total=sevens,
        direct_family=False,
        relation=Relation(label="unrelated", shared_ancestors=0,
                          shared_recent=0, gen_gap=0),
        coi=coi,
        mutual_lover=False,
        is_lover=False,
        is_hater=False,
        generation=1,
        pair_factors=factors,
        defect_rows=[],
    )


def pair(row, kids=()):
    """One host rows entry: the ``(row, kids)`` tuple the table renders."""
    return (row, list(kids))


class FakeSession:
    """Only the member the coordinator reads: ``by_key``."""

    def __init__(self, cats=()):
        self.cats = list(cats)

    @property
    def by_key(self):
        return {c.db_key: c for c in self.cats}

    @property
    def alive(self):
        return list(self.cats)


class Harness:
    """All the live widgets plus the coordinator, wired like the palette."""

    def __init__(self, session=None, assets=None):
        self.session = session
        self.assets = assets
        self.coordinator = None
        self.scheduled = []
        self.selected = []
        self.room_changes = []
        self.chosen = []
        self.cleared = []

        self.table = PartnerTableWidget()
        self.focus_panel = FocusedCatPanel()
        self.searchbox = SearchBox(
            lambda: self.session, self.chosen.append,
            lambda: self.cleared.append(True))
        self.room_bar = RoomBar(
            session_getter=lambda: self.session,
            assets_getter=lambda: self.assets,
            focus_getter=lambda: self.coordinator.focus
            if self.coordinator else None,
            on_change=lambda: self.room_changes.append(True),
        )
        self.detail = QLabel("Select a partner row for inheritance detail.")
        self.best_bar = BestMatchBar(
            on_select=lambda key: (
                self.coordinator.on_best_selected(key)
                if self.coordinator else None),
            rows_getter=lambda: self.coordinator.rows
            if self.coordinator else [],
            focus_getter=lambda: self.coordinator.focus
            if self.coordinator else None,
            stim_getter=lambda: self.room_bar.stim_value(),
            comfort_getter=lambda: self.room_bar.comfort_value(),
            malady_lines=lambda row, stim, effect:
                self.table.pair_malady_lines(row, stim, effect),
            effect_of=lambda a, b, n: (
                self.coordinator.effect_for_name(a, b, n)
                if self.coordinator else ""),
            safe_risk_cap=lambda: 15.0,
        )
        self.coordinator = TableCoordinator(
            table=self.table,
            focus_panel=self.focus_panel,
            searchbox=self.searchbox,
            best_bar=self.best_bar,
            room_bar=self.room_bar,
            detail=self.detail,
            session_getter=lambda: self.session,
            assets_getter=lambda: self.assets,
            schedule=lambda: self.scheduled.append(True),
            on_selected=lambda: self.selected.append(True),
        )

    def widgets(self):
        return [self.table, self.focus_panel, self.searchbox, self.room_bar,
                self.best_bar, self.detail]


def rendered_names(table):
    """Partner names in rendered (row) order."""
    names = []
    for r in range(table.rowCount()):
        data = table.item(r, 0).data(_USER_ROLE)
        names.append(data[0].partner.name)
    return names


def risk_texts(table):
    """Risk cell text in rendered (row) order."""
    return [table.item(r, COL_RISK).text() for r in range(table.rowCount())]


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_harness(qapp):
    harnesses = []

    def _make(session=None, assets=None):
        h = Harness(session=session, assets=assets)
        harnesses.append(h)
        return h

    yield _make
    for h in harnesses:
        for widget in h.widgets():
            widget.hide()
            widget.close()
            widget.deleteLater()
        h.coordinator.deleteLater()
    qapp.processEvents()


# ── 1. set_focus ───────────────────────────────────────────────────────────
def test_set_focus_stores_focus_clears_search_and_shows_the_card(
        make_harness):
    h = make_harness()
    cat = make_cat("Meeko", 1)
    h.searchbox.edit.setText("Mee")

    h.coordinator.set_focus(cat)

    assert h.coordinator.focus is cat
    assert h.searchbox.edit.text() == ""
    assert h.focus_panel._cat_name.text() == "Meeko"
    assert h.scheduled == [True]           # partner ranking queued


def test_recompute_partners_schedules_only_when_a_cat_is_focused(
        make_harness):
    h = make_harness()

    h.coordinator.recompute_partners()
    assert h.scheduled == []

    h.coordinator.set_focus(make_cat("Meeko", 1))
    h.coordinator.recompute_partners()

    assert h.scheduled == [True, True]


# ── 2. render + best match ─────────────────────────────────────────────────
def test_render_partners_populates_the_table_and_the_banner(make_harness):
    h = make_harness()
    focus = make_cat("Focus", 0)
    strong = make_cat("Strong", 11)
    weak = make_cat("Weak", 22)
    h.coordinator.set_focus(focus)

    rows = [
        pair(make_row(weak, focus, sevens=1.0, risk=8.0)),
        pair(make_row(strong, focus, sevens=6.0, risk=1.0)),
    ]
    h.coordinator.render_partners(rows)

    assert h.table.rowCount() == 2
    assert h.table.rows == rows
    assert h.best_bar._best_row is not None
    assert h.best_bar._best_row.partner is strong
    assert not h.best_bar._btn_best.isHidden()


def test_render_partners_replaces_previous_rows(make_harness):
    h = make_harness()
    focus = make_cat("Focus", 0)
    h.coordinator.set_focus(focus)

    h.coordinator.render_partners([pair(make_row(make_cat("A", 1), focus))])
    assert h.table.rowCount() == 1

    h.coordinator.render_partners([
        pair(make_row(make_cat("B", 2), focus)),
        pair(make_row(make_cat("C", 3), focus)),
    ])

    assert h.table.rowCount() == 2
    assert sorted(rendered_names(h.table)) == ["B", "C"]


def test_render_empty_rows_is_safe_and_hides_the_banner(make_harness):
    h = make_harness()
    h.coordinator.set_focus(make_cat("Focus", 0))

    h.coordinator.render_partners([])

    assert h.table.rowCount() == 0
    assert h.table.rows == []
    assert h.best_bar._best_row is None
    assert h.best_bar._btn_best.isHidden()


def test_update_best_delegates_to_the_banner(make_harness):
    h = make_harness()
    calls = []
    h.best_bar.update_best = lambda: calls.append(True)

    h.coordinator.update_best()

    assert calls == [True]


def test_on_best_selected_selects_the_row_and_notifies(make_harness):
    h = make_harness()
    focus = make_cat("Focus", 0)
    strong = make_cat("Strong", 11)
    h.coordinator.set_focus(focus)
    rows = [
        pair(make_row(make_cat("Weak", 22), focus, sevens=1.0)),
        pair(make_row(strong, focus, sevens=6.0)),
    ]
    h.coordinator.render_partners(rows)
    h.coordinator.update_best()

    h.best_bar._btn_best.click()

    assert h.selected == [True]
    assert h.table.currentRow() >= 0
    data = h.table.item(h.table.currentRow(), 0).data(_USER_ROLE)
    assert data[0].partner is strong


# ── 3. clear_focus ─────────────────────────────────────────────────────────
def test_clear_focus_resets_table_banner_search_and_stored_rows(
        make_harness):
    h = make_harness()
    focus = make_cat("Focus", 0)
    h.coordinator.set_focus(focus)
    rows = [pair(make_row(make_cat("Meeko", 1), focus))]
    h.coordinator.render_partners(rows)
    h.searchbox.edit.setText("keep me")
    h.detail.setText("stale detail")

    h.coordinator.clear_focus()

    assert h.coordinator.focus is None
    assert h.table.rowCount() == 0         # displayed rows are cleared
    assert h.focus_panel._cat_name.text() == "No cat selected"
    assert h.best_bar._best_row is None
    assert h.best_bar._btn_best.isHidden()
    assert h.detail.text() == "Select a partner row for inheritance detail."
    # The stored rows are dropped too, so a later refresh_theme() cannot
    # redraw stale partners for a cleared focus, and the search line is
    # emptied so it no longer reads as the cat that was focused.
    assert h.table.rows == []
    assert h.searchbox.edit.text() == ""
    assert h.cleared == []                 # silent: no manual-clear callback


def test_theme_refresh_after_clear_focus_does_not_bring_rows_back(
        make_harness):
    """Regression guard: a theme refresh must not resurrect stale rows.

    ``clear_focus`` empties both the visible table and the stored rows, so
    the next ``refresh_theme`` (a theme switch or a pin change calls it) has
    nothing to redraw even though it would redraw any non-empty ``rows``.
    """
    h = make_harness()
    focus = make_cat("Focus", 0)
    h.coordinator.set_focus(focus)
    h.coordinator.render_partners([
        pair(make_row(make_cat("Meeko", 1), focus))])
    h.coordinator.clear_focus()
    assert h.table.rowCount() == 0
    assert h.table.rows == []

    h.coordinator.refresh_theme()

    assert h.table.rowCount() == 0         # no stale row comes back
    assert h.table.rows == []
    assert h.best_bar._best_row is None
    assert h.focus_panel._cat_name.text() == "No cat selected"


def test_clear_focus_on_an_empty_coordinator_is_safe(make_harness):
    h = make_harness()

    h.coordinator.clear_focus()

    assert h.coordinator.focus is None
    assert h.table.rowCount() == 0
    assert h.detail.text() == "Select a partner row for inheritance detail."


def test_clear_focus_resets_the_sort_state_and_header_indicator(make_harness):
    """A cleared view must not keep a stale sort column or header arrow.

    ``clear_focus`` empties the table, so the tri-state sort state and the
    header indicator go back to the engine's default (no column, ascending).
    """
    h = make_harness()
    focus = make_cat("Focus", 0)
    h.coordinator.set_focus(focus)
    h.coordinator.render_partners([
        pair(make_row(make_cat("A", 1), focus, risk=9.0)),
        pair(make_row(make_cat("B", 2), focus, risk=1.0)),
    ])
    h.coordinator.on_header_clicked(COL_RISK)
    assert h.coordinator.sort_col == COL_RISK
    assert h.table.horizontalHeader().isSortIndicatorShown()

    h.coordinator.clear_focus()

    assert h.coordinator.sort_col is None
    assert h.coordinator.sort_dir == "asc"
    # The header arrow must not keep pointing at the dropped column.
    assert not h.table.horizontalHeader().isSortIndicatorShown()


# ── clear-if-content guard (moved off PaletteWindow) ──────────────────────
def test_has_content_is_false_with_neither_focus_nor_rows(make_harness):
    h = make_harness()

    assert h.coordinator.has_content() is False


def test_has_content_is_true_with_a_focused_cat(make_harness):
    h = make_harness()
    h.coordinator.set_focus(make_cat("Focus", 0))

    assert h.coordinator.has_content() is True


def test_has_content_is_true_with_stale_rows_and_no_focus(make_harness):
    h = make_harness()
    h.table.setRowCount(3)

    assert h.coordinator.focus is None
    assert h.coordinator.has_content() is True


@pytest.mark.parametrize("focus,rows,expected", [
    (False, 0, 0),       # nothing to reset -> table left alone
    (True, 0, 1),        # a cat is focused -> reset
    (False, 3, 1),       # stale rows on screen -> reset
])
def test_clear_if_content_resets_only_when_focus_or_rows(
        make_harness, focus, rows, expected):
    """The coordinator's search-clear guard, moved off the window.

    ``clear_if_content`` is the single entry point the SearchBox's on-clear
    callback reaches (via ``PaletteWindow._on_search_cleared``); it must only
    reset when a cat is focused or rows are still rendered.
    """
    h = make_harness()
    if focus:
        h.coordinator.set_focus(make_cat("Focus", 0))
    if rows:
        h.table.setRowCount(rows)
    clears = []
    h.coordinator.clear_focus = lambda: clears.append(True)

    h.coordinator.clear_if_content()

    assert len(clears) == expected


# ── 4. sorting header clicks ───────────────────────────────────────────────
def test_header_click_cycles_asc_desc_then_default(make_harness):
    h = make_harness()
    focus = make_cat("Focus", 0)
    h.coordinator.set_focus(focus)
    # deliberate default order that sorting must disturb
    h.coordinator.render_partners([
        pair(make_row(make_cat("Gamma", 3), focus, risk=10.0)),
        pair(make_row(make_cat("Alpha", 1), focus, risk=1.0)),
        pair(make_row(make_cat("Beta", 2), focus, risk=5.0)),
    ])
    default_order = rendered_names(h.table)

    h.coordinator.on_header_clicked(COL_RISK)            # asc
    assert h.coordinator.sort_col == COL_RISK
    assert h.coordinator.sort_dir == "asc"
    assert rendered_names(h.table) == ["Alpha", "Beta", "Gamma"]
    assert risk_texts(h.table) == ["1.0%", "5.0%", "10.0%"]

    h.coordinator.on_header_clicked(COL_RISK)            # desc
    assert h.coordinator.sort_col == COL_RISK
    assert h.coordinator.sort_dir == "desc"
    assert rendered_names(h.table) == ["Gamma", "Beta", "Alpha"]

    h.coordinator.on_header_clicked(COL_RISK)            # back to default
    assert h.coordinator.sort_col is None
    assert h.coordinator.sort_dir == "asc"
    assert rendered_names(h.table) == default_order


def test_switching_sort_column_restarts_at_ascending(make_harness):
    h = make_harness()
    focus = make_cat("Focus", 0)
    h.coordinator.set_focus(focus)
    h.coordinator.render_partners([
        pair(make_row(make_cat("Gamma", 3), focus, risk=10.0,
                      game_compat=0.10)),
        pair(make_row(make_cat("Alpha", 1), focus, risk=1.0,
                      game_compat=0.90)),
    ])

    h.coordinator.on_header_clicked(COL_RISK)
    h.coordinator.on_header_clicked(COL_RISK)            # desc
    h.coordinator.on_header_clicked(COL_CHANCE)          # different column

    assert h.coordinator.sort_col == COL_CHANCE
    assert h.coordinator.sort_dir == "asc"
    assert rendered_names(h.table) == ["Gamma", "Alpha"]  # low chance first


def test_rendering_new_rows_resets_the_sort(make_harness):
    h = make_harness()
    focus = make_cat("Focus", 0)
    h.coordinator.set_focus(focus)
    h.coordinator.render_partners([
        pair(make_row(make_cat("A", 1), focus, risk=9.0)),
        pair(make_row(make_cat("B", 2), focus, risk=1.0)),
    ])
    h.coordinator.on_header_clicked(COL_RISK)
    assert h.coordinator.sort_col == COL_RISK

    h.coordinator.render_partners([
        pair(make_row(make_cat("C", 3), focus, risk=5.0)),
        pair(make_row(make_cat("D", 4), focus, risk=2.0)),
    ])

    assert h.coordinator.sort_col is None
    assert h.coordinator.sort_dir == "asc"


def test_header_click_without_rows_is_safe(make_harness):
    h = make_harness()

    h.coordinator.on_header_clicked(COL_RISK)

    assert h.coordinator.sort_col == COL_RISK
    assert h.table.rowCount() == 0


# ── 5. set_focus_key ───────────────────────────────────────────────────────
def test_set_focus_key_resolves_a_cat_from_the_session(make_harness):
    cat = make_cat("Meeko", 42)
    h = make_harness(session=FakeSession([cat]))

    h.coordinator.set_focus_key(42)

    assert h.coordinator.focus is cat
    assert h.scheduled == [True]
    assert h.focus_panel._cat_name.text() == "Meeko"


def test_set_focus_key_unknown_key_is_inert(make_harness):
    h = make_harness(session=FakeSession([make_cat("Meeko", 42)]))

    h.coordinator.set_focus_key(999)

    assert h.coordinator.focus is None
    assert h.scheduled == []


def test_set_focus_key_without_a_session_is_inert(make_harness):
    h = make_harness(session=None)

    h.coordinator.set_focus_key(42)

    assert h.coordinator.focus is None
    assert h.scheduled == []


def test_search_chosen_resolves_from_the_session(make_harness):
    cat = make_cat("Bo", 7)
    h = make_harness(session=FakeSession([cat]))

    h.coordinator.on_search_chosen(7)

    assert h.coordinator.focus is cat

    h.coordinator.on_search_chosen(1234)               # unknown -> unchanged
    assert h.coordinator.focus is cat


# ── 6. adopt_session ───────────────────────────────────────────────────────
def test_adopt_session_keeps_focus_when_the_key_survives_a_rebuild(
        make_harness):
    original = make_cat("Meeko", 42)
    h = make_harness(session=FakeSession([original]))
    h.coordinator.set_focus(original)

    rebuilt = make_cat("Meeko", 42)                    # same key, new object
    h.coordinator.adopt_session(FakeSession([rebuilt]))

    assert h.coordinator.focus is rebuilt
    assert h.scheduled == [True, True]                 # re-queued


def test_adopt_session_clears_focus_when_the_cat_is_gone(make_harness):
    h = make_harness(session=FakeSession([make_cat("Meeko", 42)]))
    h.coordinator.set_focus(make_cat("Meeko", 42))

    h.coordinator.adopt_session(FakeSession([]))

    assert h.coordinator.focus is None
    assert h.table.rowCount() == 0


def test_adopt_session_none_clears_the_focus(make_harness):
    # An unloaded session must not keep a stale cat key driving partner
    # scheduling: adopt_session(None) drops the focus and its rows.
    h = make_harness()
    cat = make_cat("Meeko", 42)
    h.coordinator.set_focus(cat)
    h.coordinator.render_partners([
        pair(make_row(make_cat("Bo", 7), cat))])

    h.coordinator.adopt_session(None)

    assert h.coordinator.focus is None
    assert h.table.rowCount() == 0
    assert h.table.rows == []
    assert h.focus_panel._cat_name.text() == "No cat selected"
    assert h.best_bar._best_row is None
    assert h.scheduled == [True]           # no re-queue for a dead focus


# ── 7. effect lookup + theme refresh ───────────────────────────────────────
def test_effect_for_name_uses_the_assets_when_present(make_harness):
    effects = {"tail": {3: "short and stumpy"}}
    assets = SimpleNamespace(
        effect_for=lambda group, mid: effects.get(group, {}).get(mid, ""))
    h = make_harness(assets=assets)
    a = make_cat("A", 1)
    b = make_cat("B", 2)
    a.visual_mutation_entries = [
        {"is_defect": True, "name": "Bobtail", "group_key": "tail",
         "mutation_id": 3},
    ]

    assert h.coordinator.effect_for_name(a, b, "Bobtail") == "short and stumpy"
    assert h.coordinator.effect_for_name(a, b, "Unknown") == ""


def test_effect_for_name_without_assets_or_defects_is_empty(make_harness):
    h = make_harness(assets=None)
    a = make_cat("A", 1)
    a.visual_mutation_entries = [
        {"is_defect": True, "name": "Bobtail", "group_key": "tail",
         "mutation_id": 3},
    ]

    assert h.coordinator.effect_for_name(a, make_cat("B", 2), "Bobtail") == ""


def test_effect_for_name_routes_both_cats_through_the_shared_helper(
        make_harness, monkeypatch):
    """The two-cat lookup delegates to core.maladies.defect_effect_text.

    The shared helper is the single entry-walking implementation; the
    coordinator must try each carrier through it and return the first
    non-empty text, so the second cat is skipped once the first matches.
    """
    calls = []
    effects = {11: "first effect", 22: "second effect"}

    def helper(assets, cat, name):
        calls.append((assets, cat, name))
        return effects.get(cat.db_key, "")

    monkeypatch.setattr(_tablectl, "defect_effect_text", helper)
    assets = SimpleNamespace(effect_for=lambda g, m: "")
    h = make_harness(assets=assets)
    a = make_cat("A", 11)
    b = make_cat("B", 22)

    assert h.coordinator.effect_for_name(a, b, "Bobtail") == "first effect"
    assert calls == [(assets, a, "Bobtail")]     # b is short-circuited

    effects[11] = ""
    calls.clear()
    assert h.coordinator.effect_for_name(a, b, "Bobtail") == "second effect"
    assert calls == [(assets, a, "Bobtail"), (assets, b, "Bobtail")]


def test_window_defect_text_of_uses_the_same_shared_helper(monkeypatch):
    """The window's single-cat lookup also routes through the shared helper.

    Called unbound against a stand-in host: ``defect_text_of`` touches only
    ``self._assets.assets``, so no PaletteWindow (save, watcher, timers) is
    needed.
    """
    calls = []
    monkeypatch.setattr(
        _palette, "defect_effect_text",
        lambda assets, cat, name: calls.append((assets, cat, name)) or "effect")
    cat = make_cat("A", 11)
    host = SimpleNamespace(_assets=SimpleNamespace(assets="ASSETS"))

    assert _palette.PaletteWindow.defect_text_of(host, cat, "Bobtail") \
        == "effect"
    assert calls == [("ASSETS", cat, "Bobtail")]


def test_refresh_theme_repaints_rows_and_the_focus_card(make_harness):
    h = make_harness()
    focus = make_cat("Focus", 0)
    h.coordinator.set_focus(focus)
    h.coordinator.render_partners([
        pair(make_row(make_cat("Meeko", 1), focus))])

    h.coordinator.refresh_theme()

    assert h.table.rowCount() == 1
    assert h.focus_panel._cat_name.text() == "Focus"


def test_refresh_theme_on_an_empty_coordinator_is_safe(make_harness):
    h = make_harness()

    h.coordinator.refresh_theme()

    assert h.table.rowCount() == 0
    assert h.focus_panel._cat_name.text() == "No cat selected"
