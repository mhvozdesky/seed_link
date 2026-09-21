from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from seedlink.application import analyze_links, build_report_result
from seedlink.domain import (
    CropCategory,
    DateFilter,
    DateFilterMode,
    FunnelFilter,
    InputRole,
    LeadDateSource,
    LeadVoucherFilter,
    Measure,
    MeasureStatus,
    OtherVoucherFilter,
    ProductFilter,
    TimeBucket,
    ReportInvariantError,
    business_control_payload,
    query_funnel,
    query_lead_vouchers,
    query_other_vouchers,
    query_product_lines,
    serialize_report_result,
    validate_report_result,
)
from seedlink.domain.issues import IssueCode
from seedlink.input_xlsx import import_workbooks


MEMBER_A = "00vAbCdEfGhIjKlIVK"
MEMBER_B = "00vAbCdEfGhIjKmIVK"
MEMBER_C = "00vAbCdEfGhIjKnIVK"


def _report(workbook_set_factory, rows):
    imported = import_workbooks(workbook_set_factory(rows))
    assert imported.is_accepted
    return build_report_result(analyze_links(imported))


def _r1(
    number: str,
    survey: str,
    member: str,
    voucher: str | None,
    *,
    time_taken: str | None = None,
    answer: str | None = None,
):
    return {
        "Record Nr": number,
        "Survey Response ID": survey,
        "Campaigm Member": member,
        "Time Taken": time_taken,
        "Answer": answer,
        "Answer (Long Text)": voucher,
    }


def _r2(
    product: str,
    voucher: str,
    quantity,
    *,
    tax_id: str | None = "00000001",
    species: str | None = "SUN: Sunflowers",
    hybrid: str | None = "Гібрид",
    created: date | None = None,
):
    return {
        "Opportunity Product Id 18": product,
        "Voucher Number": voucher,
        "Current Year Planned Quantity": quantity,
        "Tax ID 1": tax_id,
        "Account Name": f"Клієнт {tax_id}" if tax_id else "Без коду",
        "Species group": species,
        "Parent Product Local Description": hybrid,
        "Opportunity Product: Created Date": created,
    }


def _r3(
    member: str,
    first: str,
    last: str,
    *,
    member_type: str = "Lead",
    associated: date | None = None,
):
    return {
        "Campaign Member Id 18": member,
        "First Name": first,
        "Last Name": last,
        "Member Type": member_type,
        "Member First Associated Date": associated,
    }


def _measure(result, key):
    return next(item for item in result.measures if item.key == key)


def test_quantities_clients_crops_and_unknowns_remain_explicit(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", MEMBER_A, "SE-100")],
            InputRole.R2: [
                _r2("P1", "SE-100", "0.1", tax_id="00000001"),
                _r2(
                    "P2",
                    "SE-100",
                    "0.2",
                    tax_id="00000002",
                    species="CRN: Corn Seeds",
                ),
                _r2(
                    "P3",
                    "SE-100",
                    None,
                    tax_id=None,
                    species=None,
                    hybrid=None,
                ),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
        },
    )

    total = _measure(result, "main.quantity")
    assert total.known_value == Decimal("0.3")
    assert total.status is MeasureStatus.PARTIAL
    assert total.unknown_count == 1
    assert _measure(result, "main.crop.sunflower").known_value == Decimal("0.1")
    assert _measure(result, "main.crop.corn").known_value == Decimal("0.2")
    assert _measure(result, "main.crop.unknown").unknown_count == 1
    assert _measure(result, "main.hybrid.known_lines").known_value == 2
    assert _measure(result, "main.hybrid.unknown_lines").known_value == 1
    assert _measure(result, "main.hybrid.unknown.quantity").unknown_count == 1
    assert len(result.hybrid_summaries) == 3
    assert {client.tax_id for client in result.clients} == {"00000001", "00000002"}
    assert {issue.code for issue in result.issues}.issuperset(
        {IssueCode.QUANTITY_UNKNOWN, IssueCode.CLIENT_UNKNOWN, IssueCode.MULTIPLE_TAX_IDS}
    )

    links = {item.key: item for item in query_lead_vouchers(result).measures}
    assert links["query.links.vouchers"].known_value == 1
    assert links["query.links.clients"].known_value == 2

    sunflower = query_product_lines(
        result, ProductFilter(crops=(CropCategory.SUNFLOWER,))
    )
    assert sunflower.measures[-1].known_value == Decimal("0.1")
    assert sunflower.measures[-1].status is MeasureStatus.COMPLETE


