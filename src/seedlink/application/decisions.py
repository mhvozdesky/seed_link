"""Apply immutable, snapshot-scoped manual decisions to automatic facts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import NoReturn

from seedlink.application.analysis import AutomaticMatchingResult
from seedlink.application.errors import (
    DecisionValidationError,
    SessionErrorCode,
    require_single_lead_ref_key,
)
from seedlink.application.reporting import build_report_result
from seedlink.domain.issues import Issue, IssueCode
from seedlink.domain.models import (
    AcceptedLink,
    DecisionAction,
    DecisionTarget,
    ManualDecision,
    ParticipantLink,
    ParticipantLinkSubject,
    PersonMatchMethod,
    ReportResult,
    Survey,
    VoucherMatchEvidence,
    VoucherMatchMethod,
    VoucherMention,
)
from seedlink.domain.voucher_matching import (
    accepted_link_key,
    voucher_match_method_priority,
)


_PERSON_ISSUE_CODES = frozenset(
    {
        IssueCode.PERSON_LINK_AMBIGUOUS,
        IssueCode.PERSON_LINK_CONFLICT,
        IssueCode.PERSON_LINK_UNRESOLVED,
        IssueCode.PERSON_DATA_MISMATCH,
    }
)
VOUCHER_DECISION_ISSUE_CODES = frozenset(
    {
        IssueCode.VOUCHER_FIELD_CONFLICT,
        IssueCode.VOUCHER_MATCH_AMBIGUOUS,
        IssueCode.VOUCHER_NOT_FOUND,
    }
)


@dataclass(frozen=True, slots=True)
class _DecisionContext:
    survey_by_key: dict[str, Survey]
    lead_ref_keys: frozenset[str]
    activity_keys: frozenset[str]
    participant_keys: frozenset[str]
    voucher_keys: frozenset[str]
    mention_by_key: dict[str, VoucherMention]
    mentions_by_survey: dict[str, tuple[VoucherMention, ...]]
    issues_by_affected_key: dict[str, tuple[Issue, ...]]
    participant_link_by_subject: dict[
        tuple[ParticipantLinkSubject, str], ParticipantLink
    ]
    automatic_vouchers_by_mention: dict[str, frozenset[str]]
    vouchers_by_mention: dict[str, frozenset[str]]

    @classmethod
    def build(cls, matching: AutomaticMatchingResult) -> _DecisionContext:
        mention_lists: dict[str, list[VoucherMention]] = defaultdict(list)
        voucher_lists_by_mention: dict[str, set[str]] = defaultdict(set)
        for mention in matching.mentions:
            mention_lists[mention.survey_key].append(mention)
            voucher_lists_by_mention[mention.key].update(
                mention.candidate_voucher_keys
            )

        issue_lists: dict[str, list[Issue]] = defaultdict(list)
        for issue in matching.issues:
            for affected_key in issue.affected_keys:
                issue_lists[affected_key].append(issue)

        automatic_lists_by_mention: dict[str, set[str]] = defaultdict(set)
        for link in matching.accepted_links:
            for evidence in link.evidence:
                automatic_lists_by_mention[evidence.mention_key].add(
                    link.voucher_key
                )
                voucher_lists_by_mention[evidence.mention_key].add(
                    link.voucher_key
                )

        return cls(
            survey_by_key={item.key: item for item in matching.surveys},
            lead_ref_keys=frozenset(item.key for item in matching.lead_refs),
            activity_keys=frozenset(item.key for item in matching.activities),
            participant_keys=frozenset(
                item.key for item in matching.participants
            ),
            voucher_keys=frozenset(item.key for item in matching.vouchers),
            mention_by_key={item.key: item for item in matching.mentions},
            mentions_by_survey={
                key: tuple(values) for key, values in mention_lists.items()
            },
            issues_by_affected_key={
                key: tuple(values) for key, values in issue_lists.items()
            },
            participant_link_by_subject={
                (item.subject_kind, item.subject_key): item
                for item in matching.participant_links
            },
            automatic_vouchers_by_mention={
                key: frozenset(values)
                for key, values in automatic_lists_by_mention.items()
            },
            vouchers_by_mention={
                key: frozenset(values)
                for key, values in voucher_lists_by_mention.items()
            },
        )


def _decision_error(
    code: SessionErrorCode,
    message_uk: str,
    *,
    details: tuple[tuple[str, str], ...] = (),
) -> NoReturn:
    raise DecisionValidationError(code, message_uk, details=details)


def _require_reason(decision: ManualDecision, outside_automatic: bool) -> None:
    if outside_automatic and not (decision.reason and decision.reason.strip()):
        _decision_error(
            SessionErrorCode.REASON_REQUIRED,
            "Для рішення поза автоматичними правилами вкажіть коротку причину.",
        )


def _survey(
    context: _DecisionContext,
    target_key: str,
    *,
    purpose_uk: str,
) -> Survey:
    survey = context.survey_by_key.get(target_key)
    if survey is None:
        _decision_error(
            SessionErrorCode.UNKNOWN_TARGET,
            f"Опитування для {purpose_uk} не знайдено в поточному комплекті.",
            details=(("target_key", target_key),),
        )
    return survey


def _person_subjects(
    context: _DecisionContext,
    decision: ManualDecision,
) -> tuple[tuple[ParticipantLinkSubject, str], ...]:
    if decision.target is DecisionTarget.LEAD_REF_PARTICIPANT:
        if decision.target_key not in context.lead_ref_keys:
            _decision_error(
                SessionErrorCode.UNKNOWN_TARGET,
                "LeadRef для ручного рішення не знайдено в поточному комплекті.",
                details=(("target_key", decision.target_key),),
            )
        return ((ParticipantLinkSubject.LEAD_REF, decision.target_key),)
    if decision.target is DecisionTarget.SURVEY_PARTICIPANT:
        survey = _survey(
            context,
            decision.target_key,
            purpose_uk="ручного рішення",
        )
        lead_ref_key = require_single_lead_ref_key(survey)
        return ((ParticipantLinkSubject.LEAD_REF, lead_ref_key),)
    if decision.target is DecisionTarget.ACTIVITY_PARTICIPANT:
        if decision.target_key not in context.activity_keys:
            _decision_error(
                SessionErrorCode.UNKNOWN_TARGET,
                "Активність для ручного рішення не знайдено в поточному комплекті.",
                details=(("target_key", decision.target_key),),
            )
        return ((ParticipantLinkSubject.ACTIVITY, decision.target_key),)
    _decision_error(
        SessionErrorCode.INVALID_DECISION,
        "Це рішення не є прив'язкою учасника.",
    )


def _issues_for_keys(
    context: _DecisionContext,
    affected_keys: set[str],
) -> tuple[Issue, ...]:
    result: dict[str, Issue] = {}
    for affected_key in affected_keys:
        for issue in context.issues_by_affected_key.get(affected_key, ()):
            result[issue.issue_id] = issue
    return tuple(result.values())


def _validate_voucher_decision(
    context: _DecisionContext,
    decision: ManualDecision,
) -> None:
    survey = _survey(
        context,
        decision.target_key,
        purpose_uk="ручного вибору ваучера",
    )
    missing_vouchers = set(decision.selected_keys) - context.voucher_keys
    if missing_vouchers:
        _decision_error(
            SessionErrorCode.UNKNOWN_SELECTION,
            "Вибраний ваучер відсутній у R2 поточного комплекту.",
            details=(("voucher_keys", ", ".join(sorted(missing_vouchers))),),
        )

    survey_mentions = context.mentions_by_survey.get(survey.key, ())
    survey_mention_keys = {item.key for item in survey_mentions}
    scoped_keys = set(decision.mention_keys)
    invalid_mentions = scoped_keys - survey_mention_keys
    if invalid_mentions:
        _decision_error(
            SessionErrorCode.UNKNOWN_SELECTION,
            "Область рішення містить згадку з іншого Survey або комплекту.",
            details=(("mention_keys", ", ".join(sorted(invalid_mentions))),),
        )
    if not scoped_keys:
        _decision_error(
            SessionErrorCode.INVALID_DECISION,
            "Ваучерне рішення має містити хоча б один mention_key.",
        )
    if decision.action is not DecisionAction.SELECT and (
        scoped_keys != survey_mention_keys
    ):
        _decision_error(
            SessionErrorCode.INVALID_DECISION,
            "REJECT і LEAVE_UNRESOLVED застосовуються до всіх згадок Survey.",
            details=(
                ("survey_key", survey.key),
                ("mention_keys", ", ".join(decision.mention_keys)),
            ),
        )

    scoped_mentions = tuple(
        context.mention_by_key[key] for key in decision.mention_keys
    )
    if decision.action is DecisionAction.SELECT and not any(
        mention.lead_ref_key is not None for mention in scoped_mentions
    ):
        _decision_error(
            SessionErrorCode.INVALID_DECISION,
            "У вибраних згадках немає LeadRef для ручного ваучерного зв'язку.",
        )

    automatic: set[str] = set()
    for mention in scoped_mentions:
        automatic.update(
            context.automatic_vouchers_by_mention.get(
                mention.key, frozenset()
            )
        )
    allowed: set[str] = set()
    for mention in scoped_mentions:
        allowed.update(context.vouchers_by_mention.get(mention.key, frozenset()))

    if decision.action is DecisionAction.SELECT:
        selected = set(decision.selected_keys)
        for voucher_key in selected:
            related = any(
                voucher_key
                in context.vouchers_by_mention.get(mention.key, frozenset())
                for mention in scoped_mentions
            )
            if not related and len(selected) > 1:
                _decision_error(
                    SessionErrorCode.INVALID_DECISION,
                    "Для кількох ручних ваучерів неможливо однозначно "
                    "визначити згадки-докази. Виберіть згадку для кожного "
                    "ваучера й застосуйте рішення окремо.",
                    details=(("voucher_key", voucher_key),),
                )
        outside = not selected.issubset(allowed) or not automatic.issubset(
            selected
        )
        _require_reason(decision, outside)
    else:
        _require_reason(decision, bool(automatic))


def _validate_person_decision(
    context: _DecisionContext,
    decision: ManualDecision,
) -> None:
    if decision.mention_keys:
        _decision_error(
            SessionErrorCode.INVALID_DECISION,
            "mention_keys дозволені лише для ваучерного рішення.",
        )
    subjects = _person_subjects(context, decision)
    missing = set(decision.selected_keys) - context.participant_keys
    if missing:
        _decision_error(
            SessionErrorCode.UNKNOWN_SELECTION,
            "Вибраний учасник відсутній у R3 поточного комплекту.",
            details=(("participant_keys", ", ".join(sorted(missing))),),
        )
    if decision.action is DecisionAction.SELECT and len(decision.selected_keys) != 1:
        _decision_error(
            SessionErrorCode.INVALID_DECISION,
            "Для LeadRef або Activity можна вибрати рівно одного учасника R3.",
        )

    automatic = {
        link.participant_key
        for subject in subjects
        if (link := context.participant_link_by_subject.get(subject)) is not None
        and link.participant_key is not None
    }
    affected = {subject_key for _, subject_key in subjects}
    allowed = set(automatic)
    for issue in _issues_for_keys(context, affected):
        if issue.code in _PERSON_ISSUE_CODES:
            allowed.update(issue.candidate_keys)
    if decision.action is DecisionAction.SELECT:
        _require_reason(decision, not set(decision.selected_keys).issubset(allowed))
    else:
        _require_reason(decision, bool(automatic))


def _raise_conflicting_decisions(
    decision: ManualDecision,
    owner: ManualDecision,
    *,
    overlapping_mentions: tuple[str, ...] = (),
) -> NoReturn:
    details = (
        ("decision_id", decision.decision_id),
        ("target", decision.target.value),
        ("target_key", decision.target_key),
        ("conflicting_decision_id", owner.decision_id),
        ("conflicting_target", owner.target.value),
        ("conflicting_target_key", owner.target_key),
    )
    if overlapping_mentions:
        details += (
            ("mention_keys", ", ".join(decision.mention_keys)),
            ("conflicting_mention_keys", ", ".join(owner.mention_keys)),
            ("overlapping_mention_keys", ", ".join(overlapping_mentions)),
        )
    _decision_error(
        SessionErrorCode.CONFLICTING_DECISIONS,
        "Рішення "
        f"{decision.decision_id} ({decision.target.value}:"
        f"{decision.target_key}) конфліктує з "
        f"{owner.decision_id} ({owner.target.value}:"
        f"{owner.target_key}).",
        details=details,
    )


def _validate_manual_decisions(
    context: _DecisionContext,
    decisions: tuple[ManualDecision, ...],
) -> None:
    if tuple(sorted(decisions, key=lambda item: item.sequence)) != decisions:
        _decision_error(
            SessionErrorCode.INVALID_DECISION,
            "Ручні рішення мають бути передані в порядку застосування.",
        )
    ids = [item.decision_id for item in decisions]
    sequences = [item.sequence for item in decisions]
    if len(ids) != len(set(ids)) or len(sequences) != len(set(sequences)):
        _decision_error(
            SessionErrorCode.INVALID_DECISION,
            "Ідентифікатори й порядкові номери рішень мають бути унікальними.",
        )

    person_owner: dict[
        tuple[ParticipantLinkSubject, str], ManualDecision
    ] = {}
    voucher_owner: dict[tuple[str, str], ManualDecision] = {}
    for decision in decisions:
        if not isinstance(decision.target, DecisionTarget) or not isinstance(
            decision.action, DecisionAction
        ):
            _decision_error(
                SessionErrorCode.INVALID_DECISION,
                "Тип цілі або дії ручного рішення не підтримується.",
            )
        if decision.target is DecisionTarget.VOUCHER_CASE:
            _validate_voucher_decision(context, decision)
            for mention_key in decision.mention_keys:
                scope = (decision.target_key, mention_key)
                owner = voucher_owner.get(scope)
                if owner is not None:
                    overlap = tuple(
                        key
                        for key in decision.mention_keys
                        if key in set(owner.mention_keys)
                    )
                    _raise_conflicting_decisions(
                        decision,
                        owner,
                        overlapping_mentions=overlap,
                    )
                voucher_owner[scope] = decision
            continue

        _validate_person_decision(context, decision)
        for subject in _person_subjects(context, decision):
            owner = person_owner.get(subject)
            if owner is not None:
                _raise_conflicting_decisions(decision, owner)
            person_owner[subject] = decision


def validate_manual_decisions(
    matching: AutomaticMatchingResult,
    decisions: tuple[ManualDecision, ...],
) -> None:
    """Validate targets, choices, order and departures from automatic rules."""

    _validate_manual_decisions(_DecisionContext.build(matching), decisions)


def _strip_mentions_from_links(
    context: _DecisionContext,
    links: tuple[AcceptedLink, ...],
    mention_keys: tuple[str, ...],
) -> tuple[AcceptedLink, ...]:
    scoped_keys = set(mention_keys)
    result: list[AcceptedLink] = []
    for link in links:
        if scoped_keys.isdisjoint(link.mention_keys):
            result.append(link)
            continue
        evidence = tuple(
            item
            for item in link.evidence
            if item.mention_key not in scoped_keys
        )
        if not evidence:
            continue
        remaining_mention_keys = tuple(
            item.mention_key for item in evidence
        )
        survey_keys = tuple(dict.fromkeys(item.survey_key for item in evidence))
        sources = tuple(
            dict.fromkeys(
                context.mention_by_key[key].source
                for key in remaining_mention_keys
            )
        )
        method = min(
            (item.method for item in evidence),
            key=voucher_match_method_priority,
        )
        result.append(
            replace(
                link,
                survey_keys=survey_keys,
                mention_keys=remaining_mention_keys,
                sources=sources,
                method=method,
                decision_id=(
                    link.decision_id
                    if method is VoucherMatchMethod.MANUAL
                    else None
                ),
                evidence=evidence,
            )
        )
    return tuple(result)


def _manual_mentions_for_voucher(
    context: _DecisionContext,
    decision: ManualDecision,
    voucher_key: str,
) -> tuple[VoucherMention, ...]:
    scoped = tuple(
        context.mention_by_key[key] for key in decision.mention_keys
    )
    related = tuple(
        mention
        for mention in scoped
        if voucher_key
        in context.vouchers_by_mention.get(mention.key, frozenset())
    )
    return related or scoped


def _apply_voucher_decision(
    context: _DecisionContext,
    links: tuple[AcceptedLink, ...],
    decision: ManualDecision,
) -> tuple[AcceptedLink, ...]:
    links = _strip_mentions_from_links(
        context, links, decision.mention_keys
    )
    if decision.action is not DecisionAction.SELECT:
        return links

    link_by_pair = {
        (item.lead_ref_key, item.voucher_key): item for item in links
    }
    for voucher_key in decision.selected_keys:
        mentions_by_lead: dict[str, list[VoucherMention]] = defaultdict(list)
        for mention in _manual_mentions_for_voucher(
            context, decision, voucher_key
        ):
            if mention.lead_ref_key is not None:
                mentions_by_lead[mention.lead_ref_key].append(mention)
        for lead_ref_key, grouped_mentions in mentions_by_lead.items():
            mentions = tuple(grouped_mentions)
            pair = (lead_ref_key, voucher_key)
            existing = link_by_pair.get(pair)
            manual_evidence = tuple(
                VoucherMatchEvidence(
                    mention_key=mention.key,
                    survey_key=decision.target_key,
                    method=VoucherMatchMethod.MANUAL,
                )
                for mention in mentions
            )
            if existing is None:
                link = AcceptedLink(
                    key=accepted_link_key(*pair),
                    lead_ref_key=lead_ref_key,
                    voucher_key=voucher_key,
                    survey_keys=(decision.target_key,),
                    mention_keys=tuple(item.key for item in mentions),
                    method=VoucherMatchMethod.MANUAL,
                    sources=tuple(
                        dict.fromkeys(item.source for item in mentions)
                    ),
                    decision_id=decision.decision_id,
                    evidence=manual_evidence,
                )
                links += (link,)
                link_by_pair[pair] = link
                continue

            evidence_by_mention = {
                item.mention_key: item for item in existing.evidence
            }
            for item in manual_evidence:
                evidence_by_mention[item.mention_key] = item
            sources = tuple(
                dict.fromkeys(
                    existing.sources
                    + tuple(item.source for item in mentions)
                )
            )
            updated = replace(
                existing,
                survey_keys=tuple(
                    dict.fromkeys(
                        existing.survey_keys + (decision.target_key,)
                    )
                ),
                mention_keys=tuple(evidence_by_mention),
                method=VoucherMatchMethod.MANUAL,
                sources=sources,
                decision_id=decision.decision_id,
                evidence=tuple(evidence_by_mention.values()),
            )
            links = tuple(
                updated if item.key == existing.key else item
                for item in links
            )
            link_by_pair[pair] = updated
    return links


def _apply_person_decision(
    context: _DecisionContext,
    links: tuple[ParticipantLink, ...],
    decision: ManualDecision,
) -> tuple[ParticipantLink, ...]:
    subjects = set(_person_subjects(context, decision))
    selected = decision.selected_keys[0] if decision.selected_keys else None
    return tuple(
        replace(
            link,
            participant_key=selected,
            method=(
                PersonMatchMethod.MANUAL
                if selected is not None
                else PersonMatchMethod.UNRESOLVED
            ),
            decision_id=decision.decision_id,
        )
        if (link.subject_kind, link.subject_key) in subjects
        else link
        for link in links
    )


def _resolve_related_issues(
    context: _DecisionContext,
    issues: tuple[Issue, ...],
    decision: ManualDecision,
) -> tuple[Issue, ...]:
    if decision.target is DecisionTarget.VOUCHER_CASE:
        scoped_mentions = set(decision.mention_keys)

        def is_covered(issue: Issue) -> bool:
            issue_mentions = set(issue.affected_keys).intersection(
                context.mention_by_key
            )
            return bool(issue_mentions) and issue_mentions.issubset(
                scoped_mentions
            )

        codes = VOUCHER_DECISION_ISSUE_CODES
    else:
        affected = {
            subject_key
            for _, subject_key in _person_subjects(context, decision)
        }

        def is_covered(issue: Issue) -> bool:
            return bool(affected.intersection(issue.affected_keys))

        codes = _PERSON_ISSUE_CODES

    resolution = (
        None
        if decision.action is DecisionAction.LEAVE_UNRESOLVED
        else decision.decision_id
    )
    return tuple(
        replace(issue, resolved_by_decision_id=resolution)
        if issue.code in codes and is_covered(issue)
        else issue
        for issue in issues
    )


def apply_manual_decisions(
    matching: AutomaticMatchingResult,
    decisions: tuple[ManualDecision, ...],
    *,
    check_cancelled: Callable[[], None] | None = None,
) -> AutomaticMatchingResult:
    """Return adjusted facts while preserving the immutable automatic baseline."""

    context = _DecisionContext.build(matching)
    _validate_manual_decisions(context, decisions)
    checkpoint = check_cancelled or (lambda: None)
    participant_links = matching.participant_links
    accepted_links = matching.accepted_links
    issues = matching.issues
    checkpoint()
    for decision in decisions:
        if decision.target is DecisionTarget.VOUCHER_CASE:
            accepted_links = _apply_voucher_decision(
                context, accepted_links, decision
            )
        else:
            participant_links = _apply_person_decision(
                context, participant_links, decision
            )
        issues = _resolve_related_issues(context, issues, decision)
        checkpoint()
    return replace(
        matching,
        participant_links=participant_links,
        accepted_links=accepted_links,
        issues=issues,
    )


def recalculate_with_decisions(
    matching: AutomaticMatchingResult,
    decisions: tuple[ManualDecision, ...],
    *,
    revision: int,
    calculated_at: datetime | None = None,
    check_cancelled: Callable[[], None] | None = None,
    decisions_applied: Callable[[], None] | None = None,
) -> ReportResult:
    adjusted = apply_manual_decisions(
        matching,
        decisions,
        check_cancelled=check_cancelled,
    )
    if decisions_applied is not None:
        decisions_applied()
    return build_report_result(
        adjusted,
        revision=revision,
        calculated_at=calculated_at,
        decisions=decisions,
        check_cancelled=check_cancelled,
    )
