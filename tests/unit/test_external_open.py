from pathlib import Path
import subprocess

from seedlink.desktop import external_open


def test_linux_opener_is_detached_and_silent(monkeypatch, tmp_path):
    artifact = tmp_path / "report.html"
    artifact.touch()
    calls: list[tuple[tuple[str, str], dict[str, object]]] = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return object()

    monkeypatch.setattr(external_open.sys, "platform", "linux")
    monkeypatch.setattr(external_open.subprocess, "Popen", fake_popen)

    assert external_open.open_local_path_detached(artifact)
    assert calls == [
        (
            ("xdg-open", str(artifact.resolve())),
            {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "close_fds": True,
                "start_new_session": True,
            },
        )
    ]


def test_opener_reports_start_failure(monkeypatch, tmp_path):
    artifact = tmp_path / "report.xlsx"
    artifact.touch()

    def fail_to_start(*_args, **_kwargs):
        raise OSError("desktop opener is unavailable")

    monkeypatch.setattr(external_open.sys, "platform", "linux")
    monkeypatch.setattr(external_open.subprocess, "Popen", fail_to_start)

    assert not external_open.open_local_path_detached(Path(artifact))
