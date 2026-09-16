"""``core/livesave.py``: locate the game's open save, resolve a reported name,
and decide when the overlay follows the game.

The module is Qt-free and never raises, so these tests need no Qt, no save file
and no game. ``find_live_save``/``game_process_running``/``detector_available``
are pointed at a fake ``/proc`` tree built under ``tmp_path`` (their
``proc_root`` parameter exists for exactly that) and nothing here ever reads the
machine's real process table.

Contract under test (changed in review):

  * the game is matched by its **exact executable name** (``Mewgenics.exe``,
    case-insensitive), never a loose substring, and the overlay's own process is
    skipped,
  * a save reached through a symlink is returned as its canonical real path,
  * ``.sav`` targets under the system temp directory are ignored, and
  * the follow policy only judges the game offline after a grace period of
    consecutive process-absent scans, with a bridge disconnect treated
    differently depending on whether a process detector exists.
"""

from __future__ import annotations

import os
import stat

import pytest

from mewgenics_overlay.core import livesave


# ── fake /proc tree ─────────────────────────────────────────────────────────
def _make_proc(root, pid, *, comm=None, cmdline=None, fds=None,
               fd_target=None):
    """Create one fake ``/proc/<pid>`` directory.

    *comm* / *cmdline* are written as the kernel would (cmdline NUL-joined).
    *fds* maps a descriptor number to its symlink target. *fd_target* replaces
    the whole ``fd`` entry with a symlink to that (possibly missing) path, which
    makes the ``fd`` directory itself unreadable.
    """
    pid_dir = root / str(pid)
    pid_dir.mkdir()
    if comm is not None:
        (pid_dir / "comm").write_text(comm, encoding="utf-8")
    if cmdline is not None:
        (pid_dir / "cmdline").write_bytes(cmdline.encode("utf-8"))
    if fd_target is not None:
        os.symlink(fd_target, pid_dir / "fd")
        return pid_dir
    if fds:
        fd_dir = pid_dir / "fd"
        fd_dir.mkdir()
        for number, target in fds.items():
            os.symlink(target, fd_dir / str(number))
    return pid_dir


def _proc_root(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    return root


def _non_temp(monkeypatch, tmp_path):
    """Make ``tmp_path`` look like a normal location, not the system temp.

    ``tmp_path`` itself usually lives under ``/tmp``; without this a real save
    written under it would be filtered out by the temp-directory guard.
    """
    monkeypatch.setattr(
        livesave.tempfile, "gettempdir",
        lambda: str(tmp_path / "not-the-system-temp"))


# ── find_live_save: matching ────────────────────────────────────────────────
def test_find_live_save_returns_the_save_a_matching_process_holds(tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 1234, comm="Mewgenics.exe",
               fds={0: "/dev/null", 7: "/games/saves/steamcampaign01.sav"})

    found = livesave.find_live_save(proc_root=str(root))

    assert found == "/games/saves/steamcampaign01.sav"


def test_find_live_save_ignores_processes_that_do_not_match_the_executable(
        tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="bash", cmdline="/usr/bin/bash",
               fds={3: "/games/saves/steamcampaign01.sav"})
    _make_proc(root, 200, comm="python3", cmdline="python3 -m pytest",
               fds={3: "/home/x/other.sav"})

    assert livesave.find_live_save(proc_root=str(root)) is None


def test_find_live_save_does_not_match_a_loose_substring_of_the_name(tmp_path):
    # The whole executable base name must match: a wrapper named after the game
    # (and this overlay's own arguments) must never be mistaken for it.
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="notmewgenics.exe",
               cmdline="notmewgenics.exe",
               fds={3: "/games/saves/steamcampaign01.sav"})
    _make_proc(root, 200, comm="MewgenicsLauncher.exe",
               cmdline="MewgenicsLauncher.exe",
               fds={3: "/games/saves/steamcampaign01.sav"})

    assert livesave.find_live_save(proc_root=str(root)) is None


