"""Apply an install plan: place the files, set the Wine override, report it.

This is the acting half of the one-click installers, the counterpart to
:mod:`install.plan`. It takes an :class:`~install.plan.InstallPlan` and carries
it out:

* every planned file is copied to its destination, with parent folders made as
  needed; the copy is staged in a temporary file beside the destination and
  swapped into place with an atomic replace, so a failed copy never leaves a
  partial or missing file. A destination that already matches is left alone;
  one that differs is moved to a timestamped backup beside it first, and that
  backup is rolled back if the swap fails;
* when the plan puts the DLL override in a Proton prefix, that prefix's
  ``pfx/user.reg`` is updated (after a backup) so Wine prefers the game-folder
  ``version.dll``; when the plan chose the Steam launch option instead, the
  option is printed and no registry file is touched;
* the achievements state and reason are printed as one explicit line.

Nothing is ever deleted, so a run is safe to repeat. Where a process table is
available (Linux and NixOS), the module runs the game-process check from
:mod:`mewgenics_overlay.core.livesave` first and refuses to touch anything while
Mewgenics is running; that check is not available on Windows, so the run
proceeds there. It returns a report the caller can print, with one status per
item: did it, already done, could not, or (in a dry run) would do. A dry run
reports the same items and changes nothing, the registry included.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from mewgenics_overlay.core.livesave import game_process_running

from install.plan import (
    MEWJECTOR_DLL_OVERRIDE,
    OS_LINUX,
    OS_NIXOS,
    OS_WINDOWS,
    OVERRIDE_LOCATION_PROTON_PREFIX,
    VARIANT_MEWTATOR,
    VARIANT_OVERLAY_ONLY,
    VARIANT_STANDALONE,
    FilePlacement,
    InstallPlan,
    UnknownInstallInput,
    plan_install,
)

log = logging.getLogger("install.apply")

#: The statuses a report item can carry. "would do" is a dry run.
STATUS_DONE = "did it"
STATUS_ALREADY = "already done"
STATUS_COULD_NOT = "could not"
STATUS_DRY_RUN = "would do"

#: The tag printed for each status in :func:`render_report`.
_STATUS_TAG = {
    STATUS_DONE: "did it",
    STATUS_ALREADY: "already done",
    STATUS_COULD_NOT: "could not",
    STATUS_DRY_RUN: "would do",
}

#: Read files in this many bytes at a time when hashing for equality.
_HASH_CHUNK = 1024 * 1024

#: NixOS ships this marker file; its presence picks the ``nixos`` target.
_NIXOS_MARKER = Path("/etc/NIXOS")

#: Where the Wine override lives inside a Proton compatdata folder. The plan's
#: ``version=n,b`` is the shorthand for the spelled-out registry value.
_REGISTRY_FILE = ("pfx", "user.reg")
_DLL_OVERRIDE_SECTION = r"Software\Wine\DllOverrides"
_DLL_OVERRIDE_PACKAGE = MEWJECTOR_DLL_OVERRIDE.partition("=")[0]
_DLL_OVERRIDE_VALUE = "native,builtin"


@dataclass(frozen=True)
class ActionReport:
    """One thing the installer did, skipped, or could not do.

    ``status`` is one of the ``STATUS_*`` values, ``reason`` is the plan's own
    explanation for the item, and ``detail`` says what actually happened.
    """

    item: str
    status: str
    reason: str
    detail: str


@dataclass(frozen=True)
class ApplyReport:
    """Everything one :func:`apply_install` run did (or would do).

    ``refused`` is set when the run was stopped before touching anything (the
    game is running, or a source file is missing). ``ok`` is False for a refusal
    or any item that could not be done.
    """

    dry_run: bool
    actions: Tuple[ActionReport, ...]
    achievements_line: str
    launch_option: Optional[str]
    refused: Optional[str] = None

    @property
    def failed(self) -> bool:
        """True when the run was refused or an item could not be done."""
        return self.refused is not None or any(
            action.status == STATUS_COULD_NOT for action in self.actions
        )

    @property
    def ok(self) -> bool:
        """True when every item was done or already in place."""
        return not self.failed


def _host_os() -> str:
    """The target OS of the machine this runs on (Linux and NixOS included)."""
    if os.name == "nt":
        return OS_WINDOWS
    if _NIXOS_MARKER.exists():
        return OS_NIXOS
    return OS_LINUX


def _report(item: str, status: str, reason: str, detail: str) -> ActionReport:
    """Build one report item and log it with the plan's reason."""
    log.info("[%s] %s | %s | %s", status, item, reason, detail)
    return ActionReport(item=item, status=status, reason=reason, detail=detail)


