"""mpv 播放就绪验证与后端能力契约。

覆盖：平台中立 verify_media_playing 的三分支判定；Linux 端到端——真实
AF_UNIX 假 mpv（应答延迟 ~0.2s，模拟「通道在而媒体未播」）经
linux/video.py 的 _connect_ipc_socket + _UnixSocketChannel 完成就绪确认；
_terminate_process_tree 对真实父/孙 sleeper 进程树的收割（SIGTERM、不升级
SIGKILL、不留孤儿）；PollingPropertyObserver 只报变化且 stop 有界 join；
LegacyModuleMpvBackend.observe_property 的平台优先/轮询回退/能力缺失三路径；
两平台能力点契约；_verify_media_ready 薄包装直传。
"""

from __future__ import annotations

import collections
import contextlib
import json
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

try:  # 发布流水线（ci.yml / release.yml）只装 pytest，不装 psutil
    import psutil
except ModuleNotFoundError:  # pragma: no cover - 轻量 CI 环境
    psutil = None

from app.mpv_backend import LegacyModuleMpvBackend, PollingPropertyObserver
from platform_adapters.mpv_ipc import MpvIpcClient, verify_media_playing


class _PropertyMpv:
    """按属性名应答 get_property 的假 mpv（verify_media_playing 分支用）。

    properties：属性名 → 应答 data；unavailable 中的属性回错误应答
    （模拟精简构建的属性不可用）。
    """

    def __init__(self, properties: dict, unavailable: tuple[str, ...] = ()) -> None:
        self._properties = dict(properties)
        self._unavailable = frozenset(unavailable)
        self._pending: collections.deque = collections.deque()
        self._unparsed = bytearray()

    def write_bytes(self, payload: bytes) -> int:
        data = bytes(payload)
        self._unparsed.extend(data)
        while True:
            index = self._unparsed.find(b"\n")
            if index < 0:
                break
            line = bytes(self._unparsed[:index])
            del self._unparsed[: index + 1]
            message = json.loads(line.decode("utf-8"))
            name = (message.get("command") or [None, None])[1]
            if name in self._unavailable:
                reply = {"error": "property unavailable", "data": None}
            else:
                reply = {"error": "success", "data": self._properties.get(name)}
            reply["request_id"] = message.get("request_id")
            self._pending.append(json.dumps(reply).encode("utf-8") + b"\n")
        return len(data)

    def read_bytes(self, timeout: float):
        if self._pending:
            return self._pending.popleft()
        return None


def _ipc_client(fake) -> MpvIpcClient:
    return MpvIpcClient(fake.write_bytes, fake.read_bytes)


def test_verify_media_playing_confirms_playback_by_time_pos():
    """time-pos > 0 是最直接的在播证据：立即通过，无需等超时。"""
    fake = _PropertyMpv({"time-pos": 5.25})
    assert verify_media_playing(_ipc_client(fake), timeout=2.0) is True


def test_verify_media_playing_times_out_when_media_never_plays():
    """time-pos 恒 0 且 duration 不可用：持续轮询至超时返回 False（调用方
    将据此拆除播放器并向上回退，防止起播黑屏被误报成功）。"""
    fake = _PropertyMpv({"time-pos": 0, "eof-reached": False}, unavailable=("duration", "pause"))
    client = _ipc_client(fake)
    started = time.monotonic()
    assert verify_media_playing(client, timeout=0.25, poll_interval=0.05) is False
    assert time.monotonic() - started < 5.0  # 小超时不应被放大成卡死


def test_verify_media_playing_confirms_by_duration_and_unpaused():
    """time-pos/eof 不可用的精简构建：duration 可读且 pause=False 即通过。"""
    fake = _PropertyMpv({"duration": 30.0, "pause": False}, unavailable=("time-pos", "eof-reached"))
    assert verify_media_playing(_ipc_client(fake), timeout=2.0) is True


@pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX 文件系统套接字在 Windows 不可用")
def test_linux_ipc_readiness_end_to_end_over_real_unix_socket(tmp_path):
    """Linux 端到端：真实 AF_UNIX 假 mpv（accept → 读请求 → 延迟 ~0.2s →
    应答 time-pos=1.5），经 linux/video.py 的 _connect_ipc_socket +
    _UnixSocketChannel 组装 MpvIpcClient 后 _verify_media_ready 为 True——
    证明就绪验证真的在轮询 IPC，而不是只看 socket 文件存在。"""
    from platform_adapters.backends.linux import video as linux_video

    # AF_UNIX sun_path 上限：Linux 108 字节、macOS 104 字节（含 NUL）。
    # GitHub 托管 runner 上 pytest 的 tmp_path（/private/var/folders/...
    # 嵌套临时目录）在 macOS 可超过 104 导致 bind 抛 "AF_UNIX path too long"
    # （CI 实测）。先尝试 tmp_path，超限时回退 tempfile.mkdtemp()（跟随
    # TMPDIR，macOS 真机 /var/folders/.../T/ 下最坏 ~78 字节，仍 <95 安全）。
    sock_dir = tmp_path
    if len(str(sock_dir / "fake-mpv.sock")) > 95:
        import tempfile

        sock_dir = Path(tempfile.mkdtemp(prefix="sb-sock-"))
        if len(str(sock_dir / "fake-mpv.sock")) > 95:  # pragma: no cover - 极端环境
            pytest.skip("找不到足够短的 AF_UNIX 套接字路径")
    sock_path = sock_dir / "fake-mpv.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    server.settimeout(5.0)
    captured: dict[str, bytes] = {}

    def serve() -> None:
        try:
            conn, _ = server.accept()
        except OSError:
            return  # 客户端未按期连入：线程自行退出，不留孤儿
        with conn:
            buffer = bytearray()
            while b"\n" not in buffer:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buffer.extend(chunk)
            captured["request"] = bytes(buffer).split(b"\n", 1)[0]
            time.sleep(0.2)  # 通道在而媒体未播：应答故意延迟
            request_id = json.loads(captured["request"]).get("request_id")
            conn.sendall(
                json.dumps({"error": "success", "data": 1.5, "request_id": request_id}).encode("utf-8") + b"\n"
            )

    thread = threading.Thread(target=serve, name="fake-mpv-e2e", daemon=True)
    thread.start()
    client = None
    try:
        sock = linux_video._connect_ipc_socket(str(sock_path), timeout=3.0)
        assert sock is not None
        channel = linux_video._UnixSocketChannel(sock)
        client = MpvIpcClient(channel.write_bytes, channel.read_bytes, close=channel.close)
        assert linux_video._verify_media_ready(client, timeout=3.0) is True
        assert b'"get_property"' in captured["request"]
        assert b"time-pos" in captured["request"]
    finally:
        if client is not None:
            client.close()
        server.close()
        thread.join(timeout=2.0)
        if sock_dir is not tmp_path:
            import shutil as _shutil

            _shutil.rmtree(sock_dir, ignore_errors=True)  # 回退目录由本测试自管


def _process_gone_or_zombie(pid: int, deadline: float = 5.0) -> bool:
    """进程已退出（或父进程先亡后暂为僵尸——同样视为已被收割，不留活体）。"""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return True
        with contextlib.suppress(psutil.NoSuchProcess):
            if proc.status() == psutil.STATUS_ZOMBIE:
                return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(sys.platform == "win32", reason="start_new_session/SIGTERM 组语义仅 POSIX")
@pytest.mark.skipif(psutil is None, reason="psutil unavailable (lightweight CI runner)")
def test_terminate_process_tree_reaps_parent_and_grandchild_without_sigkill():
    """真实父/孙 sleeper 进程树：SIGTERM 整组收割——父进程 returncode 为
    -SIGTERM（SIGKILL 升级会是 -9），孙进程在窗口内消失，不留孤儿。"""
    from platform_adapters.backends.linux import video as linux_video

    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # 与生产路径一致：播放器自成一个进程组
        text=True,
    )
    try:
        grandchild_pid = int(parent.stdout.readline().strip())
        assert grandchild_pid > 0

        # grace=5.0：慢 CI 上 SIGTERM 默认处置不应被误升级为 SIGKILL；
        # 乖巧进程约 0.05s 退出，实际耗时不受该上限影响。
        linux_video._terminate_process_tree(parent, grace=5.0)

        assert parent.returncode == -signal.SIGTERM  # 未升级 SIGKILL
        assert _process_gone_or_zombie(grandchild_pid)
    finally:
        with contextlib.suppress(Exception):
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=1.0)
        with contextlib.suppress(Exception):
            if parent.stdout is not None:
                parent.stdout.close()


def test_polling_property_observer_reports_changes_only_and_stop_joins_thread():
    """序列 [None, 1, 1, 2] → 回调恰为 [None, 1, 2]：首轮同步报一次（含
    None），1→1 不重报；stop() 有界 join，线程数回到基线。"""
    values = [None, 1, 1, 2]
    lock = threading.Lock()
    state = {"index": 0}

    def getter():
        with lock:
            index = state["index"]
            state["index"] = min(index + 1, len(values) - 1)
            return values[index]

    baseline_threads = threading.active_count()
    received: list = []
    delivered = threading.Event()

    def callback(value) -> None:
        received.append(value)
        if len(received) >= 3:
            delivered.set()

    observer = PollingPropertyObserver(getter, interval=0.02)
    try:
        assert observer.start(callback) is True
        assert delivered.wait(timeout=5.0), f"回调序列不足：{received}"
        assert received == [None, 1, 2]
    finally:
        observer.stop()

    assert threading.active_count() == baseline_threads
    assert not [t for t in threading.enumerate() if t.name == "mpv-property-poll" and t.is_alive()]


