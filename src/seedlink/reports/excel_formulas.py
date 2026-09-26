"""Small, testable Microsoft 365 formulas used by the Excel exporter.

The formulas operate only on already calculated facts.  They deliberately do
not reproduce matching or attribution rules from the domain layer.
"""

from __future__ import annotations


VISIBLE_HEADER = "Видимий"


def _column(header: str) -> str:
    """Escape a table header for use in a structured reference."""

    return header.replace("]", "]]")


def table_column(table: str, header: str) -> str:
    return f"{table}[{_column(header)}]"


def current_row(header: str) -> str:
    return f"[@[{_column(header)}]]"


def visibility_formula(key_header: str = "Ключ") -> str:
    """Return 1 for a visible, non-empty table row and 0 otherwise."""

    return f"=SUBTOTAL(103,{current_row(key_header)})"


def visible_count_formula(
    table: str,
    *,
    visible_header: str = VISIBLE_HEADER,
) -> str:
    return f"=SUM({table_column(table, visible_header)})"


def visible_unique_count_formula(
    table: str,
    key_header: str,
    *,
    visible_header: str = VISIBLE_HEADER,
) -> str:
    """Count unique non-empty keys in the table's current visible set."""

    keys = table_column(table, key_header)
    visible = table_column(table, visible_header)
    return (
        f'=IFERROR(ROWS(UNIQUE(FILTER({keys},({visible}=1)*({keys}<>"")))),0)'
    )


def selected_by_visible_key_formula(
    source_table: str,
    source_key_header: str,
    fact_key_header: str,
    *,
    source_visible_header: str = VISIBLE_HEADER,
) -> str:
    """Mark a fact row selected when its key occurs in a visible source row."""

    return (
        "=--(COUNTIFS("
        f"{table_column(source_table, source_key_header)},{current_row(fact_key_header)},"
        f"{table_column(source_table, source_visible_header)},1)>0)"
    )


def selected_unique_count_formula(
    table: str,
    key_header: str,
    selected_header: str,
) -> str:
    keys = table_column(table, key_header)
    selected = table_column(table, selected_header)
    return (
        f'=IFERROR(ROWS(UNIQUE(FILTER({keys},({selected}=1)*({keys}<>"")))),0)'
    )


def sumproduct_formula(
    table: str,
    value_header: str,
    selector_header: str = VISIBLE_HEADER,
) -> str:
    return (
        f"=SUMPRODUCT({table_column(table, selector_header)},"
        f"{table_column(table, value_header)})"
    )


def filtered_sum_formula(
    table: str,
    value_header: str,
    category_header: str,
    category: str,
    *,
    selector_header: str = VISIBLE_HEADER,
) -> str:
    escaped = category.replace('"', '""')
    return (
        f'=SUMPRODUCT({table_column(table, selector_header)},'
        f'--({table_column(table, category_header)}="{escaped}"),'
        f"{table_column(table, value_header)})"
    )


def completeness_formula(
    row_count_cell: str,
    known_contributor_cell: str,
    unknown_count_cell: str,
) -> str:
    """Distinguish empty, complete, partial and unavailable selections."""

    return (
        f'=IF({row_count_cell}=0,"немає даних",'
        f'IF({unknown_count_cell}=0,"повний",'
        f'IF({known_contributor_cell}>0,"частковий","недоступний")))'
    )

