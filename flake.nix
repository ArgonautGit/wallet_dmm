{
  description = "Wallet DMM: a credit-card-sized multimeter";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { nixpkgs, rust-overlay, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ rust-overlay.overlays.default ];
      };
      libs = pkgs.kicad.libraries;
      rust = pkgs.rust-bin.stable."1.95.0".default.override {
        extensions = [
          "rust-src"
          "llvm-tools"
        ];
        targets = [ "thumbv6m-none-eabi" ];
      };
    in
    {
      devShells.${system} = {
        # Hardware: schematic and board generation, checks, fab files.
        default = pkgs.mkShell {
          packages = [
            pkgs.kicad
            pkgs.freerouting
            pkgs.poppler-utils
            (pkgs.python3.withPackages (ps: [ ps.kicad ]))
          ];
          KICAD10_SYMBOL_DIR = "${libs.symbols}/share/kicad/symbols";
          KICAD10_FOOTPRINT_DIR = "${libs.footprints}/share/kicad/footprints";
          KICAD10_3DMODEL_DIR = "${libs.packages3d}/share/kicad/3dmodels";
        };

        # 3D models for parts without a library model (scripts/gen_models.py):
        # cadquery from PyPI through uv, with the libraries its OCCT wheel wants.
        models = pkgs.mkShell {
          packages = [
            pkgs.uv
            pkgs.python312
          ];
          UV_PYTHON = "${pkgs.python312}/bin/python3";
          UV_PYTHON_DOWNLOADS = "never";
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
            pkgs.libGL
            pkgs.libx11
            pkgs.libxext
            pkgs.libxrender
            pkgs.libxcb
            pkgs.libxau
            pkgs.libxdmcp
            pkgs.fontconfig
            pkgs.freetype
            pkgs.expat
            pkgs.zlib
            pkgs.stdenv.cc.cc
          ];
        };

        # Firmware: Rust for the STM32L051, flashing, and the simulations
        # (ngspice for the front end, Renode for the firmware).
        firmware = pkgs.mkShell {
          packages = [
            rust
            pkgs.probe-rs-tools
            pkgs.flip-link
            pkgs.ngspice
            pkgs.renode
            (pkgs.python3.withPackages (ps: [
              ps.numpy
              ps.matplotlib
              ps.pillow
            ]))
          ];
        };
      };
    };
}
