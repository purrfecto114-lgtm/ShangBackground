from __future__ import annotations

import os
from pathlib import Path

from platform_adapters.windows_job import (
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    attach_process_kill_on_close,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIDEO = PROJECT_ROOT / "src" / "platform_adapters" / "backends" / "windows" / "video.py"
HTML = PROJECT_ROOT / "src" / "platform_adapters" / "backends" / "windows" / "html_wallpaper.py"
JOB = PROJECT_ROOT / "src" / "platform_adapters" / "windows_job.py"


def test_job_limit_flag_matches_win32_contract():
    assert JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x00002000


def test_job_helper_is_import_safe_and_noop_off_windows():
    if os.name != "nt":
        assert attach_process_kill_on_close(object()) is None


def test_windows_renderer_launchers_attach_owned_processes_to_job_object():
    video = VIDEO.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    assert "attach_process_kill_on_close(_CURRENT_PROC)" in video
    assert "attach_process_kill_on_close(" in html
    assert "_close_current_job()" in video
    assert "job.close()" in html


def test_job_helper_uses_kill_on_close_and_assign_process_api():
    text = JOB.read_text(encoding="utf-8")
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in text
    assert "SetInformationJobObject" in text
    assert "AssignProcessToJobObject" in text
