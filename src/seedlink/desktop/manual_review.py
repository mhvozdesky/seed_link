"""Manual-review contexts and dialogs backed by SeedLink domain identities."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from seedlink.application import (
    PERSON_DECISION_ISSUE_CODES,
    VOUCHER_DECISION_ISSUE_CODES,
)
from seedlink.domain.aggregation import crop_measures, measure_for_lines
from seedlink.domain.dates import calendar_date_in_kyiv
from seedlink.domain.issues import Issue
from seedlink.domain.models import (
    AcceptedLink,
    DecisionAction,
    DecisionTarget,
    Participant,
    ProductLine,
    ReportResult,
    Voucher,
    VoucherMention,
    VoucherSummary,
)
from seedlink.desktop.view_models import (
    date_text,
    decimal_text,
    measure_status,
)


@dataclass(frozen=True, slots=True)
class DecisionChoice:
    key: str
    label: str
    detail: str
    recommended: bool = False


@dataclass(frozen=True, slots=True)
class ManualReviewContext:
    title: str
    description: str
    target: DecisionTarget
    target_key: str
    choices: tuple[DecisionChoice, ...]
    scopes: tuple[DecisionChoice, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    target: DecisionTarget
    target_key: str
    action: DecisionAction
    selected_keys: tuple[str, ...]
    mention_keys: tuple[str, ...] | None
    reason: str | None


def _voucher_choice(
    voucher: Voucher,
    lines: tuple[ProductLine, ...],
    summary: VoucherSummary | None,
    *,
    recommended: bool,
) -> DecisionChoice:
    quantity = (
        summary.quantity
        if summary is not None
        else measure_for_lines(
            f"review.{voucher.key}.quantity",
            "Обсяг ваучера",
            lines,
        )
    )
    cultures = (
        summary.crop_measures
        if summary is not None
        else crop_measures(lines, prefix=f"review.{voucher.key}")
    )
    tax_ids = ", ".join(dict.fromkeys(line.tax_id for line in lines if line.tax_id)) or "—"
    clients = ", ".join(
        dict.fromkeys(line.account_name for line in lines if line.account_name)
    ) or "—"
    crop_parts = tuple(
        f"{measure.label_uk}: {decimal_text(measure.known_value)} "
        f"({measure_status(measure)})"
        for measure in cultures
        if measure.known_value or measure.unknown_count
    )
    crops = ", ".join(crop_parts) or "—"
    dates = sorted(
        value
        for line in lines
        if (value := calendar_date_in_kyiv(line.created_at)) is not None
    )
    date_range = (
        f"{date_text(dates[0])}–{date_text(dates[-1])}"
        if dates
        else date_text(None)
    )
    detail = (
        f"Tax ID: {tax_ids}; клієнт: {clients}; "
        f"обсяг: {decimal_text(quantity.known_value)} ({measure_status(quantity)}); "
        f"культури: {crops}; дати: {date_range}"
    )
    return DecisionChoice(voucher.key, voucher.full_number, detail, recommended)


def _participant_choice(
    participant: Participant, *, recommended: bool
) -> DecisionChoice:
    name = " ".join(
        value for value in (participant.first_name, participant.last_name) if value
    ) or "Без ПІБ"
    associated = date_text(calendar_date_in_kyiv(participant.first_associated_at))
    detail = (
        f"Campaign Member: {participant.campaign_member_id or '—'}; "
        f"тип: {participant.member_type or '—'}; "
        f"статус: {participant.member_status or '—'}; дата: {associated}"
    )
    return DecisionChoice(participant.key, name, detail, recommended)


def _voucher_choices(
    result: ReportResult, recommended_keys: set[str]
) -> tuple[DecisionChoice, ...]:
    lines_by_voucher: dict[str, list[ProductLine]] = {}
    for line in result.product_lines:
        if line.voucher_key is not None:
            lines_by_voucher.setdefault(line.voucher_key, []).append(line)
    summary_by_voucher = {
        summary.voucher_key: summary for summary in result.voucher_summaries
    }
    return tuple(
        _voucher_choice(
            voucher,
            tuple(lines_by_voucher.get(voucher.key, ())),
            summary_by_voucher.get(voucher.key),
            recommended=voucher.key in recommended_keys,
        )
        for voucher in sorted(result.vouchers, key=lambda item: item.full_number.casefold())
    )


def _participant_choices(
    result: ReportResult, recommended_keys: set[str]
) -> tuple[DecisionChoice, ...]:
    return tuple(
        _participant_choice(
            participant,
            recommended=participant.key in recommended_keys,
        )
        for participant in sorted(
            result.participants,
            key=lambda item: (
                (item.last_name or "").casefold(),
                (item.first_name or "").casefold(),
                item.key,
            ),
        )
    )


def _scope_choice(
    mention: VoucherMention,
    survey_label: str,
    recommended: bool,
) -> DecisionChoice:
    label = (
        f"Survey {survey_label} · {mention.matched_text} · "
        f"{mention.source.field_name} · "
        f"рядок {mention.source.excel_row}"
    )
    return DecisionChoice(
        mention.key,
        label,
        mention.original_text,
        recommended,
    )


def _review_description(
    result: ReportResult, issue: Issue, target_key: str
) -> str:
    voucher_by_key = {item.key: item for item in result.vouchers}
    participant_by_key = {item.key: item for item in result.participants}
    current: list[str] = []
    for link in result.accepted_links:
        if target_key in link.survey_keys:
            voucher = voucher_by_key.get(link.voucher_key)
            current.append(voucher.full_number if voucher is not None else link.voucher_key)
    for link in result.participant_links:
        if link.subject_key == target_key:
            participant = participant_by_key.get(link.participant_key or "")
            if participant is None:
                current.append("учасника не визначено")
            else:
                current.append(
                    " ".join(
                        value
                        for value in (participant.first_name, participant.last_name)
                        if value
                    )
                    or participant.key
                )
    current_text = ", ".join(dict.fromkeys(current)) or "немає"
    sources = "\n".join(
        f"{source.role.value} · {source.file_name} · рядок "
        f"{source.excel_row} · {source.field_name}: {source.original_value}"
        for source in issue.sources
    ) or "джерела не вказані"
    return (
        f"{issue.message_uk}\n\nПоточні зв’язки: {current_text}.\n"
        f"Джерела:\n{sources}\n\n"
        "Після застосування буде перераховано всі залежні показники й проблеми."
    )


def review_context_for_issue(
    result: ReportResult, issue: Issue
) -> ManualReviewContext:
    affected = set(issue.affected_keys)
    if issue.code in VOUCHER_DECISION_ISSUE_CODES:
        survey_keys = {item.key for item in result.surveys}
        mention_by_key = {item.key: item for item in result.mentions}
        survey_key = next((key for key in issue.affected_keys if key in survey_keys), None)
        if survey_key is None:
            mention = next(
                (mention_by_key[key] for key in issue.affected_keys if key in mention_by_key),
                None,
            )
            survey_key = mention.survey_key if mention is not None else None
        if survey_key is None:
            raise ValueError("Не вдалося визначити Survey для цієї проблеми.")
        mentions = tuple(item for item in result.mentions if item.survey_key == survey_key)
        if not mentions:
            raise ValueError(
                "Survey не містить розпізнаних згадок ваучера; вибір неможливий."
            )
        return ManualReviewContext(
            title="Ручна перевірка ваучерів",
            description=_review_description(result, issue, survey_key),
            target=DecisionTarget.VOUCHER_CASE,
            target_key=survey_key,
            choices=_voucher_choices(result, set(issue.candidate_keys)),
            scopes=tuple(
                _scope_choice(
                    mention,
                    next(
                        (
                            survey.response_id or f"рядок {survey.rows[0].excel_row}"
                            for survey in result.surveys
                            if survey.key == mention.survey_key
                        ),
                        mention.survey_key,
                    ),
                    mention.key in affected,
                )
                for mention in mentions
            ),
        )

    if issue.code in PERSON_DECISION_ISSUE_CODES:
        lead_keys = {item.key for item in result.lead_refs}
        activity_keys = {item.key for item in result.activities}
        target_key = next((key for key in issue.affected_keys if key in lead_keys), None)
        target = DecisionTarget.LEAD_REF_PARTICIPANT
        if target_key is None:
            target_key = next(
                (key for key in issue.affected_keys if key in activity_keys), None
            )
            target = DecisionTarget.ACTIVITY_PARTICIPANT
        if target_key is None:
            raise ValueError("Не вдалося визначити особу або активність для рішення.")
        return ManualReviewContext(
            title="Ручна перевірка учасника",
            description=_review_description(result, issue, target_key),
            target=target,
            target_key=target_key,
            choices=_participant_choices(result, set(issue.candidate_keys)),
        )

    raise ValueError("Цей тип проблеми не підтримує ручне рішення.")


def review_context_for_link(
    result: ReportResult, link: AcceptedLink
) -> ManualReviewContext:
    survey_key = link.survey_keys[0]
    scope_keys = set(link.mention_keys)
    mention_by_key = {item.key: item for item in result.mentions}
    survey_by_key = {item.key: item for item in result.surveys}
    mentions = tuple(
        mention_by_key[key]
        for key in link.mention_keys
        if key in mention_by_key
    )
    return ManualReviewContext(
        title="Уточнення зв’язку ліда з ваучером",
        description=(
            "Підтвердьте поточний ваучер, виберіть інший із R2, відхиліть "
            "зв’язок або залиште вибрану область невирішеною."
        ),
        target=DecisionTarget.VOUCHER_CASE,
        target_key=survey_key,
        choices=_voucher_choices(result, {link.voucher_key}),
        scopes=tuple(
            _scope_choice(
                mention,
                (
                    survey.response_id or f"рядок {survey.rows[0].excel_row}"
                    if (survey := survey_by_key.get(mention.survey_key)) is not None
                    else mention.survey_key
                ),
                mention.key in scope_keys,
            )
            for mention in mentions
        ),
    )


class ManualDecisionDialog(QDialog):
    """Searchable candidate chooser with an explicit decision scope."""

    def __init__(self, context: ManualReviewContext, parent=None) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle(context.title)
        self.resize(820, 650)
        root = QVBoxLayout(self)
        description = QLabel(context.description)
        description.setWordWrap(True)
        root.addWidget(description)

        self.validation_error = QLabel()
        self.validation_error.setObjectName("validationError")
        self.validation_error.setWordWrap(True)
        self.validation_error.setStyleSheet(
            "color: #8a1c1c; background: #fdecec; padding: 8px; "
            "border: 1px solid #d99; border-radius: 5px;"
        )
        self.validation_error.hide()
        root.addWidget(self.validation_error)

        self.action = QComboBox()
        self.action.addItem("Підтвердити вибраний варіант", DecisionAction.SELECT)
        self.action.addItem("Відхилити зв’язок", DecisionAction.REJECT)
        self.action.addItem("Залишити невирішеним", DecisionAction.LEAVE_UNRESOLVED)
        root.addWidget(self.action)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Пошук у всіх кандидатах поточного комплекту…")
        root.addWidget(self.search)
        self.candidates = QListWidget()
        self.candidates.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
            if context.target == DecisionTarget.VOUCHER_CASE
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        for choice in context.choices:
            item = QListWidgetItem(f"{choice.label}\n{choice.detail}")
            item.setData(Qt.ItemDataRole.UserRole, choice.key)
            item.setToolTip(choice.detail)
            self.candidates.addItem(item)
            if choice.recommended:
                item.setSelected(True)
        root.addWidget(self.candidates, 1)

        self.scopes: QListWidget | None = None
        if context.scopes:
            scope_header = QHBoxLayout()
            scope_header.addWidget(
                QLabel(
                    "Область рішення — конкретні згадки одного або кількох Survey:"
                )
            )
            select_all = QPushButton("Вибрати всю область зв’язку")
            select_all.clicked.connect(self._select_all_scopes)
            scope_header.addWidget(select_all)
            root.addLayout(scope_header)
            self.scopes = QListWidget()
            for choice in context.scopes:
                item = QListWidgetItem(choice.label)
                item.setData(Qt.ItemDataRole.UserRole, choice.key)
                item.setToolTip(choice.detail)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if choice.recommended
                    else Qt.CheckState.Unchecked
                )
                self.scopes.addItem(item)
            if not any(choice.recommended for choice in context.scopes):
                self._select_all_scopes()
            root.addWidget(self.scopes)

        root.addWidget(QLabel("Причина (обов’язкова для виходу за автоматичні правила):"))
        self.reason = QPlainTextEdit()
        self.reason.setMaximumHeight(90)
        root.addWidget(self.reason)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Застосувати")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.search.textChanged.connect(self._filter_candidates)
        self.action.currentIndexChanged.connect(self._update_action_state)
        self._update_action_state()

    def _filter_candidates(self, text: str) -> None:
        needle = " ".join(text.casefold().split())
        for index in range(self.candidates.count()):
            item = self.candidates.item(index)
            item.setHidden(needle not in " ".join(item.text().casefold().split()))

    def _select_all_scopes(self) -> None:
        if self.scopes is None:
            return
        for index in range(self.scopes.count()):
            self.scopes.item(index).setCheckState(Qt.CheckState.Checked)

    def _update_action_state(self) -> None:
        enabled = self.action.currentData() == DecisionAction.SELECT
        self.candidates.setEnabled(enabled)

    def _selected_keys(self) -> tuple[str, ...]:
        if self.action.currentData() != DecisionAction.SELECT:
            return ()
        return tuple(
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.candidates.selectedItems()
        )

    def _scope_keys(self) -> tuple[str, ...] | None:
        if self.scopes is None:
            return None
        return tuple(
            self.scopes.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.scopes.count())
            if self.scopes.item(index).checkState() == Qt.CheckState.Checked
        )

    def accept(self) -> None:
        if self.action.currentData() == DecisionAction.SELECT and not self._selected_keys():
            QMessageBox.warning(self, "Потрібен вибір", "Виберіть хоча б одного кандидата.")
            return
        scope = self._scope_keys()
        if scope is not None and not scope:
            QMessageBox.warning(
                self,
                "Потрібна область рішення",
                "Виберіть одну чи кілька згадок або всю область зв’язку.",
            )
            return
        super().accept()

    def request(self) -> DecisionRequest:
        reason = self.reason.toPlainText().strip() or None
        return DecisionRequest(
            target=self.context.target,
            target_key=self.context.target_key,
            # Qt stores StrEnum values as plain strings in item data. Restore
            # the domain enum before passing the request to SeedLinkSession,
            # whose public boundary deliberately rejects untyped actions.
            action=DecisionAction(self.action.currentData()),
            selected_keys=self._selected_keys(),
            mention_keys=self._scope_keys(),
            reason=reason,
        )

    def show_validation_error(self, message: str) -> None:
        """Show a typed refusal without discarding the user's dialog state."""

        self.validation_error.setText(message)
        self.validation_error.show()

    @classmethod
    def choose(
        cls, context: ManualReviewContext, parent=None
    ) -> DecisionRequest | None:
        dialog = cls(context, parent)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        request = dialog.request() if accepted else None
        dialog.deleteLater()
        return request
