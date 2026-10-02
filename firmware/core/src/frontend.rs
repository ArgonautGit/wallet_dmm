//! The analog front end: turns ADC codes into volts, ohms and the rest.
//!
//! Component values match the schematic (hardware/wallet_dmm.kicad_sch). All
//! arithmetic is integer: µV, mΩ and tenths of a degree.

/// Full scale of one conversion: 12 bits, oversampled 16 times by the ADC.
pub const FULL_SCALE: u32 = 4095 * 16;
/// Codes this close to either end mean the input is clipped (4 LSB, a bit
/// more than the ADC's offset error).
pub const CLIP_MARGIN: u32 = 16 * 4;

/// R10 + R11, from the V input to the divider tap.
pub const R_TOP: i64 = 9_400_000;
/// R12, from the tap to the mid-rail bias.
pub const R_BOT: i64 = 470_000;

/// Continuity beeps below this, in mΩ.
pub const CONTINUITY_MOHM: i64 = 50_000;
/// A diode test above this reads as open, in µV.
pub const DIODE_OPEN_UV: u32 = 2_700_000;

/// Factory calibration from the MCU's system memory.
#[derive(Clone, Copy, Debug)]
pub struct Factory {
    /// VREFINT conversion at VDDA = 3.0 V, 12 bits.
    pub vrefint_cal: u16,
    /// Temperature sensor conversion at 30 °C, VDDA = 3.0 V.
    pub ts_cal1: u16,
    /// Temperature sensor conversion at 130 °C, VDDA = 3.0 V.
    pub ts_cal2: u16,
}

/// User calibration, kept in the MCU's EEPROM.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Calibration {
    /// Subtracted from voltage readings, µV.
    pub volts_offset_uv: i32,
    /// Voltage gain correction in parts per million of 1.
    pub volts_gain_ppm: u32,
    /// Probe lead resistance, subtracted from resistance readings, mΩ.
    pub lead_mohm: u32,
}

impl Default for Calibration {
    fn default() -> Self {
        Self { volts_offset_uv: 0, volts_gain_ppm: 1_000_000, lead_mohm: 0 }
    }
}

/// A reading, or why there isn't one.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Value {
    Ok(i64),
    /// Beyond the range (volts), or a voltage on the Ω input.
    Overload,
    /// Nothing connected (ohms, diode).
    Open,
}

/// VDDA in µV, from a VREFINT conversion.
pub fn vdda_uv(f: &Factory, vref_code: u32) -> u32 {
    (3_000_000u64 * f.vrefint_cal as u64 * 16 / vref_code.max(1) as u64) as u32
}

/// A conversion in µV, given VDDA.
pub fn to_uv(code: u32, vdda_uv: u32) -> u32 {
    (code as u64 * vdda_uv as u64 / FULL_SCALE as u64) as u32
}

fn clipped(code: u32) -> bool {
    !(CLIP_MARGIN..=FULL_SCALE - CLIP_MARGIN).contains(&code)
}

/// Voltage on the V input in µV, from the divider tap (ADC_V) and the bias
/// under the divider (ADC_VMID):
/// VIN = tap + (tap - VMID) x (R10 + R11) / R12.
pub fn volts(cal: &Calibration, vdda_uv: u32, tap_code: u32, mid_code: u32) -> Value {
    if clipped(tap_code) {
        return Value::Overload;
    }
    let tap = to_uv(tap_code, vdda_uv) as i64;
    let mid = to_uv(mid_code, vdda_uv) as i64;
    let vin = tap + (tap - mid) * R_TOP / R_BOT;
    Value::Ok(vin * cal.volts_gain_ppm as i64 / 1_000_000 - cal.volts_offset_uv as i64)
}

/// The gain that makes the current reading (`reading_uv`, taken with `cal`)
/// read `true_uv` instead. Needs at least 1 V, and refuses a correction of
/// more than 5 %, which would be a wrong reference rather than an error.
pub fn gain_ppm(cal: &Calibration, reading_uv: i64, true_uv: i64) -> Option<u32> {
    let off = cal.volts_offset_uv as i64;
    if true_uv.abs() < 1_000_000 || reading_uv + off == 0 {
        return None;
    }
    let gain = cal.volts_gain_ppm as i64 * (true_uv + off) / (reading_uv + off);
    (950_000..=1_050_000).contains(&gain).then_some(gain as u32)
}

/// Which reference drives the Ω input.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Range {
    /// R17, 10 kΩ, driven from OHM_LO; the top of it is measured on ADC_ELO.
    Low,
    /// R20, 1 MΩ, driven from OHM_HI straight from VDD.
    High,
}

impl Range {
    pub const fn reference_ohms(self) -> u64 {
        match self {
            Range::Low => 10_000,
            Range::High => 1_000_000,
        }
    }

