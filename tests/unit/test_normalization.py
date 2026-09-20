from __future__ import annotations

from decimal import Decimal

import pytest

from seedlink.domain.normalization import (
    DecimalNormalizationError,
    decimal_from_value,
    decimal_to_text,
    normalize_identifier,
    normalize_name,
    normalize_voucher_key,
)


def test_name_normalization_is_unicode_case_and_space_stable():
    assert normalize_name("  ЮЛІЯ   Шевченко ") == "юлія шевченко"
    assert normalize_name("ＡＢＣ") == "abc"
    assert normalize_name("  ") is None


def test_identifier_preserves_leading_zeroes():
    assert normalize_identifier("00123456") == "00123456"
    assert normalize_identifier(Decimal("123456")) == "123456"
    with pytest.raises(ValueError, match="boolean"):
        normalize_identifier(True)


def test_voucher_normalization_changes_only_technical_formatting():
    first = " SE–2777788899 /1/ ТОВ АГРОРОСЬ "
    second = "se-2777788899/1/тов агрорось"
    assert normalize_voucher_key(first) == normalize_voucher_key(second)
    assert normalize_voucher_key("SE-1/1/А") != normalize_voucher_key("XY-1/1/А")
    assert normalize_voucher_key("SE-1-безкоштовні мішки") != normalize_voucher_key(
        "SE-1"
    )


def test_decimal_conversion_is_exact_and_keeps_zero():
    assert decimal_from_value(0) == Decimal("0")
    assert decimal_from_value(0.1) + decimal_from_value("0.2") == Decimal("0.3")
    assert decimal_from_value("001.250") == Decimal("1.250")
    assert decimal_from_value("  ") is None
    assert decimal_to_text(Decimal("106.000")) == "106"
    assert decimal_to_text(Decimal("0.100")) == "0.1"


@pytest.mark.parametrize("value", ["not-a-number", "NaN", "Infinity", -1, True])
def test_invalid_quantity_is_not_silently_zero(value):
    with pytest.raises(DecimalNormalizationError):
        decimal_from_value(value)


@pytest.mark.parametrize(
    "value",
    [
        "1,234",
        "1 234",
        "1\N{NO-BREAK SPACE}234",
        "1 234,56",
        "0,2",
        "1e3",
        "１２.３",
    ],
)
def test_ambiguous_or_noncanonical_text_quantity_is_not_guessed(value):
    with pytest.raises(DecimalNormalizationError, match="ASCII digits"):
        decimal_from_value(value)


def test_negative_text_is_only_available_to_explicit_non_quantity_callers():
    with pytest.raises(DecimalNormalizationError, match="must not be negative"):
        decimal_from_value("-1.5")
    assert decimal_from_value("-1.5", allow_negative=True) == Decimal("-1.5")
