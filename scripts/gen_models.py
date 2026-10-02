#!/usr/bin/env python3
"""Writes the STEP models KiCad has no library model for, into
hardware/wallet_dmm.3dshapes/:

- OLED_0.91in_128x32_Module.stpZ: the SSD1306 module on its header, with the
  firmware's volts screen (from firmware/sim) lit on the glass.
- LiPo_403040_on_J5.stpZ: the pouch cell taped to the back, with its
  protection board and wires to J5. It hangs off J5's footprint.

Coordinates are KiCad model space: origin at the footprint origin, X right,
Y up (footprint Y flipped), Z up from the top of the board.

    nix develop .#models -c uv run --with cadquery python scripts/gen_models.py
"""

import gzip
import sys
import tempfile
from pathlib import Path

import cadquery as cq

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "hardware" / "wallet_dmm.3dshapes"
SCREEN = ROOT / "firmware" / "sim" / "out" / "screens" / "volts.txt"
BOARD = 0.8  # PCB thickness

GOLD = (0.83, 0.69, 0.22)
BLACK = (0.04, 0.04, 0.05)


class Model:
    def __init__(self, name):
        self.name = name
        self.asm = cq.Assembly(name=name)
        self.n = 0

    def add(self, shape, rgb):
        self.n += 1
        self.asm.add(shape, name=f"p{self.n}", color=cq.Color(*rgb))

    def box(self, x0, y0, z0, x1, y1, z1, rgb):
        lo = cq.Vector(min(x0, x1), min(y0, y1), min(z0, z1))
        size = (abs(x1 - x0), abs(y1 - y0), abs(z1 - z0))
        self.add(cq.Solid.makeBox(*size, pnt=lo), rgb)

    def rod(self, a, b, radius, rgb):
        """A cylinder from point a to point b."""
        va, vb = cq.Vector(*a), cq.Vector(*b)
        self.add(cq.Solid.makeCylinder(radius, (vb - va).Length, pnt=va, dir=vb - va), rgb)

    def save(self):
        OUT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as d:
            step = Path(d) / f"{self.name}.step"
            self.asm.export(str(step))
            (OUT / f"{self.name}.stpZ").write_bytes(gzip.compress(step.read_bytes(), mtime=0))
        print(f"wrote {OUT / self.name}.stpZ ({self.n} parts)")


def oled():
    """Pad 1 (GND) at the origin, pins down the -Y (model) axis, glass to +X."""
    m = Model("OLED_0.91in_128x32_Module")
    spacer_top = 2.5
    pcb_top = spacer_top + 1.0
    # Header: plastic spacer between the boards, four pins through both.
    m.box(-1.27, -8.89, 0, 1.27, 1.27, spacer_top, BLACK)
    for i in range(4):
        y = -2.54 * i
        m.box(-0.32, y - 0.32, -BOARD - 1.2, 0.32, y + 0.32, pcb_top + 1.0, GOLD)
    # Module PCB, 38 x 12 mm, and its regulator and capacitors by the header.
    m.box(-1.27, -9.81, spacer_top, 36.73, 2.19, pcb_top, (0.05, 0.12, 0.35))
    m.box(1.6, -6.2, pcb_top, 3.0, -3.3, pcb_top + 1.1, BLACK)
    for y in (-8.2, -1.4):
        m.box(1.7, y, pcb_top, 2.5, y + 1.2, pcb_top + 0.8, (0.75, 0.6, 0.45))
    # Glass, and the flex folded round the right edge.
    glass_top = pcb_top + 1.3
    m.box(4.0, -9.4, pcb_top, 36.0, 1.8, glass_top, (0.12, 0.13, 0.15))
    m.box(36.0, -7.5, pcb_top - 0.2, 36.6, -0.3, glass_top, (0.85, 0.55, 0.15))
    # Active area: 128 x 32 pixels, 22.38 x 5.58 mm, lit as the firmware draws it.
    x0, y_top = 8.3, -0.9
    px, py = 22.38 / 128, 5.58 / 32
    m.box(x0 - 0.3, y_top + 0.3, glass_top, x0 + 22.38 + 0.3, y_top - 5.58 - 0.3, glass_top + 0.01,
          (0.01, 0.01, 0.02))
    rows = SCREEN.read_text().split() if SCREEN.exists() else []
    if not rows:
        print(f"no screen at {SCREEN}: run firmware/sim first for a lit display", file=sys.stderr)
    for r, row in enumerate(rows[:32]):
        c = 0
        while c < len(row):
            if row[c] != "1":
                c += 1
                continue
            start = c
            while c < len(row) and row[c] == "1":
                c += 1
            m.box(x0 + start * px, y_top - r * py, glass_top + 0.01, x0 + c * px - 0.02,
                  y_top - (r + 1) * py + 0.02, glass_top + 0.03, (0.45, 0.8, 1.0))
    m.save()


def battery():
    """Relative to J5 (pad 1 at the origin, pad 2 at +4.5 mm X). The cell sits
    in the outline on B.Silkscreen, 25.5 mm right and 23 mm up (model Y)."""
    m = Model("LiPo_403040_on_J5")
    under = -BOARD - 0.05
    cx, cy = 25.5, 23.05
    # Kapton between board and cell.
    m.box(cx - 16, cy - 21, under, cx + 16, cy + 21, under - 0.06, (0.85, 0.55, 0.12))
    top = under - 0.06
    # A 30 x 40 x 4 mm cell: the pouch, and its protection board under yellow tape.
    m.box(cx - 15, cy - 16, top, cx + 15, cy + 20, top - 4.0, (0.58, 0.6, 0.64))
    m.box(cx - 15, cy - 20, top - 0.3, cx + 15, cy - 16, top - 3.8, (0.93, 0.75, 0.15))
    # Wires from the tab end to BAT+ (red) and BAT- (black), and up through the pads.
    tab = (cx - 13.0, cy - 19.0)
    for (px, py), rgb, dx in (((0.0, 0.0), (0.8, 0.1, 0.1), 0.0), ((4.5, 0.0), BLACK, 1.5)):
        z = top - 0.6
        bend = (px, py + 1.0, z)
        m.rod((tab[0] + dx, tab[1], z), bend, 0.45, rgb)
        m.rod(bend, (px, py, z), 0.45, rgb)
        m.rod((px, py, z), (px, py, 0.4), 0.33, rgb)
    m.save()


if __name__ == "__main__":
    oled()
    battery()
