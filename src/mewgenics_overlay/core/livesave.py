"""Find the save file the running Mewgenics process has open.

Under Proton the game keeps its campaign save open for the whole session, so
the save being played is visible as one of the game process's file descriptors
(``/proc/<pid>/fd/*``). This module:

  * :func:`find_live_save` locates that descriptor and returns its canonical
    path (symlinks resolved, so it spells the file the same way as discovery),
  * :func:`resolve_save_path` maps a bare save file name, as sent by the
    in-game mod, back to a canonical path on disk,
  * :func:`game_process_running` reports whether the game process is present
    even when it holds no save open, and
  * :class:`SaveFollowPolicy` decides when the overlay should switch to the
    save the game is playing, and when the game has really gone.

All are Qt-free and never raise: a missing ``/proc``, an unreadable process or
a host without the game running simply yields ``None``. Nothing here writes to
disk or to the game.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Iterable, Optional, Union

from mewgenics_overlay.core import discovery

log = logging.getLogger("mewgenics_overlay.livesave")

#: Where the kernel exposes running processes. Injectable so tests can point at
#: a fake tree instead of the live system.
DEFAULT_PROC_ROOT = "/proc"
#: The game's executable name, matched case-insensitively against a process's
#: ``comm`` or the base name of its ``cmdline`` argv[0]. The *whole* name is
#: matched, never a loose substring, so this overlay's own command line - which
#: mentions Mewgenics in its arguments - is not mistaken for the game.
DEFAULT_GAME_EXE = "Mewgenics.exe"
#: Only descriptors whose target ends like this are considered saves.
DEFAULT_SAVE_SUFFIX = ".sav"
#: Consecutive process-absent scans before the game is judged offline. At the
#: caller's poll interval this is a short grace period, not an immediate quit.
DEFAULT_OFFLINE_GRACE_SCANS = 3
#: Characters that mean "this is a path, not a bare file name".
_PATH_SEPARATORS = ("/", "\\")
#: A drive specifier ("C:x.sav") is also not a bare file name.
_DRIVE_MARK = ":"
_TRAVERSAL = ".."
_NULL = "\x00"

#: A discovered-save record, as returned by ``discovery.find_all_saves``, or a
#: plain path string for callers that already have one.
SaveRecord = Union[dict, str]


def canonical_path(path: Optional[str]) -> Optional[str]:
    """The real path behind *path*, with symlinks resolved; None for empty.

    A save can be reached through a symlinked Steam library, so the descriptor
    target and a discovery record can spell the same file two ways. Resolving
    at every boundary lets the policy compare one save with one save. Never
    raises: a path that cannot be resolved is returned unchanged.
    """
    if not path:
        return None
    try:
        return os.path.realpath(path)
    except (OSError, ValueError) as exc:
        log.debug("livesave: cannot canonicalise %r: %s", path, exc)
        return path


def is_save_file_name(name: object, suffix: str = DEFAULT_SAVE_SUFFIX) -> bool:
    """True when *name* is a bare save file name the overlay will trust.

    The rule (shared with the bridge's ``save`` message validation, so it has
    one home): a non-empty plain string, no path separators, no drive specifier,
    no ``..``, no NUL, ending in *suffix* case-insensitively. Anything that is
    not a string is False.
    """
    if not isinstance(name, str) or not suffix:
        return False
    text = name.strip()
    if not text or text in (".", _TRAVERSAL):
        return False
    if _NULL in text or _DRIVE_MARK in text:
        return False
    if any(sep in text for sep in _PATH_SEPARATORS):
        return False
    return text.casefold().endswith(suffix.casefold())


def _read_text(path: Path) -> str:
    """Read a small proc file as text; "" when it vanished or is unreadable."""
    try:
        return path.read_bytes().decode("utf-8", errors="replace").strip()
    except OSError as exc:
        log.debug("livesave: cannot read %s: %s", path, exc)
        return ""


def _numeric_key(name: str) -> tuple[int, int]:
    """Sort pids/fd numbers numerically, with anything else last."""
    return (0, int(name)) if name.isdigit() else (1, 0)


def _process_matches(pid_dir: Path, game_exe: str) -> bool:
    """True when this process's executable name is *game_exe*.

    ``comm`` is the kernel's process name; the first ``cmdline`` argument
    covers the case where the name was truncated there. Only the base name of
    argv[0] is compared, never the rest of the command line, so an argument
    that merely mentions the game does not match.
    """
    comm = _read_text(pid_dir / "comm").casefold()
    if comm and comm == game_exe:
        return True
    cmdline = _read_text(pid_dir / "cmdline")
    if not cmdline:
        return False
    # cmdline is NUL-separated; a Windows argv[0] may use backslashes.
    argv0 = cmdline.split(_NULL, 1)[0]
    base = argv0.replace("\\", "/").rsplit("/", 1)[-1]
    return base.casefold() == game_exe


def _matching_processes(proc_root: str, game_exe: str) -> Iterable[Path]:
    """Yield the pid dirs whose executable is *game_exe* (own process aside).

    *game_exe* is already case-folded. The overlay's own pid is skipped: its
    command line mentions the game and it briefly holds a temporary copy of a
    save open, neither of which is the game.
    """
    root = Path(proc_root)
    try:
        entries = sorted(root.iterdir(), key=lambda p: _numeric_key(p.name))
    except OSError as exc:
        log.debug("livesave: cannot list %s: %s", root, exc)
        return
    own_pid = str(os.getpid())
    for pid_dir in entries:
        if not pid_dir.name.isdigit():
            continue
        if pid_dir.name == own_pid:
            continue
        if _process_matches(pid_dir, game_exe):
            yield pid_dir


def _is_under_temp(target: str) -> bool:
    """True when *target* is, or lives under, the system temp directory.

    Opening a save copies it to a temp file first (see
    ``core/watcher.safe_read_save``); that copy is not the game's save and must
    never be followed.
    """
    try:
        canon = os.path.realpath(target)
        root = os.path.realpath(tempfile.gettempdir())
    except (OSError, ValueError) as exc:
        log.debug("livesave: cannot compare %r with the temp dir: %s",
                  target, exc)
        return False
    # A temp root of "" or the filesystem root would swallow every path.
    if root in ("", os.sep):
        return False
    if canon == root:
        return True
    return canon.startswith(root.rstrip(os.sep) + os.sep)


def _save_descriptor(pid_dir: Path, suffix: str) -> Optional[str]:
    """Canonical path of this process's first non-temp descriptor ending *suffix*."""
    fd_dir = pid_dir / "fd"
    try:
        entries = sorted(fd_dir.iterdir(), key=lambda p: _numeric_key(p.name))
    except OSError as exc:
        log.debug("livesave: cannot scan %s: %s", fd_dir, exc)
        return None
    for entry in entries:
        try:
            target = os.readlink(entry)
        except OSError as exc:
            log.debug("livesave: cannot read link %s: %s", entry, exc)
            continue
        # A deleted file shows as "<path> (deleted)" and so never matches.
        # Match case-insensitively, as :func:`is_save_file_name` does, so an
        # upper-case save name on disk is still found.
        if not target.casefold().endswith(suffix.casefold()):
            continue
        if _is_under_temp(target):
            log.debug("livesave: ignoring temp save target %s", target)
            continue
        return canonical_path(target)
    return None


