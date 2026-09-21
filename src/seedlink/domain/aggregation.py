"""Pure Block 04 aggregation rules over already matched domain entities."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from seedlink.domain._issue_factory import make_issue
from seedlink.domain.date_attribution import time_bucket
from seedlink.domain.dates import calendar_date_in_kyiv
from seedlink.domain.funnel import funnel_measures
from seedlink.domain.issues import Issue, IssueCode
from seedlink.domain.measures import Measure, quantity_measure
from seedlink.domain.models import (
    AcceptedLink,
    Client,
    CropCategory,
    FunnelRow,
    HybridSummary,
    LeadDateResolution,
    LinkProductTimeFact,
    LinkSummary,
    ParticipantLink,
    ParticipantLinkSubject,
    ProductLine,
    ProductLineFact,
    ProductSection,
    TimeBucket,
    Voucher,
    VoucherSummary,
)
from seedlink.domain.normalization import normalize_name, text_or_none
from seedlink.domain.product_metrics import crop_category
from seedlink.domain.provenance import InputRole, InputSnapshot, SourceCell


_CROP_LABELS = {
    CropCategory.SUNFLOWER: "Соняшник",
    CropCategory.CORN: "Кукурудза",
    CropCategory.OTHER: "Інші культури",
    CropCategory.UNKNOWN: "Культура не визначена",
}
_TIME_LABELS = {
    TimeBucket.BEFORE_LEAD: "До появи ліда",
    TimeBucket.ON_OR_AFTER_LEAD: "У день появи або пізніше",
    TimeBucket.UNKNOWN: "Неможливо визначити",
}


@dataclass(frozen=True, slots=True)
class MainAggregation:
    product_facts: tuple[ProductLineFact, ...]
    link_product_times: tuple[LinkProductTimeFact, ...]
    link_summaries: tuple[LinkSummary, ...]
    voucher_summaries: tuple[VoucherSummary, ...]
    hybrid_summaries: tuple[HybridSummary, ...]


def measure_for_lines(
    key: str,
    label: str,
    lines: tuple[ProductLine, ...],
    *,
    extra_unknown_count: int = 0,
) -> Measure:
    return quantity_measure(
        key,
        label,
        (line.quantity for line in lines),
        extra_unknown_count=extra_unknown_count,
    )


def crop_measures(
    lines: tuple[ProductLine, ...],
    *,
    prefix: str,
    extra_unknown_count: int = 0,
) -> tuple[Measure, ...]:
    result: list[Measure] = []
    for category in CropCategory:
        selected = tuple(
            line for line in lines if crop_category(line.species_group) is category
        )
        result.append(
            measure_for_lines(
                f"{prefix}.crop.{category.value}",
                _CROP_LABELS[category],
                selected,
                extra_unknown_count=(
                    extra_unknown_count if category is CropCategory.UNKNOWN else 0
                ),
            )
        )
    return tuple(result)


def time_measures(
    facts: tuple[ProductLineFact, ...],
    *,
    prefix: str,
    extra_unknown_count: int = 0,
) -> tuple[Measure, ...]:
    result: list[Measure] = []
    for bucket in TimeBucket:
        quantities = tuple(
            fact.quantity for fact in facts if fact.time_bucket is bucket
        )
        result.append(
            quantity_measure(
                f"{prefix}.time.{bucket.value}",
                _TIME_LABELS[bucket],
                quantities,
                extra_unknown_count=(
                    extra_unknown_count if bucket is TimeBucket.UNKNOWN else 0
                ),
            )
        )
    return tuple(result)


def hybrid_summaries(
    lines: tuple[ProductLine, ...],
    *,
    prefix: str,
    extra_unknown_count: int = 0,
) -> tuple[HybridSummary, ...]:
    grouped: dict[tuple[CropCategory, str | None], list[ProductLine]] = defaultdict(list)
    variants: dict[tuple[CropCategory, str | None], set[str]] = defaultdict(set)
    for line in lines:
        crop = crop_category(line.species_group)
        normalized = normalize_name(line.hybrid)
        group_key = (crop, normalized)
        grouped[group_key].append(line)
        if (display := text_or_none(line.hybrid)) is not None:
            variants[group_key].add(display)

    unknown_key = (CropCategory.UNKNOWN, None)
    if extra_unknown_count and unknown_key not in grouped:
        grouped[unknown_key] = []

    result: list[HybridSummary] = []
    for (crop, normalized), grouped_lines in sorted(
        grouped.items(), key=lambda item: (item[0][0].value, item[0][1] or "")
    ):
        hybrid = min(variants[(crop, normalized)]) if normalized is not None else None
        identity = f"{prefix}\x1f{crop.value}\x1f{normalized or '<unknown>'}"
        key = f"{prefix}.hybrid.{sha256(identity.encode('utf-8')).hexdigest()[:16]}"
        selected = tuple(grouped_lines)
        result.append(
            HybridSummary(
                key=key,
                crop=crop,
                hybrid=hybrid,
                product_line_keys=tuple(line.key for line in selected),
                quantity=measure_for_lines(
                    f"{key}.quantity",
                    hybrid or "Гібрид не визначено",
                    selected,
                    extra_unknown_count=(
                        extra_unknown_count if (crop, normalized) == unknown_key else 0
                    ),
                ),
            )
        )
    return tuple(result)


def hybrid_quality_measures(
    lines: tuple[ProductLine, ...],
    *,
    prefix: str,
    extra_unknown_count: int = 0,
) -> tuple[Measure, ...]:
    known = tuple(line for line in lines if text_or_none(line.hybrid) is not None)
    unknown = tuple(line for line in lines if text_or_none(line.hybrid) is None)
    total_count = len(known) + len(unknown) + extra_unknown_count
    uncertainty_reason = (
        "Є товарні групи з невизначеним унікальним обліком або гібридом.",
    )
    if extra_unknown_count:
        known_lines = Measure.partial(
            f"{prefix}.hybrid.known_lines",
            "Рядки з визначеним гібридом",
            "рядків",
            len(known),
            extra_unknown_count,
            uncertainty_reason,
        )
        unknown_lines = Measure.partial(
            f"{prefix}.hybrid.unknown_lines",
            "Рядки з невизначеним гібридом",
            "рядків",
            len(unknown),
            extra_unknown_count,
            uncertainty_reason,
        )
        coverage = Measure.unavailable(
            f"{prefix}.hybrid.coverage",
            "Покриття гібридом",
            "%",
            uncertainty_reason,
            unknown_count=extra_unknown_count,
        )
    elif total_count:
        known_lines = Measure.complete(
            f"{prefix}.hybrid.known_lines",
            "Рядки з визначеним гібридом",
            "рядків",
            len(known),
        )
        unknown_lines = Measure.complete(
            f"{prefix}.hybrid.unknown_lines",
            "Рядки з невизначеним гібридом",
            "рядків",
            len(unknown),
        )
        coverage = Measure.complete(
            f"{prefix}.hybrid.coverage",
            "Покриття гібридом",
            "%",
            Decimal(len(known)) * Decimal(100) / Decimal(total_count),
        )
    else:
        known_lines = Measure.complete(
            f"{prefix}.hybrid.known_lines",
            "Рядки з визначеним гібридом",
            "рядків",
            0,
        )
        unknown_lines = Measure.complete(
            f"{prefix}.hybrid.unknown_lines",
            "Рядки з невизначеним гібридом",
            "рядків",
            0,
        )
        coverage = Measure.unavailable(
            f"{prefix}.hybrid.coverage",
            "Покриття гібридом",
            "%",
            ("Товарних рядків немає.",),
        )
    return (
        known_lines,
        unknown_lines,
        measure_for_lines(
            f"{prefix}.hybrid.known.quantity",
            "Обсяг із визначеним гібридом",
            known,
        ),
        measure_for_lines(
            f"{prefix}.hybrid.unknown.quantity",
            "Обсяг із невизначеним гібридом",
            unknown,
            extra_unknown_count=extra_unknown_count,
        ),
        coverage,
    )


def _field_cells(
    lines: tuple[ProductLine, ...], field_name: str
) -> tuple[SourceCell, ...]:
    result: list[SourceCell] = []
    seen: set[str] = set()
    for line in lines:
        for source in line.sources:
            try:
                cell = source.cell(field_name)
            except KeyError:
                continue
            if cell.stable_key not in seen:
                seen.add(cell.stable_key)
                result.append(cell)
    return tuple(result)


def _unique_sorted(values) -> tuple:
    return tuple(sorted(set(values)))


def build_clients(
    lines: tuple[ProductLine, ...],
    vouchers_by_key: Mapping[str, Voucher],
) -> tuple[tuple[Client, ...], tuple[Issue, ...]]:
    names_by_tax: dict[str, set[str]] = defaultdict(set)
    line_keys_by_tax: dict[str, list[str]] = defaultdict(list)
    issues: list[Issue] = []
    for line in lines:
        if line.tax_id is None:
            sources = _field_cells((line,), "Tax ID 1") or tuple(
                source.cells[0] for source in line.sources if source.cells
            )
            issues.append(
                make_issue(
                    IssueCode.CLIENT_UNKNOWN,
                    "Товарний рядок основного ваучера не містить Tax ID; "
                    "обсяг збережено, ідентифікація клієнта неповна.",
                    sources=sources,
                    affected_keys=(line.key,),
                    discriminator=line.key,
                )
            )
            continue
        line_keys_by_tax[line.tax_id].append(line.key)
        if (name := text_or_none(line.account_name)) is not None:
            names_by_tax[line.tax_id].add(name)

    lines_by_voucher: dict[str, list[ProductLine]] = defaultdict(list)
    for line in lines:
        if line.voucher_key is not None:
            lines_by_voucher[line.voucher_key].append(line)
    for voucher_key, voucher_lines in lines_by_voucher.items():
        tax_ids = _unique_sorted(
            line.tax_id for line in voucher_lines if line.tax_id is not None
        )
        if len(tax_ids) < 2:
            continue
        voucher = vouchers_by_key[voucher_key]
        issues.append(
            make_issue(
                IssueCode.MULTIPLE_TAX_IDS,
                "Один ваучер містить товарні рядки кількох Tax ID; "
                "клієнтські обсяги розподілено за власними рядками.",
                sources=voucher.sources + _field_cells(tuple(voucher_lines), "Tax ID 1"),
                affected_keys=(voucher_key,),
                details=(("tax_ids", "; ".join(tax_ids)),),
                discriminator=voucher_key,
            )
        )

    clients = tuple(
        Client(
            tax_id=tax_id,
            names=tuple(sorted(names_by_tax[tax_id])),
            product_line_keys=tuple(sorted(line_keys)),
        )
        for tax_id, line_keys in sorted(line_keys_by_tax.items())
    )
    return clients, tuple(issues)


def _product_facts(
    lines: tuple[ProductLine, ...],
    *,
    voucher_key: str,
    reference_date,
) -> tuple[ProductLineFact, ...]:
    return tuple(
        ProductLineFact(
            product_line_key=line.key,
            voucher_key=voucher_key,
            section=ProductSection.MAIN,
            quantity=line.quantity,
            crop=crop_category(line.species_group),
            hybrid=line.hybrid,
            tax_id=line.tax_id,
            created_on=calendar_date_in_kyiv(line.created_at),
            reference_date=reference_date,
            time_bucket=time_bucket(
                calendar_date_in_kyiv(line.created_at), reference_date
            ),
            local_description=line.local_description,
        )
        for line in lines
    )


def build_main_aggregation(
    *,
    accepted_links: tuple[AcceptedLink, ...],
    vouchers: tuple[Voucher, ...],
    product_lines: tuple[ProductLine, ...],
    participant_links: tuple[ParticipantLink, ...],
    lead_dates: tuple[LeadDateResolution, ...],
    unknown_groups_by_voucher: Mapping[str, int],
) -> MainAggregation:
    line_by_key = {line.key: line for line in product_lines}
    voucher_by_key = {voucher.key: voucher for voucher in vouchers}
    lead_date_by_key = {item.lead_ref_key: item for item in lead_dates}
    participant_by_lead = {
        link.subject_key: link.participant_key
        for link in participant_links
        if link.subject_kind is ParticipantLinkSubject.LEAD_REF
    }
    links_by_voucher: dict[str, list[AcceptedLink]] = defaultdict(list)
    for link in accepted_links:
        links_by_voucher[link.voucher_key].append(link)

    main_facts: list[ProductLineFact] = []
    main_lines: list[ProductLine] = []
    voucher_summaries: list[VoucherSummary] = []
    for voucher_key in sorted(links_by_voucher):
        voucher = voucher_by_key[voucher_key]
        lines = tuple(
            line_by_key[key]
            for key in voucher.product_line_keys
            if key in line_by_key
        )
        main_lines.extend(lines)
        lead_ref_keys = _unique_sorted(
            link.lead_ref_key for link in links_by_voucher[voucher_key]
        )
        known_lead_dates = tuple(
            resolution.value
            for lead_ref_key in lead_ref_keys
            if (resolution := lead_date_by_key.get(lead_ref_key)) is not None
            and resolution.value is not None
        )
        reference_date = min(known_lead_dates) if known_lead_dates else None
        facts = _product_facts(
            lines, voucher_key=voucher_key, reference_date=reference_date
        )
        main_facts.extend(facts)
        unknown_groups = unknown_groups_by_voucher.get(voucher_key, 0)
        prefix = f"voucher.{voucher_key}"
        voucher_summaries.append(
            VoucherSummary(
                voucher_key=voucher_key,
                lead_ref_keys=lead_ref_keys,
                product_line_keys=tuple(line.key for line in lines),
                reference_date=reference_date,
                missing_lead_date_count=sum(
                    lead_date_by_key.get(key) is None
                    or lead_date_by_key[key].value is None
                    for key in lead_ref_keys
                ),
                quantity=measure_for_lines(
                    f"{prefix}.quantity",
                    "Обсяг ваучера",
                    lines,
                    extra_unknown_count=unknown_groups,
                ),
                crop_measures=crop_measures(
                    lines,
                    prefix=prefix,
                    extra_unknown_count=unknown_groups,
                ),
                time_measures=time_measures(
                    facts,
                    prefix=prefix,
                    extra_unknown_count=unknown_groups,
                ),
                hybrid_summaries=hybrid_summaries(
                    lines,
                    prefix=prefix,
                    extra_unknown_count=unknown_groups,
                ),
            )
        )

    link_time_facts: list[LinkProductTimeFact] = []
    link_summaries: list[LinkSummary] = []
    for link in sorted(accepted_links, key=lambda item: item.key):
        voucher = voucher_by_key[link.voucher_key]
        lines = tuple(
            line_by_key[key]
            for key in voucher.product_line_keys
            if key in line_by_key
        )
        lead_date = lead_date_by_key.get(link.lead_ref_key)
        reference_date = lead_date.value if lead_date is not None else None
        personal_facts = _product_facts(
            lines,
            voucher_key=link.voucher_key,
            reference_date=reference_date,
        )
        link_time_facts.extend(
            LinkProductTimeFact(
                link_key=link.key,
                product_line_key=fact.product_line_key,
                lead_ref_key=link.lead_ref_key,
                lead_date=reference_date,
                time_bucket=fact.time_bucket,
            )
            for fact in personal_facts
        )
        unknown_groups = unknown_groups_by_voucher.get(link.voucher_key, 0)
        prefix = f"link.{link.key}"
        link_summaries.append(
            LinkSummary(
                link_key=link.key,
                lead_ref_key=link.lead_ref_key,
                voucher_key=link.voucher_key,
                participant_key=participant_by_lead.get(link.lead_ref_key),
                product_line_keys=tuple(line.key for line in lines),
                tax_ids=_unique_sorted(
                    line.tax_id for line in lines if line.tax_id is not None
                ),
                client_names=_unique_sorted(
                    name
                    for line in lines
                    if (name := text_or_none(line.account_name)) is not None
                ),
                quantity=measure_for_lines(
                    f"{prefix}.quantity",
                    "Обсяг пари лід–ваучер",
                    lines,
                    extra_unknown_count=unknown_groups,
                ),
                crop_measures=crop_measures(
                    lines,
                    prefix=prefix,
                    extra_unknown_count=unknown_groups,
                ),
                time_measures=time_measures(
                    personal_facts,
                    prefix=prefix,
                    extra_unknown_count=unknown_groups,
                ),
                hybrid_summaries=hybrid_summaries(
                    lines,
                    prefix=prefix,
                    extra_unknown_count=unknown_groups,
                ),
            )
        )
    main_lines_tuple = tuple(main_lines)
    return MainAggregation(
        product_facts=tuple(main_facts),
        link_product_times=tuple(link_time_facts),
        link_summaries=tuple(link_summaries),
        voucher_summaries=tuple(voucher_summaries),
        hybrid_summaries=hybrid_summaries(
            main_lines_tuple,
            prefix="main",
            extra_unknown_count=sum(
                unknown_groups_by_voucher.get(key, 0) for key in links_by_voucher
            ),
        ),
    )


def build_report_measures(
    *,
    snapshot: InputSnapshot,
    product_lines: tuple[ProductLine, ...],
    main_facts: tuple[ProductLineFact, ...],
    clients: tuple[Client, ...],
    voucher_summaries: tuple[VoucherSummary, ...],
    funnel_rows: tuple[FunnelRow, ...],
    other_facts: tuple[ProductLineFact, ...],
    participant_links: tuple[ParticipantLink, ...],
    accepted_links: tuple[AcceptedLink, ...],
    survey_count: int,
    activity_count: int,
    uncertainty_count_by_role: Mapping[InputRole, int],
    unknown_groups_by_voucher: Mapping[str, int],
    issues: tuple[Issue, ...],
) -> tuple[Measure, ...]:
    """Build full-report measures without depending on application state."""

    main_voucher_keys = {summary.voucher_key for summary in voucher_summaries}
    line_by_key = {line.key: line for line in product_lines}
    main_lines = tuple(line_by_key[fact.product_line_key] for fact in main_facts)
    extra_unknown = sum(
        unknown_groups_by_voucher.get(key, 0) for key in main_voucher_keys
    )
    measures: list[Measure] = [
        Measure.complete(
            "main.vouchers",
            "Основні ваучери",
            "ваучерів",
            len(voucher_summaries),
        ),
        Measure.complete(
            "main.clients",
            "Клієнти за Tax ID",
            "клієнтів",
            len(clients),
        ),
        measure_for_lines(
            "main.quantity",
            "Обсяг Seed Selector",
            main_lines,
            extra_unknown_count=extra_unknown,
        ),
    ]
    measures.extend(
        crop_measures(
            main_lines,
            prefix="main",
            extra_unknown_count=extra_unknown,
        )
    )
    measures.extend(
        time_measures(
            main_facts,
            prefix="main",
            extra_unknown_count=extra_unknown,
        )
    )
    measures.extend(
        hybrid_quality_measures(
            main_lines,
            prefix="main",
            extra_unknown_count=extra_unknown,
        )
    )
    measures.extend(
        funnel_measures(
            funnel_rows,
            unknown_base_count=uncertainty_count_by_role.get(InputRole.R3, 0),
        )
    )

    for role, count, label in (
        (InputRole.R1, survey_count, "Унікальні опитування"),
        (InputRole.R4, activity_count, "Унікальні активності"),
    ):
        unknown = uncertainty_count_by_role.get(role, 0)
        key = "events.surveys" if role is InputRole.R1 else "events.activities"
        if unknown:
            measures.append(
                Measure.partial(
                    key,
                    label,
                    "подій",
                    count,
                    unknown,
                    ("Є групи джерельних записів із невизначеним обліком.",),
                )
            )
        else:
            measures.append(Measure.complete(key, label, "подій", count))
    for role in InputRole:
        measures.append(
            Measure.complete(
                f"source.{role.value.lower()}.rows",
                f"Рядки джерела {role.value}",
                "рядків",
                snapshot.source(role).row_count,
            )
        )
    measures.append(
        Measure.complete(
            "events.unlinked_people",
            "Незв'язані записи людей/активностей",
            "записів",
            sum(link.participant_key is None for link in participant_links),
        )
    )
    measures.extend(
        (
            Measure.complete(
                "other.vouchers",
                "Інші ваучери клієнтів",
                "ваучерів",
                len({fact.voucher_key for fact in other_facts}),
            ),
            quantity_measure(
                "other.quantity",
                "Обсяг інших ваучерів клієнтів",
                (fact.quantity for fact in other_facts),
            ),
            Measure.complete(
                "quality.unresolved_issues",
                "Невирішені проблеми",
                "проблем",
                sum(not issue.is_resolved for issue in issues),
            ),
        )
    )
    for kind, suffix, label in (
        (ParticipantLinkSubject.LEAD_REF, "lead_refs", "LeadRef, пов'язані з R3"),
        (
            ParticipantLinkSubject.ACTIVITY,
            "activities",
            "Активності, пов'язані з R3",
        ),
    ):
        links = tuple(link for link in participant_links if link.subject_kind is kind)
        resolved = sum(link.participant_key is not None for link in links)
        measures.append(
            Measure.complete(
                f"quality.{suffix}.linked",
                label,
                "записів",
                resolved,
            )
        )
        if links:
            measures.append(
                Measure.complete(
                    f"quality.{suffix}.linked_rate",
                    f"{label}, частка",
                    "%",
                    Decimal(resolved) * Decimal(100) / Decimal(len(links)),
                )
            )
        else:
            measures.append(
                Measure.unavailable(
                    f"quality.{suffix}.linked_rate",
                    f"{label}, частка",
                    "%",
                    ("Відповідних записів немає.",),
                )
            )
    accepted_mentions = {key for link in accepted_links for key in link.mention_keys}
    measures.append(
        Measure.complete(
            "quality.mentions.accepted",
            "Згадки у прийнятих зв'язках",
            "згадок",
            len(accepted_mentions),
        )
    )
    return tuple(measures)
