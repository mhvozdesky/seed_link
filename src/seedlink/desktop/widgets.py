"""Focused Qt widgets used by the SeedLink desktop window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from seedlink.domain.provenance import InputRole
from seedlink.desktop.table_models import ObjectTableModel, SearchProxyModel


class FileSlot(QFrame):
    """One role-specific file picker and drop target."""

    browse_requested = Signal(object)
    path_dropped = Signal(object, str)

    def __init__(self, role: InputRole, parent=None) -> None:
        super().__init__(parent)
        self.role = role
        self.path: Path | None = None
        self.setObjectName("fileSlot")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        title = QLabel(role.label_uk)
        title.setObjectName("slotTitle")
        self.file_label = QLabel("Файл не вибрано")
        self.file_label.setWordWrap(True)
        self.status_label = QLabel("Перетягніть .xlsx сюди або виберіть файл")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("slotStatus")
        button = QPushButton("Вибрати файл…")
        button.clicked.connect(lambda: self.browse_requested.emit(self.role))
        layout.addWidget(title)
        layout.addWidget(self.file_label)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addWidget(button)
        self.set_state("empty")

    def set_path(self, path: str | Path) -> None:
        self.path = Path(path)
        self.file_label.setText(self.path.name)
        self.file_label.setToolTip(str(self.path))
        self.status_label.setText("Очікує перевірки")
        self.set_state("pending")

    def set_validation(self, *, accepted: bool, message: str) -> None:
        self.status_label.setText(message)
        self.set_state("accepted" if accepted else "error")

    def set_state(self, state: str) -> None:
        self.setProperty("slotState", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            path = Path(urls[0].toLocalFile())
            if path.suffix.casefold() == ".xlsx":
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if len(urls) != 1 or not urls[0].isLocalFile():
            event.ignore()
            return
        path = Path(urls[0].toLocalFile())
        if path.suffix.casefold() != ".xlsx":
            event.ignore()
            return
        self.path_dropped.emit(self.role, str(path))
        event.acceptProposedAction()


class MetricCard(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setObjectName("metricLabel")
        self.value = QLabel("—")
        self.value.setObjectName("metricValue")
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.status)

    def set_metric(self, label: str, value: str, status: str) -> None:
        self.label.setText(label)
        self.value.setText(value)
        self.status.setText(status)


class BarChart(QWidget):
    """Dependency-free accessible horizontal bar chart for ready facts."""

    def __init__(self, title: str, unit: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.values: tuple[tuple[str, float, str], ...] = ()
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(title)

    def set_values(self, values: tuple[tuple[str, float, str], ...]) -> None:
        self.values = values
        self.setAccessibleDescription(
            "; ".join(f"{label}: {shown} {self.unit}" for label, _, shown in values)
        )
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QColor("#16324f"))
        title_font = QFont(self.font())
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(14, 24, f"{self.title}, {self.unit}")
        if not self.values:
            painter.setFont(self.font())
            painter.setPen(QColor("#65758b"))
            painter.drawText(14, 54, "Немає даних")
            return
        maximum = max((value for _, value, _ in self.values), default=0.0)
        row_height = max(28, min(42, (self.height() - 42) // len(self.values)))
        label_width = min(190, max(100, self.width() // 3))
        bar_left = label_width + 18
        bar_width = max(40, self.width() - bar_left - 90)
        painter.setFont(self.font())
        for index, (label, value, shown) in enumerate(self.values):
            y = 42 + index * row_height
            painter.setPen(QColor("#23364d"))
            painter.drawText(QRectF(12, y, label_width, row_height - 6), label)
            fraction = value / maximum if maximum > 0 else 0
            rect = QRectF(bar_left, y + 2, bar_width * fraction, row_height - 12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2d79b8"))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#23364d"))
            painter.drawText(
                QRectF(bar_left + bar_width + 8, y, 76, row_height - 6), shown
            )


class TablePane(QWidget):
    """Searchable/sortable table with an explicit source-details region."""

    def __init__(
        self,
        model: ObjectTableModel,
        *,
        search_placeholder: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.model = model
        self.proxy = SearchProxyModel(self)
        self.proxy.setSourceModel(model)
        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText(search_placeholder)
        self.search.textChanged.connect(self.proxy.set_search_text)
        controls.addWidget(QLabel("Пошук:"))
        controls.addWidget(self.search, 1)
        self.extra_controls = controls
        root.addLayout(controls)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setObjectName("tableSummary")
        root.addWidget(self.summary)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Виберіть рядок, щоб переглянути деталі та джерела.")
        self.details.setMaximumBlockCount(2000)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setSizes([520, 170])
        root.addWidget(splitter, 1)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)

    def add_control(self, widget: QWidget) -> None:
        self.extra_controls.addWidget(widget)

    def selected_object(self) -> object | None:
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        source = self.proxy.mapToSource(indexes[0])
        return self.model.object_at(source.row())

    def _selection_changed(self) -> None:
        value = self.selected_object()
        detail = getattr(value, "detail", "") if value is not None else ""
        self.details.setPlainText(detail)
