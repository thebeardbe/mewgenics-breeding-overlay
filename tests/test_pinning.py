"""PinningStore: the per-save keep-list ("pin for breeding").

Extracted from ``PaletteWindow`` (god-file split step 6). The store has no Qt
dependency in its public surface, so these tests run without a QApplication.
It is driven by a live settings dict plus injected "persist" and "on_change"
callables; the tests pin the persistence contract
(``settings["pinned"][save_path]`` is a list of ``unique_id``) and the pruning
rules that used to live inline in the window.
"""

from __future__ import annotations

import pytest

from mewgenics_overlay.ui.pinning import PinningStore

SAVE_A = "/saves/a.sav"
SAVE_B = "/saves/b.sav"


class _Cat:
    """Duck-typed cat: the store only touches these three attributes."""

    def __init__(self, uid, status="Alive"):
        self.unique_id = uid
        self.status = status
        self.is_pinned = False


class _StatlessCat:
    """A cat whose parser never set ``status`` (getattr fallback path)."""

    def __init__(self, uid):
        self.unique_id = uid
        self.is_pinned = False


class _Session:
    def __init__(self, *cats):
        self.cats = list(cats)


class _Harness:
    """A PinningStore plus recording persist/on_change callbacks."""

    def __init__(self, save_path=SAVE_A):
        self.settings = {"save_path": save_path, "pinned": {}}
        self.save_calls = 0
        self.saved_snapshots = []
        self.change_calls = 0
        self.store = PinningStore(self.settings, self._save, self._change)

    def _save(self):
        self.save_calls += 1
        # Snapshot what the persistence layer would write, proving the store's
        # own list was mutated *before* the callback ran.
        self.saved_snapshots.append(
            {k: list(v) for k, v in self.settings.get("pinned", {}).items()})

    def _change(self):
        self.change_calls += 1


@pytest.fixture
def h():
    return _Harness()


# ── 1. the store is keyed by save path ─────────────────────────────────────
def test_store_is_created_lazily_and_keyed_by_save_path(h):
    assert h.settings["pinned"] == {}

    store = h.store.store()

    assert store == []
    assert h.settings["pinned"] == {SAVE_A: []}
    assert h.store.store() is store          # same live list on every call


def test_store_without_a_save_path_keys_on_the_empty_string():
    settings = {"pinned": {}}
    store = PinningStore(settings, lambda: None, lambda: None)

    assert store.store() == []
    assert settings["pinned"] == {"": []}


def test_two_saves_do_not_leak_pins_into_each_other(h):
    h.store.set_pinned(_Cat(1), True)
    h.settings["save_path"] = SAVE_B
    assert h.store.store() == []

    h.store.set_pinned(_Cat(2), True)

    assert h.settings["pinned"][SAVE_A] == [1]
    assert h.settings["pinned"][SAVE_B] == [2]

    h.settings["save_path"] = SAVE_A
    assert h.store.store() == [1]            # save A's pin is still there


def test_sync_only_applies_the_current_saves_pins(h):
    h.settings["pinned"] = {SAVE_A: [1], SAVE_B: [2]}
    h.settings["save_path"] = SAVE_B
    cat1, cat2 = _Cat(1), _Cat(2)

    h.store.sync(_Session(cat1, cat2))

    assert cat2.is_pinned is True
    assert cat1.is_pinned is False
    assert h.settings["pinned"][SAVE_A] == [1]   # other save untouched


def test_pins_survive_a_simulated_restart():
    settings = {"save_path": SAVE_A, "pinned": {}}
    first = PinningStore(settings, lambda: None, lambda: None)
    first.set_pinned(_Cat(3), True)

    # A fresh store over the same settings dict is what a restart produces.
    second = PinningStore(settings, lambda: None, lambda: None)
    cat = _Cat(3)
    second.sync(_Session(cat))

    assert cat.is_pinned is True
    assert second.store() == [3]


# ── 2. set_pinned: persistence + mirroring ─────────────────────────────────
def test_set_pinned_true_appends_mirrors_and_persists(h):
    cat = _Cat(11)

    h.store.set_pinned(cat, True)

    assert h.store.store() == [11]
    assert cat.is_pinned is True
    assert h.save_calls == 1
    assert h.change_calls == 1
    assert h.saved_snapshots == [{SAVE_A: [11]}]


def test_set_pinned_false_removes_mirrors_and_persists(h):
    cat = _Cat(11)
    h.store.set_pinned(cat, True)

    h.store.set_pinned(cat, False)

    assert h.store.store() == []
    assert cat.is_pinned is False
    assert h.save_calls == 2
    assert h.saved_snapshots[-1] == {SAVE_A: []}


def test_pinning_the_same_cat_twice_keeps_one_entry(h):
    cat = _Cat(7)

    h.store.set_pinned(cat, True)
    h.store.set_pinned(cat, True)

    assert h.store.store() == [7]
    assert cat.is_pinned is True


