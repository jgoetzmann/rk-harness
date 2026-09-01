"""Central-time display formatting: rk_harness.timefmt.

Storage stays UTC; timefmt is the display-only conversion layer. These tests pin
the conversion on both sides of DST (January CST at UTC-6, July CDT at UTC-5),
the accepted input shapes, the default on unparseable input, and the agreement
between the _USCentral fallback and the primary CENTRAL zone.
"""
from __future__ import annotations

import datetime

from rk_harness.timefmt import CENTRAL, _USCentral, fmt_ct, to_ct

UTC = datetime.timezone.utc

JAN_UTC = datetime.datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)   # CST: UTC-6
JUL_UTC = datetime.datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)   # CDT: UTC-5


def test_T6a_z_suffix_iso_january_is_cst():
    assert fmt_ct("2026-01-15T12:00:00Z") == "2026-01-15 06:00 CT"
    dt = to_ct("2026-01-15T12:00:00Z")
    assert dt is not None
    assert dt.utcoffset() == datetime.timedelta(hours=-6)
    assert dt == JAN_UTC                       # same instant, different wall clock


def test_T6b_z_suffix_iso_july_is_cdt():
    assert fmt_ct("2026-07-15T12:00:00Z") == "2026-07-15 07:00 CT"
    dt = to_ct("2026-07-15T12:00:00Z")
    assert dt is not None
    assert dt.utcoffset() == datetime.timedelta(hours=-5)
    assert dt == JUL_UTC


def test_T6c_epoch_seconds_accepted():
    jan = JAN_UTC.timestamp()
    jul = JUL_UTC.timestamp()
    assert fmt_ct(jan) == "2026-01-15 06:00 CT"                  # float
    assert fmt_ct(int(jan)) == "2026-01-15 06:00 CT"             # int
    assert fmt_ct(jul) == "2026-07-15 07:00 CT"
    assert to_ct(jul) == JUL_UTC


def test_T6d_naive_iso_treated_as_utc():
    assert fmt_ct("2026-01-15T12:00:00") == "2026-01-15 06:00 CT"
    assert fmt_ct("2026-07-15 12:00:00") == "2026-07-15 07:00 CT"    # space separator parses too
    # an explicit offset is honoured, not re-interpreted as UTC
    assert fmt_ct("2026-07-15T07:00:00-05:00") == "2026-07-15 07:00 CT"


def test_T6e_invalid_or_none_returns_default():
    assert to_ct(None) is None
    assert to_ct("") is None
    assert to_ct("   ") is None
    assert to_ct("not a timestamp") is None
    assert to_ct(True) is None and to_ct(False) is None          # bools are not epochs
    assert to_ct(object()) is None
    assert to_ct(["2026-01-15T12:00:00Z"]) is None
    assert to_ct(1e18) is None                                   # epoch far out of range
    assert fmt_ct(None) == "n/a"
    assert fmt_ct("junk") == "n/a"
    assert fmt_ct(None, default="?") == "?"
    assert fmt_ct("junk", seconds=True, default="") == ""


def test_T6f_fallback_zone_agrees_with_central():
    fb = _USCentral()
    for instant in (JAN_UTC, JUL_UTC):
        primary = instant.astimezone(CENTRAL)
        fallback = instant.astimezone(fb)
        assert primary.utcoffset() == fallback.utcoffset()
        assert (primary.year, primary.month, primary.day, primary.hour, primary.minute) == \
               (fallback.year, fallback.month, fallback.day, fallback.hour, fallback.minute)
    assert fb.tzname(JAN_UTC.astimezone(fb)) == "CST"
    assert fb.tzname(JUL_UTC.astimezone(fb)) == "CDT"


def test_T6g_seconds_flag_format():
    assert fmt_ct("2026-01-15T12:34:56Z", seconds=True) == "2026-01-15 06:34:56 CT"
    assert fmt_ct("2026-07-15T12:34:56Z", seconds=True) == "2026-07-15 07:34:56 CT"


def test_T6h_datetime_objects_accepted():
    assert fmt_ct(JAN_UTC) == "2026-01-15 06:00 CT"                                    # aware
    assert fmt_ct(datetime.datetime(2026, 7, 15, 12, 0, 0)) == "2026-07-15 07:00 CT"   # naive = UTC
