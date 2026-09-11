"""Hyprland-only best-effort window raise.

Qt's ``show()`` / ``raise_()`` / ``activateWindow()`` are only *requests*:
on Hyprland a compositor policy or a stray tiling rule can keep the overlay
behind the game. This module asks the compositor directly, using the same
``hyprctl`` the desktop-shortcut backend already relies on.

The module is deliberately **Qt-free** (stdlib only) so the logic is
unit-testable without a display or PySide6, and it never raises: a missing,
hanging or failing ``hyprctl`` must not take the overlay down with it. A whole
:func:`focus_window` call is bounded by :data:`FOCUS_BUDGET_S` (each
subprocess additionally by :data:`HYPRCTL_TIMEOUT_S`), and every failure is
logged before ``focus_window`` returns ``False``.

The subprocess runner, the ``which`` lookup and the ``sleep`` used for the
first-summon retry are injectable so tests can drive the whole flow without
Hyprland installed or real waiting.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Callable, Mapping, Optional, Sequence

log = logging.getLogger("mewgenics_overlay.ui.raisewindow")

# Hyprland exports this on every session; its presence is the session test.
HYPRLAND_ENV = "HYPRLAND_INSTANCE_SIGNATURE"

HYPRCTL = "hyprctl"

# Short on purpose: a slow or wedged compositor must not stall the UI thread.
HYPRCTL_TIMEOUT_S = 2.0

# Total wall-clock budget for one ``focus_window`` call. Each subprocess is
# capped at the smaller of HYPRCTL_TIMEOUT_S and the time left, so the three
# sequential calls cannot add up to 3x the per-call timeout and freeze the UI.
FOCUS_BUDGET_S = 1.5

# One short beat for the first-summon race: ``show()`` maps the window
# asynchronously, so the first client lookup can run before Hyprland has
# registered the window. Retry once after this delay, still inside the total
# budget.
FIRST_LOOKUP_RETRY_DELAY_S = 0.15

# Application name as it appears in a Hyprland client's ``class``/``title``.
# Used to prefer the overlay's own toplevel over another window (e.g. About)
# owned by the same process.
APP_NAME = "mewgenics-overlay"

# Folded app names that count as naming the overlay. A WM_CLASS need not match
# the app id and a title is free-form text, so both shapes are accepted:
# "mewgenics-overlay" / "MewgenicsOverlay" fold to "mewgenicsoverlay" and the
# window title "Mewgenics Breeding Overlay" folds to
# "mewgenicsbreedingoverlay".
OVERLAY_NAME_TOKENS = ("mewgenicsbreedingoverlay", "mewgenicsoverlay")

# Characters a window manager may spell an app name with; dropped when
# folding so separator/casing variants all compare equal.
NAME_SEPARATORS = (" ", "-", "_")

FOCUS_COMMAND = "focuswindow"        # hyprctl dispatch focuswindow address:<a>
RAISE_COMMAND = "bringactivetotop"   # hyprctl dispatch bringactivetotop
ADDRESS_PREFIX = "address:"

# Injectable subprocess runner: ``argv -> (returncode, stdout)``. The default
# implementation never raises, so callers have one failure shape to handle.
Runner = Callable[[Sequence[str]], tuple[int, str]]


def is_hyprland(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the current session is Hyprland.

    Keyed off ``HYPRLAND_INSTANCE_SIGNATURE`` (Hyprland sets no useful
    ``XDG_CURRENT_DESKTOP``). *env* is injectable for tests; ``None`` reads
    the real process environment.
    """
    environ = os.environ if env is None else env
    return bool(environ.get(HYPRLAND_ENV))


