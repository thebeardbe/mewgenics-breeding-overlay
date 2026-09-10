"""PinningStore - the explicit keep-list ("pin for breeding") for one save.

Extracted from ``PaletteWindow`` (god-file split): owns the per-save pinned
``unique_id`` list in the settings dict, forgets any pinned cat that is ``Gone``
from the live save, and mirrors the result onto each cat's ``is_pinned`` flag.

Window-agnostic: the live settings dict, a "persist settings" callable and an
``on_change`` callable (the host re-renders its tables) arrive from the host.
The persistence format is unchanged - ``settings["pinned"][save_path]`` is a
list of ``unique_id`` values, written through the injected callback.
"""

from __future__ import annotations

from typing import Callable, Optional


class PinningStore:
    """Per-save keep-list backed by the settings dict."""

    def __init__(
        self,
        settings: dict,
        save_settings: Callable[[], None],
        on_change: Callable[[], None],
    ) -> None:
        self._settings = settings
        self._save_settings = save_settings
        self._on_change = on_change

    def store(self) -> list:
        """The pinned ``unique_id`` list for the current save (created lazily)."""
        store = self._settings.setdefault("pinned", {})
        key = self._settings.get("save_path") or ""
        return store.setdefault(key, [])

    def sync(self, session: Optional[object]) -> None:
        """Apply the saved keep-list and forget cats gone from the save.

        A pinned cat that no longer exists (status ``Gone``) or has vanished
        from the save is dropped from both the store and the settings file.
        """
        if session is None:
            return
        store = self.store()
        present = {c.unique_id for c in session.cats
                   if getattr(c, "status", "") != "Gone"}
        fresh = [uid for uid in store if uid in present]
        if len(fresh) != len(store):
            store[:] = fresh
            self._save_settings()
        for c in session.cats:
            c.is_pinned = c.unique_id in store

    def set_pinned(self, cat, on: bool) -> None:
        """Pin/unpin a cat (kept as a breeder).

        The keep-list is persisted and the host is notified **only when the
        stored state actually changed**: re-pinning an already-pinned cat
        (or unpinning one that was never pinned) never writes the settings
        file. The requested state is still mirrored onto ``cat.is_pinned``,
        so a cat whose flag had drifted out of sync with the store is
        repaired; when that repair actually changed the flag the host is
        notified (so its marker repaints) without a settings write.
        """
        store = self.store()
        uid = cat.unique_id
        previous = bool(getattr(cat, "is_pinned", False))
        cat.is_pinned = on
        if on and uid not in store:
            store.append(uid)
        elif not on and uid in store:
            store.remove(uid)
        else:
            # The store already holds the requested state, so there is nothing
            # to persist. The cat's own flag may still have drifted from it;
            # if this no-op repaired the flag, tell the host to repaint its
            # marker even though the settings file is untouched.
            if previous != on:
                self._on_change()
            return
        self._save_settings()
        self._on_change()
