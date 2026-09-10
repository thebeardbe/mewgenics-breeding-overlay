"""Hyprland shortcut backend: edit ``hyprland.conf``, then reload Hyprland.

Split out of :mod:`mewgenics_overlay.ui.shortcut_backends` to keep both
modules under the size budget. ``hyprctl keyword bind``/``unbind`` no longer
works on current Hyprland ("keyword can't work with non-legacy parsers. Use
eval."), so the backend edits the config and asks Hyprland to reload it
instead. The entry points are :func:`install` and :func:`remove`.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from mewgenics_overlay.ui.hotkeybinding import HotkeyBinding
from mewgenics_overlay.ui.shortcut_common import (
    HYPR_MARKER,
    QUOTE_SHELL,
    Runner,
    toggle_command,
)

log = logging.getLogger("mewgenics_overlay.desktopshortcut")

# One stable backup name per config file, overwritten on each change, so
# repeated installs/removals do not litter the config directory.
_HYPR_BACKUP_SUFFIX = ".mewgenics-overlay.bak"


def hyprland_conf_path() -> Optional[Path]:
    """The user's ``hyprland.conf``, or ``None`` when it does not exist."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    conf = base / "hypr" / "hyprland.conf"
    return conf if conf.is_file() else None


def hypr_bind_value(binding: HotkeyBinding, exec_path: str) -> str:
    """The ``bind`` value (combo, exec, shell-quoted command)."""
    return (f"{binding.hypr_combo()}, exec, "
            f"{toggle_command(exec_path, QUOTE_SHELL)}")


def hypr_bind_line(binding: HotkeyBinding, exec_path: str) -> str:
    """The full marked ``bind = ...`` line written to the config."""
    return f"bind = {hypr_bind_value(binding, exec_path)} {HYPR_MARKER}"


def is_managed_hypr(line: str) -> bool:
    """Whether *line* is a bind line the overlay wrote."""
    return line.strip().startswith("bind") and HYPR_MARKER in line


def install(binding: HotkeyBinding, exec_path: str,
            runner: Runner) -> tuple[bool, str]:
    """Persist the bind in ``hyprland.conf`` and reload the live config."""
    bind_line = hypr_bind_line(binding, exec_path)
    conf = hyprland_conf_path()
    if conf is None:
        return False, ("Hyprland config (hyprland.conf) was not found; add "
                       f"this line to persist the shortcut:\n  {bind_line}")
    try:
        write_managed_line(conf, bind_line)
    except OSError as exc:
        log.warning("could not update %s: %s", conf, exc)
        return False, (f"Could not update {conf} ({exc}). Add this line "
                       f"manually:\n  {bind_line}")
    error = reload_config(runner)
    if error:
        log.warning("saved the Hyprland bind to %s but the live reload "
                    "failed: %s", conf, error)
        return True, (f"Hyprland desktop shortcut saved to {conf}. Reload "
                      "Hyprland (hyprctl reload) to activate it: press "
                      f"{binding.format()} to toggle the overlay.")
    return True, (f"Hyprland desktop shortcut applied now and saved to "
                  f"{conf}: press {binding.format()} to toggle the overlay.")


def remove(runner: Runner) -> tuple[bool, str]:
    """Drop every managed bind line and reload the live config."""
    conf = hyprland_conf_path()
    if conf is None:
        return True, "No Hyprland config was found; nothing to remove."
    try:
        existing = conf.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"Could not read {conf} ({exc})."
    if not any(is_managed_hypr(line) for line in existing.splitlines()):
        return True, "No managed Hyprland shortcut was installed."
    updated = "\n".join(line for line in existing.splitlines()
                        if not is_managed_hypr(line)) + "\n"
    backup(conf, existing)
    _atomic_write(conf, updated)
    log.info("removed the managed Hyprland bind from %s", conf)
    error = reload_config(runner)
    message = "Hyprland desktop shortcut removed."
    if error:
        log.warning("removed the Hyprland bind from %s but the live reload "
                    "failed: %s", conf, error)
        message += " Reload Hyprland (hyprctl reload) to apply the removal."
    return True, message


def reload_config(runner: Runner) -> str:
    """Apply the edited config live; return an error detail, or "" on success.

    Current Hyprland rejects ``hyprctl keyword``, so the whole config is
    reloaded, falling back to the narrower ``config-only`` form when the
    first attempt fails.
    """
    hyprctl = shutil.which("hyprctl")
    if not hyprctl:
        return "hyprctl is not on PATH"
    detail = "hyprctl reload failed"
    for argv in ([hyprctl, "reload"], [hyprctl, "reload", "config-only"]):
        result = runner(argv)
        if result.ok:
            return ""
        detail = result.stderr.strip() or detail
    return detail


def write_managed_line(conf: Path, bind_line: str) -> None:
    """Replace the managed line, backing the file up only when it changes."""
    existing = conf.read_text(encoding="utf-8")
    kept = [line for line in existing.splitlines()
            if not is_managed_hypr(line)]
    updated = "\n".join([*kept, bind_line]) + "\n"
    if updated == existing:
        return
    backup(conf, existing)
    _atomic_write(conf, updated)
    log.info("saved the managed Hyprland bind to %s", conf)


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* via a same-directory temp + ``os.replace``.

    This is the user's live Hyprland config: a direct write that is
    interrupted (crash, full disk) would truncate it, and even the backup is
    overwritten the same way. The rename makes both updates atomic, matching
    :func:`mewgenics_overlay.ui.config.save`.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError as exc:
            log.debug("could not remove the temp file %s: %s", tmp, exc)
        raise


def backup(conf: Path, content: str) -> None:
    """Keep one stable backup per config file, overwriting the previous one."""
    target = conf.with_name(conf.name + _HYPR_BACKUP_SUFFIX)
    try:
        _atomic_write(target, content)
    except OSError as exc:
        log.warning("could not back up %s to %s: %s", conf, target, exc)
