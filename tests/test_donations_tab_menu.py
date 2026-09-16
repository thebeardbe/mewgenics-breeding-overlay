"""Donations row menu: Pin/Unpin plus "Show in game" when a game is connected.

``DonationsTab._show_row_menu`` opens the per-row menu. Pin/Unpin is always
offered; "Show in game" appears only while a game is connected
(``palette.in_game_available``) and sends through the same outbound path as
the partner rows and the card button. A right-click on a row must open this
row menu. The overlay itself no longer has a window-wide right-click menu
(the window actions live on the tray), so Qt's CustomContextMenu routing to
the table is the contract that keeps row menus working.

These tests drive a real offscreen ``DonationsTab`` with a recording
``QMenu`` replacement (the real ``exec`` blocks) and a fake palette/host, so
no save, donation report or ``PaletteWindow`` is needed.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QTableWidgetItem,
    QWidget,
)

from mewgenics_overlay.ui import donations_tab as _dt  # noqa: E402
from mewgenics_overlay.ui.donations_tab import DonationsTab  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeMenu:
    """Scriptable QMenu stand-in: records action texts, returns a choice."""

    def __init__(self, controller, parent=None):
        self.controller = controller
        self.parent = parent
        self.actions = []
        controller.menus.append(self)

    def addAction(self, text):
        action = SimpleNamespace(text=text)
        self.actions.append(action)
        return action

    def exec(self, global_pos):                       # noqa: A003 (Qt API)
        self.controller.last_pos = global_pos
        if isinstance(self.controller.choice, int) and self.actions:
            return self.actions[self.controller.choice]
        return None


@pytest.fixture
def fake_menu(monkeypatch):
    """Replace the tab module's QMenu with a recording, non-blocking fake."""
    controller = SimpleNamespace(menus=[], choice=None, last_pos=None)

    def factory(parent=None):
        return _FakeMenu(controller, parent)

    monkeypatch.setattr(_dt, "QMenu", factory)
    return controller


class _FakePalette:
    """The row-menu surface the tab reads from ``palette``."""

    def __init__(self, available=True):
        self.in_game_available = available
        self.pins = []
        self.shown = []
        self._session = None

    def set_pinned(self, cat, on):
        self.pins.append((cat, on))

    def show_in_game(self, key):
        self.shown.append(key)
        return True


def make_cat(name="Kit", db_key=11, pinned=False):
    return SimpleNamespace(name=name, db_key=db_key, is_pinned=pinned,
                           age=1, base_stats={}, total_stats={})


def _install_row(tab, cat):
    slot = SimpleNamespace(
        npc="Tink", wants="kittens", count=1, candidates=[cat], advice=[],
        supported=True, active=True)
    tab._slots = [slot]
    tab._combo.addItem("Tink")
    tab._combo.setCurrentIndex(0)
    tab._table.setRowCount(1)
    item = QTableWidgetItem(cat.name)
    tab._table.setItem(0, 0, item)
    tab.resize(640, 320)
    tab.show()
    return item


def _make_tab(qapp, palette, cat=None):
    cat = cat or make_cat()
    tab = DonationsTab(palette=palette)
    _WIDGETS.append(tab)
    item = _install_row(tab, cat)
    qapp.processEvents()
    return tab, cat, item


#: Widgets built by a test, destroyed before the next one so Qt never has to
#: tear a shown table down at interpreter shutdown (which segfaults with this
#: PySide6 build).
_WIDGETS: list[QWidget] = []


@pytest.fixture(autouse=True)
def _destroy_widgets(qapp):
    yield
    while _WIDGETS:
        widget = _WIDGETS.pop()
        widget.hide()
        widget.close()
        widget.deleteLater()
    qapp.processEvents()


def _open_row_menu(tab, item):
    tab._show_row_menu(tab._table.visualItemRect(item).center())


# ── "Show in game" availability ────────────────────────────────────────────
def test_row_menu_offers_pin_and_show_in_game_when_connected(qapp, fake_menu):
    palette = _FakePalette(available=True)
    tab, cat, item = _make_tab(qapp, palette)

    _open_row_menu(tab, item)

    assert [a.text for a in fake_menu.menus[0].actions] == [
        "Pin for breeding", "Show in game"]


def test_row_menu_omits_show_in_game_when_no_game_connected(qapp, fake_menu):
    palette = _FakePalette(available=False)
    tab, cat, item = _make_tab(qapp, palette)

    _open_row_menu(tab, item)

    assert [a.text for a in fake_menu.menus[0].actions] == ["Pin for breeding"]


def test_choosing_show_in_game_sends_the_cat_key_through_the_outbound_path(
        qapp, fake_menu):
    palette = _FakePalette(available=True)
    tab, cat, item = _make_tab(qapp, palette)
    fake_menu.choice = 1

    _open_row_menu(tab, item)

    assert palette.shown == [cat.db_key]
    assert palette.pins == []


def test_choosing_pin_mirrors_the_cat_and_does_not_show_in_game(
        qapp, fake_menu):
    palette = _FakePalette(available=True)
    tab, cat, item = _make_tab(qapp, palette)
    fake_menu.choice = 0

    _open_row_menu(tab, item)

    assert palette.pins == [(cat, True)]
    assert palette.shown == []


def test_dismissing_the_row_menu_does_nothing(qapp, fake_menu):
    palette = _FakePalette(available=True)
    tab, cat, item = _make_tab(qapp, palette)
    fake_menu.choice = None

    _open_row_menu(tab, item)

    assert len(fake_menu.menus) == 1
    assert palette.pins == []
    assert palette.shown == []


# ── row menu is the menu a row right-click opens ───────────────────────────
class RowHost(QWidget):
    """Host window that provides the tab's palette surface.

    The overlay no longer defines a window-wide context menu, so this host has
    no ``contextMenuEvent`` override: a row right-click is handled entirely by
    the table's CustomContextMenu routing.
    """

    def __init__(self, available=True):
        super().__init__()
        self.in_game_available = available
        self.pins = []
        self.shown = []
        self._session = None

    def set_pinned(self, cat, on):
        self.pins.append((cat, on))

    def show_in_game(self, key):
        self.shown.append(key)
        return True


def test_row_right_click_opens_the_row_menu(qapp, fake_menu):
    host = RowHost(available=True)
    _WIDGETS.append(host)
    host.resize(700, 400)
    cat = make_cat()
    tab = DonationsTab(palette=host)
    _WIDGETS.append(tab)
    tab.setParent(host)
    item = _install_row(tab, cat)
    host.show()
    qapp.processEvents()

    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                              tab._table.visualItemRect(item).center(),
                              QPoint(1, 1))
    qapp.sendEvent(tab._table.viewport(), event)
    qapp.processEvents()

    assert len(fake_menu.menus) == 1           # the row menu was built
    assert [a.text for a in fake_menu.menus[0].actions] == [
        "Pin for breeding", "Show in game"]
