"""Palette widget construction and signal wiring.

Extracted from ``PaletteWindow`` (god-file split): ``build(window)`` creates
the whole widget tree (tab widget, Breeding page, chrome, search box, focus
panel, partner table, best-match bar, room bar, Donations tab, Settings tab
with the save panel, the update-notice corner and the size grip) and connects
the signals that drive the window's own methods. It holds no data and no
behaviour beyond construction and wiring; every label, tooltip, order, margin,
spacing and object name is unchanged from the pre-extraction window.

The window exposes its `_tablectl` coordinator for the row/focus actions;
``build`` creates that coordinator itself (``_build_coordinator``) once the
widgets exist but before it wires any signal, so no callback can fire against
an unset `window._tablectl` during construction.
"""

from __future__ import annotations

import mewgenics_overlay.ui.theme as _theme
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.ui import config as cfg
from mewgenics_overlay.ui import hotkeybinding
from mewgenics_overlay.ui import windowstate as _win_state
from mewgenics_overlay.ui.bestmatch import BestMatchBar, SAFE_CAP_DEFAULT
from mewgenics_overlay.ui.chrome import TopBar
from mewgenics_overlay.ui.focuspanel import FocusedCatPanel
from mewgenics_overlay.ui.partneractions import PartnerActions
from mewgenics_overlay.ui.partnertable import COL_TIPS, PartnerTableWidget
from mewgenics_overlay.ui.roombar import RoomBar
from mewgenics_overlay.ui.savepanel import SavePanel
from mewgenics_overlay.ui.searchbox import SearchBox
from mewgenics_overlay.ui.tablectl import TableCoordinator
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt
from mewgenics_overlay.ui.updatenotice import UpdateNotice
from mewgenics_overlay.ui.windowstate import WindowController

_ROOT_SPACING = 6   # shared by the palette root layout and the SearchBox


