//! Formats a Unix-epoch-milliseconds timestamp as UTC ISO 8601, for
//! `session/list`'s `SessionInfo.updated_at` (`crates/structure-cli/src/acp/mod.rs`).
//!
//! Unlike `sessions.rs`'s relative-time labels ("3m ago"), which need only
//! subtraction and division, an absolute calendar date needs real calendar
//! arithmetic (leap years, month lengths) -- there is no way to hand-wave
//! that away, so this hand-rolls the well-known "civil_from_days" algorithm
//! (<http://howardhinnant.github.io/date_algorithms.html>, public domain,
//! also the one behind libc++'s `<chrono>`) rather than reach for a
//! chrono/time dependency for one field.

/// Days since the Unix epoch (1970-01-01) to a proleptic-Gregorian
/// `(year, month, day)`, all still relative to that same civil calendar.
fn civil_from_days(days_since_epoch: i64) -> (i64, u32, u32) {
    let z = days_since_epoch + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let day_of_era = (z - era * 146_097) as u64;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era as i64 + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let mp = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = if month <= 2 { year + 1 } else { year };
    (year, month, day)
}

/// UTC ISO 8601, e.g. `2024-02-29T12:00:00Z`. `epoch_ms` is truncated to the
/// whole second (ACP's `updated_at` is documented only as "ISO 8601
/// timestamp of last activity", not sub-second precision).
pub fn to_iso8601(epoch_ms: u64) -> String {
    let epoch_secs = epoch_ms / 1000;
    let days_since_epoch = (epoch_secs / 86_400) as i64;
    let seconds_of_day = epoch_secs % 86_400;
    let (year, month, day) = civil_from_days(days_since_epoch);
    let hour = seconds_of_day / 3600;
    let minute = (seconds_of_day % 3600) / 60;
    let second = seconds_of_day % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_known_reference_timestamps() {
        // Cross-checked against `date -u -r <epoch_secs>` on this machine.
        assert_eq!(to_iso8601(0), "1970-01-01T00:00:00Z");
        assert_eq!(to_iso8601(1_700_000_000_000), "2023-11-14T22:13:20Z");
        assert_eq!(to_iso8601(1_758_000_000_000), "2025-09-16T05:20:00Z");
        assert_eq!(to_iso8601(1_000_000_000_000), "2001-09-09T01:46:40Z");
    }

    #[test]
    fn handles_leap_years_including_the_400_year_and_100_year_rules() {
        // 2024 is an ordinary leap year (divisible by 4, not by 100).
        assert_eq!(to_iso8601(1_709_208_000_000), "2024-02-29T12:00:00Z");
        // 2000 is a leap year despite being divisible by 100, because it is
        // also divisible by 400.
        assert_eq!(to_iso8601(951_782_400_000), "2000-02-29T00:00:00Z");
        // 2100 is divisible by 100 but not 400, so it is NOT a leap year:
        // no 2100-02-29 exists, and this must land on March 1st, not a
        // nonexistent February 29th.
        assert_eq!(to_iso8601(4_107_542_400_000), "2100-03-01T00:00:00Z");
    }

    #[test]
    fn crosses_a_year_boundary_correctly() {
        assert_eq!(to_iso8601(946_684_799_000), "1999-12-31T23:59:59Z");
    }
}
