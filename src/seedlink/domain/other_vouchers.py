"""Eligibility and fixed-date attribution for the additional voucher section."""

from __future__ import annotations

from collections import defaultdict
from typing import cast

from seedlink.domain._issue_factory import make_issue
from seedlink.domain.date_attribution import time_bucket
from seedlink.domain.dates import calendar_date_in_kyiv
from seedlink.domain.issues import Issue, IssueCode
from seedlink.domain.models import (
    AcceptedLink,
    EligibleClient,
    LeadDateResolution,
    Participant,
    ParticipantLink,
    ParticipantLinkSubject,
    ProductLine,
    ProductLineFact,
    ProductSection,
)
from seedlink.domain.normalization import normalize_name
from seedlink.domain.product_metrics import crop_category
from seedlink.domain.provenance import SourceCell


def _unique_sorted(values) -> tuple:
    return tuple(sorted(set(values)))


def _member_type_cells(participant: Participant) -> tuple[SourceCell, ...]:
    result: list[SourceCell] = []
    for source in participant.sources:
        try:
            result.append(source.cell("Member Type"))
        except KeyError:
            continue
    return tuple(result)


def build_other_vouchers(
    *,
    accepted_links: tuple[AcceptedLink, ...],
    product_lines: tuple[ProductLine, ...],
    participants: tuple[Participant, ...],
    participant_links: tuple[ParticipantLink, ...],
    main_facts: tuple[ProductLineFact, ...],
    lead_dates: tuple[LeadDateResolution, ...],
) -> tuple[
    tuple[EligibleClient, ...],
    tuple[ProductLineFact, ...],
    tuple[Issue, ...],
]:
    """Build a reference-only set without ever expanding the main volume."""

    main_voucher_keys = {link.voucher_key for link in accepted_links}
    participant_by_key = {item.key: item for item in participants}
    participant_by_lead = {
        link.subject_key: link.participant_key
        for link in participant_links
        if link.subject_kind is ParticipantLinkSubject.LEAD_REF
    }
    date_by_lead = {item.lead_ref_key: item.value for item in lead_dates}
    main_tax_by_voucher: dict[str, set[str]] = defaultdict(set)
    for fact in main_facts:
        if fact.tax_id is not None:
            main_tax_by_voucher[fact.voucher_key].add(fact.tax_id)

    qualifiers: dict[str, list[tuple[str, str]]] = defaultdict(list)
    issues: list[Issue] = []
    for link in accepted_links:
        tax_ids = main_tax_by_voucher.get(link.voucher_key, set())
        if not tax_ids:
            # CLIENT_UNKNOWN already describes the missing Tax ID. With no
            # client key there is no candidate for the additional section.
            continue
        participant_key = participant_by_lead.get(link.lead_ref_key)
        participant = participant_by_key.get(participant_key or "")
        if participant is None:
            issues.append(
                make_issue(
                    IssueCode.OTHER_VOUCHER_ELIGIBILITY_UNKNOWN,
                    "Для основного ваучера не підтверджено учасника R3; "
                    "клієнта не допущено до аналізу інших ваучерів.",
                    sources=link.sources,
                    affected_keys=(link.key, link.lead_ref_key),
                    discriminator=link.key,
                )
            )
            continue
        member_type = normalize_name(participant.member_type)
        if member_type == "lead":
            for tax_id in tax_ids:
                qualifiers[tax_id].append((link.lead_ref_key, participant.key))
        elif member_type != "contact":
            issues.append(
                make_issue(
                    IssueCode.OTHER_VOUCHER_ELIGIBILITY_UNKNOWN,
                    "Тип пов'язаного учасника R3 невідомий; клієнта не "
                    "допущено до аналізу інших ваучерів.",
                    sources=_member_type_cells(participant) or link.sources,
                    affected_keys=(link.key, participant.key),
                    discriminator=link.key,
                )
            )

    other_lines_by_tax: dict[str, list[ProductLine]] = defaultdict(list)
    for line in product_lines:
        if (
            line.tax_id in qualifiers
            and line.voucher_key is not None
            and line.voucher_key not in main_voucher_keys
        ):
            other_lines_by_tax[line.tax_id].append(line)

    clients: list[EligibleClient] = []
    other_facts: list[ProductLineFact] = []
    for tax_id in sorted(qualifiers):
        lines = tuple(
            sorted(other_lines_by_tax.get(tax_id, ()), key=lambda item: item.key)
        )
        if not lines:
            continue
        qualifying_pairs = tuple(dict.fromkeys(qualifiers[tax_id]))
        lead_ref_keys = _unique_sorted(item[0] for item in qualifying_pairs)
        participant_keys = _unique_sorted(item[1] for item in qualifying_pairs)
        known_dates = tuple(
            date_by_lead[key]
            for key in lead_ref_keys
            if date_by_lead.get(key) is not None
        )
        reference_date = min(known_dates) if known_dates else None
        facts = tuple(
            ProductLineFact(
                product_line_key=line.key,
                voucher_key=cast(str, line.voucher_key),
                section=ProductSection.OTHER,
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
        other_facts.extend(facts)
        clients.append(
            EligibleClient(
                tax_id=tax_id,
                main_voucher_keys=_unique_sorted(
                    fact.voucher_key
                    for fact in main_facts
                    if fact.tax_id == tax_id
                ),
                lead_ref_keys=lead_ref_keys,
                participant_keys=participant_keys,
                reference_date=reference_date,
                other_product_line_keys=tuple(fact.product_line_key for fact in facts),
            )
        )
    return tuple(clients), tuple(other_facts), tuple(issues)
