from __future__ import annotations

from importlib import import_module
from importlib.metadata import version
import subprocess
import sys

from seedlink import __version__


def test_pinned_runtime_and_tooling_versions_are_available():
    assert sys.implementation.name == "cpython"
    assert sys.version_info[:3] == (3, 14, 7)
    expected = {
        "Jinja2": "3.1.6",
        "openpyxl": "3.1.5",
        "PySide6": "6.11.2",
        "tzdata": "2026.4",
        "XlsxWriter": "3.2.9",
        "pytest": "9.0.2",
        "PyInstaller": "6.22.3",
    }
    assert {name: version(name) for name in expected} == expected
    for module in ("jinja2", "openpyxl", "PySide6", "xlsxwriter"):
        assert import_module(module)


def test_domain_import_does_not_load_qt():
    command = (
        "import sys; import seedlink.domain; "
        "assert not any(name == 'PySide6' or name.startswith('PySide6.') "
        "for name in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_package_version_and_entry_point():
    assert __version__ == "0.1.0"
    completed = subprocess.run(
        [sys.executable, "-m", "seedlink", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "SeedLink 0.1.0"
