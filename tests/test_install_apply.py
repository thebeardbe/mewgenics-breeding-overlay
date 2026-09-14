"""Install executor: it places the files, edits the Wine prefix, and reports.

``install.apply.apply_install`` is the acting half of the one-click installers.
Given an :class:`~install.plan.InstallPlan` it copies every planned file (making
parent folders as needed), backs up and replaces a differing destination, sets
the Wine DLL override in a Proton prefix, and returns a per-item report.

These tests pin what the executor promises, using only temporary directories
for the fake game folder, the fake sources and a fake Proton prefix:

* a dry run reports every item but creates nothing, backs nothing up and never
  edits the registry;
* a real run places every file, makes missing parent folders, and reports each
  item as done;
* a re-run is a no-op: identical destinations are "already done", no extra
  backup appears, and nothing is deleted;
* a differing destination is moved to a timestamped ``.bak`` that keeps the old
  bytes, then replaced;
* the placement is atomic: a failure while swapping in the new content puts a
  differing destination back from its backup (naming it in the detail) and
  leaves a fresh destination absent, with no staged temp file or backup left
  behind either way;
* the Proton override lands in ``pfx/user.reg`` as ``native,builtin`` after a
  backup, a second run says "already done", and a launch-option plan never
  touches a registry file;
* the game-running check and a missing source both refuse without doing
  anything, and the report distinguishes done, already done and could not;
* the achievements line and the launch option are always surfaced.

The game-running check is injected, so no real game or process table is needed.
"""

from __future__ import annotations

import os
from pathlib import Path

from install.apply import (
    STATUS_ALREADY,
    STATUS_COULD_NOT,
    STATUS_DONE,
    STATUS_DRY_RUN,
    apply_install,
    render_report,
)
from install.plan import (
    OS_LINUX,
    VARIANT_MEWTATOR,
    VARIANT_OVERLAY_ONLY,
    VARIANT_STANDALONE,
    plan_install,
)


def never_running() -> bool:
    """A game-running check that always says the game is closed."""
    return False


def always_running() -> bool:
    """A game-running check that always says the game is running."""
    return True


