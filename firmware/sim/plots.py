#!/usr/bin/env python3
"""Figures and a summary from the simulation results in out/."""

import csv
import math
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
FIG = OUT / "figures"


def rows(name):
    return list(csv.DictReader((OUT / name).open()))


def num(s):
    try:
        return float(s)
    except ValueError:
        return None


def style(ax, title, xlabel, ylabel):
    ax.set_title(title, fontsize=10, loc="left")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, which="both", alpha=0.3)


def volts(summary):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for name, label, color in (("readings_volts.csv", "typical part, 1 nA pin leakage", "C0"),
                               ("readings_volts_leak50n.csv", "worst-case leakage, 50 nA", "C3")):
        xs, ys = [], []
        for r in rows(name):
            v = num(r["reading_uv"])
            if v is not None:
                xs.append(float(r["vin"]))
                ys.append((v / 1e6 - float(r["vin"])) * 1e3)
        ax.plot(xs, ys, color=color, lw=1.2, label=f"{label}, uncalibrated")
    for key, label, color in (("uncal_uv", "200 boards, uncalibrated", "C1"),
                              ("cal_uv", "200 boards, calibrated at 0 V and 10 V", "C2")):
        spread = defaultdict(list)
        for r in rows("readings_mc_volts.csv"):
            v = num(r[key])
            if v is not None:
                spread[float(r["vin"])].append((v / 1e6 - float(r["vin"])) * 1e3)
        xs = sorted(spread)
        ax.fill_between(xs, [min(spread[x]) for x in xs], [max(spread[x]) for x in xs], color=color, alpha=0.25,
                        label=label)
        low = max(abs(e) for x in xs if abs(x) <= 5 for e in spread[x])
        worst = max(abs(e) for x in xs for e in spread[x])
        clipped = sum(1 for r in rows("readings_mc_volts.csv") if num(r[key]) is None)
        summary.append(f"| Volts, {label} | ±{low:.0f} mV up to ±5 V, ±{worst:.0f} mV at ±30 V"
                       + (f", {clipped} readings over range" if clipped else "") + " |")
    ax.axhline(0, color="k", lw=0.6)
    style(ax, "DC volts: reading error", "input (V)", "error (mV)")
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(FIG / "volts.png", dpi=130)


def ohms(summary):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for name, label, color in (("readings_ohms.csv", "autorange, typical part, not zeroed", "C0"),
                               ("readings_ohms_leak50n.csv", "autorange, 50 nA pin leakage", "C3")):
        xs, ys = [], []
        for r in rows(name):
            rx, v = float(r["rx"]), num(r["auto_mohm"])
            if v is not None and rx >= 1:
                xs.append(rx)
                ys.append((v / 1000 - rx) / rx * 100)
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        ax.plot([xs[i] for i in order], [ys[i] for i in order], color=color, lw=1.2, label=label)
    for key, label, color in (("uncal_mohm", "200 boards, uncalibrated", "C1"),
                              ("cal_mohm", "200 boards, leads zeroed", "C2")):
        spread = defaultdict(list)
        for r in rows("readings_mc_ohms.csv"):
            rx, v = float(r["rx"]), num(r[key])
            valid = (r["range"] == "lo" and rx <= 100e3) or (r["range"] == "hi" and rx >= 1e6)
            if v is not None and rx >= 10 and valid:
                spread[rx].append((v / 1000 - rx) / rx * 100)
        xs = sorted(spread)
        ax.fill_between(xs, [min(spread[x]) for x in xs], [max(spread[x]) for x in xs], color=color, alpha=0.25,
                        label=label)
        if key == "cal_mohm":
            absolute = [v / 1000 - float(r["rx"]) for r in rows("readings_mc_ohms.csv")
                        if r["range"] == "lo" and float(r["rx"]) <= 100 and (v := num(r[key])) is not None]
            summary.append(f"| Ohms, {label}, up to 100 Ω | ±{max(abs(e) for e in absolute):.1f} Ω |")
            for lo, hi, name in ((1e3, 100e3, "1 kΩ–100 kΩ"), (1e6, 1e6, "1 MΩ"), (10e6, 10e6, "10 MΩ")):
                sel = [e for x in xs if lo <= x <= hi for e in spread[x]]
                summary.append(f"| Ohms, {label}, {name} | ±{max(abs(e) for e in sel):.2f} % |")
    ax.set_xscale("log")
    ax.set_ylim(-3, 3)
    ax.axvspan(150e3, 200e3, color="0.85", label="range handover (hysteresis)")
    style(ax, "Resistance: reading error, autoranging 10 kΩ / 1 MΩ", "resistance (Ω)", "error (%)")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(FIG / "ohms.png", dpi=130)


