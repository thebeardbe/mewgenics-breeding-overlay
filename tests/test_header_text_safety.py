"""Security regression: the header never renders save-derived text as markup.

``TopBar``'s title and status labels both carry save-derived text: the title
holds the save file's base name, the status line names the cat the overlay
asked the game to select. Before the fix both labels used Qt's default
AutoText, which parses a tag-like string (``<b>``, ``<img src=...>``) as rich
text, so a crafted cat name or save filename could inject HTML into the
overlay header.

These tests pin the fix at three levels:

  * the labels are forced to ``PlainText`` at construction and stay plain
    through ``set_title`` / ``set_status`` / ``restyle``;
  * a hostile name or filename round-trips through the *real* paths
    (``PaletteWindow.show_in_game`` -> ``_set_status`` -> ``TopBar.set_status``
    and ``PaletteWindow.open_save`` -> ``set_title``) and is displayed
    literally - proven by rendering the widget offscreen and matching its
    layout against a forced-plain control, because ``text()`` returns the same
    string in either format;
  * a hostile name does not change which key reaches the game.

Everything runs on the offscreen platform with no window, timer, socket or
file write: the palette methods are called on the same minimal stand-in
convention as ``test_show_in_game.py`` (the real method, a fake bridge, a real
``TopBar``).
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from mewgenics_overlay.ui import palette  # noqa: E402
from mewgenics_overlay.ui import theme as _theme  # noqa: E402
from mewgenics_overlay.ui.chrome import TopBar  # noqa: E402
from mewgenics_overlay.ui.savepanel import base_name  # noqa: E402


#: Cat names / filenames that Qt's AutoText parses as rich text. Each one
#: lays out differently under AutoText (asserted by
#: ``_assert_renders_literally``), so it is a genuine injection attempt
#: against the pre-fix label rather than a plain-looking string.
HOSTILE_NAMES = [
    "<b>Meeko</b>",
    "<img src=x onerror=alert(1)>",
    '<a href="https://evil.example">Meeko</a>',
    "<span style='color:red'>Meeko</span>",
    "<i>Meeko</i>",
]


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_theme_zoom(monkeypatch):
    # ``restyle`` writes the module-global ZOOM via theme.set_zoom(); keep that
    # from leaking into other test files.
    monkeypatch.setattr(_theme, "ZOOM", 1.0)


@pytest.fixture
def make_bar(qapp):
    """Build isolated TopBars; tear them down so no widget survives a test."""
    bars = []

    def _make(**kwargs):
        bar = TopBar(**kwargs)
        bars.append(bar)
        return bar

    yield _make
    for bar in bars:
        bar.hide()
        bar.close()
        bar.deleteLater()
    qapp.processEvents()


# ── rendering proof helpers ────────────────────────────────────────────────
def _control(label: QLabel, text: str, fmt) -> QLabel:
    """A label with *label*'s effective font rendering *text* in *fmt*."""
    control = QLabel()
    control.setFont(label.font())
    control.setTextFormat(fmt)
    control.setText(text)
    control.adjustSize()
    return control


def _assert_plain(label: QLabel, text: str) -> None:
    """Pin that *label* holds *text* in plain-text layout.

    ``text()`` returns the same string in either format, so the layout is
    compared against a forced-plain control with the same font: an equal
    ``sizeHint`` means the label drew the literal characters.
    """
    label.ensurePolished()
    label.adjustSize()
    plain = _control(label, text, Qt.TextFormat.PlainText)

    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == text
    assert label.sizeHint() == plain.sizeHint(), (
        "header drew the save-derived string differently from plain text")


def _assert_renders_literally(label: QLabel, text: str) -> None:
    """``_assert_plain`` plus proof the string really is markup-shaped.

    A control with Qt's default ``AutoText`` (exactly what the pre-fix label
    used) must lay out differently; if it does not, the input is not an
    injection attempt for AutoText and the test would prove nothing.
    """
    _assert_plain(label, text)

    auto = _control(label, text, Qt.TextFormat.AutoText)
    assert label.sizeHint() != auto.sizeHint(), (
        "the test string is not markup-shaped; it proves nothing")


# ── palette stand-ins (same convention as test_show_in_game.py) ────────────
class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _FakeBridge:
    """Records every select and returns a scripted delivery count."""

    def __init__(self, delivered=1, running=True, clients=1):
        self.delivered = delivered
        self.running = running
        self.client_count = clients
        self.keys = []
        self.game_online = _FakeSignal()

    def send_select(self, key):
        self.keys.append(key)
        return self.delivered


