"""``ui/assets.py`` AssetLoader: one-shot background gpak load + drain.

The loader has exactly three outcomes and they must stay distinguishable:

* **no resources.gpak on this machine** - a normal absent optional feature:
  no thread starts, nothing is logged above INFO, no status line, no error;
* **the gpak exists but fails to parse** - a compute failure: the worker
  records the exception, logs it with a traceback, and ``drain()`` reports a
  status line distinct from the no-gpak case;
* **the gpak reads but yields no usable tables** - also a compute failure:
  ``drain()`` leaves ``assets`` as ``None``, surfaces the same status line,
  and does **not** fire ``on_ready`` (there is nothing to refresh for);
* **a usable gpak** - ``drain()`` adopts the assets and fires ``on_ready``.

Hermetic: both ``locate_gpak`` and ``GameAssets`` are monkeypatched, so a
real ``resources.gpak`` is never opened. The load thread is replaced with an
inline runner so results land deterministically without sleeps. The fake
gpak path lives under ``tmp_path``.
"""

from __future__ import annotations

import logging
import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402
import threading  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui import assets as _assets  # noqa: E402
from mewgenics_overlay.ui.assets import AssetLoader  # noqa: E402

_LOG_NAME = "mewgenics_overlay.ui"


# ── fixtures / helpers ─────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def inline_threads(monkeypatch):
    """Replace ``threading.Thread`` with an inline runner that records starts."""
    started = []

    class _InlineThread:
        def __init__(self, target=None, name=None, daemon=None, **kwargs):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            started.append(self)
            if self.target is not None:
                self.target()

    monkeypatch.setattr(_assets, "threading",
                        SimpleNamespace(Thread=_InlineThread,
                                        Lock=threading.Lock))
    return started


@pytest.fixture
def gpak(tmp_path, monkeypatch):
    """A fake on-disk gpak path plus a recording locator.

    ``box["path"]`` is what ``locate_gpak`` returns; set it to ``None`` for the
    "not installed" case. No test ever reads the file itself.
    """
    path = tmp_path / "resources.gpak"
    path.write_bytes(b"not a real gpak")
    box = {"path": str(path)}
    calls = []

    def _locate():
        calls.append(True)
        return box["path"]

    monkeypatch.setattr(_assets, "locate_gpak", _locate)
    return SimpleNamespace(path=str(path), box=box, calls=calls)


@pytest.fixture
def fake_assets(monkeypatch):
    """Stand-in for ``GameAssets``; records the path it was built with."""
    class FakeGameAssets:
        ok = True              # usable by default
        raise_exc = None       # set to an exception to simulate a parse failure
        last_path = None

        def __init__(self, gpak_path):
            FakeGameAssets.last_path = gpak_path
            if FakeGameAssets.raise_exc is not None:
                raise FakeGameAssets.raise_exc
            self.gpak_path = gpak_path

    monkeypatch.setattr(_assets, "GameAssets", FakeGameAssets)
    return FakeGameAssets


@pytest.fixture
def make_loader(qapp):
    """Build loaders with status/ready recorders."""
    loaders = []

    def _make(*, with_ready=True):
        status = []
        ready = []
        loader = AssetLoader(
            on_status=status.append,
            on_ready=((lambda: ready.append(True)) if with_ready else None),
        )
        loaders.append(loader)
        return loader, status, ready

    yield _make
    for loader in loaders:
        loader.deleteLater()
    qapp.processEvents()


# ── 1. no gpak: optional feature merely absent ─────────────────────────────
def test_missing_gpak_is_silent_logs_info_and_starts_no_thread(
        make_loader, gpak, inline_threads, caplog):
    gpak.box["path"] = None
    caplog.set_level(logging.DEBUG, logger=_LOG_NAME)
    loader, status, ready = make_loader()

    loader.start()

    assert inline_threads == []            # no background work at all
    assert loader.assets is None
    assert loader._result is None          # no error recorded
    assert status == []                    # not a failure -> no status line
    assert ready == []
    assert "no resources.gpak found" in caplog.text
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_missing_gpak_drain_is_a_no_op(make_loader, gpak):
    gpak.box["path"] = None
    loader, status, ready = make_loader()
    loader.start()

    loader.drain()
    loader.drain()

    assert status == []
    assert ready == []
    assert loader.assets is None


def test_start_is_one_shot_before_any_thread(make_loader, gpak,
                                             inline_threads):
    gpak.box["path"] = None
    loader, _, _ = make_loader()

    loader.start()
    loader.start()

    assert len(gpak.calls) == 1            # the one-shot guard short-circuits


def test_start_is_one_shot_with_a_gpak(make_loader, gpak, inline_threads,
                                      fake_assets):
    loader, _, _ = make_loader()

    loader.start()
    loader.start()

    assert len(gpak.calls) == 1
    assert len(inline_threads) == 1
    assert fake_assets.last_path == gpak.path


