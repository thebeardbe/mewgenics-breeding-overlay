"""SaveWatcher / safe_read_save robustness tests (no Qt needed)."""

import os
import pathlib

from mewgenics_overlay.core.watcher import safe_read_save


def _write(tmp_path, name, content=b"payload"):
    p = tmp_path / name
    p.write_bytes(content)
    return str(p)


def test_safe_copy_preserves_content(tmp_path):
    src = _write(tmp_path, "campaign.sav", b"sqlite-bytes")
    dst = safe_read_save(src, dest_dir=str(tmp_path))
    assert dst is not None
    try:
        assert pathlib.Path(dst).read_bytes() == b"sqlite-bytes"
    finally:
        os.unlink(dst)


def test_hostile_filename_does_not_smuggle_uri_chars(tmp_path):
    # Names with '?' / '#' are valid on Linux and later get opened through a
    # sqlite ``file:`` URI — the temp copy must strip them so they can never
    # be parsed as URI query/fragment markers.
    src = _write(tmp_path, "evil.sav?mode=rw", b"data")
    dst = safe_read_save(src, dest_dir=str(tmp_path))
    assert dst is not None
    try:
        name = pathlib.Path(dst).name
        assert "?" not in name and "#" not in name
        assert name.startswith("mewgenics-evil.sav_")
    finally:
        os.unlink(dst)


def test_missing_source_returns_none(tmp_path):
    assert safe_read_save(str(tmp_path / "nope.sav")) is None
