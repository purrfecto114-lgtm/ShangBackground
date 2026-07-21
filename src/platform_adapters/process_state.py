"""Safe persistence and termination of wallpaper child processes.

A PID alone is not an identity: operating systems reuse PIDs.  State files
therefore include the process creation timestamp and executable path.  A stale
or legacy PID-only file is never used for destructive termination.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

SCHEMA_VERSION = 2


def read_state(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        if raw.startswith("{"):
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        return {"schema": 1, "pid": int(raw), "legacy_pid_only": True}
    except Exception:
        return {}


def _process_snapshot(pid: int) -> dict[str, Any]:
    if psutil is None:
        return {}
    proc = psutil.Process(int(pid))
    with proc.oneshot():
        try:
            executable = os.path.realpath(proc.exe())
        except Exception:
            executable = ""
        try:
            username = proc.username()
        except Exception:
            username = ""
        try:
            cmdline = proc.cmdline()
        except Exception:
            cmdline = []
        return {
            "pid": int(pid),
            "create_time": float(proc.create_time()),
            "executable": executable,
            "username": username,
            "cmdline": [str(item) for item in cmdline],
        }


def write_state(
    path: str | os.PathLike[str],
    pid: int,
    *,
    kind: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "kind": str(kind),
        "written_at": time.time(),
        "pid": int(pid),
    }
    try:
        state.update(_process_snapshot(int(pid)))
    except Exception:
        # No psutil or a process that exited before the snapshot.  Keep the
        # file for diagnostics but it will not be eligible for PID-based kill.
        state["identity_unavailable"] = True
    if extra:
        state.update(extra)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)
    return state


def process_for_state(state: dict[str, Any], *, expected_kind: str | None = None):
    """Return a verified psutil.Process, or ``None`` when identity is uncertain."""
    if psutil is None or not isinstance(state, dict):
        return None
    if int(state.get("schema") or 0) < SCHEMA_VERSION:
        return None
    if expected_kind and str(state.get("kind") or "") != str(expected_kind):
        return None
    try:
        pid = int(state.get("pid") or 0)
        expected_created = float(state.get("create_time"))
    except (TypeError, ValueError):
        return None
    if pid <= 0 or expected_created <= 0:
        return None
    try:
        proc = psutil.Process(pid)
        actual_created = float(proc.create_time())
        if abs(actual_created - expected_created) > 0.01:
            return None
        expected_exe = os.path.realpath(str(state.get("executable") or ""))
        if expected_exe:
            try:
                actual_exe = os.path.realpath(proc.exe())
            except Exception:
                return None
            if os.path.normcase(actual_exe) != os.path.normcase(expected_exe):
                return None
        expected_user = str(state.get("username") or "")
        if expected_user:
            try:
                if proc.username() != expected_user:
                    return None
            except Exception:
                return None
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return None
        return proc
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, OSError):
        return None


def is_running(path: str | os.PathLike[str], *, expected_kind: str) -> bool:
    return process_for_state(read_state(path), expected_kind=expected_kind) is not None


def terminate_verified(
    path: str | os.PathLike[str],
    *,
    expected_kind: str,
    timeout: float = 3.0,
) -> bool:
    """Terminate only the exact process represented by a verified state file.

    Returns True when the process was verified and is now gone.  Returns False
    for stale, legacy, inaccessible, or otherwise ambiguous state.
    """
    state = read_state(path)
    proc = process_for_state(state, expected_kind=expected_kind)
    if proc is None:
        return False
    try:
        children = proc.children(recursive=True)
    except Exception:
        children = []
    for child in children:
        try:
            child.terminate()
        except Exception:
            pass
    try:
        proc.terminate()
    except Exception:
        return False
    try:
        _gone, alive = psutil.wait_procs([*children, proc], timeout=max(0.1, float(timeout)))
    except Exception:
        alive = [proc]
    killed: list[Any] = []
    for item in alive:
        try:
            # Re-check create_time immediately before escalation.  psutil's
            # Process object also guards many methods against PID reuse.
            if item.pid == proc.pid and abs(float(item.create_time()) - float(state["create_time"])) > 0.01:
                continue
            item.kill()
            killed.append(item)
        except Exception:
            pass
    if killed:
        try:
            psutil.wait_procs(killed, timeout=1.0)
        except Exception:
            pass
    try:
        return not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE
    except Exception:
        return True


def remove_state(path: str | os.PathLike[str]) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
