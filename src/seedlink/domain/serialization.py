"""Canonical, JSON-safe control projection shared by later exporters."""

from __future__ import annotations

import json

from seedlink.domain.measures import Measure
from seedlink.domain.models import ProductLineFact, ReportResult
from seedlink.domain.normalization import decimal_to_text


def _measure(measure: Measure) -> dict[str, object]:
    return {
        "key": measure.key,
        "label_uk": measure.label_uk,
        "unit": measure.unit,
        "known_value": (
            decimal_to_text(measure.known_value)
            if measure.known_value is not None
            else None
        ),
        "status": measure.status.value,
        "unknown_count": measure.unknown_count,
        "reasons": list(measure.reasons),
    }


def _product_fact(
    fact: ProductLineFact,
    *,
    product_id: str | None,
    voucher_number: str,
) -> dict[str, object]:
    return {
        "product_id": product_id,
        "voucher_number": voucher_number,
        "quantity": (
            decimal_to_text(fact.quantity) if fact.quantity is not None else None
        ),
        "crop": fact.crop.value,
        "hybrid": fact.hybrid,
        "local_description": fact.local_description,
        "tax_id": fact.tax_id,
        "created_on": fact.created_on.isoformat() if fact.created_on else None,
        "reference_date": (
            fact.reference_date.isoformat() if fact.reference_date else None
        ),
        "time_bucket": fact.time_bucket.value,
        "section": fact.section.value,
    }


def serialize_report_result(result: ReportResult) -> dict[str, object]:
    """Return stable field names and decimal strings, not Python internals."""

    product_by_key = {item.key: item for item in result.product_lines}
    voucher_by_key = {item.key: item for item in result.vouchers}

    def project_fact(fact: ProductLineFact) -> dict[str, object]:
        product = product_by_key[fact.product_line_key]
        voucher = voucher_by_key[fact.voucher_key]
        return _product_fact(
            fact,
            product_id=product.product_id,
            voucher_number=voucher.full_number,
        )

    return {
        "metadata": {
            "calculation_id": result.calculation_id,
            "snapshot_id": result.snapshot_id,
            "revision": result.revision,
            "calculated_at": result.calculated_at.isoformat(),
            "program_version": result.snapshot.program_version,
            "sources": [
                {
                    "role": source.role.value,
                    "file_name": source.file_name,
                    "file_sha256": source.file_sha256,
                    "schema_version": source.schema_version,
                    "row_count": source.row_count,
                }
                for source in result.snapshot.sources
            ],
        },
        "measures": [
            _measure(item) for item in sorted(result.measures, key=lambda item: item.key)
        ],
        "main_product_lines": sorted(
            (project_fact(item) for item in result.product_facts),
            key=lambda item: (
                str(item["voucher_number"]),
                str(item["product_id"]),
                str(item["tax_id"]),
            ),
        ),
        "other_product_lines": sorted(
            (project_fact(item) for item in result.other_product_facts),
            key=lambda item: (
                str(item["voucher_number"]),
                str(item["product_id"]),
                str(item["tax_id"]),
            ),
        ),
        "hybrids": sorted(
            (
                {
                    "crop": summary.crop.value,
                    "hybrid": summary.hybrid,
                    "product_ids": sorted(
                        str(product_by_key[key].product_id)
                        for key in summary.product_line_keys
                    ),
                    "quantity": _measure(summary.quantity),
                }
                for summary in result.hybrid_summaries
            ),
            key=lambda item: (str(item["crop"]), str(item["hybrid"])),
        ),
        "funnel": {
            "participants": len(result.funnel_rows),
            "with_activity": sum(item.has_activity for item in result.funnel_rows),
            "with_result": sum(item.has_recorded_result for item in result.funnel_rows),
            "with_voucher": sum(item.has_confirmed_voucher for item in result.funnel_rows),
        },
        "issues": [
            {
                "issue_id": issue.issue_id,
                "code": issue.code.value,
                "level": issue.level.value,
                "message_uk": issue.message_uk,
                "affected_keys": list(issue.affected_keys),
                "resolved": issue.is_resolved,
            }
            for issue in sorted(result.issues, key=lambda item: item.issue_id)
        ],
    }


def business_control_payload(result: ReportResult) -> dict[str, object]:
    """Canonical facts for order-invariance tests, excluding snapshot-bound IDs."""

    payload = serialize_report_result(result)
    return {
        "measures": payload["measures"],
        "main_product_lines": payload["main_product_lines"],
        "other_product_lines": payload["other_product_lines"],
        "hybrids": payload["hybrids"],
        "funnel": payload["funnel"],
        "issue_codes": sorted(issue.code.value for issue in result.issues),
    }


def report_result_json(result: ReportResult, *, indent: int | None = None) -> str:
    return json.dumps(
        serialize_report_result(result),
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
    )