def test_legacy_backend_observe_property_platform_first_polling_fallback_or_false():
    """平台 observe_video_property 优先直接委托；平台仅有 get_video_property
    时轮询兜底（值变化才回调）；两者都缺失返回 False（能力探测不误报）。"""

    def observe(name, callback):
        delegated.append((name, callback))
        return True

    delegated: list[tuple[str, object]] = []
    platform_module = type("PlatformModule", (), {})()
    platform_module.observe_video_property = observe
    backend = LegacyModuleMpvBackend(platform_module)

    def on_event(_name, _value):
        return None

    assert backend.observe_property("time-pos", on_event) is True
    assert delegated == [("time-pos", on_event)]

    # 平台无 observe 但有 get_video_property：PollingPropertyObserver 兜底
    polling_module = type("PollingModule", (), {})()
    polling_module.get_video_property = lambda name: (True, 42)
    backend = LegacyModuleMpvBackend(polling_module)
    observed: list[tuple[str, object]] = []
    got_value = threading.Event()

    def on_value(name, value):
        observed.append((name, value))
        got_value.set()

    try:
        assert backend.observe_property("time-pos", on_value) is True
        assert got_value.wait(timeout=5.0), f"轮询回调未到达：{observed}"
        assert observed == [("time-pos", 42)]
    finally:
        for polling_observer in list(getattr(backend, "_poll_observers", {}).values()):
            polling_observer.stop()

    # 两者都缺失：返回 False
    bare_backend = LegacyModuleMpvBackend(type("BareModule", (), {})())
    assert bare_backend.observe_property("time-pos", on_event) is False


def test_platform_capability_contract():
    """两平台模块都暴露 send_video_ipc / get_video_property /
    observe_video_property / _verify_media_ready 能力点（与 mpv_backend 的
    getattr 探测名一致）；非 win32 下 windows video 的 _KERNEL32 为 None
    （导入安全契约）；两平台源码都接线 use_internal_libmpv() 统一门控。"""
    from platform_adapters.backends.linux import video as linux_video
    from platform_adapters.backends.windows import video as windows_video

    capability_points = (
        "send_video_ipc",
        "get_video_property",
        "observe_video_property",
        "_verify_media_ready",
    )
    for module in (linux_video, windows_video):
        for name in capability_points:
            assert callable(getattr(module, name, None)), f"{module.__name__}.{name} 缺失"

    if sys.platform != "win32":
        assert windows_video._KERNEL32 is None

    repo_root = Path(__file__).resolve().parents[1]
    for relative in (
        "src/platform_adapters/backends/linux/video.py",
        "src/platform_adapters/backends/windows/video.py",
    ):
        source = (repo_root / relative).read_text(encoding="utf-8")
        # 用正则而非字面串：对括号/空白等价重构稳健，仍要求接线真实存在。
        assert re.search(r"if\s+not\s+use_internal_libmpv\(\)\s*:", source), f"{relative} 未接线统一门控"


def test_verify_media_ready_thin_wrapper_passes_arguments_through(monkeypatch: pytest.MonkeyPatch):
    """平台 _verify_media_ready 是薄包装：client 与 timeout 原样直传
    verify_media_playing；client 为 None 时不触碰验证函数直接 False。"""
    from platform_adapters.backends.linux import video as linux_video
    from platform_adapters.backends.windows import video as windows_video

    seen: list[tuple[object, float]] = []

    def fake_verify(client, timeout=5.0):
        seen.append((client, timeout))
        return True

    monkeypatch.setattr(linux_video, "verify_media_playing", fake_verify)
    monkeypatch.setattr(windows_video, "verify_media_playing", fake_verify)

    sentinel = object()
    assert linux_video._verify_media_ready(sentinel, timeout=1.5) is True
    assert windows_video._verify_media_ready(sentinel, timeout=1.5) is True
    assert seen == [(sentinel, 1.5), (sentinel, 1.5)]

    assert linux_video._verify_media_ready(None) is False
    assert windows_video._verify_media_ready(None) is False
    assert len(seen) == 2  # None 直通分支不调用 verify
