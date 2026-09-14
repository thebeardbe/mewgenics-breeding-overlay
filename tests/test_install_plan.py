"""Install planner: what an install would do, decided purely and with reasons.

``install.plan.plan_install`` is the pure half of the one-click installers: it
takes the target OS, the install variant and the paths, and returns an
``InstallPlan`` describing the file placements, the Wine DLL override, the
Steam launch option and the Steam-achievements state, each with a reason.

These tests pin what the install planner promises:

* every achievement state carries a reason, and only the ``mewtator`` variant
  turns achievements off (through ``-modpaths``);
* every placement carries a reason and lands where Mewjector needs it;
* the DLL override is per platform *and* per input (``version=n,b`` on Linux,
  carried by a launch option or by the Proton prefix, never both);
* contradictory or unknown input raises instead of being guessed at.

The planner has no side effects, and the paths used below do not exist, which
is itself part of the contract: planning never touches the filesystem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from install.plan import (
    MEWJECTOR_CONFIG_NAME,
    MEWJECTOR_DLL_OVERRIDE,
    MEWJECTOR_LOADER_NAME,
    MODPATHS_FLAG,
    OS_LINUX,
    OS_NIXOS,
    OS_WINDOWS,
    OVERRIDE_LOCATION_LAUNCH_OPTION,
    OVERRIDE_LOCATION_NONE,
    OVERRIDE_LOCATION_PROTON_PREFIX,
    UnknownInstallInput,
    VARIANT_MEWTATOR,
    VARIANT_OVERLAY_ONLY,
    VARIANT_STANDALONE,
    plan_install,
)

# Paths that do not exist on this machine, so a test that quietly touched the
# filesystem would fail loudly instead of getting lucky.
LINUX_GAME_DIR = "/nonexistent/games/Mewgenics"
WINDOWS_GAME_DIR = "C:\\nonexistent\\games\\Mewgenics"
OVERLAY_BINARY = "/nonexistent/build/mewgenics-overlay"
WINDOWS_OVERLAY_BINARY = "C:\\nonexistent\\build\\mewgenics-overlay.exe"
PROTON_COMPATDATA = "/nonexistent/steam/steamapps/compatdata/686060"

MOD_DLL = "/nonexistent/mod/companion.dll"
MOD_LOADER = "/nonexistent/mod/" + MEWJECTOR_LOADER_NAME
MOD_CONFIG = "/nonexistent/mod/" + MEWJECTOR_CONFIG_NAME
ASSET_MODS = ["/nonexistent/mod/assets.pak", "/nonexistent/mod/scripts.lua"]

LOADER_MOD_FILES = [MOD_LOADER, MOD_CONFIG, MOD_DLL]

WINDOWS_MOD_LOADER = "C:\\nonexistent\\mod\\" + MEWJECTOR_LOADER_NAME
WINDOWS_MOD_CONFIG = "C:\\nonexistent\\mod\\" + MEWJECTOR_CONFIG_NAME
WINDOWS_MOD_DLL = "C:\\nonexistent\\mod\\companion.dll"
WINDOWS_LOADER_MOD_FILES = [
    WINDOWS_MOD_LOADER,
    WINDOWS_MOD_CONFIG,
    WINDOWS_MOD_DLL,
]


def linux_plan(variant, **kwargs):
    """Plan a Linux install with the standard non-existent paths."""
    kwargs.setdefault("game_dir", LINUX_GAME_DIR)
    kwargs.setdefault("overlay_binary", OVERLAY_BINARY)
    return plan_install(OS_LINUX, variant, **kwargs)


def destinations(plan):
    return [placement.destination for placement in plan.files]


def placement_for(plan, name):
    return next(p for p in plan.files if p.destination.endswith(name))


# ── achievements: the point of the whole thing ─────────────────────────────
def test_overlay_only_keeps_achievements_on_with_a_reason():
    # An install without Mewtator must never cost the user their achievements.
    plan = linux_plan(VARIANT_OVERLAY_ONLY)
    assert plan.achievements.enabled is True
    assert plan.achievements.reason.strip()


def test_standalone_keeps_achievements_on_with_a_reason():
    plan = linux_plan(VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES)
    assert plan.achievements.enabled is True
    assert plan.achievements.reason.strip()


def test_mewtator_turns_achievements_off_because_of_modpaths():
    plan = linux_plan(VARIANT_MEWTATOR, mod_files=ASSET_MODS)
    assert plan.achievements.enabled is False
    assert MODPATHS_FLAG in plan.achievements.reason
    assert plan.achievements.reason.strip()


@pytest.mark.parametrize(
    "variant, mod_files",
    [
        (VARIANT_OVERLAY_ONLY, ()),
        (VARIANT_STANDALONE, LOADER_MOD_FILES),
        (VARIANT_MEWTATOR, ASSET_MODS),
    ],
)
def test_every_achievement_state_carries_a_reason(variant, mod_files):
    plan = linux_plan(variant, mod_files=mod_files)
    assert plan.achievements.reason.strip() != ""


def test_only_mewtator_disables_achievements():
    assert linux_plan(VARIANT_OVERLAY_ONLY).achievements.enabled is True
    assert linux_plan(
        VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES
    ).achievements.enabled is True
    assert linux_plan(
        VARIANT_MEWTATOR, mod_files=ASSET_MODS
    ).achievements.enabled is False


# ── file placements ────────────────────────────────────────────────────────
def test_every_placement_carries_a_reason():
    for variant, mod_files in (
        (VARIANT_OVERLAY_ONLY, ()),
        (VARIANT_STANDALONE, LOADER_MOD_FILES),
        (VARIANT_MEWTATOR, ASSET_MODS),
    ):
        plan = linux_plan(variant, mod_files=mod_files)
        assert plan.files, variant
        for placement in plan.files:
            assert placement.source, placement
            assert placement.destination, placement
            assert placement.reason.strip(), placement


def test_mewjector_loader_and_ini_land_beside_the_executable():
    plan = linux_plan(VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES)
    assert placement_for(plan, MEWJECTOR_LOADER_NAME).destination == (
        LINUX_GAME_DIR + "/" + MEWJECTOR_LOADER_NAME
    )
    assert placement_for(plan, MEWJECTOR_CONFIG_NAME).destination == (
        LINUX_GAME_DIR + "/" + MEWJECTOR_CONFIG_NAME
    )


def test_mod_dll_lands_in_the_game_mods_folder():
    plan = linux_plan(VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES)
    assert placement_for(plan, "companion.dll").destination == (
        LINUX_GAME_DIR + "/mods/companion.dll"
    )


def test_overlay_binary_is_placed_in_the_game_dir():
    plan = linux_plan(VARIANT_OVERLAY_ONLY)
    assert len(plan.files) == 1
    placed = plan.files[0]
    assert placed.source == OVERLAY_BINARY
    assert placed.destination == LINUX_GAME_DIR + "/mewgenics-overlay"


def test_overlay_only_places_no_mod_files():
    plan = linux_plan(VARIANT_OVERLAY_ONLY)
    assert destinations(plan) == [LINUX_GAME_DIR + "/mewgenics-overlay"]


def test_mewtator_places_asset_mods_in_the_mods_folder_and_no_loader():
    plan = linux_plan(VARIANT_MEWTATOR, mod_files=ASSET_MODS)
    assert placement_for(plan, "assets.pak").destination == (
        LINUX_GAME_DIR + "/mods/assets.pak"
    )
    assert placement_for(plan, "scripts.lua").destination == (
        LINUX_GAME_DIR + "/mods/scripts.lua"
    )
    # Mewtator supplies its own loader; the planner must not invent one.
    assert MEWJECTOR_LOADER_NAME not in [
        p.destination.rsplit("/", 1)[-1] for p in plan.files
    ]


def test_windows_plan_uses_windows_path_separators():
    plan = plan_install(
        OS_WINDOWS,
        VARIANT_STANDALONE,
        game_dir=WINDOWS_GAME_DIR,
        overlay_binary=WINDOWS_OVERLAY_BINARY,
        mod_files=WINDOWS_LOADER_MOD_FILES,
    )
    assert placement_for(plan, MEWJECTOR_LOADER_NAME).destination == (
        WINDOWS_GAME_DIR + "\\" + MEWJECTOR_LOADER_NAME
    )
    assert placement_for(plan, "companion.dll").destination == (
        WINDOWS_GAME_DIR + "\\mods\\companion.dll"
    )


# ── placement reasons name only the mechanism the variant uses ────────────
def test_standalone_reasons_never_mention_modpaths():
    # Standalone is loaded by Mewjector from ``mods/``; ``-modpaths`` belongs
    # to Mewtator, so advising it here would be wrong.
    plan = linux_plan(VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES)
    assert placement_for(plan, "companion.dll").reason
    for placement in plan.files:
        assert MODPATHS_FLAG not in placement.reason


def test_standalone_content_reason_names_mewjector():
    plan = linux_plan(VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES)
    assert "Mewjector" in placement_for(plan, "companion.dll").reason


def test_mewtator_reasons_never_mention_the_mewjector_loader():
    # Mewtator does not use the Mewjector loader, so no placed file may claim
    # it does.
    plan = linux_plan(VARIANT_MEWTATOR, mod_files=ASSET_MODS)
    for placement in plan.files:
        assert "Mewjector" not in placement.reason


def test_mewtator_content_reason_names_modpaths():
    plan = linux_plan(VARIANT_MEWTATOR, mod_files=ASSET_MODS)
    assert MODPATHS_FLAG in placement_for(plan, "assets.pak").reason


# ── DLL override ───────────────────────────────────────────────────────────
def test_linux_standalone_uses_version_override_via_launch_option_by_default():
    plan = linux_plan(VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES)
    assert plan.dll_override.value == MEWJECTOR_DLL_OVERRIDE
    assert MEWJECTOR_DLL_OVERRIDE == "version=n,b"
    assert plan.dll_override.location == OVERRIDE_LOCATION_LAUNCH_OPTION
    assert plan.dll_override.reason.strip()


def test_linux_standalone_uses_proton_prefix_when_compatdata_is_given():
    plan = linux_plan(
        VARIANT_STANDALONE,
        mod_files=LOADER_MOD_FILES,
        proton_compatdata_dir=PROTON_COMPATDATA,
    )
    assert plan.dll_override.value == MEWJECTOR_DLL_OVERRIDE
    assert plan.dll_override.location == OVERRIDE_LOCATION_PROTON_PREFIX
    # The prefix carries the override, so no launch option is proposed...
    assert plan.launch_option.text is None
    # ...and the planner never proposes both mechanisms at once.
    assert plan.dll_override.location != OVERRIDE_LOCATION_LAUNCH_OPTION


def test_nixos_follows_the_same_wine_rules_as_linux():
    plan = plan_install(
        OS_NIXOS,
        VARIANT_STANDALONE,
        game_dir=LINUX_GAME_DIR,
        overlay_binary=OVERLAY_BINARY,
        mod_files=LOADER_MOD_FILES,
    )
    assert plan.os_name == OS_NIXOS
    assert plan.dll_override.value == MEWJECTOR_DLL_OVERRIDE
    assert plan.dll_override.location == OVERRIDE_LOCATION_LAUNCH_OPTION


def test_windows_needs_no_dll_override():
    plan = plan_install(
        OS_WINDOWS,
        VARIANT_STANDALONE,
        game_dir=WINDOWS_GAME_DIR,
        overlay_binary=WINDOWS_OVERLAY_BINARY,
        mod_files=WINDOWS_LOADER_MOD_FILES,
    )
    assert plan.dll_override.value is None
    assert plan.dll_override.location == OVERRIDE_LOCATION_NONE
    assert plan.dll_override.reason.strip()


def test_overlay_only_needs_no_dll_override():
    plan = linux_plan(VARIANT_OVERLAY_ONLY)
    assert plan.dll_override.value is None
    assert plan.dll_override.location == OVERRIDE_LOCATION_NONE
    assert plan.dll_override.reason.strip()


def test_mewtator_alone_needs_no_dll_override():
    # No loader among the mod files, so Wine has nothing to override.
    plan = linux_plan(VARIANT_MEWTATOR, mod_files=ASSET_MODS)
    assert plan.dll_override.value is None
    assert plan.dll_override.location == OVERRIDE_LOCATION_NONE


def test_mewtator_with_the_loader_is_refused():
    # Mewtator is a separate mod-path mechanism and never uses Mewjector's
    # loader, so a loader among its files is contradictory input, not a plan
    # that quietly installs one.
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_MEWTATOR, mod_files=LOADER_MOD_FILES)


# ── recommended launch option ──────────────────────────────────────────────
def test_overlay_only_needs_no_launch_option():
    plan = linux_plan(VARIANT_OVERLAY_ONLY)
    assert plan.launch_option.text is None
    assert plan.launch_option.reason.strip()


def test_launch_option_carries_the_override_when_no_proton_prefix():
    plan = linux_plan(VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES)
    assert plan.launch_option.text == (
        "WINEDLLOVERRIDES=version=n,b %command%"
    )


def test_mewtator_launch_option_uses_the_modpaths_form():
    plan = linux_plan(VARIANT_MEWTATOR, mod_files=ASSET_MODS)
    assert plan.launch_option.text == '-modpaths "%s/mods"' % LINUX_GAME_DIR
    assert MODPATHS_FLAG in plan.launch_option.text


def test_mewtator_with_proton_prefix_still_recommends_modpaths():
    plan = linux_plan(
        VARIANT_MEWTATOR,
        mod_files=ASSET_MODS,
        proton_compatdata_dir=PROTON_COMPATDATA,
    )
    assert plan.launch_option.text == '-modpaths "%s/mods"' % LINUX_GAME_DIR


def test_mewtator_with_only_the_loader_is_refused():
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_MEWTATOR, mod_files=[MOD_LOADER])


def test_mewtator_with_only_the_chainloader_ini_is_refused():
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_MEWTATOR, mod_files=[MOD_CONFIG])


def test_windows_standalone_needs_no_launch_option():
    plan = plan_install(
        OS_WINDOWS,
        VARIANT_STANDALONE,
        game_dir=WINDOWS_GAME_DIR,
        overlay_binary=WINDOWS_OVERLAY_BINARY,
        mod_files=WINDOWS_LOADER_MOD_FILES,
    )
    assert plan.launch_option.text is None
    assert plan.launch_option.reason.strip()


def test_windows_overlay_only_needs_no_launch_option():
    plan = plan_install(
        OS_WINDOWS,
        VARIANT_OVERLAY_ONLY,
        game_dir=WINDOWS_GAME_DIR,
        overlay_binary=WINDOWS_OVERLAY_BINARY,
    )
    assert plan.launch_option.text is None


def test_windows_mewtator_launch_option_has_no_command_placeholder():
    # Windows launch options are appended to the exe directly, so ``%command%``
    # would be garbage there.
    plan = plan_install(
        OS_WINDOWS,
        VARIANT_MEWTATOR,
        game_dir=WINDOWS_GAME_DIR,
        overlay_binary=WINDOWS_OVERLAY_BINARY,
        mod_files=[
            "C:\\nonexistent\\mod\\assets.pak",
        ],
    )
    assert plan.launch_option.text == '-modpaths "C:\\nonexistent\\games\\Mewgenics\\mods"'
    assert "%command%" not in plan.launch_option.text


@pytest.mark.parametrize(
    "variant, mod_files, proton_dir",
    [
        (VARIANT_OVERLAY_ONLY, (), None),
        (VARIANT_STANDALONE, LOADER_MOD_FILES, None),
        (VARIANT_STANDALONE, LOADER_MOD_FILES, PROTON_COMPATDATA),
        (VARIANT_MEWTATOR, ASSET_MODS, None),
        (VARIANT_MEWTATOR, ASSET_MODS, PROTON_COMPATDATA),
    ],
)
def test_every_launch_option_carries_a_reason(variant, mod_files, proton_dir):
    kwargs = {"mod_files": mod_files}
    if proton_dir:
        kwargs["proton_compatdata_dir"] = proton_dir
    plan = linux_plan(variant, **kwargs)
    assert plan.launch_option.reason.strip()


# ── refusals: raise, never guess ───────────────────────────────────────────
def test_unknown_operating_system_is_refused():
    with pytest.raises(UnknownInstallInput):
        plan_install(
            "beos",
            VARIANT_OVERLAY_ONLY,
            game_dir=LINUX_GAME_DIR,
            overlay_binary=OVERLAY_BINARY,
        )


def test_unknown_variant_is_refused():
    with pytest.raises(UnknownInstallInput):
        plan_install(
            OS_LINUX,
            "kitchen-sink",
            game_dir=LINUX_GAME_DIR,
            overlay_binary=OVERLAY_BINARY,
        )


def test_overlay_only_with_mod_files_is_refused():
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_OVERLAY_ONLY, mod_files=ASSET_MODS)


def test_standalone_without_the_loader_is_refused():
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_STANDALONE, mod_files=[MOD_DLL])


def test_standalone_without_any_mod_files_is_refused():
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_STANDALONE)


def test_standalone_with_only_the_loader_is_refused():
    # The loader alone has no mod DLL to load, so the install would place a
    # loader that finds an empty ``mods/`` folder.
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_STANDALONE, mod_files=[MOD_LOADER])


def test_standalone_with_loader_and_ini_but_no_mod_dll_is_refused():
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_STANDALONE, mod_files=[MOD_LOADER, MOD_CONFIG])


def test_standalone_with_only_the_chainloader_ini_is_refused():
    # The ini alone is not the Mewjector loader, so the install would install
    # a config for a loader that never gets placed.
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_STANDALONE, mod_files=[MOD_CONFIG])


def test_mewtator_without_mod_files_is_refused():
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_MEWTATOR)


def test_missing_game_dir_is_refused():
    with pytest.raises(UnknownInstallInput):
        plan_install(
            OS_LINUX,
            VARIANT_OVERLAY_ONLY,
            game_dir="",
            overlay_binary=OVERLAY_BINARY,
        )


def test_missing_overlay_binary_is_refused():
    with pytest.raises(UnknownInstallInput):
        plan_install(
            OS_LINUX,
            VARIANT_OVERLAY_ONLY,
            game_dir=LINUX_GAME_DIR,
            overlay_binary="",
        )


def test_mod_files_as_a_bare_string_is_refused():
    # A string is iterable, so without this guard it would silently become one
    # placement per character.
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_STANDALONE, mod_files=MOD_LOADER)


def test_mod_files_as_a_bare_path_object_is_refused():
    # ``pathlib.Path`` is not iterable as a path; without the guard it would
    # either iterate its parts or raise an unrelated TypeError.
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_STANDALONE, mod_files=Path(MOD_LOADER))


def test_mod_files_that_are_not_iterable_are_refused():
    with pytest.raises(UnknownInstallInput):
        linux_plan(VARIANT_STANDALONE, mod_files=42)


def test_two_mod_files_at_the_same_destination_are_refused():
    # Two mod DLLs with the same name from different folders would overwrite
    # each other in ``mods/``; refusing is better than silently dropping one.
    with pytest.raises(UnknownInstallInput):
        linux_plan(
            VARIANT_STANDALONE,
            mod_files=[
                MOD_LOADER,
                MOD_CONFIG,
                "/nonexistent/a/companion.dll",
                "/nonexistent/b/companion.dll",
            ],
        )


def test_duplicate_destinations_are_compared_case_insensitively():
    # Windows folds file-name case, so Mod.dll and mod.dll collide there even
    # though the strings differ.
    with pytest.raises(UnknownInstallInput):
        linux_plan(
            VARIANT_STANDALONE,
            mod_files=[
                MOD_LOADER,
                MOD_CONFIG,
                "/nonexistent/a/Companion.dll",
                "/nonexistent/b/companion.dll",
            ],
        )


def test_a_mod_file_that_would_land_on_the_overlay_is_refused():
    # The overlay binary is placed in the game folder by name, and the
    # Mewjector loader lands there too. An overlay built as ``version.dll``
    # would silently overwrite the loader (or be overwritten by it), so the
    # collision check must include the overlay placement, not just the mod
    # files.
    with pytest.raises(UnknownInstallInput):
        plan_install(
            OS_LINUX,
            VARIANT_STANDALONE,
            game_dir=LINUX_GAME_DIR,
            overlay_binary="/nonexistent/build/" + MEWJECTOR_LOADER_NAME,
            mod_files=LOADER_MOD_FILES,
        )


# ── input normalisation ────────────────────────────────────────────────────
@pytest.mark.parametrize("os_name", ["LINUX", " Linux ", "NixOS", "Windows"])
def test_os_names_are_normalised(os_name):
    plan = plan_install(
        os_name,
        VARIANT_OVERLAY_ONLY,
        game_dir=LINUX_GAME_DIR,
        overlay_binary=OVERLAY_BINARY,
    )
    assert plan.os_name == os_name.strip().lower()


@pytest.mark.parametrize(
    "variant", ["OVERLAY-ONLY", " Standalone ", "Mewtator"]
)
def test_variant_names_are_normalised(variant):
    key = variant.strip().lower()
    mod_files = () if key == VARIANT_OVERLAY_ONLY else (
        ASSET_MODS if key == VARIANT_MEWTATOR else LOADER_MOD_FILES
    )
    plan = linux_plan(variant, mod_files=mod_files)
    assert plan.variant == key


def test_loader_name_matching_is_case_insensitive():
    # Windows paths are case-insensitive; a loader shipped as VERSION.DLL must
    # still be placed beside the executable and still earn the override.
    plan = linux_plan(
        VARIANT_STANDALONE,
        mod_files=["/nonexistent/mod/VERSION.DLL", MOD_CONFIG, MOD_DLL],
    )
    assert placement_for(plan, "VERSION.DLL").destination == (
        LINUX_GAME_DIR + "/VERSION.DLL"
    )
    assert plan.dll_override.value == MEWJECTOR_DLL_OVERRIDE


# ── purity: no filesystem, no network ──────────────────────────────────────
@pytest.mark.parametrize(
    "variant, mod_files",
    [
        (VARIANT_OVERLAY_ONLY, ()),
        (VARIANT_STANDALONE, LOADER_MOD_FILES),
        (VARIANT_MEWTATOR, ASSET_MODS),
    ],
)
def test_planning_nonexistent_paths_still_returns_a_plan(variant, mod_files):
    plan = linux_plan(variant, mod_files=mod_files)
    assert plan.game_dir == LINUX_GAME_DIR
    assert plan.files
    for placement in plan.files:
        # Destinations are derived from the inputs, not from probing the disk.
        assert placement.destination.startswith(LINUX_GAME_DIR)


def test_plan_does_not_create_the_game_dir():
    import os

    assert not os.path.exists(LINUX_GAME_DIR)
    linux_plan(VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES)
    assert not os.path.exists(LINUX_GAME_DIR)


def test_plan_records_the_proton_prefix_it_was_given():
    plan = linux_plan(
        VARIANT_STANDALONE,
        mod_files=LOADER_MOD_FILES,
        proton_compatdata_dir=PROTON_COMPATDATA,
    )
    assert plan.proton_compatdata_dir == PROTON_COMPATDATA


def test_plan_without_proton_prefix_records_none():
    plan = linux_plan(VARIANT_STANDALONE, mod_files=LOADER_MOD_FILES)
    assert plan.proton_compatdata_dir is None


def test_nixos_records_the_proton_prefix_it_was_given():
    plan = plan_install(
        OS_NIXOS,
        VARIANT_STANDALONE,
        game_dir=LINUX_GAME_DIR,
        overlay_binary=OVERLAY_BINARY,
        mod_files=LOADER_MOD_FILES,
        proton_compatdata_dir=PROTON_COMPATDATA,
    )
    assert plan.proton_compatdata_dir == PROTON_COMPATDATA


def test_overlay_only_records_no_proton_prefix():
    # No loader means nothing to override, so the path would name an action
    # that never happens.
    plan = linux_plan(
        VARIANT_OVERLAY_ONLY, proton_compatdata_dir=PROTON_COMPATDATA
    )
    assert plan.proton_compatdata_dir is None


def test_mewtator_records_no_proton_prefix():
    plan = linux_plan(
        VARIANT_MEWTATOR,
        mod_files=ASSET_MODS,
        proton_compatdata_dir=PROTON_COMPATDATA,
    )
    assert plan.proton_compatdata_dir is None


def test_windows_standalone_records_no_proton_prefix():
    # Windows ignores the Wine override entirely, so a compatdata path given
    # there must not be recorded either.
    plan = plan_install(
        OS_WINDOWS,
        VARIANT_STANDALONE,
        game_dir=WINDOWS_GAME_DIR,
        overlay_binary=WINDOWS_OVERLAY_BINARY,
        mod_files=WINDOWS_LOADER_MOD_FILES,
        proton_compatdata_dir="C:\\nonexistent\\steam\\compatdata",
    )
    assert plan.proton_compatdata_dir is None


def test_planning_does_not_mutate_its_inputs_and_is_deterministic():
    mod_files = list(LOADER_MOD_FILES)
    first = linux_plan(VARIANT_STANDALONE, mod_files=mod_files)
    assert mod_files == LOADER_MOD_FILES
    second = linux_plan(VARIANT_STANDALONE, mod_files=mod_files)
    assert first == second