def test_find_live_save_does_not_match_the_game_named_only_in_later_arguments(
        tmp_path):
    # argv[0] is the process's own name; an argument that merely mentions the
    # game (as the overlay's command line does) is not the game process.
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="python3",
               cmdline="python3\x00--run\x00/opt/Mewgenics.exe/loader.py",
               fds={3: "/games/saves/steamcampaign01.sav"})

    assert livesave.find_live_save(proc_root=str(root)) is None


def test_find_live_save_ignores_a_matching_process_with_a_different_suffix(
        tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={0: "/dev/null", 4: "/games/saves/steamcampaign01.txt"})

    assert livesave.find_live_save(proc_root=str(root)) is None


def test_find_live_save_matches_a_truncated_name_through_the_command_line(
        tmp_path):
    # Linux truncates ``comm`` to 15 bytes, so a Proton process whose name is
    # the save path's tail no longer contains the game name; its command line
    # argv[0] base name does.
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="steamapps/commo",
               cmdline="Z:\\home\\x\\Mewgenics\\Mewgenics.exe",
               fds={5: "/games/saves/steamcampaign02.sav"})

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/steamcampaign02.sav"


def test_find_live_save_matching_is_case_insensitive(tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="MEWGENICS.EXE",
               fds={5: "/games/saves/SteamCampaign02.sav"})

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/SteamCampaign02.sav"


def test_find_live_save_honours_a_custom_game_executable(tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="MyGame.exe",
               fds={5: "/games/saves/profile.sav"})
    _make_proc(root, 200, comm="Mewgenics.exe",
               fds={5: "/games/saves/other.sav"})

    assert livesave.find_live_save(proc_root=str(root),
                                   game_exe="MyGame.exe") == \
        "/games/saves/profile.sav"


def test_find_live_save_matches_an_upper_case_save_suffix(tmp_path):
    # The process name already matched case-insensitively; the *save suffix*
    # must too, consistent with is_save_file_name, so a save written with an
    # upper-case extension is still found.
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={5: "/games/saves/STEAMCAMPAIGN01.SAV"})

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/STEAMCAMPAIGN01.SAV"


def test_find_live_save_matches_a_mixed_case_save_suffix(tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={5: "/games/saves/SteamCampaign01.SaV"})

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/SteamCampaign01.SaV"


def test_find_live_save_can_be_pointed_at_a_custom_suffix(tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={5: "/games/profile.dat"})

    assert livesave.find_live_save(proc_root=str(root), suffix=".dat") == \
        "/games/profile.dat"


# ── find_live_save: the overlay's own process ───────────────────────────────
def test_find_live_save_never_matches_the_overlays_own_process(tmp_path):
    # A pid directory named after the test runner's own pid simulates the
    # overlay's process: it mentions the game and may hold a save copy.
    root = _proc_root(tmp_path)
    _make_proc(root, os.getpid(), comm="Mewgenics.exe",
               cmdline="Mewgenics.exe --save /tmp/livesave-copy.sav",
               fds={5: "/games/saves/steamcampaign01.sav"})

    assert livesave.find_live_save(proc_root=str(root)) is None


def test_find_live_save_skips_its_own_process_but_uses_a_later_one(tmp_path):
    # The own pid sorts *before* the other on purpose, so a failure to skip it
    # would surface as the overlay's own save path.
    root = _proc_root(tmp_path)
    own = os.getpid()
    _make_proc(root, own, comm="Mewgenics.exe",
               fds={5: "/games/saves/own.sav"})
    _make_proc(root, own + 1_000_000, comm="Mewgenics.exe",
               fds={5: "/games/saves/other.sav"})

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/other.sav"


