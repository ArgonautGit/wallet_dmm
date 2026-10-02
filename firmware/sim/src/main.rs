//! Runs the firmware's measurement code on simulated ADC codes.
//!
//! `spice.py` leaves the voltages at the ADC pins in `out/*.csv`. This models
//! the STM32L051 ADC (quantisation, offset and gain error, INL, noise, 16x
//! hardware oversampling, a VDDA that is not exactly 3.0 V) and averages the
//! way the firmware does, then converts with `dmm_core` exactly as the
//! firmware would. `readings_*.csv` go back into `out/` for `plots.py`, and
//! `screens/*.png` shows what the display draws.
//!
//!     cargo run -p dmm-sim --release -- out

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use dmm_core::frontend::{self, Calibration, Factory, Range, Value};
use dmm_core::render;
use dmm_core::ui::{Button, Measurement, Mode, Press, Ui};
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;

/// VDDA on the simulated board: the XC6206 is 1 % low.
const VDDA: f64 = 2.97;
/// VREFINT, and the calibration value ST would have stored for it at 3.0 V.
const VREFINT: f64 = 1.2242;
const FACTORY: Factory = Factory { vrefint_cal: 1671, ts_cal1: 670, ts_cal2: 890 };

/// The ADC of one simulated part.
struct Adc {
    rng: u64,
    offset_lsb: f64,
    gain: f64,
    noise_lsb: f64,
}

impl Adc {
    fn new(seed: u64) -> Self {
        Self { rng: seed | 1, offset_lsb: 0.6, gain: 1.0008, noise_lsb: 0.8 }
    }

    fn uniform(&mut self) -> f64 {
        // xorshift64*
        self.rng ^= self.rng >> 12;
        self.rng ^= self.rng << 25;
        self.rng ^= self.rng >> 27;
        (self.rng.wrapping_mul(0x2545_F491_4F6C_DD1D) >> 11) as f64 / (1u64 << 53) as f64
    }

