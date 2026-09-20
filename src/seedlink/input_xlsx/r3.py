"""Typed values read from the R3 campaign member export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from seedlink.domain.issues import IssueCode
from seedlink.domain.provenance import SourceRecord
from seedlink.domain.salesforce_ids import salesforce_id_18
from seedlink.input_xlsx._parsing import (
    ParseProblem,
    collect,
    datetime_value,
    identifier_value,
    text_value,
)


@dataclass(frozen=True, slots=True)
class R3Record:
    source: SourceRecord
    member_status: str | None
    member_type: str | None
    first_name: str | None
    last_name: str | None
    email: str | None
    campaign_member_id: str | None
    first_associated_at: datetime | None


def parse_record(record: SourceRecord) -> tuple[R3Record, tuple[ParseProblem, ...]]:
    status, p_status = text_value(record.cell("Member Status"))
    member_type, p_type = text_value(record.cell("Member Type"))
    first_name, p_first = text_value(record.cell("First Name"))
    last_name, p_last = text_value(record.cell("Last Name"))
    email, p_email = text_value(record.cell("Email"))
    member_id_cell = record.cell("Campaign Member Id 18")
    member_id, p_id = identifier_value(member_id_cell)
    p_member_format: tuple[ParseProblem, ...] = ()
    if member_id is not None:
        try:
            member_id = salesforce_id_18(member_id)
        except ValueError:
            member_id = None
            p_member_format = (
                ParseProblem(
                    IssueCode.CAMPAIGN_MEMBER_ID_INVALID,
                    "Поле «Campaign Member Id 18» не містить підтримуваного "
                    "15- або 18-символьного Salesforce ID.",
                    member_id_cell,
                ),
            )
    associated_at, p_date = datetime_value(
        record.cell("Member First Associated Date")
    )
    parsed = R3Record(
        source=record,
        member_status=status,
        member_type=member_type,
        first_name=first_name,
        last_name=last_name,
        email=email,
        campaign_member_id=member_id,
        first_associated_at=associated_at,
    )
    return parsed, collect(
        p_status,
        p_type,
        p_first,
        p_last,
        p_email,
        p_id,
        p_member_format,
        p_date,
    )
