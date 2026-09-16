"""``core/livesave_psutil.py``: the Windows live-save backend.

Windows has no ``/proc``, so ``livesave`` asks psutil for the game process's
open files there. psutil is an optional dependency and the module is injected
into every entry point, so these tests need neither the library nor a real
process table and never touch the machine's processes.

The contract under test:

  * the game is found by its exact executable name through psutil, and the
    overlay's own pid is skipped,
  * the first non-temp open file ending in ``.sav`` is returned as its
    canonical real path, the same rule the ``/proc`` backend uses,
  * a missing psutil only makes the detector unavailable: it logs once and
    never raises, and every public entry point still answers safely,
  * a raising psutil call is contained and logged, and
  * both backends agree on the same situation.

Nothing here reads ``/proc`` or the real process table.
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

from mewgenics_overlay.core import livesave
from mewgenics_overlay.core import livesave_psutil


# ── fake psutil ─────────────────────────────────────────────────────────────
class _FakePsutilError(Exception):
    """Stands in for ``psutil.Error``."""


class _FakeOpenFile:
    def __init__(self, path):
        self.path = path


class _FakeProc:
    """One psutil process: a name and the files it has open.

    *name_error* / *open_error* make the matching call raise, so a vanished or
    denied process can be exercised without a real one.
    """

    def __init__(self, pid, name, paths=(), *, name_error=None,
                 open_error=None):
        self.pid = pid
        self._name = name
        self._paths = list(paths)
        self._name_error = name_error
        self._open_error = open_error

    def name(self):
        if self._name_error is not None:
            raise self._name_error
        return self._name

    def open_files(self):
        if self._open_error is not None:
            raise self._open_error
        return [_FakeOpenFile(p) for p in self._paths]


class _FakePsutil:
    """Minimal psutil module: only the API the backend reaches for."""

    Error = _FakePsutilError

    def __init__(self, procs=()):
        self._procs = list(procs)

    def process_iter(self):
        return list(self._procs)


class _RaisingPsutil:
    """A psutil whose process scan itself blows up."""

    Error = _FakePsutilError

    def __init__(self, exc):
        self._exc = exc

    def process_iter(self):
        raise self._exc


# ── helpers ─────────────────────────────────────────────────────────────────
def _non_temp(monkeypatch, tmp_path):
    """Make ``tmp_path`` look like a normal location, not the system temp.

    Without this every save written under ``tmp_path`` (usually ``/tmp``) would
    be filtered out by the shared temp-directory guard.
    """
    monkeypatch.setattr(
        livesave.tempfile, "gettempdir",
        lambda: str(tmp_path / "not-the-system-temp"))


def _write_save(tmp_path, name="steamcampaign01.sav"):
    path = tmp_path / "saves" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"sqlite")
    return path


def _make_proc_tree(root, pid, *, comm="Mewgenics.exe", fds):
    """One fake ``/proc/<pid>`` with fd symlinks, for the Linux comparison."""
    pid_dir = root / str(pid)
    pid_dir.mkdir()
    (pid_dir / "comm").write_text(comm, encoding="utf-8")
    fd_dir = pid_dir / "fd"
    fd_dir.mkdir()
    for number, target in fds.items():
        os.symlink(target, fd_dir / str(number))
    return pid_dir


# ── finding the save ────────────────────────────────────────────────────────
def test_find_live_save_returns_the_save_the_game_has_open(tmp_path, monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    save = _write_save(tmp_path)
    module = _FakePsutil([_FakeProc(4242, "Mewgenics.exe",
                                    ["/dev/null", str(save)])])

    found = livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module)

    assert found == str(save)


def test_find_live_save_returns_the_canonical_path_of_a_symlinked_save(
        tmp_path, monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    real = _write_save(tmp_path)
    link = tmp_path / "library.sav"
    os.symlink(real, link)
    module = _FakePsutil([_FakeProc(4242, "Mewgenics.exe", [str(link)])])

    found = livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module)

    assert found == os.path.realpath(str(real))


def test_find_live_save_matches_the_process_name_case_insensitively(
        tmp_path, monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    save = _write_save(tmp_path)
    module = _FakePsutil([_FakeProc(4242, "MEWGENICS.EXE", [str(save)])])

    assert livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"),
        psutil_module=module) == str(save)


def test_find_live_save_never_matches_the_overlays_own_pid(tmp_path, monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    save = _write_save(tmp_path)
    module = _FakePsutil([_FakeProc(os.getpid(), "Mewgenics.exe", [str(save)])])

    assert livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module) is None


def test_find_live_save_ignores_a_process_that_does_not_match(tmp_path,
                                                             monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    save = _write_save(tmp_path)
    module = _FakePsutil([_FakeProc(4242, "notmewgenics.exe", [str(save)])])

    assert livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module) is None


def test_find_live_save_ignores_open_files_that_are_not_saves(
        tmp_path, monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    decoy = tmp_path / "notes.txt"
    decoy.write_text("not a save", encoding="utf-8")
    module = _FakePsutil([_FakeProc(4242, "Mewgenics.exe", [str(decoy)])])

    assert livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module) is None


def test_find_live_save_matches_an_upper_case_suffix(tmp_path, monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    save = _write_save(tmp_path, "STEAMCAMPAIGN01.SAV")
    module = _FakePsutil([_FakeProc(4242, "Mewgenics.exe", [str(save)])])

    assert livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"),
        psutil_module=module) == str(save)


# ── the system temp directory decoy ─────────────────────────────────────────
def test_find_live_save_ignores_a_save_under_the_temp_directory(
        tmp_path, monkeypatch):
    temp_root = tmp_path / "aaa-temp"
    temp_root.mkdir()
    (temp_root / "steamcampaign01.sav").write_bytes(b"copy")
    monkeypatch.setattr(livesave.tempfile, "gettempdir",
                        lambda: str(temp_root))
    module = _FakePsutil([_FakeProc(
        4242, "Mewgenics.exe", [str(temp_root / "steamcampaign01.sav")])])

    assert livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module) is None


def test_find_live_save_skips_the_temp_decoy_and_finds_the_real_save(
        tmp_path, monkeypatch):
    temp_root = tmp_path / "aaa-temp"
    temp_root.mkdir()
    decoy = temp_root / "steamcampaign01.sav"
    decoy.write_bytes(b"copy")
    monkeypatch.setattr(livesave.tempfile, "gettempdir",
                        lambda: str(temp_root))
    real = tmp_path / "zzz-saves" / "steamcampaign01.sav"
    real.parent.mkdir()
    real.write_bytes(b"sqlite")
    # The sorted handles put the decoy first, so a failure to skip it would
    # surface as the copy's path.
    module = _FakePsutil([_FakeProc(
        4242, "Mewgenics.exe", [str(decoy), str(real)])])

    assert livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module) == str(real)


# ── presence: no matching process, and no save open ─────────────────────────
def test_find_live_save_is_none_when_the_game_is_not_running(tmp_path):
    module = _FakePsutil([_FakeProc(1, "bash", ["/tmp/x.sav"])])

    assert livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module) is None


def test_game_process_running_sees_a_game_with_no_save_open(tmp_path):
    module = _FakePsutil([_FakeProc(4242, "Mewgenics.exe")])

    assert livesave.game_process_running(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module) is True


def test_game_process_running_is_false_when_the_game_is_absent(tmp_path):
    module = _FakePsutil([_FakeProc(1, "bash")])

    assert livesave.game_process_running(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module) is False


# ── psutil missing entirely ─────────────────────────────────────────────────
def test_missing_psutil_makes_the_detector_unavailable_without_raising(
        tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(livesave_psutil, "_psutil_warned", False)
    missing = str(tmp_path / "no-proc")

    with caplog.at_level(logging.WARNING,
                         logger="mewgenics_overlay.livesave_psutil"):
        assert livesave.detector_available(
            proc_root=missing, psutil_module=None) is False
        assert livesave.find_live_save(
            proc_root=missing, psutil_module=None) is None
        assert livesave.game_process_running(
            proc_root=missing, psutil_module=None) is False

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "a missing psutil must be logged exactly once"
    assert "psutil" in warnings[0].getMessage()


def test_the_missing_psutil_warning_is_not_repeated(tmp_path, monkeypatch,
                                                    caplog):
    monkeypatch.setattr(livesave_psutil, "_psutil_warned", False)
    missing = str(tmp_path / "no-proc")

    with caplog.at_level(logging.WARNING,
                         logger="mewgenics_overlay.livesave_psutil"):
        livesave.find_live_save(proc_root=missing, psutil_module=None)
        livesave.find_live_save(proc_root=missing, psutil_module=None)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_import_psutil_returns_none_when_the_library_is_missing(monkeypatch):
    # Force the lazy import to fail: a None entry in sys.modules makes
    # ``import psutil`` raise ImportError.
    monkeypatch.setattr(livesave_psutil, "_PSUTIL_TRIED", False)
    monkeypatch.setattr(livesave_psutil, "_PSUTIL_MODULE", object())
    monkeypatch.setitem(sys.modules, "psutil", None)

    assert livesave_psutil.import_psutil() is None


def test_detector_available_still_sees_a_real_process_table(monkeypatch):
    # A missing psutil must not disable the detector where /proc exists.
    monkeypatch.setattr(livesave_psutil, "_psutil_warned", True)
    assert livesave.detector_available(psutil_module=None) is True


# ── a raising psutil call is contained and logged ───────────────────────────
def test_a_raising_process_scan_is_contained_and_logged(caplog):
    module = _RaisingPsutil(_FakePsutilError("process table went away"))

    with caplog.at_level(logging.WARNING,
                         logger="mewgenics_overlay.livesave_psutil"):
        assert livesave.find_live_save(
            proc_root="/no-such-proc", psutil_module=module) is None
        assert livesave.game_process_running(
            proc_root="/no-such-proc", psutil_module=module) is False

    assert any("process scan failed" in r.getMessage()
               for r in caplog.records)


def test_a_process_whose_name_cannot_be_read_is_skipped(tmp_path, monkeypatch,
                                                        caplog):
    _non_temp(monkeypatch, tmp_path)
    save = _write_save(tmp_path)
    module = _FakePsutil([
        _FakeProc(1, "gone", name_error=_FakePsutilError("no such process")),
        _FakeProc(4242, "Mewgenics.exe", [str(save)]),
    ])

    with caplog.at_level(logging.DEBUG,
                         logger="mewgenics_overlay.livesave_psutil"):
        found = livesave.find_live_save(
            proc_root=str(tmp_path / "no-proc"), psutil_module=module)

    assert found == str(save)
    assert any("cannot name psutil pid" in r.getMessage()
               for r in caplog.records)


def test_a_process_whose_open_files_cannot_be_read_is_skipped(
        tmp_path, monkeypatch, caplog):
    _non_temp(monkeypatch, tmp_path)
    save = _write_save(tmp_path)
    module = _FakePsutil([
        _FakeProc(1, "Mewgenics.exe",
                  open_error=_FakePsutilError("access denied")),
        _FakeProc(4242, "Mewgenics.exe", [str(save)]),
    ])

    with caplog.at_level(logging.DEBUG,
                         logger="mewgenics_overlay.livesave_psutil"):
        found = livesave.find_live_save(
            proc_root=str(tmp_path / "no-proc"), psutil_module=module)

    assert found == str(save)
    assert any("cannot read open files" in r.getMessage()
               for r in caplog.records)


# ── both backends agree ─────────────────────────────────────────────────────
def test_the_proc_and_psutil_backends_agree_on_the_same_situation(
        tmp_path, monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    real = _write_save(tmp_path)
    link = tmp_path / "library.sav"
    os.symlink(real, link)

    # The Linux backend: a fake process table with the save open.
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _make_proc_tree(proc_root, 4242, fds={5: str(link)})
    from_proc = livesave.find_live_save(proc_root=str(proc_root))

    # The psutil backend: the same process with the same handle, no table.
    module = _FakePsutil([_FakeProc(4242, "Mewgenics.exe", [str(link)])])
    from_psutil = livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module)

    assert from_proc == from_psutil == os.path.realpath(str(real))


def test_the_proc_and_psutil_backends_agree_on_a_temp_decoy(tmp_path,
                                                            monkeypatch):
    temp_root = tmp_path / "aaa-temp"
    temp_root.mkdir()
    temp = temp_root / "steamcampaign01.sav"
    temp.write_bytes(b"copy")
    monkeypatch.setattr(livesave.tempfile, "gettempdir",
                        lambda: str(temp_root))

    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _make_proc_tree(proc_root, 4242, fds={5: str(temp)})
    from_proc = livesave.find_live_save(proc_root=str(proc_root))

    module = _FakePsutil([_FakeProc(4242, "Mewgenics.exe", [str(temp)])])
    from_psutil = livesave.find_live_save(
        proc_root=str(tmp_path / "no-proc"), psutil_module=module)

    assert from_proc is None
    assert from_psutil is None
