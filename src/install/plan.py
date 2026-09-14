"""Plan what a Mewgenics overlay install would do, without doing any of it.

This is the pure half of the one-click installers. Given the target operating
system, the install variant and the paths involved, :func:`plan_install`
returns an :class:`InstallPlan` describing every file to place, the Wine DLL
override the install needs and where it belongs, the Steam launch option to
recommend, and whether Steam achievements stay on - each with a short reason.
Copying files, writing configuration and touching a Proton prefix happen in
later steps: nothing here reads or writes the filesystem, opens a network
connection, or imports Qt.

The three variants differ in what the game is told:

* ``overlay-only`` - the overlay alone; no mod, no DLL override, no launch
  option, and achievements stay on.
* ``standalone`` - the overlay plus the companion DLL mod. Mewjector loads it
  by shadowing ``version.dll``, so the loader *and* its ``chainloader.ini``
  must sit beside the game executable, with at least one mod DLL in ``mods/``;
  a lone loader is refused because nothing would load it. Under Proton the
  game needs the ``version=n,b`` override; achievements still stay on because
  nothing passes ``-modpaths`` or enables the debug console.
* ``mewtator`` - the overlay plus the asset mods that Mewtator loads. Mewtator
  is a separate mod-path mechanism and does not use the Mewjector loader, so
  the loader files are refused here. Mewtator passes ``-modpaths``, and that
  flag is what turns Steam achievements off for the session.

Unknown operating systems and variants are refused rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterable, Optional, Tuple, Type, Union

# Operating systems the planner understands. The game is Windows-only, so
# ``linux`` and ``nixos`` both run it under Proton and share the Wine rules.
OS_LINUX = "linux"
OS_NIXOS = "nixos"
OS_WINDOWS = "windows"

_KNOWN_OSES = frozenset({OS_LINUX, OS_NIXOS, OS_WINDOWS})

# Install variants, named as the user sees them.
VARIANT_OVERLAY_ONLY = "overlay-only"
VARIANT_STANDALONE = "standalone"
VARIANT_MEWTATOR = "mewtator"

_KNOWN_VARIANTS = frozenset(
    {VARIANT_OVERLAY_ONLY, VARIANT_STANDALONE, VARIANT_MEWTATOR}
)

# Mewjector, the DLL chainloader the companion mod ships with. It loads by
# shadowing the system ``version.dll``, so the loader and its config must sit
# beside the game executable; Mewjector then scans ``mods/`` for mod DLLs.
MEWJECTOR_LOADER_NAME = "version.dll"
MEWJECTOR_CONFIG_NAME = "chainloader.ini"
# Folder Mewjector scans, relative to the game executable.
MODS_DIR_NAME = "mods"

# The Wine override Mewjector needs. ``n,b`` prefers the game-folder DLL but
# keeps Wine's builtin as a fallback, which is required: with ``version=n``
# alone the prefix's 32-bit helpers cannot load the 64-bit DLL and the game
# launch aborts with no log.
MEWJECTOR_DLL_OVERRIDE = "version=n,b"

# Where a DLL override can live. The planner recommends exactly one.
OVERRIDE_LOCATION_NONE = "none"
OVERRIDE_LOCATION_LAUNCH_OPTION = "steam launch option"
OVERRIDE_LOCATION_PROTON_PREFIX = "proton prefix DllOverrides"

# The game flag Mewtator needs for asset mods. Its presence is also what
# disables Steam achievements for the session.
MODPATHS_FLAG = "-modpaths"

__all__ = [
    "Achievements",
    "DllOverride",
    "FilePlacement",
    "InstallPlan",
    "LaunchOption",
    "OS_LINUX",
    "OS_NIXOS",
    "OS_WINDOWS",
    "OVERRIDE_LOCATION_LAUNCH_OPTION",
    "OVERRIDE_LOCATION_NONE",
    "OVERRIDE_LOCATION_PROTON_PREFIX",
    "UnknownInstallInput",
    "VARIANT_MEWTATOR",
    "VARIANT_OVERLAY_ONLY",
    "VARIANT_STANDALONE",
    "plan_install",
]

_TargetPath = Union[Type[PurePosixPath], Type[PureWindowsPath]]


class UnknownInstallInput(ValueError):
    """Raised when the OS, the variant, or a required path is not understood."""


@dataclass(frozen=True)
class FilePlacement:
    """One file (or folder) to place, and why it goes there."""

    source: str
    destination: str
    reason: str


@dataclass(frozen=True)
class DllOverride:
    """The Wine DLL override this install needs and where it belongs.

    ``value`` is the override string (for example ``version=n,b``) or ``None``
    when no override is needed. ``location`` is the one recommended mechanism;
    the plan never proposes two at once.
    """

    value: Optional[str]
    location: str
    reason: str


@dataclass(frozen=True)
class LaunchOption:
    """The Steam launch-option text to recommend, or ``None`` if none is needed."""

    text: Optional[str]
    reason: str


@dataclass(frozen=True)
class Achievements:
    """Whether Steam achievements stay on for this install, and why."""

    enabled: bool
    reason: str


@dataclass(frozen=True)
class InstallPlan:
    """The complete, reasoned description of an install.

    Later steps act on this instead of re-deciding: ``files`` is exactly what
    to copy, ``dll_override`` and ``launch_option`` are what to configure, and
    ``achievements`` records the Steam state the user should expect.
    """

    os_name: str
    variant: str
    game_dir: str
    files: Tuple[FilePlacement, ...]
    dll_override: DllOverride
    launch_option: LaunchOption
    achievements: Achievements
    proton_compatdata_dir: Optional[str] = None


def _require_known_os(os_name: str) -> str:
    """Return the canonical OS key, or refuse an unknown one."""
    key = str(os_name).strip().lower()
    if key not in _KNOWN_OSES:
        raise UnknownInstallInput(
            "unknown operating system %r; expected one of %s"
            % (os_name, ", ".join(sorted(_KNOWN_OSES)))
        )
    return key


def _require_known_variant(variant: str) -> str:
    """Return the canonical variant key, or refuse an unknown one."""
    key = str(variant).strip().lower()
    if key not in _KNOWN_VARIANTS:
        raise UnknownInstallInput(
            "unknown install variant %r; expected one of %s"
            % (variant, ", ".join(sorted(_KNOWN_VARIANTS)))
        )
    return key


def _path_type(os_key: str) -> _TargetPath:
    """Pick the path flavour of the *target* OS, not of the host running this."""
    return PureWindowsPath if os_key == OS_WINDOWS else PurePosixPath


def _overlay_placement(
    path_cls: _TargetPath, game_dir: str, overlay_binary: str
) -> FilePlacement:
    """The overlay binary itself, always the first thing an install places."""
    name = path_cls(overlay_binary).name
    return FilePlacement(
        source=str(overlay_binary),
        destination=str(path_cls(game_dir) / name),
        reason=("The overlay is the app being installed; it lives in the game "
                "folder so it sits beside the game and is easy to remove."),
    )


def _mod_placements(
    path_cls: _TargetPath,
    game_dir: str,
    mod_files: Iterable[str],
    variant: str,
) -> list[FilePlacement]:
    """Place the mod files, explaining each destination.

    Mewjector only works if its loader and ``chainloader.ini`` are next to the
    game executable, so those two are recognised by name wherever the variant
    is installed; every other mod file belongs in the game's ``mods`` folder.
    A placed mod file's reason names only the mechanism its variant uses:
    Mewjector for ``standalone``, ``-modpaths`` for ``mewtator``.
    """
    root = path_cls(game_dir)
    mods = root / MODS_DIR_NAME
    if variant == VARIANT_MEWTATOR:
        content_reason = ("Mod content lives in the game's mods folder, which "
                          "-modpaths points the game at.")
    else:
        content_reason = ("Mod content lives in the game's mods folder, which "
                          "Mewjector scans for mod DLLs.")
    placements = []
    for source in mod_files:
        name = path_cls(source).name
        lowered = name.lower()
        if lowered == MEWJECTOR_LOADER_NAME:
            destination = root / name
            reason = ("Mewjector shadows Windows' version.dll, so the loader "
                      "must sit beside the game executable.")
        elif lowered == MEWJECTOR_CONFIG_NAME:
            destination = root / name
            reason = ("Mewjector reads chainloader.ini from beside the "
                      "executable to find the folder of mod DLLs.")
        else:
            destination = mods / name
            reason = content_reason
        placements.append(FilePlacement(str(source), str(destination), reason))
    return placements


def _require_distinct_destinations(placements: list[FilePlacement]) -> None:
    """Refuse any two placements that would be written to one destination.

    This includes the overlay binary, so a mod file named like the overlay is
    refused instead of silently overwriting it. Destinations are compared
    case-insensitively: Windows folds file-name case, so ``Mod.dll`` and
    ``mod.dll`` collide there even though the strings differ.
    """
    seen: dict[str, str] = {}
    for placement in placements:
        key = placement.destination.lower()
        if key in seen:
            raise UnknownInstallInput(
                "files %s and %s would land at the same destination %s; "
                "case is ignored because Windows folds file names"
                % (seen[key], placement.source, placement.destination)
            )
        seen[key] = placement.source


def _dll_override(
    os_key: str, uses_loader: bool, proton_compatdata_dir: Optional[str]
) -> DllOverride:
    """Decide the Wine override and where it belongs, or that none is needed."""
    if not uses_loader:
        return DllOverride(
            value=None,
            location=OVERRIDE_LOCATION_NONE,
            reason=("No Mewjector loader is being installed, so Wine has "
                    "nothing to override."),
        )
    if os_key == OS_WINDOWS:
        return DllOverride(
            value=None,
            location=OVERRIDE_LOCATION_NONE,
            reason=("Native Windows loads version.dll from the game folder "
                    "directly, so no Wine override is needed."),
        )
    if proton_compatdata_dir:
        return DllOverride(
            value=MEWJECTOR_DLL_OVERRIDE,
            location=OVERRIDE_LOCATION_PROTON_PREFIX,
            reason=("Wine must prefer Mewjector's version.dll over its builtin; "
                    "the Proton prefix is known, so the installer can set the "
                    "override there once. The ,b fallback keeps the prefix's "
                    "32-bit helpers working."),
        )
    return DllOverride(
        value=MEWJECTOR_DLL_OVERRIDE,
        location=OVERRIDE_LOCATION_LAUNCH_OPTION,
        reason=("Wine must prefer Mewjector's version.dll over its builtin; "
                "carried by the Steam launch option because no Proton prefix "
                "was given. The ,b fallback keeps the prefix's 32-bit helpers "
                "working."),
    )


def _launch_option(
    os_key: str, variant: str, game_dir: str, dll_override: DllOverride
) -> LaunchOption:
    """Build the recommended Steam launch-option text, or say none is needed."""
    steam_override = dll_override.location == OVERRIDE_LOCATION_LAUNCH_OPTION
    needs_modpaths = variant == VARIANT_MEWTATOR
    if not steam_override and not needs_modpaths:
        return LaunchOption(
            text=None,
            reason=("No DLL override and no -modpaths flag are needed, so the "
                    "game launches with its default options."),
        )

    arguments = []
    if needs_modpaths:
        # The game is a Windows binary, so on Linux and NixOS this value is a
        # POSIX path handed to it under Proton. That has not been verified
        # against a real Mewtator run: the game may need the DOS form
        # (Z:\...) or a path relative to the game folder instead. Confirm
        # which before the mewtator installer ships.
        mods = _path_type(os_key)(game_dir) / MODS_DIR_NAME
        arguments.extend([MODPATHS_FLAG, '"%s"' % mods])

    if steam_override:
        # On Linux, Steam expands %command% to the game's command line: the
        # environment override goes before it and the game's own arguments
        # after it. Windows launch options are appended to the exe directly,
        # so there is no placeholder to use there.
        text = "WINEDLLOVERRIDES=%s %%command%%" % MEWJECTOR_DLL_OVERRIDE
        if arguments:
            text += " " + " ".join(arguments)
    else:
        text = " ".join(arguments)

    if needs_modpaths and steam_override:
        reason = ("-modpaths points the game at the mod folder, and the same "
                  "field carries the DLL override in one paste.")
    elif needs_modpaths:
        reason = ("-modpaths points the game at the mod folder; Mewtator needs "
                  "it to load asset mods.")
    else:
        reason = ("The launch option carries the DLL override, so the Proton "
                  "prefix does not have to be edited.")
    return LaunchOption(text=text, reason=reason)


def _achievements(variant: str) -> Achievements:
    """State whether Steam achievements stay on, and why."""
    if variant == VARIANT_MEWTATOR:
        return Achievements(
            enabled=False,
            reason=("Mewtator passes -modpaths, which the game reads to keep "
                    "Steam achievements off for the session."),
        )
    return Achievements(
        enabled=True,
        reason=("Nothing here passes -modpaths or enables the debug console, "
                "so the game keeps Steam achievements on."),
    )


def plan_install(
    os_name: str,
    variant: str,
    *,
    game_dir: str,
    overlay_binary: str,
    mod_files: Iterable[str] = (),
    proton_compatdata_dir: Optional[str] = None,
) -> InstallPlan:
    """Return the reasoned plan for one install, performing no action.

    ``os_name`` is ``linux``, ``nixos`` or ``windows``; ``variant`` is
    ``overlay-only``, ``standalone`` or ``mewtator``. ``game_dir`` is the
    Mewgenics install directory and ``overlay_binary`` the built overlay to
    place in it. ``mod_files`` are the companion mod's files and are required
    for the two mod variants: ``standalone`` wants the whole Mewjector loader
    set (``version.dll`` and ``chainloader.ini``) plus at least one mod DLL,
    while ``mewtator`` takes only asset mods. ``proton_compatdata_dir`` is the
    ``compatdata`` folder of the game's Proton prefix on Linux; when given and
    the variant uses the loader, the DLL override is planned for the prefix
    instead of a launch option, and the folder is recorded on the plan.

    Raises :class:`UnknownInstallInput` for an OS or variant that is not
    recognised, or for contradictory paths (mod files with ``overlay-only``,
    none with a mod variant, ``standalone`` without the full loader set,
    ``mewtator`` with loader files, or two files that would land at one
    destination).
    """
    os_key = _require_known_os(os_name)
    variant_key = _require_known_variant(variant)
    if not game_dir:
        raise UnknownInstallInput("game_dir is required")
    if not overlay_binary:
        raise UnknownInstallInput("overlay_binary is required")
    if isinstance(mod_files, (str, bytes, PathLike)):
        raise UnknownInstallInput(
            "mod_files must be an iterable of paths, not a single string or "
            "path"
        )
    try:
        sources = tuple(str(path) for path in mod_files)
    except TypeError as exc:
        raise UnknownInstallInput(
            "mod_files must be an iterable of paths, not %s"
            % type(mod_files).__name__
        ) from exc

    path_cls = _path_type(os_key)
    placements = [_overlay_placement(path_cls, game_dir, overlay_binary)]

    if variant_key == VARIANT_OVERLAY_ONLY:
        if sources:
            raise UnknownInstallInput(
                "overlay-only installs the overlay alone; pass no mod files "
                "or choose the standalone or mewtator variant"
            )
        uses_loader = False
    else:
        if not sources:
            raise UnknownInstallInput(
                "%s needs at least one mod file" % variant_key
            )
        names = [path_cls(source).name.lower() for source in sources]
        has_loader = MEWJECTOR_LOADER_NAME in names
        has_config = MEWJECTOR_CONFIG_NAME in names
        if variant_key == VARIANT_MEWTATOR:
            for source, name in zip(sources, names):
                if name in (MEWJECTOR_LOADER_NAME, MEWJECTOR_CONFIG_NAME):
                    raise UnknownInstallInput(
                        "mewtator is a separate mod-path mechanism and does "
                        "not use the Mewjector loader; remove %s from the mod "
                        "files and use the standalone variant for it" % source
                    )
        if variant_key == VARIANT_STANDALONE:
            missing = []
            if not has_loader:
                missing.append(MEWJECTOR_LOADER_NAME)
            if not has_config:
                missing.append(MEWJECTOR_CONFIG_NAME)
            if missing:
                raise UnknownInstallInput(
                    "standalone needs the whole Mewjector loader set beside "
                    "the game executable; missing %s among the mod files"
                    % ", ".join(missing)
                )
            has_mod_dll = any(
                name not in (MEWJECTOR_LOADER_NAME, MEWJECTOR_CONFIG_NAME)
                for name in names
            )
            if not has_mod_dll:
                raise UnknownInstallInput(
                    "standalone needs the Mewjector loader %s, its %s, and "
                    "at least one mod DLL; found only the loader files"
                    % (MEWJECTOR_LOADER_NAME, MEWJECTOR_CONFIG_NAME)
                )
        uses_loader = has_loader
        placements += _mod_placements(
            path_cls, game_dir, sources, variant_key
        )
        _require_distinct_destinations(placements)

    dll_override = _dll_override(os_key, uses_loader, proton_compatdata_dir)
    launch_option = _launch_option(os_key, variant_key, game_dir, dll_override)
    # The compatdata folder only matters where the loader needs a Wine
    # override: Linux and NixOS with a loader. Windows ignores the override
    # and overlay-only has no loader, so recording the path there would name
    # an action that will not happen.
    recorded_compatdata = (
        proton_compatdata_dir
        if uses_loader and os_key in (OS_LINUX, OS_NIXOS)
        else None
    )
    return InstallPlan(
        os_name=os_key,
        variant=variant_key,
        game_dir=str(game_dir),
        files=tuple(placements),
        dll_override=dll_override,
        launch_option=launch_option,
        achievements=_achievements(variant_key),
        proton_compatdata_dir=recorded_compatdata,
    )
