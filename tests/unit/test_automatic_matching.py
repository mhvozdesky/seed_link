from __future__ import annotations

from collections import Counter

import pytest

from seedlink.application import analyze_links
from seedlink.domain.issues import IssueCode
from seedlink.domain.models import (
    ParticipantLinkSubject,
    PersonMatchMethod,
    VoucherMatchMethod,
)
from seedlink.domain.provenance import InputRole
from seedlink.domain.voucher_matching import (
    VoucherNumberKind,
    parse_voucher_number,
)
from seedlink.input_xlsx import import_workbooks


MEMBER_A = "00vAbCdEfGhIjKlIVK"
MEMBER_B = "00vAbCdEfGhIjKmIVK"
MEMBER_C = "00vAbCdEfGhIjKnIVK"


def _analyze(workbook_set_factory, rows):
    imported = import_workbooks(workbook_set_factory(rows))
    assert imported.is_accepted
    return analyze_links(imported)


def _r1(
    number: str,
    survey: str,
    *,
    member: str | None = None,
    lead_name: str | None = None,
    first: str | None = None,
    last: str | None = None,
    email: str | None = None,
    answer: str | None = None,
    correct: str | None = None,
    long: str | None = None,
):
    return {
        "Record Nr": number,
        "Survey Response ID": survey,
        "Campaigm Member": member,
        "Lead: Full Name": lead_name,
        "First Name": first,
        "Last Name": last,
        "Email": email,
        "Answer": answer,
        "Correct Answer": correct,
        "Answer (Long Text)": long,
    }


def _r2(product: str, voucher: str):
    return {
        "Opportunity Product Id 18": product,
        "Voucher Number": voucher,
        "Current Year Planned Quantity": 1,
    }


def _r3(member: str, first: str, last: str, email: str | None = None):
    return {
        "Campaign Member Id 18": member,
        "First Name": first,
        "Last Name": last,
        "Email": email,
        "Member Type": "Lead",
    }


def test_empty_valid_set_has_an_empty_automatic_result(workbook_set_factory):
    imported = import_workbooks(workbook_set_factory({}))

    result = analyze_links(imported)

    assert not result.surveys
    assert not result.lead_refs
    assert not result.vouchers
    assert not result.accepted_links
    assert not result.issues


def test_failed_import_cannot_be_analyzed(workbook_set_factory):
    paths = workbook_set_factory({})
    paths.pop(InputRole.R2)
    imported = import_workbooks(paths)

    with pytest.raises(ValueError, match="accepted import"):
        analyze_links(imported)


def test_participant_and_activity_keys_are_scoped_to_snapshot(
    workbook_set_factory,
):
    common = {
        InputRole.R3: [_r3(MEMBER_A, "Аліна", "Точна")],
        InputRole.R4: [{"Activity ID": "A1", "Name": "Аліна Точна"}],
    }
    first = _analyze(workbook_set_factory, common)
    second = _analyze(
        workbook_set_factory,
        {**common, InputRole.R2: [_r2("P1", "SE-123")]},
    )

    assert first.snapshot_id != second.snapshot_id
    assert first.participants[0].key != second.participants[0].key
    assert first.activities[0].key != second.activities[0].key


