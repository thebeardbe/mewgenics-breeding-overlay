"""Theme registry for the overlay.

Two looks, same flat flash-cartoon logic:

  * ``film``  — "Bleached Film": cool bone/sepia stock, thick ink outlines.
  * ``noir``  — "Noir Ink": deep charcoal film stock, lighter vector edges.

Module globals (``C_TEXT`` etc., ``STYLESHEET``, ``risk_color``) track the
active theme, so widgets that read them at render time switch live.
"""

from __future__ import annotations

_LIGHT = {
    "name": "Bleached Film",
    "C_TEXT": "#221d18",
    "C_MUTED": "#716c5d",
    "C_FAMILY": "#6d5f8d",
    "C_GOOD": "#4d7247",
    "C_WARN": "#a06f2f",
    "C_STAT_LOW": "#8f866f",
    "C_GRIP": "#5a5244",
    "C_STATUS": "#6d6752",
    "RISK_SAFE": "#4d7247",
    "RISK_MID": "#a06f2f",
    "RISK_HIGH": "#9e3b2b",
}

_DARK = {
    "name": "Noir Ink",
    "C_TEXT": "#e6dfc8",
    "C_MUTED": "#9b917c",
    "C_FAMILY": "#9c82c6",
    "C_GOOD": "#7fb271",
    "C_WARN": "#cf9a4e",
    "C_STAT_LOW": "#a79c81",
    "C_GRIP": "#8a8166",
    "C_STATUS": "#a89d84",
    "RISK_SAFE": "#7fb271",
    "RISK_MID": "#cf9a4e",
    "RISK_HIGH": "#d45543",
}

# Pure-QSS stylesheet per theme. Kept flat + heavy-outline; only base tones,
# fills and outline colours change between the two stocks.
_STYLES = {}

_STYLES["film"] = """
* { font-family: 'Arial','Helvetica','DejaVu Sans','Segoe UI',sans-serif; }
QWidget { background: #d9d2c0; color: #221d18; }
QDialog { background: #d9d2c0; }
QLabel#muted { color: #716c5d; }
QLabel#headerName {
    font-size: 17px; font-weight: 800; color: #17130c;
    border-bottom: 3px solid #9e3b2b; padding-bottom: 2px;
}
QLineEdit, QComboBox {
    background: #f0e9d8;
    border: 2px solid #262016; border-radius: 6px; padding: 4px 9px;
    selection-background-color: #2b2721; selection-color: #efe9dc;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #f0e9d8; color: #221d18;
    selection-background-color: #2b2721; selection-color: #efe9dc;
}
QListWidget, QTableWidget {
    background: #eee7d4; alternate-background-color: #ded5bf;
    border: 2px solid #262016; border-radius: 6px; outline: none;
    gridline-color: #c7bd9f;
}
QListWidget::item { padding: 4px 7px; }
QListWidget::item:selected, QTableWidget::item:selected {
    background: #2b2721; color: #efe9dc;
}
QHeaderView::section {
    background: #c4b998; color: #17130c;
    border: none; border-right: 1px solid #aca078;
    border-bottom: 3px solid #262016;
    padding: 6px 8px; font-weight: 800; font-size: 13px;
}
QPushButton {
    background: #d3c8ad; border: 2px solid #262016; border-radius: 7px;
    padding: 5px 13px; color: #221d18;
}
QPushButton:hover { background: #ddd2b8; }
QPushButton:pressed { background: #bdb088; }
QPushButton:checked { background: #8e3426; color: #f2ecdc; border-color: #3c150d; }
QPushButton#best {
    text-align: left; font-weight: 800; padding: 7px 11px;
    background: #d2c49f; border: 3px solid #262016; border-radius: 8px;
    color: #6b2a1c;
}
QPushButton#best:hover { background: #ded0ab; }
QToolTip {
    background: #ece5d1; color: #221d18;
    border: 2px solid #262016; border-radius: 6px; padding: 6px;
}
QScrollBar:vertical { background: transparent; width: 12px; }
QScrollBar::handle:vertical { background: #948763; border-radius: 6px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #7f7453; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 2px solid #262016; border-radius: 6px; }
QTabBar::tab {
    background: #c7ba96; color: #221d18;
    border: 2px solid #262016; border-bottom: none;
    border-top-left-radius: 7px; border-top-right-radius: 7px;
    padding: 6px 18px; margin-right: 3px; font-weight: 700;
}
QTabBar::tab:selected { background: #efe7d3; }
"""

