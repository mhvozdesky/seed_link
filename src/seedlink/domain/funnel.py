"""Campaign funnel facts over one R3 participant base."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from seedlink.domain.dates import calendar_date_in_kyiv
from seedlink.domain.measures import Measure
from seedlink.domain.models import (
    AcceptedLink,
    Activity,
    FunnelRow,
    Participant,
    ParticipantLink,
    ParticipantLinkSubject,
    Survey,
)
from seedlink.domain.normalization import text_or_none
from seedlink.domain.provenance import SourceCell, SourceValueKind


def _meaningful_answer(cell: SourceCell) -> bool:
    if cell.value_kind in {
        SourceValueKind.BLANK,
        SourceValueKind.FORMULA,
        SourceValueKind.ERROR,
        SourceValueKind.UNKNOWN,
    }:
        return False
    if cell.value_kind is SourceValueKind.TEXT:
        return text_or_none(cell.original_value) is not None
    return cell.original_value is not None


def _participant_date(participant: Participant) -> date | None:
    return calendar_date_in_kyiv(participant.first_associated_at)


def build_funnel_rows(
    participants: tuple[Participant, ...],
    surveys: tuple[Survey, ...],
    activities: tuple[Activity, ...],
    participant_links: tuple[ParticipantLink, ...],
    accepted_links: tuple[AcceptedLink, ...],
) -> tuple[FunnelRow, ...]:
    participant_by_subject = {
        (link.subject_kind, link.subject_key): link.participant_key
        for link in participant_links
        if link.participant_key is not None
    }
    activity_keys: dict[str, list[str]] = {item.key: [] for item in participants}
    result_from_activity: set[str] = set()
    for activity in activities:
        participant_key = participant_by_subject.get(
            (ParticipantLinkSubject.ACTIVITY, activity.key)
        )
        if participant_key not in activity_keys:
            continue
        activity_keys[participant_key].append(activity.key)
        if text_or_none(activity.results) is not None:
            result_from_activity.add(participant_key)

    survey_keys: dict[str, list[str]] = {item.key: [] for item in participants}
    result_from_survey: set[str] = set()
    for survey in surveys:
        linked_participants = {
            participant_by_subject.get(
                (ParticipantLinkSubject.LEAD_REF, lead_ref_key)
            )
            for lead_ref_key in survey.lead_ref_keys
        }
        linked_participants.discard(None)
        meaningful = any(_meaningful_answer(cell) for cell in survey.answer_cells)
        for participant_key in sorted(linked_participants):
            if participant_key not in survey_keys:
                continue
            survey_keys[participant_key].append(survey.key)
            if meaningful:
                result_from_survey.add(participant_key)

    voucher_keys: dict[str, list[str]] = {item.key: [] for item in participants}
    for link in accepted_links:
        participant_key = participant_by_subject.get(
            (ParticipantLinkSubject.LEAD_REF, link.lead_ref_key)
        )
        if participant_key in voucher_keys:
            voucher_keys[participant_key].append(link.voucher_key)

    rows: list[FunnelRow] = []
    for participant in participants:
        activities_for_person = tuple(dict.fromkeys(activity_keys[participant.key]))
        surveys_for_person = tuple(dict.fromkeys(survey_keys[participant.key]))
        vouchers_for_person = tuple(dict.fromkeys(voucher_keys[participant.key]))
        rows.append(
            FunnelRow(
                participant_key=participant.key,
                appeared_on=_participant_date(participant),
                member_type=participant.member_type,
                member_status=participant.member_status,
                has_activity=bool(activities_for_person),
                has_recorded_result=(
                    participant.key in result_from_activity
                    or participant.key in result_from_survey
                ),
                has_confirmed_voucher=bool(vouchers_for_person),
                activity_keys=activities_for_person,
                survey_keys=surveys_for_person,
                voucher_keys=vouchers_for_person,
            )
        )
    return tuple(rows)


def funnel_measures(
    rows: tuple[FunnelRow, ...], *, unknown_base_count: int = 0
) -> tuple[Measure, ...]:
    definitions = (
        ("participants", "Учасники кампанії", lambda row: True),
        ("activity", "Є активність", lambda row: row.has_activity),
        ("result", "Результат зафіксовано", lambda row: row.has_recorded_result),
        ("voucher", "Є підтверджений ваучер", lambda row: row.has_confirmed_voucher),
    )
    result: list[Measure] = []
    denominator = len(rows)
    for suffix, label, predicate in definitions:
        count = sum(predicate(row) for row in rows)
        key = f"funnel.{suffix}.count"
        if unknown_base_count:
            count_measure = Measure.partial(
                key,
                label,
                "осіб",
                count,
                unknown_base_count,
                ("У базі R3 є групи з невизначеним унікальним обліком.",),
            )
        else:
            count_measure = Measure.complete(key, label, "осіб", count)
        result.append(count_measure)

        rate_key = f"funnel.{suffix}.rate"
        if denominator == 0:
            result.append(
                Measure.unavailable(
                    rate_key,
                    f"{label}, частка",
                    "%",
                    ("База учасників R3 порожня.",),
                    unknown_count=unknown_base_count,
                )
            )
        elif unknown_base_count:
            result.append(
                Measure.unavailable(
                    rate_key,
                    f"{label}, частка",
                    "%",
                    ("Унікальний розмір бази R3 невідомий.",),
                    unknown_count=unknown_base_count,
                )
            )
        else:
            result.append(
                Measure.complete(
                    rate_key,
                    f"{label}, частка",
                    "%",
                    Decimal(count) * Decimal(100) / Decimal(denominator),
                )
            )
    return tuple(result)
