"""Unit tests for SaveController (refactor step 1).

SaveController has no Qt dependency in its public surface — these tests run
without QApplication or a real save. They cover the historically bug-dense
bookkeeping: the generation-token queue, session state and watcher lifecycle.
"""

import time

import pytest

from mewgenics_overlay.ui.savecontroller import SaveController


class _FakeSession:
    """Duck-typed Session for partner scheduling."""

    def __init__(self):
        self.by_key = {}

    def rank_partners(self, cat, **kwargs):
        return []


def _drain_until(ctrl, predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        items = ctrl.drain()
        if items:
            if predicate(items):
                return items
            continue
        time.sleep(0.02)
    return ctrl.drain()


def test_empty_queue_and_session_state():
    ctrl = SaveController()
    assert ctrl.drain() == []
    assert ctrl.session is None
    fake = _FakeSession()
    ctrl.session = fake
    assert ctrl.session is fake
    ctrl.stop_watcher()          # no watcher yet -> must be a safe no-op


def test_token_bump_is_monotonic_and_invalidates():
    ctrl = SaveController()
    first = ctrl.bump_token()
    second = ctrl.bump_token()
    assert second == first + 1


def test_schedule_partners_posts_result_via_queue():
    ctrl = SaveController()
    cat = object()
    session = _FakeSession()
    session.by_key = {"k": cat}
    ctrl.session = session
    token = ctrl.schedule_partners(
        cat_key="k", max_rows=50, show_blocked=None,
        include_adventure=True, order="risk", stimulation=50.0)
    assert token is not None
    items = _drain_until(ctrl, lambda its: any(
        k == "partners" for _, k, _ in its))
    kinds = [k for tok, k, _payload in items if tok == token]
    assert "partners" in kinds, (items, kinds, token)


def test_schedule_partners_noop_without_session():
    ctrl = SaveController()
    assert ctrl.schedule_partners(
        cat_key=1, max_rows=10, show_blocked=None,
        include_adventure=True, order="risk", stimulation=50.0) is None
    assert ctrl.drain() == []


def test_schedule_reload_requires_path():
    ctrl = SaveController()
    token = ctrl.schedule_reload("")       # no path -> no job, token bumped
    assert token == 1
    assert ctrl.drain() == []
