"""``ReloadCoordinator`` (ui/reloader.py): watcher hop, token guard, drain pump.

The coordinator sits between the save watcher (its own thread), the
``SaveController`` worker queue and the UI thread. These tests never start a
real watcher or worker thread: a fake controller records the watcher wiring and
exposes ``push``/``bump_token`` so results can be injected, and the drain is
driven by calling ``ReloadCoordinator._on_poll()`` directly.

The coordinator is a ``QObject`` whose poll is a ``QTimer``, so the offscreen
``QApplication`` fixture from ``test_chrome.py`` is reused. No event loop is
spun and no timer is allowed to fire on its own; the fixture stops every timer
in teardown before processing events.
"""

from __future__ import annotations

import logging
import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui.reloader import POLL_MS, ReloadCoordinator  # noqa: E402

LOG_NAME = "mewgenics_overlay.ui.reloader"


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_coord(qapp):
    """Build coordinators on a fake save; stop their timers afterwards."""
    made = []

    def _make(save, **kwargs):
        coord = ReloadCoordinator(save, **kwargs)
        made.append(coord)
        return coord

    yield _make
    for coord in made:
        coord._poll.stop()
        coord.deleteLater()
    qapp.processEvents()


# ── fakes ──────────────────────────────────────────────────────────────────
class FakeSave:
    """Stand-in for SaveController: no threads, injectable results."""

    def __init__(self):
        self.session = None
        self._token = 0
        self._pending: list[tuple] = []
        self.start_watcher_calls: list[tuple[str, object]] = []
        self.stop_watcher_calls = 0
        self.scheduled_reloads: list[str] = []
        self.scheduled_partners: list[tuple] = []
        self.watcher_on_change = None
        self.drain_calls = 0

    @property
    def token(self) -> int:
        return self._token

    @property
    def pending(self) -> list[tuple]:
        return list(self._pending)

    def bump_token(self) -> int:
        self._token += 1
        return self._token

    def push(self, token: int, kind: str, payload) -> None:
        self._pending.append((token, kind, payload))

    def drain(self) -> list:
        self.drain_calls += 1
        items, self._pending = self._pending, []
        return items

    def start_watcher(self, path: str, on_change) -> None:
        self.start_watcher_calls.append((path, on_change))
        self.watcher_on_change = on_change

    def stop_watcher(self) -> None:
        self.stop_watcher_calls += 1

    def schedule_reload(self, path: str):
        self.scheduled_reloads.append(path)
        return self.bump_token()

    def schedule_partners(self, *args):
        self.scheduled_partners.append(args)
        return self.bump_token()


class FakeSession:
    """Minimal session: ``adopt_session`` only reads ``len(sess.alive)``."""

    def __init__(self, alive=()):
        self.alive = list(alive)


def make_recorder():
    """Collect (kind, payload) events plus the live status strings."""
    events: list[tuple] = []
    statuses: list[str] = []
    return events, statuses


# ── constructor / wiring ───────────────────────────────────────────────────
def test_constructor_starts_the_poll_at_the_shared_interval(make_coord):
    fake = FakeSave()

    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: None,
                       on_status=lambda t: None)

    assert POLL_MS == 120
    assert coord._poll.interval() == POLL_MS
    assert coord._poll.isActive() is True


def test_start_watches_the_path_and_remembers_it_for_reloads(make_coord):
    fake = FakeSave()
    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: None,
                       on_status=lambda t: None)

    coord.start("campaign.sav")
    coord.request_reload()

    assert fake.start_watcher_calls[0][0] == "campaign.sav"
    assert fake.start_watcher_calls[0][1] is not None
    assert fake.scheduled_reloads == ["campaign.sav"]


def test_watcher_change_callback_drives_the_reload_path(make_coord):
    # The watcher hands its callback over as ``save_changed.emit``; calling it
    # here simulates the watcher thread hop without spawning a thread.
    fake = FakeSave()
    _, statuses = make_recorder()
    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: None,
                       on_status=statuses.append)

    coord.start("campaign.sav")
    fake.watcher_on_change()

    assert statuses == ["save changed - reloading…"]
    assert fake.scheduled_reloads == ["campaign.sav"]


def test_request_reload_without_a_path_is_a_noop(make_coord):
    fake = FakeSave()
    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: None,
                       on_status=lambda t: None)

    coord.request_reload()

    assert fake.scheduled_reloads == []
    assert fake.token == 0


