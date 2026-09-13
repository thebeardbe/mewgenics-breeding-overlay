"""Security regression: save-derived tooltip text is escaped before Qt sees it.

Qt parses a tooltip string as rich text as soon as it looks like markup, and
``QWidget.setToolTip`` / ``QTableWidgetItem.setToolTip`` have no text-format
setter. Before the fix, tooltips built from save-derived text (cat, disorder,
defect and room names, gpak effect text) were handed to Qt unescaped, so a
crafted cat name such as ``<b>Pwn</b>`` (or ``<img src=x onerror=...>``) was
rendered as HTML inside the tip.

The fix routes every one of those tooltips through
``ui.theme.rich_tooltip``: it escapes the whole string, forces rich text with
a ``<qt>`` wrapper, turns newlines into ``<br>`` and leading indentation into
``&nbsp;`` (rich text otherwise collapses both), and is always applied *after*
``wrap_tooltip``.

These tests are deliberately layered:

  * helper-level: escaping, the ``<qt>`` wrapper, newline/indent preservation,
    empty/None, unicode and a large input, plus a plain-text round trip;
  * routed-builder level: the focused cat's name and health tips, a partner
    row's Cat / Family / GenΔ / Room / Defects tips, a donations row tip and
    the best-match "why this pick" tip are driven with a hostile name and read
    back through their real rendering path.

"Rendered" means: parse the tip with ``QTextDocument`` (what Qt does when it
shows the tooltip) and compare ``toPlainText()``. Assertions there are token
based because ``wrap_tooltip`` may break a line inside a multi-word hostile
string.

Everything runs offscreen with no window, timer, socket or save file.
"""

from __future__ import annotations

import html
import os
import re
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtGui import QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.core.donations import (  # noqa: E402
    DonationAdvice,
    DonationSlot,
)
from mewgenics_overlay.core.kinship import Relation  # noqa: E402
from mewgenics_overlay.core.session import STAT_NAMES, PartnerRow  # noqa: E402
from mewgenics_overlay.ui import bestmatch as _bm  # noqa: E402
from mewgenics_overlay.ui import theme as _theme  # noqa: E402
from mewgenics_overlay.ui.bestmatch import BestMatchBar  # noqa: E402
from mewgenics_overlay.ui.donations_tab import DonationsTab  # noqa: E402
from mewgenics_overlay.ui.focuspanel import FocusedCatPanel  # noqa: E402
from mewgenics_overlay.ui.partnertable import (  # noqa: E402
    COL_CAT,
    COL_DEFECTS,
    COL_FAMILY,
    COL_GEN_DELTA,
    COL_ROOM,
    PartnerTableWidget,
)
from mewgenics_overlay.vendor.breeding import (  # noqa: E402
    PairFactors,
    PairProjection,
)

#: A tag that Qt would render live. ``<qt>``, ``</qt>`` and ``<br>`` are the
#: only tags ``rich_tooltip`` is allowed to emit, so anything else is a leak.
_RAW_TAG = re.compile(r"<(?!/?qt>|br>)[A-Za-z/!]")

#: Markup-shaped strings Qt reads as HTML: a plain name wrapped in a tag,
#: tags with attributes, and multi-word ones that exercise word wrapping.
HOSTILE = [
    "<b>Pwn</b>",
    "<i>M</i>",
    "<img src=x>",
    "<img src=x onerror=alert(1)>",
    '<a href="https://evil.example">Meeko</a>',
    "<span style='color:red'>Meeko</span>",
    "<script>alert(1)</script>",
]


# ── fakes / helpers ────────────────────────────────────────────────────────
def rendered(tip: str) -> str:
    """What Qt shows for *tip*: parse it exactly like a tooltip is parsed."""
    doc = QTextDocument()
    doc.setHtml(tip)
    return doc.toPlainText()


def assert_no_live_markup(tip: str) -> None:
    assert not _RAW_TAG.search(tip), f"live markup in tooltip: {tip!r}"


def assert_shows_literally(tip: str, raw: str) -> None:
    """*raw* is never live markup and its characters reach the rendered tip.

    Each whitespace token is checked separately: ``wrap_tooltip`` may break a
    line between the tokens of a multi-word name, and it never splits a token,
    so every token survives intact somewhere in the rendered text.
    """
    assert raw not in tip, f"raw markup reached the tooltip: {raw!r}"
    assert_no_live_markup(tip)
    text = rendered(tip)
    for token in raw.split():
        assert token in text, f"{token!r} not rendered literally in {text!r}"


