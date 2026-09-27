"""When a provider's daily allowance starts over (grill-decisions.md §19).

A preset's `daily_reset` is either "rolling" (each request frees up 24h
after it was sent, the old behaviour) or "HH:MM Area/City" (Google:
"00:00 America/Los_Angeles"). Daily counts line up with the provider's own
day, so "7 of 20 today" matches what the provider's console says.

zoneinfo needs the tzdata package on Windows, which flexrouter doesn't
ship, so the US zones providers actually use are worked out here from the
US daylight-saving rule when zoneinfo can't load them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional

ROLLING = "rolling"

# Standard-time offset in hours, for the fallback below.
_US_ZONES = {"America/Los_Angeles": -8, "America/Denver": -7,
             "America/Chicago": -6, "America/New_York": -5}
_ZONE_WORDS = {"America/Los_Angeles": "Pacific", "America/Denver": "Mountain",
               "America/Chicago": "Central", "America/New_York": "Eastern", "UTC": "UTC"}


def _nth_sunday(year: int, month: int, n: int) -> datetime:
    first = datetime(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


class _USZone(tzinfo):
    """A US zone: daylight time from 02:00 on the 2nd Sunday of March to
    02:00 (daylight) on the 1st Sunday of November."""

    def __init__(self, std_hours: int) -> None:
        self._std = timedelta(hours=std_hours)

    @staticmethod
    def _in_dst(std_naive: datetime) -> bool:
        """Whether a local *standard* time falls in daylight time."""
        start = _nth_sunday(std_naive.year, 3, 2) + timedelta(hours=2)
        end = _nth_sunday(std_naive.year, 11, 1) + timedelta(hours=1)
        return start <= std_naive < end

    def dst(self, dt):
        if dt is None:
            return timedelta(0)
        # Wall time -> standard time is at most an hour off; checking the
        # hour before wall time is right everywhere but the repeated hour.
        naive = dt.replace(tzinfo=None)
        return timedelta(hours=1) if self._in_dst(naive - timedelta(hours=1)) else timedelta(0)

    def utcoffset(self, dt):
        return self._std + self.dst(dt)

    def tzname(self, dt):
        return None

    def fromutc(self, dt):
        std = dt.replace(tzinfo=None) + self._std
        local = std + (timedelta(hours=1) if self._in_dst(std) else timedelta(0))
        return local.replace(tzinfo=self)


def _zone(name: str) -> Optional[tzinfo]:
    if name in ("UTC", "Etc/UTC"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # no tzdata (Windows), or an unknown name
        std = _US_ZONES.get(name)
        return _USZone(std) if std is not None else None


def parse(spec: Optional[str]) -> Optional[tuple[int, int, tzinfo, str]]:
    """(hour, minute, zone, zone name) for "HH:MM Zone", or None for
    rolling / anything unreadable."""
    if not spec or spec.strip().lower() == ROLLING:
        return None
    try:
        clock, name = spec.split(None, 1)
        hour, minute = (int(x) for x in clock.split(":"))
    except ValueError:
        return None
    zone = _zone(name.strip())
    return (hour, minute, zone, name.strip()) if zone is not None else None


def last_reset(spec: Optional[str], now: float) -> Optional[float]:
    """The epoch second the current provider day began, or None (rolling)."""
    parsed = parse(spec)
    if parsed is None:
        return None
    hour, minute, zone, _ = parsed
    local = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(zone)
    start = datetime(local.year, local.month, local.day, hour, minute, tzinfo=zone)
    if start.timestamp() > now:
        start -= timedelta(days=1)
        start = datetime(start.year, start.month, start.day, hour, minute, tzinfo=zone)
    return start.timestamp()


def next_reset(spec: Optional[str], now: float) -> Optional[float]:
    start = last_reset(spec, now)
    if start is None:
        return None
    hour, minute, zone, _ = parse(spec)
    day = datetime.fromtimestamp(start, tz=timezone.utc).astimezone(zone) + timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).timestamp()


def describe(spec: Optional[str]) -> str:
    """'midnight Pacific', '09:00 UTC', or '' for rolling."""
    parsed = parse(spec)
    if parsed is None:
        return ""
    hour, minute, _, name = parsed
    when = "midnight" if (hour, minute) == (0, 0) else f"{hour:02d}:{minute:02d}"
    return f"{when} {_ZONE_WORDS.get(name, name)}"
