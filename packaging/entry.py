"""Packaging entry point for PyInstaller builds.

`python -m mewgenics_overlay` works for source runs; frozen builds need a
plain importable module instead of a package __main__.
"""

import sys

from mewgenics_overlay.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
