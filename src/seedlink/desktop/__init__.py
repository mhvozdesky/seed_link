"""Public desktop shell API."""

from seedlink.desktop.app import create_application, run_desktop
from seedlink.desktop.async_commands import AsyncCommandController
from seedlink.desktop.main_window import SeedLinkMainWindow
from seedlink.desktop.table_models import Column, ObjectTableModel, SearchProxyModel
from seedlink.desktop.widgets import BarChart, FileSlot, MetricCard, TablePane

__all__ = [
    "AsyncCommandController",
    "BarChart",
    "Column",
    "FileSlot",
    "MetricCard",
    "ObjectTableModel",
    "SearchProxyModel",
    "SeedLinkMainWindow",
    "TablePane",
    "create_application",
    "run_desktop",
]
