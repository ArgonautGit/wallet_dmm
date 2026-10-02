//! Draws a `Screen` on the 128 x 32 OLED.
//!
//! ```text
//! DC V HOLD REL 10k  + [###]
//!            -12.345 V
//! ```

use embedded_graphics::mono_font::{ascii::FONT_10X20, iso_8859_7::FONT_6X10, MonoTextStyle};
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{Line, PrimitiveStyle, Rectangle};
use embedded_graphics::text::{Alignment, Baseline, Text, TextStyleBuilder};

use crate::ui::{Main, Screen};

pub const WIDTH: u32 = 128;
pub const HEIGHT: u32 = 32;

pub fn draw<D: DrawTarget<Color = BinaryColor>>(screen: &Screen, d: &mut D) -> Result<(), D::Error> {
    d.clear(BinaryColor::Off)?;
    let small = MonoTextStyle::new(&FONT_6X10, BinaryColor::On);
    let big = MonoTextStyle::new(&FONT_10X20, BinaryColor::On);
    let top = TextStyleBuilder::new().baseline(Baseline::Top).build();

    // Status line.
    Text::with_text_style(screen.title, Point::new(0, 0), small, top).draw(d)?;
    if screen.hold {
        Text::with_text_style("HOLD", Point::new(34, 0), small, top).draw(d)?;
    }
    if screen.relative {
        Text::with_text_style("REL", Point::new(62, 0), small, top).draw(d)?;
    }
    if let Some(range) = screen.range {
        Text::with_text_style(range, Point::new(84, 0), small, top).draw(d)?;
    }
    battery(d, screen.battery_pct, screen.usb)?;

    let bottom = |align| TextStyleBuilder::new().baseline(Baseline::Bottom).alignment(align).build();
    match &screen.main {
        Main::Reading { value, unit } => {
            Text::with_text_style(value.as_str(), Point::new(106, 32), big, bottom(Alignment::Right)).draw(d)?;
            Text::with_text_style(unit, Point::new(108, 31), small, bottom(Alignment::Left)).draw(d)?;
        }
        Main::Message(msg) => {
            Text::with_text_style(msg, Point::new(64, 32), big, bottom(Alignment::Center)).draw(d)?;
        }
        Main::Lines(a, b) => {
            Text::with_text_style(a.as_str(), Point::new(0, 11), small, top).draw(d)?;
            Text::with_text_style(b.as_str(), Point::new(0, 22), small, top).draw(d)?;
        }
    }
    Ok(())
}

/// Battery outline with a fill for the charge, and a plus sign on USB.
fn battery<D: DrawTarget<Color = BinaryColor>>(d: &mut D, pct: u8, usb: bool) -> Result<(), D::Error> {
    let stroke = PrimitiveStyle::with_stroke(BinaryColor::On, 1);
    let fill = PrimitiveStyle::with_fill(BinaryColor::On);
    Rectangle::new(Point::new(112, 1), Size::new(14, 8)).into_styled(stroke).draw(d)?;
    Rectangle::new(Point::new(126, 3), Size::new(2, 4)).into_styled(fill).draw(d)?;
    let w = (pct.min(100) as u32 * 10).div_ceil(100);
    if w > 0 {
        Rectangle::new(Point::new(114, 3), Size::new(w, 4)).into_styled(fill).draw(d)?;
    }
    if usb {
        Line::new(Point::new(104, 5), Point::new(108, 5)).into_styled(stroke).draw(d)?;
        Line::new(Point::new(106, 3), Point::new(106, 7)).into_styled(stroke).draw(d)?;
    }
    Ok(())
}
