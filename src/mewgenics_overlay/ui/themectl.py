"""ThemeController - which theme is active, and what switching it does.

Extracted from ``PaletteWindow`` (god-file split): owns the active theme key,
the toggle order, the settings key and the exact sequence of a theme switch
(validate -> activate -> persist -> repaint). The theme registry itself stays
in ``ui/theme.py``; this unit never redefines keys, order or titles.

Window-agnostic: the host passes two callbacks for the parts only it can do -
``style_widgets(css)`` (app stylesheet, palette stylesheet and the widgets
that re-derive their own colours) and ``refresh()`` (partner rows, focused cat
panel, donations) - plus an optional ``on_active(key)`` readout for the
Settings tab. No reference to ``PaletteWindow``.

Theme keys, cycle order and wording are unchanged.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject

import mewgenics_overlay.ui.theme as _theme


class ThemeController(QObject):
    """Active theme: validation, persistence and the restyle/refresh order.

    ``settings`` is the live settings dict and ``save_settings`` persists it
    after a switch. ``style_widgets(css)`` applies the new stylesheet to the
    app and the palette and re-derives the widget-local colours;
    ``refresh()`` re-renders everything that cached theme colours.
    """

    def __init__(
        self,
        settings: dict,
        save_settings: Callable[[], None],
        style_widgets: Callable[[str], None],
        refresh: Callable[[], None],
        on_active: Optional[Callable[[str], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._save_settings = save_settings
        self._style_widgets = style_widgets
        self._refresh = refresh
        self._on_active = on_active

    # ── state ──────────────────────────────────────────────────────────────
    @staticmethod
    def active_theme() -> str:
        return _theme.active_theme()

    def toggle_theme(self) -> None:
        """Switch to the next theme in registry order (wraps around)."""
        keys = list(_theme.THEMES)
        current = _theme.active_theme()
        self.apply_theme(keys[(keys.index(current) + 1) % len(keys)])

    def apply_theme(self, key: str) -> None:
        """Switch the active theme, restyle the app and re-render colours.

        An unknown key is ignored (the registry is the single source of
        truth), so a stale stored key can never leave the UI half-restyled.
        """
        if key not in _theme.THEMES:
            return
        _theme.set_theme(key)
        self._settings["theme"] = key
        self._save_settings()
        if self._on_active is not None:
            self._on_active(key)
        # The palette carries its own stylesheet (it shadows the app-wide
        # one), so the host must refresh both or nothing visually changes.
        self._style_widgets(_theme.stylesheet())
        self._refresh()
