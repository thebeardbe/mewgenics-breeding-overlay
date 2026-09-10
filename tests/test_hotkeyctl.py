"""``ui/hotkeyctl.py``: the hotkey controller (registration, shortcut, config).

``HotkeyController`` was extracted from ``PaletteWindow`` (configurable
hotkey): it owns the platform handle, the focused-window ``QShortcut``, the
persistence callback and the status/notify collaborators. The platform module
(``ui/hotkey.py``) is stubbed at the module seam so these tests can drive the
``active`` and rebind branches deterministically without a real global grab.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mewgenics_overlay.ui import hotkey as hotkey_mod  # noqa: E402
from mewgenics_overlay.ui.hotkeybinding import (  # noqa: E402
    DEFAULT_TEXT,
    HotkeyBinding,
)
from mewgenics_overlay.ui.hotkeyctl import HotkeyController  # noqa: E402


# ── fakes ──────────────────────────────────────────────────────────────────
class FakeHotkey:
    def __init__(self, binding=None, active=False, ok=True, error=""):
        self._binding = binding or HotkeyBinding(True, False, True, "B")
        self._active = active
        self.ok = ok
        self.error = error
        self.rebind_calls = []
        self.uninstalled = False

    @property
    def binding(self):
        return self._binding

    @property
    def active(self):
        return self._active

    @property
    def description(self):
        return self._binding.format()

    def rebind(self, binding):
        self.rebind_calls.append(binding)
        if not self.ok:
            return False, self.error
        self._binding = binding
        return True, ""

    def uninstall(self):
        self.uninstalled = True


class FakeShortcut:
    def __init__(self):
        self.keys = []
        self.enabled = None
        self.deleted = False

    def setKey(self, sequence):
        self.keys.append(sequence)

    def setEnabled(self, on):
        self.enabled = on

    def deleteLater(self):
        self.deleted = True


class _RebindingFakeHotkey(FakeHotkey):
    """``rebind`` can flip the live grab, like a real Windows register()."""

    def __init__(self, active=False, active_after=True, **kwargs):
        super().__init__(active=active, **kwargs)
        self._active_after = active_after

    def rebind(self, binding):
        ok, error = super().rebind(binding)
        if ok:
            self._active = self._active_after
        return ok, error


# ── fixtures / builders ────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_ctl(qapp):
    parents = []

    def _make(settings=None):
        settings = {} if settings is None else settings
        parent = QWidget()
        parents.append(parent)
        status = []
        saves = []
        toggles = []
        ctl = HotkeyController(
            settings,
            lambda: saves.append(True),
            status.append,
            lambda: toggles.append(True),
            parent=parent,
        )
        return ctl, settings, status, saves, toggles

    yield _make
    for parent in parents:
        parent.hide()
        parent.close()
        parent.deleteLater()
    qapp.processEvents()


# ── 1. state before install ────────────────────────────────────────────────
def test_description_defaults_before_install(make_ctl):
    ctl, *_ = make_ctl()

    assert ctl.description == DEFAULT_TEXT
    assert ctl.active is False


# ── 2. install arms both combinators and reports ───────────────────────────
def test_install_registers_the_configured_combo(make_ctl, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        hotkey_mod, "install",
        lambda app, cb, binding: seen.update(binding=binding) or
        FakeHotkey(binding=binding, active=True))
    ctl, settings, status, saves, toggles = make_ctl(
        {"hotkey": "Ctrl+Alt+K"})
    changes = []

    ctl.install(None, on_change=changes.append)

    assert seen["binding"] == HotkeyBinding(True, True, False, "K")
    assert ctl.active is True
    assert ctl.description == "Ctrl+Alt+K"
    assert status == ["global hotkey: Ctrl+Alt+K"]
    assert changes == ["Ctrl+Alt+K"]
    assert saves == []


def test_install_arms_the_focused_window_shortcut(make_ctl, monkeypatch):
    monkeypatch.setattr(hotkey_mod, "install",
                        lambda app, cb, binding: FakeHotkey(binding=binding))
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "Ctrl+Alt+K"})

    ctl.install(None)

    assert ctl._shortcut is not None
    assert ctl._shortcut.key().toString() == "Ctrl+Alt+K"
    assert toggles == []          # installation must not toggle the overlay


def test_install_uses_the_default_for_junk_config(make_ctl, monkeypatch):
    monkeypatch.setattr(hotkey_mod, "install",
                        lambda app, cb, binding: FakeHotkey(binding=binding))
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "not a combo"})

    ctl.install(None)

    assert ctl.description == DEFAULT_TEXT
    assert status == [f"hotkey {DEFAULT_TEXT} (overlay window only)"]


def test_install_uses_the_default_when_the_key_is_missing(make_ctl,
                                                          monkeypatch):
    monkeypatch.setattr(hotkey_mod, "install",
                        lambda app, cb, binding: FakeHotkey(binding=binding))
    ctl, *_ = make_ctl({})

    ctl.install(None)

    assert ctl.description == DEFAULT_TEXT


def test_shortcut_activation_calls_the_toggle(make_ctl, monkeypatch):
    monkeypatch.setattr(hotkey_mod, "install",
                        lambda app, cb, binding: FakeHotkey(binding=binding))
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "Ctrl+Shift+B"})
    ctl.install(None)

    ctl._shortcut.activated.emit()

    assert toggles == [True]


def test_status_text_marks_an_inactive_grab(make_ctl, monkeypatch):
    monkeypatch.setattr(hotkey_mod, "install",
                        lambda app, cb, binding: FakeHotkey(binding=binding,
                                                            active=False))
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "Alt+A"})

    ctl.install(None)

    assert status == ["hotkey Alt+A (overlay window only)"]


# ── 2b. the focused-window shortcut mirrors the global grab ────────────────
def _install_with_grab(ctl, monkeypatch, active,
                       binding_text="Ctrl+Shift+B"):
    """Install *ctl* with a stubbed platform grab of the given liveness."""
    ctl._settings["hotkey"] = binding_text
    monkeypatch.setattr(
        hotkey_mod, "install",
        lambda app, cb, b: FakeHotkey(binding=b, active=active))
    ctl.install(None)
    return ctl


def test_install_disables_the_shortcut_when_the_grab_is_live(make_ctl,
                                                             monkeypatch):
    ctl, *_ = make_ctl()
    _install_with_grab(ctl, monkeypatch, active=True)

    assert ctl.active is True
    assert ctl._shortcut.isEnabled() is False


def test_install_enables_the_shortcut_when_the_grab_is_not_live(
        make_ctl, monkeypatch):
    ctl, *_ = make_ctl()
    _install_with_grab(ctl, monkeypatch, active=False)

    assert ctl.active is False
    assert ctl._shortcut.isEnabled() is True


def test_successful_rebind_disables_the_shortcut_when_the_grab_goes_live(
        make_ctl, monkeypatch):
    ctl, *_ = make_ctl()
    _install_with_grab(ctl, monkeypatch, active=False)
    assert ctl._shortcut.isEnabled() is True
    ctl._hotkey = _RebindingFakeHotkey(active=False, active_after=True)

    ok, _ = ctl.set_binding("Ctrl+Alt+K")

    assert ok is True
    assert ctl.active is True
    assert ctl._shortcut.isEnabled() is False


def test_successful_rebind_enables_the_shortcut_when_no_grab_is_live(
        make_ctl, monkeypatch):
    ctl, *_ = make_ctl()
    _install_with_grab(ctl, monkeypatch, active=True)
    assert ctl._shortcut.isEnabled() is False
    ctl._hotkey = _RebindingFakeHotkey(active=True, active_after=False)

    ok, _ = ctl.set_binding("Ctrl+Alt+K")

    assert ok is True
    assert ctl.active is False
    assert ctl._shortcut.isEnabled() is True


def test_failed_rebind_keeps_the_shortcut_disabled_when_the_old_grab_survives(
        make_ctl, monkeypatch):
    ctl, settings, status, *_ = make_ctl()
    _install_with_grab(ctl, monkeypatch, active=False)
    assert ctl._shortcut.isEnabled() is True
    # The new combo failed but the platform layer restored the old grab.
    ctl._hotkey = FakeHotkey(binding=HotkeyBinding(True, False, True, "B"),
                             active=True, ok=False, error="taken")

    ok, _ = ctl.set_binding("Ctrl+Alt+K")

    assert ok is False
    assert ctl.active is True
    assert ctl._shortcut.isEnabled() is False
    assert status[-1] == "hotkey unchanged: Ctrl+Shift+B"


def test_failed_rebind_enables_the_shortcut_when_nothing_is_registered(
        make_ctl, monkeypatch):
    ctl, settings, status, *_ = make_ctl()
    _install_with_grab(ctl, monkeypatch, active=True)
    assert ctl._shortcut.isEnabled() is False
    # Neither the new combo nor the previous one could be registered.
    ctl._hotkey = FakeHotkey(binding=HotkeyBinding(True, False, True, "B"),
                             active=False, ok=False, error="taken")

    ok, _ = ctl.set_binding("Ctrl+Alt+K")

    assert ok is False
    assert ctl.active is False
    assert ctl._shortcut.isEnabled() is True
    assert status[-1] == (
        "global hotkey unavailable: only the overlay window responds; "
        "use the tray icon")


def test_install_is_idempotent(make_ctl, monkeypatch):
    calls = []

    def fake_install(app, cb, binding):
        calls.append(binding)
        return FakeHotkey(binding=binding, active=True)

    monkeypatch.setattr(hotkey_mod, "install", fake_install)
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "Ctrl+Alt+K"})
    changes = []
    ctl.install(None, on_change=changes.append)
    first_hotkey = ctl._hotkey
    first_shortcut = ctl._shortcut

    ctl.install(None, on_change=changes.append)

    assert len(calls) == 1                        # no second registration
    assert ctl._hotkey is first_hotkey            # no second helper/filter
    assert ctl._shortcut is first_shortcut        # no second QShortcut
    assert len(ctl._parent.findChildren(QShortcut)) == 1
    assert status == ["global hotkey: Ctrl+Alt+K"]  # not re-announced


# ── 3. set_binding: validation ─────────────────────────────────────────────
def test_set_binding_rejects_invalid_text_without_rebinding(make_ctl):
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "Ctrl+Shift+B"})
    ctl._hotkey = FakeHotkey()

    ok, error = ctl.set_binding("B")

    assert ok is False
    assert "modifier" in error
    assert "letter" in error
    assert ctl._hotkey.rebind_calls == []
    assert settings["hotkey"] == "Ctrl+Shift+B"
    assert saves == []


@pytest.mark.parametrize("junk", ["", "Ctrl", "Ctrl+Shift+1", "Ctrl+Ctrl+B",
                                  "Ctrl+Shift+AB", 123, None])
def test_set_binding_rejects_every_invalid_class(make_ctl, junk):
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "Ctrl+Shift+B"})
    ctl._hotkey = FakeHotkey()

    ok, _ = ctl.set_binding(junk)

    assert ok is False
    assert ctl._hotkey.rebind_calls == []
    assert settings["hotkey"] == "Ctrl+Shift+B"


# ── 4. set_binding: success ────────────────────────────────────────────────
def test_set_binding_success_persists_status_and_notifies(make_ctl):
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "Ctrl+Shift+B"})
    ctl._hotkey = FakeHotkey(active=True)
    ctl._shortcut = FakeShortcut()
    changes = []
    ctl._on_change = changes.append

    ok, error = ctl.set_binding("ctrl + alt + k")

    assert (ok, error) == (True, "")
    assert [b.format() for b in ctl._hotkey.rebind_calls] == ["Ctrl+Alt+K"]
    assert settings["hotkey"] == "Ctrl+Alt+K"       # canonicalised
    assert saves == [True]
    assert status[-1] == "global hotkey: Ctrl+Alt+K"
    assert changes == ["Ctrl+Alt+K"]
    assert ctl._shortcut.keys[-1].toString() == "Ctrl+Alt+K"


def test_set_binding_success_updates_an_existing_shortcut(make_ctl):
    ctl, settings, status, saves, toggles = make_ctl()
    ctl._hotkey = FakeHotkey(active=False)
    ctl._shortcut = FakeShortcut()

    ctl.set_binding("Ctrl+Z")

    assert ctl._shortcut.keys[-1].toString() == "Ctrl+Z"
    assert status[-1] == "hotkey Ctrl+Z (overlay window only)"


def test_set_binding_before_install_keeps_the_choice_for_next_install(
        make_ctl):
    # No platform handle yet (headless/no QApplication): the controller still
    # validates and stores so the next install picks it up from config.
    ctl, settings, status, saves, toggles = make_ctl()

    ok, error = ctl.set_binding("Ctrl+Alt+K")

    assert (ok, error) == (True, "")
    assert settings["hotkey"] == "Ctrl+Alt+K"
    assert saves == [True]
    assert ctl.description == "Ctrl+Alt+K"


def test_pre_install_choice_is_armed_by_the_next_install(make_ctl,
                                                         monkeypatch):
    """A combo set before install (headless harness) must still be armed
    when install finally runs: the placeholder handle created by
    ``set_binding`` must not be mistaken for an installed one."""
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "Ctrl+Shift+B"})
    ok, _ = ctl.set_binding("Ctrl+Alt+K")
    assert ok is True
    assert ctl._shortcut is None
    # ``set_binding`` left a platform-less placeholder handle behind; this is
    # what the install guard currently mistakes for an installed hotkey.
    assert ctl._hotkey is not None and not ctl.active

    seen = []
    monkeypatch.setattr(
        hotkey_mod, "install",
        lambda app, cb, b: seen.append(b) or FakeHotkey(binding=b, active=True))
    ctl.install(None)

    assert seen and seen[0].format() == "Ctrl+Alt+K"
    assert ctl._shortcut is not None
    assert ctl._shortcut.key().toString() == "Ctrl+Alt+K"
    assert ctl._shortcut.isEnabled() is False


# ── 5. set_binding: platform failure ───────────────────────────────────────
def test_set_binding_failure_keeps_the_previous_combo(make_ctl):
    ctl, settings, status, saves, toggles = make_ctl({"hotkey": "Ctrl+Shift+B"})
    ctl._hotkey = FakeHotkey(binding=HotkeyBinding(True, False, True, "B"),
                             active=True, ok=False, error="already registered")
    ctl._shortcut = FakeShortcut()
    changes = []
    ctl._on_change = changes.append

    ok, error = ctl.set_binding("Ctrl+Alt+K")

    assert ok is False
    assert error == "already registered"
    assert settings["hotkey"] == "Ctrl+Shift+B"
    assert saves == []
    assert status[-1] == "hotkey unchanged: Ctrl+Shift+B"
    assert changes == ["Ctrl+Shift+B"]
    assert ctl._shortcut.keys == []          # the shortcut is not switched


def test_set_binding_failure_without_a_reason_uses_a_generic_message(make_ctl):
    ctl, settings, status, saves, toggles = make_ctl()
    ctl._hotkey = FakeHotkey(ok=False, error="")

    ok, error = ctl.set_binding("Ctrl+Alt+K")

    assert ok is False
    assert error


# ── 6. notification guard and uninstall ────────────────────────────────────
def test_notify_swallows_a_failing_listener(make_ctl, caplog):
    ctl, *_ = make_ctl()
    ctl._on_change = lambda description: (_ for _ in ()).throw(
        RuntimeError("listener exploded"))

    with caplog.at_level("ERROR", logger="mewgenics_overlay.hotkey"):
        ctl._notify()          # must not raise

    assert any("hotkey change listener failed" in r.message
               for r in caplog.records)


def test_notify_without_a_listener_is_a_noop(make_ctl):
    ctl, *_ = make_ctl()

    ctl._notify()              # must not raise


def test_uninstall_releases_the_hotkey_and_the_shortcut(make_ctl):
    ctl, settings, status, saves, toggles = make_ctl()
    fake_hotkey = FakeHotkey()
    shortcut = FakeShortcut()
    ctl._hotkey = fake_hotkey
    ctl._shortcut = shortcut

    ctl.uninstall()

    assert fake_hotkey.uninstalled is True
    assert ctl._hotkey is None
    assert shortcut.enabled is False
    assert shortcut.deleted is True
    assert ctl._shortcut is None
    assert ctl.active is False


def test_uninstall_without_install_is_safe(make_ctl):
    ctl, *_ = make_ctl()

    ctl.uninstall()            # must not raise
    ctl.uninstall()

    assert ctl._hotkey is None
    assert ctl._shortcut is None


def test_description_is_default_again_after_uninstall(make_ctl, monkeypatch):
    monkeypatch.setattr(hotkey_mod, "install",
                        lambda app, cb, binding: FakeHotkey(binding=binding))
    ctl, *_ = make_ctl({"hotkey": "Ctrl+Alt+K"})
    ctl.install(None)

    ctl.uninstall()

    assert ctl.description == DEFAULT_TEXT