# ── game_process_running / detector_available ───────────────────────────────
def test_game_process_running_is_true_with_no_save_open(tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe")

    assert livesave.game_process_running(proc_root=str(root)) is True


def test_game_process_running_is_false_for_other_processes(tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="python3", cmdline="python3 -m pytest")

    assert livesave.game_process_running(proc_root=str(root)) is False


def test_game_process_running_never_counts_the_overlay_itself(tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, os.getpid(), comm="Mewgenics.exe")

    assert livesave.game_process_running(proc_root=str(root)) is False


def test_detector_available_reflects_a_process_table(tmp_path):
    root = _proc_root(tmp_path)
    assert livesave.detector_available(proc_root=str(root)) is True
    assert livesave.detector_available(
        proc_root=str(tmp_path / "no-such-proc")) is False


# ── find_live_save: robustness ──────────────────────────────────────────────
def test_find_live_save_skips_an_unreadable_fd_directory(tmp_path):
    root = _proc_root(tmp_path)
    # A vanished process: ``fd`` is a dangling symlink, so listing it raises.
    _make_proc(root, 100, comm="Mewgenics.exe", fd_target=root / "vanished-fd")
    _make_proc(root, 200, comm="Mewgenics.exe",
               fds={5: "/games/saves/steamcampaign03.sav"})

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/steamcampaign03.sav"


def test_find_live_save_skips_a_descriptor_that_cannot_be_read(tmp_path):
    root = _proc_root(tmp_path)
    # fd/1 is a regular file: readlink raises EINVAL and the scan must go on.
    pid_dir = _make_proc(root, 100, comm="Mewgenics.exe",
                         fds={2: "/games/saves/steamcampaign04.sav"})
    (pid_dir / "fd" / "1").write_text("not a link", encoding="utf-8")

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/steamcampaign04.sav"


def test_find_live_save_ignores_a_deleted_file_descriptor(tmp_path):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={5: "/games/saves/steamcampaign01.sav (deleted)"})

    assert livesave.find_live_save(proc_root=str(root)) is None


def test_find_live_save_ignores_non_numeric_proc_entries(tmp_path):
    root = _proc_root(tmp_path)
    (root / "cpuinfo").write_text("not a process", encoding="utf-8")
    (root / "self").mkdir()
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={5: "/games/saves/steamcampaign01.sav"})

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/steamcampaign01.sav"


def test_find_live_save_missing_tree_is_none(tmp_path):
    missing = tmp_path / "no-such-proc"
    assert not missing.exists()

    assert livesave.find_live_save(proc_root=str(missing)) is None


def test_find_live_save_unreadable_tree_is_none(tmp_path):
    root = _proc_root(tmp_path)
    root.chmod(0)                       # unreadable directory
    try:
        if os.access(str(root), os.R_OK):
            pytest.skip("running with privileges that ignore directory modes")
        assert livesave.find_live_save(proc_root=str(root)) is None
    finally:
        root.chmod(stat.S_IRWXU)


@pytest.mark.parametrize("kwargs", [
    {"game_exe": ""},
    {"suffix": ""},
    {"game_exe": "", "suffix": ""},
])
def test_find_live_save_with_an_empty_exe_or_suffix_is_none(tmp_path, kwargs):
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={5: "/games/saves/steamcampaign01.sav"})

    assert livesave.find_live_save(proc_root=str(root), **kwargs) is None


# ── find_live_save: the system temp directory ───────────────────────────────
def test_find_live_save_ignores_a_save_under_the_system_temp(tmp_path,
                                                             monkeypatch):
    # Opening a save copies it to a temp file; that copy is not the game's.
    temp_root = tmp_path / "systmp"
    temp_root.mkdir()
    (temp_root / "steamcampaign01.sav").write_bytes(b"copy")
    monkeypatch.setattr(livesave.tempfile, "gettempdir",
                        lambda: str(temp_root))
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={5: str(temp_root / "steamcampaign01.sav")})

    assert livesave.find_live_save(proc_root=str(root)) is None


