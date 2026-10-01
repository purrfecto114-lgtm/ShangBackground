from __future__ import annotations

from app.paths import (
    PROJECT_ROOT,
    RESOURCE_ROOT,
    app_executable_path,
    entry_script_path,
    external_media_runtime_allowed,
    is_packaged_runtime,
    mpv_bundled_exe,
)
from app.libmpv_runtime import runtime_available as libmpv_runtime_available
from app.build_features import use_internal_libmpv
from app.mpv_backend import PollingPropertyObserver

import argparse
import ctypes
import ctypes.wintypes
import re
import os
import secrets
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None

from app.config import IS_WINDOWS
from platform_adapters import process_state
from platform_adapters.mpv_ipc import MpvIpcClient, verify_media_playing
from platform_adapters.windows_job import attach_process_kill_on_close

_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "ShangBackground")
os.makedirs(_DATA_DIR, exist_ok=True)
PID_FILE = os.path.join(_DATA_DIR, "video_wallpaper.pid")


VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv")
PROCESS_KIND = "video-wallpaper-windows"
_CURRENT_PROC: subprocess.Popen | None = None
_CURRENT_JOB = None
_CANDIDATE_CACHE: dict[tuple[str, ...], tuple[float, tuple[str, ...]]] = {}
_CANDIDATE_CACHE_SECONDS = 30.0
PLAYER_LOG = os.path.join(_DATA_DIR, "video-player.log")

# mpv IPC 专用内核接口（64 位句柄安全）：仅在 Windows 上绑定；其他平台保持
# None 占位 —— 本模块必须能在任意平台安全导入（契约测试在 Linux 上断言 _KERNEL32 is None）。
if sys.platform == "win32":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _bind_kernel32_prototypes(kernel32) -> None:
        """为 mpv IPC 用到的内核函数补齐签名（避免 64 位句柄被截断）。"""
        kernel32.CreateFileW.argtypes = [
            ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.LPVOID,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        kernel32.WriteFile.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.LPCVOID,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.DWORD),
            ctypes.wintypes.LPVOID,
        ]
        kernel32.WriteFile.restype = ctypes.wintypes.BOOL
        kernel32.ReadFile.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.LPVOID,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.DWORD),
            ctypes.wintypes.LPVOID,
        ]
        kernel32.ReadFile.restype = ctypes.wintypes.BOOL
        kernel32.PeekNamedPipe.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.LPVOID,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.DWORD),
            ctypes.POINTER(ctypes.wintypes.DWORD),
            ctypes.POINTER(ctypes.wintypes.DWORD),
        ]
        kernel32.PeekNamedPipe.restype = ctypes.wintypes.BOOL
        kernel32.WaitNamedPipeW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD]
        kernel32.WaitNamedPipeW.restype = ctypes.wintypes.BOOL

    _bind_kernel32_prototypes(_KERNEL32)
    _INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value
else:
    _KERNEL32 = None
    _INVALID_HANDLE_VALUE = None


def validate_video_path(path: str | None) -> bool:
    return bool(path and os.path.isfile(path) and path.lower().endswith(VIDEO_EXTENSIONS))


def _read_state() -> dict[str, object]:
    return process_state.read_state(PID_FILE)


def _write_state(pid: int, player: str, hwnd: int | None = None, ipc_path: str = "") -> None:
    process_state.write_state(
        PID_FILE,
        pid,
        kind=PROCESS_KIND,
        extra={
            "player": player,
            "hwnd": int(hwnd or 0),
            "ipc_path": ipc_path or "",
        },
    )


def _read_pid() -> int | None:
    try:
        pid = _read_state().get("pid")
        return int(pid) if pid else None
    except Exception:
        return None


def _close_current_job() -> None:
    global _CURRENT_JOB
    job = _CURRENT_JOB
    _CURRENT_JOB = None
    if job is not None:
        try:
            job.close()
        except Exception:
            pass


def _stop_tracked_process() -> None:
    global _CURRENT_PROC
    proc = _CURRENT_PROC
    _CURRENT_PROC = None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass
    except Exception:
        pass


def stop_video_wallpaper() -> None:
    _stop_property_observers()
    _stop_tracked_process()
    process_state.terminate_verified(PID_FILE, expected_kind=PROCESS_KIND)
    # Closing the job is the final containment fallback for descendants that
    # survived or were re-parented after the renderer root exited.
    _close_current_job()
    process_state.remove_state(PID_FILE)


