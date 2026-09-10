"""SavePanel - the campaign save-slot buttons and the "Open save" picker.

Extracted from ``PaletteWindow`` (god-file split): the three in-game campaign
slots (``steamcampaign01..03``, discovered on disk) and the "📁 Open another
save file…" picker used to be built inside ``SettingsTab`` and driven by the
window. They now live here as one cohesive unit.

Window-agnostic: the live settings dict and an ``on_open(path)`` callable
arrive from the host, and the panel never references ``PaletteWindow``, the
session or the watcher. Writes to the settings file stay with the host, which
reaches them through ``open_save``. The host's ``open_save(path)`` stays the
single entry point that parses and switches a save.

Slot labels, slot order (1..3), tooltips and wording are unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.ui import theme as _theme
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt

log = logging.getLogger("mewgenics_overlay.ui")

SLOT_COUNT = 3             # steamcampaign01..03: the in-game campaign slots
_SLOT_MIN_HEIGHT = 58      # slot button height (label line + save name)
_OPEN_BTN_MAX_W = 300      # "Open another save file…" fits one short row
_SAVE_FILTER = "Mewgenics saves (*.sav)"
_CURRENT_MARK = "  ●"      # marks the slot the overlay has loaded


def _file_exists(path: str) -> bool:
    return bool(path) and os.path.exists(path)


def base_name(path) -> str:
    """File name of *path* on either separator (Windows saves arrive with ``\\``).

    Single source of truth for the save-slot label and the window title, so
    they always agree on the shown file name.
    """
    return str(path).split("/")[-1].split("\\")[-1]


# Backwards-compatible alias (existing importers use the private name).
_base_name = base_name


class SavePanel(QWidget):
    """Save-slot buttons plus the file picker, wired to plain callables.

    ``settings`` is the live settings dict (it holds ``save_path``) and
    ``on_open`` is called with a chosen path; persisting settings is the
    host's job (it writes them from its own ``open_save``).
    """

    def __init__(
        self,
        settings: dict,
        on_open: Callable[[str], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._on_open = on_open
        self._slot_paths: list = []
        self._current = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._slot_buttons: list[QPushButton] = []
        slot_row = QHBoxLayout()
        for n in range(SLOT_COUNT):
            btn = QPushButton(f"Slot {n + 1}\nno save yet")
            btn.setToolTip(_wt("Load this campaign slot's save. Slots "
                               "that don't exist yet are disabled."))
            btn.setMinimumHeight(_SLOT_MIN_HEIGHT)
            btn.setEnabled(False)
            btn.clicked.connect(lambda _=False, idx=n: self._load_slot(idx))
            self._slot_buttons.append(btn)
            slot_row.addWidget(btn, 1)
        root.addLayout(slot_row)

        open_btn = QPushButton("📁 Open another save file…")
        open_btn.setMaximumWidth(_OPEN_BTN_MAX_W)
        open_btn.clicked.connect(self.pick)
        open_row = QHBoxLayout()
        open_row.addWidget(open_btn)
        open_row.addStretch(1)
        root.addLayout(open_row)

        self._restyle_buttons()

    # ── host API ──────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Repopulate the slot labels from disk and mark the loaded slot."""
        self._slot_paths = self._discover_slot_paths()
        self.set_current(self._settings.get("save_path"))

    def set_current(self, path) -> None:
        """Mark *path* as the loaded save (adds the ● to its slot button)."""
        self._current = path or ""
        self._restyle_buttons()

    def load_last(self) -> Optional[str]:
        """Open the remembered save, else the newest one found on disk.

        Returns the path handed to ``on_open``, or ``None`` when neither the
        remembered save nor any discoverable one exists.
        """
        path = self._settings.get("save_path")
        if not path or not _file_exists(path):
            from mewgenics_overlay.core.discovery import newest_save
            found = newest_save()
            path = found["path"] if found else None
        if not path:
            return None
        self._on_open(path)
        return path

    def pick(self) -> None:
        """Ask for a save file and hand the choice to the host.

        Uses Qt's own dialog (not the OS-native one) - the native dialog is
        the usual suspect for platform crashes here. The host suspends auto
        click-through around this call and persists the chosen path.
        """
        path, _ = QFileDialog.getOpenFileName(
            self.window(),
            "Locate Mewgenics save",
            os.path.dirname(self._settings.get("save_path") or ""),
            _SAVE_FILTER,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            self._on_open(path)

    def restyle(self) -> None:
        """Re-derive the card colours from the active theme."""
        self._restyle_buttons()

    # ── slots ─────────────────────────────────────────────────────────────
    def _load_slot(self, index: int) -> None:
        if 0 <= index < len(self._slot_paths) and self._slot_paths[index]:
            self._on_open(self._slot_paths[index])

    def _discover_slot_paths(self) -> list:
        """steamcampaign01..03 saves under the discovered saves folder."""
        try:
            from mewgenics_overlay.core.discovery import find_all_saves
            records = find_all_saves()
        except Exception:
            log.exception("could not enumerate campaign saves")
            records = []
        by_num = {}
        for r in records:
            raw = str(r.get("path", "") or "")
            base = base_name(raw)
            if base.startswith("steamcampaign") and base.endswith(".sav"):
                try:
                    n = int(base[len("steamcampaign"):-4])
                except ValueError:
                    continue
                by_num[n] = raw
        return [by_num.get(n) for n in range(1, SLOT_COUNT + 1)]

    # ── theming ───────────────────────────────────────────────────────────
    def _restyle_buttons(self) -> None:
        """One source of truth for slot text, enabled state and colours."""
        grip, muted, good = _theme.C_GRIP, _theme.C_MUTED, _theme.C_GOOD
        for i, btn in enumerate(self._slot_buttons):
            path = self._slot_paths[i] if i < len(self._slot_paths) else None
            is_current = bool(path) and str(path) == self._current
            if path:
                sub = f"🐈 {base_name(path)}"
                if is_current:
                    sub += _CURRENT_MARK
            else:
                sub = "no save yet"
            btn.setText(f"Slot {i + 1}\n{sub}")
            btn.setEnabled(bool(path))
            if is_current:
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; "
                    f"border: 1px solid {good}; color: {good}; "
                    f"border-radius: 8px; padding: 6px 10px; "
                    f"text-align: center; }}\n"
                    f"QPushButton:hover {{ border-color: {good}; }}")
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; border: 1px "
                    f"solid {grip}; border-radius: 8px; padding: 6px 10px; "
                    f"text-align: center; }}\n"
                    f"QPushButton:hover {{ border-color: {muted}; }}\n"
                    f"QPushButton:disabled {{ color: {muted}; "
                    f"border-color: transparent; }}")
