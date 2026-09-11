"""``ui/raisewindow.py``: the Hyprland-only compositor focus fallback.

Qt's ``raise_``/``activateWindow`` are only requests, so on Hyprland the
overlay nudges the compositor with ``hyprctl``. The module is Qt-free and
takes its subprocess runner, its ``which`` lookup, the pid, the environment
and the retry ``sleep`` as injectable parameters, so every test here drives
the whole flow without a compositor, a real ``hyprctl``, a subprocess or a
real wait.

Covered here:

* session detection and the quiet no-op off Hyprland;
* the happy path order (clients -> focus -> bring-to-top);
* the client preference among several windows of the same pid;
* the one-shot lookup retry after ``FIRST_LOOKUP_RETRY_DELAY_S``;
* the single whole-attempt :data:`FOCUS_BUDGET_S` that every call and the
  retry are charged against;
* the default runner's concrete-failure logging;
* an injected runner that *raises* (on the first call and on a later one)
  being contained and turned into ``False`` instead of unwinding;
* every failure returning ``False`` without raising.

The last section pins the integration point: ``WindowController.engage()``
must reach the fallback (after the Qt show/raise/activate calls) and must not
care about its result, and off Hyprland it must not spawn anything at all.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from types import SimpleNamespace

# Must be set before the first QApplication is constructed (integration part).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from mewgenics_overlay.ui import raisewindow  # noqa: E402

LOG_NAME = "mewgenics_overlay.ui.raisewindow"

try:  # pragma: no cover - exercised by the environment, not by assertions
    from PySide6.QtWidgets import QApplication, QWidget
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

requires_qt = pytest.mark.skipif(not HAVE_QT, reason="PySide6 not installed")


# ── fakes / helpers ────────────────────────────────────────────────────────
class FakeRunner:
    """Records every argv; returns queued results (default: exit 0, no out).

    The default ``focus_window`` path hands the module's ``_run`` a timeout as
    a second argument, so the callable accepts and ignores extras; the
    injected-runner path only ever passes ``argv``.
    """

    def __init__(self, results=None):
        self.calls: list[list[str]] = []
        self._results = list(results or [])

    def __call__(self, argv, *args, **kwargs):
        self.calls.append([str(a) for a in argv])
        if self._results:
            return self._results.pop(0)
        return 0, ""


class FakeSleep:
    """Records ``sleep`` calls without ever waiting in real time."""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds):
        self.calls.append(seconds)


class FakeClock:
    """A monotonic clock the test drives by hand."""

    def __init__(self, now=1000.0):
        self.now = float(now)

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _patch_clock(monkeypatch, clock):
    """Point ``raisewindow``'s ``time`` at *clock*; no real waiting."""
    monkeypatch.setattr(
        raisewindow, "time",
        SimpleNamespace(monotonic=clock.monotonic, sleep=clock.advance))
    return clock


def _which(mapping):
    """``shutil.which`` stand-in: only the mapped tool names exist."""
    return lambda name: mapping.get(name)


def _client(pid, address, **fields):
    entry = {"pid": pid, "address": address}
    entry.update(fields)
    return entry


def _clients_reply(*pids, address_prefix="0x"):
    """A ``hyprctl clients -j`` payload with one client per pid."""
    return json.dumps([
        _client(pid, f"{address_prefix}{pid:04x}", **{"class": "x"})
        for pid in pids
    ])


def _hypr_env():
    return {raisewindow.HYPRLAND_ENV: "test-signature"}


HYPRCTL_PATH = "/usr/bin/hyprctl"


# ── 1. session detection ───────────────────────────────────────────────────
def test_is_hyprland_is_true_when_the_signature_is_set():
    assert raisewindow.is_hyprland(_hypr_env()) is True


@pytest.mark.parametrize("env", [
    {},
    {raisewindow.HYPRLAND_ENV: ""},          # exported but empty
    {"XDG_CURRENT_DESKTOP": "Hyprland"},     # not the signal we key off
])
def test_is_hyprland_is_false_without_the_signature(env):
    assert raisewindow.is_hyprland(env) is False


def test_is_hyprland_reads_the_process_environment_when_env_is_none(
        monkeypatch):
    monkeypatch.delenv(raisewindow.HYPRLAND_ENV, raising=False)
    assert raisewindow.is_hyprland() is False

    monkeypatch.setenv(raisewindow.HYPRLAND_ENV, "live")
    assert raisewindow.is_hyprland() is True


def test_focus_window_is_a_quiet_noop_off_hyprland():
    runner = FakeRunner(results=[(1, "should never run")])
    looked_up = []
    result = raisewindow.focus_window(
        pid=4242, which=looked_up.append, runner=runner, env={})

    assert result is False
    assert runner.calls == []          # no subprocess
    assert looked_up == []             # not even a PATH lookup


def test_focus_window_off_hyprland_logs_nothing(caplog):
    runner = FakeRunner()
    with caplog.at_level(logging.DEBUG, logger=LOG_NAME):
        raisewindow.focus_window(pid=1, which=lambda name: HYPRCTL_PATH,
                                 runner=runner, env={})

    assert caplog.records == []        # quiet, per the docstring


