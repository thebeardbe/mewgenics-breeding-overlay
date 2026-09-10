"""UpdateNotice: the hidden update-available button and its interval gate.

``mewgenics_overlay.ui.updatenotice.UpdateNotice`` was extracted out of
``PaletteWindow`` (god-file split, step 4) and now takes the live settings
dict plus a "persist it" callable. These tests drive the widget with a plain
dict and a recording save callback, so no ``PaletteWindow`` (save, watcher,
timers) is needed and no network is touched: the module's
``latest_release`` helper is always monkeypatched.

The real ``start_check`` runs its fetch on a ``threading.Thread`` and hops the
result back to the UI thread with ``QTimer.singleShot(0, receiver, cb)``.
Tests keep that shape but replace the thread with an inline runner, so a
``qapp.processEvents()`` drains the queued hop deterministically (no sleeps,
no waiting on a background thread).

Visibility: the button is created hidden (``setVisible(False)`` in
``__init__``), so it is parented to a shown host for the whole test. A child
of a shown parent reports ``isVisible()`` True/False exactly as its explicit
visibility changes; calling ``show()`` on the button directly would override
the initial hide and make the "hidden by default" assertion meaningless.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402
import webbrowser  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mewgenics_overlay import __version__  # noqa: E402
from mewgenics_overlay.ui import update_check as _updates  # noqa: E402
from mewgenics_overlay.ui import updatenotice as _un  # noqa: E402
from mewgenics_overlay.ui.updatenotice import UpdateNotice  # noqa: E402

_LOCAL = _updates.parse_version(__version__)
_NEWER = (_LOCAL[0], _LOCAL[1], _LOCAL[2] + 1)
_NEWER_LABEL = "v" + ".".join(str(x) for x in _NEWER)
_RELEASE_URL = "https://example.invalid/releases/tag/v999"


# ── fakes / helpers ────────────────────────────────────────────────────────
class _SyncThread:
    """Runs ``Thread(target=...)`` inline: no background thread in tests."""

    def __init__(self, target=None, name=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


class _Fetch:
    """Records calls to the module's release-fetch helper."""

    def __init__(self, result=None):
        self.calls = 0
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.result


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_notice(qapp):
    """Build an UpdateNotice parented to a shown host; tear everything down."""
    notices = []
    hosts = []

    def _make(settings=None):
        settings = {} if settings is None else dict(settings)
        saves = []
        host = QWidget()
        host.show()
        notice = UpdateNotice(settings, lambda: saves.append(True), host)
        notices.append(notice)
        hosts.append(host)
        qapp.processEvents()
        return notice, settings, saves

    yield _make
    for notice in notices:
        notice.close()
        notice.deleteLater()
    for host in hosts:
        host.hide()
        host.close()
        host.deleteLater()
    qapp.processEvents()


@pytest.fixture
def sync_threads(monkeypatch):
    """Replace the fetch thread with an inline runner."""
    monkeypatch.setattr(_un, "threading",
                        SimpleNamespace(Thread=_SyncThread))


@pytest.fixture
def fetch(monkeypatch):
    """Never hit the network: stub the release fetch helper."""
    box = _Fetch()
    monkeypatch.setattr(_un._updates, "latest_release", box)
    return box


# ── 1. default state ───────────────────────────────────────────────────────
def test_notice_starts_hidden_and_silent(make_notice):
    notice, settings, saves = make_notice()

    assert not notice.isVisible()
    assert notice.text() == ""
    assert notice._url == ""
    assert settings == {}
    assert saves == []


# ── 2. opt-out and interval gating ─────────────────────────────────────────
def test_opt_out_skips_fetch_and_stamp(make_notice, sync_threads, fetch):
    notice, settings, saves = make_notice({"check_for_updates": False})

    notice.start_check()

    assert fetch.calls == 0
    assert "last_update_check" not in settings
    assert saves == []
    assert not notice.isVisible()


