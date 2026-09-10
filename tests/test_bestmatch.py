"""BestMatchBar: the ⭐ banner, the tooltip and the safe-mode switch.

``mewgenics_overlay.ui.bestmatch.BestMatchBar`` was extracted out of
``PaletteWindow`` (god-file split, step 5). It is window-agnostic: the live
rows / focused cat / room Stimulation+Comfort and the gpak-backed malady and
effect providers arrive as plain callables, and ``on_select(db_key)`` tells
the host which partner the user picked. These tests drive it with fake
``(row, kids)`` pairs (duck-typed to the fields the recommender reads), so no
save, session, PartnerTable or PaletteWindow is needed.

The bar hides itself only through its child buttons (``clear()`` never hides
the bar itself), so the bar is shown once in the fixture and the assertions
read ``_btn_best.isVisible()`` / ``_btn_safe.isVisible()`` - the same
explicit-visibility contract production relies on via the host layout.

The recompute method is ``update_best()`` (renamed so it does not shadow
``QWidget.update()``).
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui import bestmatch as _bm  # noqa: E402
from mewgenics_overlay.ui.bestmatch import (  # noqa: E402
    _HIGH_RISK_PCT,
    _LOW_NIGHT_CHANCE,
    _SAFE_LABEL,
    BestMatchBar,
)

_UNSET = object()


# ── fakes ──────────────────────────────────────────────────────────────────
def make_cat(name, db_key):
    return SimpleNamespace(name=name, db_key=db_key)


def make_row(partner, focus, *, risk=2.0, sevens=2.0, exp_avg=5.0, coi=0.05,
             compatible=True, game_compat=0.5, defect_rows=()):
    """Minimal duck-typed PartnerRow: exactly the fields the recommender and
    the banner read. ``defect_rows`` is pre-set (empty) so no real cat
    ancestry is walked."""
    return SimpleNamespace(
        partner=partner,
        compatible=compatible,
        risk_pct=risk,
        seven_plus_total=sevens,
        expected_avg=exp_avg,
        coi=coi,
        game_compat=game_compat,
        defect_rows=list(defect_rows),
        pair_factors=SimpleNamespace(cat_a=focus, cat_b=partner),
    )


def pair(row, kids=()):
    """One entry of the host's rows list: the ``(row, kids)`` tuple contract."""
    return (row, list(kids))


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_bar(qapp):
    """Build an isolated, shown BestMatchBar; tear it down afterwards."""
    bars = []

    def _make(rows=None, focus=_UNSET, *, stim=50.0, comfort=0.0,
              malady_lines=None, safe_risk_cap=15.0, on_select=None,
              effect_of=None):
        rows_box = list(rows or [])            # list of (row, kids) pairs
        focus_box = [make_cat("Focus", 0) if focus is _UNSET else focus]
        picked = [] if on_select is None else None
        select_cb = on_select if on_select is not None else picked.append
        if malady_lines is None:
            malady_lines = lambda row, s, e: []  # noqa: E731
        bar = BestMatchBar(
            on_select=select_cb,
            rows_getter=lambda: list(rows_box),
            focus_getter=lambda: focus_box[0],
            stim_getter=lambda: stim,
            comfort_getter=lambda: comfort,
            malady_lines=malady_lines,
            effect_of=effect_of or (lambda a, b, n: ""),
            safe_risk_cap=lambda: safe_risk_cap,
        )
        bar.show()
        bars.append(bar)
        qapp.processEvents()
        return bar, rows_box, focus_box, picked

    yield _make
    for bar in bars:
        bar.hide()
        bar.close()
        bar.deleteLater()
    qapp.processEvents()


# ── 1. empty / no-compatible states ────────────────────────────────────────
def test_no_rows_hides_the_banner(make_bar):
    bar, _, _, _ = make_bar(rows=[])

    bar.update_best()

    assert not bar._btn_best.isVisible()
    assert not bar._btn_safe.isVisible()
    assert bar._best_row is None


def test_no_focus_hides_the_banner(make_bar):
    focus = make_cat("Focus", 0)
    rows = [pair(make_row(make_cat("Meeko", 1), focus))]
    bar, _, _, _ = make_bar(rows=rows, focus=None)

    bar.update_best()

    assert not bar._btn_best.isVisible()
    assert not bar._btn_safe.isVisible()
    assert bar._best_row is None


