#!/usr/bin/env python3
"""Generates hardware/wallet_dmm.kicad_sch.

The schematic is the source of truth for the board: the PCB script reads the
netlist KiCad exports from it. Symbols come from KiCad's stock libraries.
Each block is wired within itself; blocks meet through net labels and power
symbols, and the MCU's pins are all labelled.
"""

import math
import os
import uuid
from pathlib import Path

from sexpr import Sym, dump, find, find_all, parse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "hardware" / "wallet_dmm.kicad_sch"
SYMDIR = Path(os.environ["KICAD10_SYMBOL_DIR"])
PROJECT = "wallet_dmm"
NS = uuid.UUID("0f4a6f2c-58d1-4c43-b1f3-7f0f6e2d3a10")
ROOT_UUID = str(uuid.uuid5(NS, "root"))

Y = Sym("yes")
N = Sym("no")

# Nets drawn with power symbols rather than labels.
POWER = {"GND": "GND", "+3V0": "+3V0", "VBUS": "VBUS", "+BATT": "+BATT", "+VSW": "+VSW"}


def uid(*key):
    return str(uuid.uuid5(NS, "/".join(map(str, key))))


# ------------------------------------------------------------------ library

_libs = {}


def lib_symbol(lib_id):
    """Symbol definition from the stock library, flattened if it extends another."""
    lib, name = lib_id.split(":")
    if lib not in _libs:
        _libs[lib] = parse((SYMDIR / f"{lib}.kicad_sym").read_text())
    syms = {s[1]: s for s in find_all(_libs[lib], "symbol")}
    sym = syms[name]
    ext = find(sym, "extends")
    if ext:
        parent = syms[ext[1]]
        child_props = {p[1]: p for p in find_all(sym, "property")}
        flat = [Sym("symbol"), name]
        for c in parent[2:]:
            if isinstance(c, list) and c[0] == "property":
                flat.append(child_props.pop(c[1], c))
            elif isinstance(c, list) and c[0] == "symbol":
                sub = list(c)
                sub[1] = sub[1].replace(ext[1], name, 1)
                flat.append(sub)
            else:
                flat.append(c)
        flat[2:2] = list(child_props.values())
        sym = flat
    out = list(sym)
    out[1] = lib_id
    return out


def pins_of(sym):
    """{number: (x, y, angle)} in library coordinates (y up)."""
    pins = {}

    def walk(n):
        for c in n:
            if isinstance(c, list) and c:
                if c[0] == "pin":
                    at = find(c, "at")
                    num = find(c, "number")[1]
                    pins[num] = (float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0)
                elif c[0] == "symbol":
                    walk(c)

    walk(sym)
    return pins


# ---------------------------------------------------------------- schematic


class Part(dict):
    """Pin number -> (x, y) on the sheet, plus the outward direction of each pin."""

    def __init__(self, ref):
        super().__init__()
        self.ref = ref
        self.dirs = {}
        self.done = set()


