"""Reusable read-only table models and all-column text filtering."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QSortFilterProxyModel


@dataclass(frozen=True, slots=True)
class Column:
    label: str
    value: Callable[[object], Any]
    alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft
    display: Callable[[Any], str] | None = None


class ObjectTableModel(QAbstractTableModel):
    """Small immutable-row model; business selection stays in domain queries."""

    RawValueRole = Qt.ItemDataRole.UserRole + 1
    ObjectRole = Qt.ItemDataRole.UserRole + 2

    def __init__(
        self,
        columns: Sequence[Column],
        rows: Sequence[object] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.columns = tuple(columns)
        self.rows = tuple(rows)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            return self.columns[section].label
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        value = self.columns[index.column()].value(row)
        if role == self.ObjectRole:
            return row
        if role == self.RawValueRole:
            return value
        if role == Qt.ItemDataRole.DisplayRole:
            formatter = self.columns[index.column()].display
            if formatter is not None:
                return formatter(value)
            if value is None:
                return "—"
            if isinstance(value, bool):
                return "Так" if value else "Ні"
            return str(value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(
                self.columns[index.column()].alignment
                | Qt.AlignmentFlag.AlignVCenter
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            return "—" if value is None else str(value)
        return None

    def set_rows(self, rows: Sequence[object]) -> None:
        self.beginResetModel()
        self.rows = tuple(rows)
        self.endResetModel()

    def object_at(self, row: int) -> object | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None


class SearchProxyModel(QSortFilterProxyModel):
    """Case-insensitive contains search over the visible table projection."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""
        self.setSortRole(ObjectTableModel.RawValueRole)
        self.setDynamicSortFilter(True)

    def set_search_text(self, value: str) -> None:
        needle = " ".join(value.casefold().split())
        if needle != self._needle:
            self.beginFilterChange()
            self._needle = needle
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        left_value = left.data(ObjectTableModel.RawValueRole)
        right_value = right.data(ObjectTableModel.RawValueRole)
        if left_value is None:
            return right_value is not None
        if right_value is None:
            return False
        comparable = (Decimal, date, datetime, int, float)
        if isinstance(left_value, comparable) and isinstance(right_value, comparable):
            try:
                return left_value < right_value
            except TypeError:
                pass
        return str(left_value).casefold() < str(right_value).casefold()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        model = self.sourceModel()
        if model is None:
            return False
        values: list[str] = []
        for column in range(model.columnCount(source_parent)):
            value = model.index(source_row, column, source_parent).data(
                Qt.ItemDataRole.DisplayRole
            )
            if value is not None:
                values.append(str(value))
        haystack = " ".join(" ".join(values).casefold().split())
        return self._needle in haystack
