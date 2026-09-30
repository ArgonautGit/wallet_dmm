#!/usr/bin/env bash
# Regenerates the schematic, board and manufacturing files, stopping on any
# ERC or DRC violation. Run inside the dev shell (nix develop).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build

python3 scripts/gen_schematic.py
kicad-cli sch upgrade hardware/wallet_dmm.kicad_sch >/dev/null
kicad-cli sch erc --severity-all --exit-code-violations -o build/erc.rpt \
    hardware/wallet_dmm.kicad_sch
kicad-cli sch export netlist --format kicadsexpr -o build/wallet_dmm.net \
    hardware/wallet_dmm.kicad_sch >/dev/null

python3 scripts/gen_pcb.py build/wallet_dmm.net
kicad-cli pcb drc --schematic-parity --severity-all --refill-zones --save-board \
    --exit-code-violations -o build/drc.rpt hardware/wallet_dmm.kicad_pcb

python3 scripts/fab.py
