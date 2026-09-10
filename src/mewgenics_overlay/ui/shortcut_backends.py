"""Per-desktop shortcut backends for :mod:`mewgenics_overlay.ui.desktopshortcut`.

Owns only the environment-specific work (GNOME ``gsettings``, KDE
launcher/D-Bus, Hyprland ``hyprctl``). ``desktopshortcut`` detects the
environment and dispatches here. Shared primitives (the injectable command
runner, constants, manual text) live in ``shortcut_common``.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from typing import Optional, Sequence

from mewgenics_overlay.ui import shortcut_hyprland
from mewgenics_overlay.ui.hotkeybinding import HotkeyBinding
from mewgenics_overlay.ui.shortcut_common import (
    APP_NAME,
    APP_SLUG,
    DESKTOP_MARKER,
    GNOME,
    HYPRLAND,
    KDE,
    QUOTE_DESKTOP,
    QUOTE_SHELL,
    Runner,
    first_tool,
    manual_install,
    manual_remove,
    shell,
    toggle_command,
)

log = logging.getLogger("mewgenics_overlay.desktopshortcut")

_GNOME_MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
_GNOME_CUSTOM_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
    "mewgenics-overlay/")
_GNOME_SCHEMA = (
    f"{_GNOME_MEDIA_KEYS}.custom-keybinding:{_GNOME_CUSTOM_PATH}")
_GNOME_KEYS = ("name", "command", "binding")

_KDE_SERVICE = "org.kde.kglobalaccel"
_KDE_PATH = "/kglobalaccel"
_KDE_IFACE = "org.kde.KGlobalAccel"


def install_for(desktop: str, binding: HotkeyBinding, exec_path: str,
                runner: Runner) -> tuple[bool, str]:
    if desktop == GNOME:
        return _install_gnome(binding, exec_path, runner)
    if desktop == KDE:
        return _install_kde(binding, exec_path, runner)
    if desktop == HYPRLAND:
        return shortcut_hyprland.install(binding, exec_path, runner)
    return False, manual_install(binding, toggle_command(exec_path), desktop)


def remove_for(desktop: str, runner: Runner) -> tuple[bool, str]:
    if desktop == GNOME:
        return _remove_gnome(runner)
    if desktop == KDE:
        return _remove_kde(runner)
    if desktop == HYPRLAND:
        return shortcut_hyprland.remove(runner)
    # Unsupported desktop: there is no integration we could have installed, so
    # removal is a successful no-op (the UI must not show an error colour).
    return True, manual_remove(desktop)


# ── GNOME / gsettings ─────────────────────────────────────────────────────
def _gnome_paths(runner: Runner) -> Optional[list[str]]:
    """The user's custom-keybinding paths, or ``None`` when unavailable."""
    result = runner(["gsettings", "get", _GNOME_MEDIA_KEYS,
                     "custom-keybindings"])
    if not result.ok:
        return None
    return _parse_gvariant_list(result.stdout)


def _install_gnome(binding: HotkeyBinding, exec_path: str,
                   runner: Runner) -> tuple[bool, str]:
    paths = _gnome_paths(runner)
    if paths is None:
        return False, ("GNOME custom keybindings are unavailable (the schema "
                       "is not installed, or the stored value could not be "
                       "read). Set the shortcut in Settings > Keyboard > "
                       "Custom Shortcuts instead.")
    updated = list(paths)
    if _GNOME_CUSTOM_PATH not in updated:
        updated.append(_GNOME_CUSTOM_PATH)
    bound = toggle_command(exec_path, QUOTE_SHELL)
    steps = [
        (["gsettings", "set", _GNOME_MEDIA_KEYS, "custom-keybindings",
          _format_gvariant_list(updated)], "save the custom-keybindings list"),
        (["gsettings", "set", _GNOME_SCHEMA, "name", APP_NAME],
         "name the shortcut"),
        (["gsettings", "set", _GNOME_SCHEMA, "command", bound],
         "set the shortcut command"),
        (["gsettings", "set", _GNOME_SCHEMA, "binding",
          binding.gtk_accelerator()], "set the key combination"),
    ]
    for argv, what in steps:
        result = runner(argv)
        if not result.ok:
            detail = result.stderr.strip() or "command failed"
            return False, (f"Could not {what} ({detail}). Run it manually:\n"
                           f"  {shell(argv)}")
    return True, (f"GNOME desktop shortcut installed: press "
                  f"{binding.format()} to toggle the overlay.")