def test_find_live_save_skips_a_temp_save_and_finds_the_real_one(tmp_path,
                                                                 monkeypatch):
    temp_root = tmp_path / "systmp"
    temp_root.mkdir()
    (temp_root / "steamcampaign01.sav").write_bytes(b"copy")
    monkeypatch.setattr(livesave.tempfile, "gettempdir",
                        lambda: str(temp_root))
    root = _proc_root(tmp_path)
    # The temp copy is the lower descriptor, so it is examined first.
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={5: str(temp_root / "steamcampaign01.sav"),
                    7: "/games/saves/steamcampaign01.sav"})

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/steamcampaign01.sav"


def test_find_live_save_is_not_fooled_by_a_filesystem_temp_root(tmp_path,
                                                                monkeypatch):
    # A temp root of "/" would otherwise swallow every path.
    monkeypatch.setattr(livesave.tempfile, "gettempdir", lambda: os.sep)
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe",
               fds={5: "/games/saves/steamcampaign01.sav"})

    assert livesave.find_live_save(proc_root=str(root)) == \
        "/games/saves/steamcampaign01.sav"


# ── canonical paths ─────────────────────────────────────────────────────────
def test_canonical_path_resolves_symlinks_and_handles_empty(tmp_path):
    real = tmp_path / "real.sav"
    real.write_bytes(b"sqlite")
    link = tmp_path / "link.sav"
    os.symlink(real, link)
    expected = os.path.realpath(str(real))

    assert livesave.canonical_path(str(link)) == expected
    assert livesave.canonical_path(str(real)) == expected
    assert livesave.canonical_path(None) is None
    assert livesave.canonical_path("") is None


