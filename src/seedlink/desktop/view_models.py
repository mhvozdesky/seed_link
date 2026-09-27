"""Read-only desktop projections built from domain query results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from seedlink.domain.issues import Issue, IssueLevel
from seedlink.domain.measures import Measure, MeasureStatus
from seedlink.domain.models import (
    CropCategory,
    FunnelRow,
    LinkSummary,
    PersonMatchMethod,
    ProductLineFact,
    ReportResult,
    TimeBucket,
)
from seedlink.domain.provenance import SourceCell, SourceRecord
from seedlink.domain.queries import link_has_unresolved_issues


CROP_LABELS = {
    CropCategory.SUNFLOWER: "Соняшник",
    CropCategory.CORN: "Кукурудза",
    CropCategory.OTHER: "Інші культури",
    CropCategory.UNKNOWN: "Не визначено",
}
TIME_LABELS = {
    TimeBucket.BEFORE_LEAD: "До появи ліда",
    TimeBucket.ON_OR_AFTER_LEAD: "У день появи або пізніше",
    TimeBucket.UNKNOWN: "Неможливо визначити",
}
VOUCHER_METHOD_LABELS = {
    "exact_full": "Точний повний",
    "short_block": "Короткий номер",
    "distributor_variant": "Інший дистриб’ютор",
    "manual": "Ручне рішення",
}
ISSUE_LEVEL_LABELS = {
    IssueLevel.IMPORT_BLOCKING: "Блокування імпорту",
    IssueLevel.RECORD: "Проблема запису",
    IssueLevel.UNKNOWN_NUMERIC: "Невідомий числовий внесок",
    IssueLevel.INTERNAL: "Внутрішня помилка",
    IssueLevel.EXPORT: "Помилка експорту",
}
PERSON_METHOD_LABELS = {
    PersonMatchMethod.ID: "ID",
    PersonMatchMethod.UNIQUE_NAME: "Унікальне ПІБ",
    PersonMatchMethod.MANUAL: "Ручне рішення",
    PersonMatchMethod.UNRESOLVED: "Не зв’язано",
}


@dataclass(frozen=True, slots=True)
class ViewRow:
    values: dict[str, Any]
    detail: str


def decimal_text(value: Decimal | None) -> str:
    if value is None:
        return "—"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def date_text(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value is not None else "Дата невідома"


def measure_text(measure: Measure) -> str:
    return decimal_text(measure.known_value)


def measure_status(measure: Measure) -> str:
    if measure.status is MeasureStatus.COMPLETE:
        return "Повний показник"
    if measure.status is MeasureStatus.PARTIAL:
        return f"Відомий мінімум; невідомих внесків: {measure.unknown_count}"
    return "Недоступно: " + "; ".join(measure.reasons)


def query_summary(measures: tuple[Measure, ...]) -> str:
    parts: list[str] = []
    for measure in measures:
        value = measure_text(measure)
        suffix = f" {measure.unit}" if measure.unit else ""
        incomplete = (
            f"; невідомих внесків: {measure.unknown_count}"
            if measure.status is not MeasureStatus.COMPLETE
            else ""
        )
        parts.append(f"{measure.label_uk}: {value}{suffix}{incomplete}")
    return "  ·  ".join(parts)


def _source_cell_text(source: SourceCell) -> str:
    value = "" if source.original_value is None else str(source.original_value)
    return (
        f"{source.role.value} · {source.file_name} · {source.sheet_name} · "
        f"рядок {source.excel_row} · {source.field_name}: {value}"
    )


def _record_sources(records: tuple[SourceRecord, ...]) -> str:
    lines: list[str] = []
    for record in records:
        lines.append(
            f"{record.role.value} · {record.file_name} · {record.sheet_name} · "
            f"рядок {record.excel_row}"
        )
        lines.extend(
            f"  {cell.field_name}: {cell.original_value}"
            for cell in record.cells
            if cell.original_value is not None and str(cell.original_value) != ""
        )
    return "\n".join(lines) or "Джерела відсутні"


def build_link_rows(
    result: ReportResult, summaries: tuple[LinkSummary, ...]
) -> tuple[ViewRow, ...]:
    lead_by_key = {item.key: item for item in result.lead_refs}
    participant_by_key = {item.key: item for item in result.participants}
    voucher_by_key = {item.key: item for item in result.vouchers}
    accepted_by_key = {item.key: item for item in result.accepted_links}
    rows: list[ViewRow] = []
    for summary in summaries:
        lead = lead_by_key[summary.lead_ref_key]
        participant = participant_by_key.get(summary.participant_key or "")
        voucher = voucher_by_key[summary.voucher_key]
        accepted = accepted_by_key[summary.link_key]
        has_issue = link_has_unresolved_issues(result, accepted, summary)
        name = " / ".join(lead.candidate_names) or "Особа не визначена"
        participant_name = (
            " ".join(filter(None, (participant.first_name, participant.last_name)))
            if participant
            else "Не зв’язано"
        )
        detail = (
            f"Лід: {name}\nТелефон: {lead.phone or '—'}\n"
            f"Email: {lead.email or '—'}\nВаучер: {voucher.full_number}\n"
            f"Метод прийняття: {VOUCHER_METHOD_LABELS[accepted.method.value]}\n"
            f"Survey: {', '.join(accepted.survey_keys)}\n"
            f"Згадки: {', '.join(accepted.mention_keys)}\n\n"
            f"Джерела ліда:\n{_record_sources(lead.sources)}\n\n"
            "Джерела номера ваучера:\n"
            + "\n".join(_source_cell_text(cell) for cell in voucher.sources)
        )
        rows.append(
            ViewRow(
                {
                    "lead": name,
                    "voucher": voucher.full_number,
                    "participant": participant_name or "—",
                    "method": VOUCHER_METHOD_LABELS[accepted.method.value],
                    "tax": ", ".join(summary.tax_ids) or "—",
                    "quantity": summary.quantity.known_value,
                    "issues": "Є" if has_issue else "Немає",
                },
                detail,
            )
        )
    return tuple(rows)


def build_fact_rows(
    result: ReportResult, facts: tuple[ProductLineFact, ...]
) -> tuple[ViewRow, ...]:
    line_by_key = {item.key: item for item in result.product_lines}
    voucher_by_key = {item.key: item for item in result.vouchers}
    rows: list[ViewRow] = []
    for fact in facts:
        line = line_by_key[fact.product_line_key]
        voucher = voucher_by_key[fact.voucher_key]
        detail = (
            f"ProductLine: {line.key}\nВаучер: {voucher.full_number}\n"
            f"Tax ID: {fact.tax_id or '—'}\nКлієнт: {line.account_name or '—'}\n"
            f"Кількість: {decimal_text(fact.quantity)}\n"
            f"Культура: {CROP_LABELS[fact.crop]}\n"
            f"Гібрид: {fact.hybrid or '—'}\n"
            f"Опис: {fact.local_description or '—'}\n"
            f"Дата ProductLine: {date_text(fact.created_on)}\n"
            f"Опорна дата: {date_text(fact.reference_date)}\n\n"
            f"Джерела:\n{_record_sources(line.sources)}"
        )
        rows.append(
            ViewRow(
                {
                    "voucher": voucher.full_number,
                    "tax": fact.tax_id or "—",
                    "client": line.account_name or "—",
                    "crop": CROP_LABELS[fact.crop],
                    "hybrid": fact.hybrid or "—",
                    "quantity": fact.quantity,
                    "created": fact.created_on,
                    "reference": fact.reference_date,
                    "bucket": TIME_LABELS[fact.time_bucket],
                },
                detail,
            )
        )
    return tuple(rows)


def build_participant_rows(
    result: ReportResult, funnel_rows: tuple[FunnelRow, ...]
) -> tuple[ViewRow, ...]:
    participant_by_key = {item.key: item for item in result.participants}
    activity_by_key = {item.key: item for item in result.activities}
    rows: list[ViewRow] = []
    for item in funnel_rows:
        participant = participant_by_key[item.participant_key]
        name = (
            " ".join(filter(None, (participant.first_name, participant.last_name)))
            or "Без ПІБ"
        )
        activities = tuple(activity_by_key[key] for key in item.activity_keys)
        person_links = tuple(
            link
            for link in result.participant_links
            if link.participant_key == participant.key
        )
        method_labels = tuple(
            dict.fromkeys(PERSON_METHOD_LABELS[link.method] for link in person_links)
        )
        detail = (
            f"Учасник: {name}\n"
            f"Campaign Member ID: {participant.campaign_member_id or '—'}\n"
            f"Email: {participant.email or '—'}\n"
            f"Тип: {participant.member_type or '—'}\n"
            f"Статус: {participant.member_status or '—'}\n"
            f"Методи зв’язку: {', '.join(method_labels) or '—'}\n"
            f"Дата появи: {date_text(item.appeared_on)}\n"
            f"Активності: {len(item.activity_keys)}\n"
            f"Опитування: {len(item.survey_keys)}\n"
            f"Ваучери: {len(item.voucher_keys)}\n\n"
            f"Джерела учасника:\n{_record_sources(participant.sources)}"
        )
        if activities:
            detail += "\n\nПов’язані активності:\n" + "\n".join(
                f"{activity.activity_id or activity.key}: "
                f"{_record_sources(activity.sources)}"
                for activity in activities
            )
        rows.append(
            ViewRow(
                {
                    "name": name,
                    "type": participant.member_type or "—",
                    "status": participant.member_status or "—",
                    "methods": ", ".join(method_labels) or "—",
                    "appeared": item.appeared_on,
                    "activity": item.has_activity,
                    "result": item.has_recorded_result,
                    "voucher": item.has_confirmed_voucher,
                },
                detail,
            )
        )
    return tuple(rows)


def build_issue_rows(issues: tuple[Issue, ...]) -> tuple[ViewRow, ...]:
    rows: list[ViewRow] = []
    for issue in issues:
        source_summary = "—"
        if issue.sources:
            first = issue.sources[0]
            source_summary = (
                f"{first.role.value} · рядок {first.excel_row} · {first.field_name}"
            )
        detail_lines = [
            f"Код: {issue.code.value}",
            f"Рівень: {ISSUE_LEVEL_LABELS[issue.level]}",
            f"Стан: {'Вирішено' if issue.is_resolved else 'Потребує перевірки'}",
            f"Повідомлення: {issue.message_uk}",
        ]
        if issue.details:
            detail_lines.extend(f"{name}: {value}" for name, value in issue.details)
        if issue.sources:
            detail_lines.append("\nДжерела:")
            detail_lines.extend(_source_cell_text(source) for source in issue.sources)
        rows.append(
            ViewRow(
                {
                    "code": issue.code.value,
                    "level": ISSUE_LEVEL_LABELS[issue.level],
                    "state": "Вирішено" if issue.is_resolved else "Невирішено",
                    "message": issue.message_uk,
                    "source": source_summary,
                },
                "\n".join(detail_lines),
            )
        )
    return tuple(rows)
