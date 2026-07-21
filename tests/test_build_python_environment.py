from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from build_tools.buildlib import constants


def test_build_uses_project_venv_when_it_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("SHANGBACKGROUND_BUILD_PYTHON", raising=False)
    project_python = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    project_python.parent.mkdir(parents=True)
    project_python.write_bytes(b"python")

    assert Path(constants.python_executable()) == project_python


def test_explicit_build_python_has_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    explicit = tmp_path / ("python.exe" if os.name == "nt" else "python")
    explicit.write_bytes(b"python")
    monkeypatch.setenv("SHANGBACKGROUND_BUILD_PYTHON", os.fspath(explicit))

    assert Path(constants.python_executable()) == explicit


def test_launcher_python_is_used_only_before_project_venv_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("SHANGBACKGROUND_BUILD_PYTHON", raising=False)

    selected = Path(constants.python_executable())
    expected = Path(os.path.abspath(sys.executable))
    if os.name == "nt" and expected.name.lower() == "pythonw.exe":
        expected = expected.with_name("python.exe")
    assert selected == expected
