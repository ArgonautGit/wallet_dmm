#!/usr/bin/env python3
"""Routes the board with Freerouting and saves the result to hardware/routes.json.

Run after changing placement or the netlist (inside the dev shell):

    kicad-cli sch export netlist --format kicadsexpr -o build/wallet_dmm.net \
        hardware/wallet_dmm.kicad_sch
    python3 scripts/autoroute.py build/wallet_dmm.net

The routes are stored as plain data so scripts/build.sh stays deterministic
and does not need the router.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pcbnew

import gen_pcb
from gen_pcb import HV_CLASS, HV_NETS, OX, OY, POWER_CLASS, POWER_NETS, ROUTES, mm

BUILD = gen_pcb.ROOT / "build"


def um(v):
    return int(round(v * 1000))


def add_classes(dsn: str) -> str:
    """Split the DSN's single net class into default, power and probe classes."""
    m = re.search(r"    \(class kicad_default(.*?)\n      \(circuit.*?\n    \)\n", dsn, re.S)
    nets = re.findall(r'"[^"]*"|[^\s"]+', m.group(1))
    unq = lambda n: n.strip('"')
    groups = {"power": [], "probe": [], "default": []}
    for n in nets:
        key = "power" if unq(n) in POWER_NETS else "probe" if unq(n) in HV_NETS else "default"
        groups[key].append(n)
    via = re.search(r'\(use_via ("[^"]*"|\S+)\)', m.group(0)).group(1)

    def block(name, members, width, clearance):
        return (f"    (class {name} {' '.join(members)}\n"
                f"      (circuit\n        (use_via {via})\n      )\n"
                f"      (rule\n        (width {um(width)})\n        (clearance {um(clearance)})\n"
                f"      )\n    )\n")

    classes = (block("kicad_default", groups["default"], gen_pcb.NETCLASS["track_width"],
                     gen_pcb.NETCLASS["clearance"])
               + block("Power", groups["power"], POWER_CLASS["track_width"], POWER_CLASS["clearance"])
               + block("Probe", groups["probe"], HV_CLASS["track_width"], HV_CLASS["clearance"]))
    return dsn[:m.start()] + classes + dsn[m.end():]


def main(netlist):
    BUILD.mkdir(exist_ok=True)
    bd, _ = gen_pcb.build(netlist, routes=False)
    dsn = BUILD / "wallet_dmm.dsn"
    ses = BUILD / "wallet_dmm.ses"
    assert pcbnew.ExportSpecctraDSN(bd.b, str(dsn))
    # Only the bottom pour counts as a plane: the top one is cut up by tracks,
    # so every top-side ground pin gets its own via down to it.
    text = re.sub(r"\n    \(plane GND \(polygon F\.Cu [^)]*\)\)", "", dsn.read_text())
    dsn.write_text(add_classes(text))
    # Freerouting is randomized; retry until it leaves nothing unrouted.
    for attempt in range(1, 6):
        ses.unlink(missing_ok=True)
        run = subprocess.run(["freerouting", "-de", str(dsn), "-do", str(ses), "-mp", "40",
                              "--gui.enabled=false"], check=True, capture_output=True, text=True)
        final = re.findall(r"final score: [\d.]+ \((\d+) unrouted", run.stdout + run.stderr)
        print(f"attempt {attempt}: {final[-1] if final else '?'} unrouted")
        if final and final[-1] == "0":
            break
    else:
        sys.exit("freerouting kept leaving connections unrouted")
    assert pcbnew.ImportSpecctraSES(bd.b, str(ses))
    tracks, vias = [], []
    for t in bd.b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            p = t.GetPosition()
            vias.append({"net": t.GetNetname(), "at": [mm(p.x) - OX, mm(p.y) - OY],
                         "dia": mm(t.GetWidth(pcbnew.F_Cu)), "drill": max(mm(t.GetDrill()), 0.3)})
        else:
            s, e = t.GetStart(), t.GetEnd()
            tracks.append({"net": t.GetNetname(), "layer": t.GetLayerName(), "width": mm(t.GetWidth()),
                           "start": [mm(s.x) - OX, mm(s.y) - OY], "end": [mm(e.x) - OX, mm(e.y) - OY]})
    ROUTES.write_text(json.dumps({"tracks": tracks, "vias": vias}, indent=1) + "\n")
    print(f"wrote {ROUTES}: {len(tracks)} tracks, {len(vias)} vias")


if __name__ == "__main__":
    main(sys.argv[1])