# ── 2. hyprctl availability ────────────────────────────────────────────────
def test_focus_window_returns_false_and_runs_nothing_without_hyprctl():
    runner = FakeRunner()
    result = raisewindow.focus_window(
        pid=7, which=_which({}), runner=runner, env=_hypr_env())

    assert result is False
    assert runner.calls == []


def test_missing_hyprctl_is_logged_as_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        raisewindow.focus_window(pid=7, which=_which({}), runner=FakeRunner(),
                                 env=_hypr_env())

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "hyprctl" in warnings[0].getMessage()


# ── 3. the happy path ──────────────────────────────────────────────────────
def test_focus_window_runs_clients_then_focus_then_bring_to_top():
    runner = FakeRunner(results=[(0, _clients_reply(1234)), (0, ""), (0, "")])

    result = raisewindow.focus_window(
        pid=1234, which=_which({"hyprctl": HYPRCTL_PATH}), runner=runner,
        env=_hypr_env())

    assert result is True
    assert runner.calls == [
        [HYPRCTL_PATH, "clients", "-j"],
        [HYPRCTL_PATH, "dispatch", "focuswindow", "address:0x04d2"],
        [HYPRCTL_PATH, "dispatch", "bringactivetotop"],
    ]


def test_focus_window_picks_the_client_that_matches_the_pid():
    payload = json.dumps([
        {"pid": 10, "address": "0xaaa"},
        {"pid": 1234, "address": "0xbbb"},
        {"pid": 20, "address": "0xccc"},
    ])
    runner = FakeRunner(results=[(0, payload), (0, ""), (0, "")])

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env()) is True
    assert runner.calls[1] == [
        HYPRCTL_PATH, "dispatch", "focuswindow", "address:0xbbb"]


def test_focus_window_uses_the_first_client_when_no_client_names_it():
    payload = json.dumps([
        {"pid": 1234, "address": "0xfirst"},
        {"pid": 1234, "address": "0xsecond"},
    ])
    runner = FakeRunner(results=[(0, payload), (0, ""), (0, "")])

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env()) is True
    assert "address:0xfirst" in runner.calls[1]


def test_focus_window_defaults_to_the_current_process_pid():
    runner = FakeRunner(
        results=[(0, _clients_reply(os.getpid())), (0, ""), (0, "")])

    assert raisewindow.focus_window(
        which=lambda _n: HYPRCTL_PATH, runner=runner, env=_hypr_env()) is True
    assert f"address:0x{os.getpid():04x}" in runner.calls[1]


def test_which_defaults_to_shutil_which_when_not_injected(monkeypatch):
    """``which=None`` means the real PATH lookup, resolved at call time."""
    looked_up = []

    def _which(name):
        looked_up.append(name)
        return None

    monkeypatch.setattr(raisewindow.shutil, "which", _which)

    assert raisewindow.focus_window(pid=1, env=_hypr_env()) is False
    assert looked_up == [raisewindow.HYPRCTL]


# ── 4. picking among several clients owned by the same pid ─────────────────
def _focus_address_for(payload):
    """Drive ``focus_window`` and return the address it dispatches."""
    runner = FakeRunner(results=[(0, payload), (0, ""), (0, "")])
    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env(), sleep=FakeSleep()) is True
    return runner.calls[1][-1]


def test_focus_window_prefers_a_client_whose_class_names_the_overlay():
    payload = json.dumps([
        _client(1234, "0xplain", **{"class": "Alacritty"}),
        _client(1234, "0xoverlay", **{"class": raisewindow.APP_NAME}),
    ])

    assert _focus_address_for(payload) == "address:0xoverlay"


def test_focus_window_prefers_a_client_whose_title_names_the_overlay():
    payload = json.dumps([
        _client(1234, "0xplain", **{"class": "Alacritty",
                                    "title": "game"}),
        _client(1234, "0xoverlay", **{"class": "something",
                                      "title": raisewindow.APP_NAME}),
    ])

    assert _focus_address_for(payload) == "address:0xoverlay"


def test_focus_window_prefers_the_first_named_client():
    payload = json.dumps([
        _client(1234, "0xsecond", **{"class": "mewgenics-overlay"}),
        _client(1234, "0xthird", **{"class": "Mewgenics-Overlay"}),
    ])

    assert _focus_address_for(payload) == "address:0xsecond"


def test_focus_window_falls_back_when_the_named_client_has_no_address():
    payload = json.dumps([
        _client(1234, "0xusable", **{"class": "Alacritty"}),
        _client(1234, "", **{"class": raisewindow.APP_NAME}),
    ])

    assert _focus_address_for(payload) == "address:0xusable"


def test_address_for_pid_ignores_clients_belonging_to_other_pids():
    clients = [_client(1, "0xa"), _client(2, "0xb"), _client(3, "0xc")]

    assert raisewindow._address_for_pid(clients, 2) == "0xb"


def test_address_for_pid_returns_none_when_no_pid_matches():
    assert raisewindow._address_for_pid([_client(1, "0xa")], 2) is None


@pytest.mark.parametrize("client", [
    {"class": "Mewgenics-Overlay"},          # exact app name, odd casing
    {"class": "MEWGENICS-OVERLAY"},
    {"title": "mewgenics-overlay (About)"},
    {"title": "My Mewgenics-Overlay window"},
])
def test_names_overlay_matches_class_or_title_case_insensitively(client):
    assert raisewindow._names_overlay(client) is True


