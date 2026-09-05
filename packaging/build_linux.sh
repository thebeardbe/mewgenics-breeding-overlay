#!/usr/bin/env bash
# Build a self-contained Linux binary (no Python install needed on target).
# Run on a standard distro (or GitHub Actions ubuntu runner), not NixOS:
# PyInstaller needs the pip-wheel layout of Qt to bundle cleanly.
# Requires: Python 3.10+, PySide6-Essentials, lz4, pyinstaller.
#
# Output: dist/MewgenicsOverlay  (ship as-is or wrap into an AppImage later)
set -euo pipefail
cd "$(dirname "$0")/.."

pyinstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name MewgenicsOverlay \
  --paths src \
  --collect-submodules mewgenics_overlay \
  packaging/entry.py

echo "Built: dist/MewgenicsOverlay"
