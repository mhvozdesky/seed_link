from __future__ import annotations

from datetime import date, datetime, UTC

import pytest

from seedlink.domain.dates import (
    DateNormalizationError,
    calendar_date_in_kyiv,
    datetime_in_kyiv,
    datetime_to_text,
)


def test_utc_near_midnight_uses_kyiv_calendar_day():
    value = datetime_in_kyiv("2026-01-01T22:30:00Z")
    assert value is not None
    assert value.date() == date(2026, 1, 2)
    assert datetime_to_text(value).startswith("2026-01-02T00:30:00")


def test_naive_excel_datetime_is_interpreted_as_kyiv_local():
    value = datetime_in_kyiv(datetime(2026, 7, 1, 12, 0))
    assert value is not None
    assert value.utcoffset().total_seconds() == 3 * 60 * 60
    assert calendar_date_in_kyiv(value.astimezone(UTC)) == date(2026, 7, 1)


@pytest.mark.parametrize("value", ["123", "n/a", 123])
def test_malformed_date_is_rejected(value):
    with pytest.raises(DateNormalizationError):
        datetime_in_kyiv(value)