@pytest.mark.parametrize("client", [
    {},                                      # no class/title
    {"class": None},
    {"class": 5},
    {"title": None},
    {"class": "Alacritty", "title": "a shell"},
])
def test_names_overlay_is_false_for_other_or_non_string_values(client):
    assert raisewindow._names_overlay(client) is False


# ── 4b. overlay-name folding: separators, casing, token match ──────────────
# Every spelling a toolkit or a user-written title might produce. All of them
# must fold to one of OVERLAY_NAME_TOKENS and be accepted as naming the app.
OVERLAY_NAME_SPELLINGS = [
    "mewgenics-overlay",
    "MewgenicsOverlay",
    "Mewgenics Breeding Overlay",
    "mewgenics_breeding_overlay",
    "MEWGENICS_BREEDING_OVERLAY",
    "MEWGENICS-OVERLAY",
    "  Mewgenics   Breeding   Overlay  ",
]


@pytest.mark.parametrize("value", OVERLAY_NAME_SPELLINGS)
def test_names_overlay_accepts_casing_and_separator_spellings(value):
    assert raisewindow._names_overlay({"class": value}) is True
    assert raisewindow._names_overlay({"title": value}) is True


@pytest.mark.parametrize("value", OVERLAY_NAME_SPELLINGS)
def test_normalise_name_folds_into_a_documented_token(value):
    assert raisewindow._normalise_name(value) in raisewindow.OVERLAY_NAME_TOKENS


@pytest.mark.parametrize("separator", raisewindow.NAME_SEPARATORS)
def test_normalise_name_removes_every_documented_separator(separator):
    assert raisewindow._normalise_name(
        f"mewgenics{separator}overlay") == "mewgenicsoverlay"
    assert raisewindow._normalise_name(
        f"me{separator}wgenics") == "mewgenics"


def test_normalise_name_is_case_insensitive():
    spellings = ["mewgenics-overlay", "Mewgenics-Overlay",
                 "MEWGENICS-OVERLAY", "MeWgEnIcS-oVeRlAy"]

    folded = {raisewindow._normalise_name(v) for v in spellings}

    assert folded == {"mewgenicsoverlay"}


def test_normalise_name_of_an_empty_or_separator_only_value_is_empty():
    assert raisewindow._normalise_name("") == ""
    assert raisewindow._normalise_name(" - _ ") == ""


def test_the_configured_app_name_is_itself_recognised():
    # APP_NAME is the canonical spelling; it must fold to a token so the
    # preference test above stays meaningful.
    assert raisewindow._normalise_name(raisewindow.APP_NAME) in \
        raisewindow.OVERLAY_NAME_TOKENS
    assert raisewindow._names_overlay({"class": raisewindow.APP_NAME}) is True


@pytest.mark.parametrize("value", [
    "mewgenics",                 # one token only
    "breeding",
    "overlay",
    "Alacritty",
    "kitty",
    "game",
    "mewgenics helper",          # names the game, not the overlay
    "-",                         # separator-only folds to ""
    "___",
    "\u732b\u30b2\u30cb\u30c3\u30af\u30b9",          # unicode, no ASCII token
    "x" * 500,                   # long value, still no token
])
def test_names_overlay_rejects_values_that_do_not_name_the_overlay(value):
    assert raisewindow._names_overlay({"class": value}) is False
    assert raisewindow._names_overlay({"title": value}) is False


@pytest.mark.parametrize("client", [
    {},                                       # neither field present
    {"class": None, "title": None},          # present but null
    {"class": 42, "title": 42},              # integers, not strings
    {"class": 3.5},                           # float
    {"class": True},                          # bool is not a str
    {"class": b"mewgenics-overlay"},         # bytes
    {"class": ["mewgenics-overlay"]},        # a list of strings
    {"title": {"name": "mewgenics-overlay"}},   # a dict
])
def test_names_overlay_ignores_non_string_and_missing_values(client):
    assert raisewindow._names_overlay(client) is False


def test_address_for_pid_prefers_the_recognised_client_over_the_first():
    clients = [
        _client(7, "0xearlier", **{"class": "Alacritty", "title": "shell"}),
        _client(7, "0xoverlay", **{"class": "MewgenicsOverlay"}),
    ]

    assert raisewindow._address_for_pid(clients, 7) == "0xoverlay"


def test_focus_window_prefers_a_recognised_client_over_an_earlier_match():
    # The earlier pid match is usable but plainly another window; the later
    # one is named the overlay by its free-form title.
    payload = json.dumps([
        _client(1234, "0xearlier", **{"class": "Alacritty",
                                       "title": "shell"}),
        _client(1234, "0xlater", **{"class": "kitty",
                                     "title": "Mewgenics Breeding Overlay"}),
    ])

    assert _focus_address_for(payload) == "address:0xlater"


def test_focus_window_falls_back_to_the_first_usable_pid_match():
    # Nothing is recognised, and the only overlay-named client belongs to a
    # different pid, so the first usable pid match is the fallback.
    payload = json.dumps([
        _client(999, "0xotherpid", **{"class": "Mewgenics-Overlay"}),
        _client(1234, "0xplain1", **{"class": "Alacritty"}),
        _client(1234, "0xplain2", **{"class": "kitty"}),
    ])

    assert _focus_address_for(payload) == "address:0xplain1"


