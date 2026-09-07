# NixOS package: installs the overlay with all dependencies resolved by nix.
#
#   nix-build                      # -> result/bin/mewgenics-overlay
#   nix profile install .          # system-wide, no manual setup
#   nix run .#                     # with flakes (see flake.nix)
#
# Because this is a *source* package built against nixpkgs' PySide6 (unlike
# the portable PyInstaller binaries, which must be built on non-NixOS CI), it
# is the right packaging for NixOS machines.
{
  pkgs ? import <nixpkgs> { },
}:
let
  py = pkgs.python3Packages;
  fs = pkgs.lib.fileset;
  src = fs.toSource {
    root = ./.;
    fileset = fs.unions [
      ./src
      ./pyproject.toml
      ./README.md
      ./LICENSE
    ];
  };
in
py.buildPythonApplication {
  pname = "mewgenics-overlay";
  version = "0.1.46";
  inherit src;
  format = "pyproject";

  nativeBuildInputs = [ py.setuptools ];
  propagatedBuildInputs = [ py.lz4 py.pyside6 ];

  # Headless tests need a sample .sav (not shipped); skip during nix build.
  doCheck = false;

  meta = {
    description = "Live breeding-manager overlay for Mewgenics (read-only)";
    homepage = "https://github.com/frankieg33/MewgenicsBreedingManager";
    license = pkgs.lib.licenses.mit;
    mainProgram = "mewgenics-overlay";
    platforms = pkgs.lib.platforms.linux;
  };
}
