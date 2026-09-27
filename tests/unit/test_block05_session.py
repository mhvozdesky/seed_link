from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from seedlink.application import (
    CancellationToken,
    DecisionValidationError,
    OperationCancelled,
    OperationPhase,
    SeedLinkSession,
    SessionErrorCode,
    SessionStatus,
    SessionStateError,
    StaleOperationError,
    validate_manual_decisions,
)
from seedlink.domain import (
    DecisionAction,
    DecisionTarget,
    InputRole,
    IssueCode,
    ManualDecision,
    PersonMatchMethod,
    VoucherMatchMethod,
    business_control_payload,
    serialize_report_result,
)


MEMBER_A = "00vAbCdEfGhIjKlIVK"
MEMBER_B = "00vAbCdEfGhIjKmIVK"
MEMBER_C = "00vAbCdEfGhIjKnIVK"


def _r1(
    record: str,
    survey: str,
    voucher: str,
    *,
    member: str | None = MEMBER_A,
    lead_name: str | None = None,
):
    return {
        "Record Nr": record,
        "Survey Response ID": survey,
        "Campaigm Member": member,
        "Lead: Full Name": lead_name,
        "Answer": "Зафіксовано",
        "Answer (Long Text)": voucher,
    }


def _r2(product: str, voucher: str, quantity: int):
    return {
        "Opportunity Product Id 18": product,
        "Voucher Number": voucher,
        "Current Year Planned Quantity": quantity,
        "Tax ID 1": "00000001",
        "Account Name": "Клієнт",
        "Species group": "SUN: Sunflowers",
    }


def _r3(member: str, first: str, last: str):
    return {
        "Campaign Member Id 18": member,
        "First Name": first,
        "Last Name": last,
        "Member Type": "Lead",
    }


def _session(workbook_set_factory, rows):
    session = SeedLinkSession()
    imported = session.import_inputs(workbook_set_factory(rows))
    assert imported.is_accepted
    return session, session.analyze()


def _measure(result, key: str) -> Decimal | None:
    return next(item for item in result.measures if item.key == key).known_value


