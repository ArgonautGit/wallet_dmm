#!/usr/bin/env python3
"""Runs the real firmware binary in Renode and shows what the OLED would.

The ELF from app/ runs on an STM32L051 model (stm32l051.repl). adc_l0.py
stands in for the ADC and the analog front end; the SSD1306 is a stub that
reports every I2C write. A scripted session presses buttons and changes what
the probes touch, and this script turns the captured I2C traffic back into
frames (out/frames/*.png, and out/session.gif) next to the UART log.

    cargo build --release --manifest-path ../app/Cargo.toml
    python3 run.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
ELF = HERE.parent / "app" / "target" / "thumbv6m-none-eabi" / "release" / "wallet-dmm"
sys.path.insert(0, str(HERE.parent / "sim"))

# Buttons: (port, pin). They are active low with pull-ups.
BUTTONS = {"A": ("gpioPortA", 11), "B": ("gpioPortA", 12), "C": ("gpioPortC", 14)}

# (name, probes, button presses before running, seconds to run)
# Probes: vin = volts on V; rx = ohms on OHM (or "open"); vext = volts on OHM;
# vf = a diode's forward voltage on OHM; usb = 1 when on USB.
# A press of ("uart", text) types text into USART1 RX instead.
STEPS = [
    ("volts_5V", {"vin": 5.0}, [], 1.5),
    ("volts_12V34", {"vin": 12.34}, [], 0.6),
    ("volts_neg3V3", {"vin": -3.3}, [], 0.6),
    ("volts_hold", {"vin": -3.3}, [("B", 0.1)], 0.4),
    ("volts_hold_kept", {"vin": 20.0}, [], 0.6),
    ("volts_overload", {"vin": 45.0}, [("B", 0.1)], 0.6),
    ("volts_10V", {"vin": 10.0}, [], 0.8),
    # Another meter says the input is at 10.050 V: set the gain to match.
    ("volts_cal_10V05", {"vin": 10.0}, [("uart", "cal 10.05\r")], 2.0),
    ("ohms_4k7", {"rx": 4700}, [("A", 0.1)], 0.8),
    ("ohms_470k", {"rx": 470e3}, [], 1.2),
    ("ohms_open", {"rx": "open"}, [], 1.0),
    ("ohms_voltage", {"vext": 12.0}, [], 0.6),
    ("ohms_negative_voltage", {"vext": -5.0}, [], 0.6),
    ("continuity_short", {"rx": 0.3}, [("A", 0.1)], 0.5),
    ("continuity_open", {"rx": "open"}, [], 0.4),
    ("diode_1n4148", {"vf": 0.517}, [("A", 0.1)], 0.6),
    ("info_usb", {"usb": 1}, [("A", 0.1)], 2.2),
    ("power_off", {}, [("A", 1.8)], 0.5),
    ("wake_volts_0V", {"vin": 0.0}, [("B", 0.1)], 1.5),
    ("zero_stored", {"vin": 0.0}, [("C", 3.3)], 0.8),
    ("power_off_again", {}, [("A", 1.8)], 0.5),
    ("wake_calibration_kept", {"vin": 10.0}, [("B", 0.1)], 2.0),
]

HOOKS = r'''
from Antmicro.Renode.Core import EmulationManager
m = list(EmulationManager.Instance.CurrentEmulation.Machines)[0]
trace = open(TRACE, 'w')
def now():
    return m.ElapsedVirtualTime.TimeElapsed.TotalSeconds
def on_i2c(data):
    trace.write('%.6f I2C %s\n' % (now(), ' '.join('%02x' % (b & 0xff) for b in data)))
def on_uart(c):
    trace.write('%.6f UART %02x\n' % (now(), c & 0xff))
def probes(text):
    # Closed at once: IronPython has no refcounting, so an unclosed file is
    # flushed whenever the GC gets to it, and the ADC could read it empty.
    with open(SCENARIO, 'w') as f:
        f.write(text.replace(';', '\n'))
def mark(name):
    b = m.SystemBus
    trace.write('%.6f MARK %s %08x %08x %08x %08x\n' % (now(), name, b.ReadDoubleWord(0x50000014),
                b.ReadDoubleWord(0x50000414), b.ReadDoubleWord(0x50000814), b.ReadDoubleWord(0x40011420)))
    trace.flush()
m['sysbus.i2c1.oled'].DataReceived += on_i2c
m['sysbus.usart1'].CharReceived += on_uart
'''


def seconds(t):
    return f'"00:00:{t:09.6f}"'


def script():
    OUT.mkdir(exist_ok=True)
    scenario = OUT / "scenario.txt"
    trace = OUT / "trace.txt"
    adc = (HERE / "adc_l0.py").read_text().replace("SCENARIO_PATH", repr(str(scenario)))
    (OUT / "adc_l0.py").write_text(adc)
    repl = (HERE / "stm32l051.repl").read_text().replace('"ADC_PY"', f'"{OUT / "adc_l0.py"}"')
    (OUT / "stm32l051.repl").write_text(repl)
    (OUT / "hooks.py").write_text(f"TRACE = {str(trace)!r}\nSCENARIO = {str(scenario)!r}\n" + HOOKS)
    scenario.write_text("vin=0\n")
    lines = [
        'mach create "dmm"',
        f"machine LoadPlatformDescription @{OUT / 'stm32l051.repl'}",
        # A system reset (the firmware resets itself when it wakes) reloads the
        # image. It also clears two things the real chip keeps: the buttons'
        # pull-ups (the model's inputs go low), and EXTI_IMR's reset value,
        # which unmasks the direct lines such as USART1's interrupt.
        "macro reset",
        '"""',
        f"    sysbus LoadELF @{ELF}",
        "    sysbus WriteDoubleWord 0x40010400 0x3F840000",
        *[f"    sysbus.{port} OnGPIO {pin} true" for port, pin in BUTTONS.values()],
        '"""',
        "runMacro $reset",
        "logLevel 3",
    ]
    lines += ["sysbus.gpioPortA OnGPIO 7 false", f"python \"execfile('{OUT / 'hooks.py'}')\""]
    for name, probes, presses, run_for in STEPS:
        text = ";".join(f"{k}={v}" for k, v in probes.items() if k != "usb")
        lines.append(f'python "probes(\'{text}\')"')
        lines.append(f"sysbus.gpioPortA OnGPIO 7 {'true' if probes.get('usb') else 'false'}")
        for button, held in presses:
            if button == "uart":
                lines += [f"sysbus.usart1 WriteChar {b}" for b in held.encode()]
                continue
            port, pin = BUTTONS[button]
            # Renode 1.16 updates PC14's input but does not pass it on to the
            # EXTI, so button C also drives its EXTI line directly.
            extra = port == "gpioPortC"
            lines += [f"sysbus.{port} OnGPIO {pin} false"] + ([f"sysbus.exti OnGPIO {pin} false"] if extra else [])
            lines += [f"emulation RunFor {seconds(held)}", f"sysbus.{port} OnGPIO {pin} true"]
            lines += [f"sysbus.exti OnGPIO {pin} true"] if extra else []
        lines += [f"emulation RunFor {seconds(run_for)}", f'python "mark(\'{name}\')"']
    lines.append("quit")
    path = OUT / "session.resc"
    path.write_text("\n".join(lines) + "\n")
    return path, trace


