"""Partner-table pure core: column layout, header tips, cell formatters.

Non-widget half of the partner table (constants + text building) so the
interactive PartnerTableWidget (step 2b) stays focused on Qt rendering.
Pure functions only - no Qt widgets here.
"""

from __future__ import annotations

from mewgenics_overlay.core.maladies import (
    ASYMMETRIC_GROUPS,
    defect_inheritance_rows,
    disorder_summary,
    sexuality_label,
    side_text,
)
from mewgenics_overlay.core.recommend import better_stat_expectation
from mewgenics_overlay.core.session import STAT_NAMES, PartnerRow, display_location
from . import theme as _theme

_COLS = ["Cat", "Family", "GenΔ", "Room", "Risk", "Night", "Exp/stat", "≥7", "Defects", "Note"]

# Column explanations shown as tooltips when hovering each header.
# Column explanation tooltips, written as short human paragraphs so each
# idea, legend line or note starts on its own line.
_COL_TIP_PARAS = [
    # Cat
    [
        "The partner cat being compared. 📌 = pinned (kept for breeding). "
        "Icons right after the name: ♂/♀/? = gender · ❤️ straight · 💗 bi · 🌈 "
        "gay (orientation).",
        "Rows marked ✗ can't breed with the cat you picked - the Note "
        "column says why.",
        "Double-click a row to look at things from that cat's side instead.",
        "Tip: click any header to sort, click again to reverse, and a third "
        "click brings back the default order.",
    ],
    # Family
    [
        "How the two cats are related, found by tracing shared ancestors "
        "up to 9 generations back.",
        "Shown as simple names: parent/child, sibling, aunt/uncle, "
        "1st cousin, … or 'unrelated'.",
        "Shared family history is what drives inbreeding (the Risk column).",
        "Very distant shared ancestors barely count - each generation back "
        "halves the effect, so ~5 generations back is effectively nothing.",
    ],
    # Gen delta
    [
        "How far apart the cats sit in the family tree: the generation of "
        "the cat you picked minus theirs.",
        "A positive gap means the partner comes from an older line, a "
        "negative one a younger line.",
        "Read it together with Family: a big gap with 'unrelated' is often "
        "the safest pairing in a deep colony.",
    ],
    # Room
    [
        "Where this cat is right now.",
        "In a named room: can take part in overnight breeding.",
        "On Adventure: away from the house until the next day.",
        "Outside house: standing on screen, not in a room or adventure box.",
    ],
    # Risk
    [
        "How likely the kitten is to be born with a problem (a disorder or "
        "a birth defect), as a percentage.",
        "Two strangers sit near the base ~2%.",
        "The closer the parents are related, the higher it climbs.",
        "Colour key - green: low (≤ 5%) · amber: medium (5–12%) · "
        "red: high (> 12%).",
    ],
    # Chance (Nightly)
    [
        "How likely the pair is to breed on a given night - one number that "
        "already folds in the game's two nightly rolls.",
        "A higher room Comfort nudges it up a little.",
        "Below 5% the game won't even attempt the pair.",
        "Very high Comfort saturates the per-roll odds - the tool shows those "
        "as ≥95% instead of promising a 100% chance.",
        "Colour key - green: ≈5%+ chance per night · amber: below that "
        "(the game may still attempt pairs above its own 0.05 compat line).",
    ],
    # Exp/stat
    [
        "The size each kitten stat is likely to end up at, on a 0–7 scale "
        "(the average across all seven stats).",
        "Higher room Stimulation makes kittens inherit the better parent's "
        "stat more often.",
        "Click a row to see each stat's possible range in detail below.",
    ],
    # >=7
    [
        "How many of the kitten's stats should come out as a perfect 7.",
        "A stat where both parents are already 7 is a guaranteed one and "
        "counts fully; others count by how reachable they are.",
    ],
    # Defects
    [
        "The birth defects these parents already carry, and whether the "
        "kitten will inherit them.",
        "✓ = both parents carry it - the kitten will get it.",
        "A % = one parent carries it - that's the kitten's chance at the "
        "selected room's Stimulation.",
        "Hover a cell to see which side/part it affects, whether it comes "
        "from one shared family line, and what the defect actually does.",
    ],
    # Note
    [
        "Extra notes per row.",
        "♥ = in love with the cat you picked · ♥♥ = mutual lovers.",
        "'hates you' = the two dislike each other.",
        "Blocked rows explain why breeding can't happen.",
        "'kittens' = how many this pair has produced, and how many are "
        "still around (not dead or donated).",
    ],
]
COL_TIPS = ["\n".join(paras) for paras in _COL_TIP_PARAS]


