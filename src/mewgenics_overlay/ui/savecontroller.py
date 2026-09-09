"""Save loading / live-watch / background-work orchestration for the overlay.

Extracted from ``PaletteWindow`` (v0.1.48 refactor): this class owns the
session state, the generation-token queue for background workers, and the
save watcher. ``PaletteWindow`` keeps the same public attribute names
(``pal._session`` etc.) as thin delegations, so ``gui_smoke.py`` and callers
are unaffected.

Threading model (unchanged from the palette): heavy work runs on daemon
threads and hands results back as ``(token, kind, payload)`` tuples that the
UI thread drains from ``drain()``; stale results (token older than the newest
request) are dropped by the caller.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

from mewgenics_overlay.core.maladies import defect_inheritance_rows
from mewgenics_overlay.core.session import ALIVE_STATUSES, Session
from mewgenics_overlay.core.watcher import SaveWatcher, safe_read_save
from mewgenics_overlay.vendor.breeding import tracked_offspring

log = logging.getLogger("mewgenics_overlay.savecontroller")


class SaveController:
    """Owns ``session`` plus every background (re)load / partner-scoring job.

    All state touched by the bug-dense load/reload paths lives here instead
    of in the window class, so the UI can be a thin coordinator.
    """

    def __init__(self) -> None:
        self._session: Optional[Session] = None
        self._watcher: Optional[SaveWatcher] = None
        self._lock = threading.Lock()
        self._pending: list[tuple] = []   # (token, kind, result)
        self._token = 0

    # ── session state ─────────────────────────────────────────────────────
    @property
    def session(self) -> Optional[Session]:
        return self._session

    @session.setter
    def session(self, sess: Optional[Session]) -> None:
        self._session = sess

    @property
    def token(self) -> int:
        return self._token

    def bump_token(self) -> int:
        """Invalidate in-flight work (e.g. after adopting a new session)."""
        with self._lock:
            self._token += 1
            return self._token

    def drain(self) -> list:
        with self._lock:
            items, self._pending = self._pending, []
        return items

    def _push(self, token: int, kind: str, payload) -> None:
        with self._lock:
            self._pending.append((token, kind, payload))

    # ── save watcher ──────────────────────────────────────────────────────
    def start_watcher(self, path: str, on_change: Callable[[], None]) -> None:
        if self._watcher is not None:
            self._watcher.stop()
        # The callback runs on the watcher thread; the UI must hand it over
        # via a Qt signal before touching widgets (see palette).
        self._watcher = SaveWatcher(path, on_change=on_change)
        self._watcher.start()

    def stop_watcher(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    # ── background jobs ───────────────────────────────────────────────────
    def schedule_reload(self, path: str) -> int:
        """Parse a *copy* of the save on a worker thread; result is drained
        as ``('session', Session)`` or ``('session_error', message)``."""
        token = self.bump_token()
        if not path:
            return token

        def work():
            tmp = safe_read_save(path)
            if tmp is None:
                return
            try:
                sess = Session(tmp)
            except Exception as exc:  # corrupt/hostile save must not kill us
                log.exception("save reload failed")
                self._push(token, "session_error", str(exc))
                return
            finally:
                try:
                    os.unlink(tmp)   # unlink can itself fail (e.g. AV lock)
                except OSError:
                    pass
            self._push(token, "session", sess)

        threading.Thread(target=work, name="save-reload", daemon=True).start()
        return token

    def schedule_partners(self, cat_key: int, max_rows: int,
                          show_blocked: Optional[int], include_adventure: bool,
                          order: str, stimulation: float,
                          comfort: float = 0.0) -> Optional[int]:
        """Rank partners + precompute defect rows off the UI thread. Drained
        as ``('partners', (cat_key, [(PartnerRow, kid_names), …]))`` or
        ``('partners_error', message)``."""
        with self._lock:
            session = self._session
        if session is None:
            return None
        token = self.bump_token()

        def work():
            try:
                cat = session.by_key.get(cat_key)
                if cat is None:
                    return
                rows = session.rank_partners(
                    cat,
                    max_partners=max_rows,
                    include_adventure=include_adventure,
                    show_blocked=show_blocked,
                    order=order,
                    stimulation=stimulation,
                )
                enriched = []
                for r in rows:
                    kids = tracked_offspring(cat, r.partner)
                    r.kitty_total = len(kids)
                    r.kitty_available = sum(
                        1 for k in kids
                        if getattr(k, "status", "") in ALIVE_STATUSES)
                    enriched.append((r, [k.name for k in kids]))
                    # Defect inheritance rows are a pure function of the pair
                    # (+ COI + room Stimulation): compute them ONCE here so
                    # cells, tooltips and Best-match reuse the result instead
                    # of re-walking shared ancestry on the UI thread.
                    try:
                        r.defect_rows = defect_inheritance_rows(
                            cat, r.partner, r.coi, stimulation=stimulation)
                    except Exception:
                        r.defect_rows = None
                self._push(token, "partners", (cat_key, enriched))
            except Exception as exc:  # keep the UI alive on parser surprises
                log.exception("partner scoring failed")
                self._push(token, "partners_error", str(exc))

        threading.Thread(target=work, name="partner-score", daemon=True).start()
        return token
