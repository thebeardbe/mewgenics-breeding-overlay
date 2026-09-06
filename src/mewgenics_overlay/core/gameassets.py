"""Optional game-asset access for birth-defect / mutation effect text.

The save file stores each mutation/defect as (body part, numeric id). The
human-readable *effect* description (e.g. "+1 CON", "adds a bruise") lives in
the game's ``resources.gpak`` (``data/mutations/<part>.gon`` + text CSVs).
The vendored parser already knows how to read those (``GameData.from_gpak``),
so we reuse it — but only on machines where the game is installed.

When the gpak is missing, everything degrades gracefully: no effect text,
no error. Loading happens once, off the UI thread (the gpak is several GB,
though only small regions are actually read).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from mewgenics_overlay.vendor.save_parser import GameData

_GPAK_REL = Path("steamapps/common/Mewgenics/resources.gpak")


def _steam_roots() -> list[Path]:
    """Plausible Steam library locations on Windows + Linux."""
    roots: list[Path] = []
    home = Path.home()
    candidates = [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    for c in candidates:
        if c.is_dir():
            roots.append(c)
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        pf = os.environ.get(var)
        if pf:
            roots.append(Path(pf) / "Steam")
    return roots


def locate_gpak() -> Optional[str]:
    """Return the path of Mewgenics' resources.gpak, if the game is installed."""
    override = os.environ.get("MEWGENICS_GPAK")
    if override:
        return override if os.path.exists(override) else None
    for root in _steam_roots():
        candidate = root / _GPAK_REL
        if candidate.exists():
            return str(candidate)
    return None


class GameAssets:
    """Loaded mutation tables from resources.gpak (effect descriptions)."""

    def __init__(self, gpak_path: Optional[str] = None):
        self.gpak_path = gpak_path
        self._mutation_data: dict = {}
        self.furniture_data: dict = {}
        self.ok = False
        if gpak_path and os.path.exists(gpak_path):
            gd = GameData.from_gpak(gpak_path)
            self._mutation_data = getattr(gd, "visual_mutation_data", {}) or {}
            self.furniture_data = getattr(gd, "furniture_data", {}) or {}
            self.ok = bool(self._mutation_data or self.furniture_data)

    # The game keeps limb mutations (incl. arms) in legs.gon and fur/texture
    # mutations in texture.gon; slot groups use different names.
    _CATEGORY_ALIAS = {"arms": "legs", "fur": "texture"}

    def effect_for(self, group: Optional[str], mutation_id) -> str:
        """Effect text for one (body-part group, mutation id), '' if unknown.

        GameData tuples are (raw_name, stat_desc, gon_stats, is_birth_defect);
        the visible effect prefers the CSV description and appends the raw GON
        stat string when it adds anything.
        """
        if not group or mutation_id is None:
            return ""
        category = self._CATEGORY_ALIAS.get(group, group)
        info = self._mutation_data.get(category, {}).get(int(mutation_id))
        if not info:
            return ""
        raw, stat_desc, gon_stats = str(info[0]), str(info[1]), str(info[2])
        desc = stat_desc or raw
        if gon_stats and gon_stats.lower() != desc.lower():
            desc = f"{desc} ({gon_stats})"
        return desc
