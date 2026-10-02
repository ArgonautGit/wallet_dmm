//! Wallet DMM firmware for the STM32L051K8.
//!
//! Measures on a timer, draws on the OLED, and reads the buttons in their own
//! tasks. All maths and screen layout live in `dmm-core`; this file owns the
//! pins. A one-line log of every reading goes out on USART1 (TP1, 115200 8N1),
//! and USART1 RX (TP2) takes one command: `cal <volts>` with a known voltage
//! on the V input sets the voltage gain.
#![no_std]
#![no_main]

mod adc;
mod eeprom;
mod power;

use core::ptr::{read_volatile, write_volatile};

use dmm_core::format::{self, Text};
use dmm_core::frontend::{self, Calibration, Range, Value};
use dmm_core::render;
use dmm_core::ui::{Action, Button, Measurement, Mode, Press, Ui};
use embassy_executor::Spawner;
use embassy_futures::select::{select, select3, Either, Either3};
use embassy_stm32::exti::{self, ExtiInput};
use embassy_stm32::gpio::{Flex, Input, Level, Output, OutputType, Pull, Speed};
use embassy_stm32::i2c::{self, I2c};
use embassy_stm32::interrupt::InterruptExt;
use embassy_stm32::mode::Async;
use embassy_stm32::rcc::Sysclk;
use embassy_stm32::time::Hertz;
use embassy_stm32::timer::low_level::CountingMode;
use embassy_stm32::timer::low_level::OutputPolarity;
use embassy_stm32::timer::simple_pwm::{PwmPin, SimplePwm};
use embassy_stm32::usart::{self, Uart, UartTx};
use embassy_stm32::{bind_interrupts, interrupt, peripherals};
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::channel::Channel;
use embassy_time::{Instant, Timer};
use panic_halt as _;
use ssd1306::{prelude::*, I2CDisplayInterface, Ssd1306};

use adc::Adc;

bind_interrupts!(struct Irqs {
    EXTI4_15 => exti::InterruptHandler<interrupt::typelevel::EXTI4_15>;
});

static PRESSES: Channel<CriticalSectionRawMutex, (Button, Press), 4> = Channel::new();
static COMMANDS: Channel<CriticalSectionRawMutex, Command, 2> = Channel::new();
static RX_BYTES: Channel<CriticalSectionRawMutex, u8, 32> = Channel::new();

enum Command {
    /// The V input is at this many µV: set the gain to match.
    Cal(i64),
    Unknown,
}

/// Debounces one button and tells short presses from long ones.
#[embassy_executor::task(pool_size = 3)]
async fn button(mut pin: ExtiInput<'static, Async>, which: Button, long_ms: u64) {
    loop {
        pin.wait_for_falling_edge().await;
        Timer::after_millis(20).await;
        if pin.is_high() {
            continue;
        }
        let press = match select(pin.wait_for_high(), Timer::after_millis(long_ms)).await {
            Either::First(()) => Press::Short,
            Either::Second(()) => Press::Long,
        };
        PRESSES.send((which, press)).await;
        pin.wait_for_high().await;
        Timer::after_millis(20).await;
    }
}

const USART1_CR1: usize = 0x4001_3800;
const USART1_ISR: usize = 0x4001_381C;
const USART1_ICR: usize = 0x4001_3820;
const USART1_RDR: usize = 0x4001_3824;
const CR1_RXNEIE: u32 = 1 << 5;
const ISR_RXNE: u32 = 1 << 5;

/// Passes received bytes on to `commands`. Transmit stays blocking, so the
/// log never waits on an interrupt.
#[interrupt]
fn USART1() {
    unsafe {
        if read_volatile(USART1_ISR as *const u32) & ISR_RXNE != 0 {
            let _ = RX_BYTES.try_send(read_volatile(USART1_RDR as *const u32) as u8);
        }
        // Clear framing, noise and overrun errors so reception carries on.
        write_volatile(USART1_ICR as *mut u32, 0b1110);
    }
}

/// Reads command lines from USART1 RX.
#[embassy_executor::task]
async fn commands() {
    let mut line = Text::<24>::new();
    loop {
        match RX_BYTES.receive().await {
            b'\r' | b'\n' => {
                let text = line.as_str().trim();
                if !text.is_empty() {
                    let command = match text.strip_prefix("cal ").and_then(|v| format::parse_uv(v.trim())) {
                        Some(uv) => Command::Cal(uv),
                        None => Command::Unknown,
                    };
                    COMMANDS.send(command).await;
                }
                line = Text::new();
            }
            c => line.push(c),
        }
    }
}