def test_personal_and_global_date_attribution_for_shared_voucher(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", MEMBER_A, "SE-200", time_taken="2026-09-01T12:00:00+03:00"),
                _r1("R2", "S2", MEMBER_B, "SE-200", time_taken="2026-09-15T12:00:00+03:00"),
            ],
            InputRole.R2: [
                _r2("P1", "SE-200", 100, created=date(2026, 9, 10))
            ],
            InputRole.R3: [
                _r3(MEMBER_A, "Лід", "Ранній", associated=date(2026, 9, 1)),
                _r3(MEMBER_B, "Лід", "Пізній", associated=date(2026, 9, 15)),
            ],
        },
    )

    voucher = result.voucher_summaries[0]
    assert voucher.reference_date == date(2026, 9, 1)
    assert result.product_facts[0].time_bucket is TimeBucket.ON_OR_AFTER_LEAD
    personal = {
        (item.lead_ref_key, item.time_bucket) for item in result.link_product_times
    }
    dates = {item.lead_ref_key: item.value for item in result.lead_dates}
    early = next(key for key, value in dates.items() if value == date(2026, 9, 1))
    late = next(key for key, value in dates.items() if value == date(2026, 9, 15))
    assert (early, TimeBucket.ON_OR_AFTER_LEAD) in personal
    assert (late, TimeBucket.BEFORE_LEAD) in personal

    both = query_lead_vouchers(result)
    one = query_lead_vouchers(
        result, LeadVoucherFilter(lead_ref_keys=(early,))
    )
    assert both.measures[-1].known_value == Decimal(100)
    assert one.measures[-1].known_value == Decimal(100)


def test_time_taken_fallback_conflict_and_kyiv_calendar_day(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", MEMBER_A, None, time_taken="123"),
                _r1("R2", "S2", MEMBER_B, None, time_taken="2026-08-31T21:30:00Z"),
                _r1("R3", "S3", MEMBER_C, None, time_taken="2026-09-02"),
                _r1("R4", "S4", MEMBER_C, None, time_taken="2026-09-03"),
            ],
            InputRole.R3: [
                _r3(MEMBER_A, "Fallback", "Дата", associated=date(2026, 9, 5)),
                _r3(MEMBER_B, "UTC", "Дата", associated=date(2026, 9, 1)),
                _r3(MEMBER_C, "Конфлікт", "Дата", associated=date(2026, 9, 4)),
            ],
        },
    )

    by_source = {item.source: item for item in result.lead_dates}
    assert by_source[LeadDateSource.PARTICIPANT_FALLBACK].value == date(2026, 9, 5)
    utc_item = next(
        item
        for item in result.lead_dates
        if item.source is LeadDateSource.TIME_TAKEN and item.value == date(2026, 9, 1)
    )
    assert not utc_item.differs_from_participant_date
    conflict = next(
        item for item in result.lead_dates if item.source is LeadDateSource.CONFLICT
    )
    assert conflict.value is None
    assert {issue.code for issue in result.issues}.issuperset(
        {IssueCode.DATE_INVALID, IssueCode.DATE_CONFLICT}
    )


def test_funnel_stages_use_real_sources_and_filters_are_independent(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", MEMBER_A, "SE-300", answer="Відповідь"),
                _r1("R2", "S2", MEMBER_B, None, answer="Зафіксовано"),
            ],
            InputRole.R2: [_r2("P1", "SE-300", 5, created=date(2026, 8, 1))],
            InputRole.R3: [
                _r3(MEMBER_A, "А", "Лід", associated=date(2026, 9, 1)),
                _r3(MEMBER_B, "Б", "Лід", associated=date(2026, 10, 1)),
                _r3(MEMBER_C, "В", "Контакт", member_type="Contact"),
            ],
            InputRole.R4: [
                {"Activity ID": "A1", "Name": "В Контакт", "Results": "Недодзвон"}
            ],
        },
    )

    assert _measure(result, "funnel.participants.count").known_value == 3
    assert _measure(result, "funnel.activity.count").known_value == 1
    assert _measure(result, "funnel.result.count").known_value == 3
    assert _measure(result, "funnel.voucher.count").known_value == 1
    september = query_funnel(
        result,
        FunnelFilter(
            appeared=DateFilter(
                DateFilterMode.RANGE,
                date(2026, 9, 1),
                date(2026, 9, 30),
            )
        ),
    )
    assert len(september.rows) == 1
    # A participant filter does not narrow the independent ProductLine area.
    assert len(query_product_lines(result).rows) == 1


