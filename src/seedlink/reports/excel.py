"""Microsoft 365 Excel report for a complete :class:`ReportResult`.

The workbook is a projection only: matching, attribution and business totals
have already been decided by the domain layer.  Excel formulas aggregate the
visible rows of independent tables and carry verified initial cached values.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from seedlink.domain.issues import Issue
from seedlink.domain.measures import Measure, MeasureStatus
from seedlink.domain.models import (
    CropCategory,
    ParticipantLinkSubject,
    PersonMatchMethod,
    ProductLineFact,
    ReportResult,
    TimeBucket,
    VoucherMatchMethod,
)
from seedlink.domain.queries import issue_impacts
from seedlink.domain.provenance import SourceRecord
from seedlink.reports.excel_formulas import (
    VISIBLE_HEADER,
    completeness_formula,
    filtered_sum_formula,
    selected_by_visible_key_formula,
    selected_unique_count_formula,
    sumproduct_formula,
    table_column,
    visibility_formula,
    visible_count_formula,
    visible_unique_count_formula,
)


MAIN_SHEETS = (
    "Огляд",
    "Ліди–ваучери",
    "Товарні рядки",
    "Учасники та відповіді",
    "Інші ваучери клієнтів",
    "Проблеми",
)
AUDIT_SHEETS = ("Відповідність", "Джерела", "Метадані")
FACT_SHEET = "_Факти"

_CROP_LABEL = {
    CropCategory.SUNFLOWER: "Соняшник",
    CropCategory.CORN: "Кукурудза",
    CropCategory.OTHER: "Інші культури",
    CropCategory.UNKNOWN: "Культура не визначена",
}
_TIME_LABEL = {
    TimeBucket.BEFORE_LEAD: "До появи ліда",
    TimeBucket.ON_OR_AFTER_LEAD: "У день появи або пізніше",
    TimeBucket.UNKNOWN: "Неможливо визначити",
}
_VOUCHER_METHOD_LABEL = {
    VoucherMatchMethod.EXACT_FULL: "Точний повний номер",
    VoucherMatchMethod.SHORT_BLOCK: "Короткий номер",
    VoucherMatchMethod.DISTRIBUTOR_VARIANT: "Варіант дистриб’ютора",
    VoucherMatchMethod.MANUAL: "Ручне рішення",
}
_PERSON_METHOD_LABEL = {
    PersonMatchMethod.ID: "ID",
    PersonMatchMethod.UNIQUE_NAME: "Унікальне ім’я",
    PersonMatchMethod.MANUAL: "Ручне рішення",
    PersonMatchMethod.UNRESOLVED: "Не визначено",
}


@dataclass(frozen=True, slots=True)
class _Column:
    header: str
    width: float = 14
    kind: str = "text"
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class _Link:
    target: str
    label: str = "відкрити"


@dataclass(frozen=True, slots=True)
class _SummaryMetric:
    key: str
    label: str
    unit: str
    value_formula: str
    value_cache: int | float
    status_formula: str
    status_cache: str
    unknown_formula: str = "=0"
    unknown_cache: int | float = 0


@dataclass(frozen=True, slots=True)
class _SummaryCells:
    value: str
    status: str
    unknown: str


def _known(measure: Measure) -> Decimal:
    return measure.known_value if measure.known_value is not None else Decimal(0)


def _status(measure: Measure, *, has_rows: bool = True) -> str:
    if not has_rows and measure.known_value == 0 and not measure.unknown_count:
        return "немає даних"
    return {
        MeasureStatus.COMPLETE: "повний",
        MeasureStatus.PARTIAL: "частковий",
        MeasureStatus.UNAVAILABLE: "недоступний",
    }[measure.status]


def _measure(items: Iterable[Measure], key: str) -> Measure:
    return next(item for item in items if item.key == key)


def _measure_suffix(items: Iterable[Measure], suffix: str) -> Measure:
    return next(item for item in items if item.key.endswith(suffix))


def _join(values: Iterable[str | None], *, note: str = "повний перелік у деталізації") -> str:
    text = "; ".join(dict.fromkeys(value for value in values if value))
    if len(text) <= 30_000:
        return text
    marker = f" … [{note}]"
    return text[: 30_000 - len(marker)] + marker


def _source_location(record: SourceRecord) -> tuple[object, ...]:
    return (
        record.role,
        record.file_sha256,
        record.sheet_name,
        record.excel_row,
    )


def _sheet_ref(sheet: str, cell: str) -> str:
    return f"'{sheet.replace(chr(39), chr(39) * 2)}'!{cell}"


class _ExcelWriter:
    def __init__(self, result: ReportResult, output: Path, exported_at: datetime) -> None:
        self.result = result
        self.output = output
        self.exported_at = exported_at
        self.workbook: xlsxwriter.Workbook | None = None
        self.ws: dict[str, Any] = {}
        self.formats: dict[str, Any] = {}
        self.source_rows: dict[tuple[object, ...], int] = {}
        self.correspondence_rows: dict[str, int] = {}

        self.participant_by_key = {item.key: item for item in result.participants}
        self.lead_by_key = {item.key: item for item in result.lead_refs}
        self.voucher_by_key = {item.key: item for item in result.vouchers}
        self.product_by_key = {item.key: item for item in result.product_lines}
        self.mention_by_key = {item.key: item for item in result.mentions}
        self.decision_by_id = {item.decision_id: item for item in result.decisions}
        self.lead_date_by_key = {item.lead_ref_key: item for item in result.lead_dates}
        self.voucher_summary_by_key = {
            item.voucher_key: item for item in result.voucher_summaries
        }
        self.person_link_by_lead = {
            item.subject_key: item
            for item in result.participant_links
            if item.subject_kind is ParticipantLinkSubject.LEAD_REF
        }
        self.facts_by_voucher: dict[str, list[ProductLineFact]] = defaultdict(list)
        for fact in result.product_facts:
            self.facts_by_voucher[fact.voucher_key].append(fact)
        self.personal_times: dict[str, list[Any]] = defaultdict(list)
        for item in result.link_product_times:
            self.personal_times[item.link_key].append(item)

    def write(self) -> None:
        options = {
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "nan_inf_to_errors": False,
        }
        with xlsxwriter.Workbook(str(self.output), options) as workbook:
            self.workbook = workbook
            workbook.use_zip64()
            workbook.set_calc_mode("auto")
            workbook.set_properties(
                {
                    "title": "SeedLink — звіт Seed Selector",
                    "subject": "Повний Excel-звіт поточної ревізії",
                    "author": "SeedLink",
                    "comments": "Створено з канонічного ReportResult без макросів.",
                }
            )
            workbook.set_custom_property("SeedLink calculation ID", self.result.calculation_id)
            workbook.set_custom_property("SeedLink revision", self.result.revision, "integer")
            self._make_formats()
            for name in (*MAIN_SHEETS, *AUDIT_SHEETS, FACT_SHEET):
                self.ws[name] = workbook.add_worksheet(name)
                self.ws[name].hide_gridlines(2)
                self.ws[name].set_tab_color("#2F75B5" if name in MAIN_SHEETS else "#A5A5A5")

            self._write_sources()
            self._write_correspondence()
            self._write_facts()
            lead_cells = self._write_lead_vouchers()
            product_cells = self._write_products()
            participant_cells = self._write_participants()
            other_cells = self._write_other_vouchers()
            issue_cells = self._write_issues()
            self._write_metadata()
            self._write_overview(
                lead_cells, product_cells, participant_cells, other_cells, issue_cells
            )
            self.ws[FACT_SHEET].hide()
            self.ws["Огляд"].activate()

    def _make_formats(self) -> None:
        assert self.workbook is not None
        add = self.workbook.add_format
        base = {"font_name": "Aptos", "font_size": 10, "valign": "top"}
        self.formats = {
            "text": add(base),
            "wrap": add({**base, "text_wrap": True}),
            "id": add({**base, "num_format": "@"}),
            "number": add({**base, "num_format": "0.00"}),
            "integer": add({**base, "num_format": "0"}),
            "date": add({**base, "num_format": "dd.mm.yyyy"}),
            "datetime": add({**base, "num_format": "dd.mm.yyyy hh:mm"}),
            "title": add(
                {
                    "font_name": "Aptos Display",
                    "font_size": 20,
                    "bold": True,
                    "font_color": "#FFFFFF",
                    "bg_color": "#17365D",
                    "align": "left",
                    "valign": "vcenter",
                }
            ),
            "subtitle": add({**base, "font_color": "#44546A", "italic": True}),
            "section": add(
                {
                    **base,
                    "bold": True,
                    "font_color": "#FFFFFF",
                    "bg_color": "#2F75B5",
                }
            ),
            "metric_header": add(
                {
                    **base,
                    "bold": True,
                    "font_color": "#FFFFFF",
                    "bg_color": "#4472C4",
                    "border": 0,
                }
            ),
            "metric_label": add({**base, "bold": True, "bg_color": "#D9EAF7"}),
            "metric_value": add(
                {**base, "bold": True, "font_size": 12, "num_format": "0.00"}
            ),
            "status": add({**base, "align": "center"}),
            "note": add({**base, "font_size": 9, "font_color": "#666666", "text_wrap": True}),
            "link": add({**base, "font_color": "#0563C1", "underline": True}),
            "warning": add({**base, "font_color": "#9C0006", "bg_color": "#FFC7CE"}),
        }

    def _write_value(self, sheet: Any, row: int, col: int, value: object, kind: str = "text") -> None:
        fmt = self.formats.get(kind, self.formats["text"])
        if value is None:
            sheet.write_blank(row, col, None, fmt)
        elif isinstance(value, _Link):
            sheet.write_url(row, col, value.target, self.formats["link"], value.label)
        elif isinstance(value, bool):
            sheet.write_boolean(row, col, value, fmt)
        elif isinstance(value, Decimal):
            sheet.write_number(row, col, float(value), fmt)
        elif isinstance(value, int | float):
            sheet.write_number(row, col, value, fmt)
        elif isinstance(value, datetime):
            plain = value.replace(tzinfo=None) if value.tzinfo is not None else value
            sheet.write_datetime(row, col, plain, fmt)
        elif isinstance(value, date):
            sheet.write_datetime(row, col, datetime.combine(value, datetime.min.time()), fmt)
        else:
            sheet.write_string(row, col, str(value), fmt)

    def _title(self, sheet: Any, title: str, subtitle: str, last_col: int) -> None:
        sheet.merge_range(0, 0, 0, max(last_col, 5), title, self.formats["title"])
        sheet.set_row(0, 30)
        sheet.merge_range(1, 0, 1, max(last_col, 5), subtitle, self.formats["subtitle"])
        sheet.set_row(1, 28)

    def _source_link(self, records: Sequence[SourceRecord]) -> _Link | None:
        for record in records:
            excel_row = self.source_rows.get(_source_location(record))
            if excel_row is not None:
                return _Link(f"internal:'Джерела'!A{excel_row}")
        return None

    def _source_link_for_cell(self, cell: Any) -> _Link | None:
        key = (cell.role, cell.file_sha256, cell.sheet_name, cell.excel_row)
        excel_row = self.source_rows.get(key)
        return _Link(f"internal:'Джерела'!A{excel_row}") if excel_row else None

    def _write_table(
        self,
        sheet: Any,
        *,
        start_row: int,
        columns: Sequence[_Column],
        rows: Sequence[Sequence[object]],
        table_name: str,
        key_header: str,
        visible: bool = True,
    ) -> int:
        data_rows = rows or [tuple(None for _ in columns)]
        for row_offset, values in enumerate(data_rows, start=1):
            for col, column in enumerate(columns):
                value = values[col] if col < len(values) else None
                self._write_value(sheet, start_row + row_offset, col, value, column.kind)
        last_row = start_row + len(data_rows)
        table_columns = [
            {"header": column.header, "format": self.formats.get(column.kind)}
            for column in columns
        ]
        sheet.add_table(
            start_row,
            0,
            last_row,
            len(columns) - 1,
            {
                "name": table_name,
                "style": "Table Style Medium 2",
                "columns": table_columns,
            },
        )
        for col, column in enumerate(columns):
            sheet.set_column(col, col, column.width, self.formats.get(column.kind), {"hidden": column.hidden})
        if visible:
            visible_col = next(
                index for index, column in enumerate(columns) if column.header == VISIBLE_HEADER
            )
            formula = visibility_formula(key_header)
            for row_offset, values in enumerate(data_rows, start=1):
                key_index = next(
                    index for index, column in enumerate(columns) if column.header == key_header
                )
                cached = 1 if rows and values[key_index] not in (None, "") else 0
                sheet.write_formula(
                    start_row + row_offset,
                    visible_col,
                    formula,
                    self.formats["integer"],
                    cached,
                )
        sheet.freeze_panes(start_row + 1, 2)
        sheet.autofit(220)
        for col, column in enumerate(columns):
            sheet.set_column(col, col, min(column.width, 42), self.formats.get(column.kind), {"hidden": column.hidden})
        return last_row

    def _write_summary(
        self,
        sheet: Any,
        metrics: Sequence[_SummaryMetric],
        source_label: str,
    ) -> tuple[dict[str, _SummaryCells], int]:
        headers = ("Показник", "Значення", "Одиниця", "Повнота", "Невідомі внески", "Джерело фільтрів")
        for col, header in enumerate(headers):
            sheet.write(3, col, header, self.formats["metric_header"])
        result: dict[str, _SummaryCells] = {}
        for offset, metric in enumerate(metrics, start=4):
            sheet.write(offset, 0, metric.label, self.formats["metric_label"])
            sheet.write_formula(offset, 1, metric.value_formula, self.formats["metric_value"], metric.value_cache)
            sheet.write(offset, 2, metric.unit, self.formats["text"])
            sheet.write_formula(offset, 3, metric.status_formula, self.formats["status"], metric.status_cache)
            sheet.write_formula(offset, 4, metric.unknown_formula, self.formats["integer"], metric.unknown_cache)
            sheet.write(offset, 5, source_label, self.formats["note"])
            excel_row = offset + 1
            result[metric.key] = _SummaryCells(f"B{excel_row}", f"D{excel_row}", f"E{excel_row}")
        sheet.set_column(0, 0, 29)
        sheet.set_column(1, 1, 14)
        sheet.set_column(2, 2, 12)
        sheet.set_column(3, 4, 17)
        sheet.set_column(5, 5, 31)
        return result, 4 + len(metrics) + 2

    def _write_sources(self) -> None:
        sheet = self.ws["Джерела"]
        records = [record for source in self.result.snapshot.sources for record in source.records]
        fields: list[str] = []
        for record in records:
            for cell in record.cells:
                if cell.field_name not in fields:
                    fields.append(cell.field_name)
        columns = [
            _Column("Ключ джерельного рядка", 28, "id"),
            _Column("Роль", 8),
            _Column("Файл", 28),
            _Column("Аркуш джерела", 20),
            _Column("Excel-рядок", 12, "integer"),
            _Column("SHA-256", 66, "id"),
            *(_Column(field, 24, "wrap") for field in fields),
            _Column("Типи значень", 35, "wrap"),
            _Column("Нативні посилання джерела", 35, "wrap"),
        ]
        rows: list[tuple[object, ...]] = []
        for record in records:
            by_field = {cell.field_name: cell for cell in record.cells}
            types = _join(
                f"{cell.field_name}: {cell.value_kind.value}" for cell in record.cells
            )
            links = _join(
                f"{cell.field_name}: {cell.hyperlink_target}"
                for cell in record.cells
                if cell.hyperlink_target
            )
            rows.append(
                (
                    record.key,
                    record.role.value,
                    record.file_name,
                    record.sheet_name,
                    record.excel_row,
                    record.file_sha256,
                    *(by_field[field].original_value if field in by_field else None for field in fields),
                    types,
                    links,
                )
            )
        self._title(
            sheet,
            "Джерела",
            "Один фізичний рядок вхідного XLSX; формулоподібні рядки збережено як буквальний текст.",
            len(columns) - 1,
        )
        start = 3
        self._write_table(
            sheet,
            start_row=start,
            columns=columns,
            rows=rows,
            table_name="tblSources",
            key_header="Ключ джерельного рядка",
            visible=False,
        )
        for index, record in enumerate(records, start=start + 2):
            self.source_rows[_source_location(record)] = index

    def _write_correspondence(self) -> None:
        sheet = self.ws["Відповідність"]
        columns = (
            _Column("Ключ відповідності", 30, "id"),
            _Column("Статус", 14),
            _Column("Ключ пари", 30, "id"),
            _Column("LeadRef", 30, "id"),
            _Column("Survey", 30, "id"),
            _Column("Ключ згадки", 30, "id"),
            _Column("Згадка", 22, "wrap"),
            _Column("Поле", 24),
            _Column("Роль", 8),
            _Column("Файл", 28),
            _Column("Аркуш", 18),
            _Column("Excel-рядок", 12, "integer"),
            _Column("Повний ваучер", 34, "id"),
            _Column("Ключ ваучера", 30, "id"),
            _Column("Метод", 24),
            _Column("Кандидати R2", 38, "wrap"),
            _Column("Ручна причина", 38, "wrap"),
            _Column("Джерельний рядок", 17),
        )
        accepted_by_mention: dict[str, list[Any]] = defaultdict(list)
        for link in self.result.accepted_links:
            for key in link.mention_keys:
                accepted_by_mention[key].append(link)
        rows: list[tuple[object, ...]] = []
        row_keys: list[str] = []
        for mention in self.result.mentions:
            accepted = accepted_by_mention.get(mention.key) or [None]
            for sequence, link in enumerate(accepted, start=1):
                key = f"{mention.key}:{link.key if link else 'unmatched'}:{sequence}"
                decision = None
                voucher = self.voucher_by_key.get(link.voucher_key) if link else None
                method = ""
                if link:
                    evidence = next((item for item in link.evidence if item.mention_key == mention.key), None)
                    decision_id = evidence.decision_id if evidence else link.decision_id
                    decision = self.decision_by_id.get(decision_id or "")
                    method = _VOUCHER_METHOD_LABEL[evidence.method if evidence else link.method]
                rows.append(
                    (
                        key,
                        "Прийнято" if link else "Не прийнято",
                        link.key if link else None,
                        mention.lead_ref_key,
                        mention.survey_key,
                        mention.key,
                        mention.matched_text,
                        mention.source.field_name,
                        mention.source.role.value,
                        mention.source.file_name,
                        mention.source.sheet_name,
                        mention.source.excel_row,
                        voucher.full_number if voucher else None,
                        voucher.key if voucher else None,
                        method,
                        _join(mention.candidate_voucher_keys),
                        decision.reason if decision else None,
                        self._source_link_for_cell(mention.source),
                    )
                )
                row_keys.append(link.key if link else mention.key)
        self._title(
            sheet,
            "Відповідність опитувань і згадок",
            "Кожна згадка R1 окремо. Неприйняті згадки не створюють фіктивних ваучерів.",
            len(columns) - 1,
        )
        start = 3
        self._write_table(
            sheet,
            start_row=start,
            columns=columns,
            rows=rows,
            table_name="tblCorrespondence",
            key_header="Ключ відповідності",
            visible=False,
        )
        for row_index, row_key in enumerate(row_keys, start=start + 2):
            self.correspondence_rows.setdefault(row_key, row_index)

    def _write_facts(self) -> None:
        sheet = self.ws[FACT_SHEET]
        voucher_columns = (
            _Column("Ключ ваучера", 30, "id"),
            _Column("Повний ваучер", 34, "id"),
            _Column("Відомий обсяг", 14, "number"),
            _Column("Відомі внески", 14, "integer"),
            _Column("Невідомі внески", 16, "integer"),
            _Column("Вибрано парами", 16, "integer"),
        )
        voucher_rows: list[tuple[object, ...]] = []
        for summary in self.result.voucher_summaries:
            facts = self.facts_by_voucher[summary.voucher_key]
            voucher_rows.append(
                (
                    summary.voucher_key,
                    self.voucher_by_key[summary.voucher_key].full_number,
                    _known(summary.quantity),
                    sum(fact.quantity is not None for fact in facts),
                    summary.quantity.unknown_count,
                    None,
                )
            )
        start = 0
        last = self._write_table(
            sheet,
            start_row=start,
            columns=voucher_columns,
            rows=voucher_rows,
            table_name="tblVoucherMetrics",
            key_header="Ключ ваучера",
            visible=False,
        )
        selected_formula = selected_by_visible_key_formula(
            "tblLeadVoucher", "Ключ ваучера", "Ключ ваучера"
        )
        selected_col = 5
        for offset, row in enumerate(voucher_rows or [()], start=1):
            sheet.write_formula(start + offset, selected_col, selected_formula, self.formats["integer"], 1 if voucher_rows else 0)

        client_columns = (
            _Column("Ключ", 48, "id"),
            _Column("Ключ ваучера", 30, "id"),
            _Column("Tax ID", 18, "id"),
            _Column("Вибрано парами", 16, "integer"),
        )
        client_pairs = sorted(
            {
                (fact.voucher_key, fact.tax_id)
                for fact in self.result.product_facts
                if fact.tax_id is not None
            }
        )
        client_rows = [
            (f"{voucher_key}|{tax_id}", voucher_key, tax_id, None)
            for voucher_key, tax_id in client_pairs
        ]
        client_start = last + 3
        self._write_table(
            sheet,
            start_row=client_start,
            columns=client_columns,
            rows=client_rows,
            table_name="tblVoucherClients",
            key_header="Ключ",
            visible=False,
        )
        for offset, row in enumerate(client_rows or [()], start=1):
            sheet.write_formula(
                client_start + offset,
                3,
                selected_by_visible_key_formula("tblLeadVoucher", "Ключ ваучера", "Ключ ваучера"),
                self.formats["integer"],
                1 if client_rows else 0,
            )

    def _issues_for(self, keys: Iterable[str]) -> tuple[Issue, ...]:
        wanted = set(keys)
        return tuple(
            issue for issue in self.result.issues if wanted.intersection(issue.affected_keys)
        )

    def _product_date_range(self, keys: Iterable[str]) -> tuple[date | None, date | None]:
        wanted = set(keys)
        dates = [
            fact.created_on
            for fact in self.result.product_facts
            if fact.product_line_key in wanted and fact.created_on is not None
        ]
        return (min(dates), max(dates)) if dates else (None, None)

    def _hybrid_text(self, summaries: Iterable[Any]) -> str:
        values = []
        for item in summaries:
            label = item.hybrid or "гібрид не визначено"
            quantity = item.quantity
            known = str(quantity.known_value) if quantity.known_value is not None else "—"
            values.append(
                f"{_CROP_LABEL[item.crop]} — {label}: {known}; "
                f"{_status(quantity)}, невідомих {quantity.unknown_count}"
            )
        return _join(values, note="повний перелік у товарних рядках")

    def _write_lead_vouchers(self) -> dict[str, _SummaryCells]:
        sheet = self.ws["Ліди–ваучери"]
        main_quantity = _measure(self.result.measures, "main.quantity")
        row_count = len(self.result.link_summaries)
        metrics = (
            _SummaryMetric(
                "pairs", "Видимі пари", "пар",
                visible_count_formula("tblLeadVoucher"), row_count,
                '="повний"', "повний",
            ),
            _SummaryMetric(
                "vouchers", "Унікальні видимі ваучери", "ваучерів",
                visible_unique_count_formula("tblLeadVoucher", "Ключ ваучера"),
                len(self.result.voucher_summaries), '="повний"', "повний",
            ),
            _SummaryMetric(
                "clients", "Клієнти за Tax ID видимих ваучерів", "клієнтів",
                selected_unique_count_formula("tblVoucherClients", "Tax ID", "Вибрано парами"),
                len(self.result.clients), '="повний"', "повний",
            ),
            _SummaryMetric(
                "quantity", "Обсяг унікальних видимих ваучерів", "од.",
                sumproduct_formula("tblVoucherMetrics", "Відомий обсяг", "Вибрано парами"),
                float(_known(main_quantity)),
                completeness_formula(
                    "B5",
                    "SUMPRODUCT(tblVoucherMetrics[Вибрано парами],tblVoucherMetrics[Відомі внески])",
                    "E8",
                ),
                _status(main_quantity, has_rows=bool(row_count)),
                sumproduct_formula("tblVoucherMetrics", "Невідомі внески", "Вибрано парами"),
                main_quantity.unknown_count,
            ),
        )
        columns = (
            _Column("Ключ пари", 31, "id"),
            _Column("Особа", 24),
            _Column("Ключ учасника", 30, "id"),
            _Column("Ключ зв’язку учасника", 31, "id"),
            _Column("Метод ідентифікації", 21),
            _Column("Статус ідентифікації", 20),
            _Column("LeadRef", 31, "id"),
            _Column("Survey", 36, "wrap"),
            _Column("Згадки R1", 34, "wrap"),
            _Column("Поля R1", 25, "wrap"),
            _Column("Рядки R1", 30, "wrap"),
            _Column("Повний ваучер", 38, "id"),
            _Column("Ключ ваучера", 31, "id"),
            _Column("Назви клієнтів", 38, "wrap"),
            _Column("Tax ID", 25, "id"),
            _Column("Відомий обсяг", 16, "number"),
            _Column("Повнота обсягу", 18),
            _Column("Невідомі внески", 18, "integer"),
            _Column("Соняшник", 14, "number"),
            _Column("Кукурудза", 14, "number"),
            _Column("Інші культури", 15, "number"),
            _Column("Культура невідома", 18, "number"),
            _Column("Гібриди та повнота", 46, "wrap"),
            _Column("Дата ліда", 13, "date"),
            _Column("Джерело дати ліда", 22),
            _Column("Пара: до ліда", 14, "number"),
            _Column("Пара: у день/після", 17, "number"),
            _Column("Пара: час невідомий", 18, "number"),
            _Column("Опорна дата ваучера", 17, "date"),
            _Column("Ваучер: до опори", 16, "number"),
            _Column("Ваучер: у день/після", 19, "number"),
            _Column("Ваучер: час невідомий", 20, "number"),
            _Column("ProductLine від", 15, "date"),
            _Column("ProductLine до", 15, "date"),
            _Column("Метод прийняття", 24),
            _Column("Ручна причина", 40, "wrap"),
            _Column("Проблеми", 46, "wrap"),
            _Column("Відповідність згадок", 20),
            _Column(VISIBLE_HEADER, 10, "integer", True),
        )
        rows: list[tuple[object, ...]] = []
        accepted_by_key = {item.key: item for item in self.result.accepted_links}
        for summary in self.result.link_summaries:
            link = accepted_by_key[summary.link_key]
            person_link = self.person_link_by_lead.get(summary.lead_ref_key)
            participant = self.participant_by_key.get(summary.participant_key or "")
            person = _join(
                (participant.first_name, participant.last_name)
                if participant else self.lead_by_key[summary.lead_ref_key].candidate_names
            )
            mentions = [self.mention_by_key[key] for key in link.mention_keys]
            lead_date = self.lead_date_by_key.get(summary.lead_ref_key)
            voucher_summary = self.voucher_summary_by_key[summary.voucher_key]
            start_date, end_date = self._product_date_range(summary.product_line_keys)
            issues = self._issues_for(
                (
                    summary.link_key,
                    summary.lead_ref_key,
                    summary.voucher_key,
                    *link.survey_keys,
                    *link.mention_keys,
                    *summary.product_line_keys,
                )
            )
            crop = {item.key.rsplit(".", 1)[-1]: item for item in summary.crop_measures}
            personal_time = {item.key.rsplit(".", 1)[-1]: item for item in summary.time_measures}
            global_time = {item.key.rsplit(".", 1)[-1]: item for item in voucher_summary.time_measures}
            decision = self.decision_by_id.get(link.decision_id or "")
            correspondence_row = self.correspondence_rows.get(link.key)
            rows.append(
                (
                    summary.link_key,
                    person,
                    summary.participant_key,
                    person_link.key if person_link else None,
                    _PERSON_METHOD_LABEL[person_link.method] if person_link else "Не визначено",
                    "Пов’язано" if summary.participant_key else "Не визначено",
                    summary.lead_ref_key,
                    _join(link.survey_keys),
                    _join(mention.matched_text for mention in mentions),
                    _join(mention.source.field_name for mention in mentions),
                    _join(
                        f"{mention.source.file_name} / {mention.source.sheet_name} / {mention.source.excel_row}"
                        for mention in mentions
                    ),
                    self.voucher_by_key[summary.voucher_key].full_number,
                    summary.voucher_key,
                    _join(summary.client_names),
                    _join(summary.tax_ids),
                    _known(summary.quantity),
                    _status(summary.quantity),
                    summary.quantity.unknown_count,
                    _known(crop[CropCategory.SUNFLOWER.value]),
                    _known(crop[CropCategory.CORN.value]),
                    _known(crop[CropCategory.OTHER.value]),
                    _known(crop[CropCategory.UNKNOWN.value]),
                    self._hybrid_text(summary.hybrid_summaries),
                    lead_date.value if lead_date else None,
                    lead_date.source.value if lead_date else "unknown",
                    _known(personal_time[TimeBucket.BEFORE_LEAD.value]),
                    _known(personal_time[TimeBucket.ON_OR_AFTER_LEAD.value]),
                    _known(personal_time[TimeBucket.UNKNOWN.value]),
                    voucher_summary.reference_date,
                    _known(global_time[TimeBucket.BEFORE_LEAD.value]),
                    _known(global_time[TimeBucket.ON_OR_AFTER_LEAD.value]),
                    _known(global_time[TimeBucket.UNKNOWN.value]),
                    start_date,
                    end_date,
                    _VOUCHER_METHOD_LABEL[link.method],
                    decision.reason if decision else None,
                    _join(f"{issue.code.value}: {issue.message_uk}" for issue in issues),
                    _Link(f"internal:'Відповідність'!A{correspondence_row}") if correspondence_row else None,
                    None,
                )
            )
        self._title(
            sheet,
            "Ліди–ваучери",
            "Один рядок — одна прийнята пара LeadRef–Voucher. Підсумки реагують лише на фільтри цієї таблиці.",
            len(columns) - 1,
        )
        cells, table_start = self._write_summary(sheet, metrics, "За фільтрами «Ліди–ваучери»")
        self._write_table(
            sheet, start_row=table_start, columns=columns, rows=rows,
            table_name="tblLeadVoucher", key_header="Ключ пари",
        )
        return cells

    def _write_products(self) -> dict[str, _SummaryCells]:
        sheet = self.ws["Товарні рядки"]
        quantity = _measure(self.result.measures, "main.quantity")
        row_count = len(self.result.product_facts)
        materialized_unknown = sum(item.quantity is None for item in self.result.product_facts)
        extra_unknown = max(quantity.unknown_count - materialized_unknown, 0)
        visible_count = visible_count_formula("tblMainProducts")
        unknown_formula = sumproduct_formula("tblMainProducts", "Невідомий внесок")
        if extra_unknown:
            unknown_formula = (
                f"={unknown_formula[1:]}+IF({visible_count[1:]}="
                f"COUNTA(tblMainProducts[Ключ ProductLine]),{extra_unknown},0)"
            )
        metrics = (
            _SummaryMetric("rows", "Видимі ProductLine", "рядків", visible_count, row_count, '="повний"', "повний"),
            _SummaryMetric(
                "vouchers", "Унікальні видимі ваучери", "ваучерів",
                visible_unique_count_formula("tblMainProducts", "Ключ ваучера"),
                len({item.voucher_key for item in self.result.product_facts}), '="повний"', "повний",
            ),
            _SummaryMetric(
                "clients", "Унікальні видимі Tax ID", "клієнтів",
                visible_unique_count_formula("tblMainProducts", "Tax ID"),
                len({item.tax_id for item in self.result.product_facts if item.tax_id}), '="повний"', "повний",
            ),
            _SummaryMetric(
                "quantity", "Видимий обсяг", "од.",
                sumproduct_formula("tblMainProducts", "Кількість"), float(_known(quantity)),
                completeness_formula(
                    "B5", "SUMPRODUCT(tblMainProducts[Видимий],tblMainProducts[Відомий внесок])", "E8"
                ),
                _status(quantity, has_rows=bool(row_count)), unknown_formula, quantity.unknown_count,
            ),
        )
        columns = (
            _Column("Ключ ProductLine", 31, "id"), _Column("Product ID", 25, "id"),
            _Column("Повний ваучер", 38, "id"), _Column("Ключ ваучера", 31, "id"),
            _Column("Кількість", 14, "number"), _Column("Відомий внесок", 15, "integer"),
            _Column("Невідомий внесок", 17, "integer"), _Column("Культура", 22),
            _Column("Група культур R2", 24), _Column("Гібрид", 28),
            _Column("Гібрид визначено", 18), _Column("Локальний опис", 32, "wrap"),
            _Column("Tax ID", 18, "id"), _Column("Назва клієнта", 32, "wrap"),
            _Column("Дата ProductLine", 17, "date"), _Column("Місяць ProductLine", 18),
            _Column("Опорна дата", 15, "date"), _Column("Часова складова", 27),
            _Column("Проблеми", 44, "wrap"), _Column("Джерельний рядок", 18),
            _Column(VISIBLE_HEADER, 10, "integer", True),
        )
        rows: list[tuple[object, ...]] = []
        for fact in self.result.product_facts:
            product = self.product_by_key[fact.product_line_key]
            issues = self._issues_for((fact.product_line_key, fact.voucher_key))
            rows.append((
                fact.product_line_key, product.product_id,
                self.voucher_by_key[fact.voucher_key].full_number, fact.voucher_key,
                fact.quantity, int(fact.quantity is not None), int(fact.quantity is None),
                _CROP_LABEL[fact.crop], product.species_group, fact.hybrid,
                "Так" if fact.hybrid else "Ні", fact.local_description, fact.tax_id,
                product.account_name, fact.created_on,
                fact.created_on.strftime("%Y-%m") if fact.created_on else "Дата невідома",
                fact.reference_date, _TIME_LABEL[fact.time_bucket],
                _join(f"{issue.code.value}: {issue.message_uk}" for issue in issues),
                self._source_link(product.sources), None,
            ))
        self._title(
            sheet, "Товарні рядки",
            "Один рядок — один ProductLine основного ваучера. Фільтри цієї таблиці не змінюють воронку.",
            len(columns) - 1,
        )
        cells, table_start = self._write_summary(sheet, metrics, "За фільтрами «Товарні рядки»")
        self._write_table(
            sheet, start_row=table_start, columns=columns, rows=rows,
            table_name="tblMainProducts", key_header="Ключ ProductLine",
        )
        return cells

    def _write_participants(self) -> dict[str, _SummaryCells]:
        sheet = self.ws["Учасники та відповіді"]
        keys = ("participants", "activity", "result", "voucher")
        labels = ("Учасники", "Є активність", "Результат зафіксовано", "Є підтверджений ваучер")
        headers = ("Ключ учасника", "Є активність", "Є результат", "Є ваучер")
        measures = [_measure(self.result.measures, f"funnel.{key}.count") for key in keys]
        metrics = tuple(
            _SummaryMetric(
                key, label, "осіб",
                visible_count_formula("tblParticipants") if key == "participants" else
                sumproduct_formula("tblParticipants", headers[index]),
                float(_known(measure)),
                '="повний"' if measure.unknown_count == 0 else
                f'=IF(SUM(tblParticipants[Видимий])=COUNTA(tblParticipants[Ключ учасника]),"частковий","повний")',
                _status(measure, has_rows=bool(self.result.funnel_rows)),
                f'=IF(SUM(tblParticipants[Видимий])=COUNTA(tblParticipants[Ключ учасника]),{measure.unknown_count},0)',
                measure.unknown_count,
            )
            for index, (key, label, measure) in enumerate(zip(keys, labels, measures))
        )
        columns = (
            _Column("Ключ учасника", 31, "id"), _Column("Campaign Member ID", 24, "id"),
            _Column("Ім’я", 18), _Column("Прізвище", 22), _Column("Email", 28, "id"),
            _Column("Тип", 16), _Column("Статус", 18), _Column("Дата появи", 15, "date"),
            _Column("Є активність", 14, "integer"), _Column("Є результат", 14, "integer"),
            _Column("Є ваучер", 12, "integer"), _Column("Активності", 40, "wrap"),
            _Column("Survey", 40, "wrap"), _Column("Ваучери", 42, "wrap"),
            _Column("Методи зв’язку", 36, "wrap"), _Column("Ключі зв’язків", 42, "wrap"),
            _Column("Джерельний рядок", 18), _Column(VISIBLE_HEADER, 10, "integer", True),
        )
        links_by_participant: dict[str, list[Any]] = defaultdict(list)
        for link in self.result.participant_links:
            if link.participant_key:
                links_by_participant[link.participant_key].append(link)
        rows = []
        for funnel in self.result.funnel_rows:
            participant = self.participant_by_key[funnel.participant_key]
            links = links_by_participant[funnel.participant_key]
            rows.append((
                participant.key, participant.campaign_member_id, participant.first_name,
                participant.last_name, participant.email, participant.member_type,
                participant.member_status, funnel.appeared_on, int(funnel.has_activity),
                int(funnel.has_recorded_result), int(funnel.has_confirmed_voucher),
                _join(funnel.activity_keys), _join(funnel.survey_keys),
                _join(self.voucher_by_key[key].full_number for key in funnel.voucher_keys),
                _join(_PERSON_METHOD_LABEL[link.method] for link in links),
                _join(link.key for link in links), self._source_link(participant.sources), None,
            ))
        self._title(
            sheet, "Учасники та відповіді",
            "Один рядок — один учасник R3; ключі дають доступ до всіх пов’язаних Survey та активностей.",
            len(columns) - 1,
        )
        cells, table_start = self._write_summary(sheet, metrics, "За фільтрами «Учасники та відповіді»")
        self._write_table(
            sheet, start_row=table_start, columns=columns, rows=rows,
            table_name="tblParticipants", key_header="Ключ учасника",
        )
        return cells

    def _write_other_vouchers(self) -> dict[str, _SummaryCells]:
        sheet = self.ws["Інші ваучери клієнтів"]
        grouped: dict[tuple[str, str], list[ProductLineFact]] = defaultdict(list)
        for fact in self.result.other_product_facts:
            grouped[(fact.tax_id or "", fact.voucher_key)].append(fact)
        other_quantity = _measure(self.result.measures, "other.quantity")
        metrics = (
            _SummaryMetric("rows", "Видимі клієнтські частини", "рядків", visible_count_formula("tblOtherVouchers"), len(grouped), '="повний"', "повний"),
            _SummaryMetric("vouchers", "Унікальні видимі ваучери", "ваучерів", visible_unique_count_formula("tblOtherVouchers", "Ключ ваучера"), len({key[1] for key in grouped}), '="повний"', "повний"),
            _SummaryMetric("clients", "Унікальні видимі Tax ID", "клієнтів", visible_unique_count_formula("tblOtherVouchers", "Tax ID"), len({key[0] for key in grouped if key[0]}), '="повний"', "повний"),
            _SummaryMetric(
                "quantity", "Видимий обсяг", "од.", sumproduct_formula("tblOtherVouchers", "Відомий обсяг"),
                float(_known(other_quantity)),
                completeness_formula("B5", "SUMPRODUCT(tblOtherVouchers[Видимий],tblOtherVouchers[Відомі внески])", "E8"),
                _status(other_quantity, has_rows=bool(grouped)),
                sumproduct_formula("tblOtherVouchers", "Невідомі внески"), other_quantity.unknown_count,
            ),
        )
        columns = (
            _Column("Ключ клієнт–ваучер", 44, "id"), _Column("Tax ID", 18, "id"),
            _Column("Назви клієнта", 34, "wrap"), _Column("Повний ваучер", 38, "id"),
            _Column("Ключ ваучера", 31, "id"), _Column("ProductLine", 44, "wrap"),
            _Column("Відомий обсяг", 15, "number"), _Column("Відомі внески", 15, "integer"),
            _Column("Невідомі внески", 17, "integer"), _Column("Повнота", 16),
            _Column("Основні ваучери", 40, "wrap"), _Column("LeadRef-підстава", 40, "wrap"),
            _Column("Учасники-підстава", 40, "wrap"), _Column("Опорна дата Lead", 17, "date"),
            _Column("До ліда", 14, "number"), _Column("У день/після", 16, "number"),
            _Column("Час невідомий", 16, "number"), _Column("ProductLine від", 15, "date"),
            _Column("ProductLine до", 15, "date"), _Column("Культури", 34, "wrap"),
            _Column("Джерельні рядки", 19), _Column(VISIBLE_HEADER, 10, "integer", True),
        )
        eligible = {item.tax_id: item for item in self.result.eligible_clients}
        rows = []
        for (tax_id, voucher_key), facts in sorted(grouped.items()):
            products = [self.product_by_key[fact.product_line_key] for fact in facts]
            client = eligible.get(tax_id)
            known = sum((fact.quantity for fact in facts if fact.quantity is not None), Decimal(0))
            unknown = sum(fact.quantity is None for fact in facts)
            known_count = sum(fact.quantity is not None for fact in facts)
            dates = [fact.created_on for fact in facts if fact.created_on]
            time_totals = {
                bucket: sum((fact.quantity for fact in facts if fact.time_bucket is bucket and fact.quantity is not None), Decimal(0))
                for bucket in TimeBucket
            }
            crops = _join(
                f"{_CROP_LABEL[crop]}: {sum((fact.quantity for fact in facts if fact.crop is crop and fact.quantity is not None), Decimal(0))}"
                for crop in CropCategory if any(fact.crop is crop for fact in facts)
            )
            rows.append((
                f"{tax_id}|{voucher_key}", tax_id,
                _join(product.account_name for product in products),
                self.voucher_by_key[voucher_key].full_number, voucher_key,
                _join(fact.product_line_key for fact in facts), known, known_count, unknown,
                "повний" if unknown == 0 else "частковий" if known_count else "недоступний",
                _join(self.voucher_by_key[key].full_number for key in (client.main_voucher_keys if client else ())),
                _join(client.lead_ref_keys if client else ()), _join(client.participant_keys if client else ()),
                client.reference_date if client else None,
                time_totals[TimeBucket.BEFORE_LEAD], time_totals[TimeBucket.ON_OR_AFTER_LEAD],
                time_totals[TimeBucket.UNKNOWN], min(dates) if dates else None, max(dates) if dates else None,
                crops, self._source_link(tuple(source for product in products for source in product.sources)), None,
            ))
        self._title(
            sheet, "Інші ваучери клієнтів",
            "Один рядок — клієнтська частина іншого ваучера; допуск і дата опори вже визначені ядром.",
            len(columns) - 1,
        )
        cells, table_start = self._write_summary(sheet, metrics, "За фільтрами «Інші ваучери клієнтів»")
        self._write_table(
            sheet, start_row=table_start, columns=columns, rows=rows,
            table_name="tblOtherVouchers", key_header="Ключ клієнт–ваучер",
        )
        return cells

    def _write_issues(self) -> dict[str, _SummaryCells]:
        sheet = self.ws["Проблеми"]
        unresolved = sum(not issue.is_resolved for issue in self.result.issues)
        metrics = (
            _SummaryMetric("count", "Видимі проблеми", "проблем", visible_count_formula("tblIssues"), len(self.result.issues), '="повний"', "повний"),
            _SummaryMetric(
                "unresolved", "Невирішені видимі проблеми", "проблем",
                '=SUMPRODUCT(tblIssues[Видимий],--(tblIssues[Вирішено]="Ні"))',
                unresolved, '="повний"', "повний",
            ),
        )
        columns = (
            _Column("ID проблеми", 36, "id"), _Column("Код", 33), _Column("Рівень", 20),
            _Column("Повідомлення", 58, "wrap"), _Column("Ролі джерел", 18),
            _Column("Вплив", 34, "wrap"), _Column("Вирішено", 12),
            _Column("ID рішення", 36, "id"), _Column("Зачеплені ключі", 46, "wrap"),
            _Column("Кандидати", 46, "wrap"), _Column("Деталі", 48, "wrap"),
            _Column("Джерела", 58, "wrap"), _Column("Джерельний рядок", 18),
            _Column(VISIBLE_HEADER, 10, "integer", True),
        )
        rows = []
        for issue in self.result.issues:
            rows.append((
                issue.issue_id, issue.code.value, issue.level.value, issue.message_uk,
                _join(source.role.value for source in issue.sources),
                _join(impact.value for impact in issue_impacts(issue)),
                "Так" if issue.is_resolved else "Ні", issue.resolved_by_decision_id,
                _join(issue.affected_keys), _join(issue.candidate_keys),
                _join(f"{name}: {value}" for name, value in issue.details),
                _join(
                    f"{source.role.value}: {source.file_name} / {source.sheet_name} / {source.excel_row} / {source.field_name}"
                    for source in issue.sources
                ),
                self._source_link_for_cell(issue.sources[0]) if issue.sources else None, None,
            ))
        self._title(
            sheet, "Проблеми",
            "Незнайдені, спірні, некоректні й вирішені випадки. Фільтрація не змінює розраховані факти.",
            len(columns) - 1,
        )
        cells, table_start = self._write_summary(sheet, metrics, "За фільтрами «Проблеми»")
        self._write_table(
            sheet, start_row=table_start, columns=columns, rows=rows,
            table_name="tblIssues", key_header="ID проблеми",
        )
        sheet.conditional_format(
            table_start + 1, 6, table_start + max(len(rows), 1), 6,
            {"type": "text", "criteria": "containing", "value": "Ні", "format": self.formats["warning"]},
        )
        return cells

    def _write_metadata(self) -> None:
        sheet = self.ws["Метадані"]
        statuses = defaultdict(int)
        for measure in self.result.measures:
            statuses[measure.status.value] += 1
        metadata = [
            ("calculation_id", self.result.calculation_id),
            ("snapshot_id", self.result.snapshot_id),
            ("revision", self.result.revision),
            ("calculated_at", self.result.calculated_at.isoformat()),
            ("exported_at", self.exported_at.isoformat()),
            ("program_version", self.result.snapshot.program_version),
            ("input_complete", "Так" if self.result.snapshot.is_complete else "Ні"),
            ("roles", ", ".join(role.value for role in sorted((source.role for source in self.result.snapshot.sources), key=lambda role: role.value))),
            ("measures_complete", statuses["complete"]),
            ("measures_partial", statuses["partial"]),
            ("measures_unavailable", statuses["unavailable"]),
            ("unresolved_issues", sum(not issue.is_resolved for issue in self.result.issues)),
            ("excel_target", "Microsoft 365"),
            ("m365_filter_acceptance", "НЕ ПЕРЕВІРЕНО — обов’язково виконати на Windows"),
        ]
        self._title(
            sheet, "Метадані",
            "Ідентифікатори ревізії, джерела, схеми, хеші й ознаки повноти.",
            6,
        )
        columns = (_Column("Параметр", 30, "id"), _Column("Значення", 86, "wrap"))
        last = self._write_table(
            sheet, start_row=3, columns=columns, rows=metadata,
            table_name="tblMetadata", key_header="Параметр", visible=False,
        )
        source_columns = (
            _Column("Роль", 8), _Column("Назва ролі", 28), _Column("Файл", 32),
            _Column("Аркуш", 22), _Column("Версія схеми", 18), _Column("Рядків", 12, "integer"),
            _Column("SHA-256", 68, "id"),
        )
        source_rows = [
            (
                source.role.value, source.role.label_uk, source.file_name, source.sheet_name,
                source.schema_version, source.row_count, source.file_sha256,
            )
            for source in sorted(self.result.snapshot.sources, key=lambda item: item.role.value)
        ]
        self._write_table(
            sheet, start_row=last + 3, columns=source_columns, rows=source_rows,
            table_name="tblSourceMetadata", key_header="Роль", visible=False,
        )

    def _write_overview(
        self,
        lead: dict[str, _SummaryCells],
        products: dict[str, _SummaryCells],
        participants: dict[str, _SummaryCells],
        other: dict[str, _SummaryCells],
        issues: dict[str, _SummaryCells],
    ) -> None:
        sheet = self.ws["Огляд"]
        self._title(
            sheet, "SeedLink — огляд",
            "Показники повного ReportResult; джерело незалежних фільтрів указане для кожного рядка.",
            19,
        )
        for col, name in enumerate((*MAIN_SHEETS[1:], *AUDIT_SHEETS)):
            sheet.write_url(2, col, f"internal:'{name}'!A1", self.formats["link"], name)
        for col, header in enumerate(("Показник", "Значення", "Одиниця", "Повнота", "Невідомі внески", "Джерело фільтрів")):
            sheet.write(4, col, header, self.formats["metric_header"])

        funnel_labels = (
            ("Учасники", "participants"), ("Є активність", "activity"),
            ("Результат зафіксовано", "result"), ("Є підтверджений ваучер", "voucher"),
        )
        overview_rows = []
        for label, key in funnel_labels:
            measure = _measure(self.result.measures, f"funnel.{key}.count")
            overview_rows.append((label, participants[key], "Учасники та відповіді", "осіб", float(_known(measure)), _status(measure), measure.unknown_count))
        main_quantity = _measure(self.result.measures, "main.quantity")
        overview_rows.extend((
            ("Унікальні основні ваучери", lead["vouchers"], "Ліди–ваучери", "ваучерів", len(self.result.voucher_summaries), "повний", 0),
            ("Клієнти за Tax ID", lead["clients"], "Ліди–ваучери", "клієнтів", len(self.result.clients), "повний", 0),
            ("Обсяг основних ваучерів", lead["quantity"], "Ліди–ваучери", "од.", float(_known(main_quantity)), _status(main_quantity), main_quantity.unknown_count),
            ("Видимі ProductLine", products["rows"], "Товарні рядки", "рядків", len(self.result.product_facts), "повний", 0),
            ("Обсяг товарної деталізації", products["quantity"], "Товарні рядки", "од.", float(_known(main_quantity)), _status(main_quantity), main_quantity.unknown_count),
            ("Інші ваучери", other["vouchers"], "Інші ваучери клієнтів", "ваучерів", len({item.voucher_key for item in self.result.other_product_facts}), "повний", 0),
            ("Невирішені проблеми", issues["unresolved"], "Проблеми", "проблем", sum(not item.is_resolved for item in self.result.issues), "повний", 0),
        ))
        for offset, (label, cells, source_sheet, unit, cached, status, unknown) in enumerate(overview_rows, start=5):
            sheet.write(offset, 0, label, self.formats["metric_label"])
            sheet.write_formula(offset, 1, f"={_sheet_ref(source_sheet, cells.value)}", self.formats["metric_value"], cached)
            sheet.write(offset, 2, unit, self.formats["text"])
            sheet.write_formula(offset, 3, f"={_sheet_ref(source_sheet, cells.status)}", self.formats["status"], status)
            sheet.write_formula(offset, 4, f"={_sheet_ref(source_sheet, cells.unknown)}", self.formats["integer"], unknown)
            sheet.write(offset, 5, f"За фільтрами «{source_sheet}»", self.formats["note"])

        static_start = 5 + len(overview_rows) + 1
        sheet.write(static_start, 0, "Показники всього комплекту", self.formats["section"])
        static = (
            ("Унікальні опитування", "events.surveys"),
            ("Унікальні активності", "events.activities"),
            ("Покриття LeadRef→R3", "quality.lead_refs.linked_rate"),
            ("Покриття Activity→R3", "quality.activities.linked_rate"),
        )
        for row, (label, key) in enumerate(static, start=static_start + 1):
            measure = _measure(self.result.measures, key)
            sheet.write(row, 0, label, self.formats["metric_label"])
            self._write_value(sheet, row, 1, _known(measure), "number")
            sheet.write(row, 2, measure.unit)
            sheet.write(row, 3, _status(measure), self.formats["status"])
            sheet.write_number(row, 4, measure.unknown_count, self.formats["integer"])
            sheet.write(row, 5, "Весь комплект; не залежить від Excel-фільтрів", self.formats["note"])

        chart_row = 34
        sheet.write(chart_row, 0, "Дані графіків (ті самі видимі факти)", self.formats["section"])
        chart_specs = []
        funnel_data = [
            (label, f"={_sheet_ref('Учасники та відповіді', participants[key].value)}", float(_known(_measure(self.result.measures, f"funnel.{key}.count"))))
            for label, key in funnel_labels
        ]
        chart_specs.append(("Охоплення учасників, осіб", 0, funnel_data))
        crop_data = [
            (
                _CROP_LABEL[crop],
                filtered_sum_formula("tblMainProducts", "Кількість", "Культура", _CROP_LABEL[crop]),
                float(_known(_measure(self.result.measures, f"main.crop.{crop.value}"))),
            )
            for crop in CropCategory
        ]
        chart_specs.append(("Обсяг за культурами, од.", 4, crop_data))
        time_data = [
            (
                _TIME_LABEL[bucket],
                filtered_sum_formula("tblMainProducts", "Кількість", "Часова складова", _TIME_LABEL[bucket]),
                float(_known(_measure(self.result.measures, f"main.time.{bucket.value}"))),
            )
            for bucket in TimeBucket
        ]
        chart_specs.append(("Часовий розподіл, од.", 8, time_data))
        by_month: dict[str, Decimal] = defaultdict(Decimal)
        for fact in self.result.product_facts:
            month = fact.created_on.strftime("%Y-%m") if fact.created_on else "Дата невідома"
            if fact.quantity is not None:
                by_month[month] += fact.quantity
        month_data = [
            (month, filtered_sum_formula("tblMainProducts", "Кількість", "Місяць ProductLine", month), float(value))
            for month, value in sorted(by_month.items())
        ] or [("Немає дат", "=0", 0.0)]
        chart_specs.append(("Обсяг за місяцями ProductLine, од.", 12, month_data))
        for title, base_col, data in chart_specs:
            sheet.write(chart_row + 1, base_col, "Категорія", self.formats["metric_header"])
            sheet.write(chart_row + 1, base_col + 1, "Значення", self.formats["metric_header"])
            for row, (label, formula, cached) in enumerate(data, start=chart_row + 2):
                sheet.write(row, base_col, label)
                sheet.write_formula(row, base_col + 1, formula, self.formats["number"], cached)
            chart = self.workbook.add_chart({"type": "column"})
            chart.add_series({
                "name": title,
                "categories": ["Огляд", chart_row + 2, base_col, chart_row + 1 + len(data), base_col],
                "values": ["Огляд", chart_row + 2, base_col + 1, chart_row + 1 + len(data), base_col + 1],
                "data_labels": {"value": True},
                "fill": {"color": "#5B9BD5"},
            })
            chart.set_title({"name": title})
            chart.set_y_axis({"name": "Кількість" if "осіб" in title else "Одиниці", "major_gridlines": {"visible": False}})
            chart.set_legend({"none": True})
            chart.set_style(10)
            sheet.insert_chart({0: "H4", 4: "N4", 8: "H20", 12: "N20"}[base_col], chart, {"x_scale": 1.05, "y_scale": 0.9})

        breakdown = chart_row + max(len(item[2]) for item in chart_specs) + 4
        sheet.write(breakdown, 0, "Додаткові розрізи з тих самих фактів", self.formats["section"])
        sheet.write(breakdown + 1, 0, "Метод прийняття", self.formats["metric_header"])
        sheet.write(breakdown + 1, 1, "Пар", self.formats["metric_header"])
        for row, method in enumerate(VoucherMatchMethod, start=breakdown + 2):
            label = _VOUCHER_METHOD_LABEL[method]
            sheet.write(row, 0, label)
            sheet.write_formula(
                row, 1,
                f'=SUMPRODUCT(tblLeadVoucher[Видимий],--(tblLeadVoucher[Метод прийняття]="{label}"))',
                self.formats["integer"], sum(link.method is method for link in self.result.accepted_links),
            )
        sheet.write(breakdown + 1, 3, "Події", self.formats["metric_header"])
        sheet.write(breakdown + 1, 4, "Кількість", self.formats["metric_header"])
        for row, (label, key) in enumerate((("Опитування", "events.surveys"), ("Активності", "events.activities")), start=breakdown + 2):
            sheet.write(row, 3, label)
            self._write_value(sheet, row, 4, _known(_measure(self.result.measures, key)), "integer")
        client_totals: dict[str, Decimal] = defaultdict(Decimal)
        for fact in self.result.product_facts:
            if fact.tax_id and fact.quantity is not None:
                client_totals[fact.tax_id] += fact.quantity
        sheet.write(breakdown + 1, 6, "Tax ID", self.formats["metric_header"])
        sheet.write(breakdown + 1, 7, "Відомий обсяг", self.formats["metric_header"])
        for row, (tax_id, value) in enumerate(sorted(client_totals.items(), key=lambda item: (-item[1], item[0]))[:10], start=breakdown + 2):
            sheet.write_string(row, 6, tax_id, self.formats["id"])
            self._write_value(sheet, row, 7, value, "number")
        sheet.set_column(0, 0, 31)
        sheet.set_column(1, 1, 15)
        sheet.set_column(2, 2, 11)
        sheet.set_column(3, 4, 17)
        sheet.set_column(5, 5, 35)
        sheet.set_column(6, 19, 15)
        sheet.freeze_panes(5, 0)
        sheet.set_zoom(85)


def write_excel_report(
    result: ReportResult,
    output: str | Path,
    *,
    exported_at: datetime | None = None,
) -> Path:
    """Write a new macro-free XLSX workbook and return its path."""

    path = Path(output)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Excel report path must end with .xlsx")
    timestamp = exported_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("exported_at must be timezone-aware")
    _ExcelWriter(result, path, timestamp).write()
    return path

