from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group escalation test")
def test_sigterm_kills_compiler_tree_even_when_child_ignores_term(tmp_path: Path):
    child_pid_file = tmp_path / "child.pid"
    child_code = (
        "import os,pathlib,signal,time; "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    )
    wrapper_code = (
        "from pathlib import Path; import sys; "
        "import build_tools.buildlib.runner as runner; "
        f"runner.PROJECT_ROOT=Path({str(tmp_path)!r}); "
        f"raise SystemExit(runner.run_build([sys.executable, '-c', {child_code!r}], "
        "target='linux', dry_run=False))"
    )
    wrapper = subprocess.Popen(
        [sys.executable, "-c", wrapper_code],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not child_pid_file.is_file():
        if wrapper.poll() is not None:
            pytest.fail(f"build wrapper exited before child started: {wrapper.returncode}")
        time.sleep(0.05)
    assert child_pid_file.is_file()
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))

    wrapper.send_signal(signal.SIGTERM)
    assert wrapper.wait(timeout=10) == 128 + signal.SIGTERM

    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
