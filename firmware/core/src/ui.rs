//! Modes, buttons and what the screen shows.
//!
//! Buttons: A cycles the mode (hold 1.5 s to turn off), B holds the reading,
//! C shows readings relative to the current one (hold 3 s with the probes
//! shorted to store the zero in EEPROM).

use crate::format::{self, Number, Text};
use crate::frontend::{Range, Value, CONTINUITY_MOHM};

/// Turn off after this long without a button press, unless on USB.
pub const AUTO_OFF_MS: u32 = 5 * 60 * 1000;
/// Turn off below this battery voltage, µV.
pub const LOW_BATTERY_UV: u32 = 3_350_000;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    Volts,
    Ohms,
    Continuity,
    Diode,
    Info,
}

impl Mode {
    pub fn next(self) -> Self {
        match self {
            Mode::Volts => Mode::Ohms,
            Mode::Ohms => Mode::Continuity,
            Mode::Continuity => Mode::Diode,
            Mode::Diode => Mode::Info,
            Mode::Info => Mode::Volts,
        }
    }

    pub fn title(self) -> &'static str {
        match self {
            Mode::Volts => "DC V",
            Mode::Ohms => "OHMS",
            Mode::Continuity => "CONT",
            Mode::Diode => "DIODE",
            Mode::Info => "INFO",
        }
    }

    /// Milliseconds between readings. The hold capacitors settle in 5 time
    /// constants: 105 ms for the V divider, 115 ms for the 1 MΩ range, 5 ms
    /// for the 10 kΩ range.
    pub fn period_ms(self, _range: Range) -> u32 {
        match self {
            Mode::Continuity => 30,
            Mode::Info => 1000,
            _ => 200,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Button {
    A,
    B,
    C,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Press {
    Short,
    Long,
}

/// What the firmware has to do in response.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Action {
    None,
    PowerOff,
    /// Store the current raw reading as the zero for this mode.
    StoreZero(Mode, i64),
}

/// One round of measurements, already converted by `frontend`.
#[derive(Clone, Copy, Debug)]
pub struct Measurement {
    /// The reading for the current mode (µV or mΩ); unused in Info.
    pub value: Value,
    pub range: Range,
    pub battery_uv: u32,
    pub battery_pct: u8,
    pub usb: bool,
    pub temperature_dc: i32,
    pub vdda_uv: u32,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Main {
    Reading { value: Number, unit: &'static str },
    Message(&'static str),
    Lines(Text<22>, Text<22>),
}

/// Everything the display shows.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Screen {
    pub title: &'static str,
    pub hold: bool,
    pub relative: bool,
    /// "10k" or "1M" in the resistance modes.
    pub range: Option<&'static str>,
    pub battery_pct: u8,
    pub usb: bool,
    pub main: Main,
}

/// Averages up to 8 readings while the input holds still, and starts over
/// when it moves, so the last digit settles without slowing real changes.
#[derive(Clone, Copy)]
struct Filter {
    buf: [i64; 8],
    len: usize,
    next: usize,
}

impl Filter {
    const fn new() -> Self {
        Self { buf: [0; 8], len: 0, next: 0 }
    }

    fn clear(&mut self) {
        self.len = 0;
    }

    fn mean(&self) -> Option<i64> {
        (self.len > 0).then(|| self.buf[..self.len].iter().sum::<i64>() / self.len as i64)
    }

    /// Adds a reading; a jump of more than `step` plus 0.5 % restarts the average.
    fn push(&mut self, v: i64, step: i64) -> i64 {
        if let Some(m) = self.mean() {
            if (v - m).abs() > step + m.abs() / 200 {
                self.clear();
            }
        }
        if self.len == 0 {
            self.next = 0;
        }
        self.buf[self.next] = v;
        self.next = (self.next + 1) % self.buf.len();
        self.len = (self.len + 1).min(self.buf.len());
        self.mean().unwrap_or(v)
    }
}

pub struct Ui {
    pub mode: Mode,
    held: Option<Screen>,
    relative: Option<i64>,
    relative_pending: bool,
    filter: Filter,
    last_raw: Option<i64>,
    idle_ms: u32,
}

impl Default for Ui {
    fn default() -> Self {
        Self::new()
    }
}

impl Ui {
    pub const fn new() -> Self {
        Self {
            mode: Mode::Volts,
            held: None,
            relative: None,
            relative_pending: false,
            filter: Filter::new(),
            last_raw: None,
            idle_ms: 0,
        }
    }

    pub fn press(&mut self, button: Button, press: Press) -> Action {
        self.idle_ms = 0;
        match (button, press) {
            (Button::A, Press::Short) => {
                self.mode = self.mode.next();
                self.held = None;
                self.relative = None;
                self.last_raw = None;
                self.filter.clear();
            }
            (Button::A, Press::Long) => return Action::PowerOff,
            (Button::B, _) => {
                // Toggled in `screen`, so hold captures what is on the display.
                if self.held.is_some() {
                    self.held = None;
                } else {
                    self.held = Some(HOLD_PENDING);
                }
            }
            (Button::C, Press::Short) => {
                if self.relative.is_some() {
                    self.relative = None;
                } else {
                    self.relative_pending = true;
                }
            }
            (Button::C, Press::Long) => {
                if let (Some(raw), Mode::Volts | Mode::Ohms | Mode::Continuity) = (self.last_raw, self.mode) {
                    self.relative = None;
                    return Action::StoreZero(self.mode, raw);
                }
            }
        }
        Action::None
    }

    /// Time passes; returns PowerOff when idle too long or the battery is flat.
    pub fn tick(&mut self, elapsed_ms: u32, m: &Measurement) -> Action {
        self.idle_ms = self.idle_ms.saturating_add(elapsed_ms);
        let flat = m.battery_uv > 1_000_000 && m.battery_uv < LOW_BATTERY_UV && !m.usb;
        if flat || (self.idle_ms > AUTO_OFF_MS && !m.usb) {
            Action::PowerOff
        } else {
            Action::None
        }
    }

    /// The continuity beeper: on while the probes are joined.
    pub fn beep(&self, m: &Measurement) -> bool {
        self.mode == Mode::Continuity && self.held.is_none() && matches!(m.value, Value::Ok(r) if r < CONTINUITY_MOHM)
    }

    pub fn screen(&mut self, m: &Measurement) -> Screen {
        if let Some(held) = self.held {
            if held != HOLD_PENDING {
                return Screen { battery_pct: m.battery_pct, usb: m.usb, ..held };
            }
        }
        // Filtered: 20 mV or 2 Ω plus 0.5 % counts as the input moving.
        let step = if self.mode == Mode::Volts || self.mode == Mode::Diode { 20_000 } else { 2_000 };
        let raw = match m.value {
            Value::Ok(v) => Some(self.filter.push(v, step)),
            _ => {
                self.filter.clear();
                None
            }
        };
        self.last_raw = raw;
        if self.relative_pending {
            self.relative_pending = false;
            self.relative = raw;
        }
        let shown = match (raw, self.relative) {
            (Some(v), Some(zero)) => Value::Ok(v - zero),
            (Some(v), None) => Value::Ok(v),
            (None, _) => m.value,
        };
        let range = match (self.mode, m.range) {
            (Mode::Ohms, Range::Low) => Some("10k"),
            (Mode::Ohms, Range::High) => Some("1M"),
            _ => None,
        };
        let main = match self.mode {
            Mode::Volts => match shown {
                Value::Ok(uv) => reading(format::volts(uv)),
                _ => Main::Message("OL"),
            },
            Mode::Ohms | Mode::Continuity => match shown {
                Value::Ok(mohm) if self.mode == Mode::Continuity && mohm > 999_900 => Main::Message("OPEN"),
                Value::Ok(mohm) => reading(format::ohms(mohm)),
                Value::Open if self.mode == Mode::Continuity => Main::Message("OPEN"),
                Value::Open => Main::Message("OL"),
                Value::Overload => Main::Message("VOLTS!"),
            },
            Mode::Diode => match shown {
                Value::Ok(uv) => reading(format::volts(uv)),
                Value::Open => Main::Message("OL"),
                Value::Overload => Main::Message("VOLTS!"),
            },
            Mode::Info => info(m),
        };
        let screen = Screen {
            title: self.mode.title(),
            hold: false,
            relative: self.relative.is_some(),
            range,
            battery_pct: m.battery_pct,
            usb: m.usb,
            main,
        };
        if self.held == Some(HOLD_PENDING) {
            let held = Screen { hold: true, ..screen };
            self.held = Some(held);
            return held;
        }
        screen
    }
}

/// Marks "hold pressed, capture the next screen".
const HOLD_PENDING: Screen =
    Screen { title: "", hold: true, relative: false, range: None, battery_pct: 0, usb: false, main: Main::Message("") };

fn reading((value, unit): (Number, &'static str)) -> Main {
    Main::Reading { value, unit }
}

fn info(m: &Measurement) -> Main {
    let mut a = Text::new();
    a.push_str("BAT ");
    a.push_fixed(m.battery_uv as i64, 6, 2);
    a.push_str("V ");
    a.push_uint(m.battery_pct as u64, 1);
    a.push_str("%");
    if m.usb {
        a.push_str(" USB");
    }
    let mut b = Text::new();
    b.push_fixed(m.temperature_dc as i64, 1, 1);
    b.push_str("°C  VDD ");
    b.push_fixed(m.vdda_uv as i64, 6, 2);
    b.push_str("V");
    Main::Lines(a, b)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn m(value: Value) -> Measurement {
        Measurement {
            value,
            range: Range::Low,
            battery_uv: 3_900_000,
            battery_pct: 65,
            usb: false,
            temperature_dc: 245,
            vdda_uv: 3_000_000,
        }
    }

    #[test]
    fn modes_cycle() {
        let mut ui = Ui::new();
        for expect in [Mode::Ohms, Mode::Continuity, Mode::Diode, Mode::Info, Mode::Volts] {
            ui.press(Button::A, Press::Short);
            assert_eq!(ui.mode, expect);
        }
        assert_eq!(ui.press(Button::A, Press::Long), Action::PowerOff);
    }

    #[test]
    fn hold_freezes_the_reading() {
        let mut ui = Ui::new();
        ui.screen(&m(Value::Ok(1_000_000)));
        ui.press(Button::B, Press::Short);
        let s = ui.screen(&m(Value::Ok(2_000_000)));
        assert!(s.hold);
        let s = ui.screen(&m(Value::Ok(5_000_000)));
        assert_eq!(s.main, Main::Reading { value: Number::from("2.000"), unit: "V" });
        ui.press(Button::B, Press::Short);
        let s = ui.screen(&m(Value::Ok(5_000_000)));
        assert!(!s.hold);
    }

    #[test]
    fn relative_subtracts() {
        let mut ui = Ui::new();
        ui.press(Button::C, Press::Short);
        ui.screen(&m(Value::Ok(1_000_000)));
        let s = ui.screen(&m(Value::Ok(1_250_000)));
        assert!(s.relative);
        assert_eq!(s.main, Main::Reading { value: Number::from("0.250"), unit: "V" });
    }

    #[test]
    fn continuity_beeps_below_threshold() {
        let mut ui = Ui::new();
        ui.mode = Mode::Continuity;
        assert!(ui.beep(&m(Value::Ok(12_000))));
        assert!(!ui.beep(&m(Value::Ok(80_000))));
        assert_eq!(ui.screen(&m(Value::Open)).main, Main::Message("OPEN"));
    }

    #[test]
    fn store_zero_uses_the_filtered_reading() {
        let mut ui = Ui::new();
        for v in [3_000, -1_000, 2_000, 0] {
            ui.screen(&m(Value::Ok(v)));
        }
        assert_eq!(ui.press(Button::C, Press::Long), Action::StoreZero(Mode::Volts, 1_000));
    }

    #[test]
    fn filter_follows_real_changes_at_once() {
        let mut ui = Ui::new();
        for v in [1_000_000, 1_002_000, 998_000] {
            ui.screen(&m(Value::Ok(v)));
        }
        let s = ui.screen(&m(Value::Ok(5_000_000)));
        assert_eq!(s.main, Main::Reading { value: Number::from("5.000"), unit: "V" });
    }

    #[test]
    fn turns_off_when_idle_or_flat() {
        let mut ui = Ui::new();
        assert_eq!(ui.tick(1000, &m(Value::Open)), Action::None);
        assert_eq!(ui.tick(AUTO_OFF_MS, &m(Value::Open)), Action::PowerOff);
        let mut ui = Ui::new();
        let flat = Measurement { battery_uv: 3_300_000, ..m(Value::Open) };
        assert_eq!(ui.tick(10, &flat), Action::PowerOff);
    }
}
