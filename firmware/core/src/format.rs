//! Readings as text, without floating point or `core::fmt` (both cost flash
//! on a Cortex-M0+).

/// A short string on the stack.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Text<const N: usize> {
    buf: [u8; N],
    len: usize,
}

impl<const N: usize> From<&str> for Text<N> {
    fn from(s: &str) -> Self {
        let mut t = Self::new();
        t.push_str(s);
        t
    }
}

impl<const N: usize> Text<N> {
    pub const fn new() -> Self {
        Self { buf: [0; N], len: 0 }
    }

    pub fn as_str(&self) -> &str {
        // Only ASCII and whole UTF-8 strings are ever pushed.
        core::str::from_utf8(&self.buf[..self.len]).unwrap_or("?")
    }

    pub fn push_str(&mut self, s: &str) {
        let b = s.as_bytes();
        let n = b.len().min(N - self.len);
        // Never cut a multi-byte character in half.
        let n = (0..=n).rev().find(|&i| s.is_char_boundary(i)).unwrap_or(0);
        self.buf[self.len..self.len + n].copy_from_slice(&b[..n]);
        self.len += n;
    }

    pub fn push(&mut self, c: u8) {
        if self.len < N {
            self.buf[self.len] = c;
            self.len += 1;
        }
    }

    /// Decimal digits of `n`, at least `width` of them.
    pub fn push_uint(&mut self, mut n: u64, width: usize) {
        let mut digits = [0u8; 20];
        let mut i = 0;
        while n > 0 || i < width.max(1) {
            digits[i] = b'0' + (n % 10) as u8;
            n /= 10;
            i += 1;
        }
        while i > 0 {
            i -= 1;
            self.push(digits[i]);
        }
    }

    /// `value / 10^decimals` with that many decimals, rounded.
    pub fn push_fixed(&mut self, value: i64, scale_pow10: u32, decimals: u32) {
        let div = 10i64.pow(scale_pow10 - decimals);
        let rounded = (value.abs() + div / 2) / div;
        if value < 0 && rounded != 0 {
            self.push(b'-');
        }
        let unit = 10u64.pow(decimals);
        self.push_uint(rounded as u64 / unit, 1);
        if decimals > 0 {
            self.push(b'.');
            self.push_uint(rounded as u64 % unit, decimals as usize);
        }
    }
}

impl<const N: usize> Default for Text<N> {
    fn default() -> Self {
        Self::new()
    }
}

impl<const N: usize> core::fmt::Debug for Text<N> {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.write_str(self.as_str())
    }
}

pub type Number = Text<12>;

/// µV as volts: 1 mV steps below 10 V, 10 mV above.
pub fn volts(uv: i64) -> (Number, &'static str) {
    let mut t = Number::new();
    if uv.abs() < 9_999_500 {
        t.push_fixed(uv, 6, 3);
    } else {
        t.push_fixed(uv, 6, 2);
    }
    (t, "V")
}

/// mΩ with four significant digits and the unit to match.
pub fn ohms(mohm: i64) -> (Number, &'static str) {
    let mut t = Number::new();
    let r = mohm.max(0);
    let unit = if r < 999_950 {
        t.push_fixed(r, 3, 1);
        "Ω"
    } else if r < 9_999_500 {
        t.push_fixed(r, 6, 3);
        "kΩ"
    } else if r < 99_995_000 {
        t.push_fixed(r, 6, 2);
        "kΩ"
    } else if r < 999_950_000 {
        t.push_fixed(r, 6, 1);
        "kΩ"
    } else if r < 9_999_500_000 {
        t.push_fixed(r, 9, 3);
        "MΩ"
    } else {
        t.push_fixed(r, 9, 2);
        "MΩ"
    };
    (t, unit)
}

/// Parses volts such as "10", "-5.03" or "9.9981" into µV.
pub fn parse_uv(s: &str) -> Option<i64> {
    let (neg, s) = match s.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, s.strip_prefix('+').unwrap_or(s)),
    };
    let (whole, frac) = s.split_once('.').unwrap_or((s, ""));
    if whole.is_empty() && frac.is_empty() || whole.len() > 4 || frac.len() > 6 {
        return None;
    }
    let mut uv: i64 = 0;
    for c in whole.bytes().chain(frac.bytes()).chain(core::iter::repeat_n(b'0', 6 - frac.len())) {
        if !c.is_ascii_digit() {
            return None;
        }
        uv = uv * 10 + (c - b'0') as i64;
    }
    Some(if neg { -uv } else { uv })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_point() {
        let mut t = Number::new();
        t.push_fixed(-12_345_678, 6, 3);
        assert_eq!(t.as_str(), "-12.346");
        let mut t = Number::new();
        t.push_fixed(-400, 6, 3);
        assert_eq!(t.as_str(), "0.000");
    }

    #[test]
    fn volt_ranges() {
        assert_eq!(volts(1_234_567).0.as_str(), "1.235");
        assert_eq!(volts(-5_000).0.as_str(), "-0.005");
        assert_eq!(volts(12_345_678).0.as_str(), "12.35");
        assert_eq!(volts(9_999_700).0.as_str(), "10.00");
    }

    #[test]
    fn ohm_ranges() {
        let s = |m| {
            let (t, u) = ohms(m);
            let mut all = Text::<16>::from(t.as_str());
            all.push_str(u);
            all
        };
        assert_eq!(s(12_345).as_str(), "12.3Ω");
        assert_eq!(s(4_700_000).as_str(), "4.700kΩ");
        assert_eq!(s(47_000_000).as_str(), "47.00kΩ");
        assert_eq!(s(470_000_000).as_str(), "470.0kΩ");
        assert_eq!(s(4_700_000_000).as_str(), "4.700MΩ");
        assert_eq!(s(15_000_000_000).as_str(), "15.00MΩ");
    }

    #[test]
    fn parses_volts() {
        assert_eq!(parse_uv("10"), Some(10_000_000));
        assert_eq!(parse_uv("-5.03"), Some(-5_030_000));
        assert_eq!(parse_uv("+9.998123"), Some(9_998_123));
        assert_eq!(parse_uv(".5"), Some(500_000));
        for bad in ["", "-", ".", "1.2.3", "12a", "1.0000001", "12345"] {
            assert_eq!(parse_uv(bad), None, "{bad}");
        }
    }

    #[test]
    fn utf8_is_never_split() {
        let mut t = Text::<3>::new();
        t.push_str("aΩb");
        assert_eq!(t.as_str(), "aΩ");
    }
}