def _remove_gnome(runner: Runner) -> tuple[bool, str]:
    paths = _gnome_paths(runner)
    if paths is None:
        return False, ("GNOME media-keys settings are unavailable (the "
                       "schema is not installed); nothing was changed.")
    remaining = [path for path in paths if path != _GNOME_CUSTOM_PATH]
    if remaining != paths:
        result = runner(["gsettings", "set", _GNOME_MEDIA_KEYS,
                         "custom-keybindings",
                         _format_gvariant_list(remaining)])
        if not result.ok:
            detail = result.stderr.strip() or "command failed"
            return False, (f"Could not update the GNOME custom-keybindings "
                           f"list ({detail}).")
    for key in _GNOME_KEYS:
        # Resetting a key that was never set is harmless, so remove() stays
        # idempotent; only a real failure is worth logging.
        result = runner(["gsettings", "reset", _GNOME_SCHEMA, key])
        if not result.ok:
            log.info("could not reset GNOME key %s: %s", key, result.stderr)
    return True, "GNOME desktop shortcut removed."


def _parse_gvariant_list(text: str) -> Optional[list[str]]:
    """Parse ``gsettings get`` output for an ``as`` list of strings.

    Only the literal empty list (``[]`` / ``@as []``) is an empty list;
    empty or whitespace-only output is a read failure and returns ``None``,
    as does anything that cannot be parsed, so a caller never confuses a
    failed read with "the user has no custom keybindings" and drops the
    entries it could not read.
    """
    raw = text.strip()
    if not raw:
        log.warning("gsettings returned no value for a GVariant string list")
        return None
    if raw.startswith("@as "):
        raw = raw[4:].strip()
    if raw == "[]":
        return []
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        log.warning("could not parse a GVariant string list: %r", text)
        return None
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    log.warning("GVariant value was not a string list: %r", text)
    return None


def _format_gvariant_list(paths: Sequence[str]) -> str:
    return "[" + ", ".join(f"'{path}'" for path in paths) + "]"


# ── KDE Plasma ────────────────────────────────────────────────────────────
def _kde_desktop_file() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "applications" / f"{APP_SLUG}.desktop"


