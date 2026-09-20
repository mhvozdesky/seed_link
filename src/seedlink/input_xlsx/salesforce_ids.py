"""Exact Salesforce 15/18-character ID conversion used by person matching."""

from __future__ import annotations

import re


_ID_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9]{18}|[A-Za-z0-9]{15})(?![A-Za-z0-9])"
)
_CHECKSUM_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def salesforce_id_18(value: str) -> str:
    """Return an 18-character ID, adding Salesforce's case suffix when needed."""

    if not re.fullmatch(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?", value):
        raise ValueError("Salesforce ID must contain 15 or 18 ASCII alphanumerics")
    if len(value) == 18:
        return value
    suffix: list[str] = []
    for block_start in range(0, 15, 5):
        flags = 0
        for offset, character in enumerate(value[block_start : block_start + 5]):
            if "A" <= character <= "Z":
                flags |= 1 << offset
        suffix.append(_CHECKSUM_ALPHABET[flags])
    return value + "".join(suffix)


def salesforce_id_key(value: str) -> str:
    """Return the case-insensitive comparison key shared by R1 and R3."""

    return salesforce_id_18(value).casefold()


def find_salesforce_ids(value: str) -> tuple[str, ...]:
    """Find ID-shaped tokens without assuming an object-prefix such as ``00v``."""

    result: list[str] = []
    for match in _ID_TOKEN.finditer(value):
        normalized = salesforce_id_18(match.group(1))
        if normalized not in result:
            result.append(normalized)
    return tuple(result)
