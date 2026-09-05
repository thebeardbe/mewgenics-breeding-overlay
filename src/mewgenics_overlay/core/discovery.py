"""Locate Mewgenics save files on Windows and Linux (native + Proton).

Save layout (both platforms):

    <APPDATA>/Glaiel Games/Mewgenics/<SteamNumericId>/saves/*.sav
    C:/Users/<you>/AppData/Roaming/Glaiel Games/Mewgenics/<id>/saves/steamcampaign01.sav  (Windows)
    ~/.steam/steam/steamapps/compatdata/<appid>/pfx/drive_c/users/<you>/AppData/Roaming/... (Proton)

Override with the MEWGENICS_SAVES_ROOT environment variable (a path to the
`Glaiel Games/Mewgenics` root, i.e. the directory containing per-profile
folders with a `saves/` subdirectory).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from mewgenics_overlay.vendor.save_parser import find_save_files

_ENV_ROOT = "MEWGENICS_SAVES_ROOT"
_GAME_VENDOR_DIR = "Glaiel Games/Mewgenics"


def _proton_compat_roots() -> Iterable[Path]:
    """Common places a Steam library may live on Linux."""
    home = Path.home()
    candidates = [
        home / ".steam/steam/steamapps/compatdata",
        home / ".local/share/Steam/steamapps/compatdata",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata",  # Flatpak
        Path("/run/media") if False else None,  # placeholder guard (never matched)
    ]
    for c in candidates:
        if c and c.is_dir():
            yield c


def _roaming_roots() -> Iterable[Path]:
    """Return paths to `<root>/Glaiel Games/Mewgenics` that exist."""
    seen: set[str] = set()
    roots: list[Path] = []

    override = os.environ.get(_ENV_ROOT)
    if override:
        roots.append(Path(override))

    # Windows native
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / _GAME_VENDOR_DIR)

    # Proton prefixes (Windows app data lives under each prefix's drive_c)
    for compat in _proton_compat_roots():
        try:
            pfx_users = compat.glob("*/pfx/drive_c/users/*/AppData/Roaming")
        except OSError:
            continue
        for users in pfx_users:
            candidate = users / _GAME_VENDOR_DIR
            if candidate.is_dir():
                roots.append(candidate)

    # XDG config (some native Linux ports / lutris layouts)
    xdg = os.environ.get("XDG_CONFIG_HOME") or (str(Path.home() / ".config"))
    roots.append(Path(xdg) / _GAME_VENDOR_DIR)

    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            yield root


def find_all_saves() -> list[dict]:
    """Return newest-first save records across every discovered game root.

    Each record: {"path": str, "root": str, "mtime": float}. Duplicate
    entries for the same file (e.g. mirrored Steam library paths) are merged.
    """
    records: dict[str, dict] = {}
    for root in _roaming_roots():
        try:
            for path in find_save_files(str(root)):
                p = Path(path)
                try:
                    canon = str(p.resolve())
                except OSError:
                    canon = str(p)
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                existing = records.get(canon)
                if existing is None or existing["mtime"] < mtime:
                    records[canon] = {"path": str(p), "root": str(root), "mtime": mtime}
        except OSError:
            continue
    result = list(records.values())
    result.sort(key=lambda r: r["mtime"], reverse=True)
    return result


def newest_save() -> dict | None:
    """Return the most recently modified save file, if any."""
    records = find_all_saves()
    return records[0] if records else None
