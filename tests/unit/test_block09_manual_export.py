from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import errno
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from seedlink.application import (
    CancellationToken,
    DecisionValidationError,
    ExportError,
    ExportService,
    OperationCancelled,
    SeedLinkSession,
    SessionErrorCode,
)
from seedlink.desktop import SeedLinkMainWindow, create_application
from seedlink.desktop.manual_review import (
    DecisionChoice,
    DecisionRequest,
    ManualDecisionDialog,
    ManualReviewContext,
    review_context_for_issue,
    review_context_for_link,
)
from seedlink.domain import (
    DecisionAction,
    DecisionTarget,
    InputRole,
    CropCategory,
    IssueCode,
    business_control_payload,
)
from seedlink.domain.dates import KYIV
from seedlink.support.settings import AppSettings
from seedlink.reports import write_excel_report


MEMBER_A = "00vAbCdEfGhIjKlIVK"


def _rows(answer: str = "SE-900"):
    return {
        InputRole.R1: [{
            "Record Nr": "R1",
            "Survey Response ID": "S1",
            "Campaigm Member": MEMBER_A,
            "Answer": "Зафіксовано",
            "Answer (Long Text)": answer,
        }],
        InputRole.R2: [
            {
                "Opportunity Product Id 18": "P1",
                "Voucher Number": "SE-900",
                "Current Year Planned Quantity": 5,
                "Tax ID 1": "00000001",
                "Account Name": "Клієнт 1",
                "Species group": "SUN: Sunflowers",
            },
            {
                "Opportunity Product Id 18": "P2",
                "Voucher Number": "XY-901",
                "Current Year Planned Quantity": 7,
                "Tax ID 1": "00000002",
                "Account Name": "Клієнт 2",
                "Species group": "CRN: Corn",
            },
        ],
        InputRole.R3: [{
            "Campaign Member Id 18": MEMBER_A,
            "First Name": "Лід",
            "Last Name": "Один",
            "Member Type": "Lead",
        }],
    }


def _session(workbook_set_factory, rows=None):
    session = SeedLinkSession()
    imported = session.import_inputs(workbook_set_factory(rows or _rows()))
    assert imported.is_accepted
    return session, session.analyze()


