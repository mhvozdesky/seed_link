"""Main Qt Widgets window for Block 08 import, calculation and review."""

from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer, Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from seedlink.application import SeedLinkSession
from seedlink.application.errors import (
    OperationCancelled,
    SessionError,
    StaleOperationError,
)
from seedlink.application.state import ProgressUpdate, SessionStatus
from seedlink.domain.issues import IssueLevel
from seedlink.domain.models import (
    CropCategory,
    ReportResult,
    TimeBucket,
    VoucherMatchMethod,
)
from seedlink.domain.provenance import InputRole
from seedlink.domain.queries import (
    DateFilter,
    DateFilterMode,
    FunnelFilter,
    IssueFilter,
    LeadVoucherFilter,
    OtherVoucherFilter,
    ProductFilter,
    query_funnel,
    query_issues,
    query_lead_vouchers,
    query_other_vouchers,
    query_product_lines,
)
from seedlink.desktop.async_commands import AsyncCommandController
from seedlink.desktop.table_models import Column, ObjectTableModel
from seedlink.desktop.widgets import BarChart, FileSlot, MetricCard, TablePane
from seedlink.support.paths import diagnostic_log_path
from seedlink.support.settings import AppSettings, load_settings, save_settings
from seedlink.desktop.view_models import (
    CROP_LABELS as _CROP_LABELS,
    TIME_LABELS as _TIME_LABELS,
    build_fact_rows,
    build_issue_rows,
    build_link_rows,
    build_participant_rows,
    date_text as _date_text,
    decimal_text as _decimal_text,
    measure_status as _measure_status,
    measure_text as _measure_text,
    query_summary,
)


_LOGGER = logging.getLogger("seedlink.desktop.main_window")


def _column(
    label: str,
    key: str,
    *,
    right: bool = False,
    display=None,
) -> Column:
    alignment = Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft
    return Column(
        label, lambda row, name=key: row.values.get(name), alignment, display
    )


class DiagnosticsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Діагностика SeedLink")
        self.resize(840, 520)
        layout = QVBoxLayout(self)
        path = diagnostic_log_path()
        layout.addWidget(QLabel(f"Локальний журнал: {path}"))
        text = QTextBrowser()
        try:
            content = path.read_text(encoding="utf-8")
            text.setPlainText(content[-120_000:])
        except FileNotFoundError:
            text.setPlainText("Журнал ще не створено.")
        except OSError as error:
            text.setPlainText(f"Не вдалося прочитати журнал: {error}")
        layout.addWidget(text, 1)
        close = QPushButton("Закрити")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)


