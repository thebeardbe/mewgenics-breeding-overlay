"""Shared primitives for the desktop shortcut backends.

Constants, the injectable command runner and the manual-instruction text used
by both :mod:`mewgenics_overlay.ui.shortcut_backends` and
:mod:`mewgenics_overlay.ui.desktopshortcut`. Kept separate so each backend
module stays small and no two of them import each other.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Union

from mewgenics_overlay.ui import hotkeybinding
from mewgenics_overlay.ui.hotkeybinding import HotkeyBinding

log = logging.getLogger("mewgenics_overlay.desktopshortcut")

GNOME = "gnome"
KDE = "kde"
HYPRLAND = "hyprland"
OTHER = "other"

APP_NAME = "Mewgenics Breeding Overlay"
APP_SLUG = "mewgenics-overlay"
COMMAND_TIMEOUT_S = 15

TOGGLE_FLAG = "--toggle"

# How the executable is quoted in the bound command. Each consumer parses its
# value differently, so one style does not fit all.
QUOTE_PLAIN = "plain"      # informational text only (never executed as-is)
QUOTE_SHELL = "shell"      # shell-parsed values: GNOME command, Hyprland exec
QUOTE_DESKTOP = "desktop"  # Desktop Entry Exec= (KDE launcher)

# Markers that tell our own configuration apart from the user's, so remove()
# never deletes something we did not write.
HYPR_MARKER = "# mewgenics-overlay"
DESKTOP_MARKER = "# Managed by Mewgenics Breeding Overlay"

BindingLike = Union[HotkeyBinding, str, None]


@dataclass
class CommandResult:
    """Outcome of one command: ``ok`` plus captured stdout/stderr."""

    ok: bool
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str]], CommandResult]


def run_command(argv: Sequence[str]) -> CommandResult:
    """Run *argv* with a timeout; never raises (a failure is ``ok=False``)."""
    cmd = [str(arg) for arg in argv]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=COMMAND_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("command failed: %s (%s)", " ".join(cmd), exc)
        return CommandResult(False, "", str(exc))
    if proc.returncode != 0:
        log.info("command exited %d: %s", proc.returncode, " ".join(cmd))
    return CommandResult(proc.returncode == 0, proc.stdout, proc.stderr)


def desktop_label(desktop: str) -> str:
    return {
        GNOME: "GNOME",
        KDE: "KDE Plasma",
        HYPRLAND: "Hyprland",
        OTHER: "this desktop",
    }.get(desktop, "this desktop")


def as_binding(binding: BindingLike) -> HotkeyBinding:
    if isinstance(binding, HotkeyBinding):
        return binding
    return hotkeybinding.binding_or_default(binding)


def _desktop_quote(path: str) -> str:
    """Desktop Entry ``Exec=`` double-quoted argument for *path*.

    The Desktop Entry spec reserves backslash, double quote, backtick,
    dollar and percent inside a quoted argument, so each is escaped; an
    unescaped one would make the launcher parse a different command (or run
    an injected one). A percent is doubled rather than backslash-escaped
    because ``%f``/``%u``/``%c``/``%k`` are field codes a launcher expands.
    Spaces are handled by the surrounding quotes alone.
    """
    escaped = path
    for char in ("\\", '"', "`", "$"):
        escaped = escaped.replace(char, "\\" + char)
    escaped = escaped.replace("%", "%%")
    return f'"{escaped}"'


def toggle_command(exec_path: str, quote: str = QUOTE_PLAIN) -> str:
    """The command a desktop shortcut runs: ``<exec_path> --toggle``.

    Single source for the bound command so the GNOME ``command`` value, the
    Hyprland ``exec`` parameter and the KDE ``Exec=`` line cannot drift.
    *quote* picks the syntax the consumer parses: ``QUOTE_SHELL`` shell-quotes
    the path (a path with spaces or metacharacters cannot break the binding
    or inject another argument), ``QUOTE_DESKTOP`` uses the Desktop Entry
    double-quoted executable form (with the reserved characters escaped), and
    ``QUOTE_PLAIN`` is the bare form for text that is shown to the user and
    never parsed as a command line.
    """
    if quote == QUOTE_SHELL:
        return f"{shlex.quote(exec_path)} {TOGGLE_FLAG}"
    if quote == QUOTE_DESKTOP:
        return f"{_desktop_quote(exec_path)} {TOGGLE_FLAG}"
    return f"{exec_path} {TOGGLE_FLAG}"


def first_tool(*names: str) -> Optional[str]:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def shell(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in argv)


def manual_install(binding: HotkeyBinding, command: str,
                   desktop: str) -> str:
    return (f"No automatic desktop shortcut integration for "
            f"{desktop_label(desktop)}. Add a custom shortcut in your desktop "
            f"settings that runs:\n  {command}\n"
            f"and bind it to {binding.format()}.")


def manual_remove(desktop: str) -> str:
    return (f"No automatic desktop shortcut integration for "
            f"{desktop_label(desktop)}; remove any shortcut you added "
            "manually.")