/// The pins that set up the two inputs.
struct FrontEnd {
    vmid_en: Output<'static>,
    ohm_lo: Flex<'static>,
    ohm_hi: Flex<'static>,
}

impl FrontEnd {
    /// Pull the Ω input low through R16 + R17 to see whether a source is
    /// connected to it: a passive load reads ~0 V then.
    fn sink(&mut self) {
        self.ohm_hi.set_as_analog();
        self.ohm_lo.set_low();
        self.ohm_lo.set_as_output(Speed::Low);
    }

    /// Bias the V divider or drive one ohms reference; the other drive pin
    /// stays analog (high impedance). Returns how long the nodes need to settle.
    fn set(&mut self, mode: Mode, range: Range) -> u64 {
        let (vmid, drive) = match mode {
            Mode::Volts => (true, None),
            Mode::Ohms => (false, Some(range)),
            Mode::Continuity | Mode::Diode => (false, Some(Range::Low)),
            Mode::Info => (false, None),
        };
        self.vmid_en.set_level(if vmid { Level::High } else { Level::Low });
        for (pin, r) in [(&mut self.ohm_lo, Range::Low), (&mut self.ohm_hi, Range::High)] {
            if drive == Some(r) {
                pin.set_high();
                pin.set_as_output(Speed::Low);
            } else {
                pin.set_as_analog();
            }
        }
        match (mode, drive) {
            (Mode::Volts, _) => 150,       // 5 x (448 kΩ x 47 nF)
            (_, Some(Range::High)) => 150, // 5 x (1.05 MΩ x 22 nF)
            (_, Some(Range::Low)) => 10,   // 5 x (57 kΩ x 22 nF)
            _ => 0,
        }
    }

    fn off(&mut self) {
        self.vmid_en.set_low();
        self.ohm_lo.set_as_analog();
        self.ohm_hi.set_as_analog();
    }
}

/// Active-low LED outputs.
struct Leds {
    red: Output<'static>,
    green: Output<'static>,
    blue: Output<'static>,
}

impl Leds {
    fn set(&mut self, red: bool, green: bool, blue: bool) {
        for (led, on) in [(&mut self.red, red), (&mut self.green, green), (&mut self.blue, blue)] {
            led.set_level(if on { Level::Low } else { Level::High });
        }
    }
}

