from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from seedlink.application import analyze_links, build_report_result
from seedlink.domain import (
    InputRole,
    LeadVoucherFilter,
    MeasureStatus,
    ProductFilter,
    query_lead_vouchers,
    query_product_lines,
)
from seedlink.domain.issues import IssueCode
from seedlink.input_xlsx import import_workbooks
from seedlink.reports import build_html_projection, write_html_report


MEMBER = "00vAbCdEfGhIjKlIVK"
MEMBER_B = "00vAbCdEfGhIjKmIVK"
MEMBER_C = "00vAbCdEfGhIjKnIVK"
LEAD_MAIL = "lead.private@example.invalid"
PARTICIPANT_MAIL = "participant.private@example.invalid"
MOBILE = "+380991234567"
RAW_ANSWER = "приватна нотатка, якої не має бути у звіті"
RAW_ACTIVITY = "закрита нотатка активності"
DANGEROUS_NAME = "Клієнт </script><img src=x>"
NODE = shutil.which("node")
JS_HARNESS = Path(__file__).parents[1] / "js" / "report_dom_harness.js"


class _DocumentInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.external_attributes: list[tuple[str, str, str]] = []
        self.data_script_parts: list[str] = []
        self._inside_data_script = False

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "script" and dict(attrs).get("id") == "seedlink-data":
            self._inside_data_script = True
        for name, value in attrs:
            if name in {"src", "href"} and value and not value.startswith("#"):
                self.external_attributes.append((tag, name, value))

    def handle_endtag(self, tag) -> None:
        if tag == "script" and self._inside_data_script:
            self._inside_data_script = False

    def handle_data(self, data) -> None:
        if self._inside_data_script:
            self.data_script_parts.append(data)


def _result(
    workbook_set_factory, *, empty: bool = False, unmatched_mention: bool = False
):
    rows = {} if empty else {
        InputRole.R1: [
            {
                "Record Nr": "R1",
                "Campaigm Member": MEMBER,
                "First Name": "Тест",
                "Last Name": "Лід",
                "Mobile": MOBILE,
                "Email": LEAD_MAIL,
                "Time Taken": "2026-09-01",
                "Survey Response ID": "S1",
                "Answer": "SE-999" if unmatched_mention else RAW_ANSWER,
                "Answer (Long Text)": "SE-100",
            }
        ],
        InputRole.R2: [
            {
                "Opportunity Product Id 18": "P1",
                "Voucher Number": "SE-100",
                "Current Year Planned Quantity": "0.1",
                "Tax ID 1": "00000001",
                "Account Name": DANGEROUS_NAME,
                "Species group": "SUN: Sunflowers",
                "Parent Product Local Description": "Гібрид А",
                "Opportunity Product: Created Date": date(2026, 9, 2),
            },
            {
                "Opportunity Product Id 18": "P2",
                "Voucher Number": "SE-100",
                "Current Year Planned Quantity": "0.2",
                "Tax ID 1": "00000002",
                "Account Name": "Клієнт Б",
                "Species group": "CRN: Corn Seeds",
                "Opportunity Product: Created Date": date(2026, 9, 3),
            },
            {
                "Opportunity Product Id 18": "P3",
                "Voucher Number": "SE-100",
                "Current Year Planned Quantity": None,
                "Tax ID 1": "00000001",
                "Account Name": DANGEROUS_NAME,
            },
            {
                "Opportunity Product Id 18": "P4",
                "Voucher Number": "SE-200",
                "Current Year Planned Quantity": 4,
                "Tax ID 1": "00000001",
                "Account Name": DANGEROUS_NAME,
                "Species group": "SUN: Sunflowers",
                "Opportunity Product: Created Date": date(2026, 8, 31),
            },
        ],
        InputRole.R3: [
            {
                "Member Status": "Responded",
                "Member Type": "Lead",
                "First Name": "Тест",
                "Last Name": "Лід",
                "Email": PARTICIPANT_MAIL,
                "Campaign Member Id 18": MEMBER,
                "Member First Associated Date": date(2026, 9, 1),
            }
        ],
        InputRole.R4: [
            {
                "Activity ID": "A1",
                "Name": "Тест Лід",
                "Results": "Результат",
                "Notes": RAW_ACTIVITY,
            }
        ],
    }
    imported = import_workbooks(workbook_set_factory(rows))
    assert imported.is_accepted
    return build_report_result(
        analyze_links(imported), calculated_at=datetime(2026, 9, 10, tzinfo=UTC)
    )


