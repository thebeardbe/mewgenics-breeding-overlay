"""RoomBar: the "Breed room" selector and the Stim/Comfort it applies.

``mewgenics_overlay.ui.roombar.RoomBar`` was extracted from ``PaletteWindow``
(god-file split, step 7). It populates the combo from the save's furniture via
``resources.gpak`` definitions and exposes the active Stimulation/Comfort pair
that breeding math reads.

The widget is window-agnostic: the session, the gpak assets and the focused
cat arrive as plain getters. These tests build it offscreen with fake
session/assets objects that satisfy exactly the attributes ``refresh`` reads
(``session.data.furniture_by_room`` and ``assets.furniture_data``), plus the
real ``room_env_map`` maths, so no save or gpak file is needed.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.core.stimulation import STIMULATION_DEFAULT  # noqa: E402
from mewgenics_overlay.ui.roombar import RoomBar  # noqa: E402


# ── fixtures / helpers ─────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def env_for(rooms: dict):
    """Build (session, assets) fakes for ``{room: (stimulation, comfort)}``.

    Each room gets one normal (non-rare) furniture item whose definition feeds
    the real ``room_env_map`` pipeline.
    """
    furniture, definitions = {}, {}
    for room, (stim, comfort) in rooms.items():
        item_name = f"item {room}"
        furniture[room] = [SimpleNamespace(item_name=item_name, room=room,
                                           is_rare=False)]
        definitions[item_name] = SimpleNamespace(
            effects={"Stimulation": float(stim), "Comfort": float(comfort)})
    session = SimpleNamespace(
        data=SimpleNamespace(furniture_by_room=furniture))
    assets = SimpleNamespace(furniture_data=definitions)
    return session, assets


@pytest.fixture
def make_bar(qapp):
    """Build isolated RoomBars; returns bar + change recorder + focus holder."""
    bars = []

    def _make(session, assets, focus=None):
        holder = {"session": session, "assets": assets, "focus": focus}
        changes = []
        bar = RoomBar(
            session_getter=lambda: holder["session"],
            assets_getter=lambda: holder["assets"],
            focus_getter=lambda: holder["focus"],
            on_change=lambda: changes.append(1),
        )
        bars.append(bar)
        return SimpleNamespace(bar=bar, changes=changes, holder=holder)

    yield _make

    for bar in bars:
        bar.hide()
        bar.close()
        bar.deleteLater()
    qapp.processEvents()


# ── 1. population ──────────────────────────────────────────────────────────
def test_refresh_populates_placeholder_then_rooms_by_descending_stimulation(
        make_bar):
    session, assets = env_for({
        "Attic": (40.0, 12.0),
        "Basement": (80.0, 0.0),
        "Cellar": (-5.0, 3.0),
    })
    h = make_bar(session, assets)

    h.bar.refresh()

    texts = [h.bar.combo.itemText(i) for i in range(h.bar.combo.count())]
    assert texts == [
        "- Stim 50 (no room)",
        "Basement - Stim 80",              # comfort 0 is omitted from the label
        "Attic - Stim 40, Comf 12",
        "Cellar - Stim -5, Comf 3",
    ]
    assert h.bar.combo.isEnabled() is True
    assert h.bar.combo.minimumWidth() >= 170


def test_refresh_renders_a_unicode_room_name_verbatim(make_bar):
    session, assets = env_for({"Küche 😺": (30.0, 4.0)})
    h = make_bar(session, assets)

    h.bar.refresh()

    assert h.bar.combo.itemText(1) == "Küche 😺 - Stim 30, Comf 4"
    h.bar.combo.setCurrentIndex(1)     # no focus -> user picks it
    assert h.bar.selected_room() == "Küche 😺"


# ── 2. default selection ───────────────────────────────────────────────────
def test_default_selection_without_focus_is_the_neutral_placeholder(make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0)})
    h = make_bar(session, assets)

    h.bar.refresh()

    assert h.bar.combo.currentIndex() == 0
    assert h.bar.selected_room() is None
    assert h.bar.stim_value() == STIMULATION_DEFAULT == 50.0
    assert h.bar.comfort_value() == 0.0
    assert h.changes == []             # no focus -> nothing to redraw


def test_focused_cats_room_is_selected_by_default(make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0), "Cellar": (10.0, 0.0)})
    h = make_bar(session, assets, focus=SimpleNamespace(room="Attic"))

    h.bar.refresh()

    assert h.bar.selected_room() == "Attic"
    assert h.bar.stim_value() == 40.0
    assert h.bar.comfort_value() == 12.0
    assert h.changes == [1]            # 50 -> 40 changes the numbers


def test_focus_in_an_unknown_room_falls_back_to_the_placeholder(make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0)})
    h = make_bar(session, assets, focus=SimpleNamespace(room="Nowhere"))

    h.bar.refresh()

    assert h.bar.selected_room() is None
    assert h.bar.stim_value() == 50.0


# ── 3. selected_room / stim / comfort ──────────────────────────────────────
def test_known_room_reports_its_furniture_values(make_bar):
    session, assets = env_for({"Basement": (80.0, 25.0)})
    h = make_bar(session, assets, focus=SimpleNamespace(room="Basement"))

    h.bar.refresh()

    assert h.bar.selected_room() == "Basement"
    assert h.bar.stim_value() == pytest.approx(80.0)
    assert h.bar.comfort_value() == pytest.approx(25.0)


def test_placeholder_reports_the_default_stimulation_and_zero_comfort(
        make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0)})
    h = make_bar(session, assets)
    h.bar.refresh()
    h.bar.combo.setCurrentIndex(1)     # pick Attic
    assert h.bar.stim_value() == 40.0

    h.bar.combo.setCurrentIndex(0)     # back to "no room"

    assert h.bar.selected_room() is None
    assert h.bar.stim_value() == STIMULATION_DEFAULT
    assert h.bar.comfort_value() == 0.0


def test_negative_comfort_is_floored_at_zero(make_bar):
    # ``room_env_map`` can hand back a negative effective Comfort (crowding);
    # the value used by the breeding math is never allowed below zero.
    session, assets = env_for({"Basement": (0.0, -3.0)})
    h = make_bar(session, assets, focus=SimpleNamespace(room="Basement"))

    h.bar.refresh()

    assert h.bar.comfort_value() == 0.0


def test_negative_stimulation_is_reported_verbatim(make_bar):
    session, assets = env_for({"Cellar": (-12.0, 0.0)})
    h = make_bar(session, assets, focus=SimpleNamespace(room="Cellar"))

    h.bar.refresh()

    assert h.bar.stim_value() == pytest.approx(-12.0)


# ── 4. refresh vs change callback ──────────────────────────────────────────
def test_previous_pick_survives_a_refresh(make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0), "Basement": (80.0, 0.0)})
    h = make_bar(session, assets)
    h.bar.refresh()
    h.bar.combo.setCurrentIndex(2)     # Attic
    assert h.bar.selected_room() == "Attic"

    h.bar.refresh()                    # no focus, but the pick is remembered

    assert h.bar.selected_room() == "Attic"
    assert h.bar.stim_value() == 40.0


def test_user_selection_fires_on_change_each_time(make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0), "Basement": (80.0, 0.0)})
    h = make_bar(session, assets)
    h.bar.refresh()
    assert h.changes == []

    h.bar.combo.setCurrentIndex(1)
    assert h.changes == [1]

    h.bar.combo.setCurrentIndex(2)
    assert h.changes == [1, 1]

    h.bar.combo.setCurrentIndex(0)
    assert h.changes == [1, 1, 1]


def test_silent_refresh_without_focus_does_not_fire_on_change(make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0)})
    h = make_bar(session, assets)

    h.bar.refresh()
    h.bar.refresh()
    h.bar.refresh()

    assert h.changes == []


def test_silent_refresh_does_not_fire_when_the_numbers_are_unchanged(
        make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0)})
    h = make_bar(session, assets, focus=SimpleNamespace(room="Attic"))

    h.bar.refresh()
    assert h.changes == [1]            # 50/0 -> 40/12

    h.bar.refresh()                    # same room, same numbers

    assert h.changes == [1]            # no second redraw


def test_refresh_does_not_fire_when_the_focused_room_was_already_chosen(
        make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0)})
    h = make_bar(session, assets)
    h.bar.refresh()
    h.bar.combo.setCurrentIndex(1)     # user already picked Attic
    assert h.changes == [1]

    h.holder["focus"] = SimpleNamespace(room="Attic")
    h.bar.refresh()

    assert h.bar.selected_room() == "Attic"
    assert h.changes == [1]            # unchanged numbers -> silent


# ── 5. missing / empty data ────────────────────────────────────────────────
@pytest.mark.parametrize("session,assets", [
    (None, None),                                            # nothing loaded
    (SimpleNamespace(data=None), None),                      # session w/o data
    (SimpleNamespace(data=SimpleNamespace(furniture_by_room={})),
     SimpleNamespace(furniture_data={})),                    # empty save
    (SimpleNamespace(data=SimpleNamespace(
        furniture_by_room={"Attic": [SimpleNamespace(
            item_name="x", room="Attic", is_rare=False)]})),
     None),                                                  # assets missing
    (SimpleNamespace(data=SimpleNamespace(
        furniture_by_room={"Attic": [SimpleNamespace(
            item_name="x", room="Attic", is_rare=False)]})),
     SimpleNamespace(furniture_data={})),                    # gpak missing
])
def test_missing_or_empty_data_leaves_only_the_disabled_placeholder(
        make_bar, session, assets):
    h = make_bar(session, assets)

    h.bar.refresh()

    assert h.bar.combo.count() == 1
    assert h.bar.combo.itemText(0) == "- Stim 50 (no room)"
    assert h.bar.combo.isEnabled() is False
    assert h.bar.selected_room() is None
    assert h.bar.stim_value() == STIMULATION_DEFAULT
    assert h.bar.comfort_value() == 0.0
    assert h.changes == []


def test_refresh_recovers_when_data_arrives_after_an_empty_start(make_bar):
    h = make_bar(None, None)
    h.bar.refresh()
    assert h.bar.combo.isEnabled() is False
    assert h.bar.stim_value() == STIMULATION_DEFAULT

    good_session, good_assets = env_for({"Attic": (40.0, 12.0)})
    h.holder["session"] = good_session
    h.holder["assets"] = good_assets

    h.bar.refresh()

    assert h.bar.combo.isEnabled() is True
    assert h.bar.combo.count() == 2
    assert h.bar.selected_room() is None     # no focus -> placeholder
    assert h.changes == []                   # 50 -> 50, nothing changed


# ── 6. restyle ─────────────────────────────────────────────────────────────
def test_restyle_does_not_raise_and_keeps_the_combo(make_bar):
    session, assets = env_for({"Attic": (40.0, 12.0)})
    h = make_bar(session, assets)
    h.bar.refresh()

    h.bar.restyle()                    # must not raise

    assert h.bar.combo.count() == 2
    assert h.bar.selected_room() is None