    /// Above this fraction of the drive (per mille) the input reads as open.
    const fn open_permille(self) -> u64 {
        match self {
            Range::Low => 985,  // 657 kΩ: the high range takes over well before
            Range::High => 970, // 32 MΩ
        }
    }
}

/// Above this, with the drive pulled low, a source is connected to the Ω
/// input: a passive load reads ~0 V then.
pub const LIVE_INPUT_UV: u32 = 200_000;

/// True when something drives the Ω input. Measured with OHM_LO pulled low,
/// so a resistor or diode reads ~0 V and only a source can lift it.
pub fn input_is_live(input_uv_drive_low: u32) -> bool {
    input_uv_drive_low > LIVE_INPUT_UV
}

/// The low-range drive node cannot sag below VDD x R17 / (R17 + R16 + 40 Ω)
/// (0.906 x VDD, the probes shorted) with a passive load; lower means a
/// negative source is pulling the input down.
fn pulled_below_ground(range: Range, drive_uv: u32, vdda_uv: u32) -> bool {
    range == Range::Low && (drive_uv as u64) * 1000 < vdda_uv as u64 * 880
}

/// Resistance on the Ω input in mΩ. `drive_uv` is the top of the reference
/// (ADC_ELO in the low range, VDDA in the high range) and `input_uv` the
/// input itself (ADC_OHM).
pub fn ohms(cal: &Calibration, range: Range, vdda_uv: u32, drive_uv: u32, input_uv: u32) -> Value {
    // More on the input than we drive, or less than a short could pull the
    // drive down to: something else is powering it.
    if input_uv > drive_uv.saturating_add(30_000) || pulled_below_ground(range, drive_uv, vdda_uv) {
        return Value::Overload;
    }
    let (e, j) = (drive_uv as u64, input_uv as u64);
    if j * 1000 > e * range.open_permille() {
        return Value::Open;
    }
    let mohm = range.reference_ohms() * 1000 * j / (e - j);
    Value::Ok(mohm as i64 - cal.lead_mohm as i64)
}

/// The range to use next, given what the current one read. The two ranges
/// overlap between 150 kΩ and 200 kΩ so a reading there does not flip-flop.
pub fn next_range(range: Range, v: Value) -> Range {
    match (range, v) {
        (Range::Low, Value::Open) => Range::High,
        (Range::Low, Value::Ok(r)) if r > 200_000_000 => Range::High,
        (Range::High, Value::Ok(r)) if r < 150_000_000 => Range::Low,
        _ => range,
    }
}

/// Forward voltage on the Ω input in µV, driven through the low range.
pub fn diode(vdda_uv: u32, drive_uv: u32, input_uv: u32) -> Value {
    if input_uv > drive_uv.saturating_add(30_000) || pulled_below_ground(Range::Low, drive_uv, vdda_uv) {
        Value::Overload
    } else if input_uv > DIODE_OPEN_UV || input_uv as u64 * 1000 > drive_uv as u64 * 985 {
        Value::Open
    } else {
        Value::Ok(input_uv as i64)
    }
}

/// Switched battery voltage in µV (VSW_SENSE is half of it).
pub fn battery_uv(vdda_uv: u32, code: u32) -> u32 {
    2 * to_uv(code, vdda_uv)
}

/// Rough state of charge of a LiPo from its resting voltage.
pub fn battery_percent(uv: u32) -> u8 {
    const CURVE: [(u32, u8); 11] = [
        (3_300_000, 0),
        (3_500_000, 5),
        (3_600_000, 10),
        (3_650_000, 15),
        (3_700_000, 25),
        (3_750_000, 40),
        (3_800_000, 50),
        (3_900_000, 65),
        (4_000_000, 80),
        (4_100_000, 90),
        (4_200_000, 100),
    ];
    if uv <= CURVE[0].0 {
        return 0;
    }
    for w in CURVE.windows(2) {
        let ((v0, p0), (v1, p1)) = (w[0], w[1]);
        if uv <= v1 {
            return p0 + ((uv - v0) as u64 * (p1 - p0) as u64 / (v1 - v0) as u64) as u8;
        }
    }
    100
}

/// Die temperature in tenths of a degree, from the internal sensor.
pub fn temperature_dc(f: &Factory, vdda_uv: u32, ts_code: u32) -> i32 {
    // The calibration points were taken at VDDA = 3.0 V; rescale to that.
    let at_3v = ts_code as i64 * vdda_uv as i64 / 3_000_000;
    let (c1, c2) = (f.ts_cal1 as i64 * 16, f.ts_cal2 as i64 * 16);
    (300 + (at_3v - c1) * 1000 / (c2 - c1).max(1)) as i32
}

#[cfg(test)]
mod tests {
    use super::*;

    const F: Factory = Factory { vrefint_cal: 1671, ts_cal1: 670, ts_cal2: 897 };