def test_address_for_pid_falls_back_to_the_first_usable_pid_match():
    clients = [
        _client(7, "0xfirst", **{"class": "Alacritty"}),
        _client(7, "0xsecond", **{"class": "kitty"}),
    ]

    assert raisewindow._address_for_pid(clients, 7) == "0xfirst"


def test_address_for_pid_ignores_a_recognised_client_of_another_pid():
    clients = [
        _client(8, "0xother", **{"class": "mewgenics_breeding_overlay"}),
        _client(7, "0xmine", **{"class": "Alacritty"}),
    ]

    assert raisewindow._address_for_pid(clients, 7) == "0xmine"


# ── 5. miss / failure paths (all -> False, logged, never raised) ───────────
def test_no_client_for_the_pid_retries_once_then_stops(caplog):
    runner = FakeRunner(results=[(0, _clients_reply(999)),
                                 (0, _clients_reply(999))])
    sleep = FakeSleep()

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        result = raisewindow.focus_window(
            pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
            env=_hypr_env(), sleep=sleep)

    assert result is False
    assert runner.calls == [[HYPRCTL_PATH, "clients", "-j"]] * 2
    assert sleep.calls == [raisewindow.FIRST_LOOKUP_RETRY_DELAY_S]
    assert any("no Hyprland client for pid 1234" in r.getMessage()
               for r in caplog.records)


def test_the_lookup_retry_waits_the_documented_delay():
    runner = FakeRunner(results=[(0, _clients_reply(999)),
                                 (0, _clients_reply(1234)),
                                 (0, ""), (0, "")])
    sleep = FakeSleep()

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env(), sleep=sleep) is True
    assert [c[1] for c in runner.calls] == [
        "clients", "clients", "dispatch", "dispatch"]
    assert sleep.calls == [raisewindow.FIRST_LOOKUP_RETRY_DELAY_S]


def test_a_successful_first_lookup_never_waits():
    runner = FakeRunner(results=[(0, _clients_reply(1234)), (0, ""), (0, "")])
    sleep = FakeSleep()

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env(), sleep=sleep) is True
    assert sleep.calls == []


def test_empty_client_list_is_a_quietly_logged_failure():
    runner = FakeRunner(results=[(0, "[]"), (0, "[]")])

    assert raisewindow.focus_window(
        pid=1, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env(), sleep=FakeSleep()) is False
    assert runner.calls == [[HYPRCTL_PATH, "clients", "-j"]] * 2


def test_clients_command_nonzero_exit_returns_false(caplog):
    runner = FakeRunner(results=[(1, ""), (1, "")])

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        result = raisewindow.focus_window(
            pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
            env=_hypr_env(), sleep=FakeSleep())

    assert result is False
    assert runner.calls == [[HYPRCTL_PATH, "clients", "-j"]] * 2
    assert any("clients -j failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("payload", [
    "not json at all",
    "",
    "{unterminated",
    '{"pid": 1234}',            # valid JSON, wrong shape
    '"a string"',
    "[]",                       # valid but no clients -> covered above too
])
def test_invalid_or_unusable_clients_payload_returns_false(payload):
    runner = FakeRunner(results=[(0, payload), (0, payload)])

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env(), sleep=FakeSleep()) is False
    assert runner.calls == [[HYPRCTL_PATH, "clients", "-j"]] * 2


def test_unparseable_clients_json_is_logged(caplog):
    runner = FakeRunner(results=[(0, "<not json>"), (0, "<not json>")])

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        raisewindow.focus_window(pid=1, which=lambda _n: HYPRCTL_PATH,
                                 runner=runner, env=_hypr_env(),
                                 sleep=FakeSleep())

    assert any("could not parse" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("client", [
    {"pid": 1234},                      # no address key
    {"pid": 1234, "address": ""},       # empty address
    {"pid": 1234, "address": None},     # unusable type
    {"pid": 1234, "address": 0x1234},   # int, not the string hyprctl sends
])
def test_client_without_a_usable_address_returns_false(client):
    payload = json.dumps([client])
    runner = FakeRunner(results=[(0, payload), (0, payload)])

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env(), sleep=FakeSleep()) is False
    assert runner.calls == [[HYPRCTL_PATH, "clients", "-j"]] * 2


def test_client_without_a_usable_address_is_logged(caplog):
    runner = FakeRunner(results=[(0, json.dumps([{"pid": 1234}])),
                                 (0, "[]")])

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        raisewindow.focus_window(pid=1234, which=lambda _n: HYPRCTL_PATH,
                                 runner=runner, env=_hypr_env(),
                                 sleep=FakeSleep())

    assert any("no usable address" in r.getMessage() for r in caplog.records)


def test_non_dict_client_entries_are_skipped():
    payload = json.dumps(["oops", None, 42, {"pid": 1234, "address": "0xok"}])
    runner = FakeRunner(results=[(0, payload), (0, ""), (0, "")])

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env(), sleep=FakeSleep()) is True
    assert "address:0xok" in runner.calls[1]


