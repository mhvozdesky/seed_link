"""Typed values read from the R1 survey export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
import re
from urllib.parse import unquote

from seedlink.domain.issues import IssueCode
from seedlink.domain.provenance import SourceCell, SourceRecord, SourceValueKind
from seedlink.domain.salesforce_ids import find_salesforce_ids
from seedlink.input_xlsx._parsing import (
    ParseProblem,
    collect,
    datetime_value,
    identifier_value,
    text_value,
)


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.text_parts: list[str] = []
        self.saw_anchor = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.casefold() != "a":
            return
        self.saw_anchor = True
        for name, value in attrs:
            if name.casefold() == "href" and value:
                self.hrefs.append(value)

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)


_CAMPAIGN_MEMBER_PATH = re.compile(
    r"(?:^|/)CampaignMember/([A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?)(?:/|$)",
    re.IGNORECASE,
)


def _supported_campaign_member_ids(candidate: str) -> tuple[str, ...]:
    decoded = unquote(candidate)
    explicit = _CAMPAIGN_MEMBER_PATH.search(decoded)
    if explicit is not None:
        return find_salesforce_ids(explicit.group(1))
    return tuple(
        value
        for value in find_salesforce_ids(decoded)
        if value.casefold().startswith("00v")
    )


def _campaign_member_ids(
    cell: SourceCell,
) -> tuple[tuple[str, ...], tuple[ParseProblem, ...]]:
    """Extract and normalize IDs without rendering or executing HTML."""

    if cell.value_kind in {SourceValueKind.FORMULA, SourceValueKind.ERROR}:
        return (), ()
    candidates: list[str] = []
    invalid_reference_seen = False
    if isinstance(cell.original_value, str):
        parser = _HrefCollector()
        try:
            parser.feed(cell.original_value)
            parser.close()
        except Exception:
            # The input remains inert; a malformed snippet is handled as plain text.
            pass
        if parser.saw_anchor:
            hrefs = [href.strip() for href in parser.hrefs if href.strip()]
            candidates.extend(hrefs)
            invalid_reference_seen = bool(hrefs)
            visible_text = " ".join(parser.text_parts)
            if find_salesforce_ids(visible_text):
                candidates.append(visible_text)
        else:
            plain_text = cell.original_value.strip()
            if plain_text:
                candidates.append(plain_text)
                invalid_reference_seen = plain_text.casefold() not in {
                    "campaign member id",
                    "campaign member",
                }
    if cell.hyperlink_target:
        candidates.append(cell.hyperlink_target)
        invalid_reference_seen = True

    result: list[str] = []
    for candidate in candidates:
        found = _supported_campaign_member_ids(candidate)
        if found:
            invalid_reference_seen = False
        for value in found:
            if value not in result:
                result.append(value)
    if result:
        return tuple(result), ()
    if invalid_reference_seen:
        return (), (
            ParseProblem(
                IssueCode.CAMPAIGN_MEMBER_ID_INVALID,
                "Посилання Campaigm Member не містить підтримуваного "
                "15- або 18-символьного Salesforce ID.",
                cell,
            ),
        )
    return (), ()


def campaign_member_ids(cell: SourceCell) -> tuple[str, ...]:
    """Public convenience accessor returning canonical 18-character IDs."""

    return _campaign_member_ids(cell)[0]


@dataclass(frozen=True, slots=True)
class R1Record:
    source: SourceRecord
    record_number: str | None
    created_at: datetime | None
    campaign_member_ids: tuple[str, ...]
    lead_full_name: str | None
    account_id: str | None
    account_tax_id: str | None
    first_name: str | None
    last_name: str | None
    mobile: str | None
    time_taken: datetime | None
    email: str | None
    survey_response_id: str | None
    answer_cells: tuple[SourceCell, ...]


def parse_record(record: SourceRecord) -> tuple[R1Record, tuple[ParseProblem, ...]]:
    record_number, p_record_number = identifier_value(record.cell("Record Nr"))
    created_at, p_created = datetime_value(record.cell("Created Date"))
    lead_full_name, p_lead = text_value(record.cell("Lead: Full Name"))
    account_id, p_account = identifier_value(record.cell("Account: 18-digits ID"))
    account_tax_id, p_tax = identifier_value(record.cell("Account: Tax ID 1"))
    first_name, p_first = text_value(record.cell("First Name"))
    last_name, p_last = text_value(record.cell("Last Name"))
    mobile, p_mobile = identifier_value(record.cell("Mobile"))
    time_taken, p_time = datetime_value(record.cell("Time Taken"))
    email, p_email = text_value(record.cell("Email"))
    survey_id, p_survey = identifier_value(record.cell("Survey Response ID"))
    campaign_cell = record.cell("Campaigm Member")
    member_ids, p_member_ids = _campaign_member_ids(campaign_cell)
    parsed = R1Record(
        source=record,
        record_number=record_number,
        created_at=created_at,
        campaign_member_ids=member_ids,
        lead_full_name=lead_full_name,
        account_id=account_id,
        account_tax_id=account_tax_id,
        first_name=first_name,
        last_name=last_name,
        mobile=mobile,
        time_taken=time_taken,
        email=email,
        survey_response_id=survey_id,
        answer_cells=tuple(
            record.cell(field)
            for field in ("Answer", "Correct Answer", "Answer (Long Text)")
        ),
    )
    return parsed, collect(
        p_record_number,
        p_created,
        p_member_ids,
        p_lead,
        p_account,
        p_tax,
        p_first,
        p_last,
        p_mobile,
        p_time,
        p_email,
        p_survey,
    )