def overload(summary):
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 4))
    r = [x for x in rows("overload.csv") if x["drive"] == "off"]
    v = [float(x["vext"]) for x in r]
    a.plot(v, [float(x["i_input"]) * 1e3 for x in r], label="drawn from the source")
    a.plot(v, [-float(x["i_batt"]) * 1e3 for x in r], label="into the battery (D2, D3)")
    a.plot(v, [-float(x["i_vdd"]) * 1e3 for x in r], label="into the 3.0 V rail (pin clamps)")
    a.plot(v, [float(x["p_r17"]) * 1e3 / 100 for x in r], "--", label="R17 power / 100 (mW)")
    style(a, "Voltage on the Ω input, drives off", "applied voltage (V)", "current (mA)")
    a.legend(fontsize=7)
    s = rows("stop_rail.csv")
    v = [float(x["vext"]) for x in s]
    b.plot(v, [float(x["vdd_noclamp"]) for x in s], color="C3", label="3.0 V rail, without U4")
    b.plot(v, [float(x["vdd_clamp"]) for x in s], color="C2", label="3.0 V rail, with U4 (TL431)")
    b.plot(v, [float(x["pa5_clamp"]) for x in s], color="C0", ls="--", label="PA5 pin, with U4")
    b.axhline(3.6, color="0.5", lw=0.8, ls=":")
    b.axhline(4.0, color="k", lw=0.8, ls=":")
    b.text(0.5, 4.1, "MCU absolute maximum 4.0 V", fontsize=7)
    b.set_ylim(0, 12)
    style(b, "Same fault while the MCU sleeps (1 µA load)", "applied voltage (V)", "voltage (V)")
    b.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "overload.png", dpi=130)
    at30 = next(x for x in r if float(x["vext"]) == 30)
    s30 = next(x for x in s if float(x["vext"]) == 30)
    summary.append(f"| 30 V on the Ω input | {-float(at30['i_batt']) * 1e3:.1f} mA into the battery, "
                   f"{float(at30['p_r17']) * 1e3:.0f} mW in R17 |")
    summary.append(f"| Same, MCU asleep | rail {float(s30['vdd_noclamp']):.1f} V without U4, "
                   f"{float(s30['vdd_clamp']):.2f} V with it |")


def transients(summary):
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 4))
    t = rows("continuity_tran.csv")
    ts = [float(x["t"]) * 1e3 for x in t]
    j = [float(x["input"]) for x in t]
    e = [float(x["elo"]) for x in t]
    a.plot(ts, j, label="PA5 (input, through R21 into C13)")
    a.plot(ts, e, label="PA0 (E_LO)")
    # The firmware reads every 30 ms in continuity mode; beep below 50 ohm.
    beep_at = None
    for k in range(0, 7):
        ts_k = 30.0 * k + 5
        i = min(range(len(ts)), key=lambda n: abs(ts[n] - ts_k))
        r = 1e4 * j[i] / max(e[i] - j[i], 1e-9)
        beeps = r < 50
        a.axvline(ts_k, color="C2" if beeps else "0.6", lw=0.8, ls="--")
        if beeps and beep_at is None and ts_k > 20:
            beep_at = ts_k
    a.axvspan(20, 120, color="C2", alpha=0.08, label="probes touching")
    style(a, "Continuity: probes touch at 20 ms (dashed: readings, green = beep)", "time (ms)", "volts")
    a.legend(fontsize=7)
    t = rows("volts_tran.csv")
    ts = [float(x["t"]) * 1e3 for x in t]
    tap = [float(x["tap"]) for x in t]
    vin = [x + (x - float(m["vmid"])) * 20 for x, m in zip(tap, t)]
    b.plot(ts, vin, label="input as the firmware would compute it")
    b.axhline(10, color="k", lw=0.6)
    b.axvline(10, color="0.6", ls=":")
    final = vin[-1]
    settle = next(tt for tt, v in zip(ts, vin) if tt > 10 and abs(v - final) < 0.005)
    b.axvline(settle, color="C2", ls="--", label=f"within 5 mV of final after {settle - 10:.0f} ms")
    style(b, "V input: 0 → 10 V step at 10 ms", "time (ms)", "volts")
    b.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "transients.png", dpi=130)
    if beep_at:
        summary.append(f"| Continuity response | beeps at the first reading after contact ({beep_at - 20:.0f} ms) |")
    summary.append(f"| V input settling | {settle - 10:.0f} ms to 5 mV (firmware reads every 200 ms) |")


def gallery():
    shots = sorted((OUT / "screens").glob("*.png"))
    cols = 2
    fig, axes = plt.subplots(math.ceil(len(shots) / cols), cols, figsize=(8, 1.25 * math.ceil(len(shots) / cols)))
    for ax in axes.flat:
        ax.axis("off")
    for ax, p in zip(axes.flat, shots):
        ax.imshow(mpimg.imread(p))
        ax.set_title(p.stem.replace("_", " "), fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "screens.png", dpi=130, facecolor="white")


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    summary = ["| Check | Result |", "|---|---|"]
    volts(summary)
    ohms(summary)
    for r in rows("readings_diode.csv"):
        summary.append(f"| Diode test, {r['part']} | reads {int(r['reading_uv']) / 1e6:.3f} V "
                       f"(true {float(r['vf_true']):.3f} V) |")
    overload(summary)
    transients(summary)
    gallery()
    (OUT / "summary.md").write_text("\n".join(summary) + "\n")
    print("\n".join(summary))
