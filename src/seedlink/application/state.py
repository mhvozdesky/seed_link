"""In-memory session state, progress and cooperative cancellation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Event

from seedlink.application.analysis import AutomaticMatchingResult
from seedlink.application.errors import OperationCancelled
from seedlink.domain.models import ManualDecision, ReportResult
from seedlink.input_xlsx.workbook_reader import ImportResult


class SessionStatus(StrEnum):
    EMPTY = "empty"
    IMPORT_FAILED = "import_failed"
    IMPORTED = "imported"
    RESULT = "result"


class OperationPhase(StrEnum):
    IMPORTING = "importing"
    MATCHING = "matching"
    EXPORTING = "exporting"
    APPLYING_DECISIONS = "applying_decisions"
    CALCULATING = "calculating"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    phase: OperationPhase
    message_uk: str
    completed: int | None = None
    total: int | None = None

    def __post_init__(self) -> None:
        if not self.message_uk.strip():
            raise ValueError("progress message must not be blank")
        if (self.completed is None) != (self.total is None):
            raise ValueError("completed and total must be supplied together")
        if self.completed is not None:
            if self.completed < 0 or self.total is None or self.total < 1:
                raise ValueError("progress values are invalid")
            if self.completed > self.total:
                raise ValueError("completed progress cannot exceed total")


class CancellationToken:
    """Thread-safe flag checked between bounded portions of work."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise OperationCancelled()


@dataclass(frozen=True, slots=True)
class SessionState:
    status: SessionStatus = SessionStatus.EMPTY
    import_result: ImportResult | None = None
    automatic_result: AutomaticMatchingResult | None = None
    report_result: ReportResult | None = None
    decisions: tuple[ManualDecision, ...] = ()
    next_decision_sequence: int = 1
    last_exported_calculation_id: str | None = None

    def __post_init__(self) -> None:
        if self.next_decision_sequence < 1:
            raise ValueError("next decision sequence must be positive")
        if self.status is SessionStatus.EMPTY and any(
            value is not None
            for value in (
                self.import_result,
                self.automatic_result,
                self.report_result,
            )
        ):
            raise ValueError("empty session cannot carry loaded results")
        if self.status is SessionStatus.IMPORT_FAILED:
            if self.import_result is None or self.import_result.is_accepted:
                raise ValueError("failed state needs a rejected import")
        if self.status in {SessionStatus.IMPORTED, SessionStatus.RESULT}:
            if self.import_result is None or not self.import_result.is_accepted:
                raise ValueError("loaded state needs an accepted import")
        if self.status is SessionStatus.IMPORTED and (
            self.automatic_result is not None or self.report_result is not None
        ):
            raise ValueError("imported state cannot carry an analysis result")
        if self.status is SessionStatus.RESULT:
            if self.automatic_result is None or self.report_result is None:
                raise ValueError("result state needs automatic and report results")
            if self.decisions != self.report_result.decisions:
                raise ValueError("state decisions must match the report revision")

    @property
    def current_revision(self) -> int | None:
        return self.report_result.revision if self.report_result else None

    @property
    def export_is_current(self) -> bool:
        return (
            self.report_result is not None
            and self.last_exported_calculation_id
            == self.report_result.calculation_id
        )

    @property
    def has_stale_export(self) -> bool:
        return (
            self.last_exported_calculation_id is not None
            and not self.export_is_current
        )
