"""Qt-independent command service for one in-memory SeedLink session."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from os import PathLike
from threading import RLock

from seedlink import __version__
from seedlink.application.analysis import analyze_links
from seedlink.application.decisions import (
    VOUCHER_DECISION_ISSUE_CODES,
    recalculate_with_decisions,
)
from seedlink.application.errors import (
    DecisionValidationError,
    SessionErrorCode,
    SessionStateError,
    StaleOperationError,
    require_single_lead_ref_key,
)
from seedlink.application.reporting import build_report_result
from seedlink.application.state import (
    CancellationToken,
    OperationPhase,
    ProgressUpdate,
    SessionState,
    SessionStatus,
)
from seedlink.domain.models import (
    DecisionAction,
    DecisionTarget,
    ManualDecision,
    ReportResult,
)
from seedlink.domain.provenance import InputRole
from seedlink.input_xlsx.workbook_reader import ImportResult, import_workbooks


ProgressCallback = Callable[[ProgressUpdate], None]


class SeedLinkSession:
    """Own one loaded snapshot and its non-persistent manual decisions."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._generation = 0
        self._state = SessionState()

    @property
    def state(self) -> SessionState:
        with self._lock:
            return self._state

    def _start(self) -> tuple[int, SessionState]:
        with self._lock:
            self._generation += 1
            return self._generation, self._state

    def _commit(
        self,
        generation: int,
        state: SessionState,
        *,
        preserve_export: bool = True,
    ) -> None:
        with self._lock:
            if generation != self._generation:
                raise StaleOperationError()
            exported_id = (
                self._state.last_exported_calculation_id
                if preserve_export
                else None
            )
            self._state = replace(
                state,
                last_exported_calculation_id=exported_id,
            )

    @staticmethod
    def _token(token: CancellationToken | None) -> CancellationToken:
        return token if token is not None else CancellationToken()

    @staticmethod
    def _emit(
        callback: ProgressCallback | None,
        token: CancellationToken,
        update: ProgressUpdate,
    ) -> None:
        token.raise_if_cancelled()
        if callback is not None:
            callback(update)
        token.raise_if_cancelled()

    @staticmethod
    def _complete(callback: ProgressCallback | None, message_uk: str) -> None:
        if callback is not None:
            callback(ProgressUpdate(OperationPhase.COMPLETED, message_uk))

    def import_inputs(
        self,
        paths: Mapping[InputRole, str | PathLike[str] | None],
        *,
        loaded_at: datetime | None = None,
        program_version: str = __version__,
        cancellation: CancellationToken | None = None,
        progress: ProgressCallback | None = None,
    ) -> ImportResult:
        """Validate a new four-file set and reset all prior session state."""

        generation, _ = self._start()
        token = self._token(cancellation)
        self._emit(
            progress,
            token,
            ProgressUpdate(
                OperationPhase.IMPORTING,
                "Перевірка вхідних книг.",
                0,
                len(InputRole),
            ),
        )

        def workbook_loaded(role: InputRole, completed: int, total: int) -> None:
            self._emit(
                progress,
                token,
                ProgressUpdate(
                    OperationPhase.IMPORTING,
                    f"Перевірено {role.label_uk}.",
                    completed,
                    total,
                ),
            )

        imported = import_workbooks(
            paths,
            loaded_at=loaded_at,
            program_version=program_version,
            check_cancelled=token.raise_if_cancelled,
            workbook_loaded=workbook_loaded,
        )
        token.raise_if_cancelled()
        state = SessionState(
            status=(
                SessionStatus.IMPORTED
                if imported.is_accepted
                else SessionStatus.IMPORT_FAILED
            ),
            import_result=imported,
        )
        self._commit(generation, state, preserve_export=False)
        self._complete(
            progress,
            "Комплект готовий до розрахунку."
            if imported.is_accepted
            else "Перевірку комплекту завершено з блокувальними проблемами.",
        )
        return imported

    def analyze(
        self,
        *,
        calculated_at: datetime | None = None,
        cancellation: CancellationToken | None = None,
        progress: ProgressCallback | None = None,
    ) -> ReportResult:
        generation, state = self._start()
        imported = state.import_result
        if (
            state.status is not SessionStatus.IMPORTED
            or imported is None
            or not imported.is_accepted
        ):
            raise SessionStateError(
                SessionErrorCode.INVALID_STATE,
                "Спочатку завантажте й успішно перевірте всі чотири книги.",
            )
        token = self._token(cancellation)
        self._emit(
            progress,
            token,
            ProgressUpdate(OperationPhase.MATCHING, "Зіставлення записів."),
        )
        automatic = analyze_links(
            imported,
            check_cancelled=token.raise_if_cancelled,
        )
        self._emit(
            progress,
            token,
            ProgressUpdate(OperationPhase.CALCULATING, "Розрахунок показників."),
        )
        report = build_report_result(
            automatic,
            revision=0,
            calculated_at=calculated_at,
            check_cancelled=token.raise_if_cancelled,
        )
        token.raise_if_cancelled()
        result_state = SessionState(
            status=SessionStatus.RESULT,
            import_result=imported,
            automatic_result=automatic,
            report_result=report,
        )
        self._commit(generation, result_state)
        self._complete(progress, "Розрахунок завершено.")
        return report

    def _result_state(self) -> tuple[int, SessionState]:
        generation, state = self._start()
        if (
            state.status is not SessionStatus.RESULT
            or state.automatic_result is None
            or state.report_result is None
        ):
            raise SessionStateError(
                SessionErrorCode.INVALID_STATE,
                "Ручне рішення або перерахунок потребує готового результату.",
            )
        return generation, state

    def _run_revision(
        self,
        generation: int,
        state: SessionState,
        decisions: tuple[ManualDecision, ...],
        *,
        revision: int,
        next_sequence: int,
        calculated_at: datetime | None,
        cancellation: CancellationToken | None,
        progress: ProgressCallback | None,
    ) -> ReportResult:
        assert state.automatic_result is not None
        assert state.import_result is not None
        token = self._token(cancellation)
        decision_total = len(decisions)
        start_progress = (
            ProgressUpdate(
                OperationPhase.APPLYING_DECISIONS,
                "Застосування ручних рішень.",
                0,
                decision_total,
            )
            if decision_total
            else ProgressUpdate(
                OperationPhase.APPLYING_DECISIONS,
                "Повернення до автоматичного результату.",
            )
        )
        self._emit(progress, token, start_progress)

        def after_decisions() -> None:
            applied_progress = (
                ProgressUpdate(
                    OperationPhase.APPLYING_DECISIONS,
                    "Ручні рішення застосовано.",
                    decision_total,
                    decision_total,
                )
                if decision_total
                else ProgressUpdate(
                    OperationPhase.APPLYING_DECISIONS,
                    "Автоматичний результат відновлено.",
                )
            )
            self._emit(progress, token, applied_progress)
            self._emit(
                progress,
                token,
                ProgressUpdate(
                    OperationPhase.CALCULATING,
                    "Перерахунок усіх залежних показників.",
                ),
            )

        report = recalculate_with_decisions(
            state.automatic_result,
            decisions,
            revision=revision,
            calculated_at=calculated_at,
            check_cancelled=token.raise_if_cancelled,
            decisions_applied=after_decisions,
        )
        token.raise_if_cancelled()
        new_state = SessionState(
            status=SessionStatus.RESULT,
            import_result=state.import_result,
            automatic_result=state.automatic_result,
            report_result=report,
            decisions=decisions,
            next_decision_sequence=next_sequence,
        )
        self._commit(generation, new_state)
        self._complete(progress, f"Готова ревізія {revision}.")
        return report

    def recalculate(
        self,
        *,
        calculated_at: datetime | None = None,
        cancellation: CancellationToken | None = None,
        progress: ProgressCallback | None = None,
    ) -> ReportResult:
        generation, state = self._result_state()
        assert state.report_result is not None
        return self._run_revision(
            generation,
            state,
            state.decisions,
            revision=state.report_result.revision + 1,
            next_sequence=state.next_decision_sequence,
            calculated_at=calculated_at,
            cancellation=cancellation,
            progress=progress,
        )

    @staticmethod
    def _keys(values: Iterable[str], field_name: str) -> tuple[str, ...]:
        if isinstance(values, str):
            raise DecisionValidationError(
                SessionErrorCode.INVALID_DECISION,
                f"{field_name} має бути колекцією ключів, а не рядком.",
            )
        keys = tuple(values)
        if len(keys) != len(set(keys)):
            raise DecisionValidationError(
                SessionErrorCode.INVALID_DECISION,
                f"{field_name} не може містити повторювані ключі.",
            )
        return keys

    @staticmethod
    def _lead_ref_for_survey(
        state: SessionState,
        survey_key: str,
    ) -> str:
        automatic = state.automatic_result
        survey = (
            next(
                (item for item in automatic.surveys if item.key == survey_key),
                None,
            )
            if automatic is not None
            else None
        )
        if survey is None:
            raise DecisionValidationError(
                SessionErrorCode.UNKNOWN_TARGET,
                "Опитування для ручного рішення не знайдено.",
                details=(("survey_key", survey_key),),
            )
        return require_single_lead_ref_key(survey)

    @staticmethod
    def _decision_id(
        snapshot_id: str,
        sequence: int,
        target: DecisionTarget,
        target_key: str,
        action: DecisionAction,
        selected_keys: tuple[str, ...],
        mention_keys: tuple[str, ...],
        created_at: datetime,
    ) -> str:
        identity = "\x1f".join(
            (
                snapshot_id,
                str(sequence),
                target.value,
                target_key,
                action.value,
                "\x1e".join(selected_keys),
                "\x1d".join(mention_keys),
                created_at.isoformat(),
            )
        )
        return f"decision:{sha256(identity.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _voucher_mention_scope(
        state: SessionState,
        survey_key: str,
        selected_keys: tuple[str, ...],
        action: DecisionAction,
        supplied_keys: tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        assert state.automatic_result is not None
        automatic = state.automatic_result
        survey = next(
            (item for item in automatic.surveys if item.key == survey_key),
            None,
        )
        if survey is None:
            raise DecisionValidationError(
                SessionErrorCode.UNKNOWN_TARGET,
                "Опитування для ручного вибору ваучера не знайдено.",
                details=(("target_key", survey_key),),
            )
        mentions = tuple(
            item for item in automatic.mentions if item.survey_key == survey.key
        )
        if supplied_keys is not None:
            return supplied_keys
        if action is not DecisionAction.SELECT:
            return tuple(item.key for item in mentions)

        selected = set(selected_keys)
        related: set[str] = {
            mention.key
            for mention in mentions
            if selected.intersection(mention.candidate_voucher_keys)
        }
        for link in automatic.accepted_links:
            if link.voucher_key not in selected:
                continue
            related.update(
                evidence.mention_key
                for evidence in link.evidence
                if evidence.survey_key == survey.key
            )
        if related:
            survey_mention_keys = {item.key for item in mentions}
            for issue in automatic.issues:
                if issue.code not in VOUCHER_DECISION_ISSUE_CODES:
                    continue
                issue_mentions = set(issue.affected_keys).intersection(
                    survey_mention_keys
                )
                if related.intersection(issue_mentions):
                    related.update(issue_mentions)
            return tuple(
                item.key for item in mentions if item.key in related
            )
        if len(mentions) == 1:
            return (mentions[0].key,)
        raise DecisionValidationError(
            SessionErrorCode.INVALID_DECISION,
            "Для ручного вибору неможливо однозначно визначити згадки; "
            "передайте mention_keys.",
            details=(
                ("survey_key", survey.key),
                ("available_mention_keys", ", ".join(item.key for item in mentions)),
            ),
        )

    def apply_decision(
        self,
        target: DecisionTarget,
        target_key: str,
        action: DecisionAction,
        *,
        selected_keys: Iterable[str] = (),
        mention_keys: Iterable[str] | None = None,
        reason: str | None = None,
        created_at: datetime | None = None,
        calculated_at: datetime | None = None,
        cancellation: CancellationToken | None = None,
        progress: ProgressCallback | None = None,
    ) -> ReportResult:
        generation, state = self._result_state()
        assert state.automatic_result is not None
        assert state.report_result is not None
        if not isinstance(target, DecisionTarget) or not isinstance(
            action, DecisionAction
        ):
            raise DecisionValidationError(
                SessionErrorCode.INVALID_DECISION,
                "Тип цілі або дії ручного рішення не підтримується.",
            )
        if target is DecisionTarget.SURVEY_PARTICIPANT:
            target_key = self._lead_ref_for_survey(state, target_key)
            target = DecisionTarget.LEAD_REF_PARTICIPANT
        selected = self._keys(selected_keys, "selected_keys")
        supplied_mentions = (
            self._keys(mention_keys, "mention_keys")
            if mention_keys is not None
            else None
        )
        scoped_mentions = (
            self._voucher_mention_scope(
                state,
                target_key,
                selected,
                action,
                supplied_mentions,
            )
            if target is DecisionTarget.VOUCHER_CASE
            else supplied_mentions or ()
        )
        timestamp = created_at or datetime.now(UTC)
        normalized_reason = (
            (reason.strip() or None) if reason is not None else None
        )
        try:
            decision = ManualDecision(
                decision_id=self._decision_id(
                    state.automatic_result.snapshot_id,
                    state.next_decision_sequence,
                    target,
                    target_key,
                    action,
                    selected,
                    scoped_mentions,
                    timestamp,
                ),
                target=target,
                target_key=target_key,
                action=action,
                selected_keys=selected,
                reason=normalized_reason,
                sequence=state.next_decision_sequence,
                created_at=timestamp,
                mention_keys=scoped_mentions,
            )
        except ValueError as error:
            raise DecisionValidationError(
                SessionErrorCode.INVALID_DECISION,
                f"Некоректне ручне рішення: {error}.",
            ) from error

        def is_replaced(item: ManualDecision) -> bool:
            if (item.target, item.target_key) != (target, target_key):
                return False
            if target is not DecisionTarget.VOUCHER_CASE:
                return True
            return frozenset(item.mention_keys) == frozenset(scoped_mentions)

        decisions = tuple(
            item for item in state.decisions if not is_replaced(item)
        ) + (decision,)
        decisions = tuple(sorted(decisions, key=lambda item: item.sequence))
        return self._run_revision(
            generation,
            state,
            decisions,
            revision=state.report_result.revision + 1,
            next_sequence=state.next_decision_sequence + 1,
            calculated_at=calculated_at,
            cancellation=cancellation,
            progress=progress,
        )

    def select_vouchers(
        self,
        survey_key: str,
        voucher_keys: Iterable[str],
        *,
        mention_keys: Iterable[str] | None = None,
        reason: str | None = None,
        **kwargs,
    ) -> ReportResult:
        return self.apply_decision(
            DecisionTarget.VOUCHER_CASE,
            survey_key,
            DecisionAction.SELECT,
            selected_keys=voucher_keys,
            mention_keys=mention_keys,
            reason=reason,
            **kwargs,
        )

    def set_lead_ref_participant(
        self,
        lead_ref_key: str,
        participant_key: str,
        *,
        reason: str | None = None,
        **kwargs,
    ) -> ReportResult:
        return self.apply_decision(
            DecisionTarget.LEAD_REF_PARTICIPANT,
            lead_ref_key,
            DecisionAction.SELECT,
            selected_keys=(participant_key,),
            reason=reason,
            **kwargs,
        )

    def set_survey_participant(
        self,
        survey_key: str,
        participant_key: str,
        *,
        reason: str | None = None,
        **kwargs,
    ) -> ReportResult:
        return self.apply_decision(
            DecisionTarget.SURVEY_PARTICIPANT,
            survey_key,
            DecisionAction.SELECT,
            selected_keys=(participant_key,),
            reason=reason,
            **kwargs,
        )

    def set_activity_participant(
        self,
        activity_key: str,
        participant_key: str,
        *,
        reason: str | None = None,
        **kwargs,
    ) -> ReportResult:
        return self.apply_decision(
            DecisionTarget.ACTIVITY_PARTICIPANT,
            activity_key,
            DecisionAction.SELECT,
            selected_keys=(participant_key,),
            reason=reason,
            **kwargs,
        )

    def undo_decision(
        self,
        decision_id: str,
        *,
        calculated_at: datetime | None = None,
        cancellation: CancellationToken | None = None,
        progress: ProgressCallback | None = None,
    ) -> ReportResult:
        generation, state = self._result_state()
        assert state.report_result is not None
        decisions = tuple(
            item for item in state.decisions if item.decision_id != decision_id
        )
        if len(decisions) == len(state.decisions):
            raise DecisionValidationError(
                SessionErrorCode.DECISION_NOT_FOUND,
                "Активне ручне рішення для скасування не знайдено.",
                details=(("decision_id", decision_id),),
            )
        return self._run_revision(
            generation,
            state,
            decisions,
            revision=state.report_result.revision + 1,
            next_sequence=state.next_decision_sequence,
            calculated_at=calculated_at,
            cancellation=cancellation,
            progress=progress,
        )

    def mark_exported(self, calculation_id: str) -> None:
        with self._lock:
            report = self._state.report_result
            if report is None or report.calculation_id != calculation_id:
                raise SessionStateError(
                    SessionErrorCode.EXPORT_REVISION_MISMATCH,
                    "Не можна позначити експортованою застарілу ревізію.",
                )
            self._state = replace(
                self._state,
                last_exported_calculation_id=calculation_id,
            )

    def reset(self) -> None:
        with self._lock:
            self._generation += 1
            self._state = SessionState()

    close = reset
