"""Partner-table pure core: column layout, header tips, cell formatters.

Non-widget half of the partner table (constants + text building) so the
interactive PartnerTableWidget (step 2b) stays focused on Qt rendering.
Pure functions only — no Qt widgets here.
"""

from __future__ import annotations

from mewgenics_overlay.core.maladies import (
    defect_inheritance_rows,
    sexuality_label,
)
from mewgenics_overlay.ui.theme import gender_badge
from mewgenics_overlay.vendor.save_parser import (
    _stimulation_inheritance_weight as _better_stat_weight,
)

STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]

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
        "Rows marked ✗ can't breed with the cat you picked — the Note "
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
        "Very distant shared ancestors barely count — each generation back "
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
        "Colour key — green: low (≤ 5%) · amber: medium (5–12%) · "
        "red: high (> 12%).",
    ],
    # Chance (Nightly)
    [
        "How likely the pair is to breed on a given night — one number that "
        "already folds in the game's two nightly rolls.",
        "A higher room Comfort nudges it up a little.",
        "Below 5% the game won't even attempt the pair.",
        "Very high Comfort saturates the per-roll odds — the tool shows those "
        "as ≥95% instead of promising a 100% chance.",
        "Colour key — green: ≈5%+ chance per night · amber: below that "
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
        "✓ = both parents carry it — the kitten will get it.",
        "A % = one parent carries it — that's the kitten's chance at the "
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
_COL_TIPS = ["\n".join(paras) for paras in _COL_TIP_PARAS]


# ── column indexes (keep in sync with _COLS) ─────────────────────
(COL_CAT, COL_FAMILY, COL_GEN_DELTA, COL_ROOM, COL_RISK, COL_CHANCE,
 COL_EXP, COL_SEVEN, COL_DEFECTS, COL_NOTE) = range(10)
assert len(_COLS) == 10


_ORIENT_ICONS = {"straight": "❤️", "bi": "💗", "gay": "🌈"}


def _cat_glyphs(cat) -> str:
    """Compact gender + orientation icons shown beside a partner's name.

    Gender: ♂ male · ♀ female · ? neutral. Orientation (only for male /
    female cats): ❤️ straight · 💗 bi · 🌈 gay — see the Cat column header
    tooltip for the legend. Purely cosmetic; sorting ignores the glyphs.
    """
    g = gender_badge(getattr(cat, "gender", "?"))
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
    '3 kittens, none available' — 'available' means still in the house /
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
    — pure function of the parents + COI + room Stimulation) and every
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
        parts.append(short + (" ✓" if len(d.carriers) == 2
                              else f" ≈{d.chance_pct:.0f}%"))
    return "; ".join(parts)


def _any_defect_guaranteed(row, stimulation: float = 50.0) -> bool:
    """True when the pair carries a defect on BOTH parents (guaranteed pass)."""
    return any(len(d.carriers) == 2
               for d in _defect_rows_of(row, stimulation))




CHANCE_DISPLAY_CAP = 95.0   # never advertise a guaranteed breed


def _fmt_compat(v: float) -> str:
    return f"{v:.3f}"


def _fmt_chance(v: float, comfort: float = 0.0) -> str:
    """Nightly breeding chance % — both of the game's rolls folded into one.

    Comfort-rich rooms can saturate the per-roll odds (chance → 100 % in the
    model); we display those as "≥95 %" rather than promising a guarantee.
    """
    pct = _night_chance(v, comfort) * 100.0
    if pct >= CHANCE_DISPLAY_CAP:
        return f"≥{CHANCE_DISPLAY_CAP:.0f}%"
    return f"{pct:.0f}%"


def _roll_chance(v: float, comfort: float = 0.0) -> float:
    """Per-roll success chance (0..1): compat × √(1 + 0.1×Comfort)."""
    roll = v * (1.0 + 0.1 * max(0.0, comfort)) ** 0.5
    return max(0.0, min(1.0, roll))


def _night_chance(v: float, comfort: float = 0.0) -> float:
    """Chance the pair breeds on a given night: both of the game's two nightly
    rolls must succeed, so it is the per-roll chance squared."""
    roll = _roll_chance(v, comfort)
    return roll * roll


def _better_stat_expectation(row, stimulation: float = 50.0):
    """(expected, differing) over the 7 stats for a partner row.

    ``expected`` is how many stats are expected to take the HIGHER parent's
    value at this room Stimulation (the calculator's "expected better stats",
    counted only over the stats the parents actually differ on); ``differing``
    is that count. Wiki rule: each stat takes one parent's value with
    P(better) = (100 + Stim) / (200 + |Stim|).
    """
    factors = row.pair_factors
    if factors is None:
        return None
    proj = factors.projection
    expected = 0.0
    differing = 0
    for s in STAT_NAMES:
        lo, hi = proj.stat_ranges[s]
        if hi > lo:
            differing += 1
            expected += _better_stat_weight(stimulation)
    return expected, differing