def test_focus_dispatch_failure_stops_before_bringactivetotop(caplog):
    runner = FakeRunner(results=[(0, _clients_reply(1234)), (1, "")])

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        result = raisewindow.focus_window(
            pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
            env=_hypr_env(), sleep=FakeSleep())

    assert result is False
    assert runner.calls == [
        [HYPRCTL_PATH, "clients", "-j"],
        [HYPRCTL_PATH, "dispatch", "focuswindow", "address:0x04d2"],
    ]
    assert any("dispatch focuswindow failed" in r.getMessage()
               for r in caplog.records)


def test_bringactivetotop_failure_returns_false(caplog):
    runner = FakeRunner(results=[(0, _clients_reply(1234)), (0, ""), (2, "")])

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        result = raisewindow.focus_window(
            pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
            env=_hypr_env(), sleep=FakeSleep())

    assert result is False
    assert runner.calls[-1] == [
        HYPRCTL_PATH, "dispatch", "bringactivetotop"]
    assert any("dispatch bringactivetotop failed" in r.getMessage()
               for r in caplog.records)


def test_no_failure_path_raises(caplog):
    """Every miss returns False - the overlay must never die for a raise."""
    scenarios = [
        FakeRunner(results=[(3, ""), (3, "")]),           # clients fails twice
        FakeRunner(results=[(0, "{"), (0, "{")]),         # unparseable twice
        FakeRunner(results=[(0, "[]"), (0, "[]")]),       # no pid match twice
        FakeRunner(results=[(0, _clients_reply(1234)),
                            (9, "")]),   # focus dispatch fails
        FakeRunner(results=[(0, _clients_reply(1234)), (0, ""),
                            (9, "")]),                    # bring-above fails
    ]
    with caplog.at_level(logging.DEBUG, logger=LOG_NAME):
        for runner in scenarios:
            assert raisewindow.focus_window(
                pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
                env=_hypr_env(), sleep=FakeSleep()) is False


# ── 5b. an injected runner that raises is contained, never propagated ──────
# ``_guarded`` promises that any exception from an injected runner becomes a
# failed result, because ``focus_window`` is called from the UI thread. These
# tests drive the public entry point with a runner that raises and check the
# exception cannot escape.
def _raising_runner(exc, raise_on_call=1, clients_payload=None):
    """A recording runner that raises *exc* on its Nth call (1-indexed)."""
    calls = []

    def runner(argv):
        calls.append([str(a) for a in argv])
        if len(calls) == raise_on_call:
            raise exc
        return 0, clients_payload if argv[1] == "clients" else ""

    return runner, calls


def test_focus_window_contains_a_runner_that_raises_on_the_first_call(
        monkeypatch, caplog):
    """The documented guarantee: a raising injected runner must not unwind
    out of ``focus_window`` on any call."""
    _patch_clock(monkeypatch, FakeClock(1000.0))
    runner, calls = _raising_runner(RuntimeError("hyprctl exploded"))

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        result = raisewindow.focus_window(
            pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
            env=_hypr_env(), sleep=FakeSleep())

    assert result is False
    # The guarded raise counts as a failed command, so the lookup retries
    # once (and raises again) before giving up with False.
    assert calls == [[HYPRCTL_PATH, "clients", "-j"]] * 2
    assert any("raised" in r.getMessage()
               and "hyprctl exploded" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("raise_on_call", [2, 3])
def test_focus_window_contains_a_runner_that_raises_on_a_later_call(
        raise_on_call):
    """A raise after the client lookup succeeded (on the focus or the
    bring-to-top dispatch) also stops the attempt with a logged False."""
    runner, calls = _raising_runner(
        RuntimeError("hyprctl died"), raise_on_call=raise_on_call,
        clients_payload=_clients_reply(1234))

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env(), sleep=FakeSleep()) is False
    assert calls == [
        [HYPRCTL_PATH, "clients", "-j"],
        [HYPRCTL_PATH, "dispatch", "focuswindow", "address:0x04d2"],
        [HYPRCTL_PATH, "dispatch", "bringactivetotop"],
    ][:raise_on_call]


@pytest.mark.parametrize("exc", [
    RuntimeError("boom"),
    OSError("hyprctl vanished"),
    KeyError("address"),
    ValueError("bad payload"),
    json.JSONDecodeError("bad json", "{", 0),
])
def test_focus_window_contains_every_injected_runner_exception_type(
        exc, monkeypatch):
    """The containment is by base class, not one hand-picked exception."""
    _patch_clock(monkeypatch, FakeClock(1000.0))
    runner, calls = _raising_runner(exc)

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
        env=_hypr_env(), sleep=FakeSleep()) is False
    assert calls        # it really tried, and still returned False


# ── 6. the single whole-attempt budget ─────────────────────────────────────
def test_the_total_budget_is_smaller_than_three_per_call_timeouts():
    # The whole attempt is capped, so three sequential calls cannot each burn
    # the per-call timeout (3 x 2 s) and freeze the UI thread.
    assert 0 < raisewindow.FOCUS_BUDGET_S <= 1.5
    assert raisewindow.FOCUS_BUDGET_S < 3 * raisewindow.HYPRCTL_TIMEOUT_S


