"""Build Block 03 entities and run automatic person/voucher matching."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from seedlink.domain._issue_factory import make_issue
from seedlink.domain.identity import match_participants
from seedlink.domain.issues import Issue, IssueCode
from seedlink.domain.models import (
    AcceptedLink,
    Activity,
    LeadRef,
    Participant,
    ParticipantLink,
    ProductLine,
    Survey,
    Voucher,
    VoucherMention,
)
from seedlink.domain.normalization import (
    normalize_name,
    normalize_voucher_key,
    text_or_none,
)
from seedlink.domain.provenance import InputRole, InputSnapshot, SourceRecord
from seedlink.domain.salesforce_ids import salesforce_id_key
from seedlink.domain.voucher_matching import (
    VoucherNumberKind,
    match_vouchers,
    parse_voucher_number,
    source_row_location,
)
from seedlink.input_xlsx.r1 import R1Record
from seedlink.input_xlsx.r2 import R2Record
from seedlink.input_xlsx.r3 import R3Record
from seedlink.input_xlsx.r4 import R4Record
from seedlink.input_xlsx.workbook_reader import (
    ImportResult,
    ParsedRecord,
    RecordGroupStatus,
    WorkbookReadResult,
)


@dataclass(frozen=True, slots=True)
class SourceUncertainty:
    role: InputRole
    status: RecordGroupStatus
    identifier_field: str
    identifier: str | None
    record_keys: tuple[str, ...]
    related_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AutomaticMatchingResult:
    """Complete, immutable output of the automatic Block 03 analysis."""

    snapshot: InputSnapshot
    surveys: tuple[Survey, ...]
    participants: tuple[Participant, ...]
    activities: tuple[Activity, ...]
    lead_refs: tuple[LeadRef, ...]
    participant_links: tuple[ParticipantLink, ...]
    mentions: tuple[VoucherMention, ...]
    vouchers: tuple[Voucher, ...]
    product_lines: tuple[ProductLine, ...]
    accepted_links: tuple[AcceptedLink, ...]
    source_uncertainties: tuple[SourceUncertainty, ...]
    issues: tuple[Issue, ...]

    def __post_init__(self) -> None:
        self.snapshot.require_complete()
        for field_name in (
            "surveys",
            "participants",
            "activities",
            "lead_refs",
            "participant_links",
            "mentions",
            "vouchers",
            "product_lines",
            "accepted_links",
        ):
            keys = tuple(item.key for item in getattr(self, field_name))
            if len(keys) != len(set(keys)):
                raise ValueError(f"{field_name} must contain unique keys")
        issue_ids = tuple(issue.issue_id for issue in self.issues)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("issues must contain unique IDs")

    @property
    def snapshot_id(self) -> str:
        return self.snapshot.snapshot_id

    @property
    def accepted_voucher_keys(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(link.voucher_key for link in self.accepted_links)
        )


@dataclass(frozen=True, slots=True)
class _RecordUnit:
    record: ParsedRecord
    sources: tuple[SourceRecord, ...]


def _stable_key(prefix: str, *parts: str) -> str:
    identity = "\x1f".join(parts)
    return f"{prefix}:{sha256(identity.encode('utf-8')).hexdigest()}"


def _record_units(workbook: WorkbookReadResult) -> tuple[_RecordUnit, ...]:
    parsed_by_key = {record.source.key: record for record in workbook.records}
    source_by_key = {
        record.source.key: record.source for record in workbook.records
    }
    units: list[_RecordUnit] = []
    for group in workbook.record_groups:
        representative = group.representative_record_key
        if representative is None:
            continue
        units.append(
            _RecordUnit(
                record=parsed_by_key[representative],
                sources=tuple(source_by_key[key] for key in group.record_keys),
            )
        )
    return tuple(units)


def _unique_sources(units: list[_RecordUnit]) -> tuple[SourceRecord, ...]:
    result: list[SourceRecord] = []
    seen: set[str] = set()
    for unit in units:
        for source in unit.sources:
            if source.key not in seen:
                seen.add(source.key)
                result.append(source)
    return tuple(result)


def _display_values(values: list[str | None]) -> tuple[str, ...]:
    result: list[str] = []
    normalized_seen: set[str] = set()
    for value in values:
        display = text_or_none(value)
        normalized = normalize_name(display)
        if (
            display is not None
            and normalized is not None
            and normalized not in normalized_seen
        ):
            normalized_seen.add(normalized)
            result.append(display)
    return tuple(result)


def _single_display(values: list[str | None]) -> str | None:
    unique = _display_values(values)
    return unique[0] if len(unique) == 1 else None


def _build_participants(
    units: tuple[_RecordUnit, ...], snapshot_id: str
) -> tuple[Participant, ...]:
    result: list[Participant] = []
    for unit in units:
        record = unit.record
        if not isinstance(record, R3Record):
            raise TypeError("R3 unit contains a non-R3 record")
        identity = (
            salesforce_id_key(record.campaign_member_id)
            if record.campaign_member_id is not None
            else record.source.key
        )
        result.append(
            Participant(
                key=_stable_key("participant", snapshot_id, identity),
                campaign_member_id=record.campaign_member_id,
                first_name=record.first_name,
                last_name=record.last_name,
                email=record.email,
                member_type=record.member_type,
                first_associated_at=record.first_associated_at,
                sources=unit.sources,
                member_status=record.member_status,
            )
        )
    return tuple(result)


def _build_activities(
    units: tuple[_RecordUnit, ...], snapshot_id: str
) -> tuple[Activity, ...]:
    result: list[Activity] = []
    for unit in units:
        record = unit.record
        if not isinstance(record, R4Record):
            raise TypeError("R4 unit contains a non-R4 record")
        identity = record.activity_id or record.source.key
        result.append(
            Activity(
                key=_stable_key("activity", snapshot_id, identity),
                activity_id=record.activity_id,
                person_name=record.person_name,
                results=record.results,
                completed_at=record.completed_at,
                sources=unit.sources,
            )
        )
    return tuple(result)


def _survey_identity(record: R1Record) -> str:
    if record.survey_response_id is not None:
        return f"response:{record.survey_response_id}"
    return f"row:{record.source.key}"


def _build_r1_entities(
    units: tuple[_RecordUnit, ...], snapshot_id: str
) -> tuple[
    tuple[Survey, ...],
    tuple[LeadRef, ...],
    dict[tuple[str, str, str, int], str],
]:
    survey_groups: dict[str, list[_RecordUnit]] = defaultdict(list)
    for unit in units:
        record = unit.record
        if not isinstance(record, R1Record):
            raise TypeError("R1 unit contains a non-R1 record")
        identity = _survey_identity(record)
        survey_groups[identity].append(unit)

    survey_key_by_identity = {
        identity: _stable_key("survey", snapshot_id, identity)
        for identity in survey_groups
    }
    lead_identity_by_unit: dict[str, str] = {}
    for survey_identity, grouped_units in survey_groups.items():
        single_ids = {
            record.campaign_member_ids[0].casefold()
            for unit in grouped_units
            if isinstance((record := unit.record), R1Record)
            and len(record.campaign_member_ids) == 1
        }
        for unit in grouped_units:
            record = unit.record
            assert isinstance(record, R1Record)
            if len(record.campaign_member_ids) == 1:
                identity = f"id:{record.campaign_member_ids[0].casefold()}"
            elif len(record.campaign_member_ids) > 1:
                identity = f"conflicting-ids:{record.source.key}"
            elif len(single_ids) == 1:
                identity = f"id:{next(iter(single_ids))}"
            else:
                identity = f"survey:{survey_identity}"
            lead_identity_by_unit[record.source.key] = identity

    lead_groups: dict[str, list[_RecordUnit]] = defaultdict(list)
    for grouped_units in survey_groups.values():
        for unit in grouped_units:
            lead_groups[lead_identity_by_unit[unit.record.source.key]].append(unit)

    lead_key_by_identity = {
        identity: _stable_key("lead-ref", snapshot_id, identity)
        for identity in lead_groups
    }
    lead_refs: list[LeadRef] = []
    lead_ref_by_source_row: dict[tuple[str, str, str, int], str] = {}
    for identity, grouped_units in lead_groups.items():
        records = [unit.record for unit in grouped_units]
        if not all(isinstance(record, R1Record) for record in records):
            raise TypeError("lead group contains a non-R1 record")
        typed_records = [record for record in records if isinstance(record, R1Record)]
        campaign_ids = tuple(
            dict.fromkeys(
                value
                for record in typed_records
                for value in record.campaign_member_ids
            )
        )
        candidate_names: list[str | None] = []
        for record in typed_records:
            candidate_names.append(record.lead_full_name)
            if record.first_name and record.last_name:
                candidate_names.append(f"{record.first_name} {record.last_name}")
        sources = _unique_sources(grouped_units)
        lead_key = lead_key_by_identity[identity]
        lead_refs.append(
            LeadRef(
                key=lead_key,
                campaign_member_ids=campaign_ids,
                candidate_names=_display_values(candidate_names),
                phone=_single_display([record.mobile for record in typed_records]),
                email=_single_display([record.email for record in typed_records]),
                sources=sources,
            )
        )
        for source in sources:
            location = source_row_location(source.cells[0])
            lead_ref_by_source_row[location] = lead_key

    surveys: list[Survey] = []
    for identity, grouped_units in survey_groups.items():
        rows = _unique_sources(grouped_units)
        answer_cells = tuple(
            source.cell(field)
            for source in rows
            for field in ("Answer", "Correct Answer", "Answer (Long Text)")
        )
        lead_keys = tuple(
            dict.fromkeys(
                lead_key_by_identity[
                    lead_identity_by_unit[unit.record.source.key]
                ]
                for unit in grouped_units
            )
        )
        representative = grouped_units[0].record
        assert isinstance(representative, R1Record)
        surveys.append(
            Survey(
                key=survey_key_by_identity[identity],
                response_id=representative.survey_response_id,
                lead_ref_keys=lead_keys,
                rows=rows,
                answer_cells=answer_cells,
            )
        )
    return tuple(surveys), tuple(lead_refs), lead_ref_by_source_row


def _build_vouchers_and_lines(
    workbook: WorkbookReadResult, snapshot_id: str
) -> tuple[tuple[Voucher, ...], tuple[ProductLine, ...], tuple[Issue, ...]]:
    units = _record_units(workbook)
    issues: list[Issue] = []
    records_by_key = {record.source.key: record for record in workbook.records}

    # Validate voucher numbers per accounting group, including groups with no
    # representative. Their source facts must remain auditable even though no
    # arbitrary ProductLine winner is selected.
    for group in workbook.record_groups:
        records = tuple(records_by_key[key] for key in group.record_keys)
        if not all(isinstance(record, R2Record) for record in records):
            raise TypeError("R2 group contains a non-R2 record")
        typed_records = tuple(
            record for record in records if isinstance(record, R2Record)
        )
        missing = tuple(
            record for record in typed_records
            if parse_voucher_number(record.voucher_number) is None
        )
        invalid = tuple(
            record
            for record in typed_records
            if (parts := parse_voucher_number(record.voucher_number)) is not None
            and parts.kind is VoucherNumberKind.INVALID
        )
        if missing:
            issues.append(
                make_issue(
                    IssueCode.VOUCHER_NUMBER_MISSING,
                    "Товарний рядок R2 не містить номера ваучера.",
                    sources=tuple(
                        record.source.cell("Voucher Number") for record in missing
                    ),
                    affected_keys=tuple(record.source.key for record in missing),
                    discriminator="|".join(record.source.key for record in missing),
                )
            )
        if invalid:
            issues.append(
                make_issue(
                    IssueCode.VOUCHER_NUMBER_INVALID,
                    "Формат номера ваучера R2 не розпізнано; значення "
                    "збережено для точного зіставлення.",
                    sources=tuple(
                        record.source.cell("Voucher Number") for record in invalid
                    ),
                    affected_keys=tuple(record.source.key for record in invalid),
                    details=(
                        (
                            "vouchers",
                            "; ".join(
                                dict.fromkeys(
                                    record.voucher_number or "" for record in invalid
                                )
                            ),
                        ),
                    ),
                    discriminator="|".join(record.source.key for record in invalid),
                )
            )

    product_lines: list[ProductLine] = []
    product_line_keys_by_voucher: dict[str, list[str]] = defaultdict(list)
    for unit in units:
        record = unit.record
        if not isinstance(record, R2Record):
            raise TypeError("R2 unit contains a non-R2 record")
        normalized = normalize_voucher_key(record.voucher_number)
        voucher_key = (
            _stable_key("voucher", snapshot_id, normalized)
            if normalized is not None
            else None
        )
        product_identity = record.product_id or record.source.key
        line = ProductLine(
            key=_stable_key("product-line", snapshot_id, product_identity),
            product_id=record.product_id,
            voucher_key=voucher_key,
            quantity=record.quantity,
            species_group=record.species_group,
            hybrid=record.hybrid,
            tax_id=record.tax_id,
            account_name=record.account_name,
            created_at=record.product_created_at,
            sources=unit.sources,
            local_description=record.local_description,
        )
        product_lines.append(line)
        if voucher_key is not None:
            product_line_keys_by_voucher[voucher_key].append(line.key)

    # Voucher existence is independent of whether a conflicting Product ID has
    # a countable ProductLine. This lets R1 match a known R2 voucher while the
    # later quantity result remains explicitly incomplete.
    voucher_records: dict[str, list[R2Record]] = defaultdict(list)
    voucher_normalized: dict[str, str] = {}
    for group in workbook.record_groups:
        records = tuple(records_by_key[key] for key in group.record_keys)
        typed_records = tuple(
            record for record in records if isinstance(record, R2Record)
        )
        if len(typed_records) != len(records):
            raise TypeError("R2 workbook contains a non-R2 record")
        normalized_values = tuple(
            normalize_voucher_key(record.voucher_number)
            for record in typed_records
        )
        normalized = {
            value for value in normalized_values if value is not None
        }
        # A conflicting Product ID may still establish its voucher when every
        # row agrees on that full number. Conflicting voucher numbers remain
        # uncertain candidates and do not become accepted R2 voucher facts.
        if len(normalized) != 1 or any(
            value is None for value in normalized_values
        ):
            continue
        normalized_number = next(iter(normalized))
        voucher_key = _stable_key("voucher", snapshot_id, normalized_number)
        voucher_records[voucher_key].extend(typed_records)
        voucher_normalized[voucher_key] = normalized_number

    vouchers: list[Voucher] = []
    for voucher_key, records in voucher_records.items():
        number_variants = tuple(
            sorted(
                dict.fromkeys(
                    value
                    for record in records
                    if (value := text_or_none(record.voucher_number)) is not None
                )
            )
        )
        vouchers.append(
            Voucher(
                key=voucher_key,
                full_number=number_variants[0],
                normalized_number=voucher_normalized[voucher_key],
                product_line_keys=tuple(
                    product_line_keys_by_voucher.get(voucher_key, ())
                ),
                sources=tuple(
                    record.source.cell("Voucher Number") for record in records
                ),
                number_variants=number_variants,
            )
        )
    return tuple(vouchers), tuple(product_lines), tuple(issues)


def _uncertainties(
    import_result: ImportResult, snapshot_id: str
) -> tuple[SourceUncertainty, ...]:
    result: list[SourceUncertainty] = []
    for workbook in import_result.workbooks:
        records_by_key = {record.source.key: record for record in workbook.records}
        for group in workbook.uncertain_groups:
            related_keys: tuple[str, ...] = ()
            if workbook.expected_role is InputRole.R2:
                records = tuple(
                    records_by_key[record_key] for record_key in group.record_keys
                )
                assert all(isinstance(record, R2Record) for record in records)
                normalized_values = tuple(
                    normalize_voucher_key(record.voucher_number)
                    for record in records
                    if isinstance(record, R2Record)
                )
                normalized = {
                    value for value in normalized_values if value is not None
                }
                all_valid = all(
                    value is not None for value in normalized_values
                )
                if all_valid and len(normalized) == 1:
                    related_keys = (
                        _stable_key(
                            "voucher", snapshot_id, next(iter(normalized))
                        ),
                    )
            result.append(
                SourceUncertainty(
                    role=workbook.expected_role,
                    status=group.status,
                    identifier_field=group.identifier_field,
                    identifier=group.identifier,
                    record_keys=group.record_keys,
                    related_keys=related_keys,
                )
            )
    return tuple(result)


def analyze_links(
    import_result: ImportResult,
    *,
    check_cancelled: Callable[[], None] | None = None,
) -> AutomaticMatchingResult:
    """Run Block 03 over a successfully validated four-workbook import."""

    if not import_result.is_accepted or import_result.snapshot is None:
        raise ValueError("automatic analysis requires an accepted import")

    checkpoint = check_cancelled or (lambda: None)
    checkpoint()

    r1_units = _record_units(import_result.workbook(InputRole.R1))
    r3_units = _record_units(import_result.workbook(InputRole.R3))
    r4_units = _record_units(import_result.workbook(InputRole.R4))
    checkpoint()

    participants = _build_participants(
        r3_units, import_result.snapshot.snapshot_id
    )
    activities = _build_activities(
        r4_units, import_result.snapshot.snapshot_id
    )
    surveys, lead_refs, lead_ref_by_source_row = _build_r1_entities(
        r1_units, import_result.snapshot.snapshot_id
    )
    checkpoint()
    vouchers, product_lines, entity_issues = _build_vouchers_and_lines(
        import_result.workbook(InputRole.R2), import_result.snapshot.snapshot_id
    )
    checkpoint()
    people = match_participants(participants, lead_refs, activities)
    checkpoint()
    voucher_matching = match_vouchers(
        surveys, vouchers, lead_ref_by_source_row
    )
    checkpoint()
    issues = (
        import_result.issues
        + entity_issues
        + people.issues
        + voucher_matching.issues
    )
    return AutomaticMatchingResult(
        snapshot=import_result.snapshot,
        surveys=surveys,
        participants=participants,
        activities=activities,
        lead_refs=lead_refs,
        participant_links=people.links,
        mentions=voucher_matching.mentions,
        vouchers=vouchers,
        product_lines=product_lines,
        accepted_links=voucher_matching.accepted_links,
        source_uncertainties=_uncertainties(
            import_result, import_result.snapshot.snapshot_id
        ),
        issues=issues,
    )
