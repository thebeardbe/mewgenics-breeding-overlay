"""Shared colours and styling helpers for the overlay UI.

Look: macabre flash-cartoon on bleached Technicolor film. Thick black
vector-style outlines, flat fills, washed-out sepia/tan tones, faded
mid-century palette — no saturated primaries.
"""

from __future__ import annotations

# Shared UI colours (single source of truth for widgets)
C_TEXT = "#221d16"        # ink text
C_MUTED = "#7d7460"       # blocked rows / low emphasis
C_FAMILY = "#6f5f8f"      # related but breedable
C_GOOD = "#57753f"        # pass / high stat (desaturated green)
C_WARN = "#a8702c"        # caution / inherited defect (faded ochre)
C_STAT_LOW = "#8f846a"    # low-ish stat chip
C_GRIP = "#4a4236"        # drag grip
C_STATUS = "#6f6750"      # header status text


def wrap_tooltip(text: str, width: int = 88) -> str:
    """Hard-wrap every line at *width* characters so tooltips stay compact
    instead of stretching across the screen on one line."""
    out: list[str] = []
    for para in (text or "").split("\n"):
        if not para.strip():
            out.append("")
            continue
        indent = para[: len(para) - len(para.lstrip())]
        words = para.split()
        line = indent
        for word in words:
            candidate = word if not line else f"{line} {word}"
            if len(candidate) > width and len(line) > len(indent):
                out.append(line)
                line = indent + word
            else:
                line = candidate
        out.append(line)
    return "\n".join(out)


def risk_color(risk_pct: float) -> str:
    """Risk colour key — washed out but distinguishable on film stock."""
    if risk_pct <= 5.0:
        return "#57753f"      # safe
    if risk_pct <= 12.0:
        return "#a8702c"      # caution
    return "#9c3a2a"          # dangerous


def gender_badge(gender: str) -> str:
    g = (gender or "?").strip().lower()
    return {"male": "♂", "female": "♀", "?": "?"}.get(g, "?")


def apply_casual_font(app) -> None:
    """No custom font: let the system font carry the bold flash-cartoon look
    (weight handled in the stylesheet)."""


# Macabre flash-cartoon styling: flat fills, thick black vector outlines,
# bleached bone/sepia palette, faded accents.
STYLESHEET = """
* { font-family: 'Arial','Helvetica','DejaVu Sans','Segoe UI',sans-serif; }
QWidget { background: #e8dfc6; color: #221d16; }
QDialog { background: #e8dfc6; }
QLabel#muted { color: #6f6750; }
QLabel#headerName {
    font-size: 17px; font-weight: 800; color: #17130c;
    border-bottom: 3px solid #9c3a2a; padding-bottom: 2px;
}

QLineEdit, QComboBox {
    background: #f3ebd3;
    border: 2px solid #221c12;
    border-radius: 6px;
    padding: 4px 9px;
    selection-background-color: #2b251c;
    selection-color: #efe6d0;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #f3ebd3; color: #221d16;
    selection-background-color: #2b251c; selection-color: #efe6d0;
}

QListWidget, QTableWidget {
    background: #f0e7d0;
    alternate-background-color: #e2d6b8;
    border: 2px solid #221c12;
    border-radius: 6px;
    outline: none;
    gridline-color: #cfc09a;
}
QListWidget::item { padding: 4px 7px; }
QListWidget::item:selected, QTableWidget::item:selected {
    background: #2b251c; color: #efe6d0;
}
QHeaderView::section {
    background: #cbb98f;
    color: #17130c;
    border: none;
    border-right: 1px solid #b7a273;
    border-bottom: 3px solid #221c12;
    padding: 6px 8px;
    font-weight: 800;
    font-size: 13px;
}

QPushButton {
    background: #ddd0ae;
    border: 2px solid #221c12;
    border-radius: 7px;
    padding: 5px 13px;
    color: #221d16;
}
QPushButton:hover { background: #e9dcba; }
QPushButton:pressed { background: #c9b78c; }
QPushButton:checked {
    background: #9c3a2a; color: #f4ecd8; border-color: #3c150d;
}
QPushButton#best {
    text-align: left; font-weight: 800; padding: 7px 11px;
    background: #d7c69e;
    border: 3px solid #221c12;
    border-radius: 8px;
    color: #6b2a1c;
}
QPushButton#best:hover { background: #e2d3ad; }

QToolTip {
    background: #efe6d0; color: #221d16;
    border: 2px solid #221c12; border-radius: 6px; padding: 6px;
}

QScrollBar:vertical { background: transparent; width: 12px; }
QScrollBar::handle:vertical {
    background: #9a8a5f; border-radius: 6px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #86774e; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 2px solid #221c12; border-radius: 6px; }
QTabBar::tab {
    background: #d4c49d; color: #221d16;
    border: 2px solid #221c12; border-bottom: none;
    border-top-left-radius: 7px; border-top-right-radius: 7px;
    padding: 6px 18px; margin-right: 3px; font-weight: 700;
}
QTabBar::tab:selected { background: #efe6d0; }
"""