def test_manual_voucher_override_requires_reason_recalculates_and_undoes(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-100")],
            InputRole.R2: [
                _r2("P1", "SE-100", 5),
                _r2("P2", "XY-999", 7),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey_key = automatic.surveys[0].key
    outside_voucher = next(
        item.key for item in automatic.vouchers if item.full_number == "XY-999"
    )
    session.mark_exported(automatic.calculation_id)

    with pytest.raises(DecisionValidationError) as captured:
        session.select_vouchers(
            survey_key, (outside_voucher,), reason="   "
        )
    assert captured.value.code is SessionErrorCode.REASON_REQUIRED
    assert session.state.report_result is automatic

    revised = session.select_vouchers(
        survey_key,
        (outside_voucher,),
        reason="У відповіді помилково вказано префікс і цифри",
    )

    assert revised.revision == 1
    assert _measure(revised, "main.quantity") == Decimal(7)
    assert {item.voucher_key for item in revised.accepted_links} == {
        outside_voucher
    }
    assert revised.accepted_links[0].method is VoucherMatchMethod.MANUAL
    assert revised.accepted_links[0].decision_id == revised.decisions[0].decision_id
    assert revised.decisions[0].reason == (
        "У відповіді помилково вказано префікс і цифри"
    )
    assert session.state.has_stale_export
    assert not session.state.export_is_current
    payload = serialize_report_result(revised)
    assert payload["decisions"][0]["reason"] == revised.decisions[0].reason
    assert payload["accepted_links"][0]["method"] == "manual"
    with pytest.raises(SessionStateError) as stale_export:
        session.mark_exported(automatic.calculation_id)
    assert (
        stale_export.value.code
        is SessionErrorCode.EXPORT_REVISION_MISMATCH
    )

    recalculated = session.recalculate()
    assert recalculated.revision == 2
    assert recalculated.decisions == revised.decisions
    assert _measure(recalculated, "main.quantity") == Decimal(7)

    with pytest.raises(SessionStateError) as repeated_analysis:
        session.analyze()
    assert repeated_analysis.value.code is SessionErrorCode.INVALID_STATE

    restored = session.undo_decision(revised.decisions[0].decision_id)

    assert restored.revision == 3
    assert not restored.decisions
    assert business_control_payload(restored) == business_control_payload(automatic)
    assert _measure(restored, "main.quantity") == Decimal(5)


def test_candidate_subset_and_rejecting_auto_require_reason(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-200")],
            InputRole.R2: [
                _r2("P1", "SE-200/1/А", 3),
                _r2("P2", "SE-200/2/Б", 4),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey_key = automatic.surveys[0].key
    all_candidates = tuple(item.key for item in automatic.vouchers)
    chosen = all_candidates[0]

    selected_all = session.select_vouchers(survey_key, all_candidates)
    assert {item.voucher_key for item in selected_all.accepted_links} == set(
        all_candidates
    )
    assert all(
        item.method is VoucherMatchMethod.MANUAL
        for item in selected_all.accepted_links
    )

    with pytest.raises(DecisionValidationError) as subset_error:
        session.select_vouchers(survey_key, (chosen,))
    assert subset_error.value.code is SessionErrorCode.REASON_REQUIRED

    selected = session.select_vouchers(
        survey_key,
        (chosen,),
        reason="Другий автоматичний збіг не належить відповіді",
    )
    assert {item.voucher_key for item in selected.accepted_links} == {chosen}
    assert selected.decisions[0].reason is not None

    with pytest.raises(DecisionValidationError) as captured:
        session.apply_decision(
            DecisionTarget.VOUCHER_CASE,
            survey_key,
            DecisionAction.REJECT,
        )
    assert captured.value.code is SessionErrorCode.REASON_REQUIRED

    rejected = session.apply_decision(
        DecisionTarget.VOUCHER_CASE,
        survey_key,
        DecisionAction.REJECT,
        reason="Згадка не належить цьому опитуванню",
    )
    assert not rejected.accepted_links
    assert _measure(rejected, "main.quantity") == Decimal(0)


def test_survey_and_activity_can_be_bound_to_r3_and_issues_are_audited(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1(
                    "R1",
                    "S1",
                    "SE-300",
                    member=None,
                    lead_name="Спільне Ім'я",
                )
            ],
            InputRole.R2: [_r2("P1", "SE-300", 5)],
            InputRole.R3: [
                _r3(MEMBER_A, "Спільне", "Ім'я"),
                _r3(MEMBER_B, "Спільне", "Ім'я"),
            ],
            InputRole.R4: [
                {
                    "Activity ID": "A1",
                    "Name": "Спільне Ім'я",
                    "Results": "Недодзвон",
                }
            ],
        },
    )
    survey_key = automatic.surveys[0].key
    activity_key = automatic.activities[0].key
    first, second = automatic.participants

    lead_key = automatic.surveys[0].lead_ref_keys[0]
    left_unresolved = session.apply_decision(
        DecisionTarget.LEAD_REF_PARTICIPANT,
        lead_key,
        DecisionAction.LEAVE_UNRESOLVED,
    )
    lead_issue = next(
        issue for issue in left_unresolved.issues if lead_key in issue.affected_keys
    )
    assert not lead_issue.is_resolved

    survey_bound = session.set_survey_participant(survey_key, first.key)
    assert survey_bound.revision == 2
    assert any(
        link.method is PersonMatchMethod.MANUAL
        and link.participant_key == first.key
        for link in survey_bound.participant_links
    )
    first_funnel = next(
        row for row in survey_bound.funnel_rows if row.participant_key == first.key
    )
    assert first_funnel.has_confirmed_voucher
    assert any(issue.is_resolved for issue in survey_bound.issues)

    both_bound = session.set_activity_participant(activity_key, second.key)
    second_funnel = next(
        row for row in both_bound.funnel_rows if row.participant_key == second.key
    )
    assert second_funnel.has_activity
    assert second_funnel.has_recorded_result
    assert len(both_bound.decisions) == 2

    survey_decision = next(
        item
        for item in both_bound.decisions
        if item.target is DecisionTarget.LEAD_REF_PARTICIPANT
    )
    undone = session.undo_decision(survey_decision.decision_id)
    assert len(undone.decisions) == 1
    assert not any(row.has_confirmed_voucher for row in undone.funnel_rows)
    lead_link = next(
        item
        for item in undone.participant_links
        if item.subject_key == lead_key
    )
    assert lead_link.method is PersonMatchMethod.UNRESOLVED


def test_new_import_and_close_do_not_carry_manual_decisions(
    workbook_set_factory,
):
    session, automatic = _session(workbook_set_factory, {
        InputRole.R1: [_r1("R1", "S1", "SE-400")],
        InputRole.R2: [
            _r2("P1", "SE-400", 1),
            _r2("P2", "XY-400", 2),
        ],
        InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
    })
    survey_key = automatic.surveys[0].key
    other = next(item.key for item in automatic.vouchers if item.full_number == "XY-400")
    revised = session.select_vouchers(
        survey_key,
        (other,),
        reason="Виправлення номера",
    )
    session.mark_exported(revised.calculation_id)

    paths = workbook_set_factory(
        {
            InputRole.R1: [_r1("R9", "S9", "SE-409")],
            InputRole.R2: [_r2("P9", "SE-409", 9)],
            InputRole.R3: [_r3(MEMBER_B, "Інший", "Лід")],
        }
    )
    reimported = session.import_inputs(paths)

    assert reimported.is_accepted
    assert session.state.status is SessionStatus.IMPORTED
    assert not session.state.decisions
    assert session.state.report_result is None
    assert session.state.next_decision_sequence == 1
    assert session.state.last_exported_calculation_id is None
    assert not session.state.has_stale_export

    new_automatic = session.analyze()
    assert new_automatic.snapshot.snapshot_id != automatic.snapshot.snapshot_id
    assert new_automatic.calculation_id != revised.calculation_id
    assert not session.state.export_is_current
    assert not session.state.has_stale_export

    session.close()
    assert session.state.status is SessionStatus.EMPTY
    assert not session.state.decisions
    assert session.state.last_exported_calculation_id is None


def test_cancellation_between_workbooks_preserves_prior_session_and_reports_progress(
    workbook_set_factory,
):
    rows = {
        InputRole.R1: [_r1("R1", "S1", "SE-500")],
        InputRole.R2: [_r2("P1", "SE-500", 1)],
        InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
    }
    session, original = _session(workbook_set_factory, rows)
    new_paths = workbook_set_factory(rows)
    token = CancellationToken()
    updates = []

    def progress(update):
        updates.append(update)
        if (
            update.phase is OperationPhase.IMPORTING
            and update.completed == 1
        ):
            token.cancel()

    with pytest.raises(OperationCancelled):
        session.import_inputs(
            new_paths,
            cancellation=token,
            progress=progress,
        )

    assert session.state.report_result is original
    assert updates[0].completed == 0
    assert updates[-1].completed == 1


def test_invalid_state_and_stale_background_result_are_typed(
    workbook_set_factory,
):
    session = SeedLinkSession()
    with pytest.raises(SessionStateError) as captured:
        session.analyze()
    assert captured.value.code is SessionErrorCode.INVALID_STATE

    paths = workbook_set_factory({})

    def reset_during_import(update):
        if update.phase is OperationPhase.IMPORTING and update.completed == 1:
            session.reset()

    with pytest.raises(StaleOperationError) as stale:
        session.import_inputs(paths, progress=reset_during_import)
    assert stale.value.code is SessionErrorCode.STALE_OPERATION
    assert session.state.status is SessionStatus.EMPTY


def test_manual_voucher_uses_each_mentions_lead_provenance(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", "SE-600", member=MEMBER_A),
                _r1("R2", "S1", "SE-600", member=MEMBER_B),
            ],
            InputRole.R2: [
                _r2("P1", "SE-600", 1),
                _r2("P2", "XY-601", 2),
            ],
            InputRole.R3: [
                _r3(MEMBER_A, "Лід", "Один"),
                _r3(MEMBER_B, "Лід", "Два"),
            ],
        },
    )
    survey = automatic.surveys[0]
    replacement = next(
        item.key for item in automatic.vouchers if item.full_number == "XY-601"
    )

    revised = session.select_vouchers(
        survey.key,
        (replacement,),
        reason="У двох рядках Survey виправлено номер",
        mention_keys=tuple(item.key for item in automatic.mentions),
    )

    assert {item.lead_ref_key for item in revised.accepted_links} == set(
        survey.lead_ref_keys
    )
    assert {item.voucher_key for item in revised.accepted_links} == {
        replacement
    }
    assert all(
        item.method is VoucherMatchMethod.MANUAL
        for item in revised.accepted_links
    )
    assert _measure(revised, "main.quantity") == Decimal(2)


