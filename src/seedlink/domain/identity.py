"""Deterministic participant matching without fuzzy name heuristics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256

from seedlink.domain._issue_factory import make_issue
from seedlink.domain.issues import Issue, IssueCode
from seedlink.domain.models import (
    Activity,
    LeadRef,
    Participant,
    ParticipantLink,
    ParticipantLinkSubject,
    PersonMatchMethod,
)
from seedlink.domain.normalization import normalize_name, text_or_none
from seedlink.domain.provenance import SourceCell, SourceValueKind
from seedlink.domain.salesforce_ids import salesforce_id_key


@dataclass(frozen=True, slots=True)
class PersonMatchingResult:
    links: tuple[ParticipantLink, ...]
    issues: tuple[Issue, ...]


def _unique(values):
    return tuple(dict.fromkeys(values))


def _link_key(kind: ParticipantLinkSubject, subject_key: str) -> str:
    identity = f"{kind.value}\x1f{subject_key}"
    return f"person-link:{sha256(identity.encode('utf-8')).hexdigest()}"


def _participant_name(participant: Participant) -> str | None:
    if not participant.first_name or not participant.last_name:
        return None
    return normalize_name(f"{participant.first_name} {participant.last_name}")


def _id_key(value: str) -> str:
    return salesforce_id_key(value)


def _cells(lead: LeadRef, fields: tuple[str, ...]) -> tuple[SourceCell, ...]:
    result: list[SourceCell] = []
    seen: set[str] = set()
    for source in lead.sources:
        for field in fields:
            try:
                cell = source.cell(field)
            except KeyError:
                continue
            if cell.stable_key not in seen:
                seen.add(cell.stable_key)
                result.append(cell)
    return tuple(result)


def _text_values(lead: LeadRef, field: str) -> tuple[str, ...]:
    values: list[str] = []
    for cell in _cells(lead, (field,)):
        if cell.value_kind is not SourceValueKind.TEXT:
            continue
        value = text_or_none(cell.original_value)
        if value is not None:
            normalized = normalize_name(value)
            if normalized is not None and normalized not in values:
                values.append(normalized)
    return tuple(values)


def _unresolved_link(
    *,
    kind: ParticipantLinkSubject,
    subject_key: str,
    sources: tuple[SourceCell, ...],
) -> ParticipantLink:
    return ParticipantLink(
        key=_link_key(kind, subject_key),
        subject_kind=kind,
        subject_key=subject_key,
        participant_key=None,
        method=PersonMatchMethod.UNRESOLVED,
        sources=sources,
    )


def _resolved_link(
    *,
    kind: ParticipantLinkSubject,
    subject_key: str,
    participant_key: str,
    method: PersonMatchMethod,
    sources: tuple[SourceCell, ...],
) -> ParticipantLink:
    return ParticipantLink(
        key=_link_key(kind, subject_key),
        subject_kind=kind,
        subject_key=subject_key,
        participant_key=participant_key,
        method=method,
        sources=sources,
    )


def _lead_link(
    lead: LeadRef,
    *,
    participants_by_key: dict[str, Participant],
    id_index: dict[str, tuple[str, ...]],
    name_index: dict[str, tuple[str, ...]],
) -> tuple[ParticipantLink, Issue | None]:
    sources = _cells(
        lead,
        (
            "Campaigm Member",
            "Lead: Full Name",
            "First Name",
            "Last Name",
            "Email",
        ),
    )
    if not sources:
        # LeadRef itself guarantees R1 rows; this fallback keeps the model valid
        # for hand-built domain objects with a reduced test schema.
        sources = (lead.sources[0].cells[0],)

    id_values = _unique(_id_key(value) for value in lead.campaign_member_ids)
    id_candidates = _unique(
        candidate
        for value in id_values
        for candidate in id_index.get(value, ())
    )
    unique_name_candidates: list[str] = []
    ambiguous_name_candidates: list[str] = []
    for name in lead.candidate_names:
        normalized = normalize_name(name)
        candidates = name_index.get(normalized or "", ())
        if len(candidates) == 1:
            unique_name_candidates.extend(candidates)
        elif len(candidates) > 1:
            ambiguous_name_candidates.extend(candidates)
    unique_name_candidates = list(_unique(unique_name_candidates))
    ambiguous_name_candidates = list(_unique(ambiguous_name_candidates))
    email_values = _text_values(lead, "Email")

    conflict_reason: str | None = None
    if len(id_values) > 1:
        conflict_reason = (
            "У записах R1 наявні різні ідентифікатори учасника."
        )
    elif len(id_candidates) > 1:
        conflict_reason = (
            "Ідентифікатор R1 відповідає кільком учасникам R3."
        )
    elif len(unique_name_candidates) > 1:
        conflict_reason = (
            "Поля імені R1 ведуть до різних учасників R3."
        )
    elif id_candidates and unique_name_candidates:
        if id_candidates[0] != unique_name_candidates[0]:
            conflict_reason = (
                "Ідентифікатор та ім'я R1 ведуть до різних учасників R3."
            )
    selected_key: str | None = None
    method: PersonMatchMethod | None = None
    if conflict_reason is None and len(id_candidates) == 1:
        selected_key = id_candidates[0]
        method = PersonMatchMethod.ID
    elif conflict_reason is None and len(unique_name_candidates) == 1:
        selected_key = unique_name_candidates[0]
        method = PersonMatchMethod.UNIQUE_NAME
        participant = participants_by_key[selected_key]
        if id_values and participant.campaign_member_id is not None:
            if id_values[0] != _id_key(participant.campaign_member_id):
                conflict_reason = (
                    "Наявні ідентифікатори R1 і R3 суперечать збігу за ім'ям."
                )
        if ambiguous_name_candidates:
            conflict_reason = (
                "Одне з полів імені R1 має кілька кандидатів у R3."
            )

    email_mismatch_reason: str | None = None
    if conflict_reason is None and selected_key is not None:
        participant = participants_by_key[selected_key]
        participant_email = normalize_name(participant.email)
        if len(email_values) > 1:
            email_mismatch_reason = (
                "Для одного LeadRef у R1 наявні різні адреси email."
            )
        elif (
            email_values
            and participant_email is not None
            and email_values[0] != participant_email
        ):
            email_mismatch_reason = (
                "Адреси email у R1 і R3 відрізняються."
            )
        if (
            method is PersonMatchMethod.UNIQUE_NAME
            and email_mismatch_reason is not None
        ):
            conflict_reason = (
                f"{email_mismatch_reason} Автозв'язок за ім'ям не застосовано."
            )

    if conflict_reason is not None:
        candidates = _unique(
            (*id_candidates, *unique_name_candidates, *ambiguous_name_candidates)
        )
        issue = make_issue(
            IssueCode.PERSON_LINK_CONFLICT,
            conflict_reason,
            sources=sources,
            affected_keys=(lead.key,),
            candidate_keys=candidates,
            discriminator=lead.key,
        )
        return (
            _unresolved_link(
                kind=ParticipantLinkSubject.LEAD_REF,
                subject_key=lead.key,
                sources=sources,
            ),
            issue,
        )

    if selected_key is not None and method is not None:
        link = _resolved_link(
            kind=ParticipantLinkSubject.LEAD_REF,
            subject_key=lead.key,
            participant_key=selected_key,
            method=method,
            sources=sources,
        )
        if method is PersonMatchMethod.ID and email_mismatch_reason is not None:
            return (
                link,
                make_issue(
                    IssueCode.PERSON_DATA_MISMATCH,
                    f"{email_mismatch_reason} Зв'язок збережено за точним ID.",
                    sources=sources,
                    affected_keys=(lead.key, selected_key),
                    candidate_keys=(selected_key,),
                    discriminator=f"{lead.key}:email",
                ),
            )
        return link, None

    if ambiguous_name_candidates:
        code = IssueCode.PERSON_LINK_AMBIGUOUS
        message = (
            "Повне ім'я R1 відповідає кільком учасникам R3."
        )
        candidates = tuple(ambiguous_name_candidates)
    else:
        code = IssueCode.PERSON_LINK_UNRESOLVED
        message = (
            "Для LeadRef не знайдено підтвердженого учасника R3."
        )
        candidates = ()
    issue = make_issue(
        code,
        message,
        sources=sources,
        affected_keys=(lead.key,),
        candidate_keys=candidates,
        discriminator=lead.key,
    )
    return (
        _unresolved_link(
            kind=ParticipantLinkSubject.LEAD_REF,
            subject_key=lead.key,
            sources=sources,
        ),
        issue,
    )


def _activity_link(
    activity: Activity,
    *,
    name_index: dict[str, tuple[str, ...]],
) -> tuple[ParticipantLink, Issue | None]:
    sources = tuple(
        source.cell("Name")
        for source in activity.sources
        if any(cell.field_name == "Name" for cell in source.cells)
    )
    if not sources:
        sources = (activity.sources[0].cells[0],)
    normalized = normalize_name(activity.person_name)
    candidates = name_index.get(normalized or "", ())
    if len(candidates) == 1:
        return (
            _resolved_link(
                kind=ParticipantLinkSubject.ACTIVITY,
                subject_key=activity.key,
                participant_key=candidates[0],
                method=PersonMatchMethod.UNIQUE_NAME,
                sources=sources,
            ),
            None,
        )
    if len(candidates) > 1:
        code = IssueCode.PERSON_LINK_AMBIGUOUS
        message = (
            "Повне ім'я активності R4 відповідає кільком учасникам R3."
        )
    else:
        code = IssueCode.PERSON_LINK_UNRESOLVED
        message = (
            "Для активності R4 не знайдено учасника за повним ім'ям."
        )
    issue = make_issue(
        code,
        message,
        sources=sources,
        affected_keys=(activity.key,),
        candidate_keys=candidates,
        discriminator=activity.key,
    )
    return (
        _unresolved_link(
            kind=ParticipantLinkSubject.ACTIVITY,
            subject_key=activity.key,
            sources=sources,
        ),
        issue,
    )


def match_participants(
    participants: tuple[Participant, ...],
    lead_refs: tuple[LeadRef, ...],
    activities: tuple[Activity, ...],
) -> PersonMatchingResult:
    """Match R1/R4 people to R3 by ID, then by an exact unique full name."""

    id_lists: dict[str, list[str]] = defaultdict(list)
    name_lists: dict[str, list[str]] = defaultdict(list)
    participants_by_key = {participant.key: participant for participant in participants}
    for participant in participants:
        if participant.campaign_member_id:
            id_lists[_id_key(participant.campaign_member_id)].append(participant.key)
        name = _participant_name(participant)
        if name:
            name_lists[name].append(participant.key)
    id_index = {key: tuple(values) for key, values in id_lists.items()}
    name_index = {key: tuple(values) for key, values in name_lists.items()}

    links: list[ParticipantLink] = []
    issues: list[Issue] = []
    for lead in lead_refs:
        link, issue = _lead_link(
            lead,
            participants_by_key=participants_by_key,
            id_index=id_index,
            name_index=name_index,
        )
        links.append(link)
        if issue is not None:
            issues.append(issue)
    for activity in activities:
        link, issue = _activity_link(activity, name_index=name_index)
        links.append(link)
        if issue is not None:
            issues.append(issue)
    return PersonMatchingResult(tuple(links), tuple(issues))
