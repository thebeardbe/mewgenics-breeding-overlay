"""Theme registry for the overlay.

Two film-noir looks — high-key ("bright") and low-key ("dark") monochrome:

  * ``film``  — Noir · Bright: silver/ivory stock, ink-black lines.
  * ``noir``  — Noir · Dark: deep charcoal stock, pale silver lines.

The only chroma allowed is a restrained noir-red (danger, selected). Risk is
otherwise graded by ink weight: dark = low, mid-grey = medium, red = high.

Module globals (``C_TEXT`` etc., ``STYLESHEET``, ``risk_color``) track the
active theme, so render-time code follows live switches.
"""

from __future__ import annotations

# Tray/logo brand colours (theme-independent)
C_TRAY_BASE = "#453a7a"
C_TRAY_EAR = "#8a7bf0"

_BRIGHT = {
    "name": "Noir · Bright",
    "C_TEXT": "#201f1c",
    "C_MUTED": "#6f6d68",
    "C_FAMILY": "#6a4fa3",
    "C_GOOD": "#2e7d32",     # strong ink (pass / 7s)
    "C_WARN": "#b26b0a",     # mid grey (caution)
    "C_STAT_LOW": "#8f8675",
    "C_GRIP": "#6a6761",
    "C_STATUS": "#6f6c65",
    "RISK_SAFE": "#2e7d32",
    "RISK_MID": "#b26b0a",
    "RISK_HIGH": "#c62828",
}

_DARK = {
    "name": "Noir · Dark",
    "C_TEXT": "#e6e3da",
    "C_MUTED": "#9d998e",
    "C_FAMILY": "#b39ddb",
    "C_GOOD": "#81c784",     # pale silver (pass / 7s)
    "C_WARN": "#e0a458",     # grey (caution)
    "C_STAT_LOW": "#9f9a8e",
    "C_GRIP": "#8d887b",
    "C_STATUS": "#a49f92",
    "RISK_SAFE": "#81c784",
    "RISK_MID": "#e0a458",
    "RISK_HIGH": "#ff5f52",
}

_STYLES = {}

_STYLES["film"] = """
* { font-family: 'Arial','Helvetica','DejaVu Sans','Segoe UI',sans-serif; }
QWidget { background: #d8d3c7; color: #201f1c; }
QDialog { background: #d8d3c7; }
QLabel#muted { color: #6f6c65; }
QLabel#headerName {
    font-size: 17px; font-weight: 800; color: #111;
    border-bottom: 3px solid #1a1a1a; padding-bottom: 2px;
}
QLineEdit, QComboBox {
    background: #efece2;
    border: 2px solid #262626; border-radius: 5px; padding: 4px 9px;
    selection-background-color: #1a1a1a; selection-color: #f4f2ed;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #efece2; color: #201f1c;
    selection-background-color: #1a1a1a; selection-color: #f4f2ed;
}
QListWidget, QTableWidget {
    background: #e6e2d6; alternate-background-color: #d6d2c5;
    border: 2px solid #262626; border-radius: 5px; outline: none;
    gridline-color: #c6c2b8;
}
QListWidget::item { padding: 4px 7px; }
QListWidget::item:selected, QTableWidget::item:selected {
    background: #1a1a1a; color: #f4f2ed;
}
QHeaderView::section {
    background: #c2beb0; color: #141414;
    border: none; border-right: 1px solid #b5b1a6;
    border-bottom: 3px solid #262626;
    padding: 6px 8px; font-weight: 800; font-size: 13px;
}
QPushButton {
    background: #cbc7b9; border: 2px solid #262626; border-radius: 6px;
    padding: 5px 13px; color: #201f1c;
}
QPushButton:hover { background: #d9d5c8; }
QPushButton:pressed { background: #b8b3a5; }
QPushButton:checked { background: #8f2b22; color: #f6f2ea; border-color: #3c0e0a; }
QPushButton#best {
    text-align: left; font-weight: 800; padding: 7px 11px;
    background: #c7c3b5; border: 3px solid #262626; border-radius: 7px;
    color: #6b241d;
}
QPushButton#best:hover { background: #d3cfc1; }
QToolTip {
    background: #e6e2d6; color: #201f1c;
    border: 2px solid #262626; border-radius: 5px; padding: 6px;
}
QScrollBar:vertical { background: transparent; width: 12px; }
QScrollBar::handle:vertical { background: #a3a096; border-radius: 6px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #8b877d; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 2px solid #262626; border-radius: 5px; }
QTabBar::tab {
    background: #bbb7aa; color: #201f1c;
    border: 2px solid #262626; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
    padding: 6px 18px; margin-right: 3px; font-weight: 700;
}
QTabBar::tab:selected { background: #e6e2d6; }
QPushButton#iconbtn { font-size: 20px; padding: 2px 0; }
"""

