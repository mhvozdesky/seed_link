"""Narrow scalar parsers shared by the four role readers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from seedlink.domain.dates import DateNormalizationError, datetime_in_kyiv
from seedlink.domain.issues import IssueCode
from seedlink.domain.normalization import (
    DecimalNormalizationError,
    decimal_from_value,
    normalize_identifier,
    text_or_none,
)
from seedlink.domain.provenance import SourceCell, SourceValueKind


@dataclass(frozen=True, slots=True)
class ParseProblem:
    code: IssueCode
    message_uk: str
    cell: SourceCell


def text_value(cell: SourceCell) -> tuple[str | None, tuple[ParseProblem, ...]]:
    if cell.value_kind is SourceValueKind.BLANK:
        return None, ()
    if cell.value_kind in {SourceValueKind.FORMULA, SourceValueKind.ERROR}:
        return None, ()
    if cell.value_kind is not SourceValueKind.TEXT:
        return None, (
            ParseProblem(
                IssueCode.UNSUPPORTED_CELL_VALUE,
                f"Поле «{cell.field_name}» має бути текстом.",
                cell,
            ),
        )
    return text_or_none(cell.original_value), ()


def identifier_value(
    cell: SourceCell,
) -> tuple[str | None, tuple[ParseProblem, ...]]:
    """Read an ID while making numeric precision/leading-zero risk explicit."""

    if cell.value_kind is SourceValueKind.BLANK:
        return None, ()
    if cell.value_kind in {SourceValueKind.FORMULA, SourceValueKind.ERROR}:
        return None, ()
    if cell.value_kind is SourceValueKind.NUMBER:
        raw = cell.original_value
        numeric = Decimal(str(raw))
        if numeric.is_finite() and numeric == numeric.to_integral_value():
            value = normalize_identifier(numeric)
            return value, (
                ParseProblem(
                    IssueCode.NUMERIC_ID_RISK,
                    f"Поле «{cell.field_name}» прочитано як числовий ID. "
                    "Початкові нулі або точність могли бути втрачені в Excel.",
                    cell,
                ),
            )
        return None, (
            ParseProblem(
                IssueCode.UNSUPPORTED_CELL_VALUE,
                f"Поле «{cell.field_name}» містить нецілий числовий ID.",
                cell,
            ),
        )
    value, problems = text_value(cell)
    if problems or value is None:
        return value, problems
    try:
        return normalize_identifier(value), ()
    except ValueError:
        return None, (
            ParseProblem(
                IssueCode.UNSUPPORTED_CELL_VALUE,
                f"Поле «{cell.field_name}» містить "
                "непідтримуваний ідентифікатор.",
                cell,
            ),
        )


def datetime_value(
    cell: SourceCell,
) -> tuple[datetime | None, tuple[ParseProblem, ...]]:
    if cell.value_kind is SourceValueKind.BLANK:
        return None, ()
    if cell.value_kind in {SourceValueKind.FORMULA, SourceValueKind.ERROR}:
        return None, ()
    try:
        return datetime_in_kyiv(cell.original_value), ()
    except DateNormalizationError:
        return None, (
            ParseProblem(
                IssueCode.DATE_INVALID,
                f"Поле «{cell.field_name}» містить некоректну дату.",
                cell,
            ),
        )


def decimal_value(
    cell: SourceCell,
) -> tuple[Decimal | None, tuple[ParseProblem, ...]]:
    if cell.value_kind in {SourceValueKind.FORMULA, SourceValueKind.ERROR}:
        return None, ()
    if cell.value_kind is SourceValueKind.BLANK:
        return None, (
            ParseProblem(
                IssueCode.QUANTITY_UNKNOWN,
                f"Поле «{cell.field_name}» порожнє; "
                "внесок кількості невідомий.",
                cell,
            ),
        )
    try:
        return decimal_from_value(cell.original_value), ()
    except DecimalNormalizationError:
        return None, (
            ParseProblem(
                IssueCode.QUANTITY_UNKNOWN,
                f"Поле «{cell.field_name}» не містить однозначної "
                "невід'ємної кількості.",
                cell,
            ),
        )


def collect(*problem_groups: tuple[ParseProblem, ...]) -> tuple[ParseProblem, ...]:
    return tuple(problem for group in problem_groups for problem in group)
