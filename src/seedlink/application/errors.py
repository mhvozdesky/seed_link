"""Typed, user-facing failures raised by the session application layer."""

from __future__ import annotations

from enum import StrEnum

from seedlink.domain.models import Survey


class SessionErrorCode(StrEnum):
    INVALID_STATE = "INVALID_STATE"
    UNKNOWN_TARGET = "UNKNOWN_TARGET"
    UNKNOWN_SELECTION = "UNKNOWN_SELECTION"
    REASON_REQUIRED = "REASON_REQUIRED"
    INVALID_DECISION = "INVALID_DECISION"
    CONFLICTING_DECISIONS = "CONFLICTING_DECISIONS"
    DECISION_NOT_FOUND = "DECISION_NOT_FOUND"
    OPERATION_CANCELLED = "OPERATION_CANCELLED"
    STALE_OPERATION = "STALE_OPERATION"
    EXPORT_REVISION_MISMATCH = "EXPORT_REVISION_MISMATCH"
    INTERNAL_COMMAND_STATE = "INTERNAL_COMMAND_STATE"
    EXPORT_IO_FAILED = "EXPORT_IO_FAILED"


class SessionError(RuntimeError):
    """Base error safe to translate into a normal GUI message."""

    def __init__(
        self,
        code: SessionErrorCode,
        message_uk: str,
        *,
        details: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.code = code
        self.message_uk = message_uk
        self.details = details
        super().__init__(f"{code.value}: {message_uk}")


class SessionStateError(SessionError):
    pass


class DecisionValidationError(SessionError):
    pass


class ExportError(SessionError):
    pass


def require_single_lead_ref_key(survey: Survey) -> str:
    """Return the only LeadRef or raise the shared typed scope error."""

    if len(survey.lead_ref_keys) != 1:
        lead_keys = ", ".join(survey.lead_ref_keys) or "—"
        raise DecisionValidationError(
            SessionErrorCode.INVALID_DECISION,
            "Survey має кілька LeadRef; виберіть конкретний LeadRef: "
            f"{lead_keys}.",
            details=(
                ("survey_key", survey.key),
                ("lead_ref_keys", lead_keys),
            ),
        )
    return survey.lead_ref_keys[0]


class OperationCancelled(SessionError):
    def __init__(self) -> None:
        super().__init__(
            SessionErrorCode.OPERATION_CANCELLED,
            "Операцію скасовано користувачем.",
        )


class StaleOperationError(SessionError):
    def __init__(self) -> None:
        super().__init__(
            SessionErrorCode.STALE_OPERATION,
            "Результат застарілої операції відкинуто.",
        )