class _Host:
    """The slice of ``PaletteWindow`` the exercised methods read."""

    _bridge = None
    _session = None
    _chrome = None
    _settings = None
    bridge_available = palette.PaletteWindow.bridge_available
    in_game_available = palette.PaletteWindow.in_game_available
    _cat_name = palette.PaletteWindow._cat_name
    _set_status = palette.PaletteWindow._set_status
    show_in_game = palette.PaletteWindow.show_in_game
    open_save = palette.PaletteWindow.open_save


def _host(bar, bridge=None, session=None):
    host = _Host()
    host._bridge = bridge
    host._session = session
    host._chrome = bar
    host._settings = {}
    return host


def _session_with(*cats):
    return SimpleNamespace(by_key={cat.db_key: cat for cat in cats})


def _open_save(monkeypatch, bar, path):
    """Run the real ``PaletteWindow.open_save`` against *bar*, no file I/O."""
    monkeypatch.setattr(palette.cfg, "save", lambda settings: None)
    started = []
    reloaded = []
    host = _host(bar)
    host._save_panel = SimpleNamespace(set_current=lambda p: None)
    host._reloader = SimpleNamespace(
        start=started.append,
        request_reload=lambda: reloaded.append(True))
    palette.PaletteWindow.open_save(host, path)
    return host, started, reloaded


# ── 1. the choke point: both header labels are forced plain ────────────────
def test_construction_forces_the_title_and_status_to_plain_text(make_bar):
    bar = make_bar()

    assert bar.title_label.textFormat() == Qt.TextFormat.PlainText
    assert bar.status_label.textFormat() == Qt.TextFormat.PlainText


def test_title_and_status_stay_plain_after_markup_lookalikes(make_bar):
    bar = make_bar()
    hostile = "<img src=x onerror=alert(1)>"

    bar.set_title(f"🐈 Overlay - {hostile}.sav")
    bar.set_status(f"asked the game to select {hostile}")

    assert bar.title_label.textFormat() == Qt.TextFormat.PlainText
    assert bar.status_label.textFormat() == Qt.TextFormat.PlainText


def test_restyle_does_not_revert_the_labels_to_auto_text(make_bar):
    # ``restyle`` swaps stylesheets; the text format must survive it, or a
    # later zoom change would silently reopen the hole.
    bar = make_bar()
    hostile = "<b>Meeko</b>"
    bar.set_status(f"asked the game to select {hostile}")

    bar.restyle(2.0)

    assert bar.status_label.textFormat() == Qt.TextFormat.PlainText
    _assert_renders_literally(bar.status_label,
                              f"asked the game to select {hostile}")


# ── 2. the status line: real show_in_game -> real status label ─────────────
@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_status_message_renders_a_hostile_cat_name_literally(make_bar, name):
    bridge = _FakeBridge(delivered=1)
    cat = SimpleNamespace(db_key=423, name=name)
    bar = make_bar()
    host = _host(bar, bridge, session=_session_with(cat))

    assert palette.PaletteWindow.show_in_game(host, 423) is True

    expected = f"asked the game to select {name}"
    assert bar.status_label.text() == expected
    _assert_renders_literally(bar.status_label, expected)


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_status_message_renders_a_hostile_unicode_cat_name_literally(
        make_bar, name):
    bridge = _FakeBridge(delivered=1)
    cat = SimpleNamespace(db_key=7, name=f"Mèeko 🐱 {name}")
    bar = make_bar()
    host = _host(bar, bridge, session=_session_with(cat))

    assert palette.PaletteWindow.show_in_game(host, 7) is True

    expected = f"asked the game to select Mèeko 🐱 {name}"
    assert bar.status_label.text() == expected
    _assert_renders_literally(bar.status_label, expected)


# ── 3. a hostile name must not change the key the game receives ────────────
@pytest.mark.parametrize("key,name", [
    (0, "<b>Meeko</b>"),
    (1, "<img src=x onerror=alert(1)>"),
    (2 ** 31 - 1, "Meeko</b><script>alert(1)</script>"),
    (2 ** 63 - 1, "<span style='color:red'>Meeko</span>"),
])
def test_hostile_cat_name_does_not_change_the_key_sent(make_bar, key, name):
    bridge = _FakeBridge(delivered=1)
    cat = SimpleNamespace(db_key=key, name=name)
    host = _host(make_bar(), bridge, session=_session_with(cat))

    assert palette.PaletteWindow.show_in_game(host, key) is True

    assert bridge.keys == [key]


