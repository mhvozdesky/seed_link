"""Orchestrate the complete immutable Block 04 ReportResult."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
import logging

from seedlink.application.analysis import AutomaticMatchingResult
from seedlink.domain.aggregation import (
    build_clients,
    build_main_aggregation,
    build_report_measures,
)
from seedlink.domain.date_attribution import resolve_lead_dates
from seedlink.domain.funnel import build_funnel_rows
from seedlink.domain.invariants import ReportInvariantError, validate_report_result
from seedlink.domain.models import ManualDecision, ReportResult
from seedlink.domain.other_vouchers import build_other_vouchers
from seedlink.domain.provenance import InputRole


_LOGGER = logging.getLogger("seedlink.application.reporting")
_LOGGER.addHandler(logging.NullHandler())


def _unknown_groups_by_voucher(
    matching: AutomaticMatchingResult,
) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for uncertainty in matching.source_uncertainties:
        if uncertainty.role is not InputRole.R2:
            continue
        for voucher_key in uncertainty.related_keys:
            result[voucher_key] += 1
    return dict(result)


def _uncertainty_counts_by_role(
    matching: AutomaticMatchingResult,
) -> dict[InputRole, int]:
    result: dict[InputRole, int] = defaultdict(int)
    for uncertainty in matching.source_uncertainties:
        result[uncertainty.role] += 1
    return dict(result)


def build_report_result(
    matching: AutomaticMatchingResult,
    *,
    revision: int = 0,
    calculated_at: datetime | None = None,
    decisions: tuple[ManualDecision, ...] = (),
    check_cancelled: Callable[[], None] | None = None,
) -> ReportResult:
    """Turn Block 03 automatic facts into a complete Block 04 result."""

    if revision < 0:
        raise ValueError("revision must not be negative")
    checkpoint = check_cancelled or (lambda: None)
    checkpoint()
    lead_dates, date_issues = resolve_lead_dates(
        matching.lead_refs,
        matching.participants,
        matching.participant_links,
    )
    unknown_groups = _unknown_groups_by_voucher(matching)
    main = build_main_aggregation(
        accepted_links=matching.accepted_links,
        vouchers=matching.vouchers,
        product_lines=matching.product_lines,
        participant_links=matching.participant_links,
        lead_dates=lead_dates,
        unknown_groups_by_voucher=unknown_groups,
    )
    checkpoint()
    main_line_by_key = {line.key: line for line in matching.product_lines}
    main_lines = tuple(
        main_line_by_key[fact.product_line_key] for fact in main.product_facts
    )
    voucher_by_key = {voucher.key: voucher for voucher in matching.vouchers}
    clients, client_issues = build_clients(main_lines, voucher_by_key)
    funnel_rows = build_funnel_rows(
        matching.participants,
        matching.surveys,
        matching.activities,
        matching.participant_links,
        matching.accepted_links,
    )
    checkpoint()
    eligible_clients, other_facts, eligibility_issues = build_other_vouchers(
        accepted_links=matching.accepted_links,
        product_lines=matching.product_lines,
        participants=matching.participants,
        participant_links=matching.participant_links,
        main_facts=main.product_facts,
        lead_dates=lead_dates,
    )
    checkpoint()
    issues_by_id = {
        issue.issue_id: issue
        for issue in (
            matching.issues
            + date_issues
            + client_issues
            + eligibility_issues
        )
    }
    issues = tuple(issues_by_id.values())
    measures = build_report_measures(
        snapshot=matching.snapshot,
        product_lines=matching.product_lines,
        main_facts=main.product_facts,
        clients=clients,
        voucher_summaries=main.voucher_summaries,
        funnel_rows=funnel_rows,
        other_facts=other_facts,
        participant_links=matching.participant_links,
        accepted_links=matching.accepted_links,
        survey_count=len(matching.surveys),
        activity_count=len(matching.activities),
        uncertainty_count_by_role=_uncertainty_counts_by_role(matching),
        unknown_groups_by_voucher=unknown_groups,
        issues=issues,
    )
    checkpoint()
    decision_identity = "\x1e".join(
        "\x1f".join(
            (
                decision.decision_id,
                decision.target.value,
                decision.target_key,
                decision.action.value,
                "\x1d".join(decision.selected_keys),
                "\x1c".join(decision.mention_keys),
                decision.reason or "",
                str(decision.sequence),
            )
        )
        for decision in decisions
    )
    calculation_identity = (
        f"{matching.snapshot_id}\x1f{revision}\x1f{decision_identity}"
    )
    calculation_id = (
        "calculation:"
        + sha256(calculation_identity.encode("utf-8")).hexdigest()
    )
    result = ReportResult(
        calculation_id=calculation_id,
        snapshot=matching.snapshot,
        revision=revision,
        calculated_at=calculated_at or datetime.now(UTC),
        surveys=matching.surveys,
        participants=matching.participants,
        activities=matching.activities,
        lead_refs=matching.lead_refs,
        participant_links=matching.participant_links,
        mentions=matching.mentions,
        vouchers=matching.vouchers,
        product_lines=matching.product_lines,
        accepted_links=matching.accepted_links,
        clients=clients,
        decisions=decisions,
        issues=issues,
        measures=measures,
        lead_dates=lead_dates,
        link_summaries=main.link_summaries,
        voucher_summaries=main.voucher_summaries,
        product_facts=main.product_facts,
        link_product_times=main.link_product_times,
        funnel_rows=funnel_rows,
        eligible_clients=eligible_clients,
        other_product_facts=other_facts,
        hybrid_summaries=main.hybrid_summaries,
    )
    try:
        validate_report_result(result)
    except ReportInvariantError as error:
        _LOGGER.exception(
            "Внутрішня помилка контрольних сум; issue_code=%s; diagnostic_ref=%s",
            error.code.value,
            error.diagnostic_ref,
        )
        raise
    checkpoint()
    return result