def test_numeric_answer_counts_as_a_recorded_funnel_result(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", MEMBER_A, None, answer=42)],
            InputRole.R3: [_r3(MEMBER_A, "Числова", "Відповідь")],
        },
    )

    assert _measure(result, "funnel.result.count").known_value == 1


def test_other_vouchers_require_lead_and_never_overlap_main(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", MEMBER_A, "SE-400", time_taken="2026-09-05"),
                _r1("R2", "S2", MEMBER_B, "SE-400", time_taken="2026-09-01"),
            ],
            InputRole.R2: [
                _r2("P1", "SE-400", 10, tax_id="00000001", created=date(2026, 9, 2)),
                _r2("P2", "SE-401", 4, tax_id="00000001", created=date(2026, 8, 31)),
                _r2("P3", "SE-402", 6, tax_id="00000001", created=date(2026, 9, 1)),
                _r2("P4", "SE-402", 99, tax_id="99999999", created=date(2026, 9, 1)),
            ],
            InputRole.R3: [
                _r3(MEMBER_A, "Особа", "Contact", member_type="Contact"),
                _r3(MEMBER_B, "Особа", "Lead", member_type="Lead"),
            ],
        },
    )

    assert len(result.eligible_clients) == 1
    assert result.eligible_clients[0].reference_date == date(2026, 9, 1)
    assert {fact.voucher_key for fact in result.product_facts}.isdisjoint(
        {fact.voucher_key for fact in result.other_product_facts}
    )
    assert sum(
        (fact.quantity for fact in result.other_product_facts if fact.quantity is not None),
        Decimal(0),
    ) == Decimal(10)
    assert {fact.tax_id for fact in result.other_product_facts} == {"00000001"}
    assert {fact.time_bucket for fact in result.other_product_facts} == {
        TimeBucket.BEFORE_LEAD,
        TimeBucket.ON_OR_AFTER_LEAD,
    }
    only_later = query_other_vouchers(
        result,
        OtherVoucherFilter(
            created=DateFilter(
                DateFilterMode.RANGE,
                date(2026, 9, 1),
                date(2026, 9, 30),
            )
        ),
    )
    assert only_later.measures[-1].known_value == Decimal(6)


def test_control_projection_is_json_safe_and_business_facts_are_canonical(
    workbook_set_factory,
):
    rows = {
        InputRole.R1: [_r1("R1", "S1", MEMBER_A, "SE-500")],
        InputRole.R2: [_r2("P1", "SE-500", "1.20", tax_id="00000001")],
        InputRole.R3: [_r3(MEMBER_A, "Лід", "Один")],
    }
    result = _report(workbook_set_factory, rows)

    payload = serialize_report_result(result)
    control = business_control_payload(result)

    assert payload["main_product_lines"][0]["quantity"] == "1.2"
    assert control["funnel"]["with_voucher"] == 1
    assert control["main_product_lines"] == payload["main_product_lines"]


def test_conflicting_product_id_keeps_visible_link_voucher_and_unknown_volume(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", MEMBER_A, "SE-610")],
            InputRole.R2: [
                _r2("P1", "SE-610", 10),
                _r2("P1", "SE-610", 20),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Конфлікт")],
        },
    )

    selected = query_lead_vouchers(result)
    selected_measures = {item.key: item for item in selected.measures}

    assert len(selected.rows) == 1
    assert _measure(result, "main.vouchers").known_value == 1
    assert selected_measures["query.links.vouchers"].known_value == 1
    assert selected_measures["query.links.quantity"].status is MeasureStatus.UNAVAILABLE
    assert selected_measures["query.links.quantity"].unknown_count == 1
    assert IssueCode.PRODUCT_ID_CONFLICT in {issue.code for issue in result.issues}


def test_missing_date_puts_all_known_volume_in_unknown_time_bucket(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", MEMBER_A, "SE-620")],
            InputRole.R2: [
                _r2("P1", "SE-620", 12, created=date(2026, 9, 10))
            ],
            InputRole.R3: [_r3(MEMBER_A, "Лід", "Без дати")],
        },
    )

    assert _measure(result, "main.time.before_lead").known_value == 0
    assert _measure(result, "main.time.on_or_after_lead").known_value == 0
    assert _measure(result, "main.time.unknown").known_value == 12
    assert result.product_facts[0].time_bucket is TimeBucket.UNKNOWN


