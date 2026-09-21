"""Lead-date resolution and calendar-day attribution for Block 04."""

from __future__ import annotations

from datetime import date

from seedlink.domain._issue_factory import make_issue
from seedlink.domain.dates import DateNormalizationError, calendar_date_in_kyiv
from seedlink.domain.issues import Issue, IssueCode
from seedlink.domain.models import (
    LeadDateResolution,
    LeadDateSource,
    LeadRef,
    Participant,
    ParticipantLink,
    ParticipantLinkSubject,
    TimeBucket,
)
from seedlink.domain.provenance import SourceCell, SourceValueKind


def time_bucket(created_on: date | None, reference_date: date | None) -> TimeBucket:
    if created_on is None or reference_date is None:
        return TimeBucket.UNKNOWN
    if created_on < reference_date:
        return TimeBucket.BEFORE_LEAD
    return TimeBucket.ON_OR_AFTER_LEAD


def _field_cells(lead: LeadRef, field_name: str) -> tuple[SourceCell, ...]:
    result: list[SourceCell] = []
    for source in lead.sources:
        try:
            result.append(source.cell(field_name))
        except KeyError:
            continue
    return tuple(result)


def _participant_date_cell(participant: Participant | None) -> SourceCell | None:
    if participant is None:
        return None
    for source in participant.sources:
        try:
            return source.cell("Member First Associated Date")
        except KeyError:
            continue
    return None


def resolve_lead_dates(
    lead_refs: tuple[LeadRef, ...],
    participants: tuple[Participant, ...],
    participant_links: tuple[ParticipantLink, ...],
) -> tuple[tuple[LeadDateResolution, ...], tuple[Issue, ...]]:
    """Apply Time Taken -> R3 fallback without hiding conflicting R1 dates."""

    participant_by_key = {item.key: item for item in participants}
    participant_key_by_lead = {
        link.subject_key: link.participant_key
        for link in participant_links
        if link.subject_kind is ParticipantLinkSubject.LEAD_REF
        and link.participant_key is not None
    }
    resolutions: list[LeadDateResolution] = []
    issues: list[Issue] = []

    for lead in lead_refs:
        time_cells = _field_cells(lead, "Time Taken")
        valid_dates: set[date] = set()
        valid_cells: list[SourceCell] = []
        for cell in time_cells:
            if cell.value_kind in {
                SourceValueKind.BLANK,
                SourceValueKind.FORMULA,
                SourceValueKind.ERROR,
            }:
                continue
            try:
                parsed = calendar_date_in_kyiv(cell.original_value)
            except DateNormalizationError:
                # The import layer already owns the DATE_INVALID issue.
                continue
            if parsed is not None:
                valid_dates.add(parsed)
                valid_cells.append(cell)

        participant_key = participant_key_by_lead.get(lead.key)
        participant = participant_by_key.get(participant_key or "")
        participant_date = (
            calendar_date_in_kyiv(participant.first_associated_at)
            if participant is not None
            else None
        )
        participant_cell = _participant_date_cell(participant)

        if len(valid_dates) > 1:
            issue_sources = tuple(valid_cells)
            issues.append(
                make_issue(
                    IssueCode.DATE_CONFLICT,
                    "Для одного ліда наявні різні коректні дати Time Taken; "
                    "дату ліда не визначено.",
                    sources=issue_sources,
                    affected_keys=(lead.key,),
                    details=(
                        (
                            "dates",
                            "; ".join(
                                sorted(item.isoformat() for item in valid_dates)
                            ),
                        ),
                    ),
                    discriminator=f"{lead.key}:multiple-time-taken",
                )
            )
            resolutions.append(
                LeadDateResolution(
                    lead_ref_key=lead.key,
                    value=None,
                    source=LeadDateSource.CONFLICT,
                    participant_key=participant_key,
                    differs_from_participant_date=False,
                    sources=issue_sources,
                )
            )
            continue

        if valid_dates:
            value = next(iter(valid_dates))
            differs = participant_date is not None and participant_date != value
            resolution_sources = tuple(valid_cells) + (
                (participant_cell,) if participant_cell is not None else ()
            )
            if differs:
                issues.append(
                    make_issue(
                        IssueCode.DATE_CONFLICT,
                        "Time Taken і Member First Associated Date мають різні "
                        "календарні дати; використано Time Taken.",
                        sources=resolution_sources,
                        affected_keys=tuple(
                            item
                            for item in (lead.key, participant_key)
                            if item is not None
                        ),
                        details=(
                            ("time_taken", value.isoformat()),
                            ("participant_date", participant_date.isoformat()),
                        ),
                        discriminator=f"{lead.key}:r1-r3-date",
                    )
                )
            resolutions.append(
                LeadDateResolution(
                    lead_ref_key=lead.key,
                    value=value,
                    source=LeadDateSource.TIME_TAKEN,
                    participant_key=participant_key,
                    differs_from_participant_date=differs,
                    sources=resolution_sources,
                )
            )
            continue

        if participant_date is not None:
            resolutions.append(
                LeadDateResolution(
                    lead_ref_key=lead.key,
                    value=participant_date,
                    source=LeadDateSource.PARTICIPANT_FALLBACK,
                    participant_key=participant_key,
                    differs_from_participant_date=False,
                    sources=(participant_cell,) if participant_cell is not None else (),
                )
            )
        else:
            resolutions.append(
                LeadDateResolution(
                    lead_ref_key=lead.key,
                    value=None,
                    source=LeadDateSource.UNKNOWN,
                    participant_key=participant_key,
                    differs_from_participant_date=False,
                    sources=time_cells,
                )
            )

    return tuple(resolutions), tuple(issues)
