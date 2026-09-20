"""Immutable source references used by every imported fact and issue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import re
from typing import TypeAlias


class InputRole(StrEnum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"

    @property
    def label_uk(self) -> str:
        return {
            InputRole.R1: "R1 — Опитування",
            InputRole.R2: "R2 — Ваучери",
            InputRole.R3: "R3 — Учасники кампанії",
            InputRole.R4: "R4 — Активності",
        }[self]


RawValue: TypeAlias = (
    str | int | float | bool | Decimal | date | datetime | time | timedelta | None
)


class SourceValueKind(StrEnum):
    """Workbook scalar kind captured without evaluating source formulas."""

    BLANK = "blank"
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    DURATION = "duration"
    FORMULA = "formula"
    ERROR = "error"
    UNKNOWN = "unknown"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _required(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _validate_sha256(value: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("file_sha256 must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class SourceCell:
    """One original workbook cell with a stable key inside a snapshot."""

    role: InputRole
    file_name: str
    file_sha256: str
    sheet_name: str
    excel_row: int
    field_name: str
    original_value: RawValue
    value_kind: SourceValueKind | None = None
    hyperlink_target: str | None = None

    def __post_init__(self) -> None:
        _required(self.file_name, "file_name")
        _validate_sha256(self.file_sha256)
        _required(self.sheet_name, "sheet_name")
        if self.excel_row < 2:
            raise ValueError("data cell excel_row must be at least 2")
        _required(self.field_name, "field_name")
        if self.value_kind is None:
            object.__setattr__(
                self, "value_kind", _infer_value_kind(self.original_value)
            )
        elif not isinstance(self.value_kind, SourceValueKind):
            raise ValueError("value_kind must be a SourceValueKind")
        if self.hyperlink_target is not None and not self.hyperlink_target.strip():
            raise ValueError("hyperlink_target cannot be blank")

    @property
    def stable_key(self) -> str:
        identity = "\x1f".join(
            (
                self.role.value,
                self.file_sha256,
                self.sheet_name,
                str(self.excel_row),
                self.field_name,
            )
        )
        return f"cell:{sha256(identity.encode('utf-8')).hexdigest()}"


def _infer_value_kind(value: RawValue) -> SourceValueKind:
    if value is None:
        return SourceValueKind.BLANK
    if isinstance(value, bool):
        return SourceValueKind.BOOLEAN
    if isinstance(value, datetime):
        return SourceValueKind.DATETIME
    if isinstance(value, date):
        return SourceValueKind.DATE
    if isinstance(value, time):
        return SourceValueKind.TIME
    if isinstance(value, timedelta):
        return SourceValueKind.DURATION
    if isinstance(value, str):
        return SourceValueKind.TEXT
    if isinstance(value, (int, float, Decimal)):
        return SourceValueKind.NUMBER
    return SourceValueKind.UNKNOWN


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """All captured cells from one physical source row."""

    key: str
    role: InputRole
    file_name: str
    file_sha256: str
    sheet_name: str
    excel_row: int
    cells: tuple[SourceCell, ...]

    def __post_init__(self) -> None:
        _required(self.key, "key")
        _required(self.file_name, "file_name")
        _validate_sha256(self.file_sha256)
        _required(self.sheet_name, "sheet_name")
        if self.excel_row < 2:
            raise ValueError("data record excel_row must be at least 2")
        names: set[str] = set()
        for cell in self.cells:
            location = (
                cell.role,
                cell.file_name,
                cell.file_sha256,
                cell.sheet_name,
                cell.excel_row,
            )
            expected = (
                self.role,
                self.file_name,
                self.file_sha256,
                self.sheet_name,
                self.excel_row,
            )
            if location != expected:
                raise ValueError("record cells must point to the same source row")
            if cell.field_name in names:
                raise ValueError(f"duplicate field in source record: {cell.field_name}")
            names.add(cell.field_name)

    def value(self, field_name: str) -> RawValue:
        return self.cell(field_name).original_value

    def cell(self, field_name: str) -> SourceCell:
        for cell in self.cells:
            if cell.field_name == field_name:
                return cell
        raise KeyError(field_name)


@dataclass(frozen=True, slots=True)
class InputSource:
    """Metadata and immutable raw rows for one accepted workbook."""

    role: InputRole
    file_name: str
    file_sha256: str
    sheet_name: str
    schema_version: str
    row_count: int
    records: tuple[SourceRecord, ...] = ()

    def __post_init__(self) -> None:
        _required(self.file_name, "file_name")
        _validate_sha256(self.file_sha256)
        _required(self.sheet_name, "sheet_name")
        _required(self.schema_version, "schema_version")
        if self.row_count < 0:
            raise ValueError("row_count must not be negative")
        if self.row_count != len(self.records):
            raise ValueError("row_count must equal the number of captured records")
        if any(record.role is not self.role for record in self.records):
            raise ValueError("all records must have the source role")


@dataclass(frozen=True, slots=True)
class InputSnapshot:
    """A set of immutable source workbooks loaded for one calculation."""

    snapshot_id: str
    program_version: str
    loaded_at: datetime
    sources: tuple[InputSource, ...]

    def __post_init__(self) -> None:
        _required(self.snapshot_id, "snapshot_id")
        _required(self.program_version, "program_version")
        if self.loaded_at.tzinfo is None or self.loaded_at.utcoffset() is None:
            raise ValueError("loaded_at must be timezone-aware")
        roles = [source.role for source in self.sources]
        if len(roles) != len(set(roles)):
            raise ValueError("snapshot must not contain duplicate roles")

    @property
    def is_complete(self) -> bool:
        return {source.role for source in self.sources} == set(InputRole)

    def require_complete(self) -> None:
        missing = sorted(set(InputRole) - {source.role for source in self.sources})
        if missing:
            labels = ", ".join(role.value for role in missing)
            raise ValueError(f"snapshot is missing roles: {labels}")

    def source(self, role: InputRole) -> InputSource:
        for source in self.sources:
            if source.role is role:
                return source
        raise KeyError(role)
