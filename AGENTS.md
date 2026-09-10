# AGENTS.md — Mewgenics Breeding Overlay

Onboarding for LLM agents & contributors. Everything you need to navigate,
run, extend and ship this codebase, plus the hard-won gotchas that will
otherwise cost you a debugging session.

## 1. What this is

> **Mandatory reading: this repo ships `CODING_GUIDELINES.md` (PEP 8 / Google
> Python style + Qt discipline + project invariants). All agents and
> contributors **must respect it** on every change. It contains the audit
> checklist (§8) that guards releases; treat a failed audit item as a blocker,
> not a suggestion.**

A cross-platform companion overlay for **Mewgenics** (Steam, custom C++
engine). It watches the game's live save file and answers, without leaving the
game: *who should this cat breed with, how risky is it, which cats to donate
to NPCs, and which room to breed in.*

Important architectural stance: **the game is never touched** — no injection,
no memory hooks, no mod API. Everything is derived from Mewgenics' own save
file (`.sav` = LZ4-compressed SQLite, per-cat blobs) plus the optional
`resources.gpak` for in-game effect texts. Saves are opened **read-only**
(`file:…?mode=ro`) or copied before parsing.

## 2. Quick commands

```bash
# dev shell (core only; add --arg withGui true for PySide6)
nix-shell --arg withGui true

# run the GUI / headless CLI
PYTHONPATH=src python -m mewgenics_overlay [--save path]
PYTHONPATH=src python -m mewgenics_overlay.cli list
PYTHONPATH=src python -m mewgenics_overlay.cli pick "Meeko"
PYTHONPATH=src python -m mewgenics_overlay.cli partners "Baby Jane"
PYTHONPATH=src python -m mewgenics_overlay.cli donate

# tests (need a real save; skip otherwise)
MEWGENICS_SAMPLE_SAV=/path/to/steamcampaign01.sav python -m pytest tests/ -q

# GUI smoke test (no display)
QT_QPA_PLATFORM=offscreen python scripts/gui_smoke.py

# run on Hyprland/Wayland (auto-detected)
nix-build default.nix -o /tmp/mg-overlay && /tmp/mg-overlay/bin/mewgenics-overlay
```

Release = bump version in **exactly three files, atomically, never skips a
file** (`pyproject.toml`, `default.nix`, `src/mewgenics_overlay/__init__.py`),
run the audit checklist in `CODING_GUIDELINES.md` §8, update the status line
in §8 of this file if it quotes a release number, commit, then
`git tag vX.Y.Z && git push origin vX.Y.Z` — CI verifies the three files and
the tag all agree (`version-check`), then builds Windows/Linux binaries and
publishes a GitHub Release. A partial bump is a release blocker.

## 3. Layout — what lives where

