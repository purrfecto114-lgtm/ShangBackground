from __future__ import annotations

from app.paths import PROJECT_ROOT, RESOURCE_ROOT, mpv_bundled_exe

import argparse
import ctypes
import ctypes.wintypes
import json
import re
import os
import secrets
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None

from app.config import IS_WINDOWS

_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "ShangBackground")
os.makedirs(_DATA_DIR, exist_ok=True)
PID_FILE = os.path.join(_DATA_DIR, "video_wallpaper.pid")


def _secure_pid_file_permissions() -> None:
    """Best-effort: keep runtime PID/state file readable only by current user where supported."""
    try:
        os.chmod(PID_FILE, 0o600)
    except (AttributeError, OSError):
        pass
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv")


def validate_video_path(path: str | None) -> bool:
    return bool(path and os.path.isfile(path) and path.lower().endswith(VIDEO_EXTENSIONS))


def _read_state() -> dict[str, object]:
    try:
        raw = Path(PID_FILE).read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        if raw.startswith("{"):
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        return {"pid": int(raw)}
    except Exception:
        return {}


def _write_state(pid: int, player: str, hwnd: int | None = None, ipc_path: str = "") -> None:
    try:
        Path(PID_FILE).write_text(
            json.dumps(
                {
                    "pid": int(pid),
                    "player": player,
                    "hwnd": int(hwnd or 0),
                    # IPC endpoint for live volume/mute control without restart.
                    # Empty when the backend does not support live control (e.g. VLC),
                    # in which case the GUI must fall back to stop+restart.
                    "ipc_path": ipc_path or "",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        _secure_pid_file_permissions()
    except Exception:
        Path(PID_FILE).write_text(str(pid), encoding="utf-8")
        _secure_pid_file_permissions()


def _read_pid() -> int | None:
    try:
        pid = _read_state().get("pid")
        return int(pid) if pid else None
    except Exception:
        return None


def _is_process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except Exception:
            pass
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    return False


def _terminate_pid(pid: int | None) -> None:
    if not _is_process_alive(pid):
        return
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            children = proc.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except Exception:
                    pass
            proc.terminate()
            _gone, alive = psutil.wait_procs([*children, proc], timeout=3)
            for item in alive:
                try:
                    item.kill()
                except Exception:
                    pass
            return
        except psutil.NoSuchProcess:
            return
        except Exception:
            pass
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, int(pid))
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass


def stop_video_wallpaper() -> None:
    pid = _read_pid()
    if pid:
        _terminate_pid(pid)
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def is_video_wallpaper_running() -> bool:
    return _is_process_alive(_read_pid())



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
    match = re.match(r'^(.*?\.exe)(?:\s|$)', command, re.IGNORECASE)
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
        app_path_keys.extend([
            rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{item}",
            rf"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{item}",
        ])

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

def _candidate_paths(*names: str) -> list[str]:
    result: list[str] = []
    # v1.4.6: 统一用 app.paths.resolve_mpv_path() 解析 mpv, 优先级:
    #   1. 用户安装目录 (自动下载) %LOCALAPPDATA%\ShangBackground\bin\mpv\
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
    for name in names:
        found = shutil.which(name)
        if found:
            result.append(found)
        result.extend(_registry_executable_candidates(name))

    base_dirs = [
        os.path.dirname(sys.executable),
        os.getcwd(),
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


def _find_workerw() -> int:
    """Create/find the hidden WorkerW window behind desktop icons."""
    user32 = ctypes.windll.user32
    progman = user32.FindWindowW("Progman", None)
    result = ctypes.c_ulong(0)
    try:
        user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0x0002, 1000, ctypes.byref(result))
    except Exception:
        pass

    workerw = ctypes.c_void_p(0)
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _enum(hwnd, _lparam):
        shell = user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
        if shell:
            candidate = user32.FindWindowExW(0, hwnd, "WorkerW", None)
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

def _mpv_command(video_path: str, muted: bool, volume: int = 100) -> tuple[str, list[str], str] | None:
    candidates = _candidate_paths("mpv", "mpv.exe")
    mpv = next((path for path in candidates if os.path.basename(path).lower() in {"mpv", "mpv.exe"}), None)
    if not mpv:
        return None
    workerw = _find_workerw()
    wid_arg = f"--wid={workerw}" if workerw else "--wid=0"
    # mpv --volume takes 0-100 (100 = original loudness).  When muted we
    # still pass --mute=yes so the user can un-mute instantly without
    # losing the saved volume level.
    clamped_volume = max(0, min(100, int(volume)))
    # IPC socket so the GUI can adjust volume/mute live without restarting mpv.
    # See https://mpv.io/manual/stable/#json-ipc for the protocol.
    ipc_path = _mpv_ipc_path()
    cmd = [
        mpv,
        wid_arg,
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
        f"--volume={0 if muted else clamped_volume}",
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
    if muted:
        cmd.extend(["--no-audio", "--volume=0"])
    else:
        # VLC's --volume uses 0-1024 where 256 == 100%.  Map 0-100 → 0-256
        # so the slider's percentage matches the user's mental model.
        clamped_volume = max(0, min(100, int(volume)))
        cmd.append(f"--volume={int(clamped_volume * 256 / 100)}")
    cmd.append(os.path.abspath(video_path))
    return "vlc", cmd, ""


def _launch(cmd: list[str]) -> subprocess.Popen:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


def _start_player(name: str, cmd: list[str], ipc_path: str = "") -> tuple[bool, str]:
    process = _launch(cmd)
    time.sleep(0.25)
    if process.poll() is not None:
        return False, f"{name} 启动后立即退出"
    hwnd = None
    for arg in cmd:
        if isinstance(arg, str) and arg.startswith("--wid="):
            try:
                hwnd = int(arg.split("=", 1)[1])
            except Exception:
                hwnd = None
            break
    _write_state(process.pid, name, hwnd, ipc_path=ipc_path)
    return True, ""


def start_video_wallpaper(video_path: str, muted: bool = True, volume: int = 100) -> tuple[bool, str]:
    if not validate_video_path(video_path):
        return False, "请选择有效的视频文件：mp4/mov/m4v/avi/mkv/webm/wmv"
    stop_video_wallpaper()

    candidates: list[tuple[str, list[str], str]] = []
    mpv = _mpv_command(video_path, muted, volume)
    if mpv:
        candidates.append(mpv)
    vlc = _vlc_command(video_path, muted, volume)
    if vlc:
        candidates.append(vlc)

    errors: list[str] = []
    for name, cmd, ipc_path in candidates:
        try:
            ok, message = _start_player(name, cmd, ipc_path=ipc_path)
            if ok:
                return True, message
            errors.append(message)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    return False, (
        "未找到可用的视频壁纸播放器，或播放器无法启动。已尝试 PATH、mpv-register.bat 注册表项、App Paths 和常见安装目录；请确认 mpv.exe/VLC 可执行文件仍存在。"
        + ("\n" + "；".join(errors[-4:]) if errors else "")
    )


def _send_mpv_ipc_commands(commands: list[dict]) -> bool:
    state = _read_state()
    ipc_path = str(state.get("ipc_path") or "")
    if not ipc_path:
        return False
    try:
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        INVALID_HANDLE_VALUE = -1
        handle = ctypes.windll.kernel32.CreateFileW(
            ipc_path, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
        )
        if handle == INVALID_HANDLE_VALUE or handle == 0:
            return False
        try:
            for obj in commands:
                payload = (json.dumps(obj) + "\n").encode("utf-8")
                written = ctypes.wintypes.DWORD(0)
                ok = ctypes.windll.kernel32.WriteFile(
                    handle, payload, len(payload), ctypes.byref(written), None
                )
                if not ok:
                    return False
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False


def set_video_volume(muted: bool, volume: int) -> bool:
    """Live-adjust volume/mute on the running player without restart."""
    clamped_volume = max(0, min(100, int(volume)))
    return _send_mpv_ipc_commands([
        {"command": ["set_property", "mute", bool(muted)]},
        {"command": ["set_property", "volume", 0 if muted else clamped_volume]},
    ])


def set_video_paused(paused: bool) -> bool:
    """Live-pause/resume the running mpv wallpaper via JSON IPC."""
    return _send_mpv_ipc_commands([
        {"command": ["set_property", "pause", bool(paused)]},
    ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video_path")
    parser.add_argument("--muted", action="store_true")
    parser.add_argument("--volume", type=int, default=100,
                        help="audio volume 0-100, only effective when --muted is not set (default: 100)")
    args = parser.parse_args()
    ok, message = start_video_wallpaper(args.video_path, muted=args.muted, volume=args.volume)
    if not ok:
        print(message, file=sys.stderr)
        raise SystemExit(1)
    if message:
        print(message)


if __name__ == "__main__":
    main()