def test_person_matching_uses_id_then_unique_name_and_reports_conflicts(
    workbook_set_factory,
):
    rows = {
        InputRole.R1: [
            _r1("R1", "S1", member=MEMBER_A, lead_name="Невідоме Ім'я"),
            _r1("R2", "S2", lead_name="Аліна Точна"),
            _r1("R3", "S3", member=MEMBER_C, lead_name="Богдан Інший"),
            _r1(
                "R4",
                "S4",
                lead_name="Богдан Інший",
                first="Аліна",
                last="Точна",
            ),
            _r1("R5", "S5", lead_name="Спільне Ім'я"),
            _r1("R6", "S6", lead_name="Особа Поза Базою"),
        ],
        InputRole.R3: [
            _r3(MEMBER_A, "Аліна", "Точна"),
            _r3(MEMBER_B, "Богдан", "Інший"),
            _r3(MEMBER_C, "Спільне", "Ім'я"),
            _r3("00vAbCdEfGhIjKoIVK", "Спільне", "Ім'я"),
        ],
        InputRole.R4: [
            {"Activity ID": "A1", "Name": "Аліна Точна"},
            {"Activity ID": "A2", "Name": "Спільне Ім'я"},
        ],
    }
    result = _analyze(workbook_set_factory, rows)
    lead_by_survey = {
        survey.response_id: survey.lead_ref_keys[0] for survey in result.surveys
    }
    link_by_subject = {
        link.subject_key: link for link in result.participant_links
    }

    assert link_by_subject[lead_by_survey["S1"]].method is PersonMatchMethod.ID
    assert (
        link_by_subject[lead_by_survey["S2"]].method
        is PersonMatchMethod.UNIQUE_NAME
    )
    assert link_by_subject[lead_by_survey["S3"]].participant_key is None
    assert link_by_subject[lead_by_survey["S4"]].participant_key is None
    assert link_by_subject[lead_by_survey["S5"]].participant_key is None
    assert link_by_subject[lead_by_survey["S6"]].participant_key is None

    activity_links = [
        link
        for link in result.participant_links
        if link.subject_kind is ParticipantLinkSubject.ACTIVITY
    ]
    assert [link.method for link in activity_links] == [
        PersonMatchMethod.UNIQUE_NAME,
        PersonMatchMethod.UNRESOLVED,
    ]
    codes = Counter(issue.code for issue in result.issues)
    assert codes[IssueCode.PERSON_LINK_CONFLICT] == 2
    assert codes[IssueCode.PERSON_LINK_AMBIGUOUS] == 2
    assert codes[IssueCode.PERSON_LINK_UNRESOLVED] == 1


def test_exact_person_id_survives_email_difference_with_visible_note(
    workbook_set_factory,
):
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1(
                    "R1",
                    "S1",
                    member=MEMBER_A,
                    lead_name="Аліна Точна",
                    email="personal@example.test",
                )
            ],
            InputRole.R3: [
                _r3(
                    MEMBER_A,
                    "Аліна",
                    "Точна",
                    email="work@example.test",
                )
            ],
        },
    )

    assert result.participant_links[0].method is PersonMatchMethod.ID
    assert result.participant_links[0].participant_key is not None
    assert IssueCode.PERSON_DATA_MISMATCH in {
        issue.code for issue in result.issues
    }
    assert IssueCode.PERSON_LINK_CONFLICT not in {
        issue.code for issue in result.issues
    }


def test_email_difference_still_blocks_name_only_person_match(
    workbook_set_factory,
):
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1(
                    "R1",
                    "S1",
                    lead_name="Аліна Точна",
                    email="personal@example.test",
                )
            ],
            InputRole.R3: [
                _r3(
                    MEMBER_A,
                    "Аліна",
                    "Точна",
                    email="work@example.test",
                )
            ],
        },
    )

    assert result.participant_links[0].method is PersonMatchMethod.UNRESOLVED
    assert IssueCode.PERSON_LINK_CONFLICT in {
        issue.code for issue in result.issues
    }


