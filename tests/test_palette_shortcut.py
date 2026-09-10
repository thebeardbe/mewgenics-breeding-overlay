"""``PaletteWindow`` desktop-shortcut actions delegate to the manager.

Building a full ``PaletteWindow`` is heavy (save controller, watcher, asset
loader, threads), so these tests call the real ``_setup_desktop_shortcut`` /
``_remove_desktop_shortcut`` methods on a small stand-in carrying only the
``_settings`` mapping they read. The manager itself
(``ui/desktopshortcut.py``) is stubbed at the module seam, so no real
``gsettings``/``qdbus``/``hyprctl`` runs and no file is written.

Gap: a stand-in proves the delegation contract (which binding, which message)
but not that a live PaletteWindow wires it to the Settings buttons; that
wiring is covered by ``test_layout.py``.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from mewgenics_overlay.ui import hotkeybinding  # noqa: E402
from mewgenics_overlay.ui import palette  # noqa: E402


def _host(**settings):
    return SimpleNamespace(_settings=dict(settings))


# ── install ────────────────────────────────────────────────────────────────
def test_setup_delegates_the_configured_combo(monkeypatch):
    seen = {}

    def fake_install(binding):
        seen["binding"] = binding
        return True, "desktop shortcut installed"

    monkeypatch.setattr(palette.desktopshortcut, "install", fake_install)

    result = palette.PaletteWindow._setup_desktop_shortcut(
        _host(hotkey="Ctrl+Alt+K"))

    assert result == (True, "desktop shortcut installed")
    assert seen["binding"].format() == "Ctrl+Alt+K"


def test_setup_falls_back_to_the_default_for_an_unusable_setting(monkeypatch):
    seen = {}

    def fake_install(binding):
        seen["binding"] = binding
        return True, ""

    monkeypatch.setattr(palette.desktopshortcut, "install", fake_install)

    palette.PaletteWindow._setup_desktop_shortcut(_host(hotkey="not a combo"))

    assert seen["binding"].format() == hotkeybinding.DEFAULT_TEXT


def test_setup_reflects_a_failure_message(monkeypatch):
    monkeypatch.setattr(palette.desktopshortcut, "install",
                        lambda binding: (False, "schema missing"))

    ok, message = palette.PaletteWindow._setup_desktop_shortcut(
        _host(hotkey="Ctrl+B"))

    assert ok is False
    assert message == "schema missing"


def test_setup_is_safe_with_no_hotkey_setting(monkeypatch):
    seen = {}

    def fake_install(binding):
        seen["binding"] = binding
        return True, ""

    monkeypatch.setattr(palette.desktopshortcut, "install", fake_install)

    palette.PaletteWindow._setup_desktop_shortcut(_host())

    assert seen["binding"].format() == hotkeybinding.DEFAULT_TEXT


# ── remove ─────────────────────────────────────────────────────────────────
def test_remove_delegates_and_returns_the_manager_message(monkeypatch):
    monkeypatch.setattr(palette.desktopshortcut, "remove",
                        lambda: (False, "could not remove"))

    result = palette.PaletteWindow._remove_desktop_shortcut(_host())

    assert result == (False, "could not remove")


def test_remove_reflects_a_success_message(monkeypatch):
    monkeypatch.setattr(palette.desktopshortcut, "remove",
                        lambda: (True, "removed"))

    assert palette.PaletteWindow._remove_desktop_shortcut(_host()) == \
        (True, "removed")
