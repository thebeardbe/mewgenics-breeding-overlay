"""FocusedCatPanel — the summary card for the cat being analysed.

Extracted from ``PaletteWindow`` (refactor step 3): owns the name / meta /
stats / lovers / health labels and their rendering. The window feeds it a
cat plus an optional gpak-effect lookup; it has no knowledge of the rest of
the overlay.

Security rule (unchanged): every label that can hold save-derived text is
set to PlainText — never rich text — so hostile cat/disorder/defect names
can never render as HTML. ``_stats_html`` is the only rich label and builds
its own markup from stat values only.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from mewgenics_overlay.core.maladies import defect_lines, sexuality_label
from mewgenics_overlay.core.session import STAT_NAMES, Cat, display_location
from mewgenics_overlay.ui import theme as _theme


def _stats_html(cat: Cat) -> str:
    parts = []
    for s in STAT_NAMES:
        v = cat.base_stats[s]
        color = (_theme.C_GOOD if v >= 7
                 else (_theme.C_TEXT if v >= 4 else _theme.C_STAT_LOW))
        parts.append(f'<span style="color:{color}"><b>{s}</b> {v}</span>')
    return "   ".join(parts)


class FocusedCatPanel(QWidget):
    """Name/meta/stats/lovers/health summary for one focused cat."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        self._cat_name = QLabel("No cat selected")
        self._cat_name.setObjectName("headerName")
        self._cat_meta = QLabel("")
        self._cat_meta.setObjectName("muted")
        self._cat_stats = QLabel("")
        self._cat_stats.setTextFormat(Qt.TextFormat.RichText)  # our own html
        self._cat_lovers = QLabel("")
        self._cat_lovers.setObjectName("muted")
        self._cat_health = QLabel("")
        # save-derived text => plain text only (see module docstring)
        for lbl in (self._cat_name, self._cat_meta, self._cat_lovers,
                    self._cat_health):
            lbl.setTextFormat(Qt.TextFormat.PlainText)
        self._cat_health.setWordWrap(True)
        self.restyle()
        self._cat_health.setToolTip(_theme.wrap_tooltip(
            "Things this cat carries that can be passed on to kittens.\n"
            "• Disorders — a 15% chance per parent that carries one of "
            "passing a random disorder to the kitten.\n"
            "• Birth defects — kittens inherit these per body part; pick a "
            "partner to see the exact odds for that pairing."
        ))

        for w in (self._cat_name, self._cat_meta, self._cat_stats,
                  self._cat_lovers, self._cat_health):
            lay.addWidget(w)
        self._extras = lay

    def append_row(self, widget: QWidget) -> None:
        """Allow the window to slot extra controls under the health line."""
        self._extras.addWidget(widget)

    def restyle(self, zoom: float | None = None) -> None:
        """Re-derive the health label style for the current theme + zoom so
        the ⚠ defects/disorders text grows with the UI."""
        if zoom is not None:
            _theme.set_zoom(zoom)
        self._cat_health.setStyleSheet(
            f"color:{_theme.C_WARN}; "
            f"font-size:{_theme.zoom_px(11)}px;")

    # ── rendering ─────────────────────────────────────────────────────────
    def clear(self) -> None:
        self._cat_name.setText("No cat selected")
        self._cat_meta.setText("")
        self._cat_stats.setText("")
        self._cat_lovers.setText("")
        self._cat_health.setText("")

    def show_cat(self, cat: Cat,
                 effect_for=None) -> None:
        """Fill the card for *cat*. ``effect_for(group_key, mutation_id)``
        returns the gpak effect text for a defect (None/absent → no effects
        shown)."""
        gen = "stray" if cat.generation == 0 else f"gen {cat.generation}"
        _gender = (cat.gender or "?").lower()
        _sex = sexuality_label(getattr(cat, "sexuality_raw", None)) \
            if _gender in ("male", "female") else None
        meta = f"{_theme.gender_badge(cat.gender)} {cat.gender}" + \
            (f" · {_sex}" if _sex else "") + \
            f" · {display_location(cat)} · {gen}"
        if cat.age is not None:
            meta += f" · {cat.age}d"
        if cat.inbredness > 0.03:
            meta += f" · inbred {cat.inbredness * 100:.0f}%"
        pin = f"{_theme.PIN} " if getattr(cat, "is_pinned", False) else ""
        self._cat_name.setText(f"{pin}{cat.name}")
        self._cat_name.setToolTip(_theme.wrap_tooltip(
            f"{cat.name}  (save id {cat.db_key})\n"
            "The cat you are analysing. Double-click a partner to switch "
            "the analysis to them."
        ))
        self._cat_meta.setText(meta)
        self._cat_meta.setToolTip(_theme.wrap_tooltip(
            f"{cat.gender} · {display_location(cat)} · "
            f"generation {cat.generation} (0 = stray, each generation adds "
            f"depth and shared ancestry)"
            + (f"\nSexuality: {_sex}. "
               "Bi/gay cats can breed with the same sex." if _sex else "")
            + (f" · age {cat.age} days" if cat.age is not None else "")
            + (f"\nInbreeding coefficient {cat.inbredness * 100:.1f}% = kinship "
               "of this cat's parents — flagged above 3%."
               if cat.inbredness > 0.03 else "")
        ))
        self._cat_stats.setText(_stats_html(cat))
        self._cat_stats.setToolTip(_theme.wrap_tooltip(
            "Base stats (STR DEX CON INT SPD CHA LCK, 0–7) — the birth stats "
            "kittens inherit from. Green = 7. These drive breeding math; "
            "gear/mod bonuses are not shown here."
        ))
        lover_txt = ", ".join(l.name for l in getattr(cat, "lovers", []))
        self._cat_lovers.setText(
            f"{_theme.HEART} in love with: {lover_txt}"
            if lover_txt else "no lovers"
        )
        self._cat_lovers.setToolTip(_theme.wrap_tooltip(
            "In-game relationships.\n"
            "Being lovers gives the pair a bonus when breeding.\n"
            "If a cat already loves someone else, picking a different "
            "partner can complicate things later." if lover_txt
            else "This cat is not in love with anyone right now."
        ))
        # traits this cat already carries (defects / disorders)
        disorders = list(getattr(cat, "disorders", None) or [])
        own_defects = defect_lines(cat)
        health_bits = []
        if disorders:
            health_bits.append("disorders: " + ", ".join(disorders))
        if own_defects:
            health_bits.append("birth defects: " + ", ".join(own_defects))
        self._cat_health.setText(
            f"{_theme.WARN} " + " · ".join(health_bits)
            if health_bits else ""
        )
        self._cat_health.setToolTip(_theme.wrap_tooltip(
            self._health_tooltip(cat, disorders, own_defects, effect_for)))

    def _health_tooltip(self, cat: Cat, disorders, own_defects,
                        effect_for) -> str:
        """Hover text for the ⚠ health line: carried traits + in-game effects
        (effects come from resources.gpak when available)."""
        if not (disorders or own_defects):
            return "No birth defects or disorders."
        lines = [f"{cat.name} carries:"]
        if disorders:
            lines.append("• disorders: " + ", ".join(disorders)
                         + " — each parent with a disorder has a 15% chance "
                           "to pass one")
        if own_defects:
            lines.append("• birth defects (odds depend on the partner — "
                         "select one to see them):")
            seen: set = set()
            for e in (getattr(cat, "visual_mutation_entries", None) or []):
                if not e.get("is_defect"):
                    continue
                name = e.get("name")
                if not name or name in seen:
                    continue
                seen.add(name)
                eff = (effect_for(e.get("group_key"), e.get("mutation_id"))
                       if effect_for is not None else "")
                lines.append("    · " + name + (f" — {eff}" if eff else ""))
        return "\n".join(lines)
