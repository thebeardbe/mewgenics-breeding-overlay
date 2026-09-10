"""BestMatchBar - the ⭐ best-match banner and its safe-mode switch.

Extracted from ``PaletteWindow`` (god-file split, step 5): owns the big
best-match button, the "safe mode" checkbox and the recommendation banner's
text, tooltip and visibility.

Window-agnostic: the current rows, focused cat, room Stimulation/Comfort and
the gpak effect/malady providers arrive as plain callables, and
``on_select(db_key)`` tells the host which partner the user picked.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from mewgenics_overlay.core.recommend import recommend as recommend_best
from mewgenics_overlay.ui.partnertable import _night_chance
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt

_HIGH_RISK_PCT = 35.0        # banner appends a warning above this risk %
_LOW_NIGHT_CHANCE = 0.10     # banner appends "breeds rarely" below this
_DEFAULT_SPACING = 6         # fallback when the host passes no spacing
# Risk ceiling safe mode assumes when the host has not configured one. The
# palette falls back to this value, so it is the single source of the default.
SAFE_CAP_DEFAULT = 15.0


def _format_cap(cap: float) -> str:
    """Shown form of the safe-risk cap: 15.0 -> '15', 12.5 -> '12.5'.

    ``:g`` drops the trailing ``.0`` the default cap carries, so the copy
    stays "🛡 Safe ≤ 15% risk" at the default while any other configured
    ``safe_risk_cap`` is quoted truthfully.
    """
    return f"{float(cap):g}"


def _safe_label(cap: float) -> str:
    """Checkbox caption for the live safe-risk cap."""
    return f"🛡 Safe ≤ {_format_cap(cap)}% risk"


def _safe_tooltip(cap: float) -> str:
    """Checkbox explanation, quoting the live safe-risk cap."""
    return _wt(
        "Limit the ⭐ Best match to partners that are low risk "
        f"({_format_cap(cap)}% or less), so you only breed pairs that are "
        "unlikely to produce a defective kitten.\n"
        "If no partner is that safe, the normal 7s-first pick is shown "
        "instead - clearly labelled."
    )


# The caption at the default cap, derived from the constant above so the copy
# cannot drift from the number it quotes.
_SAFE_LABEL = _safe_label(SAFE_CAP_DEFAULT)


class BestMatchBar(QWidget):
    """The ⭐ best-match row: pick button plus the safe-mode checkbox.

    ``on_select`` receives the chosen partner's ``db_key``; the other
    callables return the live values (``rows_getter`` yields the rendered
    ``(row, kids)`` pairs, ``focus_getter`` the focused cat or ``None``).
    ``malady_lines`` and ``effect_of`` are the gpak-backed providers used for
    the "why this pick" tooltip.
    """

    def __init__(
        self,
        on_select: Callable[[int], None],
        rows_getter: Callable[[], list],
        focus_getter: Callable[[], object],
        stim_getter: Callable[[], float],
        comfort_getter: Callable[[], float],
        malady_lines: Callable[[object, float, object], list],
        effect_of: Callable[[object, object, str], str],
        safe_risk_cap: Callable[[], float],
        parent: Optional[QWidget] = None,
        spacing: int = _DEFAULT_SPACING,
    ) -> None:
        super().__init__(parent)
        self._on_select = on_select
        self._rows_getter = rows_getter
        self._focus_getter = focus_getter
        self._stim_getter = stim_getter
        self._comfort_getter = comfort_getter
        self._malady_lines = malady_lines
        self._effect_of = effect_of
        self._safe_risk_cap = safe_risk_cap
        self._best_row = None
        self._safe_mode = False
        initial_cap = self._safe_risk_cap()

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(spacing)

        self._btn_best = QPushButton("⭐ Best match")
        self._btn_best.setObjectName("best")
        self._btn_best.setToolTip("")
        self._btn_best.setVisible(False)
        self._btn_best.clicked.connect(self._on_clicked)

        self._btn_safe = QPushButton(_safe_label(initial_cap))
        self._btn_safe.setCheckable(True)
        self._btn_safe.setToolTip(_safe_tooltip(initial_cap))
        self._btn_safe.setVisible(False)
        self._btn_safe.toggled.connect(self._on_safe_toggled)

        row.addWidget(self._btn_best, 1)
        row.addWidget(self._btn_safe)

    # ── safe-mode state ────────────────────────────────────────────────────
    def is_safe_mode(self) -> bool:
        return self._safe_mode

    def set_safe_mode(self, on: bool) -> None:
        """Mirror the safe-mode state without re-firing the toggle handler."""
        self._safe_mode = bool(on)
        self._btn_safe.blockSignals(True)
        self._btn_safe.setChecked(self._safe_mode)
        self._btn_safe.blockSignals(False)

    def clear(self) -> None:
        """Hide the banner (no focused cat / no rows)."""
        self._best_row = None
        self._btn_best.setVisible(False)
        self._btn_safe.setVisible(False)

    def _refresh_safe_cap(self, cap: float) -> None:
        """Keep the checkbox copy in step with the live safe-risk cap."""
        label = _safe_label(cap)
        if self._btn_safe.text() != label:
            self._btn_safe.setText(label)
        tip = _safe_tooltip(cap)
        if self._btn_safe.toolTip() != tip:
            self._btn_safe.setToolTip(tip)

    # ── recommendation banner ──────────────────────────────────────────────
    def update_best(self) -> None:
        """Recompute and show the ⭐ best-match banner for the focused cat.

        Named ``update_best`` (not ``update``) so it never shadows
        ``QWidget.update()``.
        """
        rows = self._rows_getter() or []
        focus = self._focus_getter()
        # The safe cap is user-configurable, so the checkbox copy and the
        # fallback wording are derived from it instead of hardcoded.
        cap = self._safe_risk_cap()
        self._refresh_safe_cap(cap)
        if not rows or focus is None:
            self.clear()
            return
        compat = [r for r, _ in rows if r.compatible]
        if not compat:
            self.clear()
            return
        effect = self._effect_of
        stimulation = self._stim_getter()
        comfort = self._comfort_getter()
        overall = recommend_best(compat, focus, effect_of=effect,
                                 stimulation=stimulation, comfort=comfort)
        chosen = overall
        fallback = False
        if self._safe_mode:
            safe_rows = [r for r in compat if r.risk_pct <= cap]
            safe_rec = (recommend_best(safe_rows, focus, effect_of=effect,
                                       stimulation=stimulation, comfort=comfort)
                        if safe_rows else recommend_best([], focus))
            if safe_rec.row is not None:
                chosen = safe_rec
            elif overall.row is not None:
                chosen = overall            # fall back to the 7s-first pick
                fallback = True
        self._best_row = chosen.row
        self._btn_safe.setVisible(True)
        if chosen.row is None:
            self._btn_best.setVisible(False)
            return
        partner = chosen.row.partner
        prefix = "⭐ Best match"
        if self._safe_mode and not fallback:
            prefix = "🛡 Safe best"
        elif fallback:
            prefix = f"⭐ Best (no ≤{_format_cap(cap)}% risk partner)"
        text = (
            f"{prefix}: {partner.name} - Risk {chosen.row.risk_pct:.1f}% · "
            f"≥7 ≈{chosen.row.seven_plus_total:.1f} · "
            f"COI {chosen.row.coi * 100:.1f}%"
        )
        if chosen.row.risk_pct > _HIGH_RISK_PCT:
            text += "   ⚠ high risk"
        if _night_chance(chosen.row.game_compat, comfort) < _LOW_NIGHT_CHANCE:
            text += "   ⚠ breeds rarely"
        self._btn_best.setText(text)
        tool = "Why this pick:\n" + "\n".join(chosen.breakdown)
        malady = self._malady_lines(chosen.row, stimulation, effect)
        if malady:
            tool += "\n\n" + "\n".join(malady)
        tool += "\n\nClick to select this partner."
        self._btn_best.setToolTip(_wt(tool))
        self._btn_best.setVisible(True)

    # ── user input ─────────────────────────────────────────────────────────
    def _on_safe_toggled(self, checked: bool) -> None:
        self._safe_mode = bool(checked)
        self.update_best()

    def _on_clicked(self) -> None:
        if self._best_row is None:
            return
        self._on_select(self._best_row.partner.db_key)
