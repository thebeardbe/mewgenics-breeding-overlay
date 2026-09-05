# Flake wrapper so NixOS users can `nix run` / `nix profile install` the
# overlay without touching Python. The real definition lives in default.nix.
{
  description = "Mewgenics Breeding Overlay — zero-install nix package";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkg = system: nixpkgs.legacyPackages.${system}.callPackage ./default.nix { };
    in
    {
      packages = forAllSystems (system: {
        default = pkg system;
        inherit (pkg system) mewgenics-overlay;
      });
      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${pkg system}/bin/mewgenics-overlay";
        };
      });
      defaultPackage = forAllSystems (system: pkg system);
    };
}
