"""Persistent settings for the overlay (JSON, atomic writes).

Stored per-user in a platform config dir:
  Linux:  ~/.config/mewgenics-overlay/config.json
  Windows: %APPDATA%\\mewgenics-overlay\\config.json
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_KEY = "mewgenics-overlay"
DEFAULTS = {
    "theme": "noir",            # UI theme: film (bright) | noir (dark)
    "save_path": None,            # last save shown
    "include_adventure": True,    # consider Adventure cats as partners
    "order": "risk",            # partner sort: "risk" (safe first) or "quality"
    "max_partners": 100,
    "show_blocked": 3,
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    d = Path(base) / _KEY
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update(saved)
    except (OSError, ValueError):
        pass
    return data


def save(data: dict) -> None:
    path = config_path()
    merged = dict(DEFAULTS)
    merged.update(data)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, sort_keys=True)
        os.replace(tmp, str(path))   # atomic
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
