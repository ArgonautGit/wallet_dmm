//! The STM32L0 ADC, driven through its registers so the hardware oversampler
//! (16 samples summed into one 16-bit result) and the internal channels are
//! available. Channels: 0 E_LO, 1 VMID, 4 V divider, 5 Ω input, 9 battery / 2,
//! 17 VREFINT, 18 temperature.

use core::ptr::{read_volatile, write_volatile};

const RCC_APB2ENR: *mut u32 = 0x4002_1034 as *mut u32;
const ADC: usize = 0x4001_2400;
const ISR: usize = 0x00;
const CR: usize = 0x08;
const CFGR1: usize = 0x0C;
const CFGR2: usize = 0x10;
const SMPR: usize = 0x14;
const CHSELR: usize = 0x28;
const DR: usize = 0x40;
const CCR: usize = 0x308;

const ISR_ADRDY: u32 = 1 << 0;
const ISR_EOC: u32 = 1 << 2;
const CR_ADEN: u32 = 1 << 0;
const CR_ADDIS: u32 = 1 << 1;
const CR_ADSTART: u32 = 1 << 2;
const CR_ADVREGEN: u32 = 1 << 28;
const CR_ADCAL: u32 = 1 << 31;
const CFGR1_OVRMOD: u32 = 1 << 12;
const CFGR2_OVSE: u32 = 1 << 0;
const CFGR2_OVSR_16X: u32 = 0b011 << 2;
const SMPR_160_5_CYCLES: u32 = 0b111;
const CCR_VREFEN: u32 = 1 << 22;
const CCR_TSEN: u32 = 1 << 23;

pub const CH_E_LO: u32 = 0;
pub const CH_VMID: u32 = 1;
pub const CH_V_TAP: u32 = 4;
pub const CH_OHM: u32 = 5;
pub const CH_BATTERY: u32 = 9;
pub const CH_VREFINT: u32 = 17;
pub const CH_TEMPERATURE: u32 = 18;

fn reg(offset: usize) -> *mut u32 {
    (ADC + offset) as *mut u32
}

fn read(offset: usize) -> u32 {
    unsafe { read_volatile(reg(offset)) }
}

fn write(offset: usize, value: u32) {
    unsafe { write_volatile(reg(offset), value) }
}

fn modify(offset: usize, f: impl FnOnce(u32) -> u32) {
    write(offset, f(read(offset)));
}

pub struct Adc(());

impl Adc {
    /// Powers up, calibrates and enables the ADC. The kernel clock is the
    /// asynchronous HSI16 (CKMODE = 0), which the system clock already uses.
    pub fn new() -> Self {
        unsafe { write_volatile(RCC_APB2ENR, read_volatile(RCC_APB2ENR) | 1 << 9) };
        write(CFGR2, CFGR2_OVSE | CFGR2_OVSR_16X);
        write(CFGR1, CFGR1_OVRMOD);
        write(SMPR, SMPR_160_5_CYCLES);
        write(CR, CR_ADVREGEN);
        cortex_m::asm::delay(400); // t_ADCVREG_STUP, 10 µs
        modify(CR, |v| v | CR_ADCAL);
        while read(CR) & CR_ADCAL != 0 {}
        write(ISR, ISR_ADRDY);
        modify(CR, |v| v | CR_ADEN);
        while read(ISR) & ISR_ADRDY == 0 {}
        modify(CCR, |v| v | CCR_VREFEN | CCR_TSEN);
        cortex_m::asm::delay(160); // VREFINT and sensor start-up
        Adc(())
    }

    /// One oversampled conversion: the sum of 16 samples, 0..=65520.
    pub fn convert(&mut self, channel: u32) -> u32 {
        write(CHSELR, 1 << channel);
        modify(CR, |v| v | CR_ADSTART);
        while read(ISR) & ISR_EOC == 0 {}
        read(DR) & 0xFFFF
    }

    /// Mean of `n` oversampled conversions.
    pub fn average(&mut self, channel: u32, n: u32) -> u32 {
        // The first conversion after switching channels charges the sampling
        // capacitor from the previous channel's voltage; throw it away.
        self.convert(channel);
        (0..n).map(|_| self.convert(channel)).sum::<u32>() / n
    }

    /// Disables the ADC, its regulator and the internal channels.
    pub fn off(&mut self) {
        modify(CCR, |v| v & !(CCR_VREFEN | CCR_TSEN));
        modify(CR, |v| v | CR_ADDIS);
        while read(CR) & CR_ADEN != 0 {}
        modify(CR, |v| v & !CR_ADVREGEN);
    }
}