struct Log(UartTx<'static, embassy_stm32::mode::Blocking>);

impl Log {
    fn line(&mut self, parts: &[&str]) {
        for p in parts {
            let _ = self.0.blocking_write(p.as_bytes());
        }
        let _ = self.0.blocking_write(b"\r\n");
    }
}

fn number(v: i64) -> Text<21> {
    let mut t = Text::new();
    if v < 0 {
        t.push(b'-');
    }
    t.push_uint(v.unsigned_abs(), 1);
    t
}

#[embassy_executor::main]
async fn main(spawner: Spawner) {
    let mut config = embassy_stm32::Config::default();
    config.rcc.hsi = true;
    config.rcc.sys = Sysclk::HSI;
    let p = embassy_stm32::init(config);

    let mut uart_config = usart::Config::default();
    uart_config.baudrate = 115_200;
    // `_rx` lives as long as main: dropping it would turn the receiver off.
    let (tx, _rx) = Uart::new_blocking(p.USART1, p.PA10, p.PA9, uart_config).unwrap().split();
    let mut log = Log(tx);
    log.line(&["wallet-dmm rev 3"]);
    unsafe {
        write_volatile(USART1_CR1 as *mut u32, read_volatile(USART1_CR1 as *const u32) | CR1_RXNEIE);
        interrupt::USART1.unpend();
        interrupt::USART1.enable();
    }
    spawner.spawn(commands().unwrap());

    let mut fe = FrontEnd {
        vmid_en: Output::new(p.PA2, Level::Low, Speed::Low),
        ohm_lo: Flex::new(p.PA6),
        ohm_hi: Flex::new(p.PA3),
    };
    fe.off();
    let vbus = Input::new(p.PA7, Pull::None);
    let mut leds = Leds {
        red: Output::new(p.PA15, Level::High, Speed::Low),
        green: Output::new(p.PB3, Level::High, Speed::Low),
        blue: Output::new(p.PA8, Level::High, Speed::Low),
    };

    // Piezo: TIM22 CH1 and CH2 in antiphase at 4 kHz, 6 Vp-p across it.
    let mut piezo = SimplePwm::new(
        p.TIM22,
        Some(PwmPin::new(p.PB4, OutputType::PushPull)),
        Some(PwmPin::new(p.PB5, OutputType::PushPull)),
        None,
        None,
        Hertz(4_000),
        CountingMode::EdgeAlignedUp,
    );
    piezo.ch1().set_duty_cycle_percent(50);
    piezo.ch2().set_duty_cycle_percent(50);
    piezo.ch2().set_polarity(OutputPolarity::ActiveLow);

    // OLED module: power it through Q1, then give its regulator time to start.
    let mut oled_en = Output::new(p.PC15, Level::High, Speed::Low);
    Timer::after_millis(50).await;
    let mut i2c_config = i2c::Config::default();
    i2c_config.frequency = Hertz(400_000);
    let i2c = I2c::new_blocking(p.I2C1, p.PB6, p.PB7, i2c_config);
    let mut display = Ssd1306::new(I2CDisplayInterface::new(i2c), DisplaySize128x32, DisplayRotation::Rotate0)
        .into_buffered_graphics_mode();
    let display_ok = display.init().is_ok();
    if !display_ok {
        log.line(&["oled: no answer"]);
    }

    let mut adc = Adc::new();
    let factory = eeprom::factory();
    let mut cal = eeprom::load().unwrap_or_default();

    let buttons = [
        (ExtiInput::new(p.PA11, p.EXTI11, Pull::Up, Irqs), Button::A, 1500),
        (ExtiInput::new(p.PA12, p.EXTI12, Pull::Up, Irqs), Button::B, 1500),
        (ExtiInput::new(p.PC14, p.EXTI14, Pull::Up, Irqs), Button::C, 3000),
    ];
    for (pin, which, long_ms) in buttons {
        spawner.spawn(button(pin, which, long_ms).unwrap());
    }

    let mut ui = Ui::new();
    let mut range = Range::Low;
    let mut settle = fe.set(ui.mode, range);
    let mut last = Instant::now();
    let mut blink = false;

    loop {
        let period = ui.mode.period_ms(range) as u64;
        let wait = Timer::after_millis(period.max(settle));
        match select3(PRESSES.receive(), COMMANDS.receive(), wait).await {
            Either3::First((which, press)) => {
                let name = match which {
                    Button::A => "A",
                    Button::B => "B",
                    Button::C => "C",
                };
                log.line(&["button ", name, if press == Press::Long { " long" } else { "" }]);
                let before = ui.mode;
                match ui.press(which, press) {
                    Action::PowerOff => {
                        let _ = display.set_display_on(false);
                        power_off(&mut log, &mut fe, &mut leds, &mut adc, &mut piezo, &mut oled_en)
                    }
                    Action::StoreZero(mode, raw) => {
                        cal = zeroed(cal, mode, raw);
                        eeprom::store(&cal);
                        log.line(&["zero stored"]);
                    }
                    Action::None => {}
                }
                if ui.mode != before {
                    range = Range::Low;
                    settle = fe.set(ui.mode, range);
                }
                continue;
            }
            Either3::Second(Command::Cal(true_uv)) => {
                if ui.mode != Mode::Volts {
                    log.line(&["cal: switch to DC V first"]);
                    continue;
                }
                let vdda = frontend::vdda_uv(&factory, adc.average(adc::CH_VREFINT, 4));
                let tap = adc.average(adc::CH_V_TAP, 64);
                let mid = adc.average(adc::CH_VMID, 16);
                let Value::Ok(reading) = frontend::volts(&cal, vdda, tap, mid) else {
                    log.line(&["cal: overload"]);
                    continue;
                };
                match frontend::gain_ppm(&cal, reading, true_uv) {
                    Some(gain) => {
                        cal.volts_gain_ppm = gain;
                        eeprom::store(&cal);
                        log.line(&["gain stored ", number(gain as i64).as_str(), " ppm"]);
                    }
                    None => log.line(&["cal: reading ", number(reading).as_str(), " uV is too far off"]),
                }
                continue;
            }
            Either3::Second(Command::Unknown) => {
                log.line(&["commands: cal <volts on the V input>"]);
                continue;
            }
            Either3::Third(()) => settle = 0,
        }

        // One round of measurements.
        let vref = adc.average(adc::CH_VREFINT, 2);
        let vdda = frontend::vdda_uv(&factory, vref);
        let value = match ui.mode {
            Mode::Volts => {
                let tap = adc.average(adc::CH_V_TAP, 8);
                let mid = adc.average(adc::CH_VMID, 2);
                frontend::volts(&cal, vdda, tap, mid)
            }
            Mode::Ohms | Mode::Continuity | Mode::Diode => {
                let r = if ui.mode == Mode::Ohms { range } else { Range::Low };
                // First look for a source on the input with the drive pulled low
                // (5 x 1.3 ms for C13 to follow), then measure.
                fe.sink();
                Timer::after_millis(7).await;
                let live = frontend::input_is_live(frontend::to_uv(adc.average(adc::CH_OHM, 1), vdda));
                if live {
                    Value::Overload
                } else {
                    Timer::after_millis(fe.set(ui.mode, r)).await;
                    let input = frontend::to_uv(adc.average(adc::CH_OHM, 4), vdda);
                    let drive = match r {
                        Range::Low => frontend::to_uv(adc.average(adc::CH_E_LO, 2), vdda),
                        Range::High => vdda,
                    };
                    if ui.mode == Mode::Diode {
                        frontend::diode(vdda, drive, input)
                    } else {
                        frontend::ohms(&cal, r, vdda, drive, input)
                    }
                }
            }
            Mode::Info => Value::Open,
        };
        let battery_uv = frontend::battery_uv(vdda, adc.average(adc::CH_BATTERY, 1));
        let m = Measurement {
            value,
            range,
            battery_uv,
            battery_pct: frontend::battery_percent(battery_uv),
            usb: vbus.is_high(),
            temperature_dc: frontend::temperature_dc(&factory, vdda, adc.average(adc::CH_TEMPERATURE, 1)),
            vdda_uv: vdda,
        };

        // A voltage on the ohms input: stop driving it until the next check.
        if value == Value::Overload && ui.mode != Mode::Volts {
            fe.off();
        } else if ui.mode == Mode::Ohms {
            let next = frontend::next_range(range, value);
            if next != range {
                range = next;
                settle = fe.set(ui.mode, range);
            }
        }

        let now = Instant::now();
        let elapsed = (now - last).as_millis() as u32;
        last = now;
        if ui.tick(elapsed, &m) == Action::PowerOff {
            let _ = display.set_display_on(false);
            power_off(&mut log, &mut fe, &mut leds, &mut adc, &mut piezo, &mut oled_en);
        }

        let screen = ui.screen(&m);
        if display_ok {
            let _ = render::draw(&screen, &mut display);
            let _ = display.flush();
        }
        let beep = ui.beep(&m);
        if beep {
            piezo.ch1().enable();
            piezo.ch2().enable();
        } else {
            piezo.ch1().disable();
            piezo.ch2().disable();
        }
        blink = !blink;
        leds.set(value == Value::Overload && ui.mode != Mode::Volts, beep, m.usb && blink);

        let raw = match value {
            _ if ui.mode == Mode::Info => Text::from("-"),
            Value::Ok(v) => number(v),
            Value::Open => Text::from("open"),
            Value::Overload => Text::from("overload"),
        };
        log.line(&[
            ui.mode.title(),
            " ",
            raw.as_str(),
            " vdda ",
            number(vdda as i64).as_str(),
            " bat ",
            number(battery_uv as i64).as_str(),
            " temp ",
            number(m.temperature_dc as i64).as_str(),
        ]);
    }
}

/// Folds a reading taken with the probes shorted into the calibration.
fn zeroed(mut cal: Calibration, mode: Mode, raw: i64) -> Calibration {
    match mode {
        Mode::Volts => cal.volts_offset_uv += raw as i32,
        _ => cal.lead_mohm = (cal.lead_mohm as i64 + raw).max(0) as u32,
    }
    cal
}

fn power_off(
    log: &mut Log,
    fe: &mut FrontEnd,
    leds: &mut Leds,
    adc: &mut Adc,
    piezo: &mut SimplePwm<'static, peripherals::TIM22>,
    oled_en: &mut Output<'static>,
) -> ! {
    log.line(&["off"]);
    let _ = log.0.blocking_flush();
    fe.off();
    leds.set(false, false, false);
    piezo.ch1().disable();
    piezo.ch2().disable();
    adc.off();
    // Cut the module's supply, and stop the I2C pins feeding it through its pull-ups.
    oled_en.set_low();
    power::i2c_pins_analog();
    power::stop_until_button()
}
