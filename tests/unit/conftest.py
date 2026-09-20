from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from seedlink.domain.provenance import InputRole, SourceCell, SourceRecord


FILE_SHA = "a" * 64


@pytest.fixture
def source_cell_factory():
    def factory(
        role: InputRole = InputRole.R1,
        *,
        row: int = 2,
        field: str = "Field",
        value: str | int | Decimal | datetime | None = "value",
    ) -> SourceCell:
        return SourceCell(
            role=role,
            file_name=f"{role.value}.xlsx",
            file_sha256=FILE_SHA,
            sheet_name=role.value,
            excel_row=row,
            field_name=field,
            original_value=value,
        )

    return factory


@pytest.fixture
def source_record_factory(source_cell_factory):
    def factory(
        role: InputRole = InputRole.R1,
        *,
        row: int = 2,
        field: str = "Field",
        value: str = "value",
    ) -> SourceRecord:
        cell = source_cell_factory(role, row=row, field=field, value=value)
        return SourceRecord(
            key=f"{role.value}:{row}",
            role=role,
            file_name=cell.file_name,
            file_sha256=cell.file_sha256,
            sheet_name=cell.sheet_name,
            excel_row=row,
            cells=(cell,),
        )

    return factory
