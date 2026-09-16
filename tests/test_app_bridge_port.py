"""``ui/app.py``: the busy-bridge-port message on startup.

When the bridge socket cannot bind because the port is already taken, the
overlay must say so clearly: the log line and the window status must name the
*configured* port (the bound port is ``None`` until the socket listens, so the
message must not read "port None busy?"), and a normal start must put nothing
on the status line.

This reuses the bootstrap harness from ``test_app_bootstrap`` (fake
QApplication / PaletteWindow / BridgeController plus the config stubs) rather
than duplicating it, and lives in its own file so that module stays under the
1000-line test budget. The transport itself is covered by ``test_bridge.py``
and ``test_bridgectl.py``.
"""

from __future__ import annotations

import logging
import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from mewgenics_overlay.core import bridge  # noqa: E402
from mewgenics_overlay.ui import app  # noqa: E402

# noqa comments keep the harness imports obviously intentional.
from test_app_bootstrap import (  # noqa: E402,F401
    _FakeBridgeController,
    bootstrap,
)

#: A configured port deliberately different from ``bridge.DEFAULT_PORT`` so a
#: message that fell back to a default (or to a bound port) is caught.
BRIDGE_PORT = 45699


def _start(bootstrap, monkeypatch, settings):
    """Run ``app.main`` with the fake bridge enabled and *settings* loaded."""
    bootstrap.install_instance([True], toggle_ok=True)
    monkeypatch.setattr(app.ui_config, "load", lambda: settings)
    _FakeBridgeController.instances = []
    monkeypatch.setattr(app.bridgectl, "BridgeController", _FakeBridgeController)

    assert app.main(["--no-tray"]) == 0

    return (_FakeBridgeController.instances[-1],
            bootstrap.palette.instances[-1])


def _busy_messages(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and "busy" in r.getMessage()]


def test_a_busy_port_is_logged_with_the_configured_port(bootstrap, monkeypatch,
                                                        caplog):
    monkeypatch.setattr(_FakeBridgeController, "start_result", False)

    with caplog.at_level(logging.WARNING):
        ctl, palette = _start(bootstrap, monkeypatch,
                              {"bridge_enabled": True,
                               "bridge_port": BRIDGE_PORT})

    assert ctl.started is True
    assert ctl.stopped is True                 # teardown still runs
    # A controller that never listened is not attached, so the window does not
    # advertise "Show in game" through a dead bridge.
    assert palette.bridges == []

    messages = _busy_messages(caplog)
    assert messages, "the failed bind was not logged"
    assert any(str(BRIDGE_PORT) in m for m in messages), messages


def test_a_busy_port_sets_a_status_naming_the_configured_port(
        bootstrap, monkeypatch, caplog):
    monkeypatch.setattr(_FakeBridgeController, "start_result", False)

    with caplog.at_level(logging.WARNING):
        _ctl, palette = _start(bootstrap, monkeypatch,
                               {"bridge_enabled": True,
                                "bridge_port": BRIDGE_PORT})

    assert palette.statuses, "the window status line was not set on a busy port"
    assert any(str(BRIDGE_PORT) in s and "another instance or program" in s
               for s in palette.statuses), palette.statuses
    assert any("in use" in s for s in palette.statuses)


def test_the_busy_port_is_the_configured_one_not_the_unbound_one(
        bootstrap, monkeypatch, caplog):
    # The real controller's ``port`` is None until the socket is listening, so
    # a message built from it would read "port None busy?". Simulate that.
    def failing_start(self):
        self.port = None
        self.started = True
        return False

    monkeypatch.setattr(_FakeBridgeController, "start", failing_start)

    with caplog.at_level(logging.WARNING):
        _ctl, palette = _start(bootstrap, monkeypatch,
                               {"bridge_enabled": True,
                                "bridge_port": BRIDGE_PORT})

    busy = _busy_messages(caplog)
    assert busy, "the failed bind was not logged"
    assert all("None" not in m for m in busy), busy
    assert any(str(BRIDGE_PORT) in m for m in busy), busy

    # The status line carries the same configured port, never ``None``.
    assert any(str(BRIDGE_PORT) in s and "None" not in s
               for s in palette.statuses), palette.statuses


def test_the_default_port_is_named_when_none_is_configured(bootstrap,
                                                           monkeypatch,
                                                           caplog):
    monkeypatch.setattr(_FakeBridgeController, "start_result", False)

    with caplog.at_level(logging.WARNING):
        _ctl, palette = _start(bootstrap, monkeypatch,
                              {"bridge_enabled": True})

    messages = _busy_messages(caplog)
    assert any(str(bridge.DEFAULT_PORT) in m for m in messages), messages
    assert any(str(bridge.DEFAULT_PORT) in s for s in palette.statuses), \
        palette.statuses


def test_a_normal_bridge_start_sets_no_busy_status(bootstrap, monkeypatch,
                                                   caplog):
    with caplog.at_level(logging.WARNING):
        ctl, palette = _start(bootstrap, monkeypatch,
                              {"bridge_enabled": True,
                               "bridge_port": BRIDGE_PORT})

    assert ctl.started is True
    assert palette.bridges == [ctl]
    # A healthy start says nothing to the user and logs no busy-port warning.
    assert palette.statuses == []
    assert not any("busy" in r.getMessage() for r in caplog.records)
    assert not any("not listening" in r.getMessage() for r in caplog.records)


def test_a_disabled_bridge_sets_no_busy_status(bootstrap, monkeypatch,
                                               caplog):
    bootstrap.install_instance([True], toggle_ok=True)
    monkeypatch.setattr(app.ui_config, "load",
                        lambda: {"bridge_enabled": False})
    _FakeBridgeController.instances = []
    monkeypatch.setattr(app.bridgectl, "BridgeController",
                        _FakeBridgeController)

    with caplog.at_level(logging.WARNING):
        assert app.main(["--no-tray"]) == 0

    # No controller is built at all, so there is no bind and no busy message.
    assert _FakeBridgeController.instances == []
    assert bootstrap.palette.instances[-1].statuses == []
    assert not _busy_messages(caplog)
