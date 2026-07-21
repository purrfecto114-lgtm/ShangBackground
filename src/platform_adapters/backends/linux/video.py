from __future__ import annotations

import argparse
import json
import os
import shutil
import secrets
import socket
import subprocess
import sys
import time

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None

from platform_adapters import process_state

try:
    from app.paths import (
        app_executable_path, entry_script_path, external_media_runtime_allowed,
        is_packaged_runtime, mpv_bundled_exe,
    )
    from app.libmpv_runtime import runtime_available as libmpv_runtime_available
except Exception:  # pragma: no cover - allow import without app package
    def mpv_bundled_exe():
        return None
    def libmpv_runtime_available():
        return False
    def is_packaged_runtime():
        return False
    def external_media_runtime_allowed():
        return True
    def app_executable_path():
        return sys.executable
    def entry_script_path():
        return sys.argv[0]


def _user_state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "ShangBackground")


_DATA_DIR = _user_state_dir()
os.makedirs(_DATA_DIR, exist_ok=True)
PID_FILE = os.path.join(_DATA_DIR, "video_wallpaper.pid")
# 单独文件保存 IPC socket 路径；与 PID 文件分离，避免破坏旧的纯 int PID 格式。
IPC_FILE = os.path.join(_DATA_DIR, "video_wallpaper.ipc")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")
PROCESS_KIND = "video-wallpaper-linux"
_CURRENT_PROC: subprocess.Popen | None = None


def validate_video_path(path: str | None) -> bool:
    return bool(path and os.path.isfile(path) and path.lower().endswith(VIDEO_EXTENSIONS))


def _read_state() -> dict[str, object]:
    return process_state.read_state(PID_FILE)


def _read_pid() -> int | None:
    try:
        pid = _read_state().get("pid")
        return int(pid) if pid else None
    except Exception:
        return None


def _stop_tracked_process() -> None:
    global _CURRENT_PROC
    proc = _CURRENT_PROC
    _CURRENT_PROC = None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass
    except Exception:
        pass


def stop_video_wallpaper() -> None:
    # A live Popen object is an exact capability owned by this process.  For
    # crash recovery, terminate only a process whose persisted identity still
    # matches; legacy PID-only files are deliberately non-destructive.
    _stop_tracked_process()
    process_state.terminate_verified(PID_FILE, expected_kind=PROCESS_KIND)
    process_state.remove_state(PID_FILE)
    try:
        if os.path.exists(IPC_FILE):
            os.remove(IPC_FILE)
    except Exception:
        pass


def is_video_wallpaper_running() -> bool:
    if _CURRENT_PROC is not None and _CURRENT_PROC.poll() is None:
        return True
    return process_state.is_running(PID_FILE, expected_kind=PROCESS_KIND)


def _wait_for_ipc(process: subprocess.Popen, ipc_path: str, timeout: float = 3.0) -> bool:
    if not ipc_path:
        time.sleep(0.25)
        return process.poll() is None
    deadline = time.monotonic() + max(0.2, float(timeout))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if os.path.exists(ipc_path):
            return True
        time.sleep(0.05)
    return False


def _terminate_failed_process(process: subprocess.Popen) -> None:
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


def _start_process(cmd: list[str], fail_name: str, ipc_path: str = "") -> tuple[bool, str]:
    global _CURRENT_PROC
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        _CURRENT_PROC = process
        if not _wait_for_ipc(process, ipc_path):
            _terminate_failed_process(process)
            process_state.remove_state(PID_FILE)
            return False, f"{fail_name} 未在限定时间内建立控制通道，已自动回退。"
        process_state.write_state(
            PID_FILE, process.pid, kind=PROCESS_KIND,
            extra={"ipc_path": ipc_path or "", "command": list(cmd)},
        )
        try:
            with open(IPC_FILE, "w", encoding="utf-8") as fh:
                fh.write(ipc_path or "")
            try:
                os.chmod(IPC_FILE, 0o600)
            except OSError:
                pass
        except Exception:
            pass
        return True, ""
    except Exception as exc:
        _CURRENT_PROC = None
        process_state.remove_state(PID_FILE)
        return False, f"启动视频壁纸失败：{exc}"


