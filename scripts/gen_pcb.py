#!/usr/bin/env python3
"""Generates hardware/wallet_dmm.kicad_pcb from the schematic's netlist.

Placement, outline, pours and silkscreen are laid out below; the netlist
supplies footprints, values, fields and nets, so the board stays in sync with
the schematic (DRC's schematic-parity check confirms it). Tracks and vias come
from hardware/routes.json, which scripts/autoroute.py writes.
"""

import json
import math
import os
import sys
import uuid
from pathlib import Path

import pcbnew
from pcbnew import FromMM, VECTOR2I

from sexpr import find, find_all, parse

ROOT = Path(__file__).resolve().parent.parent
HW = ROOT / "hardware"
PCB = HW / "wallet_dmm.kicad_pcb"
ROUTES = HW / "routes.json"
FPDIR = Path(os.environ["KICAD10_FOOTPRINT_DIR"])
LOCAL_LIB = HW / "wallet_dmm.pretty"

OX, OY = 100.0, 100.0  # placement origin on the page
W, H = 85.6, 53.98     # ISO/IEC 7810 ID-1 card
CORNER = 3.18
THICKNESS = 0.8

# ref: (x, y, rotation) in board coordinates (mm from the top-left corner, y down)
PLACE = {
    # Display, LED and buttons.
    "J3": (7.0, 4.0, 0),
    "Q1": (3.6, 17.6, 90),
    "Q2": (8.6, 17.6, 90),
    "R22": (3.2, 21.2, 0),
    "R23": (12.0, 17.6, 90),
    "C14": (6.2, 15.2, 0),
    "D4": (47.8, 5.2, 0),
    "R24": (45.0, 11.6, 90),
    "R25": (46.6, 11.6, 90),
    "R26": (48.2, 11.6, 90),
    "SW1": (8.5, 24.5, 0),
    "SW2": (19.5, 24.5, 0),
    "SW3": (30.5, 24.5, 0),
    "BZ1": (9.0, 43.5, 90),
    "R27": (19.0, 34.6, 0),
    "R28": (19.0, 36.2, 0),
    # MCU and its support.
    "U1": (44.0, 29.0, 90),
    "C6": (40.6, 36.4, 90),
    "C7": (48.0, 21.8, 0),
    "C8": (44.6, 36.4, 90),
    "C9": (46.2, 36.4, 90),
    "R9": (35.4, 31.0, 0),
    "C10": (42.6, 36.4, 90),
    "SW4": (72.0, 5.5, 0),
    "J4": (51.4, 3.2, 90),
    "TP1": (40.4, 20.4, 0),
    "TP2": (37.6, 20.4, 0),
    # Voltage input.
    "J2": (82.4, 26.5, 0),
    "R10": (75.8, 21.0, 0),
    "R11": (69.0, 21.0, 0),
    "R12": (58.4, 24.8, 90),
    "C11": (56.8, 24.8, 90),
    "R13": (52.0, 38.8, 90),
    "R14": (53.6, 38.8, 90),
    "C12": (55.2, 38.8, 90),
    "R15": (50.4, 38.8, 90),
    # Resistance input.
    "R16": (60.2, 30.4, 0),
    "R17": (70.2, 33.2, 0),
    "R18": (60.2, 35.2, 0),
    "D2": (65.2, 37.6, 0),
    "R19": (60.2, 42.6, 0),
    "R20": (70.2, 42.6, 0),
    "D3": (65.2, 46.6, 0),
    "R21": (70.2, 29.6, 0),
    "C13": (64.6, 27.2, 0),
    # Power.
    "J1": (27.0, 50.0, 0),
    "R1": (23.6, 42.8, 90),
    "R2": (30.6, 42.8, 90),
    "U2": (35.4, 44.6, 90),
    "C1": (22.0, 42.8, 90),
    "C2": (38.6, 45.8, 90),
    "R3": (33.0, 40.6, 0),
    "D1": (16.6, 49.2, 90),
    "R4": (18.2, 49.2, 90),
    "R5": (32.4, 37.4, 0),
    "R6": (32.4, 35.8, 0),
    "J5": (39.0, 50.8, 0),
    "SW5": (57.0, 52.0, 0),
    "U3": (47.0, 44.8, 180),
    "C3": (50.6, 45.2, 90),
    "C4": (43.8, 42.2, 0),
    "R7": (54.8, 45.6, 90),
    "R8": (56.4, 45.6, 90),
    "C5": (58.0, 45.6, 90),
    "TP3": (66.0, 51.2, 0),
    "TP4": (69.4, 51.2, 0),
    "TP5": (72.8, 51.2, 0),
}


