"""``ui/app.py`` routing for the new raise message.

``bridge_ctl.raise_requested`` must reach the palette's *raise* entry point
(``raise_reported_cat``: select the cat, then engage the window), while
``focus_requested`` keeps reaching the silent ``select_reported_cat``. The
``app.main`` startup path is heavy, so this reuses the bootstrap harness from
``test_app_bootstrap`` (fake QApplication / PaletteWindow / BridgeController
plus the config stubs) rather than duplicating it; the two fixtures are
re-exported by the import.

This file is separate so ``test_app_bootstrap.py`` stays under the 1000-line
test budget. The transport, the controller signal and the palette method's own
behaviour are covered by ``test_bridge.py``, ``test_bridgectl.py`` and
``test_raise_reported_cat.py``.
"""

from __future__ import annotations

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

BRIDGE_PORT = 45699


def _start_with_bridge(bootstrap, monkeypatch):
    """Run ``app.main`` with the fake bridge enabled; return the live pair."""
    bootstrap.install_instance([True], toggle_ok=True)
    monkeypatch.setattr(app.ui_config, "load",
                        lambda: {"bridge_enabled": True,
                                 "bridge_port": BRIDGE_PORT})
    _FakeBridgeController.instances = []
    monkeypatch.setattr(app.bridgectl, "BridgeController",
                        _FakeBridgeController)

    assert app.main(["--no-tray"]) == 0

    return (_FakeBridgeController.instances[-1],
            bootstrap.palette.instances[-1])


def test_a_raise_signal_selects_and_engages_through_the_palette(
        bootstrap, monkeypatch):
    ctl, palette = _start_with_bridge(bootstrap, monkeypatch)
    request = bridge.RaiseRequest(key=341)

    ctl.raise_requested.emit(request)

    # The raise reaches the palette's raise entry point, which selects the
    # reported cat and then engages the window. The echo check lives on the
    # focus path only, so the raise must bypass it entirely.
    assert palette.raised == [request]
    assert palette.reported == []
    assert palette.focused == [341]
    assert palette.engaged is True