    fn code(uv: u32, vdda: u32) -> u32 {
        (uv as u64 * FULL_SCALE as u64 / vdda as u64) as u32
    }

    #[test]
    fn vdda_from_vrefint() {
        assert_eq!(vdda_uv(&F, 1671 * 16), 3_000_000);
        assert!(vdda_uv(&F, 1671 * 16 * 30 / 33).abs_diff(3_300_000) < 200);
    }

    #[test]
    fn volts_round_trip() {
        let cal = Calibration::default();
        let vdda = 3_000_000;
        for vin in [-30_000_000i64, -1_000_000, 0, 1_234_000, 12_000_000, 30_000_000] {
            let mid = 1_561_000i64; // VMID_EN x R14 / (R13 + R14)
            let tap = mid + (vin - mid) * R_BOT / (R_TOP + R_BOT);
            let got = volts(&cal, vdda, code(tap as u32, vdda), code(mid as u32, vdda));
            let Value::Ok(v) = got else { panic!("{vin}: {got:?}") };
            assert!((v - vin).abs() < 5_000, "{vin} -> {v}");
        }
    }

    #[test]
    fn volts_clips() {
        let cal = Calibration::default();
        assert_eq!(volts(&cal, 3_000_000, FULL_SCALE, 32_000), Value::Overload);
        assert_eq!(volts(&cal, 3_000_000, 0, 32_000), Value::Overload);
    }

    #[test]
    fn gain_calibration() {
        let cal = Calibration { volts_offset_uv: 1_500, ..Calibration::default() };
        let g = gain_ppm(&cal, 9_950_000, 10_000_000).unwrap();
        assert_eq!(g, 1_005_024);
        // The raw reading behind 9.950 V now gives 10.000 V.
        let raw = (9_950_000 + 1_500) * 1_000_000 / cal.volts_gain_ppm as i64;
        let cal = Calibration { volts_gain_ppm: g, ..cal };
        assert!((raw * cal.volts_gain_ppm as i64 / 1_000_000 - 1_500 - 10_000_000).abs() < 20);
        assert_eq!(gain_ppm(&cal, -4_990_000, -5_000_000).map(|g| g / 1000), Some(1_007));
        assert_eq!(gain_ppm(&cal, 500_000, 510_000), None);
        assert_eq!(gain_ppm(&cal, 10_000_000, 12_000_000), None);
    }

    #[test]
    fn ohms_low_and_high() {
        let cal = Calibration::default();
        let e = 2_990_000u32;
        for rx in [0u64, 10, 1_000, 47_000, 150_000] {
            let j = (e as u64 * rx / (rx + 10_000)) as u32;
            let Value::Ok(m) = ohms(&cal, Range::Low, 3_000_000, e, j) else { panic!() };
            assert!((m - rx as i64 * 1000).abs() <= (rx as i64 + 1) * 2, "{rx} -> {m}");
        }
        let j = (3_000_000u64 * 4_700_000 / 5_700_000) as u32;
        let Value::Ok(m) = ohms(&cal, Range::High, 3_000_000, 3_000_000, j) else { panic!() };
        assert!((m - 4_700_000_000).abs() < 5_000_000, "{m}");
        assert_eq!(ohms(&cal, Range::High, 3_000_000, 3_000_000, 2_999_000), Value::Open);
        assert_eq!(ohms(&cal, Range::Low, 3_000_000, 3_000_000, 3_100_000), Value::Overload);
        // A negative source drags the drive node below what a short could.
        assert_eq!(ohms(&cal, Range::Low, 3_000_000, 2_200_000, 0), Value::Overload);
        assert!(matches!(ohms(&cal, Range::Low, 3_000_000, 2_718_000, 0), Value::Ok(_)));
        assert!(input_is_live(3_000_000) && !input_is_live(40_000));
    }

    #[test]
    fn ranges_switch_with_hysteresis() {
        assert_eq!(next_range(Range::Low, Value::Open), Range::High);
        assert_eq!(next_range(Range::Low, Value::Ok(180_000_000)), Range::Low);
        assert_eq!(next_range(Range::High, Value::Ok(180_000_000)), Range::High);
        assert_eq!(next_range(Range::High, Value::Ok(100_000_000)), Range::Low);
    }

    #[test]
    fn battery_curve() {
        assert_eq!(battery_percent(3_000_000), 0);
        assert_eq!(battery_percent(3_800_000), 50);
        assert_eq!(battery_percent(3_850_000), 57);
        assert_eq!(battery_percent(4_300_000), 100);
    }

    #[test]
    fn temperature() {
        assert_eq!(temperature_dc(&F, 3_000_000, 670 * 16), 300);
        assert_eq!(temperature_dc(&F, 3_000_000, 897 * 16), 1300);
    }
}