def make_cat(name, *, db_key=4, room="Attic", status="In House", generation=1,
             age=4, inbredness=0.0, disorders=(), defects=(), entries=(),
             lovers=(), is_pinned=False):
    base = {s: 4 for s in STAT_NAMES}
    return SimpleNamespace(
        name=name, db_key=db_key, gender="female", status=status, room=room,
        generation=generation, age=age, inbredness=inbredness,
        base_stats=base, total_stats=dict(base), sexuality_raw=0.0,
        lovers=list(lovers), haters=[], disorders=list(disorders),
        defects=list(defects), visual_mutation_entries=list(entries),
        is_pinned=is_pinned, is_dead=False, must_breed=False,
    )


def make_defect(name, *, carriers=("b",), chance=50.0):
    """Duck-typed inheritance row, the shape the worker precomputes."""
    return SimpleNamespace(
        name=name, carriers=list(carriers), chance_pct=chance,
        group="arms", same_line=True, slots_a=(), slots_b=(),
    )


def _projection():
    return PairProjection(
        expected_stats={s: 4.0 for s in STAT_NAMES},
        stat_ranges={s: (3, 5) for s in STAT_NAMES},
        locked_stats=(),
        reachable_stats=tuple(STAT_NAMES),
        missing_stats=(),
        sum_range=(21, 35),
        avg_expected=4.0,
        seven_plus_total=2.0,
        distance_total=0.0,
    )


def make_row(partner, focus, *, relation=None, compatible=True, risk=2.0,
             coi=0.05, game_compat=0.5, defect_rows=()):
    factors = PairFactors(
        cat_a=focus, cat_b=partner, compatible=compatible, reason="",
        risk=risk, projection=_projection(), complementarity_bonus=0.0,
        variance_penalty=0.0, personality_bonus=0.0, trait_bonus=0.0,
        must_breed_bonus=0.0, lover_bonus=0.0, quality=0.0,
        game_compat=game_compat,
    )
    return PartnerRow(
        partner=partner, compatible=compatible, reason="",
        risk_pct=risk, game_compat=game_compat, expected_avg=4.0,
        stat_sum_range=(21, 35), seven_plus_total=2.0, direct_family=False,
        relation=relation or Relation(label="unrelated", shared_ancestors=0,
                                      shared_recent=0, gen_gap=3),
        coi=coi, mutual_lover=False, is_lover=False, is_hater=False,
        generation=1, pair_factors=factors, defect_rows=list(defect_rows),
    )


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_panel(qapp):
    panels = []

    def _make():
        panel = FocusedCatPanel()
        panels.append(panel)
        return panel

    yield _make
    for panel in panels:
        panel.hide()
        panel.close()
        panel.deleteLater()
    qapp.processEvents()


@pytest.fixture
def make_table(qapp):
    tables = []

    def _make(row, *, effect_of=None):
        table = PartnerTableWidget()
        table.resize(840, 200)
        table.set_rows([(row, [])])
        table.redraw(stimulation=50.0, comfort=0.0, effect_of=effect_of)
        table.show()
        tables.append(table)
        return table

    yield _make
    for table in tables:
        table.hide()
        table.close()
        table.deleteLater()
    qapp.processEvents()


# ── 1. the helper itself ───────────────────────────────────────────────────
@pytest.mark.parametrize("raw", HOSTILE)
def test_rich_tooltip_escapes_markup_and_shows_the_literal_characters(raw):
    tip = _theme.rich_tooltip(raw)
    # Pre-fix behaviour: Qt drops/paints the markup (asserted below) - the raw
    # string must no longer survive, its escaped form must.
    assert raw not in tip
    assert html.escape(raw, quote=False) in tip
    assert_no_live_markup(tip)
    assert rendered(tip) == raw


def test_qt_would_render_the_raw_string_as_live_markup():
    """The vulnerability this fix closes still exists without the helper."""
    assert rendered("<b>Pwn</b>") == "Pwn"
    assert rendered("<b>Pwn</b>") != "<b>Pwn</b>"
    # ... and the helper is what stops it.
    assert rendered(_theme.rich_tooltip("<b>Pwn</b>")) == "<b>Pwn</b>"


