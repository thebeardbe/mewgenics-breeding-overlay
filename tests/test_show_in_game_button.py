"""The card's visible "Show in game" button.

``ui/layout.py`` builds the button into the selected-cat card
(``FocusedCatPanel``) and ``PaletteWindow`` owns its live state:
``refresh_show_in_game`` re-gates and re-tips it from the bridge state, the
game connection and the focused cat. The button is *hidden* unless a game is
connected (``in_game_available``) - a hidden button reserves no layout space,
matching the hidden context-menu item. With a game connected it is visible,
enabled only when a cat is focused. ``_on_show_selected_in_game``
delegates to ``show_focused_in_game`` (the shared guard: nothing focused logs
and returns, otherwise the focused key goes through ``show_in_game``, the one
outbound path, shared with the row context menu and the Ctrl+G shortcut in
``ui/app.py``). ``attach_bridge`` connects the controller's ``game_online``
signal so the button re-gates as the game connects and disconnects.

These tests build the **real** widget tree (``layout.build``) on a
``QWidget`` host that carries the real ``PaletteWindow`` methods under test.
The tree is built with a real ``TableCoordinator``, so the focus-change
callback (``on_focus_changed=window.refresh_show_in_game``) is the real
wiring: selecting a cat and clearing the selection must re-gate the button.

Hermetic: the campaign-slot scan and ``config.save`` are stubbed, and the
bridge is a recording fake, so no save, watcher or timer is started and no
window is shown (offscreen platform). The connect/disconnect test drives a
real ``BridgeController`` on an ephemeral loopback port by registering a fake
peer, so it never depends on the real game or a fixed port.

Gap: a real ``PaletteWindow`` is not constructed (its asset loader, watcher
and timers are heavy), so this proves the button's own wiring; the Ctrl+G
shortcut path in ``ui/app.py`` is covered by ``test_app_bootstrap.py``.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QPushButton,
    QWidget,
)

from mewgenics_overlay.core import discovery  # noqa: E402
from mewgenics_overlay.core.session import STAT_NAMES  # noqa: E402
from mewgenics_overlay.ui import config as _cfg  # noqa: E402
from mewgenics_overlay.ui import layout as _layout  # noqa: E402
from mewgenics_overlay.ui import palette  # noqa: E402
from mewgenics_overlay.ui import selectecho  # noqa: E402
from mewgenics_overlay.ui.bridgectl import BridgeController  # noqa: E402
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt  # noqa: E402

LOG_NAME = "mewgenics_overlay.ui"

PICK_CAT_TIP = "Pick a cat first, then ask the game to select them."
READY_TIP = "Ask the game to select this cat in game."


# ── fakes / helpers ────────────────────────────────────────────────────────
class _FakeSignal:
    """Minimal Qt-signal stand-in: records slots and emits synchronously."""

    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in self._slots:
            slot(*args)


class _FakeBridge:
    """Records every select and scripts the delivery / connection state.

    Mirrors the surface ``show_in_game`` / ``bridge_available`` /
    ``in_game_available`` read from a real ``BridgeController``: ``running``,
    ``client_count``, ``game_online`` and ``send_select(key) -> int``.
    """

    def __init__(self, delivered=1, running=True, clients=1):
        self.delivered = delivered
        self.running = running
        self.client_count = clients
        self.keys = []
        self.game_online = _FakeSignal()

    def send_select(self, key):
        self.keys.append(key)
        return self.delivered


class _FakeConn:
    """Hashable peer stand-in for the bridge server's bookkeeping."""

    def close(self):
        pass


def make_cat(name, db_key):
    """Duck-typed Cat with every field the focus card reads."""
    base = {s: 4 for s in STAT_NAMES}
    return SimpleNamespace(
        name=name,
        db_key=db_key,
        gender="female",
        status="In House",
        room="Attic",
        generation=1,
        age=5,
        inbredness=0.0,
        base_stats=base,
        total_stats=dict(base),
        sexuality_raw=0.0,
        lovers=[],
        haters=[],
        disorders=[],
        defects=[],
        visual_mutation_entries=[],
        is_pinned=False,
        is_dead=False,
        must_breed=False,
    )


def _install_session(host, *cats):
    """Point the host's session lookup at the given cats (for named status)."""
    host._session = SimpleNamespace(by_key={c.db_key: c for c in cats})


