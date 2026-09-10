"""AssetLoader - one-shot background load of the resources.gpak effect tables.

Extracted from ``PaletteWindow`` (god-file split): owns the single background
load of ``resources.gpak`` (the in-game effect texts for birth defects and the
furniture definitions behind room Stimulation/Comfort), the one-shot guard,
the worker thread plus its lock, and the distinction between "no gpak on this
machine" (normal, no load is started) and "the gpak exists but failed to
parse" (a compute failure worth a status line).

Window-agnostic: ``on_status(text)`` receives the failure line and the
optional ``on_ready()`` fires on the UI thread once usable assets are in
place, so the host can refresh the widgets that need them. ``assets`` exposes
the loaded :class:`GameAssets` (or ``None``).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QObject

from mewgenics_overlay.core.gameassets import GameAssets, locate_gpak

log = logging.getLogger("mewgenics_overlay.ui")

# One status line for both gpak failure modes (found but unreadable, or read
# but yielding no usable tables), so the wording never drifts.
_READ_FAILED_STATUS = ("⚠ could not read resources.gpak - defect "
                       "effect text unavailable, see the log")


@dataclass
class _AssetLoad:
    """One finished background gpak load: the assets plus any parse error.

    ``drain`` must tell "no resources.gpak was found" (no load is started, so
    no result ever arrives) from "the gpak exists but failed to parse" (a
    result with ``error`` set). The first is a normal optional feature being
    absent; the second is a compute failure worth a status line.
    """

    assets: Optional[GameAssets] = None
    error: Optional[Exception] = None


class AssetLoader(QObject):
    """Owns the asynchronous ``resources.gpak`` load and its result drain.

    ``on_status(text)`` is called with the failure line when a found gpak
    cannot be read; ``on_ready()`` runs once, on the UI thread, after usable
    assets have been adopted.
    """

    def __init__(
        self,
        on_status: Callable[[str], None],
        on_ready: Optional[Callable[[], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._on_status = on_status
        self._on_ready = on_ready
        self._lock = threading.Lock()
        self._started = False
        self._result: Optional[_AssetLoad] = None
        self._assets: Optional[GameAssets] = None

    @property
    def assets(self) -> Optional[GameAssets]:
        """The loaded gpak effect tables, or ``None`` while absent."""
        return self._assets

    def start(self) -> None:
        """Load resources.gpak effect tables off the UI thread (once)."""
        if self._started:
            return
        self._started = True
        path = locate_gpak()
        if not path:
            # No gpak on this machine: the optional feature is simply absent.
            log.info("no resources.gpak found - defect effect text disabled")
            return

        def work():
            result = _AssetLoad()
            try:
                result.assets = GameAssets(path)
            except Exception as exc:
                # The gpak exists but failed to parse: log the reason and pass
                # the failure on so the drain can surface it (compute-failed,
                # distinct from "no gpak found").
                log.exception("failed to read resources.gpak %s", path)
                result.error = exc
            with self._lock:
                self._result = result

        threading.Thread(target=work, name="gpak-assets", daemon=True).start()

    def drain(self) -> None:
        """Collect a finished gpak load (called from the host's poll tick)."""
        with self._lock:
            result = self._result
            self._result = None
        if result is None:
            return
        if result.error is not None:
            # The gpak was found but unreadable: say so instead of silently
            # showing no effect text ("no data" vs "compute failed").
            self._on_status(_READ_FAILED_STATUS)
            return
        assets = result.assets
        if assets is None:
            return
        if not assets.ok:
            # The gpak was read but held no usable tables: a parse failure,
            # not a missing feature. Surface it and do not announce ready.
            log.warning("resources.gpak yielded no usable asset tables")
            self._assets = None
            self._on_status(_READ_FAILED_STATUS)
            return
        self._assets = assets
        if self._on_ready is not None:
            self._on_ready()
