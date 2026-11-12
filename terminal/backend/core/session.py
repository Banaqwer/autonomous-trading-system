"""Market session state per calendar: "open" | "closed" | "unknown" (REBUILD_SPEC §3.3).

Uses zoneinfo (stdlib) with the IANA database from the OS or the `tzdata` wheel
(Windows needs `pip install tzdata`). If that database is missing (a Windows Python
whose requirements were not re-installed), the three zones the terminal depends on —
America/New_York, Europe/Berlin, Europe/London — are computed from their daylight-saving
rules as in force since 2007 (US) / 1996 (EU), so a session is never judged "unknown"
there and an after-hours print is never mistaken for a session price. `tz_source()`
says which one is in use ("iana" | "builtin"); the gateway logs a warning for "builtin".
Any other zone without data gives "unknown" — never a guess.

Calendars
    crypto     always open
    us_equity  Mon–Fri 09:30–16:00 America/New_York, NYSE full-day holidays excluded;
               13:00 early close on the day after Thanksgiving, on 24 December and on
               3 July when those are trading days (NYSE Rule 7.2)
    us_rates   Mon–Fri 08:00–17:00 America/New_York, bond-market holidays excluded
    eu_rates   Mon–Fri 09:00–17:30 Europe/Berlin, TARGET holidays excluded
    fx         Sun 17:00 – Fri 17:00 America/New_York; closed on 25 December and
               1 January until the 17:00 reopen
    cme        Sun 18:00 – Fri 17:00 America/New_York with the daily 17:00–18:00 break;
               closed on 25 December, 1 January and Good Friday until the 18:00 reopen
               (shortened CME holiday sessions — Thanksgiving, 24 December… — are not modelled)

`state(calendar, now, period=(start, end))`: when the quote source itself reports the
regular trading period of the current day (Yahoo does), that period decides between
open and closed on a business day — it knows about index-specific hours (VIX, TNX).
"""
from __future__ import annotations

import datetime as dt
import time
from functools import lru_cache

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"
NY, BERLIN = "America/New_York", "Europe/Berlin"


LONDON = "Europe/London"


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    """n-th Sunday of a month (n=-1: the last one)."""
    if n > 0:
        d = dt.date(year, month, 1)
        return d + dt.timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - 6) % 7)


class RuleZone(dt.tzinfo):
    """A zone computed from its current daylight-saving rule (no database needed).

    kind "us": DST from the 2nd Sunday of March 02:00 local standard time to the 1st
    Sunday of November 02:00 local daylight time. kind "eu": from the last Sunday of
    March 01:00 UTC to the last Sunday of October 01:00 UTC."""

    def __init__(self, name: str, std_hours: float, kind: str):
        self.name, self.std, self.kind = name, dt.timedelta(hours=std_hours), kind

    def _dst_utc(self, utc: dt.datetime) -> bool:
        """`utc` is a naive UTC datetime."""
        y = utc.year
        if self.kind == "us":
            start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(2)) - self.std
            end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(2)) - self.std - dt.timedelta(hours=1)
        else:
            start = dt.datetime.combine(_nth_sunday(y, 3, -1), dt.time(1))
            end = dt.datetime.combine(_nth_sunday(y, 10, -1), dt.time(1))
        return start <= utc < end

    def utcoffset(self, when):
        if when is None:
            return self.std
        naive, hour = when.replace(tzinfo=None), dt.timedelta(hours=1)
        as_dst = self._dst_utc(naive - self.std - hour)      # if this wall time is daylight time
        as_std = self._dst_utc(naive - self.std)             # if it is standard time
        if as_dst and as_std:
            return self.std + hour
        if as_dst and not as_std:                            # the repeated hour in autumn (PEP 495)
            return self.std + (hour if not getattr(when, "fold", 0) else dt.timedelta(0))
        return self.std                                      # winter, or the skipped hour in spring

    def dst(self, when):
        return self.utcoffset(when) - self.std

    def tzname(self, when):
        return self.name

    def fromutc(self, when):
        naive, hour = when.replace(tzinfo=None), dt.timedelta(hours=1)
        dst = self._dst_utc(naive)
        local = (naive + self.std + (hour if dst else dt.timedelta(0))).replace(tzinfo=self)
        if not dst and self._dst_utc(naive - hour):          # second pass through the repeated hour
            local = local.replace(fold=1)
        return local

    def __repr__(self):
        return f"RuleZone({self.name!r})"


