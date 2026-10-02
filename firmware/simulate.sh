#!/usr/bin/env bash
# Runs the tests and every simulation, and refreshes docs/simulation/.
# Run inside the firmware dev shell (nix develop .#firmware).
set -euo pipefail
cd "$(dirname "$0")"

cargo test -q -p dmm-core
(cd sim && python3 spice.py)                      # front end in ngspice
cargo run -q --release -p dmm-sim -- sim/out      # ADC model + firmware maths
(cd sim && python3 plots.py)
(cd app && cargo build -q --release)              # uses app/.cargo/config.toml
(cd renode && python3 run.py)                     # the real firmware in Renode

out=../docs/simulation
mkdir -p "$out"
cp sim/out/figures/*.png sim/out/summary.md "$out/"
cp renode/out/session.gif renode/out/session.md "$out/"
cp renode/out/montage.png "$out/renode-frames.png" 2>/dev/null || true
