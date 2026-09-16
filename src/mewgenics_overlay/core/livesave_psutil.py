"""Windows live-save backend: read the game's open files through psutil.

Windows has no ``/proc``, so :mod:`mewgenics_overlay.core.livesave` cannot see
the running game's file descriptors there. This module is that other half of
the detector, built on psutil's process API. psutil is an optional dependency:
without it :func:`resolve` reports the detector unavailable (and logs once)
instead of raising.

A process the scan cannot read does not fail silently: a matched game process
whose open files cannot be read is logged at WARNING once per process, so an
elevated game (which denies its handles to a non-elevated overlay) shows up at
the default log level instead of only at debug. A process whose *name* cannot
be read is a different matter: psutil's Windows ``name()`` is the basename of
``exe()``, so that failure never identifies the game (a matched process has
already been named) and can only be an unrelated protected system process or
one that exited mid-scan; it stays at debug so it cannot consume the one
warning that names the game.

``open_files()`` enumerates every handle the game process holds, which is far
more expensive than reading one ``/proc`` entry, so callers must run a scan on
the background worker (``ui/app.py`` ``LiveSaveScanner``), never the UI thread.

Everything here is reached only through ``livesave``'s public functions, so the
callers and the follow policy never branch on the platform, and the save
selection rule is passed in (``select``) so both platforms share it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Iterable, Optional

log = logging.getLogger("mewgenics_overlay.livesave_psutil")

#: Sentinel meaning "the caller did not inject a psutil module", so an explicit
#: ``None`` can mean "pretend the library is not installed".
PSUTIL_UNSET = object()

#: The candidate selector shared with the ``/proc`` path: it filters by suffix
#: and temp location and canonicalises. Passed in rather than imported so this
#: module does not reach into ``livesave``'s private helpers.
SelectSave = Callable[[Iterable[str], str], Optional[str]]

_PSUTIL_MODULE: Optional[object] = None
_PSUTIL_TRIED = False
_psutil_warned = False
_blind_warned = False


def _warn_blind(message: str, *args: object) -> None:
    """Warn once that a matched game process could not be read.

    The scan reruns every few seconds, so a persistent failure (an elevated
    game whose handles psutil cannot open) must not flood the log. At the
    default INFO level the first occurrence still has to be visible, or the
    detector stays silently blind to the game it exists to find.
    """
    global _blind_warned
    if _blind_warned:
        return
    _blind_warned = True
    log.warning(message, *args)


def import_psutil() -> Optional[object]:
    """Import psutil on demand; None when it is not installed.

    The result (including the failure) is cached, so the import is attempted
    once per process. Importing lazily keeps the Linux path from paying for a
    library it never uses.
    """
    global _PSUTIL_MODULE, _PSUTIL_TRIED
    if not _PSUTIL_TRIED:
        _PSUTIL_TRIED = True
        try:
            import psutil
        except ImportError:
            _PSUTIL_MODULE = None
        else:
            _PSUTIL_MODULE = psutil
    return _PSUTIL_MODULE


def resolve(psutil_module: object) -> Optional[object]:
    """The psutil module to use for one call, or None when unavailable.

    The sentinel means "use the optional import"; an injected module (or an
    explicit ``None``) overrides it, which is how this backend is tested without
    the library and without a real process table. A missing library is logged
    once, not once per scan.
    """
    global _psutil_warned
    module = import_psutil() if psutil_module is PSUTIL_UNSET else psutil_module
    if module is None and not _psutil_warned:
        _psutil_warned = True
        log.warning("livesave: psutil is not installed; the Windows live-save "
                    "detector is unavailable (the bridge still reports the "
                    "save the game plays)")
    return module


def use_backend(is_windows: bool, proc_root: str, psutil_module: object) -> bool:
    """True when a call must go through psutil instead of ``/proc``.

    An injected module always forces it, so the backend is testable on Linux
    without a fake process tree; otherwise it is chosen only where no process
    table exists (Windows), leaving the Linux path exactly as it was.
    """
    if psutil_module is not PSUTIL_UNSET:
        return True
    return is_windows and not Path(proc_root).is_dir()


def available(is_windows: bool, proc_root: str, psutil_module: object) -> bool:
    """True when this host exposes a process table the detector can scan.

    ``/proc`` counts wherever it exists; otherwise (Windows) the detector is
    available only when psutil is installed.
    """
    if Path(proc_root).is_dir():
        return True
    if psutil_module is PSUTIL_UNSET and not is_windows:
        return False
    return resolve(psutil_module) is not None


def _game_processes(psutil_module: object, game_exe: str) -> Iterable[object]:
    """Yield psutil processes whose executable name is *game_exe*.

    Mirrors the ``/proc`` matcher: the *whole* process name is compared
    case-insensitively, never the command line, so the overlay's own arguments
    that mention the game do not match; the overlay's own pid is skipped.
    """
    error = getattr(psutil_module, "Error", OSError)
    own_pid = os.getpid()
    try:
        for proc in psutil_module.process_iter():
            if getattr(proc, "pid", None) == own_pid:
                continue
            try:
                name = proc.name()
            except (error, OSError) as exc:
                # A name that cannot be read means an unrelated protected
                # process (psutil's name() is the basename of exe(), so the
                # failure is the same one) or one that exited mid-scan: either
                # way it is not the game we could have matched, so the skip
                # stays at debug. Only the matched game's failures are warning
                # material (see find_live_save).
                log.debug("livesave: cannot name psutil pid %s: %s",
                          getattr(proc, "pid", "?"), exc)
                continue
            if isinstance(name, str) and name.casefold() == game_exe:
                yield proc
    except (error, OSError) as exc:
        log.warning("livesave: psutil process scan failed: %s", exc)


def find_live_save(psutil_module: object, game_exe: str, suffix: str,
                   select: SelectSave) -> Optional[str]:
    """First non-temp save the running game holds open, found via psutil.

    ``open_files()`` enumerates every handle the process owns, so this belongs
    on the background scan thread. Never raises: a vanished process or a denied
    handle is skipped. The process *is* the game at this point, so a read
    failure is warned about once instead of only being debug-logged: a game
    running elevated reads as no game at all, which is exactly the case that
    must not be invisible. *select* is the shared candidate rule (suffix match,
    temp exclusion, canonicalisation) from the ``/proc`` path.
    """
    error = getattr(psutil_module, "Error", OSError)
    for proc in _game_processes(psutil_module, game_exe.casefold()):
        try:
            open_files = proc.open_files()
        except (error, OSError) as exc:
            _warn_blind(
                "livesave: cannot read the open files of the game process "
                "(psutil pid %s), so the live-save detector is blind to it "
                "(an elevated game denies its handles): %s",
                getattr(proc, "pid", "?"), exc)
            log.debug("livesave: cannot read open files of psutil pid %s: %s",
                      getattr(proc, "pid", "?"), exc)
            continue
        # Sort the handles so the choice among several saves is deterministic,
        # as the /proc backend's numeric fd order is.
        paths = sorted(f.path for f in open_files
                       if isinstance(getattr(f, "path", None), str))
        target = select(paths, suffix)
        if target:
            log.debug("livesave: live save from psutil pid %s: %s",
                      getattr(proc, "pid", "?"), target)
            return target
    log.debug("livesave: no %s process holds a %s (psutil)", game_exe, suffix)
    return None


def game_running(psutil_module: object, game_exe: str) -> bool:
    """True when a process named *game_exe* is running, found via psutil."""
    for _proc in _game_processes(psutil_module, game_exe.casefold()):
        return True
    return False
