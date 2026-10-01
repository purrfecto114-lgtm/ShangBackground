"""KDE 插件级壁纸状态保存/恢复（schema=3，KDE_SUPPORT_PLAN 任务 2）+ D-Bus 前置检查（任务 3 步骤 1-2）。

v1.6.2 批次选择了"明确降级范围"（kde_wallpaper_restore_scope + doctor WARN）；
本批次交付替代目标 schema=3：

- ``capture_wallpaper_state()`` 逐 containment 读取 plugin/Image/FillMode 与
  screen 关联；诚实规则：``config_captured=True`` 仅当 plugin==org.kde.image
  且读到 Image 值（FillMode 可缺省——KConfig 不写默认值键，缺省即缺省，
  恢复时不写该键）——slideshow/color/第三方插件只保插件名，
  内部配置不伪造；远程 URL 原样记录、不转成本地路径。
- ``restore_wallpaper_state()`` 生成一段 Plasma 脚本按 id→screen→全量兜底的
  顺序恢复 containment（复用 ``_kde_set_script`` 的 json.dumps 防注入模式）。
- D-Bus 前置检查：``DBUS_SESSION_BUS_ADDRESS`` 与 ``$XDG_RUNTIME_DIR/bus``
  均不可用时，在任何外部命令 spawn 之前返回可操作错误——与"命令缺失"
  （command not found）和"Plasma 拒绝"（rc!=0 stderr）三种错误可区分。

风格对齐 tests/test_kde_restore_scope.py：monkeypatch ``_run_plasma_script`` /
``_run_args`` / 环境，绝不触碰真实桌面。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# capture_wallpaper_state()
# ---------------------------------------------------------------------------


def _capture(monkeypatch: pytest.MonkeyPatch, *, kde: bool, script_ok: bool = True, out: str = ""):
    from platform_adapters.backends.linux import integration

    monkeypatch.setattr(integration, "_is_kde_session", lambda: kde)
    monkeypatch.setattr(
        integration,
        "_run_plasma_script",
        lambda _script, timeout=8, allow_dbus_send=False: (script_ok, out, "" if script_ok else "qdbus not found"),
    )
    return integration.capture_wallpaper_state()


def test_kde_snapshot_preserves_plugin_and_config(monkeypatch: pytest.MonkeyPatch):
    """两个 containment：image 全量（plugin+image+fillmode）与 slideshow 部分
    （只有插件名）——dict 形状与诚实标志必须逐字段正确。"""
    out = (
        "ID:12  SCREEN:0  PLUGIN:org.kde.image\n"
        "IMAGE:file:///home/u/p.jpg\n"
        "FILLMODE:2\n"
        "ID:13  SCREEN:1  PLUGIN:org.kde.slideshow\n"
    )
    state = _capture(monkeypatch, kde=True, out=out)
    assert isinstance(state, dict)
    assert state["schema"] == 3
    assert state["kind"] == "kde-plasma-containments"
    image_entry, slideshow_entry = state["containments"]
    assert image_entry == {
        "id": 12,
        "screen": 0,
        "plugin": "org.kde.image",
        "image": "/home/u/p.jpg",
        "image_uri": "file:///home/u/p.jpg",
        "fill_mode": 2,
        "config_captured": True,
    }
    assert slideshow_entry == {
        "id": 13,
        "screen": 1,
        "plugin": "org.kde.slideshow",
        "image": "",
        "image_uri": "",
        "fill_mode": None,
        "config_captured": False,
    }


def test_capture_keeps_remote_image_raw(monkeypatch: pytest.MonkeyPatch):
    """远程 URL：image_uri 原样、image 留空（不伪造本地路径），
    plugin 为 org.kde.image 且 FillMode 有效时 config_captured 仍为 True。"""
    out = "ID:7  SCREEN:0  PLUGIN:org.kde.image\nIMAGE:https://example.com/wall.jpg\nFILLMODE:1\n"
    state = _capture(monkeypatch, kde=True, out=out)
    entry = state["containments"][0]
    assert entry["image"] == ""
    assert entry["image_uri"] == "https://example.com/wall.jpg"
    assert entry["config_captured"] is True


def test_capture_accepts_bare_local_path(monkeypatch: pytest.MonkeyPatch):
    """Plasma 也可能存绝对路径形态：image/image_uri 都能承载本地路径，
    restore 对两态都能写。"""
    out = "ID:8  SCREEN:0  PLUGIN:org.kde.image\nIMAGE:/home/u/q.jpg\nFILLMODE:0\n"
    state = _capture(monkeypatch, kde=True, out=out)
    entry = state["containments"][0]
    assert entry["image"] == "/home/u/q.jpg"
    assert entry["image_uri"] == "/home/u/q.jpg"
    assert entry["config_captured"] is True


def test_capture_returns_none_when_not_kde_or_unreachable(monkeypatch: pytest.MonkeyPatch):
    # 非 KDE 会话：无状态级捕获（服务层回退 schema=2）。
    assert _capture(monkeypatch, kde=False) is None
    # KDE 会话但脚本通道不可达：同样回退，不抛异常。
    assert _capture(monkeypatch, kde=True, script_ok=False) is None
    # 脚本成功但没有任何 containment 行：视为不可捕获，不返回空壳状态。
    assert _capture(monkeypatch, kde=True, out="") is None


def test_capture_image_plugin_without_fillmode_keeps_image(monkeypatch: pytest.MonkeyPatch):
    """org.kde.image 且 FillMode 缺省（KConfig 默认不写默认值键，常见形态）：
    config_captured 必须仍为 True 且 Image 必须被记录——主线程校准 R1：
    若把缺省 FillMode 视为"配置不完整"，恢复时只写插件名会丢掉 Image，
    比 schema=2 更差。诚实语义=缺省键保持缺省（fill_mode=None，恢复不写）。
    """
    out = "ID:9  SCREEN:0  PLUGIN:org.kde.image\nIMAGE:file:///home/u/r.jpg\nFILLMODE:\n"
    state = _capture(monkeypatch, kde=True, out=out)
    entry = state["containments"][0]
    assert entry["fill_mode"] is None
    assert entry["config_captured"] is True
    assert entry["image"] == "/home/u/r.jpg"
    assert entry["image_uri"] == "file:///home/u/r.jpg"


def test_restore_with_missing_fillmode_writes_image_but_not_fillmode(monkeypatch: pytest.MonkeyPatch):
    """R1 回归钉：fill_mode=None 的完整条目——恢复脚本必须携带 Image，
    且 entries JSON 中 fill_mode 为 null（JS 侧 null 检查跳过 writeConfig）。"""
    state = {
        "schema": 3,
        "kind": "kde-plasma-containments",
        "containments": [
            {
                "id": 9,
                "screen": 0,
                "plugin": "org.kde.image",
                "image": "/home/u/r.jpg",
                "image_uri": "file:///home/u/r.jpg",
                "fill_mode": None,
                "config_captured": True,
            }
        ],
    }
    (ok, message), captured = _restore(monkeypatch, state)
    assert ok is True
    script = captured["script"]
    # Image 必须进入恢复脚本（writeConfig 由 JS 侧 imageValue 门控写入）。
    assert "file:///home/u/r.jpg" in script
    # fill_mode=null 进入 entries JSON；JS 的 null 检查保证不写 FillMode。
    assert '"fill_mode": null' in script


# ---------------------------------------------------------------------------
# restore_wallpaper_state()
# ---------------------------------------------------------------------------


def _restore(monkeypatch: pytest.MonkeyPatch, state, *, script_ok=True, out="SHANGBACKGROUND_KDE_RESTORE_DONE:2\n"):
    """mock 掉 Plasma 脚本通道，捕获生成的脚本并返回 (结果, 脚本)。"""
    from platform_adapters.backends.linux import integration

    captured: dict[str, str] = {}

    def fake_run(script, timeout=10, allow_dbus_send=False):
        captured["script"] = script
        captured["timeout"] = timeout
        return (script_ok, out if script_ok else "", "" if script_ok else "qdbus: rejected by Plasma")

    monkeypatch.setattr(integration, "_is_kde_session", lambda: True)
    monkeypatch.setattr(integration, "_run_plasma_script", fake_run)
    result = integration.restore_wallpaper_state(state)
    return result, captured


def _two_entry_state() -> dict:
    return {
        "schema": 3,
        "kind": "kde-plasma-containments",
        "containments": [
            {
                "id": 12,
                "screen": 0,
                "plugin": "org.kde.image",
                "image": "/home/u/p.jpg",
                "image_uri": "file:///home/u/p.jpg",
                "fill_mode": 2,
                "config_captured": True,
            },
            {
                "id": 13,
                "screen": 1,
                "plugin": "org.kde.slideshow",
                "image": "",
                "image_uri": "",
                "fill_mode": None,
                "config_captured": False,
            },
        ],
    }


def test_restore_writes_plugin_and_config(monkeypatch: pytest.MonkeyPatch):
    result, captured = _restore(monkeypatch, _two_entry_state())
    ok, message = result
    assert ok is True
    assert "2 个 containment" in message
    script = captured["script"]
    # 插件与配置写入原语必须存在。
    assert "wallpaperPlugin" in script
    assert 'writeConfig("Image"' in script
    assert 'writeConfig("FillMode"' in script
    assert "reloadConfig()" in script
    # 状态经 json.dumps 注入（防注入 + 可断言）：id 与插件名原样进入脚本。
    assert '"id": 12' in script
    assert '"org.kde.image"' in script
    assert '"org.kde.slideshow"' in script
    # 诚实边界：config_captured=False 的条目只写插件名——门控标志进入脚本。
    assert '"config_captured": false' in script
    assert '"config_captured": true' in script
    # id 匹配优先于 screen 回退。
    assert "findById" in script
    assert "findByScreen" in script
    # 6s 超时（区别于读脚本的 8-10s）。
    assert captured["timeout"] == 6


def test_restore_records_outcome_for_diagnostics(monkeypatch: pytest.MonkeyPatch):
    from platform_adapters.backends.linux import integration

    result, _captured = _restore(monkeypatch, _two_entry_state())
    ok, _message = result
    outcome = integration.last_kde_set_outcome()
    assert ok is True
    assert outcome["accepted"] is True
    # 插件级恢复不伪造读回确认：verified=False 且 detail 如实说明。
    assert outcome["verified"] is False
    assert outcome["method"] == "restore_state"


def test_restore_failure_reports_plasma_rejection(monkeypatch: pytest.MonkeyPatch):
    result, _captured = _restore(monkeypatch, _two_entry_state(), script_ok=False)
    ok, message = result
    assert ok is False
    assert "qdbus: rejected by Plasma" in message


def test_restore_rejects_unknown_state(monkeypatch: pytest.MonkeyPatch):
    ok, message = _restore(monkeypatch, {"schema": 3})[0]
    assert ok is False
    assert "无法识别的壁纸状态格式" in message
    ok, message = _restore(monkeypatch, {"schema": 3, "kind": "something-else", "containments": []})[0]
    assert ok is False
    assert "无法识别的壁纸状态格式" in message
    ok, message = _restore(monkeypatch, {"schema": 3, "kind": "kde-plasma-containments", "containments": []})[0]
    assert ok is False
    assert "无法识别的壁纸状态格式" in message
    ok, message = _restore(monkeypatch, "not-a-dict")[0]
    assert ok is False


def test_restore_declines_outside_kde_session(monkeypatch: pytest.MonkeyPatch):
    """诚实边界：非 KDE 会话不执行 Plasma 脚本（Linux GNOME 用户的
    schema=2 转换状态会得到明确拒绝，服务层回退路径恢复）。"""
    from platform_adapters.backends.linux import integration

    monkeypatch.setattr(integration, "_is_kde_session", lambda: False)
    monkeypatch.setattr(
        integration,
        "_run_plasma_script",
        lambda _script, timeout=6, allow_dbus_send=False: (_ for _ in ()).throw(
            AssertionError("must not spawn Plasma scripts outside KDE")
        ),
    )
    ok, message = integration.restore_wallpaper_state(_two_entry_state())
    assert ok is False
    assert "KDE" in message


# ---------------------------------------------------------------------------
# D-Bus 前置检查（KDE_SUPPORT_PLAN 任务 3 步骤 2）
# ---------------------------------------------------------------------------


def test_session_bus_endpoint_missing_matrix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from platform_adapters.backends.linux import integration

    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert integration._session_bus_endpoint_missing() is True

    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    assert integration._session_bus_endpoint_missing() is False

    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "bus").write_bytes(b"")  # os.path.exists 即可，不校验 socket 类型
    assert integration._session_bus_endpoint_missing() is False

    (tmp_path / "bus").unlink()
    assert integration._session_bus_endpoint_missing() is True


def _dbus_env(monkeypatch: pytest.MonkeyPatch, *, address: str, runtime_dir: str):
    if address:
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    else:
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    if runtime_dir:
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime_dir)
    else:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)


def _run_set_with_dbus_env(monkeypatch: pytest.MonkeyPatch, *, address: str, runtime_dir: str):
    """mock 掉外部命令，跑 _set_kde_wallpaper 并记录 _run_args 调用。"""
    from platform_adapters.backends.linux import integration

    _dbus_env(monkeypatch, address=address, runtime_dir=runtime_dir)
    calls: list[list[str]] = []
    monkeypatch.setattr(integration.shutil, "which", lambda name: "/usr/bin/qdbus6" if name == "qdbus6" else None)
    monkeypatch.setattr(
        integration,
        "_run_args",
        lambda cmd, timeout=10: calls.append(list(cmd)) or (0, "SHANGBACKGROUND_KDE_SET_DONE:1", ""),
    )
    monkeypatch.setattr(integration, "_ensure_existing_file", lambda path: "/tmp/wp.png")
    monkeypatch.setattr(integration, "_file_uri", lambda path: f"file://{path}")
    monkeypatch.setattr(integration, "_verify_kde_wallpaper", lambda abs_path, timeout=0.5: (True, abs_path))
    result = integration._set_kde_wallpaper("/tmp/wp.png")
    return result, calls


def test_dbus_precheck_blocks_before_spawning_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # (a) DBUS_SESSION_BUS_ADDRESS 设置 → 正常执行外部命令。
    result, calls = _run_set_with_dbus_env(monkeypatch, address="unix:path=/tmp/bus", runtime_dir="")
    assert result[0] is True
    assert calls and calls[0][0] == "qdbus6"

    # (b) 仅 $XDG_RUNTIME_DIR/bus 存在 → 正常执行外部命令。
    (tmp_path / "bus").write_bytes(b"")
    result, calls = _run_set_with_dbus_env(monkeypatch, address="", runtime_dir=str(tmp_path))
    assert result[0] is True
    assert calls and calls[0][0] == "qdbus6"

    # (c) 两者皆无 → 不 spawn 任何命令，错误可操作且可与其他失败区分。
    result, calls = _run_set_with_dbus_env(monkeypatch, address="", runtime_dir="")
    assert result[0] is False
    assert calls == []
    assert "会话总线" in result[1]
    assert "DBUS_SESSION_BUS_ADDRESS" in result[1]
    assert "command not found" not in result[1]


def test_dbus_precheck_covers_plasma_script_channel(monkeypatch: pytest.MonkeyPatch):
    """_run_plasma_script 自身也前置检查：qdbus 存在时也绝不 spawn。"""
    from platform_adapters.backends.linux import integration

    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(integration.shutil, "which", lambda name: "/usr/bin/qdbus6" if name == "qdbus6" else None)
    monkeypatch.setattr(
        integration,
        "_run_args",
        lambda cmd, timeout=10: (_ for _ in ()).throw(AssertionError("must not spawn qdbus without a session bus")),
    )
    ok, out, detail = integration._run_plasma_script("print('hi');")
    assert ok is False
    assert out == ""
    assert "session bus unavailable" in detail
    assert "DBUS_SESSION_BUS_ADDRESS" in detail


def test_dbus_precheck_failure_is_observable_in_outcome(monkeypatch: pytest.MonkeyPatch):
    """前置检查失败也要记录 outcome——不得保留上一次操作的成功快照误导诊断。"""
    from platform_adapters.backends.linux import integration

    result, _calls = _run_set_with_dbus_env(monkeypatch, address="", runtime_dir="")
    assert result[0] is False
    outcome = integration.last_kde_set_outcome()
    assert outcome["accepted"] is False
    assert outcome["verified"] is False
    assert "会话总线" in str(outcome["detail"])


# ---------------------------------------------------------------------------
# 装配层：bootstrap 两个适配器 + engine 布线
# ---------------------------------------------------------------------------


def test_module_wallpaper_backend_passes_state_through():
    from app.bootstrap import ModuleWallpaperBackend

    module = ModuleType("fake_linux_integration")
    module.get_current_wallpaper_platform = lambda: "/tmp/a.jpg"
    module.configure_fit_mode = lambda mode: None
    module.set_wallpaper_platform = lambda path: None
    module.capture_wallpaper_state = lambda: {
        "schema": 3,
        "kind": "kde-plasma-containments",
        "containments": [{"plugin": "org.kde.image"}],
    }
    module.restore_wallpaper_state = lambda state: (True, "恢复 1 个 containment 的壁纸插件状态")
    backend = ModuleWallpaperBackend(module)
    assert backend.capture_state() == {
        "schema": 3,
        "kind": "kde-plasma-containments",
        "containments": [{"plugin": "org.kde.image"}],
    }
    result = backend.restore_state({"schema": 3})
    assert result.ok is True
    assert "1 个 containment" in result.message


def test_module_wallpaper_backend_without_state_functions_degrades():
    """Windows/macOS 集成模块没有这两个函数：诚实降级而不是抛异常。

    注：适配器按规格无条件提供 capture_state/restore_state 方法（内部
    getattr 探测），因此 isinstance 结构检查恒真——降级的可观测性在
    行为（None / BackendResult(False)）而非结构。
    """
    from app.bootstrap import ModuleWallpaperBackend

    module = ModuleType("fake_windows_integration")
    module.get_current_wallpaper_platform = lambda: "C:\\wall.jpg"
    module.configure_fit_mode = lambda mode: None
    module.set_wallpaper_platform = lambda path: None
    backend = ModuleWallpaperBackend(module)
    assert backend.capture_state() is None
    result = backend.restore_state({"schema": 3})
    assert result.ok is False
    assert "not support" in result.message


def test_callback_wallpaper_backend_normalizes_restore_results():
    from app.bootstrap import CallbackWallpaperBackend
    from app.ports import BackendResult, StatefulWallpaperBackend

    base = dict(get_current=lambda: "", configure_fit_mode=lambda mode: None, set_wallpaper=lambda path: None)

    # tuple 形态 → BackendResult(*前两元素)
    backend = CallbackWallpaperBackend(restore_state=lambda state: (True, "ok-tuple"), **base)
    assert backend.restore_state({}) == BackendResult(True, "ok-tuple")
    # BackendResult 形态 → 原样
    raw = BackendResult(False, "plasma refused")
    backend = CallbackWallpaperBackend(restore_state=lambda state: raw, **base)
    assert backend.restore_state({}) is raw
    # bool 形态 → BackendResult(b, "")
    backend = CallbackWallpaperBackend(restore_state=lambda state: True, **base)
    assert backend.restore_state({}) == BackendResult(True, "")
    # 回调缺失 → BackendResult(False, "not supported")（行为降级；适配器
    # 恒提供方法，结构检查不作为降级依据）。
    backend = CallbackWallpaperBackend(**base)
    result = backend.restore_state({})
    assert result.ok is False
    assert "not supported" in result.message
    # 提供回调后满足可选协议（runtime_checkable 正向检查）。
    backend = CallbackWallpaperBackend(
        capture_state=lambda: {"schema": 3}, restore_state=lambda state: (True, ""), **base
    )
    assert isinstance(backend, StatefulWallpaperBackend)
    assert backend.capture_state() == {"schema": 3}


def test_callback_wallpaper_backend_capture_missing_callback_returns_none():
    from app.bootstrap import CallbackWallpaperBackend

    backend = CallbackWallpaperBackend(
        get_current=lambda: "",
        configure_fit_mode=lambda mode: None,
        set_wallpaper=lambda path: None,
    )
    assert backend.capture_state() is None


def test_stateful_wallpaper_backend_protocol_contract():
    """端口协议存在且 runtime_checkable：契约注释要求的形状。"""
    from app.ports import StatefulWallpaperBackend

    class Full:
        def get_current(self):
            return ""

        def configure_fit_mode(self, mode): ...
        def set_wallpaper(self, path): ...
        def capture_state(self):
            return {"schema": 3}

        def restore_state(self, state):
            from app.ports import BackendResult

            return BackendResult(True, "")

    assert isinstance(Full(), StatefulWallpaperBackend)


def test_engine_wires_state_callbacks_on_linux():
    """engine 顶部 try/except 导入：Linux 解析到真函数，其他平台 None。
    同时钉住 _build_application_services 的布线不被误删。"""
    import sys
    from pathlib import Path as _Path

    from core import engine

    if sys.platform.startswith("linux"):
        assert callable(engine._capture_wallpaper_state)
        assert callable(engine._restore_wallpaper_state)
    else:  # pragma: no cover - Windows/macOS CI 矩阵
        assert engine._capture_wallpaper_state is None
        assert engine._restore_wallpaper_state is None

    source = (_Path(__file__).resolve().parents[1] / "src" / "core" / "engine.py").read_text(encoding="utf-8")
    assert "capture_state=(lambda: _capture_wallpaper_state()) if _capture_wallpaper_state else None," in source
    assert (
        "restore_state=(lambda state: _restore_wallpaper_state(state)) if _restore_wallpaper_state else None," in source
    )
