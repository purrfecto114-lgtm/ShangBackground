"""Minimal Windows desktop-context dispatch before the GUI stack is imported.

Explorer should never have to wait for PySide6/core application startup just to
forward a previous/next/random/jump command.  The common case talks directly to
the existing message-only compatibility window; a cold launch detaches a child
and returns to Explorer immediately.  The child then follows the normal,
authenticated application startup path.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import ctypes
import os
from pathlib import Path
import subprocess
import sys

_CONTEXT_FLAG = "--from-context-menu"
_CHILD_FLAG = "--context-menu-dispatched-child"
_WINDOW_CLASS = "ShangBackgroundIpcWindowClass"
_WM_COPYDATA = 0x004A
_SMTO_ABORTIFHUNG = 0x0002
_HWND_MESSAGE = -3
_LEGACY_FAST_COMMANDS = frozenset({"previous", "next", "random", "jump", "show"})


class _CopyDataStruct(ctypes.Structure):
    _fields_ = [
        ("dwData", ctypes.c_size_t),
        ("cbData", ctypes.c_uint32),
        ("lpData", ctypes.c_void_p),
    ]


def command_from_argv(argv: Sequence[str]) -> str | None:
    values = list(argv)
    if "--previous" in values:
        return "previous"
    if "--next" in values:
        return "next"
    if "--random" in values:
        return "random"
    if "--jump-to-wallpaper" in values:
        return "jump"
    if "--show" in values:
        return "show"
    if "--set-wallpaper" in values:
        index = values.index("--set-wallpaper")
        try:
            target = os.path.abspath(os.path.expanduser(values[index + 1]))
        except (IndexError, TypeError):
            return None
        return "set_wallpaper|" + target
    return None


def _message_parent() -> ctypes.c_void_p:
    bits = ctypes.sizeof(ctypes.c_void_p) * 8
    return ctypes.c_void_p((1 << bits) + _HWND_MESSAGE)


def _send_to_existing_instance(command: str, *, timeout_ms: int = 120) -> bool:
    """Best-effort legacy IPC with a hard timeout so Explorer cannot hang."""
    if os.name != "nt" or not command:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowExW.restype = ctypes.c_void_p
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.SendMessageTimeoutW.restype = ctypes.c_void_p
        hwnd = user32.FindWindowExW(_message_parent(), None, _WINDOW_CLASS, None)
        if not hwnd:
            hwnd = user32.FindWindowW(_WINDOW_CLASS, None)
        if not hwnd:
            return False

        payload = str(command).encode("utf-8") + b"\x00"
        buffer = ctypes.create_string_buffer(payload)
        data = _CopyDataStruct(
            1,
            len(payload),
            ctypes.cast(buffer, ctypes.c_void_p),
        )
        message_result = ctypes.c_size_t(0)
        sent = user32.SendMessageTimeoutW(
            hwnd,
            _WM_COPYDATA,
            0,
            ctypes.byref(data),
            _SMTO_ABORTIFHUNG,
            max(1, int(timeout_ms)),
            ctypes.byref(message_result),
        )
        return bool(sent) and int(message_result.value) == 1
    except Exception:
        return False


def _is_packaged_runtime() -> bool:
    if bool(getattr(sys, "frozen", False)):
        return True
    if globals().get("__compiled__") is not None:
        return True
    main_module = sys.modules.get("__main__")
    return bool(main_module is not None and getattr(main_module, "__compiled__", None) is not None)


def _source_interpreter() -> str:
    executable = Path(sys.executable)
    if os.name == "nt":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            return os.fspath(pythonw)
    return os.fspath(executable)


def detached_child_command(argv: Sequence[str]) -> list[str]:
    """Build the exact child command without importing application modules."""
    values = list(argv)
    rest = values[1:] if values else []
    if _CHILD_FLAG not in rest:
        rest.append(_CHILD_FLAG)
    if _is_packaged_runtime():
        return [sys.executable, *rest]

    entry = Path(values[0] if values else sys.argv[0]).expanduser()
    try:
        entry = entry.resolve()
    except OSError:
        entry = entry.absolute()
    return [_source_interpreter(), os.fspath(entry), *rest]


def _spawn_detached(argv: Sequence[str]) -> bool:
    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            )
        subprocess.Popen(
            detached_child_command(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def handle_context_menu_fastpath(
    argv: Sequence[str] | None = None,
    *,
    is_windows: bool | None = None,
    sender: Callable[[str], bool] | None = None,
    spawner: Callable[[Sequence[str]], bool] | None = None,
) -> int | None:
    """Handle an Explorer launch before ``app.entry`` is imported.

    ``None`` means "continue normal startup".  A zero result means either the
    command was delivered to an existing instance or a detached cold-start
    child was launched successfully.
    """
    values = list(sys.argv if argv is None else argv)
    windows = os.name == "nt" if is_windows is None else bool(is_windows)
    if not windows or _CONTEXT_FLAG not in values or _CHILD_FLAG in values:
        return None
    command = command_from_argv(values)
    if not command:
        return None

    send = sender or _send_to_existing_instance
    # The legacy window predates authenticated QLocalSocket IPC. Restrict the
    # accelerator to fixed, non-payload shell actions; path-bearing commands
    # take the normal detached/authenticated route instead.
    if command in _LEGACY_FAST_COMMANDS and send(command):
        return 0

    spawn = spawner or _spawn_detached
    if spawn(values):
        return 0
    # Fall back to the historical in-process path if detaching failed. Losing a
    # wallpaper command is worse than one slow Explorer invocation.
    return None


__all__ = [
    "command_from_argv",
    "detached_child_command",
    "handle_context_menu_fastpath",
]
