"""PartnerActions: row selection, right-click pin menu and double-click focus.

``mewgenics_overlay.ui.partneractions.PartnerActions`` was extracted from
``PaletteWindow`` (god-file split). It reacts to partner-table interactions:
it writes the inheritance detail strip, opens the pin/unpin context menu and
re-focuses the picked cat. Everything window-specific (the active room
Stimulation, the pair's malady lines, the gpak effect lookup, pinning and
focusing) arrives as a callable from the host.

These tests drive the unit offscreen with a real ``PartnerTableWidget`` and a
``QLabel`` but *fake* the host callables: a duck-typed partner row (exactly the
fields the detail builder reads) plus recording providers, so no save,
session, gpak or ``PaletteWindow`` is needed. The context menu is replaced by
a scriptable stand-in for ``QMenu`` because the real ``exec`` blocks.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QTableWidgetItem,
)

from mewgenics_overlay.core.session import STAT_NAMES  # noqa: E402
from mewgenics_overlay.ui import partneractions as _pa  # noqa: E402
from mewgenics_overlay.ui.partneractions import PartnerActions  # noqa: E402
from mewgenics_overlay.ui.partnertable import PartnerTableWidget  # noqa: E402


# ── fixtures / helpers ─────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# Seven ranges that all differ, so the "higher parent" count is 7.
_STAT_RANGES = {
    "STR": (0, 2), "DEX": (1, 3), "CON": (2, 4), "INT": (3, 5),
    "SPD": (4, 6), "CHA": (5, 7), "LCK": (6, 7),
}
assert set(_STAT_RANGES) == set(STAT_NAMES)
# Same dict but every range is a single value -> nothing differs.
_FLAT_RANGES = {s: (3, 3) for s in STAT_NAMES}


def make_cat(name, *, db_key=1, gender="female", sexuality_raw=0.0,
             status="In House", room="Nursery", is_pinned=False):
    return SimpleNamespace(
        name=name, db_key=db_key, gender=gender, sexuality_raw=sexuality_raw,
        status=status, room=room, is_pinned=is_pinned,
    )


def make_row(partner, *, focus=None, relation_label="unrelated", gen_gap=3,
             compatible=True, reason="", coi=0.05, stat_ranges=None):
    """Duck-typed PartnerRow: only the fields the detail builder reads."""
    return SimpleNamespace(
        partner=partner,
        relation=SimpleNamespace(label=relation_label, gen_gap=gen_gap),
        compatible=compatible,
        reason=reason,
        coi=coi,
        pair_factors=SimpleNamespace(
            cat_a=focus if focus is not None else make_cat("Focus", db_key=0),
            cat_b=partner,
            projection=SimpleNamespace(
                stat_ranges=dict(stat_ranges or _STAT_RANGES)),
        ),
    )


class _FakeMenu:
    """Scriptable QMenu stand-in: records action texts, returns a choice.

    ``controller.choice`` selects what ``exec`` returns: ``None`` (dismissed)
    or ``"first"`` (the first action was clicked).
    """

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
        if self.controller.choice == "first" and self.actions:
            return self.actions[0]
        return None


@pytest.fixture
def fake_menu(monkeypatch):
    """Replace the module's QMenu with a recording, non-blocking fake."""
    controller = SimpleNamespace(menus=[], choice=None, last_pos=None)

    def factory(parent=None):
        return _FakeMenu(controller, parent)

    monkeypatch.setattr(_pa, "QMenu", factory)
    return controller


@pytest.fixture
def make_actions(qapp, fake_menu):
    """Build isolated PartnerActions over a real table with recording fakes."""
    widgets = []

    def _make(row=None, kids=(), *, stim=50.0, malady_fn=None, select=True):
        table = PartnerTableWidget()
        table.resize(840, 260)
        name_item = None
        if row is not None:
            table.setRowCount(1)
            name_item = QTableWidgetItem(row.partner.name)
            name_item.setData(Qt.ItemDataRole.UserRole, (row, list(kids)))
            table.setItem(0, 0, name_item)
            table.setItem(0, 4, QTableWidgetItem("2.0%"))
            if select:
                table.setCurrentItem(name_item)
        table.show()
        qapp.processEvents()

        detail = QLabel("")
        stim_calls: list = []
        malady_calls: list = []
        effect_calls: list = []
        pins: list = []
        focuses: list = []

        def stim_value():
            stim_calls.append(stim)
            return stim

        def malady_lines(r, s, effect):
            malady_calls.append((r, s, effect))
            return list(malady_fn(r, s, effect)) if malady_fn else []

        def effect_of(a, b, name):
            effect_calls.append((a, b, name))
            return ""

        actions = PartnerActions(
            table, detail, stim_value, malady_lines, effect_of,
            on_pin=lambda cat, on: pins.append((cat, on)),
            on_focus=focuses.append,
        )
        env = SimpleNamespace(
            table=table, name_item=name_item, detail=detail, actions=actions,
            stim_calls=stim_calls, malady_calls=malady_calls,
            effect_calls=effect_calls, effect_of=effect_of,
            pins=pins, focuses=focuses,
        )
        widgets.append(table)
        return env

    yield _make
    for table in widgets:
        table.hide()
        table.close()
        table.deleteLater()
    qapp.processEvents()


