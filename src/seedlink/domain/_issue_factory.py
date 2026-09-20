"""Deterministic construction of issues raised by business analysis."""

from __future__ import annotations

from hashlib import sha256

from seedlink.domain.issues import ISSUE_LEVEL_BY_CODE, Issue, IssueCode
from seedlink.domain.provenance import SourceCell


def make_issue(
    code: IssueCode,
    message_uk: str,
    *,
    sources: tuple[SourceCell, ...],
    affected_keys: tuple[str, ...] = (),
    candidate_keys: tuple[str, ...] = (),
    details: tuple[tuple[str, str], ...] = (),
    discriminator: str = "",
) -> Issue:
    """Build an issue whose identity is stable for the same source snapshot."""

    source_keys = tuple(source.stable_key for source in sources)
    identity = "\x1f".join(
        (
            code.value,
            *source_keys,
            "\x1e".join(affected_keys),
            "\x1e".join(candidate_keys),
            discriminator,
        )
    )
    return Issue(
        issue_id=f"issue:{sha256(identity.encode('utf-8')).hexdigest()}",
        code=code,
        level=ISSUE_LEVEL_BY_CODE[code],
        message_uk=message_uk,
        sources=sources,
        affected_keys=affected_keys,
        candidate_keys=candidate_keys,
        details=details,
    )