# ── 4. the title: real open_save -> real title label ───────────────────────
@pytest.mark.parametrize("sav", [
    # The *base name* itself must look like markup. No ``/`` may appear inside
    # the crafted name: ``base_name`` splits on it (``</b>`` would truncate).
    "/tmp/<img src=x onerror=alert(1)>.sav",
    "/tmp/saves/<b>campaign.sav",
    "/tmp/saves/<i>campaign.sav",
    "/tmp/saves/<font color=red>campaign.sav",
])
def test_save_base_name_looking_like_markup_renders_literally(
        make_bar, monkeypatch, sav):
    bar = make_bar()

    host, started, reloaded = _open_save(monkeypatch, bar, sav)

    assert host._settings["save_path"] == sav
    assert started == [sav]
    assert reloaded == [True]
    expected = f"🐈 Overlay - {base_name(sav)}"
    assert bar.title_label.text() == expected
    assert base_name(sav) in expected     # the crafted name really is shown
    _assert_renders_literally(bar.title_label, expected)


def test_title_keeps_the_base_name_of_a_windows_style_hostile_path(
        make_bar, monkeypatch):
    bar = make_bar()
    sav = r"C:\Games\<img src=x onerror=alert(1)>.sav"

    _open_save(monkeypatch, bar, sav)

    expected = "🐈 Overlay - <img src=x onerror=alert(1)>.sav"
    assert bar.title_label.text() == expected
    _assert_renders_literally(bar.title_label, expected)


# ── 5. boundaries and error paths ──────────────────────────────────────────
def test_unknown_key_falls_back_to_a_plain_generic_status(make_bar):
    bridge = _FakeBridge(delivered=1)
    bar = make_bar()
    host = _host(bar, bridge, session=_session_with(
        SimpleNamespace(db_key=1, name="Someone else")))

    assert palette.PaletteWindow.show_in_game(host, 999) is True

    _assert_plain(bar.status_label,
                  "asked the game to select this cat")


def test_cat_without_a_name_falls_back_to_a_plain_generic_status(make_bar):
    bridge = _FakeBridge(delivered=1)
    cat = SimpleNamespace(db_key=5, name="")
    bar = make_bar()
    host = _host(bar, bridge, session=_session_with(cat))

    assert palette.PaletteWindow.show_in_game(host, 5) is True

    _assert_plain(bar.status_label,
                  "asked the game to select this cat")


def test_no_game_connected_error_line_is_plain_too(make_bar):
    bridge = _FakeBridge(delivered=0)
    bar = make_bar()
    host = _host(bar, bridge)

    assert palette.PaletteWindow.show_in_game(host, 1) is False

    _assert_plain(bar.status_label,
                  "no game connected - is the mod running?")


def test_an_extremely_long_hostile_name_stays_plain(make_bar):
    bar = make_bar()
    hostile = "<b>" + "A" * 5000 + "</b>"

    bar.set_status(f"asked the game to select {hostile}")
    bar.set_title(f"🐈 Overlay - {hostile}.sav")

    assert bar.status_label.textFormat() == Qt.TextFormat.PlainText
    assert bar.title_label.textFormat() == Qt.TextFormat.PlainText
    assert bar.status_label.text() == f"asked the game to select {hostile}"
    assert bar.title_label.text() == f"🐈 Overlay - {hostile}.sav"
    # Rendering a 5k-character literal must not fall back to parsing it.
    bar.status_label.adjustSize()
    bar.title_label.adjustSize()


def test_empty_status_and_title_are_still_plain(make_bar):
    bar = make_bar()

    bar.set_status("")
    bar.set_title("")

    assert bar.status_label.textFormat() == Qt.TextFormat.PlainText
    assert bar.title_label.textFormat() == Qt.TextFormat.PlainText
    assert bar.status_label.text() == ""
    assert bar.title_label.text() == ""


@pytest.mark.parametrize("lookalike", [
    "&lt;b&gt;Meeko&lt;/b&gt;",     # already-escaped markup stays literal text
    "Meeko & Co <3",
    "50% < 100%",
    "<not a tag",
    "🐈 <b>😺</b> 🐱",
])
def test_entity_and_partial_tag_lookalikes_stay_plain(make_bar, lookalike):
    bar = make_bar()

    bar.set_status(f"asked the game to select {lookalike}")

    assert bar.status_label.textFormat() == Qt.TextFormat.PlainText
    assert bar.status_label.text() == f"asked the game to select {lookalike}"


def test_script_tag_name_stays_plain_and_literal(make_bar):
    # Qt's AutoText never parsed this one (so it cannot be pinned with the
    # AutoText proof), but the label is plain regardless and must show it.
    bridge = _FakeBridge(delivered=1)
    name = "Meeko</b><script>alert(1)</script>"
    cat = SimpleNamespace(db_key=11, name=name)
    bar = make_bar()
    host = _host(bar, bridge, session=_session_with(cat))

    assert palette.PaletteWindow.show_in_game(host, 11) is True

    assert bridge.keys == [11]
    _assert_plain(bar.status_label, f"asked the game to select {name}")
