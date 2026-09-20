"""Small deterministic normalizations; no fuzzy business matching lives here."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata


class DecimalNormalizationError(ValueError):
    pass


_DASH_TRANSLATION = str.maketrans(
    {
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
    }
)
_DECIMAL_TEXT = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?$")


def normalize_unicode(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def collapse_whitespace(value: str) -> str:
    return " ".join(normalize_unicode(value).split())


def text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = collapse_whitespace(str(value))
    return text or None


def normalize_name(value: str | None) -> str | None:
    text = text_or_none(value)
    return text.casefold() if text is not None else None


def normalize_identifier(value: str | int | Decimal | None) -> str | None:
    """Normalize an ID without ever converting a textual leading zero away."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid identifier")
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("numeric identifier must be a finite integer")
        value = format(value, "f")
    text = collapse_whitespace(str(value))
    return text or None


def normalize_voucher_key(value: str | None) -> str | None:
    """Normalize only agreed technical differences in full voucher text."""

    text = text_or_none(value)
    if text is None:
        return None
    text = text.translate(_DASH_TRANSLATION)
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def decimal_from_value(value: object, *, allow_negative: bool = False) -> Decimal | None:
    """Convert an unambiguous Excel scalar; blank means an unknown value.

    Text supports only ASCII digits, an optional sign and an optional decimal
    point. Thousands separators, decimal commas, internal whitespace and
    scientific notation are deliberately rejected instead of guessed.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        raise DecimalNormalizationError("boolean is not a decimal quantity")
    if isinstance(value, str):
        candidate = value.strip(" \t\r\n")
        if not candidate:
            return None
        if not _DECIMAL_TEXT.fullmatch(candidate):
            raise DecimalNormalizationError(
                "text quantity must use ASCII digits and an optional decimal point"
            )
        try:
            result = Decimal(candidate)
        except InvalidOperation as exc:
            raise DecimalNormalizationError(f"invalid decimal value: {value!r}") from exc
    elif isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(str(value))
    else:
        raise DecimalNormalizationError(
            f"unsupported decimal value type: {type(value).__name__}"
        )
    if not result.is_finite():
        raise DecimalNormalizationError("decimal quantity must be finite")
    if not allow_negative and result < 0:
        raise DecimalNormalizationError("decimal quantity must not be negative")
    return result


def decimal_to_text(value: Decimal) -> str:
    if not value.is_finite():
        raise DecimalNormalizationError("cannot render a non-finite decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text