def test_schedule_partners_forwards_the_view_settings(make_coord):
    fake = FakeSave()
    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: None,
                       on_status=lambda t: None)

    coord.schedule_partners(42, 100, 0, True, "risk", 50.0)

    assert fake.scheduled_partners == [(42, 100, 0, True, "risk", 50.0)]


# ── token guard: results are never adopted across a save switch ────────────
def test_stale_session_result_is_dropped_after_a_newer_token(make_coord):
    fake = FakeSave()
    events, _ = make_recorder()
    coord = make_coord(fake,
                       on_session=lambda s: events.append(("session", s)),
                       on_partners=lambda k, r: events.append(("partners", k, r)),
                       on_status=lambda t: None)
    old = FakeSession(alive=["a"])
    old_token = fake.bump_token()         # a previous schedule assigned token 1
    fake.push(old_token, "session", old)  # in flight from the previous save
    fake.bump_token()                     # a newer schedule: token 1 is stale

    coord._on_poll()

    assert events == []
    assert fake.session is None


def test_stale_partner_result_is_dropped_after_a_newer_token(make_coord):
    fake = FakeSave()
    events, _ = make_recorder()
    coord = make_coord(fake,
                       on_session=lambda s: events.append(("session", s)),
                       on_partners=lambda k, r: events.append(("partners", k, r)),
                       on_status=lambda t: None)
    fake.push(1, "partners", (7, [("row",)]))
    fake.bump_token()
    fake.bump_token()

    coord._on_poll()

    assert events == []


def test_stale_session_error_is_dropped_after_a_newer_token(make_coord):
    fake = FakeSave()
    _, statuses = make_recorder()
    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: None,
                       on_status=statuses.append)
    fake.push(1, "session_error", "old corrupt save")
    fake.bump_token()
    fake.bump_token()

    coord._on_poll()

    assert statuses == []


# ── adoption + ordering ────────────────────────────────────────────────────
def test_current_token_session_result_is_adopted_status_written_first(
        make_coord):
    fake = FakeSave()
    sess = FakeSession(alive=["a", "b", "c"])
    fake.push(0, "session", sess)
    order: list[tuple[str, object]] = []
    coord = make_coord(fake,
                       on_session=lambda s: order.append(("session", s)),
                       on_partners=lambda k, r: None,
                       on_status=lambda t: order.append(("status", t)))

    coord._on_poll()

    assert order == [("status", "3 cats in house/on adventures"),
                     ("session", sess)]
    assert fake.session is sess


def test_empty_session_adopts_and_reports_zero_cats(make_coord):
    fake = FakeSave()
    sess = FakeSession(alive=[])
    fake.push(0, "session", sess)
    seen = []
    coord = make_coord(fake,
                       on_session=lambda s: seen.append(("session", s)),
                       on_partners=lambda k, r: None,
                       on_status=lambda t: seen.append(("status", t)))

    coord._on_poll()

    assert seen == [("status", "0 cats in house/on adventures"),
                    ("session", sess)]


def test_adopt_session_none_clears_and_reports_no_save_loaded(make_coord):
    fake = FakeSave()
    fake.session = FakeSession(alive=["a"])
    seen = []
    coord = make_coord(fake,
                       on_session=lambda s: seen.append(("session", s)),
                       on_partners=lambda k, r: None,
                       on_status=lambda t: seen.append(("status", t)))

    coord.adopt_session(None)

    assert fake.session is None
    assert seen == [("status", "no save loaded"), ("session", None)]


def test_adopt_session_accepts_unicode_names(make_coord):
    fake = FakeSave()
    sess = FakeSession(alive=["Mîlø", "😺"])
    seen = []
    coord = make_coord(fake,
                       on_session=lambda s: seen.append(("session", s)),
                       on_partners=lambda k, r: None,
                       on_status=lambda t: seen.append(("status", t)))

    coord.adopt_session(sess)

    assert seen[0] == ("status", "2 cats in house/on adventures")
    assert seen[1] == ("session", sess)


