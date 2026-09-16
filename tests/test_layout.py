"""``ui/layout.py``: the palette widget tree built by ``build(window)``.

``build`` is the god-file split's construction step: it creates the whole
widget tree on a host window and connects the signals that reach the host's
own methods (and, lazily, its ``_tablectl`` row coordinator). These tests
drive it with a **real QWidget standing in for PaletteWindow** - only the
attributes ``build`` assigns and the callbacks it connects are provided.

Hermetic by construction: the campaign-slot scan (``discovery.find_all_saves``)
is stubbed and ``config.save`` is a no-op, so nothing touches the user's real
save folders or config. No save, watcher, thread or timer is started.

Note on idempotence: ``build`` is **not** idempotent. A second call does not
raise, but it creates a fresh tree and repoints the window's attributes at it;
Qt also warns that the window already has a layout. The second-call test
asserts that observable behaviour rather than pretending it is a no-op.
"""

from __future__ import annotations

import os
import sys

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QSizeGrip,
    QTabWidget,
    QTableWidgetItem,
    QWidget,
)

from mewgenics_overlay.core import discovery  # noqa: E402
from mewgenics_overlay.ui import config as _cfg  # noqa: E402
from mewgenics_overlay.ui import layout as _layout  # noqa: E402
from mewgenics_overlay.ui import partneractions as _pa  # noqa: E402
from mewgenics_overlay.ui import shortcutworker  # noqa: E402
from mewgenics_overlay.ui.bestmatch import BestMatchBar  # noqa: E402
from mewgenics_overlay.ui.chrome import TopBar  # noqa: E402
from mewgenics_overlay.ui.donations_tab import DonationsTab  # noqa: E402
from mewgenics_overlay.ui.focuspanel import FocusedCatPanel  # noqa: E402
from mewgenics_overlay.ui.partneractions import PartnerActions  # noqa: E402
from mewgenics_overlay.ui.partnertable import PartnerTableWidget  # noqa: E402
from mewgenics_overlay.ui.roombar import RoomBar  # noqa: E402
from mewgenics_overlay.ui.savepanel import SavePanel  # noqa: E402
from mewgenics_overlay.ui.searchbox import SearchBox  # noqa: E402
from mewgenics_overlay.ui.settings_tab import SettingsTab  # noqa: E402
from mewgenics_overlay.ui.updatenotice import UpdateNotice  # noqa: E402
from mewgenics_overlay.ui.windowstate import WindowController  # noqa: E402


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No real slot scan and no real config write during a build."""
    monkeypatch.setattr(discovery, "find_all_saves", lambda: [])
    monkeypatch.setattr(_cfg, "save", lambda settings: None)


class FakePalette(QWidget):
    """A real QWidget with exactly the host surface ``layout.build`` touches.

    The methods ``build`` connects at construction time must exist as real
    callables (Qt connects them eagerly); the plain attributes it assigns are
    created by ``build`` itself. Every callback appends to ``calls`` so the
    wiring, not just the widget tree, can be asserted.
    """

    def __init__(self, settings=None):
        super().__init__()
        self._settings = dict(settings or {})
        self.calls = []
        self._session = None
        self._assets = SimpleNamespace(assets=None)
        self._tablectl = None        # created by PaletteWindow *after* build
        # layout.build hands the predicate ``lambda: window.in_game_available``
        # to PartnerActions as the "Show in game" availability gate; the real
        # palette derives it from a running bridge with a game connected.
        # Tests may set it False to hide the item.
        self.in_game_available = True

    # callbacks connected by layout.build / the widgets it creates
    def _on_ct_clicked(self, checked):
        self.calls.append(("click_through", checked))

    def _on_close_clicked(self):
        self.calls.append(("close",))

    def _on_search_cleared(self):
        self.calls.append(("search_cleared",))

    def _on_room_change(self):
        self.calls.append(("room_change",))

    def _stim_value(self):
        return 50.0

    def _comfort_value(self):
        return 0.0

    def _zoom_inc(self):
        self.calls.append(("zoom_in",))

    def _zoom_dec(self):
        self.calls.append(("zoom_out",))

    def _zoom_default(self):
        self.calls.append(("zoom_reset",))

    def _show_about(self):
        self.calls.append(("about",))

    def _open_report(self):
        self.calls.append(("report",))

    def _set_update_check(self, on):
        self.calls.append(("check_updates", on))

    def _set_hotkey(self, text):
        # layout.build wires this into the Settings tab's set_hotkey action;
        # the real PaletteWindow returns (ok, error) from HotkeyController.
        self.calls.append(("set_hotkey", text))
        return (True, "")

    @property
    def hotkey_active(self):
        return False

    def open_save(self, path):
        self.calls.append(("open_save", path))

    def open_save_manual(self, path):
        # layout.build hands this to SavePanel as the slot-card/picker open;
        # the real PaletteWindow notes a user choice and then loads.
        self.calls.append(("open_save_manual", path))
        self.open_save(path)

    def set_pinned(self, cat, on):
        self.calls.append(("pin_cat", cat, on))

    def set_focus(self, cat):
        self.calls.append(("focus", cat))

    def show_in_game(self, db_key):
        # layout.build injects this into PartnerActions as the context menu's
        # "Show in game" callback; the real PaletteWindow returns bool.
        self.calls.append(("show_in_game", db_key))
        return True

    def refresh_show_in_game(self):
        # layout.build calls this at the end of _wire_ui and installs it as the
        # coordinator's focus-change callback. The real re-gate of the card
        # button is covered by test_show_in_game_button.py.
        self.calls.append(("refresh_show_in_game",))

    def _on_show_selected_in_game(self):
        # layout.build connects the card's "Show in game" button to this slot.
        self.calls.append(("show_selected_in_game",))

    def apply_theme(self, key):
        self.calls.append(("theme", key))

    def _on_partner_selected(self):
        self.calls.append(("selected",))

    def _schedule_partners(self):
        self.calls.append(("schedule_partners",))

    def _on_partner_double(self, item):
        self.calls.append(("double", item))

    def _show_breeding_menu(self, pos):
        self.calls.append(("menu", pos))

    def _on_header_clicked(self, col):
        self.calls.append(("header", col))


class _PickShowInGameMenu:
    """Non-blocking QMenu stand-in that always picks "Show in game".

    The real ``exec`` opens a modal loop, so the layout test injects this to
    drive the partner context menu deterministically. It records itself so the
    test can assert the item was actually offered.
    """

    def __init__(self, log=None, parent=None):
        self.actions = []
        self.picked = None
        if log is not None:
            log.append(self)

    def addAction(self, text):
        action = SimpleNamespace(text=text)
        self.actions.append(action)
        return action

    def exec(self, global_pos):                       # noqa: A003 (Qt API)
        self.picked = next((a for a in self.actions
                            if a.text == "Show in game"), None)
        return self.picked


def _attach_shortcut_actions(host, ok=True, message="done"):
    """Give *host* the optional desktop-shortcut capability layout wires."""

    def setup():
        host.calls.append(("setup_shortcut",))
        return ok, message

    def remove():
        host.calls.append(("remove_shortcut",))
        return ok, message

    host._setup_desktop_shortcut = setup
    host._remove_desktop_shortcut = remove


class _InlineThread:
    """Run a worker target inline so its queued UI callback is predictable."""

    def __init__(self, target=None, name=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


@pytest.fixture
def inline_shortcut_threads(monkeypatch):
    monkeypatch.setattr(shortcutworker, "threading",
                        SimpleNamespace(Thread=_InlineThread))


def _pump(qapp, predicate, attempts=400):
    """Process events until *predicate* holds (bounded, never sleeps)."""
    for _ in range(attempts):
        if predicate():
            return True
        qapp.processEvents()
    return predicate()


@pytest.fixture
def make_host(qapp, inline_shortcut_threads):
    """Build a fake host window; tear it (and its tree) down afterwards."""
    hosts = []

    def _make(settings=None, build=True, with_shortcut=False,
              shortcut_ok=True, shortcut_message="done"):
        host = FakePalette(settings)
        if with_shortcut:
            _attach_shortcut_actions(host, ok=shortcut_ok,
                                     message=shortcut_message)
        hosts.append(host)
        if build:
            _layout.build(host)
        return host

    yield _make
    for host in hosts:
        host.hide()
        host.close()
        host.deleteLater()
    qapp.processEvents()


# ── 1. the widget tree ─────────────────────────────────────────────────────
def test_build_creates_the_three_tabs_in_order(make_host):
    host = make_host()

    assert isinstance(host._tabs, QTabWidget)
    assert host._tabs.count() == 3
    assert host._tabs.tabText(0) == "Breeding"
    assert host._tabs.tabText(1) == "Donations"
    assert host._tabs.tabText(2) == "⚙ Settings"
    assert host._tabs.widget(0) is host._page_main
    assert host._tabs.widget(1) is host._donations_tab
    assert host._tabs.widget(2) is host._settings_tab


def test_build_creates_every_expected_panel(make_host):
    host = make_host()

    assert isinstance(host._chrome, TopBar)
    assert isinstance(host._win, WindowController)
    assert isinstance(host._searchbox, SearchBox)
    assert isinstance(host._focus_panel, FocusedCatPanel)
    assert isinstance(host._table, PartnerTableWidget)
    assert isinstance(host._best_bar, BestMatchBar)
    assert isinstance(host._room_bar, RoomBar)
    assert isinstance(host._donations_tab, DonationsTab)
    assert isinstance(host._settings_tab, SettingsTab)
    assert isinstance(host._save_panel, SavePanel)
    assert isinstance(host._update_notice, UpdateNotice)
    assert isinstance(host._actions, PartnerActions)
    assert isinstance(host._detail, QLabel)


def test_breeding_page_layout_margins_and_spacing(make_host):
    host = make_host()

    root = host._page_main.layout()
    assert root is not None
    assert root.spacing() == _layout._ROOT_SPACING
    assert root.contentsMargins().left() == 10
    assert root.contentsMargins().top() == 8
    assert root.contentsMargins().right() == 10
    assert root.contentsMargins().bottom() == 10


def test_swap_checkbox_detail_strip_and_table_headers(make_host):
    host = make_host()

    assert host._btn_swap.isCheckable()
    assert host._btn_swap.text() == "Hide blocked rows"
    assert host._btn_swap.toolTip() != ""

    assert host._detail.textFormat() == Qt.TextFormat.PlainText
    assert host._detail.objectName() == "muted"
    assert host._detail.toolTip() != ""

    # every partner column carries an explanatory header tooltip
    for i in range(host._table.columnCount()):
        item = host._table.horizontalHeaderItem(i)
        assert item is not None and item.toolTip() != ""


def test_update_notice_is_the_tab_corner_widget(make_host):
    host = make_host()

    corner = host._tabs.cornerWidget(Qt.Corner.TopRightCorner)
    assert corner is not None
    assert corner.findChild(UpdateNotice) is host._update_notice


def test_settings_tab_owns_the_save_panel(make_host):
    host = make_host()

    assert host._settings_tab._save_panel is host._save_panel
    assert host._save_panel._settings is host._settings


def test_size_grip_exists_in_the_bottom_right(make_host):
    host = make_host()

    grip = host.findChild(QSizeGrip)
    assert grip is not None
    assert (grip.width(), grip.height()) == (22, 22)
    assert grip.toolTip() != ""


# ── 2. settings sync ───────────────────────────────────────────────────────
def test_update_checkbox_defaults_to_on_when_unset(make_host):
    host = make_host(settings={})

    assert host._settings_tab._update_check.isChecked() is True


def test_update_checkbox_reflects_a_persisted_opt_out(make_host):
    host = make_host(settings={"check_for_updates": False})

    assert host._settings_tab._update_check.isChecked() is False


def test_toggling_the_update_checkbox_notifies_the_host(make_host):
    host = make_host(settings={"check_for_updates": True})

    host._settings_tab._update_check.setChecked(False)

    assert ("check_updates", False) in host.calls


def test_hotkey_widgets_sync_from_persisted_settings(make_host):
    host = make_host(settings={"hotkey": "Ctrl+Alt+K"})

    assert host._settings_tab._hotkey_mods["ctrl"].isChecked() is True
    assert host._settings_tab._hotkey_mods["alt"].isChecked() is True
    assert host._settings_tab._hotkey_mods["shift"].isChecked() is False
    assert host._settings_tab._hotkey_key.text() == "K"


def test_hotkey_change_reaches_the_host_callback(make_host):
    host = make_host(settings={"hotkey": "Ctrl+Shift+B"})

    host._settings_tab._hotkey_key.setText("k")

    assert ("set_hotkey", "Ctrl+Shift+K") in host.calls


def test_invalid_hotkey_change_never_reaches_the_host(make_host):
    host = make_host(settings={"hotkey": "Ctrl+B"})

    # Remove the only modifier: a bare key is not a valid combo, so the tab
    # must reject it itself instead of calling the host action.
    host._settings_tab._hotkey_mods["ctrl"].setChecked(False)

    assert not any(c[0] == "set_hotkey" for c in host.calls)


# ── 3. signal wiring reaches the host and its coordinator ──────────────────
def test_header_click_is_forwarded_to_the_host(make_host):
    host = make_host()

    host._table.horizontalHeader().sectionClicked.emit(4)

    assert ("header", 4) in host.calls


def test_row_selection_is_forwarded_to_the_host(make_host):
    host = make_host()

    host._table.itemSelectionChanged.emit()

    assert ("selected",) in host.calls


def test_context_menu_request_is_forwarded_to_the_host(make_host):
    from PySide6.QtCore import QPoint

    host = make_host()

    host._table.customContextMenuRequested.emit(QPoint(1, 2))

    assert ("menu", QPoint(1, 2)) in host.calls


def test_context_menu_show_in_game_reaches_the_host(make_host, monkeypatch,
                                                    qapp):
    # layout.build injects window.show_in_game into PartnerActions; picking
    # the menu item must land on the host's outbound path with the row's key.
    host = make_host()
    menus: list = []
    monkeypatch.setattr(_pa, "QMenu",
                        lambda parent=None: _PickShowInGameMenu(menus))

    partner = SimpleNamespace(name="Meeko", db_key=341, is_pinned=False)
    item = QTableWidgetItem("Meeko")
    item.setData(Qt.ItemDataRole.UserRole,
                 (SimpleNamespace(partner=partner), []))
    host._table.setRowCount(1)
    host._table.setItem(0, 0, item)
    host._table.show()
    qapp.processEvents()

    host._actions.show_breeding_menu(
        host._table.visualItemRect(item).center())

    assert [a.text for a in menus[0].actions] == [
        "Pin for breeding", "Show in game"]
    assert ("show_in_game", 341) in host.calls
    assert not any(c[0] == "pin_cat" for c in host.calls)


def test_context_menu_hides_show_in_game_when_no_game_is_connected(
        make_host, monkeypatch, qapp):
    # The predicate reaches the palette's in_game_available property: a bridge
    # with no game connected keeps the item out of the menu.
    host = make_host()
    host.in_game_available = False
    menus: list = []
    monkeypatch.setattr(_pa, "QMenu",
                        lambda parent=None: _PickShowInGameMenu(menus))

    partner = SimpleNamespace(name="Meeko", db_key=341, is_pinned=False)
    item = QTableWidgetItem("Meeko")
    item.setData(Qt.ItemDataRole.UserRole,
                 (SimpleNamespace(partner=partner), []))
    host._table.setRowCount(1)
    host._table.setItem(0, 0, item)
    host._table.show()
    qapp.processEvents()

    host._actions.show_breeding_menu(
        host._table.visualItemRect(item).center())

    assert [a.text for a in menus[0].actions] == ["Pin for breeding"]
    assert not any(c[0] == "show_in_game" for c in host.calls)


def test_swap_toggle_reaches_the_later_created_coordinator(make_host):
    # PaletteWindow creates _tablectl *after* build; the wired lambda must
    # resolve it at click time, not capture a missing attribute at build time.
    host = make_host()
    calls = []
    host._tablectl = SimpleNamespace(
        recompute_partners=lambda: calls.append(True))

    host._btn_swap.setChecked(True)
    assert calls == [True]

    host._btn_swap.setChecked(False)
    assert calls == [True, True]


def test_chrome_buttons_reach_the_host_callbacks(make_host):
    # The header carries click-through and hide only; the pin button and its
    # callback were removed with the unconditional always-on-top change.
    host = make_host()

    assert not hasattr(host._chrome, "_btn_pin")
    host._chrome._btn_ct.click()
    host._chrome._btn_close.click()

    assert not any(c[0] == "pin" for c in host.calls)
    assert any(c[0] == "click_through" for c in host.calls)
    assert ("close",) in host.calls


def test_search_box_callbacks_are_wired_to_the_host(make_host):
    host = make_host()

    # The box's session getter reads the live host session; clear fires the
    # host's reset guard.
    assert host._searchbox._session_getter() is None
    host._searchbox._on_clear()

    assert ("search_cleared",) in host.calls


# ── 4. second build (documented, non-idempotent behaviour) ─────────────────
def test_second_build_does_not_raise_but_replaces_the_tree(make_host):
    host = make_host()
    first_tabs = host._tabs
    first_table = host._table
    first_panel = host._focus_panel

    _layout.build(host)          # Qt warns about the second layout; no raise

    # The window's attributes point at a freshly built tree, not the first.
    assert host._tabs is not first_tabs
    assert host._table is not first_table
    assert host._focus_panel is not first_panel
    assert host._tabs.count() == 3
    assert host._tabs.tabText(0) == "Breeding"


# ── 5. optional desktop-shortcut host capability ───────────────────────────
# The Settings tab exposes Linux-only buttons that call injected actions. The
# real palette always provides both; a host without them must still build,
# and the settings actions must not appear in the injected map.
def test_shortcut_actions_are_wired_when_the_host_exposes_them(make_host):
    host = make_host(with_shortcut=True)

    actions = host._settings_tab._actions
    assert actions["setup_desktop_shortcut"] is host._setup_desktop_shortcut
    assert actions["remove_desktop_shortcut"] is host._remove_desktop_shortcut


def test_shortcut_buttons_reach_the_host(make_host, qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    host = make_host(with_shortcut=True)

    host._settings_tab._shortcut_set.click()
    host._settings_tab._shortcut_remove.click()

    # The worker serializes, so the Remove action only starts after the Set up
    # result was processed; pump the event loop until both have run.
    assert _pump(qapp,
                 lambda: ("remove_shortcut",) in host.calls) is True
    assert ("setup_shortcut",) in host.calls
    assert _pump(qapp, lambda: host._settings_tab._shortcut_result.text()
                 == "done") is True


def test_shortcut_setup_shows_a_busy_label_until_the_worker_finishes(
        make_host, qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    host = make_host(with_shortcut=True)

    host._settings_tab._shortcut_set.click()
    assert host._settings_tab._shortcut_result.text() == "Setting up\u2026"

    assert _pump(qapp, lambda: host._settings_tab._shortcut_result.text()
                 == "done") is True


def test_shortcut_failure_is_reflected_in_the_settings_hint(make_host, qapp):
    if not sys.platform.startswith("linux"):
        pytest.skip("desktop-shortcut controls are Linux-only")
    host = make_host(with_shortcut=True, shortcut_ok=False,
                     shortcut_message="could not install")

    host._settings_tab._shortcut_set.click()
    assert _pump(qapp, lambda: host._settings_tab._shortcut_result.text()
                 == "could not install") is True

    assert ("setup_shortcut",) in host.calls
    assert host._settings_tab._shortcut_result_error is True
    assert host._settings_tab._shortcut_result.text() == "could not install"


def test_build_is_safe_when_the_host_lacks_shortcut_actions(make_host):
    host = make_host()                      # default: no shortcut methods

    actions = host._settings_tab._actions
    assert "setup_desktop_shortcut" not in actions
    assert "remove_desktop_shortcut" not in actions
    if sys.platform.startswith("linux"):
        # The Linux controls exist but are inert without the action.
        host._settings_tab._shortcut_set.click()
        host._settings_tab._shortcut_remove.click()
    assert host._settings_tab is not None
