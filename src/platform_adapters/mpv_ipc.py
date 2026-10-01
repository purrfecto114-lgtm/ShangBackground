"""平台中立的 mpv JSON IPC 协议层（仅标准库，无第三方依赖）。

mpv 的 ``--input-ipc-server`` 以「一行一条 JSON 消息」的 UTF-8 文本协议通信：

- 请求：``{"command": ["get_property", "time-pos"], "request_id": 7}\\n``
- 响应：``{"data": 3.5, "error": "success", "request_id": 7}\\n``
- 事件：``{"event": "end-file", ...}`` —— 不含 ``request_id``。

本模块刻意把「字节读写」注入化（write_bytes/read_bytes/close 三个小函数），
因此 Windows 命名管道与 Linux Unix socket 都能直接接入，测试也可以用内存
I/O 驱动协议层，不需要真实的 mpv 进程。
"""

from __future__ import annotations

import collections
import json
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any, Callable

__all__ = ["MpvIpcClient", "verify_media_playing"]

# 单行上限（约 1 MiB）：超出视为协议帧损坏，判死通道避免无界内存增长。
_MAX_LINE_BYTES = 1024 * 1024
# 事件暂存上限：溢出按 deque 语义挤出最旧事件。
_MAX_EVENTS = 256
# 写失败重试间隔与单次读取切片上限。
_WRITE_RETRY_INTERVAL = 0.02
_READ_SLICE_SECONDS = 0.25

WriteBytesFn = Callable[[bytes], int]
ReadBytesFn = Callable[[float], "bytes | None"]
CloseFn = Callable[[], None]

_UNSET = object()


class MpvIpcClient:
    """注入式 I/O 的 mpv JSON IPC 客户端。

    - ``write_bytes(payload) -> int``：全量写出（返回 <=0 或抛异常视为本次
      写失败，协议层会重试直至截止时间，仍未成功则判死）。
    - ``read_bytes(timeout) -> bytes | None``：返回 bytes（可能是不完整分片）、
      ``None``（超时内无数据，通道保留）、``b""``（EOF，判死）或抛异常
      （通道破裂，判死）。
    - 线程安全：RLock 串行化整个事务（写请求 → 读到匹配应答）。
    - 通道死亡（EOF / 异常 / 超长帧 / 写超时）后置 ``dead``，此后所有请求
      快速失败 ``(False, None)``，不再触碰 I/O。
    """

    def __init__(
        self,
        write_bytes: WriteBytesFn,
        read_bytes: ReadBytesFn,
        *,
        close: CloseFn | None = None,
    ) -> None:
        self._write_bytes = write_bytes
        self._read_bytes = read_bytes
        self._close_fn = close
        self._lock = threading.RLock()
        self._buffer = bytearray()
        self._events: collections.deque = collections.deque(maxlen=_MAX_EVENTS)
        self._request_counter = 0
        # dead：I/O 故障或协议破坏后置位；closed：close() 之后置位。
        self.dead = False
        self.closed = False

    # ------------------------------------------------------------------
    # 事务
    # ------------------------------------------------------------------
    def request(self, command: Sequence[Any] | Mapping[str, Any], timeout: float = 3.0) -> tuple[bool, Any]:
        """发送一条命令并等待携带相同 ``request_id`` 的应答。

        返回 ``(ok, data)``：``data`` 是完整应答字典（含 error/data 字段，
        失败应答也原样返回以便诊断）；超时、非法命令或通道死亡时返回
        ``(False, None)``。等待期间收到的事件（无 request_id 的消息）会
        暂存到内部队列，经 :meth:`take_events` 取出。
        """
        with self._lock:
            if self.dead or self.closed:
                return False, None
            message = self._build_message(command)
            if message is None:
                # 非法命令：不写线上任何字节。
                return False, None
            self._request_counter += 1
            request_id = self._request_counter
            message["request_id"] = request_id
            try:
                line = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                payload = (line + "\n").encode("utf-8")
            except (TypeError, ValueError, UnicodeError):
                return False, None
            deadline = time.monotonic() + max(0.05, float(timeout))
            if not self._write_payload(payload, deadline):
                return False, None
            return self._await_response(request_id, deadline)

    # ------------------------------------------------------------------
    # 便捷封装
    # ------------------------------------------------------------------
    def get_property(self, name: str, timeout: float = 3.0) -> tuple[bool, Any]:
        """读取属性；成功返回 ``(True, 属性值)``，失败返回 ``(False, None)``。"""
        ok, response = self.request(["get_property", str(name)], timeout=timeout)
        if not ok or not isinstance(response, dict):
            return False, None
        return True, response.get("data")

    def set_property(self, name: str, value: Any, timeout: float = 3.0) -> bool:
        """设置属性，返回 mpv 是否确认成功。"""
        ok, _response = self.request(["set_property", str(name), value], timeout=timeout)
        return ok

    def observe_property(self, oid: int, name: str, timeout: float = 3.0) -> bool:
        """注册 mpv 原生属性观察（事件会经 take_events 吐出）。"""
        try:
            oid_value = int(oid)
        except (TypeError, ValueError):
            return False
        ok, _response = self.request(["observe_property", oid_value, str(name)], timeout=timeout)
        return ok

    def take_events(self) -> list[dict[str, Any]]:
        """取出并清空暂存的 mpv 事件（无 request_id 的消息）。"""
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def close(self) -> None:
        """关闭底层通道；之后所有请求快速失败。"""
        with self._lock:
            if self.closed:
                return
            self.closed = True
            close_fn = self._close_fn
        if close_fn is not None:
            try:
                close_fn()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    @staticmethod
    def _build_message(command: Any) -> dict[str, Any] | None:
        """把命令规范化为待发送字典；非法命令返回 None（不写线上）。"""
        if isinstance(command, Mapping):
            message = dict(command)
            if not message.get("command"):
                return None
        elif isinstance(command, (str, bytes, bytearray)):
            return None
        elif isinstance(command, Sequence):
            items = list(command)
            if not items:
                return None
            message = {"command": items}
        else:
            return None
        return message

    def _write_payload(self, payload: bytes, deadline: float) -> bool:
        """全量写出 payload；<=0 或异常按固定间隔重试直至截止时间。"""
        offset = 0
        while offset < len(payload):
            if self.dead or self.closed:
                return False
            if time.monotonic() >= deadline:
                self.dead = True
                return False
            try:
                written = int(self._write_bytes(payload[offset:]))
            except Exception:
                written = 0
            if written > 0:
                offset += written
            else:
                time.sleep(_WRITE_RETRY_INTERVAL)
        return True

    def _await_response(self, request_id: int, deadline: float) -> tuple[bool, Any]:
        """按行读取直到出现匹配 request_id 的应答；事件暂存、坏行容忍。"""
        while True:
            if self.dead or self.closed:
                return False, None
            if time.monotonic() >= deadline:
                return False, None
            line = self._read_line(deadline)
            if line is None:
                # 超时（通道保留）或 EOF/故障（已判死）。
                return False, None
            try:
                message = json.loads(line.decode("utf-8", errors="replace"))
            except ValueError:
                continue  # 无法解析的行：容忍并跳过。
            if not isinstance(message, dict):
                continue
            peer_id = message.get("request_id")
            if peer_id is None:
                self._events.append(message)  # 事件：暂存供 take_events()。
                continue
            if peer_id == request_id:
                return message.get("error") == "success", message
            # 其它 request_id 的陈旧响应（串行化下不应出现）：丢弃。

    def _read_line(self, deadline: float) -> bytes | None:
        """跨分片组装一条完整行；None 表示超时或通道已死。"""
        while True:
            index = self._buffer.find(b"\n")
            if index >= 0:
                line = bytes(self._buffer[:index])
                del self._buffer[: index + 1]
                return line.rstrip(b"\r")
            if len(self._buffer) > _MAX_LINE_BYTES:
                # 帧损坏：丢弃缓冲并判死，避免无界内存增长。
                self._buffer.clear()
                self.dead = True
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                chunk = self._read_bytes(min(remaining, _READ_SLICE_SECONDS))
            except Exception:
                self.dead = True  # 通道破裂。
                return None
            if chunk is None:
                continue  # 提供方超时：由总 deadline 决定何时放弃。
            if chunk == b"":
                self.dead = True  # EOF。
                return None
            self._buffer.extend(chunk)