class Sheet:
    def __init__(self):
        self.lib = {}
        self.items = []
        self.segments = []
        self.points = []
        self.nets = {}
        self.pwr = 0
        self.flg = 0

    def _libsym(self, lib_id):
        if lib_id not in self.lib:
            self.lib[lib_id] = lib_symbol(lib_id)
        return self.lib[lib_id]

    def place(self, ref, lib_id, value, x, y, rot=0, mirror=False, footprint=None, flip=False,
              fields=None, ref_at=None, val_at=None, in_bom=True, on_board=True,
              hide_value=False, hide_ref=False):
        sym = self._libsym(lib_id)
        part = Part(ref)
        c, s = round(math.cos(math.radians(rot))), round(math.sin(math.radians(rot)))

        def tf(sx, sy):
            if mirror:
                sx = -sx
            if flip:
                sy = -sy
            return sx * c + sy * s, -sx * s + sy * c

        for num, (px, py, ang) in pins_of(sym).items():
            rx, ry = tf(px, -py)
            part[num] = (round(x + rx, 3), round(y + ry, 3))
            a = math.radians(ang)
            dx, dy = tf(-round(math.cos(a)), round(math.sin(a)))
            part.dirs[num] = (dx, dy)
        self.points += list(part.values())
        props = {p[1]: p for p in find_all(sym, "property")}
        is_power = ref.startswith("#")

        def prop(name, val, at, hide=False, justify=None):
            eff = [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]
            if justify:
                eff.append([Sym("justify"), Sym(justify)])
            p = [Sym("property"), name, val, [Sym("at"), at[0], at[1], rot % 180]]
            if hide:
                p.append([Sym("hide"), Y])
            p.append(eff)
            return p

        if ref_at is None:
            ref_at = (x + 2.54, y - 1.27, "left")
        if val_at is None:
            val_at = (x + 2.54, y + 1.27, "left")
        fp = footprint if footprint is not None else props["Footprint"][2]
        node = [Sym("symbol"), [Sym("lib_id"), lib_id], [Sym("at"), x, y, rot]]
        if mirror:
            node.append([Sym("mirror"), Sym("y")])
        if flip:
            node.append([Sym("mirror"), Sym("x")])
        node += [
            [Sym("unit"), 1],
            [Sym("exclude_from_sim"), N],
            [Sym("in_bom"), Y if in_bom else N],
            [Sym("on_board"), Y if on_board else N],
            [Sym("dnp"), N],
            [Sym("uuid"), uid("sym", ref)],
            prop("Reference", ref, ref_at[:2], hide=is_power or hide_ref, justify=ref_at[2]),
            prop("Value", value, val_at[:2], hide=hide_value, justify=val_at[2]),
            prop("Footprint", fp, (x, y), hide=True),
            prop("Datasheet", props["Datasheet"][2] if "Datasheet" in props else "~", (x, y), hide=True),
            prop("Description", props["Description"][2] if "Description" in props else "", (x, y), hide=True),
        ]
        for k, v in (fields or {}).items():
            node.append(prop(k, v, (x, y), hide=True))
        for num in pins_of(sym):
            node.append([Sym("pin"), num, [Sym("uuid"), uid("pin", ref, num)]])
        node.append([Sym("instances"), [Sym("project"), PROJECT,
                     [Sym("path"), f"/{ROOT_UUID}", [Sym("reference"), ref], [Sym("unit"), 1]]]])
        self.items.append(node)
        return part

    def power(self, kind, at, direction=(0, 1)):
        """Power symbol at `at`, its body pointing along `direction`."""
        if kind == "PWR_FLAG":
            self.flg += 1
            ref = f"#FLG0{self.flg:02d}"
        else:
            self.pwr += 1
            ref = f"#PWR0{self.pwr:02d}"
        down = kind == "GND"
        # Stock GND draws below its pin; the supply symbols and PWR_FLAG draw above.
        base = (0, 1) if down else (0, -1)
        rots = {0: base, 90: (base[1], -base[0]), 180: (-base[0], -base[1]), 270: (-base[1], base[0])}
        rot = next(r for r, d in rots.items() if d == tuple(direction))
        x, y = at
        dx, dy = direction
        val_at = (x + dx * 5.08, y + dy * 5.08, None)
        self.place(ref, f"power:{kind}", kind, x, y, rot, in_bom=False, on_board=True,
                   ref_at=(x, y, None), val_at=val_at, hide_value=(kind == "GND"))

    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            assert a[0] == b[0] or a[1] == b[1], (a, b)
            self.segments.append((a, b))

    def label(self, name, at, direction=(1, 0)):
        self.points.append(at)
        angle, justify = {(1, 0): (0, "left"), (-1, 0): (180, "right"),
                          (0, -1): (90, "left"), (0, 1): (270, "right")}[tuple(direction)]
        self.items.append([Sym("label"), name, [Sym("at"), at[0], at[1], angle],
                           [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]],
                            [Sym("justify"), Sym(justify), Sym("bottom")]],
                           [Sym("uuid"), uid("label", name, *at)]])

    def wlabel(self, name, at, direction=(1, 0)):
        """Name a wire with a label sitting on it."""
        angle, justify = {(1, 0): (0, "left"), (-1, 0): (180, "right"),
                          (0, -1): (90, "left"), (0, 1): (270, "right")}[tuple(direction)]
        self.items.append([Sym("label"), name, [Sym("at"), at[0], at[1], angle],
                           [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]],
                            [Sym("justify"), Sym(justify), Sym("bottom")]],
                           [Sym("uuid"), uid("wlabel", name, *at)]])

    def conn(self, part, pin, net, length=2.54, bend=True):
        """Stub off a pin, ending in a label or power symbol for `net`.

        Pins stacked on the same spot are joined by KiCad already, so only the
        first gets a stub. A power symbol on a sideways pin gets an L-shaped
        stub so the symbol stands upright.
        """
        x, y = part[pin]
        if (x, y) in part.done:
            return
        part.done.add((x, y))
        dx, dy = part.dirs[pin]
        end = (round(x + dx * length, 3), round(y + dy * length, 3))
        self.nets.setdefault(net, []).append(f"{part.ref}.{pin}")
        if net in POWER and dy == 0 and bend:
            vy = 1 if net == "GND" else -1
            corner = end
            end = (corner[0], round(corner[1] + vy * length, 3))
            self.wire((x, y), corner, end)
            self.power(POWER[net], end, (0, vy))
            return
        self.wire((x, y), end)
        if net in POWER:
            self.power(POWER[net], end, (dx, dy))
        else:
            self.label(net, end, (dx, dy))

    def connect(self, part, mapping, length=2.54):
        for pin, net in mapping.items():
            self.conn(part, pin, net, length)

    def flag(self, net, at, direction=(0, -1)):
        """PWR_FLAG on a net, for nets fed through passive pins."""
        x, y = at
        dx, dy = direction
        end = (x + dx * 5.08, y + dy * 5.08)
        self.wire(at, end)
        if net in POWER:
            self.power(POWER[net], at, (-dx, -dy))
        else:
            self.label(net, at, (-dx, -dy))
            self.nets.setdefault(net, []).append("PWR_FLAG")
        self.power("PWR_FLAG", end, (dx, dy))

    def no_connect(self, part, pin):
        x, y = part[pin]
        self.items.append([Sym("no_connect"), [Sym("at"), x, y], [Sym("uuid"), uid("nc", part.ref, pin)]])

    def text(self, body, at, size=1.27):
        self.items.append([Sym("text"), body, [Sym("exclude_from_sim"), N],
                           [Sym("at"), at[0], at[1], 0],
                           [Sym("effects"), [Sym("font"), [Sym("size"), size, size]],
                            [Sym("justify"), Sym("left"), Sym("top")]],
                           [Sym("uuid"), uid("text", body[:20], *at)]])

    def box(self, title, x0, y0, x1, y1):
        self.items.append([Sym("rectangle"), [Sym("start"), x0, y0], [Sym("end"), x1, y1],
                           [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("dash")]],
                           [Sym("fill"), [Sym("type"), Sym("none")]],
                           [Sym("uuid"), uid("box", title)]])
        self.text(title, (x0 + 1.27, y0 + 1.27), size=1.8)

    def junctions(self):
        count = {}
        for a, b in self.segments:
            for p in (a, b):
                count[p] = count.get(p, 0) + 1
        for p in self.points:
            if p in count:
                count[p] += 1
        for p in list(count) + self.points:
            for a, b in self.segments:
                if p in (a, b):
                    continue
                on = (a[0] == b[0] == p[0] and min(a[1], b[1]) < p[1] < max(a[1], b[1])) or (
                    a[1] == b[1] == p[1] and min(a[0], b[0]) < p[0] < max(a[0], b[0]))
                assert not on, f"point {p} lies inside wire {a}-{b}; split the wire there"
        return [p for p, n in count.items() if n >= 3]

    def check_labels(self):
        lonely = [n for n, uses in self.nets.items() if len(uses) < 2 and n not in POWER]
        assert not lonely, f"labels used once: {lonely}"

    def render(self, title_block):
        self.check_labels()
        out = [Sym("kicad_sch"), [Sym("version"), 20250114], [Sym("generator"), "eeschema"],
               [Sym("generator_version"), "9.0"], [Sym("uuid"), ROOT_UUID], [Sym("paper"), "A3"],
               title_block, [Sym("lib_symbols")] + list(self.lib.values())]
        for p in self.junctions():
            out.append([Sym("junction"), [Sym("at"), p[0], p[1]], [Sym("diameter"), 0],
                        [Sym("color"), 0, 0, 0, 0], [Sym("uuid"), uid("junction", *p)]])
        for a, b in self.segments:
            out.append([Sym("wire"), [Sym("pts"), [Sym("xy"), a[0], a[1]], [Sym("xy"), b[0], b[1]]],
                        [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]],
                        [Sym("uuid"), uid("wire", *a, *b)]])
        out += self.items
        out.append([Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]])
        out.append([Sym("embedded_fonts"), N])
        return dump(out) + "\n"


