from __future__ import annotations

from app.paths import entry_script_path
from platform_adapters import process_state

import argparse
import importlib.util
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


def _user_state_dir() -> str:
    base = os.path.expanduser("~/Library/Application Support")
    return os.path.join(base, "ShangBackground")


_DATA_DIR = _user_state_dir()
os.makedirs(_DATA_DIR, exist_ok=True)
PID_FILE = os.path.join(_DATA_DIR, "video_wallpaper.pid")
# 单独文件保存 IPC socket 路径；与 PID 文件分离，避免破坏旧的纯 int PID 格式。
# macOS 上 AVPlayer 跑在子进程（通过 --internal-video-player 重新启动），父进程
# 通过这个 Unix socket 向子进程发送 {muted, volume} 指令，实现不中断播放的热调音量。
IPC_FILE = os.path.join(_DATA_DIR, "video_wallpaper.ipc")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")
PROCESS_KIND = "video-wallpaper-macos"
_CURRENT_PROC: subprocess.Popen | None = None


def _ensure_private_dir(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _mpv_ipc_path() -> str:
    """构建 macOS 上 AVPlayer 子进程监听的 Unix socket 路径。"""
    runtime = os.environ.get("TMPDIR") or os.path.join(_DATA_DIR, "runtime")
    base = _ensure_private_dir(os.path.join(runtime.rstrip("/"), "ShangBackground"))
    stem = f"shangbg-avplayer-{secrets.token_hex(8)}.sock"
    return os.path.join(base, stem)

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


def _reap_child(pid: int | None) -> None:
    if not pid:
        return
    try:
        while True:
            waited, _status = os.waitpid(int(pid), os.WNOHANG)
            if waited == 0:
                break
    except ChildProcessError:
        pass
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
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass
    except Exception:
        pass
    _reap_child(getattr(proc, "pid", None))


def stop_video_wallpaper() -> None:
    _stop_tracked_process()
    state = process_state.read_state(PID_FILE)
    pid = state.get("pid")
    process_state.terminate_verified(PID_FILE, expected_kind=PROCESS_KIND)
    process_state.remove_state(PID_FILE)
    _reap_child(int(pid) if pid else None)
    try:
        if os.path.exists(IPC_FILE):
            os.remove(IPC_FILE)
    except Exception:
        pass


def is_video_wallpaper_running() -> bool:
    if _CURRENT_PROC is not None and _CURRENT_PROC.poll() is None:
        return True
    return process_state.is_running(PID_FILE, expected_kind=PROCESS_KIND)


def start_video_wallpaper(video_path: str, muted: bool = True, volume: int = 100) -> tuple[bool, str]:
    if not validate_video_path(video_path):
        return False, "请选择有效的视频文件：mp4/mov/m4v/avi/mkv/webm"
    stop_video_wallpaper()
    missing = [name for name in ("AVFoundation", "AppKit", "Quartz") if importlib.util.find_spec(name) is None]
    if missing:
        # A standalone mpv window is not a macOS desktop wallpaper: without an
        # in-process NSWindow it cannot be placed below Finder's icon layer.
        # Fail explicitly instead of reporting a normal borderless window as
        # success.
        return False, (
            "缺少 macOS 原生视频壁纸依赖：" + ", ".join(missing) +
            "。请安装 requirements/macos.txt 中的 PyObjC/Cocoa/Quartz/AVFoundation 依赖。"
        )
    cmd = [sys.executable]
    if not getattr(sys, "frozen", False):
        cmd.append(entry_script_path())
    cmd.extend(["--internal-video-player", os.path.abspath(video_path)])
    if muted:
        cmd.append("--muted")
    # Forward the volume level to the in-process player.  Clamped to 0-100
    # so a corrupted settings.json cannot crash AVPlayer.setVolume_.
    clamped_volume = max(0, min(100, int(volume)))
    cmd.extend(["--volume", str(clamped_volume)])
    # 把 IPC socket 路径传给子进程，子进程在 run_player 内监听该 socket，
    # 接收父进程发来的 {muted, volume} 热更新指令，实现不中断播放的音量调整。
    ipc_path = _mpv_ipc_path()
    cmd.extend(["--volume-ipc", ipc_path])
    try:
        global _CURRENT_PROC
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        _CURRENT_PROC = process
        process_state.write_state(
            PID_FILE, process.pid, kind=PROCESS_KIND,
            extra={"ipc_path": ipc_path or "", "command": list(cmd)},
        )
        # 持久化 IPC socket 路径，供 set_video_volume 在父进程中读取
        try:
            with open(IPC_FILE, "w", encoding="utf-8") as fh:
                fh.write(ipc_path)
            try:
                os.chmod(IPC_FILE, 0o600)
            except OSError:
                pass
        except Exception:
            pass
        time.sleep(0.2)
        if process.poll() is not None:
            _CURRENT_PROC = None
            process_state.remove_state(PID_FILE)
            try:
                os.remove(IPC_FILE)
            except OSError:
                pass
            _reap_child(getattr(process, "pid", None))
            return False, "macOS 视频播放器启动后立即退出，请检查权限、依赖或文件编码。"
        return True, ""
    except Exception as exc:
        _CURRENT_PROC = None
        process_state.remove_state(PID_FILE)
        try:
            os.remove(IPC_FILE)
        except OSError:
            pass
        return False, f"启动视频壁纸失败：{exc}"


def set_video_volume(muted: bool, volume: int) -> bool:
    """通过 Unix socket 向 AVPlayer 子进程发送音量/静音指令，不中断播放。

    返回 True 表示热更新成功；返回 False 表示 socket 不可用或子进程未响应，
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
        # 子进程 run_player 中的 socket 服务线程接收一行 JSON 指令。
        payload = json.dumps({"muted": bool(muted), "volume": 0 if muted else clamped_volume}) + "\n"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(ipc_path)
        try:
            sock.sendall(payload.encode("utf-8"))
            return True
        finally:
            try:
                sock.close()
            except Exception:
                pass
    except Exception:
        return False


def _desktop_level(Quartz):
    try:
        return Quartz.CGWindowLevelForKey(Quartz.kCGDesktopIconWindowLevelKey) - 1
    except Exception:
        return Quartz.CGWindowLevelForKey(Quartz.kCGDesktopWindowLevelKey) + 1


def run_player(video_path: str, muted: bool = True, volume: int = 100, volume_ipc: str = "") -> None:
    if not validate_video_path(video_path):
        raise SystemExit(2)
    import objc
    import AVFoundation
    import Quartz
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSBorderlessWindowMask,
        NSColor,
        NSScreen,
        NSWindow,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorIgnoresCycle,
        NSWindowCollectionBehaviorStationary,
    )
    from Foundation import NSObject, NSNotificationCenter, NSURL
    try:
        import CoreMedia
    except Exception:
        CoreMedia = None

    # Clamp volume to 0-100 and convert to AVPlayer's 0.0-1.0 range.
    clamped_volume = max(0, min(100, int(volume)))
    av_volume = clamped_volume / 100.0

    class LoopObserver(NSObject):
        player = objc.ivar()

        def initWithPlayer_(self, player):
            self = objc.super(LoopObserver, self).init()
            if self is None:
                return None
            self.player = player
            return self

        def playerDidFinish_(self, _notification):
            try:
                if CoreMedia is not None:
                    self.player.seekToTime_(CoreMedia.CMTimeMake(0, 1))
                else:
                    self.player.seekToTime_(AVFoundation.CMTimeMake(0, 1))
                self.player.play()
            except Exception:
                pass

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    players = []
    observers = []
    windows = []
    level = _desktop_level(Quartz)
    video_url = NSURL.fileURLWithPath_(os.path.abspath(video_path))

    # 共享状态：socket 线程写入最新音量/静音指令，主线程通过 NSTimer 轮询应用
    # （AVPlayer API 必须在主线程调用，不能直接在 socket 线程里 setVolume_）。
    pending_volume = {"muted": bool(muted), "volume": 0.0 if muted else av_volume, "dirty": False}
    pending_pause = {"paused": False, "dirty": False}
    volume_lock = threading.Lock()

    # 用一个 NSObject 子类承载 NSTimer 回调，因为 NSTimer 必须用 ObjC selector。
    class VolumeApplier(NSObject):
        def apply_(self, _sender):
            try:
                with volume_lock:
                    volume_dirty = bool(pending_volume.get("dirty", False))
                    new_muted = bool(pending_volume["muted"])
                    new_vol = float(pending_volume["volume"])
                    pending_volume["dirty"] = False
                    pause_dirty = bool(pending_pause.get("dirty", False))
                    paused = bool(pending_pause.get("paused", False))
                    pending_pause["dirty"] = False
                if volume_dirty:
                    for player in players:
                        try:
                            player.setMuted_(new_muted)
                            player.setVolume_(0.0 if new_muted else new_vol)
                        except Exception:
                            pass
                if pause_dirty:
                    for player in players:
                        try:
                            if paused:
                                player.pause()
                            else:
                                player.play()
                        except Exception:
                            pass
            except Exception:
                pass

    applier = VolumeApplier.alloc().init()

    def _terminate(_signum=None, _frame=None):
        for player in players:
            try:
                player.pause()
            except Exception:
                pass
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except Exception:
            pass
        try:
            if os.path.exists(IPC_FILE):
                os.remove(IPC_FILE)
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    # 在子线程启动 Unix socket 服务，监听父进程发来的音量热更新指令。
    # 收到指令后写入 pending_volume，由主线程 NSTimer 周期性应用。
    def _volume_ipc_server(ipc_path: str):
        try:
            if os.path.exists(ipc_path):
                try:
                    os.remove(ipc_path)
                except Exception:
                    pass
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(ipc_path)
            try:
                os.chmod(ipc_path, 0o600)
            except OSError:
                pass
            server.listen(1)
            server.settimeout(0.5)
            while True:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break
                try:
                    conn.settimeout(0.5)
                    data = b""
                    while b"\n" not in data:
                        chunk = conn.recv(256)
                        if not chunk:
                            break
                        data += chunk
                    line = data.decode("utf-8", errors="replace").split("\n", 1)[0].strip()
                    if line:
                        obj = json.loads(line)
                        with volume_lock:
                            if "paused" in obj:
                                pending_pause["paused"] = bool(obj.get("paused", False))
                                pending_pause["dirty"] = True
                            if "muted" in obj or "volume" in obj:
                                new_muted = bool(obj.get("muted", pending_volume["muted"]))
                                new_vol_raw = int(obj.get("volume", 100))
                                new_vol = max(0.0, min(1.0, new_vol_raw / 100.0))
                                pending_volume["muted"] = new_muted
                                pending_volume["volume"] = 0.0 if new_muted else new_vol
                                pending_volume["dirty"] = True
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except Exception:
            pass

    if volume_ipc:
        threading.Thread(target=_volume_ipc_server, args=(volume_ipc,), daemon=True).start()

    for screen in NSScreen.screens():
        frame = screen.frame()
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSBorderlessWindowMask, NSBackingStoreBuffered, False
        )
        window.setLevel_(level)
        window.setOpaque_(True)
        window.setBackgroundColor_(NSColor.blackColor())
        window.setIgnoresMouseEvents_(True)
        window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        view = window.contentView()
        view.setWantsLayer_(True)
        item = AVFoundation.AVPlayerItem.playerItemWithURL_(video_url)
        player = AVFoundation.AVPlayer.playerWithPlayerItem_(item)
        player.setMuted_(bool(muted))
        # AVPlayer.setVolume_ accepts 0.0-1.0.  When muted, force 0.0 so
        # the OS does not briefly play at the saved level before applying
        # the mute flag (some macOS builds defer setMuted_ by one runloop).
        try:
            player.setVolume_(0.0 if muted else av_volume)
        except Exception:
            pass
        layer = AVFoundation.AVPlayerLayer.playerLayerWithPlayer_(player)
        layer.setFrame_(view.bounds())
        try:
            layer.setAutoresizingMask_(18)
        except Exception:
            pass
        try:
            layer.setVideoGravity_(AVFoundation.AVLayerVideoGravityResizeAspectFill)
        except Exception:
            layer.setVideoGravity_("AVLayerVideoGravityResizeAspectFill")
        view.layer().addSublayer_(layer)
        observer = LoopObserver.alloc().initWithPlayer_(player)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            observer,
            "playerDidFinish:",
            AVFoundation.AVPlayerItemDidPlayToEndTimeNotification,
            item,
        )
        window.orderFrontRegardless()
        player.play()
        windows.append(window)
        players.append(player)
        observers.append(observer)

    # 主线程 NSTimer 每 100ms 检查 pending_volume 并应用到所有 AVPlayer。
    # 足以让用户感觉“实时”，又不会过度占用主线程。
    from Foundation import NSTimer
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.1, applier, b"apply:", None, True
    )
    app.run()


def set_video_paused(paused: bool) -> bool:
    """通过 AVPlayer 子进程 Unix socket 实时暂停/恢复视频壁纸。"""
    try:
        ipc_path = ""
        try:
            with open(IPC_FILE, "r", encoding="utf-8") as fh:
                ipc_path = fh.read().strip()
        except Exception:
            return False
        if not ipc_path or not os.path.exists(ipc_path):
            return False
        payload = json.dumps({"paused": bool(paused)}) + "\n"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(ipc_path)
        try:
            sock.sendall(payload.encode("utf-8"))
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
    parser.add_argument("--run-player", dest="video_path")
    parser.add_argument("--muted", action="store_true")
    parser.add_argument("--volume", type=int, default=100,
                        help="audio volume 0-100, only effective when --muted is not set (default: 100)")
    parser.add_argument("--volume-ipc", dest="volume_ipc", default="",
                        help="Unix socket path for live volume control from parent process")
    args = parser.parse_args()
    if args.video_path:
        run_player(args.video_path, muted=args.muted, volume=args.volume, volume_ipc=args.volume_ipc)


if __name__ == "__main__":
    main()