def is_video_wallpaper_running() -> bool:
    if _CURRENT_PROC is not None and _CURRENT_PROC.poll() is None:
        return True
    return process_state.is_running(PID_FILE, expected_kind=PROCESS_KIND)


def _split_registry_command(command: str) -> str | None:
    """Extract executable path from a Windows registry command string."""
    command = (command or "").strip()
    if not command:
        return None
    try:
        parts = shlex.split(command, posix=False)
    except Exception:
        parts = []
    if parts:
        exe = parts[0].strip('"')
        if os.path.isfile(exe):
            return exe
    match = re.match(r'^"([^"]+\.exe)"', command, re.IGNORECASE)
    if match and os.path.isfile(match.group(1)):
        return match.group(1)
    match = re.match(r"^(.*?\.exe)(?:\s|$)", command, re.IGNORECASE)
    if match:
        exe = match.group(1).strip('"')
        if os.path.isfile(exe):
            return exe
    return None


def _registry_default_value(winreg_module, key) -> str | None:
    """Read a default registry value; support both None and empty-name forms."""
    for value_name in (None, ""):
        try:
            value, _ = winreg_module.QueryValueEx(key, value_name)
            if value:
                return str(value)
        except Exception:
            pass
    return None


def _registry_executable_candidates(exe_name: str) -> list[str]:
    """Find executables registered by mpv-register.bat/VLC installers without relying on PATH.

    mpv's register helper writes Windows registry integration in-place.  That can
    make mpv visible to ShellExecute/Default Apps while still invisible to
    CreateProcess/PATH lookup, so we read App Paths and open-command entries
    directly before falling back to common directories.
    """
    result: list[str] = []
    if not IS_WINDOWS:
        return result
    try:
        import winreg
    except Exception:
        return result

    exe_lower = exe_name.lower()
    app_path_names = {exe_name}
    if exe_lower == "mpv":
        app_path_names.add("mpv.exe")
    if exe_lower == "vlc":
        app_path_names.add("vlc.exe")

    roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
    app_path_keys = []
    for item in app_path_names:
        app_path_keys.extend(
            [
                rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{item}",
                rf"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{item}",
            ]
        )

    if "mpv" in exe_lower:
        open_command_keys = [
            r"Software\Classes\Applications\mpv.exe\shell\open\command",
            r"Software\Classes\mpv.exe\shell\open\command",
            r"Software\Classes\mpv\shell\open\command",
            r"Software\Classes\MpvFile\shell\open\command",
            r"Software\Clients\Media\mpv\shell\open\command",
        ]
    elif "vlc" in exe_lower:
        open_command_keys = [
            r"Software\Classes\Applications\vlc.exe\shell\open\command",
            r"Software\Classes\VLC.mp4\shell\Open\command",
            r"Software\Clients\Media\VLC\shell\open\command",
        ]
    else:
        open_command_keys = [rf"Software\Classes\Applications\{exe_name}\shell\open\command"]

    for root_key in roots:
        for subkey in app_path_keys:
            try:
                with winreg.OpenKey(root_key, subkey) as key:
                    value = _registry_default_value(winreg, key)
                    if value and os.path.isfile(value):
                        result.append(value)
            except Exception:
                pass
        for subkey in open_command_keys:
            try:
                with winreg.OpenKey(root_key, subkey) as key:
                    value = _registry_default_value(winreg, key)
                    exe = _split_registry_command(value or "")
                    if exe:
                        result.append(exe)
            except Exception:
                pass
    for subkey in (
        rf"Applications\{exe_name}\shell\open\command",
        r"Applications\mpv.exe\shell\open\command" if "mpv" in exe_lower else "",
        r"Applications\vlc.exe\shell\open\command" if "vlc" in exe_lower else "",
    ):
        if not subkey:
            continue
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, subkey) as key:
                value = _registry_default_value(winreg, key)
                exe = _split_registry_command(value or "")
                if exe:
                    result.append(exe)
        except Exception:
            pass
    return result