def P(x, y):
    return VECTOR2I(FromMM(OX + x), FromMM(OY + y))


def mm(v):
    return round(pcbnew.ToMM(v), 4)


class Board:
    def __init__(self, netlist):
        self.b = pcbnew.CreateEmptyBoard()
        self.nets = {}
        self.pads = {}
        self.parse_netlist(netlist)

    def net(self, name):
        if name not in self.nets:
            n = pcbnew.NETINFO_ITEM(self.b, name)
            self.b.Add(n)
            self.nets[name] = n
        return self.nets[name]

    def parse_netlist(self, path):
        nl = parse(Path(path).read_text())
        comps = {}
        for c in find_all(find(nl, "components"), "comp"):
            ref = find(c, "ref")[1]
            fields = {f[1][1]: f[2] for f in find_all(find(c, "fields") or [], "field") if len(f) > 2}
            comps[ref] = {
                "value": find(c, "value")[1],
                "footprint": find(c, "footprint")[1],
                "uuid": find(c, "tstamps")[1],
                "fields": fields,
                "no_bom": any(find(p, "name")[1] == "exclude_from_bom"
                              for p in find_all(c, "property")),
            }
        self.comps = comps
        pad_net = {}
        for n in find_all(find(nl, "nets"), "net"):
            name = find(n, "name")[1]
            for node in find_all(n, "node"):
                pad_net[(find(node, "ref")[1], find(node, "pin")[1])] = name
        self.pad_net = pad_net

    def place_all(self):
        missing = set(self.comps) - set(PLACE)
        assert not missing, f"no placement for {sorted(missing)}"
        for ref, c in self.comps.items():
            lib, name = c["footprint"].split(":")
            libdir = LOCAL_LIB if lib == "wallet_dmm" else FPDIR / f"{lib}.pretty"
            fp = pcbnew.FootprintLoad(str(libdir), name)
            fp.SetFPID(pcbnew.LIB_ID(lib, name))
            fp.SetReference(ref)
            fp.SetValue(c["value"])
            if c["no_bom"]:
                fp.SetExcludedFromBOM(True)
            fp.SetPath(pcbnew.KIID_PATH(f"/{c['uuid']}"))
            for k, v in c["fields"].items():
                if k == "Footprint":
                    continue
                fp.SetField(k, v)
                fp.GetField(k).SetVisible(False)
            # References live on the fab layer (assembly drawing), not silkscreen.
            fp.Reference().SetVisible(False)
            fp.Value().SetVisible(False)
            x, y, rot = PLACE[ref]
            fp.SetPosition(P(x, y))
            fp.SetOrientationDegrees(rot)
            self.b.Add(fp)
            for pad in fp.Pads():
                if ref == "J1":
                    pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
                num = pad.GetNumber()
                if (ref, num) in self.pad_net:
                    pad.SetNet(self.net(self.pad_net[(ref, num)]))
                self.pads[(ref, num)] = pad

    def pad_xy(self, ref, num):
        p = self.pads[(ref, num)].GetPosition()
        return (mm(p.x) - OX, mm(p.y) - OY)

    def track(self, net, width, *pts, layer=pcbnew.F_Cu):
        for a, b in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(self.b)
            t.SetStart(P(*a))
            t.SetEnd(P(*b))
            t.SetWidth(FromMM(width))
            t.SetLayer(layer)
            t.SetNet(self.net(net))
            self.b.Add(t)

    def via(self, net, x, y, dia=0.6, drill=0.3):
        v = pcbnew.PCB_VIA(self.b)
        v.SetPosition(P(x, y))
        v.SetWidth(FromMM(dia))
        v.SetDrill(FromMM(drill))
        v.SetNet(self.net(net))
        self.b.Add(v)

    def zone(self, net, layer, pts, priority=0, clearance=0.25, name=None):
        z = pcbnew.ZONE(self.b)
        z.SetLayer(layer)
        z.SetNet(self.net(net))
        ol = z.Outline()
        ol.NewOutline()
        for x, y in pts:
            ol.Append(FromMM(OX + x), FromMM(OY + y))
        z.SetAssignedPriority(priority)
        z.SetLocalClearance(FromMM(clearance))
        z.SetMinThickness(FromMM(0.2))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
        z.SetThermalReliefGap(FromMM(0.3))
        z.SetThermalReliefSpokeWidth(FromMM(0.4))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        if name:
            z.SetZoneName(name)
        self.b.Add(z)
        return z

    def keepout(self, layers, pts, name):
        z = pcbnew.ZONE(self.b)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(False)
        z.SetDoNotAllowVias(False)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
        z.SetDoNotAllowZoneFills(True)
        ls = pcbnew.LSET()
        for layer in layers:
            ls.AddLayer(layer)
        z.SetLayerSet(ls)
        ol = z.Outline()
        ol.NewOutline()
        for x, y in pts:
            ol.Append(FromMM(OX + x), FromMM(OY + y))
        z.SetZoneName(name)
        self.b.Add(z)

    def shape(self, kind, layer, width, *geom):
        s = pcbnew.PCB_SHAPE(self.b, kind)
        if kind == pcbnew.SHAPE_T_ARC:
            s.SetArcGeometry(P(*geom[0]), P(*geom[1]), P(*geom[2]))
        elif kind == pcbnew.SHAPE_T_RECT:
            s.SetStart(P(*geom[0]))
            s.SetEnd(P(*geom[1]))
        else:
            s.SetStart(P(*geom[0]))
            s.SetEnd(P(*geom[1]))
        s.SetLayer(layer)
        s.SetWidth(FromMM(width))
        self.b.Add(s)

    def outline(self, r=CORNER):
        seg = lambda a, b: self.shape(pcbnew.SHAPE_T_SEGMENT, pcbnew.Edge_Cuts, 0.1, a, b)
        arc = lambda a, m, b: self.shape(pcbnew.SHAPE_T_ARC, pcbnew.Edge_Cuts, 0.1, a, m, b)
        k = r * (1 - math.sqrt(0.5))
        seg((r, 0), (W - r, 0))
        seg((W, r), (W, H - r))
        seg((W - r, H), (r, H))
        seg((0, H - r), (0, r))
        arc((W - r, 0), (W - k, k), (W, r))
        arc((W, H - r), (W - k, H - k), (W - r, H))
        arc((r, H), (k, H - k), (0, H - r))
        arc((0, r), (k, k), (r, 0))

    def text(self, s, x, y, size=1.0, layer=pcbnew.F_SilkS, bold=False, justify=0, angle=0):
        t = pcbnew.PCB_TEXT(self.b)
        t.SetText(s)
        t.SetPosition(P(x, y))
        t.SetLayer(layer)
        t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
        # JLCPCB prints silkscreen strokes down to about 0.15 mm.
        t.SetTextThickness(FromMM(max(0.15, size * (0.2 if bold else 0.15))))
        t.SetBold(bold)
        if angle:
            t.SetTextAngleDegrees(angle)
        if justify:
            t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT if justify < 0 else pcbnew.GR_TEXT_H_ALIGN_RIGHT)
        if layer in (pcbnew.B_SilkS, pcbnew.B_Fab):
            t.SetMirrored(True)
        self.b.Add(t)