def _wait_until(app, predicate, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Qt operation did not finish before timeout")


def test_export_service_publishes_two_verified_files_atomically(
    workbook_set_factory, tmp_path
):
    _, result = _session(workbook_set_factory)
    service = ExportService()
    timestamp = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

    first = service.export(result, tmp_path, exported_at=timestamp)
    second = service.export(result, tmp_path, exported_at=timestamp)

    assert first.directory.parent == tmp_path
    assert first.directory != second.directory
    assert first.excel_path.is_file()
    assert first.html_path.is_file()
    assert first.calculation_id == result.calculation_id
    assert first.revision == result.revision
    assert not list(tmp_path.glob(".seedlink-*"))


def test_export_failure_or_cancellation_leaves_no_partial_bundle(
    workbook_set_factory, tmp_path
):
    _, result = _session(workbook_set_factory)
    export_root = tmp_path / "exports"
    export_root.mkdir()
    previous = export_root / "SeedLink_previous"
    previous.mkdir()
    sentinel = previous / "keep.txt"
    sentinel.write_text("unchanged", encoding="utf-8")

    def broken_html(result, output, *, exported_at=None):
        del result, output, exported_at
        raise OSError("disk full")

    with pytest.raises(ExportError) as failed:
        ExportService(html_writer=broken_html).export(result, export_root)
    assert failed.value.code is SessionErrorCode.EXPORT_IO_FAILED
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert not list(export_root.glob(".seedlink-*"))
    assert [path.name for path in export_root.iterdir()] == ["SeedLink_previous"]

    token = CancellationToken()

    def cancel_between_files(update):
        if update.completed == 1:
            token.cancel()

    with pytest.raises(OperationCancelled):
        ExportService().export(
            result,
            export_root,
            cancellation=token,
            progress=cancel_between_files,
        )
    assert not list(export_root.glob(".seedlink-*"))
    assert [path.name for path in export_root.iterdir()] == ["SeedLink_previous"]


@pytest.mark.parametrize(
    ("error", "message_part"),
    (
        (PermissionError(errno.EACCES, "denied"), "немає прав"),
        (OSError(errno.ENOSPC, "full"), "бракує вільного місця"),
        (OSError(errno.EBUSY, "busy"), "зайнятий іншою програмою"),
    ),
)
def test_export_io_failures_have_stable_code_and_actionable_message(
    workbook_set_factory,
    tmp_path,
    error,
    message_part,
):
    _, result = _session(workbook_set_factory)

    def broken_excel(result, output, *, exported_at=None):
        del result, output, exported_at
        raise error

    with pytest.raises(ExportError) as captured:
        ExportService(excel_writer=broken_excel).export(result, tmp_path)

    assert captured.value.code is SessionErrorCode.EXPORT_IO_FAILED
    assert message_part in captured.value.message_uk
    assert "іншій папці" in captured.value.message_uk
    assert not list(tmp_path.glob(".seedlink-*"))


def test_export_folder_uses_kyiv_time_and_excel_metadata_is_exact(
    workbook_set_factory,
    tmp_path,
):
    _, automatic = _session(workbook_set_factory)
    timestamp = datetime(2026, 9, 27, 21, 30, tzinfo=UTC)

    bundle = ExportService().export(
        automatic,
        tmp_path,
        exported_at=timestamp,
    )
    assert bundle.directory.name.startswith("SeedLink_20260928_003000_r0")

    expected = replace(automatic, revision=1)
    wrong = replace(expected, revision=10)

    def wrong_revision_excel(result, output, *, exported_at=None):
        del result
        return write_excel_report(wrong, output, exported_at=exported_at)

    with pytest.raises(ExportError) as captured:
        ExportService(excel_writer=wrong_revision_excel).export(
            expected,
            tmp_path,
            exported_at=timestamp,
        )
    assert captured.value.code is SessionErrorCode.EXPORT_IO_FAILED
    assert "Excel-звіт" in captured.value.message_uk



def test_r04_requires_explicit_scope_and_rejects_survey_without_mentions(
    workbook_set_factory,
):
    rows = _rows("SE-900, XY-901")
    session, result = _session(workbook_set_factory, rows)
    survey = result.surveys[0]
    selected = next(item.key for item in result.vouchers if item.full_number == "SE-900")

    with pytest.raises(DecisionValidationError) as scope_error:
        session.select_vouchers(survey.key, (selected,), reason="Вибрано одну згадку")
    assert scope_error.value.code is SessionErrorCode.INVALID_DECISION
    assert dict(scope_error.value.details)["available_mention_keys"]

    no_mentions = _rows("текст без номера")
    empty_session, empty_result = _session(workbook_set_factory, no_mentions)
    with pytest.raises(DecisionValidationError) as no_mentions_error:
        empty_session.select_vouchers(
            empty_result.surveys[0].key,
            (empty_result.vouchers[0].key,),
            reason="Ручний вибір",
        )
    assert no_mentions_error.value.code is SessionErrorCode.INVALID_DECISION
    assert "не містить розпізнаних згадок" in no_mentions_error.value.message_uk


def test_r05_undo_restores_issue_resolution_projection(workbook_set_factory):
    rows = _rows("XY-901")
    rows[InputRole.R1][0]["Answer"] = "SE-900"
    session, original = _session(workbook_set_factory, rows)
    conflict = next(
        issue for issue in original.issues
        if issue.code is IssueCode.VOUCHER_FIELD_CONFLICT
    )
    survey = original.surveys[0]
    voucher = next(item for item in original.vouchers if item.full_number == "SE-900")

    revised = session.select_vouchers(survey.key, (voucher.key,))
    resolved = next(issue for issue in revised.issues if issue.issue_id == conflict.issue_id)
    assert resolved.is_resolved

    restored = session.undo_decision(revised.decisions[0].decision_id)
    after_undo = next(issue for issue in restored.issues if issue.issue_id == conflict.issue_id)
    assert not after_undo.is_resolved
    assert after_undo.resolved_by_decision_id is None
    assert business_control_payload(restored) == business_control_payload(original)


def test_r07_one_decision_rejects_multi_survey_link_and_one_undo_restores_it(
    workbook_set_factory,
):
    rows = _rows()
    rows[InputRole.R1].append(
        {
            "Record Nr": "R2",
            "Survey Response ID": "S2",
            "Campaigm Member": MEMBER_A,
            "Answer": "Зафіксовано повторно",
            "Answer (Long Text)": "SE-900",
        }
    )
    session, original = _session(workbook_set_factory, rows)
    link = next(
        item
        for item in original.accepted_links
        if len(item.survey_keys) == 2
    )

    context = review_context_for_link(original, link)

    assert len(context.scopes) == 2
    assert all(scope.recommended for scope in context.scopes)
    assert any("Survey S1" in scope.label for scope in context.scopes)
    assert any("Survey S2" in scope.label for scope in context.scopes)

    revised = session.apply_decision(
        context.target,
        context.target_key,
        DecisionAction.REJECT,
        mention_keys=tuple(scope.key for scope in context.scopes),
        reason="Зв’язок не належить цьому ліду",
    )

    assert len(revised.decisions) == 1
    assert not any(item.key == link.key for item in revised.accepted_links)

    restored = session.undo_decision(revised.decisions[0].decision_id)
    restored_link = next(item for item in restored.accepted_links if item.key == link.key)
    assert set(restored_link.survey_keys) == set(link.survey_keys)
    assert set(restored_link.mention_keys) == set(link.mention_keys)
    assert business_control_payload(restored) == business_control_payload(original)


def test_manual_review_context_searches_all_r2_and_exposes_scope(
    workbook_set_factory,
):
    rows = _rows("QQ-999")
    _, result = _session(workbook_set_factory, rows)
    issue = next(item for item in result.issues if item.code is IssueCode.VOUCHER_NOT_FOUND)

    context = review_context_for_issue(result, issue)

    assert context.target is DecisionTarget.VOUCHER_CASE
    assert {choice.label for choice in context.choices} == {"SE-900", "XY-901"}
    assert len(context.scopes) == 1
    assert context.scopes[0].recommended
    assert "Tax ID" in context.choices[0].detail


def test_participant_review_context_uses_all_r3_candidates(workbook_set_factory):
    rows = _rows()
    rows[InputRole.R1][0]["Campaigm Member"] = None
    rows[InputRole.R1][0]["Lead: Full Name"] = "Спільне Ім'я"
    rows[InputRole.R3] = [
        {
            "Campaign Member Id 18": MEMBER_A,
            "First Name": "Спільне",
            "Last Name": "Ім'я",
            "Member Type": "Lead",
        },
        {
            "Campaign Member Id 18": "00vAbCdEfGhIjKmIVK",
            "First Name": "Спільне",
            "Last Name": "Ім'я",
            "Member Type": "Contact",
        },
    ]
    _, result = _session(workbook_set_factory, rows)
    issue = next(
        item for item in result.issues
        if item.code is IssueCode.PERSON_LINK_AMBIGUOUS
    )

    context = review_context_for_issue(result, issue)

    assert context.target is DecisionTarget.LEAD_REF_PARTICIPANT
    assert len(context.choices) == 2
    assert all(choice.recommended for choice in context.choices)

    dated = replace(
        result.participants[0],
        first_associated_at=datetime(2026, 1, 2, 0, 30, tzinfo=KYIV),
    )
    dated_result = replace(
        result,
        participants=(dated,) + result.participants[1:],
    )
    dated_context = review_context_for_issue(dated_result, issue)
    dated_choice = next(item for item in dated_context.choices if item.key == dated.key)
    assert "дата: 02.01.2026" in dated_choice.detail


def test_voucher_card_uses_revision_summary_for_quantity_and_crops(
    workbook_set_factory,
):
    rows = _rows()
    rows[InputRole.R2].extend(
        [
            {
                "Opportunity Product Id 18": "P9",
                "Voucher Number": "SE-900",
                "Current Year Planned Quantity": 8,
                "Species group": "CRN: Corn Seeds",
            },
            {
                "Opportunity Product Id 18": "P9",
                "Voucher Number": "SE-900",
                "Current Year Planned Quantity": 9,
                "Species group": "CRN: Corn Seeds",
            },
        ]
    )
    _, result = _session(workbook_set_factory, rows)
    link = next(
        item
        for item in result.accepted_links
        if next(
            voucher.full_number
            for voucher in result.vouchers
            if voucher.key == item.voucher_key
        )
        == "SE-900"
    )

    context = review_context_for_link(result, link)
    choice = next(item for item in context.choices if item.label == "SE-900")
    summary = next(
        item for item in result.voucher_summaries if item.voucher_key == link.voucher_key
    )

    assert summary.quantity.known_value == Decimal(5)
    assert summary.quantity.unknown_count == 1
    assert "обсяг: 5 (Відомий мінімум; невідомих внесків: 1)" in choice.detail
    assert "Соняшник: 5" in choice.detail
    assert "Культура не визначена" in choice.detail
    assert "невідомих внесків: 1" in choice.detail


def test_manual_decision_dialog_validates_and_preserves_state(
    monkeypatch,
):
    app = create_application([])
    context = ManualReviewContext(
        title="Перевірка",
        description="Опис",
        target=DecisionTarget.VOUCHER_CASE,
        target_key="survey:1",
        choices=(
            DecisionChoice("voucher:1", "SE-1", "Кандидат 1"),
            DecisionChoice("voucher:2", "SE-2", "Кандидат 2"),
        ),
        scopes=(
            DecisionChoice("mention:1", "Survey S1 · SE-1", "Текст 1"),
            DecisionChoice("mention:2", "Survey S2 · SE-1", "Текст 2"),
        ),
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    dialog = ManualDecisionDialog(context)

    assert dialog.scopes is not None
    assert all(
        dialog.scopes.item(index).checkState() == Qt.CheckState.Checked
        for index in range(dialog.scopes.count())
    )
    reject_index = dialog.action.findData(DecisionAction.REJECT)
    dialog.action.setCurrentIndex(reject_index)
    assert not dialog.candidates.isEnabled()
    select_index = dialog.action.findData(DecisionAction.SELECT)
    dialog.action.setCurrentIndex(select_index)
    assert dialog.candidates.isEnabled()

    dialog.accept()
    assert warnings == ["Виберіть хоча б одного кандидата."]
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog.candidates.item(1).setSelected(True)
    dialog.search.setText("SE-2")
    dialog.reason.setPlainText("Перевірено вручну")
    dialog.show_validation_error("Для рішення потрібна причина")
    assert not dialog.validation_error.isHidden()
    assert dialog.validation_error.text() == "Для рішення потрібна причина"
    assert dialog.candidates.item(1).isSelected()
    assert dialog.reason.toPlainText() == "Перевірено вручну"

    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    request = dialog.request()
    assert request.action is DecisionAction.SELECT
    assert request.selected_keys == ("voucher:2",)
    assert request.mention_keys == ("mention:1", "mention:2")
    dialog.deleteLater()
    app.processEvents()


def test_typed_refusal_reopens_same_dialog_without_raw_keys(
    monkeypatch,
):
    app = create_application([])
    window = SeedLinkMainWindow(settings=AppSettings())
    context = ManualReviewContext(
        title="Перевірка",
        description="Опис",
        target=DecisionTarget.VOUCHER_CASE,
        target_key="survey:secret",
        choices=(DecisionChoice("voucher:1", "SE-1", "Кандидат"),),
        scopes=(DecisionChoice("mention:secret", "Survey S1 · SE-1", "Текст"),),
    )
    dialog = ManualDecisionDialog(context, window)
    dialog.candidates.item(0).setSelected(True)
    dialog.reason.setPlainText("Мій текст")
    window._decision_dialogs[77] = dialog
    reopened: list[ManualDecisionDialog] = []
    monkeypatch.setattr(
        window,
        "_run_manual_dialog",
        lambda current: reopened.append(current),
    )
    error = DecisionValidationError(
        SessionErrorCode.REASON_REQUIRED,
        "Для рішення поза автоматичними правилами вкажіть коротку причину.",
        details=(
            ("survey_key", "survey:secret"),
            ("available_mention_keys", "mention:one, mention:two"),
        ),
    )

    window._operation_failed(77, error)
    app.processEvents()

    assert reopened == [dialog]
    assert dialog.candidates.item(0).isSelected()
    assert dialog.reason.toPlainText() == "Мій текст"
    assert "Доступних згадок у картці: 2" in dialog.validation_error.text()
    assert "survey:secret" not in dialog.validation_error.text()
    assert "mention:one" not in dialog.validation_error.text()
    dialog.deleteLater()
    window.deleteLater()
    app.processEvents()


def test_desktop_decision_undo_and_export_full_result(
    workbook_set_factory, tmp_path, monkeypatch
):
    app = create_application([])
    paths = workbook_set_factory(_rows())
    window = SeedLinkMainWindow(settings=AppSettings())
    for role, path in paths.items():
        window._set_role_path(role, str(path))
    window.validate_inputs()
    _wait_until(app, lambda: not window.controller.is_running)
    window.calculate()
    _wait_until(app, lambda: not window.controller.is_running and window.result is not None)
    original = window.result
    survey = original.surveys[0]
    voucher = next(item for item in original.vouchers if item.full_number == "XY-901")
    mention = original.mentions[0]

    window._apply_manual_request(
        DecisionRequest(
            DecisionTarget.VOUCHER_CASE,
            survey.key,
            DecisionAction.SELECT,
            (voucher.key,),
            (mention.key,),
            "Виправлено номер ваучера",
        )
    )
    _wait_until(
        app,
        lambda: not window.controller.is_running and window.result.revision == 1,
    )
    assert window.session.state.decisions
    assert window.undo_button.isEnabled()
    assert "ще не збережено" in window.export_state_label.text()

    window._undo_selected_decision()
    _wait_until(
        app,
        lambda: not window.controller.is_running and window.result.revision == 2,
    )
    assert not window.session.state.decisions
    assert business_control_payload(window.result) == business_control_payload(original)

    corn_index = window.product_crop.findData(CropCategory.CORN)
    window.product_crop.setCurrentIndex(corn_index)
    assert window.product_model.rowCount() == 0
    monkeypatch.setattr(
        "seedlink.desktop.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path),
    )
    window.export_reports()
    _wait_until(app, lambda: not window.controller.is_running and window._last_bundle is not None)

    assert window.session.state.export_is_current
    assert window._last_bundle.excel_path.is_file()
    assert window._last_bundle.html_path.is_file()
    assert "збережено" in window.export_state_label.text()
    assert "SE-900" in window._last_bundle.html_path.read_text(encoding="utf-8")
    assert window.open_excel_button.isEnabled()

    window._apply_manual_request(
        DecisionRequest(
            DecisionTarget.VOUCHER_CASE,
            survey.key,
            DecisionAction.SELECT,
            (voucher.key,),
            (mention.key,),
            "Після експорту уточнено ваучер",
        )
    )
    _wait_until(
        app,
        lambda: not window.controller.is_running and window.result.revision == 3,
    )
    assert window.session.state.has_stale_export
    assert "попередньої ревізії" in window.export_state_label.text()
    window.session.close()
    window.deleteLater()
    app.processEvents()


def test_replacement_and_close_warn_about_unexported_decisions(
    workbook_set_factory, monkeypatch
):
    from seedlink.desktop.main_window import QMessageBox

    app = create_application([])
    paths = workbook_set_factory(_rows())
    window = SeedLinkMainWindow(settings=AppSettings())
    window.show()
    for role, path in paths.items():
        window._set_role_path(role, str(path))
    window.validate_inputs()
    _wait_until(app, lambda: not window.controller.is_running)
    window.calculate()
    _wait_until(app, lambda: not window.controller.is_running and window.result is not None)
    survey = window.result.surveys[0]
    mention = window.result.mentions[0]
    replacement = next(
        item for item in window.result.vouchers if item.full_number == "XY-901"
    )
    window._apply_manual_request(
        DecisionRequest(
            DecisionTarget.VOUCHER_CASE,
            survey.key,
            DecisionAction.SELECT,
            (replacement.key,),
            (mention.key,),
            "Виправлено ваучер",
        )
    )
    _wait_until(app, lambda: not window.controller.is_running)
    assert window.session.state.decisions

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    window._set_role_path(InputRole.R2, str(paths[InputRole.R2]))
    assert window._paths_are_verified()
    assert window.file_slots[InputRole.R2].property("slotState") == "accepted"
    assert not window.close()
    assert window.session.state.decisions

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window._set_role_path(InputRole.R2, str(paths[InputRole.R2]))
    assert not window._paths_are_verified()
    assert window.file_slots[InputRole.R2].property("slotState") == "pending"
    monkeypatch.setattr(
        "seedlink.desktop.main_window.save_settings",
        lambda settings: None,
    )
    assert window.close()
    assert not window.session.state.decisions
    app.processEvents()
