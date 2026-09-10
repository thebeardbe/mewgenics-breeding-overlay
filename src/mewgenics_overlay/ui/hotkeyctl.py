"""Global hotkey: binding the config value to RegisterHotKey and the shortcut.

A thin controller in the same shape as :class:`ZoomController` and
:class:`ThemeController`: :class:`~mewgenics_overlay.ui.palette.PaletteWindow`
owns the user-facing API (``install_hotkey``, ``_set_hotkey``,
``hotkey_active``) and delegates here.

Two combinators follow the same binding:

  * the global registration (``ui/hotkey.py``; Windows only), and
  * a focused-window ``QShortcut``, which keeps the chosen combo usable when
    the global grab is unavailable (Linux/Wayland, or a taken Windows combo).

There is no fallback combination list: the user picks one combo and the app
never silently changes it.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtGui import QKeySequence, QShortcut

from mewgenics_overlay.ui import hotkey as hotkey_mod
from mewgenics_overlay.ui.hotkeybinding import (
    DEFAULT_TEXT,
    HotkeyBinding,
    binding_or_default,
    parse,
)

log = logging.getLogger("mewgenics_overlay.hotkey")


class HotkeyController:
    """Owns the platform hotkey handle, the shortcut and their persistence.

    Collaborators are injected: the settings dict plus a saver, a status-line
    setter, the window toggle and the shortcut's parent widget. ``on_change``
    (optional) is called with the active description after install and after
    every accepted rebind, so the tray tooltip can follow.
    """

    def __init__(self, settings: dict, save_settings: Callable[[], None],
                 on_status: Callable[[str], None],
                 toggle: Callable[[], None], parent=None):
        self._settings = settings
        self._save = save_settings
        self._on_status = on_status
        self._toggle = toggle
        self._parent = parent
        self._hotkey: Optional[hotkey_mod.Hotkey] = None
        self._shortcut: Optional[QShortcut] = None
        self._installed = False
        self._on_change: Optional[Callable[[str], None]] = None

    # ── state ─────────────────────────────────────────────────────────────
    @property
    def active(self) -> bool:
        """True when the global grab is live (Windows).

        While it is, the focused-window shortcut is disabled so a single
        keypress cannot toggle the overlay twice.
        """
        return self._hotkey is not None and self._hotkey.active

    @property
    def description(self) -> str:
        if self._hotkey is None:
            return DEFAULT_TEXT
        return self._hotkey.description

    # ── lifecycle ─────────────────────────────────────────────────────────
    def install(self, app, on_change: Optional[Callable[[str], None]] = None
                ) -> None:
        """Register the configured combo and arm the focused-window shortcut.

        Idempotent once a real install has happened: a second call would
        otherwise install another native filter, registration and
        ``QShortcut``, so it only refreshes the change listener and keeps the
        existing grab. A config-only placeholder left by an earlier
        :meth:`set_binding` (before a ``QApplication`` existed) is *not* an
        install: it is replaced here, so the pending choice is actually
        registered instead of silently leaving the tray as the only toggle.
        """
        self._on_change = on_change
        if self._installed:
            log.info("global hotkey already installed; keeping the existing "
                     "grab and shortcut")
            return
        # Read the binding from config: ``set_binding`` persists the pending
        # choice there before any install, so this picks it up whether or not
        # a placeholder handle already exists.
        binding = binding_or_default(self._settings.get("hotkey"))
        self._hotkey = hotkey_mod.install(app, self._toggle, binding)
        self._shortcut = QShortcut(QKeySequence(binding.format()),
                                   self._parent, activated=self._toggle)
        # The shortcut is the *fallback*: when the global grab is live it must
        # stay disabled or the focused overlay would toggle twice per press.
        self._installed = True
        self._sync_shortcut()
        self._on_status(self._status_text())
        self._notify()

    def uninstall(self) -> None:
        """Release the grab and drop the shortcut (on quit)."""
        self._installed = False
        if self._hotkey is not None:
            self._hotkey.uninstall()
            self._hotkey = None
        if self._shortcut is not None:
            self._shortcut.setEnabled(False)
            self._shortcut.deleteLater()
            self._shortcut = None

    # ── user action (Settings tab) ────────────────────────────────────────
    def set_binding(self, text: str) -> tuple[bool, str]:
        """Validate *text*, rebind, persist and report ``(ok, error)``.

        On failure the previous binding stays in force (the platform layer
        restores it) and the user gets the readable reason.
        """
        binding = parse(text)
        if binding is None:
            return False, ("choose at least one modifier (Ctrl, Alt or Shift) "
                           "and one letter A to Z")
        if self._hotkey is None:
            # No QApplication yet (headless harness, or a choice made before
            # app startup). Keep a config-only placeholder and let the next
            # install() replace it with the real platform grab. The
            # placeholder is never taken for an install (see ``_installed``).
            self._hotkey = hotkey_mod.Hotkey(None, binding=binding)
        ok, error = self._hotkey.rebind(binding)
        if not ok:
            if self.active:
                self._on_status(f"hotkey unchanged: {self.description}")
            else:
                # The restore-on-failure failed too, so nothing is registered:
                # only the focused window and the tray icon remain.
                self._on_status(
                    "global hotkey unavailable: only the overlay window "
                    "responds; use the tray icon")
            self._sync_shortcut()
            self._notify()
            return False, error or "that combination is not available"
        self._store(binding)
        self._sync_shortcut()
        self._on_status(self._status_text())
        self._notify()
        return True, ""

    # ── internals ─────────────────────────────────────────────────────────
    def _sync_shortcut(self) -> None:
        """Enable the focused-window shortcut only when no global grab is
        live, so one keypress never fires the toggle twice on Windows."""
        if self._shortcut is not None:
            self._shortcut.setEnabled(not self.active)

    def _store(self, binding: HotkeyBinding) -> None:
        self._settings["hotkey"] = binding.format()
        self._save()
        if self._shortcut is not None:
            self._shortcut.setKey(QKeySequence(binding.format()))

    def _status_text(self) -> str:
        if self.active:
            return f"global hotkey: {self.description}"
        return f"hotkey {self.description} (overlay window only)"

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(self.description)
        except Exception:
            log.exception("hotkey change listener failed")
