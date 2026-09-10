# Dev shell for the Mewgenics Breeding Overlay.
#
#   nix-shell                      # core only (python3 + lz4 + pytest, light)
#   nix-shell --arg withGui true   # adds PySide6 for the overlay UI
#
# Note: the shell APPENDS ./src to PYTHONPATH instead of overwriting it;
# nixpkgs' python wrapper injects package paths through PYTHONPATH.

{ pkgs ? import <nixpkgs> { }, withGui ? false }:

let
  py = pkgs.python3.withPackages (ps:
    [ ps.lz4 ps.pytest ]
    ++ pkgs.lib.optionals withGui [ ps.pyside6 ]);
in
pkgs.mkShell {
  packages = [ py ];
  shellHook = ''
    export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
  '';
}