def test_find_live_save_returns_the_canonical_path_of_a_symlinked_save(
        tmp_path, monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    real_dir = tmp_path / "games" / "saves"
    real_dir.mkdir(parents=True)
    real = real_dir / "steamcampaign01.sav"
    real.write_bytes(b"sqlite")
    link = tmp_path / "library.sav"
    os.symlink(real, link)
    root = _proc_root(tmp_path)
    _make_proc(root, 100, comm="Mewgenics.exe", fds={5: str(link)})

    assert livesave.find_live_save(proc_root=str(root)) == \
        os.path.realpath(str(real))


# ── resolve_save_path ───────────────────────────────────────────────────────
SAVES = [
    {"path": "/steam/root/A/steamcampaign01.sav", "root": "/steam/root/A",
     "mtime": 3.0},
    {"path": "/steam/root/B/SteamCampaign02.SAV", "root": "/steam/root/B",
     "mtime": 2.0},
]


def test_resolve_save_path_matches_a_bare_name_against_the_records():
    assert livesave.resolve_save_path("steamcampaign01.sav", SAVES) == \
        "/steam/root/A/steamcampaign01.sav"


def test_resolve_save_path_is_case_insensitive():
    assert livesave.resolve_save_path("steamcampaign02.sav", SAVES) == \
        "/steam/root/B/SteamCampaign02.SAV"


def test_resolve_save_path_accepts_plain_path_strings():
    assert livesave.resolve_save_path(
        "steamcampaign01.sav", ["/steam/root/A/steamcampaign01.sav"]) == \
        "/steam/root/A/steamcampaign01.sav"


def test_resolve_save_path_unknown_name_is_none():
    assert livesave.resolve_save_path("steamcampaign99.sav", SAVES) is None


def test_resolve_save_path_skips_malformed_records():
    saves = [{"no": "path"}, 42, {"path": "/saves/steamcampaign01.sav"}]
    assert livesave.resolve_save_path("steamcampaign01.sav", saves) == \
        "/saves/steamcampaign01.sav"


def test_resolve_save_path_an_existing_absolute_path_is_returned(tmp_path):
    path = tmp_path / "steamcampaign03.sav"
    path.write_bytes(b"sqlite")

    assert livesave.resolve_save_path(str(path)) == os.path.realpath(str(path))


def test_resolve_save_path_returns_the_canonical_path_of_a_symlink(
        tmp_path, monkeypatch):
    _non_temp(monkeypatch, tmp_path)
    real = tmp_path / "steamcampaign03.sav"
    real.write_bytes(b"sqlite")
    link = tmp_path / "alias.sav"
    os.symlink(real, link)

    assert livesave.resolve_save_path(str(link)) == os.path.realpath(str(real))


def test_resolve_save_path_a_missing_absolute_path_is_none(tmp_path):
    missing = tmp_path / "steamcampaign03.sav"
    assert not missing.exists()

    assert livesave.resolve_save_path(str(missing)) is None


def test_resolve_save_path_refuses_an_absolute_path_that_is_not_a_save(
        tmp_path):
    # A confused or hostile mod must not point the overlay at a non-save file,
    # even when it exists and the path itself is safe.
    notes = tmp_path / "notes.txt"
    notes.write_text("not a save", encoding="utf-8")

    assert livesave.resolve_save_path(str(notes)) is None


@pytest.mark.parametrize("name", [
    "sub/dir.sav",                    # relative path, not a bare name
    "sub\\dir.sav",                   # Windows separator
    "C:steamcampaign01.sav",          # drive specifier
    "/steam/root/../secrets.sav",     # traversal in an absolute path
    "../../steamcampaign01.sav",      # traversal in a relative path
    "..\\..\\steamcampaign01.sav",
    ".",
    "..",
    "",
    None,
    42,
    {"file": "steamcampaign01.sav"},
])
def test_resolve_save_path_refuses_hostile_or_empty_inputs(name):
    assert livesave.resolve_save_path(name, SAVES) is None


def test_resolve_save_path_falls_back_to_discovery(monkeypatch):
    calls = []

    def fake_find_all_saves():
        calls.append(True)
        return SAVES

    monkeypatch.setattr(livesave.discovery, "find_all_saves", fake_find_all_saves)

    assert livesave.resolve_save_path("steamcampaign01.sav") == \
        "/steam/root/A/steamcampaign01.sav"
    assert calls == [True]


def test_is_save_file_name_rejects_and_accepts():
    assert livesave.is_save_file_name("steamcampaign01.sav") is True
    assert livesave.is_save_file_name("STEAMCAMPAIGN01.SAV") is True
    assert livesave.is_save_file_name(" steamcampaign01.sav ") is True
    assert livesave.is_save_file_name("steamcampaign01.txt") is False
    assert livesave.is_save_file_name("steamcampaign01.sav", suffix="") is False
    assert livesave.is_save_file_name(7) is False


# ── SaveFollowPolicy: following and pinning ─────────────────────────────────
def test_first_detection_returns_the_path_and_marks_online():
    policy = livesave.SaveFollowPolicy()

    assert policy.note_game_save("/saves/a.sav", None) == "/saves/a.sav"
    assert policy.online is True
    assert policy.pinned is False
    assert policy.game_save == "/saves/a.sav"


def test_the_current_save_returns_nothing():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/a.sav", None)

    assert policy.note_game_save("/saves/a.sav", "/saves/a.sav") is None
    assert policy.online is True
    assert policy.game_save == "/saves/a.sav"


def test_disabled_policy_never_returns_a_path():
    policy = livesave.SaveFollowPolicy(enabled=False)

    assert policy.note_game_save("/saves/a.sav", None) is None
    assert policy.note_game_save("/saves/b.sav", "/saves/a.sav") is None
    # The observation is still recorded, so enabling later behaves sanely.
    assert policy.online is True
    assert policy.game_save == "/saves/b.sav"


def test_a_manual_open_while_the_game_is_online_pins_and_blocks_following():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)

    policy.note_manual_open("/saves/user.sav")

    assert policy.pinned is True
    assert policy.pinned_path == "/saves/user.sav"
    assert policy.note_game_save("/saves/game.sav", "/saves/user.sav") is None