# Region on the back where the LiPo pouch is taped. No through-hole pins there.
BATTERY = (49.0, 7.0, 80.0, 48.5)


def stitch(b, pitch=5.0, r=0.3):
    """GND vias wherever a via fits inside both filled ground pours.

    The fills already keep every other net's clearance, so a via that sits
    inside both of them (shrunk by the via radius) is clear on both layers.
    A regular grid ties the pours together; then any pour fragment still
    without a via or ground pin gets one of its own.
    """
    zones = {z.GetZoneName(): z for z in b.Zones()}
    fills = {}
    for name, layer in (("GND_TOP", pcbnew.F_Cu), ("GND_BOTTOM", pcbnew.B_Cu)):
        full = pcbnew.SHAPE_POLY_SET(zones[name].GetFilledPolysList(layer))
        inner = pcbnew.SHAPE_POLY_SET(full)
        inner.Deflate(FromMM(r + 0.05), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, FromMM(0.01))
        fills[layer] = (full, inner)
    pads = []
    anchors = []  # ground points that already tie a fragment to the other layer
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            bb = pad.GetBoundingBox()
            pads.append((bb.GetLeft() - FromMM(0.3), bb.GetTop() - FromMM(0.3),
                         bb.GetRight() + FromMM(0.3), bb.GetBottom() + FromMM(0.3)))
            if pad.GetNetname() == "GND" and pad.HasHole():
                anchors.append(pad.GetPosition())
    holes = []
    for t in b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            holes.append(t.GetPosition())
            if t.GetNetname() == "GND":
                anchors.append(t.GetPosition())

    def fits(p):
        if not all(fills[layer][1].Contains(p) for layer in fills):
            return False
        if any(x0 < p.x < x1 and y0 < p.y < y1 for x0, y0, x1, y1 in pads):
            return False
        return all((p - h).EuclideanNorm() >= FromMM(1.0) for h in holes)

    def add(p):
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(p)
        v.SetWidth(FromMM(2 * r))
        v.SetDrill(FromMM(0.3))
        v.SetNet(b.FindNet("GND"))
        b.Add(v)
        holes.append(p)
        anchors.append(p)

    added = 0
    y = 1.0
    while y < H:
        x = 1.0 + (pitch / 2 if int(y / pitch) % 2 else 0)
        while x < W:
            p = P(x, y)
            if fits(p):
                add(p)
                added += 1
            x += pitch
        y += pitch

    for layer in fills:
        full = fills[layer][0]
        for i in range(full.OutlineCount()):
            chain = full.Outline(i)
            if any(chain.PointInside(a) for a in anchors):
                continue
            bb = chain.BBox()
            step = FromMM(0.2)
            done = False
            yy = bb.GetTop()
            while yy < bb.GetBottom() and not done:
                xx = bb.GetLeft()
                while xx < bb.GetRight():
                    p = VECTOR2I(xx, yy)
                    if chain.PointInside(p) and fits(p):
                        add(p)
                        added += 1
                        done = True
                        break
                    xx += step
                yy += step
    return added


