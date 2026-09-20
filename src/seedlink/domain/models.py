"""Immutable business entities for SeedLink calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from seedlink.domain.issues import Issue
from seedlink.domain.measures import Measure
from seedlink.domain.provenance import InputRole, InputSnapshot, SourceCell, SourceRecord


def _required(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _unique(values: tuple[str, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique values")


def _sources_have_role(
    sources: tuple[SourceRecord, ...] | tuple[SourceCell, ...], role: InputRole
) -> bool:
    return all(source.role is role for source in sources)


class HasKey(Protocol):
    key: str


class PersonMatchMethod(StrEnum):
    ID = "id"
    UNIQUE_NAME = "unique_name"
    MANUAL = "manual"
    UNRESOLVED = "unresolved"


class ParticipantLinkSubject(StrEnum):
    LEAD_REF = "lead_ref"
    ACTIVITY = "activity"


class VoucherMatchMethod(StrEnum):
    EXACT_FULL = "exact_full"
    SHORT_BLOCK = "short_block"
    DISTRIBUTOR_VARIANT = "distributor_variant"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class VoucherMatchEvidence:
    """The matching rule used for one source mention in an accepted link."""

    mention_key: str
    survey_key: str
    method: VoucherMatchMethod

    def __post_init__(self) -> None:
        _required(self.mention_key, "mention_key")
        _required(self.survey_key, "survey_key")


class DecisionTarget(StrEnum):
    VOUCHER_CASE = "voucher_case"
    SURVEY_PARTICIPANT = "survey_participant"
    ACTIVITY_PARTICIPANT = "activity_participant"


class DecisionAction(StrEnum):
    SELECT = "select"
    REJECT = "reject"
    LEAVE_UNRESOLVED = "leave_unresolved"


@dataclass(frozen=True, slots=True)
class Survey:
    key: str
    response_id: str | None
    lead_ref_keys: tuple[str, ...]
    rows: tuple[SourceRecord, ...]
    answer_cells: tuple[SourceCell, ...]

    def __post_init__(self) -> None:
        _required(self.key, "key")
        _unique(self.lead_ref_keys, "lead_ref_keys")
        if not self.rows:
            raise ValueError("survey must preserve at least one source row")
        if not _sources_have_role(self.rows, InputRole.R1):
            raise ValueError("survey rows must come from R1")
        if not _sources_have_role(self.answer_cells, InputRole.R1):
            raise ValueError("survey answer cells must come from R1")


@dataclass(frozen=True, slots=True)
class Participant:
    key: str
    campaign_member_id: str | None
    first_name: str | None
    last_name: str | None
    email: str | None
    member_type: str | None
    first_associated_at: datetime | None
    sources: tuple[SourceRecord, ...]

    def __post_init__(self) -> None:
        _required(self.key, "key")
        if self.first_associated_at is not None:
            _aware(self.first_associated_at, "first_associated_at")
        if not self.sources or not _sources_have_role(self.sources, InputRole.R3):
            raise ValueError("participant must have R3 source provenance")


@dataclass(frozen=True, slots=True)
class Activity:
    key: str
    activity_id: str | None
    person_name: str | None
    results: str | None
    completed_at: datetime | None
    sources: tuple[SourceRecord, ...]

    def __post_init__(self) -> None:
        _required(self.key, "key")
        if self.completed_at is not None:
            _aware(self.completed_at, "completed_at")
        if not self.sources or not _sources_have_role(self.sources, InputRole.R4):
            raise ValueError("activity must have R4 source provenance")


@dataclass(frozen=True, slots=True)
class LeadRef:
    key: str
    campaign_member_ids: tuple[str, ...]
    candidate_names: tuple[str, ...]
    phone: str | None
    email: str | None
    sources: tuple[SourceRecord, ...]

    def __post_init__(self) -> None:
        _required(self.key, "key")
        _unique(self.campaign_member_ids, "campaign_member_ids")
        _unique(self.candidate_names, "candidate_names")
        if not self.sources or not _sources_have_role(self.sources, InputRole.R1):
            raise ValueError("lead reference must have R1 source provenance")


@dataclass(frozen=True, slots=True)
class ParticipantLink:
    key: str
    subject_kind: ParticipantLinkSubject
    subject_key: str
    participant_key: str | None
    method: PersonMatchMethod
    sources: tuple[SourceCell, ...]
    decision_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.key, "key")
        _required(self.subject_key, "subject_key")
        if self.method is PersonMatchMethod.UNRESOLVED and self.participant_key is not None:
            raise ValueError("unresolved person link cannot have a participant")
        if self.method is not PersonMatchMethod.UNRESOLVED and self.participant_key is None:
            raise ValueError("resolved person link must have a participant")
        if self.method is PersonMatchMethod.MANUAL and not self.decision_id:
            raise ValueError("manual person link must reference a decision")
        if not self.sources:
            raise ValueError("person link must preserve source provenance")
        expected_role = {
            ParticipantLinkSubject.LEAD_REF: InputRole.R1,
            ParticipantLinkSubject.ACTIVITY: InputRole.R4,
        }[self.subject_kind]
        if not any(source.role is expected_role for source in self.sources):
            raise ValueError(
                f"{self.subject_kind.value} person link must include {expected_role.value} provenance"
            )


@dataclass(frozen=True, slots=True)
class VoucherMention:
    key: str
    survey_key: str
    lead_ref_key: str | None
    source: SourceCell
    original_text: str
    start: int
    end: int
    normalized_token: str
    candidate_voucher_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.key, "key")
        _required(self.survey_key, "survey_key")
        _required(self.original_text, "original_text")
        _required(self.normalized_token, "normalized_token")
        if self.source.role is not InputRole.R1:
            raise ValueError("voucher mention must come from R1")
        if self.start < 0 or self.end <= self.start or self.end > len(self.original_text):
            raise ValueError("voucher mention character range is invalid")
        _unique(self.candidate_voucher_keys, "candidate_voucher_keys")

    @property
    def matched_text(self) -> str:
        return self.original_text[self.start : self.end]


@dataclass(frozen=True, slots=True)
class Voucher:
    key: str
    full_number: str
    normalized_number: str
    product_line_keys: tuple[str, ...]
    sources: tuple[SourceCell, ...]
    number_variants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.key, "key")
        _required(self.full_number, "full_number")
        _required(self.normalized_number, "normalized_number")
        _unique(self.product_line_keys, "product_line_keys")
        if not self.sources or not _sources_have_role(self.sources, InputRole.R2):
            raise ValueError("voucher must have R2 source provenance")
        if not self.number_variants:
            object.__setattr__(self, "number_variants", (self.full_number,))
        _unique(self.number_variants, "number_variants")
        if self.full_number not in self.number_variants:
            raise ValueError("full_number must be one of number_variants")
        for variant in self.number_variants:
            _required(variant, "number_variants")


@dataclass(frozen=True, slots=True)
class ProductLine:
    key: str
    product_id: str | None
    voucher_key: str | None
    quantity: Decimal | None
    species_group: str | None
    hybrid: str | None
    tax_id: str | None
    account_name: str | None
    created_at: datetime | None
    sources: tuple[SourceRecord, ...]

    def __post_init__(self) -> None:
        _required(self.key, "key")
        if self.voucher_key is not None:
            _required(self.voucher_key, "voucher_key")
        if self.quantity is not None:
            if not self.quantity.is_finite() or self.quantity < 0:
                raise ValueError("known product quantity must be finite and non-negative")
        if self.created_at is not None:
            _aware(self.created_at, "created_at")
        if not self.sources or not _sources_have_role(self.sources, InputRole.R2):
            raise ValueError("product line must have R2 source provenance")


@dataclass(frozen=True, slots=True)
class AcceptedLink:
    key: str
    lead_ref_key: str
    voucher_key: str
    survey_keys: tuple[str, ...]
    mention_keys: tuple[str, ...]
    method: VoucherMatchMethod
    sources: tuple[SourceCell, ...]
    decision_id: str | None = None
    evidence: tuple[VoucherMatchEvidence, ...] = ()

    def __post_init__(self) -> None:
        _required(self.key, "key")
        _required(self.lead_ref_key, "lead_ref_key")
        _required(self.voucher_key, "voucher_key")
        _unique(self.survey_keys, "survey_keys")
        _unique(self.mention_keys, "mention_keys")
        if not self.survey_keys or not self.mention_keys or not self.sources:
            raise ValueError("accepted link must preserve its evidence")
        if self.method is VoucherMatchMethod.MANUAL and not self.decision_id:
            raise ValueError("manual voucher link must reference a decision")
        if self.evidence:
            evidence_mentions = tuple(item.mention_key for item in self.evidence)
            _unique(evidence_mentions, "accepted link evidence mention keys")
            if set(evidence_mentions) != set(self.mention_keys):
                raise ValueError(
                    "accepted link evidence must describe every mention exactly once"
                )
            if any(item.survey_key not in self.survey_keys for item in self.evidence):
                raise ValueError("accepted link evidence must reference its surveys")


@dataclass(frozen=True, slots=True)
class Client:
    tax_id: str
    names: tuple[str, ...]
    product_line_keys: tuple[str, ...]

    @property
    def key(self) -> str:
        return self.tax_id

    def __post_init__(self) -> None:
        _required(self.tax_id, "tax_id")
        _unique(self.names, "names")
        _unique(self.product_line_keys, "product_line_keys")


@dataclass(frozen=True, slots=True)
class ManualDecision:
    decision_id: str
    target: DecisionTarget
    target_key: str
    action: DecisionAction
    selected_keys: tuple[str, ...]
    reason: str | None
    sequence: int
    created_at: datetime

    @property
    def key(self) -> str:
        return self.decision_id

    def __post_init__(self) -> None:
        _required(self.decision_id, "decision_id")
        _required(self.target_key, "target_key")
        _unique(self.selected_keys, "selected_keys")
        if self.sequence < 1:
            raise ValueError("decision sequence must be positive")
        _aware(self.created_at, "created_at")
        if self.action is DecisionAction.SELECT and not self.selected_keys:
            raise ValueError("select decision must contain selected keys")
        if self.action is not DecisionAction.SELECT and self.selected_keys:
            raise ValueError("non-select decision cannot contain selected keys")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("decision reason cannot be blank")


def _entity_keys(entities: tuple[HasKey, ...], field_name: str) -> None:
    keys = tuple(entity.key for entity in entities)
    _unique(keys, field_name)


@dataclass(frozen=True, slots=True)
class ReportResult:
    calculation_id: str
    snapshot: InputSnapshot
    revision: int
    calculated_at: datetime
    surveys: tuple[Survey, ...]
    participants: tuple[Participant, ...]
    activities: tuple[Activity, ...]
    lead_refs: tuple[LeadRef, ...]
    participant_links: tuple[ParticipantLink, ...]
    mentions: tuple[VoucherMention, ...]
    vouchers: tuple[Voucher, ...]
    product_lines: tuple[ProductLine, ...]
    accepted_links: tuple[AcceptedLink, ...]
    clients: tuple[Client, ...]
    decisions: tuple[ManualDecision, ...]
    issues: tuple[Issue, ...]
    measures: tuple[Measure, ...]

    def __post_init__(self) -> None:
        _required(self.calculation_id, "calculation_id")
        self.snapshot.require_complete()
        if self.revision < 0:
            raise ValueError("revision must not be negative")
        _aware(self.calculated_at, "calculated_at")
        for field_name in (
            "surveys",
            "participants",
            "activities",
            "lead_refs",
            "participant_links",
            "mentions",
            "vouchers",
            "product_lines",
            "accepted_links",
            "clients",
            "decisions",
        ):
            _entity_keys(getattr(self, field_name), field_name)
        _unique(tuple(issue.issue_id for issue in self.issues), "issues")
        _unique(tuple(measure.key for measure in self.measures), "measures")

    @property
    def snapshot_id(self) -> str:
        return self.snapshot.snapshot_id
