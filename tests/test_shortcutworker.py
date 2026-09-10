"""``ui/shortcutworker.py``: the background desktop-shortcut action runner.

The worker runs an injected action on a daemon thread and hops the
``(ok, message)`` result back to the UI thread with ``QTimer.singleShot(0,
self, ...)`` under a token guard, so a result from a superseded request never
overwrites a newer state. These tests keep the real QObject/QTimer shape and
drive delivery with an event loop (no sleeps decide a test): the
token/cancel/exception cases use an inline-thread seam for determinism, and
two tests exercise the real worker thread and wait on its callback.
"""

from __future__ import annotations

import logging
import os
import threading
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QThread, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui import shortcutworker  # noqa: E402
from mewgenics_overlay.ui.shortcutworker import ShortcutWorker  # noqa: E402

_LOG_NAME = "mewgenics_overlay.shortcutworker"


# ── fixtures / helpers ─────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _InlineThread:
    """Run a worker target inline so the queued UI callback is deterministic."""

    def __init__(self, target=None, name=None, daemon=None, **kwargs):
        self._target = target
        self.name = name
        self.daemon = daemon

    def start(self):
        if self._target is not None:
            self._target()


@pytest.fixture
def inline_threads(monkeypatch):
    monkeypatch.setattr(shortcutworker, "threading",
                        SimpleNamespace(Thread=_InlineThread))


class _ManualThread:
    """A thread seam that queues its target for the test to release."""

    def __init__(self, queue, target):
        self._queue = queue
        self._target = target

    def start(self):
        self._queue.append(self._target)


class _ControlledThreads:
    """Explicitly-released thread seam for the serialization tests.

    ``started`` counts the worker threads spawned so far and ``queue`` holds
    the targets the test has not run yet; ``run_next`` runs the oldest. That
    makes "a second action only starts once the first one finished"
    observable without timing, sleeps or real threads (which would be
    flaky).
    """

    def __init__(self):
        self.started = []
        self.queue = []

    def namespace(self):
        def spawn(target=None, name=None, daemon=None, **kwargs):
            self.started.append(target)
            return _ManualThread(self.queue, target)
        return SimpleNamespace(Thread=spawn)

    def run_next(self):
        assert self.queue, "no worker target is queued to run"
        target = self.queue.pop(0)
        target()


@pytest.fixture
def controlled_threads(monkeypatch):
    threads = _ControlledThreads()
    monkeypatch.setattr(shortcutworker, "threading", threads.namespace())
    return threads


def _wait_until(qapp, predicate, timeout_ms=3000):
    """Event-driven wait for *predicate*; bounded, never sleeps on purpose."""
    if predicate():
        return True
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(5)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    poll.start()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    poll.stop()
    return bool(predicate())


# ── 1. delivery ────────────────────────────────────────────────────────────
def test_result_is_delivered_on_the_ui_thread(qapp):
    worker = ShortcutWorker(qapp)
    seen = []

    def on_done(ok, message):
        seen.append((ok, message, QThread.currentThread() is qapp.thread()))

    worker.run(lambda: (True, "installed"), on_done)

    assert _wait_until(qapp, lambda: seen) is True
    assert seen == [(True, "installed", True)]


def test_action_runs_off_the_ui_thread(qapp):
    worker = ShortcutWorker(qapp)
    seen = []
    loop = QEventLoop()

    def action():
        seen.append(QThread.currentThread() is not qapp.thread())
        return True, ""

    def on_done(ok, message):
        seen.append("done")
        loop.quit()

    worker.run(action, on_done)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()

    assert seen == [True, "done"]


def test_result_is_delivered_once(qapp, inline_threads):
    worker = ShortcutWorker()
    seen = []

    worker.run(lambda: (True, "only"), lambda ok, m: seen.append((ok, m)))

    qapp.processEvents()
    for _ in range(20):
        qapp.processEvents()

    assert seen == [(True, "only")]


# ── 2. token guard / cancel ────────────────────────────────────────────────
def test_run_returns_an_increasing_token(qapp, inline_threads):
    worker = ShortcutWorker()

    first = worker.run(lambda: (True, ""), lambda ok, m: None)
    second = worker.run(lambda: (True, ""), lambda ok, m: None)

    assert second > first


def test_a_superseded_result_is_dropped(qapp, inline_threads):
    worker = ShortcutWorker()
    older, newer = [], []

    worker.run(lambda: (True, "older"), lambda ok, m: older.append((ok, m)))
    worker.run(lambda: (True, "newer"), lambda ok, m: newer.append((ok, m)))

    # The worker serializes, so the newer request stays pending until the
    # older action's result has been processed; that stale result is then
    # dropped instead of delivered.
    assert _wait_until(qapp, lambda: newer) is True

    assert older == []
    assert newer == [(True, "newer")]


def test_actions_run_one_at_a_time(qapp, controlled_threads):
    worker = ShortcutWorker()
    events = []

    def action(tag):
        def run():
            events.append(f"{tag}:run")
            return True, tag
        return run

    worker.run(action("A"), lambda ok, m: events.append("A:done"))
    worker.run(action("B"), lambda ok, m: events.append("B:done"))

    # B is only pending: exactly one worker thread (A) has been started.
    assert len(controlled_threads.started) == 1

    controlled_threads.run_next()          # A runs, its finish is queued
    assert events == ["A:run"]

    # B must not start until A's result was processed, so two subprocess
    # runs can never overlap.
    assert _wait_until(
        qapp, lambda: len(controlled_threads.started) == 2) is True
    assert events == ["A:run"]              # A's result was dropped as stale
    assert len(controlled_threads.queue) == 1

    controlled_threads.run_next()          # B runs
    assert _wait_until(qapp, lambda: "B:done" in events) is True
    assert events == ["A:run", "B:run", "B:done"]


