"""Voucher mention extraction and automatic matching rules."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import re

from seedlink.domain._issue_factory import make_issue
from seedlink.domain.issues import Issue, IssueCode
from seedlink.domain.models import (
    AcceptedLink,
    Survey,
    Voucher,
    VoucherMatchEvidence,
    VoucherMatchMethod,
    VoucherMention,
)
from seedlink.domain.normalization import normalize_unicode, normalize_voucher_key
from seedlink.domain.provenance import SourceCell, SourceValueKind


class VoucherNumberKind(StrEnum):
    SHORT = "short"
    DISTRIBUTOR = "distributor"
    OTHER_FULL = "other_full"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class VoucherNumberParts:
    normalized: str
    prefix: str
    digits: str
    kind: VoucherNumberKind
    distributor_segment: str | None = None

    @property
    def block_key(self) -> tuple[str, str]:
        return self.prefix, self.digits

    @property
    def distributor_key(self) -> tuple[str, str, str] | None:
        if self.distributor_segment is None:
            return None
        return self.prefix, self.digits, self.distributor_segment


@dataclass(frozen=True, slots=True)
class VoucherMatchingResult:
    mentions: tuple[VoucherMention, ...]
    accepted_links: tuple[AcceptedLink, ...]
    issues: tuple[Issue, ...]


@dataclass(frozen=True, slots=True)
class _RawMention:
    start: int
    end: int
    normalized: str


@dataclass(frozen=True, slots=True)
class _Evaluation:
    mention: VoucherMention
    accepted_keys: tuple[str, ...]
    method: VoucherMatchMethod | None
    ambiguous: bool = False


_NUMBER_RE = re.compile(
    r"^(?P<prefix>[^\W\d_]+)-(?P<digits>[0-9]+)(?P<suffix>.*)$",
    re.UNICODE,
)
_CORE_RE = re.compile(
    r"(?<![\w])(?P<prefix>[^\W\d_]+)\s*"
    r"[-‐‑‒–—―−]\s*(?P<digits>[0-9]+)",
    re.UNICODE | re.IGNORECASE,
)
_DISTRIBUTOR_SUFFIX_RE = re.compile(
    r"^\s*/\s*(?P<segment>[^/\s]+)\s*/", re.UNICODE
)
_DASHES = str.maketrans(
    {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-"}
)


def parse_voucher_number(value: str | None) -> VoucherNumberParts | None:
    """Parse only the agreed prefix/digit structure; never alter the digits."""

    normalized = normalize_voucher_key(value)
    if normalized is None:
        return None
    match = _NUMBER_RE.fullmatch(normalized)
    if match is None:
        return VoucherNumberParts(normalized, "", "", VoucherNumberKind.INVALID)
    prefix = match.group("prefix")
    digits = match.group("digits")
    suffix = match.group("suffix")
    if not suffix:
        kind = VoucherNumberKind.SHORT
        segment = None
    else:
        distributor = re.fullmatch(r"/([^/]+)/(.+)", suffix)
        if distributor is not None:
            kind = VoucherNumberKind.DISTRIBUTOR
            segment = distributor.group(1)
        elif suffix:
            kind = VoucherNumberKind.OTHER_FULL
            segment = None
        else:
            kind = VoucherNumberKind.INVALID
            segment = None
    return VoucherNumberParts(normalized, prefix, digits, kind, segment)


def source_row_location(cell: SourceCell) -> tuple[str, str, str, int]:
    """A stable row locator shared with the application entity builder."""

    return (cell.file_sha256, cell.sheet_name, cell.role.value, cell.excel_row)


def _compact_with_positions(text: str) -> tuple[str, tuple[int, ...]]:
    compact: list[str] = []
    positions: list[int] = []
    for position, original in enumerate(text):
        normalized = normalize_unicode(original).translate(_DASHES)
        for character in normalized:
            if character.isspace():
                continue
            for folded in character.casefold():
                compact.append(folded)
                positions.append(position)
    return "".join(compact), tuple(positions)


def _suffix_end(text: str, start: int, next_core: int | None) -> int:
    limit = len(text) if next_core is None else next_core
    for delimiter in (";", ",", "\n", "\r"):
        found = text.find(delimiter, start, limit)
        if found >= 0:
            limit = min(limit, found)
    while limit > start and text[limit - 1].isspace():
        limit -= 1
    while limit > start and text[limit - 1] in ".!?)]}":
        limit -= 1
    return limit


def _known_full_span(
    text: str,
    start: int,
    candidates: tuple[str, ...],
) -> tuple[int, str] | None:
    compact, positions = _compact_with_positions(text[start:])
    for normalized in candidates:
        if not compact.startswith(normalized):
            continue
        end = start + positions[len(normalized) - 1] + 1
        if end < len(text):
            following = text[end]
            if following.isalnum() or following in "_/-‐‑‒–—―−":
                continue
        return end, normalized
    return None


def _raw_mentions(
    text: str,
    full_numbers_by_block: Mapping[tuple[str, str], tuple[str, ...]],
) -> tuple[_RawMention, ...]:
    mentions: list[_RawMention] = []
    core_matches = list(_CORE_RE.finditer(text))
    for index, match in enumerate(core_matches):
        start = match.start()
        next_core = (
            core_matches[index + 1].start()
            if index + 1 < len(core_matches)
            else None
        )
        core = parse_voucher_number(match.group(0))
        candidates = (
            full_numbers_by_block.get(core.block_key, ())
            if core is not None and core.kind is VoucherNumberKind.SHORT
            else ()
        )
        known = _known_full_span(text, start, candidates)
        if known is not None:
            end, normalized = known
            mentions.append(_RawMention(start, end, normalized))
            continue
        cursor = match.end()
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        end = match.end()
        suffix = text[match.end() :]
        distributor = _DISTRIBUTOR_SUFFIX_RE.match(suffix)
        if distributor is not None:
            content_start = match.end() + distributor.end()
            end = _suffix_end(text, content_start, next_core)
        elif cursor < len(text) and text[cursor] in "-‐‑‒–—―−/":
            end = _suffix_end(text, cursor + 1, next_core)
        if end <= start:
            continue
        token = text[start:end]
        normalized = normalize_voucher_key(token)
        if normalized is not None:
            mentions.append(_RawMention(start, end, normalized))

    mentions.sort(key=lambda item: (item.start, item.end, item.normalized))
    unique: list[_RawMention] = []
    seen: set[tuple[int, int, str]] = set()
    for mention in mentions:
        identity = (mention.start, mention.end, mention.normalized)
        if identity not in seen:
            seen.add(identity)
            unique.append(mention)
    return tuple(unique)


def _mention_key(source: SourceCell, start: int, end: int) -> str:
    identity = f"{source.stable_key}\x1f{start}\x1f{end}"
    return f"mention:{sha256(identity.encode('utf-8')).hexdigest()}"


def _evaluate_cell(
    source: SourceCell,
    *,
    survey_key: str,
    lead_ref_key: str | None,
    exact_index: Mapping[str, tuple[str, ...]],
    block_index: Mapping[tuple[str, str], tuple[str, ...]],
    distributor_index: Mapping[tuple[str, str, str], tuple[str, ...]],
    full_numbers_by_block: Mapping[tuple[str, str], tuple[str, ...]],
) -> tuple[_Evaluation, ...]:
    if source.value_kind in {SourceValueKind.FORMULA, SourceValueKind.ERROR}:
        return ()
    if not isinstance(source.original_value, str) or not source.original_value.strip():
        return ()
    evaluations: list[_Evaluation] = []
    for raw in _raw_mentions(source.original_value, full_numbers_by_block):
        parts = parse_voucher_number(raw.normalized)
        candidates: tuple[str, ...] = ()
        accepted: tuple[str, ...] = ()
        method: VoucherMatchMethod | None = None
        ambiguous = False
        exact = exact_index.get(raw.normalized, ())
        if parts is not None and parts.kind is VoucherNumberKind.SHORT:
            candidates = block_index.get(parts.block_key, ())
            accepted = candidates
            if accepted:
                method = VoucherMatchMethod.SHORT_BLOCK
        elif exact:
            candidates = exact
            accepted = exact
            method = VoucherMatchMethod.EXACT_FULL
        elif (
            parts is not None
            and parts.kind is VoucherNumberKind.DISTRIBUTOR
            and parts.distributor_key is not None
        ):
            candidates = distributor_index.get(parts.distributor_key, ())
            if len(candidates) == 1:
                accepted = candidates
                method = VoucherMatchMethod.DISTRIBUTOR_VARIANT
            elif len(candidates) > 1:
                ambiguous = True
        mention = VoucherMention(
            key=_mention_key(source, raw.start, raw.end),
            survey_key=survey_key,
            lead_ref_key=lead_ref_key,
            source=source,
            original_text=source.original_value,
            start=raw.start,
            end=raw.end,
            normalized_token=raw.normalized,
            candidate_voucher_keys=candidates,
        )
        evaluations.append(
            _Evaluation(mention, accepted, method, ambiguous=ambiguous)
        )
    return tuple(evaluations)


def _index_vouchers(vouchers: tuple[Voucher, ...]):
    exact: dict[str, list[str]] = defaultdict(list)
    block: dict[tuple[str, str], list[str]] = defaultdict(list)
    distributor: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    full_numbers: dict[tuple[str, str], list[str]] = defaultdict(list)
    for voucher in vouchers:
        parts = parse_voucher_number(voucher.normalized_number)
        exact[voucher.normalized_number].append(voucher.key)
        if parts is not None and parts.kind is not VoucherNumberKind.INVALID:
            block[parts.block_key].append(voucher.key)
            full_numbers[parts.block_key].append(parts.normalized)
            if parts.distributor_key is not None:
                distributor[parts.distributor_key].append(voucher.key)
    return (
        {key: tuple(values) for key, values in exact.items()},
        {key: tuple(values) for key, values in block.items()},
        {key: tuple(values) for key, values in distributor.items()},
        {
            key: tuple(sorted(values, key=lambda value: (-len(value), value)))
            for key, values in full_numbers.items()
        },
    )


def _method_priority(method: VoucherMatchMethod) -> int:
    return {
        VoucherMatchMethod.EXACT_FULL: 0,
        VoucherMatchMethod.SHORT_BLOCK: 1,
        VoucherMatchMethod.DISTRIBUTOR_VARIANT: 2,
        VoucherMatchMethod.MANUAL: 3,
    }[method]


def match_vouchers(
    surveys: tuple[Survey, ...],
    vouchers: tuple[Voucher, ...],
    lead_ref_by_source_row: Mapping[tuple[str, str, str, int], str],
) -> VoucherMatchingResult:
    """Extract all R1 mentions and apply the automatic rules survey by survey."""

    (
        exact_index,
        block_index,
        distributor_index,
        full_numbers_by_block,
    ) = _index_vouchers(vouchers)
    all_evaluations: list[_Evaluation] = []
    issues: list[Issue] = []
    accepted_evidence: dict[
        tuple[str, str], list[tuple[_Evaluation, VoucherMatchMethod]]
    ] = defaultdict(list)

    for survey in surveys:
        evaluations: list[_Evaluation] = []
        for cell in survey.answer_cells:
            lead_ref_key = lead_ref_by_source_row.get(source_row_location(cell))
            evaluations.extend(
                _evaluate_cell(
                    cell,
                    survey_key=survey.key,
                    lead_ref_key=lead_ref_key,
                    exact_index=exact_index,
                    block_index=block_index,
                    distributor_index=distributor_index,
                    full_numbers_by_block=full_numbers_by_block,
                )
            )
        all_evaluations.extend(evaluations)

        for evaluation in evaluations:
            mention = evaluation.mention
            if evaluation.ambiguous:
                issues.append(
                    make_issue(
                        IssueCode.VOUCHER_MATCH_AMBIGUOUS,
                        "Згадка ваучера має кілька можливих варіантів "
                        "дистриб'ютора; потрібен ручний вибір.",
                        sources=(mention.source,),
                        affected_keys=(survey.key, mention.key),
                        candidate_keys=mention.candidate_voucher_keys,
                        discriminator=mention.key,
                    )
                )
            elif not evaluation.accepted_keys:
                issues.append(
                    make_issue(
                        IssueCode.VOUCHER_NOT_FOUND,
                        "Згаданий номер ваучера не знайдено у R2.",
                        sources=(mention.source,),
                        affected_keys=(survey.key, mention.key),
                        details=(("mention", mention.matched_text),),
                        discriminator=mention.key,
                    )
                )

        # A possible alternative distributor changes the survey result, so an
        # exact match in another field must not hide that review case.
        if any(evaluation.ambiguous for evaluation in evaluations):
            continue

        by_cell: dict[str, set[str]] = defaultdict(set)
        for evaluation in evaluations:
            by_cell[evaluation.mention.source.stable_key].update(
                evaluation.accepted_keys
            )
        nonempty_sets = {
            frozenset(values) for values in by_cell.values() if values
        }
        if len(nonempty_sets) > 1:
            relevant = tuple(
                evaluation.mention
                for evaluation in evaluations
                if evaluation.accepted_keys
            )
            candidates = tuple(
                dict.fromkeys(
                    key
                    for evaluation in evaluations
                    for key in evaluation.accepted_keys
                )
            )
            details = tuple(
                (f"matched_set_{index}", ", ".join(sorted(values)))
                for index, values in enumerate(
                    sorted(nonempty_sets, key=lambda value: tuple(sorted(value))),
                    start=1,
                )
            )
            issues.append(
                make_issue(
                    IssueCode.VOUCHER_FIELD_CONFLICT,
                    "Різні клітинки відповідей одного опитування ведуть "
                    "до різних наборів ваучерів.",
                    sources=tuple(dict.fromkeys(item.source for item in relevant)),
                    affected_keys=(
                        survey.key,
                        *(item.key for item in relevant),
                    ),
                    candidate_keys=candidates,
                    details=details,
                    discriminator=survey.key,
                )
            )
            continue
        if not nonempty_sets:
            continue

        accepted_set = next(iter(nonempty_sets))
        for evaluation in evaluations:
            if evaluation.method is None or evaluation.mention.lead_ref_key is None:
                continue
            for voucher_key in evaluation.accepted_keys:
                if voucher_key in accepted_set:
                    accepted_evidence[
                        (evaluation.mention.lead_ref_key, voucher_key)
                    ].append((evaluation, evaluation.method))

    accepted_links: list[AcceptedLink] = []
    for (lead_ref_key, voucher_key), evidence_items in accepted_evidence.items():
        evidence_by_mention: dict[str, tuple[_Evaluation, VoucherMatchMethod]] = {}
        for item in evidence_items:
            evidence_by_mention.setdefault(item[0].mention.key, item)
        ordered = sorted(
            evidence_by_mention.values(),
            key=lambda item: (
                item[0].mention.source.excel_row,
                item[0].mention.start,
                item[0].mention.key,
            ),
        )
        methods = tuple(item[1] for item in ordered)
        method = min(methods, key=_method_priority)
        survey_keys = tuple(
            dict.fromkeys(item[0].mention.survey_key for item in ordered)
        )
        mention_keys = tuple(item[0].mention.key for item in ordered)
        sources = tuple(
            dict.fromkeys(item[0].mention.source for item in ordered)
        )
        identity = f"{lead_ref_key}\x1f{voucher_key}"
        accepted_links.append(
            AcceptedLink(
                key=f"accepted-link:{sha256(identity.encode('utf-8')).hexdigest()}",
                lead_ref_key=lead_ref_key,
                voucher_key=voucher_key,
                survey_keys=survey_keys,
                mention_keys=mention_keys,
                method=method,
                sources=sources,
                evidence=tuple(
                    VoucherMatchEvidence(
                        mention_key=item[0].mention.key,
                        survey_key=item[0].mention.survey_key,
                        method=item[1],
                    )
                    for item in ordered
                ),
            )
        )

    return VoucherMatchingResult(
        mentions=tuple(item.mention for item in all_evaluations),
        accepted_links=tuple(accepted_links),
        issues=tuple(issues),
    )