def find_live_save(
    proc_root: str = DEFAULT_PROC_ROOT,
    game_exe: str = DEFAULT_GAME_EXE,
    suffix: str = DEFAULT_SAVE_SUFFIX,
) -> Optional[str]:
    """Return the canonical save path the running game has open, or None.

    Only a process whose executable name is *game_exe* is examined, and only
    its descriptors pointing at a non-temp ``suffix`` file count. Walking every
    process measured about 8 ms with a few hundred processes, so callers should
    not run this on a UI thread; it is cheap per process and read errors are
    debug-logged and skipped, never fatal.

    *proc_root* is injectable so tests can use a fake tree.
    """
    exe = game_exe.casefold()
    if not exe or not suffix:
        return None
    for pid_dir in _matching_processes(proc_root, exe):
        target = _save_descriptor(pid_dir, suffix)
        if target:
            log.debug("livesave: live save from pid %s: %s", pid_dir.name, target)
            return target
    log.debug("livesave: no %s process holds a %s under %s",
              game_exe, suffix, proc_root)
    return None


def game_process_running(
    proc_root: str = DEFAULT_PROC_ROOT,
    game_exe: str = DEFAULT_GAME_EXE,
) -> bool:
    """True when a process named *game_exe* is running.

    Separate from :func:`find_live_save` because a running game with no save
    open (menus, between campaigns) still counts as present: the follow policy
    must not treat that as the game having quit.
    """
    exe = game_exe.casefold()
    if not exe:
        return False
    for _pid_dir in _matching_processes(proc_root, exe):
        return True
    return False