def test_voucher_rules_keep_suffixes_lists_conflicts_and_unmatched_mentions(
    workbook_set_factory,
):
    vouchers = [
        "SE-00001234/1/DIST A",
        "SE-00001234-безкоштовні мішки",
        "SE-00005678/1/AGRO",
        "XY-00005678/1/AGRO",
        "SE-00009999/1/A",
        "SE-00009999/1/B",
    ]
    r1_rows = [
        _r1("R1", "S1", member=MEMBER_A, long="SE-00001234"),
        _r1(
            "R2",
            "S2",
            member=MEMBER_A,
            answer="SE-00005678/1/AGRO",
            long="XY-00005678/1/AGRO",
        ),
        _r1(
            "R3",
            "S3",
            member=MEMBER_A,
            correct="ZZ-99999999",
            long="SE-00005678/1/AGRO",
        ),
        _r1(
            "R4",
            "S4",
            member=MEMBER_A,
            answer="Не залежить від статусу",
            long="SE-00001234-безкоштовні мішки",
        ),
        _r1("R5", "S5", member=MEMBER_A, long="SE-00005678/1/OTHER"),
        _r1("R6", "S6", member=MEMBER_A, long="SE-00009999/1/OTHER"),
        _r1(
            "R7",
            "S7",
            member=MEMBER_A,
            answer="SE-00005678/1/AGRO; XY-00005678/1/AGRO",
            long="SE–00005678/1/AGRO, XY-00005678/1/AGRO",
        ),
        _r1(
            "R8",
            "S8",
            member=MEMBER_A,
            answer="SE-00001234",
            long="SE-00001234/1/DIST A",
        ),
    ]
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R1: r1_rows,
            InputRole.R2: [
                _r2(f"P{index}", voucher)
                for index, voucher in enumerate(vouchers, start=1)
            ],
            InputRole.R3: [_r3(MEMBER_A, "Учасник", "Один")],
        },
    )
    voucher_key = {voucher.full_number: voucher.key for voucher in result.vouchers}
    survey_key = {survey.response_id: survey.key for survey in result.surveys}
    links_by_survey = {
        response_id: {
            link.voucher_key
            for link in result.accepted_links
            if survey_key[response_id] in link.survey_keys
        }
        for response_id in survey_key
    }

    assert links_by_survey["S1"] == {
        voucher_key[vouchers[0]],
        voucher_key[vouchers[1]],
    }
    assert links_by_survey["S2"] == set()
    assert links_by_survey["S3"] == {voucher_key[vouchers[2]]}
    assert links_by_survey["S4"] == {voucher_key[vouchers[1]]}
    assert links_by_survey["S5"] == {voucher_key[vouchers[2]]}
    assert links_by_survey["S6"] == set()
    assert links_by_survey["S7"] == {
        voucher_key[vouchers[2]],
        voucher_key[vouchers[3]],
    }
    assert links_by_survey["S8"] == set()

    codes = Counter(issue.code for issue in result.issues)
    assert codes[IssueCode.VOUCHER_NOT_FOUND] == 1
    assert codes[IssueCode.VOUCHER_MATCH_AMBIGUOUS] == 1
    assert codes[IssueCode.VOUCHER_FIELD_CONFLICT] == 2
    free_mentions = [
        mention
        for mention in result.mentions
        if mention.matched_text == "SE-00001234-безкоштовні мішки"
    ]
    assert len(free_mentions) == 1
    assert free_mentions[0].candidate_voucher_keys == (voucher_key[vouchers[1]],)
    variant_link = next(
        link
        for link in result.accepted_links
        if survey_key["S5"] in link.survey_keys
    )
    assert any(
        evidence.survey_key == survey_key["S5"]
        and evidence.method is VoucherMatchMethod.DISTRIBUTOR_VARIANT
        for evidence in variant_link.evidence
    )


@pytest.mark.parametrize(
    ("value", "kind", "digits"),
    [
        (" SE–001234 ", VoucherNumberKind.SHORT, "001234"),
        (
            "SE-001234/2/ТОВ Дистриб'ютор",
            VoucherNumberKind.DISTRIBUTOR,
            "001234",
        ),
        (
            "SE-001234-безкоштовні мішки",
            VoucherNumberKind.OTHER_FULL,
            "001234",
        ),
        ("SE-001234 ТОВ АГРО", VoucherNumberKind.OTHER_FULL, "001234"),
        ("SE001234", VoucherNumberKind.INVALID, ""),
    ],
)
def test_voucher_number_parser_changes_only_technical_formatting(
    value, kind, digits
):
    parts = parse_voucher_number(value)
    assert parts is not None
    assert parts.kind is kind
    assert parts.digits == digits


def test_every_nonempty_r2_voucher_is_kept_and_shortness_belongs_to_r1(
    workbook_set_factory,
):
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R1: [_r1("R1", "S1", long="SE-123")],
            InputRole.R2: [
                {
                    **_r2("P1", "SE-123"),
                    "Current Year Planned Quantity": 50,
                },
                _r2("P2", "SE-123 ТОВ АГРО"),
                _r2("P3", "not a voucher"),
            ]
        },
    )

    assert {voucher.full_number for voucher in result.vouchers} == {
        "SE-123",
        "SE-123 ТОВ АГРО",
        "not a voucher",
    }
    assert len(result.product_lines) == 3
    assert all(line.voucher_key is not None for line in result.product_lines)
    assert next(
        line.quantity for line in result.product_lines if line.product_id == "P1"
    ) == 50
    assert len(result.accepted_links) == 2
    assert {
        link.method for link in result.accepted_links
    } == {VoucherMatchMethod.SHORT_BLOCK}
    assert Counter(issue.code for issue in result.issues)[
        IssueCode.VOUCHER_NUMBER_INVALID
    ] == 1


def test_voucher_keeps_all_deterministic_display_variants(
    workbook_set_factory,
):
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R2: [
                _r2("P1", "SE–123/1/ТОВ АГРО"),
                _r2("P2", "se-123/1/тов агро"),
            ]
        },
    )

    assert len(result.vouchers) == 1
    voucher = result.vouchers[0]
    assert voucher.number_variants == tuple(sorted(voucher.number_variants))
    assert set(voucher.number_variants) == {
        "SE–123/1/ТОВ АГРО",
        "se-123/1/тов агро",
    }
    assert voucher.full_number == voucher.number_variants[0]


