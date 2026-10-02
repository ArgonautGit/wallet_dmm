//! Measurement maths, user interface state and display rendering for the
//! wallet DMM.
//!
//! Nothing here touches hardware: the firmware feeds in ADC codes and button
//! presses and draws what comes out, and the simulations in `../sim` run the
//! same code on a PC.
#![no_std]

pub mod format;
pub mod frontend;
pub mod render;
pub mod ui;
