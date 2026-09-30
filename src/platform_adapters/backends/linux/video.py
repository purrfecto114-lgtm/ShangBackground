from __future__ import annotations

import argparse
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None

from platform_adapters import process_state
from platform_adapters.backends.linux.session import is_wayland_session
from platform_adapters.mpv_ipc import MpvIpcClient, verify_media_playing

try:
    from app.paths import (
        app_executable_path, entry_script_path, external_media_runtime_allowed,
        is_packaged_runtime, mpv_bundled_exe,
    )
    from app.libmpv_runtime import runtime_available as libmpv_runtime_available
    from app.build_features import use_internal_libmpv
    from app.mpv_backend import PollingPropertyObserver
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
    def use_internal_libmpv():
        # app 包不可用时内部 libmpv 运行时同样不可用。
        return False


def _user_state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "ShangBackground")


_DATA_DIR = _user_state_dir()
os.makedirs(_DATA_DIR, exist_ok=True)
PID_FILE = os.path.join(_DATA_DIR, "video_wallpaper.pid")
# 单独文件保存 IPC socket 路径；与 PID 文件分离，避免破坏旧的纯 int PID 格式。
IPC_FILE = os.path.join(_DATA_DIR, "video_wallpaper.ipc")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv")
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
    _terminate_process_tree(proc, grace=2.0)


def stop_video_wallpaper() -> None:
    # A live Popen object is an exact capability owned by this process.  For
    # crash recovery, terminate only a process whose persisted identity still
    # matches; legacy PID-only files are deliberately non-destructive.
    _stop_property_observers()
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


class _UnixSocketChannel:
    """Unix socket 读写通道：为 MpvIpcClient 提供注入式 I/O。

    read_bytes 契约：返回 bytes（可能是不完整分片）、None（超时内无数据）、
    b""（EOF）或抛异常（连接破裂）。
    """

    _READ_CHUNK = 65536

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def write_bytes(self, payload: bytes) -> int:
        # sendall 保证全量写出；为贴合协议层契约返回写入长度。
        self._sock.sendall(payload)
        return len(payload)

    def read_bytes(self, timeout: float) -> bytes | None:
        self._sock.settimeout(max(0.01, float(timeout)))
        try:
            chunk = self._sock.recv(self._READ_CHUNK)
        except socket.timeout:
            return None  # TimeoutError 是 OSError 子类，必须先于 OSError 捕获。
        if chunk:
            return chunk
        return b""  # 对端有序关闭：EOF。

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _connect_ipc_socket(ipc_path: str, timeout: float = 3.0) -> socket.socket | None:
    """连接 mpv IPC Unix socket；文件存在但尚未监听时重试直至超时。"""
    deadline = time.monotonic() + max(0.2, float(timeout))
    while time.monotonic() < deadline:
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(min(0.25, max(0.05, deadline - time.monotonic())))
            sock.connect(ipc_path)
            return sock
        except OSError:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            time.sleep(0.05)
    return None


def _open_ipc_client(ipc_path: str, timeout: float = 3.0) -> MpvIpcClient | None:
    """连接 socket 并包装为 MpvIpcClient；失败返回 None。"""
    sock = _connect_ipc_socket(ipc_path, timeout)
    if sock is None:
        return None
    channel = _UnixSocketChannel(sock)
    return MpvIpcClient(channel.write_bytes, channel.read_bytes, close=channel.close)


# 运行时 IPC（音量/暂停/属性读写）的打开预算：通道应已存在，缺失即快速
# 失败；启动期就绪验证仍用长预算（见 _verify_started_player）。
_RUNTIME_IPC_OPEN_TIMEOUT = 0.5


def _heal_dead_player_state() -> None:
    """播放器进程已死亡但状态残留时清理状态，使后续运行时 IPC 快速失败。

    mpv 非 stop 路径自行退出（崩溃/被杀）时状态文件仍含 ipc_path；若继续
    按启动期预算轮询连接 socket，GUI 线程每次调用会空耗整个超时窗口。
    """
    global _CURRENT_PROC
    proc = _CURRENT_PROC
    if proc is None or proc.poll() is None:
        return
    _CURRENT_PROC = None
    process_state.remove_state(PID_FILE)


