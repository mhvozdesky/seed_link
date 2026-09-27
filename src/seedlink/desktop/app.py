"""Qt application bootstrap kept separate for testability and packaging."""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from seedlink import __version__
from seedlink.desktop.main_window import SeedLinkMainWindow
from seedlink.support.logging_setup import configure_logging


def create_application(argv: list[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    QCoreApplication.setOrganizationName("SeedLink")
    QCoreApplication.setApplicationName("SeedLink")
    QCoreApplication.setApplicationVersion(__version__)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    return QApplication(argv if argv is not None else sys.argv)


def run_desktop(argv: list[str] | None = None) -> int:
    configure_logging()
    app = create_application(argv)
    window = SeedLinkMainWindow()
    window.show()
    return app.exec()
