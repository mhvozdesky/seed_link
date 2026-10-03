"""Atomic publication of one Excel/HTML bundle from one report revision."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import errno
from pathlib import Path
import shutil
import tempfile
from xml.etree.ElementTree import ParseError, fromstring
from zipfile import BadZipFile, ZipFile

from seedlink.application.errors import ExportError, OperationCancelled, SessionErrorCode
from seedlink.application.state import CancellationToken, OperationPhase, ProgressUpdate
from seedlink.domain.dates import KYIV
from seedlink.domain.models import ReportResult
from seedlink.reports import write_excel_report, write_html_report


ReportWriter = Callable[..., Path]
ProgressCallback = Callable[[ProgressUpdate], None]


@dataclass(frozen=True, slots=True)
class ExportBundle:
    """Paths and identity of one successfully published report bundle."""

    directory: Path
    excel_path: Path
    html_path: Path
    calculation_id: str
    revision: int
    exported_at: datetime


class ExportService:
    """Write both reports privately, verify them, then publish atomically."""

    def __init__(
        self,
        *,
        excel_writer: ReportWriter = write_excel_report,
        html_writer: ReportWriter = write_html_report,
    ) -> None:
        self._excel_writer = excel_writer
        self._html_writer = html_writer

    @staticmethod
    def _emit(
        callback: ProgressCallback | None,
        token: CancellationToken,
        message_uk: str,
        completed: int,
    ) -> None:
        token.raise_if_cancelled()
        if callback is not None:
            callback(
                ProgressUpdate(
                    OperationPhase.EXPORTING,
                    message_uk,
                    completed,
                    3,
                )
            )
        token.raise_if_cancelled()

    @staticmethod
    def _verify_excel(path: Path, result: ReportResult) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise OSError("Excel report was not created")
        try:
            with ZipFile(path) as archive:
                names = set(archive.namelist())
                required = {
                    "[Content_Types].xml",
                    "xl/workbook.xml",
                    "docProps/custom.xml",
                }
                if not required.issubset(names):
                    raise OSError("Excel report has incomplete OOXML structure")
                metadata = archive.read("docProps/custom.xml")
        except BadZipFile as error:
            raise OSError("Excel report is not a valid OOXML archive") from error
        try:
            root = fromstring(metadata)
        except ParseError as error:
            raise OSError("Excel report custom properties are invalid") from error
        properties = {
            property_node.attrib.get("name"): (
                next(iter(property_node)).text or ""
                if len(property_node)
                else ""
            )
            for property_node in root.findall("{*}property")
        }
        if (
            properties.get("SeedLink calculation ID") != result.calculation_id
            or properties.get("SeedLink revision") != str(result.revision)
        ):
            raise OSError("Excel report metadata does not match the revision")

    @staticmethod
    def _verify_html(path: Path, result: ReportResult) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise OSError("HTML report was not created")
        content = path.read_text(encoding="utf-8")
        if (
            result.calculation_id not in content
            or f'"revision":{result.revision}' not in content
        ):
            raise OSError("HTML report metadata does not match the revision")

    @staticmethod
    def _unique_destination(root: Path, exported_at: datetime, revision: int) -> Path:
        stem = f"SeedLink_{exported_at.astimezone(KYIV):%Y%m%d_%H%M%S}_r{revision}"
        candidate = root / stem
        suffix = 2
        while candidate.exists():
            candidate = root / f"{stem}_{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _io_error(error: Exception, stage: str) -> ExportError:
        error_number = getattr(error, "errno", None)
        windows_error = getattr(error, "winerror", None)
        retry = " Спробуйте повторити збереження в іншій папці."
        if windows_error in {32, 33} or error_number in {
            errno.EBUSY,
            getattr(errno, "ETXTBSY", errno.EBUSY),
        }:
            message = (
                "Не вдалося зберегти комплект: файл зайнятий іншою програмою. "
                "Закрийте його в Excel або іншій програмі."
                + retry
            )
        elif error_number == errno.ENOSPC:
            message = (
                "Не вдалося зберегти комплект: на диску бракує вільного місця."
                + retry
            )
        elif isinstance(error, PermissionError):
            message = (
                "Не вдалося зберегти комплект: немає прав на запис у вибрану папку."
                + retry
            )
        elif stage == "excel":
            message = "Не вдалося записати або перевірити Excel-звіт." + retry
        elif stage == "html":
            message = "Не вдалося записати або перевірити HTML-звіт." + retry
        elif stage == "publish":
            message = "Не вдалося опублікувати готовий комплект." + retry
        else:
            message = "Не вдалося підготувати вибрану папку для експорту." + retry
        return ExportError(
            SessionErrorCode.EXPORT_IO_FAILED,
            message,
            details=(
                ("stage", stage),
                ("error_type", type(error).__name__),
            ),
        )

    def export(
        self,
        result: ReportResult,
        destination: str | Path,
        *,
        cancellation: CancellationToken | None = None,
        progress: ProgressCallback | None = None,
        exported_at: datetime | None = None,
    ) -> ExportBundle:
        token = cancellation or CancellationToken()
        timestamp = exported_at or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("exported_at must be timezone-aware")
        root = Path(destination).expanduser()
        temporary: Path | None = None
        stage = "destination"
        try:
            root.mkdir(parents=True, exist_ok=True)
            if not root.is_dir():
                raise OSError("export destination is not a directory")
            temporary = Path(tempfile.mkdtemp(prefix=".seedlink-", dir=root))
            excel = temporary / "SeedLink_report.xlsx"
            html = temporary / "SeedLink_report.html"

            stage = "excel"
            self._emit(progress, token, "Створення Excel-звіту.", 0)
            self._excel_writer(result, excel, exported_at=timestamp)
            self._verify_excel(excel, result)
            stage = "html"
            self._emit(progress, token, "Створення автономного HTML-звіту.", 1)
            self._html_writer(result, html, exported_at=timestamp)
            self._verify_html(html, result)
            stage = "publish"
            self._emit(progress, token, "Публікація готового комплекту.", 2)

            published = self._unique_destination(root, timestamp, result.revision)
            temporary.rename(published)
            temporary = None
            bundle = ExportBundle(
                directory=published,
                excel_path=published / excel.name,
                html_path=published / html.name,
                calculation_id=result.calculation_id,
                revision=result.revision,
                exported_at=timestamp,
            )
            if progress is not None:
                progress(
                    ProgressUpdate(
                        OperationPhase.EXPORTING,
                        "Комплект збережено.",
                        3,
                        3,
                    )
                )
            return bundle
        except OperationCancelled:
            raise
        except ExportError:
            raise
        except Exception as error:
            raise self._io_error(error, stage) from error
        finally:
            if temporary is not None and temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
