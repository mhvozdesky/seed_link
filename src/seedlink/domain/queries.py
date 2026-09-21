"""Independent, side-effect-free query areas over a fixed ReportResult."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from seedlink.domain.measures import Measure, quantity_measure
from seedlink.domain.issues import Issue, IssueCode, IssueLevel
from seedlink.domain.models import (
    CropCategory,
    FunnelRow,
    LinkSummary,
    ProductLineFact,
    ReportResult,
    VoucherMatchMethod,
)
from seedlink.domain.normalization import normalize_name
from seedlink.domain.provenance import InputRole


class DateFilterMode(StrEnum):
    ALL = "all"
    RANGE = "range"
    UNKNOWN_ONLY = "unknown_only"


class IssueImpact(StrEnum):
    INPUT = "input"
    MATCHING = "matching"
    QUANTITY = "quantity"
    CLIENT = "client"
    DATE = "date"
    OTHER_VOUCHERS = "other_vouchers"
    INTERNAL_OR_EXPORT = "internal_or_export"


@dataclass(frozen=True, slots=True)
class DateFilter:
    mode: DateFilterMode = DateFilterMode.ALL
    date_from: date | None = None
    date_to: date | None = None

    def __post_init__(self) -> None:
        if self.mode is DateFilterMode.RANGE:
            if self.date_from is None and self.date_to is None:
                raise ValueError("range date filter needs at least one boundary")
            if (
                self.date_from is not None
                and self.date_to is not None
                and self.date_from > self.date_to
            ):
                raise ValueError("date_from must not be after date_to")
        elif self.date_from is not None or self.date_to is not None:
            raise ValueError("date boundaries are valid only in range mode")

    def matches(self, value: date | None) -> bool:
        if self.mode is DateFilterMode.ALL:
            return True
        if self.mode is DateFilterMode.UNKNOWN_ONLY:
            return value is None
        if value is None:
            return False
        return not (
            self.date_from is not None and value < self.date_from
        ) and not (self.date_to is not None and value > self.date_to)


@dataclass(frozen=True, slots=True)
class FunnelFilter:
    appeared: DateFilter = DateFilter()
    member_types: tuple[str, ...] = ()
    member_statuses: tuple[str, ...] = ()
    search: str | None = None


@dataclass(frozen=True, slots=True)
class LeadVoucherFilter:
    lead_ref_keys: tuple[str, ...] = ()
    participant_keys: tuple[str, ...] = ()
    voucher_keys: tuple[str, ...] = ()
    tax_ids: tuple[str, ...] = ()
    methods: tuple[VoucherMatchMethod, ...] = ()
    has_issues: bool | None = None


@dataclass(frozen=True, slots=True)
class ProductFilter:
    created: DateFilter = DateFilter()
    crops: tuple[CropCategory, ...] = ()
    hybrids: tuple[str, ...] = ()
    tax_ids: tuple[str, ...] = ()
    voucher_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OtherVoucherFilter:
    tax_ids: tuple[str, ...] = ()
    voucher_keys: tuple[str, ...] = ()
    created: DateFilter = DateFilter()


@dataclass(frozen=True, slots=True)
class IssueFilter:
    codes: tuple[IssueCode, ...] = ()
    levels: tuple[IssueLevel, ...] = ()
    roles: tuple[InputRole, ...] = ()
    impacts: tuple[IssueImpact, ...] = ()
    resolved: bool | None = None
    search: str | None = None


@dataclass(frozen=True, slots=True)
class FunnelQueryResult:
    rows: tuple[FunnelRow, ...]
    measures: tuple[Measure, ...]


@dataclass(frozen=True, slots=True)
class FactQueryResult:
    rows: tuple[ProductLineFact, ...]
    measures: tuple[Measure, ...]


@dataclass(frozen=True, slots=True)
class LinkQueryResult:
    rows: tuple[LinkSummary, ...]
    measures: tuple[Measure, ...]


@dataclass(frozen=True, slots=True)
class IssueQueryResult:
    rows: tuple[Issue, ...]
    measures: tuple[Measure, ...]


def _normalized_set(values: tuple[str, ...]) -> set[str]:
    return {
        normalized
        for value in values
        if (normalized := normalize_name(value)) is not None
    }


def query_funnel(
    result: ReportResult, filters: FunnelFilter = FunnelFilter()
) -> FunnelQueryResult:
    participant_by_key = {item.key: item for item in result.participants}
    member_types = _normalized_set(filters.member_types)
    member_statuses = _normalized_set(filters.member_statuses)
    search = normalize_name(filters.search)
    selected: list[FunnelRow] = []
    for row in result.funnel_rows:
        if not filters.appeared.matches(row.appeared_on):
            continue
        if member_types and normalize_name(row.member_type) not in member_types:
            continue
        if member_statuses and normalize_name(row.member_status) not in member_statuses:
            continue
        participant = participant_by_key[row.participant_key]
        searchable = normalize_name(
            " ".join(
                item
                for item in (
                    participant.first_name,
                    participant.last_name,
                    participant.member_type,
                    participant.member_status,
                )
                if item
            )
        )
        if search is not None and search not in (searchable or ""):
            continue
        selected.append(row)

    rows = tuple(selected)
    denominator = len(rows)
    base_measure = next(
        (
            measure
            for measure in result.measures
            if measure.key == "funnel.participants.count"
        ),
        None,
    )
    unknown_base = base_measure.unknown_count if base_measure is not None else 0
    definitions = (
        ("participants", "Учасники кампанії", lambda row: True),
        ("activity", "Є активність", lambda row: row.has_activity),
        ("result", "Результат зафіксовано", lambda row: row.has_recorded_result),
        ("voucher", "Є підтверджений ваучер", lambda row: row.has_confirmed_voucher),
    )
    measures: list[Measure] = []
    for suffix, label, predicate in definitions:
        count = sum(predicate(row) for row in rows)
        if unknown_base:
            measures.append(
                Measure.partial(
                    f"query.funnel.{suffix}.count",
                    label,
                    "осіб",
                    count,
                    unknown_base,
                    ("Є групи R3 з невизначеним унікальним обліком.",),
                )
            )
        else:
            measures.append(
                Measure.complete(
                    f"query.funnel.{suffix}.count", label, "осіб", count
                )
            )
        if denominator and not unknown_base:
            measures.append(
                Measure.complete(
                    f"query.funnel.{suffix}.rate",
                    f"{label}, частка",
                    "%",
                    Decimal(count) * Decimal(100) / Decimal(denominator),
                )
            )
        else:
            measures.append(
                Measure.unavailable(
                    f"query.funnel.{suffix}.rate",
                    f"{label}, частка",
                    "%",
                    (
                        "Унікальний розмір видимої бази R3 невідомий."
                        if unknown_base
                        else "Видима база учасників порожня."
                    ,),
                    unknown_count=unknown_base,
                )
            )
    return FunnelQueryResult(rows, tuple(measures))


def _fact_measures(
    rows: tuple[ProductLineFact, ...], *, prefix: str, extra_unknown: int = 0
) -> tuple[Measure, ...]:
    return (
        Measure.complete(
            f"{prefix}.vouchers",
            "Унікальні ваучери",
            "ваучерів",
            len({row.voucher_key for row in rows}),
        ),
        Measure.complete(
            f"{prefix}.clients",
            "Унікальні клієнти",
            "клієнтів",
            len({row.tax_id for row in rows if row.tax_id is not None}),
        ),
        quantity_measure(
            f"{prefix}.quantity",
            "Видимий обсяг",
            (row.quantity for row in rows),
            extra_unknown_count=extra_unknown,
        ),
    )


def query_product_lines(
    result: ReportResult, filters: ProductFilter = ProductFilter()
) -> FactQueryResult:
    hybrid_values = _normalized_set(filters.hybrids)
    rows = tuple(
        fact
        for fact in result.product_facts
        if filters.created.matches(fact.created_on)
        and (not filters.crops or fact.crop in filters.crops)
        and (
            not hybrid_values
            or normalize_name(fact.hybrid) in hybrid_values
        )
        and (not filters.tax_ids or fact.tax_id in filters.tax_ids)
        and (not filters.voucher_keys or fact.voucher_key in filters.voucher_keys)
    )
    # Unmaterialized conflicting Product IDs belong only to the unrestricted
    # set (or explicitly unknown crop/date), never to a known filtered bucket.
    include_unmaterialized = (
        not filters.hybrids
        and not filters.tax_ids
        and (
            not filters.crops or filters.crops == (CropCategory.UNKNOWN,)
        )
        and filters.created.mode in {
            DateFilterMode.ALL,
            DateFilterMode.UNKNOWN_ONLY,
        }
    )
    voucher_scope = (
        set(filters.voucher_keys)
        if filters.voucher_keys
        else {summary.voucher_key for summary in result.voucher_summaries}
    )
    scoped_facts = tuple(
        fact for fact in result.product_facts if fact.voucher_key in voucher_scope
    )
    materialized_unknown = sum(fact.quantity is None for fact in scoped_facts)
    scoped_unknown = sum(
        summary.quantity.unknown_count
        for summary in result.voucher_summaries
        if summary.voucher_key in voucher_scope
    )
    extra_unknown = (
        max(scoped_unknown - materialized_unknown, 0)
        if include_unmaterialized
        else 0
    )
    return FactQueryResult(
        rows,
        _fact_measures(rows, prefix="query.products", extra_unknown=extra_unknown),
    )


def query_lead_vouchers(
    result: ReportResult,
    filters: LeadVoucherFilter = LeadVoucherFilter(),
) -> LinkQueryResult:
    link_by_key = {item.key: item for item in result.accepted_links}
    affected = {
        key
        for issue in result.issues
        for key in issue.affected_keys
        if not issue.is_resolved
    }
    rows = tuple(
        summary
        for summary in result.link_summaries
        if (not filters.lead_ref_keys or summary.lead_ref_key in filters.lead_ref_keys)
        and (
            not filters.participant_keys
            or summary.participant_key in filters.participant_keys
        )
        and (not filters.voucher_keys or summary.voucher_key in filters.voucher_keys)
        and (not filters.tax_ids or bool(set(summary.tax_ids).intersection(filters.tax_ids)))
        and (
            not filters.methods
            or link_by_key[summary.link_key].method in filters.methods
        )
        and (
            filters.has_issues is None
            or filters.has_issues
            == bool(
                {
                    summary.link_key,
                    summary.lead_ref_key,
                    summary.voucher_key,
                    *link_by_key[summary.link_key].survey_keys,
                    *link_by_key[summary.link_key].mention_keys,
                    *summary.product_line_keys,
                }.intersection(affected)
            )
        )
    )
    visible_vouchers = {row.voucher_key for row in rows}
    facts = tuple(
        fact for fact in result.product_facts if fact.voucher_key in visible_vouchers
    )
    unknown_by_voucher = {
        summary.voucher_key: summary.quantity.unknown_count
        for summary in result.voucher_summaries
    }
    materialized_unknown = sum(fact.quantity is None for fact in facts)
    extra_unknown = max(
        sum(unknown_by_voucher.get(key, 0) for key in visible_vouchers)
        - materialized_unknown,
        0,
    )
    visible_tax_ids = {
        tax_id for row in rows for tax_id in row.tax_ids
    }
    return LinkQueryResult(
        rows,
        (
            Measure.complete(
                "query.links.vouchers",
                "Унікальні ваучери",
                "ваучерів",
                len(visible_vouchers),
            ),
            Measure.complete(
                "query.links.clients",
                "Унікальні клієнти",
                "клієнтів",
                len(visible_tax_ids),
            ),
            quantity_measure(
                "query.links.quantity",
                "Видимий обсяг",
                (fact.quantity for fact in facts),
                extra_unknown_count=extra_unknown,
            ),
        ),
    )


def query_other_vouchers(
    result: ReportResult,
    filters: OtherVoucherFilter = OtherVoucherFilter(),
) -> FactQueryResult:
    rows = tuple(
        fact
        for fact in result.other_product_facts
        if (not filters.tax_ids or fact.tax_id in filters.tax_ids)
        and (not filters.voucher_keys or fact.voucher_key in filters.voucher_keys)
        and filters.created.matches(fact.created_on)
    )
    return FactQueryResult(rows, _fact_measures(rows, prefix="query.other"))


def issue_impacts(issue: Issue) -> tuple[IssueImpact, ...]:
    impacts: list[IssueImpact] = []
    if issue.level is IssueLevel.IMPORT_BLOCKING:
        impacts.append(IssueImpact.INPUT)
    if issue.code in {
        IssueCode.PERSON_LINK_AMBIGUOUS,
        IssueCode.PERSON_LINK_CONFLICT,
        IssueCode.PERSON_LINK_UNRESOLVED,
        IssueCode.PERSON_DATA_MISMATCH,
        IssueCode.VOUCHER_FIELD_CONFLICT,
        IssueCode.VOUCHER_MATCH_AMBIGUOUS,
        IssueCode.VOUCHER_NOT_FOUND,
        IssueCode.VOUCHER_NUMBER_MISSING,
        IssueCode.VOUCHER_NUMBER_INVALID,
    }:
        impacts.append(IssueImpact.MATCHING)
    if issue.code in {
        IssueCode.QUANTITY_UNKNOWN,
        IssueCode.PRODUCT_ID_CONFLICT,
    }:
        impacts.append(IssueImpact.QUANTITY)
    if issue.code in {IssueCode.CLIENT_UNKNOWN, IssueCode.MULTIPLE_TAX_IDS}:
        impacts.append(IssueImpact.CLIENT)
    if issue.code in {IssueCode.DATE_INVALID, IssueCode.DATE_CONFLICT}:
        impacts.append(IssueImpact.DATE)
    if issue.code is IssueCode.OTHER_VOUCHER_ELIGIBILITY_UNKNOWN:
        impacts.append(IssueImpact.OTHER_VOUCHERS)
    if issue.level in {IssueLevel.INTERNAL, IssueLevel.EXPORT}:
        impacts.append(IssueImpact.INTERNAL_OR_EXPORT)
    if issue.code in {
        IssueCode.DUPLICATE_UNCERTAIN,
        IssueCode.CONFLICTING_ID_DATA,
    }:
        impacts.append(IssueImpact.INPUT)
        if any(source.role is InputRole.R2 for source in issue.sources):
            impacts.append(IssueImpact.QUANTITY)
    return tuple(dict.fromkeys(impacts))


def query_issues(
    result: ReportResult, filters: IssueFilter = IssueFilter()
) -> IssueQueryResult:
    search = normalize_name(filters.search)
    rows = tuple(
        issue
        for issue in result.issues
        if (not filters.codes or issue.code in filters.codes)
        and (not filters.levels or issue.level in filters.levels)
        and (
            not filters.impacts
            or bool(set(issue_impacts(issue)).intersection(filters.impacts))
        )
        and (
            not filters.roles
            or any(source.role in filters.roles for source in issue.sources)
        )
        and (filters.resolved is None or issue.is_resolved is filters.resolved)
        and (
            search is None
            or search
            in (
                normalize_name(
                    " ".join(
                        (
                            issue.code.value,
                            issue.message_uk,
                            *(value for _, value in issue.details),
                        )
                    )
                )
                or ""
            )
        )
    )
    return IssueQueryResult(
        rows,
        (
            Measure.complete(
                "query.issues.count", "Видимі проблеми", "проблем", len(rows)
            ),
            Measure.complete(
                "query.issues.unresolved",
                "Невирішені видимі проблеми",
                "проблем",
                sum(not issue.is_resolved for issue in rows),
            ),
        ),
    )