def test_unpinning_a_cat_that_was_never_pinned_is_harmless(h):
    # A true no-op: nothing to remove, so nothing is written or re-rendered.
    cat = _Cat(8)

    h.store.set_pinned(cat, False)

    assert h.store.store() == []
    assert cat.is_pinned is False
    assert h.save_calls == 0
    assert h.change_calls == 0


def test_zero_unique_id_is_a_valid_pin(h):
    cat = _Cat(0)

    h.store.set_pinned(cat, True)

    assert h.store.store() == [0]

    h.store.sync(_Session(_Cat(0)))
    assert h.store.store() == [0]


# ── 3. on_change cadence ───────────────────────────────────────────────────
def test_set_pinned_fires_on_change_once_per_call(h):
    cat = _Cat(1)

    h.store.set_pinned(cat, True)
    assert h.change_calls == 1

    h.store.set_pinned(cat, False)
    assert h.change_calls == 2


def test_noop_set_pinned_does_not_persist_or_notify(h):
    # Re-pinning an already-pinned cat is a true no-op: no settings write and
    # no re-render, while the requested state is still mirrored onto the cat.
    cat = _Cat(1)
    h.store.set_pinned(cat, True)

    h.store.set_pinned(cat, True)

    assert h.store.store() == [1]
    assert cat.is_pinned is True
    assert h.save_calls == 1
    assert h.change_calls == 1


def test_sync_never_fires_on_change(h):
    h.settings["pinned"] = {SAVE_A: [1, 2]}

    h.store.sync(_Session(_Cat(1), _Cat(2)))       # no prune
    h.store.sync(_Session(_Cat(1), _Cat(2, status="Gone")))  # prune

    assert h.change_calls == 0


# ── 4. sync: applying and pruning ──────────────────────────────────────────
def test_sync_mirrors_is_pinned_onto_every_cat(h):
    h.settings["pinned"] = {SAVE_A: [1, 2]}
    a, b, c = _Cat(1), _Cat(2), _Cat(3)

    h.store.sync(_Session(a, b, c))

    assert (a.is_pinned, b.is_pinned, c.is_pinned) == (True, True, False)
    assert h.save_calls == 0                        # nothing was pruned
    assert h.store.store() == [1, 2]


def test_sync_prunes_gone_cats_and_persists(h):
    h.settings["pinned"] = {SAVE_A: [1, 2]}
    alive, gone = _Cat(1), _Cat(2, status="Gone")

    h.store.sync(_Session(alive, gone))

    assert h.store.store() == [1]
    assert alive.is_pinned is True
    assert gone.is_pinned is False
    assert h.save_calls == 1
    assert h.saved_snapshots[-1] == {SAVE_A: [1]}


def test_sync_prunes_a_pinned_cat_that_vanished_from_the_save(h):
    h.settings["pinned"] = {SAVE_A: [1, 99]}

    h.store.sync(_Session(_Cat(1)))

    assert h.store.store() == [1]
    assert h.save_calls == 1


def test_sync_rewrites_the_store_in_place(h):
    h.settings["pinned"] = {SAVE_A: [1, 2]}
    live = h.store.store()

    h.store.sync(_Session(_Cat(1)))          # cat 2 is gone

    assert live == [1]                       # same list object, pruned
    assert h.store.store() is live


def test_sync_keeps_cats_without_a_status_attribute(h):
    h.settings["pinned"] = {SAVE_A: [5]}
    statless = _StatlessCat(5)

    h.store.sync(_Session(statless))

    assert h.store.store() == [5]
    assert statless.is_pinned is True
    assert h.save_calls == 0


@pytest.mark.parametrize("status", ["Alive", "Dead", "Donated", "Outside house"])
def test_sync_keeps_any_status_other_than_gone(h, status):
    h.settings["pinned"] = {SAVE_A: [4]}
    cat = _Cat(4, status=status)

    h.store.sync(_Session(cat))

    assert h.store.store() == [4]
    assert cat.is_pinned is True
    assert h.save_calls == 0


def test_sync_without_a_session_is_a_noop(h):
    h.settings["pinned"] = {SAVE_A: [1]}

    h.store.sync(None)

    assert h.store.store() == [1]
    assert h.save_calls == 0
    assert h.change_calls == 0


def test_sync_with_an_empty_roster_prunes_every_pin(h):
    h.settings["pinned"] = {SAVE_A: [1, 2]}

    h.store.sync(_Session())

    assert h.store.store() == []
    assert h.save_calls == 1


def test_sync_remirrors_a_flag_that_was_changed_externally(h):
    h.settings["pinned"] = {SAVE_A: [1]}
    cat = _Cat(1)

    h.store.sync(_Session(cat))
    assert cat.is_pinned is True

    cat.is_pinned = False                    # e.g. a stale UI copy
    h.store.sync(_Session(cat))

    assert cat.is_pinned is True


def test_noop_sync_does_not_persist_or_notify(h):
    h.settings["pinned"] = {SAVE_A: [1]}

    h.store.sync(_Session(_Cat(1)))

    assert h.save_calls == 0
    assert h.change_calls == 0
    assert h.saved_snapshots == []
