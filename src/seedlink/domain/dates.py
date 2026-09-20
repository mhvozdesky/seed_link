"""Timezone-stable date representations used by later matching blocks."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo


KYIV_TIMEZONE_NAME = "Europe/Kyiv"
KYIV = ZoneInfo(KYIV_TIMEZONE_NAME)


class DateNormalizationError(ValueError):
    pass


def datetime_in_kyiv(value: date | datetime | str | None) -> datetime | None:
    """Return an aware Kyiv datetime; naive Excel values are treated as Kyiv local."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate or not any(character in candidate for character in "-T:"):
            raise DateNormalizationError(f"unsupported date text: {value!r}")
        if candidate.endswith(("Z", "z")):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise DateNormalizationError(f"invalid date text: {value!r}") from exc
    else:
        raise DateNormalizationError(f"unsupported date type: {type(value).__name__}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=KYIV)
    return parsed.astimezone(KYIV)


def calendar_date_in_kyiv(value: date | datetime | str | None) -> date | None:
    parsed = datetime_in_kyiv(value)
    return parsed.date() if parsed is not None else None


def datetime_to_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DateNormalizationError("datetime must be timezone-aware")
    return value.astimezone(KYIV).isoformat(timespec="seconds")