def test_budgeted_runner_caps_each_call_at_the_smaller_timeout(monkeypatch):
    _patch_clock(monkeypatch, FakeClock(1000.0))
    timeouts = []
    monkeypatch.setattr(
        raisewindow, "_run",
        lambda argv, timeout: (timeouts.append(timeout), (0, ""))[1])

    run = raisewindow._budgeted_runner(1000.0 + raisewindow.FOCUS_BUDGET_S)
    run([HYPRCTL_PATH, "clients", "-j"])

    # 1.5 s of budget left is smaller than the 2 s per-call timeout.
    assert timeouts == [raisewindow.FOCUS_BUDGET_S]


def test_budgeted_runner_shrinks_the_timeout_as_the_deadline_nears(
        monkeypatch):
    clock = _patch_clock(monkeypatch, FakeClock(1000.0))
    timeouts = []
    monkeypatch.setattr(
        raisewindow, "_run",
        lambda argv, timeout: (timeouts.append(timeout), (0, ""))[1])

    run = raisewindow._budgeted_runner(1002.0)   # 2.0 s of budget left
    clock.advance(1.0)                           # -> 1.0 s left
    run([HYPRCTL_PATH])
    clock.advance(0.75)                          # -> 0.25 s left
    run([HYPRCTL_PATH])

    assert timeouts == [1.0, 0.25]


def test_budgeted_runner_refuses_to_spawn_once_the_budget_is_spent(
        monkeypatch, caplog):
    _patch_clock(monkeypatch, FakeClock(1000.0))
    ran = []
    monkeypatch.setattr(
        raisewindow, "_run",
        lambda argv, timeout: (ran.append(argv), (0, ""))[1])

    run = raisewindow._budgeted_runner(1000.0)   # exactly at the deadline

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        assert run([HYPRCTL_PATH, "clients", "-j"]) == (1, "")

    assert ran == []
    assert any("budget" in r.getMessage() for r in caplog.records)


def test_invoke_runs_the_runner_while_the_budget_covers_it(monkeypatch):
    clock = _patch_clock(monkeypatch, FakeClock(1000.0))
    calls = []

    runner = lambda argv: (calls.append(argv), (0, "ok"))[1]

    result = raisewindow._invoke(runner, [HYPRCTL_PATH], clock.now + 1.0)

    assert result == (0, "ok")
    assert calls == [[HYPRCTL_PATH]]


def test_invoke_skips_the_runner_once_the_deadline_has_passed(
        monkeypatch, caplog):
    clock = _patch_clock(monkeypatch, FakeClock(1000.0))
    calls = []

    runner = lambda argv: (calls.append(argv), (0, "ok"))[1]

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        assert raisewindow._invoke(runner, [HYPRCTL_PATH], clock.now) is None

    assert calls == []
    assert any("budget" in r.getMessage() for r in caplog.records)


def test_focus_window_stops_the_attempt_when_the_budget_is_spent(
        monkeypatch, caplog):
    clock = _patch_clock(monkeypatch, FakeClock(1000.0))
    calls = []

    def runner(argv):
        calls.append([str(a) for a in argv])
        clock.advance(raisewindow.FOCUS_BUDGET_S)   # a wedged hyprctl eats it
        return 0, _clients_reply(1234)

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        result = raisewindow.focus_window(
            pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
            env=_hypr_env(), sleep=FakeSleep())

    assert result is False
    # The client lookup ran, but focus/bring-above never did.
    assert calls == [[HYPRCTL_PATH, "clients", "-j"]]
    assert any("budget" in r.getMessage() for r in caplog.records)


def test_focus_window_does_not_retry_without_budget_for_the_wait(
        monkeypatch, caplog):
    clock = _patch_clock(monkeypatch, FakeClock(1000.0))
    calls = []
    sleep = FakeSleep()

    def runner(argv):
        calls.append([str(a) for a in argv])
        # Leave less than the retry delay in the budget.
        clock.advance(raisewindow.FOCUS_BUDGET_S - 0.05)
        return 0, _clients_reply(999)              # no pid match

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        result = raisewindow.focus_window(
            pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
            env=_hypr_env(), sleep=sleep)

    assert result is False
    assert len(calls) == 1          # no second lookup
    assert sleep.calls == []        # and no real wait
    assert any("only" in r.getMessage() for r in caplog.records)


def test_the_lookup_retry_is_charged_against_the_same_budget(
        monkeypatch, caplog):
    clock = _patch_clock(monkeypatch, FakeClock(1000.0))
    clients_calls = []

    def runner(argv):
        if argv[1] == "clients":
            clients_calls.append([str(a) for a in argv])
        return 0, _clients_reply(999)              # never matches

    def sleep(seconds):
        clock.advance(raisewindow.FOCUS_BUDGET_S)  # the wait exhausts it

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        result = raisewindow.focus_window(
            pid=1234, which=lambda _n: HYPRCTL_PATH, runner=runner,
            env=_hypr_env(), sleep=sleep)

    assert result is False
    assert len(clients_calls) == 1     # the retry lookup was budget-blocked
    assert any("budget" in r.getMessage() for r in caplog.records)


def _install_subprocess(monkeypatch, run):
    monkeypatch.setattr(
        raisewindow, "subprocess",
        SimpleNamespace(run=run, SubprocessError=subprocess.SubprocessError,
                        TimeoutExpired=subprocess.TimeoutExpired))


