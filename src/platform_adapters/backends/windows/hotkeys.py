"""Windows RegisterHotKey backend and foreground focus guard."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
from ctypes import wintypes
import os
import threading

from platform_adapters.hotkey_bindings import parse_hotkey, to_win32

_LOCK = threading.RLock()
_THREAD: threading.Thread | None = None


def refresh(bindings: Mapping[str, str], dispatch: Callable[[str], None]) -> bool:
    global _THREAD
    with _LOCK:
        stop()
        specs: dict[int, tuple[int, int, str]] = {}
        hotkey_id = 0xC000
        for action, value in bindings.items():
            parsed = to_win32(value)
            if parsed is None:
                continue
            specs[hotkey_id] = (*parsed, str(action))
            hotkey_id += 1
        if not specs:
            return False
        ready = threading.Event()
        stopped = threading.Event()
        thread = threading.Thread(
            target=_message_loop,
            args=(specs, dispatch, ready, stopped),
            daemon=True,
            name="ShangBackgroundHotkeyThread",
        )
        thread._stopped_event = stopped  # type: ignore[attr-defined]
        thread.start()
        _THREAD = thread
        ready.wait(1.0)
        return bool(getattr(thread, "_registered_count", 0))


def stop() -> None:
    global _THREAD
    with _LOCK:
        thread = _THREAD
        _THREAD = None
        if thread is None:
            return
        thread_id = int(getattr(thread, "_win_thread_id", 0) or 0)
        if thread_id:
            ctypes.windll.user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)
        stopped = getattr(thread, "_stopped_event", None)
        if stopped is not None:
            stopped.wait(3.0)
        thread.join(timeout=0.3)


def _message_loop(
    specs: Mapping[int, tuple[int, int, str]],
    dispatch: Callable[[str], None],
    ready: threading.Event,
    stopped: threading.Event,
) -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    thread = threading.current_thread()
    thread._win_thread_id = int(kernel32.GetCurrentThreadId())  # type: ignore[attr-defined]
    registered: list[int] = []
    try:
        MOD_NOREPEAT = 0x4000
        for hotkey_id, (mod, vk, _action) in specs.items():
            ok = bool(user32.RegisterHotKey(None, hotkey_id, mod | MOD_NOREPEAT, vk))
            if not ok:
                ok = bool(user32.RegisterHotKey(None, hotkey_id, mod, vk))
            if ok:
                registered.append(hotkey_id)
        thread._registered_count = len(registered)  # type: ignore[attr-defined]
        ready.set()
        if not registered:
            return
        msg = wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == 0x0312:
                record = specs.get(int(msg.wParam))
                if record is not None:
                    dispatch(record[2])
    finally:
        for hotkey_id in registered:
            try:
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
        ready.set()
        stopped.set()


def focus_block_reason(_action: str, binding: str) -> str:
    parsed = parse_hotkey(binding)
    if parsed is None or not parsed.is_focus_sensitive:
        return ""
    snapshot = _focus_snapshot()
    if snapshot is None:
        return ""
    flags = int(snapshot.get("flags") or 0)
    if flags & (0x00000002 | 0x00000004 | 0x00000008 | 0x00000010):
        return "当前窗口正在显示菜单、弹出菜单或处于拖动/调整大小状态"
    focus_class = str(snapshot.get("focus_class") or "")
    foreground_class = str(snapshot.get("foreground_class") or "")
    hover_class = str(snapshot.get("hover_class") or "")
    pid = int(snapshot.get("pid") or 0)
    caret = int(snapshot.get("caret") or 0)
    text_tokens = ("edit", "richedit", "scintilla", "textbox", "textinput", "qlineedit", "qtextedit")
    if caret or _class_matches(focus_class, text_tokens):
        return "当前窗口存在文本编辑焦点，已避免打字时误触"
    if pid == os.getpid():
        if "record" in foreground_class.lower() or "hotkey" in foreground_class.lower():
            return "当前在快捷键录制窗口内"
        return ""
    desktop_classes = {"Progman", "WorkerW"}
    explorer_classes = {"CabinetWClass", "ExploreWClass"}
    list_tokens = ("syslistview32", "directuihwnd", "shelldll_defview", "uiviewwndclass")
    if foreground_class in explorer_classes:
        return "当前焦点在资源管理器窗口内，简单热键已保护"
    if _class_matches(focus_class, list_tokens) or _class_matches(hover_class, list_tokens):
        return "当前焦点在文件或列表区域，简单热键已保护"
    if foreground_class not in desktop_classes:
        return f"当前前台窗口不是桌面({foreground_class or 'unknown'})，简单热键已保护"
    return ""


def _class_matches(value: str, tokens: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(token in lowered for token in tokens)


def _window_class(user32, hwnd) -> str:
    if not hwnd:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return str(buffer.value or "")


def _focus_snapshot() -> dict[str, object] | None:
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        gui = GUITHREADINFO()
        gui.cbSize = ctypes.sizeof(GUITHREADINFO)
        if thread_id:
            user32.GetGUIThreadInfo(thread_id, ctypes.byref(gui))
        focus = gui.hwndFocus or hwnd
        active = gui.hwndActive or hwnd
        point = POINT()
        hover = user32.WindowFromPoint(point) if user32.GetCursorPos(ctypes.byref(point)) else None
        return {
            "flags": int(gui.flags or 0),
            "caret": int(gui.hwndCaret or 0),
            "pid": int(pid.value or 0),
            "foreground_class": _window_class(user32, hwnd),
            "active_class": _window_class(user32, active),
            "focus_class": _window_class(user32, focus),
            "hover_class": _window_class(user32, hover),
        }
    except Exception:
        return None
