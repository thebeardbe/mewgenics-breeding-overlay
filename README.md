# Mewgenics Breeding Overlay

A cross-platform companion for [Mewgenics](https://store.steampowered.com/app/686060/Mewgenics/) that answers **"which of my cats is this one compatible with — and is it a safe breed?"** while you play.

Click a cat in-game, summon the overlay (`Ctrl+Shift+B` on Windows, tray icon elsewhere), type its name, and get every partner ranked by what matters:

- **Risk** — birth-defect chance for the pair, from its inbreeding coefficient (COI)
- **Compat** — the game's actual compatibility value (needs > 0.05 to breed)
- **Family & COI** — how the two are related (parent, half-sibling, 1st cousin …) and how much shared ancestry is close enough to matter
- **Expected kitten stats** and ≥7 count, existing kittens for the pair, lover/haters, blocked-pair reasons

Every column is sortable, headers explain each metric, and the roster refreshes automatically when the game saves. Saves are **read-only**: the overlay parses copies and never writes to your `.sav` files.

Built on the parser/genetics engine of the MIT-licensed [MewgenicsBreedingManager](https://github.com/frankieg33/MewgenicsBreedingManager).

## Download

Grab the latest binary from the [Releases](https://github.com/thebeardbe/mewgenics-breeding-overlay/releases) page — no Python or install needed:

- `MewgenicsOverlay.exe` — Windows
- `MewgenicsOverlay` — Linux

NixOS users: `nix run github:thebeardbe/mewgenics-breeding-overlay`

## Running from source

Requires Python 3.10+.

```bash
pip install -r requirements.txt "PySide6-Essentials>=6.5"
python -m mewgenics_overlay                          # from the repo root (src on PYTHONPATH)
python -m mewgenics_overlay.cli partners "Meeko"     # headless
```

The overlay auto-detects your save (`%APPDATA%\Glaiel Games\Mewgenics\...\saves\` on Windows, the Proton prefix equivalent on Linux) — or open one with the 📁 button. Set `MEWGENICS_SAVES_ROOT` to override discovery.

## Reading the partner list

| Column | Meaning |
|---|---|
| Family | How the partner relates to the focused cat (traced ≤9 generations). |
| COI | Inbreeding coefficient — shared ancestry is weighted `0.5^(gens_a+gens_b+1)`, so ancestors ~5 generations back add ~nothing. Drives Risk. |
| GenΔ | Focused cat's generation minus the partner's. |
| Risk | Birth-defect chance from the COI (green ≤5 %, red >12 %). |
| Compat | Game compatibility: `0.15 × charisma × libido × lover × sexuality`, pass line at 0.05. |
| Exp/stat, ≥7 | Expected kitten stat average, and how many stats land at 7. |

## Save safety

Never writes to a save. The parser opens SQLite read-only; the live-watch path parses a temp copy of the file. Pinned by a test that fingerprints a save before/after an engine pass.

## Tests

```bash
pip install pytest
MEWGENICS_SAMPLE_SAV=/path/to/a/save.sav pytest tests/
```

(No sample saves are committed; point the env var at one of your own.)

## License

MIT. Parser/genetics engine vendored from [MewgenicsBreedingManager](https://github.com/frankieg33/MewgenicsBreedingManager) (MIT, © 2026 frankieg33) — see `src/mewgenics_overlay/vendor/_VENDORED.md`.
