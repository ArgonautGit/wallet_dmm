#!/usr/bin/env python3
"""Writes the manufacturing outputs into fab/ from the KiCad design.

fab/gerbers/            Gerber + Excellon files (also zipped for upload)
fab/bom-jlcpcb.csv      BOM in JLCPCB's assembly format (LCSC part numbers)
fab/cpl-jlcpcb.csv      Pick-and-place in JLCPCB's format
fab/bom.csv             Full BOM with manufacturer part numbers
fab/wallet_dmm-schematic.pdf, fab/wallet_dmm-assembly.pdf
"""

import csv
import io
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HW = ROOT / "hardware"
FAB = ROOT / "fab"
SCH = HW / "wallet_dmm.kicad_sch"
PCB = HW / "wallet_dmm.kicad_pcb"


def cli(*args):
    subprocess.run(["kicad-cli", *map(str, args)], check=True, stdout=subprocess.DEVNULL)


def gerbers():
    out = FAB / "gerbers"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    layers = "F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts"
    cli("pcb", "export", "gerbers", "--layers", layers, "--subtract-soldermask",
        "--no-x2", "--no-netlist", "--check-zones", "--use-drill-file-origin", "-o", f"{out}/", PCB)
    cli("pcb", "export", "drill", "--format", "excellon", "--excellon-units", "mm",
        "--excellon-zeros-format", "decimal", "--excellon-oval-format", "alternate",
        "--drill-origin", "plot", "--excellon-separate-th", "-o", f"{out}/", PCB)
    zpath = FAB / "wallet_dmm-gerbers.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.iterdir()):
            z.write(f, f.name)
    return sorted(f.name for f in out.iterdir())


def bom():
    raw = FAB / "bom-raw.csv"
    cli("sch", "export", "bom", "--fields",
        "Reference,Value,Footprint,${QUANTITY},LCSC,MPN,Manufacturer,${DNP}",
        "--labels", "Refs,Value,Footprint,Qty,LCSC,MPN,Manufacturer,DNP",
        "--group-by", "Value,Footprint,LCSC", "--ref-range-delimiter", "", "--exclude-dnp",
        "-o", raw, SCH)
    rows = list(csv.DictReader(raw.open()))
    raw.unlink()
    rows = [r for r in rows if r["LCSC"]]  # test pads and holes carry no part
    rows.sort(key=lambda r: (r["Refs"][0], int("".join(c for c in r["Refs"].split(",")[0] if c.isdigit()))))
    with (FAB / "bom.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Qty", "Value", "Footprint", "Manufacturer", "MPN", "LCSC"])
        for r in rows:
            w.writerow([r["Refs"], r["Qty"], r["Value"], r["Footprint"].split(":")[1],
                        r["Manufacturer"], r["MPN"], r["LCSC"]])
    with (FAB / "bom-jlcpcb.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for r in rows:
            w.writerow([r["Value"], r["Refs"], r["Footprint"].split(":")[1], r["LCSC"]])
    return rows


def cpl(bom_rows):
    placed = {ref.strip() for r in bom_rows for ref in r["Refs"].split(",")}
    raw = FAB / "pos-raw.csv"
    cli("pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
        "--use-drill-file-origin", "-o", raw, PCB)
    rows = list(csv.DictReader(raw.open()))
    raw.unlink()
    with (FAB / "cpl-jlcpcb.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in rows:
            if r["Ref"] not in placed:
                continue
            w.writerow([r["Ref"], f"{float(r['PosX']):.4f}mm", f"{float(r['PosY']):.4f}mm",
                        "Top" if r["Side"] == "top" else "Bottom", f"{float(r['Rot']) % 360:.0f}"])
    missing = placed - {r["Ref"] for r in rows}
    return missing


def drawings():
    cli("sch", "export", "pdf", "-o", FAB / "wallet_dmm-schematic.pdf", SCH)
    cli("pcb", "export", "pdf", "--layers", "F.Fab,Edge.Cuts", "--mode-single",
        "--include-border-title", "-o", FAB / "wallet_dmm-assembly.pdf", PCB)


if __name__ == "__main__":
    FAB.mkdir(exist_ok=True)
    files = gerbers()
    rows = bom()
    missing = cpl(rows)
    drawings()
    print("gerbers:", " ".join(files))
    print(f"bom: {len(rows)} lines, {sum(int(r['Qty']) for r in rows)} parts")
    if missing:
        print("not in placement file (through-hole, hand or wave soldered):", " ".join(sorted(missing)))
