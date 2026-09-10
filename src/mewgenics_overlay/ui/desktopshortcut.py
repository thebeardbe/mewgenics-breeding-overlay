"""Desktop-environment shortcut integration for the Linux toggle.

On Linux the app cannot grab a global key, so the desktop environment owns
the shortcut and runs ``<app> --toggle``. This module detects the active
desktop, works out the command to bind, and installs (or removes) a shortcut
through the matching backend.

The per-desktop work lives in
:mod:`mewgenics_overlay.ui.shortcut_backends`; this module owns the public
API and the environment detection. The actual work is driven through an
injectable command runner, so the logic is unit-testable without a real
session. Nothing outside the app's own files is touched unless :func:`install`
is called explicitly from the CLI or a Settings button.

Supported backends:

  * GNOME - a ``gsettings`` custom keybinding under our own path, appended to
    the user's existing list rather than replacing it.
  * KDE Plasma - a ``.desktop`` launcher plus a best-effort D-Bus/kwriteconfig
    registration; on any failure the exact manual commands are returned.
  * Hyprland - persisted as one clearly marked line in ``hyprland.conf``
    (backed up first) and applied with ``hyprctl reload``.
  * anything else - manual instructions, with ``ok=False`` so the UI shows them.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from typing import Optional

from mewgenics_overlay.ui import shortcut_backends
from mewgenics_overlay.ui import shortcut_common
from mewgenics_overlay.ui.shortcut_common import (
    CommandResult,  # re-exported for callers and tests
    GNOME,
    HYPRLAND,
    KDE,
    OTHER,
    Runner,         # re-exported for callers and tests
    run_command,
)

__all__ = [
    "GNOME",
    "HYPRLAND",
    "KDE",
    "OTHER",
    "CommandResult",
    "Runner",
    "run_command",
    "detect_desktop",
    "desktop_label",
    "command_path",
    "toggle_command",
    "install",
    "remove",
]

log = logging.getLogger("mewgenics_overlay.desktopshortcut")

_APP_SLUG = "mewgenics-overlay"
# ``python -m mewgenics_overlay`` sets argv[0] to this executable file, which
# is not the command a shortcut should run; fall back to the console script.
_MODULE_ENTRY_POINTS = frozenset({"__main__.py"})


def detect_desktop() -> str:
    """Identify the active desktop as gnome / kde / hyprland / other.

    Detection keys off ``XDG_CURRENT_DESKTOP`` (``gnome``/``unity``/
    ``cinnamon`` -> GNOME, ``kde``/``plasma`` -> KDE) and
    ``HYPRLAND_INSTANCE_SIGNATURE`` (Hyprland sets no useful
    ``XDG_CURRENT_DESKTOP``). ``XDG_SESSION_TYPE`` is deliberately not used:
    it names the display protocol (``wayland``/``x11``), not the desktop.
    Tool availability is only a fallback for sessions that set none of the
    above.
    """
    env = os.environ
    current = (env.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "gnome" in current or "unity" in current or "cinnamon" in current:
        return GNOME
    if "kde" in current or "plasma" in current:
        return KDE
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return HYPRLAND
    if shutil.which("gsettings"):
        return GNOME
    if shortcut_common.first_tool(
            "qdbus6", "qdbus", "kwriteconfig6", "kwriteconfig5"):
        return KDE
    if shutil.which("hyprctl"):
        return HYPRLAND
    return OTHER


def desktop_label(desktop: Optional[str] = None) -> str:
    """Human-readable name for the detected (or given) desktop."""
    return shortcut_common.desktop_label(desktop or detect_desktop())


def command_path() -> str:
    """Absolute path to the executable a shortcut should run.

    Prefers the running entry point (``sys.argv[0]``) and falls back to the
    ``mewgenics-overlay`` console script on ``PATH``. A module entry point
    (``__main__.py``, not executable) does not qualify, so launching via
    ``python -m mewgenics_overlay`` still binds the installed command.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if os.path.basename(argv0) not in _MODULE_ENTRY_POINTS:
        resolved = _resolve_executable(argv0)
        if resolved:
            return resolved
    resolved = _resolve_executable(shutil.which(_APP_SLUG) or "")
    if resolved:
        return resolved
    if argv0 and os.path.basename(argv0) not in _MODULE_ENTRY_POINTS:
        return os.path.abspath(argv0)
    return _APP_SLUG


def toggle_command() -> str:
    """The bare command a shortcut runs, e.g. ``/usr/bin/app --toggle``.

    Backends build their own quoted form from :func:`command_path`; this
    unquoted form is only for messages and manual instructions.
    """
    return shortcut_common.toggle_command(command_path())


def install(binding: shortcut_common.BindingLike,
            runner: Runner = run_command) -> tuple[bool, str]:
    """Install a desktop shortcut for *binding*.

    Returns ``(ok, message)``; *message* is shown to the user (Settings
    label / CLI stdout) and explains any manual fallback. The current
    combination is converted to the active environment's syntax.
    """
    parsed = shortcut_common.as_binding(binding)
    desktop = detect_desktop()
    log.info("installing the %s desktop shortcut for %s",
             desktop, parsed.format())
    return shortcut_backends.install_for(desktop, parsed, command_path(),
                                         runner)


def remove(runner: Runner = run_command) -> tuple[bool, str]:
    """Remove the shortcut we installed; idempotent and never touches others."""
    desktop = detect_desktop()
    log.info("removing the %s desktop shortcut", desktop)
    return shortcut_backends.remove_for(desktop, runner)


def _resolve_executable(path: str) -> str:
    if not path:
        return ""
    real = os.path.realpath(path)
    if os.path.isfile(real) and os.access(real, os.X_OK):
        return real
    found = shutil.which(path)
    return os.path.realpath(found) if found else ""