_BUILTIN = {NY: (-5, "us"), "US/Eastern": (-5, "us"), "America/Chicago": (-6, "us"),
            BERLIN: (1, "eu"), LONDON: (0, "eu")}


def _iana(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:           # ZoneInfoNotFoundError (no tzdata), bad name, or no zoneinfo at all
        return None


@lru_cache(maxsize=32)
def _zone(name: str):
    z = _iana(name)
    if z is None and name in _BUILTIN:
        z = RuleZone(name, *_BUILTIN[name])
    return z


def zone(name):
    """tzinfo for an IANA zone name, or None when it is unknown here (callers must then
    say "unknown" / use a documented fallback — never guess)."""
    if not isinstance(name, str) or not name or len(name) > 64:
        return None
    return _zone(name)


def tz_available() -> bool:
    """True when the IANA time-zone database is installed (zoneinfo can load New York)."""
    return _iana(NY) is not None


TZ_WARNING = ("No IANA time-zone database on this machine (run: pip install tzdata). Session "
              "open/closed is computed from built-in US/EU daylight-saving rules instead.")


def tz_source() -> str:
    """"iana" (database present) or "builtin" (US/EU daylight-saving rules computed here)."""
    return "iana" if tz_available() else "builtin"


def _local(now: float, zone: str) -> dt.datetime | None:
    z = _zone(zone)
    if z is None:
        return None
    return dt.datetime.fromtimestamp(now, dt.timezone.utc).astimezone(z)


# ----------------------------------------------------------------- holidays
def _easter(year: int) -> dt.date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(year, month, day)


def _nth(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th (1-based) given weekday of a month; n=-1 is the last one."""
    if n > 0:
        d = dt.date(year, month, 1)
        d += dt.timedelta(days=(weekday - d.weekday()) % 7)
        return d + dt.timedelta(weeks=n - 1)
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: dt.date, saturday_to_friday: bool = True) -> dt.date | None:
    if d.weekday() == 5:
        return d - dt.timedelta(days=1) if saturday_to_friday else None
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def us_holidays(year: int, bonds: bool = False) -> frozenset:
    """NYSE full-day holidays (bonds=True adds Columbus Day and Veterans Day)."""
    days = {
        _observed(dt.date(year, 1, 1), saturday_to_friday=False),   # NYSE: no Friday observance
        _nth(year, 1, 0, 3),                    # Martin Luther King Jr. Day
        _nth(year, 2, 0, 3),                    # Washington's Birthday
        _easter(year) - dt.timedelta(days=2),   # Good Friday
        _nth(year, 5, 0, -1),                   # Memorial Day
        _observed(dt.date(year, 6, 19)),        # Juneteenth
        _observed(dt.date(year, 7, 4)),         # Independence Day
        _nth(year, 9, 0, 1),                    # Labor Day
        _nth(year, 11, 3, 4),                   # Thanksgiving
        _observed(dt.date(year, 12, 25)),       # Christmas
    }
    if bonds:
        days |= {_nth(year, 10, 0, 2), _observed(dt.date(year, 11, 11))}
    return frozenset(d for d in days if d is not None)


def us_early_close(day: dt.date) -> bool:
    """True on a NYSE trading day that ends at 13:00 New York: the day after Thanksgiving,
    24 December and 3 July, when they are trading days (NYSE Rule 7.2)."""
    if day.weekday() >= 5 or day in us_holidays(day.year):
        return False
    return day in (_nth(day.year, 11, 3, 4) + dt.timedelta(days=1),
                   dt.date(day.year, 12, 24), dt.date(day.year, 7, 3))


def us_close_hm(day: dt.date) -> tuple[int, int]:
    """(hour, minute) New York of the regular close of a NYSE trading day."""
    return (13, 0) if us_early_close(day) else (16, 0)


def _globex_closed(calendar: str, day: dt.date) -> bool:
    """Full-day closures of CME Globex / the FX market on a New York calendar day."""
    if (day.month, day.day) in ((12, 25), (1, 1)):
        return True
    return calendar == "cme" and day == _easter(day.year) - dt.timedelta(days=2)


@lru_cache(maxsize=64)
def eu_holidays(year: int) -> frozenset:
    e = _easter(year)
    return frozenset({dt.date(year, 1, 1), e - dt.timedelta(days=2), e + dt.timedelta(days=1),
                      dt.date(year, 5, 1), dt.date(year, 12, 25), dt.date(year, 12, 26)})


# ------------------------------------------------------------------- state
def _weekday_window(now, zone, start, end, holidays):
    t = _local(now, zone)
    if t is None:
        return UNKNOWN
    if t.weekday() >= 5 or t.date() in holidays(t.year):
        return CLOSED
    return OPEN if start <= (t.hour, t.minute) < end else CLOSED


def is_business_day(calendar: str, now: float | None = None) -> bool | None:
    """True/False for the weekday calendars, None when unknown / not applicable."""
    now = time.time() if now is None else now
    if calendar in ("us_equity", "us_rates"):
        t = _local(now, NY)
        if t is None:
            return None
        return t.weekday() < 5 and t.date() not in us_holidays(t.year, calendar == "us_rates")
    if calendar == "eu_rates":
        t = _local(now, BERLIN)
        if t is None:
            return None
        return t.weekday() < 5 and t.date() not in eu_holidays(t.year)
    return None


def state(calendar: str, now: float | None = None,
          period: tuple[float, float] | None = None) -> str:
    now = time.time() if now is None else now
    if calendar == "crypto":
        return OPEN
    if calendar in ("us_equity", "us_rates", "eu_rates"):
        if period and period[0] and period[1] and period[1] > period[0]:
            business = is_business_day(calendar, now)
            if business is False:
                return CLOSED
            return OPEN if period[0] <= now < period[1] else CLOSED
        if calendar == "us_equity":
            t = _local(now, NY)
            if t is None:
                return UNKNOWN
            if t.weekday() >= 5 or t.date() in us_holidays(t.year):
                return CLOSED
            return OPEN if (9, 30) <= (t.hour, t.minute) < us_close_hm(t.date()) else CLOSED
        if calendar == "us_rates":
            return _weekday_window(now, NY, (8, 0), (17, 0), lambda y: us_holidays(y, True))
        return _weekday_window(now, BERLIN, (9, 0), (17, 30), eu_holidays)
    if calendar in ("fx", "cme"):
        t = _local(now, NY)
        if t is None:
            return UNKNOWN
        wd, hm = t.weekday(), (t.hour, t.minute)
        reopen = (17, 0) if calendar == "fx" else (18, 0)
        if wd == 5:
            return CLOSED
        if _globex_closed(calendar, t.date()):
            # closed for the calendar day; trading resumes at the evening reopen
            # (never on a Friday or Saturday evening)
            return OPEN if (hm >= reopen and wd not in (4, 5)) else CLOSED
        if hm >= reopen and _globex_closed(calendar, t.date() + dt.timedelta(days=1)) and calendar == "cme":
            return CLOSED                   # Globex does not open the evening before a closure
        if wd == 6:
            return OPEN if hm >= reopen else CLOSED
        if wd == 4:
            return OPEN if hm < (17, 0) else CLOSED
        if calendar == "cme" and (17, 0) <= hm < (18, 0):
            return CLOSED
        return OPEN
    return UNKNOWN


def local_date(zone: str, now: float | None = None) -> str | None:
    """'YYYY-MM-DD' of `now` in `zone` (None without tz data)."""
    t = _local(time.time() if now is None else now, zone)
    return t.date().isoformat() if t is not None else None


def us_close_ts(date_iso: str) -> float | None:
    """Epoch seconds of the New York close (16:00, or 13:00 on an early-close day) of a
    'YYYY-MM-DD' date (None without tz data)."""
    z = _zone(NY)
    if z is None:
        return None
    try:
        d = dt.date.fromisoformat(date_iso[:10])
    except ValueError:
        return None
    hh, mm = us_close_hm(d)
    return dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=z).timestamp()


def local_to_epoch(naive_iso: str, zone: str) -> float | None:
    """'2026-11-11T16:15:01' in `zone` -> epoch seconds (None without tz data / bad input)."""
    z = _zone(zone)
    if z is None:
        return None
    try:
        return dt.datetime.fromisoformat(naive_iso).replace(tzinfo=z).timestamp()
    except ValueError:
        return None
