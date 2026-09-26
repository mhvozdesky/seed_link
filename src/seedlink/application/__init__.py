"""Use-case orchestration over the Qt-independent SeedLink core."""

from seedlink.application.analysis import (
    AutomaticMatchingResult,
    SourceUncertainty,
    analyze_links,
)
from seedlink.application.decisions import (
    apply_manual_decisions,
    recalculate_with_decisions,
    validate_manual_decisions,
)
from seedlink.application.errors import (
    DecisionValidationError,
    OperationCancelled,
    SessionError,
    SessionErrorCode,
    SessionStateError,
    StaleOperationError,
)
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
    "OperationCancelled",
    "OperationPhase",
    "ProgressUpdate",
    "SeedLinkSession",
    "SessionError",
    "SessionErrorCode",
    "SessionState",
    "SessionStateError",
    "SessionStatus",
    "SourceUncertainty",
    "StaleOperationError",
    "analyze_links",
    "apply_manual_decisions",
    "build_report_result",
    "recalculate_with_decisions",
    "validate_manual_decisions",
]
