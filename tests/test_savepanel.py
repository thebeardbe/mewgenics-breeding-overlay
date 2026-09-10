"""SavePanel: the campaign save-slot cards and the "open another save" picker.

Extracted from ``PaletteWindow`` (god-file split step 6). The panel is
window-agnostic: it takes the live settings dict and an ``on_open(path)``
callable, so it can be driven here without a ``PaletteWindow``, a session, a
watcher or a real config file.

Slot discovery deliberately goes through the real
``core.discovery.find_all_saves``: a throwaway ``Glaiel Games/Mewgenics`` root
is planted and every other discovery root (Steam/Proton prefixes, XDG config,
APPDATA) is blanked, so a developer's real saves can never leak into an
assertion and no real save or config is ever written.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

import mewgenics_overlay.ui.savepanel as savepanel  # noqa: E402
from mewgenics_overlay.core import discovery  # noqa: E402
from mewgenics_overlay.ui import theme as _theme  # noqa: E402
from mewgenics_overlay.ui.savepanel import (  # noqa: E402
    SLOT_COUNT,
    _CURRENT_MARK,
    _SAVE_FILTER,
    SavePanel,
    _base_name,
    _file_exists,
)


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def saves_root(tmp_path, monkeypatch):
    """A throwaway ``Glaiel Games/Mewgenics`` root with one empty profile."""
    root = tmp_path / "Mewgenics"
    (root / "profile1" / "saves").mkdir(parents=True)
    monkeypatch.setenv("MEWGENICS_SAVES_ROOT", str(root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-xdg"))
    monkeypatch.delenv("APPDATA", raising=False)
    # Steam/Proton compatdata on the host must not contribute any records.
    monkeypatch.setattr(discovery, "_proton_compat_roots", lambda: iter(()))
    return root


def add_slot(root, number: int, *, mtime: float | None = None,
             name: str | None = None, content: bytes = b"fake save") -> str:
    """Create a save inside the profile and return its path as a string."""
    filename = name or f"steamcampaign{number:02d}.sav"
    path = root / "profile1" / "saves" / filename
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return str(path)


@pytest.fixture
def make_panel(qapp):
    """Build isolated SavePanels; tear them down so no widget survives."""
    panels = []

    def _make(settings: dict | None = None, on_open=None) -> SavePanel:
        panel = SavePanel({} if settings is None else settings,
                          on_open or (lambda path: None))
        panels.append(panel)
        return panel

    yield _make
    for panel in panels:
        panel.hide()
        panel.close()
        panel.deleteLater()
    qapp.processEvents()


# ── 1. slot discovery / population ─────────────────────────────────────────
def test_refresh_populates_the_three_slots_from_disk(saves_root, make_panel):
    slot1 = add_slot(saves_root, 1)
    slot3 = add_slot(saves_root, 3)

    panel = make_panel()
    panel.refresh()

    assert panel._slot_paths == [slot1, None, slot3]
    assert len(panel._slot_buttons) == SLOT_COUNT == 3

    assert panel._slot_buttons[0].isEnabled() is True
    assert panel._slot_buttons[1].isEnabled() is False
    assert panel._slot_buttons[2].isEnabled() is True

    assert panel._slot_buttons[0].text() == "Slot 1\n🐈 steamcampaign01.sav"
    assert panel._slot_buttons[1].text() == "Slot 2\nno save yet"
    assert panel._slot_buttons[2].text() == "Slot 3\n🐈 steamcampaign03.sav"


def test_slots_start_empty_and_disabled_before_refresh(make_panel):
    panel = make_panel()

    assert panel._slot_paths == []
    for btn in panel._slot_buttons:
        assert btn.isEnabled() is False
        assert btn.text() == f"Slot {panel._slot_buttons.index(btn) + 1}\nno save yet"


@pytest.mark.parametrize("name,expected_slot", [
    ("steamcampaign1.sav", 1),      # unpadded number still maps to slot 1
    ("steamcampaign2.sav", 2),
])
def test_unpadded_campaign_numbers_are_accepted(saves_root, make_panel,
                                                name, expected_slot):
    path = add_slot(saves_root, expected_slot, name=name)
    panel = make_panel()

    panel.refresh()

    assert panel._slot_paths[expected_slot - 1] == path


def test_malformed_and_out_of_range_names_are_ignored(saves_root, make_panel):
    slot1 = add_slot(saves_root, 1)
    slot2 = add_slot(saves_root, 2)
    add_slot(saves_root, 4)                              # out of range
    add_slot(saves_root, 99)                             # out of range
    add_slot(saves_root, 0, name="steamcampaign0X.sav")  # non-numeric
    add_slot(saves_root, 0, name="other.sav")            # not a campaign save
    add_slot(saves_root, 0, name="campaign01.sav")       # wrong prefix

    panel = make_panel()
    panel.refresh()

    assert panel._slot_paths == [slot1, slot2, None]


def test_discovery_failure_leaves_empty_disabled_slots(saves_root, make_panel,
                                                       monkeypatch):
    def _boom():
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr(discovery, "find_all_saves", _boom)
    panel = make_panel()

    panel.refresh()                      # must not raise

    assert panel._slot_paths == [None, None, None]
    for btn in panel._slot_buttons:
        assert btn.isEnabled() is False


def test_windows_style_paths_are_labelled_and_matched(saves_root, make_panel,
                                                      monkeypatch):
    win = (r"C:\Users\me\AppData\Roaming\Glaiel Games\Mewgenics"
           r"\profile\saves\steamcampaign01.sav")
    monkeypatch.setattr(discovery, "find_all_saves",
                        lambda: [{"path": win, "root": "x", "mtime": 1.0}])
    panel = make_panel()

    panel.refresh()
    assert panel._slot_paths == [win, None, None]
    assert panel._slot_buttons[0].text() == "Slot 1\n🐈 steamcampaign01.sav"

    panel.set_current(win)
    assert panel._slot_buttons[0].text().endswith(_CURRENT_MARK)
    assert _theme.C_GOOD in panel._slot_buttons[0].styleSheet()


def test_slot_tooltips_are_set(make_panel):
    panel = make_panel()

    for btn in panel._slot_buttons:
        assert "Load this campaign slot's save." in btn.toolTip()


# ── 2. current-slot highlighting ───────────────────────────────────────────
def test_set_current_marks_only_the_matching_slot(saves_root, make_panel):
    slot1 = add_slot(saves_root, 1)
    slot3 = add_slot(saves_root, 3)
    panel = make_panel({"save_path": slot3})

    panel.refresh()

    marked, plain = panel._slot_buttons[2], panel._slot_buttons[0]
    assert marked.text() == "Slot 3\n🐈 steamcampaign03.sav" + _CURRENT_MARK
    assert plain.text() == "Slot 1\n🐈 steamcampaign01.sav"
    assert _CURRENT_MARK not in plain.text()
    assert _theme.C_GOOD in marked.styleSheet()
    assert _theme.C_GOOD not in plain.styleSheet()
    assert _theme.C_GRIP in plain.styleSheet()

    # Moving the mark to the other slot clears it from the first.
    panel.set_current(slot1)
    assert panel._slot_buttons[0].text().endswith(_CURRENT_MARK)
    assert _CURRENT_MARK not in panel._slot_buttons[2].text()
    assert _theme.C_GOOD in panel._slot_buttons[0].styleSheet()
    assert _theme.C_GOOD not in panel._slot_buttons[2].styleSheet()


def test_set_current_to_a_path_no_slot_holds_marks_nothing(saves_root,
                                                           make_panel):
    add_slot(saves_root, 1)
    panel = make_panel({"save_path": str(saves_root / "elsewhere.sav")})

    panel.refresh()

    assert panel._current == str(saves_root / "elsewhere.sav")
    for btn in panel._slot_buttons:
        assert _CURRENT_MARK not in btn.text()


def test_set_current_none_clears_the_mark(saves_root, make_panel):
    slot1 = add_slot(saves_root, 1)
    panel = make_panel({"save_path": slot1})
    panel.refresh()
    assert panel._slot_buttons[0].text().endswith(_CURRENT_MARK)

    panel.set_current(None)

    assert panel._current == ""
    assert panel._slot_buttons[0].text() == "Slot 1\n🐈 steamcampaign01.sav"


# ── 3. idempotence ─────────────────────────────────────────────────────────
def test_refresh_is_idempotent(saves_root, make_panel):
    slot1 = add_slot(saves_root, 1)
    add_slot(saves_root, 2)
    panel = make_panel({"save_path": slot1})

    panel.refresh()
    first = (list(panel._slot_paths),
             [b.text() for b in panel._slot_buttons],
             [b.isEnabled() for b in panel._slot_buttons],
             [b.styleSheet() for b in panel._slot_buttons])

    panel.refresh()
    second = (list(panel._slot_paths),
              [b.text() for b in panel._slot_buttons],
              [b.isEnabled() for b in panel._slot_buttons],
              [b.styleSheet() for b in panel._slot_buttons])

    assert first == second


def test_restyle_preserves_labels_and_state(saves_root, make_panel):
    slot1 = add_slot(saves_root, 1)
    panel = make_panel({"save_path": slot1})
    panel.refresh()
    before = ([b.text() for b in panel._slot_buttons],
              [b.isEnabled() for b in panel._slot_buttons])

    panel.restyle()

    after = ([b.text() for b in panel._slot_buttons],
             [b.isEnabled() for b in panel._slot_buttons])
    assert after == before
    assert _theme.C_GOOD in panel._slot_buttons[0].styleSheet()


# ── 4. load_last ───────────────────────────────────────────────────────────
def test_load_last_opens_the_newest_save_when_none_remembered(saves_root,
                                                              make_panel):
    add_slot(saves_root, 1, mtime=1_000_000_000.0)
    newest = add_slot(saves_root, 3, mtime=1_700_000_000.0)
    opened = []
    panel = make_panel({}, on_open=opened.append)

    result = panel.load_last()

    assert result == newest
    assert opened == [newest]


def test_load_last_prefers_the_remembered_save(saves_root, make_panel):
    remembered = add_slot(saves_root, 1, mtime=1_000_000_000.0)
    add_slot(saves_root, 3, mtime=1_700_000_000.0)      # newer, but not chosen
    opened = []
    panel = make_panel({"save_path": remembered}, on_open=opened.append)

    result = panel.load_last()

    assert result == remembered
    assert opened == [remembered]


def test_load_last_falls_back_when_the_remembered_save_is_missing(
        saves_root, make_panel, tmp_path):
    add_slot(saves_root, 1, mtime=1_000_000_000.0)
    newest = add_slot(saves_root, 3, mtime=1_700_000_000.0)
    opened = []
    panel = make_panel({"save_path": str(tmp_path / "gone.sav")},
                       on_open=opened.append)

    result = panel.load_last()

    assert result == newest
    assert opened == [newest]


def test_load_last_returns_none_and_opens_nothing_without_any_save(
        saves_root, make_panel):
    opened = []
    panel = make_panel({}, on_open=opened.append)

    assert panel.load_last() is None
    assert opened == []


# ── 5. the file picker (no real dialog) ────────────────────────────────────
def test_pick_forwards_the_chosen_path_through_the_qt_dialog(
        make_panel, tmp_path, monkeypatch):
    chosen = str(tmp_path / "picked.sav")
    remembered = str(tmp_path / "steamcampaign01.sav")
    seen = {}

    class _FakeDialog:
        Option = QFileDialog.Option

        @staticmethod
        def getOpenFileName(parent, title, directory, filt, options=None):
            seen.update(parent=parent, title=title, directory=directory,
                        filt=filt, options=options)
            return chosen, filt

    monkeypatch.setattr(savepanel, "QFileDialog", _FakeDialog)
    opened = []
    panel = make_panel({"save_path": remembered}, on_open=opened.append)

    panel.pick()

    assert opened == [chosen]
    assert seen["parent"] is panel
    assert seen["title"] == "Locate Mewgenics save"
    assert seen["directory"] == str(tmp_path)
    assert seen["filt"] == _SAVE_FILTER
    assert seen["options"] == QFileDialog.Option.DontUseNativeDialog


def test_pick_cancelled_does_not_open_anything(make_panel, monkeypatch):
    class _FakeDialog:
        Option = QFileDialog.Option

        @staticmethod
        def getOpenFileName(*args, **kwargs):
            return "", ""

    monkeypatch.setattr(savepanel, "QFileDialog", _FakeDialog)
    opened = []
    panel = make_panel({}, on_open=opened.append)

    panel.pick()

    assert opened == []


# ── 6. slot buttons ────────────────────────────────────────────────────────
def test_clicking_a_slot_button_opens_that_slot(saves_root, make_panel):
    slot1 = add_slot(saves_root, 1)
    slot3 = add_slot(saves_root, 3)
    opened = []
    panel = make_panel(on_open=opened.append)
    panel.refresh()

    panel._slot_buttons[0].click()
    panel._slot_buttons[2].click()

    assert opened == [slot1, slot3]


def test_clicking_an_empty_slot_is_inert(saves_root, make_panel):
    add_slot(saves_root, 1)
    opened = []
    panel = make_panel(on_open=opened.append)
    panel.refresh()

    panel._slot_buttons[1].click()       # disabled (empty slot)
    panel._slot_buttons[2].click()       # disabled (empty slot)
    assert opened == []

    panel._slot_buttons[0].click()
    assert opened == [panel._slot_paths[0]]


def test_clicking_before_refresh_is_inert(make_panel):
    opened = []
    panel = make_panel(on_open=opened.append)

    for btn in panel._slot_buttons:
        btn.click()

    assert opened == []


# ── 7. module helpers ──────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("/tmp/saves/steamcampaign01.sav", "steamcampaign01.sav"),
    (r"C:\Users\me\saves\steamcampaign02.sav", "steamcampaign02.sav"),
    ("steamcampaign03.sav", "steamcampaign03.sav"),
    (r"C:\only\back\slashes.sav", "slashes.sav"),
    ("/only/forward/name with spaces.sav", "name with spaces.sav"),
])
def test_base_name_handles_both_separators(raw, expected):
    assert _base_name(raw) == expected


def test_file_exists_treats_empty_and_missing_as_false(tmp_path):
    existing = tmp_path / "here.sav"
    existing.write_bytes(b"x")

    assert _file_exists("") is False
    assert _file_exists(None) is False
    assert _file_exists(str(tmp_path / "nope.sav")) is False
    assert _file_exists(str(existing)) is True
