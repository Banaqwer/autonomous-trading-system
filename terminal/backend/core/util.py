"""Small pure helpers shared by providers and core (time parsing, number coercion)."""
from __future__ import annotations

import calendar
import math
import re

_ISO = re.compile(r"^\s*(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d)(?::(\d\d)(\.\d+)?)?)?\s*"
                  r"(Z|[+-]\d\d:?\d\d)?\s*$")


def iso_epoch(text) -> float | None:
    """ISO-8601 -> epoch seconds. Accepts 'Z', offsets, a space separator, nanosecond
    fractions and a bare date; a string without zone is read as UTC. None if unparseable."""
    if not isinstance(text, str):
        return None
    m = _ISO.match(text)
    if not m:
        return None
    y, mo, d, hh, mi, ss, frac, zone = m.groups()
    try:
        base = calendar.timegm((int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0)))
    except (ValueError, OverflowError):
        return None
    out = base + (float(frac) if frac else 0.0)
    if zone and zone != "Z":
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        out -= sign * (int(digits[:2]) * 3600 + int(digits[2:4]) * 60)
    return out


def date_epoch(text) -> float | None:
    """'YYYY-MM-DD…' -> epoch seconds of 00:00 UTC of that date."""
    if not isinstance(text, str) or len(text) < 10:
        return None
    return iso_epoch(text[:10])


def epoch_date(t: float) -> str:
    """epoch seconds -> 'YYYY-MM-DD' (UTC)."""
    import time
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def num(value) -> float | None:
    """float(value) or None for None / '' / '.' / NaN / inf / garbage. Never raises."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def day_floor(t: float, gmtoffset: float = 0) -> int:
    """00:00 UTC of the local calendar date that `t` falls on in a zone `gmtoffset` s east of UTC."""
    return int((t + gmtoffset) // 86400) * 86400


def week_floor(day_t: int) -> int:
    """Monday 00:00 UTC of the week containing the (already day-floored) timestamp."""
    return day_t - ((day_t // 86400 + 3) % 7) * 86400      # 1970-01-01 was a Thursday