def test_opt_out_truthy_value_runs_the_check(make_notice, sync_threads, fetch):
    notice, settings, saves = make_notice({"check_for_updates": True})

    notice.start_check()

    assert fetch.calls == 1
    assert saves == [True]


def test_recent_check_within_interval_is_skipped(make_notice, sync_threads,
                                                 fetch):
    stamp = time.time()
    notice, settings, saves = make_notice({"last_update_check": stamp})

    notice.start_check()

    assert fetch.calls == 0
    assert settings["last_update_check"] == stamp   # untouched
    assert saves == []


def test_stale_check_fetches_and_stamps_now(make_notice, sync_threads, fetch):
    stale = time.time() - _updates.CHECK_INTERVAL - 1.0
    notice, settings, saves = make_notice({"last_update_check": stale})

    before = time.time()
    notice.start_check()
    after = time.time()

    assert fetch.calls == 1
    assert before <= settings["last_update_check"] <= after
    assert saves == [True]


def test_first_ever_check_runs_when_key_missing(make_notice, sync_threads,
                                                fetch):
    notice, settings, saves = make_notice()

    notice.start_check()

    assert fetch.calls == 1
    assert "last_update_check" in settings
    assert saves == [True]


def test_exactly_at_the_interval_boundary_is_due(make_notice, sync_threads,
                                                 fetch):
    # ``due`` returns now - last >= CHECK_INTERVAL, so the exact boundary runs.
    oldest_due = time.time() - _updates.CHECK_INTERVAL
    notice, settings, saves = make_notice({"last_update_check": oldest_due})

    notice.start_check()

    assert fetch.calls == 1


# ── 3. newer vs older/equal release ────────────────────────────────────────
def test_newer_release_shows_the_button_with_expected_text(
        make_notice, sync_threads, fetch, qapp):
    fetch.result = (_NEWER, _RELEASE_URL)
    notice, settings, saves = make_notice()

    notice.start_check()
    qapp.processEvents()

    assert notice.isVisible()
    assert _NEWER_LABEL in notice.text()
    assert "available" in notice.text()
    assert notice._url == _RELEASE_URL


def test_newer_release_via_show_available_sets_url(make_notice):
    notice, _, _ = make_notice()

    notice._show_update_available((_NEWER, _RELEASE_URL))

    assert notice.isVisible()
    assert notice._url == _RELEASE_URL


@pytest.mark.parametrize("remote", [
    _LOCAL,          # equal
    (0, 0, 1),       # older
    (0, 1, 0),       # older minor
    (0,),            # junk-ish
])
def test_equal_or_older_release_is_ignored(make_notice, remote):
    notice, _, _ = make_notice()

    notice._show_update_available((remote, _RELEASE_URL))

    assert not notice.isVisible()
    assert notice._url == ""
    assert notice.text() == ""


def test_none_result_is_ignored(make_notice):
    notice, _, _ = make_notice()

    notice._show_update_available(None)

    assert not notice.isVisible()
    assert notice._url == ""


def test_start_check_with_no_result_stays_hidden(make_notice, sync_threads,
                                                 fetch, qapp):
    fetch.result = None
    notice, _, _ = make_notice()

    notice.start_check()
    qapp.processEvents()

    assert not notice.isVisible()
    assert fetch.calls == 1


# ── 4. click opens the release page (never a real browser) ─────────────────
def test_click_opens_the_stored_release_url(make_notice, monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    notice, _, _ = make_notice()
    notice._show_update_available((_NEWER, _RELEASE_URL))

    notice.click()

    assert opened == [_RELEASE_URL]


def test_click_without_a_result_opens_the_releases_page(make_notice,
                                                        monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    notice, _, _ = make_notice()

    notice.click()

    assert opened == [_updates.RELEASES_URL]


def test_constructing_the_notice_opens_nothing(make_notice, monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)

    make_notice()

    assert opened == []
