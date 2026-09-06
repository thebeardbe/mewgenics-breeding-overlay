"""Sync vendor files to the maintained MBM fork (whyayala v5.9.5) wholesale,
then rewrite import paths + provenance headers so they stay importable inside
mewgenics_overlay. Everything else (core/, ui/) is untouched here.
"""

import re

FORK = "/tmp/mbm-whyayala/src"
VENDOR = "src/mewgenics_overlay/vendor"

SAP_DOC = '''"""Save parser and core data model — vendored from the maintained MBM fork.

Upstreams (both MIT):
  * frankieg33/MewgenicsBreedingManager (original, archived v5.8.4)
  * whyayala/MewgenicsBreedingManager (maintained fork, v5.9.5) — source of
    this copy, updated for the Mewgenics 1.1 breeding-model overhaul.
See vendor/_VENDORED.md for provenance. Only import paths differ from
upstream; the body is upstream code and must stay re-vendorable.
"""
'''

BREED_DOC = '''"""Shared breeding compatibility and scoring helpers.

---
Vendored from the maintained MBM fork (whyayala v5.9.5, MIT), which continues
frankieg33/MewgenicsBreedingManager v5.8.4 (MIT). Synced for the Mewgenics 1.1
breeding model (gender-role compat gate, neutral-sexuality rule, same-sex
never produces kittens, negative-Stimulation clamp). See _VENDORED.md.
Only import paths differ from upstream.
---
"""

from __future__ import annotations
'''

sap = open(f"{FORK}/save_parser.py", encoding="utf-8").read()
sap = re.sub(r'^""".*?"""\n\n', SAP_DOC, sap, count=1, flags=re.DOTALL)
sap = sap.replace(
    "from visual_mutation_catalog import "
    "load_visual_mutation_names, VISUAL_MUTATION_NAMES",
    "from mewgenics_overlay.vendor.visual_mutation_catalog import "
    "load_visual_mutation_names, VISUAL_MUTATION_NAMES",
)
open(f"{VENDOR}/save_parser.py", "w", encoding="utf-8").write(sap)

breed = open(f"{FORK}/breeding.py", encoding="utf-8").read()
breed = re.sub(r'^""".*?"""\n\n', BREED_DOC, breed, count=1, flags=re.DOTALL)
breed = breed.replace(
    "from save_parser import (",
    "from mewgenics_overlay.vendor.save_parser import (",
)
open(f"{VENDOR}/breeding.py", "w", encoding="utf-8").write(breed)
print("vendor synced to fork v5.9.5 (save_parser, breeding)")