def _clamped_float(raw, default: float, floor: float | None = None,
                   ceil: float | None = None) -> float:
    """Coerce *raw* to a float inside [floor, ceil], falling back on *default*."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if floor is not None:
        value = max(value, floor)
    if ceil is not None:
        value = min(value, ceil)
    return value


def build(window) -> None:
    """Construct the palette widget tree on *window* and wire its signals."""
    outer = QVBoxLayout(window)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    tabs = QTabWidget()
    tabs.setDocumentMode(True)
    outer.addWidget(tabs)

    window._page_main = QWidget()
    root = QVBoxLayout(window._page_main)
    root.setContentsMargins(10, 8, 10, 10)
    root.setSpacing(_ROOT_SPACING)
    tabs.addTab(window._page_main, "Breeding")

    _build_chrome(window, outer)
    _build_breeding(window, root)
    _build_coordinator(window)
    _build_donations(window, tabs)
    _build_settings(window, tabs, outer)

    window._save_panel.refresh()
    window._tabs = tabs
    _wire_ui(window)


def _build_chrome(window, outer: QVBoxLayout) -> None:
    """Header bar (drag grip, title, status, pin/click-through/hide)."""
    window._chrome = TopBar(
        pinned=_win_state.PIN_DEFAULT,
        click_through=_win_state.CLICK_THROUGH_DEFAULT,
        on_pin=window._toggle_pin,
        on_click_through=window._on_ct_clicked,
        on_hide=window._on_close_clicked,
    )
    window._win = WindowController(
        window, window._chrome, window._settings,
        lambda: cfg.save(window._settings))
    outer.insertWidget(0, window._chrome)


def _build_breeding(window, root: QVBoxLayout) -> None:
    """Breeding page: search, focused cat, best match, table and detail."""
    # search (owns its own line edit + results dropdown)
    window._searchbox = SearchBox(
        lambda: window._session,
        lambda key: window._tablectl.on_search_chosen(key),
        window._on_search_cleared,
        spacing=_ROOT_SPACING,
    )
    root.addWidget(window._searchbox)

    # focused cat summary
    window._focus_panel = FocusedCatPanel()
    root.addWidget(window._focus_panel)
    row2 = QHBoxLayout()
    window._room_bar = RoomBar(
        session_getter=lambda: window._session,
        assets_getter=lambda: window._assets.assets,
        focus_getter=lambda: window._tablectl.focus,
        on_change=window._on_room_change,
    )
    window._btn_swap = QPushButton("Hide blocked rows")
    window._btn_swap.setCheckable(True)
    window._btn_swap.setToolTip(_wt(
        "When checked, pairs that cannot breed (direct family, hater, "
        "sexuality blocks) are hidden instead of listed below."
    ))
    row2.addWidget(window._room_bar)
    row2.addStretch(1)
    row2.addWidget(window._btn_swap)
    _row_holder = QWidget()
    _row_holder.setLayout(row2)
    window._focus_panel.append_row(_row_holder)

    # best-match banner (+ safe-mode switch) - self-contained widget that
    # reads the live rows/focus/room values through these callables.
    window._best_bar = BestMatchBar(
        on_select=lambda key: window._tablectl.on_best_selected(key),
        rows_getter=lambda: window._tablectl.rows,
        focus_getter=lambda: window._tablectl.focus,
        stim_getter=window._stim_value,
        comfort_getter=window._comfort_value,
        malady_lines=lambda row, stim, effect: (
            window._table.pair_malady_lines(row, stim, effect)),
        effect_of=lambda a, b, n: window._tablectl.effect_for_name(a, b, n),
        safe_risk_cap=lambda: _clamped_float(
            window._settings.get("safe_risk_cap", SAFE_CAP_DEFAULT),
            SAFE_CAP_DEFAULT, floor=1.0, ceil=100.0),
        spacing=_ROOT_SPACING,
    )
    root.addWidget(window._best_bar)

    # partners
    window._table = PartnerTableWidget(window)
    window._table.set_header_tooltips(COL_TIPS, _wt)
    root.addWidget(window._table, 1)

    # detail strip
    window._detail = QLabel("Select a partner row for inheritance detail.")
    window._detail.setWordWrap(True)
    window._detail.setObjectName("muted")
    # can embed partner names (save-derived) - never auto-rich-text them
    window._detail.setTextFormat(Qt.TextFormat.PlainText)
    window._detail.setToolTip(_wt(
        "Information about the row you have highlighted:\n"
        "• What each kitten stat could come out as (the possible range "
        "per parent).\n"
        "• Kittens this pair has already produced together."
    ))
    root.addWidget(window._detail)

    # partner-row interaction: selection detail, right-click pin menu
    # and double-click re-focus (ui/partneractions.py).
    window._actions = PartnerActions(
        window._table,
        window._detail,
        window._stim_value,
        lambda row, stim, effect: window._table.pair_malady_lines(
            row, stim, effect),
        lambda a, b, n: window._tablectl.effect_for_name(a, b, n),
        on_pin=window.set_pinned,
        on_focus=window.set_focus,
        parent=window,
    )


def _build_coordinator(window) -> None:
    """Create the row/focus coordinator before any signal can fire.

    Built here (not in the window's ``__init__``) so it exists before
    ``_wire_ui`` connects the callbacks that dereference it, and so no
    constructor-time signal raised during the rest of the build can hit an
    unset ``window._tablectl``.
    """
    window._tablectl = TableCoordinator(
        table=window._table,
        focus_panel=window._focus_panel,
        searchbox=window._searchbox,
        best_bar=window._best_bar,
        room_bar=window._room_bar,
        detail=window._detail,
        session_getter=lambda: window._session,
        assets_getter=lambda: window._assets.assets,
        schedule=window._schedule_partners,
        on_selected=window._on_partner_selected,
        parent=window,
    )


def _build_donations(window, tabs: QTabWidget) -> None:
    """Donations tab plus the update notice on the tab-row corner."""
    # Donations tab (last, so it exists before sessions arrive)
    from mewgenics_overlay.ui.donations_tab import DonationsTab

    window._donations_tab = DonationsTab(palette=window)
    tabs.addTab(window._donations_tab, "Donations")
    # the update notice sits on the right of the tab row
    window._update_notice = UpdateNotice(
        window._settings, lambda: cfg.save(window._settings))

    corner = QWidget()
    cl = QHBoxLayout(corner)
    cl.setContentsMargins(0, 0, 4, 0)
    cl.setSpacing(2)
    cl.addWidget(window._update_notice)
    tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)


def _build_settings(window, tabs: QTabWidget, outer: QVBoxLayout) -> None:
    """⚙ Settings tab (save slots, appearance, help) and the size grip."""
    # ⚙ Settings tab (save / appearance / zoom / help). The save-slot
    # cards and the picker are their own widget (ui/savepanel.py), built
    # here where those controls used to live in this window.
    from mewgenics_overlay.ui.settings_tab import SettingsTab

    window._save_panel = SavePanel(
        window._settings, on_open=window.open_save)
    actions = {
        "set_theme": window.apply_theme,
        "zoom_in": window._zoom_inc,
        "zoom_out": window._zoom_dec,
        "zoom_reset": window._zoom_default,
        "about": window._show_about,
        "report": window._open_report,
        "set_check_updates": window._set_update_check,
        "set_hotkey": window._set_hotkey,
    }
    # Desktop-shortcut actions are optional host capabilities: only wire the
    # keys a given host provides (the real palette always has both).
    for key, attr in (("setup_desktop_shortcut", "_setup_desktop_shortcut"),
                      ("remove_desktop_shortcut", "_remove_desktop_shortcut")):
        handler = getattr(window, attr, None)
        if handler is not None:
            actions[key] = handler
    window._settings_tab = SettingsTab(
        actions,
        window._save_panel,
        titles={k: _theme.THEMES[k]["title"] for k in _theme.THEMES},
    )
    tabs.addTab(window._settings_tab, "⚙ Settings")
    # Sync the checkbox with the persisted setting (default on) so the UI
    # matches the config at startup. The setter blocks its own signal, so
    # this never writes the unchanged value back to disk.
    window._settings_tab.set_check_updates(
        bool(window._settings.get("check_for_updates", True)))
    # Sync the hotkey widgets with the persisted combo (validated at load
    # time); the setter blocks its signals so it never rebinds at startup.
    window._settings_tab.set_hotkey(
        window._settings.get("hotkey", hotkeybinding.DEFAULT_TEXT))
    # resize handle in the bottom-right corner (frameless window)
    size_row = QHBoxLayout()
    size_row.setContentsMargins(6, 0, 6, 4)
    size_row.addStretch(1)
    grip_w = QSizeGrip(window)
    grip_w.setFixedSize(22, 22)
    grip_w.setToolTip("Drag the corner to resize the window")
    size_row.addWidget(grip_w)
    outer.addLayout(size_row)


def _wire_ui(window) -> None:
    """Connect the signals that drive the window and its row coordinator."""
    window._table.itemSelectionChanged.connect(window._on_partner_selected)
    window._table.itemDoubleClicked.connect(window._on_partner_double)
    window._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    window._table.customContextMenuRequested.connect(window._show_breeding_menu)
    window._table.horizontalHeader().sectionClicked.connect(
        window._on_header_clicked)
    window._btn_swap.toggled.connect(
        lambda _checked: window._tablectl.recompute_partners())