def _mpv_ipc_transact(
    ipc_path: str, command, timeout: float = _RUNTIME_IPC_OPEN_TIMEOUT
) -> tuple[bool, Any]:
    """执行一次 JSON IPC 事务：发送命令并等待 mpv 应答。

    运行时路径默认短超时：socket 缺失（播放器已退出）时快速失败并自愈状态，
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
    """读取当前播放器的 IPC socket 路径（IPC_FILE 优先，回退状态文件）。"""
    try:
        with open(IPC_FILE, "r", encoding="utf-8") as fh:
            ipc_path = fh.read().strip()
        if ipc_path:
            return ipc_path
    except Exception:
        pass
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

    无 IPC 通道的后端跳过验证；验证失败由调用方整树拆除并向上回退。
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


def _group_has_live_members(group_id: int) -> bool | None:
    """进程组内是否仍有非僵尸成员；无法判定时返回 None（回退 killpg 探测）。

    僵尸成员不代表仍在运行：容器等 PID 1 不收割孤儿的环境里，
    killpg(0) 会因组内残留僵尸而永远成功，导致终止流程空耗宽限
    并无谓升级 SIGKILL。直接枚举 /proc 按进程组归属判定。
    """
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as fh:
                stat = fh.read()
        except OSError:
            continue
        try:
            # comm 可含空格与括号：定位最后一个 ')' 之后的字段。
            # ) 之后依次为 state(0) ppid(1) pgrp(2)。
            rest = stat[stat.rindex(b")") + 2:].split()
            if int(rest[2]) != group_id:
                continue
            if rest[0] != b"Z":
                return True
        except (ValueError, IndexError):
            continue
    return False


def _process_tree_gone(group_id: int | None, survivors: list) -> bool:
    """进程树是否已全部退出（psutil 可用时含孙进程检查）。"""
    for child in survivors:
        try:
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                return False
        except Exception:
            continue
    if group_id is None:
        return True
    live = _group_has_live_members(group_id)
    if live is not None:
        return not live
    try:
        os.killpg(group_id, 0)  # 信号 0：探测进程组是否仍有成员。
    except ProcessLookupError:
        return True
    except OSError:
        return True
    return False


def _terminate_process_tree(process: subprocess.Popen, *, grace: float = 2.0) -> None:
    """终止以 start_new_session=True 启动的播放器及其整棵进程树。

    顺序：SIGTERM 整个进程组（避免 xwinwrap→mpv 之类的子进程沦为孤儿）
    → 宽限等待 → 仍存活则 SIGKILL 升级。psutil 仅用于更精细的孙进程
    兕底枚举；缺失时 os.killpg 已覆盖全部组内进程，不留孤儿。
    """
    global _CURRENT_PROC
    pid = process.pid
    survivors: list = []
    if psutil is not None:
        try:
            survivors = psutil.Process(pid).children(recursive=True)
        except Exception:
            survivors = []
    try:
        # os.getpgid 是 POSIX-only：Windows 上抛 AttributeError（非 OSError），
        # 会穿透 except OSError 直接毁掉终止流程本身。CI 矩阵在 Windows 上
        # 以 monkeypatch 方式运行本模块的失败路径测试，必须平台安全。
        group_id = os.getpgid(pid) if hasattr(os, "getpgid") else None
    except OSError:
        group_id = None
    own_group = bool(group_id and group_id == pid)

    def _signal(sig: int) -> None:
        if own_group:
            try:
                os.killpg(group_id, sig)
            except OSError:
                pass

    _signal(signal.SIGTERM)
    try:
        process.terminate()
    except Exception:
        pass
    deadline = time.monotonic() + max(0.2, float(grace))
    while time.monotonic() < deadline:
        if process.poll() is not None and _process_tree_gone(group_id, survivors):
            break
        time.sleep(0.05)
    if process.poll() is None or not _process_tree_gone(group_id, survivors):
        # 宽限期内未退净：SIGKILL 升级，确保不留孤儿。Windows 的 signal
        # 模块没有 SIGKILL 属性（AttributeError 在实参求值时即抛出，先于
        # own_group 检查）——回退 SIGTERM 占位，Windows 上 own_group 恒为
        # False，信号永远不会真正发送，仅保证属性访问不崩。
        _signal(getattr(signal, "SIGKILL", signal.SIGTERM))
        try:
            process.kill()
        except Exception:
            pass
        for child in survivors:
            try:
                child.kill()
            except Exception:
                pass
    try:
        process.wait(timeout=1.0)
    except Exception:
        pass
    if _CURRENT_PROC is process:
        _CURRENT_PROC = None


def _start_process(cmd: list[str], fail_name: str, ipc_path: str = "") -> tuple[bool, str]:
    global _CURRENT_PROC
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        _CURRENT_PROC = process
        try:
            ownership = process_state.write_state(
                PID_FILE, process.pid, kind=PROCESS_KIND,
                extra={"ipc_path": ipc_path or "", "command": list(cmd)},
            )
            if isinstance(ownership, dict) and ownership.get("identity_unavailable"):
                raise OSError("无法确认新播放器进程身份")
        except Exception as exc:
            _terminate_process_tree(process, grace=1.5)
            process_state.remove_state(PID_FILE)
            return False, f"{fail_name} 无法记录进程所有权，已终止新进程：{exc}"
        if not _wait_for_ipc(process, ipc_path):
            _terminate_process_tree(process, grace=1.5)
            process_state.remove_state(PID_FILE)
            return False, f"{fail_name} 未在限定时间内建立控制通道，已自动回退。"
        if not _verify_started_player(process, ipc_path):
            # 通道在而媒体不在播（黑屏风险）：整树拆除并向上回退到下一个候选。
            _terminate_process_tree(process, grace=1.5)
            process_state.remove_state(PID_FILE)
            return False, f"{fail_name} 控制通道已建立但未确认媒体在播放，已自动回退。"
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
    """Whether mpvpaper's layer-shell model is plausible for this session.

    v1.6.2 审查修正：mpvpaper 官方定位是 wlroots 系合成器（Sway/Hyprland/
    Wayfire/river）。KDE/KWin 会话默认不再自动视为可运行——KWin 的
    layer-shell 兼容性未经真机验证，仅当用户显式设置
    ``SHANGBACKGROUND_ALLOW_MPVPAPER=1`` 实验开关时才允许尝试。该开关
    只影响启动尝试，不改变 capabilities.py 的能力声明（报告要求 3）。
    """
    tokens = " ".join(
        filter(None, (
            os.environ.get("XDG_CURRENT_DESKTOP", ""),
            os.environ.get("XDG_SESSION_DESKTOP", ""),
            os.environ.get("DESKTOP_SESSION", ""),
        ))
    ).lower()
    if any(os.environ.get(name) for name in ("SWAYSOCK", "HYPRLAND_INSTANCE_SIGNATURE", "WAYFIRE_SOCKET")):
        return True
    if any(name in tokens for name in ("sway", "hyprland", "wayfire", "river", "wlroots")):
        return True
    if any(name in tokens for name in ("kde", "plasma")):
        return os.environ.get("SHANGBACKGROUND_ALLOW_MPVPAPER", "").strip() == "1"
    return False


def _internal_libmpv_x11_command(
    xwinwrap: str, video_path: str, ipc_path: str, muted: bool, volume: int
) -> list[str] | None:
    if not libmpv_runtime_available():
        return None
    # v1.4.4: Don't spawn the full packaged app as a child process when the
    # build didn't bundle libmpv. Same fix as Windows video backend.
    # 门控统一：与 windows/video.py 共用 build_features.use_internal_libmpv()。
    if not use_internal_libmpv():
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
        return False, "请选择有效的视频文件：mp4/mov/m4v/avi/mkv/webm/wmv"
    stop_video_wallpaper()
    abs_video = os.path.abspath(video_path)
    # Clamp volume to 0-100 once so both backends receive a sane value.
    clamped_volume = max(0, min(100, int(volume)))
    # IPC socket for live volume/mute control (mpv / mpvpaper both支持)。
    ipc_path = _mpv_ipc_path()
    if is_wayland_session():
        if not _wayland_layer_shell_session():
            # 与 _wayland_layer_shell_session 同源的三令牌判定（验收轮修正：
            # 仅 DESKTOP_SESSION=plasma 的会话此前会落到通用文案，错过实验开关指引）。
            desktop_tokens = " ".join(
                filter(None, (
                    os.environ.get("XDG_CURRENT_DESKTOP", ""),
                    os.environ.get("XDG_SESSION_DESKTOP", ""),
                    os.environ.get("DESKTOP_SESSION", ""),
                ))
            ).lower()
            desktop = (
                os.environ.get("XDG_CURRENT_DESKTOP")
                or os.environ.get("XDG_SESSION_DESKTOP")
                or os.environ.get("DESKTOP_SESSION")
                or "unknown"
            )
            if "kde" in desktop_tokens or "plasma" in desktop_tokens:
                return False, (
                    "KDE/KWin 会话默认不启用 mpvpaper 视频壁纸：mpvpaper 面向"
                    " wlroots 系合成器，KWin 的 layer-shell 兼容性未经真机验证。"
                    "如需自行实验，请设置环境变量 SHANGBACKGROUND_ALLOW_MPVPAPER=1 "
                    "后重试；后果自负（可能黑屏或无首帧）。"
                )
            return False, (
                "当前 Wayland 桌面不提供本项目已实现的通用视频壁纸层。"
                f"检测到桌面：{desktop}。mpvpaper 仅适用于兼容 layer-shell 的"
                " wlroots 系合成器（Sway/Hyprland/Wayfire/river）；"
                "GNOME Wayland 需要桌面扩展/插件后端。"
            )
        mpvpaper = shutil.which("mpvpaper") if external_media_runtime_allowed() else None
        if mpvpaper:
            # mpvpaper accepts mpv options via -o as a space-separated string.
            # Keep the audio track alive and preserve the configured volume;
            # mpv's mute property is independently hot-controllable over IPC.
            # 同时启用 input-ipc-server 让 GUI 能热调音量而不重启播放。
            # See https://github.com/GhostNaN/mpvpaper (mpv IPC support).
            safe_options = (
                "no-config load-scripts=no autoload-files=no sub-auto=no "
                "audio-file-auto=no no-osc no-osd-bar no-input-default-bindings "
                "loop-file=inf"
            )
            mpv_options = (
                f"{safe_options} volume={clamped_volume} "
                f"mute={'yes' if muted else 'no'} input-ipc-server={ipc_path}"
            )
            # Current mpvpaper documents ALL as the selector for every output.
            # Keep an opt-in override for users who want one named connector.
            output = os.environ.get("SHANGBACKGROUND_MPVPAPER_OUTPUT", "").strip() or "ALL"
            return _start_process([mpvpaper, "-o", mpv_options, output, abs_video], "mpvpaper", ipc_path=ipc_path)
        return False, "当前 Wayland 合成器可尝试 mpvpaper layer-shell，但未找到可执行文件。请安装 mpvpaper，或切换到 X11 后使用 xwinwrap + mpv。"
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
    # Keep volume and mute independent so hot unmute restores saved volume.
    mpv_args.append(f"--volume={clamped_volume}")
    mpv_args.append(f"--mute={'yes' if muted else 'no'}")
    mpv_args.append(abs_video)
    cmd = [xwinwrap, "-ov", "-fs", "--", *mpv_args]
    return _start_process(cmd, "xwinwrap/mpv", ipc_path=ipc_path)


def set_video_volume(muted: bool, volume: int) -> bool:
    """通过 mpv JSON IPC 实时调整音量/静音，不中断播放。

    返回 True 表示 mpv 应答确认成功；返回 False 表示 socket 不可用或命令
    被拒绝，GUI 应回退到 stop + start 重新启动播放进程。
    """
    ipc_path = _current_ipc_path()
    if not ipc_path:
        return False
    client = _open_ipc_client(ipc_path, timeout=_RUNTIME_IPC_OPEN_TIMEOUT)
    if client is None:
        _heal_dead_player_state()
        return False
    try:
        clamped_volume = max(0, min(100, int(volume)))
        if not client.set_property("volume", clamped_volume):
            return False
        return client.set_property("mute", bool(muted))
    finally:
        client.close()


def set_video_paused(paused: bool) -> bool:
    """通过 mpv JSON IPC 实时暂停/恢复视频壁纸（以 mpv 应答为准）。"""
    ipc_path = _current_ipc_path()
    if not ipc_path:
        return False
    client = _open_ipc_client(ipc_path, timeout=_RUNTIME_IPC_OPEN_TIMEOUT)
    if client is None:
        _heal_dead_player_state()
        return False
    try:
        return client.set_property("pause", bool(paused))
    finally:
        client.close()


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
    parser.add_argument("--volume", type=int, default=100,
                        help="audio volume 0-100 (default: 100); preserved while muted so unmuting restores it immediately")
    args = parser.parse_args()
    ok, message = start_video_wallpaper(args.video_path, muted=args.muted, volume=args.volume)
    if not ok:
        print(message, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
