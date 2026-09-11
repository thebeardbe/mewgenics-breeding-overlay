"""Auxiliary save reads: current day, npc_progress flags, save properties.

``_read_aux_save_data`` opens the save read-only and returns
``(current_day, npc_progress flags, properties)`` in one connection. Property
values are UTF-8 decoded with replacement and capped so an oversized blob can
never be retained; a missing or corrupt database degrades to empty results
without raising.
"""

import os
import sqlite3

import pytest

from mewgenics_overlay.core.session import (
    SAVE_PROPERTY_MAX_CHARS,
    Session,
    _read_aux_save_data,
)


def _make_db(path, properties=(), files=()):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE properties (key TEXT, data)")
        conn.execute("CREATE TABLE files (key TEXT, data)")
        conn.executemany(
            "INSERT INTO properties (key, data) VALUES (?, ?)", properties)
        conn.executemany("INSERT INTO files (key, data) VALUES (?, ?)", files)
        conn.commit()
    finally:
        conn.close()
    return str(path)


def test_normal_value_returns_day_flags_and_property(tmp_path):
    path = _make_db(
        tmp_path / "ok.sav",
        properties=[("current_day", 42), ("some_flag", b"hello")],
        files=[("npc_progress", b"unlock_frank unlock_tracy")],
    )
    day, flags, props = _read_aux_save_data(path)

    assert day == 42
    assert flags == {"unlock_frank", "unlock_tracy"}
    assert props["some_flag"] == "hello"
    # The current_day row is part of the properties map too.
    assert props["current_day"] == "42"


def test_overlong_value_is_truncated_to_the_cap(tmp_path):
    long_value = b"a" * (SAVE_PROPERTY_MAX_CHARS * 4)
    path = _make_db(
        tmp_path / "long.sav",
        properties=[("blob", long_value)],
    )
    _, _, props = _read_aux_save_data(path)

    assert len(props["blob"]) == SAVE_PROPERTY_MAX_CHARS
    assert props["blob"] == "a" * SAVE_PROPERTY_MAX_CHARS


def test_invalid_utf8_is_replaced_without_raising(tmp_path):
    path = _make_db(
        tmp_path / "badbytes.sav",
        properties=[("weird", b"ok\xff\xfebad")],
    )
    _, _, props = _read_aux_save_data(path)

    assert props["weird"].startswith("ok")
    assert "\ufffd" in props["weird"]
    assert len(props["weird"]) <= SAVE_PROPERTY_MAX_CHARS


def test_missing_database_degrades_gracefully(tmp_path):
    result = _read_aux_save_data(str(tmp_path / "does-not-exist.sav"))
    assert result == (None, set(), {})


def test_corrupt_database_degrades_gracefully(tmp_path):
    path = tmp_path / "corrupt.sav"
    path.write_bytes(b"this is definitely not a sqlite database at all")
    result = _read_aux_save_data(str(path))
    assert result[0] is None
    assert result[1] == set()
    assert result[2] == {}


def test_empty_database_returns_no_day_flags_or_properties(tmp_path):
    path = _make_db(tmp_path / "empty.sav")
    day, flags, props = _read_aux_save_data(path)
    assert day is None
    assert flags == set()
    assert props == {}


def _sample():
    path = os.environ.get("MEWGENICS_SAMPLE_SAV")
    if not path or not os.path.exists(path):
        pytest.skip("set MEWGENICS_SAMPLE_SAV to a .sav file to run save tests")
    return path


def test_session_exposes_save_properties_from_real_save():
    sess = Session(_sample())
    assert isinstance(sess.save_properties, dict)
    assert isinstance(sess.npc_progress_flags, set)
