"""Cross-checks that stop publication of an internally inconsistent result."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256

from seedlink.domain._issue_factory import make_issue
from seedlink.domain.issues import Issue, IssueCode
from seedlink.domain.measures import Measure
from seedlink.domain.models import (
    CropCategory,
    ProductSection,
    ReportResult,
    TimeBucket,
)


class ReportInvariantError(RuntimeError):
    """Typed internal failure; a final report must not be published after it."""

    def __init__(self, code: IssueCode, message_uk: str) -> None:
        if code not in {IssueCode.CONTROL_TOTAL_MISMATCH, IssueCode.INTERNAL_ERROR}:
            raise ValueError("report invariant error needs an internal issue code")
        self.code = code
        self.message_uk = message_uk
        identity = f"{code.value}\x1f{message_uk}"
        self.diagnostic_ref = sha256(identity.encode("utf-8")).hexdigest()[:12]
        self.issue: Issue = make_issue(
            code,
            message_uk,
            sources=(),
            details=(("diagnostic_ref", self.diagnostic_ref),),
            discriminator=self.diagnostic_ref,
        )
        super().__init__(f"{code.value}: {message_uk} [{self.diagnostic_ref}]")


def _control_failure(message: str) -> None:
    raise ReportInvariantError(IssueCode.CONTROL_TOTAL_MISMATCH, message)


def _internal_failure(message: str) -> None:
    raise ReportInvariantError(IssueCode.INTERNAL_ERROR, message)


def _known(measure: Measure) -> Decimal:
    return measure.known_value if measure.known_value is not None else Decimal(0)


def _required_measure(measures: dict[str, Measure], key: str) -> Measure:
    measure = measures.get(key)
    if measure is None:
        _internal_failure(f"required measure is missing: {key}")
    assert measure is not None
    return measure


def _assert_partition(
    total: Measure, parts: tuple[Measure, ...], label: str
) -> None:
    if sum((_known(item) for item in parts), Decimal(0)) != _known(total):
        _control_failure(f"{label}: known quantity does not reconcile")
    if sum(item.unknown_count for item in parts) != total.unknown_count:
        _control_failure(f"{label}: unknown contributions do not reconcile")


def validate_report_result(result: ReportResult) -> None:
    """Raise before export when a required Block 04 invariant is broken."""

    measure_by_key = {measure.key: measure for measure in result.measures}
    main_total = _required_measure(measure_by_key, "main.quantity")

    main_line_keys = [fact.product_line_key for fact in result.product_facts]
    if len(main_line_keys) != len(set(main_line_keys)):
        _internal_failure("main ProductLine is counted more than once")
    if any(fact.section is not ProductSection.MAIN for fact in result.product_facts):
        _internal_failure("main facts contain a non-main row")
    if any(
        fact.section is not ProductSection.OTHER
        for fact in result.other_product_facts
    ):
        _internal_failure("other facts contain a non-other row")

    line_by_key = {line.key: line for line in result.product_lines}
    missing_main_lines = set(main_line_keys) - set(line_by_key)
    if missing_main_lines:
        _internal_failure("main facts reference missing ProductLine entities")
    known_from_lines = sum(
        (
            line_by_key[key].quantity
            for key in main_line_keys
            if line_by_key[key].quantity is not None
        ),
        Decimal(0),
    )
    if known_from_lines != _known(main_total):
        _control_failure("main known quantity does not match ProductLine facts")

    crop_parts = tuple(
        _required_measure(measure_by_key, f"main.crop.{category.value}")
        for category in CropCategory
    )
    time_parts = tuple(
        _required_measure(measure_by_key, f"main.time.{bucket.value}")
        for bucket in TimeBucket
    )
    _assert_partition(main_total, crop_parts, "crop partition")
    _assert_partition(main_total, time_parts, "time partition")
    hybrid_parts = (
        _required_measure(measure_by_key, "main.hybrid.known.quantity"),
        _required_measure(measure_by_key, "main.hybrid.unknown.quantity"),
    )
    _assert_partition(main_total, hybrid_parts, "hybrid completeness partition")
    _assert_partition(
        main_total,
        tuple(summary.quantity for summary in result.hybrid_summaries),
        "hybrid value partition",
    )
    hybrid_line_keys = [
        key
        for summary in result.hybrid_summaries
        for key in summary.product_line_keys
    ]
    if len(hybrid_line_keys) != len(set(hybrid_line_keys)):
        _internal_failure("one ProductLine belongs to multiple hybrid groups")
    if set(hybrid_line_keys) != set(main_line_keys):
        _control_failure("hybrid groups do not cover all main ProductLine rows")

    coded_main_lines = {
        fact.product_line_key
        for fact in result.product_facts
        if fact.tax_id is not None
    }
    client_line_keys = [
        key for client in result.clients for key in client.product_line_keys
    ]
    if len(client_line_keys) != len(set(client_line_keys)):
        _internal_failure("one ProductLine belongs to multiple client keys")
    if set(client_line_keys) != coded_main_lines:
        _control_failure("client partition does not cover coded main rows")
    tax_by_line = {
        fact.product_line_key: fact.tax_id for fact in result.product_facts
    }
    if any(key not in tax_by_line for key in client_line_keys):
        _internal_failure("client partition references a missing main fact")
    if any(
        tax_by_line[key] != client.tax_id
        for client in result.clients
        for key in client.product_line_keys
    ):
        _control_failure("client partition uses the wrong Tax ID")

    main_vouchers = {fact.voucher_key for fact in result.product_facts}
    main_vouchers.update(summary.voucher_key for summary in result.voucher_summaries)
    other_vouchers = {fact.voucher_key for fact in result.other_product_facts}
    if main_vouchers.intersection(other_vouchers):
        _control_failure("main and other vouchers overlap")

    eligible_by_tax = {client.tax_id: client for client in result.eligible_clients}
    for fact in result.other_product_facts:
        if fact.tax_id is None or fact.tax_id not in eligible_by_tax:
            _control_failure("other ProductLine has no eligible Tax ID")
        if fact.product_line_key not in eligible_by_tax[fact.tax_id].other_product_line_keys:
            _control_failure("other ProductLine is not traced by its client")
        if fact.product_line_key not in line_by_key:
            _internal_failure("other ProductLine has no source entity")

    participant_keys = {participant.key for participant in result.participants}
    for row in result.funnel_rows:
        if row.participant_key not in participant_keys:
            _control_failure("funnel row is outside the R3 base")

    voucher_by_key = {voucher.key: voucher for voucher in result.vouchers}
    for link in result.accepted_links:
        if link.voucher_key not in voucher_by_key:
            _internal_failure("accepted link references a missing voucher")
