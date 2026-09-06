"""Shared colours, fonts and styling helpers for the overlay UI.

Mewgenics notebook look: warm ruled-paper cream, ink-brown borders,
highlighter-yellow selections, marker-red accents, and a bundled
handwritten font (Patrick Hand, OFL) so it looks hand-drawn everywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Shared UI colours (single source of truth for widgets)
C_TEXT = "#33281f"        # main ink text
C_MUTED = "#8a7354"       # blocked rows / low emphasis
C_FAMILY = "#7d5ba6"      # related but breedable
C_GOOD = "#2e7d43"        # pass / high stat
C_WARN = "#b9730f"        # caution / inherited defect
C_STAT_LOW = "#9c8a68"    # low-ish stat chip
C_GRIP = "#8a6f49"        # drag grip
C_STATUS = "#8f7a55"      # header status text

# Handwriting font bundled with the app (Patrick Hand, OFL).
_BUNDLED_FONT = Path(__file__).resolve().parent \
    / "assets" / "fonts" / "PatrickHand-Regular.ttf"
_FONT_ORDER = [
    "Patrick Hand",
    "Comic Neue",
    "Comic Sans MS",
    "Segoe Print",
    "Chalkboard SE",
    "DejaVu Sans",
]


def wrap_tooltip(text: str, width: int = 84) -> str:
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
    """Semantic colour for a pair's combined birth-defect risk %."""
    if risk_pct <= 5.0:
        return "#2e7d43"      # safe
    if risk_pct <= 12.0:
        return "#b9730f"      # caution
    return "#b5372a"          # dangerous


def gender_badge(gender: str) -> str:
    g = (gender or "?").strip().lower()
    return {"male": "♂", "female": "♀", "?": "?"}.get(g, "?")


def _font_paths():
    paths = [_BUNDLED_FONT]
    meipass = getattr(sys, "_MEIPASS", None)   # PyInstaller onefile bundle
    if meipass:
        paths.append(Path(meipass)
                     / "mewgenics_overlay/ui/assets/fonts/PatrickHand-Regular.ttf")
    return [p for p in paths if p.is_file()]


def apply_casual_font(app) -> None:
    """Load the bundled handwritten font and apply it app-wide."""
    try:
        from PySide6.QtGui import QFont, QFontDatabase
        for path in _font_paths():
            QFontDatabase.addApplicationFont(str(path))
        available = set(QFontDatabase.families())
        chosen = next((f for f in _FONT_ORDER if f in available), None)
        if chosen:
            font = QFont(chosen)
            size = font.pointSize()
            font.setPointSize(size + 1 if size > 0 else 12)
            app.setFont(font)
    except Exception:
        pass


# Hand-drawn notebook styling: paper gradients, highlighter selections,
# ink-brown doodle borders, marker-red accents.
STYLESHEET = """
* { font-family: 'Patrick Hand','Comic Neue','DejaVu Sans',sans-serif; }
QWidget {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f8eecf, stop:1 #efd9ae);
    color: #33281f;
}
QDialog { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f8eecf, stop:1 #efd9ae); }
QLabel#muted { color: #8f7a55; }
QLabel#headerName {
    font-size: 18px; font-weight: 700; color: #4a3520;
    background-color: #f7d878; padding: 1px 8px; border-radius: 4px;
}

QLineEdit, QComboBox {
    background: #fdf6e3;
    border: 2px solid #8a6f49;
    border-radius: 9px;
    padding: 4px 9px;
    selection-background-color: #f6d97e;
    selection-color: #33281f;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #fdf6e3; color: #33281f;
    selection-background-color: #f6d97e;
}

QListWidget, QTableWidget {
    background: #fbf2d8;
    alternate-background-color: #efe0bd;
    border: 2px solid #9c7c4d;
    border-radius: 12px;
    outline: none;
    gridline-color: #ddcaa2;
}
QListWidget::item { padding: 4px 7px; border-radius: 4px; }
QListWidget::item:selected, QTableWidget::item:selected {
    background: #f6d97e; color: #33281f;      /* highlighter swipe */
}
QHeaderView::section {
    background: #e8d4a2;
    color: #5a442c;
    border: none;
    border-right: 1px solid #d4bd87;
    border-bottom: 3px solid #9c7c4d;
    padding: 6px 8px;
    font-weight: 700;
    font-size: 14px;
}

QPushButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #f0ddb0, stop:1 #e0c48c);
    border: 2px solid #7d5f3b;
    border-radius: 11px;
    padding: 5px 13px;
    color: #3a2c1e;
}
QPushButton:hover { background: #f6e6bd; }
QPushButton:pressed { background: #d8b97f; }
QPushButton:checked {
    background: #bf5a3a; color: #fff3e0; border-color: #8f3f26;
}
QPushButton#best {
    text-align: left; font-weight: 700; padding: 7px 11px;
    background: #fbf0cd;
    border: 2px dashed #a8763e;
    border-bottom: 4px solid #c2572f;
    border-radius: 12px;
    color: #7c3f22;
}
QPushButton#best:hover { background: #fff6dc; }

QToolTip {
    background: #fdf3d3; color: #33281f;
    border: 2px solid #8a6f49; border-radius: 9px; padding: 6px;
}

QScrollBar:vertical { background: transparent; width: 12px; }
QScrollBar::handle:vertical {
    background: #b99a63; border-radius: 6px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #a98a53; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 2px solid #9c7c4d; border-radius: 12px; }
QTabBar::tab {
    background: #e6cf9f; color: #5a442c;
    border: 2px solid #9c7c4d; border-bottom: none;
    border-top-left-radius: 10px; border-top-right-radius: 10px;
    padding: 6px 18px; margin-right: 4px; font-size: 15px;
}
QTabBar::tab:selected { background: #f6d97e; font-weight: 700; }
"""
