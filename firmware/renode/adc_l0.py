# Renode Python peripheral (IronPython 2.7): the STM32L0 ADC and, behind it,
# the wallet DMM's analog front end.
#
# The firmware's GPIO outputs decide what the front end does (VMID_EN on PA2,
# OHM_HI on PA3, OHM_LO on PA6), so each conversion reads GPIOA and solves the
# circuit for the channel being converted. What is connected to the probes
# comes from the scenario file that run.py rewrites between steps:
#   vin=<volts on V>  rx=<ohms on OHM, or open>  vext=<volts on OHM>
#   vbat=<battery volts>  vdda=<rail volts>  temp=<degrees C>

if request.IsInit:
    import math
    import random
    from Antmicro.Renode.Core import EmulationManager

    ISR, IER, CR, CFGR1, CFGR2, SMPR, TR, CHSELR, DR, CALFACT, CCR = (
        0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x20, 0x28, 0x40, 0xB4, 0x308)
    regs = {ISR: 0, IER: 0, CR: 0, CFGR1: 0, CFGR2: 0, SMPR: 0, TR: 0x0FFF0000, CHSELR: 0, DR: 0,
            CALFACT: 0, CCR: 0}
    rng = random.Random(1234)
    scenario_path = SCENARIO_PATH
    conversions = [0]

    def scenario():
        s = {"vin": 0.0, "rx": "open", "vext": None, "vf": None, "vbat": 3.9, "vdda": 2.97, "temp": 24.0}
        try:
            with open(scenario_path) as f:
                lines = f.read().splitlines()
        except IOError:
            lines = []
        for line in lines:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                s[k] = v if k == "rx" and v == "open" else (None if v == "none" else float(v))
        return s

    def bus():
        for m in EmulationManager.Instance.CurrentEmulation.Machines:
            return m.SystemBus

    def driven_high(moder, odr, pin):
        return (moder >> (2 * pin)) & 3 == 1 and (odr >> pin) & 1 == 1

    def driven_low(moder, odr, pin):
        return (moder >> (2 * pin)) & 3 == 1 and (odr >> pin) & 1 == 0

    def clamp(v, vdd):
        # Pin clamp diodes: to GND, and to VDD on the TC pins.
        return max(-0.6, min(v, vdd + 0.6))

    def front_end(channel):
        s = scenario()
        vdd = s["vdda"]
        b = bus()
        moder, odr = b.ReadDoubleWord(0x50000000), b.ReadDoubleWord(0x50000014)
        en = driven_high(moder, odr, 2)
        hi = driven_high(moder, odr, 3)
        lo = driven_high(moder, odr, 6)
        sink = driven_low(moder, odr, 6)

        if channel in (1, 4):
            # Tap T and bias M: V input -(9.4M)- T -(470k)- M; M -(4.74k)- PA2, M -(5.1k)- GND.
            gt, g12, g13, g14 = 1 / 9.4e6, 1 / 470e3, 1 / 4.74e3, 1 / 5.1e3
            ven = vdd if en else 0.0
            a11, a12, b1 = gt + g12, -g12, s["vin"] * gt
            a21, a22, b2 = -g12, g12 + g13 + g14, ven * g13
            det = a11 * a22 - a12 * a21
            tap = (b1 * a22 - a12 * b2) / det
            mid = (a11 * b2 - a21 * b1) / det
            return clamp(tap, vdd) if channel == 4 else mid

        if channel in (0, 5):
            # OHM input J, low-range drive node E: PA6 -(1.04k)- E -(10k)- J -(Rx)- GND.
            if sink:
                # OHM_LO pulled low: only a source can lift the input.
                j = s["vext"] if s["vext"] is not None else 0.0
                e = max(-0.7, min(j * 1040.0 / 11040.0, s["vbat"] + 0.7))
            elif s["vf"] is not None and lo:
                # A diode: the drive sets the current, the diode the voltage.
                j = s["vf"]
                e = j + (vdd - j) / (1040.0 + 1e4) * 1e4
            elif s["vext"] is not None:
                j = s["vext"]
                e = (vdd / 1040.0 + j / 1e4) / (1 / 1040.0 + 1 / 1e4) if lo else j
                e = max(-0.7, min(e, s["vbat"] + 0.7))  # D2
            else:
                rx = None if s["rx"] == "open" else s["rx"]
                if lo:
                    series = 1040.0 + 1e4
                elif hi:
                    series = 1040.0 + 1e6
                else:
                    series = None
                if series is None:
                    j = 0.0 if rx is not None else 0.0
                    e = j
                elif rx is None:
                    j = e = vdd
                else:
                    i = vdd / (series + rx)
                    j = i * rx
                    e = j + i * 1e4 if lo else j
            return clamp(e if channel == 0 else j, vdd)
        if channel == 9:
            return s["vbat"] / 2.0
        if channel == 17:
            return 1.2242
        if channel == 18:
            return 0.49084 + 0.0016117 * (s["temp"] - 30.0)
        return 0.0

    def sample(volts, vdd):
        code = volts / vdd * 4096.0 + 0.6 + rng.gauss(0.0, 0.8)
        return int(max(0, min(4095, round(code))))

    def convert():
        sel = regs[CHSELR]
        channel = 0
        while channel < 19 and not (sel >> channel) & 1:
            channel += 1
        vdd = scenario()["vdda"]
        volts = front_end(channel)
        cfgr2 = regs[CFGR2]
        if cfgr2 & 1:
            n = 2 << ((cfgr2 >> 2) & 7)
            shift = (cfgr2 >> 5) & 15
            value = sum(sample(volts, vdd) for _ in range(n)) >> shift
        else:
            value = sample(volts, vdd)
        regs[DR] = value & 0xFFFF
        regs[ISR] |= (1 << 2) | (1 << 3)  # EOC, EOS
        conversions[0] += 1

elif request.IsRead:
    value = regs.get(request.Offset, 0)
    if request.Offset == DR:
        regs[ISR] &= ~(1 << 2)
    request.Value = value

elif request.IsWrite:
    off, val = request.Offset, request.Value
    if off == ISR:
        regs[ISR] &= ~val
    elif off == CR:
        if val & (1 << 31):  # ADCAL: done at once
            val &= ~(1 << 31)
            regs[ISR] |= 1 << 11
        if val & (1 << 1):  # ADDIS
            val &= ~((1 << 1) | 1)
        if val & 1:  # ADEN
            regs[ISR] |= 1
        if val & (1 << 2):  # ADSTART
            convert()
            val &= ~(1 << 2)
        regs[CR] = val
    else:
        regs[off] = val