```
pyproject.toml            version, deps (lz4; optional PySide6-Essentials), console scripts
default.nix / flake.nix   NixOS package (buildPythonApplication)
shell.nix                 dev shell (withGui arg adds PySide6)
README.md / LICENSE
.github/workflows/release.yml   tag → build + publish binaries
packaging/entry.py, build_windows.bat, build_linux.sh   PyInstaller

> Bug reports: the self-hosted Bugbox intake service lives in its own repo
> (thebeardbe/mewgenics-overlay-website — "Bugbox"), not here, so forks of
> the app stay clean.
scripts/gui_smoke.py      offscreen end-to-end UI test (loads save, sorts, theme, pin…)
tests/                    unit tests (see §6)
src/mewgenics_overlay/
  __init__.py             __version__
  __main__.py             GUI entry
  cli.py                  list / pick / partners / donate commands
  core/
    discovery.py          save + resources.gpak location (Windows/Proton/Linux)
    watcher.py            debounced file watcher + safe copy-before-read
    session.py            parse wrapper: cats, alive/dead, current_day,
                          npc_progress_flags, rank_partners(stimulation),
                          same-sex straight block, display_location ("Outside house")
    kinship.py            relationship labels (sibling/cousin/aunt…) + gen gap
    maladies.py           disorders/defects helpers, per-part inheritance rows,
                          sexuality_label, stat-effect net parsing helpers
    gameassets.py         GameAssets: gpak effect texts + furniture defs (async)
    stimulation.py        per-room Stimulation & Comfort from furniture
    recommend.py          ⭐ Best-match recommender (weights: 7s dominate, risk,
                          nightly attempt chance, defect stat-sign)
    donations.py          donation matrix: roster-relative strength, inbredness,
                          living offspring, line quality, defect signs, pin keepers
    vendor/               MBM parser/genetics (MIT; frankieg33 v5.8.4, synced to the
                          whyayala fork v5.9.5 for game-1.1 rules) + _VENDORED.md
  ui/
    theme.py              theme registry: "film" (Noir·Bright), "noir" (Noir·Dark),
                          C_* colour globals, STYLESHEET, risk_color, wrap_tooltip
    palette.py            window coordinator: lifecycle, tray/hotkey/open-save
                          entry points, thin delegations to the modules below
    layout.py             build(window): constructs and wires the whole widget tree
    tablectl.py           TableCoordinator: focus, partner rows, sorting, best match
    assets.py             AssetLoader: resources.gpak worker thread + result drain
    chrome.py             frameless top bar: drag grip, title/status, pin /
                          click-through / hide buttons, zoom restyle
    searchbox.py          cat search box + result dropdown (focus/clear state)
    partnertable.py       partner table: sorting, rendering, tooltips, malady lines
    focuspanel.py         focused-cat detail panel
    bestmatch.py          Best-match banner + safe-mode toggle
    partneractions.py     partner row selection text, pin menu, double-click refocus
    roombar.py            room selector (Stimulation/Comfort from furniture)
    savepanel.py          campaign save-slot cards + file picker
    windowstate.py        frameless window state: geometry, pin/topmost,
                          click-through, engage/hide, OS event hooks
    zoom.py               ZoomController: clamp/step/cycle, persistence, Ctrl+wheel
    themectl.py           ThemeController: theme choice, persistence, restyle order
    reloader.py           ReloadCoordinator: watcher, debounced reload, partner
                          jobs, generation tokens, UI-thread result drain
    updatenotice.py       update-available button + check scheduling
    aboutdialog.py        About dialog, credits, report URL, debug copy
    pinning.py            per-save pinned/keep-list store with Gone pruning
    donations_tab.py      Donations tab: NPC dropdown, candidate table with
                          Donate?/Maybe/Keep ratings + reasons, pin context menu
    app.py                bootstrap: theme-from-config, tray, hotkey install,
                          file logger + excepthook, Hyprland/xcb + gtk3-theme fix
    hotkey.py             Windows global Ctrl+Shift+B via RegisterHotKey
    config.py             per-user settings (theme, save path, pinned map, …)
```

## 4. Data flow

```
save on disk ──> discovery finds it ──> Session.parse (vendor parser)
                ──> watcher triggers reload when the game writes
Session ──> palette model (breeding rows / donation slots)
GameAssets (async gpak) ──> defect effect text, furniture Stim/Comfort
Background worker threads + token guard → results drained by a UI poll timer
```

Key invariants:
- All heavy parsing/scoring runs on background threads; the UI thread only
  adopts results whose generation token is still current.
- The palette carries its **own stylesheet** (see §7) and re-renders theme
  colours on switch; Donations tab is refreshed from the palette.

## 5. Game-rule knowledge encoded here (important for future edits)

- **Stats**: STR/DEX/CON/INT/SPD/CHA/LCK, 0–7 each. Breeding uses `base_stats`
  (birth stats); gear/mods are separate (`total_stats`).
- **Risk / COI**: birth-defect risk % from coefficient of inbreeding;
  distant shared ancestors decay by `0.5^(gens_a+gens_b+1)` (≈0 beyond ~5 gens).
- **Stimulation/Comfort**: rooms provide Stimulation (inheritance odds) and
  Comfort (scales per-roll breeding chance: `√(1 + 0.1·Comfort)`).
- **Compatibility**: `0.15 × charisma × libido × lover × sexuality`; per-night
  attempt chance is per-roll chance² (two rolls). Straight cats never breed
  same-sex; bi/gay cats can. `?` gender bypasses sexuality.
- **Birth defects**: inherit per body part; shared-line vs different-line
  matters for asymmetric parts (arms/legs/ears/…). Stat effects parsed from
  gpak; net-positive defects are treated as assets.
- **Donation matrix**: ranks vs. the *current living roster* (percentile),
  adds inbreeding penalty, living-offspring bonus (line continues), recent-line
  quality, defect sign, lovers; pinned/must-breed forced Keep.
- **Pinning**: per-save keep-list keyed by `unique_id`; cats whose status is
  `Gone` are pruned automatically.