    fn gauss(&mut self) -> f64 {
        let (u1, u2) = (self.uniform().max(1e-12), self.uniform());
        (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
    }

    /// One 12-bit sample: offset, gain, a bow of INL, noise, quantisation.
    fn sample(&mut self, volts: f64) -> u32 {
        let ideal = volts / VDDA * 4096.0;
        let inl = 1.0 * (std::f64::consts::PI * ideal / 4096.0).sin();
        let code = ideal * self.gain + self.offset_lsb + inl + self.noise_lsb * self.gauss();
        code.round().clamp(0.0, 4095.0) as u32
    }

    /// One conversion with OVSR = 16x, OVSS = 0: the sum of 16 samples.
    fn convert(&mut self, volts: f64) -> u32 {
        (0..16).map(|_| self.sample(volts)).sum()
    }

    /// `Adc::average` in the firmware: a thrown-away conversion, then the mean of n.
    fn average(&mut self, volts: f64, n: u32) -> u32 {
        self.convert(volts);
        (0..n).map(|_| self.convert(volts)).sum::<u32>() / n
    }

    fn vdda_uv(&mut self) -> u32 {
        frontend::vdda_uv(&FACTORY, self.average(VREFINT, 2))
    }
}

/// A CSV file as a list of column -> value maps.
fn read_csv(path: &Path) -> Vec<BTreeMap<String, String>> {
    let text = fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let mut lines = text.lines();
    let head: Vec<String> = lines.next().unwrap().split(',').map(str::to_string).collect();
    lines.map(|l| head.iter().cloned().zip(l.split(',').map(str::to_string)).collect()).collect()
}

fn num(row: &BTreeMap<String, String>, key: &str) -> f64 {
    row[key].parse().unwrap_or_else(|_| panic!("{key}: {}", row[key]))
}

fn write_csv(path: &Path, head: &str, rows: &[String]) {
    fs::write(path, format!("{head}\n{}\n", rows.join("\n"))).unwrap();
    println!("{}: {} rows", path.display(), rows.len());
}

fn value(v: Value) -> String {
    match v {
        Value::Ok(x) => x.to_string(),
        Value::Open => "open".into(),
        Value::Overload => "overload".into(),
    }
}

fn read_volts(adc: &mut Adc, cal: &Calibration, tap: f64, vmid: f64) -> Value {
    let vdda = adc.vdda_uv();
    let tap = adc.average(tap, 8);
    let mid = adc.average(vmid, 2);
    frontend::volts(cal, vdda, tap, mid)
}

fn read_ohms(adc: &mut Adc, cal: &Calibration, range: Range, elo: f64, input: f64) -> Value {
    let vdda = adc.vdda_uv();
    let j = frontend::to_uv(adc.average(input, 4), vdda);
    let e = match range {
        Range::Low => frontend::to_uv(adc.average(elo, 2), vdda),
        Range::High => vdda,
    };
    frontend::ohms(cal, range, vdda, e, j)
}

/// Calibration from two points: 0 V and 10 V on the V input, and the probes shorted.
fn calibrate(adc: &mut Adc, zero: (f64, f64), ten: (f64, f64), short: (f64, f64)) -> Calibration {
    let mut cal = Calibration::default();
    let Value::Ok(z) = settled(|| read_volts(adc, &cal, zero.0, zero.1)) else { panic!("zero") };
    let Value::Ok(t) = settled(|| read_volts(adc, &cal, ten.0, ten.1)) else { panic!("ten") };
    cal.volts_gain_ppm = (10_000_000i64 * 1_000_000 / (t - z)) as u32;
    let Value::Ok(z) = settled(|| read_volts(adc, &cal, zero.0, zero.1)) else { panic!("zero") };
    cal.volts_offset_uv = z as i32;
    let Value::Ok(lead) = settled(|| read_ohms(adc, &cal, Range::Low, short.0, short.1)) else { panic!("short") };
    cal.lead_mohm = lead.max(0) as u32;
    cal
}

fn sweeps(out: &Path) {
    let cal = Calibration::default();
    for tag in ["", "_leak50n"] {
        let mut adc = Adc::new(7);
        let rows: Vec<String> = read_csv(&out.join(format!("volts{tag}.csv")))
            .iter()
            .map(|r| {
                let v = read_volts(&mut adc, &cal, num(r, "tap"), num(r, "vmid"));
                format!("{},{}", r["vin"], value(v))
            })
            .collect();
        write_csv(&out.join(format!("readings_volts{tag}.csv")), "vin,reading_uv", &rows);

        // Ohms as the firmware runs it: start in the low range, autorange,
        // and report what it settles on.
        let table = read_csv(&out.join(format!("ohms{tag}.csv")));
        let mut by_rx: BTreeMap<String, [(f64, f64); 2]> = BTreeMap::new();
        for r in &table {
            let e = by_rx.entry(r["rx"].clone()).or_insert([(0.0, 0.0); 2]);
            e[(r["range"] == "hi") as usize] = (num(r, "elo"), num(r, "input"));
        }
        let mut adc = Adc::new(11);
        let mut rows = Vec::new();
        for (rx, nodes) in &by_rx {
            let mut range = Range::Low;
            let mut v = Value::Open;
            for _ in 0..3 {
                let (elo, input) = nodes[(range == Range::High) as usize];
                v = read_ohms(&mut adc, &cal, range, elo, input);
                let next = frontend::next_range(range, v);
                if next == range {
                    break;
                }
                range = next;
            }
            let lo = read_ohms(&mut adc, &cal, Range::Low, nodes[0].0, nodes[0].1);
            let hi = read_ohms(&mut adc, &cal, Range::High, nodes[1].0, nodes[1].1);
            rows.push(format!(
                "{rx},{},{},{},{}",
                value(v),
                if range == Range::Low { "lo" } else { "hi" },
                value(lo),
                value(hi)
            ));
        }
        write_csv(&out.join(format!("readings_ohms{tag}.csv")), "rx,auto_mohm,range,low_mohm,high_mohm", &rows);
    }

    let mut adc = Adc::new(5);
    let rows: Vec<String> = read_csv(&out.join("diode.csv"))
        .iter()
        .map(|r| {
            let vdda = adc.vdda_uv();
            let j = frontend::to_uv(adc.average(num(r, "input"), 4), vdda);
            let e = frontend::to_uv(adc.average(num(r, "elo"), 2), vdda);
            format!("{},{},{}", r["part"], r["vf"], value(frontend::diode(vdda, e, j)))
        })
        .collect();
    write_csv(&out.join("readings_diode.csv"), "part,vf_true,reading_uv", &rows);
}

/// What the display settles on: the UI filter averages 8 readings of a
/// steady input.
fn settled(mut read: impl FnMut() -> Value) -> Value {
    let mut sum = 0i64;
    for _ in 0..8 {
        match read() {
            Value::Ok(v) => sum += v,
            other => return other,
        }
    }
    Value::Ok(sum / 8)
}

/// Every simulated board: resistor tolerances from spice.py, and its own ADC.
fn monte_carlo(out: &Path) {
    let volts = read_csv(&out.join("mc_volts.csv"));
    let ohms = read_csv(&out.join("mc_ohms.csv"));
    let samples = volts.iter().map(|r| num(r, "sample") as u64).max().unwrap() + 1;
    let (mut vrows, mut orows) = (Vec::new(), Vec::new());
    for s in 0..samples {
        let mut adc = Adc::new(1000 + s);
        // Every part gets its own ADC offset and gain error.
        adc.offset_lsb = 2.0 * adc.uniform() - 1.0;
        adc.gain = 1.0 + (adc.uniform() - 0.5) * 0.004;
        let v: Vec<_> = volts.iter().filter(|r| num(r, "sample") as u64 == s).collect();
        let o: Vec<_> = ohms.iter().filter(|r| num(r, "sample") as u64 == s).collect();
        let at = |vin: f64| v.iter().find(|r| num(r, "vin") == vin).map(|r| (num(r, "tap"), num(r, "vmid"))).unwrap();
        let short = o.iter().find(|r| num(r, "rx") == 0.0 && r["range"] == "lo").unwrap();
        let cal = calibrate(&mut adc, at(0.0), at(10.0), (num(short, "elo"), num(short, "input")));
        for r in &v {
            let raw = settled(|| read_volts(&mut adc, &Calibration::default(), num(r, "tap"), num(r, "vmid")));
            let fixed = settled(|| read_volts(&mut adc, &cal, num(r, "tap"), num(r, "vmid")));
            vrows.push(format!("{s},{},{},{}", r["vin"], value(raw), value(fixed)));
        }
        for r in &o {
            let range = if r["range"] == "lo" { Range::Low } else { Range::High };
            let raw = settled(|| read_ohms(&mut adc, &Calibration::default(), range, num(r, "elo"), num(r, "input")));
            let fixed = settled(|| read_ohms(&mut adc, &cal, range, num(r, "elo"), num(r, "input")));
            orows.push(format!("{s},{},{},{},{}", r["rx"], r["range"], value(raw), value(fixed)));
        }
    }
    write_csv(&out.join("readings_mc_volts.csv"), "sample,vin,uncal_uv,cal_uv", &vrows);
    write_csv(&out.join("readings_mc_ohms.csv"), "sample,rx,range,uncal_mohm,cal_mohm", &orows);
}

/// A 128 x 32 frame buffer that saves itself as a PNG, OLED blue on black.
struct Frame([[bool; 128]; 32]);

impl OriginDimensions for Frame {
    fn size(&self) -> Size {
        Size::new(render::WIDTH, render::HEIGHT)
    }
}

impl DrawTarget for Frame {
    type Color = BinaryColor;
    type Error = core::convert::Infallible;
    fn draw_iter<I: IntoIterator<Item = Pixel<BinaryColor>>>(&mut self, pixels: I) -> Result<(), Self::Error> {
        for Pixel(p, c) in pixels {
            if (0..128).contains(&p.x) && (0..32).contains(&p.y) {
                self.0[p.y as usize][p.x as usize] = c.is_on();
            }
        }
        Ok(())
    }
}

/// Writes a frame as a PNG, `scale` pixels per OLED pixel with a small gap.
pub fn save_png(pixels: &[[bool; 128]; 32], path: &Path, scale: u32) {
    let (w, h) = (128 * scale + 2 * scale, 32 * scale + 2 * scale);
    let mut data = vec![0u8; (w * h * 3) as usize];
    for (i, px) in data.chunks_mut(3).enumerate() {
        px.copy_from_slice(&[8, 10, 14]);
        let (x, y) = (i as u32 % w, i as u32 / w);
        let (cx, cy) = (x.wrapping_sub(scale) / scale, y.wrapping_sub(scale) / scale);
        let inside = x >= scale && y >= scale && cx < 128 && cy < 32;
        let edge = (x - scale.min(x)) % scale == scale - 1 || (y - scale.min(y)) % scale == scale - 1;
        if inside && pixels[cy as usize][cx as usize] && !(edge && scale > 3) {
            px.copy_from_slice(&[120, 200, 255]);
        }
    }
    let file = fs::File::create(path).unwrap();
    let mut enc = png::Encoder::new(std::io::BufWriter::new(file), w, h);
    enc.set_color(png::ColorType::Rgb);
    enc.set_depth(png::BitDepth::Eight);
    enc.write_header().unwrap().write_image_data(&data).unwrap();
}

/// A screen to draw: name, mode, reading, range, relative, and the presses before it.
type Case = (&'static str, Mode, Value, Range, bool, &'static [(Button, Press)]);

/// What the display shows in each mode, drawn by the firmware's renderer.
fn screens(out: &Path) {
    let dir = out.join("screens");
    fs::create_dir_all(&dir).unwrap();
    let base = Measurement {
        value: Value::Open,
        range: Range::Low,
        battery_uv: 3_910_000,
        battery_pct: frontend::battery_percent(3_910_000),
        usb: false,
        temperature_dc: 247,
        vdda_uv: 2_970_000,
    };
    let cases: [Case; 10] = [
        ("volts", Mode::Volts, Value::Ok(12_346_000), Range::Low, false, &[]),
        ("volts_negative", Mode::Volts, Value::Ok(-3_302_000), Range::Low, false, &[]),
        ("volts_hold", Mode::Volts, Value::Ok(4_998_000), Range::Low, false, &[(Button::B, Press::Short)]),
        ("volts_overload", Mode::Volts, Value::Overload, Range::Low, false, &[]),
        ("ohms_low", Mode::Ohms, Value::Ok(4_702_300), Range::Low, false, &[]),
        ("ohms_high", Mode::Ohms, Value::Ok(4_690_000_000), Range::High, false, &[]),
        ("continuity", Mode::Continuity, Value::Ok(300), Range::Low, false, &[]),
        ("ohms_voltage", Mode::Ohms, Value::Overload, Range::Low, false, &[]),
        ("diode", Mode::Diode, Value::Ok(517_000), Range::Low, false, &[]),
        ("info", Mode::Info, Value::Open, Range::Low, true, &[]),
    ];
    for (name, mode, v, range, usb, presses) in cases {
        let mut ui = Ui::new();
        while ui.mode != mode {
            ui.press(Button::A, Press::Short);
        }
        let m = Measurement { value: v, range, usb, ..base };
        ui.screen(&m);
        for &(b, p) in presses {
            ui.press(b, p);
        }
        let screen = ui.screen(&m);
        let mut frame = Frame([[false; 128]; 32]);
        render::draw(&screen, &mut frame).unwrap();
        save_png(&frame.0, &dir.join(format!("{name}.png")), 4);
        // Also as rows of 0/1, for the OLED in the 3D model (scripts/gen_models.py).
        let bits: String = frame
            .0
            .iter()
            .map(|row| row.iter().map(|&p| if p { '1' } else { '0' }).collect::<String>() + "\n")
            .collect();
        fs::write(dir.join(format!("{name}.txt")), bits).unwrap();
    }
    println!("{}: {} screens", dir.display(), cases.len());
}

fn main() {
    let out: PathBuf = std::env::args().nth(1).unwrap_or_else(|| "out".into()).into();
    sweeps(&out);
    monte_carlo(&out);
    screens(&out);
}