class ShowInGameHost(QWidget):
    """A real QWidget with ``layout.build``'s host surface plus the *real*
    ``PaletteWindow`` methods this feature lives in.

    Only the host callbacks ``layout.build`` connects are stubs; the button
    state machine (``refresh_show_in_game``), the click slot
    (``_on_show_selected_in_game``), ``show_focused_in_game`` (the shared
    outbound guard), ``attach_bridge``, ``show_in_game``, ``set_focus`` and the
    ``bridge_available`` / ``in_game_available`` / ``_focus`` properties are
    bound straight from ``PaletteWindow``.
    """

    refresh_show_in_game = palette.PaletteWindow.refresh_show_in_game
    _on_show_selected_in_game = palette.PaletteWindow._on_show_selected_in_game
    show_focused_in_game = palette.PaletteWindow.show_focused_in_game
    attach_bridge = palette.PaletteWindow.attach_bridge
    show_in_game = palette.PaletteWindow.show_in_game
    _cat_name = palette.PaletteWindow._cat_name
    set_focus = palette.PaletteWindow.set_focus
    _on_search_cleared = palette.PaletteWindow._on_search_cleared
    bridge_available = palette.PaletteWindow.bridge_available
    in_game_available = palette.PaletteWindow.in_game_available
    _focus = palette.PaletteWindow._focus

    def __init__(self, settings=None):
        super().__init__()
        self._settings = dict(settings or {})
        self._session = None
        self._assets = SimpleNamespace(assets=None)
        self._tablectl = None            # created by layout.build
        self._bridge = None
        # The real window always carries one (PaletteWindow.__init__); the
        # outbound path records into the real filter.
        self._select_echo = selectecho.SelectEchoFilter()
        self.statuses = []
        self.calls = []

    # status sink the outbound path posts to
    def _set_status(self, text):
        self.statuses.append(text)

    # ── callbacks layout.build connects ────────────────────────────────────
    def _toggle_pin(self, checked):
        self.calls.append(("pin", checked))

    def _on_ct_clicked(self, checked):
        self.calls.append(("click_through", checked))

    def _on_close_clicked(self):
        self.calls.append(("close",))

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
        self.calls.append(("set_hotkey", text))
        return (True, "")

    def open_save(self, path):
        self.calls.append(("open_save", path))

    def open_save_manual(self, path):
        # layout.build hands this to SavePanel as the slot-card/picker open.
        self.calls.append(("open_save_manual", path))
        self.open_save(path)

    def set_pinned(self, cat, on):
        self.calls.append(("pin_cat", cat, on))

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


def _records(caplog, level):
    return [r for r in caplog.records if r.name == LOG_NAME
            and r.levelno == level]


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


@pytest.fixture
def make_host(qapp):
    """Build the real tree on a host; tear it down afterwards."""
    hosts = []

    def _make(settings=None):
        host = ShowInGameHost(settings)
        hosts.append(host)
        _layout.build(host)
        return host

    yield _make
    for host in hosts:
        host.close()
        host.deleteLater()
    qapp.processEvents()


# ── 1. the button in the card ──────────────────────────────────────────────
def test_the_selected_cat_card_has_a_show_in_game_button(make_host):
    host = make_host()

    btn = host._btn_show_in_game

    assert isinstance(btn, QPushButton)
    assert btn.text() == "Show in game"
    # It lives in the selected-cat card, not on the chrome or the tab bar.
    assert host._focus_panel.isAncestorOf(btn)


def test_the_button_is_hidden_and_disabled_at_build_time(make_host):
    host = make_host()

    btn = host._btn_show_in_game

    assert host.bridge_available is False
    assert host.in_game_available is False
    # Hidden, not merely disabled: with no game the card must not offer the
    # action at all (the context-menu item is hidden for the same reason).
    assert btn.isHidden() is True
    assert btn.isEnabled() is False


def test_a_hidden_button_reserves_no_layout_space(make_host):
    # A hidden button must not leave an empty gap in the card row: Qt's layout
    # item for it reports empty, so the row collapses around it.
    host = make_host()

    btn = host._btn_show_in_game
    assert btn.isHidden() is True
    row = btn.parentWidget().layout()
    assert row.indexOf(btn) >= 0
    assert row.itemAt(row.indexOf(btn)).isEmpty() is True


def test_a_visible_button_does_reserve_layout_space(make_host):
    host = make_host()
    host.attach_bridge(_FakeBridge(running=True, clients=1))

    btn = host._btn_show_in_game
    assert btn.isHidden() is False
    row = btn.parentWidget().layout()
    assert row.itemAt(row.indexOf(btn)).isEmpty() is False