_STYLES["noir"] = """
* { font-family: 'Arial','Helvetica','DejaVu Sans','Segoe UI',sans-serif; }
QWidget { background: #141311; color: #e6e3da; }
QDialog { background: #141311; }
QLabel#muted { color: #a49f92; }
QLabel#headerName {
    font-size: 17px; font-weight: 800; color: #f0ede4;
    border-bottom: 3px solid #8a857a; padding-bottom: 2px;
}
QLineEdit, QComboBox {
    background: #211f1b; color: #e6e3da;
    border: 2px solid #6a665e; border-radius: 5px; padding: 4px 9px;
    selection-background-color: #e5483c; selection-color: #17130f;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #211f1b; color: #e6e3da;
    selection-background-color: #e5483c; selection-color: #17130f;
}
QListWidget, QTableWidget {
    background: #1a1814; alternate-background-color: #26231d;
    border: 2px solid #6a665e; border-radius: 5px; outline: none;
    gridline-color: #4a473f;
}
QListWidget::item { padding: 4px 7px; }
QListWidget::item:selected, QTableWidget::item:selected {
    background: #e5483c; color: #17130f;
}
QHeaderView::section {
    background: #26231d; color: #d9d5cb;
    border: none; border-right: 1px solid #403c34;
    border-bottom: 3px solid #8a857a;
    padding: 6px 8px; font-weight: 800; font-size: 13px;
}
QPushButton {
    background: #2c2923; color: #e6e3da;
    border: 2px solid #6a665e; border-radius: 6px; padding: 5px 13px;
}
QPushButton:hover { background: #37332c; }
QPushButton:pressed { background: #221f19; }
QPushButton:checked { background: #8f2b22; color: #f6f2ea; border-color: #d33; }
QPushButton#best {
    text-align: left; font-weight: 800; padding: 7px 11px;
    background: #26231d; border: 3px solid #8a857a; border-radius: 7px;
    color: #e8a9a1;
}
QPushButton#best:hover { background: #312d26; }
QToolTip {
    background: #2b2822; color: #e6e3da;
    border: 2px solid #6a665e; border-radius: 5px; padding: 6px;
}
QScrollBar:vertical { background: transparent; width: 12px; }
QScrollBar::handle:vertical { background: #6a665e; border-radius: 6px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #7d786e; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 2px solid #6a665e; border-radius: 5px; }
QTabBar::tab {
    background: #2c2923; color: #cfccc3;
    border: 2px solid #6a665e; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
    padding: 6px 18px; margin-right: 3px; font-weight: 700;
}
QTabBar::tab:selected { background: #37332c; color: #f0ede4; }
QPushButton#iconbtn { font-size: 20px; padding: 2px 0; }
"""

THEMES = {
    "film": {"title": "Noir · Bright", "colors": _BRIGHT, "css": _STYLES["film"]},
    "noir": {"title": "Noir · Dark", "colors": _DARK, "css": _STYLES["noir"]},
}
DEFAULT_THEME = "noir"
_ACTIVE = "film"


def active_theme() -> str:
    return _ACTIVE


def theme_titles() -> list[tuple[str, str]]:
    return [(key, info["title"]) for key, info in THEMES.items()]


def set_theme(key: str) -> None:
    global _ACTIVE
    info = THEMES[key]
    _ACTIVE = key
    _globals = globals()
    for name, value in info["colors"].items():
        _globals[name] = value
    _globals["STYLESHEET"] = info["css"]


def stylesheet() -> str:
    return THEMES[_ACTIVE]["css"]


def risk_color(risk_pct: float) -> str:
    """Risk grading in film-noir ink weight: dark = low, grey = medium,
    red = high."""
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


set_theme(DEFAULT_THEME)