def detector_available(proc_root: str = DEFAULT_PROC_ROOT) -> bool:
    """True when this host exposes a process table the detector can scan.

    Linux has ``/proc``; Windows has no equivalent here, so there the detector
    is off and the bridge is the only signal for the game coming and going.
    """
    return Path(proc_root).is_dir()


def _is_safe_full_path(text: str) -> bool:
    """True for an absolute path that cannot traverse upwards."""
    if _NULL in text:
        return False
    path = Path(text)
    return path.is_absolute() and _TRAVERSAL not in path.parts


def _find_by_name(name: str, saves: Optional[Iterable[SaveRecord]]) -> Optional[str]:
    """First already-discovered save whose base name matches *name*."""
    if saves is None:
        try:
            saves = discovery.find_all_saves()
        except OSError as exc:
            log.warning("livesave: save discovery failed: %s", exc)
            return None
    wanted = name.casefold()
    for record in saves:
        path = record.get("path") if isinstance(record, dict) else record
        if not isinstance(path, str):
            log.debug("livesave: skipping malformed save record %r", record)
            continue
        if Path(path).name.casefold() == wanted:
            return path
    log.debug("livesave: no discovered save named %r", name)
    return None


def resolve_save_path(
    name: str,
    saves: Optional[Iterable[SaveRecord]] = None,
) -> Optional[str]:
    """Map a save name from the game to a canonical full path on disk, or None.

    *name* is either a bare file name, compared case-insensitively against the
    base name of every discovered save, or an absolute path to an existing
    file, which is returned as its real path. An absolute path must also pass
    the shared :func:`is_save_file_name` rule, so a path that is not a ``.sav``
    is refused. Anything else (empty, a relative path, a path with ``..``, a
    non-``.sav`` name) resolves to None, so a hostile or confused mod cannot
    point the overlay outside the save folders.

    *saves* defaults to :func:`mewgenics_overlay.core.discovery.find_all_saves`
    and may hold the dicts it returns or plain path strings. A matched record is
    trusted as-is: discovery only reports files that exist.
    """
    if not isinstance(name, str):
        return None
    text = name.strip()
    if not text:
        return None
    if is_save_file_name(text):
        return canonical_path(_find_by_name(text, saves))
    if _is_safe_full_path(text):
        path = Path(text)
        if not path.is_file() or not is_save_file_name(path.name):
            log.debug("livesave: refusing non-save path %r", text)
            return None
        return canonical_path(str(path))
    log.debug("livesave: refusing save name %r", text)
    return None