# ── 2. the visibility / enablement state matrix ────────────────────────────
def test_a_running_bridge_with_a_game_but_no_cat_asks_the_user_to_pick_one(
        make_host):
    host = make_host()
    host.attach_bridge(_FakeBridge(running=True, clients=1))

    btn = host._btn_show_in_game

    assert host.bridge_available is True
    assert host.in_game_available is True
    assert btn.isHidden() is False
    assert btn.isEnabled() is False
    assert btn.toolTip() == _wt(PICK_CAT_TIP)


def test_a_running_bridge_with_no_game_hides_the_button(make_host):
    host = make_host()
    host.attach_bridge(_FakeBridge(running=True, clients=0))

    btn = host._btn_show_in_game

    assert host.bridge_available is True
    assert host.in_game_available is False
    assert btn.isHidden() is True
    assert btn.isEnabled() is False


def test_a_shown_cat_without_a_game_hides_the_button(make_host):
    host = make_host()
    host.set_focus(make_cat("Meeko", 341))

    btn = host._btn_show_in_game

    assert host.in_game_available is False
    assert btn.isHidden() is True
    assert btn.isEnabled() is False


def test_a_running_bridge_with_a_game_and_a_shown_cat_enables_the_button(
        make_host):
    host = make_host()
    host.attach_bridge(_FakeBridge(running=True, clients=1))
    host.set_focus(make_cat("Meeko", 341))

    btn = host._btn_show_in_game

    assert host.in_game_available is True
    assert btn.isHidden() is False
    assert btn.isEnabled() is True
    assert btn.toolTip() == _wt(READY_TIP)


def test_a_running_bridge_without_a_game_hides_it_even_with_a_shown_cat(
        make_host):
    host = make_host()
    host.attach_bridge(_FakeBridge(running=True, clients=0))
    host.set_focus(make_cat("Meeko", 341))

    btn = host._btn_show_in_game

    assert host.in_game_available is False
    assert btn.isHidden() is True
    assert btn.isEnabled() is False


def test_an_attached_but_stopped_bridge_keeps_it_hidden(make_host):
    host = make_host()
    host.attach_bridge(_FakeBridge(running=False, clients=1))
    host.set_focus(make_cat("Meeko", 341))

    btn = host._btn_show_in_game

    assert host.bridge_available is False
    assert host.in_game_available is False
    assert btn.isHidden() is True
    assert btn.isEnabled() is False


# ── 3. refresh on state changes (not only at build time) ───────────────────
def test_attaching_the_bridge_refreshes_a_button_that_waited(make_host):
    # Focus first: the button was built hidden and no bridge existed when
    # it was created, so only attach_bridge's refresh can show and enable it.
    host = make_host()
    host.set_focus(make_cat("Meeko", 341))
    assert host._btn_show_in_game.isHidden() is True
    assert host._btn_show_in_game.isEnabled() is False

    host.attach_bridge(_FakeBridge(running=True, clients=1))

    assert host._btn_show_in_game.isHidden() is False
    assert host._btn_show_in_game.isEnabled() is True
    assert host._btn_show_in_game.toolTip() == _wt(READY_TIP)


def test_showing_a_cat_refreshes_a_button_that_waited(make_host):
    # Bridge first: the button was refreshed with no cat, so only the
    # coordinator's focus-change callback can enable it.
    host = make_host()
    host.attach_bridge(_FakeBridge(running=True, clients=1))
    assert host._btn_show_in_game.isHidden() is False
    assert host._btn_show_in_game.isEnabled() is False

    host.set_focus(make_cat("Meeko", 341))

    assert host._btn_show_in_game.isEnabled() is True


def test_clearing_the_selection_disables_the_button_again(make_host):
    host = make_host()
    host.attach_bridge(_FakeBridge(running=True, clients=1))
    host.set_focus(make_cat("Meeko", 341))
    assert host._btn_show_in_game.isEnabled() is True

    host._tablectl.clear_focus()

    btn = host._btn_show_in_game
    assert host._focus is None
    # Still connected, so the button stays visible but asks for a cat.
    assert btn.isHidden() is False
    assert btn.isEnabled() is False
    assert btn.toolTip() == _wt(PICK_CAT_TIP)


def test_replacing_a_stopped_bridge_with_a_running_one_enables_it(make_host):
    host = make_host()
    host.attach_bridge(_FakeBridge(running=False, clients=1))
    host.set_focus(make_cat("Meeko", 341))
    assert host._btn_show_in_game.isHidden() is True
    assert host._btn_show_in_game.isEnabled() is False

    host.attach_bridge(_FakeBridge(running=True, clients=1))

    assert host._btn_show_in_game.isHidden() is False
    assert host._btn_show_in_game.isEnabled() is True


