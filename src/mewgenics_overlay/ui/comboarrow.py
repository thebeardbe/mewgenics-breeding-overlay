"""Visible down-arrow for QComboBoxes.

The app stylesheet styles QComboBox::drop-down but provides no arrow image,
so Qt draws no arrow at all. We render a small arrow PNG per theme colour
into the user config dir and attach it via a widget-local stylesheet.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from mewgenics_overlay.ui.config import config_dir


def arrow_file(color: str) -> Path:
    """Return a cached arrow PNG path for a #rrggbb colour."""
    safe = hashlib.sha1(color.encode()).hexdigest()[:10]
    path = config_dir() / f"combo-arrow-{safe}.png"
    if not path.exists():
        _render_arrow(path, color)
    return path


def qss_url(path: Path) -> str:
    """A Qt stylesheet ``url(...)`` that survives a Windows path.

    Native Windows paths use backslashes and often contain spaces (the user
    profile directory), neither of which the QSS parser can express in the
    bare ``url(C:\\Users\\a b\\x.png)`` form, so the rule parses and the combo
    draws no arrow. POSIX separators plus a quoted value give a form Qt
    accepts everywhere, with the same image as before on Linux.
    """
    return 'url("{}")'.format(path.as_posix())


def _render_arrow(path: Path, color: str) -> None:
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

    pm = QPixmap(14, 14)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 2)
    p.setPen(pen)
    p.drawLine(2, 4, 7, 10)
    p.drawLine(7, 10, 12, 4)
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    pm.save(str(path), "PNG")


def style_combo(combo, color: str) -> None:
    """Attach a visible arrow to *combo* using the given #rrggbb colour."""
    file = arrow_file(color)
    combo.setStyleSheet(
        f"QComboBox::down-arrow {{ image: {qss_url(file)}; width: 14px; "
        "height: 14px; }\n"
        "QComboBox::drop-down { border: none; width: 22px; }"
    )
