"""Guarantee: the overlay never modifies a save file.

Everything here runs against *copies* or opens the database read-only:

  * `Session` parses through the vendored parser, which opens SQLite with
    `file:…?mode=ro` and closes the connection immediately after reading.
  * The live watcher path never opens the real file at all: `safe_read_save`
    copies the `.sav` (+ any `-wal`/`-shm`/`-journal` sidecars) to a temp
    file first, and the parser reads that temp copy.
  * Nothing in this project writes to the `Glaiel Games` directory or the
    saves folder; per-user settings live in the platform config directory.

These tests pin that contract: hashing and stat-ing a save before and after a
full engine pass (parse, search, partner ranking, safe copy) must show zero
byte changes and no mtime/size change.
"""

import hashlib
import os
import shutil

import pytest

from mewgenics_overlay.core.session import Session
from mewgenics_overlay.core.watcher import safe_read_save


def _sample() -> str:
    path = os.environ.get("MEWGENICS_SAMPLE_SAV")
    if not path or not os.path.exists(path):
        pytest.skip("set MEWGENICS_SAMPLE_SAV to a .sav file to run save tests")
    return path


def _fingerprint(path: str):
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    st = os.stat(path)
    return digest, st.st_size, st.st_mtime_ns


def test_engine_pass_leaves_save_untouched(tmp_path):
    src = _sample()
    save = tmp_path / "copy.sav"
    shutil.copyfile(src, save)

    before = _fingerprint(save)
    sess = Session(str(save))

    # full exercise: search, focus summary, ranked partners (incl. blocked)
    assert len(sess.alive) > 0
    target = sess.alive[0]
    hits = sess.search(target.name[:2])
    assert hits
    sess.summary(target)
    rows = sess.rank_partners(target, max_partners=10, show_blocked=3)
    assert len(rows) >= 1

    # live-read path must go through a copy, never the real file
    live = safe_read_save(str(save))
    assert live is not None
    try:
        assert os.path.abspath(live) != os.path.abspath(save)
        with open(live, "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == before[0]
        Session(live)  # parsing the copy is also fine
    finally:
        os.unlink(live)

    after = _fingerprint(save)
    assert after == before, "save file changed while the engine ran"
