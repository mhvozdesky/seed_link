from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt

from seedlink.application import (
    DecisionValidationError,
    OperationPhase,
    SeedLinkSession,
)
from seedlink.desktop import (
    Column,
    ObjectTableModel,
    SearchProxyModel,
    SeedLinkMainWindow,
    create_application,
)
from seedlink.domain.provenance import InputRole
from seedlink.support.settings import AppSettings


MEMBER_A = "00vAbCdEfGhIjKlIVK"


def _rows():
    return {
        InputRole.R1: [{
            "Record Nr": "R1",
            "Survey Response ID": "S1",
            "Campaigm Member": MEMBER_A,
            "Answer": "Зафіксовано",
            "Answer (Long Text)": "SE-900",
            "Time Taken": "2026-09-01T09:00:00+03:00",
        }],
        InputRole.R2: [{
            "Opportunity Product Id 18": "P1",
            "Voucher Number": "SE-900",
            "Current Year Planned Quantity": 5,
            "Tax ID 1": "00000001",
            "Account Name": "Клієнт",
            "Species group": "SUN: Sunflowers",
            "Opportunity Product: Created Date": "2026-09-02",
        }],
        InputRole.R3: [{
            "Campaign Member Id 18": MEMBER_A,
            "First Name": "Лід",
            "Last Name": "Один",
            "Member Type": "Lead",
            "Member First Associated Date": "2026-09-01",
        }],
    }


class _BlockingDuplicateKeys:
    def __init__(self, started: Event, release: Event) -> None:
        self.started = started
        self.release = release

    def __iter__(self):
        self.started.set()
        assert self.release.wait(5)
        yield "missing-voucher"
        yield "missing-voucher"


def test_r06_rejected_command_does_not_invalidate_background_result(
    workbook_set_factory,
):
    session = SeedLinkSession()
    assert session.import_inputs(workbook_set_factory(_rows())).is_accepted
    original = session.analyze()

    background_paused = Event()
    release_background = Event()
    invalid_started = Event()
    release_invalid = Event()

    def progress(update):
        if (
            update.phase is OperationPhase.APPLYING_DECISIONS
            and not background_paused.is_set()
        ):
            background_paused.set()
            assert release_background.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        background = pool.submit(session.recalculate, progress=progress)
        assert background_paused.wait(5)
        rejected = pool.submit(
            session.select_vouchers,
            original.surveys[0].key,
            _BlockingDuplicateKeys(invalid_started, release_invalid),
        )
        assert invalid_started.wait(5)
        release_background.set()
        revised = background.result(timeout=5)
        release_invalid.set()
        with pytest.raises(DecisionValidationError):
            rejected.result(timeout=5)

    assert revised.revision == 1
    assert session.state.report_result is revised


def test_object_table_model_and_search_proxy_filter_all_columns():
    app = create_application([])
    model = ObjectTableModel(
        (Column("Назва", lambda row: row["name"]), Column("Код", lambda row: row["code"])),
        ({"name": "Соняшник", "code": "SUN"}, {"name": "Кукурудза", "code": "CRN"}),
    )
    proxy = SearchProxyModel()
    proxy.setSourceModel(model)

    proxy.set_search_text("crn")
    app.processEvents()

    assert proxy.rowCount() == 1
    assert proxy.index(0, 0).data(Qt.ItemDataRole.DisplayRole) == "Кукурудза"
    assert proxy.index(0, 0).data(ObjectTableModel.ObjectRole)["code"] == "CRN"


