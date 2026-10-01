"""mpv JSON IPC 协议层契约（注入式内存 I/O，无需真实 mpv 进程）。

钉住 MpvIpcClient 的关键语义：request_id 单调关联、事件（无 request_id）
暂存不与应答混淆、错误应答原样透传、超时/EOF/读异常判死与快速失败、
跨 read_bytes 分片按行重组、get/set/observe_property 线上帧格式、
非法命令不写线上、事件队列 deque(maxlen=256) 的挤出与排空语义。
"""

from __future__ import annotations

import collections
import json

from platform_adapters.mpv_ipc import MpvIpcClient


class _FakeMpv:
    """内存假 mpv：write 时解析命令行，responder 决定吐回的读取分片序列。

    responder(message) 返回一个列表，每个元素为一次 read_bytes 的返回值：
    - bytes：本轮回给协议层的数据分片（完整行或半截分片均可）；
    - None：本轮无数据（通道保留，由总 deadline 决定何时放弃）；
    - b""：EOF（协议层判死）。
    """

    def __init__(self, responder=None) -> None:
        self.responder = responder
        self.wire: list[bytes] = []  # 每次 write_bytes 的原始分片
        self.pending: collections.deque = collections.deque()
        self.read_calls = 0
        self.write_calls = 0
        self.closed = False
        self.eof = False
        self.read_error: BaseException | None = None
        self._unparsed = bytearray()

    def write_bytes(self, payload: bytes) -> int:
        self.write_calls += 1
        data = bytes(payload)
        self.wire.append(data)
        self._unparsed.extend(data)
        while True:
            index = self._unparsed.find(b"\n")
            if index < 0:
                break
            line = bytes(self._unparsed[:index])
            del self._unparsed[: index + 1]
            if line.strip() and callable(self.responder):
                self.pending.extend(self.responder(json.loads(line.decode("utf-8"))))
        return len(data)

    def read_bytes(self, timeout: float):
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        if self.eof:
            return b""
        if self.pending:
            return self.pending.popleft()
        return None

    def close(self) -> None:
        self.closed = True

    def wire_lines(self) -> list[bytes]:
        return b"".join(self.wire).split(b"\n")[:-1]


def _success(message: dict, data) -> bytes:
    payload = {"error": "success", "data": data, "request_id": message.get("request_id")}
    return json.dumps(payload).encode("utf-8") + b"\n"


def test_request_id_correlation_and_event_parking_interleave():
    """应答按 request_id 关联；等待期事件（无 request_id）只暂存不混淆。"""

    def responder(message: dict) -> list:
        return [
            b'{"event": "end-file", "reason": "stop"}\n',  # 应答前到达的事件
            _success(message, 3.5),
            b'{"event": "tick"}\n',  # 应答后到达的事件
        ]

    fake = _FakeMpv(responder)
    client = MpvIpcClient(fake.write_bytes, fake.read_bytes, close=fake.close)

    ok, data = client.request(["get_property", "time-pos"], timeout=2.0)

    assert ok is True
    assert data["data"] == 3.5
    assert data["request_id"] == 1  # 首个请求的 request_id 单调从 1 开始
    assert json.loads(fake.wire[0])["request_id"] == 1
    # 应答前夹塞的事件被暂存，绝不与应答混淆
    assert client.take_events() == [{"event": "end-file", "reason": "stop"}]
    assert client.take_events() == []  # take_events 排空语义
    # 匹配应答一到达事务即结束：其后的事件留在通道里，由下一次读取消费
    assert list(fake.pending) == [b'{"event": "tick"}\n']


def test_error_response_returns_false_with_full_data():
    """失败应答返回 (False, 完整应答字典) 以便诊断，而非吞成 (False, None)。"""

    def responder(message: dict) -> list:
        payload = {
            "error": "property unavailable",
            "data": None,
            "request_id": message.get("request_id"),
        }
        return [json.dumps(payload).encode("utf-8") + b"\n"]

    fake = _FakeMpv(responder)
    client = MpvIpcClient(fake.write_bytes, fake.read_bytes)

    ok, data = client.request(["get_property", "duration"], timeout=1.0)

    assert ok is False
    assert data == {"error": "property unavailable", "data": None, "request_id": 1}