# ── helpers ────────────────────────────────────────────────────────────────
def write_file(path: Path, text: str = "content") -> Path:
    """Create *path* (and its parents) with *text*; return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tree(root: Path) -> dict:
    """A snapshot of every file (bytes) and directory under *root*."""
    entries: dict = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        entries[key] = None if path.is_dir() else path.read_bytes()
    return entries


def backups(path: Path) -> list:
    """The timestamped backups that sit beside *path*."""
    return sorted(path.parent.glob(path.name + ".*.bak"))


def make_sources(tmp_path: Path, overlay_name: str = "mewgenics-overlay"):
    """A fake overlay binary and a full Mewjector mod set, all fake files."""
    src = tmp_path / "sources"
    overlay = write_file(src / overlay_name, "overlay-binary")
    loader = write_file(src / "version.dll", "loader")
    config = write_file(src / "chainloader.ini", "[mods]\n")
    dll = write_file(src / "companion.dll", "mod-dll")
    return overlay, [loader, config, dll]


def make_compatdata(tmp_path: Path, user_reg: str = "WINE REGISTRY Version 2\n"):
    """A fake Proton prefix with a ``pfx/user.reg`` already in place."""
    prefix = tmp_path / "compatdata" / "686060"
    write_file(prefix / "pfx" / "user.reg", user_reg)
    return prefix


def standalone_plan(tmp_path: Path, compatdata: Path = None, game_dir: Path = None):
    """Plan a standalone Linux install from fake sources."""
    overlay, mods = make_sources(tmp_path)
    return plan_install(
        OS_LINUX,
        VARIANT_STANDALONE,
        game_dir=str(game_dir or (tmp_path / "game")),
        overlay_binary=str(overlay),
        mod_files=[str(mod) for mod in mods],
        proton_compatdata_dir=str(compatdata) if compatdata else None,
    )


def plan_action(report, needle: str):
    """The one report item whose text mentions *needle*."""
    return next(action for action in report.actions if needle in action.item)


# ── dry run: report everything, change nothing ────────────────────────────
def test_dry_run_changes_nothing_and_reports_every_item_as_would_do(tmp_path):
    prefix = make_compatdata(tmp_path)
    plan = standalone_plan(tmp_path, compatdata=prefix)
    before = tree(tmp_path)

    report = apply_install(plan, dry_run=True, is_game_running=never_running)

    assert report.dry_run is True
    assert report.refused is None
    assert report.actions  # the registry item is included alongside the files
    assert [action.status for action in report.actions] == [
        STATUS_DRY_RUN
    ] * len(report.actions)
    # Nothing existed afterwards that did not exist before.
    assert tree(tmp_path) == before
    assert not (tmp_path / "game").exists()
    assert not backups(prefix / "pfx" / "user.reg")
    assert report.ok is True


def test_dry_run_registry_still_holds_its_original_text(tmp_path):
    original = "WINE REGISTRY Version 2\n[Software\\\\Wine\\\\DllOverrides]\n"
    prefix = make_compatdata(tmp_path, original)
    plan = standalone_plan(tmp_path, compatdata=prefix)

    report = apply_install(plan, dry_run=True, is_game_running=never_running)

    registry = plan_action(report, "user.reg")
    assert registry.status == STATUS_DRY_RUN
    assert (prefix / "pfx" / "user.reg").read_text() == original


def test_dry_run_rendering_says_would_do(tmp_path):
    plan = standalone_plan(tmp_path)

    rendered = render_report(
        apply_install(plan, dry_run=True, is_game_running=never_running)
    )

    assert "[would do]" in rendered


# ── real run: everything placed ───────────────────────────────────────────
def test_real_run_places_every_file_and_makes_missing_parents(tmp_path):
    plan = standalone_plan(tmp_path)
    game_dir = Path(plan.game_dir)
    assert not game_dir.exists()

    report = apply_install(plan, is_game_running=never_running)

    assert report.refused is None
    assert all(action.status == STATUS_DONE for action in report.actions)
    for placement in plan.files:
        destination = Path(placement.destination)
        assert destination.is_file()
        assert destination.read_bytes() == Path(placement.source).read_bytes()
    # ``mods/`` did not exist and had to be created for the content DLL.
    assert (game_dir / "mods" / "companion.dll").is_file()


def test_real_run_keeps_binary_and_unicode_content_intact(tmp_path):
    payload = "héllo ☃ \U0001F600".encode("utf-8") + bytes(range(256))
    overlay = tmp_path / "sources" / "mewgenics-overlay"
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(payload)
    plan = plan_install(
        OS_LINUX,
        VARIANT_OVERLAY_ONLY,
        game_dir=str(tmp_path / "game"),
        overlay_binary=str(overlay),
    )

    apply_install(plan, is_game_running=never_running)

    assert Path(plan.files[0].destination).read_bytes() == payload


# ── re-running is harmless ────────────────────────────────────────────────
def test_rerun_reports_already_done_and_makes_no_backups(tmp_path):
    plan = standalone_plan(tmp_path)
    first = apply_install(plan, is_game_running=never_running)
    assert all(action.status == STATUS_DONE for action in first.actions)
    after_first = tree(tmp_path)

    second = apply_install(plan, is_game_running=never_running)

    assert all(action.status == STATUS_ALREADY for action in second.actions)
    assert tree(tmp_path) == after_first
    assert not list(tmp_path.rglob("*.bak"))


def test_empty_destination_matching_an_empty_source_is_already_done(tmp_path):
    overlay = write_file(tmp_path / "sources" / "mewgenics-overlay", "")
    game_dir = tmp_path / "game"
    plan = plan_install(
        OS_LINUX,
        VARIANT_OVERLAY_ONLY,
        game_dir=str(game_dir),
        overlay_binary=str(overlay),
    )
    destination = Path(plan.files[0].destination)
    write_file(destination, "")

    report = apply_install(plan, is_game_running=never_running)

    assert report.actions[0].status == STATUS_ALREADY
    assert not backups(destination)


# ── a differing destination is backed up, then replaced ───────────────────
def test_differing_destination_is_moved_to_a_timestamped_backup(tmp_path):
    plan = standalone_plan(tmp_path)
    destination = Path(plan.files[0].destination)
    write_file(destination, "old-overlay-bytes")

    report = apply_install(plan, is_game_running=never_running)

    assert destination.read_text() == "overlay-binary"
    found = backups(destination)
    assert len(found) == 1
    # The backup keeps what used to be there, so nothing is ever lost.
    assert found[0].read_text() == "old-overlay-bytes"
    action = report.actions[0]
    assert action.status == STATUS_DONE
    assert ".bak" in action.detail


def test_two_differing_destinations_each_get_their_own_backup(tmp_path):
    plan = standalone_plan(tmp_path)
    for placement in plan.files[:2]:
        write_file(Path(placement.destination), "stale")

    apply_install(plan, is_game_running=never_running)

    for placement in plan.files[:2]:
        destination = Path(placement.destination)
        assert len(backups(destination)) == 1
        assert backups(destination)[0].read_text() == "stale"


# ── the Proton override ───────────────────────────────────────────────────
def test_proton_override_is_set_and_the_registry_backed_up_first(tmp_path):
    original = "WINE REGISTRY Version 2\n\n[Software\\\\Wine\\\\DllOverrides]\n"
    prefix = make_compatdata(tmp_path, original)
    plan = standalone_plan(tmp_path, compatdata=prefix)

    report = apply_install(plan, is_game_running=never_running)

    registry = prefix / "pfx" / "user.reg"
    text = registry.read_text()
    assert r"[Software\\Wine\\DllOverrides]" in text
    assert '"version"="native,builtin"' in text
    found = backups(registry)
    assert len(found) == 1
    assert found[0].read_text() == original
    action = plan_action(report, "user.reg")
    assert action.status == STATUS_DONE
    assert ".bak" in action.detail


def test_proton_override_replaces_a_different_existing_value(tmp_path):
    original = (
        "WINE REGISTRY Version 2\n"
        "\n"
        r"[Software\\Wine\\DllOverrides] 1700000000" "\n"
        '"version"="builtin"\n'
    )
    prefix = make_compatdata(tmp_path, original)
    plan = standalone_plan(tmp_path, compatdata=prefix)

    apply_install(plan, is_game_running=never_running)

    text = (prefix / "pfx" / "user.reg").read_text()
    assert '"version"="native,builtin"' in text
    assert '"version"="builtin"' not in text
    # The old value is still recoverable from the backup.
    assert backups(prefix / "pfx" / "user.reg")[0].read_text() == original


def test_second_run_reports_the_override_as_already_set(tmp_path):
    prefix = make_compatdata(tmp_path)
    plan = standalone_plan(tmp_path, compatdata=prefix)
    apply_install(plan, is_game_running=never_running)
    registry = prefix / "pfx" / "user.reg"
    after_first = registry.read_text()
    first_backups = backups(registry)

    second = apply_install(plan, is_game_running=never_running)

    action = plan_action(second, "user.reg")
    assert action.status == STATUS_ALREADY
    assert registry.read_text() == after_first
    assert backups(registry) == first_backups


def test_launch_option_plan_never_touches_a_registry_file(tmp_path):
    plan = standalone_plan(tmp_path)  # no compatdata -> launch option
    assert plan.proton_compatdata_dir is None
    # A stray prefix in the game folder must not be mistaken for the plan's.
    stray = write_file(Path(plan.game_dir) / "pfx" / "user.reg", "untouched")

    report = apply_install(plan, is_game_running=never_running)

    assert not any("user.reg" in action.item for action in report.actions)
    assert stray.read_text() == "untouched"
    assert not list(tmp_path.rglob("*.bak"))
    assert report.launch_option == "WINEDLLOVERRIDES=version=n,b %command%"


# ── refusals do nothing ───────────────────────────────────────────────────
def test_refuses_while_the_game_is_running_and_touches_nothing(tmp_path):
    prefix = make_compatdata(tmp_path)
    plan = standalone_plan(tmp_path, compatdata=prefix)
    before = tree(tmp_path)

    report = apply_install(plan, is_game_running=always_running)

    assert report.refused is not None
    assert report.actions == ()
    assert report.failed is True
    assert report.ok is False
    assert tree(tmp_path) == before
    assert not (tmp_path / "game").exists()
    assert "REFUSED" in render_report(report)


def test_refuses_when_a_source_is_missing_and_touches_nothing(tmp_path):
    prefix = make_compatdata(tmp_path)
    plan = standalone_plan(tmp_path, compatdata=prefix)
    missing = next(p for p in plan.files if p.source.endswith("companion.dll"))
    Path(missing.source).unlink()
    before = tree(tmp_path)

    report = apply_install(plan, is_game_running=never_running)

    assert report.refused is not None
    assert "not found" in report.refused
    assert [action.status for action in report.actions] == [STATUS_COULD_NOT]
    assert report.failed is True
    # Even the sources that do exist were left alone.
    assert tree(tmp_path) == before
    assert not (tmp_path / "game").exists()
    assert not backups(prefix / "pfx" / "user.reg")


def test_a_source_that_is_missing_is_named_in_the_report(tmp_path):
    plan = standalone_plan(tmp_path)
    missing = next(p for p in plan.files if p.source.endswith("companion.dll"))
    Path(missing.source).unlink()

    report = apply_install(plan, is_game_running=never_running)

    rendered = render_report(report)
    assert "companion.dll" in rendered
    assert "[could not]" in rendered


# ── the report separates the three outcomes ───────────────────────────────
def test_report_separates_done_already_and_could_not(tmp_path):
    plan = standalone_plan(tmp_path)
    loader = next(p for p in plan.files if p.source.endswith("version.dll"))
    dll = next(p for p in plan.files if p.source.endswith("companion.dll"))
    # An identical destination: already done, untouched.
    write_file(Path(loader.destination), Path(loader.source).read_text())
    # A directory where a file should go: could not be done.
    Path(dll.destination).mkdir(parents=True)

    report = apply_install(plan, is_game_running=never_running)

    assert plan_action(report, "version.dll").status == STATUS_ALREADY
    assert plan_action(report, "companion.dll").status == STATUS_COULD_NOT
    assert any(action.status == STATUS_DONE for action in report.actions)
    assert report.failed is True
    rendered = render_report(report)
    assert "[did it]" in rendered
    assert "[already done]" in rendered
    assert "[could not]" in rendered


# ── achievements and launch option are always surfaced ────────────────────
def test_overlay_only_report_keeps_achievements_on(tmp_path):
    overlay = write_file(tmp_path / "sources" / "mewgenics-overlay", "bin")
    plan = plan_install(
        OS_LINUX,
        VARIANT_OVERLAY_ONLY,
        game_dir=str(tmp_path / "game"),
        overlay_binary=str(overlay),
    )

    report = apply_install(plan, dry_run=True, is_game_running=never_running)

    assert report.achievements_line.startswith("Achievements: ON")
    assert plan.achievements.reason in report.achievements_line
    assert report.achievements_line in render_report(report)


def test_standalone_report_keeps_achievements_on(tmp_path):
    plan = standalone_plan(tmp_path)

    report = apply_install(plan, dry_run=True, is_game_running=never_running)

    assert report.achievements_line.startswith("Achievements: ON")
    assert plan.achievements.reason in report.achievements_line


def test_mewtator_report_turns_achievements_off_and_names_modpaths(tmp_path):
    overlay = write_file(tmp_path / "sources" / "mewgenics-overlay", "bin")
    asset = write_file(tmp_path / "mods-src" / "assets.pak", "pak")
    plan = plan_install(
        OS_LINUX,
        VARIANT_MEWTATOR,
        game_dir=str(tmp_path / "game"),
        overlay_binary=str(overlay),
        mod_files=[str(asset)],
    )

    report = apply_install(plan, dry_run=True, is_game_running=never_running)

    assert report.achievements_line.startswith("Achievements: OFF")
    assert plan.achievements.reason in report.achievements_line
    assert "-modpaths" in report.achievements_line
    rendered = render_report(report)
    assert report.achievements_line in rendered
    assert report.launch_option is not None
    assert report.launch_option in rendered


def test_launch_option_is_rendered_under_its_heading(tmp_path):
    plan = standalone_plan(tmp_path)

    report = apply_install(plan, dry_run=True, is_game_running=never_running)

    rendered = render_report(report)
    assert report.launch_option == "WINEDLLOVERRIDES=version=n,b %command%"
    assert "Steam launch option" in rendered
    assert report.launch_option in rendered


def test_refusal_still_surfaces_achievements_and_launch_option(tmp_path):
    plan = standalone_plan(tmp_path)

    report = apply_install(plan, is_game_running=always_running)

    assert report.achievements_line
    assert report.launch_option == "WINEDLLOVERRIDES=version=n,b %command%"
    rendered = render_report(report)
    assert report.achievements_line in rendered
    assert report.launch_option in rendered


# ── atomic placement: a failure never leaves a broken destination ─────────
def overlay_only_plan(tmp_path, content: str = "overlay-binary"):
    """A one-file plan and its destination, for failure injection."""
    overlay = write_file(tmp_path / "sources" / "mewgenics-overlay", content)
    plan = plan_install(
        OS_LINUX,
        VARIANT_OVERLAY_ONLY,
        game_dir=str(tmp_path / "game"),
        overlay_binary=str(overlay),
    )
    return plan, Path(plan.files[0].destination)


def parent_entries(destination: Path) -> list:
    """Every entry beside *destination*, staged temp files included."""
    if not destination.parent.exists():
        return []
    return sorted(path.name for path in destination.parent.iterdir())


def replacing_into(target: Path, real_replace, restored: list = None):
    """An ``os.replace`` that fails only when *target* is the destination.

    The backup's own move onto *target* is allowed (and recorded in
    *restored*), so a real swap failure exercises the rollback. Everything
    else goes through the real :func:`os.replace`.
    """
    def fake_replace(src, dst, *args, **kwargs):
        if Path(dst) == target:
            if Path(src).name.endswith(".bak"):
                if restored is not None:
                    restored.append(Path(src))
            else:
                raise OSError("simulated swap failure")
        return real_replace(src, dst, *args, **kwargs)

    return fake_replace


def failing_copy2(*args, **kwargs):
    """A ``shutil.copy2`` that always raises, to force a staging failure."""
    raise OSError("simulated copy failure")


def test_failed_swap_restores_the_previous_file_and_leaves_no_litter(
    tmp_path, monkeypatch
):
    plan, destination = overlay_only_plan(tmp_path)
    write_file(destination, "old-overlay-bytes")
    restored: list = []
    monkeypatch.setattr(
        "install.apply.os.replace",
        replacing_into(destination, os.replace, restored),
    )

    report = apply_install(plan, is_game_running=never_running)

    action = report.actions[0]
    assert action.status == STATUS_COULD_NOT
    assert report.failed is True
    assert report.ok is False
    # The original file is back in place, byte for byte, still under its name.
    assert destination.is_file()
    assert destination.read_text() == "old-overlay-bytes"
    # Neither the staged temp file nor the moved-aside backup survives.
    assert parent_entries(destination) == [destination.name]
    assert restored, "the swap failure should have triggered a restore"
    assert len(restored) == 1
    # The detail says the previous file was restored and names where it came
    # from; that named backup no longer sits beside the destination.
    assert "restored the previous file" in action.detail
    assert str(restored[0]) in action.detail
    assert not restored[0].exists()
    assert "[could not]" in render_report(report)


def test_failed_copy_while_replacing_keeps_the_original_untouched(
    tmp_path, monkeypatch
):
    plan, destination = overlay_only_plan(tmp_path)
    write_file(destination, "old-overlay-bytes")
    monkeypatch.setattr("install.apply.shutil.copy2", failing_copy2)

    report = apply_install(plan, is_game_running=never_running)

    action = report.actions[0]
    assert action.status == STATUS_COULD_NOT
    assert report.failed is True
    # Staging fails before anything at the destination is touched, so the old
    # file is still there and no backup was made.
    assert destination.read_text() == "old-overlay-bytes"
    assert parent_entries(destination) == [destination.name]
    assert "copy failed" in action.detail


def test_failed_swap_while_creating_leaves_no_file_and_no_temp(
    tmp_path, monkeypatch
):
    plan, destination = overlay_only_plan(tmp_path)
    monkeypatch.setattr(
        "install.apply.os.replace",
        replacing_into(destination, os.replace),
    )

    report = apply_install(plan, is_game_running=never_running)

    action = report.actions[0]
    assert action.status == STATUS_COULD_NOT
    assert not destination.exists()
    # The fresh destination is absent and the staged temp file is gone too.
    assert parent_entries(destination) == []
    assert "copy failed" in action.detail


def test_failed_copy_while_creating_leaves_no_file_and_no_temp(
    tmp_path, monkeypatch
):
    plan, destination = overlay_only_plan(tmp_path)
    monkeypatch.setattr("install.apply.shutil.copy2", failing_copy2)

    report = apply_install(plan, is_game_running=never_running)

    action = report.actions[0]
    assert action.status == STATUS_COULD_NOT
    assert not destination.exists()
    assert parent_entries(destination) == []
    assert "copy failed" in action.detail


def test_when_the_restore_also_fails_the_backup_is_kept_and_named(
    tmp_path, monkeypatch
):
    plan, destination = overlay_only_plan(tmp_path)
    write_file(destination, "old-overlay-bytes")
    real_replace = os.replace

    def fail_onto_target(src, dst, *args, **kwargs):
        if Path(dst) == destination:
            raise OSError("simulated failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr("install.apply.os.replace", fail_onto_target)

    report = apply_install(plan, is_game_running=never_running)

    action = report.actions[0]
    assert action.status == STATUS_COULD_NOT
    assert report.failed is True
    # Worst case: the destination could not be put back, but the bytes are not
    # lost. They stay at the timestamped backup, which the detail names.
    assert not destination.exists()
    kept = list(destination.parent.glob(destination.name + ".*.bak"))
    assert len(kept) == 1
    assert kept[0].read_text() == "old-overlay-bytes"
    assert str(kept[0]) in action.detail
    assert "could not be restored" in action.detail


def test_atomic_success_backs_up_replaces_and_leaves_no_temp(
    tmp_path, monkeypatch
):
    plan, destination = overlay_only_plan(tmp_path)
    write_file(destination, "old-overlay-bytes")
    # Record that nothing tries to replace outside the normal swap pattern.
    monkeypatch.setattr("install.apply.os.replace", os.replace)

    first = apply_install(plan, is_game_running=never_running)

    assert first.actions[0].status == STATUS_DONE
    assert destination.read_text() == "overlay-binary"
    found = list(destination.parent.glob(destination.name + ".*.bak"))
    assert len(found) == 1
    assert found[0].read_text() == "old-overlay-bytes"
    # Exactly the destination and its one backup: no staged temp file remains.
    assert parent_entries(destination) == sorted(
        [destination.name, found[0].name]
    )

    second = apply_install(plan, is_game_running=never_running)

    assert second.actions[0].status == STATUS_ALREADY
    assert destination.read_text() == "overlay-binary"
    assert parent_entries(destination) == sorted(
        [destination.name, found[0].name]
    )