# ── column indexes (keep in sync with _COLS) ─────────────────────
(COL_CAT, COL_FAMILY, COL_GEN_DELTA, COL_ROOM, COL_RISK, COL_CHANCE,
 COL_EXP, COL_SEVEN, COL_DEFECTS, COL_NOTE) = range(10)
assert len(_COLS) == 10


_ORIENT_ICONS = {"straight": "❤️", "bi": "💗", "gay": "🌈"}


def _cat_glyphs(cat) -> str:
    """Compact gender + orientation icons shown beside a partner's name.

    Gender: ♂ male · ♀ female · ? neutral. Orientation (only for male /
    female cats): ❤️ straight · 💗 bi · 🌈 gay - see the Cat column header
    tooltip for the legend. Purely cosmetic; sorting ignores the glyphs.
    """
    g = _theme.gender_badge(getattr(cat, "gender", "?"))
    if g == "?":
        return g
    label = sexuality_label(getattr(cat, "sexuality_raw", None))
    return g + _ORIENT_ICONS.get(label, _ORIENT_ICONS["straight"])


def _note_text(row, kids: list[str]) -> str:
    """The human-readable Note cell contents for a partner row."""
    parts = []
    if row.mutual_lover:
        parts.append("♥♥")
    elif row.is_lover:
        parts.append("♥")
    if row.is_hater:
        parts.append("hates you")
    if not row.compatible and row.reason:
        parts.append(row.reason)
    kittens = _kittens_label(row)
    if kittens:
        parts.append(kittens)
    return "  ".join(parts)


def _kittens_label(row) -> str:
    """e.g. '1 kitten', '3 kittens', '3 kittens, 1 available',
    '3 kittens, none available' - 'available' means still in the house /
    on adventures (dead or donated/gone kittens are excluded)."""
    total = int(getattr(row, "kitty_total", 0) or 0)
    if total <= 0:
        return ""
    noun = "1 kitten" if total == 1 else f"{total} kittens"
    available = int(getattr(row, "kitty_available", total) or 0)
    if available < total:
        noun += ", none available" if available == 0 \
            else f", {available} available"
    return noun


def _defect_short(name: str) -> str:
    return name.replace(" Birth Defect", "") or name


def _defect_rows_of(row, stimulation: float = 50.0):
    """Inheritance rows for a partner row's carried defects.

    The background worker precomputes these once per pair (``row.defect_rows``
    - pure function of the parents + COI + room Stimulation) and every
    render/tooltip/recommend pass reuses them instead of re-walking shared
    ancestry on the UI thread. Fresh computation here is only a fallback for
    rows that never went through the worker.
    """
    rows = getattr(row, "defect_rows", None)
    if rows is not None:
        return rows
    factors = row.pair_factors
    if factors is None:
        return []
    return defect_inheritance_rows(factors.cat_a, factors.cat_b, row.coi,
                                   stimulation=stimulation)


def _defects_summary(row, stimulation: float = 50.0) -> str:
    """Compact Defects cell text: shared defects as '✓', single-carrier as %."""
    parts = []
    for d in _defect_rows_of(row, stimulation):
        short = _defect_short(d.name)
        parts.append(short + (f" {_theme.CHECK}" if len(d.carriers) == 2
                              else f" {_theme.APPROX}{d.chance_pct:.0f}%"))
    return "; ".join(parts)


def _any_defect_guaranteed(row, stimulation: float = 50.0) -> bool:
    """True when the pair carries a defect on BOTH parents (guaranteed pass)."""
    return any(len(d.carriers) == 2
               for d in _defect_rows_of(row, stimulation))




CHANCE_DISPLAY_CAP = 95.0   # never advertise a guaranteed breed


def _fmt_compat(v: float) -> str:
    return f"{v:.3f}"