def pours(bd):
    m = 0.3
    board = [(m, m), (W - m, m), (W - m, H - m), (m, H - m)]
    # 0.5 mm matches the probe nets' clearance, so a fill made here matches
    # the one DRC makes with the project's net classes.
    bd.zone("GND", pcbnew.F_Cu, board, clearance=0.5, name="GND_TOP")
    bd.zone("GND", pcbnew.B_Cu, board, clearance=0.5, name="GND_BOTTOM")


# Ground pins hemmed in by their neighbours get a fixed via before routing.
FANOUT = [("U2", "2", (35.4, 47.6))]


def fanouts(bd):
    for ref, num, (x, y) in FANOUT:
        px, py = bd.pad_xy(ref, num)
        bd.track("GND", 0.3, (px, py), (x, y))
        bd.via("GND", x, y)
    for t in bd.b.GetTracks():
        if t.GetNetname() == "GND":
            t.SetLocked(True)


def load_routes(bd):
    if not ROUTES.exists():
        return 0
    data = json.loads(ROUTES.read_text())
    layers = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}
    for t in data["tracks"]:
        bd.track(t["net"], t["width"], tuple(t["start"]), tuple(t["end"]), layer=layers[t["layer"]])
    for v in data["vias"]:
        bd.via(v["net"], v["at"][0], v["at"][1], v["dia"], v["drill"] if v["drill"] > 0 else 0.3)
    return len(data["tracks"])