class SeedLinkMainWindow(QMainWindow):
    """Responsive desktop projection over SeedLinkSession and domain queries."""

    def __init__(
        self,
        session: SeedLinkSession | None = None,
        *,
        settings: AppSettings | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.session = session or SeedLinkSession()
        self.settings = settings or load_settings()
        self.paths: dict[InputRole, Path] = {}
        self._verified_paths: dict[InputRole, Path] | None = None
        self.result: ReportResult | None = None
        self._operation_kind: dict[int, str] = {}
        self._closing = False
        self._last_input_dir = self.settings.last_input_dir
        self.controller = AsyncCommandController(self)
        self.controller.started.connect(self._operation_started)
        self.controller.progress.connect(self._progress_updated)
        self.controller.succeeded.connect(self._operation_succeeded)
        self.controller.failed.connect(self._operation_failed)
        self.controller.finished.connect(self._operation_finished)
        self.controller.queued.connect(self._operation_queued)
        self._build_ui()
        self._apply_style()
        self.resize(self.settings.window_width, self.settings.window_height)
        self.setMinimumSize(880, 640)
        self.setWindowTitle("SeedLink — Seed Selector")
        self._refresh_actions()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("SeedLink")
        title.setObjectName("appTitle")
        subtitle = QLabel("Перевірка експортів Salesforce і звіт Seed Selector")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        self.state_label = QLabel("Комплект не завантажено")
        self.state_label.setObjectName("stateBadge")
        header.addWidget(self.state_label)
        diagnostics = QPushButton("Діагностика")
        diagnostics.clicked.connect(self._show_diagnostics)
        header.addWidget(diagnostics)
        root.addLayout(header)

        inputs = QGroupBox("Вхідні файли")
        grid = QGridLayout(inputs)
        self.file_slots: dict[InputRole, FileSlot] = {}
        for index, role in enumerate(InputRole):
            slot = FileSlot(role)
            slot.browse_requested.connect(self._browse_for_role)
            slot.path_dropped.connect(self._set_role_path)
            self.file_slots[role] = slot
            grid.addWidget(slot, index // 2, index % 2)
        root.addWidget(inputs)

        self.input_issues = QGroupBox("Зауваження до вхідного комплекту")
        self.input_issues_layout = QVBoxLayout(self.input_issues)
        self.input_issues.setVisible(False)
        root.addWidget(self.input_issues)

        actions = QHBoxLayout()
        self.validate_button = QPushButton("Перевірити комплект")
        self.validate_button.clicked.connect(self.validate_inputs)
        self.calculate_button = QPushButton("Розрахувати")
        self.calculate_button.setObjectName("primaryButton")
        self.calculate_button.clicked.connect(self.calculate)
        self.cancel_button = QPushButton("Скасувати операцію")
        self.cancel_button.clicked.connect(self.controller.cancel)
        self.cancel_button.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        self.progress.setMinimumWidth(260)
        actions.addWidget(self.validate_button)
        actions.addWidget(self.calculate_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.progress, 1)
        root.addLayout(actions)

        self.result_context = QLabel()
        self.result_context.setObjectName("resultContext")
        self.result_context.setWordWrap(True)
        self.result_context.setVisible(False)
        root.addWidget(self.result_context)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_overview(), "Огляд")
        self.tabs.addTab(self._build_leads_area(), "Ліди та ваучери")
        self.tabs.addTab(self._build_participants_area(), "Учасники й активності")
        self.tabs.addTab(self._build_issues_area(), "Потребує перевірки")
        self.tabs.setEnabled(False)
        root.addWidget(self.tabs, 1)
        self.statusBar().showMessage("Виберіть по одному файлу для R1, R2, R3 і R4.")

    def _build_overview(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        self.metric_grid = QGridLayout()
        self.metric_cards: list[MetricCard] = []
        for index in range(8):
            card = MetricCard()
            self.metric_cards.append(card)
            self.metric_grid.addWidget(card, index // 4, index % 4)
        layout.addLayout(self.metric_grid)
        charts = QHBoxLayout()
        self.funnel_chart = BarChart("Охоплення", "осіб")
        self.crop_chart = BarChart("Культури", "од.")
        self.time_chart = BarChart("Часовий розподіл", "од.")
        charts.addWidget(self.funnel_chart)
        charts.addWidget(self.crop_chart)
        charts.addWidget(self.time_chart)
        layout.addLayout(charts)
        self.coverage_label = QLabel()
        self.coverage_label.setWordWrap(True)
        layout.addWidget(self.coverage_label)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _build_leads_area(self) -> QWidget:
        tabs = QTabWidget()
        self.link_model = ObjectTableModel(
            (
                _column("Лід", "lead"), _column("Ваучер", "voucher"),
                _column("Учасник R3", "participant"), _column("Метод", "method"),
                _column("Tax ID", "tax"), _column("Обсяг", "quantity", right=True, display=_decimal_text),
                _column("Проблеми", "issues"),
            )
        )
        self.link_pane = TablePane(self.link_model, search_placeholder="Лід, ваучер, Tax ID…")
        self.link_method = QComboBox()
        self.link_method.addItem("Усі методи", None)
        for value, label in (
            ("exact_full", "Точний повний"), ("short_block", "Короткий номер"),
            ("distributor_variant", "Інший дистриб’ютор"), ("manual", "Ручний"),
        ):
            self.link_method.addItem(label, value)
        self.link_method.currentIndexChanged.connect(self._refresh_links)
        self.link_issue = QComboBox()
        self.link_issue.addItem("Усі стани", None)
        self.link_issue.addItem("Є проблеми", True)
        self.link_issue.addItem("Без проблем", False)
        self.link_issue.currentIndexChanged.connect(self._refresh_links)
        self.link_pane.add_control(self.link_method)
        self.link_pane.add_control(self.link_issue)
        tabs.addTab(self.link_pane, "Ліди–ваучери")

        self.product_model = ObjectTableModel(
            (
                _column("Ваучер", "voucher"), _column("Tax ID", "tax"),
                _column("Клієнт", "client"), _column("Культура", "crop"),
                _column("Гібрид", "hybrid"), _column("Обсяг", "quantity", right=True, display=_decimal_text),
                _column("Створено", "created", display=_date_text), _column("Відносно ліда", "bucket"),
            )
        )
        self.product_pane = TablePane(self.product_model, search_placeholder="Ваучер, клієнт, гібрид…")
        self.product_crop = QComboBox()
        self.product_crop.addItem("Усі культури", None)
        for crop in CropCategory:
            self.product_crop.addItem(_CROP_LABELS[crop], crop)
        self.product_crop.currentIndexChanged.connect(self._refresh_products)
        self.product_date = self._date_mode_combo(self._refresh_products)
        self.product_pane.add_control(self.product_crop)
        self.product_pane.add_control(self.product_date)
        tabs.addTab(self.product_pane, "Товарні рядки")

        self.other_model = ObjectTableModel(
            (
                _column("Ваучер", "voucher"), _column("Tax ID", "tax"),
                _column("Клієнт", "client"), _column("Обсяг", "quantity", right=True, display=_decimal_text),
                _column("Створено", "created", display=_date_text), _column("Опорна дата", "reference", display=_date_text),
                _column("Відносно ліда", "bucket"),
            )
        )
        self.other_pane = TablePane(self.other_model, search_placeholder="Інший ваучер або Tax ID…")
        self.other_date = self._date_mode_combo(self._refresh_other)
        self.other_pane.add_control(self.other_date)
        tabs.addTab(self.other_pane, "Інші ваучери клієнтів")
        return tabs

    def _build_participants_area(self) -> QWidget:
        self.participant_model = ObjectTableModel(
            (
                _column("Учасник", "name"), _column("Тип", "type"),
                _column("Статус", "status"), _column("Метод зв’язку", "methods"),
                _column("Дата появи", "appeared", display=_date_text),
                _column("Активність", "activity"), _column("Результат", "result"),
                _column("Ваучер", "voucher"),
            )
        )
        self.participant_pane = TablePane(
            self.participant_model, search_placeholder="ПІБ, тип або статус…"
        )
        self.participant_type = QComboBox()
        self.participant_type.addItem("Усі типи", None)
        self.participant_type.addItem("Lead", "Lead")
        self.participant_type.addItem("Contact", "Contact")
        self.participant_type.currentIndexChanged.connect(self._refresh_participants)
        self.participant_date = self._date_mode_combo(self._refresh_participants)
        self.participant_pane.add_control(self.participant_type)
        self.participant_pane.add_control(self.participant_date)
        return self.participant_pane

    def _build_issues_area(self) -> QWidget:
        self.issue_model = ObjectTableModel(
            (
                _column("Код", "code"), _column("Рівень", "level"),
                _column("Стан", "state"), _column("Повідомлення", "message"),
                _column("Джерело", "source"),
            )
        )
        self.issue_pane = TablePane(
            self.issue_model, search_placeholder="Код, повідомлення або джерело…"
        )
        self.issue_level = QComboBox()
        self.issue_level.addItem("Усі рівні", None)
        for level, label in (
            (IssueLevel.RECORD, "Проблеми записів"),
            (IssueLevel.UNKNOWN_NUMERIC, "Невідомий обсяг"),
            (IssueLevel.IMPORT_BLOCKING, "Блокування імпорту"),
            (IssueLevel.INTERNAL, "Внутрішні"),
            (IssueLevel.EXPORT, "Експорт"),
        ):
            self.issue_level.addItem(label, level)
        self.issue_state = QComboBox()
        self.issue_state.addItem("Усі стани", None)
        self.issue_state.addItem("Невирішені", False)
        self.issue_state.addItem("Вирішені", True)
        self.issue_level.currentIndexChanged.connect(self._refresh_issues)
        self.issue_state.currentIndexChanged.connect(self._refresh_issues)
        self.issue_pane.add_control(self.issue_level)
        self.issue_pane.add_control(self.issue_state)
        return self.issue_pane

    @staticmethod
    def _date_mode_combo(callback) -> QComboBox:
        combo = QComboBox()
        combo.addItem("Усі дати", DateFilterMode.ALL)
        combo.addItem("Дата невідома", DateFilterMode.UNKNOWN_ONLY)
        combo.currentIndexChanged.connect(callback)
        return combo

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f5f7fa; color: #17283b; }
            QLabel#appTitle { font-size: 26px; font-weight: 700; color: #123d66; }
            QLabel#subtitle { color: #5d6d7e; }
            QLabel#stateBadge { padding: 7px 12px; background: #e9f1f8; border-radius: 8px; font-weight: 600; }
            QLabel#resultContext { padding: 9px 12px; color: #704800; background: #fff3cd; border: 1px solid #e0bd62; border-radius: 7px; font-weight: 600; }
            QFrame#fileSlot { background: white; border: 1px solid #c9d4df; border-radius: 9px; }
            QFrame#fileSlot[slotState="accepted"] { border: 2px solid #2e7d5b; }
            QFrame#fileSlot[slotState="error"] { border: 2px solid #b63d3d; }
            QFrame#fileSlot[slotState="pending"] { border: 2px solid #bf7b16; }
            QFrame#fileSlot:focus { border: 3px solid #1769a8; }
            QLabel#slotTitle { font-weight: 700; font-size: 14px; }
            QLabel#slotStatus { color: #5d6d7e; }
            QPushButton { min-height: 30px; padding: 3px 11px; }
            QPushButton#primaryButton { background: #1769a8; color: white; border: 0; border-radius: 5px; font-weight: 700; }
            QPushButton#primaryButton:disabled { background: #a9b6c2; }
            QFrame#metricCard { background: white; border: 1px solid #d5dee8; border-radius: 10px; }
            QLabel#metricLabel { color: #526579; }
            QLabel#metricValue { font-size: 23px; font-weight: 700; color: #123d66; }
            QTableView { background: white; alternate-background-color: #f3f7fb; gridline-color: #dbe3ec; }
            QHeaderView::section { background: #173c5f; color: white; padding: 6px; border: 0; }
            QTabWidget::pane { border: 1px solid #c9d4df; background: white; }
            """
        )

    @staticmethod
    def _canonical_paths(
        paths: dict[InputRole, Path],
    ) -> dict[InputRole, Path]:
        return {
            role: path.expanduser().resolve(strict=False)
            for role, path in paths.items()
        }

    def _paths_are_verified(self) -> bool:
        return (
            self._verified_paths is not None
            and set(self.paths) == set(InputRole)
            and self._canonical_paths(self.paths) == self._verified_paths
        )

    @staticmethod
    def _snapshot_label(result: ReportResult) -> str:
        loaded = result.snapshot.loaded_at.astimezone().strftime("%d.%m.%Y %H:%M")
        files = ", ".join(
            f"{source.role.value}: {source.file_name}"
            for source in result.snapshot.sources
        )
        short_id = result.snapshot_id.removeprefix("snapshot:")[:12]
        return f"{loaded}; {files}; snapshot {short_id}"

    def _update_result_context(self) -> None:
        if self.result is None:
            self.result_context.setVisible(False)
            return
        current = self.session.state.report_result
        is_current = (
            current is not None
            and current.calculation_id == self.result.calculation_id
            and self._paths_are_verified()
        )
        if is_current:
            self.result_context.setVisible(False)
            return
        self.result_context.setText(
            "Показано результат попереднього комплекту ("
            + self._snapshot_label(self.result)
            + "). Поточні вибрані або перевірені файли відрізняються; "
            "виконайте новий розрахунок."
        )
        self.result_context.setVisible(True)

    def _show_input_issue_summary(self, imported: object) -> None:
        while self.input_issues_layout.count():
            item = self.input_issues_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        issue_count = 0
        for role in InputRole:
            workbook = imported.workbook(role)
            if not workbook.issues:
                continue
            issue_count += len(workbook.issues)
            first = workbook.issues[0]
            button = QPushButton(
                f"{role.label_uk}: {len(workbook.issues)} — {first.message_uk}"
            )
            button.setToolTip("Перейти до проблемного файлового слота")
            button.clicked.connect(
                lambda checked=False, selected=role: self._focus_input_slot(
                    selected
                )
            )
            self.input_issues_layout.addWidget(button)
        heading = (
            "Зауваження до вхідного комплекту"
            if imported.is_accepted
            else "Проблеми вхідного комплекту"
        )
        self.input_issues.setTitle(f"{heading}: {issue_count}")
        self.input_issues.setVisible(issue_count > 0)

    def _focus_input_slot(self, role: InputRole) -> None:
        slot = self.file_slots[role]
        slot.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.statusBar().showMessage(
            f"Перевірте {role.label_uk}: {slot.status_label.text()}"
        )

    @Slot(object)
    def _browse_for_role(self, role: InputRole) -> None:
        start = self._last_input_dir or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Виберіть {role.label_uk}",
            start,
            "Книги Excel (*.xlsx)",
        )
        if path:
            self._set_role_path(role, path)

    @Slot(object, str)
    def _set_role_path(self, role: InputRole, path: str) -> None:
        selected = Path(path)
        self._verified_paths = None
        self.paths[role] = selected
        self._last_input_dir = str(selected.parent)
        self.file_slots[role].set_path(selected)
        self.state_label.setText("Комплект змінено — потрібна перевірка")
        self._update_result_context()

        self._refresh_actions()

    @Slot()
    def validate_inputs(self) -> None:
        if set(self.paths) != set(InputRole):
            return
        paths = dict(self.paths)
        operation_id = self.controller.start(
            "Перевірка вхідних книг",
            lambda token, progress: (
                self.session.import_inputs(
                    paths, cancellation=token, progress=progress
                ),
                paths,
            ),
        )
        self._operation_kind[operation_id] = "import"

    @Slot()
    def calculate(self) -> None:
        if (
            self.session.state.status is not SessionStatus.IMPORTED
            or not self._paths_are_verified()
        ):
            self.state_label.setText(
                "Вибрані файли відрізняються від перевіреного комплекту"
            )
            self._update_result_context()
            self._refresh_actions()
            return
        operation_id = self.controller.start(
            "Розрахунок звіту",
            lambda token, progress: self.session.analyze(
                cancellation=token, progress=progress
            ),
        )
        self._operation_kind[operation_id] = "analyze"

    @Slot(int, str)
    def _operation_started(self, operation_id: int, label: str) -> None:
        del operation_id
        self.state_label.setText(label)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat(label)
        self.cancel_button.setVisible(True)
        self._refresh_actions()

    @Slot(int, str)
    def _operation_queued(self, operation_id: int, label: str) -> None:
        del operation_id
        self.state_label.setText(f"{label} очікує завершення поточної операції")
        self.statusBar().showMessage(
            "Поточну операцію скасовано; наступна команда буде запущена після її виходу."
        )

    @Slot(int, object)
    def _progress_updated(self, operation_id: int, update: object) -> None:
        if operation_id != self.controller.active_operation_id:
            return
        if not isinstance(update, ProgressUpdate):
            return
        self.progress.setFormat(update.message_uk)
        if update.completed is None or update.total is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, update.total)
            self.progress.setValue(update.completed)
        self.statusBar().showMessage(update.message_uk)

    @Slot(int, object)
    def _operation_succeeded(self, operation_id: int, value: object) -> None:
        kind = self._operation_kind.get(operation_id)
        if kind == "import" and isinstance(value, tuple) and len(value) == 2:
            imported, validated_paths = value
            self._show_import_result(imported, validated_paths)
        elif kind == "analyze" and isinstance(value, ReportResult):
            self.result = value
            self._show_report(value)

    def _show_import_result(
        self,
        imported: object,
        validated_paths: dict[InputRole, Path],
    ) -> None:
        if not hasattr(imported, "workbooks"):
            return
        self._verified_paths = (
            self._canonical_paths(validated_paths)
            if imported.is_accepted
            else None
        )
        for role in InputRole:
            workbook = imported.workbook(role)
            if workbook.is_accepted:
                count = (
                    workbook.source.row_count
                    if workbook.source
                    else len(workbook.records)
                )
                warning_count = len(workbook.issues)
                message = f"Перевірено: {count} записів"
                if warning_count:
                    message += f"; зауважень: {warning_count}"
                self.file_slots[role].set_validation(
                    accepted=True, message=message
                )
            else:
                message = "\n".join(
                    issue.message_uk for issue in workbook.fatal_issues
                )
                self.file_slots[role].set_validation(
                    accepted=False, message=message or "Файл не прийнято"
                )
        self._show_input_issue_summary(imported)
        if imported.is_accepted:
            self.state_label.setText(
                "Комплект перевірено — готовий до розрахунку"
            )
            self.statusBar().showMessage(
                "Структуру всіх чотирьох книг підтверджено."
            )
        else:
            self.state_label.setText("Комплект має блокувальні проблеми")
            self.statusBar().showMessage(
                "Виправте позначені файли й повторіть перевірку."
            )
        self._update_result_context()

    def _show_report(self, result: ReportResult) -> None:
        self.tabs.setEnabled(True)
        self.state_label.setText(f"Результат · ревізія {result.revision}")
        self.statusBar().showMessage(
            f"Розрахунок завершено. Невирішених проблем: "
            f"{sum(not issue.is_resolved for issue in result.issues)}."
        )
        self._refresh_overview()
        self._refresh_links()
        self._refresh_products()
        self._refresh_other()
        self._refresh_participants()
        self._refresh_issues()
        self._update_result_context()

    @Slot(int, object)
    def _operation_failed(self, operation_id: int, error: object) -> None:
        del operation_id
        if isinstance(error, OperationCancelled):
            self.state_label.setText(
                "Операцію скасовано; попередній стан збережено"
            )
            self.statusBar().showMessage(error.message_uk)
            self._update_result_context()
            return
        if isinstance(error, StaleOperationError):
            self.state_label.setText("Застарілий результат операції відкинуто")
            self.statusBar().showMessage(error.message_uk)
            self._update_result_context()
            return
        message = (
            error.message_uk
            if isinstance(error, SessionError)
            else "Не вдалося завершити операцію. Деталі записано в діагностику."
        )
        if not self._closing:
            QMessageBox.critical(self, "SeedLink — помилка", message)
        self.state_label.setText("Помилка операції; перевірте повідомлення")
        self._update_result_context()

    @Slot(int)
    def _operation_finished(self, operation_id: int) -> None:
        self._operation_kind.pop(operation_id, None)
        if self.controller.is_running:
            return
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        running = self.controller.is_running
        complete_selection = set(self.paths) == set(InputRole)
        self.validate_button.setEnabled(not running and complete_selection)
        self.calculate_button.setEnabled(
            not running
            and self.session.state.status is SessionStatus.IMPORTED
            and self._paths_are_verified()
        )
        for slot in self.file_slots.values():
            slot.setEnabled(not running)

    def _refresh_overview(self) -> None:
        if self.result is None:
            return
        measures = {item.key: item for item in self.result.measures}
        keys = (
            "funnel.participants.count", "funnel.activity.count",
            "funnel.result.count", "funnel.voucher.count",
            "main.vouchers", "main.clients", "main.quantity",
            "quality.unresolved_issues",
        )
        for card, key in zip(self.metric_cards, keys, strict=True):
            measure = measures[key]
            unit = f" {measure.unit}" if measure.unit else ""
            card.set_metric(
                measure.label_uk,
                f"{_measure_text(measure)}{unit}",
                _measure_status(measure),
            )
        self.funnel_chart.set_values(
            tuple(
                (measures[key].label_uk, float(measures[key].known_value or 0), _measure_text(measures[key]))
                for key in (
                    "funnel.participants.count", "funnel.activity.count",
                    "funnel.result.count", "funnel.voucher.count",
                )
            )
        )
        self.crop_chart.set_values(
            tuple(
                (_CROP_LABELS[crop], float(measures[f"main.crop.{crop.value}"].known_value or 0), _measure_text(measures[f"main.crop.{crop.value}"]))
                for crop in CropCategory
            )
        )
        self.time_chart.set_values(
            tuple(
                (_TIME_LABELS[bucket], float(measures[f"main.time.{bucket.value}"].known_value or 0), _measure_text(measures[f"main.time.{bucket.value}"]))
                for bucket in TimeBucket
            )
        )
        self.coverage_label.setText(
            "Показники побудовано для повного завантаженого комплекту. Фільтри "
            "вкладок незалежні й не змінюють встановлені зв’язки або належність "
            "ваучера до основного/додаткового розділу."
        )

    def _refresh_links(self) -> None:
        result = self.result
        if result is None:
            return
        selected_method = self.link_method.currentData()
        methods = (VoucherMatchMethod(selected_method),) if selected_method else ()
        queried = query_lead_vouchers(
            result,
            LeadVoucherFilter(
                methods=methods,
                has_issues=self.link_issue.currentData(),
            ),
        )
        self.link_model.set_rows(build_link_rows(result, queried.rows))
        self.link_pane.summary.setText(query_summary(queried.measures))

    def _refresh_products(self) -> None:
        result = self.result
        if result is None:
            return
        crop = self.product_crop.currentData()
        mode = self.product_date.currentData() or DateFilterMode.ALL
        queried = query_product_lines(
            result,
            ProductFilter(
                created=DateFilter(mode=mode),
                crops=(crop,) if crop is not None else (),
            ),
        )
        self.product_model.set_rows(build_fact_rows(result, queried.rows))
        self.product_pane.summary.setText(query_summary(queried.measures))

    def _refresh_other(self) -> None:
        result = self.result
        if result is None:
            return
        mode = self.other_date.currentData() or DateFilterMode.ALL
        queried = query_other_vouchers(
            result, OtherVoucherFilter(created=DateFilter(mode=mode))
        )
        self.other_model.set_rows(build_fact_rows(result, queried.rows))
        self.other_pane.summary.setText(query_summary(queried.measures))

    def _refresh_participants(self) -> None:
        result = self.result
        if result is None:
            return
        member_type = self.participant_type.currentData()
        mode = self.participant_date.currentData() or DateFilterMode.ALL
        queried = query_funnel(
            result,
            FunnelFilter(
                appeared=DateFilter(mode=mode),
                member_types=(member_type,) if member_type else (),
            ),
        )
        self.participant_model.set_rows(
            build_participant_rows(result, queried.rows)
        )
        count_measures = tuple(
            measure
            for measure in queried.measures
            if measure.key.endswith(".count")
        )
        self.participant_pane.summary.setText(query_summary(count_measures))

    def _refresh_issues(self) -> None:
        result = self.result
        if result is None:
            return
        level = self.issue_level.currentData()
        queried = query_issues(
            result,
            IssueFilter(
                levels=(level,) if level is not None else (),
                resolved=self.issue_state.currentData(),
            ),
        )
        self.issue_model.set_rows(build_issue_rows(queried.rows))
        self.issue_pane.summary.setText(query_summary(queried.measures))

    @Slot()
    def _show_diagnostics(self) -> None:
        DiagnosticsDialog(self).exec()

    def _cancel_and_wait_for_worker(self, timeout_ms: int = 30_000) -> bool:
        if not self.controller.is_running:
            return True
        self.controller.cancel_all()
        dialog = QProgressDialog(
            "Завершення поточної операції…", "", 0, 0, self
        )
        dialog.setCancelButton(None)
        dialog.setWindowTitle("SeedLink")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setMinimumDuration(0)
        loop = QEventLoop(self)
        timer = QTimer(self)
        timer.setSingleShot(True)

        def stop_if_idle(operation_id: int) -> None:
            del operation_id
            if not self.controller.is_running:
                loop.quit()

        self.controller.finished.connect(stop_if_idle)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        dialog.show()
        if self.controller.is_running:
            loop.exec()
        timer.stop()
        dialog.close()
        try:
            self.controller.finished.disconnect(stop_if_idle)
        except (RuntimeError, TypeError):
            pass
        return not self.controller.is_running

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.controller.is_running:
            answer = QMessageBox.question(
                self,
                "Завершити SeedLink?",
                "Операція ще виконується. Скасувати її, дочекатися "
                "безпечного завершення та закрити програму?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._closing = True
            if not self._cancel_and_wait_for_worker():
                self._closing = False
                QMessageBox.warning(
                    self,
                    "SeedLink ще завершує операцію",
                    "Операція не завершилася впродовж 30 секунд. Вікно "
                    "залишиться відкритим; спробуйте закрити його ще раз після "
                    "завершення.",
                )
                event.ignore()
                return
        updated = replace(
            self.settings,
            last_input_dir=self._last_input_dir,
            window_width=max(self.width(), 640),
            window_height=max(self.height(), 480),
        )
        try:
            save_settings(updated)
        except OSError:
            _LOGGER.exception("Could not persist desktop settings")
        self.session.close()
        event.accept()