def _fmt_chance(v: float, comfort: float = 0.0) -> str:
    """Nightly breeding chance % - both of the game's rolls folded into one.

    Comfort-rich rooms can saturate the per-roll odds (chance → 100 % in the
    model); we display those as "≥95 %" rather than promising a guarantee.
    """
    pct = night_chance(v, comfort) * 100.0
    if pct >= CHANCE_DISPLAY_CAP:
        return f"≥{CHANCE_DISPLAY_CAP:.0f}%"
    return f"{pct:.0f}%"


def _roll_chance(v: float, comfort: float = 0.0) -> float:
    """Per-roll success chance (0..1): compat × √(1 + 0.1×Comfort)."""
    roll = v * (1.0 + 0.1 * max(0.0, comfort)) ** 0.5
    return max(0.0, min(1.0, roll))


def night_chance(v: float, comfort: float = 0.0) -> float:
    """Chance the pair breeds on a given night: both of the game's two nightly
    rolls must succeed, so it is the per-roll chance squared."""
    roll = _roll_chance(v, comfort)
    return roll * roll




# ── interactive partner table (step 2b) ─────────────────────────────────────
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtWidgets import QTableWidget

# column widths, in _COLS order (GenΔ & Exp wide enough for headers/icons)
_COL_WIDTHS = [130, 104, 66, 74, 56, 58, 88, 40, 132]