def _row_center(env, col=0):
    """Viewport point at the centre of row 0 / column *col*."""
    return env.table.visualItemRect(env.table.item(0, col)).center()


# ── 1. selection detail (compatible) ───────────────────────────────────────
def test_selection_shows_full_inheritance_strip_for_compatible_row(make_actions):
    partner = make_cat("Meeko")
    row = make_row(partner)
    env = make_actions(row)

    env.actions.on_partner_selected()

    ranges = "  ".join(
        f"{s} {_STAT_RANGES[s][0]}–{_STAT_RANGES[s][1]}" for s in STAT_NAMES)
    assert env.detail.text() == (
        "Meeko: unrelated · Δgen +3 · COI 5.0%\n"
        f"Kitten stats per parent range: {ranges}\n"
        "The kitten takes the higher of the two parents' values in "
        "≈4.2 of 7 differing stats"
    )


def test_selection_uses_relation_label_gap_and_coi(make_actions):
    partner = make_cat("Coco")
    row = make_row(partner, relation_label="1st cousin", gen_gap=-2, coi=0.187)
    env = make_actions(row)

    env.actions.on_partner_selected()

    assert "Coco: 1st cousin · Δgen -2 · COI 18.7%" in env.detail.text()


def test_selection_appends_existing_kittens_even_with_unicode(make_actions):
    partner = make_cat("Meeko")
    row = make_row(partner)
    env = make_actions(row, kids=["Kit A", "Mîlø 😺"])

    env.actions.on_partner_selected()

    assert (
        "   ·   existing kittens: Kit A, Mîlø 😺"
        in env.detail.text()
    )


def test_selection_omits_kitten_line_when_no_kids(make_actions):
    env = make_actions(make_row(make_cat("Meeko")), kids=[])

    env.actions.on_partner_selected()

    assert "existing kittens" not in env.detail.text()


# ── 2. selection detail (incompatible) ─────────────────────────────────────
def test_selection_shows_block_reason_for_incompatible_row(make_actions):
    partner = make_cat("Blocked")
    row = make_row(partner, compatible=False, reason="both female")
    env = make_actions(row)

    env.actions.on_partner_selected()

    assert env.detail.text() == (
        "Blocked: unrelated · Δgen +3 · COI 5.0%\n"
        "Can't breed: both female"
    )
    assert "Kitten stats" not in env.detail.text()


def test_selection_incompatible_without_reason_says_blocked(make_actions):
    row = make_row(make_cat("Blocked"), compatible=False, reason="")
    env = make_actions(row)

    env.actions.on_partner_selected()

    assert env.detail.text().endswith("\nCan't breed: blocked")


# ── 3. providers are consulted ─────────────────────────────────────────────
def test_selection_consults_stim_and_malady_providers(make_actions):
    partner = make_cat("Meeko")
    row = make_row(partner)
    env = make_actions(row, stim=37.0,
                       malady_fn=lambda r, s, e: ["⚠ carries a disorder"])

    env.actions.on_partner_selected()

    assert env.stim_calls == [37.0]              # exactly one read
    assert len(env.malady_calls) == 1
    called_row, called_stim, called_effect = env.malady_calls[0]
    assert called_row is row
    assert called_stim == 37.0
    assert called_effect is env.effect_of        # effect lookup is handed over
    assert "⚠ carries a disorder" in env.detail.text()


def test_effect_provider_is_reached_through_the_malady_provider(make_actions):
    partner = make_cat("Meeko")
    row = make_row(partner)
    focus = row.pair_factors.cat_a

    def malady_fn(r, s, effect):
        # The host's malady builder calls back into effect_of for gpak text.
        effect(r.pair_factors.cat_a, r.pair_factors.cat_b, "short tail")
        return ["→ short tail: inherited"]

    env = make_actions(row, malady_fn=malady_fn)

    env.actions.on_partner_selected()

    assert env.effect_calls == [(focus, partner, "short tail")]
    assert "→ short tail: inherited" in env.detail.text()


def test_malady_lines_are_appended_each_on_its_own_line(make_actions):
    row = make_row(make_cat("Meeko"))
    env = make_actions(row, malady_fn=lambda r, s, e: ["first", "second"])

    env.actions.on_partner_selected()

    assert env.detail.text().endswith("\nfirst\nsecond")


