"""Input validation for untrusted user data."""
from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_time(raw: str) -> str | None:
    """Accept 'H:MM' / 'HH:MM' (24h) and return canonical 'HH:MM', else None."""
    match = _TIME_RE.match(raw.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return f"{hour:02d}:{minute:02d}"


def normalize_timezone(raw: str) -> str | None:
    """Return the IANA name if valid, else None."""
    candidate = raw.strip()
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return candidate


_DAY_ALIASES = {
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}
_DAY_FULL = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3, "пятница": 4,
    "суббота": 5, "воскресенье": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6,
}
_DAY_GROUPS = {
    "будни": [0, 1, 2, 3, 4], "weekdays": [0, 1, 2, 3, 4],
    "выходные": [5, 6], "weekend": [5, 6],
    "ежедневно": [], "daily": [], "каждый": [],
}
DAY_NAMES_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def parse_days(raw: str) -> str | None:
    """'вт,чт' / 'tue thu' / 'будни' / 'daily' -> '1,3' (Mon=0); '' means every day; None = invalid."""
    tokens = [t for t in re.split(r"[,\s]+", raw.strip().lower()) if t]
    if not tokens:
        return None
    days: set[int] = set()
    for token in tokens:
        if token in _DAY_GROUPS:
            days.update(_DAY_GROUPS[token])
            if not _DAY_GROUPS[token]:
                return ""
        elif token in _DAY_ALIASES:
            days.add(_DAY_ALIASES[token])
        elif token in _DAY_FULL:
            days.add(_DAY_FULL[token])
        else:
            return None
    return ",".join(str(d) for d in sorted(days))


def describe_days(days: str) -> str:
    if not days:
        return "каждый день"
    return ",".join(DAY_NAMES_RU[int(d)] for d in days.split(","))
