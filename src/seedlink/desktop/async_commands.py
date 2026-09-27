"""Thread-pool command bridge for the Qt desktop shell."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from seedlink.application.errors import OperationCancelled, StaleOperationError
from seedlink.application.state import CancellationToken, ProgressUpdate


_LOGGER = logging.getLogger("seedlink.desktop")
_LOGGER.addHandler(logging.NullHandler())
DesktopCommand = Callable[[CancellationToken, Callable[[ProgressUpdate], None]], Any]


class _WorkerSignals(QObject):
    progress = Signal(int, object)
    succeeded = Signal(int, object)
    failed = Signal(int, object)
    finished = Signal(int)


class _CommandWorker(QRunnable):
    def __init__(
        self,
        operation_id: int,
        command: DesktopCommand,
        cancellation: CancellationToken,
    ) -> None:
        super().__init__()
        self.operation_id = operation_id
        self.command = command
        self.cancellation = cancellation
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            value = self.command(
                self.cancellation,
                lambda update: self.signals.progress.emit(
                    self.operation_id, update
                ),
            )
        except BaseException as error:  # transported to the GUI thread
            if isinstance(error, (OperationCancelled, StaleOperationError)):
                _LOGGER.info(
                    "Desktop command ended normally: %s", error.code.value
                )
            else:
                _LOGGER.exception("Desktop command failed")
            self.signals.failed.emit(self.operation_id, error)
        else:
            self.signals.succeeded.emit(self.operation_id, value)
        finally:
            self.signals.finished.emit(self.operation_id)


class AsyncCommandController(QObject):
    """Run one session command at a time and ignore stale UI callbacks.

    A replacement request cooperatively cancels the current command and starts
    only after that worker has exited. This preserves optimistic commit ordering
    while keeping all work outside the GUI thread.
    """

    started = Signal(int, str)
    progress = Signal(int, object)
    succeeded = Signal(int, object)
    failed = Signal(int, object)
    finished = Signal(int)
    queued = Signal(int, str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._pool = pool or QThreadPool.globalInstance()
        self._serial = 0
        self._active_id: int | None = None
        self._active_token: CancellationToken | None = None
        self._pending: tuple[int, str, DesktopCommand] | None = None

    @property
    def is_running(self) -> bool:
        return self._active_id is not None

    @property
    def active_operation_id(self) -> int | None:
        return self._active_id

    @property
    def pending_operation_id(self) -> int | None:
        return self._pending[0] if self._pending is not None else None

    def start(self, label: str, command: DesktopCommand) -> int:
        self._serial += 1
        operation_id = self._serial
        if self.is_running:
            replaced = self._pending
            if replaced is not None:
                replaced_id = replaced[0]
                self.failed.emit(replaced_id, OperationCancelled())
                self.finished.emit(replaced_id)
            self.cancel()
            self._pending = (operation_id, label, command)
            self.queued.emit(operation_id, label)
        else:
            self._launch(operation_id, label, command)
        return operation_id

    def _launch(
        self, operation_id: int, label: str, command: DesktopCommand
    ) -> None:
        token = CancellationToken()
        worker = _CommandWorker(operation_id, command, token)
        worker.signals.progress.connect(self.progress)
        worker.signals.succeeded.connect(self._forward_success)
        worker.signals.failed.connect(self._forward_failure)
        worker.signals.finished.connect(self._worker_finished)
        self._active_id = operation_id
        self._active_token = token
        self.started.emit(operation_id, label)
        self._pool.start(worker)

    @Slot()
    def cancel(self) -> None:
        if self._active_token is not None:
            self._active_token.cancel()

    @Slot()
    def cancel_all(self) -> None:
        pending = self._pending
        self._pending = None
        if pending is not None:
            pending_id = pending[0]
            self.failed.emit(pending_id, OperationCancelled())
            self.finished.emit(pending_id)
        self.cancel()

    @Slot(int, object)
    def _forward_success(self, operation_id: int, value: object) -> None:
        if operation_id == self._active_id and self._pending is None:
            self.succeeded.emit(operation_id, value)

    @Slot(int, object)
    def _forward_failure(self, operation_id: int, error: object) -> None:
        if operation_id == self._active_id and self._pending is None:
            self.failed.emit(operation_id, error)

    @Slot(int)
    def _worker_finished(self, operation_id: int) -> None:
        if operation_id != self._active_id:
            return
        self._active_id = None
        self._active_token = None
        pending = self._pending
        self._pending = None
        self.finished.emit(operation_id)
        if pending is not None:
            self._launch(*pending)
