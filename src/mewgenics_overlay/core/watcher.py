"""Debounced save-file watcher (pure Python, no Qt dependency).

The game keeps an in-memory SQLite save and flushes it to ``.sav`` at game
checkpoints (day rollover, breeding, room changes, …). Mewgenics writes the
file in place, so we watch for size+mtime changes. A debounce window collapses
the game's burst of writes (and any ``-wal``/``-shm``/``-journal`` sidecar
activity) into a single refresh, mirroring MBM's approach.

The watcher runs on its own thread and calls ``on_change()`` after a quiet
period. It never blocks on the save: callers that open the file should copy it
first (MBM copies to a temp file to avoid file-handle contention with the game;
see ``safe_read_save``).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("mewgenics_overlay.watcher")

_WAL_SUFFIXES = ("-wal", "-shm", "-journal")


class SaveWatcher:
    """Poll a save file and fire ``on_change`` after it stops changing."""

    def __init__(
        self,
        path: str,
        on_change: Callable[[], None],
        debounce_seconds: float = 0.6,
        poll_seconds: float = 0.5,
    ):
        self.path = path
        self.on_change = on_change
        self.debounce = debounce_seconds
        self.poll = poll_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_signature: Optional[tuple] = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_signature = self._signature(self.path)  # prime: no initial fire
        self._thread = threading.Thread(
            target=self._run, name="mewgenics-save-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ── internals ──────────────────────────────────────────────────────────
    @staticmethod
    def _signature(path: str) -> Optional[tuple]:
        """(mtime_ns, size, inode) of the save, or None when missing/unreadable.

        inode catches atomic rewrites (write-to-temp + rename) where mtime and
        size would otherwise be identical.
        """
        try:
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            return None

    def _run(self) -> None:
        last = self._signature(self.path)   # primed in start(); never fire on it
        dirty = False
        quiet_since: Optional[float] = None
        while not self._stop.is_set():
            sig = self._signature(self.path)
            if sig is not None and sig != last:
                last = sig
                dirty = True
                quiet_since = time.monotonic()
            if (
                dirty
                and sig is not None
                and quiet_since is not None
                and time.monotonic() - quiet_since >= self.debounce
            ):
                dirty = False
                quiet_since = None
                if not self._stop.is_set():
                    try:
                        self.on_change()
                    except Exception:
                        # Never kill the watcher thread on a callback bug, but
                        # do leave a trace — silent reload-kills are the worst
                        # kind of failure.
                        log.exception("SaveWatcher change callback failed")
            self._stop.wait(self.poll)


def safe_read_save(path: str, dest_dir: Optional[str] = None) -> Optional[str]:
    """Copy a save (plus any WAL sidecars) to a temp file and return its path.

    Returns None when the file cannot be copied. The caller owns the returned
    path and should delete it afterwards. This mirrors MBM's crash-safety fix
    (issue #94): never open the live file while the game may be mid-write.
    """
    try:
        base = os.path.basename(path)
        # sqlite ``file:`` URIs treat '?' / '#' as query / fragment markers, and
        # the temp name below is later opened through such a URI. A hostile
        # save filename (e.g. ``evil.sav?mode=rw``) could smuggle URI params,
        # so temp names only keep safe characters.
        base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"mewgenics-{base}-", suffix=".sav", delete=False, dir=dest_dir
        )
        tmp.close()
        shutil.copyfile(path, tmp.name)
        for suffix in _WAL_SUFFIXES:
            sidecar = path + suffix
            if os.path.exists(sidecar):
                shutil.copyfile(sidecar, tmp.name + suffix)
        return tmp.name
    except OSError:
        return None
