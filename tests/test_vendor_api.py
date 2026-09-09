"""Fail-fast guard for the vendor surface the overlay depends on.

An upstream re-vendor (frankieg33 MBM / whyayala fork) that renames or
removes one of these symbols must break *here* with a clear message — not as
a scattered AttributeError deep inside rank_partners() or the Donations tab.

Names marked with a leading underscore are private in the vendored modules
but the overlay deliberately depends on them (see vendor/_VENDORED.md); keep
this list in sync when vendor syncing changes.
"""

import importlib

import pytest

_EXPECT = {
    "save_parser": [
        "Cat", "SaveData", "parse_save", "find_save_files", "GameData",
        "summarize_furniture_room", "get_all_ancestors", "can_breed",
        "risk_percent", "kinship_coi",
        "_kinship", "_stimulation_inheritance_weight",
    ],
    "breeding": [
        "PairFactors", "is_direct_family_pair", "score_pair",
        "pair_projection", "tracked_offspring",
    ],
    "visual_mutation_catalog": [
        "load_visual_mutation_names", "VISUAL_MUTATION_NAMES",
    ],
}


@pytest.mark.parametrize("module", sorted(_EXPECT))
def test_vendor_surface(module):
    m = importlib.import_module(f"mewgenics_overlay.vendor.{module}")
    for name in _EXPECT[module]:
        assert hasattr(m, name), (
            f"vendored {module}.py lost {name!r} — an upstream sync changed "
            f"the API. Re-vendor and update vendor/_VENDORED.md.")