def _candidate_paths_uncached(*names: str) -> list[str]:
    result: list[str] = []
    allow_external = external_media_runtime_allowed()
    # v1.4.6: 统一用 app.paths.resolve_mpv_path() 解析 mpv, 优先级:
    #   1. 用户运行时目录 %LOCALAPPDATA%\ShangBackground\bin\mpv\
    #   2. 打包内置 <resource_root>/bin/
    #   3. 系统 PATH (shutil.which)
    # 清理过期查找路径: mpv.net (已停更), scoop 路径 (用户特定), Software\Clients\Media (罕见).
    _names_lower = [n.lower() for n in names]
    if "mpv.exe" in _names_lower or "mpv" in _names_lower:
        try:
            from app.paths import resolve_mpv_path

            resolved = resolve_mpv_path()
            if resolved:
                result.append(resolved)
        except Exception:
            pass
        # 保留打包内置兜底 (resolve_mpv_path 已含, 但显式再查一次以防导入失败)
        try:
            bundled = mpv_bundled_exe()
            if bundled and bundled not in result:
                result.append(bundled)
        except Exception:
            pass
    if not allow_external:
        deduped: list[str] = []
        seen: set[str] = set()
        wanted = {name.lower() for name in names}
        for path in result:
            try:
                key = os.path.normcase(os.path.abspath(path))
            except Exception:
                continue
            if os.path.basename(path).lower() in wanted and key not in seen and os.path.isfile(path):
                seen.add(key)
                deduped.append(path)
        return deduped
    for name in names:
        found = shutil.which(name)
        if found:
            result.append(found)
        result.extend(_registry_executable_candidates(name))

    base_dirs = [
        os.path.dirname(sys.executable),
        # v1.6.1 security fix: removed os.getcwd(). Resolving players from the
        # current working directory let a dropped "mpv.exe"/"vlc.exe" in a
        # download folder (typical portable-launch scenario) execute silently
        # whenever the app started from that folder in system mode.
        os.fspath(RESOURCE_ROOT),
        os.fspath(PROJECT_ROOT),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramW6432"),
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("APPDATA"),
    ]
    # v1.4.6: 移除 mpv.net (已停更) 和 scoop 路径 (用户特定, 非通用)
    common_parts = [
        ("mpv", "mpv.exe"),
        ("mpv", "current", "mpv.exe"),
        ("VideoLAN", "VLC", "vlc.exe"),
        ("Programs", "mpv", "mpv.exe"),
        ("Programs", "VideoLAN", "VLC", "vlc.exe"),
    ]
    for base in base_dirs:
        if not base:
            continue
        for name in names:
            candidate = os.path.join(base, name)
            if os.path.isfile(candidate):
                result.append(candidate)
        for parts in common_parts:
            candidate = os.path.join(base, *parts)
            if os.path.isfile(candidate):
                result.append(candidate)
    deduped: list[str] = []
    seen: set[str] = set()
    wanted = {n.lower() for n in names}
    for path in result:
        try:
            if os.path.basename(path).lower() not in wanted:
                continue
            key = os.path.normcase(os.path.abspath(path))
        except Exception:
            continue
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            deduped.append(path)
    return deduped


def _candidate_paths(*names: str) -> list[str]:
    key = tuple(sorted(str(name).lower() for name in names))
    now = time.monotonic()
    cached = _CANDIDATE_CACHE.get(key)
    if cached is not None and now - cached[0] <= _CANDIDATE_CACHE_SECONDS:
        valid = [path for path in cached[1] if os.path.isfile(path)]
        if valid:
            return valid
    values = _candidate_paths_uncached(*names)
    _CANDIDATE_CACHE[key] = (now, tuple(values))
    return values


def _rotate_player_log() -> None:
    try:
        if os.path.getsize(PLAYER_LOG) > 512 * 1024:
            backup = PLAYER_LOG + ".1"
            try:
                os.replace(PLAYER_LOG, backup)
            except OSError:
                os.remove(PLAYER_LOG)
    except OSError:
        pass


def _find_workerw() -> int:
    """Create/find the hidden WorkerW window behind desktop icons."""
    user32 = ctypes.windll.user32
    # v1.6.1 fix: declare full Win64-safe signatures. Previously
    # FindWindowW/FindWindowExW returned truncated c_int HWNDs and
    # SendMessageTimeoutW received a 4-byte c_ulong buffer where the API
    # writes an 8-byte DWORD_PTR on x64 (stack/heap corruption risk).
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowExW.restype = ctypes.c_void_p
    user32.SendMessageTimeoutW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_size_t
    progman = user32.FindWindowW("Progman", None)
    result = ctypes.c_size_t(0)
    try:
        user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0x0002, 1000, ctypes.byref(result))
    except Exception:
        pass

    workerw = ctypes.c_void_p(0)
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _enum(hwnd, _lparam):
        shell = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if shell:
            candidate = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if candidate:
                workerw.value = candidate
                return False
        return True

    user32.EnumWindows(EnumWindowsProc(_enum), 0)
    return int(workerw.value or progman or 0)