def verify_media_playing(client: MpvIpcClient, timeout: float = 5.0, poll_interval: float = 0.1) -> bool:
    """轮询 IPC 确认媒体「真实在播放」，防止起播黑屏被误报成功。

    就绪条件（任一满足即通过）：

    1. ``time-pos`` 可读且 > 0 —— 最直接的证据：播放位置已在前进；
    2. ``duration`` 可读且 > 0 且 ``pause`` 为 False —— 文件已加载且未暂停，
       用于 time-pos/eof-reached 不可用的精简构建。

    其余情况（属性恒 0/None、已暂停、通道死亡）持续轮询直至超时，
    超时返回 False，由调用方拆除播放器并向上回退。
    """
    if client is None:
        return False
    deadline = time.monotonic() + max(0.1, float(timeout))
    interval = max(0.01, float(poll_interval))

    def budget() -> float:
        """单次请求预算：不越过总截止时间，且单请求至多 1s。

        每次调用重新计算：若沿用轮首旧值，同轮最多 4 个串行
        get_property 会各自独享这份预算，实际耗时被放大到数倍。
        """
        return max(0.0, min(deadline - time.monotonic(), 1.0))

    while True:
        if client.dead or client.closed:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        ok, position = client.get_property("time-pos", timeout=budget())
        if ok and isinstance(position, (int, float)) and position > 0:
            return True
        ok, ended = client.get_property("eof-reached", timeout=budget())
        if not (ok and ended is True):
            # 未到文件末尾（或属性不可用）：用 duration + pause 综合判定。
            ok, duration = client.get_property("duration", timeout=budget())
            if ok and isinstance(duration, (int, float)) and duration > 0:
                ok_pause, paused = client.get_property("pause", timeout=budget())
                if ok_pause and paused is False:
                    return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))