def test_only_incompatible_rows_hide_the_banner(make_bar):
    focus = make_cat("Focus", 0)
    rows = [pair(make_row(make_cat("Blocked", 1), focus, compatible=False))]
    bar, _, _, _ = make_bar(rows=rows, focus=focus)

    bar.update_best()

    assert not bar._btn_best.isVisible()
    assert bar._best_row is None


def test_empty_rows_list_from_the_host_is_safe(make_bar):
    # ``rows_getter() or []`` guards a None/empty return from the host.
    bar, _, _, _ = make_bar(rows=[])
    bar._rows_getter = lambda: None

    bar.update_best()

    assert not bar._btn_best.isVisible()


# ── 2. the happy path: best pick + banner text ─────────────────────────────
def test_shows_the_best_pick_with_label_risk_sevens_and_coi(make_bar):
    focus = make_cat("Focus", 0)
    strong = make_cat("Meeko", 11)
    weak = make_cat("Bo", 22)
    rows = [
        pair(make_row(weak, focus, sevens=1.0, risk=2.0, coi=0.02)),
        pair(make_row(strong, focus, sevens=5.0, risk=2.0, coi=0.05)),
    ]
    bar, _, _, _ = make_bar(rows=rows, focus=focus)

    bar.update_best()

    assert bar._btn_best.isVisible()
    assert bar._btn_safe.isVisible()
    assert bar._best_row.partner is strong
    text = bar._btn_best.text()
    assert text.startswith("⭐ Best match")
    assert "Meeko" in text
    assert "Risk 2.0%" in text
    assert "≥7 ≈5.0" in text
    assert "COI 5.0%" in text
    assert "⚠" not in text          # low risk, good nightly chance


def test_incompatible_rows_are_ignored_in_the_pick(make_bar):
    focus = make_cat("Focus", 0)
    blocked = make_cat("Blocked", 1)
    ok = make_cat("Meeko", 2)
    rows = [
        pair(make_row(blocked, focus, sevens=9.0, compatible=False)),  # tempting
        pair(make_row(ok, focus, sevens=1.0)),
    ]
    bar, _, _, _ = make_bar(rows=rows, focus=focus)

    bar.update_best()

    assert bar._best_row.partner is ok
    assert "Meeko" in bar._btn_best.text()


def test_tie_keeps_the_first_row_the_host_lists(make_bar):
    # ``recommend`` keeps the first best when scores are equal.
    focus = make_cat("Focus", 0)
    first = make_cat("First", 1)
    second = make_cat("Second", 2)
    rows = [
        pair(make_row(first, focus, sevens=3.0)),
        pair(make_row(second, focus, sevens=3.0)),
    ]
    bar, _, _, _ = make_bar(rows=rows, focus=focus)

    bar.update_best()

    assert bar._best_row.partner is first


# ── 3. click callback carries the selected row's db key ────────────────────
def test_click_passes_the_selected_rows_db_key(make_bar):
    focus = make_cat("Focus", 0)
    partner = make_cat("Meeko", 4242)
    bar, _, _, picked = make_bar(rows=[pair(make_row(partner, focus))],
                                 focus=focus)

    bar.update_best()
    bar._btn_best.click()

    assert picked == [4242]


def test_click_after_clear_is_inert(make_bar):
    focus = make_cat("Focus", 0)
    partner = make_cat("Meeko", 4242)
    bar, _, _, picked = make_bar(rows=[pair(make_row(partner, focus))],
                                 focus=focus)
    bar.update_best()
    bar.clear()

    bar._btn_best.click()

    assert picked == []


# ── 4. tooltip: breakdown + malady lines ───────────────────────────────────
def test_tooltip_contains_breakdown_and_click_hint(make_bar):
    focus = make_cat("Focus", 0)
    partner = make_cat("Meeko", 1)
    bar, _, _, _ = make_bar(rows=[pair(make_row(partner, focus))], focus=focus)

    bar.update_best()

    tip = bar._btn_best.toolTip()
    assert "Why this pick:" in tip
    assert "≥7 stats:" in tip
    assert "expected avg:" in tip
    assert "nightly attempt:" in tip
    assert "risk:" in tip
    assert "score:" in tip
    assert tip.rstrip().endswith("Click to select this partner.")


