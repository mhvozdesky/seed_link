"""OS-appropriate writable locations and package resource lookup."""

from __future__ import annotations

from importlib.resources import files
import os
from pathlib import Path
import sys
from typing import Mapping


APP_NAME = "SeedLink"


def user_data_dir(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    platform = platform or sys.platform
    environ = environ if environ is not None else os.environ
    home = home or Path.home()
    if platform == "win32":
        root = environ.get("LOCALAPPDATA") or environ.get("APPDATA")
        return Path(root) / APP_NAME if root else home / "AppData" / "Local" / APP_NAME
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    root = environ.get("XDG_DATA_HOME")
    return Path(root) / APP_NAME if root else home / ".local" / "share" / APP_NAME


def user_config_dir(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    platform = platform or sys.platform
    environ = environ if environ is not None else os.environ
    home = home or Path.home()
    if platform == "win32":
        root = environ.get("APPDATA") or environ.get("LOCALAPPDATA")
        return Path(root) / APP_NAME if root else home / "AppData" / "Roaming" / APP_NAME
    if platform == "darwin":
        return home / "Library" / "Preferences" / APP_NAME
    root = environ.get("XDG_CONFIG_HOME")
    return Path(root) / APP_NAME if root else home / ".config" / APP_NAME


def settings_path() -> Path:
    return user_config_dir() / "settings.json"


def diagnostic_log_path() -> Path:
    return user_data_dir() / "logs" / "seedlink.log"


def resource_path(*parts: str) -> Path:
    """Find a bundled resource without relying on the working directory."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(frozen_root).joinpath("seedlink", "resources", *parts)
    resource = files("seedlink").joinpath("resources", *parts)
    return Path(str(resource))