def silk(bd):
    F, B = pcbnew.F_SilkS, pcbnew.B_SilkS
    bd.text("WALLET DMM", 57.0, 11.6, 1.4, bold=True, justify=-1)
    bd.text("REV 3", 57.0, 13.9, 0.9, justify=-1)
    bd.text("30 V DC MAX", 57.0, 15.9, 0.9, bold=True, justify=-1)
    bd.text("NOT FOR MAINS", 57.0, 17.6, 0.9, justify=-1)
    # Probe inputs.
    for label, dy in (("COM", 0.0), ("V", 2.54), ("Ω ▶))", 5.08)):
        bd.text(label, 80.6, 26.5 + dy, 1.0, bold=True, justify=1)
    # Buttons, switch, USB, debug.
    for label, x in (("A", 8.5), ("B", 19.5), ("C", 30.5)):
        bd.text(label, x, 28.6, 1.0, bold=True)
    bd.text("RESET", 72.0, 9.4, 0.8)
    bd.text("SWD", 49.0, 3.2, 0.8, justify=1)
    bd.text("3V0 DIO CLK RST GND", 56.5, 5.4, 0.8)
    bd.text("TX", 40.4, 18.6, 0.8)
    bd.text("RX", 37.6, 18.6, 0.8)
    bd.text("CHG", 16.6, 46.6, 0.8)
    bd.text("ON   OFF", 57.0, 48.4, 0.8)
    bd.text("BAT  3V0  GND", 69.4, 49.2, 0.8)
    # Back: battery outline and wiring note.
    x0, y0, x1, y1 = BATTERY
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        bd.shape(pcbnew.SHAPE_T_SEGMENT, B, 0.15, a, b)
    bd.text("LiPo 3.7 V, protected", (x0 + x1) / 2, y0 + 3.0, 1.0, layer=B)
    bd.text("max 30 x 40 x 4 mm", (x0 + x1) / 2, y0 + 5.0, 1.0, layer=B)
    bd.text("tape here, wires to BAT+ / BAT-", (x0 + x1) / 2, y0 + 7.0, 0.8, layer=B)
    bd.text("BAT-  BAT+", 41.25, 48.6, 0.8, layer=B)  # read from the back
    bd.text("BAT+  BAT-", 41.25, 48.6, 0.8, layer=F)


# JLCPCB 2-layer capabilities, with some margin.
RULES = {
    "min_clearance": 0.127,
    "min_connection": 0.0,
    "min_copper_edge_clearance": 0.3,
    "min_hole_clearance": 0.25,
    "min_hole_to_hole": 0.5,
    "min_resolved_spokes": 2,
    "min_silk_clearance": 0.0,
    "min_text_height": 0.6,
    "min_text_thickness": 0.1,
    "min_through_hole_diameter": 0.3,
    "min_track_width": 0.127,
    "min_via_annular_width": 0.13,
    "min_via_diameter": 0.56,
}
NETCLASS = {"clearance": 0.2, "track_width": 0.25, "via_diameter": 0.6, "via_drill": 0.3}
POWER_NETS = ["GND", "+BATT", "+VSW", "VBUS", "+3V0", "/OLED_VCC"]
POWER_CLASS = {"name": "Power", "clearance": 0.2, "track_width": 0.4, "via_diameter": 0.6,
               "via_drill": 0.3}
# The 10 Mohm divider and the clamped ohms nodes see up to 30 V: keep them apart.
HV_NETS = ["/IN_V", "/V_MID1", "/IN_OHM"]
HV_CLASS = {"name": "Probe", "clearance": 0.5, "track_width": 0.3, "via_diameter": 0.6,
            "via_drill": 0.3}