# -------------------------------------------------------------------- parts

R0603 = "Resistor_SMD:R_0603_1608Metric"
R1206 = "Resistor_SMD:R_1206_3216Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
SOT23 = "Package_TO_SOT_SMD:SOT-23"
TP = "TestPoint:TestPoint_Pad_D1.5mm"
BUTTON = "Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A"


def part(lcsc, mpn, mfr):
    return {"LCSC": lcsc, "MPN": mpn, "Manufacturer": mfr}


RES = {
    "10": ("C22859", "0603WAF100JT5E"),
    "220": ("C22962", "0603WAF2200T5E"),
    "330": ("C23138", "0603WAF3300T5E"),
    "680": ("C23228", "0603WAF6800T5E"),
    "1k": ("C21190", "0603WAF1001T5E"),
    "5.1k": ("C23186", "0603WAF5101T5E"),
    "10k": ("C25804", "0603WAF1002T5E"),
    "15k": ("C22809", "0603WAF1502T5E"),
    "47k": ("C25819", "0603WAF4702T5E"),
    "100k": ("C25803", "0603WAF1003T5E"),
    "470k": ("C23178", "0603WAF4703T5E"),
    "1M": ("C22935", "0603WAF1004T5E"),
}
CAP = {
    "10nF": ("C57112", "0603B103K500NT", "Fenghua"),
    "47nF": ("C1622", "CL10B473KB8NNNC", "Samsung Electro-Mechanics"),
    "100nF": ("C14663", "CC0603KRX7R9BB104", "YAGEO"),
    "1uF": ("C15849", "CL10A105KB8NNNC", "Samsung Electro-Mechanics"),
    "4.7uF": ("C19666", "CL10A475KO8NNNC", "Samsung Electro-Mechanics"),
    "10uF": ("C19702", "CL10A106KP8NNNC", "Samsung Electro-Mechanics"),
}


