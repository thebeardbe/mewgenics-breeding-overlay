"""Shared colors and styling helpers for the overlay UI."""

from __future__ import annotations


def risk_color(risk_pct: float) -> str:
    """Semantic color for a pair's combined birth-defect risk %."""
    if risk_pct <= 5.0:
        return "#3dbf6f"      # safe
    if risk_pct <= 12.0:
        return "#e0a63a"      # caution
    return "#e2574c"          # dangerous


def gender_badge(gender: str) -> str:
    g = (gender or "?").strip().lower()
    return {"male": "♂", "female": "♀", "?": "?"}.get(g, "?")


STYLESHEET = """
* { font-family: 'DejaVu Sans', 'Segoe UI', sans-serif; }
QWidget { background: rgba(24, 22, 30, 0.94); color: #e8e6ee; }
QLineEdit {
    background: #17141d; border: 1px solid #3a3450; border-radius: 6px;
    padding: 4px 8px; selection-background-color: #6d5bd0;
}
QLineEdit:focus { border-color: #8a7bf0; }
QListWidget, QTableWidget {
    background: #17141d; alternate-background-color: #1e1a29;
    border: 1px solid #2c2740; border-radius: 6px;
    outline: none;
}
QListWidget::item { padding: 4px 8px; border-radius: 4px; }
QListWidget::item:selected { background: #453a7a; }
QHeaderView::section {
    background: #221d30; color: #b9b2d4; border: none;
    padding: 4px 6px; font-weight: 600;
}
QPushButton {
    background: #332c4d; border: 1px solid #4a4072; border-radius: 6px;
    padding: 4px 10px;
}
QPushButton:hover { background: #413865; }
QPushButton:pressed { background: #2a2440; }
QPushButton:disabled { color: #6d6890; }
QLabel#headerName { font-size: 15px; font-weight: 700; }
QLabel#muted { color: #9a94b8; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #4a4270; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
