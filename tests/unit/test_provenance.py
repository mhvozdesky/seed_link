from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, UTC

import pytest

from seedlink.domain.provenance import InputRole, InputSnapshot, InputSource


def test_source_cell_key_is_stable_and_ignores_mutable_display_value(
    source_cell_factory,
):
    first = source_cell_factory(value="before")
    second = source_cell_factory(value="after")

    assert first.stable_key == second.stable_key
    assert first.stable_key.startswith("cell:")
    with pytest.raises(FrozenInstanceError):
        first.field_name = "Other"


def test_input_snapshot_tracks_completeness(source_record_factory):
    sources = tuple(
        InputSource(
            role=role,
            file_name=f"{role.value}.xlsx",
            file_sha256="a" * 64,
            sheet_name=role.value,
            schema_version="1",
            row_count=1,
            records=(source_record_factory(role),),
        )
        for role in InputRole
    )
    snapshot = InputSnapshot("snapshot-1", "0.1.0", datetime.now(UTC), sources)

    assert snapshot.is_complete
    snapshot.require_complete()
    assert snapshot.source(InputRole.R3).schema_version == "1"


def test_input_snapshot_reports_missing_roles(source_record_factory):
    source = InputSource(
        role=InputRole.R1,
        file_name="R1.xlsx",
        file_sha256="a" * 64,
        sheet_name="R1",
        schema_version="1",
        row_count=1,
        records=(source_record_factory(InputRole.R1),),
    )
    snapshot = InputSnapshot("snapshot-1", "0.1.0", datetime.now(UTC), (source,))

    assert not snapshot.is_complete
    with pytest.raises(ValueError, match="R2, R3, R4"):
        snapshot.require_complete()
