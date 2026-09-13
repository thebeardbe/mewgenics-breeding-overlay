"""``ui/app.py``'s ``LiveSaveScanner``: scan off the UI thread, deliver on it.

The scanner is the seam that keeps the periodic follow scan off the UI thread.
These tests drive the real class with the detection functions stubbed, so no
real ``/proc`` is read, and they wait on the queued ``scanned`` signal through
an event loop rather than by sleeping.

Split out of ``test_app_bootstrap.py`` (which owns ``main``'s startup wiring)
to keep that module under the file-size budget; the feature and the seam live
here.
"""

from __future__ import annotations

import logging
import os
import threading

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui import app  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """A real QApplication: the queued signal is delivered on its thread."""
    app_ = QApplication.instance() or QApplication([])
    yield app_


def _join_scan_workers():
    """Wait for in-flight scanner workers so none outlives its QObject.

    A worker that is still emitting when its scanner is garbage-collected
    raises ``RuntimeError: Signal source has been deleted`` from the thread.
    Joining keeps the test's teardown deterministic (no sleeps involved).
    """
    for thread in threading.enumerate():
        if thread.name == "livesave-scan":
            thread.join(timeout=5)


def _run_real_scanner(qapp, monkeypatch, find, present):
    """Drive one real ``LiveSaveScanner`` request to completion.

    The scanner is created on the test (UI) thread; ``request`` starts its
    worker, and the queued ``scanned`` signal is drained by the event loop.
    Returns ``(found, present, handler_thread)`` triples.
    """
    monkeypatch.setattr(app.livesave, "find_live_save", find)
    monkeypatch.setattr(app.livesave, "game_process_running", present)
    scanner = app.LiveSaveScanner()
    results = []
    loop = QEventLoop()
    scanner.set_handler(lambda found, is_present: (
        results.append((found, is_present, threading.current_thread())),
        loop.quit()))
    QTimer.singleShot(0, scanner.request)
    QTimer.singleShot(10000, loop.quit)          # guard, not a wait
    loop.exec()
    _join_scan_workers()
    return results


def test_the_scanner_runs_off_thread_and_delivers_on_the_ui_thread(
        qapp, monkeypatch):
    main_thread = threading.current_thread()
    worker_threads = []

    def fake_find():
        worker_threads.append(threading.current_thread())
        return "/steam/root/game.sav"

    results = _run_real_scanner(qapp, monkeypatch, fake_find, lambda: True)

    assert results
    assert results[0][:2] == ("/steam/root/game.sav", True)
    # The scan ran on a worker thread; the result was applied on the UI thread.
    assert worker_threads and all(t is not main_thread for t in worker_threads)
    assert results[0][2] is main_thread


def test_the_scanner_reports_no_save_when_the_scan_raises(qapp, monkeypatch,
                                                          caplog):
    def boom():
        raise OSError("proc went away")

    monkeypatch.setattr(app.livesave, "find_live_save", boom)
    monkeypatch.setattr(app.livesave, "game_process_running", lambda: True)
    scanner = app.LiveSaveScanner()
    results = []
    loop = QEventLoop()
    scanner.set_handler(lambda found, present: (
        results.append((found, present)), loop.quit()))
    QTimer.singleShot(0, scanner.request)
    QTimer.singleShot(10000, loop.quit)
    with caplog.at_level(logging.ERROR, logger="mewgenics_overlay.app"):
        loop.exec()
    _join_scan_workers()

    # A failed scan is never a crash and never a phantom save.
    assert results == [(None, False)]
    assert any("scan failed" in r.getMessage() for r in caplog.records)
