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
