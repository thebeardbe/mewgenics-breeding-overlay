"""Core engine tests (headless — no Qt required).

The parser/genetics engine is vendored from the MIT-licensed
MewgenicsBreedingManager and tested here through this project's own API.

Sample saves are not committed to this repo; point MEWGENICS_SAMPLE_SAV at a
`.sav` file (any of Mewgenics' own saves, or a copy from upstream MBM's
`tools/saves/saves.zip`). Tests skip when no sample is configured.
"""

import os

import pytest

from mewgenics_overlay.core.session import Session


def _sample() -> str:
    path = os.environ.get("MEWGENICS_SAMPLE_SAV")
    if not path or not os.path.exists(path):
        pytest.skip("set MEWGENICS_SAMPLE_SAV to a .sav file to run save tests")
    return path


def test_parses_and_indexes():
    sess = Session(_sample())
    assert sess.data is not None
    assert len(sess.cats) > 0
    assert len(sess.alive) <= len(sess.cats)
    # every alive cat should resolve through by_key
    for c in sess.cats:
        assert sess.by_key[c.db_key] is c


def test_search_roundtrip():
    sess = Session(_sample())
    assert sess.search("") == []
    cat = sess.alive[0]
    hits = sess.search(cat.name[: max(1, len(cat.name) // 2)])
    assert any(c.db_key == cat.db_key for c in hits)


def test_rank_partners_shapes():
    sess = Session(_sample())
    cat = sess.alive[0]
    rows = sess.rank_partners(cat, max_partners=10, show_blocked=2)
    assert len(rows) >= 1
    ok_rows = [r for r in rows if r.compatible]
    blocked = [r for r in rows if not r.compatible]
    assert len(blocked) <= 2
    for r in ok_rows:
        assert 0.0 <= r.risk_pct <= 100.0
        assert r.game_compat > 0.05
        assert 0.0 <= r.expected_avg <= 7.0
        assert r.partner is not cat


def test_summary_fields():
    sess = Session(_sample())
    s = sess.summary(sess.alive[0])
    assert s.stat_sum == sum(s.base_stats.values())
    assert len(s.base_stats) == 7
    assert s.gender in ("male", "female", "?")
