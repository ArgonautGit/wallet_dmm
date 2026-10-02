//! User calibration in the MCU's data EEPROM, plus the factory calibration in
//! system memory.

use core::ptr::{read_volatile, write_volatile};

use dmm_core::frontend::{Calibration, Factory};

const FLASH: usize = 0x4002_2000;
const PECR: usize = FLASH + 0x04;
const PEKEYR: usize = FLASH + 0x0C;
const SR: usize = FLASH + 0x18;
const PECR_PELOCK: u32 = 1 << 0;
const SR_BSY: u32 = 1 << 0;

const EEPROM: usize = 0x0808_0000;
const MAGIC: u32 = 0x444D_4D33; // "DMM3"

pub fn factory() -> Factory {
    let rd = |addr: usize| unsafe { read_volatile(addr as *const u16) };
    Factory { vrefint_cal: rd(0x1FF8_0078), ts_cal1: rd(0x1FF8_007A), ts_cal2: rd(0x1FF8_007E) }
}

fn word(i: usize) -> u32 {
    unsafe { read_volatile((EEPROM + 4 * i) as *const u32) }
}

pub fn load() -> Option<Calibration> {
    (word(0) == MAGIC).then(|| Calibration {
        volts_offset_uv: word(1) as i32,
        volts_gain_ppm: word(2),
        lead_mohm: word(3),
    })
}

pub fn store(cal: &Calibration) {
    unsafe {
        if read_volatile(PECR as *const u32) & PECR_PELOCK != 0 {
            write_volatile(PEKEYR as *mut u32, 0x89AB_CDEF);
            write_volatile(PEKEYR as *mut u32, 0x0203_0405);
        }
        let words = [MAGIC, cal.volts_offset_uv as u32, cal.volts_gain_ppm, cal.lead_mohm];
        for (i, w) in words.into_iter().enumerate() {
            if word(i) != w {
                write_volatile((EEPROM + 4 * i) as *mut u32, w);
                while read_volatile(SR as *const u32) & SR_BSY != 0 {}
            }
        }
        write_volatile(PECR as *mut u32, read_volatile(PECR as *const u32) | PECR_PELOCK);
    }
}
