"""``install.sh`` end to end, offscreen: forwarding, idempotence, output.

``install.sh`` is the one-click entry point: it locates the game, then hands
the work to ``python -m install.apply``. These tests run the real script in a
temporary game folder, so only fake files are ever touched, and they never rely
on a real game install or a running game.

The game-running check lives deep in ``install.apply`` and cannot be injected
through the script's CLI, so the tests point the script's ``PYTHON`` at a tiny
wrapper that patches ``mewgenics_overlay.core.livesave.game_process_running``
to ``False`` before running the same ``install.apply`` entry point. Everything
else - argument parsing, path resolution, ``PYTHONPATH``, ``exec`` - is the real
script.

Covered here:

* a forwarded ``--dry-run`` reports "would do" and creates nothing;
* running it twice is safe: the second run says "already done" and leaves no
  backup;
* the achievements line reaches stdout, stating ON for standalone and OFF with
  ``-modpaths`` named for mewtator.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


def write_file(path: Path, text: str = "content") -> Path:
    """Create *path* (and its parents) with *text*; return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _game_check_stub(tmp_path: Path) -> Path:
    """A ``PYTHON`` for install.sh that never sees a running game.

    The wrapper is a two-line shell script around a helper module; the helper
    patches the process check and then runs the real ``install.apply`` main.
    """
    helper = write_file(
        tmp_path / "stub" / "no_game.py",
        "import sys\n"
        "import mewgenics_overlay.core.livesave as livesave\n"
        "livesave.game_process_running = lambda *args, **kwargs: False\n"
        "from install.apply import main\n"
        # install.sh invokes ``python -m install.apply <args>``; sys.argv[0]
        # is this helper, argv[1:3] are ``-m install.apply``, and the real
        # arguments start at argv[3].
        "sys.exit(main(sys.argv[3:]))\n",
    )
    wrapper = write_file(
        tmp_path / "stub" / "python",
        '#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, helper),
    )
    wrapper.chmod(0o755)
    return wrapper


@pytest.fixture
def run_install(tmp_path):
    """Invoke the real ``install.sh`` with the patched game check."""
    python = _game_check_stub(tmp_path)
    game_dir = tmp_path / "game"
    no_compatdata = tmp_path / "no-such-compatdata"

    def run(*args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHON"] = str(python)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        return subprocess.run(
            [str(INSTALL_SH), *args],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    return run, game_dir, no_compatdata


def test_dry_run_is_forwarded_and_changes_nothing(run_install, tmp_path):
    run, game_dir, no_compatdata = run_install
    overlay = write_file(tmp_path / "build" / "mewgenics-overlay", "OVERLAY")

    result = run(
        "--dry-run",
        "--variant", "overlay-only",
        "--overlay", str(overlay),
        "--game-dir", str(game_dir),
        "--compatdata", str(no_compatdata),
    )

    assert result.returncode == 0, result.stderr
    assert "would do" in result.stdout
    assert not game_dir.exists()
    assert not no_compatdata.exists()
    assert "Achievements: ON" in result.stdout


def test_running_the_script_twice_is_safe(run_install, tmp_path):
    run, game_dir, no_compatdata = run_install
    overlay = write_file(tmp_path / "build" / "mewgenics-overlay", "OVERLAY")
    args = (
        "--variant", "overlay-only",
        "--overlay", str(overlay),
        "--game-dir", str(game_dir),
        "--compatdata", str(no_compatdata),
    )

    first = run(*args)
    assert first.returncode == 0, first.stderr
    assert "did it" in first.stdout
    placed = game_dir / "mewgenics-overlay"
    assert placed.read_text() == "OVERLAY"

    second = run(*args)
    assert second.returncode == 0, second.stderr
    assert "already done" in second.stdout
    assert placed.read_text() == "OVERLAY"
    assert not list(game_dir.rglob("*.bak"))
    assert "Achievements: ON" in second.stdout


def test_standalone_dry_run_surfaces_the_achievements_line(run_install, tmp_path):
    run, game_dir, no_compatdata = run_install
    overlay = write_file(tmp_path / "build" / "mewgenics-overlay", "OVERLAY")
    loader = write_file(tmp_path / "mod" / "version.dll", "LOADER")
    config = write_file(tmp_path / "mod" / "chainloader.ini", "[mods]\n")
    dll = write_file(tmp_path / "mod" / "companion.dll", "DLL")
    # A standalone install needs a Proton prefix, so give it a fake one rather
    # than letting discovery look at the machine's real Steam libraries.
    compatdata = tmp_path / "compatdata" / "686060"
    write_file(compatdata / "pfx" / "user.reg", "WINE REGISTRY Version 2\n")

    result = run(
        "--dry-run",
        "--variant", "standalone",
        "--overlay", str(overlay),
        "--game-dir", str(game_dir),
        "--compatdata", str(compatdata),
        str(loader), str(config), str(dll),
    )

    assert result.returncode == 0, result.stderr
    assert "would do" in result.stdout
    assert "Achievements: ON" in result.stdout
    assert not game_dir.exists()
    assert (compatdata / "pfx" / "user.reg").read_text() == (
        "WINE REGISTRY Version 2\n"
    )


def test_mewtator_achievements_line_is_not_swallowed_and_names_modpaths(
    run_install, tmp_path
):
    run, game_dir, no_compatdata = run_install
    overlay = write_file(tmp_path / "build" / "mewgenics-overlay", "OVERLAY")
    asset = write_file(tmp_path / "mod" / "assets.pak", "PAK")

    result = run(
        "--dry-run",
        "--variant", "mewtator",
        "--overlay", str(overlay),
        "--game-dir", str(game_dir),
        "--compatdata", str(no_compatdata),
        str(asset),
    )

    assert result.returncode == 0, result.stderr
    assert "Achievements: OFF" in result.stdout
    assert "-modpaths" in result.stdout


def test_the_real_run_places_the_overlay_under_its_own_name(run_install, tmp_path):
    run, game_dir, no_compatdata = run_install
    overlay = write_file(tmp_path / "elsewhere" / "mewgenics-overlay", "PAYLOAD")

    result = run(
        "--variant", "overlay-only",
        "--overlay", str(overlay),
        "--game-dir", str(game_dir),
        "--compatdata", str(no_compatdata),
    )

    assert result.returncode == 0, result.stderr
    assert (game_dir / "mewgenics-overlay").read_text() == "PAYLOAD"