def _wait_until(app, predicate, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Qt operation did not finish before timeout")


def test_main_window_full_import_calculate_review_cycle(workbook_set_factory):
    app = create_application([])
    paths = workbook_set_factory(_rows())
    window = SeedLinkMainWindow(settings=AppSettings())

    for role, path in paths.items():
        window._set_role_path(role, str(path))
    assert window.validate_button.isEnabled()

    window.validate_inputs()
    _wait_until(
        app,
        lambda: not window.controller.is_running
        and window.session.state.status.value == "imported",
    )
    assert window.calculate_button.isEnabled()
    assert all(slot.property("slotState") == "accepted" for slot in window.file_slots.values())

    window.calculate()
    _wait_until(app, lambda: not window.controller.is_running and window.result is not None)

    assert window.tabs.isEnabled()
    assert window.tabs.count() == 4
    assert window.link_model.rowCount() == 1
    assert window.product_model.rowCount() == 1
    assert window.participant_model.rowCount() == 1
    assert "ревізія 0" in window.state_label.text()
    assert window.funnel_chart.values
    assert window.crop_chart.values
    assert window.time_chart.values

    window.session.close()
    window.deleteLater()
    app.processEvents()


def test_async_controller_cancels_replaced_work_and_ignores_stale_callback():
    from seedlink.desktop import AsyncCommandController

    app = create_application([])
    controller = AsyncCommandController()
    first_started = Event()
    delivered: list[tuple[int, object]] = []
    failures: list[tuple[int, object]] = []
    controller.succeeded.connect(lambda operation_id, value: delivered.append((operation_id, value)))
    controller.failed.connect(lambda operation_id, error: failures.append((operation_id, error)))

    def first(token, progress):
        del progress
        first_started.set()
        while not token.is_cancelled:
            time.sleep(0.002)
        token.raise_if_cancelled()

    first_id = controller.start("Перша", first)
    assert first_started.wait(5)
    second_id = controller.start("Друга", lambda token, progress: "чинний")
    _wait_until(app, lambda: not controller.is_running and bool(delivered))

    assert first_id != second_id
    assert delivered == [(second_id, "чинний")]
    assert not failures


def _empty_workbook(path, role):
    from openpyxl import Workbook
    from seedlink.input_xlsx.schemas import schema_for

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = role.value
    worksheet.append(schema_for(role).columns)
    workbook.save(path)
    workbook.close()


def test_changed_slot_blocks_calculation_and_old_snapshot_is_labelled(
    workbook_set_factory, tmp_path
):
    app = create_application([])
    paths = workbook_set_factory(_rows())
    window = SeedLinkMainWindow(settings=AppSettings())
    window.show()
    app.processEvents()
    for role, path in paths.items():
        window._set_role_path(role, str(path))
    window.validate_inputs()
    _wait_until(app, lambda: not window.controller.is_running)
    window.calculate()
    _wait_until(app, lambda: not window.controller.is_running and window.result is not None)
    assert window.product_model.rowCount() == 1

    empty_r2 = tmp_path / "R2-empty.xlsx"
    _empty_workbook(empty_r2, InputRole.R2)
    window._set_role_path(InputRole.R2, str(empty_r2))

    assert not window.calculate_button.isEnabled()
    assert window.result_context.isVisible()
    assert "попереднього комплекту" in window.result_context.text()
    previous = window.result
    window.calculate()  # direct calls are guarded too
    assert window.result is previous

    window.validate_inputs()
    _wait_until(
        app,
        lambda: not window.controller.is_running
        and window.session.state.status.value == "imported",
    )
    assert window.calculate_button.isEnabled()
    assert window.result_context.isVisible()

    window.calculate()
    _wait_until(
        app,
        lambda: not window.controller.is_running
        and window.result is not previous,
    )
    assert window.product_model.rowCount() == 0
    assert not window.result_context.isVisible()
    window.session.close()
    window.deleteLater()
    app.processEvents()


def test_reselecting_same_path_requires_revalidation(workbook_set_factory):
    app = create_application([])
    paths = workbook_set_factory(_rows())
    window = SeedLinkMainWindow(settings=AppSettings())
    window.show()
    app.processEvents()
    for role, path in paths.items():
        window._set_role_path(role, str(path))

    window.validate_inputs()
    _wait_until(
        app,
        lambda: not window.controller.is_running
        and window.session.state.status.value == "imported",
    )
    previous_import = window.session.state.import_result
    assert window._paths_are_verified()
    assert window.calculate_button.isEnabled()

    r2_path = paths[InputRole.R2]
    _empty_workbook(r2_path, InputRole.R2)
    window._set_role_path(InputRole.R2, str(r2_path))

    assert window.file_slots[InputRole.R2].property("slotState") == "pending"
    assert not window._paths_are_verified()
    assert not window.calculate_button.isEnabled()
    window.calculate()  # the direct entry point must use the same guard
    app.processEvents()
    assert window.result is None
    assert window.session.state.import_result is previous_import

    window.validate_inputs()
    _wait_until(
        app,
        lambda: not window.controller.is_running
        and window.session.state.status.value == "imported",
    )
    window.calculate()
    _wait_until(
        app,
        lambda: not window.controller.is_running and window.result is not None,
    )
    assert window.product_model.rowCount() == 0
    window.session.close()
    window.deleteLater()
    app.processEvents()


def test_import_warnings_are_summarized_and_navigate_to_slot(
    workbook_set_factory,
):
    app = create_application([])
    rows = _rows()
    rows[InputRole.R1][0]["Time Taken"] = "123"
    paths = workbook_set_factory(rows)
    window = SeedLinkMainWindow(settings=AppSettings())
    window.show()
    app.processEvents()
    for role, path in paths.items():
        window._set_role_path(role, str(path))

    window.validate_inputs()
    _wait_until(app, lambda: not window.controller.is_running)

    assert window.session.state.status.value == "imported"
    assert window.input_issues.isVisible()
    assert "зауважень" in window.file_slots[InputRole.R1].status_label.text()
    assert window.input_issues.title().startswith("Зауваження")
    button = window.input_issues_layout.itemAt(0).widget()
    button.click()
    assert window.file_slots[InputRole.R1].hasFocus()
    window.session.close()
    window.deleteLater()
    app.processEvents()


def test_replacing_pending_command_finishes_the_discarded_request():
    from seedlink.desktop import AsyncCommandController

    app = create_application([])
    controller = AsyncCommandController()
    first_started = Event()
    delivered: list[tuple[int, object]] = []
    failed: list[tuple[int, object]] = []
    finished: list[int] = []
    controller.succeeded.connect(lambda operation_id, value: delivered.append((operation_id, value)))
    controller.failed.connect(lambda operation_id, error: failed.append((operation_id, error)))
    controller.finished.connect(finished.append)

    def first(token, progress):
        del progress
        first_started.set()
        while not token.is_cancelled:
            time.sleep(0.002)
        token.raise_if_cancelled()

    first_id = controller.start("Перша", first)
    assert first_started.wait(5)
    second_id = controller.start("Друга", lambda token, progress: "друга")
    third_id = controller.start("Третя", lambda token, progress: "третя")
    _wait_until(app, lambda: not controller.is_running and bool(delivered))

    assert delivered == [(third_id, "третя")]
    assert any(operation_id == second_id for operation_id, _ in failed)
    assert second_id in finished
    assert first_id in finished


def test_close_waits_for_cooperative_worker_exit(monkeypatch):
    from seedlink.desktop.main_window import QMessageBox

    app = create_application([])
    window = SeedLinkMainWindow(settings=AppSettings())
    started = Event()

    def command(token, progress):
        del progress
        started.set()
        while not token.is_cancelled:
            time.sleep(0.002)
        time.sleep(0.05)
        token.raise_if_cancelled()

    window.controller.start("Тривала операція", command)
    assert started.wait(5)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        "seedlink.desktop.main_window.save_settings",
        lambda settings: None,
    )

    assert window.close()
    assert not window.controller.is_running
    app.processEvents()


def test_normal_cancellation_is_logged_as_info_without_traceback(tmp_path):
    from seedlink.desktop import AsyncCommandController
    from seedlink.support.logging_setup import configure_logging

    app = create_application([])
    target = tmp_path / "desktop.log"
    logger = configure_logging(target)
    controller = AsyncCommandController()
    started = Event()

    def command(token, progress):
        del progress
        started.set()
        while not token.is_cancelled:
            time.sleep(0.002)
        token.raise_if_cancelled()

    controller.start("Скасування", command)
    assert started.wait(5)
    controller.cancel()
    _wait_until(app, lambda: not controller.is_running)
    for handler in logger.handlers:
        handler.flush()

    content = target.read_text(encoding="utf-8")
    assert "INFO" in content
    assert "OPERATION_CANCELLED" in content
    assert "ERROR" not in content
    assert "Traceback" not in content
