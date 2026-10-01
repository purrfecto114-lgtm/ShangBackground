"""SessionWallpaperService 的 schema=3 行为（KDE_SUPPORT_PLAN 任务 2 步骤 4）。

schema=2 现状：仅保存一个本地可恢复壁纸路径（wallpaper 字段语义 = 本地可恢复
路径，不得伪造）。schema=3 在此之上增加 ``backend_state``（KDE containment
插件级状态），并保持三条诚实边界：

- 无状态后端 / capture 失败 → 逐字节走 schema=2 现状路径（回归钉死）；
- wallpaper 字段仅在本地可恢复时填值，slideshow/远程场景留空不伪造；
- schema=2 旧文件读取时按需转换（converted_from=2），后端不支持状态恢复
  时不合成。

说明：本文件为 v1.6.3 批次 A 新建——任务规格将其表述为"扩展现有
tests/test_session_wallpaper_service.py"，但该文件在本仓库此前并不存在
（服务行为此前由集成测试间接覆盖），故按规格中的 FakeBackend 模式新建。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.ports import BackendResult
from app.runtime_state import SessionWallpaperState
from app.session_wallpaper_service import SessionWallpaperService

NOW = 1_700_000_000.0


class StatefulFakeBackend:
    """实现可选 StatefulWallpaperBackend 端口的假后端（记录调用顺序）。"""

    def __init__(self, current: str = "", state: dict | None = None, restore_ok: bool = True) -> None:
        self._current = current
        self._state = state
        self._restore_ok = restore_ok
        self.calls: list[str] = []
        self.set_calls: list[str] = []
        self.restore_calls: list[Any] = []

    def get_current(self) -> str:
        self.calls.append("get_current")
        return self._current

    def configure_fit_mode(self, mode: str) -> None:
        pass

    def set_wallpaper(self, path: str) -> None:
        self.calls.append("set_wallpaper")
        self.set_calls.append(path)

    def capture_state(self):
        self.calls.append("capture_state")
        return self._state

    def restore_state(self, state):
        self.calls.append("restore_state")
        self.restore_calls.append(state)
        return BackendResult(self._restore_ok, "restored" if self._restore_ok else "plasma refused")


class PlainFakeBackend:
    """schema=2 世界的不变量后端：没有 capture_state/restore_state。"""

    def __init__(self, current: str = "") -> None:
        self._current = current
        self.set_calls: list[str] = []

    def get_current(self) -> str:
        return self._current

    def configure_fit_mode(self, mode: str) -> None:
        pass

    def set_wallpaper(self, path: str) -> None:
        self.set_calls.append(path)


class RaisingCaptureBackend(PlainFakeBackend):
    def capture_state(self):
        raise RuntimeError("plasma scripting channel exploded")


def _make_service(tmp_path: Path, backend: Any):
    state = SessionWallpaperState()
    config: dict[str, Any] = {}
    logs: list[str] = []
    session_file = tmp_path / "session_wallpaper.json"
    service = SessionWallpaperService(
        state=state,
        backend=backend,
        config=lambda: config,
        persist_config=lambda: True,
        primary_file=lambda: str(session_file),
        legacy_files=lambda: (),
        platform_name=lambda: "linux",
        app_base_dir=lambda: str(tmp_path),
        log=logs.append,
        verify_delays=(0.0,),
        now=lambda: NOW,
    )
    return service, state, config, logs, session_file


def _image_file(tmp_path: Path) -> Path:
    image = tmp_path / "wp.jpg"
    image.write_bytes(b"image-data")
    return image


def _state_dict(image: str = "", plugin: str = "org.kde.slideshow") -> dict:
    return {
        "schema": 3,
        "kind": "kde-plasma-containments",
        "containments": [
            {
                "id": 12,
                "screen": 0,
                "plugin": plugin,
                "image": image,
                "image_uri": f"file://{image}" if image else "",
                "fill_mode": 2 if image else None,
                "config_captured": bool(image) and plugin == "org.kde.image",
            }
        ],
    }


def _write_session_file(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# capture + persist（测试 8-10）
# ---------------------------------------------------------------------------


def test_schema3_persist_roundtrip(tmp_path: Path):
    """状态后端：文件内容 schema==3、backend_state 在、路径语义不变。"""
    image = _image_file(tmp_path)
    backend = StatefulFakeBackend(current=str(image), state=_state_dict(str(image), plugin="org.kde.image"))
    service, state, _config, _logs, session_file = _make_service(tmp_path, backend)

    assert service.capture() is True
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    assert payload["schema"] == 3
    assert payload["wallpaper"] == str(image)
    assert payload["backend_state"] == _state_dict(str(image), plugin="org.kde.image")
    # 顺序契约：先 get_current() 再 capture_state()（只读捕获不受污染）。
    assert backend.calls == ["get_current", "capture_state"]
    snapshot = state.snapshot()
    assert snapshot.captured is True
    assert snapshot.backend_state == _state_dict(str(image), plugin="org.kde.image")


def test_capture_slideshow_with_state_records_empty_wallpaper(tmp_path: Path):
    """slideshow 场景 get_current() 返回 ""：仍记录（这正是 v1.6.2 WARN
    声明的缺口），wallpaper 字段留空不伪造本地路径。"""
    backend = StatefulFakeBackend(current="", state=_state_dict(""))
    service, state, _config, logs, session_file = _make_service(tmp_path, backend)

    assert service.capture() is True
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    assert payload["schema"] == 3
    assert payload["wallpaper"] == ""
    assert payload["backend_state"]["containments"][0]["plugin"] == "org.kde.slideshow"
    assert any("插件级状态" in message for message in logs)
    assert state.snapshot().backend_state is not None
    # 有状态时可恢复候选成立（内存或文件任一）。
    assert service.has_restore_candidate() is True


def test_capture_without_state_backend_stays_schema2(tmp_path: Path):
    """回归：无状态后端 → schema=2 现状逐字节不变。"""
    image = _image_file(tmp_path)
    backend = PlainFakeBackend(current=str(image))
    service, _state, _config, _logs, session_file = _make_service(tmp_path, backend)

    assert service.capture() is True
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    assert payload["schema"] == 2
    assert "backend_state" not in payload
    assert payload["wallpaper"] == str(image)


def test_capture_state_exception_falls_back_to_schema2(tmp_path: Path):
    """capture_state 抛异常：安静回退路径模式（日志如实），schema=2。"""
    image = _image_file(tmp_path)
    backend = RaisingCaptureBackend(current=str(image))
    service, _state, _config, logs, session_file = _make_service(tmp_path, backend)

    assert service.capture() is True
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    assert payload["schema"] == 2
    assert "backend_state" not in payload
    assert any("捕获壁纸插件状态失败" in message for message in logs)


def test_capture_unrestorable_image_with_state_keeps_empty_wallpaper(tmp_path: Path):
    """get_current() 返回远程 URL（不可恢复）：wallpaper 留空，状态照记。"""
    backend = StatefulFakeBackend(current="https://example.com/x.jpg", state=_state_dict(""))
    service, _state, _config, _logs, session_file = _make_service(tmp_path, backend)

    assert service.capture() is True
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    assert payload["schema"] == 3
    assert payload["wallpaper"] == ""


# ---------------------------------------------------------------------------
# load（测试 11、15）
# ---------------------------------------------------------------------------


def test_load_schema2_converts_to_backend_state_when_supported(tmp_path: Path):
    """计划步骤 4：schema=2 路径本来就是 org.kde.image 语义——后端支持
    restore_state 时合成状态（fill_mode None=不动）。"""
    image = _image_file(tmp_path)
    backend = StatefulFakeBackend(current=str(image))
    service, state, _config, _logs, session_file = _make_service(tmp_path, backend)
    _write_session_file(
        session_file,
        {
            "schema": 2,
            "platform": "linux",
            "wallpaper": str(image),
            "style": {},
            "captured_at": NOW - 10,
            "pid": 1,
            "app_base_dir": str(tmp_path),
        },
    )

    assert service.load() is True
    converted = state.snapshot().backend_state
    assert converted is not None
    assert converted["schema"] == 3
    assert converted["kind"] == "kde-plasma-containments"
    assert converted["converted_from"] == 2
    entry = converted["containments"][0]
    assert entry["plugin"] == "org.kde.image"
    assert entry["image"] == str(image)
    assert entry["image_uri"] == str(image)
    assert entry["fill_mode"] is None
    assert entry["config_captured"] is True
    assert state.snapshot().wallpaper == str(image)


def test_load_schema2_does_not_convert_without_state_backend(tmp_path: Path):
    image = _image_file(tmp_path)
    backend = PlainFakeBackend(current=str(image))
    service, state, _config, _logs, session_file = _make_service(tmp_path, backend)
    _write_session_file(
        session_file,
        {
            "schema": 2,
            "platform": "linux",
            "wallpaper": str(image),
            "style": {},
            "captured_at": NOW - 10,
            "pid": 1,
            "app_base_dir": str(tmp_path),
        },
    )

    assert service.load() is True
    assert state.snapshot().backend_state is None
    assert state.snapshot().wallpaper == str(image)


def test_load_schema3_empty_wallpaper_keeps_file_and_state(tmp_path: Path):
    """schema=3 空 wallpaper + backend_state：不删文件、状态载入
    （状态级恢复不依赖本地路径）。"""
    backend = StatefulFakeBackend(current="")
    service, state, _config, _logs, session_file = _make_service(tmp_path, backend)
    state_payload = _state_dict("")
    _write_session_file(
        session_file,
        {
            "schema": 3,
            "platform": "linux",
            "wallpaper": "",
            "style": {},
            "backend_state": state_payload,
            "captured_at": NOW - 10,
            "pid": 1,
            "app_base_dir": str(tmp_path),
        },
    )

    assert service.load() is True
    assert session_file.exists()
    assert state.snapshot().wallpaper == ""
    assert state.snapshot().backend_state == state_payload


def test_load_schema3_missing_backend_state_falls_back_to_path_logic(tmp_path: Path):
    """schema=3 但 backend_state 缺失/非 dict → 回落现有路径逻辑
    （不可恢复即按现状忽略并删除）。"""
    backend = StatefulFakeBackend(current="")
    service, _state, _config, _logs, session_file = _make_service(tmp_path, backend)
    _write_session_file(
        session_file,
        {
            "schema": 3,
            "platform": "linux",
            "wallpaper": "",
            "style": {},
            "captured_at": NOW - 10,
            "pid": 1,
            "app_base_dir": str(tmp_path),
        },
    )

    assert service.load() is False
    assert not session_file.exists()


# ---------------------------------------------------------------------------
# restore（测试 12-14）
# ---------------------------------------------------------------------------


def test_restore_with_state_skips_path_set(tmp_path: Path):
    """有状态恢复成功：set_wallpaper 不得被调用（避免把非 image
    containment 强转为 org.kde.image），config 更新。"""
    image = _image_file(tmp_path)
    backend = StatefulFakeBackend(current=str(image), state=_state_dict(str(image), plugin="org.kde.image"))
    service, _state, config, logs, session_file = _make_service(tmp_path, backend)
    assert service.capture() is True

    assert service.restore(finalize=True) is True
    assert backend.restore_calls and backend.restore_calls[0] == _state_dict(str(image), plugin="org.kde.image")
    assert backend.set_calls == []
    assert config["current_wallpaper"] == str(image)
    assert any("已恢复启动前壁纸插件状态" in message for message in logs)
    assert not session_file.exists()  # finalize 语义不变


def test_restore_with_state_failure_falls_back_to_path(tmp_path: Path):
    """插件状态恢复失败 + target 可恢复 → 诚实降级为图片路径恢复。"""
    image = _image_file(tmp_path)
    backend = StatefulFakeBackend(
        current=str(image), state=_state_dict(str(image), plugin="org.kde.image"), restore_ok=False
    )
    service, _state, config, logs, session_file = _make_service(tmp_path, backend)
    assert service.capture() is True

    assert service.restore(finalize=True) is True
    assert backend.restore_calls  # 状态恢复确实尝试过
    assert backend.set_calls == [str(image)]  # 回退路径恢复被执行
    assert config["current_wallpaper"] == str(image)
    assert any("回退" in message for message in logs)
    assert not session_file.exists()


def test_restore_with_state_failure_and_unrestorable_target_returns_false(tmp_path: Path):
    """插件状态恢复失败 + target 不可恢复（slideshow）→ False，
    保留会话记录供重试（不 clear_files）。"""
    backend = StatefulFakeBackend(current="", state=_state_dict(""), restore_ok=False)
    service, _state, config, logs, session_file = _make_service(tmp_path, backend)
    assert service.capture() is True

    assert service.restore(finalize=True) is False
    assert backend.set_calls == []
    assert "current_wallpaper" not in config
    assert session_file.exists()
    assert any("插件状态恢复失败" in message for message in logs)


def test_restore_without_backend_state_is_unchanged_schema2(tmp_path: Path):
    """回归：无状态（schema=2）→ 现状路径恢复逐字节不变。"""
    image = _image_file(tmp_path)
    backend = PlainFakeBackend(current=str(image))
    service, _state, config, _logs, session_file = _make_service(tmp_path, backend)
    assert service.capture() is True

    assert service.restore(finalize=True) is True
    assert backend.set_calls == [str(image)]
    assert config["current_wallpaper"] == str(image)
    assert not session_file.exists()


def test_restore_state_loaded_from_file_restores_plugin_state(tmp_path: Path):
    """内存无状态（重启后）：restore 先 load() 再走状态路径。"""
    backend = StatefulFakeBackend(current="", state=None, restore_ok=True)
    service, _state, config, logs, session_file = _make_service(tmp_path, backend)
    state_payload = _state_dict("")
    _write_session_file(
        session_file,
        {
            "schema": 3,
            "platform": "linux",
            "wallpaper": "",
            "style": {},
            "backend_state": state_payload,
            "captured_at": NOW - 10,
            "pid": 1,
            "app_base_dir": str(tmp_path),
        },
    )

    assert service.restore(finalize=True) is True
    assert backend.restore_calls == [state_payload]
    assert backend.set_calls == []
    assert "current_wallpaper" not in config  # target 为空：不写
    assert any("已恢复启动前壁纸插件状态" in message for message in logs)
    assert not session_file.exists()


def test_restore_state_backend_missing_falls_back_to_path(tmp_path: Path):
    """内存有状态但后端没有 restore_state（装配缺口）→ 现状路径。"""
    image = _image_file(tmp_path)
    backend = PlainFakeBackend(current=str(image))
    service, state, config, _logs, _session_file = _make_service(tmp_path, backend)
    # 直接植入状态（模拟 capture 时的后端与 restore 时的后端不一致）。
    state.replace(str(image), {}, _state_dict(str(image), plugin="org.kde.image"))

    assert service.restore(finalize=True) is True
    assert backend.set_calls == [str(image)]
    assert config["current_wallpaper"] == str(image)


def test_restore_persist_failure_keeps_session_record(tmp_path: Path):
    """状态恢复成功但配置保存失败：回滚 config 值、保留会话记录、返回 False
    ——与现状失败语义一致。"""
    image = _image_file(tmp_path)
    backend = StatefulFakeBackend(current=str(image), state=_state_dict(str(image), plugin="org.kde.image"))
    service, _state, config, _logs, session_file = _make_service(tmp_path, backend)
    assert service.capture() is True

    original_persist = service._persist_config
    service._persist_config = lambda: False  # type: ignore[method-assign]
    try:
        assert service.restore(finalize=True) is False
    finally:
        service._persist_config = original_persist  # type: ignore[method-assign]
    assert config.get("current_wallpaper") is None
    assert session_file.exists()  # 保留供重试