class PartnerTableWidget(QTableWidget):
    """The partners table: owns its configuration, rendered rows and the
    tri-state per-column sort.

    Step 2b (part 1): creation/config + row storage + sort state live here;
    rendering & tooltip building still live in PaletteWindow and call back
    through ``set_rows``/``toggle_sort``/``show_sort_indicator``. The next
    pass moves the rendering itself into this class.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(0, len(_COLS), parent)
        self.setHorizontalHeaderLabels(_COLS)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hdr = self.horizontalHeader()
        hdr.setStretchLastSection(True)
        for i, w in enumerate(_COL_WIDTHS):
            self.setColumnWidth(i, w)
        self.setSortingEnabled(False)          # manual tri-state sort
        self.rows: list = []                   # currently rendered (row, kids)
        self.sort_col: int | None = None
        self.sort_dir: str = "asc"

    def scale_columns(self, zoom: float) -> None:
        for i, w in enumerate(_COL_WIDTHS):
            self.setColumnWidth(i, max(1, int(w * zoom)))

    def set_header_tooltips(self, tips, wrap) -> None:
        for i, tip in enumerate(tips):
            item = self.horizontalHeaderItem(i)
            if item is not None:
                item.setToolTip(wrap(tip))

    def set_rows(self, rows) -> None:
        self.rows = list(rows)

    def reset_sort(self) -> None:
        """New data → back to the engine's default order."""
        self.sort_col = None
        self.sort_dir = "asc"

    def toggle_sort(self, col: int) -> None:
        """Tri-state: asc → desc → default order."""
        if self.sort_col == col:
            if self.sort_dir == "asc":
                self.sort_dir = "desc"
            else:
                self.reset_sort()
        else:
            self.sort_col = col
            self.sort_dir = "asc"

    def show_sort_indicator(self) -> None:
        hdr = self.horizontalHeader()
        if self.sort_col is None:
            hdr.setSortIndicatorShown(False)
            return
        order = (Qt.SortOrder.AscendingOrder if self.sort_dir == "asc"
                 else Qt.SortOrder.DescendingOrder)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(self.sort_col, order)


    def _col_key(self, col: int, row: PartnerRow, kids: list[str]):
        """Sort key for a column (text columns sort as strings, rest numeric)."""
        p = row.partner
        if col == COL_CAT:
            return p.name.lower()
        if col == COL_FAMILY:
            return row.relation.label.lower()
        if col == COL_GEN_DELTA:
            return row.relation.gen_gap
        if col == COL_ROOM:
            return display_location(p).lower()
        if col == COL_RISK:
            return row.risk_pct
        if col == COL_CHANCE:
            return row.game_compat
        if col == COL_EXP:
            return row.expected_avg
        if col == COL_SEVEN:
            return row.seven_plus_total
        if col == COL_DEFECTS:
            return _defects_summary(row, self._stim).lower()
        return _note_text(row, kids).lower()

    def _order_rows(self) -> list:
        """Compatible partners first (per active column), blocked rows after."""
        if self.sort_col is None:
            return list(self.rows)          # default safe-first order from engine
        rev = self.sort_dir == "desc"
        good = [e for e in self.rows if e[0].compatible]
        blocked = [e for e in self.rows if not e[0].compatible]
        good.sort(key=lambda e: self._col_key(self.sort_col, e[0], e[1]),
                  reverse=rev)
        # Blocked rows follow the same asc/desc sort as the rest, so a
        # column click behaves consistently for everyone (they previously
        # kept a fixed order unless a text column was active).
        blocked.sort(key=lambda e: self._col_key(self.sort_col, e[0], e[1]),
                     reverse=rev)
        return good + blocked

    def redraw(self, stimulation: float = 50.0, comfort: float = 0.0,
               effect_of=None) -> None:
        """Rebuild every row's cells from self.rows using the current
        room Stimulation/Comfort and the gpak effect-text provider."""
        self._stim = stimulation
        self._comfort = comfort
        self._effect_of = effect_of

        ordered = self._order_rows()
        self.setRowCount(0)
        self.setRowCount(len(ordered))
        for r_i, (row, kids) in enumerate(ordered):
            p = row.partner
            ok = row.compatible
            _pin = f"{_theme.PIN} " if getattr(p, "is_pinned", False) else ""
            glyphs = _cat_glyphs(p)
            nm = f"{_pin}{p.name} {glyphs}"
            name = nm if ok else f"{nm}  ({_theme.BLOCK_MARK})"
            rel = row.relation

            it_name = QTableWidgetItem(name)
            it_name.setData(Qt.ItemDataRole.UserRole, (row, kids))
            it_name.setToolTip(self._partner_tooltip(row, kids))

            it_family = QTableWidgetItem(rel.label)
            it_family.setToolTip(self._family_tooltip(row))
            it_gap = QTableWidgetItem(f"{rel.gen_gap:+d}" if rel.gen_gap else "0")
            it_gap.setToolTip(
                f"Generation gap for {p.name}: focused gen − partner gen "
                f"= {rel.gen_gap:+d}."
            )

            it_room = QTableWidgetItem(display_location(p))
            it_risk = QTableWidgetItem(f"{row.risk_pct:.1f}%" if ok else "-")
            it_comp = QTableWidgetItem(
                _fmt_chance(row.game_compat, self._comfort)
                if ok else "-")
            it_exp = QTableWidgetItem(f"{row.expected_avg:.2f}" if ok else "-")
            it_7 = QTableWidgetItem(f"{row.seven_plus_total:.1f}" if ok else "-")
            if not getattr(row, "defect_rows_ok", True):
                # Compute failed in the worker: show a distinct marker, not
                # an identical empty cell ("we don't know" vs "no defects").
                it_defects = QTableWidgetItem("-")
                it_defects.setToolTip(_theme.wrap_tooltip(
                    "Defect information is unavailable for this pair - "
                    "scoring failed (details in the log). The Risk column "
                    "remains the safer guide here."))
            else:
                defects_text = _defects_summary(row, self._stim)
                it_defects = QTableWidgetItem(defects_text)
                it_defects.setToolTip(
                    _theme.wrap_tooltip("\n".join(self.pair_malady_lines(row, self._stim))
                        or "Both parents clean.")
                )
            it_note = QTableWidgetItem(_note_text(row, kids))

            if ok:
                it_risk.setToolTip(
                    f"Birth-defect risk for this pair: {row.risk_pct:.1f}%."
                )
                _comfort = self._comfort
                it_comp.setToolTip(_theme.wrap_tooltip(
                    f"Nightly breeding chance: "
                    f"{_fmt_chance(row.game_compat, _comfort)}.\n"
                    "That's the answer to 'will they breed tonight?' - the "
                    "game's two rolls are already folded in."
                    + ("\nAbove the game's compat line (0.05) so the game "
                       "will try it - but amber below means under 5% per "
                       "night despite that." if row.game_compat > 0.05
                       else "\nBelow the game's compat line (0.05) - the "
                            "game won't attempt it.")
                ))
                proj = row.pair_factors.projection
                better = better_stat_expectation(row, self._stim)
                it_exp.setToolTip(
                    f"Expected offspring stat average: {row.expected_avg:.2f} / 7.\n"
                    + (f"The kitten takes the HIGHER of the two parents' "
                       f"values in ≈{better[0]:.1f} of {better[1]} differing "
                       f"stats (at Stim {self._stim:g}).\n"
                       if better else "")
                    + "Per-stat inheritance ranges for this pair:\n"
                    + "\n".join(
                        f"  {s}: {proj.stat_ranges[s][0]}–{proj.stat_ranges[s][1]}"
                        for s in STAT_NAMES
                    )
                )
                it_7.setToolTip(
                    f"Expected stats at 7 or higher: "
                    f"{row.seven_plus_total:.1f} of 7.\n"
                    "Locked 7s for this pair (both parents at 7): "
                    + (", ".join(proj.locked_stats) or "none")
                )
            else:
                for it in (it_risk, it_comp, it_exp, it_7):
                    it.setToolTip("No values - this pair cannot breed. "
                                  "Reason is in the Note column.")
            it_room.setToolTip(f"Current location of {p.name}.")
            it_note.setToolTip(
                (row.reason if (not ok and row.reason) else "")
                + (f"Existing kittens: {_kittens_label(row)}" if kids else "")
            )

            cells = [it_name, it_family, it_gap, it_room, it_risk, it_comp,
                     it_exp, it_7, it_defects, it_note]
            specials: dict = {}
            if ok:
                if rel.is_family:
                    specials[COL_FAMILY] = _theme.C_FAMILY      # related, breedable
                specials[COL_RISK] = _theme.risk_color(row.risk_pct)
                # Colour tracks the chance actually shown: green ≈ ≥5% per
                # night, amber below it (the game's own 0.05 *compat* gate is
                # separate - it only decides whether attempts happen at all).
                specials[COL_CHANCE] = (_theme.C_GOOD
                                        if night_chance(
                                            row.game_compat,
                                            self._comfort) >= 0.05
                                        else _theme.C_WARN)
                if getattr(row, "defect_rows_ok", True) and \
                        _any_defect_guaranteed(row, self._stim):
                    specials[COL_DEFECTS] = _theme.C_WARN       # inherited defects
            base_color = _theme.C_MUTED if not ok else _theme.C_TEXT
            for col, it in enumerate(cells):
                it.setForeground(QColor(specials.get(col, base_color)))
                self.setItem(r_i, col, it)
        self.show_sort_indicator()

    @staticmethod
    def _family_tooltip(row: PartnerRow) -> str:
        """Row-specific facts only; the COI weighting explanation lives in the
        Family column header tooltip."""
        rel = row.relation
        lines = [f"Relationship: {rel.label}",
                 f"Shared family history (COI): {row.coi * 100:.1f}%"]
        if rel.is_family:
            close = f"{rel.shared_recent} shared ancestor(s) close enough " \
                    "to matter"
            if rel.shared_ancestors > rel.shared_recent:
                close += f" (of {rel.shared_ancestors} in total)"
            lines.append(close)
            if row.direct_family:
                lines.append("Direct family - the game stops this pairing.")
            else:
                lines.append("Related, but allowed - this shared history "
                             "is what raises the Risk %.")
        else:
            lines.append("No shared family history that matters - the "
                         "safest kind of pairing.")
        lines.append("Longer explanation: hover the Family heading above.")
        return _theme.wrap_tooltip("\n".join(lines))

    def _partner_tooltip(self, row: PartnerRow, kids: list[str]) -> str:
        p = row.partner
        _pg = (p.gender or "?").lower()
        _pl = sexuality_label(getattr(p, "sexuality_raw", None)) \
            if _pg in ("male", "female") else None
        lines = [f"{p.name}  ({p.gender}"
                 + (f" · {_pl}" if _pl else "")
                 + f", {display_location(p)})"]
        rel = row.relation
        lines.append(f"Family: {rel.label} · Δgen {rel.gen_gap:+d} "
                     f"· COI {row.coi * 100:.1f}%")
        lines.append(f"Birth-defect risk: {row.risk_pct:.1f}%")
        lines.append(
            f"Breed attempt/night: {_fmt_chance(row.game_compat, self._comfort)} "
            f"(compat {row.game_compat:.3f} > 0.05)"
        )
        if row.compatible:
            proj = row.pair_factors.projection
            ranges = "  ".join(
                f"{s} {proj.stat_ranges[s][0]}–{proj.stat_ranges[s][1]}"
                for s in STAT_NAMES
            )
            lines.append(f"Expected kitten stats: {ranges}")
            lines.append(f"Expected ≥7 stats: {row.seven_plus_total:.1f}")
            better = better_stat_expectation(row, self._stim)
            if better:
                lines.append(
                    f"Takes the higher of the two parents' values in "
                    f"≈{better[0]:.1f} of {better[1]} differing stats"
                )
        malady = self.pair_malady_lines(row, self._stim)
        if malady:
            lines.append("")
            lines.extend(malady)
        if row.is_lover or row.mutual_lover:
            lines.append("They are lovers" if row.mutual_lover else "They like you")
        if kids:
            names = ", ".join(kids)
            label = _kittens_label(row)
            lines.append(f"Existing kittens together: {names}")
            if label:
                lines.append(f"   ({label})")
        lines.append("Double-click to analyse breeding from this cat.")
        return _theme.wrap_tooltip("\n".join(lines))

    def pair_malady_lines(self, row: PartnerRow,
                           stimulation: float = 50.0,
                           effect_of=None) -> list[str]:
        """Inheritance of traits the parents ALREADY carry (disorders exact,
        visual birth defects per body part, effects when the gpak is present).
        Empty when both parents are clean."""
        if row.pair_factors is None:
            return []
        a = row.pair_factors.cat_a          # focused cat
        b = row.pair_factors.cat_b          # partner
        dis = disorder_summary(a, b)
        lines: list[str] = []
        if dis["a"]:
            lines.append(f"⚠ {a.name} carries disorder(s): "
                         + ", ".join(dis["a"]))
        if dis["b"]:
            lines.append(f"⚠ {b.name} carries disorder(s): "
                         + ", ".join(dis["b"]))
        if dis["a"] or dis["b"]:
            lines.append(f"→ Kitten inherits ≥1 parent disorder: "
                         f"{dis['any_pct']:.0f}% "
                         f"(15% per parent that carries one)")
        rows = _defect_rows_of(row, stimulation)
        for drow in rows:
            asym = drow.group in ASYMMETRIC_GROUPS
            if len(drow.carriers) == 2:
                if not asym:
                    lines.append(f"→ {drow.name}: both parents carry it - "
                                 f"the kitten gets it (≈100%)")
                elif drow.same_line:
                    lines.append(
                        f"→ {drow.name}: both parents carry it from the SAME "
                        f"line → the kitten gets it on the same part/side "
                        f"(≈100%)"
                    )
                else:
                    lines.append(
                        f"→ {drow.name}: both parents carry it from DIFFERENT "
                        f"lines → the kitten gets it on the same side as one "
                        f"parent OR the opposite side (≈100%)"
                    )
                if asym:
                    sa = side_text(drow.slots_a)
                    sb = side_text(drow.slots_b)
                    if sa and sb:
                        lines.append(f"    ({a.name}: {sa} · {b.name}: {sb})")
            else:
                who = a.name if drow.carriers[0] == "a" else b.name
                where = side_text(drow.slots_a or drow.slots_b)
                loc = f", on {where}" if where else ""
                lines.append(
                    f"→ {drow.name} (carried by {who} only{loc}): "
                    f"≈{drow.chance_pct:.0f}% to pass at "
                    f"{stimulation:g} Stimulation"
                )
            provider = effect_of if effect_of is not None \
                else getattr(self, "_effect_of", None)
            effect = provider(a, b, drow.name) if provider else ""
            if effect:
                lines.append(f"    effect: {effect}")
        if rows and any(len(r.carriers) == 1 for r in rows):
            lines.append("(single-sided odds assume the other parent's matching "
                         "body part is normal; a 20% part-reroll can still "
                         "change one part)")
        return lines