def test_the_default_runner_stays_inside_the_total_budget(monkeypatch):
    _patch_clock(monkeypatch, FakeClock(1000.0))
    timeouts = []

    def _sub_run(cmd, **kwargs):
        timeouts.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout=_clients_reply(1234),
                               stderr="")

    _install_subprocess(monkeypatch, _sub_run)

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, env=_hypr_env(),
        sleep=FakeSleep()) is True
    # The fake clock never advances, so every call sees the whole budget,
    # which is smaller than the per-call timeout.
    assert timeouts == [raisewindow.FOCUS_BUDGET_S] * 3
    assert all(t <= raisewindow.HYPRCTL_TIMEOUT_S for t in timeouts)


def test_the_default_runner_stops_when_the_first_command_spends_the_budget(
        monkeypatch):
    clock = _patch_clock(monkeypatch, FakeClock(1000.0))
    commands = []

    def _sub_run(cmd, **kwargs):
        commands.append(cmd)
        clock.advance(raisewindow.FOCUS_BUDGET_S)  # first call eats it all
        return SimpleNamespace(returncode=0, stdout=_clients_reply(1234),
                               stderr="")

    _install_subprocess(monkeypatch, _sub_run)

    assert raisewindow.focus_window(
        pid=1234, which=lambda _n: HYPRCTL_PATH, env=_hypr_env(),
        sleep=FakeSleep()) is False
    assert len(commands) == 1          # focus/bring-above never spawned


# ── 7. the default runner ──────────────────────────────────────────────────
def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout,
                           stderr=stderr)


def _fake_subprocess(monkeypatch, result=None, exc=None):
    """Replace the module's ``subprocess`` with a recording namespace."""
    calls = []

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if exc is not None:
            raise exc
        return result

    fake = SimpleNamespace(run=_run,
                           SubprocessError=subprocess.SubprocessError,
                           TimeoutExpired=subprocess.TimeoutExpired)
    monkeypatch.setattr(raisewindow, "subprocess", fake)
    return calls


def test_default_runner_is_timeout_bounded(monkeypatch):
    calls = _fake_subprocess(monkeypatch, result=_proc(stdout="hi"))

    code, out = raisewindow._run([HYPRCTL_PATH, "clients", "-j"])

    assert (code, out) == (0, "hi")
    assert calls[0][1]["timeout"] == raisewindow.HYPRCTL_TIMEOUT_S
    assert calls[0][1]["timeout"] > 0


def test_default_runner_stringifies_every_argument(monkeypatch):
    calls = _fake_subprocess(monkeypatch, result=_proc())

    raisewindow._run([HYPRCTL_PATH, 42, None])

    assert calls[0][0] == [HYPRCTL_PATH, "42", "None"]


def test_default_runner_turns_none_stdout_into_an_empty_string(monkeypatch):
    _fake_subprocess(monkeypatch, result=_proc(stdout=None))

    assert raisewindow._run([HYPRCTL_PATH]) == (0, "")


def test_default_runner_reports_the_exit_code(monkeypatch):
    _fake_subprocess(monkeypatch, result=_proc(returncode=5,
                                               stdout="ignored"))

    assert raisewindow._run([HYPRCTL_PATH]) == (5, "ignored")


def test_default_runner_swallows_a_missing_binary(monkeypatch):
    _fake_subprocess(monkeypatch, exc=FileNotFoundError("hyprctl"))

    assert raisewindow._run([HYPRCTL_PATH]) == (1, "")


def test_default_runner_swallows_a_timeout(monkeypatch):
    _fake_subprocess(monkeypatch,
                     exc=subprocess.TimeoutExpired("hyprctl", 2.0))

    assert raisewindow._run([HYPRCTL_PATH]) == (1, "")


def test_default_runner_swallows_other_os_errors(monkeypatch):
    _fake_subprocess(monkeypatch, exc=PermissionError("nope"))

    assert raisewindow._run([HYPRCTL_PATH]) == (1, "")


def test_default_runner_logs_the_timeout_reason(monkeypatch, caplog):
    _fake_subprocess(monkeypatch,
                     exc=subprocess.TimeoutExpired("hyprctl", 1.5))

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        assert raisewindow._run([HYPRCTL_PATH, "clients", "-j"]) == (1, "")

    messages = [r.getMessage() for r in caplog.records]
    assert any("timed out" in m for m in messages)


def test_default_runner_logs_the_exception_type_and_message(monkeypatch,
                                                            caplog):
    _fake_subprocess(monkeypatch, exc=FileNotFoundError("hyprctl missing"))

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        assert raisewindow._run([HYPRCTL_PATH]) == (1, "")

    messages = [r.getMessage() for r in caplog.records]
    assert any("FileNotFoundError" in m and "hyprctl missing" in m
               for m in messages)


def test_default_runner_logs_stderr_on_a_nonzero_exit(monkeypatch, caplog):
    _fake_subprocess(
        monkeypatch,
        result=_proc(returncode=2, stderr="  no such dispatch  \n"))

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        assert raisewindow._run([HYPRCTL_PATH]) == (2, "")

    messages = [r.getMessage() for r in caplog.records]
    assert any("no such dispatch" in m for m in messages)


def test_default_runner_says_no_stderr_when_stderr_is_empty(monkeypatch,
                                                            caplog):
    _fake_subprocess(monkeypatch, result=_proc(returncode=2, stderr=None))

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        assert raisewindow._run([HYPRCTL_PATH]) == (2, "")

    messages = [r.getMessage() for r in caplog.records]
    assert any("no stderr" in m for m in messages)


