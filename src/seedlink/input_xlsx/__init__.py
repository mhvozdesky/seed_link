"""Public XLSX import contracts."""

from seedlink.input_xlsx.schemas import SCHEMAS, SchemaDefinition, schema_for
from seedlink.input_xlsx.r1 import R1Record
from seedlink.input_xlsx.r2 import R2Record
from seedlink.input_xlsx.r3 import R3Record
from seedlink.input_xlsx.r4 import R4Record
from seedlink.input_xlsx.salesforce_ids import salesforce_id_18, salesforce_id_key
from seedlink.input_xlsx.workbook_reader import (
    ImportResult,
    RecordAccounting,
    RecordGroup,
    RecordGroupStatus,
    WorkbookReadResult,
    import_workbooks,
    read_workbook,
)

__all__ = [
    "SCHEMAS",
    "ImportResult",
    "RecordAccounting",
    "RecordGroup",
    "RecordGroupStatus",
    "R1Record",
    "R2Record",
    "R3Record",
    "R4Record",
    "SchemaDefinition",
    "WorkbookReadResult",
    "import_workbooks",
    "read_workbook",
    "salesforce_id_18",
    "salesforce_id_key",
    "schema_for",
]
