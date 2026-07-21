from __future__ import annotations

import subprocess

import pytest

from build_tools import _entry


def test_cli_guard_reports_runtime_error_without_traceback(capsys: pytest.CaptureFixture[str]):
    code = _entry._guard_cli(lambda: (_ for _ in ()).throw(RuntimeError("missing prerequisite")))

    assert code == 1
    assert capsys.readouterr().err.strip() == "ERROR: missing prerequisite"


def test_cli_guard_preserves_compiler_exit_code(capsys: pytest.CaptureFixture[str]):
    code = _entry._guard_cli(lambda: (_ for _ in ()).throw(subprocess.CalledProcessError(7, ["compiler"])))

    assert code == 7
    assert "status 7" in capsys.readouterr().err


def test_cli_guard_traceback_mode_reraises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHANGBACKGROUND_BUILD_TRACEBACK", "1")

    with pytest.raises(RuntimeError, match="debug"):
        _entry._guard_cli(lambda: (_ for _ in ()).throw(RuntimeError("debug")))
