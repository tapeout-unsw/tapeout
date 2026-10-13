{
  description = "Toolchains";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in {
      devShells = forAllSystems (pkgs:
        let
          ciTools = with pkgs; [
            verilator
            iverilog
            yosys
            python3
            gnumake
            coreutils
            bash
            git
            pkgsCross.riscv32-embedded.buildPackages.binutils
          ];
        in {
          default = pkgs.mkShell {
            packages = ciTools ++ pkgs.lib.optionals pkgs.stdenv.isLinux [ pkgs.gtkwave ];
          };

          ci = pkgs.mkShell { packages = ciTools; };
        });
    };
}
