"""Stable issue taxonomy shared by the core, GUI and report projections."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from seedlink.domain.provenance import SourceCell


class IssueLevel(StrEnum):
    IMPORT_BLOCKING = "import_blocking"
    RECORD = "record"
    UNKNOWN_NUMERIC = "unknown_numeric"
    INTERNAL = "internal"
    EXPORT = "export"


class IssueCode(StrEnum):
    MISSING_INPUT = "MISSING_INPUT"
    WORKBOOK_UNREADABLE = "WORKBOOK_UNREADABLE"
    INVALID_XLSX = "INVALID_XLSX"
    UNSUPPORTED_STRUCTURE = "UNSUPPORTED_STRUCTURE"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    ROLE_MISMATCH = "ROLE_MISMATCH"
    UNSUPPORTED_CELL_VALUE = "UNSUPPORTED_CELL_VALUE"
    NUMERIC_ID_RISK = "NUMERIC_ID_RISK"
    CAMPAIGN_MEMBER_ID_INVALID = "CAMPAIGN_MEMBER_ID_INVALID"
    DUPLICATE_ID = "DUPLICATE_ID"
    DUPLICATE_UNCERTAIN = "DUPLICATE_UNCERTAIN"
    CONFLICTING_ID_DATA = "CONFLICTING_ID_DATA"
    PERSON_LINK_AMBIGUOUS = "PERSON_LINK_AMBIGUOUS"
    PERSON_LINK_CONFLICT = "PERSON_LINK_CONFLICT"
    PERSON_LINK_UNRESOLVED = "PERSON_LINK_UNRESOLVED"
    PERSON_DATA_MISMATCH = "PERSON_DATA_MISMATCH"
    VOUCHER_FIELD_CONFLICT = "VOUCHER_FIELD_CONFLICT"
    VOUCHER_MATCH_AMBIGUOUS = "VOUCHER_MATCH_AMBIGUOUS"
    VOUCHER_NOT_FOUND = "VOUCHER_NOT_FOUND"
    VOUCHER_NUMBER_MISSING = "VOUCHER_NUMBER_MISSING"
    VOUCHER_NUMBER_INVALID = "VOUCHER_NUMBER_INVALID"
    QUANTITY_UNKNOWN = "QUANTITY_UNKNOWN"
    PRODUCT_ID_CONFLICT = "PRODUCT_ID_CONFLICT"
    CLIENT_UNKNOWN = "CLIENT_UNKNOWN"
    MULTIPLE_TAX_IDS = "MULTIPLE_TAX_IDS"
    OTHER_VOUCHER_ELIGIBILITY_UNKNOWN = "OTHER_VOUCHER_ELIGIBILITY_UNKNOWN"
    DATE_INVALID = "DATE_INVALID"
    DATE_CONFLICT = "DATE_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    CONTROL_TOTAL_MISMATCH = "CONTROL_TOTAL_MISMATCH"
    EXPORT_IO_FAILED = "EXPORT_IO_FAILED"


ISSUE_LEVEL_BY_CODE: Mapping[IssueCode, IssueLevel] = MappingProxyType(
    {
        IssueCode.MISSING_INPUT: IssueLevel.IMPORT_BLOCKING,
        IssueCode.WORKBOOK_UNREADABLE: IssueLevel.IMPORT_BLOCKING,
        IssueCode.INVALID_XLSX: IssueLevel.IMPORT_BLOCKING,
        IssueCode.UNSUPPORTED_STRUCTURE: IssueLevel.IMPORT_BLOCKING,
        IssueCode.SCHEMA_MISMATCH: IssueLevel.IMPORT_BLOCKING,
        IssueCode.ROLE_MISMATCH: IssueLevel.IMPORT_BLOCKING,
        IssueCode.UNSUPPORTED_CELL_VALUE: IssueLevel.RECORD,
        IssueCode.NUMERIC_ID_RISK: IssueLevel.RECORD,
        IssueCode.CAMPAIGN_MEMBER_ID_INVALID: IssueLevel.RECORD,
        IssueCode.DUPLICATE_ID: IssueLevel.RECORD,
        IssueCode.DUPLICATE_UNCERTAIN: IssueLevel.RECORD,
        IssueCode.CONFLICTING_ID_DATA: IssueLevel.RECORD,
        IssueCode.PERSON_LINK_AMBIGUOUS: IssueLevel.RECORD,
        IssueCode.PERSON_LINK_CONFLICT: IssueLevel.RECORD,
        IssueCode.PERSON_LINK_UNRESOLVED: IssueLevel.RECORD,
        IssueCode.PERSON_DATA_MISMATCH: IssueLevel.RECORD,
        IssueCode.VOUCHER_FIELD_CONFLICT: IssueLevel.RECORD,
        IssueCode.VOUCHER_MATCH_AMBIGUOUS: IssueLevel.RECORD,
        IssueCode.VOUCHER_NOT_FOUND: IssueLevel.RECORD,
        IssueCode.VOUCHER_NUMBER_MISSING: IssueLevel.RECORD,
        IssueCode.VOUCHER_NUMBER_INVALID: IssueLevel.RECORD,
        IssueCode.QUANTITY_UNKNOWN: IssueLevel.UNKNOWN_NUMERIC,
        IssueCode.PRODUCT_ID_CONFLICT: IssueLevel.UNKNOWN_NUMERIC,
        IssueCode.CLIENT_UNKNOWN: IssueLevel.RECORD,
        IssueCode.MULTIPLE_TAX_IDS: IssueLevel.RECORD,
        IssueCode.OTHER_VOUCHER_ELIGIBILITY_UNKNOWN: IssueLevel.RECORD,
        IssueCode.DATE_INVALID: IssueLevel.RECORD,
        IssueCode.DATE_CONFLICT: IssueLevel.RECORD,
        IssueCode.INTERNAL_ERROR: IssueLevel.INTERNAL,
        IssueCode.CONTROL_TOTAL_MISMATCH: IssueLevel.INTERNAL,
        IssueCode.EXPORT_IO_FAILED: IssueLevel.EXPORT,
    }
)


@dataclass(frozen=True, slots=True)
class Issue:
    issue_id: str
    code: IssueCode
    level: IssueLevel
    message_uk: str
    sources: tuple[SourceCell, ...]
    affected_keys: tuple[str, ...] = ()
    candidate_keys: tuple[str, ...] = ()
    details: tuple[tuple[str, str], ...] = ()
    resolved_by_decision_id: str | None = None

    def __post_init__(self) -> None:
        if not self.issue_id.strip():
            raise ValueError("issue_id must not be blank")
        if not self.message_uk.strip():
            raise ValueError("message_uk must not be blank")
        expected_level = ISSUE_LEVEL_BY_CODE[self.code]
        if self.level is not expected_level:
            raise ValueError(
                f"{self.code.value} must have issue level {expected_level.value}"
            )
        if len(set(self.affected_keys)) != len(self.affected_keys):
            raise ValueError("affected_keys must be unique")
        if len(set(self.candidate_keys)) != len(self.candidate_keys):
            raise ValueError("candidate_keys must be unique")
        detail_names = [name for name, _ in self.details]
        if len(set(detail_names)) != len(detail_names):
            raise ValueError("issue detail names must be unique")
        if expected_level in {IssueLevel.RECORD, IssueLevel.UNKNOWN_NUMERIC}:
            if not self.sources:
                raise ValueError("record and numeric issues must have source provenance")

    @property
    def is_resolved(self) -> bool:
        return self.resolved_by_decision_id is not None
