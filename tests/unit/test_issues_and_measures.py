from __future__ import annotations

from decimal import Decimal

import pytest

from seedlink.domain.issues import ISSUE_LEVEL_BY_CODE, Issue, IssueCode, IssueLevel
from seedlink.domain.measures import Measure, MeasureStatus


def test_partial_measure_keeps_known_zero_distinct_from_unknown():
    measure = Measure.partial(
        "quantity",
        "Кількість",
        "од.",
        Decimal("0"),
        2,
        ("QUANTITY_UNKNOWN",),
    )

    assert measure.known_value == 0
    assert measure.status is MeasureStatus.PARTIAL
    assert measure.unknown_count == 2


def test_unavailable_percentage_has_no_synthetic_zero():
    measure = Measure.unavailable(
        "funnel_rate",
        "Частка воронки",
        "%",
        ("Порожня база учасників",),
    )

    assert measure.known_value is None
    assert measure.status is MeasureStatus.UNAVAILABLE


def test_complete_measure_cannot_hide_unknown_components():
    with pytest.raises(ValueError, match="no unknowns"):
        Measure(
            "quantity",
            "Кількість",
            "од.",
            Decimal("10"),
            MeasureStatus.COMPLETE,
            unknown_count=1,
        )


def test_record_issue_requires_provenance(source_cell_factory):
    issue = Issue(
        issue_id="I-1",
        code=IssueCode.VOUCHER_NOT_FOUND,
        level=IssueLevel.RECORD,
        message_uk="Ваучер не знайдено",
        sources=(source_cell_factory(field="Correct Answer"),),
        affected_keys=("S-1",),
    )
    assert not issue.is_resolved

    with pytest.raises(ValueError, match="provenance"):
        Issue(
            issue_id="I-2",
            code=IssueCode.VOUCHER_NOT_FOUND,
            level=IssueLevel.RECORD,
            message_uk="Ваучер не знайдено",
            sources=(),
        )


def test_every_issue_code_has_one_enforced_level():
    assert set(ISSUE_LEVEL_BY_CODE) == set(IssueCode)
    with pytest.raises(ValueError, match="import_blocking"):
        Issue(
            issue_id="I-3",
            code=IssueCode.SCHEMA_MISMATCH,
            level=IssueLevel.RECORD,
            message_uk="Структура не підтримується",
            sources=(),
        )


def test_global_internal_issue_does_not_require_fake_cell_provenance():
    issue = Issue(
        issue_id="I-INTERNAL",
        code=IssueCode.INTERNAL_ERROR,
        level=IssueLevel.INTERNAL,
        message_uk="Внутрішня помилка",
        sources=(),
        details=(("diagnostic_ref", "abc123"),),
    )

    assert issue.level is IssueLevel.INTERNAL