def test_timeout_returns_false_none_and_keeps_channel_alive():
    """应答超时：(False, None)，通道保留（不判死，可继续复用）。"""
    fake = _FakeMpv(lambda _message: [])  # 从不应答
    client = MpvIpcClient(fake.write_bytes, fake.read_bytes)

    ok, data = client.request(["get_property", "time-pos"], timeout=0.15)

    assert (ok, data) == (False, None)
    assert client.dead is False
    assert fake.read_calls > 0
    # 通道仍在：后续请求照常写线上
    writes_before = fake.write_calls
    client.request(["get_property", "pause"], timeout=0.15)
    assert fake.write_calls == writes_before + 1


def test_eof_marks_dead_and_later_requests_fast_fail_without_reads():
    """EOF（read 返回 b""）判死；之后请求快速失败，读调用次数冻结。"""
    fake = _FakeMpv()
    client = MpvIpcClient(fake.write_bytes, fake.read_bytes, close=fake.close)

    def responder(_message):
        fake.eof = True  # 下一次 read_bytes 返回 EOF
        return []

    fake.responder = responder
    assert client.request(["get_property", "time-pos"], timeout=1.0) == (False, None)
    assert client.dead is True

    reads_at_death = fake.read_calls
    writes_at_death = fake.write_calls
    for _ in range(3):
        assert client.request(["get_property", "pause"], timeout=0.5) == (False, None)
    assert fake.read_calls == reads_at_death  # 快速失败：不再触碰 I/O
    assert fake.write_calls == writes_at_death
    assert fake.closed is False  # 判死不等于关闭；close 需显式调用


def test_read_exception_marks_dead_channel():
    """read_bytes 抛异常视为通道破裂：判死并快速失败。"""
    fake = _FakeMpv()
    client = MpvIpcClient(fake.write_bytes, fake.read_bytes)

    def responder(_message):
        fake.read_error = OSError("ipc channel broken")
        return []

    fake.responder = responder
    assert client.request(["get_property", "time-pos"], timeout=1.0) == (False, None)
    assert client.dead is True

    reads_at_death = fake.read_calls
    assert client.request(["get_property", "pause"], timeout=0.5) == (False, None)
    assert fake.read_calls == reads_at_death


def test_fragmented_reads_reassemble_across_chunk_boundary():
    """跨 read_bytes 分片按行重组：拆分点刻意落在 "success" 单词内部——
    任何拼接/重组错误都会让 error != "success" 或 JSON 解析失败而超时。"""
    head = b'{"error": "suc'
    tail = b'cess", "data": 42, "request_id": 1}\n'
    assert json.loads(head + tail)["error"] == "success"  # 防止 fixture 自身拼写错位

    fake = _FakeMpv(lambda _message: [head, tail])
    client = MpvIpcClient(fake.write_bytes, fake.read_bytes)

    ok, data = client.request(["get_property", "time-pos"], timeout=2.0)

    assert ok is True
    assert data["data"] == 42


def test_property_helpers_wire_frames_and_invalid_command_writes_nothing():
    """get/set/observe_property 的线上帧；非法命令不写出任何字节。"""
    fake = _FakeMpv(lambda message: [_success(message, True)])
    client = MpvIpcClient(fake.write_bytes, fake.read_bytes)

    assert client.get_property("time-pos") == (True, True)
    assert client.set_property("pause", True) is True
    assert client.observe_property(7, "time-pos") is True

    lines = fake.wire_lines()
    assert lines == [
        b'{"command":["get_property","time-pos"],"request_id":1}',
        b'{"command":["set_property","pause",true],"request_id":2}',
        b'{"command":["observe_property",7,"time-pos"],"request_id":3}',
    ]

    writes_before = fake.write_calls
    assert client.request([]) == (False, None)  # 空序列
    assert client.request("get_property pause") == (False, None)  # 裸字符串
    assert client.request({"command": []}) == (False, None)  # 映射缺命令
    assert fake.write_calls == writes_before
    assert fake.wire_lines() == lines  # 线上没有任何新增字节


def test_take_events_drains_and_evicts_beyond_maxlen_256():
    """等待应答期间涌入 300 条事件：deque(maxlen=256) 挤出最旧，保留 45..300。"""

    def responder(message: dict) -> list:
        chunks = [json.dumps({"event": "tick", "seq": seq}).encode("utf-8") + b"\n" for seq in range(1, 301)]
        chunks.append(_success(message, None))
        return chunks

    fake = _FakeMpv(responder)
    client = MpvIpcClient(fake.write_bytes, fake.read_bytes)

    assert client.request(["get_property", "time-pos"], timeout=2.0)[0] is True

    events = client.take_events()
    assert len(events) == 256
    assert [event["seq"] for event in events] == list(range(45, 301))
    assert client.take_events() == []  # 再次取出：已排空