class Build:
    def __init__(self):
        self.s = Sheet()

    def R(self, ref, value, x, y, nets=None, rot=0, fp=R0603, fields=None):
        if fields is None:
            lcsc, mpn = RES[value]
            fields = part(lcsc, mpn, "UNI-ROYAL")
        horiz = rot in (90, 270)
        ref_at = (x, y - 2.54, None) if horiz else (x + 2.54, y - 1.27, "left")
        val_at = (x, y + 2.54, None) if horiz else (x + 2.54, y + 1.27, "left")
        p = self.s.place(ref, "Device:R", value, x, y, rot, footprint=fp, fields=fields,
                         ref_at=ref_at, val_at=val_at)
        self.s.connect(p, nets or {})
        return p

    def C(self, ref, value, x, y, nets=None, rot=0):
        lcsc, mpn, mfr = CAP[value]
        horiz = rot in (90, 270)
        ref_at = (x, y - 2.54, None) if horiz else (x + 2.54, y - 1.27, "left")
        val_at = (x, y + 2.54, None) if horiz else (x + 2.54, y + 1.27, "left")
        p = self.s.place(ref, "Device:C", value, x, y, rot, footprint=C0603,
                         fields=part(lcsc, mpn, mfr), ref_at=ref_at, val_at=val_at)
        self.s.connect(p, nets if nets is not None else {"2": "GND"})
        return p

    # -------------------------------------------------------------- power
    def power(self):
        s = self.s
        s.box("Power: USB-C charging, LiPo, 3.0 V rail", 12.7, 12.7, 157.48, 120.65)
        usb = s.place("J1", "Connector:USB_C_Receptacle_USB2.0_16P", "USB-C (charge only)",
                      30.48, 55.88, footprint="Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
                      fields=part("C165948", "TYPE-C-31-M-12", "Korean Hroparts Elec"),
                      ref_at=(22.86, 27.94, "left"), val_at=(22.86, 30.48, "left"))
        s.connect(usb, {"A1": "GND", "SH": "GND"})
        for pin in ("A6", "B6", "A7", "B7", "A8", "B8"):
            s.no_connect(usb, pin)
        # VBUS rail along the top of the block.
        yv = 30.48
        s.wire(usb["A4"], (53.34, 40.64), (53.34, yv))
        s.power("VBUS", (53.34, yv), (0, -1))
        s.wire((53.34, yv), (63.5, yv), (96.52, yv), (96.52, 35.56))
        c1 = self.C("C1", "4.7uF", 63.5, 36.83)
        s.wire((63.5, yv), c1["1"])

        # CC pull-downs: advertise a 5 V sink.
        r2 = self.R("R2", "5.1k", 53.34, 55.88, {"2": "GND"})
        r1 = self.R("R1", "5.1k", 60.96, 55.88, {"2": "GND"})
        s.wire(usb["B5"], (53.34, 48.26), r2["1"])
        s.wire(usb["A5"], (60.96, 45.72), r1["1"])
        s.wlabel("CC1", (46.99, 45.72))
        s.wlabel("CC2", (46.99, 48.26))

        u2 = s.place("U2", "Battery_Management:MCP73831-2-OT", "MCP73831T-2ACI/OT", 96.52, 43.18,
                     fields=part("C424093", "MCP73831T-2ACI/OT", "Microchip"),
                     ref_at=(99.06, 33.02, "left"), val_at=(83.82, 55.88, "left"))
        s.connect(u2, {"2": "GND"})
        r3 = self.R("R3", "10k", 81.28, 50.8, {"2": "GND"})
        s.wire(u2["5"], (81.28, 45.72), r3["1"])
        # VBAT out with its capacitor.
        s.wire(u2["3"], (114.3, 40.64))
        s.power("+BATT", (114.3, 40.64), (0, -1))
        c2 = self.C("C2", "4.7uF", 114.3, 45.72)
        s.wire((114.3, 40.64), c2["1"])
        # Charge LED: lit while STAT pulls low.
        r4 = self.R("R4", "1k", 124.46, 40.64, {"1": "VBUS"})
        d1 = s.place("D1", "Device:LED", "CHG red", 124.46, 50.8, 90, footprint="LED_SMD:LED_0603_1608Metric",
                     fields=part("C2286", "KT-0603R", "Hubei KENTO"),
                     ref_at=(127.0, 49.53, "left"), val_at=(127.0, 52.07, "left"))
        s.wire(r4["2"], d1["2"])
        s.wire(u2["1"], (109.22, 45.72), (109.22, 60.96), (124.46, 60.96), d1["1"])
        s.wlabel("CHG_STAT", (111.76, 60.96))
        s.text("R3 = 10k: 100 mA charge (1000 V / R3).\nSize it for 0.5-1 C of the cell.", (78.74, 60.96))

        # VBUS present -> PA7 (3.0 V at 5 V in).
        r5 = self.R("R5", "10k", 147.32, 68.58, {"1": "VBUS"})
        r6 = self.R("R6", "15k", 147.32, 76.2, {"2": "GND"})
        s.wire(r5["2"], (139.7, 72.39))
        s.label("VBUS_SENSE", (139.7, 72.39), (-1, 0))
        s.nets.setdefault("VBUS_SENSE", []).append("R5")

        # Battery, power switch, 3.0 V LDO.
        bat = s.place("J5", "Connector_Generic:Conn_01x02", "LiPo 3.7 V", 22.86, 95.25, mirror=True,
                      footprint="Connector_Wire:SolderWire-0.25sqmm_1x02_P4.5mm_D0.65mm_OD2mm",
                      in_bom=False, ref_at=(22.86, 91.44, None), val_at=(22.86, 102.87, None))
        s.connect(bat, {"1": "+BATT", "2": "GND"})
        sw = s.place("SW5", "Switch:SW_SPDT", "POWER", 60.96, 101.6,
                     footprint="Button_Switch_SMD:SW_SPDT_Shouhan_MSK12C02",
                     fields=part("C431540", "MSK12C02", "SHOU HAN"),
                     ref_at=(60.96, 95.25, None), val_at=(60.96, 107.95, None))
        s.connect(sw, {"2": "+BATT"})
        s.no_connect(sw, "3")
        u3 = s.place("U3", "Regulator_Linear:XC6206PxxxMR", "XC6206P302MR (3.0 V)", 93.98, 99.06,
                     fields=part("C9972", "XC6206P302MR-G", "Torex"),
                     ref_at=(99.06, 107.95, "left"), val_at=(99.06, 110.49, "left"))
        s.connect(u3, {"1": "GND"})
        c3 = self.C("C3", "1uF", 78.74, 104.14)
        s.wire(sw["1"], (71.12, 99.06), (78.74, 99.06), u3["3"])
        s.wire((78.74, 99.06), c3["1"])
        s.power("+VSW", (71.12, 99.06), (0, -1))
        c4 = self.C("C4", "10uF", 109.22, 104.14)
        s.wire(u3["2"], (109.22, 99.06), c4["1"])
        s.power("+3V0", (109.22, 99.06), (0, -1))
        # Switched battery -> PB1, halved.
        r7 = self.R("R7", "1M", 124.46, 99.06, {"1": "+VSW"})
        r8 = self.R("R8", "1M", 124.46, 106.68, {"2": "GND"})
        c5 = self.C("C5", "100nF", 132.08, 106.68)
        s.wire(r7["2"], (132.08, 102.87), c5["1"])
        s.wire((132.08, 102.87), (139.7, 102.87))
        s.label("VSW_SENSE", (139.7, 102.87), (1, 0))
        s.nets.setdefault("VSW_SENSE", []).append("R7")
        s.text("SW5 disconnects the battery; charging still works.", (48.26, 115.57))

        # Power flags for nets fed only through passive pins.
        s.power("PWR_FLAG", (96.52, 30.48), (0, -1))
        s.flag("+VSW", (53.34, 78.74), (0, 1))
        s.flag("GND", (40.64, 88.9), (0, -1))

    # ---------------------------------------------------------------- MCU
    def mcu(self):
        s = self.s
        s.box("MCU", 162.56, 12.7, 262.89, 152.4)
        u1 = s.place("U1", "MCU_ST_STM32L0:STM32L051K8Tx", "STM32L051K8T6", 213.36, 80.01,
                     fields=part("C915970", "STM32L051K8T6", "STMicroelectronics"),
                     ref_at=(217.17, 110.49, "left"), val_at=(217.17, 113.03, "left"))
        # VDD, VDD and VDDA tied together at the top.
        top = u1["17"][1] - 2.54
        s.wire(u1["1"], (u1["1"][0], top))
        s.wire(u1["17"], (u1["17"][0], top))
        s.wire(u1["5"], (u1["5"][0], top))
        s.wire((u1["1"][0], top), (u1["17"][0], top), (u1["5"][0], top))
        s.power("+3V0", (u1["17"][0], top), (0, -1))
        for pin in ("1", "5", "17"):
            u1.done.add(u1[pin])
        s.connect(u1, {
            "16": "GND", "4": "NRST", "31": "BOOT0",
            "6": "ADC_ELO", "7": "ADC_VMID", "8": "VMID_EN", "9": "OHM_HI", "10": "ADC_V",
            "11": "ADC_OHM", "12": "OHM_LO", "13": "VBUS_SENSE", "15": "VSW_SENSE",
            "18": "LED_B", "19": "UART_TX", "20": "UART_RX", "21": "BTN_A", "22": "BTN_B",
            "23": "SWDIO", "24": "SWCLK", "25": "LED_R",
            "26": "LED_G", "27": "PIEZO_A", "28": "PIEZO_B", "29": "I2C_SCL", "30": "I2C_SDA",
            "2": "BTN_C", "3": "OLED_EN",
        })
        s.no_connect(u1, "14")
        for i, (ref, val) in enumerate((("C6", "100nF"), ("C7", "100nF"), ("C8", "1uF"), ("C9", "10nF"))):
            self.C(ref, val, 177.8 + i * 12.7, 33.02, {"1": "+3V0", "2": "GND"})
        s.text("C6, C7 at VDD pins 1 and 17;\nC8, C9 at VDDA pin 5", (175.26, 44.45))
        self.R("R9", "10k", 177.8, 129.54, {"1": "BOOT0", "2": "GND"})
        self.C("C10", "100nF", 190.5, 129.54, {"1": "NRST", "2": "GND"})
        rst = s.place("SW4", "Switch:SW_Push", "RESET", 205.74, 129.54, 270, footprint=BUTTON,
                      fields=part("C318884", "TS-1187A-B-A-B", "XKB Connection"),
                      ref_at=(209.55, 128.27, "left"), val_at=(209.55, 130.81, "left"))
        s.connect(rst, {"1": "NRST", "2": "GND"})
        swd = s.place("J4", "Connector_Generic:Conn_01x05", "SWD", 246.38, 129.54,
                      footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
                      in_bom=False, ref_at=(246.38, 120.65, None), val_at=(246.38, 138.43, None))
        s.connect(swd, {"1": "+3V0", "2": "SWDIO", "3": "SWCLK", "4": "NRST", "5": "GND"})
        s.text("J4 unpopulated: hold a header in it\nor solder one for an ST-Link.\n"
               "1 3V0 2 SWDIO 3 SWCLK 4 NRST 5 GND", (220.98, 140.97))
        for ref, net, x in (("TP1", "UART_TX", 175.26), ("TP2", "UART_RX", 190.5)):
            tp = s.place(ref, "Connector:TestPoint", net, x, 144.78, footprint=TP, in_bom=False,
                         ref_at=(x + 2.54, 142.24, "left"), val_at=(x + 2.54, 139.7, "left"))
            s.connect(tp, {"1": net})

    # ------------------------------------------------------- volts input
    def volts(self):
        s = self.s
        s.box("Voltage input: 10 Mohm, +/-30 V", 267.97, 12.7, 408.94, 91.44)
        j = s.place("J2", "Connector_Generic:Conn_01x03", "PROBES", 281.94, 38.1, mirror=True,
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
                    in_bom=False, ref_at=(281.94, 30.48, None), val_at=(281.94, 45.72, None))
        s.conn(j, "1", "GND", bend=False)
        s.connect(j, {"3": "IN_OHM"})
        s.text("1 COM   2 V   3 OHM / continuity / diode", (274.32, 50.8))
        r47 = {"LCSC": "C37800", "MPN": "1206W4F4704T5E", "Manufacturer": "UNI-ROYAL"}
        y = 38.1
        r10 = self.R("R10", "4.7M", 298.45, y, rot=90, fp=R1206, fields=r47)
        r11 = self.R("R11", "4.7M", 317.5, y, rot=90, fp=R1206, fields=r47)
        s.wire(j["2"], r10["1"])
        s.wire(r10["2"], r11["1"])
        s.wlabel("IN_V", (288.29, y))
        s.wlabel("V_MID1", (303.53, y))
        c11 = self.C("C11", "47nF", 325.12, 45.72)
        r12 = self.R("R12", "470k", 335.28, 45.72)
        s.wire(r11["2"], (325.12, y), (335.28, y), (345.44, y))
        s.label("ADC_V", (345.44, y), (1, 0))
        s.nets.setdefault("ADC_V", []).append("R11")
        s.wire((325.12, y), c11["1"])
        s.wire((335.28, y), r12["1"])
        # Mid-rail bias, on only while measuring.
        r13 = self.R("R13", "10k", 360.68, 45.72, {"1": "VMID_EN"})
        r14 = self.R("R14", "10k", 360.68, 60.96, {"2": "GND"})
        c12 = self.C("C12", "1uF", 370.84, 60.96)
        r15 = self.R("R15", "1k", 383.54, 55.88, rot=90, nets={"2": "ADC_VMID"})
        ym = 55.88
        s.wire(r12["2"], (335.28, ym), (360.68, ym))
        s.wire(r13["2"], (360.68, ym), r14["1"])
        s.wire((360.68, ym), (370.84, ym), r15["1"])
        s.wire((370.84, ym), c12["1"])
        s.wlabel("VMID", (340.36, ym))
        s.text("ADC_V = VMID + (VIN - VMID) x R12 / (R10 + R11 + R12)\n"
               "VIN = ADC_V + (ADC_V - VMID) x 20.0; VMID is measured on ADC_VMID.\n"
               "VMID_EN high only while measuring: VMID = 1.5 V, range about +/-31 V.\n"
               "Overload current is set by R10 + R11 (3 uA at 30 V) into the PA4 clamp.",
               (274.32, 71.12))

    # ------------------------------------------------------- ohms input
    def ohms(self):
        s = self.s
        s.box("Resistance / continuity / diode input, protected to +/-30 V", 267.97, 96.52, 408.94, 200.66)
        r10k = {"LCSC": "C136889", "MPN": "RT1206BRD0710KL", "Manufacturer": "YAGEO"}
        bus = 358.14
        rows = {}
        for key, y, rd, rr, val, fp, fields, drive, dref in (
                ("LO", 127.0, "R16", "R17", "10k 0.1%", R1206, r10k, "OHM_LO", "D2"),
                ("HI", 157.48, "R19", "R20", "1M", R0603, None, "OHM_HI", "D3")):
            node = (320.04, y)
            a = self.R(rd, "1k", 299.72, y, rot=90, nets={"1": drive})
            b = self.R(rr, val, 337.82, y, rot=90, fp=fp, fields=fields)
            s.wire(a["2"], node, b["1"])
            s.wire(b["2"], (bus, y))
            d = s.place(dref, "Diode:BAV99", "BAV199", 320.04, y + 12.7, 180,
                        fields=part("C40919", "BAV199,215", "Nexperia"),
                        ref_at=(320.04, y + 17.78, None), val_at=(320.04, y + 20.32, None))
            s.connect(d, {"2": "+BATT", "1": "GND"})
            s.wire(node, d["3"])
            rows[key] = node
        # E_LO is also measured, through R18.
        r18 = self.R("R18", "10k", 320.04, 114.3)
        s.wire(r18["2"], rows["LO"])
        s.wire(r18["1"], (320.04, 107.95), (325.12, 107.95))
        s.label("ADC_ELO", (325.12, 107.95), (1, 0))
        s.nets.setdefault("ADC_ELO", []).append("R18")
        # The input itself, sensed through R21 into a hold capacitor.
        s.wire((bus + 5.08, 116.84), (bus, 116.84), (bus, 127.0), (bus, 142.24), (bus, 157.48))
        s.label("IN_OHM", (bus + 5.08, 116.84), (1, 0))
        s.nets.setdefault("IN_OHM", []).append("bus")
        r21 = self.R("R21", "47k", 370.84, 142.24, rot=90)
        c13 = self.C("C13", "100nF", 381.0, 147.32)
        s.wire((bus, 142.24), r21["1"])
        s.wire(r21["2"], (381.0, 142.24), (391.16, 142.24))
        s.wire((381.0, 142.24), c13["1"])
        s.label("ADC_OHM", (391.16, 142.24), (1, 0))
        s.nets.setdefault("ADC_OHM", []).append("R21")
        s.wlabel("E_LO", (322.58, 127.0))
        s.wlabel("E_HI", (322.58, 157.48))
        s.text("Low range (0-100k, continuity, diode): drive OHM_LO high,\n"
               "  RX = R17 x ADC_OHM / (ADC_ELO - ADC_OHM).\n"
               "High range (100k-10M): drive OHM_HI high,\n"
               "  RX = R20 x ADC_OHM / (VDD - ADC_OHM).\n"
               "The unused drive pin stays analog (high-Z). D2/D3 clamp E_LO/E_HI\n"
               "to GND and the battery, so 30 V on the input pushes 2.5 mA\n"
               "through R17 into the cell, never into the 3.0 V rail.",
               (274.32, 182.88))

    # ----------------------------------------------------------------- UI
    def ui(self):
        s = self.s
        s.box("Display, LED, piezo, buttons", 12.7, 125.73, 157.48, 256.54)
        # OLED module supply switch (Q1 high side, Q2 level shift).
        q1 = s.place("Q1", "Transistor_FET:AO3401A", "AO3401A", 40.64, 147.32, flip=True,
                     fields=part("C15127", "AO3401A", "Alpha & Omega"),
                     ref_at=(46.99, 146.05, "left"), val_at=(46.99, 148.59, "left"))
        rail = 137.16
        r22 = self.R("R22", "100k", 30.48, 142.24)
        s.wire((30.48, rail), (36.83, rail), (43.18, rail), q1["2"])
        s.wire((30.48, rail), r22["1"])
        s.power("+VSW", (36.83, rail), (0, -1))
        gate = (30.48, 147.32)
        s.wire(r22["2"], gate, q1["1"])
        q2 = s.place("Q2", "Transistor_FET:2N7002", "2N7002", 27.94, 160.02,
                     fields=part("C8545", "2N7002", "Jiangsu Changjing"),
                     ref_at=(33.02, 161.29, "left"), val_at=(33.02, 163.83, "left"))
        s.wire(gate, q2["3"])
        s.connect(q2, {"2": "GND"})
        r23 = self.R("R23", "100k", 20.32, 166.37, {"2": "GND"})
        s.wire(q2["1"], (20.32, 160.02), r23["1"])
        s.wire((20.32, 160.02), (20.32, 154.94))
        s.label("OLED_EN", (20.32, 154.94), (0, -1))
        s.nets.setdefault("OLED_EN", []).append("Q2")
        oled = s.place("J3", "Connector_Generic:Conn_01x04", "OLED 0.91in 128x32 I2C", 86.36, 152.4,
                       footprint="wallet_dmm:OLED_0.91in_128x32_Module", in_bom=False,
                       ref_at=(86.36, 144.78, None), val_at=(86.36, 162.56, None))
        c14 = self.C("C14", "1uF", 53.34, 157.48)
        s.wire(q1["3"], (43.18, 152.4), (53.34, 152.4), oled["2"])
        s.wire((53.34, 152.4), c14["1"])
        s.conn(oled, "1", "GND", bend=False)
        s.connect(oled, {"3": "I2C_SCL", "4": "I2C_SDA"})
        s.wlabel("OLED_VCC", (58.42, 152.4))
        s.wlabel("OLED_G", (30.48, 151.13), (-1, 0))
        s.text("SSD1306 module, pins GND VCC SCL SDA. Its own pull-ups\n"
               "hold SDA/SCL; Q1 cuts its supply when the meter is off.", (96.52, 147.32))

        # RGB LED, common anode on the battery so green and blue get enough headroom.
        led = s.place("D4", "Device:LED_ARGB", "RGB", 116.84, 180.34,
                      footprint="wallet_dmm:LED_RGB_1615_CA",
                      fields=part("C601683", "TJ-S1615CY6TGLCCYRGB-A5", "TOGIALED"),
                      ref_at=(116.84, 171.45, None), val_at=(116.84, 189.23, None))
        s.connect(led, {"1": "+VSW"})
        for ref, val, net, pin, y, jog in (("R24", "680", "LED_R", "2", 170.18, 175.26),
                                           ("R25", "1k", "LED_G", "3", 180.34, None),
                                           ("R26", "330", "LED_B", "4", 190.5, 185.42)):
            r = self.R(ref, val, 93.98, y, rot=90, nets={"1": net})
            if jog is None:
                s.wire(r["2"], led[pin])
            else:
                s.wire(r["2"], (104.14, y), (104.14, jog), led[pin])

        # Piezo driven push-pull from TIM22 CH1/CH2 (complementary): 6 Vp-p from 3 V.
        bz = s.place("BZ1", "Device:Buzzer", "PKLCS1212E4001", 63.5, 213.36,
                     footprint="Buzzer_Beeper:Buzzer_Murata_PKLCS1212E",
                     fields=part("C113159", "PKLCS1212E4001-R1", "Murata"),
                     ref_at=(68.58, 212.09, "left"), val_at=(68.58, 214.63, "left"))
        r27 = self.R("R27", "220", 45.72, 210.82, rot=90, nets={"1": "PIEZO_A"})
        r28 = self.R("R28", "220", 45.72, 220.98, rot=90, nets={"1": "PIEZO_B"})
        s.wire(r27["2"], bz["1"])
        s.wire(r28["2"], (55.88, 220.98), (55.88, 215.9), bz["2"])

        for i, (ref, net) in enumerate((("SW1", "BTN_A"), ("SW2", "BTN_B"), ("SW3", "BTN_C"))):
            x = 96.52 + i * 20.32
            b = s.place(ref, "Switch:SW_Push", net.replace("BTN_", "BUTTON "), x, 220.98, 270,
                        footprint=BUTTON, fields=part("C318884", "TS-1187A-B-A-B", "XKB Connection"),
                        ref_at=(x + 3.81, 219.71, "left"), val_at=(x + 3.81, 222.25, "left"))
            s.connect(b, {"1": net, "2": "GND"})
        s.text("Buttons use the MCU pull-ups and wake it from Stop mode.", (91.44, 234.95))

        for ref, net, x in (("TP3", "+BATT", 25.4), ("TP4", "+3V0", 38.1), ("TP5", "GND", 50.8)):
            tp = s.place(ref, "Connector:TestPoint", net.lstrip("+"), x, 246.38, footprint=TP,
                         in_bom=False, ref_at=(x + 2.54, 243.84, "left"), val_at=(x + 2.54, 241.3, "left"))
            s.connect(tp, {"1": net})

    def build(self):
        self.power()
        self.mcu()
        self.volts()
        self.ohms()
        self.ui()
        self.s.text(
            "Rev 3. Not for mains: inputs are rated 30 V DC max.\n"
            "The 3.0 V rail stays on (1 uA LDO, MCU in Stop);\n"
            "SW5 disconnects the battery for storage.",
            (167.64, 160.02), size=1.6)
        tb = [Sym("title_block"), [Sym("title"), "Wallet DMM"],
              [Sym("date"), "2026-09-30"], [Sym("rev"), "3"],
              [Sym("comment"), 1, "STM32L051, LiPo with USB-C charging, 0.91in OLED"],
              [Sym("comment"), 2, "Card size 85.6 x 54 mm, 2 layers, 0.8 mm FR4"]]
        return self.s.render(tb)


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(Build().build())
    print(f"wrote {OUT}")