def _owns_kde_launcher(path: Path) -> tuple[bool, str]:
    """Whether we may write *path*, and any read error to report.

    A missing path is ours to create. An existing file is ours only when it
    carries :data:`DESKTOP_MARKER`; anything else must never be overwritten or
    deleted. Returns ``(owned, error)`` where *error* is non-empty only when an
    existing file could not be read.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return True, ""
    except OSError as exc:
        log.warning("could not read the KDE launcher %s: %s", path, exc)
        return False, f"Could not read the existing file {path} ({exc})."
    return DESKTOP_MARKER in content, ""


def _write_kde_desktop_file(exec_path: str) -> tuple[Optional[Path], str]:
    path = _kde_desktop_file()
    owned, error = _owns_kde_launcher(path)
    if error:
        return None, error
    if not owned:
        log.info("not overwriting %s: it was not written by the overlay",
                 path)
        return None, (
            f"{path} already exists and was not written by this app, so it "
            "was left untouched. Move it aside or rename it, then try "
            "again.")
    content = (
        f"{DESKTOP_MARKER}\n"
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={toggle_command(exec_path, QUOTE_DESKTOP)}\n"
        "X-KDE-GlobalAccel-CommandShortcut=true\n"
        "NoDisplay=true\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        log.warning("could not write the KDE launcher %s: %s", path, exc)
        return None, (f"Could not write the KDE launcher {path}. Check that "
                       "your home directory is writable.")
    return path, ""


def _install_kde(binding: HotkeyBinding, exec_path: str,
                 runner: Runner) -> tuple[bool, str]:
    path, error = _write_kde_desktop_file(exec_path)
    if path is None:
        return False, error
    qdbus = first_tool("qdbus6", "qdbus")
    if qdbus:
        ok, error = _register_kde_accel(qdbus, binding, runner)
        if ok:
            return True, (f"KDE Plasma desktop shortcut installed "
                          f"(launcher {path}): press {binding.format()} to "
                          "toggle the overlay.")
        log.info("KDE D-Bus registration failed: %s", error)
    kwrite = first_tool("kwriteconfig6", "kwriteconfig5")
    if kwrite:
        ok, error = _persist_kde_accel(kwrite, binding, runner)
        if ok:
            return True, (f"KDE Plasma desktop shortcut saved "
                          f"(launcher {path}): press {binding.format()} to "
                          "toggle the overlay.")
        log.info("KDE kwriteconfig registration failed: %s", error)
    return False, _kde_manual(binding, path)


def _kde_action_id() -> tuple[str, str]:
    return APP_SLUG, APP_SLUG


def _register_kde_accel(qdbus: str, binding: HotkeyBinding,
                        runner: Runner) -> tuple[bool, str]:
    component, action = _kde_action_id()
    registered = runner([qdbus, _KDE_SERVICE, _KDE_PATH,
                         f"{_KDE_IFACE}.doRegister", component, action])
    if not registered.ok:
        return False, registered.stderr.strip() or "doRegister failed"
    assigned = runner([qdbus, _KDE_SERVICE, _KDE_PATH,
                       f"{_KDE_IFACE}.setShortcut", component, action,
                       binding.kde_shortcut(), "0"])
    if not assigned.ok:
        return False, assigned.stderr.strip() or "setShortcut failed"
    return True, ""


def _persist_kde_accel(kwrite: str, binding: HotkeyBinding,
                       runner: Runner) -> tuple[bool, str]:
    # kglobalshortcutsrc stores "<Shortcut>\t<DefaultShortcut>\t<Name>"; the
    # binding belongs in both shortcut slots, not the app name, or the key is
    # never bound even though the write succeeds.
    component, action = _kde_action_id()
    shortcut = binding.kde_shortcut()
    value = f"{shortcut}\t{shortcut}\t{APP_NAME}"
    result = runner([kwrite, "--file", "kglobalshortcutsrc", "--group",
                     component, "--key", action, value])
    if not result.ok:
        return False, result.stderr.strip() or "kwriteconfig failed"
    return True, ""


def _kde_manual(binding: HotkeyBinding, path: Path) -> str:
    component, action = _kde_action_id()
    qdbus = first_tool("qdbus6", "qdbus") or "qdbus"
    return (
        "Could not register the shortcut with KDE automatically. "
        f"The launcher was written to {path}. To finish manually, run:\n"
        f"  {qdbus} {_KDE_SERVICE} {_KDE_PATH} {_KDE_IFACE}.doRegister "
        f"{component} {action}\n"
        f"  {qdbus} {_KDE_SERVICE} {_KDE_PATH} {_KDE_IFACE}.setShortcut "
        f"{component} {action} {binding.kde_shortcut()} 0\n"
        "or set it in System Settings > Shortcuts.")


def _remove_kde(runner: Runner) -> tuple[bool, str]:
    path = _kde_desktop_file()
    component, action = _kde_action_id()
    owned, error = _owns_kde_launcher(path)
    if error:
        return False, error
    if not owned:
        # install() never creates a foreign launcher, so any accel entry or
        # D-Bus shortcut belongs to the user's own file; leave the file, the
        # kglobalshortcutsrc entry and the registered shortcut alone.
        log.info("not removing %s: it was not written by the overlay", path)
        return True, manual_remove(KDE)
    removed = False
    if path.exists():
        try:
            path.unlink()
            removed = True
        except OSError as exc:
            log.warning("could not remove %s: %s", path, exc)
            return False, f"Could not remove {path} ({exc})."
    # install() may have used kwriteconfig instead of D-Bus; delete the same
    # group/key so no stale global-shortcut entry survives the launcher.
    kwrite = first_tool("kwriteconfig6", "kwriteconfig5")
    if kwrite:
        result = runner([kwrite, "--file", "kglobalshortcutsrc", "--group",
                         component, "--key", action, "--delete"])
        if not result.ok:
            detail = result.stderr.strip() or "kwriteconfig failed"
            log.warning("could not delete the KDE shortcut entry: %s", detail)
            return False, (f"Could not remove the KDE shortcut entry "
                           f"({detail}).")
    qdbus = first_tool("qdbus6", "qdbus")
    if qdbus:
        runner([qdbus, _KDE_SERVICE, _KDE_PATH,
                f"{_KDE_IFACE}.setShortcut", component, action, "", "0"])
    if removed:
        return True, "KDE Plasma desktop shortcut removed."
    return True, "No KDE Plasma desktop shortcut was installed."


# ── Hyprland / hyprctl ────────────────────────────────────────────────────
# The Hyprland backend lives in mewgenics_overlay.ui.shortcut_hyprland (split
# out to keep this module under the size budget); install_for/remove_for call
# it directly.
