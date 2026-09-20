from __future__ import annotations

from seedlink.domain.provenance import InputRole
from seedlink.input_xlsx.schemas import SCHEMAS, schema_for


def test_all_four_versioned_schemas_are_fixed():
    assert set(SCHEMAS) == set(InputRole)
    assert {role: len(schema_for(role).columns) for role in InputRole} == {
        InputRole.R1: 21,
        InputRole.R2: 26,
        InputRole.R3: 7,
        InputRole.R4: 14,
    }
    assert all(schema_for(role).version == "1" for role in InputRole)


def test_r1_agreed_typo_is_not_replaced_by_alias():
    columns = schema_for(InputRole.R1).columns
    assert "Campaigm Member" in columns
    assert "Campaign Member" not in columns