def write_project():
    """Saving the board rewrites the project file with defaults; restore our rules."""
    path = PCB.with_suffix(".kicad_pro")
    pro = json.loads(path.read_text())
    ds = pro.setdefault("board", {}).setdefault("design_settings", {})
    ds.setdefault("rules", {}).update(RULES)
    ds["track_widths"] = [0.0, 0.2, 0.25, 0.3, 0.4, 0.6]
    ds["via_dimensions"] = [{"diameter": 0.0, "drill": 0.0}, {"diameter": 0.6, "drill": 0.3},
                            {"diameter": 0.7, "drill": 0.35}]
    d = ds.setdefault("defaults", {})
    d.update({"silk_text_size_h": 1.0, "silk_text_size_v": 1.0, "silk_text_thickness": 0.15,
              "silk_line_width": 0.15})
    classes = pro["net_settings"]["classes"]
    default = next(nc for nc in classes if nc["name"] == "Default")
    default.update(NETCLASS)
    classes[:] = [default]
    for extra in (POWER_CLASS, HV_CLASS):
        nc = dict(default)
        nc.update(extra)
        classes.append(nc)
    pro["net_settings"]["netclass_patterns"] = (
        [{"netclass": "Power", "pattern": n} for n in POWER_NETS]
        + [{"netclass": "Probe", "pattern": n} for n in HV_NETS])
    path.write_text(json.dumps(pro, indent=2) + "\n")


def build(netlist, routes=True):
    bd = Board(netlist)
    bd.place_all()
    bd.outline()
    silk(bd)
    if routes and ROUTES.exists():
        n = load_routes(bd)
    else:
        n = 0
        fanouts(bd)
    pours(bd)
    ds = bd.b.GetDesignSettings()
    ds.SetBoardThickness(FromMM(THICKNESS))
    ds.SetAuxOrigin(P(0, H))
    ds.SetGridOrigin(P(0, H))
    return bd, n


def stable_uuids(b):
    """Derive every item's UUID from what it is and where it sits, so rebuilding
    an unchanged design writes an identical file."""
    ns = uuid.UUID("4c1d3e0a-9b7e-4f55-8f2a-6a3b9d7e5c21")
    seen = {}

    def put(item, key):
        seen[key] = seen.get(key, 0) + 1
        item.SetUuid(pcbnew.KIID(str(uuid.uuid5(ns, f"{key}#{seen[key]}"))))

    def xy(p):
        return f"{p.x},{p.y}"

    for fp in b.GetFootprints():
        ref = fp.GetReference()
        put(fp, ref)
        for i, field in enumerate(fp.GetFields()):
            put(field, f"{ref}/field/{i}")
        for i, pad in enumerate(fp.Pads()):
            put(pad, f"{ref}/pad/{i}")
        for i, g in enumerate(fp.GraphicalItems()):
            put(g, f"{ref}/gfx/{i}")
        for i, z in enumerate(fp.Zones()):
            put(z, f"{ref}/zone/{i}")
    for t in b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            put(t, f"via/{t.GetNetname()}/{xy(t.GetPosition())}")
        else:
            put(t, f"track/{t.GetNetname()}/{t.GetLayer()}/{xy(t.GetStart())}/{xy(t.GetEnd())}")
    for z in b.Zones():
        put(z, f"zone/{z.GetZoneName()}")
    for d in b.GetDrawings():
        text = d.GetText() if isinstance(d, pcbnew.PCB_TEXT) else ""
        put(d, f"drawing/{d.GetLayer()}/{xy(d.GetPosition())}/{text}")


def main(netlist):
    bd, n = build(netlist, routes="--unrouted" not in sys.argv)
    if "--pads" in sys.argv:
        for (ref, num), pad in sorted(bd.pads.items()):
            print(ref, num, bd.pad_xy(ref, num), pad.GetNetname())
        return
    PCB.parent.mkdir(exist_ok=True)
    stable_uuids(bd.b)
    bd.b.Save(str(PCB))
    write_project()
    # Reload so the project's net classes apply to the fills, then stitch.
    b = pcbnew.LoadBoard(str(PCB))
    filler = pcbnew.ZONE_FILLER(b)
    filler.Fill(b.Zones())
    added = stitch(b) if n else 0
    filler.Fill(b.Zones())
    tb = b.GetTitleBlock()
    tb.SetTitle("Wallet DMM")
    tb.SetRevision("3")
    stable_uuids(b)
    b.Save(str(PCB))
    write_project()
    print(f"wrote {PCB} ({n} track segments, {added} stitching vias)")


if __name__ == "__main__":
    main(sys.argv[1])