def test_default_runner_logs_nothing_on_success(monkeypatch, caplog):
    _fake_subprocess(monkeypatch, result=_proc(stdout="ok"))

    with caplog.at_level(logging.DEBUG, logger=LOG_NAME):
        assert raisewindow._run([HYPRCTL_PATH]) == (0, "ok")

    assert caplog.records == []


def test_the_timeout_is_short_enough_for_the_ui_thread():
    # 2 s is the documented per-call cap; a wedged compositor must not block
    # longer than that on a single command.
    assert 0 < raisewindow.HYPRCTL_TIMEOUT_S <= 2.0


def test_focus_window_survives_a_runner_that_reports_failure_via_the_default(
        monkeypatch):
    """End-to-end through the real default runner, with only the binary
    lookup and ``subprocess.run`` stubbed - no hyprctl, no compositor."""
    _fake_subprocess(monkeypatch,
                     exc=FileNotFoundError("hyprctl is not installed"))

    assert raisewindow.focus_window(
        pid=os.getpid(), which=lambda _n: HYPRCTL_PATH, env=_hypr_env(),
        sleep=FakeSleep()
    ) is False


# ── 8. integration: WindowController.engage() reaches the fallback ─────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeChrome:
    def __init__(self):
        self.pinned = []
        self.click_through = []

    def set_pinned(self, value):
        self.pinned.append(bool(value))

    def set_click_through(self, value):
        self.click_through.append(bool(value))


@pytest.fixture
def make_ctl(qapp):
    from mewgenics_overlay.ui.windowstate import WindowController

    windows = []

    def _make():
        window = QWidget()
        windows.append(window)
        ctl = WindowController(window, FakeChrome(), {}, lambda: None)
        return SimpleNamespace(ctl=ctl, window=window)

    yield _make

    for window in windows:
        window.hide()
        window.close()
        window.deleteLater()
    qapp.processEvents()


@pytest.fixture
def raises(monkeypatch):
    """Replace the module-level fallback with a recorder."""
    seen = []

    def _fake(*args, **kwargs):
        seen.append((args, kwargs, True))
        return True

    monkeypatch.setattr(raisewindow, "focus_window", _fake)
    return seen


@requires_qt
def test_engage_calls_the_fallback_once_with_the_default_arguments(
        make_ctl, raises, qapp):
    h = make_ctl()

    h.ctl.engage()
    qapp.processEvents()

    assert len(raises) == 1
    assert raises[0][0] == ()          # pid/which/runner/env all defaulted
    assert raises[0][1] == {}


@requires_qt
def test_engage_runs_the_fallback_after_the_window_is_visible(make_ctl,
                                                              monkeypatch,
                                                              qapp):
    h = make_ctl()
    visible_when_called = []
    monkeypatch.setattr(
        raisewindow, "focus_window",
        lambda *a, **k: visible_when_called.append(h.window.isVisible()))

    h.ctl.engage()
    qapp.processEvents()

    # The fallback is a *nudge after* Qt's own show/raise/activate, never a
    # substitute for them: the window is already up when it runs.
    assert visible_when_called == [True]
    assert h.window.isVisible() is True


@requires_qt
@pytest.mark.parametrize("fallback_result", [True, False])
def test_engage_ignores_the_fallback_result(make_ctl, monkeypatch,
                                            fallback_result, qapp):
    monkeypatch.setattr(raisewindow, "focus_window",
                        lambda *a, **k: fallback_result)
    h = make_ctl()
    h.ctl.set_click_through(True)

    h.ctl.engage()
    qapp.processEvents()

    assert h.window.isVisible() is True
    assert h.ctl.click_through is False


@requires_qt
def test_engage_off_hyprland_spawns_no_subprocess(make_ctl, monkeypatch, qapp):
    """The real fallback, with only ``subprocess.run`` watched: off Hyprland
    nothing is executed at all (the quiet no-op guarantee)."""
    monkeypatch.delenv(raisewindow.HYPRLAND_ENV, raising=False)
    spawned = []
    monkeypatch.setattr(
        raisewindow, "subprocess",
        SimpleNamespace(run=lambda *a, **k: spawned.append(a),
                        SubprocessError=subprocess.SubprocessError))
    h = make_ctl()

    h.ctl.engage()
    qapp.processEvents()

    assert spawned == []
    assert h.window.isVisible() is True


@requires_qt
def test_engage_on_hyprland_drives_the_real_fallback_with_a_stub_runner(
        make_ctl, monkeypatch, qapp):
    """Full integration without a compositor: fake HYPRLAND env, fake
    ``hyprctl`` on PATH, fake runner; the window's own pid is looked up and
    focused."""
    monkeypatch.setenv(raisewindow.HYPRLAND_ENV, "test-signature")
    monkeypatch.setattr(raisewindow.shutil, "which",
                        lambda _n: HYPRCTL_PATH)
    runner = FakeRunner(
        results=[(0, _clients_reply(os.getpid())), (0, ""), (0, "")])
    monkeypatch.setattr(raisewindow, "_run", runner)
    h = make_ctl()

    h.ctl.engage()
    qapp.processEvents()

    assert [c[1] for c in runner.calls] == ["clients", "dispatch", "dispatch"]
