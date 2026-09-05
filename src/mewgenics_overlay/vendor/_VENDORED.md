# Vendored code provenance

These three modules are copied unmodified (except for one import line each) from
[frankieg33/MewgenicsBreedingManager](https://github.com/frankieg33/MewgenicsBreedingManager)
v5.8.4, which is licensed MIT (Copyright (c) 2026 frankieg33).

| File | Source | Change |
|---|---|---|
| `save_parser.py` | `src/save_parser.py` | `from visual_mutation_catalog import …` → `from mewgenics_overlay.vendor.visual_mutation_catalog import …` |
| `breeding.py` | `src/breeding.py` | `from save_parser import …` → `from mewgenics_overlay.vendor.save_parser import …` |
| `visual_mutation_catalog.py` | `src/visual_mutation_catalog.py` | none |

The vendored save parser is itself based on research credited in MBM's README
([pzx521521/mewgenics-save-editor](https://github.com/pzx521521/mewgenics-save-editor),
community reverse-engineering).

To update: copy newer files from the upstream repo and re-apply the two import
rewrites above. Everything else in this project's `mewgenics_overlay/` package
is original overlay code.
