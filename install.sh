#!/usr/bin/env bash
#
# One-click installer for the Mewgenics overlay and companion mod on Linux and
# NixOS. It finds the game folder and the Proton prefix, then hands the work to
# `python -m install.apply`, which plans the install and applies it.
#
#   ./install.sh [--dry-run] [--variant overlay-only|standalone|mewtator] \
#                [--overlay PATH] [--game-dir PATH] [--compatdata PATH] \
#                [MOD_FILE ...]
#
# Running it twice is safe: an install that is already up to date is left alone
# and nothing is deleted. The achievements line printed by the installer is
# never swallowed.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${PYTHON:-python3}"
# Mewgenics' Steam app id, which names its Proton compatdata prefix.
app_id="686060"

variant="standalone"
overlay=""
game_dir=""
compatdata=""
dry_run=0
mods=()

usage() {
    cat <<'EOF'
Usage: install.sh [options] [MOD_FILE ...]

  --dry-run            report what would happen and change nothing
  --variant VARIANT    overlay-only | standalone | mewtator (default: standalone)
  --overlay PATH       the built overlay binary (default: a repo build)
  --game-dir PATH      override the detected Mewgenics install folder
  --compatdata PATH    override the detected Proton compatdata folder
  -h, --help           show this help

Any other arguments are companion mod files (for standalone and mewtator).
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        --variant) variant="${2:?--variant needs a value}"; shift 2 ;;
        --overlay) overlay="${2:?--overlay needs a value}"; shift 2 ;;
        --game-dir) game_dir="${2:?--game-dir needs a value}"; shift 2 ;;
        --compatdata) compatdata="${2:?--compatdata needs a value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        --) shift; mods+=("$@"); break ;;
        -*) echo "install.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
        *) mods+=("$1"); shift ;;
    esac
done

export PYTHONPATH="${here}/src${PYTHONPATH:+:${PYTHONPATH}}"

if [ -z "$overlay" ]; then
    for candidate in "$here/dist/MewgenicsOverlay" \
                     "$here/result/bin/mewgenics-overlay"; do
        if [ -x "$candidate" ]; then overlay="$candidate"; break; fi
    done
fi
[ -n "$overlay" ] || {
    echo "install.sh: pass --overlay PATH (no built overlay found)" >&2
    exit 2
}

# Find the game folder and Proton prefix with the existing discovery helpers.
if [ -z "$game_dir" ] || [ -z "$compatdata" ]; then
    detected="$(MEWGENICS_APP_ID="$app_id" "$python_bin" - <<'PY'
import os
from pathlib import Path

from mewgenics_overlay.core import gameassets

game_dir = ""
compatdata = ""
gpak = gameassets.locate_gpak()
if gpak:
    game_dir = str(Path(gpak).parent)
    prefix = Path(game_dir).parent.parent / "compatdata" / os.environ["MEWGENICS_APP_ID"]
    if prefix.is_dir():
        compatdata = str(prefix)
print(game_dir)
print(compatdata)
PY
)"
    found_game="$(printf '%s\n' "$detected" | sed -n '1p')"
    found_compatdata="$(printf '%s\n' "$detected" | sed -n '2p')"
    [ -n "$game_dir" ] || game_dir="$found_game"
    [ -n "$compatdata" ] || compatdata="$found_compatdata"
fi

[ -n "$game_dir" ] || {
    echo "install.sh: could not find the Mewgenics folder; pass --game-dir" >&2
    exit 2
}

if [ -e /etc/NIXOS ]; then target_os=nixos; else target_os=linux; fi
args=(--os "$target_os" --variant "$variant" --game-dir "$game_dir"
      --overlay "$overlay")
if [ -n "$compatdata" ]; then args+=(--compatdata "$compatdata"); fi
if [ "$dry_run" = 1 ]; then args+=(--dry-run); fi
if [ "${#mods[@]}" -gt 0 ]; then args+=("${mods[@]}"); fi

exec "$python_bin" -m install.apply "${args[@]}"
