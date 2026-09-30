{
  description = "Wallet DMM: a credit-card-sized multimeter";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      libs = pkgs.kicad.libraries;
    in {
      devShells.${system}.default = pkgs.mkShell {
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
    };
}