def _all_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _all_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _all_keys(nested)


def _run_javascript(report_path, actions=()):
    completed = subprocess.run(
        [NODE, str(JS_HARNESS), str(report_path), json.dumps(actions)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _conflicting_product_result(workbook_set_factory):
    imported = import_workbooks(
        workbook_set_factory(
            {
                InputRole.R1: [
                    {
                        "Record Nr": "R1",
                        "Campaigm Member": MEMBER,
                        "Time Taken": "2026-09-01",
                        "Survey Response ID": "S1",
                        "Answer (Long Text)": "SE-610",
                    }
                ],
                InputRole.R2: [
                    {
                        "Opportunity Product Id 18": "P1",
                        "Voucher Number": "SE-610",
                        "Current Year Planned Quantity": 10,
                    },
                    {
                        "Opportunity Product Id 18": "P1",
                        "Voucher Number": "SE-610",
                        "Current Year Planned Quantity": 20,
                    },
                ],
                InputRole.R3: [
                    {
                        "Member Type": "Lead",
                        "First Name": "Лід",
                        "Last Name": "Конфлікт",
                        "Campaign Member Id 18": MEMBER,
                    }
                ],
            }
        )
    )
    assert imported.is_accepted
    return build_report_result(analyze_links(imported))


def _fraction_result(workbook_set_factory):
    imported = import_workbooks(
        workbook_set_factory(
            {
                InputRole.R1: [
                    {"Record Nr": "R1", "Survey Response ID": "S1", "Campaigm Member": MEMBER},
                    {"Record Nr": "R2", "Survey Response ID": "S2", "Campaigm Member": MEMBER_B},
                    {"Record Nr": "R3", "Survey Response ID": "S3", "Campaigm Member": MEMBER_C},
                ],
                InputRole.R3: [
                    {"Campaign Member Id 18": MEMBER, "First Name": "Перший"},
                    {"Campaign Member Id 18": MEMBER_B, "First Name": "Другий"},
                ],
            }
        )
    )
    assert imported.is_accepted
    return build_report_result(analyze_links(imported))


def _two_voucher_result(workbook_set_factory):
    imported = import_workbooks(
        workbook_set_factory(
            {
                InputRole.R1: [
                    {
                        "Record Nr": "R1",
                        "Survey Response ID": "S1",
                        "Campaigm Member": MEMBER,
                        "Answer (Long Text)": "SE-200",
                    },
                    {
                        "Record Nr": "R2",
                        "Survey Response ID": "S2",
                        "Campaigm Member": MEMBER,
                        "Answer (Long Text)": "SE-100",
                    },
                ],
                InputRole.R2: [
                    {
                        "Opportunity Product Id 18": "P1",
                        "Voucher Number": "SE-200",
                        "Current Year Planned Quantity": 12,
                        "Species group": "CRN: Corn Seeds",
                    },
                    {
                        "Opportunity Product Id 18": "P2",
                        "Voucher Number": "SE-100",
                        "Current Year Planned Quantity": 34,
                        "Species group": "SUN: Sunflowers",
                    },
                ],
                InputRole.R3: [
                    {"Campaign Member Id 18": MEMBER, "First Name": "Лід"}
                ],
            }
        )
    )
    assert imported.is_accepted
    return build_report_result(analyze_links(imported))


def test_html_projection_is_whitelisted_contact_free_and_exact(workbook_set_factory) -> None:
    result = _result(workbook_set_factory)
    projection = build_html_projection(
        result, exported_at=datetime(2026, 9, 11, tzinfo=UTC)
    )

    serialized = json.dumps(projection, ensure_ascii=False)
    for secret in (LEAD_MAIL, PARTICIPANT_MAIL, MOBILE, RAW_ANSWER, RAW_ACTIVITY):
        assert secret not in serialized
    forbidden_keys = {"email", "phone", "mobile", "answer", "notes", "details", "original_value"}
    assert forbidden_keys.isdisjoint({key.casefold() for key in _all_keys(projection)})

    assert [row["quantity"] for row in projection["products"]] == ["0.1", "0.2", None]
    assert projection["controls"]["products"]["quantity"] == {
        "known": "0.3",
        "unknown": 1,
        "status": "partial",
    }
    assert projection["controls"]["otherProducts"]["quantity"]["known"] == "4"
    projected_known = sum(
        (Decimal(row["quantity"]) for row in projection["products"] if row["quantity"] is not None),
        Decimal(0),
    )
    projected_unknown = sum(row["quantity"] is None for row in projection["products"])
    projected_unknown += sum(projection["productExtraUnknownByVoucher"].values())
    python_quantity = query_product_lines(result).measures[-1]
    assert projected_known == python_quantity.known_value
    assert projected_unknown == python_quantity.unknown_count

    voucher_known = sum(
        (Decimal(row["quantity"]) for row in projection["vouchers"] if row["quantity"] is not None),
        Decimal(0),
    )
    assert voucher_known == Decimal(projection["controls"]["vouchers"]["quantity"]["known"])
    assert sum(row["unknown"] for row in projection["vouchers"]) == 1
    assert all("message" in issue and "locations" in issue for issue in projection["issues"])
    assert all("details" not in issue for issue in projection["issues"])


def test_html_report_is_one_offline_escaped_file(tmp_path, workbook_set_factory) -> None:
    result = _result(workbook_set_factory)
    output = tmp_path / "SeedLink report.html"
    returned = write_html_report(
        result, output, exported_at=datetime(2026, 9, 11, tzinfo=UTC)
    )
    assert returned == output
    document = output.read_text(encoding="utf-8")

    for secret in (LEAD_MAIL, PARTICIPANT_MAIL, MOBILE, RAW_ANSWER, RAW_ACTIVITY):
        assert secret not in document
    assert DANGEROUS_NAME not in document
    assert r"\u003c/script\u003e\u003cimg src=x\u003e" in document
    assert "BigInt" in document
    assert "innerHTML" not in document
    assert "connect-src 'none'" in document
    assert "https://" not in document and "http://" not in document
    assert all(
        caption in document
        for caption in (
            "Воронка учасників, осіб",
            "Обсяг за культурами, од.",
            "Часовий розподіл обсягу, од.",
            "Обсяг за місяцями ProductLine, од.",
        )
    )

    inspector = _DocumentInspector()
    inspector.feed(document)
    assert inspector.external_attributes == []
    payload = json.loads("".join(inspector.data_script_parts))
    assert payload["meta"]["calculationId"] == result.calculation_id
    assert payload["products"][0]["quantity"] == "0.1"


def test_voucher_issue_flag_uses_survey_and_mention_keys(workbook_set_factory) -> None:
    result = _result(workbook_set_factory, unmatched_mention=True)
    assert IssueCode.VOUCHER_NOT_FOUND in {issue.code for issue in result.issues}
    assert len(
        query_lead_vouchers(result, LeadVoucherFilter(has_issues=True)).rows
    ) == 1
    assert not query_lead_vouchers(
        result, LeadVoucherFilter(has_issues=False)
    ).rows

    projection = build_html_projection(result)
    assert len(projection["vouchers"]) == 1
    assert projection["vouchers"][0]["hasIssues"] is True


def test_html_report_supports_empty_tables_and_validates_arguments(
    tmp_path, workbook_set_factory
) -> None:
    result = _result(workbook_set_factory, empty=True)
    output = write_html_report(result, tmp_path / "empty.htm")
    assert output.is_file()
    projection = build_html_projection(result)
    assert projection["participants"] == []
    assert projection["vouchers"] == []
    assert projection["products"] == []
    assert projection["controls"]["products"]["quantity"]["known"] == "0"

    with pytest.raises(ValueError, match="must end"):
        write_html_report(result, tmp_path / "wrong.txt")
    with pytest.raises(ValueError, match="timezone-aware"):
        write_html_report(
            result,
            tmp_path / "wrong-time.html",
            exported_at=datetime(2026, 9, 11),
        )


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_report_javascript_matches_python_queries(
    tmp_path, workbook_set_factory
) -> None:
    result = _result(workbook_set_factory, unmatched_mention=True)
    output = write_html_report(result, tmp_path / "javascript-report.html")
    states = _run_javascript(
        output,
        (
            {"area": "products", "filter": "taxId", "value": "00000002"},
            {"area": "vouchers", "filter": "issues", "value": "yes"},
            {"area": "vouchers", "filter": "issues", "value": "no"},
        ),
    )

    assert states[0]["integrity"] == "Контрольні підсумки збігаються"
    assert states[0]["metrics"]["products"]["Відомий обсяг"] == "0,3 + 1 невід."
    python_products = query_product_lines(
        result, ProductFilter(tax_ids=("00000002",))
    )
    assert len(python_products.rows) == states[1]["rows"]["products"] == 1
    assert python_products.measures[-1].known_value == Decimal("0.2")
    assert states[1]["metrics"]["products"]["Відомий обсяг"] == "0,2"
    assert states[1]["rows"]["participants"] == states[0]["rows"]["participants"]

    python_with_issues = query_lead_vouchers(
        result, LeadVoucherFilter(has_issues=True)
    )
    assert len(python_with_issues.rows) == states[2]["rows"]["vouchers"] == 1
    assert states[3]["rows"]["vouchers"] == 0


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_product_search_preserves_unmaterialized_unknown_contribution(
    tmp_path, workbook_set_factory
) -> None:
    result = _conflicting_product_result(workbook_set_factory)
    python_products = query_product_lines(result)
    quantity = python_products.measures[-1]
    assert quantity.status is MeasureStatus.UNAVAILABLE
    assert quantity.unknown_count == 1

    output = write_html_report(result, tmp_path / "conflict.html")
    states = _run_javascript(
        output,
        ({"area": "products", "filter": "search", "value": "se-610"},),
    )
    assert states[0]["integrity"] == "Контрольні підсумки збігаються"
    assert states[0]["metrics"]["products"]["Відомий обсяг"] == "— + 1 невід."
    assert states[1]["metrics"]["products"]["Відомий обсяг"] == "— + 1 невід."


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_javascript_shows_a_readable_error_for_incompatible_payload(
    tmp_path, workbook_set_factory
) -> None:
    result = _result(workbook_set_factory)
    output = write_html_report(result, tmp_path / "broken.html")
    document = output.read_text(encoding="utf-8")
    output.write_text(
        document.replace('"controls":{', '"incompatibleControls":{', 1),
        encoding="utf-8",
    )

    states = _run_javascript(output)
    assert states[0]["fatal"] == "Не вдалося відобразити звіт"


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_percentage_cards_round_for_display_without_changing_payload(
    tmp_path, workbook_set_factory
) -> None:
    result = _fraction_result(workbook_set_factory)
    projection = build_html_projection(result)
    measure = next(
        item
        for item in projection["overview"]["measures"]
        if item["key"] == "quality.lead_refs.linked_rate"
    )
    assert measure["known"] == "66.66666666666666666666666667"

    output = write_html_report(result, tmp_path / "rate.html")
    states = _run_javascript(output)
    assert states[0]["overview"]["LeadRef, пов'язані з R3, частка"] == "66,7 %"


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_voucher_crop_column_sorts_by_its_rendered_value(
    tmp_path, workbook_set_factory
) -> None:
    result = _two_voucher_result(workbook_set_factory)
    output = write_html_report(result, tmp_path / "crop-sort.html")
    states = _run_javascript(
        output,
        (
            {"type": "sort", "area": "vouchers", "column": "Культури"},
            {"type": "sort", "area": "vouchers", "column": "Культури"},
        ),
    )
    ascending = [row[7] for row in states[1]["tableRows"]["vouchers"]]
    descending = [row[7] for row in states[2]["tableRows"]["vouchers"]]
    assert ascending == ["Кукурудза: 12", "Соняшник: 34"]
    assert descending == list(reversed(ascending))