def test_the_game_switching_campaign_lifts_the_pin_and_follows():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/old.sav", None)
    policy.note_manual_open("/saves/user.sav")
    assert policy.pinned is True

    assert policy.note_game_save("/saves/new.sav", "/saves/user.sav") == \
        "/saves/new.sav"
    assert policy.pinned is False
    assert policy.game_save == "/saves/new.sav"


def test_re_reporting_the_same_save_keeps_the_pin():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)
    policy.note_manual_open("/saves/user.sav")

    assert policy.note_game_save("/saves/game.sav", "/saves/user.sav") is None
    assert policy.pinned is True


def test_note_game_online_marks_online_without_a_known_save():
    policy = livesave.SaveFollowPolicy()
    assert policy.online is False
    assert policy.game_save is None

    policy.note_game_online()

    assert policy.online is True
    assert policy.game_save is None
    assert policy.pinned is False


def test_note_game_online_does_not_clobber_a_known_game_save():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)

    policy.note_game_online()

    assert policy.online is True
    assert policy.game_save == "/saves/game.sav"


def test_a_manual_open_after_note_game_online_pins():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_online()

    policy.note_manual_open("/saves/user.sav")

    assert policy.pinned is True
    assert policy.pinned_path == "/saves/user.sav"


def test_a_detection_after_note_game_online_does_not_override_the_pin():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_online()
    policy.note_manual_open("/saves/user.sav")

    assert policy.note_game_save("/saves/game.sav", "/saves/user.sav") is None
    assert policy.pinned is True
    assert policy.game_save == "/saves/game.sav"


def test_a_later_different_game_save_lifts_the_pin_after_note_game_online():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_online()
    policy.note_manual_open("/saves/user.sav")
    # The first report only records the game's save; the pin still holds.
    assert policy.note_game_save("/saves/game.sav", "/saves/user.sav") is None
    assert policy.pinned is True

    assert policy.note_game_save("/saves/other.sav", "/saves/user.sav") == \
        "/saves/other.sav"
    assert policy.pinned is False
    assert policy.game_save == "/saves/other.sav"


def test_note_game_offline_clears_online_and_the_pin():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)
    policy.note_manual_open("/saves/user.sav")
    assert (policy.online, policy.pinned) == (True, True)

    policy.note_game_offline()

    assert policy.online is False
    assert policy.pinned is False
    assert policy.pinned_path is None


def test_a_manual_open_while_offline_does_not_pin():
    policy = livesave.SaveFollowPolicy()

    policy.note_manual_open("/saves/user.sav")

    assert policy.online is False
    assert policy.pinned is False


def test_a_manual_open_after_the_game_left_does_not_pin():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)
    policy.note_game_offline()

    policy.note_manual_open("/saves/user.sav")

    assert policy.pinned is False


@pytest.mark.parametrize("path", [None, "", 0])
def test_a_falsy_reported_path_is_ignored(path):
    policy = livesave.SaveFollowPolicy()

    assert policy.note_game_save(path, None) is None
    assert policy.online is False
    assert policy.game_save is None


def test_a_falsy_manual_path_is_ignored():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)

    policy.note_manual_open(None)
    policy.note_manual_open("")

    assert policy.pinned is False


def test_following_resumes_after_the_game_returns():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)
    policy.note_game_offline()
    policy.note_manual_open("/saves/user.sav")     # offline: no pin

    assert policy.note_game_save("/saves/game.sav", "/saves/user.sav") == \
        "/saves/game.sav"
    assert policy.pinned is False


# ── SaveFollowPolicy: process presence and the grace period ─────────────────
def test_the_game_is_offline_only_after_the_grace_period():
    policy = livesave.SaveFollowPolicy()               # default grace of 3
    assert policy.offline_grace_scans == 3
    policy.note_game_save("/saves/game.sav", None)

    assert policy.note_game_process(False) is False    # 1 absent
    assert policy.note_game_process(False) is False    # 2 absent
    assert policy.online is True

    assert policy.note_game_process(False) is True     # 3 absent: offline
    assert policy.online is False


