"""Shared colours, fonts and styling helpers for the overlay UI.

Palette follows Mewgenics' paper-craft notebook look: warm cream paper,
dark ink outlines, marker-red accents, muted kraft tones.
"""

from __future__ import annotations

# Shared UI colours (single source of truth for widgets)
C_TEXT = "#33281f"        # main ink text
C_MUTED = "#8a7354"       # blocked rows / low emphasis
C_FAMILY = "#7d5ba6"      # related but breedable
C_GOOD = "#2e7d43"        # pass / high stat
C_WARN = "#b9730f"        # caution / inherited defect
C_STAT_LOW = "#9c8a68"    # low-ish stat chip
C_GRIP = "#8a6f49"        # drag grip
C_STATUS = "#8f7a55"      # header status text

# Casual, sketchy-looking fonts, tried in order (first installed wins).
PREFERRED_FONTS = [
    "Comic Sans MS",
    "Comic Neue",
    "Chalkboard SE",
    "Segoe Print",
    "Marker Felt",
    "Patrick Hand",
    "Comic Relief",
    "DejaVu Sans",
]


def wrap_tooltip(text: str, width: int = 84) -> str:
    """Hard-wrap every line at *width* characters so tooltips stay compact
    instead of stretching across the screen on one line.

    Preserves blank lines and leading indentation; long single words are
    left unbroken rather than mangled.
    """
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
    """Semantic colour for a pair's combined birth-defect risk % (ink-safe)."""
    if risk_pct <= 5.0:
        return "#2e7d43"      # safe
    if risk_pct <= 12.0:
        return "#b9730f"      # caution
    return "#b5372a"          # dangerous


def gender_badge(gender: str) -> str:
    g = (gender or "?").strip().lower()
    return {"male": "♂", "female": "♀", "?": "?"}.get(g, "?")


def apply_casual_font(app) -> None:
    """Pick the first installed casual font from PREFERRED_FONTS and apply it
    app-wide (falls back to whatever Qt resolves, usually DejaVu)."""
    try:
        from PySide6.QtGui import QFont, QFontDatabase
        available = set(QFontDatabase.families())
        chosen = next((f for f in PREFERRED_FONTS if f in available), None)
        if chosen:
            font = QFont(chosen)
            font.setPointSize(font.pointSize() + 1 if font.pointSize() > 0 else 11)
            app.setFont(font)
    except Exception:
        pass


# Warm paper stylesheet. The emoji buttons are fine; everything else leans on
# kraft paper tones with marker-ish borders and soft rounded corners.
STYLESHEET = """
* { font-family: 'Comic Sans MS','Comic Neue','Segoe Print','DejaVu Sans',sans-serif; }
QWidget {
    background: #f2e4c8;
    color: #33281f;
}
QMainWindow, QDialog { background: #f2e4c8; }
QLabel#muted { color: #8f7a55; }
QLabel#headerName { font-size: 16px; font-weight: 700; color: #4a3520; }

QLineEdit, QComboBox {
    background: #fdf6e3;
    border: 2px solid #8a6f49;
    border-radius: 8px;
    padding: 4px 8px;
    selection-background-color: #d9b97c;
    selection-color: #33281f;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #fdf6e3;
    color: #33281f;
    selection-background-color: #e3c78f;
}

QListWidget, QTableWidget {
    background: #f9efd9;
    alternate-background-color: #efdfc0;
    border: 2px solid #9c7c4d;
    border-radius: 10px;
    outline: none;
    gridline-color: #d8c49a;
}
QListWidget::item, QTableWidget::item { padding: 3px 6px; }
QListWidget::item:selected, QTableWidget::item:selected {
    background: #e3c78f; color: #33281f;
}
QHeaderView::section {
    background: #e3cf9f;
    color: #5a442c;
    border: none;
    border-right: 1px solid #d0b581;
    border-bottom: 2px solid #9c7c4d;
    padding: 5px 7px;
    font-weight: 700;
}

QPushButton {
    background: #e6cf9f;
    border: 2px solid #8a6f49;
    border-radius: 10px;
    padding: 5px 12px;
    color: #3a2c1e;
}
QPushButton:hover { background: #efdcb2; }
QPushButton:pressed { background: #d8b97f; }
QPushButton:checked { background: #bf5a3a; color: #fff3e0; border-color: #8f3f26; }
QPushButton#best {
    text-align: left; font-weight: 700; padding: 6px 10px;
    background: #f3dcae; border: 2px dashed #a8763e; border-radius: 10px;
    color: #7c3f22;
}
QPushButton#best:hover { background: #f6e3bf; }

QToolTip {
    background: #fdf6e3;
    color: #33281f;
    border: 1px solid #8a6f49;
    border-radius: 8px;
    padding: 6px;
}

QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical {
    background: #b99a63; border-radius: 5px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #a98a53; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 2px solid #9c7c4d; border-radius: 10px; }
QTabBar::tab {
    background: #e6cf9f; color: #5a442c;
    border: 2px solid #9c7c4d; border-bottom: none;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
    padding: 5px 16px; margin-right: 3px;
}
QTabBar::tab:selected { background: #f9efd9; font-weight: 700; }
"""