def test_rich_tooltip_always_emits_a_qt_wrapper():
    tip = _theme.rich_tooltip("plain")
    assert tip.startswith("<qt>") and tip.endswith("</qt>")
    assert rendered(tip) == "plain"


def test_rich_tooltip_turns_newlines_into_line_breaks():
    raw = "first line\nsecond line"
    tip = _theme.rich_tooltip(raw)
    assert "<br>" in tip
    assert rendered(tip) == raw


def test_rich_tooltip_preserves_leading_indentation():
    raw = "no indent\n    bullet under a heading\n  tail"
    tip = _theme.rich_tooltip(raw)
    assert tip.count("&nbsp;") == 6       # 4 + 2 leading spaces
    assert rendered(tip) == raw


def test_rich_tooltip_handles_empty_and_none():
    assert _theme.rich_tooltip("") == "<qt></qt>"
    assert _theme.rich_tooltip(None) == "<qt></qt>"


def test_rich_tooltip_handles_unicode_and_a_large_input():
    raw = ("<b>Ünïcøde</b> " * 200) + "🐈"
    tip = _theme.rich_tooltip(raw)
    assert raw not in tip
    assert "&lt;b&gt;Ünïcøde&lt;/b&gt;" in tip
    assert_no_live_markup(tip)
    assert rendered(tip) == raw


@pytest.mark.parametrize("raw", [
    "The cat you are analysing. Double-click a partner to switch "
    "the analysis to them.",
    "• disorders: Narcolepsy - each parent with a disorder has a 15% "
    "chance to pass one\n    • birth defects (odds depend on the partner):",
    "STR 3–5  DEX 3–5       trailing spaces survive",
    "≥7 stats: 2.0 ×7 (+14.0)   ⭐ Best match - Risk 2.0%",
    "",
])
def test_rich_tooltip_renders_ordinary_wrapped_text_unchanged(raw):
    """Ordinary text must render exactly as ``wrap_tooltip`` produced it."""
    wrapped = _theme.wrap_tooltip(raw)
    assert rendered(_theme.rich_tooltip(wrapped)) == wrapped


# ── 2. FocusedCatPanel: name + health tips ─────────────────────────────────
@pytest.mark.parametrize("raw", ["<b>Pwn</b>", "<i>M</i>", "<img src=x>"])
def test_focus_name_tip_escapes_a_hostile_name(make_panel, raw):
    panel = make_panel()
    panel.show_cat(make_cat(raw))
    assert_shows_literally(panel._cat_name.toolTip(), raw)


def test_focus_meta_tip_escapes_a_hostile_room(make_panel):
    panel = make_panel()
    panel.show_cat(make_cat("Meeko", room="<b>Attic</b>"))
    assert_shows_literally(panel._cat_meta.toolTip(), "<b>Attic</b>")


def test_focus_health_tip_escapes_disorders_defects_and_effect_text(
        make_panel):
    disorder = "<b>Narcolepsy</b>"
    defect = "<img src=x> Birth Defect"
    effect = "<i>+2 STR</i>"
    cat = make_cat(
        "Meeko", disorders=[disorder], defects=[defect],
        entries=[{"is_defect": True, "name": defect, "group_key": "arms",
                  "mutation_id": 1}],
    )
    panel = make_panel()
    panel.show_cat(cat, effect_for=lambda group, mutation: effect)
    tip = panel._cat_health.toolTip()
    assert_shows_literally(tip, disorder)
    assert_shows_literally(tip, defect)
    assert_shows_literally(tip, effect)


def test_focus_health_tip_is_rich_text_from_construction(make_panel):
    tip = make_panel()._cat_health.toolTip()
    assert tip.startswith("<qt>")
    assert "Birth defects" in rendered(tip)


@pytest.mark.parametrize("name", ["Meeko", "<b>Pwn</b>"])
def test_focus_name_tip_renders_exactly_the_pre_fix_plain_text(
        make_panel, monkeypatch, name):
    panel = make_panel()
    panel.show_cat(make_cat(name, db_key=4))
    fixed = panel._cat_name.toolTip()
    monkeypatch.setattr(_theme, "rich_tooltip", lambda s: s)
    panel.show_cat(make_cat(name, db_key=4))
    pre_fix = panel._cat_name.toolTip()
    assert rendered(fixed) == pre_fix
    assert pre_fix.startswith(f"{name} (save id 4)")
    assert "The cat you are analysing." in pre_fix


