from __future__ import annotations

from datetime import datetime, UTC
from decimal import Decimal

import pytest

from seedlink.domain.models import (
    AcceptedLink,
    Activity,
    DecisionAction,
    DecisionTarget,
    ManualDecision,
    ParticipantLink,
    ParticipantLinkSubject,
    PersonMatchMethod,
    ProductLine,
    ReportResult,
    VoucherMatchMethod,
    VoucherMention,
)
from seedlink.domain.provenance import InputRole, InputSnapshot, InputSource


def test_product_line_keeps_decimal_and_rejects_negative(source_record_factory):
    source = source_record_factory(InputRole.R2)
    line = ProductLine(
        key="PL-1",
        product_id="00001",
        voucher_key="V-1",
        quantity=Decimal("0.1"),
        species_group="Кукурудза",
        hybrid="H-1",
        tax_id="00123456",
        account_name="Господарство",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        sources=(source,),
    )
    assert line.quantity + Decimal("0.2") == Decimal("0.3")
    assert line.tax_id == "00123456"

    with pytest.raises(ValueError, match="non-negative"):
        ProductLine(
            key="PL-2",
            product_id=None,
            voucher_key="V-1",
            quantity=Decimal("-1"),
            species_group=None,
            hybrid=None,
            tax_id=None,
            account_name=None,
            created_at=None,
            sources=(source,),
        )


def test_manual_decision_has_scoped_target_and_order():
    decision = ManualDecision(
        decision_id="D-1",
        target=DecisionTarget.VOUCHER_CASE,
        target_key="S-1",
        action=DecisionAction.SELECT,
        selected_keys=("V-1", "V-2"),
        reason="Виправлено одну цифру у згадці",
        sequence=1,
        created_at=datetime.now(UTC),
    )
    assert decision.key == "D-1"

    with pytest.raises(ValueError, match="selected keys"):
        ManualDecision(
            decision_id="D-2",
            target=DecisionTarget.VOUCHER_CASE,
            target_key="S-1",
            action=DecisionAction.REJECT,
            selected_keys=("V-1",),
            reason=None,
            sequence=2,
            created_at=datetime.now(UTC),
        )


def test_unresolved_person_link_cannot_silently_select_participant(
    source_cell_factory,
):
    with pytest.raises(ValueError, match="unresolved"):
        ParticipantLink(
            key="person-link-1",
            subject_kind=ParticipantLinkSubject.LEAD_REF,
            subject_key="L-1",
            participant_key="P-1",
            method=PersonMatchMethod.UNRESOLVED,
            sources=(source_cell_factory(),),
        )


def test_activity_identity_link_is_only_represented_by_participant_link(
    source_cell_factory, source_record_factory
):
    activity = Activity(
        key="A-1",
        activity_id="ACT-1",
        person_name="Учасник Один",
        results="Зафіксовано результат",
        completed_at=None,
        sources=(source_record_factory(InputRole.R4),),
    )
    link = ParticipantLink(
        key="A-1:P-1",
        subject_kind=ParticipantLinkSubject.ACTIVITY,
        subject_key=activity.key,
        participant_key="P-1",
        method=PersonMatchMethod.UNIQUE_NAME,
        sources=(source_cell_factory(InputRole.R4),),
    )

    assert not hasattr(activity, "participant_key")
    assert link.subject_kind is ParticipantLinkSubject.ACTIVITY


def test_participant_link_requires_subject_role_provenance(source_cell_factory):
    with pytest.raises(ValueError, match="R4 provenance"):
        ParticipantLink(
            key="A-1:P-1",
            subject_kind=ParticipantLinkSubject.ACTIVITY,
            subject_key="A-1",
            participant_key="P-1",
            method=PersonMatchMethod.ID,
            sources=(source_cell_factory(InputRole.R1),),
        )


def test_voucher_mention_preserves_exact_source_span(source_cell_factory):
    text = "Номер SE-00001234 у відповіді"
    start = text.index("SE-")
    mention = VoucherMention(
        key="M-1",
        survey_key="S-1",
        lead_ref_key="L-1",
        source=source_cell_factory(field="Answer", value=text),
        original_text=text,
        start=start,
        end=start + len("SE-00001234"),
        normalized_token="se-00001234",
        candidate_voucher_keys=("V-1", "V-2"),
    )
    assert mention.matched_text == "SE-00001234"


def test_accepted_manual_link_requires_decision(source_cell_factory):
    with pytest.raises(ValueError, match="decision"):
        AcceptedLink(
            key="L-1:V-1",
            lead_ref_key="L-1",
            voucher_key="V-1",
            survey_keys=("S-1",),
            mention_keys=("M-1",),
            method=VoucherMatchMethod.MANUAL,
            sources=(source_cell_factory(),),
        )


def test_report_result_carries_complete_snapshot_metadata(source_record_factory):
    sources = tuple(
        InputSource(
            role=role,
            file_name=f"{role.value}.xlsx",
            file_sha256="a" * 64,
            sheet_name=role.value,
            schema_version="1",
            row_count=1,
            records=(source_record_factory(role),),
        )
        for role in InputRole
    )
    snapshot = InputSnapshot(
        snapshot_id="snapshot-1",
        program_version="0.1.0",
        loaded_at=datetime.now(UTC),
        sources=sources,
    )
    result = ReportResult(
        calculation_id="calculation-1",
        snapshot=snapshot,
        revision=0,
        calculated_at=datetime.now(UTC),
        surveys=(),
        participants=(),
        activities=(),
        lead_refs=(),
        participant_links=(),
        mentions=(),
        vouchers=(),
        product_lines=(),
        accepted_links=(),
        clients=(),
        decisions=(),
        issues=(),
        measures=(),
    )

    assert result.snapshot_id == "snapshot-1"
    assert result.snapshot.program_version == "0.1.0"
    assert {source.role for source in result.snapshot.sources} == set(InputRole)
    assert {source.schema_version for source in result.snapshot.sources} == {"1"}
    assert {source.file_sha256 for source in result.snapshot.sources} == {"a" * 64}