def test_loaded_thread_is_a_named_daemon(make_loader, gpak, inline_threads,
                                         fake_assets):
    loader, _, _ = make_loader()

    loader.start()

    assert len(inline_threads) == 1
    assert inline_threads[0].name == "gpak-assets"
    assert inline_threads[0].daemon is True


def test_assets_is_none_before_any_load(make_loader):
    loader, _, _ = make_loader()

    assert loader.assets is None


# ── 2. gpak exists but fails to parse: a compute failure ───────────────────
def test_failed_parse_records_error_logs_and_reports_a_distinct_status(
        make_loader, gpak, inline_threads, fake_assets, caplog):
    fake_assets.raise_exc = ValueError("corrupt gpak table")
    caplog.set_level(logging.DEBUG, logger=_LOG_NAME)
    loader, status, ready = make_loader()

    loader.start()

    # worker ran and recorded the failure
    assert len(inline_threads) == 1
    assert loader._result is not None
    assert isinstance(loader._result.error, ValueError)
    assert loader._result.assets is None
    assert loader.assets is None
    assert "failed to read resources.gpak" in caplog.text
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    assert status == []                    # status is only emitted by drain()

    loader.drain()

    assert len(status) == 1
    assert status[0].startswith("⚠")
    assert "could not read resources.gpak" in status[0]
    assert "defect effect text unavailable" in status[0]
    assert "see the log" in status[0]
    assert ready == []                     # failure never fires the ready path
    assert loader.assets is None


def test_parse_failure_status_differs_from_the_no_gpak_case(
        make_loader, gpak, inline_threads, fake_assets):
    # no gpak
    gpak.box["path"] = None
    absent, absent_status, _ = make_loader()
    absent.start()
    absent.drain()

    # gpak present but broken
    gpak.box["path"] = gpak.path
    fake_assets.raise_exc = RuntimeError("unreadable")
    broken, broken_status, _ = make_loader()
    broken.start()
    broken.drain()

    assert absent_status == []
    assert len(broken_status) == 1


def test_failed_parse_result_is_consumed_once(make_loader, gpak,
                                              inline_threads, fake_assets):
    fake_assets.raise_exc = RuntimeError("boom")
    loader, status, _ = make_loader()
    loader.start()

    loader.drain()
    loader.drain()

    assert len(status) == 1
    assert loader._result is None


# ── 3. successful load: assets adopted, ready fired once ───────────────────
def test_successful_load_adopts_assets_and_fires_ready_once(
        make_loader, gpak, inline_threads, fake_assets):
    loader, status, ready = make_loader()

    loader.start()
    assert loader.assets is None           # not on the UI thread until drain
    assert fake_assets.last_path == gpak.path

    loader.drain()

    assert loader.assets is not None
    assert isinstance(loader.assets, fake_assets)
    assert status == []
    assert ready == [True]

    loader.drain()                         # second drain: result already gone
    assert ready == [True]
    assert loader.assets is not None


def test_ready_is_optional(make_loader, gpak, inline_threads, fake_assets):
    loader, status, _ = make_loader(with_ready=False)

    loader.start()
    loader.drain()                         # must not raise with on_ready=None

    assert loader.assets is not None
    assert status == []


def test_unusable_assets_surface_a_status_and_do_not_fire_ready(
        make_loader, gpak, inline_threads, fake_assets, caplog):
    # A readable-but-empty gpak is a compute failure, not a missing feature:
    # assets stay None, the same status line as a parse error is surfaced and
    # on_ready is withheld (the host must not refresh against nothing).
    fake_assets.ok = False
    caplog.set_level(logging.DEBUG, logger=_LOG_NAME)
    loader, status, ready = make_loader()

    loader.start()
    loader.drain()

    assert loader.assets is None
    assert len(status) == 1
    assert status[0].startswith("⚠")
    assert "could not read resources.gpak" in status[0]
    assert "defect effect text unavailable" in status[0]
    assert "see the log" in status[0]
    assert ready == []                     # nothing usable -> no ready path
    assert "yielded no usable asset tables" in caplog.text


def test_unusable_assets_status_matches_the_parse_failure_wording(
        make_loader, gpak, inline_threads, fake_assets):
    # Both compute failures share one status line so the wording cannot drift.
    fake_assets.raise_exc = RuntimeError("unreadable")
    broken, broken_status, _ = make_loader()
    broken.start()
    broken.drain()

    fake_assets.raise_exc = None
    fake_assets.ok = False
    empty, empty_status, empty_ready = make_loader()
    empty.start()
    empty.drain()

    assert broken_status == empty_status
    assert empty_ready == []
    assert empty.assets is None