# ── 3. PartnerTableWidget: the row cell tips ───────────────────────────────
def _hostile_table(make_table, name="<b>Pwn</b>", *, effect_of=None):
    focus = make_cat("Focus", db_key=0)
    partner = make_cat(name, db_key=9, room="<i>Attic</i>",
                       disorders=["<b>Narcolepsy</b>"])
    defect = make_defect("<img src=x> Birth Defect")
    row = make_row(partner, focus, relation=Relation(
        label="sibling", shared_ancestors=2, shared_recent=1, gen_gap=3),
        defect_rows=[defect])
    return make_table(row, effect_of=effect_of)


def test_partner_cat_tip_escapes_hostile_name(make_table):
    table = _hostile_table(make_table)
    assert_shows_literally(table.item(0, COL_CAT).toolTip(), "<b>Pwn</b>")


def test_partner_gap_tip_escapes_hostile_name(make_table):
    table = _hostile_table(make_table)
    tip = table.item(0, COL_GEN_DELTA).toolTip()
    assert_shows_literally(tip, "<b>Pwn</b>")
    assert "Generation gap" in rendered(tip)


def test_partner_room_tip_escapes_hostile_name(make_table):
    table = _hostile_table(make_table)
    tip = table.item(0, COL_ROOM).toolTip()
    assert_shows_literally(tip, "<b>Pwn</b>")
    assert "Current location of" in rendered(tip)


def test_partner_defects_tip_escapes_names_disorders_and_effects(make_table):
    table = _hostile_table(make_table, effect_of=lambda a, b, n: "<i>curse</i>")
    tip = table.item(0, COL_DEFECTS).toolTip()
    assert_shows_literally(tip, "<b>Pwn</b>")
    assert_shows_literally(tip, "<b>Narcolepsy</b>")
    assert_shows_literally(tip, "<img src=x> Birth Defect")
    assert_shows_literally(tip, "<i>curse</i>")


def test_partner_family_tip_is_routed_through_the_helper(make_table):
    table = _hostile_table(make_table)
    tip = table.item(0, COL_FAMILY).toolTip()
    assert tip.startswith("<qt>")
    text = rendered(tip)
    assert "Relationship: sibling" in text
    assert "Longer explanation: hover the Family heading above." in text


def test_partner_family_tip_escapes_its_relation_label(make_table):
    focus = make_cat("Focus", db_key=0)
    partner = make_cat("Meeko", db_key=9)
    row = make_row(partner, focus, relation=Relation(
        label="<b>sibling</b>", shared_ancestors=2, shared_recent=1,
        gen_gap=3))
    table = make_table(row)
    assert_shows_literally(table.item(0, COL_FAMILY).toolTip(),
                           "<b>sibling</b>")


@pytest.mark.parametrize("name", ["Meeko", "<b>Pwn</b>"])
def test_partner_tip_renders_exactly_the_pre_fix_plain_text(
        make_table, monkeypatch, name):
    """The displayed tip equals the plain wrapped text the tooltip had before
    the fix - the escaping only changes how Qt gets there, not the wording."""
    focus = make_cat("Focus", db_key=0)
    partner = make_cat(name, db_key=9)
    table = make_table(make_row(partner, focus))
    fixed = table.item(0, COL_CAT).toolTip()
    monkeypatch.setattr(_theme, "rich_tooltip", lambda s: s)
    table.redraw(stimulation=50.0, comfort=0.0)
    pre_fix = table.item(0, COL_CAT).toolTip()
    assert rendered(fixed) == pre_fix
    assert "Double-click to analyse breeding from this cat." in pre_fix


# ── 4. DonationsTab: the row tip ───────────────────────────────────────────
def _donations_tab(qapp, cat, advice):
    tab = DonationsTab()
    slot = DonationSlot(
        npc="Tink", wants="1-day-old kittens", candidates=[cat],
        advice=[advice])
    tab._render_slot(slot)
    return tab


def test_donations_row_tip_escapes_name_reasons_and_room(qapp):
    cat = make_cat("<b>Pwn</b>", age=1, room="<i>Attic</i>")
    advice = DonationAdvice(give=["<b>weak</b> stats"], keep=[],
                            keep_for_breeding=False)
    tab = _donations_tab(qapp, cat, advice)
    try:
        tip = tab._table.item(0, 0).toolTip()
        assert_shows_literally(tip, "<b>Pwn</b>")
        assert_shows_literally(tip, "<b>weak</b>")
        assert_shows_literally(tip, "<i>Attic</i>")
        assert "Suitable for: Tink" in rendered(tip)
        # every cell of the row carries the same routed tip
        assert tab._table.item(0, 5).toolTip() == tip
    finally:
        tab.hide()
        tab.close()
        tab.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("name", ["Meeko", "<b>Pwn</b>"])
