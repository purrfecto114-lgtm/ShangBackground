"""Legacy WM_COPYDATA compatibility used only for older Windows instances."""
from __future__ import annotations

from collections.abc import Callable
import ctypes
import time
from typing import Any


def message_parent(hwnd_type: type, hwnd_message: int):
    try:
        return hwnd_type(hwnd_message)
    except Exception:
        try:
            return ctypes.c_void_p(hwnd_message)
        except Exception:
            bits = ctypes.sizeof(ctypes.c_void_p) * 8
            return ctypes.c_void_p((1 << bits) + hwnd_message)


def find_window(*, timeout: float, class_name: str, hwnd_type: type, hwnd_message: int,
                is_windows: bool, log: Callable[[str], None]):
    if not is_windows:
        return None
    deadline = time.time() + max(0.0, float(timeout))
    while True:
        try:
            user32 = ctypes.windll.user32
            existing = user32.FindWindowExW(message_parent(hwnd_type, hwnd_message), hwnd_type(0), class_name, None)
            if not existing:
                existing = user32.FindWindowW(class_name, None)
            if existing:
                return existing
        except Exception as exc:
            log(f"查找已有实例 IPC 窗口失败: {exc}")
        if time.time() >= deadline:
            return None
        time.sleep(0.1)


def send_command(*, target: Any, command: str, copydata_type: type, hwnd_type: type,
                 uint_type: type, wparam_type: type, lparam_type: type, wm_copydata: int,
                 is_windows: bool, win_int: Callable[[Any], int], log: Callable[[str], None]) -> bool:
    if not is_windows or not target:
        return False
    try:
        payload = str(command).encode("utf-8") + b"\x00"
        buffer = ctypes.create_string_buffer(payload)
        data = copydata_type()
        data.dwData = 1
        data.cbData = len(payload)
        data.lpData = ctypes.cast(buffer, ctypes.c_void_p)
        lparam = lparam_type(ctypes.addressof(data))
        # v1.6.1 fix: SendMessageW blocks without a timeout. If the primary
        # instance's GUI thread is busy (exit transaction, long wallpaper
        # switch), a second launch forwarding a tray/context-menu action hung
        # indefinitely. Use SendMessageTimeoutW with SMTO_ABORTIFHUNG (same
        # pattern as the context-menu fastpath) and a 1s budget.
        result = ctypes.c_size_t(0)
        sent = ctypes.windll.user32.SendMessageTimeoutW(
            hwnd_type(win_int(target)), uint_type(wm_copydata), wparam_type(0), lparam,
            0x0002,  # SMTO_ABORTIFHUNG
            1000,    # timeout in ms
            ctypes.byref(result),
        )
        if not sent:
            log("发送命令到已有实例超时或目标无响应")
            return False
        return int(result.value or 0) == 1
    except Exception as exc:
        log(f"发送命令到已有实例失败: {exc}")
        return False


def activate(*, existing: Any, send_show: Callable[[Any, str], bool], log: Callable[[str], None]) -> bool:
    if not existing:
        return False
    activated = send_show(existing, "show")
    try:
        user32 = ctypes.windll.user32
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(existing, None)
        attached = current_thread != target_thread
        if attached:
            user32.AttachThreadInput(current_thread, target_thread, True)
        user32.ShowWindow(existing, 9)
        user32.ShowWindow(existing, 1)
        user32.SetForegroundWindow(existing)
        user32.BringWindowToTop(existing)
        if attached:
            try:
                user32.AttachThreadInput(current_thread, target_thread, False)
            except Exception:
                pass
    except Exception as exc:
        log(f"激活已有实例失败: {exc}")
    return activated or existing is not None