def test_multi_lead_survey_requires_specific_lead_ref_and_preserves_other_link(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", "SE-710", member=MEMBER_A),
                _r1(
                    "R2",
                    "S1",
                    "SE-711",
                    member=MEMBER_B,
                    lead_name="Третя Особа",
                ),
            ],
            InputRole.R2: [
                _r2("P1", "SE-710", 1),
                _r2("P2", "SE-711", 2),
            ],
            InputRole.R3: [
                _r3(MEMBER_A, "Лід", "Один"),
                _r3(MEMBER_C, "Третя", "Особа"),
            ],
        },
    )
    survey = automatic.surveys[0]
    assert len(survey.lead_ref_keys) == 2
    participant_by_id = {
        item.campaign_member_id: item for item in automatic.participants
    }
    automatic_by_subject = {
        item.subject_key: item for item in automatic.participant_links
    }
    resolved_lead = next(
        key
        for key in survey.lead_ref_keys
        if automatic_by_subject[key].participant_key
        == participant_by_id[MEMBER_A].key
    )
    unresolved_lead = next(
        key
        for key in survey.lead_ref_keys
        if automatic_by_subject[key].participant_key is None
    )

    with pytest.raises(DecisionValidationError) as ambiguous_scope:
        session.set_survey_participant(
            survey.key,
            participant_by_id[MEMBER_C].key,
        )
    assert ambiguous_scope.value.code is SessionErrorCode.INVALID_DECISION
    assert dict(ambiguous_scope.value.details)["lead_ref_keys"]

    revised = session.set_lead_ref_participant(
        unresolved_lead,
        participant_by_id[MEMBER_C].key,
    )
    revised_by_subject = {
        item.subject_key: item for item in revised.participant_links
    }
    assert revised_by_subject[resolved_lead] == automatic_by_subject[resolved_lead]
    assert revised_by_subject[unresolved_lead].participant_key == (
        participant_by_id[MEMBER_C].key
    )
    assert revised_by_subject[unresolved_lead].method is PersonMatchMethod.MANUAL
    assert revised.decisions[0].target is DecisionTarget.LEAD_REF_PARTICIPANT
    assert revised.decisions[0].target_key == unresolved_lead