def _ensure_private_dir(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _mpv_ipc_path() -> str:
    """构建 mpv JSON IPC 的 Unix domain socket 路径。

    mpv IPC is not a security boundary, so use a per-user private directory
    and an unguessable socket name instead of a PID-derived path.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        base = _ensure_private_dir(os.path.join(runtime, "ShangBackground"))
    else:
        base = _ensure_private_dir(os.path.join(_DATA_DIR, "runtime"))
    stem = f"shangbg-mpv-{secrets.token_hex(8)}.sock"
    return os.path.join(base, stem)


_LAST_MPV_PROBE_ERROR = ""


def _probe_executable(path: str | None, *args: str) -> tuple[bool, str]:
    """Return whether an external backend can actually be executed.

    Merely finding a file is insufficient for Linux bundles: an ELF may target
    the wrong architecture or depend on library SONAMEs absent on the user's
    distribution.  Probe it before advertising the backend as available.
    """
    if not path or not os.path.isfile(path):
        return False, "executable not found"
    try:
        result = subprocess.run(
            [path, *(args or ("--version",))],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        return False, detail[-800:]
    return True, (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr).strip() else "ok"


def _resolve_mpv() -> str | None:
    """Return a runnable mpv, not merely an existing path."""
    global _LAST_MPV_PROBE_ERROR
    errors: list[str] = []
    candidates: list[str] = []
    try:
        bundled = mpv_bundled_exe()
        if bundled and os.path.isfile(bundled):
            try:
                # Bundled helper executables must be traversable/executable by the user.
                os.chmod(bundled, 0o755)  # nosec B103
            except OSError:
                pass
            candidates.append(bundled)
    except Exception as exc:
        errors.append(f"bundled mpv lookup: {exc}")
    if external_media_runtime_allowed():
        system = shutil.which("mpv")
        if system and system not in candidates:
            candidates.append(system)
    for candidate in candidates:
        ok, detail = _probe_executable(candidate, "--version")
        if ok:
            _LAST_MPV_PROBE_ERROR = ""
            return candidate
        errors.append(f"{candidate}: {detail}")
    _LAST_MPV_PROBE_ERROR = " | ".join(errors) or "mpv not found"
    return None


def _wayland_layer_shell_session() -> bool:
    """Whether mpvpaper's layer-shell model is plausible for this session."""
    if os.environ.get("SHANGBACKGROUND_ALLOW_MPVPAPER", "").strip() == "1":
        return True
    tokens = " ".join(
        filter(None, (
            os.environ.get("XDG_CURRENT_DESKTOP", ""),
            os.environ.get("XDG_SESSION_DESKTOP", ""),
            os.environ.get("DESKTOP_SESSION", ""),
        ))
    ).lower()
    if any(os.environ.get(name) for name in ("SWAYSOCK", "HYPRLAND_INSTANCE_SIGNATURE", "WAYFIRE_SOCKET")):
        return True
    return any(name in tokens for name in ("sway", "hyprland", "wayfire", "river", "wlroots"))


def _internal_libmpv_x11_command(
    xwinwrap: str, video_path: str, ipc_path: str, muted: bool, volume: int
) -> list[str] | None:
    if not libmpv_runtime_available():
        return None
    if is_packaged_runtime():
        player = [app_executable_path()]
    else:
        player = [sys.executable, entry_script_path()]
    player.extend([
        "--internal-libmpv-player", os.path.abspath(video_path),
        "--wid", "WID",
        "--ipc-path", ipc_path,
        "--volume", str(max(0, min(100, int(volume)))),
    ])
    if muted:
        player.append("--muted")
    return [xwinwrap, "-ov", "-fs", "--", *player]


def start_video_wallpaper(video_path: str, muted: bool = True, volume: int = 100) -> tuple[bool, str]:
    if not validate_video_path(video_path):
        return False, "请选择有效的视频文件：mp4/mov/m4v/avi/mkv/webm"
    stop_video_wallpaper()
    abs_video = os.path.abspath(video_path)
    # Clamp volume to 0-100 once so both backends receive a sane value.
    clamped_volume = max(0, min(100, int(volume)))
    # IPC socket for live volume/mute control (mpv / mpvpaper both支持)。
    ipc_path = _mpv_ipc_path()
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        if not _wayland_layer_shell_session():
            desktop = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("XDG_SESSION_DESKTOP") or "unknown"
            return False, (
                "当前 Wayland 桌面不提供本项目已实现的通用视频壁纸层。"
                f"检测到桌面：{desktop}。mpvpaper 仅适用于兼容 layer-shell 的合成器；"
                "GNOME/KDE Wayland 需要各自的桌面扩展/插件后端，不能用普通窗口冒充壁纸。"
            )
        mpvpaper = shutil.which("mpvpaper") if external_media_runtime_allowed() else None
        if mpvpaper:
            # mpvpaper accepts mpv options via -o as a space-separated string.
            # `volume=N` is the mpv-native 0-100 knob.  When muted we still
            # pass `volume=0` plus `no-audio` so the player is fully silent
            # (some mpvpaper builds ignore `volume=0` on its own).
            # 同时启用 input-ipc-server 让 GUI 能热调音量而不重启播放。
            # See https://github.com/GhostNaN/mpvpaper (mpv IPC support).
            if muted:
                mpv_options = f"loop-file=inf no-audio volume=0 input-ipc-server={ipc_path}"
            else:
                mpv_options = f"loop-file=inf volume={clamped_volume} input-ipc-server={ipc_path}"
            return _start_process([mpvpaper, "-o", mpv_options, "*", abs_video], "mpvpaper", ipc_path=ipc_path)
        return False, "当前 Wayland 合成器可尝试 mpvpaper，但未找到可执行文件。请安装 mpvpaper，或切换到 X11 后使用 xwinwrap + mpv。"
    xwinwrap = shutil.which("xwinwrap")
    if not xwinwrap:
        return False, "Linux X11 视频壁纸需要 xwinwrap。请使用发行版包管理器安装。"
    internal_cmd = _internal_libmpv_x11_command(
        xwinwrap, abs_video, ipc_path, muted, clamped_volume
    )
    if internal_cmd is not None:
        ok, message = _start_process(internal_cmd, "xwinwrap/libmpv", ipc_path=ipc_path)
        if ok:
            return ok, message
    mpv = _resolve_mpv()
    if not mpv:
        detail = _LAST_MPV_PROBE_ERROR or "未找到"
        return False, (
            "Linux X11 视频壁纸需要内置 libmpv 或可运行的 mpv。"
            f"当前外部 mpv 状态：{detail}。"
        )
    mpv_args = [
        mpv,
        "--wid=WID",
        "--no-config",
        "--load-scripts=no",
        "--autoload-files=no",
        "--sub-auto=no",
        "--audio-file-auto=no",
        "--loop-file=inf",
        "--no-osc",
        "--no-osd-bar",
        "--no-input-default-bindings",
        "--panscan=1.0",
        "--keepaspect=no",
        "--keepaspect-window=no",
        "--no-border",
        "--really-quiet",
        f"--input-ipc-server={ipc_path}",
    ]
    # mpv --volume takes 0-100 (100 = original loudness).  --mute is kept
    # so the user can un-mute instantly without losing the saved volume.
    mpv_args.append(f"--volume={0 if muted else clamped_volume}")
    mpv_args.append(f"--mute={'yes' if muted else 'no'}")
    mpv_args.append(abs_video)
    cmd = [xwinwrap, "-ov", "-fs", "--", *mpv_args]
    return _start_process(cmd, "xwinwrap/mpv", ipc_path=ipc_path)


def set_video_volume(muted: bool, volume: int) -> bool:
    """通过 mpv JSON IPC 实时调整音量/静音，不中断播放。

    返回 True 表示热更新成功；返回 False 表示 socket 不可用或写入失败，
    GUI 应回退到 stop + start 重新启动播放进程。
    """
    try:
        ipc_path = ""
        try:
            with open(IPC_FILE, "r", encoding="utf-8") as fh:
                ipc_path = fh.read().strip()
        except Exception:
            return False
        if not ipc_path or not os.path.exists(ipc_path):
            return False
        clamped_volume = max(0, min(100, int(volume)))
        # mpv JSON IPC: 每行一条命令，UTF-8 编码。
        # See https://mpv.io/manual/stable/#json-ipc
        cmds = [
            {"command": ["set_property", "mute", bool(muted)]},
            {"command": ["set_property", "volume", 0 if muted else clamped_volume]},
        ]
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(ipc_path)
        try:
            for obj in cmds:
                sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
            return True
        finally:
            try:
                sock.close()
            except Exception:
                pass
    except Exception:
        return False


def set_video_paused(paused: bool) -> bool:
    """通过 mpv JSON IPC 实时暂停/恢复视频壁纸。"""
    try:
        ipc_path = ""
        try:
            with open(IPC_FILE, "r", encoding="utf-8") as fh:
                ipc_path = fh.read().strip()
        except Exception:
            return False
        if not ipc_path or not os.path.exists(ipc_path):
            return False
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(ipc_path)
        try:
            sock.sendall((json.dumps({"command": ["set_property", "pause", bool(paused)]}) + "\n").encode("utf-8"))
            return True
        finally:
            try:
                sock.close()
            except Exception:
                pass
    except Exception:
        return False

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


if __name__ == "__main__":
    main()