- **Lover/rival affinity**: the save stores an affinity float beside each
  relationship UID (probed: ~0.25 when formed, ×0.9 decay otherwise), but the
  vendored parser only exposes the UID lists — affinity-driven lover
  multipliers stay approximated (1.25/0.75/1.0). Parsing it is an upstream
  (MBM fork) change, not an overlay one.
- **Fertility/twins** (twin chance = combined fertility − 1) is a hidden stat
  the game never reveals (Tink has no display for it); not modelled.
- **NPC unlocks** are read from the save's `npc_progress` flags; locked NPCs
  are hidden entirely (spoiler guard). Organ Grinder counts only *visible*
  (non-`Gone`) dead cats. Butch chapter progress and per-NPC donation counters
  are **not** in the save — shown as unsupported/notice.

## 6. Tests

| File | Covers |
|---|---|
| `test_core.py` | parsing, search, partner ranking (needs sample save) |
| `test_save_safety.py` | read-only guarantee (hash/stat unchanged) |
| `test_kinship.py` | relationship labels + generation gaps |
| `test_maladies.py` | defect/disorder inheritance, sexuality thresholds |
| `test_recommend.py` | recommender weights, chance overrides, defect sign |
| `test_stimulation.py` | Stim/Comfort effect on inheritance & odds |
| `test_location.py` | "Outside house", same-sex straight block |
| `test_donations.py` | matrix factors, NPCS, rating, organ filters, pin |
| `test_wiki_math.py` | formula pins vs mewgenicswiki.org calculator + SciresM gist + wiki.gg datamine (stat curve, ability breakpoints, inbreeding rolls, defect inheritance, sexuality averages, kinship escalation, compat/night rolls) — no save needed; a failure here is a vendor-drift alarm |
| `test_savecontroller.py`, `test_reloader.py` | parse/reload coordination, generation tokens, UI-thread result drain |
| `test_searchbox.py`, `test_chrome.py`, `test_roombar.py` | extracted UI modules: search, top bar, room selector |
| `test_bestmatch.py`, `test_partneractions.py`, `test_pinning.py`, `test_savepanel.py` | best-match banner, partner row actions, per-save pin store, save slots |
| `test_windowstate.py`, `test_zoom.py`, `test_themectl.py`, `test_updatenotice.py`, `test_aboutdialog.py` | frameless window state, zoom, themes, update notice, About/report helpers |

Plus `scripts/gui_smoke.py` for the real UI offscreen.

## 7. Hard-won gotchas (read before touching UI/threading)

- **PySide6 native event filter**: the Win32 message is a `Shiboken.VoidPtr`;
  decode with `ctypes.wintypes.MSG.from_address(int(message))` — **never**
  `message[0]` (crashes the Windows app).
- **QMessageBox has no `setOpenExternalLinks`** in PySide6. About dialog is a
  `QDialog` + rich-text `QLabel` instead.
- **Theme switching**: the palette applies its own `setStyleSheet`, which
  shadows app-wide styles. To switch themes you must restyle **both** the app
  and the palette, then re-render (rows/cat panel/donations). Colours are read
  live via `theme` module attributes (`_theme.C_GOOD` etc.).
- **Dropdown refresh**: capture the active NPC *before* rebuilding the combo —
  rebuilding fires selection handlers that overwrite the remembered state.
- **Linux file dialog crash**: `QT_QPA_PLATFORMTHEME=gtk3` without GSettings
  schemas aborts; the app forces `generic` on Linux at startup.
- **Hyprland**: Qt runs under xcb (auto-forced) so window rules/pinning work;
  Wayland-native can't float above the game.
- **Emoji header buttons**: tiny glyphs; give them `objectName = "iconbtn"`
  and bump font-size in the theme QSS.
- **Qt QSS limits**: no box-shadow, no repeating gradients; use
  `qlineargradient` and colour borders only.
- **Version lives in 3 files — bump ALL of them in the same commit, no
  skips**: partial bumps make CI/Nix/About drift. The release workflow's
  `version-check` job rejects any `v*` tag whose three files don't match
  each other and the tag.
- **Vendored files stay untouched** apart from import paths (see
  `vendor/_VENDORED.md`); keep them re-vendorable.
- Table header tooltips: set on `horizontalHeaderItem(i).setToolTip(...)`.

## 8. Status / roadmap

- Latest release: **v0.1.51**. Next: **v0.2.0** after tester review.
- Known gaps: per-NPC donation counters and Butch chapter progress are not
  recoverable from the save; Frank/retired is heuristic (abilities+stat
  gains); aggression is displayed for future fighter-room optimisation but
  not scored; grain/vignette film effects are not yet rendered.
