#!/usr/bin/env python3
"""DC and transient runs of the analog front end in ngspice.

The netlist mirrors hardware/wallet_dmm.kicad_sch: the 10 Mohm divider with its
mid-rail bias, the ohms references with their BAV199 clamps, and the MCU pins
the nodes land on (clamp diodes where the pin has them, leakage, and the
40 ohm on-resistance of a GPIO driving high). Results go to out/*.csv as the
voltages the ADC pins see, for sim/src/main.rs to convert.
"""

import csv
import math
import subprocess
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"

VDD = 2.97    # the XC6206 rail, 1 % low (sim/src/main.rs assumes the same)
VBAT = 3.8    # a half-charged cell

HEADER = """* Wallet DMM rev 3 front end
.model DPIN D(IS=1e-14 N=1.0)
.model DBAV199 D(IS=4e-12 N=1.4 RS=2 BV=85 IBV=5n CJO=2p)
.model D1N4148 D(IS=2.52n RS=0.568 N=1.752 BV=100 IBV=100u CJO=4p)
.model DLEDRED D(IS=1e-19 N=1.9 RS=5)
{rail}
VBAT batt 0 {vbat}

* V input: R10 + R11 to the tap, R12 to the bias, C11 holds the tap.
R10 inv mid1 {r10}
R11 mid1 tap {r11}
C11 tap 0 47n
R12 tap vmid {r12}
* VMID: PA2 through R13 into R14, C12; sensed on PA1 through R15.
R13 en vmid {r13}
R14 vmid 0 {r14}
C12 vmid 0 1u
R15 vmid pa1 1k
ILK1 pa1 0 {ileak}
* PA4 (TC): clamps to VDD and GND, leakage.
DPA4H tap vdd DPIN
DPA4L 0 tap DPIN
ILK4 tap 0 {ileak}

* Ohms, low range: PA6 through R16 to E_LO, R17 to the input.
R16 lo elo {r16}
R17 elo inohm {r17}
D2A 0 elo DBAV199
D2B elo batt DBAV199
R18 elo pa0 {r18}
DPA0H pa0 vdd DPIN
DPA0L 0 pa0 DPIN
ILK0 pa0 0 {ileak}
DPA6L 0 lo DPIN
* High range: PA3 through R19 to E_HI, R20 to the input.
R19 hi ehi 1k
R20 ehi inohm {r20}
D3A 0 ehi DBAV199
D3B ehi batt DBAV199
DPA3L 0 hi DPIN
* The input, through R21 into the C13 hold capacitor on PA5 (TC).
R21 inohm pa5 {r21}
C13 pa5 0 22n
DPA5H pa5 vdd DPIN
DPA5L 0 pa5 DPIN
ILK5 pa5 0 {ileak}
"""


NOMINAL = {"r10": 4.7e6, "r11": 4.7e6, "r12": 470e3, "r13": 4.7e3, "r14": 5.1e3, "r16": 1e3,
           "r17": 10e3, "r18": 10e3, "r20": 1e6, "r21": 47e3}
TOLERANCE = {"r17": 0.001}  # everything else is 1 %

# The 3.0 V rail. Running: an ideal source. Asleep: the LDO can only source
# current, the MCU draws about 1 uA, and optionally U4 (TL431, R29/R30) clamps
# at 2.495 V x (1 + 330k / 1M).
RAIL_RUNNING = "VDD vdd 0 {vdd}"
RAIL_ASLEEP = """VLDO ldo 0 {vdd}
DLDO ldo vdd DIDEAL
.model DIDEAL D(IS=1e-12 N=0.01)
ISTOP vdd 0 1u
VDD vdd vddm 0
RSENSE vddm 0 1T"""
CLAMP = "BCLAMP vdd 0 I = V(vdd) > 3.318 ? (V(vdd) - 3.318) / 0.5 : 0"


def netlist(vmid_en=False, drive=None, v_in=0.0, dut=None, ileak=1e-9, vdd=VDD, vbat=VBAT, extra="",
            values=None, asleep=False, clamp=True):
    """drive: None, "lo" or "hi" (that pin pushes VDD through 40 ohm).
    dut: ("r", ohms) | ("v", volts) | ("d", model) | None, on the ohms input."""
    rail = (RAIL_ASLEEP if asleep else RAIL_RUNNING).format(vdd=vdd)
    if clamp:
        rail += "\n" + CLAMP
    n = HEADER.format(vdd=vdd, vbat=vbat, ileak=ileak, rail=rail, **{**NOMINAL, **(values or {})})
    n += f"VIN inv 0 DC {v_in}\n"
    n += "VEN en_src 0 DC {}\nRON_EN en_src en 40\n".format(vdd if vmid_en else 0)
    for name in ("lo", "hi"):
        if drive == name:
            n += f"V{name.upper()} {name}_src 0 DC {vdd}\nRON_{name.upper()} {name}_src {name} 40\n"
        else:
            n += f"RZ_{name.upper()} {name} 0 1T\n"  # analog mode: open
    if dut is None:
        n += "RDUT inohm 0 1T\n"
    elif dut[0] == "r":
        n += f"RDUT inohm 0 {dut[1]}\n"
    elif dut[0] == "v":
        n += f"VDUT inohm 0 DC {dut[1]}\n"
    elif dut[0] == "d":
        n += f"DDUT inohm 0 {dut[1]}\n"
    return n + extra