def _mpv_ipc_path(pid: int | None = None) -> str:
    """Build the Windows named-pipe path used for mpv JSON IPC.

    mpv IPC is not a security boundary, so avoid predictable PID-derived
    pipe names.  The generated pipe path is persisted in the private state
    file for the live-control helper.
    """
    suffix = secrets.token_hex(8)
    return rf"\\.\pipe\shangbg-mpv-{suffix}"


def _internal_libmpv_command(
    video_path: str, muted: bool, volume: int, workerw: int
) -> tuple[str, list[str], str] | None:
    """Build the legacy isolated ctypes/libmpv fallback command.

    Only use the internal ctypes/libmpv player when the
    build manifest explicitly bundled libmpv (mode == "bundled"). When the
    manifest says "system" (as in our --mpv-runtime system builds), spawning
    the full packaged executable as a child process just to play a video
    causes massive memory/CPU overhead — the child loads ALL Qt DLLs, Python
    modules, and resources before even reaching the libmpv ctypes call.

    If video_runtime_mode() is "system" or "disabled", skip the internal
    libmpv path.  In bundled mode this function is now a compatibility fallback
    for older libmpv-only payloads; a bundled mpv.exe is preferred because it
    avoids launching a second full ShangBackground runtime as the player.
    """
    if not libmpv_runtime_available():
        return None
    if not workerw:
        return None
    # v1.4.4: Don't spawn the full packaged app as a child process when the
    # build didn't bundle libmpv. The "system" mode means we should use the
    # external mpv.exe, not the internal ctypes player.
    # 门控统一：与 linux/video.py 共用 build_features.use_internal_libmpv()。
    if not use_internal_libmpv():
        return None
    ipc_path = _mpv_ipc_path()
    if is_packaged_runtime():
        cmd = [app_executable_path()]
    else:
        cmd = [sys.executable, entry_script_path()]
    cmd.extend(
        [
            "--internal-libmpv-player",
            os.path.abspath(video_path),
            "--wid",
            str(workerw),
            "--ipc-path",
            ipc_path,
            "--volume",
            str(max(0, min(100, int(volume)))),
        ]
    )
    if muted:
        cmd.append("--muted")
    return "libmpv", cmd, ipc_path


def _mpv_command(video_path: str, muted: bool, volume: int, workerw: int) -> tuple[str, list[str], str] | None:
    candidates = _candidate_paths("mpv", "mpv.exe")
    mpv = next((path for path in candidates if os.path.basename(path).lower() in {"mpv", "mpv.exe"}), None)
    if not mpv:
        return None
    if not workerw:
        return None
    wid_arg = f"--wid={workerw}"
    # mpv keeps volume and mute as separate properties. Preserve the saved
    # volume while muted so a later hot unmute restores it immediately.
    clamped_volume = max(0, min(100, int(volume)))
    # IPC socket so the GUI can adjust volume/mute live without restarting mpv.
    # See https://mpv.io/manual/stable/#json-ipc for the protocol.
    ipc_path = _mpv_ipc_path()
    cmd = [
        mpv,
        wid_arg,
        "--no-config",
        "--load-scripts=no",
        "--autoload-files=no",
        "--sub-auto=no",
        "--audio-file-auto=no",
        "--loop-file=inf",
        "--hwdec=auto-safe",
        "--no-osc",
        "--no-osd-bar",
        "--no-input-default-bindings",
        "--keep-open=yes",
        "--panscan=1.0",
        "--keepaspect=no",
        "--keepaspect-window=no",
        "--no-border",
        "--really-quiet",
        f"--input-ipc-server={ipc_path}",
        f"--volume={clamped_volume}",
        f"--mute={'yes' if muted else 'no'}",
        os.path.abspath(video_path),
    ]
    return "mpv", cmd, ipc_path