def _digest(path: Path) -> bytes:
    """The SHA-256 digest of *path*, streamed so large binaries are fine."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.digest()


def _same_content(source: Path, destination: Path) -> bool:
    """True when both files have the same size and content hash."""
    try:
        if source.stat().st_size != destination.stat().st_size:
            return False
        return _digest(source) == _digest(destination)
    except OSError as exc:
        log.debug("apply: cannot compare %s and %s: %s", source, destination, exc)
        return False


def _backup_path(path: Path) -> Path:
    """A free ``<name>.<timestamp>.bak`` path beside *path*.

    The timestamp is to the second; a second clash in the same second (a very
    fast double run) gets a numeric suffix so an existing backup is never
    overwritten.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name("%s.%s.bak" % (path.name, stamp))
    counter = 1
    while candidate.exists():
        candidate = path.with_name("%s.%s-%d.bak" % (path.name, stamp, counter))
        counter += 1
    return candidate


def _stage_copy(source: Path, destination: Path) -> Path:
    """Copy *source* into a fresh temp file in *destination*'s own folder.

    Staging beside the destination keeps the later swap a same-filesystem
    :func:`os.replace`, which is atomic. Nothing at *destination* is touched;
    the caller either swaps the staged file in or discards it.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        prefix=destination.name + ".", dir=str(destination.parent)
    )
    os.close(handle_fd)
    try:
        shutil.copy2(source, temp_name)
    except OSError:
        _discard_staged(Path(temp_name))
        raise
    return Path(temp_name)


def _discard_staged(staged: Path) -> None:
    """Remove a staged temp file, logging (not raising) a cleanup failure."""
    try:
        os.unlink(staged)
    except OSError as exc:
        log.debug("apply: could not remove temp file %s: %s", staged, exc)


def _swap_staged_in(staged: Path, destination: Path) -> None:
    """Atomically swap a staged file into *destination*; discard on failure."""
    try:
        os.replace(staged, destination)
    except OSError:
        _discard_staged(staged)
        raise


def _restore_backup(backup: Path, destination: Path) -> bool:
    """Move *backup* back onto *destination*; True when it succeeded."""
    try:
        os.replace(backup, destination)
        return True
    except OSError as exc:
        log.error("apply: cannot restore %s from %s: %s",
                  destination, backup, exc)
        return False


def _apply_placement(placement: FilePlacement, dry_run: bool) -> ActionReport:
    """Place one planned file atomically, backing up a differing destination.

    The new content is always staged in a temp file beside the destination and
    swapped in with :func:`os.replace`, so a failed copy never leaves a partial
    or missing destination. A differing destination is moved to a timestamped
    backup first and put back if the swap fails.
    """
    source = Path(placement.source)
    destination = Path(placement.destination)
    item = "Place %s at %s" % (source, destination)
    reason = placement.reason
    if not source.is_file():
        return _report(item, STATUS_COULD_NOT, reason, "source file is missing")
    if destination.exists():
        if destination.is_file() and _same_content(source, destination):
            return _report(item, STATUS_ALREADY, reason,
                           "already up to date; left untouched")
        if not destination.is_file():
            return _report(item, STATUS_COULD_NOT, reason,
                           "destination exists and is not a regular file")
        backup = _backup_path(destination)
        if dry_run:
            return _report(item, STATUS_DRY_RUN, reason,
                           "would move the existing file to %s and copy the "
                           "new one" % backup)
        try:
            staged = _stage_copy(source, destination)
        except OSError as exc:
            return _report(item, STATUS_COULD_NOT, reason,
                           "copy failed while staging: %s; the existing file "
                           "is untouched" % exc)
        try:
            os.replace(destination, backup)
        except OSError as exc:
            _discard_staged(staged)
            return _report(item, STATUS_COULD_NOT, reason,
                           "could not move the existing file to %s: %s; the "
                           "existing file is untouched" % (backup, exc))
        try:
            _swap_staged_in(staged, destination)
        except OSError as exc:
            if _restore_backup(backup, destination):
                return _report(item, STATUS_COULD_NOT, reason,
                               "copy failed: %s; restored the previous file "
                               "from %s" % (exc, backup))
            return _report(item, STATUS_COULD_NOT, reason,
                           "copy failed: %s; the previous file could not be "
                           "restored and remains at %s" % (exc, backup))
        return _report(item, STATUS_DONE, reason,
                       "moved the previous file to %s and copied the new one"
                       % backup)
    if dry_run:
        return _report(item, STATUS_DRY_RUN, reason,
                       "would create %s and copy the file" % destination)
    try:
        staged = _stage_copy(source, destination)
        _swap_staged_in(staged, destination)
    except OSError as exc:
        return _report(item, STATUS_COULD_NOT, reason, "copy failed: %s" % exc)
    return _report(item, STATUS_DONE, reason, "copied")


def _section_name(line: str) -> Optional[str]:
    """The registry key a ``[Key]`` line names, unescaped, or None."""
    text = line.strip()
    if not text.startswith("["):
        return None
    end = text.find("]")
    if end < 0:
        return None
    # Wine writes backslashes doubled in a section name; anything after the
    # closing bracket is the key's last-modified timestamp.
    return text[1:end].replace("\\\\", "\\")


def _split_value_line(line: str) -> Optional[Tuple[str, str]]:
    """Split a ``"name"="data"`` registry line into (name, data), or None."""
    text = line.strip()
    if not text.startswith('"'):
        return None
    end = text.find('"', 1)
    if end < 0:
        return None
    name = text[1:end]
    rest = text[end + 1:].lstrip()
    if not rest.startswith("="):
        return None
    data = rest[1:].strip()
    if len(data) >= 2 and data.startswith('"') and data.endswith('"'):
        data = data[1:-1]
    return name, data


def _set_override_in_text(
    text: str, section: str, package: str, value: str
) -> Tuple[str, bool]:
    """Return *(new_text, changed)* with *package* set to *value* in *section*.

    ``changed`` is False when the file already said exactly that, so the caller
    can leave the registry untouched.
    """
    lines = text.split("\n")
    header = None
    for index, line in enumerate(lines):
        if _section_name(line) == section:
            header = index
            break
    if header is None:
        if text and not text.endswith("\n"):
            lines.append("")
        escaped = section.replace("\\", "\\\\")
        lines.append("[%s] %d" % (escaped, int(time.time())))
        lines.append('"%s"="%s"' % (package, value))
        return "\n".join(lines), True
    for index in range(header + 1, len(lines)):
        if _section_name(lines[index]) is not None:
            break
        parsed = _split_value_line(lines[index])
        if parsed is None or parsed[0].lower() != package.lower():
            continue
        if parsed[1].lower() == value.lower():
            return text, False
        lines[index] = '"%s"="%s"' % (package, value)
        return "\n".join(lines), True
    lines.insert(header + 1, '"%s"="%s"' % (package, value))
    return "\n".join(lines), True


def _read_text(path: Path) -> Optional[str]:
    """Read a registry file, preserving bytes that are not valid UTF-8."""
    try:
        return path.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as exc:
        log.warning("apply: cannot read %s: %s", path, exc)
        return None


def _write_text_atomic(path: Path, text: str) -> None:
    """Replace *path* with *text* through a same-folder temp file."""
    handle_fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8",
                       errors="surrogateescape", newline="") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        except OSError:
            log.debug("apply: could not remove temp file %s", temp_name)
        raise


def _registry_path(plan: InstallPlan) -> Optional[Path]:
    """The ``user.reg`` to edit, or None when the plan does not use the prefix."""
    if plan.dll_override.location != OVERRIDE_LOCATION_PROTON_PREFIX:
        return None
    if not plan.proton_compatdata_dir:
        return None
    return Path(plan.proton_compatdata_dir).joinpath(*_REGISTRY_FILE)


def _apply_registry(plan: InstallPlan, dry_run: bool) -> Optional[ActionReport]:
    """Set the Wine DLL override in the prefix the plan carries, if any."""
    registry = _registry_path(plan)
    if registry is None:
        return None
    reason = plan.dll_override.reason
    item = "Set the Wine override %s in %s" % (_DLL_OVERRIDE_VALUE, registry)
    if not registry.is_file():
        return _report(item, STATUS_COULD_NOT, reason,
                       "the prefix has no user.reg to edit")
    text = _read_text(registry)
    if text is None:
        return _report(item, STATUS_COULD_NOT, reason,
                       "user.reg could not be read")
    updated, changed = _set_override_in_text(
        text, _DLL_OVERRIDE_SECTION, _DLL_OVERRIDE_PACKAGE, _DLL_OVERRIDE_VALUE
    )
    if not changed:
        return _report(item, STATUS_ALREADY, reason,
                       "the prefix already sets this override")
    if dry_run:
        return _report(item, STATUS_DRY_RUN, reason,
                       "would back user.reg up beside itself and set the "
                       "override")
    backup = _backup_path(registry)
    try:
        shutil.copy2(registry, backup)
        _write_text_atomic(registry, updated)
    except OSError as exc:
        return _report(item, STATUS_COULD_NOT, reason,
                       "registry write failed: %s" % exc)
    return _report(item, STATUS_DONE, reason,
                   "backed user.reg up to %s and set the override" % backup)


def _achievements_line(plan: InstallPlan) -> str:
    """The one explicit line stating the Steam achievements state and why."""
    state = "ON" if plan.achievements.enabled else "OFF"
    return "Achievements: %s (%s)" % (state, plan.achievements.reason)


def apply_install(
    plan: InstallPlan,
    *,
    dry_run: bool = False,
    is_game_running: Optional[Callable[[], bool]] = None,
) -> ApplyReport:
    """Execute *plan* and return an item-by-item report of what happened.

    Refuses, without touching anything, while Mewgenics is running or when a
    planned source file is missing; ``is_game_running`` overrides the
    game-process check for callers that already know (tests, mostly). With
    ``dry_run`` every item is still examined and reported but nothing is
    created, copied, backed up or written, the registry included.
    """
    checker = is_game_running or game_process_running
    achievements_line = _achievements_line(plan)
    if checker():
        log.warning("apply: refusing to install while Mewgenics is running")
        return ApplyReport(
            dry_run=dry_run,
            actions=(),
            achievements_line=achievements_line,
            launch_option=plan.launch_option.text,
            refused=("Mewgenics is running; close the game first so the files "
                     "and the Proton prefix are not in use."),
        )
    missing = [item for item in plan.files if not Path(item.source).is_file()]
    if missing:
        actions = tuple(
            _report("Place %s at %s" % (item.source, item.destination),
                    STATUS_COULD_NOT, item.reason, "source file is missing")
            for item in missing
        )
        refusal = "source file(s) not found: %s" % ", ".join(
            item.source for item in missing
        )
        log.error("apply: refusing, %s", refusal)
        return ApplyReport(
            dry_run=dry_run,
            actions=actions,
            achievements_line=achievements_line,
            launch_option=plan.launch_option.text,
            refused=refusal,
        )
    actions: List[ActionReport] = [
        _apply_placement(placement, dry_run) for placement in plan.files
    ]
    registry = _apply_registry(plan, dry_run)
    if registry is not None:
        actions.append(registry)
    return ApplyReport(
        dry_run=dry_run,
        actions=tuple(actions),
        achievements_line=achievements_line,
        launch_option=plan.launch_option.text,
    )


def render_report(report: ApplyReport) -> str:
    """A printable, item-by-item rendering of *report*.

    Each item shows its status tag, what it was, the plan's reason and what
    actually happened, so a user can see exactly which parts of the install
    took effect. The launch option (when the plan needs one) and the
    achievements line are printed last and never suppressed.
    """
    lines: List[str] = []
    if report.refused:
        lines.append("REFUSED: %s" % report.refused)
    for action in report.actions:
        lines.append("[%s] %s" % (_STATUS_TAG.get(action.status, action.status),
                                  action.item))
        if action.reason:
            lines.append("         %s" % action.reason)
        if action.detail:
            lines.append("         %s" % action.detail)
    if report.launch_option:
        lines.append("")
        lines.append("Steam launch option (Steam > Mewgenics > Properties > "
                     "Launch Options):")
        lines.append("    %s" % report.launch_option)
    lines.append("")
    lines.append(report.achievements_line)
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point: plan an install, apply it, print the report."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="install.apply",
        description="Apply a Mewgenics overlay install plan.",
    )
    parser.add_argument("--os", dest="os_name", default=_host_os(),
                        choices=(OS_LINUX, OS_NIXOS, OS_WINDOWS),
                        help="target operating system (default: this host)")
    parser.add_argument("--variant", required=True,
                        choices=(VARIANT_OVERLAY_ONLY, VARIANT_STANDALONE,
                                 VARIANT_MEWTATOR))
    parser.add_argument("--overlay", required=True,
                        help="the built overlay binary to place")
    parser.add_argument("--game-dir", required=True,
                        help="the Mewgenics install folder")
    parser.add_argument("--compatdata", default=None,
                        help="the game's Proton compatdata folder, if known")
    parser.add_argument("--dry-run", action="store_true",
                        help="report every action and change nothing")
    parser.add_argument("mod_files", nargs="*",
                        help="the companion mod's files")
    args = parser.parse_args(argv)
    try:
        plan = plan_install(
            args.os_name,
            args.variant,
            game_dir=args.game_dir,
            overlay_binary=args.overlay,
            mod_files=args.mod_files,
            proton_compatdata_dir=args.compatdata,
        )
    except UnknownInstallInput as exc:
        print("refusing: %s" % exc, file=sys.stderr)
        return 2
    report = apply_install(plan, dry_run=args.dry_run)
    print(render_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