def test_malady_lines_are_appended_to_the_tooltip(make_bar):
    focus = make_cat("Focus", 0)
    partner = make_cat("Meeko", 1)
    row = make_row(partner, focus)
    seen = []

    def malady_lines(r, stim, effect):
        seen.append((r, stim, effect))
        return ["Carries: itchy skin", "Shared defect: short tail"]

    bar, _, _, _ = make_bar(rows=[pair(row)], focus=focus, stim=37.0,
                            malady_lines=malady_lines)

    bar.update_best()

    tip = bar._btn_best.toolTip()
    assert "Carries: itchy skin" in tip
    assert "Shared defect: short tail" in tip
    assert seen and seen[0][0] is row
    assert seen[0][1] == 37.0


def test_no_malady_lines_keeps_the_tooltip_clean(make_bar):
    focus = make_cat("Focus", 0)
    bar, _, _, _ = make_bar(rows=[pair(make_row(make_cat("Meeko", 1), focus))],
                            focus=focus, malady_lines=lambda r, s, e: [])

    bar.update_best()

    assert "Carries:" not in bar._btn_best.toolTip()


# ── 5. warning suffixes ────────────────────────────────────────────────────
def test_high_risk_warning_appended(make_bar):
    focus = make_cat("Focus", 0)
    rows = [pair(make_row(make_cat("Risky", 1), focus, risk=50.0))]
    bar, _, _, _ = make_bar(rows=rows, focus=focus)

    bar.update_best()

    assert "⚠ high risk" in bar._btn_best.text()


def test_high_risk_warning_boundary_is_strict(make_bar):
    focus = make_cat("Focus", 0)
    row = make_row(make_cat("Edge", 1), focus, risk=_HIGH_RISK_PCT)
    rows = [pair(row)]
    bar, _, _, _ = make_bar(rows=rows, focus=focus)

    bar.update_best()
    assert "⚠ high risk" not in bar._btn_best.text()

    row.risk_pct = _HIGH_RISK_PCT + 0.1
    bar.update_best()
    assert "⚠ high risk" in bar._btn_best.text()


def test_breeds_rarely_warning_appended(make_bar):
    focus = make_cat("Focus", 0)
    rows = [pair(make_row(make_cat("Shy", 1), focus, game_compat=0.1))]
    bar, _, _, _ = make_bar(rows=rows, focus=focus, comfort=0.0)

    bar.update_best()

    assert "⚠ breeds rarely" in bar._btn_best.text()


def test_night_chance_warning_boundary_is_strict(make_bar, monkeypatch):
    focus = make_cat("Focus", 0)
    rows = [pair(make_row(make_cat("Shy", 1), focus))]
    bar, _, _, _ = make_bar(rows=rows, focus=focus)

    monkeypatch.setattr(_bm, "_night_chance",
                        lambda v, c=0.0: _LOW_NIGHT_CHANCE)
    bar.update_best()
    assert "⚠ breeds rarely" not in bar._btn_best.text()

    monkeypatch.setattr(_bm, "_night_chance",
                        lambda v, c=0.0: _LOW_NIGHT_CHANCE - 1e-9)
    bar.update_best()
    assert "⚠ breeds rarely" in bar._btn_best.text()


# ── 6. safe mode ───────────────────────────────────────────────────────────
def test_safe_mode_round_trip(make_bar):
    bar, _, _, _ = make_bar()

    assert bar.is_safe_mode() is False
    assert bar._btn_safe.isChecked() is False
    assert bar._btn_safe.text() == _SAFE_LABEL

    bar.set_safe_mode(True)
    assert bar.is_safe_mode() is True
    assert bar._btn_safe.isChecked() is True

    bar.set_safe_mode(False)
    assert bar.is_safe_mode() is False
    assert bar._btn_safe.isChecked() is False


def test_set_safe_mode_does_not_recompute(make_bar):
    bar, _, _, _ = make_bar()
    calls = []
    bar.update_best = lambda: calls.append(True)

    bar.set_safe_mode(True)
    assert calls == []

    # Signals are unblocked afterwards, so a real toggle still recomputes.
    bar._btn_safe.setChecked(False)
    assert calls == [True]


def test_safe_mode_picks_within_cap_and_switches_the_label(make_bar):
    focus = make_cat("Focus", 0)
    risky = make_cat("Risky", 1)
    safe = make_cat("Safe", 2)
    rows = [
        pair(make_row(risky, focus, sevens=6.0, risk=40.0, coi=0.30)),
        pair(make_row(safe, focus, sevens=1.0, risk=5.0, coi=0.01)),
    ]
    bar, _, _, _ = make_bar(rows=rows, focus=focus, safe_risk_cap=15.0)

    bar.set_safe_mode(True)
    bar.update_best()
    assert bar._best_row.partner is safe
    assert bar._btn_best.text().startswith("🛡 Safe best")
    assert "Safe" in bar._btn_best.text()
    assert "Risk 5.0%" in bar._btn_best.text()

    bar.set_safe_mode(False)
    bar.update_best()
    assert bar._best_row.partner is risky
    assert bar._btn_best.text().startswith("⭐ Best match")


