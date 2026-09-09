# Vendored code provenance

These three modules are synced from the **maintained fork** of
[MewgenicsBreedingManager](https://github.com/whyayala/MewgenicsBreedingManager)
([whyayala](https://github.com/whyayala), v5.9.5), which continues the
archived original
[frankieg33/MewgenicsBreedingManager](https://github.com/frankieg33/MewgenicsBreedingManager)
(v5.8.4). Both are MIT (Copyright (c) 2026 frankieg33 / whyayala); this
project credits both in its About box and README.

The fork's v5.9.5 sources are current with the **Mewgenics 1.1 breeding
overhaul** — including the same-sex-never-produces-kittens rule, the
gender-role compatibility gate, the neutral-("?")-cat sexuality rule, and the
negative-Stimulation inheritance clamp.

| File | Source | Change |
|---|---|---|
| `save_parser.py` | fork `src/save_parser.py` | docstring → provenance; `from visual_mutation_catalog import …` → `from mewgenics_overlay.vendor.visual_mutation_catalog import …` |
| `breeding.py` | fork `src/breeding.py` | docstring → provenance; `from save_parser import …` → `from mewgenics_overlay.vendor.save_parser import …` |
| `visual_mutation_catalog.py` | identical in both upstreams | none |

The vendored save parser is itself based on research credited in MBM's README
([pzx521521/mewgenics-save-editor](https://github.com/pzx521521/mewgenics-save-editor),
community reverse-engineering).

## Private-name dependencies (contract — see tests/test_vendor_api.py)

Overlay code deliberately imports a few leading-underscore helpers across the
vendor boundary; a vendor sync must keep them (or update this list + the
callers in one move):

| Symbol | Consumed by |
|---|---|
| `save_parser._kinship` | `core/session.py` (generation tracing) |
| `save_parser.kinship_coi`, `risk_percent`, `can_breed` | `core/session.py`, `core/donations.py` |
| `save_parser._stimulation_inheritance_weight` | vendored `breeding.py` (stat inheritance) |
| `breeding.pair_projection`, `score_pair`, `tracked_offspring` | `core/donations.py`, `ui/palette.py` |
| `visual_mutation_catalog.*` | vendored parser + `core/maladies.py` |

To update: copy newer files from the fork and re-apply the two import rewrites
above. Everything else in this project's `mewgenics_overlay/` package is
original overlay code.