def _vlc_command(video_path: str, muted: bool, volume: int = 100) -> tuple[str, list[str], str] | None:
    candidates = _candidate_paths("vlc", "vlc.exe")
    vlc = next((p for p in candidates if os.path.basename(p).lower() in {"vlc", "vlc.exe"}), None)
    if not vlc:
        return None
    # VLC 原生 --video-wallpaper 已负责桌面壁纸模式，不再叠加 --fullscreen。
    # VLC 的 RC 接口在 Windows 命名管道上不稳定，因此 VLC 后端不启用 IPC；
    # GUI 在 VLC 后端上调整音量时回退到 stop+restart（与历史行为一致）。
    cmd = [
        vlc,
        "--video-wallpaper",
        "--loop",
        "--no-video-title-show",
        "--no-osd",
        "--no-video-deco",
        "--qt-start-minimized",
        "--no-qt-system-tray",
        "--avcodec-hw=any",
    ]
    # v1.6.0 审计修复（REVIEW 3.2 同源问题）: 静音时不再传 --volume=0。
    # 与 mpv 侧修法同语义：保留已保存的音量值，解除静音（GUI 走 stop+restart）
    # 时立即恢复，避免静音期间把用户音量清零。
    # VLC's --volume uses 0-1024 where 256 == 100%.  Map 0-100 → 0-256
    # so the slider's percentage matches the user's mental model.
    clamped_volume = max(0, min(100, int(volume)))
    mapped_volume = int(clamped_volume * 256 / 100)
    if muted:
        cmd.extend(["--no-audio", f"--volume={mapped_volume}"])
    else:
        cmd.append(f"--volume={mapped_volume}")
    cmd.append(os.path.abspath(video_path))
    return "vlc", cmd, ""


def _command_for_log(cmd: list[str]) -> str:
    """Return a diagnostic command without local media paths or IPC names."""
    redacted: list[str] = []
    for index, value in enumerate(cmd):
        text = str(value)
        lower = text.lower()
        if lower.startswith(("--input-ipc-server=", "--ipc-path=")):
            redacted.append(text.split("=", 1)[0] + "=<redacted>")
        elif index > 0 and os.path.isfile(text):
            redacted.append("<local-file>")
        elif lower.startswith(r"\\.\pipe\shangbg-"):
            redacted.append("<ipc-pipe>")
        else:
            redacted.append(text)
    return subprocess.list2cmdline(redacted)


def _launch(cmd: list[str]) -> subprocess.Popen:
    global _CURRENT_PROC, _CURRENT_JOB
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    _rotate_player_log()
    with open(PLAYER_LOG, "a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launch: {_command_for_log(cmd)}\n")
        log.flush()
        _CURRENT_PROC = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        _CURRENT_JOB = attach_process_kill_on_close(_CURRENT_PROC)
    return _CURRENT_PROC


def _terminate_failed_player(process: subprocess.Popen) -> None:
    global _CURRENT_PROC
    try:
        process.terminate()
        process.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=1.0)
        except Exception:
            pass
    except Exception:
        pass
    if _CURRENT_PROC is process:
        _CURRENT_PROC = None
        _close_current_job()


class _MpvNamedPipeChannel:
    """ctypes 命名管道读写通道：为 MpvIpcClient 提供注入式 I/O。

    read_bytes 契约：返回 bytes（可能是不完整分片）、None（超时内无数据）、
    b""（EOF）或抛异常（管道破裂）。用 PeekNamedPipe 做非阻塞可读字节查询，
    避免无数据时阻塞在 ReadFile 上。
    """

    _READ_CHUNK = 65536
    _POLL_INTERVAL = 0.02  # 50 Hz 足够就绪/属性轮询，避免 200 Hz 忙等空转 CPU

    def __init__(self, handle: int) -> None:
        self._handle = int(handle)

    def write_bytes(self, payload: bytes) -> int:
        if _KERNEL32 is None:
            raise OSError("named pipe is only available on Windows")
        data = bytes(payload)
        written = ctypes.wintypes.DWORD(0)
        ok = _KERNEL32.WriteFile(
            ctypes.wintypes.HANDLE(self._handle),
            data,
            len(data),
            ctypes.byref(written),
            None,
        )
        if not ok or written.value <= 0:
            raise OSError(f"WriteFile failed (error={ctypes.get_last_error()})")
        return int(written.value)

    def read_bytes(self, timeout: float) -> bytes | None:
        if _KERNEL32 is None:
            raise OSError("named pipe is only available on Windows")
        deadline = time.monotonic() + max(0.0, float(timeout))
        handle = ctypes.wintypes.HANDLE(self._handle)
        while True:
            available = ctypes.wintypes.DWORD(0)
            if not _KERNEL32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                # 管道已关闭/破裂：交由协议层判死。
                raise OSError(f"PeekNamedPipe failed (error={ctypes.get_last_error()})")
            if available.value > 0:
                to_read = min(int(available.value), self._READ_CHUNK)
                buffer = ctypes.create_string_buffer(to_read)
                read = ctypes.wintypes.DWORD(0)
                if not _KERNEL32.ReadFile(handle, buffer, to_read, ctypes.byref(read), None):
                    raise OSError(f"ReadFile failed (error={ctypes.get_last_error()})")
                data = buffer.raw[: read.value]
                return data if data else b""
            if time.monotonic() >= deadline:
                return None
            time.sleep(self._POLL_INTERVAL)

    def close(self) -> None:
        if _KERNEL32 is None or not self._handle:
            return
        try:
            _KERNEL32.CloseHandle(ctypes.wintypes.HANDLE(self._handle))
        except Exception:
            pass
        self._handle = 0


