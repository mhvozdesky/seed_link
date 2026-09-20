"""Typed values read from the R4 activity export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from seedlink.domain.provenance import SourceRecord
from seedlink.input_xlsx._parsing import (
    ParseProblem,
    collect,
    datetime_value,
    identifier_value,
    text_value,
)


@dataclass(frozen=True, slots=True)
class R4Record:
    source: SourceRecord
    completed_at: datetime | None
    account_id: str | None
    activity_id: str | None
    created_at: datetime | None
    due_at: datetime | None
    person_name: str | None
    results: str | None


def parse_record(record: SourceRecord) -> tuple[R4Record, tuple[ParseProblem, ...]]:
    completed, p_completed = datetime_value(record.cell("Completed Date/Time"))
    account_id, p_account = identifier_value(record.cell("Account ID"))
    activity_id, p_activity = identifier_value(record.cell("Activity ID"))
    created_at, p_created = datetime_value(record.cell("Created Date"))
    due_at, p_due = datetime_value(record.cell("Due Date"))
    name, p_name = text_value(record.cell("Name"))
    results, p_results = text_value(record.cell("Results"))
    parsed = R4Record(
        source=record,
        completed_at=completed,
        account_id=account_id,
        activity_id=activity_id,
        created_at=created_at,
        due_at=due_at,
        person_name=name,
        results=results,
    )
    return parsed, collect(
        p_completed,
        p_account,
        p_activity,
        p_created,
        p_due,
        p_name,
        p_results,
    )
