//! Off is Stop mode: the 3.0 V rail stays up, the MCU draws about 0.4 µA, and
//! any button brings it back through a reset.

use core::ptr::{read_volatile, write_volatile};

use cortex_m::peripheral::{NVIC, SCB};
use embassy_stm32::interrupt;

const GPIOA_IDR: usize = 0x5000_0010;
const GPIOB_MODER: usize = 0x5000_0400;
const GPIOC_IDR: usize = 0x5000_0810;
const EXTI_IMR: usize = 0x4001_0400;
const EXTI_RTSR: usize = 0x4001_0408;
const EXTI_FTSR: usize = 0x4001_040C;
const EXTI_PR: usize = 0x4001_0414;
const RCC_APB1ENR: usize = 0x4002_1038;
const PWR_CR: usize = 0x4000_7000;

/// EXTI lines of the buttons: PA11, PA12, PC14.
const BUTTON_LINES: u32 = 1 << 11 | 1 << 12 | 1 << 14;

unsafe fn rd(addr: usize) -> u32 {
    read_volatile(addr as *const u32)
}

unsafe fn wr(addr: usize, v: u32) {
    write_volatile(addr as *mut u32, v)
}

/// PB6/PB7 to analog, so the OLED module is not powered through its I2C pull-ups.
pub fn i2c_pins_analog() {
    unsafe { wr(GPIOB_MODER, rd(GPIOB_MODER) | 0b1111 << 12) };
}

fn buttons_released() -> bool {
    let (a, c) = unsafe { (rd(GPIOA_IDR), rd(GPIOC_IDR)) };
    a & (1 << 11 | 1 << 12) == 1 << 11 | 1 << 12 && c & 1 << 14 != 0
}

pub fn stop_until_button() -> ! {
    // Letting go of the power button must not count as a press.
    while !buttons_released() {}
    cortex_m::asm::delay(16_000 * 30);
    cortex_m::interrupt::disable();
    unsafe {
        // Only a button may wake us: mask every other interrupt and drop
        // anything already pending (a timer tick would end WFI at once).
        let nvic = &*NVIC::PTR;
        nvic.icer[0].write(0xFFFF_FFFF);
        nvic.icpr[0].write(0xFFFF_FFFF);
        wr(EXTI_RTSR, rd(EXTI_RTSR) & !BUTTON_LINES);
        wr(EXTI_FTSR, rd(EXTI_FTSR) | BUTTON_LINES);
        wr(EXTI_IMR, rd(EXTI_IMR) | BUTTON_LINES);
        wr(EXTI_PR, BUTTON_LINES);
        NVIC::unpend(interrupt::EXTI4_15);
        NVIC::unmask(interrupt::EXTI4_15);
        wr(RCC_APB1ENR, rd(RCC_APB1ENR) | 1 << 28);
        // LPSDSR (low-power regulator), CWUF, ULP (VREFINT off), FWU; PDDS = 0 for Stop.
        wr(PWR_CR, (rd(PWR_CR) & !(1 << 1)) | 1 << 0 | 1 << 2 | 1 << 9 | 1 << 10);
        let mut core = cortex_m::Peripherals::steal();
        core.SCB.set_sleepdeep();
    }
    // With interrupts masked, WFI still returns once the EXTI line is pending.
    loop {
        cortex_m::asm::dsb();
        cortex_m::asm::wfi();
        if unsafe { rd(EXTI_PR) } & BUTTON_LINES != 0 {
            break;
        }
        NVIC::unpend(interrupt::EXTI4_15);
    }
    SCB::sys_reset()
}
