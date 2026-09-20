"""Versioned, exact workbook schemas agreed for the first supported exports."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from seedlink.domain.provenance import InputRole


@dataclass(frozen=True, slots=True)
class SchemaDefinition:
    role: InputRole
    version: str
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("schema version must not be blank")
        if not self.columns or any(not column for column in self.columns):
            raise ValueError("schema columns must be non-empty")
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("schema columns must be unique")


R1_COLUMNS = (
    "Record Nr",
    "Created Date",
    "Created By: Full Name",
    "Campaigm Member",
    "Lead: Full Name",
    "Account: 18-digits ID",
    "Account: Tax ID 1",
    "Account: Account Name",
    "Account: Billing Location",
    "First Name",
    "Last Name",
    "Mobile",
    "Time Taken",
    "Email",
    "Survey Response ID",
    "Question",
    "Answer",
    "Correct Answer",
    "Answer (Long Text)",
    "City",
    "Address",
)

R2_COLUMNS = (
    "Opportunity ID",
    "Opportunity Product Id 18",
    "Tax ID 1",
    "Account Name",
    "Parent Account",
    "Parent Account ID",
    "Data Source",
    "INTL Account Name",
    "Seeds KAM",
    "Next Step",
    "Voucher Number",
    "Local Description",
    "Parent Product Local Description",
    "Species group",
    "Current Year Planned Quantity",
    "Planned Quantity for IBP Integration",
    "Opportunity Product: Created Date",
    "Status",
    "Loyalty Comment",
    "Supplier",
    "Line Description",
    "Billing State/Province",
    "Billing Location",
    "Opportunity Owner: Manager",
    "Supplied from Channel Stock",
    "Created Date",
)

R3_COLUMNS = (
    "Member Status",
    "Member Type",
    "First Name",
    "Last Name",
    "Email",
    "Campaign Member Id 18",
    "Member First Associated Date",
)

R4_COLUMNS = (
    "Completed Date/Time",
    "Account ID",
    "Activity ID",
    "Company / Account",
    "Created Date",
    "Due Date",
    "Name",
    "Subject",
    "Activity Type",
    "Results",
    "Objective / Initiative",
    "Notes",
    "Assigned",
    "Related To",
)

_SCHEMAS = {
    InputRole.R1: SchemaDefinition(InputRole.R1, "1", R1_COLUMNS),
    InputRole.R2: SchemaDefinition(InputRole.R2, "1", R2_COLUMNS),
    InputRole.R3: SchemaDefinition(InputRole.R3, "1", R3_COLUMNS),
    InputRole.R4: SchemaDefinition(InputRole.R4, "1", R4_COLUMNS),
}

SCHEMAS: Mapping[InputRole, SchemaDefinition] = MappingProxyType(_SCHEMAS)


def schema_for(role: InputRole) -> SchemaDefinition:
    return SCHEMAS[role]
