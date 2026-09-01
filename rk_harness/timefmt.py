"""Central-time display formatting.

Storage stays UTC everywhere (archive records, events, HEARTBEAT: the watchdog's
staleness math and archive determinism depend on it). This module is the one place
that converts for human-facing display: watch panels, the dashboard, and site pages.

Uses the real America/Chicago zone when the tz database is available (it is in the
container; on the host it needs the tzdata wheel). Falls back to a built-in tzinfo
with the current US DST rules so display never crashes on a machine without tzdata.
"""
from __future__ import annotations

import datetime

_ZERO = datetime.timedelta(0)
_HOUR = datetime.timedelta(hours=1)


class _USCentral(datetime.tzinfo):
    """US Central with post-2007 DST rules (second Sunday of March to the
    Sunday starting November, both at 02:00 local)."""

    def _dst_bounds(self, year: int) -> tuple[datetime.datetime, datetime.datetime]:
        d = datetime.date(year, 3, 8)
        d += datetime.timedelta(days=(6 - d.weekday()) % 7)
        start = datetime.datetime.combine(d, datetime.time(2))
        n = datetime.date(year, 11, 1)
        n += datetime.timedelta(days=(6 - n.weekday()) % 7)
        end = datetime.datetime.combine(n, datetime.time(2))
        return start, end

    def utcoffset(self, dt):
        return datetime.timedelta(hours=-6) + self.dst(dt)

    def dst(self, dt):
        if dt is None:
            return _ZERO
        start, end = self._dst_bounds(dt.year)
        naive = dt.replace(tzinfo=None)
        return _HOUR if start <= naive < end else _ZERO

    def tzname(self, dt):
        return "CDT" if self.dst(dt) else "CST"


def _central() -> datetime.tzinfo:
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo("America/Chicago")
    except Exception:
        return _USCentral()


CENTRAL: datetime.tzinfo = _central()


def to_ct(value) -> datetime.datetime | None:
    """UTC-ish value -> aware Central datetime, or None if unparseable.

    Accepts ISO-8601 strings ('Z' suffix, explicit offset, or naive = UTC),
    epoch seconds as int/float, and datetime objects (naive = UTC).
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
    elif isinstance(value, bool):
        return None
    elif isinstance(value, (int, float)):
        try:
            dt = datetime.datetime.fromtimestamp(float(value), datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        return None
    return dt.astimezone(CENTRAL)


def fmt_ct(value, seconds: bool = False, default: str = "n/a") -> str:
    """Format for display: '2026-09-01 14:03 CT' (':SS' with seconds=True)."""
    dt = to_ct(value)
    if dt is None:
        return default
    fmt = "%Y-%m-%d %H:%M:%S CT" if seconds else "%Y-%m-%d %H:%M CT"
    return dt.strftime(fmt)
