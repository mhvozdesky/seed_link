"""Typed values read from the R2 voucher product export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from seedlink.domain.provenance import SourceRecord
from seedlink.input_xlsx._parsing import (
    ParseProblem,
    collect,
    datetime_value,
    decimal_value,
    identifier_value,
    text_value,
)


@dataclass(frozen=True, slots=True)
class R2Record:
    source: SourceRecord
    opportunity_id: str | None
    product_id: str | None
    tax_id: str | None
    account_name: str | None
    parent_account_id: str | None
    voucher_number: str | None
    local_description: str | None
    hybrid: str | None
    species_group: str | None
    quantity: Decimal | None
    product_created_at: datetime | None
    created_at: datetime | None


def parse_record(record: SourceRecord) -> tuple[R2Record, tuple[ParseProblem, ...]]:
    opportunity_id, p_opportunity = identifier_value(record.cell("Opportunity ID"))
    product_id, p_product = identifier_value(
        record.cell("Opportunity Product Id 18")
    )
    tax_id, p_tax = identifier_value(record.cell("Tax ID 1"))
    account_name, p_account = text_value(record.cell("Account Name"))
    parent_id, p_parent = identifier_value(record.cell("Parent Account ID"))
    voucher, p_voucher = text_value(record.cell("Voucher Number"))
    local_description, p_local = text_value(record.cell("Local Description"))
    hybrid, p_hybrid = text_value(record.cell("Parent Product Local Description"))
    species, p_species = text_value(record.cell("Species group"))
    quantity, p_quantity = decimal_value(record.cell("Current Year Planned Quantity"))
    created_at, p_created = datetime_value(
        record.cell("Opportunity Product: Created Date")
    )
    source_created_at, p_source_created = datetime_value(record.cell("Created Date"))
    parsed = R2Record(
        source=record,
        opportunity_id=opportunity_id,
        product_id=product_id,
        tax_id=tax_id,
        account_name=account_name,
        parent_account_id=parent_id,
        voucher_number=voucher,
        local_description=local_description,
        hybrid=hybrid,
        species_group=species,
        quantity=quantity,
        product_created_at=created_at,
        created_at=source_created_at,
    )
    return parsed, collect(
        p_opportunity,
        p_product,
        p_tax,
        p_account,
        p_parent,
        p_voucher,
        p_local,
        p_hybrid,
        p_species,
        p_quantity,
        p_created,
        p_source_created,
    )
