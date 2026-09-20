from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook

from seedlink.domain.provenance import InputRole, SourceCell, SourceRecord
from seedlink.input_xlsx.schemas import schema_for


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


@pytest.fixture
def workbook_set_factory(tmp_path):
    def factory(
        rows_by_role: dict[InputRole, list[dict[str, object]]],
    ) -> dict[InputRole, object]:
        paths = {}
        for role in InputRole:
            path = tmp_path / f"{role.value}.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = role.value
            columns = schema_for(role).columns
            worksheet.append(columns)
            for supplied in rows_by_role.get(role, []):
                worksheet.append([supplied.get(column) for column in columns])
            workbook.save(path)
            workbook.close()
            paths[role] = path
        return paths

    return factory