def _open_ipc_pipe(ipc_path: str, timeout: float = 3.0) -> int | None:
    """以读写模式打开 mpv 命名管道（CreateFileW）。

    mpv 建立管道服务端存在短暂窗口：ERROR_PIPE_BUSY 时用 WaitNamedPipeW 等
    待空闲实例；其余错误（含管道尚未创建）按固定间隔轮询直至超时。
    """
    if _KERNEL32 is None:
        return None
    generic_read_write = 0xC0000000  # GENERIC_READ | GENERIC_WRITE
    open_existing = 3
    error_pipe_busy = 231
    deadline = time.monotonic() + max(0.2, float(timeout))
    while time.monotonic() < deadline:
        handle = _KERNEL32.CreateFileW(ipc_path, generic_read_write, 0, None, open_existing, 0, None)
        if handle not in (None, _INVALID_HANDLE_VALUE):
            return int(handle)
        if ctypes.get_last_error() == error_pipe_busy:
            _KERNEL32.WaitNamedPipeW(ipc_path, 200)
        time.sleep(0.05)
    return None


def _open_ipc_client(ipc_path: str, timeout: float = 3.0) -> MpvIpcClient | None:
    """打开命名管道并包装为 MpvIpcClient；失败返回 None。"""
    handle = _open_ipc_pipe(ipc_path, timeout)
    if not handle:
        return None
    channel = _MpvNamedPipeChannel(handle)
    return MpvIpcClient(channel.write_bytes, channel.read_bytes, close=channel.close)


# 运行时 IPC（音量/暂停/属性读写）的打开预算：通道应已存在，缺失即快速
# 失败；启动期就绪验证仍用长预算（见 _verify_started_player）。
_RUNTIME_IPC_OPEN_TIMEOUT = 0.5


def _heal_dead_player_state() -> None:
    """播放器进程已死亡但状态残留时清理状态，使后续运行时 IPC 快速失败。

    mpv 非 stop 路径自行退出（崩溃/被杀）时状态文件仍含 ipc_path；若继续
    按启动期预算轮询打开管道，GUI 线程每次调用会空耗整个超时窗口。
    """
    global _CURRENT_PROC
    proc = _CURRENT_PROC
    if proc is None or proc.poll() is None:
        return
    _CURRENT_PROC = None
    process_state.remove_state(PID_FILE)


def _mpv_ipc_transact(ipc_path: str, command, timeout: float = _RUNTIME_IPC_OPEN_TIMEOUT) -> tuple[bool, Any]:
    """执行一次 JSON IPC 事务：发送命令并等待 mpv 应答。

    运行时路径默认短超时：管道缺失（播放器已退出）时快速失败并自愈状态，
    不按启动期预算在 GUI 线程长时间轮询。
    """
    client = _open_ipc_client(ipc_path, timeout)
    if client is None:
        _heal_dead_player_state()
        return False, None
    try:
        return client.request(command, timeout=timeout)
    finally:
        client.close()


def _current_ipc_path() -> str:
    """读取当前播放器的 IPC 通道路径（随播放器状态持久化）。"""
    try:
        return str(_read_state().get("ipc_path") or "")
    except Exception:
        return ""


def _verify_media_ready(client: MpvIpcClient | None, timeout: float = 5.0) -> bool:
    """薄包装：转发到平台中立的 verify_media_playing（便于测试替换与平台差分）。"""
    if client is None:
        return False
    return verify_media_playing(client, timeout=timeout)


