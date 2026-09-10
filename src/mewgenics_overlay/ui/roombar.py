"""RoomBar - the "Breed room" selector and its Stimulation/Comfort values.

Extracted from ``PaletteWindow`` (god-file split, step 7): owns the room
combo, its population from the save's furniture (via resources.gpak
definitions) and the currently-applied Stimulation/Comfort pair that the
breeding math reads.

Window-agnostic: the live session, the gpak assets and the focused cat arrive
as plain getters, and ``on_change`` tells the host to recompute/redraw when the
selection (or the Stim/Comfort it implies) changes.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from mewgenics_overlay.core.stimulation import (
    STIMULATION_DEFAULT,
    room_env_map,
)
from mewgenics_overlay.ui import theme as _theme
from mewgenics_overlay.ui.comboarrow import style_combo
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt


class RoomBar(QWidget):
    """The "Breed room" label plus its combo, wired to plain callables.

    ``session_getter`` / ``assets_getter`` return the live save session and
    the gpak assets (either may be ``None``); ``focus_getter`` returns the
    focused cat (used to default the room pick). ``on_change`` is fired after
    the selection is applied and after a refresh that changed Stim/Comfort.
    """

    def __init__(
        self,
        session_getter: Callable[[], object],
        assets_getter: Callable[[], object],
        focus_getter: Callable[[], object],
        on_change: Callable[[], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._session_getter = session_getter
        self._assets_getter = assets_getter
        self._focus_getter = focus_getter
        self._on_change = on_change
        self._stim = STIMULATION_DEFAULT     # active breeding Stimulation
        self._comfort = 0.0                  # active room Comfort (roll chance)
        self._items: list = []               # combo entries (room, stim, comf)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)

        room_lbl = QLabel("Breed room:")
        room_lbl.setToolTip(_wt(
            "The room where you plan to breed.\n"
            "Its furniture changes two things in the numbers:\n"
            "• Stimulation - decides how often kittens inherit the better "
            "stat, and how likely a lone defect is to pass.\n"
            "• Comfort - decides how often a breeding attempt actually "
            "succeeds each night.\n"
            "Until a room is chosen, a neutral Stimulation of 50 is assumed."
        ))
        self._combo = QComboBox()
        self._combo.setToolTip(room_lbl.toolTip())
        self._combo.setMinimumWidth(170)
        self._combo.setEnabled(False)
        self._combo.currentIndexChanged.connect(self._on_index_changed)
        row.addWidget(room_lbl)
        row.addWidget(self._combo)

    # ── public state ───────────────────────────────────────────────────────
    @property
    def combo(self) -> QComboBox:
        """The room combo (hosts style it per theme)."""
        return self._combo

    def restyle(self) -> None:
        """Re-apply the theme's combo arrow colour."""
        style_combo(self._combo, _theme.C_MUTED)

    def stim_value(self) -> float:
        """Active Stimulation for pair math (selected room's furniture value
        or the default 50 when no room is chosen)."""
        return float(self._stim)

    def comfort_value(self) -> float:
        return max(float(self._comfort), 0.0)

    def selected_room(self):
        idx = self._combo.currentIndex()
        if 0 <= idx < len(self._items):
            entry = self._items[idx]
            return entry[0] if entry else None
        return None

    # ── population ─────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Rebuild the room list from furniture (needs resources.gpak defs)."""
        prev_room = self.selected_room()
        rooms_env: dict = {}
        session = self._session_getter()
        assets = self._assets_getter()
        if session is not None and session.data is not None \
                and assets is not None:
            fb = session.data.furniture_by_room or {}
            if fb and assets.furniture_data:
                rooms_env = room_env_map(fb, assets.furniture_data)
        self._combo.blockSignals(True)
        self._combo.clear()
        self._items = []
        self._combo.addItem("- Stim 50 (no room)")
        self._items.append(None)
        for room in sorted(rooms_env, key=lambda r: -rooms_env[r][0]):
            stim, comfort = float(rooms_env[room][0]), float(rooms_env[room][1])
            label = f"{room} - Stim {stim:g}"
            if comfort:
                label += f", Comf {comfort:g}"
            self._combo.addItem(label)
            self._items.append((room, stim, comfort))
        self._combo.setEnabled(bool(rooms_env))
        # prefer the previous pick, else the focused cat's room
        focus = self._focus_getter()
        target = None
        if prev_room is not None and prev_room in rooms_env:
            target = prev_room
        elif focus is not None and focus.room in rooms_env:
            target = focus.room
        idx = 0
        for i, entry in enumerate(self._items):
            if entry is not None and entry[0] == target:
                idx = i
                break
        old_stim = self.stim_value()
        old_comf = self.comfort_value()
        self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)
        self._apply_selection()
        if focus is not None and (self.stim_value() != old_stim
                                  or self.comfort_value() != old_comf):
            self._on_change()   # numbers change with Stim/Comfort

    # ── selection ──────────────────────────────────────────────────────────
    def _on_index_changed(self, index: int) -> None:
        self._apply_selection()
        self._on_change()

    def _apply_selection(self) -> None:
        entry = None
        idx = self._combo.currentIndex()
        if 0 <= idx < len(self._items):
            entry = self._items[idx]
        if entry is None:
            self._stim = STIMULATION_DEFAULT
            self._comfort = 0.0
        else:
            self._stim = float(entry[1])
            self._comfort = float(entry[2])