def run(net, control):
    with tempfile.TemporaryDirectory() as d:
        cir = Path(d) / "c.cir"
        cir.write_text(net + ".control\nset noaskquit\n" + control + "\n.endc\n.end\n")
        p = subprocess.run(["ngspice", "-b", str(cir)], capture_output=True, text=True, cwd=d)
        out = p.stdout
        data = {}
        for line in out.splitlines():
            if "=" in line and not line.startswith(("*", "Note", "Warning")):
                k, _, v = line.partition("=")
                try:
                    data[k.strip()] = float(v.split()[0])
                except ValueError:
                    pass
        if not data:
            raise RuntimeError(p.stdout[-2000:] + p.stderr[-2000:])
        return data


PROBES = ("v(tap)", "v(pa1)", "v(pa0)", "v(pa5)", "v(elo)", "v(ehi)", "v(inohm)",
          "i(vbat)", "i(vdd)", "i(vdut)")


def op(net, probes=PROBES):
    lines = ["op"] + [f"print {p}" for p in probes]
    return run(net, "\n".join(lines))


def write(name, rows):
    OUT.mkdir(exist_ok=True)
    with (OUT / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"out/{name}: {len(rows)} rows")


def decades(lo, hi, per):
    n = int(round(math.log10(hi / lo) * per))
    return [lo * 10 ** (i / per) for i in range(n + 1)]


def volts_sweep(ileak=1e-9, tag=""):
    rows = []
    for i in range(-140, 141):
        vin = i * 0.25
        d = op(netlist(vmid_en=True, v_in=vin, ileak=ileak), ("v(tap)", "v(pa1)", "i(vdd)"))
        rows.append({"vin": vin, "tap": d["v(tap)"], "vmid": d["v(pa1)"], "i_vdd": d["i(vdd)"]})
    write(f"volts{tag}.csv", rows)


def ohms_sweep(ileak=1e-9, tag=""):
    rows = []
    for rx in [0.0] + decades(0.1, 30e6, 12):
        dut = ("r", max(rx, 1e-6))
        for drive in ("lo", "hi"):
            d = op(netlist(drive=drive, dut=dut, ileak=ileak), ("v(pa0)", "v(pa5)", "v(elo)", "v(inohm)"))
            rows.append({"rx": rx, "range": drive, "elo": d["v(pa0)"], "input": d["v(pa5)"],
                         "node_elo": d["v(elo)"], "node_in": d["v(inohm)"]})
    write(f"ohms{tag}.csv", rows)


def diode_cases():
    rows = []
    for name, model in (("1N4148", "D1N4148"), ("red LED", "DLEDRED")):
        d = op(netlist(drive="lo", dut=("d", model)), ("v(pa0)", "v(pa5)", "v(inohm)", "i(vlo)"))
        rows.append({"part": name, "elo": d["v(pa0)"], "input": d["v(pa5)"], "vf": d["v(inohm)"],
                     "i_test": -d["i(vlo)"]})
    write("diode.csv", rows)


def overload_sweep():
    """A voltage source on the ohms input, with the low range driving (worst
    case: firmware has not noticed yet) and with both drives off."""
    rows = []
    probes = ("v(pa0)", "v(pa5)", "v(elo)", "v(ehi)", "i(vbat)", "i(vdd)", "i(vdut)", "v(lo)")
    for i in range(-40, 41):
        vext = i * 1.0
        for drive in ("lo", None):
            d = op(netlist(drive=drive, dut=("v", vext)), probes)
            # Current into the battery through D2B/D3B, and into the 3.0 V rail.
            rows.append({"vext": vext, "drive": drive or "off", "pa0": d["v(pa0)"], "pa5": d["v(pa5)"],
                         "elo": d["v(elo)"], "ehi": d["v(ehi)"], "i_batt": -d["i(vbat)"],
                         "i_vdd": -d["i(vdd)"], "i_input": -d["i(vdut)"],
                         "p_r17": (vext - d["v(elo)"]) ** 2 / 10e3})
    write("overload.csv", rows)
    rows = []
    for i in range(-20, 21):
        vin = i * 5.0
        d = op(netlist(vmid_en=True, v_in=vin), ("v(tap)", "i(vdd)", "i(vin)"))
        rows.append({"vin": vin, "tap": d["v(tap)"], "i_in": -d["i(vin)"]})
    write("overload_v.csv", rows)