def _verify_started_player(process: subprocess.Popen, ipc_path: str) -> bool:
    """控制通道建立后再经 JSON IPC 确认媒体真实在播放（防黑屏误报）。

    无 IPC 通道的后端（如 VLC）跳过验证，维持原行为；验证失败由调用方
    拆除播放器并向上回退到下一个候选后端。
    """
    if not ipc_path:
        return True
    poll = getattr(process, "poll", None)
    if poll is not None and poll() is not None:
        return False
    client = _open_ipc_client(ipc_path, timeout=2.5)
    if client is None:
        return False
    try:
        return _verify_media_ready(client, timeout=5.0)
    finally:
        client.close()


def _wait_for_player_ready(process: subprocess.Popen, ipc_path: str, timeout: float = 3.0) -> bool:
    """Wait until mpv creates its named-pipe endpoint, not merely a live PID."""
    if not ipc_path:
        time.sleep(0.25)
        return process.poll() is None
    deadline = time.monotonic() + max(0.2, float(timeout))
    wait_named_pipe = ctypes.windll.kernel32.WaitNamedPipeW
    wait_named_pipe.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD]
    wait_named_pipe.restype = ctypes.wintypes.BOOL
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            if wait_named_pipe(ipc_path, 100):
                return True
        except Exception:
            # Keep polling: older Wine/compatibility environments can expose
            # the process before the named-pipe API becomes usable.
            pass
        time.sleep(0.05)
    return False


def _extract_wid(cmd: list[str]) -> int | None:
    for index, arg in enumerate(cmd):
        if not isinstance(arg, str):
            continue
        value = ""
        if arg.startswith("--wid="):
            value = arg.split("=", 1)[1]
        elif arg == "--wid" and index + 1 < len(cmd):
            value = str(cmd[index + 1])
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _start_player(name: str, cmd: list[str], ipc_path: str = "") -> tuple[bool, str]:
    process = _launch(cmd)
    if not _wait_for_player_ready(process, ipc_path):
        _terminate_failed_player(process)
        return False, f"{name} 未在限定时间内建立控制通道，已自动回退"
    try:
        _write_state(process.pid, name, _extract_wid(cmd), ipc_path=ipc_path)
    except Exception:
        # A player without durable ownership state cannot be stopped safely on
        # the next launch.  Do not leave an orphaned WorkerW child running.
        _terminate_failed_player(process)
        process_state.remove_state(PID_FILE)
        raise
    if not _verify_started_player(process, ipc_path):
        # 通道在而媒体不在播（黑屏风险）：拆除并向上报错，让上层回退到下一个候选。
        _terminate_failed_player(process)
        process_state.remove_state(PID_FILE)
        return False, f"{name} 控制通道已建立但未确认媒体在播放，已自动回退"
    return True, ""


def start_video_wallpaper(video_path: str, muted: bool = True, volume: int = 100) -> tuple[bool, str]:
    if not validate_video_path(video_path):
        return False, "请选择有效的视频文件：mp4/mov/m4v/avi/mkv/webm/wmv"
    stop_video_wallpaper()
    workerw = _find_workerw()
    if not workerw:
        return False, "无法定位 Windows 桌面 WorkerW，已取消视频启动以避免打开普通播放器窗口"

    errors: list[str] = []
    # Prefer mpv's executable + JSON IPC. In bundled mode candidate discovery
    # is restricted to the verified packaged runtime, so this does not silently
    # pick up a random system player. It also avoids starting a second complete
    # ShangBackground executable merely to host libmpv.
    external_mpv = _mpv_command(video_path, muted, volume, workerw)
    if external_mpv is not None:
        name, cmd, ipc_path = external_mpv
        try:
            ok, message = _start_player(name, cmd, ipc_path=ipc_path)
            if ok:
                return True, message
            errors.append(message)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    # Compatibility for an existing libmpv-only bundled payload. This remains
    # isolated in a subprocess because a bad native DLL must not take down the
    # primary GUI process.
    embedded = _internal_libmpv_command(video_path, muted, volume, workerw)
    if embedded is not None:
        name, cmd, ipc_path = embedded
        try:
            ok, message = _start_player(name, cmd, ipc_path=ipc_path)
            if ok:
                return True, message
            errors.append(message)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    candidate = _vlc_command(video_path, muted, volume)
    if candidate is not None:
        name, cmd, ipc_path = candidate
        try:
            ok, message = _start_player(name, cmd, ipc_path=ipc_path)
            if ok:
                return True, message
            errors.append(message)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    return False, (
        "未找到可用的视频壁纸播放器，或播放器无法启动。优先尝试了 mpv 可执行运行时，随后尝试兼容 libmpv/VLC。"
        f" 诊断日志：{PLAYER_LOG}" + ("\n" + "；".join(errors[-4:]) if errors else "")
    )