_STYLES["noir"] = """
* { font-family: 'Arial','Helvetica','DejaVu Sans','Segoe UI',sans-serif; }
QWidget { background: #16130e; color: #e6dfc8; }
QDialog { background: #16130e; }
QLabel#muted { color: #9b917c; }
QLabel#headerName {
    font-size: 17px; font-weight: 800; color: #efe8d2;
    border-bottom: 3px solid #d45543; padding-bottom: 2px;
}
QLineEdit, QComboBox {
    background: #211c14; color: #e6dfc8;
    border: 2px solid #6a5f45; border-radius: 6px; padding: 4px 9px;
    selection-background-color: #7a2f24; selection-color: #f6efda;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #211c14; color: #e6dfc8;
    selection-background-color: #7a2f24; selection-color: #f6efda;
}
QListWidget, QTableWidget {
    background: #1d1812; alternate-background-color: #282217;
    border: 2px solid #6a5f45; border-radius: 6px; outline: none;
    gridline-color: #4c4431;
}
QListWidget::item { padding: 4px 7px; }
QListWidget::item:selected, QTableWidget::item:selected {
    background: #7a2f24; color: #f6efda;
}
QHeaderView::section {
    background: #241f16; color: #cfc49f;
    border: none; border-right: 1px solid #453d2b;
    border-bottom: 3px solid #6a5f45;
    padding: 6px 8px; font-weight: 800; font-size: 13px;
}
QPushButton {
    background: #2c2619; color: #e6dfc8;
    border: 2px solid #6a5f45; border-radius: 7px; padding: 5px 13px;
}
QPushButton:hover { background: #3a3222; }
QPushButton:pressed { background: #221d12; }
QPushButton:checked { background: #7a2f24; color: #f6efda; border-color: #a03c2c; }
QPushButton#best {
    text-align: left; font-weight: 800; padding: 7px 11px;
    background: #2a241a; border: 3px solid #8a7a55; border-radius: 8px;
    color: #e8a79b;
}
QPushButton#best:hover { background: #38301f; }
QToolTip {
    background: #2a241a; color: #e6dfc8;
    border: 2px solid #6a5f45; border-radius: 6px; padding: 6px;
}
QScrollBar:vertical { background: transparent; width: 12px; }
QScrollBar::handle:vertical { background: #6a5f45; border-radius: 6px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #7c7053; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 2px solid #6a5f45; border-radius: 6px; }
QTabBar::tab {
    background: #2c2619; color: #cfc49f;
    border: 2px solid #6a5f45; border-bottom: none;
    border-top-left-radius: 7px; border-top-right-radius: 7px;
    padding: 6px 18px; margin-right: 3px; font-weight: 700;
}
QTabBar::tab:selected { background: #3a3222; color: #efe8d2; }
"""

THEMES = {
    "film": {"title": "Bleached Film", "colors": _LIGHT, "css": _STYLES["film"]},
    "noir": {"title": "Noir Ink", "colors": _DARK, "css": _STYLES["noir"]},
}
DEFAULT_THEME = "film"
_ACTIVE = "film"


def active_theme() -> str:
    return _ACTIVE


def theme_titles() -> list[tuple[str, str]]:
    return [(key, info["title"]) for key, info in THEMES.items()]


def set_theme(key: str) -> None:
    """Make *key* the active theme and refresh module-level colour constants
    and the stylesheet so existing render code follows automatically."""
    global _ACTIVE
    info = THEMES[key]
    _ACTIVE = key
    colours = info["colors"]
    _globals = globals()
    for name, value in colours.items():
        _globals[name] = value
    _globals["STYLESHEET"] = info["css"]


def stylesheet() -> str:
    return THEMES[_ACTIVE]["css"]


def risk_color(risk_pct: float) -> str:
    """Semantic colour for a pair's combined birth-defect risk %."""
    c = THEMES[_ACTIVE]["colors"]
    if risk_pct <= 5.0:
        return c["RISK_SAFE"]
    if risk_pct <= 12.0:
        return c["RISK_MID"]
    return c["RISK_HIGH"]


def gender_badge(gender: str) -> str:
    g = (gender or "?").strip().lower()
    return {"male": "♂", "female": "♀", "?": "?"}.get(g, "?")


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


def apply_casual_font(app) -> None:
    """No custom font — the bold vector look comes from the stylesheet."""


# Apply the default theme at import time so bare imports behave as before.
set_theme(DEFAULT_THEME)
