from __future__ import annotations

import json
from pathlib import Path
import queue
import threading
import time

import pytest

from build_tools.buildlib.gui import BuildHistory, BuildWorker, PresetManager


def test_preset_rejects_non_object_root(tmp_path: Path):
    manager = PresetManager(tmp_path)
    (tmp_path / "bad.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        manager.load("bad")


def test_history_ignores_invalid_root_and_writes_atomically(tmp_path: Path):
    path = tmp_path / "history.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")
    history = BuildHistory(path)

    assert history.entries() == []
    history.record({"exit_code": 0})

    assert json.loads(path.read_text(encoding="utf-8")) == [{"exit_code": 0}]
    assert not path.with_suffix(".json.tmp").exists()


def test_worker_stop_during_process_launch_is_not_lost(monkeypatch: pytest.MonkeyPatch):
    import build_tools.buildlib.gui as gui

    entered = threading.Event()
    release = threading.Event()
    terminated = threading.Event()

    class FakeStdout:
        def __iter__(self):
            return iter(())

    class FakeProcess:
        pid = 12345
        stdout = FakeStdout()

        def __init__(self):
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = -15 if terminated.is_set() else 0
            return self.returncode

    fake = FakeProcess()

    def delayed_popen(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return fake

    monkeypatch.setattr(gui.subprocess, "Popen", delayed_popen)
    monkeypatch.setattr(gui, "_terminate_process_tree", lambda process: terminated.set())
    events: queue.Queue[tuple[str, object]] = queue.Queue()
    worker = BuildWorker(events)

    worker.start(["fake-builder"])
    assert entered.wait(5)
    assert worker.running
    assert worker.stop() is True
    release.set()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and worker.running:
        time.sleep(0.01)

    assert terminated.is_set()
    assert not worker.running


def test_build_gui_creates_real_tk_layout_under_xvfb():
    import shutil
    import subprocess
    import sys

    xvfb_run = shutil.which("xvfb-run")
    if xvfb_run is None:
        pytest.skip("xvfb-run is unavailable")
    script = """
import time
import tkinter as tk
from build_tools.buildlib.gui import create_app
root = tk.Tk()
app = create_app(root)
root.update_idletasks()
root.update()
app._state.set(
    profile='lite',
    dry_run=True,
    skip_install=True,
    features={key: False for key in app._state.features},
)
app._start()
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    root.update()
    if not app._worker.running and 'Build completed' in app._state.status:
        break
    time.sleep(0.02)
else:
    raise SystemExit(f'GUI dry-run did not complete: {app._state.status!r}')
root.destroy()
"""
    result = subprocess.run(
        [xvfb_run, "-a", sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_open_output_does_not_create_phantom_release_directory(tmp_path: Path):
    from build_tools.buildlib.gui import _open_path

    output = tmp_path / "dist-pyinstaller" / "linux"
    with pytest.raises(FileNotFoundError, match="does not exist yet"):
        _open_path(output)

    assert not output.exists()


def test_app_state_rejects_invalid_preset_values():
    from build_tools.buildlib.gui import AppState

    state = AppState()
    with pytest.raises(ValueError, match="unsupported value"):
        state.restore_snapshot({"tool": "unknown-builder"})
    with pytest.raises(ValueError, match="unknown feature"):
        state.restore_snapshot({"features": {"html": True, "invented": True}})
    with pytest.raises(ValueError, match="must be booleans"):
        state.restore_snapshot({"features": {"html": "yes"}})


def test_build_gui_tracks_canvas_width_when_resized_under_xvfb():
    import shutil
    import subprocess
    import sys

    xvfb_run = shutil.which("xvfb-run")
    if xvfb_run is None:
        pytest.skip("xvfb-run is unavailable")
    script = r'''
import tkinter as tk
from build_tools.buildlib.gui import create_app
root = tk.Tk()
app = create_app(root)
root.geometry("1120x800")
root.update_idletasks(); root.update()
canvas = next(w for w in root.winfo_children()[0].winfo_children() if isinstance(w, tk.Canvas))
for geometry in ("1600x1000", "900x640", "1400x900"):
    root.geometry(geometry)
    root.update_idletasks(); root.update()
    canvas_width = canvas.winfo_width()
    content_width = app._content_frame.winfo_width()
    if abs(canvas_width - content_width) > 2:
        raise SystemExit(f"width mismatch at {geometry}: canvas={canvas_width}, content={content_width}")
root.destroy()
'''
    result = subprocess.run(
        [xvfb_run, "-a", sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_build_gui_launcher_imports_local_entry_from_arbitrary_cwd(tmp_path: Path):
    import subprocess
    import sys

    project = Path(__file__).resolve().parents[1]
    script = project / "build_tools" / "build_gui.py"
    code = (
        "import runpy; "
        f"ns=runpy.run_path({str(script)!r}, run_name='not_main'); "
        "fn=ns['_load_run_gui'](); "
        "assert fn.__module__ == 'build_tools._entry'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