def _send_mpv_ipc_commands(commands: list[dict]) -> bool:
    """通过 JSON IPC 逐条发送命令并确认 mpv 应答成功。"""
    ipc_path = _current_ipc_path()
    if not ipc_path:
        return False
    for command in commands:
        ok, _response = _mpv_ipc_transact(ipc_path, command, timeout=_RUNTIME_IPC_OPEN_TIMEOUT)
        if not ok:
            return False
    return True


def set_video_volume(muted: bool, volume: int) -> bool:
    """Live-adjust volume/mute on the running player without restart."""
    clamped_volume = max(0, min(100, int(volume)))
    return _send_mpv_ipc_commands(
        [
            {"command": ["set_property", "volume", clamped_volume]},
            {"command": ["set_property", "mute", bool(muted)]},
        ]
    )


def set_video_paused(paused: bool) -> bool:
    """Live-pause/resume the running mpv wallpaper via JSON IPC."""
    return _send_mpv_ipc_commands(
        [
            {"command": ["set_property", "pause", bool(paused)]},
        ]
    )


_PROPERTY_OBSERVERS: dict[str, PollingPropertyObserver] = {}
_OBSERVERS_LOCK = threading.Lock()


def _stop_property_observers() -> None:
    """停止并清空全部属性观察者（stop_video_wallpaper 时调用）。"""
    with _OBSERVERS_LOCK:
        observers = list(_PROPERTY_OBSERVERS.values())
        _PROPERTY_OBSERVERS.clear()
    for observer in observers:
        observer.stop()


def send_video_ipc(command) -> bool:
    """向运行中的播放器发送任意 mpv JSON IPC 命令（LegacyModuleMpvBackend.ipc 探测点）。

    返回 mpv 是否确认成功；无 IPC 通道或应答失败一律返回 False。
    """
    ipc_path = _current_ipc_path()
    if not ipc_path:
        return False
    ok, _response = _mpv_ipc_transact(ipc_path, command, timeout=_RUNTIME_IPC_OPEN_TIMEOUT)
    return ok


def get_video_property(name: str) -> tuple[bool, Any]:
    """读取运行中播放器的属性；(False, None) 表示通道或属性不可用。"""
    ipc_path = _current_ipc_path()
    if not ipc_path:
        return False, None
    ok, response = _mpv_ipc_transact(ipc_path, ["get_property", str(name)], timeout=_RUNTIME_IPC_OPEN_TIMEOUT)
    if ok and isinstance(response, dict):
        return True, response.get("data")
    return False, None


def observe_video_property(name: str, callback: Callable[[str, Any], None]) -> bool:
    """注册属性观察：以 PollingPropertyObserver 轮询 get_video_property 实现。

    callback 以 (属性名, 当前值) 调用，值变化时触发（首次订阅同步一次当前值）；
    同名属性的重复订阅会替换旧观察者。返回是否注册成功。
    """
    key = str(name or "").strip()
    if not key or not callable(callback):
        return False
    with _OBSERVERS_LOCK:
        previous = _PROPERTY_OBSERVERS.pop(key, None)
        observer = PollingPropertyObserver(lambda: get_video_property(key)[1])
        # start() 必须在锁内完成：若放到锁外，并发同名注册可能在
        # store→start 间隙 pop 并 stop 本观察者，随后的 start() 会把它
        # 复活成注册表再也引用不到的泄漏线程。
        started = observer.start(lambda value: callback(key, value))
        if started:
            _PROPERTY_OBSERVERS[key] = observer
    if previous is not None:
        # stop() 内部有界 join，放锁外避免阻塞并发注册方。
        previous.stop()
    return started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video_path")
    parser.add_argument("--muted", action="store_true")
    parser.add_argument(
        "--volume",
        type=int,
        default=100,
        help="audio volume 0-100; preserved while muted for later unmute (default: 100)",
    )
    args = parser.parse_args()
    ok, message = start_video_wallpaper(args.video_path, muted=args.muted, volume=args.volume)
    if not ok:
        print(message, file=sys.stderr)
        raise SystemExit(1)
    if message:
        print(message)


if __name__ == "__main__":
    main()