def test_main_voucher_from_another_lead_never_becomes_other_after_filtering(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", MEMBER_A, "SE-630"),
                _r1("R2", "S2", MEMBER_B, "SE-631"),
            ],
            InputRole.R2: [
                _r2("P1", "SE-630", 10, tax_id="00000001"),
                _r2("P2", "SE-631", 20, tax_id="00000001"),
                _r2("P3", "SE-632", 30, tax_id="00000001"),
            ],
            InputRole.R3: [
                _r3(MEMBER_A, "Перший", "Лід"),
                _r3(MEMBER_B, "Другий", "Лід"),
            ],
        },
    )
    lead_a = next(
        item.lead_ref_key
        for item in result.link_summaries
        if item.voucher_key == result.accepted_links[0].voucher_key
    )
    visible = query_lead_vouchers(
        result, LeadVoucherFilter(lead_ref_keys=(lead_a,))
    )
    voucher_number_by_key = {
        voucher.key: voucher.full_number for voucher in result.vouchers
    }

    assert len(visible.rows) == 1
    assert {
        voucher_number_by_key[fact.voucher_key]
        for fact in result.other_product_facts
    } == {"SE-632"}


def test_client_without_other_rows_is_not_emitted_and_missing_tax_has_no_noise(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", MEMBER_A, "SE-640"),
                _r1("R2", "S2", MEMBER_B, "SE-641"),
            ],
            InputRole.R2: [
                _r2("P1", "SE-640", 1, tax_id="00000001"),
                _r2("P2", "SE-641", 2, tax_id=None),
            ],
            InputRole.R3: [
                _r3(MEMBER_A, "Відомий", "Lead"),
                _r3(MEMBER_B, "Невідомий", "Тип", member_type=""),
            ],
        },
    )

    assert not result.eligible_clients
    assert IssueCode.CLIENT_UNKNOWN in {issue.code for issue in result.issues}
    assert IssueCode.OTHER_VOUCHER_ELIGIBILITY_UNKNOWN not in {
        issue.code for issue in result.issues
    }


def test_invariant_failure_exposes_stable_issue_code_and_diagnostic(
    workbook_set_factory,
):
    result = _report(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", MEMBER_A, "SE-650")],
            InputRole.R2: [_r2("P1", "SE-650", 1)],
            InputRole.R3: [_r3(MEMBER_A, "Контрольний", "Лід")],
        },
    )
    broken_measures = tuple(
        Measure.complete(item.key, item.label_uk, item.unit, 999)
        if item.key == "main.quantity"
        else item
        for item in result.measures
    )

    with pytest.raises(ReportInvariantError) as captured:
        validate_report_result(replace(result, measures=broken_measures))

    assert captured.value.code is IssueCode.CONTROL_TOTAL_MISMATCH
    assert captured.value.issue.code is IssueCode.CONTROL_TOTAL_MISMATCH
    assert captured.value.diagnostic_ref


def test_row_order_does_not_change_canonical_business_result(
    workbook_set_factory,
):
    r2_rows = [
        _r2("P1", "SE-600", 7, tax_id="00000001"),
        _r2(
            "P2",
            "SE-600",
            33,
            tax_id="00000002",
            species="CRN: Corn Seeds",
        ),
    ]
    r1_rows = [
        _r1("R1", "S1", MEMBER_A, "SE-600"),
        _r1("R2", "S2", MEMBER_B, None, answer="Відповідь"),
    ]
    r3_rows = [
        _r3(MEMBER_A, "Лід", "Один"),
        _r3(MEMBER_B, "Лід", "Два"),
    ]
    r4_rows = [
        {"Activity ID": "A1", "Name": "Лід Один", "Results": "Результат"},
        {"Activity ID": "A2", "Name": "Лід Два"},
    ]
    first = _report(
        workbook_set_factory,
        {
            InputRole.R1: r1_rows,
            InputRole.R2: r2_rows,
            InputRole.R3: r3_rows,
            InputRole.R4: r4_rows,
        },
    )
    second = _report(
        workbook_set_factory,
        {
            InputRole.R1: list(reversed(r1_rows)),
            InputRole.R2: list(reversed(r2_rows)),
            InputRole.R3: list(reversed(r3_rows)),
            InputRole.R4: list(reversed(r4_rows)),
        },
    )

    assert first.snapshot_id != second.snapshot_id
    assert business_control_payload(first) == business_control_payload(second)