class Ssd1306:
    """Just enough of the SSD1306 to rebuild frames from its command stream."""

    ARGS = {0x20: 1, 0x21: 2, 0x22: 2, 0x81: 1, 0x8D: 1, 0xA8: 1, 0xD3: 1, 0xD5: 1, 0xD9: 1, 0xDA: 1,
            0xDB: 1, 0x26: 6, 0x27: 6, 0x29: 5, 0x2A: 5, 0xA3: 2}

    def __init__(self):
        self.ram = [[0] * 128 for _ in range(8)]
        self.col = (0, 127)
        self.page = (0, 7)
        self.ptr = (0, 0)
        self.on = False
        self.pending = []

    def command(self, bytes_):
        self.pending += bytes_
        while self.pending:
            op = self.pending[0]
            need = self.ARGS.get(op, 0)
            if len(self.pending) < need + 1:
                return
            args = self.pending[1:need + 1]
            self.pending = self.pending[need + 1:]
            if op == 0xAE:
                self.on = False
            elif op == 0xAF:
                self.on = True
            elif op == 0x21:
                self.col = (args[0], args[1])
                self.ptr = (self.page[0], args[0])
            elif op == 0x22:
                self.page = (args[0] & 7, args[1] & 7)
                self.ptr = (args[0] & 7, self.col[0])

    def data(self, bytes_):
        """Returns True when the write wrapped back to the start of the window."""
        wrapped = False
        page, col = self.ptr
        for b in bytes_:
            self.ram[page][col] = b
            col += 1
            if col > self.col[1]:
                col = self.col[0]
                page += 1
                if page > self.page[1]:
                    page = self.page[0]
                    wrapped = True
        self.ptr = (page, col)
        return wrapped

    def pixels(self):
        return [[bool(self.on and self.ram[y // 8][x] >> (y % 8) & 1) for x in range(128)] for y in range(32)]


def decode(trace):
    oled = Ssd1306()
    frames, marks, uart = [], [], []
    line = ""
    for raw in trace.read_text().splitlines():
        t, kind, rest = raw.split(" ", 2)
        t = float(t)
        if kind == "I2C":
            b = [int(x, 16) for x in rest.split()]
            if b and b[0] == 0x00:
                was_on = oled.on
                oled.command(b[1:])
                if was_on != oled.on:
                    frames.append((t, oled.pixels()))
            elif b and b[0] == 0x40 and oled.data(b[1:]):
                frames.append((t, oled.pixels()))
        elif kind == "UART":
            c = chr(int(rest, 16))
            if c == "\n":
                uart.append((t, line.strip()))
                line = ""
            else:
                line += c
        elif kind == "MARK":
            name, *state = rest.split()
            marks.append((t, name, [int(s, 16) for s in state]))
    return frames, marks, uart


def montage(names, path):
    from PIL import Image

    shots = [Image.open(OUT / "frames" / f"{n}.png") for n in names if (OUT / "frames" / f"{n}.png").exists()]
    w, h = shots[0].size
    rows = (len(shots) + 1) // 2
    sheet = Image.new("RGB", (2 * w + 18, rows * (h + 6) + 6), (40, 40, 40))
    for i, im in enumerate(shots):
        sheet.paste(im, (6 + (i % 2) * (w + 6), 6 + (i // 2) * (h + 6)))
    sheet.save(path)


def main():
    if not ELF.exists():
        sys.exit(f"build the firmware first: {ELF} is missing")
    resc, trace = script()
    shutil.rmtree(OUT / "frames", ignore_errors=True)
    (OUT / "frames").mkdir()
    # stdin is closed so a script error ends Renode instead of leaving it at its prompt.
    run = subprocess.run(["renode", "--disable-gui", "--console", "-e", f"include @{resc}"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
    (OUT / "renode.log").write_text(run.stdout + run.stderr)
    frames, marks, uart = decode(trace)
    print(f"{len(frames)} display frames, {len(uart)} UART lines, {len(marks)} steps")

    from dmm_frames import save_frame, save_gif  # noqa: E402

    report = []
    for t, name, (gpioa, gpiob, gpioc, ccer) in marks:
        shown = [f for f in frames if f[0] <= t]
        if shown:
            save_frame(shown[-1][1], OUT / "frames" / f"{name}.png")
        last = [u for u in uart if u[0] <= t]
        leds = [c for c, bit, port in (("red", 15, gpioa), ("green", 3, gpiob), ("blue", 8, gpioa))
                if not port >> bit & 1]
        piezo = "on" if ccer & 0b10001 == 0b10001 else "off"
        oled_power = "on" if gpioc >> 15 & 1 else "off"
        report.append((name, last[-1][1] if last else "", ",".join(leds) or "-", piezo, oled_power))
    save_gif(frames, OUT / "session.gif")
    montage([name for _, name, _ in marks], OUT / "montage.png")
    with (OUT / "session.md").open("w") as f:
        f.write("| Step | Last UART line | LEDs lit | Piezo | OLED power |\n|---|---|---|---|---|\n")
        for r in report:
            f.write("| " + " | ".join(r) + " |\n")
    print((OUT / "session.md").read_text())


if __name__ == "__main__":
    main()