def continuity_transient():
    """Probes open, then shorted at 20 ms and opened again at 120 ms."""
    net = netlist(drive="lo", dut=None).replace("RDUT inohm 0 1T\n", "")
    net += "SW1 inohm 0 ctl 0 SWMOD\nVCTL ctl 0 PULSE(0 1 20m 1u 1u 100m 1)\n"
    net += ".model SWMOD SW(RON=0.05 ROFF=1T VT=0.5 VH=0.1)\n"
    ctl = "tran 50u 200m\nwrdata trans.txt v(pa5) v(pa0)\n"
    with tempfile.TemporaryDirectory() as d:
        cir = Path(d) / "c.cir"
        cir.write_text(net + ".ic v(pa5)=2.99\n.control\n" + ctl + ".endc\n.end\n")
        subprocess.run(["ngspice", "-b", str(cir)], capture_output=True, text=True, cwd=d, check=True)
        rows = []
        for line in (Path(d) / "trans.txt").read_text().split("\n"):
            parts = line.split()
            if len(parts) >= 4:
                rows.append({"t": float(parts[0]), "input": float(parts[1]), "elo": float(parts[3])})
    write("continuity_tran.csv", rows)


def settle_transient():
    """V input stepped from 0 to 10 V at 10 ms, VMID already on."""
    net = netlist(vmid_en=True).replace("VIN inv 0 DC 0.0\n", "VIN inv 0 PULSE(0 10 10m 1u 1u 1 2)\n")
    with tempfile.TemporaryDirectory() as d:
        cir = Path(d) / "c.cir"
        cir.write_text(net + ".control\ntran 100u 300m\nwrdata st.txt v(tap) v(pa1)\n.endc\n.end\n")
        subprocess.run(["ngspice", "-b", str(cir)], capture_output=True, text=True, cwd=d, check=True)
        rows = []
        for line in (Path(d) / "st.txt").read_text().split("\n"):
            parts = line.split()
            if len(parts) >= 4:
                rows.append({"t": float(parts[0]), "tap": float(parts[1]), "vmid": float(parts[3])})
    write("volts_tran.csv", rows)


def stop_mode_rail():
    """30 V class faults on the ohms input while the MCU sleeps: where does
    the rail go, with and without the TL431 clamp?"""
    rows = []
    for i in range(0, 41):
        vext = float(i)
        row = {"vext": vext}
        for clamp in (False, True):
            d = op(netlist(dut=("v", vext), asleep=True, clamp=clamp), ("v(vdd)", "i(vbat)", "v(pa5)"))
            key = "clamp" if clamp else "noclamp"
            row[f"vdd_{key}"] = d["v(vdd)"]
            row[f"pa5_{key}"] = d["v(pa5)"]
        rows.append(row)
    write("stop_rail.csv", rows)


def monte_carlo(n=200, seed=3):
    """Resistor tolerances (uniform within 1 %, R17 0.1 %), nominal leakage."""
    import random
    rng = random.Random(seed)
    vrows, orows = [], []
    for s in range(n):
        vals = {k: v * (1 + rng.uniform(-1, 1) * TOLERANCE.get(k, 0.01)) for k, v in NOMINAL.items()}
        for vin in (-30.0, -10.0, -1.0, 0.0, 1.0, 5.0, 10.0, 20.0, 30.0):
            d = op(netlist(vmid_en=True, v_in=vin, values=vals), ("v(tap)", "v(pa1)"))
            vrows.append({"sample": s, "vin": vin, "tap": d["v(tap)"], "vmid": d["v(pa1)"]})
        for rx in (0.0, 1.0, 10.0, 100.0, 1e3, 10e3, 100e3, 1e6, 10e6):
            for drive in ("lo", "hi"):
                d = op(netlist(drive=drive, dut=("r", max(rx, 1e-6)), values=vals), ("v(pa0)", "v(pa5)"))
                orows.append({"sample": s, "rx": rx, "range": drive, "elo": d["v(pa0)"], "input": d["v(pa5)"]})
    write("mc_volts.csv", vrows)
    write("mc_ohms.csv", orows)


if __name__ == "__main__":
    volts_sweep()
    volts_sweep(ileak=50e-9, tag="_leak50n")
    ohms_sweep()
    ohms_sweep(ileak=50e-9, tag="_leak50n")
    diode_cases()
    overload_sweep()
    continuity_transient()
    settle_transient()
    stop_mode_rail()
    monte_carlo()
