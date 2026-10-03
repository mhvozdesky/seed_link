"""Use-case orchestration over the Qt-independent SeedLink core."""

from seedlink.application.analysis import (
    AutomaticMatchingResult,
    SourceUncertainty,
    analyze_links,
)
from seedlink.application.decisions import (
    PERSON_DECISION_ISSUE_CODES,
    VOUCHER_DECISION_ISSUE_CODES,
    apply_manual_decisions,
    recalculate_with_decisions,
    validate_manual_decisions,
)
from seedlink.application.errors import (
    DecisionValidationError,
    ExportError,
    OperationCancelled,
    SessionError,
    SessionErrorCode,
    SessionStateError,
    StaleOperationError,
)
from seedlink.application.exporting import ExportBundle, ExportService
from seedlink.application.reporting import build_report_result
from seedlink.application.service import SeedLinkSession
from seedlink.application.state import (
    CancellationToken,
    OperationPhase,
    ProgressUpdate,
    SessionState,
    SessionStatus,
)

__all__ = [
    "AutomaticMatchingResult",
    "CancellationToken",
    "DecisionValidationError",
    "ExportBundle",
    "ExportError",
    "ExportService",
    "OperationCancelled",
    "OperationPhase",
    "PERSON_DECISION_ISSUE_CODES",
    "ProgressUpdate",
    "SeedLinkSession",
    "SessionError",
    "SessionErrorCode",
    "SessionState",
    "SessionStateError",
    "SessionStatus",
    "SourceUncertainty",
    "StaleOperationError",
    "VOUCHER_DECISION_ISSUE_CODES",
    "analyze_links",
    "apply_manual_decisions",
    "build_report_result",
    "recalculate_with_decisions",
    "validate_manual_decisions",
]
