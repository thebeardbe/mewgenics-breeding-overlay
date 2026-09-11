"""NPC donation availability signals (pure helper + donation_report wiring).

A donation NPC only starts accepting cats when the save carries one of its
EXACT accepting tokens, a per-NPC tip-tracker property, or (Organ Grinder
only) an explicit ``organname_set`` value. Prefix matching on the NPC slug
used to falsely unlock NPCs whose quest/shop flags merely share the name.
"""

from types import SimpleNamespace

import pytest

from mewgenics_overlay.core.donations import (
    NPC_ACCEPTING_TOKENS,
    NPC_ORDER,
    NPC_PROFILES,
    NPCRSTRACKER_PREFIX,
    ORGAN_NAME_SET_PROPERTY,
    ORGAN_NAME_SET_VALUE,
    donation_report,
    npc_available,
)

# Tokens that share an NPC's slug but are quest/shop flags, not "accepting
# cats" signals. None of these may unlock anybody.
QUEST_OR_SHOP_TOKENS = (
    "beanies_quests_intro",
    "tracy_foodstorage1",
    "organ_unlock",
)


def _cat():
    return SimpleNamespace(
        name="c", age=40, defects=[], disorders=[],
        visual_mutation_entries=[], lovers=[], children=[],
        parent_a=None, parent_b=None, must_breed=False, inbredness=0.0,
        status="In House", room="",
        base_stats={"STR": 5, "DEX": 5, "CON": 5, "INT": 5,
                    "SPD": 5, "CHA": 5, "LCK": 5},
        abilities=[], stat_mod=[], death_day=None,
        has_adventured=lambda: False,
    )


def _slot(report, npc):
    return next(s for s in report if s.npc == npc)


@pytest.mark.parametrize(
    "npc,token",
    [(npc, t) for npc in NPC_ORDER for t in NPC_ACCEPTING_TOKENS[npc]],
)
def test_each_accepting_token_activates_only_its_own_npc(npc, token):
    assert token in NPC_ACCEPTING_TOKENS[npc]
    assert npc_available(npc, {token}) is True
    for other in NPC_ORDER:
        if other == npc:
            continue
        assert npc_available(other, {token}) is False, (
            f"{token!r} wrongly activated {other}")


@pytest.mark.parametrize("token", QUEST_OR_SHOP_TOKENS)
@pytest.mark.parametrize("npc", NPC_ORDER)
def test_quest_and_shop_tokens_activate_nobody(npc, token):
    assert npc_available(npc, {token}) is False


@pytest.mark.parametrize("npc", NPC_ORDER)
def test_tip_tracker_property_activates_only_matching_npc(npc):
    slug = NPC_PROFILES[npc].slug
    props = {f"{NPCRSTRACKER_PREFIX}{slug}_seen": "1"}
    assert npc_available(npc, set(), props) is True
    for other in NPC_ORDER:
        if other == npc:
            continue
        assert npc_available(other, set(), props) is False, (
            f"tracker for {npc} wrongly activated {other}")


@pytest.mark.parametrize("npc", NPC_ORDER)
def test_tracker_requires_the_slug_and_a_trailing_underscore(npc):
    slug = NPC_PROFILES[npc].slug
    # No trailing underscore: the prefix is not a complete tracker marker.
    assert npc_available(npc, set(), {f"{NPCRSTRACKER_PREFIX}{slug}": "1"}) is False
    # A different slug that merely starts with this one must not match.
    assert npc_available(
        npc, set(), {f"{NPCRSTRACKER_PREFIX}{slug}x_seen": "1"}) is False


def test_organ_name_set_activates_organ_grinder_only():
    props = {ORGAN_NAME_SET_PROPERTY: ORGAN_NAME_SET_VALUE}
    assert npc_available("Organ Grinder", set(), props) is True
    for other in NPC_ORDER:
        if other == "Organ Grinder":
            continue
        assert npc_available(other, set(), props) is False


def test_organ_name_set_zero_does_not_activate():
    props = {ORGAN_NAME_SET_PROPERTY: "0"}
    assert npc_available("Organ Grinder", set(), props) is False


@pytest.mark.parametrize("bad", ["0", "true", "True", "yes", "", " 1", "1 "])
def test_organ_name_set_only_accepts_exact_one(bad):
    assert npc_available(
        "Organ Grinder", set(), {ORGAN_NAME_SET_PROPERTY: bad}) is False


def test_empty_and_missing_inputs_are_false():
    for npc in NPC_ORDER:
        assert npc_available(npc, set()) is False
        assert npc_available(npc, set(), None) is False
        assert npc_available(npc, set(), {}) is False
    # Unknown NPC names never activate, even with a stray flag present.
    assert npc_available("Nobody", {"unlock_frank"}) is False


def test_accepting_token_passed_as_property_does_not_activate():
    # Only the npc_progress flag set carries accepting tokens; a property of
    # the same name is not an availability signal.
    props = {"unlock_beanies": "1"}
    assert npc_available("Dr. Beanies", set(), props) is False


def test_donation_report_sets_active_flags_from_properties():
    props = {f"{NPCRSTRACKER_PREFIX}{NPC_PROFILES['Frank'].slug}_seen": "1"}
    report = donation_report([_cat()], properties=props)

    assert _slot(report, "Frank").active is True
    # Every other supported NPC stays locked.
    for npc in NPC_ORDER:
        if npc == "Frank":
            continue
        assert _slot(report, npc).active is False, f"{npc} should be locked"


def test_donation_report_organ_name_set_flips_only_organ_grinder():
    report = donation_report(
        [_cat()], properties={ORGAN_NAME_SET_PROPERTY: ORGAN_NAME_SET_VALUE})
    assert _slot(report, "Organ Grinder").active is True
    for npc in NPC_ORDER:
        if npc == "Organ Grinder":
            continue
        assert _slot(report, npc).active is False


def test_donation_report_without_properties_locks_everyone():
    report = donation_report([_cat()])
    for npc in NPC_ORDER:
        assert _slot(report, npc).active is False