def test_donations_row_tip_renders_exactly_the_pre_fix_plain_text(
        qapp, monkeypatch, name):
    cat = make_cat(name, age=1, room="Attic")
    advice = DonationAdvice(give=["weak stats"], keep=[],
                            keep_for_breeding=False)
    tab = _donations_tab(qapp, cat, advice)
    try:
        fixed = tab._table.item(0, 0).toolTip()
        monkeypatch.setattr(_theme, "rich_tooltip", lambda s: s)
        tab._render_slot(DonationSlot(
            npc="Tink", wants="1-day-old kittens", candidates=[cat],
            advice=[advice]))
        pre_fix = tab._table.item(0, 0).toolTip()
        assert rendered(fixed) == pre_fix
        bullet = next(line for line in pre_fix.split("\n")
                      if "weak stats" in line)
        assert bullet.startswith("  ")          # indent survived escaping
        assert bullet.lstrip() == "• weak stats"
        assert "Suitable for: Tink" in pre_fix
    finally:
        tab.hide()
        tab.close()
        tab.deleteLater()
        qapp.processEvents()


# ── 5. BestMatchBar: the "why this pick" tip ───────────────────────────────
def _best_bar(qapp, row, focus, effect):
    """A BestMatchBar wired like the palette, incl. the real malady provider."""
    table = PartnerTableWidget()
    table._stim = 50.0
    bar = BestMatchBar(
        on_select=lambda key: None,
        rows_getter=lambda: [(row, [])],
        focus_getter=lambda: focus,
        stim_getter=lambda: 50.0,
        comfort_getter=lambda: 0.0,
        malady_lines=lambda r, stim, eff: table.pair_malady_lines(
            r, stim, eff),
        effect_of=lambda a, b, n: effect,
        safe_risk_cap=lambda: 15.0,
    )
    bar.show()
    qapp.processEvents()
    bar.update_best()
    return bar, table


def _close_best_bar(qapp, bar, table):
    bar.hide()
    bar.close()
    bar.deleteLater()
    table.deleteLater()
    qapp.processEvents()


def _hostile_best_row():
    focus = make_cat("Focus", db_key=0)
    partner = make_cat("<b>Pwn</b>", db_key=9,
                       disorders=["<b>Narcolepsy</b>"])
    row = make_row(partner, focus,
                   defect_rows=[make_defect("<img src=x> Birth Defect")])
    return row, focus


def test_best_match_tip_escapes_name_defect_and_effect(qapp):
    row, focus = _hostile_best_row()
    bar, table = _best_bar(qapp, row, focus, "<i>curse</i>")
    try:
        tip = bar._btn_best.toolTip()
        assert tip.startswith("<qt>")
        assert_shows_literally(tip, "<b>Pwn</b>")
        assert_shows_literally(tip, "<b>Narcolepsy</b>")
        assert_shows_literally(tip, "<img src=x> Birth Defect")
        assert_shows_literally(tip, "<i>curse</i>")
        text = rendered(tip)
        assert text.startswith("Why this pick:")
        assert text.rstrip().endswith("Click to select this partner.")
    finally:
        _close_best_bar(qapp, bar, table)


@pytest.mark.parametrize("name", ["Meeko", "<b>Pwn</b>"])
def test_best_match_tip_renders_exactly_the_pre_fix_plain_text(
        qapp, monkeypatch, name):
    focus = make_cat("Focus", db_key=0)
    partner = make_cat(name, db_key=9, disorders=["Narcolepsy"])
    row = make_row(partner, focus, defect_rows=[make_defect("Curse")])
    bar, table = _best_bar(qapp, row, focus, "curse")
    try:
        fixed = bar._btn_best.toolTip()
        monkeypatch.setattr(_bm, "_rich", lambda s: s)
        bar.update_best()
        pre_fix = bar._btn_best.toolTip()
        assert rendered(fixed) == pre_fix
        assert pre_fix.startswith("Why this pick:")
    finally:
        _close_best_bar(qapp, bar, table)
