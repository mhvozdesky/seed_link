"""Small non-sensitive interface settings persisted as UTF-8 JSON."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
import tempfile

from seedlink.domain.dates import KYIV_TIMEZONE_NAME
from seedlink.support.paths import settings_path


_LOGGER = logging.getLogger("seedlink")


@dataclass(frozen=True, slots=True)
class AppSettings:
    language: str = "uk"
    timezone: str = KYIV_TIMEZONE_NAME
    last_input_dir: str | None = None
    last_output_dir: str | None = None
    window_width: int = 1280
    window_height: int = 800

    def __post_init__(self) -> None:
        if self.language != "uk":
            raise ValueError("the first version supports the Ukrainian interface only")
        if self.timezone != KYIV_TIMEZONE_NAME:
            raise ValueError("business calendar timezone must remain Europe/Kyiv")
        if self.window_width < 640 or self.window_height < 480:
            raise ValueError("window dimensions are below the supported minimum")


def load_settings(path: Path | None = None) -> AppSettings:
    target = path or settings_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            _LOGGER.warning("Settings ignored: JSON root is not an object")
            return AppSettings()
        allowed = {field for field in AppSettings.__dataclass_fields__}
        return AppSettings(**{key: value for key, value in payload.items() if key in allowed})
    except FileNotFoundError:
        return AppSettings()
    except (OSError, ValueError, TypeError) as exc:
        _LOGGER.warning("Settings ignored: %s", type(exc).__name__)
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(payload)
            stream.flush()
            temporary = Path(stream.name)
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return target
