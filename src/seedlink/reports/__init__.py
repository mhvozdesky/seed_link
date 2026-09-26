"""Report exporters over a complete immutable :class:`ReportResult`."""

from seedlink.reports.excel import write_excel_report
from seedlink.reports.html import build_html_projection, write_html_report

__all__ = ["build_html_projection", "write_excel_report", "write_html_report"]