def test_a_present_process_resets_the_grace_count():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)
    policy.note_game_process(False)
    policy.note_game_process(False)

    policy.note_game_process(True)                     # presence resets it

    assert policy.online is True
    policy.note_game_process(False)
    policy.note_game_process(False)
    assert policy.online is True                       # only 2 since the reset
    policy.note_game_process(False)
    assert policy.online is False


def test_a_present_process_marks_the_game_online():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_offline()
    assert policy.online is False

    assert policy.note_game_process(True) is False
    assert policy.online is True


def test_the_grace_period_is_configurable():
    policy = livesave.SaveFollowPolicy(offline_grace_scans=1)
    policy.note_game_save("/saves/game.sav", None)

    assert policy.note_game_process(False) is True
    assert policy.online is False


def test_a_zero_grace_period_is_clamped_to_one():
    policy = livesave.SaveFollowPolicy(offline_grace_scans=0)
    assert policy.offline_grace_scans == 1
    policy.note_game_save("/saves/game.sav", None)

    assert policy.note_game_process(False) is True


def test_a_grace_period_that_never_was_online_reports_nothing():
    policy = livesave.SaveFollowPolicy(offline_grace_scans=1)

    # Nothing was online, so there is no transition to report.
    assert policy.note_game_process(False) is False
    assert policy.online is False


def test_the_pin_holds_until_the_grace_period_expires():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)
    policy.note_manual_open("/saves/user.sav")
    assert policy.pinned is True

    policy.note_game_process(False)
    policy.note_game_process(False)
    assert (policy.online, policy.pinned) == (True, True)

    policy.note_game_process(False)
    assert (policy.online, policy.pinned) == (False, False)


def test_a_detected_save_resets_the_grace_count():
    policy = livesave.SaveFollowPolicy()
    policy.note_game_save("/saves/game.sav", None)
    policy.note_game_process(False)
    policy.note_game_process(False)

    # A scan that sees the game's save is itself proof the process is present.
    assert policy.note_game_save("/saves/game.sav", "/saves/game.sav") is None

    policy.note_game_process(False)
    policy.note_game_process(False)
    assert policy.online is True
    policy.note_game_process(False)
    assert policy.online is False


# ── SaveFollowPolicy: bridge connection vs. process detector ────────────────
def test_a_bridge_connect_marks_online_without_a_detector():
    policy = livesave.SaveFollowPolicy()

    policy.note_bridge_connection(True, detector_available=False)

    assert policy.online is True


def test_a_bridge_disconnect_without_a_detector_clears_online_and_pin():
    policy = livesave.SaveFollowPolicy()
    policy.note_bridge_connection(True, detector_available=False)
    policy.note_manual_open("/saves/user.sav")
    assert (policy.online, policy.pinned) == (True, True)

    policy.note_bridge_connection(False, detector_available=False)

    assert policy.online is False
    assert policy.pinned is False


def test_a_bridge_disconnect_with_a_detector_keeps_online_and_pin():
    # A mod hiccup is not proof the game quit: the process scan is the
    # authority, so the user's pin survives.
    policy = livesave.SaveFollowPolicy()
    policy.note_bridge_connection(True, detector_available=True)
    policy.note_manual_open("/saves/user.sav")
    assert (policy.online, policy.pinned) == (True, True)

    policy.note_bridge_connection(False, detector_available=True)

    assert policy.online is True
    assert policy.pinned is True
    assert policy.pinned_path == "/saves/user.sav"


def test_a_bridge_disconnect_with_a_detector_still_follows_a_known_save():
    policy = livesave.SaveFollowPolicy()
    policy.note_bridge_connection(True, detector_available=True)
    policy.note_game_save("/saves/game.sav", None)

    policy.note_bridge_connection(False, detector_available=True)

    # The game's own save is still remembered; only the process scan can end it.
    assert policy.game_save == "/saves/game.sav"
    assert policy.online is True