def test_newest_pending_request_wins(qapp, controlled_threads):
    worker = ShortcutWorker()
    events = []

    def action(tag):
        def run():
            events.append(f"{tag}:run")
            return True, tag
        return run

    def done(tag):
        return lambda ok, m: events.append(f"{tag}:done")

    worker.run(action("A"), done("A"))     # starts now
    worker.run(action("B"), done("B"))     # pending
    worker.run(action("C"), done("C"))     # replaces B as the newest pending

    assert len(controlled_threads.started) == 1
    controlled_threads.run_next()           # A runs
    assert _wait_until(
        qapp, lambda: len(controlled_threads.started) == 2) is True

    # B was replaced by C, so only A and C ever ran.
    assert events == ["A:run"]
    assert len(controlled_threads.queue) == 1
    controlled_threads.run_next()           # C runs
    assert _wait_until(qapp, lambda: "C:done" in events) is True
    assert events == ["A:run", "C:run", "C:done"]


def test_cancel_drops_the_pending_result(qapp, inline_threads):
    worker = ShortcutWorker()
    seen = []

    worker.run(lambda: (True, "gone"), lambda ok, m: seen.append((ok, m)))
    worker.cancel()

    qapp.processEvents()

    assert seen == []


def test_cancel_clears_a_pending_request(qapp, inline_threads):
    worker = ShortcutWorker()
    ran, seen = [], []

    def first():
        ran.append("first")
        return True, "first"

    def second():
        ran.append("second")
        return True, "second"

    worker.run(first, lambda ok, m: seen.append((ok, m)))
    worker.run(second, lambda ok, m: seen.append((ok, m)))
    worker.cancel()

    for _ in range(20):
        qapp.processEvents()

    assert ran == ["first"]           # the pending action was cleared
    assert seen == []


def test_run_after_cancel_delivers_again(qapp, inline_threads):
    worker = ShortcutWorker()
    seen = []

    worker.run(lambda: (True, "first"), lambda ok, m: seen.append((ok, m)))
    worker.cancel()
    worker.run(lambda: (True, "second"), lambda ok, m: seen.append((ok, m)))

    assert _wait_until(qapp, lambda: seen) is True

    assert seen == [(True, "second")]


# ── 3. action failure ──────────────────────────────────────────────────────
def test_action_exception_is_reported_and_logged(qapp, inline_threads,
                                                 caplog):
    worker = ShortcutWorker()
    seen = []

    def boom():
        raise RuntimeError("nope")

    with caplog.at_level(logging.ERROR, logger=_LOG_NAME):
        worker.run(boom, lambda ok, m: seen.append((ok, m)))
        qapp.processEvents()

    assert seen and seen[0][0] is False
    assert "unexpectedly" in seen[0][1]
    assert any("desktop-shortcut action failed" in record.message
               for record in caplog.records)


def test_a_returned_failure_message_is_passed_through(qapp, inline_threads):
    worker = ShortcutWorker()
    seen = []

    worker.run(lambda: (False, "schema missing"),
               lambda ok, m: seen.append((ok, m)))
    qapp.processEvents()

    assert seen == [(False, "schema missing")]


# ── 4. deleted worker Qt object ──────────────────────────────────────────
class _DeletedTimer:
    """A ``QTimer`` whose ``singleShot`` fails like a deleted C++ object."""

    @staticmethod
    def singleShot(*args, **kwargs):
        raise RuntimeError(
            "Internal C++ object (ShortcutWorker) already deleted.")


def test_deleted_worker_object_does_not_raise_or_deliver(
        qapp, inline_threads, monkeypatch, caplog):
    # The settings tab can close while an action runs, deleting the worker's
    # Qt object; scheduling the result on it then raises from the daemon
    # thread. That must be swallowed (the result is dropped), not escape.
    worker = ShortcutWorker()
    seen = []
    monkeypatch.setattr(shortcutworker, "QTimer", _DeletedTimer)

    with caplog.at_level(logging.DEBUG, logger=_LOG_NAME):
        worker.run(lambda: (True, "installed"),
                   lambda ok, m: seen.append((ok, m)))

    assert seen == []
    assert any("dropping a desktop-shortcut result" in record.message
               for record in caplog.records)


def test_deleted_worker_result_never_escapes_the_daemon_thread(
        qapp, monkeypatch, caplog):
    # Regression for the real daemon thread: the RuntimeError must not reach
    # ``threading.excepthook`` (which would print an unhandled traceback) and
    # must not fire the callback.
    worker = ShortcutWorker()
    seen = []
    escaped = []
    monkeypatch.setattr(shortcutworker, "QTimer", _DeletedTimer)

    real_hook = threading.excepthook
    monkeypatch.setattr(threading, "excepthook",
                        lambda args: escaped.append(args.exc_value))

    with caplog.at_level(logging.DEBUG, logger=_LOG_NAME):
        worker.run(lambda: (True, "installed"),
                   lambda ok, m: seen.append((ok, m)))
        assert _wait_until(
            qapp,
            lambda: "dropping a desktop-shortcut result" in caplog.text,
            timeout_ms=2000) is True

    assert escaped == []
    assert seen == []