def test_shared_lead_ref_is_one_canonical_decision_scope(workbook_set_factory):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", "SE-720", member=MEMBER_A),
                _r1("R2", "S2", "SE-721", member=MEMBER_A),
            ],
            InputRole.R2: [
                _r2("P1", "SE-720", 1),
                _r2("P2", "SE-721", 2),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    first_survey, second_survey = automatic.surveys
    assert first_survey.lead_ref_keys == second_survey.lead_ref_keys
    participant_key = automatic.participants[0].key

    session.set_survey_participant(first_survey.key, participant_key)
    revised = session.set_survey_participant(second_survey.key, participant_key)

    assert len(revised.decisions) == 1
    assert revised.decisions[0].target is DecisionTarget.LEAD_REF_PARTICIPANT
    assert revised.decisions[0].target_key == first_survey.lead_ref_keys[0]
    assert revised.decisions[0].sequence == 2


def test_logical_person_scope_conflict_reports_both_decisions(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-730")],
            InputRole.R2: [_r2("P1", "SE-730", 1)],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey = automatic.surveys[0]
    participant_key = automatic.participants[0].key
    timestamp = datetime.now(UTC)
    survey_decision = ManualDecision(
        decision_id="decision:survey-scope",
        target=DecisionTarget.SURVEY_PARTICIPANT,
        target_key=survey.key,
        action=DecisionAction.SELECT,
        selected_keys=(participant_key,),
        reason=None,
        sequence=1,
        created_at=timestamp,
    )
    lead_decision = ManualDecision(
        decision_id="decision:lead-scope",
        target=DecisionTarget.LEAD_REF_PARTICIPANT,
        target_key=survey.lead_ref_keys[0],
        action=DecisionAction.SELECT,
        selected_keys=(participant_key,),
        reason=None,
        sequence=2,
        created_at=timestamp,
    )

    with pytest.raises(DecisionValidationError) as conflict:
        assert session.state.automatic_result is not None
        validate_manual_decisions(
            session.state.automatic_result,
            (survey_decision, lead_decision),
        )

    assert conflict.value.code is SessionErrorCode.CONFLICTING_DECISIONS
    details = dict(conflict.value.details)
    assert details["conflicting_decision_id"] == survey_decision.decision_id
    assert details["conflicting_target"] == DecisionTarget.SURVEY_PARTICIPANT.value
    assert details["decision_id"] == lead_decision.decision_id


def test_voucher_decision_resolves_only_issues_covered_by_mention_scope(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-700; QQ-777")],
            InputRole.R2: [_r2("P1", "SE-700", 1)],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey = automatic.surveys[0]
    voucher = automatic.vouchers[0]
    missing_before = next(
        item for item in automatic.issues if item.code is IssueCode.VOUCHER_NOT_FOUND
    )
    matched_mention = next(
        item for item in automatic.mentions if item.matched_text == "SE-700"
    )

    revised = session.select_vouchers(survey.key, (voucher.key,))

    missing_after = next(
        item for item in revised.issues if item.issue_id == missing_before.issue_id
    )
    assert not missing_after.is_resolved
    assert revised.decisions[0].mention_keys == (matched_mention.key,)


def test_manual_voucher_evidence_contains_only_selected_mentions(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-740; SE-741")],
            InputRole.R2: [
                _r2("P1", "SE-740", 1),
                _r2("P2", "SE-741", 2),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey = automatic.surveys[0]
    selected_voucher = next(
        item for item in automatic.vouchers if item.full_number == "SE-740"
    )
    selected_mention = next(
        item for item in automatic.mentions if item.matched_text == "SE-740"
    )

    revised = session.select_vouchers(
        survey.key,
        (selected_voucher.key,),
        mention_keys=(selected_mention.key,),
    )

    assert revised.decisions[0].mention_keys == (selected_mention.key,)
    assert revised.decisions[0].reason is None
    assert len(revised.accepted_links) == 2
    link = next(
        item
        for item in revised.accepted_links
        if item.voucher_key == selected_voucher.key
    )
    assert link.mention_keys == (selected_mention.key,)
    assert tuple(item.mention_key for item in link.evidence) == (
        selected_mention.key,
    )
    assert link.sources == (selected_mention.source,)


def test_all_manual_decision_failure_codes_are_typed(workbook_set_factory):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-750")],
            InputRole.R2: [_r2("P1", "SE-750", 1)],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    lead_key = automatic.lead_refs[0].key
    participant_key = automatic.participants[0].key

    cases = (
        (
            SessionErrorCode.UNKNOWN_TARGET,
            lambda: session.set_lead_ref_participant("missing", participant_key),
        ),
        (
            SessionErrorCode.UNKNOWN_SELECTION,
            lambda: session.set_lead_ref_participant(
                lead_key,
                "participant:missing",
                reason="Перевірка помилки",
            ),
        ),
        (
            SessionErrorCode.INVALID_DECISION,
            lambda: session.apply_decision(
                DecisionTarget.LEAD_REF_PARTICIPANT,
                lead_key,
                DecisionAction.SELECT,
            ),
        ),
        (
            SessionErrorCode.DECISION_NOT_FOUND,
            lambda: session.undo_decision("decision:missing"),
        ),
    )
    for expected, operation in cases:
        with pytest.raises(DecisionValidationError) as captured:
            operation()
        assert captured.value.code is expected


def test_reimporting_same_files_clears_export_marker_before_analysis(
    workbook_set_factory,
):
    paths = workbook_set_factory(
        {
            InputRole.R1: [_r1("R1", "S1", "SE-760")],
            InputRole.R2: [_r2("P1", "SE-760", 1)],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        }
    )
    session = SeedLinkSession()
    assert session.import_inputs(paths).is_accepted
    first = session.analyze()
    session.mark_exported(first.calculation_id)
    assert session.state.export_is_current

    assert session.import_inputs(paths).is_accepted
    second = session.analyze()

    assert second.calculation_id == first.calculation_id
    assert session.state.last_exported_calculation_id is None
    assert not session.state.export_is_current
    assert not session.state.has_stale_export


def test_rejected_import_clears_previous_result_decisions_and_export(
    workbook_set_factory,
):
    paths = workbook_set_factory(
        {
            InputRole.R1: [_r1("R1", "S1", "SE-770")],
            InputRole.R2: [
                _r2("P1", "SE-770", 1),
                _r2("P2", "XY-770", 2),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        }
    )
    session = SeedLinkSession()
    assert session.import_inputs(paths).is_accepted
    automatic = session.analyze()
    replacement = next(
        item.key for item in automatic.vouchers if item.full_number == "XY-770"
    )
    revised = session.select_vouchers(
        automatic.surveys[0].key,
        (replacement,),
        reason="Виправлення номера",
    )
    session.mark_exported(revised.calculation_id)

    rejected_paths = dict(paths)
    rejected_paths[InputRole.R2] = None
    rejected = session.import_inputs(rejected_paths)

    assert not rejected.is_accepted
    assert session.state.status is SessionStatus.IMPORT_FAILED
    assert session.state.report_result is None
    assert session.state.automatic_result is None
    assert not session.state.decisions
    assert session.state.next_decision_sequence == 1
    assert session.state.last_exported_calculation_id is None


def test_disjoint_voucher_mention_decisions_accumulate_in_one_survey(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "AA-1; BB-2")],
            InputRole.R2: [
                _r2("P1", "AA-1", 1),
                _r2("P2", "BB-2", 2),
                _r2("P3", "XX-1", 10),
                _r2("P4", "YY-2", 20),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey = automatic.surveys[0]
    mention_by_text = {item.matched_text: item for item in automatic.mentions}
    voucher_by_number = {item.full_number: item for item in automatic.vouchers}

    first = session.select_vouchers(
        survey.key,
        (voucher_by_number["XX-1"].key,),
        mention_keys=(mention_by_text["AA-1"].key,),
        reason="У першій згадці виправлено префікс",
    )
    assert {
        item.voucher_key for item in first.accepted_links
    } == {
        voucher_by_number["XX-1"].key,
        voucher_by_number["BB-2"].key,
    }
    assert _measure(first, "main.quantity") == Decimal(12)

    second = session.select_vouchers(
        survey.key,
        (voucher_by_number["YY-2"].key,),
        mention_keys=(mention_by_text["BB-2"].key,),
        reason="У другій згадці виправлено префікс",
    )

    assert len(second.decisions) == 2
    assert {
        frozenset(item.mention_keys) for item in second.decisions
    } == {
        frozenset((mention_by_text["AA-1"].key,)),
        frozenset((mention_by_text["BB-2"].key,)),
    }
    assert {
        item.voucher_key for item in second.accepted_links
    } == {
        voucher_by_number["XX-1"].key,
        voucher_by_number["YY-2"].key,
    }
    assert _measure(second, "main.quantity") == Decimal(30)

    with pytest.raises(DecisionValidationError) as conflict:
        session.apply_decision(
            DecisionTarget.VOUCHER_CASE,
            survey.key,
            DecisionAction.REJECT,
            reason="Відхилити весь випадок",
        )
    assert conflict.value.code is SessionErrorCode.CONFLICTING_DECISIONS
    assert dict(conflict.value.details)["overlapping_mention_keys"]


def test_scoped_voucher_correction_preserves_neighbor_automatic_link(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-800; QQ-801")],
            InputRole.R2: [
                _r2("P1", "SE-800", 1),
                _r2("P2", "XX-801", 9),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey = automatic.surveys[0]
    mention_by_text = {item.matched_text: item for item in automatic.mentions}
    voucher_by_number = {item.full_number: item for item in automatic.vouchers}

    revised = session.select_vouchers(
        survey.key,
        (voucher_by_number["XX-801"].key,),
        mention_keys=(mention_by_text["QQ-801"].key,),
        reason="У другій згадці неправильний префікс",
    )

    assert {
        item.voucher_key for item in revised.accepted_links
    } == {
        voucher_by_number["SE-800"].key,
        voucher_by_number["XX-801"].key,
    }
    assert _measure(revised, "main.quantity") == Decimal(10)
    automatic_link = next(
        item
        for item in revised.accepted_links
        if item.voucher_key == voucher_by_number["SE-800"].key
    )
    assert automatic_link.method is automatic.accepted_links[0].method
    assert automatic_link.decision_id is None


def test_inferred_scope_expands_to_close_multi_mention_field_conflict(
    workbook_set_factory,
):
    row = _r1("R1", "S1", "SE-601")
    row["Answer"] = "SE-600"
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [row],
            InputRole.R2: [
                _r2("P1", "SE-600", 4),
                _r2("P2", "SE-601", 6),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey = automatic.surveys[0]
    voucher = next(
        item for item in automatic.vouchers if item.full_number == "SE-600"
    )
    conflict_before = next(
        item
        for item in automatic.issues
        if item.code is IssueCode.VOUCHER_FIELD_CONFLICT
    )
    assert not automatic.accepted_links

    revised = session.select_vouchers(survey.key, (voucher.key,))

    assert set(revised.decisions[0].mention_keys) == {
        item.key for item in automatic.mentions
    }
    conflict_after = next(
        item
        for item in revised.issues
        if item.issue_id == conflict_before.issue_id
    )
    assert conflict_after.is_resolved
    assert conflict_after.resolved_by_decision_id == (
        revised.decisions[0].decision_id
    )
    assert {item.voucher_key for item in revised.accepted_links} == {
        voucher.key
    }
    assert _measure(revised, "main.quantity") == Decimal(4)

    replacement = next(
        item for item in automatic.vouchers if item.full_number == "SE-601"
    )
    replaced = session.select_vouchers(survey.key, (replacement.key,))
    assert len(replaced.decisions) == 1
    assert replaced.decisions[0].sequence == 2
    assert set(replaced.decisions[0].mention_keys) == {
        item.key for item in automatic.mentions
    }
    assert {item.voucher_key for item in replaced.accepted_links} == {
        replacement.key
    }
    assert _measure(replaced, "main.quantity") == Decimal(6)


def test_r01_multi_mention_outside_choice_requires_explicit_scope(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-810; QQ-811")],
            InputRole.R2: [
                _r2("P1", "SE-810", 1),
                _r2("P2", "XX-811", 8),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey = automatic.surveys[0]
    mention_by_text = {item.matched_text: item for item in automatic.mentions}
    voucher_by_number = {item.full_number: item for item in automatic.vouchers}

    with pytest.raises(DecisionValidationError) as missing_scope:
        session.select_vouchers(
            survey.key,
            (voucher_by_number["XX-811"].key,),
            reason="Виправлено префікс",
        )
    assert missing_scope.value.code is SessionErrorCode.INVALID_DECISION
    assert dict(missing_scope.value.details)["available_mention_keys"]

    revised = session.select_vouchers(
        survey.key,
        (voucher_by_number["XX-811"].key,),
        mention_keys=(mention_by_text["QQ-811"].key,),
        reason="Виправлено префікс другої згадки",
    )

    assert {
        item.voucher_key for item in revised.accepted_links
    } == {
        voucher_by_number["SE-810"].key,
        voucher_by_number["XX-811"].key,
    }
    assert revised.decisions[0].mention_keys == (
        mention_by_text["QQ-811"].key,
    )


def test_reject_can_target_one_of_multiple_automatic_mentions(workbook_set_factory):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-400, XY-500")],
            InputRole.R2: [_r2("P1", "SE-400", 10), _r2("P2", "XY-500", 20)],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey = automatic.surveys[0]
    mention = next(
        item for item in automatic.mentions if item.matched_text == "XY-500"
    )

    revised = session.apply_decision(
        DecisionTarget.VOUCHER_CASE,
        survey.key,
        DecisionAction.REJECT,
        mention_keys=(mention.key,),
        reason="Друга згадка помилкова",
    )

    accepted_numbers = {
        voucher.full_number
        for voucher in revised.vouchers
        if any(link.voucher_key == voucher.key for link in revised.accepted_links)
    }
    assert accepted_numbers == {"SE-400"}
    assert _measure(revised, "main.quantity") == Decimal(10)


def test_manual_evidence_keeps_its_decision_after_other_mention_is_replaced(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-400, SE-400, QQ-777")],
            InputRole.R2: [_r2("P1", "SE-400", 10), _r2("P2", "XY-500", 20)],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    survey = automatic.surveys[0]
    mentions = sorted(automatic.mentions, key=lambda item: item.start)
    vouchers = {item.full_number: item for item in automatic.vouchers}
    first = session.select_vouchers(
        survey.key,
        (vouchers["SE-400"].key,),
        mention_keys=(mentions[2].key,),
        reason="Ручне підтвердження нерозпізнаної згадки",
    )
    first_decision = first.decisions[0]
    second = session.select_vouchers(
        survey.key,
        (vouchers["XY-500"].key,),
        mention_keys=(mentions[0].key,),
        reason="Перша згадка належить іншому ваучеру",
    )

    retained = next(
        link
        for link in second.accepted_links
        if link.voucher_key == vouchers["SE-400"].key
    )
    manual = next(
        item for item in retained.evidence if item.mention_key == mentions[2].key
    )
    assert retained.method is VoucherMatchMethod.MANUAL
    assert retained.decision_id == first_decision.decision_id
    assert manual.decision_id == first_decision.decision_id


def test_rejected_decision_rolls_back_shared_command_scope(
    workbook_set_factory,
):
    session, automatic = _session(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", "SE-900")],
            InputRole.R2: [_r2("P1", "SE-900", 1)],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )
    state_before = session.state

    with pytest.raises(DecisionValidationError) as rejected:
        session.select_vouchers(automatic.surveys[0].key, ("missing-voucher",))
    assert rejected.value.code is SessionErrorCode.UNKNOWN_SELECTION
    assert session.state is state_before

    revised = session.recalculate()
    assert revised.revision == 1
    assert session.state.report_result is revised
