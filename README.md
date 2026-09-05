# Mewgenics Breeding Overlay

A small cross-platform companion for [Mewgenics](https://store.steampowered.com/app/686060/Mewgenics/):
while you play, it answers **"which of my cats is this one compatible with — and is it a safe, smart breed?"**

Built on top of the parser + genetics engine of the MIT-licensed
[MewgenicsBreedingManager](https://github.com/frankieg33/MewgenicsBreedingManager)
(MBM), but as a lightweight overlay instead of a full desktop app. It reads
the same live save the game writes, so it never touches the game process.

```
[Search: type the cat you clicked]  ────────────────
┌──────────────────────────────────────────────────┐
│ Apothelia ♀ · Floor1_Small · gen 5 · 4d          │
│ STR 3 DEX 5 CON 4 INT 7 SPD 4 CHA 5 LCK 5       │
│ ♥ in love with: (none)                           │
├──────────────────────────────────────────────────┤
│ Cat          Room          Risk  Compat Exp ≥7   │
│ Simba        Floor1_Small  2.0%  0.415 5.26 1.2  │
│ Katia        Attic         2.0%  0.438 5.31 1.2  │
│ Didou        Floor2_Large 18.1%  0.417 5.74 2.0  │
│ …                                               │
│ ✗ Zinc        (both straight — same sex)         │
└──────────────────────────────────────────────────┘
```

## Why this instead of only MBM?

MBM is a full desktop app you Alt-Tab to. The overlay is the *decision moment*
compressed to one keystroke: you click a cat in-game, summon the overlay
(`Ctrl+Shift+B` on Windows / tray icon elsewhere), and see the ranked partners
you'd otherwise tab out to look up — **risk %, the game's compatibility value,
expected kitten stats, existing kittens for that pair** — then double-click a
partner to keep browsing.

## What works

| Feature | Status |
|---|---|
| Auto-detect saves (Windows `%APPDATA%`; Linux Steam/Proton prefixes) | ✅ |
| Live save watching (re-parses when the game writes) | ✅ |
| Cat search from live roster (sprites later) | ✅ |
| Ranked partners: birth-defect risk %, game compatibility, expected stat ranges/avg, ≥7 count, gen depth | ✅ |
| Safe-first ordering (2%-risk cats float to the top, not buried under inbred ones) | ✅ |
| Relationship flags: mutual lover ♥♥, hater, blocked-row reasons (direct family, straight same-sex, …) | ✅ |
| Existing kittens per pair (`tracked_offspring`) | ✅ |
| Global hotkey (Windows `RegisterHotKey`) | ✅ |
| System tray toggle (Windows + Linux desktops with a tray) | ✅ |
| Headless CLI for scripting/verification | ✅ |
| In-game hook bridge (true click-sync on both OSes — same `Mewgenics.exe` under Proton) | 🔜 next milestone |

## Distribution: zero-install binaries

Most players don't want to touch Python. Two supported routes:

**1. Standalone executables (recommended, free).** PyInstaller bundles the
interpreter + Qt + engine into one file per platform — download, double-click,
no install step:

```bash
# Windows (on any Windows box with Python)
pip install -r requirements.txt PySide6-Essentials pyinstaller
packaging\build_windows.bat          # -> dist\MewgenicsOverlay.exe

# Linux
bash packaging/build_linux.sh        # -> dist/MewgenicsOverlay
```

A GitHub Actions workflow (`.github/workflows/release.yml`) builds **both**
artifacts automatically and attaches them to a GitHub Release whenever you
push a `v*` tag:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Host the release on GitHub, or mirror to Itch.io for discoverability.

**NixOS users** get a true zero-setup package (Python + Qt resolved by nix):

```bash
nix run .#                                   # flakes
# or legacy:
nix-build -o /tmp/overlay && /tmp/overlay/bin/mewgenics-overlay
# or install system-wide:
nix profile install .
```

**2. Steam.** See the "Steam?" section below for the honest trade-offs.

## Save safety: your saves are never modified

**Read-only by construction.** This overlay never writes to a `.sav` file or
anything inside the `Glaiel Games` folder:

- The parser opens SQLite in read-only mode (`file:…?mode=ro`) and closes the
  connection immediately.
- The live-watch path never opens the real file at all: it copies the save
  (plus any `-wal`/`-shm`/`-journal` sidecars) to a temp file first and
  parses the copy — the same crash-safety trick MewgenicsBreedingManager uses.
- Settings live in your per-user config directory, never near the saves.

Pinned by `tests/test_save_safety.py`, which hashes and stat-errors a save
before/after a full engine pass and asserts zero changes.

## Requirements

- Python 3.10+
- `lz4` (engine) and optionally `PySide6-Essentials` (UI)
- A Mewgenics save. On Linux, the game runs under Steam/Proton; the overlay
  finds saves under
  `~/.steam/steam/steamapps/compatdata/<appid>/pfx/drive_c/users/<user>/AppData/Roaming/Glaiel Games/Mewgenics/<id>/saves/`.
  Override with `MEWGENICS_SAVES_ROOT` (the `…/Glaiel Games/Mewgenics` root).

## Quick start

```bash
# UI (add PySide6-Essentials first)
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt "PySide6-Essentials>=6.5"
PYTHONPATH=src python -m mewgenics_overlay --save "/path/to/steamcampaign01.sav"

# or let it auto-discover the save:
PYTHONPATH=src python -m mewgenics_overlay

# start hidden (summon with tray / Ctrl+Shift+B on Windows):
PYTHONPATH=src python -m mewgenics_overlay --hidden

# Headless, for testing / scripting:
PYTHONPATH=src python -m mewgenics_overlay.cli list
PYTHONPATH=src python -m mewgenics_overlay.cli pick Meeko
PYTHONPATH=src python -m mewgenics_overlay.cli partners Meeko
```

On NixOS: `nix-shell --arg withGui true` (see `shell.nix`).

Windows: `pip install -r requirements.txt PySide6-Essentials` then run
`python -m mewgenics_overlay` (add `src` to `PYTHONPATH` or install the
package with `pip install -e .`). The save folder is auto-detected under
`%APPDATA%\Glaiel Games\Mewgenics\<SteamId>\saves\`.

The overlay starts visible by default on both platforms; use the ✕ button to
hide it (a tray icon keeps it alive), or `--hidden` to start hidden.

GUI smoke test (no display needed):

```bash
QT_QPA_PLATFORM=offscreen python scripts/gui_smoke.py [save.sav]
```

## Tests

```bash
pip install pytest
MEWGENICS_SAMPLE_SAV=/path/to/steamcampaign01.sav pytest tests/
```

Tests skip when the env var is unset (sample saves aren't committed; copy one
from MBM's `tools/saves/saves.zip`, or use your own).

## Layout

```
src/mewgenics_overlay/
  vendor/            MBM parser + genetics (MIT, untouched — see _VENDORED.md)
  core/
    discovery.py     save location on Windows / Linux / Proton
    session.py       parsed save + relationship maps + partner ranking
    watcher.py       debounced file watcher + safe (copied) save reads
  ui/
    palette.py       the overlay window (search → focus cat → partners)
    app.py           bootstrap: tray, hotkey, arg parsing
    hotkey.py        global hotkey (Windows native; tray fallback elsewhere)
    config.py        per-user JSON settings (atomic writes)
    theme.py         colors / styles
  cli.py             headless commands (list / pick / partners)
```

## How the idea could go deeper (roadmap)

1. **In-game hook bridge (selection sync).** Both Windows and Linux/Proton run
   the *same* `Mewgenics.exe`, so a single Amoeba-style hook DLL could detect
   "cat detail opened" and whisper the cat key to the overlay over localhost.
   The overlay already exposes `set_focus_key()` for exactly that. Cost: one
   function-offset RE per game patch; we deliberately keep it optional and
   off by default. (See `repo/tools/mewgenics_analysis-master/cpp/amoeba/` for
   the proven hook+ImGui machinery.)
2. **Cat sprites** in search results (MBM renders them from `resources.gpak`
   SWF shapes; we'd point at an existing extracted `DefinedShapes/` cache).
3. **Perfect-7 progress + room-stats context** for the focused cat.
4. **Writing back to the save** (e.g. marking must-breed) via MBM's sidecar
   format so the two tools share state.

### Honest limitations

- The overlay is a *companion panel*, not pixels drawn inside the game.
  True in-game rendering requires DLL injection (feasible — Amoeba does it —
  but patch-fragile; see above).
- Clicking a cat in-game writes *nothing* to the save, so v1 needs one hotkey
  + a few keystrokes to identify the cat. The hook bridge removes that.
- Wayland compositors may not let a separate window float above the game;
  the palette is draggable so this is cosmetic.
- Mewgenics' official mod system is asset/data replacement only (GON/CSV
  `.merge/.append/.patch`) — no way to add game UI or logic through it today.

## Steam?

There is **no supported way to deliver this through Mewgenics' own Steam
page**, and it cannot be a Steam Workshop item:

- The overlay is an *external program* (reads the save like MBM does), not an
  in-game asset mod — and Mewgenics' mod support is data-file replacement
  (`-modpaths`, GON/CSV), with no workshop at all.
- Mewgenics is someone else's product: we can't add a companion/DLC to its
  Steam package.

The only Steam route is publishing the overlay as its **own independent
"Software" title** via Steam Direct. The real costs of that:

- **$100 USD per app** (Steam Direct fee) for paid *or* free apps, refunded
  only after $1,000 in net sales — a free companion app never recoups it.
- Valve **review + store page + age rating + tax/payout onboarding**, and the
  ongoing maintenance expectations that come with a storefront listing.
- Even then, Steam only downloads/updates the binary for you — it doesn't
  auto-run the overlay or integrate it with the game.

So: ship **zero-install binaries** (GitHub Release + optionally Itch.io) first
— same end-user experience as a Steam download for free — and treat a Steam
"Software" listing as a later call only if it's worth $100 + review to you.

## Credits & license

MIT. The parser and genetics code are vendored from
[MewgenicsBreedingManager](https://github.com/frankieg33/MewgenicsBreedingManager)
(MIT, © 2026 frankieg33) — see `src/mewgenics_overlay/vendor/_VENDORED.md`.
Save-format research credit goes to MBM's README (pzx521521/mewgenics-save-editor
and the community).
