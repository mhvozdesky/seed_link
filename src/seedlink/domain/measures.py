"""Numerical result with explicit completeness semantics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class MeasureStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Measure:
    key: str
    label_uk: str
    unit: str
    known_value: Decimal | None
    status: MeasureStatus
    unknown_count: int = 0
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.label_uk.strip() or not self.unit.strip():
            raise ValueError("measure key, label and unit must not be blank")
        if self.known_value is not None and not self.known_value.is_finite():
            raise ValueError("known_value must be finite")
        if self.unknown_count < 0:
            raise ValueError("unknown_count must not be negative")
        if self.status is MeasureStatus.COMPLETE:
            if self.known_value is None or self.unknown_count or self.reasons:
                raise ValueError("complete measure must have a value and no unknowns")
        elif self.status is MeasureStatus.PARTIAL:
            if self.known_value is None or self.unknown_count < 1 or not self.reasons:
                raise ValueError("partial measure needs a known part and unknown reasons")
        elif self.status is MeasureStatus.UNAVAILABLE:
            if self.known_value is not None or not self.reasons:
                raise ValueError("unavailable measure needs reasons and no value")

    @classmethod
    def complete(
        cls, key: str, label_uk: str, unit: str, value: Decimal | int | str
    ) -> Measure:
        return cls(key, label_uk, unit, Decimal(value), MeasureStatus.COMPLETE)

    @classmethod
    def partial(
        cls,
        key: str,
        label_uk: str,
        unit: str,
        known_value: Decimal | int | str,
        unknown_count: int,
        reasons: tuple[str, ...],
    ) -> Measure:
        return cls(
            key,
            label_uk,
            unit,
            Decimal(known_value),
            MeasureStatus.PARTIAL,
            unknown_count,
            reasons,
        )

    @classmethod
    def unavailable(
        cls, key: str, label_uk: str, unit: str, reasons: tuple[str, ...]
    ) -> Measure:
        return cls(
            key,
            label_uk,
            unit,
            None,
            MeasureStatus.UNAVAILABLE,
            0,
            reasons,
        )
