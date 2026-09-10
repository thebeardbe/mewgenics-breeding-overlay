# Mewgenics Breeding Overlay

A cross-platform companion for [Mewgenics](https://store.steampowered.com/app/686060/Mewgenics/) that answers **"which of my cats is this one compatible with — and is it a safe breed?"** while you play.

Click a cat in-game, summon the overlay (`Ctrl+Shift+B` by default on Windows, configurable in Settings; tray icon elsewhere), type its name, and get every partner ranked by what matters:

- **Risk** — birth-defect chance for the pair, from its inbreeding coefficient (COI)
- **Compat** — the game's actual compatibility value (needs > 0.05 to breed)
- **Family & COI** — how the two are related (parent, half-sibling, 1st cousin …) and how much shared ancestry is close enough to matter
- **Expected kitten stats** and ≥7 count, existing kittens for the pair, lover/haters, blocked-pair reasons

Every column is sortable, headers explain each metric, and the roster refreshes automatically when the game saves. Saves are **read-only**: the overlay parses copies and never writes to your `.sav` files.

> **Same-sex pairs** (male-male / female-female) mate but never produce a
> kitten — they raise the Gay-Stray chance instead (1.1 rule) — so they're
> listed under blocked pairs with that reason. Neutral-gender cats pair
> normally with anyone.

Built on the parser/genetics engine of the MIT-licensed [MewgenicsBreedingManager](https://github.com/frankieg33/MewgenicsBreedingManager) — synced to the [maintained fork by whyayala](https://github.com/whyayala/MewgenicsBreedingManager) (v5.9.5) for the Mewgenics 1.1 breeding model.

**Breeding formulas** come from the datamined game code: the [Mewgenics breeding calculator](https://mewgenicswiki.org/tools/breeding-calculator) and [SciresM's analysis](https://gist.github.com/SciresM/95a9dbba22937420e75d4da617af1397) of `glaiel::CatData::breed`, cross-checked against [wiki.gg's datamined tables](https://mewgenics.wiki.gg/wiki/Breeding). Every formula is pinned by `tests/test_wiki_math.py`, so a future engine re-sync that drifts from the game data fails the test suite instead of silently changing your numbers.

## Install & run

No Python or install step is needed for the binary builds — download, run, done. The overlay auto-detects your save (`%APPDATA%\Glaiel Games\Mewgenics\...\saves\` on Windows, the Proton prefix equivalent on Linux), or open one manually with the 📁 button. Set `MEWGENICS_SAVES_ROOT` to override discovery.

### Windows

1. Download **`MewgenicsOverlay.exe`** from the [download section](https://mewgenics.thebeard.be/#download) of the project website (the files themselves are hosted on GitHub Releases).
2. Double-click it and press **Run anyway** when prompted (see below).
3. Launch Mewgenics, and summon the overlay from anywhere with **`Ctrl+Shift+B`** — or right-click its tray icon. The combo is configurable in **Settings → Global hotkey** (Ctrl/Alt/Shift + one letter); there is no hidden fallback, so if another app already owns the combo the overlay says so and you pick a different one.

> **Security warning — please read.** The exe is **not code-signed**, so Windows will show **“Windows protected your PC” (SmartScreen)** on first launch, and Defender/AV software may flag it. This is expected for small open-source projects: a code-signing certificate costs money. It is **not** a sign of malware. To run it: click **More info → Run anyway**. The project is MIT-licensed and fully open source — you can inspect the code, or build the exe yourself from source (below) if you prefer not to trust the release artifact.

### Linux (Arch, Ubuntu, Fedora, …)

1. Download **`MewgenicsOverlay`** from the [download section](https://mewgenics.thebeard.be/#download) of the project website (the files themselves are hosted on GitHub Releases).
2. Make it executable and run it:

```bash
chmod +x MewgenicsOverlay
./MewgenicsOverlay
```

The binary bundles Python, Qt and everything else — no dependencies to install. It targets glibc x64 (not musl/Alpine). On Wayland sessions the overlay floats correctly under XWayland (auto-selected, e.g. on Hyprland); full Wayland-native support is not guaranteed.

#### Linux: the global hotkey

Windows gets a real global hotkey through `RegisterHotKey`. Wayland forbids apps from grabbing keys, so on Linux the overlay instead exposes a toggle command and lets the desktop own the shortcut:

```bash
mewgenics-overlay --toggle           # toggles a running overlay, else starts it
mewgenics-overlay --setup-shortcut   # auto-configure for GNOME / KDE / Hyprland
mewgenics-overlay --remove-shortcut  # undo it
```

**Settings → Global hotkey → Set up desktop shortcut** runs the same setup for you:

| Desktop | What it does |
|---|---|
| GNOME | adds one gsettings custom keybinding (`org.gnome.settings-daemon.plugins.media-keys`) that runs `--toggle`; your other custom shortcuts are never rewritten |
| KDE Plasma | writes a marked `~/.local/share/applications/mewgenics-overlay.desktop` and registers it with KGlobalAccel (falls back to `kwriteconfig`, with the manual commands printed if neither is available) |
| Hyprland | appends one clearly marked line to `hyprland.conf` (backed up first) and runs `hyprctl reload` |
| anything else | prints the one-line command to bind yourself |

Any other desktop can simply bind `mewgenics-overlay --toggle` to a key in its own shortcut settings; a second launch always toggles the running instance instead of opening a second window.

### NixOS

The repository is a flake, so NixOS users install it directly from GitHub — Nix pulls the exact source revision and builds it once (then caches it):

```bash
# run without installing:
nix run github:thebeardbe/mewgenics-breeding-overlay

# install into your profile:
nix profile install github:thebeardbe/mewgenics-breeding-overlay
mewgenics-overlay
```

### From source (any distro, power users / developers)

Requires Python 3.10+ and a real Mewgenics save for full functionality.

```bash
# install as an app (GUI extra pulls in PySide6):
pip install "git+https://github.com/thebeardbe/mewgenics-breeding-overlay[ui]"
mewgenics-overlay

# or run straight from a checkout:
pip install -r requirements.txt "PySide6-Essentials>=6.5"
PYTHONPATH=src python -m mewgenics_overlay
```

Headless CLI (no GUI needed):

```bash
PYTHONPATH=src python -m mewgenics_overlay.cli list
PYTHONPATH=src python -m mewgenics_overlay.cli partners "Meeko"
PYTHONPATH=src python -m mewgenics_overlay.cli donate
```

## Using the overlay

- **Breeding tab** — pick a cat, see every compatible partner ranked by risk/chance/expected ≥7s; the ⭐ **Best match** banner highlights the strongest candidate. Right-click any row for actions (pin for breeding, etc.). Pinned cats show a 📌 and are kept out of donation advice.
- **Donations tab** — pick an NPC to see which of your cats they'd pay well for and why (only NPCs you have actually unlocked appear).
- **Theme** — Noir·Dark is the default; hit the ◐ button in the header for a light variant. Your choice is remembered.
- Column headers explain each metric; hover rows for detailed reasons. The window is a floating overlay — move it, or use the tray icon to hide/show while playing.

## Reading the partner list

| Column | Meaning |
|---|---|
| Family | How the partner relates to the focused cat (traced ≤9 generations). |
| COI | Inbreeding coefficient — shared ancestry is weighted `0.5^(gens_a+gens_b+1)`, so ancestors ~5 generations back add ~nothing. Drives Risk. |
| GenΔ | Focused cat's generation minus the partner's. |
| Risk | Birth-defect chance from the COI (green ≤5 %, red >12 %). |
| Compat | Game compatibility: `0.15 × charisma × libido × lover × sexuality`, pass line at 0.05. |
| Exp/stat, ≥7 | Expected kitten stat average, and how many stats land at 7. |

## Update check

On start (and every few hours while running) the app asks GitHub whether a
newer release exists and shows a **⬇ update** button if so. It sends no data and
never downloads anything automatically - the button just opens the
[download section](https://mewgenics.thebeard.be/#download) of the project
website. You can turn the check off in
**Settings → Check for updates on start**.

## Feedback & bug reports

Bug reports and feature requests go through the form at
[mewgenics.thebeard.be/report](https://mewgenics.thebeard.be/report) - the
**Report a problem** button in the About dialog opens it directly, and
**Copy debug info** puts the version, save path and OS details on your clipboard
for pasting into the report.

## Save safety

Never writes to a save. The parser opens SQLite read-only; the live-watch path parses a temp copy of the file. Pinned by a test that fingerprints a save before/after an engine pass.

## Tests

```bash
pip install pytest
MEWGENICS_SAMPLE_SAV=/path/to/a/save.sav pytest tests/
```

(No sample saves are committed; point the env var at one of your own.
`tests/test_wiki_math.py` needs **no** save — it pins every breeding formula
(stat inheritance, ability/disorder/defect odds, sexuality, kinship,
compatibility) to the public game-code documentation.)

## License

MIT. Parser/genetics engine vendored from [MewgenicsBreedingManager](https://github.com/frankieg33/MewgenicsBreedingManager) (MIT, © 2026 frankieg33) and its [maintained fork by whyayala](https://github.com/whyayala/MewgenicsBreedingManager) (MIT, v5.9.5, game-1.1 rules) — see `src/mewgenics_overlay/vendor/_VENDORED.md`.