def test_safe_mode_falls_back_and_labels_when_nothing_is_safe(make_bar):
    focus = make_cat("Focus", 0)
    only = make_cat("Only", 1)
    rows = [pair(make_row(only, focus, sevens=4.0, risk=40.0))]
    bar, _, _, _ = make_bar(rows=rows, focus=focus, safe_risk_cap=15.0)

    bar.set_safe_mode(True)
    bar.update_best()

    assert bar._best_row.partner is only
    assert bar._btn_best.text().startswith("⭐ Best (no ≤15% risk partner)")
    assert "⚠ high risk" in bar._btn_best.text()


@pytest.mark.parametrize("risk,safe", [(15.0, True), (15.01, False)])
def test_safe_cap_boundary_is_inclusive(make_bar, risk, safe):
    focus = make_cat("Focus", 0)
    edge = make_cat("Edge", 1)
    risky = make_cat("Risky", 2)
    rows = [
        pair(make_row(edge, focus, sevens=1.0, risk=risk)),
        pair(make_row(risky, focus, sevens=6.0, risk=40.0)),
    ]
    bar, _, _, _ = make_bar(rows=rows, focus=focus, safe_risk_cap=15.0)

    bar.set_safe_mode(True)
    bar.update_best()

    if safe:
        assert bar._best_row.partner is edge
        assert bar._btn_best.text().startswith("🛡 Safe best")
    else:
        assert bar._best_row.partner is risky
        assert bar._btn_best.text().startswith(
            "⭐ Best (no ≤15% risk partner)")


def test_safe_mode_honours_a_non_default_cap(make_bar):
    # The cap comes from the host (settings ``safe_risk_cap``); selection must
    # follow it, not the 15 % default. (The banner's literal "≤15%" wording is
    # a separate, documented finding.)
    focus = make_cat("Focus", 0)
    moderate = make_cat("Moderate", 1)
    rows = [pair(make_row(moderate, focus, sevens=1.0, risk=20.0))]
    bar, _, _, _ = make_bar(rows=rows, focus=focus, safe_risk_cap=30.0)

    bar.set_safe_mode(True)
    bar.update_best()

    assert bar._best_row.partner is moderate
    assert bar._btn_best.text().startswith("🛡 Safe best")


def test_toggling_the_checkbox_recomputes_the_pick(make_bar):
    focus = make_cat("Focus", 0)
    risky = make_cat("Risky", 1)
    safe = make_cat("Safe", 2)
    rows = [
        pair(make_row(risky, focus, sevens=6.0, risk=40.0)),
        pair(make_row(safe, focus, sevens=1.0, risk=5.0)),
    ]
    bar, _, _, _ = make_bar(rows=rows, focus=focus, safe_risk_cap=15.0)
    bar.update_best()
    assert bar._best_row.partner is risky

    bar._btn_safe.click()

    assert bar.is_safe_mode() is True
    assert bar._best_row.partner is safe
    assert bar._btn_best.text().startswith("🛡 Safe best")


# ── 7. clear ───────────────────────────────────────────────────────────────
def test_clear_hides_the_banner_and_forgets_the_pick(make_bar):
    focus = make_cat("Focus", 0)
    bar, _, _, _ = make_bar(rows=[pair(make_row(make_cat("Meeko", 1), focus))],
                            focus=focus)
    bar.update_best()
    assert bar._btn_best.isVisible()
    assert bar._btn_safe.isVisible()

    bar.clear()

    assert not bar._btn_best.isVisible()
    assert not bar._btn_safe.isVisible()
    assert bar._best_row is None


def test_clear_then_new_rows_shows_again(make_bar):
    focus = make_cat("Focus", 0)
    bar, rows_box, _, _ = make_bar(rows=[], focus=focus)
    bar.update_best()
    assert not bar._btn_best.isVisible()

    rows_box.append(pair(make_row(make_cat("Meeko", 1), focus)))
    bar.update_best()

    assert bar._btn_best.isVisible()
    assert bar._best_row.partner.name == "Meeko"
