from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pytest

from seedlink.application import analyze_links
from seedlink.domain.issues import IssueCode
from seedlink.domain.provenance import InputRole
from seedlink.input_xlsx import import_workbooks


PROJECT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT / "tests" / "fixtures" / "real" / "manifest.json"


def test_local_real_workbooks_match_independent_manifest():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    paths = {
        InputRole(item["role"]): (PROJECT / item["path_from_project"]).resolve()
        for item in manifest["sources"]
    }
    if not all(path.is_file() for path in paths.values()):
        pytest.skip(
            "Локальний приватний комплект XLSX відсутній"
        )
    hashes_before = {
        role: sha256(path.read_bytes()).hexdigest()
        for role, path in paths.items()
    }

    result = import_workbooks(paths)

    hashes_after = {
        role: sha256(path.read_bytes()).hexdigest()
        for role, path in paths.items()
    }

    assert result.is_accepted
    assert result.snapshot is not None
    assert {
        source.role.value: source.row_count for source in result.snapshot.sources
    } == manifest["expected"]["row_counts"]
    assert {
        source.role.value: source.file_sha256 for source in result.snapshot.sources
    } == {item["role"]: item["sha256"] for item in manifest["sources"]}
    assert all(
        result.workbook(role).detected_role is role for role in InputRole
    )
    assert all(
        len(result.workbook(role).countable_records)
        == manifest["expected"]["row_counts"][role.value]
        for role in InputRole
    )
    assert hashes_after == hashes_before
    assert [
        issue.code for issue in result.workbook(InputRole.R1).issues
    ] == [IssueCode.DATE_INVALID]
    assert not result.workbook(InputRole.R3).blocking_issues
    assert all(
        record.campaign_member_id is None
        for record in result.workbook(InputRole.R3).records
    )


def test_local_real_workbooks_produce_three_auditable_automatic_vouchers():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    paths = {
        InputRole(item["role"]): (PROJECT / item["path_from_project"]).resolve()
        for item in manifest["sources"]
    }
    if not all(path.is_file() for path in paths.values()):
        pytest.skip("Локальний приватний комплект XLSX відсутній")

    matching = analyze_links(import_workbooks(paths))
    accepted_numbers = {
        voucher.full_number
        for voucher in matching.vouchers
        if voucher.key in matching.accepted_voucher_keys
    }

    assert accepted_numbers == {
        item["full_number"] for item in manifest["expected"]["matched_vouchers"]
    }
    assert len(matching.accepted_links) == 3
    accepted_lines = [
        line
        for line in matching.product_lines
        if line.voucher_key in matching.accepted_voucher_keys
    ]
    assert len(accepted_lines) == 5
    assert sum(
        (line.quantity for line in accepted_lines if line.quantity is not None),
        Decimal(0),
    ) == Decimal(manifest["expected"]["known_quantity"])
    assert len(matching.participants) == 9
    assert len(matching.activities) == 3
    assert all(link.participant_key for link in matching.participant_links)
    assert {
        issue.code for issue in matching.issues
    } == {IssueCode.DATE_INVALID, IssueCode.VOUCHER_NOT_FOUND}
    assert all(link.evidence for link in matching.accepted_links)
