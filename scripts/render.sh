#!/usr/bin/env bash
# 3D renders of the assembled board into docs/renders/, and a GLB for viewers
# into build/. Run inside the dev shell (nix develop) after scripts/build.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
out=docs/renders
mkdir -p "$out" build
pcb=hardware/wallet_dmm.kicad_pcb
render() { kicad-cli pcb render --quality high --background opaque -w 2400 -h 1500 "$@" "$pcb" >/dev/null; }
render --side top -o "$out/top.png"
render --side bottom -o "$out/bottom.png"
render --rotate "-35,0,-30" --perspective --zoom 1.1 --floor -o "$out/front.png"
render --rotate "-145,0,-25" --perspective --zoom 1.1 --floor -o "$out/back.png"
render --rotate "-80,0,0" --perspective --zoom 1.6 -o "$out/edge.png"
kicad-cli pcb export glb --include-tracks --include-pads --include-zones --include-silkscreen \
    --include-soldermask --subst-models -f -o build/wallet_dmm.glb "$pcb" >/dev/null
echo "renders in $out, model in build/wallet_dmm.glb"
