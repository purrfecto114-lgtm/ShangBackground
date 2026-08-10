from __future__ import annotations

import argparse
from pathlib import Path

from app.relaunch_service import RelaunchService
from app.support import _wait_for_relaunch_parent


def _service(tmp_path: Path, *, argv=None, events=None) -> RelaunchService:
    events = events if events is not None else []
    exe = tmp_path / "ShangBackground.exe"
    exe.write_bytes(b"stub")
    return RelaunchService(
        is_windows=True,
        is_frozen=lambda: True,
        executable_path=lambda: str(exe),
        base_dir=lambda: str(tmp_path),
        capture_session=lambda: events.append("capture"),
        persist_session=lambda: events.append("persist"),
        release_guard=lambda: events.append("release"),
        cleanup_tray=lambda: events.append("tray"),
        recover_guard=lambda: events.append("recover"),
        log=lambda *_args, **_kwargs: None,
        argv=lambda: tuple(argv or [str(exe)]),
        python_executable=lambda: str(exe),
        popen=lambda cmd, **_kwargs: events.append(("launch", list(cmd))),
    )


def _nonwindows_service(tmp_path: Path, *, argv=None, events=None) -> RelaunchService:
    events = events if events is not None else []
    entry = tmp_path / "main.py"
    entry.write_text("# stub\n", encoding="utf-8")
    python = tmp_path / "python"
    python.write_bytes(b"stub")
    return RelaunchService(
        is_windows=False,
        is_frozen=lambda: False,
        executable_path=lambda: str(entry),
        base_dir=lambda: str(tmp_path),
        capture_session=lambda: events.append("capture"),
        persist_session=lambda: events.append("persist"),
        release_guard=lambda: events.append("release"),
        cleanup_tray=lambda: events.append("tray"),
        recover_guard=lambda: events.append("recover"),
        log=lambda *_args, **_kwargs: None,
        argv=lambda: tuple(argv or [str(entry)]),
        python_executable=lambda: str(python),
        popen=lambda cmd, **kwargs: events.append(("launch", list(cmd), dict(kwargs))),
    )


def test_restart_launches_waiting_child_before_irreversible_cleanup(tmp_path: Path):
    events: list[object] = []
    service = _service(tmp_path, events=events)

    assert service.restart() is True

    names = [item[0] if isinstance(item, tuple) else item for item in events]
    assert names[:2] == ["capture", "persist"]
    assert names.index("launch") < names.index("release") < names.index("tray")
    launch = next(item for item in events if isinstance(item, tuple) and item[0] == "launch")[1]
    assert "--relaunch-wait-pid" in launch
    assert "--inherit-session-wallpaper" in launch
    assert "recover" not in names


def test_failed_spawn_does_not_release_or_recover_live_instance(tmp_path: Path):
    events: list[object] = []
    service = _service(tmp_path, events=events)

    def fail_popen(*_args, **_kwargs):
        events.append("launch-failed")
        raise OSError("simulated spawn failure")

    service._popen = fail_popen
    assert service.restart() is False
    assert events == ["capture", "persist", "launch-failed"]


def test_uac_cancellation_happens_before_exit_cleanup(tmp_path: Path):
    events: list[object] = []
    service = _service(tmp_path, events=events)
    service.is_windows_admin = lambda: False
    service._shell_execute_runas = lambda _exe, args, _cwd: (
        events.append(("runas", list(args))) or (False, "cancelled")
    )

    assert service.restart_as_admin() is False
    names = [item[0] if isinstance(item, tuple) else item for item in events]
    assert names == ["capture", "persist", "runas"]
    assert "release" not in names
    assert "recover" not in names


def test_filtered_restart_args_drop_stale_handoff_parent(tmp_path: Path):
    service = _service(
        tmp_path,
        argv=[
            "ShangBackground.exe",
            "--relaunch-wait-pid",
            "123",
            "--relaunch-wait-created-at=45.0",
            "--verbose",
        ],
    )
    args = service.filtered_restart_args()
    assert "123" not in args
    assert not any(arg.startswith("--relaunch-wait-") for arg in args)
    assert "--verbose" in args
    assert "--inherit-session-wallpaper" in args


def test_wait_for_relaunch_parent_ignores_pid_reuse_identity_mismatch():
    import os
    import psutil

    args = argparse.Namespace(
        relaunch_wait_pid=os.getpid(),
        relaunch_wait_created_at=psutil.Process(os.getpid()).create_time() - 1000.0,
    )
    assert _wait_for_relaunch_parent(args, timeout=0.1) is True

def test_nonwindows_restart_uses_same_safe_argument_filter_and_base_workdir(tmp_path: Path):
    events: list[object] = []
    service = _nonwindows_service(
        tmp_path,
        events=events,
        argv=[
            str(tmp_path / "main.py"),
            "--verbose",
            "--quit",
            "--wait-for-exit",
            "--internal-video-player",
            "--muted",
            "--set-wallpaper",
            "/tmp/old.jpg",
            "--relaunch-wait-pid",
            "123",
        ],
    )

    assert service.restart(["--custom-safe"]) is True

    names = [item[0] if isinstance(item, tuple) else item for item in events]
    assert names.index("launch") < names.index("release") < names.index("tray")
    launch = next(item for item in events if isinstance(item, tuple) and item[0] == "launch")
    cmd, kwargs = launch[1], launch[2]
    assert cmd[1] == str(tmp_path / "main.py")
    assert "--verbose" in cmd
    assert "--custom-safe" in cmd
    assert "--inherit-session-wallpaper" in cmd
    assert "--quit" not in cmd
    assert "--wait-for-exit" not in cmd
    assert "--internal-video-player" not in cmd
    assert "--muted" not in cmd
    assert "/tmp/old.jpg" not in cmd
    assert "123" not in cmd
    assert kwargs["cwd"] == str(tmp_path)


def test_nonwindows_failed_spawn_keeps_live_instance_intact(tmp_path: Path):
    events: list[object] = []
    service = _nonwindows_service(tmp_path, events=events)

    def fail_popen(*_args, **_kwargs):
        events.append("launch-failed")
        raise OSError("simulated nonwindows spawn failure")

    service._popen = fail_popen
    assert service.restart() is False
    assert events == ["capture", "persist", "launch-failed"]