def test_the_button_re_gates_as_the_game_connects_and_disconnects(make_host):
    # Drive the *real* controller's game_online signal by registering and
    # unregistering a peer; no game and no fixed port are involved.
    host = make_host()
    ctl = BridgeController(port=0)
    assert ctl.start() is True
    try:
        host.attach_bridge(ctl)
        host.set_focus(make_cat("Meeko", 341))

        # Bridge listening, no mod connected: there is no game to answer, so
        # the button disappears from the card entirely.
        assert ctl.client_count == 0
        assert host._btn_show_in_game.isHidden() is True
        assert host._btn_show_in_game.isEnabled() is False

        peer = _FakeConn()
        assert ctl._server._register(peer) is True

        assert ctl.client_count == 1
        assert host._btn_show_in_game.isHidden() is False
        assert host._btn_show_in_game.isEnabled() is True
        assert host._btn_show_in_game.toolTip() == _wt(READY_TIP)

        ctl._server._unregister(peer)

        assert ctl.client_count == 0
        assert host._btn_show_in_game.isHidden() is True
        assert host._btn_show_in_game.isEnabled() is False
    finally:
        ctl.stop()


# ── 4. the outbound path (same one the shortcut and menu use) ──────────────
def test_clicking_asks_the_game_to_select_the_shown_cat(make_host):
    host = make_host()
    bridge = _FakeBridge(delivered=1)
    host.attach_bridge(bridge)
    cat = make_cat("Meeko", 341)
    host.set_focus(cat)
    _install_session(host, cat)

    host._btn_show_in_game.click()

    assert bridge.keys == [341]
    # The success line names the cat, not the raw key.
    assert host.statuses == ["asked the game to select Meeko"]


def test_clicking_after_switching_cats_sends_the_new_key(make_host):
    host = make_host()
    bridge = _FakeBridge(delivered=1)
    host.attach_bridge(bridge)

    host.set_focus(make_cat("Meeko", 341))
    host._btn_show_in_game.click()
    host.set_focus(make_cat("Baby Jane", 903))
    host._btn_show_in_game.click()

    assert bridge.keys == [341, 903]


@pytest.mark.parametrize("key", [0, 1, 2 ** 63 - 1])
def test_the_button_forwards_the_exact_key(make_host, key):
    host = make_host()
    bridge = _FakeBridge(delivered=1)
    host.attach_bridge(bridge)
    host.set_focus(make_cat("Meeko", key))

    host._btn_show_in_game.click()

    assert bridge.keys == [key]


def test_a_unicode_name_does_not_change_the_sent_key(make_host):
    host = make_host()
    bridge = _FakeBridge(delivered=1)
    host.attach_bridge(bridge)
    cat = make_cat("Mèeko 🐱", 4242)
    host.set_focus(cat)
    _install_session(host, cat)

    host._btn_show_in_game.click()

    assert bridge.keys == [4242]
    assert host.statuses == ["asked the game to select Mèeko 🐱"]


# ── 5. negative paths ──────────────────────────────────────────────────────
def test_a_click_with_no_cat_reaches_no_bridge(make_host, caplog):
    host = make_host()
    bridge = _FakeBridge(delivered=1)
    host.attach_bridge(bridge)

    # The button is disabled, so drive the connected slot directly the way a
    # stray signal would; it must refuse, not send a stale key.
    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        host._on_show_selected_in_game()

    assert bridge.keys == []
    assert host.statuses == []
    infos = _records(caplog, logging.INFO)
    assert len(infos) == 1
    assert "nothing focused" in infos[0].getMessage()


def test_an_undelivered_click_reports_it_and_keeps_the_button_enabled(
        make_host, caplog):
    host = make_host()
    bridge = _FakeBridge(delivered=0)
    host.attach_bridge(bridge)
    host.set_focus(make_cat("Meeko", 341))

    with caplog.at_level(logging.WARNING, logger=LOG_NAME):
        host._btn_show_in_game.click()

    assert bridge.keys == [341]
    # The failure wording is unchanged by the success-line fix.
    assert host.statuses == ["no game connected - is the mod running?"]
    assert len(_records(caplog, logging.WARNING)) == 1
    # A failed delivery does not change the local gate: a retry is possible.
    assert host._btn_show_in_game.isEnabled() is True


def test_a_negative_delivery_count_is_a_failure_with_the_same_status(
        make_host):
    host = make_host()
    bridge = _FakeBridge(delivered=-1)
    host.attach_bridge(bridge)
    host.set_focus(make_cat("Meeko", 341))

    assert host.show_focused_in_game() is False

    assert host.statuses == ["no game connected - is the mod running?"]
