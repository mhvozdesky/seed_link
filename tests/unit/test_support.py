from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import seedlink.support.settings as settings_module
from seedlink.support.logging_setup import configure_logging, redact_sensitive
from seedlink.support.paths import resource_path, user_config_dir, user_data_dir
from seedlink.support.settings import AppSettings, load_settings, save_settings


def test_platform_paths_do_not_depend_on_working_directory(tmp_path):
    assert user_data_dir(
        platform="linux", environ={"XDG_DATA_HOME": str(tmp_path / "data")}, home=tmp_path
    ) == tmp_path / "data" / "SeedLink"
    assert user_config_dir(
        platform="win32", environ={"APPDATA": "C:/Profiles/Test/AppData/Roaming"}, home=tmp_path
    ) == Path("C:/Profiles/Test/AppData/Roaming") / "SeedLink"
    assert resource_path("README.txt").is_file()


def test_settings_round_trip_is_utf8_and_ignores_unknown_fields(tmp_path):
    path = tmp_path / "налаштування" / "settings.json"
    expected = AppSettings(last_input_dir="D:/Вхідні файли")
    save_settings(expected, path)
    assert load_settings(path) == expected

    path.write_text('{"language": "uk", "future_option": true}', encoding="utf-8")
    assert load_settings(path) == AppSettings()


def test_broken_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("not json", encoding="utf-8")
    with patch.object(settings_module._LOGGER, "warning") as warning:
        assert load_settings(path) == AppSettings()
    warning.assert_called_once_with("Settings ignored: %s", "JSONDecodeError")


def test_absent_settings_are_normal_and_not_logged(tmp_path):
    with patch.object(settings_module._LOGGER, "warning") as warning:
        assert load_settings(tmp_path / "missing.json") == AppSettings()
    warning.assert_not_called()


def test_diagnostic_logging_redacts_contacts(tmp_path):
    path = tmp_path / "seedlink.log"
    logger = configure_logging(path, level=logging.INFO)
    logger.info("contact user@example.test or +380 67 123 45 67")
    for handler in logger.handlers:
        handler.flush()
    contents = path.read_text(encoding="utf-8")
    assert "user@example.test" not in contents
    assert "+380 67 123 45 67" not in contents
    assert "[EMAIL REDACTED]" in contents
    assert "[PHONE REDACTED]" in contents


def test_redaction_leaves_non_contact_counts_readable():
    assert redact_sensitive("R2 row 117 quantity 106") == "R2 row 117 quantity 106"
    assert (
        redact_sensitive("ваучер SE-2777788899/1/ТОВ АГРОРОСЬ")
        == "ваучер SE-2777788899/1/ТОВ АГРОРОСЬ"
    )
    assert redact_sensitive("знімок 2026-09-20") == "знімок 2026-09-20"
    assert redact_sensitive("ваучер SE-0123456789/1/АГРО") == "ваучер SE-0123456789/1/АГРО"