# ── error paths ────────────────────────────────────────────────────────────
def test_failed_session_parse_surfaces_status_and_skips_session_callback(
        make_coord, caplog):
    fake = FakeSave()
    sessions = []
    statuses: list[str] = []
    coord = make_coord(fake,
                       on_session=lambda s: sessions.append(s),
                       on_partners=lambda k, r: None,
                       on_status=statuses.append)
    fake.push(0, "session_error", "database is locked")

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        coord._on_poll()

    assert sessions == []
    assert statuses == ["⚠ could not read save - keeping the previous cats"]
    assert "save reload failed: database is locked" in caplog.text


def test_current_token_partner_rows_are_handed_to_the_callback(make_coord):
    fake = FakeSave()
    rows = [("row-a",), ("row-b",)]
    fake.push(0, "partners", (7, rows))
    seen = []
    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: seen.append((k, r)),
                       on_status=lambda t: None)

    coord._on_poll()

    assert seen == [(7, rows)]


def test_partner_error_sets_a_friendly_status_without_crashing(make_coord):
    fake = FakeSave()
    statuses: list[str] = []
    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: None,
                       on_status=statuses.append)
    fake.push(0, "partners_error", "ZeroDivisionError: boom")

    coord._on_poll()   # must not raise at the host

    assert statuses == ["⚠ partner scoring failed - see the log; "
                        "try selecting another cat"]


def test_unknown_result_kind_is_ignored(make_coord):
    fake = FakeSave()
    events, statuses = make_recorder()
    coord = make_coord(fake,
                       on_session=lambda s: events.append(("session", s)),
                       on_partners=lambda k, r: events.append(("partners", k, r)),
                       on_status=statuses.append)
    fake.push(0, "future_kind", object())

    coord._on_poll()   # must be inert

    assert events == []
    assert statuses == []


def test_session_change_logs_and_warns_when_reload_raises(make_coord, caplog):
    class ExplodingSave(FakeSave):
        def schedule_reload(self, path):
            raise RuntimeError("watcher gone")

    fake = ExplodingSave()
    statuses: list[str] = []
    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: None,
                       on_status=statuses.append)
    coord.start("campaign.sav")

    with caplog.at_level(logging.ERROR, logger=LOG_NAME):
        coord.on_save_changed()   # must not raise

    assert statuses == [
        "save changed - reloading…",
        "⚠ save changed but reload failed - see the log",
    ]
    assert "save-change handler failed" in caplog.text


# ── on_tick ordering (gpak assets collected before the drain) ──────────────
def test_on_tick_runs_before_the_result_drain(make_coord):
    fake = FakeSave()
    sess = FakeSession(alive=["a"])
    fake.push(0, "session", sess)
    order: list[str] = []
    coord = make_coord(fake,
                       on_session=lambda s: order.append("session"),
                       on_partners=lambda k, r: None,
                       on_status=lambda t: None,
                       on_tick=lambda: (order.append("tick"),
                                        order.append(f"pending={len(fake.pending)}")))

    coord._on_poll()

    # The tick saw the result still queued; only then was it drained.
    assert order == ["tick", "pending=1", "session"]
    assert fake.session is sess


def test_poll_works_without_an_on_tick_hook(make_coord):
    fake = FakeSave()
    sess = FakeSession(alive=["a"])
    fake.push(0, "session", sess)
    seen = []
    coord = make_coord(fake, on_session=seen.append,
                       on_partners=lambda k, r: None,
                       on_status=lambda t: None)

    coord._on_poll()

    assert seen == [sess]


def test_drain_clears_the_queue_so_a_second_poll_has_nothing(
        make_coord):
    fake = FakeSave()
    sess = FakeSession(alive=["a"])
    fake.push(0, "session", sess)
    seen = []
    coord = make_coord(fake, on_session=seen.append,
                       on_partners=lambda k, r: None,
                       on_status=lambda t: None)

    coord._on_poll()
    coord._on_poll()

    assert seen == [sess]
    assert fake.drain_calls == 2
    assert fake.pending == []


# ── lifecycle ──────────────────────────────────────────────────────────────
def test_stop_stops_the_poll_timer_and_the_watcher(make_coord):
    fake = FakeSave()
    coord = make_coord(fake, on_session=lambda s: None,
                       on_partners=lambda k, r: None,
                       on_status=lambda t: None)
    coord.start("campaign.sav")
    assert coord._poll.isActive() is True

    coord.stop()

    assert coord._poll.isActive() is False
    assert fake.stop_watcher_calls == 1