def focus_window(
    pid: Optional[int] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    runner: Optional[Runner] = None,
    env: Optional[Mapping[str, str]] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> bool:
    """Ask Hyprland to focus and raise the window owned by *pid*.

    Returns ``True`` only when both dispatch commands succeed. On a
    non-Hyprland session this is a quiet ``False`` (the Qt calls already did
    what they could); every other miss is logged and returns ``False``.

    The whole call is bounded by :data:`FOCUS_BUDGET_S`, not just each
    ``hyprctl`` invocation, so a wedged compositor cannot stall the UI thread
    for the sum of the per-call timeouts. When the first lookup misses (the
    first-summon race), it waits :data:`FIRST_LOOKUP_RETRY_DELAY_S` and looks
    once more, budget permitting.

    ``pid`` defaults to the current process, ``which`` to :func:`shutil.which`
    and ``runner`` to a timeout-bounded :func:`subprocess.run` wrapper.
    ``sleep`` defaults to the stdlib :func:`time.sleep` and is injectable so
    tests need not really wait.
    """
    if not is_hyprland(env):
        return False

    which = which or shutil.which
    sleep = sleep or time.sleep
    deadline = time.monotonic() + FOCUS_BUDGET_S
    if runner is None:
        run = _guarded(_budgeted_runner(deadline))
    else:
        run = _guarded(runner)

    hyprctl = which(HYPRCTL)
    if not hyprctl:
        log.warning("cannot raise overlay: %s is not on PATH", HYPRCTL)
        return False

    target = os.getpid() if pid is None else pid
    address = _lookup(run, hyprctl, target, deadline)
    if address is None and _wait_to_retry(sleep, deadline):
        address = _lookup(run, hyprctl, target, deadline)
    if address is None:
        log.warning("cannot raise overlay: no Hyprland client for pid %s",
                    target)
        return False

    if not _dispatch(run, hyprctl, [FOCUS_COMMAND,
                                    f"{ADDRESS_PREFIX}{address}"], deadline):
        return False
    if not _dispatch(run, hyprctl, [RAISE_COMMAND], deadline):
        return False
    log.debug("raised Hyprland client %s for pid %s", address, target)
    return True


def _budgeted_runner(deadline: float) -> Runner:
    """The default runner bound to one call's *deadline*.

    Each subprocess gets the smaller of :data:`HYPRCTL_TIMEOUT_S` and the
    time left, so the per-call cap still applies while the total cannot run
    past the deadline. A call with no time left is logged and fails without
    spawning anything.
    """
    def run(argv: Sequence[str]) -> tuple[int, str]:
        timeout = min(HYPRCTL_TIMEOUT_S, deadline - time.monotonic())
        if timeout <= 0:
            log.warning("cannot raise overlay: Hyprland focus budget of "
                        "%.1fs is spent", FOCUS_BUDGET_S)
            return 1, ""
        return _run(argv, timeout)
    return run


def _run(argv: Sequence[str],
         timeout: float = HYPRCTL_TIMEOUT_S) -> tuple[int, str]:
    """Default runner: run *argv* with *timeout*; never raises.

    A missing binary, timeout or any other OS error becomes a failed result
    so the caller logs it once instead of the UI thread seeing an exception.
    The concrete reason is logged here (the exception type and message, or
    the command's stderr on a non-zero exit) because the caller only sees the
    exit code and would otherwise report a bare "exit 1".
    """
    cmd = [str(arg) for arg in argv]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("Hyprland command %s timed out after %.2fs", cmd[0],
                    timeout)
        return 1, ""
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Hyprland command %s failed: %s: %s", cmd[0],
                    type(exc).__name__, exc)
        return 1, ""
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        log.warning("Hyprland command %s exited %s: %s", cmd[0],
                    proc.returncode, stderr or "no stderr")
    return proc.returncode, proc.stdout or ""


def _guarded(runner: Runner) -> Runner:
    """Wrap *runner* so a raised exception becomes the failure result.

    The default runner already converts OS and subprocess errors, but an
    injected runner may raise: this module promises never to raise, so the
    exception is logged and turned into a failed result instead of unwinding
    out of ``WindowController.engage()``.
    """
    def run(argv: Sequence[str]) -> tuple[int, str]:
        try:
            return runner(argv)
        except Exception as exc:  # any failure here must stay contained
            log.warning("Hyprland command %s raised: %s",
                        argv[0] if argv else "?", exc)
            return 1, ""
    return run


def _lookup(runner: Runner, hyprctl: str, pid: int,
            deadline: float) -> Optional[str]:
    """One client-list fetch and pid lookup, or ``None`` when either misses."""
    clients = _clients(runner, hyprctl, deadline)
    if clients is None:
        return None
    return _address_for_pid(clients, pid)