# ── 4. selection with nothing selected / empty data ────────────────────────
def test_selection_with_no_current_item_leaves_detail_untouched(make_actions):
    env = make_actions(row=None)          # empty table, no current item
    env.detail.setText("sentinel")

    env.actions.on_partner_selected()

    assert env.detail.text() == "sentinel"


def test_selection_item_without_role_data_leaves_detail_untouched(
        make_actions):
    env = make_actions(make_row(make_cat("Meeko")))
    env.name_item.setData(Qt.ItemDataRole.UserRole, None)
    env.detail.setText("sentinel")

    env.actions.on_partner_selected()

    assert env.detail.text() == "sentinel"


def test_selection_with_zero_differing_stats_omits_the_better_parent_line(
        make_actions):
    # Both parents identical in every stat -> nothing differs, so the
    # meaningless "≈0.0 of 0 differing stats" line is omitted entirely while
    # the rest of the inheritance strip still renders.
    row = make_row(make_cat("Twin"), stat_ranges=_FLAT_RANGES)
    env = make_actions(row)

    env.actions.on_partner_selected()

    text = env.detail.text()
    assert text.startswith("Twin: unrelated")
    assert "Kitten stats per parent range:" in text
    assert "higher of the two parents" not in text
    assert "differing stats" not in text


# ── 5. right-click pin/unpin menu ──────────────────────────────────────────
def test_menu_offers_pin_and_calls_back_true_when_cat_unpinned(
        make_actions, fake_menu):
    partner = make_cat("Meeko", is_pinned=False)
    env = make_actions(make_row(partner))
    fake_menu.choice = "first"

    env.actions.show_breeding_menu(_row_center(env))

    assert len(fake_menu.menus) == 1
    assert [a.text for a in fake_menu.menus[0].actions] == ["Pin for breeding"]
    assert env.pins == [(partner, True)]


def test_menu_offers_unpin_and_calls_back_false_when_cat_pinned(
        make_actions, fake_menu):
    partner = make_cat("Meeko", is_pinned=True)
    env = make_actions(make_row(partner))
    fake_menu.choice = "first"

    env.actions.show_breeding_menu(_row_center(env))

    assert [a.text for a in fake_menu.menus[0].actions] == \
        ["Unpin - allow donation"]
    assert env.pins == [(partner, False)]


def test_menu_treats_a_missing_pinned_attribute_as_unpinned(
        make_actions, fake_menu):
    partner = make_cat("Meeko")
    del partner.is_pinned                       # no pin state recorded yet
    env = make_actions(make_row(partner))
    fake_menu.choice = "first"

    env.actions.show_breeding_menu(_row_center(env))

    assert [a.text for a in fake_menu.menus[0].actions] == ["Pin for breeding"]
    assert env.pins == [(partner, True)]


def test_dismissing_the_menu_does_not_pin(make_actions, fake_menu):
    partner = make_cat("Meeko", is_pinned=False)
    env = make_actions(make_row(partner))
    fake_menu.choice = None                    # exec returned nothing

    env.actions.show_breeding_menu(_row_center(env))

    assert len(fake_menu.menus) == 1           # menu was still built
    assert env.pins == []


def test_right_click_on_a_non_name_column_finds_the_partner(make_actions,
                                                            fake_menu):
    # The menu reads the partner from column 0, not from the clicked cell.
    partner = make_cat("Meeko", is_pinned=False)
    env = make_actions(make_row(partner))
    fake_menu.choice = "first"

    env.actions.show_breeding_menu(_row_center(env, col=4))

    assert env.pins == [(partner, True)]


def test_right_click_on_empty_table_builds_no_menu(make_actions, fake_menu):
    env = make_actions(row=None)

    env.actions.show_breeding_menu(QPoint(5, 5))

    assert fake_menu.menus == []
    assert env.pins == []


def test_right_click_below_the_last_row_builds_no_menu(make_actions,
                                                       fake_menu):
    env = make_actions(make_row(make_cat("Meeko")))

    env.actions.show_breeding_menu(QPoint(5, env.table.height() - 2))

    assert fake_menu.menus == []
    assert env.pins == []


# ── 6. double-click re-focus ───────────────────────────────────────────────
def test_double_click_refocuses_the_partner(make_actions):
    partner = make_cat("Meeko")
    env = make_actions(make_row(partner))

    env.actions.on_partner_double(env.name_item)

    assert env.focuses == [partner]


def test_double_click_ignores_the_kids_half_of_the_data(make_actions):
    partner = make_cat("Meeko")
    env = make_actions(make_row(partner), kids=["Kit A"])

    env.actions.on_partner_double(env.name_item)

    assert env.focuses == [partner]


def test_double_click_without_role_data_does_not_focus(make_actions):
    env = make_actions(make_row(make_cat("Meeko")))
    bare = QTableWidgetItem("bare")

    env.actions.on_partner_double(bare)

    assert env.focuses == []
