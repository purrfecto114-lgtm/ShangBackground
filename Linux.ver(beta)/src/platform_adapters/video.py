from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


def _user_state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "ShangBackground")


_DATA_DIR = _user_state_dir()
os.makedirs(_DATA_DIR, exist_ok=True)
PID_FILE = os.path.join(_DATA_DIR, "video_wallpaper.pid")
# 单独文件保存 IPC socket 路径；与 PID 文件分离，避免破坏旧的纯 int PID 格式。
IPC_FILE = os.path.join(_DATA_DIR, "video_wallpaper.ipc")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")


def validate_video_path(path: str | None) -> bool:
    return bool(path and os.path.isfile(path) and path.lower().endswith(VIDEO_EXTENSIONS))


def _read_pid() -> int | None:
    try:
        with open(PID_FILE, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
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
        os.kill(int(pid), 0)
        return True
    except OSError:
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
            gone, alive = psutil.wait_procs([proc, *children], timeout=2)
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
        os.kill(int(pid), signal.SIGTERM)
        for _ in range(30):
            if not _is_process_alive(pid):
                return
            time.sleep(0.05)
        os.kill(int(pid), signal.SIGKILL)
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
    # 清理 IPC socket 路径文件；socket 本身由 mpv 在退出时移除
    try:
        if os.path.exists(IPC_FILE):
            os.remove(IPC_FILE)
    except Exception:
        pass


def is_video_wallpaper_running() -> bool:
    return _is_process_alive(_read_pid())


def _start_process(cmd: list[str], fail_name: str, ipc_path: str = "") -> tuple[bool, str]:
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        with open(PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(process.pid))
        # 持久化 IPC socket 路径，供 set_video_volume 在 GUI 进程中读取
        try:
            with open(IPC_FILE, "w", encoding="utf-8") as fh:
                fh.write(ipc_path or "")
        except Exception:
            pass
        time.sleep(0.25)
        if process.poll() is not None:
            return False, f"{fail_name} 启动后立即退出，请检查会话类型、编码格式或文件路径。"
        return True, ""
    except Exception as exc:
        return False, f"启动视频壁纸失败：{exc}"


def _mpv_ipc_path() -> str:
    """构建 mpv JSON IPC 的 Unix domain socket 路径。

    嵌入当前进程 PID 以避免多实例冲突；放在 XDG_RUNTIME_DIR 或 /tmp 下
    以确保用户态可访问。参见 https://mpv.io/manual/stable/#json-ipc。
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    stem = f"shangbg-mpv-{os.getpid()}.sock"
    return os.path.join(runtime, stem)


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
        mpvpaper = shutil.which("mpvpaper")
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
            return _start_process([mpvpaper, "-o", mpv_options, "ALL", abs_video], "mpvpaper", ipc_path=ipc_path)
        return False, "当前是 Wayland 会话；已支持 mpvpaper 作为替代后端，但未找到 mpvpaper。请安装 mpvpaper，或切换到 X11 后使用 xwinwrap + mpv。"
    xwinwrap = shutil.which("xwinwrap")
    mpv = shutil.which("mpv")
    if not xwinwrap or not mpv:
        return False, "Linux X11 视频壁纸需要 xwinwrap + mpv；Wayland 需要 mpvpaper。请安装对应工具并加入 PATH。"
    mpv_args = [
        mpv,
        "--wid=WID",
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