def test_analysis_preserves_duplicate_provenance_and_source_uncertainty(
    workbook_set_factory,
):
    duplicate_product = _r2("P1", "SE-12345/1/VALID")
    duplicate_activity = {"Activity ID": "A1", "Name": "Відомий Учасник"}
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R2: [duplicate_product, duplicate_product],
            InputRole.R3: [
                {
                    **_r3(MEMBER_A, "Перший", "Варіант"),
                    "Member Status": "One",
                },
                {
                    **_r3(MEMBER_A, "Інший", "Варіант"),
                    "Member Status": "Two",
                },
                _r3(MEMBER_B, "Відомий", "Учасник"),
            ],
            InputRole.R4: [duplicate_activity, duplicate_activity],
        },
    )

    assert len(result.product_lines) == 1
    assert len(result.product_lines[0].sources) == 2
    assert len(result.activities) == 1
    assert len(result.activities[0].sources) == 2
    assert len(result.participants) == 1
    uncertainty = next(
        item for item in result.source_uncertainties if item.role is InputRole.R3
    )
    assert uncertainty.status.value == "conflicting_id"
    assert len(uncertainty.record_keys) == 2


def test_repeated_rows_of_one_survey_are_compared_as_separate_cells(
    workbook_set_factory,
):
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", member=MEMBER_A, answer="SE-11111/1/A"),
                _r1("R2", "S1", member=MEMBER_A, answer="SE-22222/1/B"),
            ],
            InputRole.R2: [
                _r2("P1", "SE-11111/1/A"),
                _r2("P2", "SE-22222/1/B"),
            ],
            InputRole.R3: [_r3(MEMBER_A, "Учасник", "Один")],
        },
    )

    assert len(result.surveys) == 1
    assert len(result.surveys[0].rows) == 2
    assert not result.accepted_links
    assert Counter(issue.code for issue in result.issues)[
        IssueCode.VOUCHER_FIELD_CONFLICT
    ] == 1


def test_voucher_remains_accepted_when_lead_has_no_r3_participant(
    workbook_set_factory,
):
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1(
                    "R1",
                    "S1",
                    lead_name="Особа Поза Базою",
                    long="SE-12345/1/VALID",
                )
            ],
            InputRole.R2: [_r2("P1", "SE-12345/1/VALID")],
        },
    )

    assert len(result.accepted_links) == 1
    assert result.participant_links[0].method is PersonMatchMethod.UNRESOLVED
    assert IssueCode.PERSON_LINK_UNRESOLVED in {
        issue.code for issue in result.issues
    }


def test_conflicting_product_keeps_voucher_match_and_unknown_line_group(
    workbook_set_factory,
):
    common = {
        "Opportunity Product Id 18": "P1",
        "Voucher Number": "SE-12345/1/VALID",
    }
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", long="SE-12345/1/VALID")
            ],
            InputRole.R2: [
                {**common, "Current Year Planned Quantity": 1},
                {**common, "Current Year Planned Quantity": 2},
            ],
        },
    )

    assert len(result.vouchers) == 1
    assert not result.product_lines
    assert len(result.accepted_links) == 1
    uncertainty = next(
        item for item in result.source_uncertainties if item.role is InputRole.R2
    )
    assert uncertainty.related_keys == (result.vouchers[0].key,)
    assert IssueCode.PRODUCT_ID_CONFLICT in {
        issue.code for issue in result.issues
    }


def test_conflicting_product_voucher_numbers_are_not_arbitrary_r2_facts(
    workbook_set_factory,
):
    common = {"Opportunity Product Id 18": "P1"}
    result = _analyze(
        workbook_set_factory,
        {
            InputRole.R1: [
                _r1("R1", "S1", long="SE-12345/1/FIRST")
            ],
            InputRole.R2: [
                {
                    **common,
                    "Voucher Number": "SE-12345/1/FIRST",
                    "Current Year Planned Quantity": 1,
                },
                {
                    **common,
                    "Voucher Number": "SE-99999/1/SECOND",
                    "Current Year Planned Quantity": 1,
                },
            ],
        },
    )

    assert not result.vouchers
    assert not result.accepted_links
    assert result.source_uncertainties[0].related_keys == ()
    assert IssueCode.VOUCHER_NOT_FOUND in {
        issue.code for issue in result.issues
    }
