"""Runtime support that does not depend on the GUI."""

from seedlink.support.settings import AppSettings, load_settings, save_settings

__all__ = ["AppSettings", "load_settings", "save_settings"]
