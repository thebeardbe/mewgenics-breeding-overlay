"""Persistent settings for the overlay (JSON, atomic writes).

Stored per-user in a platform config dir:
  Linux:  ~/.config/mewgenics-overlay/config.json
  Windows: %APPDATA%\\mewgenics-overlay\\config.json
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger("mewgenics_overlay.config")

_KEY = "mewgenics-overlay"
DEFAULTS = {
    "theme": "noir",            # UI theme: film (bright) | noir (dark)
    "zoom": 1.0,               # user zoom multiplier (Ctrl+wheel/buttons)
    "check_for_updates": True, # ask GitHub for a newer release on start
    "save_path": None,            # last save shown
    "include_adventure": True,    # consider Adventure cats as partners
    "order": "risk",            # partner sort: "risk" (safe first) or "quality"
    "max_partners": 100,
    # Where the About-box "Report a problem" button points. None uses the
    # built-in default (the self-hosted Bugbox form); override it to point at
    # a different report form.
    "report_url": None,
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


def _coerce(saved: dict) -> dict:
    """Return *saved* typed against DEFAULTS, junk-proof.

    The config file is plain JSON sitting in a user-writable directory, so a
    corrupt or tampered value must never reach code that assumes a type
    (e.g. ``QRect(*window_rect)`` or ``int(max_partners)``) and crash the
    overlay at startup or mid-session. Keys the app owns at runtime but that
    are not defaults (``window_rect``, the pinned map, …) pass through as-is,
    except ``window_rect`` which is sanity-checked.
    """
    data = dict(DEFAULTS)

    def _int(v, default, lo=None, hi=None):
        try:
            i = int(v)
        except (TypeError, ValueError):
            return default
        if lo is not None and i < lo:
            i = lo
        if hi is not None and i > hi:
            i = hi
        return i

    def _bool(v, default):
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v != 0
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return default

    def _float(v, default):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        if f != f or f in (float("inf"), float("-inf")):
            return default
        return f

    def _rect(v):
        if not (isinstance(v, list) and len(v) == 4):
            return None
        try:
            xs = [int(x) for x in v]
        except (TypeError, ValueError):
            return None
        if not all(-100000 <= x <= 100000 for x in xs) or xs[2] <= 0 or xs[3] <= 0:
            return None
        return xs

    for key, v in saved.items():
        if key == "window_rect":
            data[key] = _rect(v)
            continue
        if key == "report_url":
            # URL defaults are validated once here: only http(s) is ever
            # accepted, so no consumer can be tricked into opening
            # javascript:/file: schemes even if a new call site appears.
            if isinstance(v, str) and v.strip().lower().startswith(
                    ("http://", "https://")):
                data[key] = v.strip()
            else:
                data[key] = DEFAULTS["report_url"]
            continue
        if key not in DEFAULTS:
            data[key] = v          # runtime-owned key (pinned map, …)
            continue
        default = DEFAULTS[key]
        if default is None:
            # null-defaulted strings: only accept real strings (or null)
            data[key] = v if isinstance(v, str) else None
        elif key == "max_partners":
            data[key] = _int(v, default, lo=1, hi=500)
        elif isinstance(default, bool):
            data[key] = _bool(v, default)
        elif isinstance(default, (int, float)):
            data[key] = _float(v, default)
        else:
            data[key] = v if isinstance(v, str) else default
    return data


def load() -> dict:
    saved: dict = {}
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            saved = raw
    except (OSError, ValueError):
        pass
    return _coerce(saved)


def save(data: dict) -> None:
    path = config_path()
    merged = dict(DEFAULTS)
    merged.update(data)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, sort_keys=True)
        os.replace(tmp, str(path))   # atomic
    except OSError as exc:
        log.warning("could not persist settings to %s: %s", path, exc)
        try:
            os.unlink(tmp)
        except OSError as exc2:
            log.debug("could not remove config temp %s: %s", tmp, exc2)
