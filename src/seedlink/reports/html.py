"""Offline, contact-safe HTML projection of a complete ``ReportResult``.

The projection is deliberately assembled field by field. In particular it
never serializes lead phone/e-mail fields, participant e-mail fields, raw
survey answers, activity notes, source cell values, or free-form issue details.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files
import json
from pathlib import Path
from typing import Any, Iterable

from jinja2 import Environment, StrictUndefined, select_autoescape
from markupsafe import Markup

from seedlink.domain.issues import Issue
from seedlink.domain.measures import Measure
from seedlink.domain.models import (
    CropCategory,
    ProductLineFact,
    ReportResult,
    TimeBucket,
    VoucherMatchMethod,
)
from seedlink.domain.queries import (
    issue_impacts,
    link_related_keys,
    query_funnel,
    query_issues,
    query_lead_vouchers,
    query_other_vouchers,
    query_product_lines,
)


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
_METHOD_LABEL = {
    VoucherMatchMethod.EXACT_FULL: "Точний повний номер",
    VoucherMatchMethod.SHORT_BLOCK: "Короткий номер",
    VoucherMatchMethod.DISTRIBUTOR_VARIANT: "Варіант дистриб’ютора",
    VoucherMatchMethod.MANUAL: "Ручне рішення",
}


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _date_text(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _measure(measure: Measure) -> dict[str, Any]:
    return {
        "key": measure.key,
        "label": measure.label_uk,
        "unit": measure.unit,
        "known": _decimal_text(measure.known_value),
        "status": measure.status.value,
        "unknown": measure.unknown_count,
    }


def _measure_by_suffix(measures: Iterable[Measure], suffix: str) -> Measure:
    return next(item for item in measures if item.key.endswith(suffix))


def _person_name(first_name: str | None, last_name: str | None) -> str:
    return " ".join(part for part in (first_name, last_name) if part) or "Не вказано"


def _issue_locations(issue: Issue) -> list[dict[str, Any]]:
    """Return coordinates only; original cell values are intentionally absent."""

    seen: set[tuple[str, str, int, str]] = set()
    locations: list[dict[str, Any]] = []
    for source in issue.sources:
        identity = (
            source.role.value,
            source.sheet_name,
            source.excel_row,
            source.field_name,
        )
        if identity in seen:
            continue
        seen.add(identity)
        locations.append(
            {
                "role": source.role.value,
                "sheet": source.sheet_name,
                "row": source.excel_row,
                "field": source.field_name,
            }
        )
    return locations


def _quantity_control(measures: Iterable[Measure]) -> dict[str, Any]:
    quantity = _measure_by_suffix(measures, ".quantity")
    return {
        "known": _decimal_text(quantity.known_value),
        "unknown": quantity.unknown_count,
        "status": quantity.status.value,
    }


def _facts_projection(
    facts: Iterable[ProductLineFact], *, result: ReportResult
) -> list[dict[str, Any]]:
    product_by_key = {item.key: item for item in result.product_lines}
    voucher_by_key = {item.key: item for item in result.vouchers}
    rows: list[dict[str, Any]] = []
    for fact in facts:
        product = product_by_key[fact.product_line_key]
        rows.append(
            {
                "voucherKey": fact.voucher_key,
                "voucher": voucher_by_key[fact.voucher_key].full_number,
                "quantity": _decimal_text(fact.quantity),
                "crop": fact.crop.value,
                "cropLabel": _CROP_LABEL[fact.crop],
                "hybrid": fact.hybrid,
                "taxId": fact.tax_id,
                "client": product.account_name,
                "createdOn": _date_text(fact.created_on),
                "referenceDate": _date_text(fact.reference_date),
                "timeBucket": fact.time_bucket.value,
                "timeLabel": _TIME_LABEL[fact.time_bucket],
            }
        )
    return rows


def build_html_projection(
    result: ReportResult, *, exported_at: datetime | None = None
) -> dict[str, Any]:
    """Build the explicit, contact-safe payload permitted in the HTML report."""

    timestamp = exported_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("exported_at must be timezone-aware")

    participant_by_key = {item.key: item for item in result.participants}
    voucher_by_key = {item.key: item for item in result.vouchers}
    accepted_by_key = {item.key: item for item in result.accepted_links}
    affected = {
        key
        for issue in result.issues
        if not issue.is_resolved
        for key in issue.affected_keys
    }

    participant_rows: list[dict[str, Any]] = []
    for row in result.funnel_rows:
        participant = participant_by_key[row.participant_key]
        participant_rows.append(
            {
                "name": _person_name(participant.first_name, participant.last_name),
                "memberType": row.member_type,
                "memberStatus": row.member_status,
                "appearedOn": _date_text(row.appeared_on),
                "hasActivity": row.has_activity,
                "hasResult": row.has_recorded_result,
                "hasVoucher": row.has_confirmed_voucher,
                "vouchers": [voucher_by_key[key].full_number for key in row.voucher_keys],
            }
        )

    links_by_voucher: dict[str, list[Any]] = defaultdict(list)
    for summary in result.link_summaries:
        links_by_voucher[summary.voucher_key].append(summary)
    voucher_rows: list[dict[str, Any]] = []
    for summary in result.voucher_summaries:
        links = links_by_voucher[summary.voucher_key]
        methods = tuple(
            dict.fromkeys(
                _METHOD_LABEL[accepted_by_key[item.link_key].method] for item in links
            )
        )
        people = tuple(
            dict.fromkeys(
                _person_name(
                    participant_by_key[item.participant_key].first_name,
                    participant_by_key[item.participant_key].last_name,
                )
                for item in links
                if item.participant_key is not None
            )
        )
        related_keys = set().union(
            *(
                link_related_keys(accepted_by_key[item.link_key], item)
                for item in links
            )
        )
        voucher_rows.append(
            {
                "voucherKey": summary.voucher_key,
                "voucher": voucher_by_key[summary.voucher_key].full_number,
                "people": list(people),
                "taxIds": list(dict.fromkeys(tax for item in links for tax in item.tax_ids)),
                "clients": list(dict.fromkeys(name for item in links for name in item.client_names)),
                "methods": list(methods),
                "referenceDate": _date_text(summary.reference_date),
                "quantity": _decimal_text(summary.quantity.known_value),
                "quantityStatus": summary.quantity.status.value,
                "unknown": summary.quantity.unknown_count,
                "hasIssues": bool(related_keys.intersection(affected)),
                "crops": {
                    crop.value: _decimal_text(
                        _measure_by_suffix(summary.crop_measures, f".{crop.value}").known_value
                    )
                    for crop in CropCategory
                },
            }
        )

    main_products = _facts_projection(result.product_facts, result=result)
    other_products = _facts_projection(result.other_product_facts, result=result)
    issue_rows = [
        {
            "code": issue.code.value,
            "level": issue.level.value,
            "message": issue.message_uk,
            "resolved": issue.is_resolved,
            "impacts": [item.value for item in issue_impacts(issue)],
            "locations": _issue_locations(issue),
        }
        for issue in result.issues
    ]

    materialized_unknown: dict[str, int] = defaultdict(int)
    for fact in result.product_facts:
        materialized_unknown[fact.voucher_key] += int(fact.quantity is None)
    extra_unknown_by_voucher = {
        summary.voucher_key: max(
            summary.quantity.unknown_count - materialized_unknown[summary.voucher_key], 0
        )
        for summary in result.voucher_summaries
        if summary.quantity.unknown_count > materialized_unknown[summary.voucher_key]
    }

    funnel_query = query_funnel(result)
    links_query = query_lead_vouchers(result)
    products_query = query_product_lines(result)
    other_query = query_other_vouchers(result)
    issues_query = query_issues(result)

    return {
        "meta": {
            "calculationId": result.calculation_id,
            "snapshotId": result.snapshot_id,
            "revision": result.revision,
            "calculatedAt": result.calculated_at.isoformat(),
            "exportedAt": timestamp.isoformat(),
            "programVersion": result.snapshot.program_version,
            "inputComplete": result.snapshot.is_complete,
            "sources": [
                {
                    "role": source.role.value,
                    "label": source.role.label_uk,
                    "file": source.file_name,
                    "sheet": source.sheet_name,
                    "schema": source.schema_version,
                    "rows": source.row_count,
                    "sha256": source.file_sha256,
                }
                for source in sorted(result.snapshot.sources, key=lambda item: item.role.value)
            ],
        },
        "overview": {
            "measures": [_measure(item) for item in result.measures],
            "unresolvedIssues": sum(not item.is_resolved for item in result.issues),
        },
        "participants": participant_rows,
        "vouchers": voucher_rows,
        "products": main_products,
        "otherProducts": other_products,
        "issues": issue_rows,
        "productExtraUnknownByVoucher": extra_unknown_by_voucher,
        "controls": {
            "participants": {
                item.key.rsplit(".", 2)[-2]: _measure(item)
                for item in funnel_query.measures
                if item.key.endswith(".count")
            },
            "vouchers": {
                "vouchers": int(
                    _measure_by_suffix(links_query.measures, ".vouchers").known_value
                    or 0
                ),
                "clients": int(_measure_by_suffix(links_query.measures, ".clients").known_value or 0),
                "quantity": _quantity_control(links_query.measures),
            },
            "products": {
                "rows": len(products_query.rows),
                "vouchers": int(
                    _measure_by_suffix(products_query.measures, ".vouchers").known_value
                    or 0
                ),
                "clients": int(
                    _measure_by_suffix(products_query.measures, ".clients").known_value
                    or 0
                ),
                "quantity": _quantity_control(products_query.measures),
            },
            "otherProducts": {
                "rows": len(other_query.rows),
                "vouchers": int(
                    _measure_by_suffix(other_query.measures, ".vouchers").known_value
                    or 0
                ),
                "clients": int(
                    _measure_by_suffix(other_query.measures, ".clients").known_value
                    or 0
                ),
                "quantity": _quantity_control(other_query.measures),
            },
            "issues": {
                "rows": int(
                    _measure_by_suffix(issues_query.measures, ".count").known_value
                    or 0
                ),
                "unresolved": int(
                    _measure_by_suffix(issues_query.measures, ".unresolved").known_value
                    or 0
                ),
            },
        },
    }


def _safe_json(payload: dict[str, Any]) -> Markup:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    serialized = (
        serialized.replace("&", r"\u0026")
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("\u2028", r"\u2028")
        .replace("\u2029", r"\u2029")
    )
    return Markup(serialized)


def write_html_report(
    result: ReportResult,
    output: str | Path,
    *,
    exported_at: datetime | None = None,
) -> Path:
    """Write one self-contained, offline ``.html`` file and return its path."""

    path = Path(output)
    if path.suffix.lower() not in {".html", ".htm"}:
        raise ValueError("HTML report path must end with .html or .htm")
    timestamp = exported_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("exported_at must be timezone-aware")

    resource_root = files("seedlink.resources")
    environment = Environment(
        autoescape=select_autoescape(("html", "xml"), default=True),
        undefined=StrictUndefined,
    )
    template = environment.from_string(
        resource_root.joinpath("report.html.j2").read_text(encoding="utf-8")
    )
    rendered = template.render(
        css=Markup(resource_root.joinpath("report.css").read_text(encoding="utf-8")),
        javascript=Markup(resource_root.joinpath("report.js").read_text(encoding="utf-8")),
        payload_json=_safe_json(build_html_projection(result, exported_at=timestamp)),
    )
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return path