def _wait_to_retry(sleep: Callable[[float], None],
                   deadline: float) -> bool:
    """Wait one beat for a just-mapped window, if the budget allows it.

    Returns ``False`` (with a log) when too little of the total budget is
    left for the wait, so the caller stops rather than sleeping past the
    deadline.
    """
    remaining = deadline - time.monotonic()
    if remaining <= FIRST_LOOKUP_RETRY_DELAY_S:
        log.warning("cannot raise overlay: no Hyprland client yet and only "
                    "%.2fs of the %.1fs budget left", max(remaining, 0.0),
                    FOCUS_BUDGET_S)
        return False
    sleep(FIRST_LOOKUP_RETRY_DELAY_S)
    return True


def _invoke(runner: Runner, argv: Sequence[str],
            deadline: Optional[float]) -> Optional[tuple[int, str]]:
    """Run *argv* when the focus budget covers it, else log and return None."""
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.warning("cannot raise overlay: Hyprland focus budget of "
                        "%.1fs is spent before %s", FOCUS_BUDGET_S,
                        argv[0] if argv else "?")
            return None
    return runner(argv)


def _clients(runner: Runner, hyprctl: str,
             deadline: Optional[float] = None) -> Optional[list]:
    """The ``hyprctl clients -j`` window list, or ``None`` with a log line."""
    result = _invoke(runner, [hyprctl, "clients", "-j"], deadline)
    if result is None:
        return None
    code, out = result
    if code != 0:
        log.warning("%s clients -j failed (exit %s)", HYPRCTL, code)
        return None
    try:
        clients = json.loads(out)
    except (TypeError, ValueError) as exc:
        log.warning("could not parse %s clients JSON: %s", HYPRCTL, exc)
        return None
    if not isinstance(clients, list):
        log.warning("unexpected %s clients payload: %s", HYPRCTL,
                    type(clients).__name__)
        return None
    return clients


def _normalise_name(value: str) -> str:
    """Fold a client ``class``/``title`` for name comparison.

    Lowercased with spaces, hyphens and underscores removed, so
    ``mewgenics-overlay``, ``MewgenicsOverlay`` and ``Mewgenics Breeding
    Overlay`` all reduce to a comparable token.
    """
    normalised = value.lower()
    for separator in NAME_SEPARATORS:
        normalised = normalised.replace(separator, "")
    return normalised


def _names_overlay(client: Mapping[str, object]) -> bool:
    """Whether the client's ``class`` or ``title`` names the overlay app.

    Tolerant of the separator and casing a toolkit chose: Hyprland reports
    whatever WM_CLASS/title was set, which need not match
    :data:`APP_NAME`. The value is folded by :func:`_normalise_name` and
    matched against :data:`OVERLAY_NAME_TOKENS`.
    """
    for field in ("class", "title"):
        value = client.get(field)
        if not isinstance(value, str):
            continue
        name = _normalise_name(value)
        if any(token in name for token in OVERLAY_NAME_TOKENS):
            return True
    return False


def _address_for_pid(clients: Sequence[object], pid: int) -> Optional[str]:
    """The ``address`` of the best usable client whose ``pid`` is *pid*.

    A process can own several toplevels (the palette and, say, an About
    dialog) and ``hyprctl clients -j`` order is unspecified, so among the
    pid matches a client whose class or title names the overlay wins;
    the first usable pid match is only the fallback when none is named.

    A pid match with a missing or unusable ``address`` is skipped and the
    scan continues: another window of the same process may carry a usable
    one, so only a fully checked list means "no client for this pid".
    """
    fallback: Optional[str] = None
    for client in clients:
        if not isinstance(client, dict) or client.get("pid") != pid:
            continue
        address = client.get("address")
        if not isinstance(address, str) or not address:
            log.warning("Hyprland client for pid %s has no usable address; "
                        "checking the remaining clients", pid)
            continue
        if _names_overlay(client):
            return address
        if fallback is None:
            fallback = address
    return fallback


def _dispatch(runner: Runner, hyprctl: str, args: Sequence[str],
              deadline: Optional[float] = None) -> bool:
    """Run one ``hyprctl dispatch`` command; log and return ``False`` on exit."""
    argv = [hyprctl, "dispatch", *args]
    result = _invoke(runner, argv, deadline)
    if result is None:
        return False
    code, _out = result
    if code != 0:
        log.warning("%s dispatch %s failed (exit %s)", HYPRCTL,
                    args[0] if args else "?", code)
        return False
    return True