class SaveFollowPolicy:
    """Decide when the overlay switches to the save the game is playing.

    Qt-free and side-effect free: the callers feed it observations
    (:meth:`note_game_save`, :meth:`note_game_online`, :meth:`note_manual_open`,
    :meth:`note_game_process`, :meth:`note_game_offline`) and act on the path it
    returns; loading the save stays with the UI (``palette.open_save``).

    Rules, agreed with the user:

      * The overlay follows the game's save only while :attr:`enabled`.
      * A save the *user* opened is pinned while the game stays online, so an
        automatic follow never yanks the view away from a deliberate choice.
      * When the game itself switches campaign (the save it reports or holds
        open changes), the pin is lifted and the overlay follows the new
        campaign.
      * A mod disconnect alone is not proof the game quit: with a detector
        available only the process scan decides that (after
        :attr:`offline_grace_scans` consecutive absent scans, see
        :meth:`note_game_process`); where no detector exists the disconnect
        keeps its old meaning and clears the pin (:meth:`note_bridge_connection`).
    """

    def __init__(self, enabled: bool = True,
                 offline_grace_scans: int = DEFAULT_OFFLINE_GRACE_SCANS) -> None:
        self.enabled = bool(enabled)
        #: Consecutive process-absent scans before the game is judged offline.
        self.offline_grace_scans = max(1, int(offline_grace_scans))
        self._online = False
        self._game_save: Optional[str] = None
        self._pin: Optional[str] = None
        self._absent_scans = 0

    @property
    def online(self) -> bool:
        """True once the game has been seen, until :meth:`note_game_offline`."""
        return self._online

    @property
    def pinned(self) -> bool:
        """True while a user-chosen save overrides the game's own."""
        return self._pin is not None

    @property
    def pinned_path(self) -> Optional[str]:
        """The user-chosen save being kept, or None."""
        return self._pin

    @property
    def game_save(self) -> Optional[str]:
        """The save the game last reported, or was detected with, or None."""
        return self._game_save

    def note_game_save(self, path: Optional[str],
                       current: Optional[str]) -> Optional[str]:
        """The game is on *path*: update state and return a save to load.

        Marks the game online and records *path*. When the recorded save
        changed, the game switched campaign, so any pin is lifted. Returns
        *path* only when following is enabled, it differs from *current*, and
        no pin is active; otherwise None (nothing to do). ``None``/empty
        *path* is ignored so a failed detection cannot clear the state.

        *path* and *current* are expected in the same canonical form (see
        :func:`canonical_path`), so one file reached two ways is not read as a
        campaign switch.
        """
        if not path:
            return None
        self._online = True
        self._absent_scans = 0
        switched = self._game_save is not None and self._game_save != path
        self._game_save = path
        if switched:
            self._pin = None
        if not self.enabled or self._pin is not None:
            return None
        if current is not None and path == current:
            return None
        return path

    def note_game_online(self) -> None:
        """The game is online, before any save is known.

        The mod connecting is enough to know the game is open, so a manual
        open that happens before the first save is detected or reported still
        pins. The last recorded game save is left untouched: if one is already
        known, this only re-asserts the online flag.
        """
        self._online = True
        self._absent_scans = 0

    def note_bridge_connection(self, online: bool,
                               detector_available: bool) -> None:
        """The mod connected or left; *detector_available* says which rule wins.

        Connecting always marks the game online. A disconnect only drops the
        online flag and any pin where no process detector exists to confirm
        the game has really gone; where it does exist the scan is the
        authority, so a transient mod hiccup does not lift the user's pin.
        """
        if online:
            self.note_game_online()
        elif not detector_available:
            self.note_game_offline()

    def note_manual_open(self, path: Optional[str]) -> None:
        """The *user* opened *path*: pin it, but only while the game is online.

        A user who loads another save while the game is open keeps it; while
        the game is offline there is nothing to hold it against, so no pin is
        recorded.
        """
        if self._online and path:
            self._pin = path

    def note_game_process(self, present: bool) -> bool:
        """Feed one process-detector scan; True when it marked the game offline.

        The detector is a weak signal: a scan racing a game restart, or a
        transient read failure, must not lift the user's pin. A *present*
        process therefore clears the absent-run counter and re-asserts online,
        and only :attr:`offline_grace_scans` consecutive absent scans drop the
        online flag and the pin. The bridge is deliberately not consulted here:
        a mod disconnect on its own is not the game quitting.
        """
        if present:
            self._absent_scans = 0
            self._online = True
            return False
        self._absent_scans += 1
        if self._absent_scans < self.offline_grace_scans:
            return False
        was_online = self._online or self._pin is not None
        self.note_game_offline()
        return was_online

    def note_game_offline(self) -> None:
        """The game went away: drop the online flag and any pin."""
        self._online = False
        self._pin = None
        self._absent_scans = 0
