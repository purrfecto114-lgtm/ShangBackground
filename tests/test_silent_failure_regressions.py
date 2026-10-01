"""v1.6.0 审计（静默失败专项）修复的回归钉。

覆盖合并审计报告的 P0/P1 修复：
- P0-1: IPC 壁纸命令丢弃返回值 → 失败抛 RuntimeError 走既有通知路径
- P0-1b: _notify_ipc_failure 关键词白名单移除（真实失败消息不再被过滤）
- P0-2: configure_fit_mode 显式 bool 契约；WallpaperService 消费 False
- P0-3: set_fit_mode 设置 last_operation_error 并返回 False（假成功修复）
- P1-1: is_context_menu_synced 要求本会话已成功通知 Explorer（破除自锁）
- P1-3: Windows multi_monitor_static 能力口径与 NULL-monitor 实现一致
- P1-4: IPC 转发全链失败时写入死信队列，下次启动播报
- P1-5: 安装器在安装时捕获、卸载时回滚 WallpaperStyle/TileWallpaper
"""

from __future__ import annotations

import argparse
from pathlib import Path
from threading import RLock

import pytest

from app.wallpaper_service import WallpaperService


# ---------------------------------------------------------------------------
# P0-1: 右键/IPC 路径必须与 GUI 按钮一样反馈失败
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "func_name", "fallback"),
    [
        ("previous", "previous_wallpaper", "切换到上一张壁纸失败"),
        ("next", "next_wallpaper", "切换到下一张壁纸失败"),
        ("random", "random_wallpaper", "随机切换壁纸失败"),
    ],
)
def test_ipc_wallpaper_command_failure_raises_with_reason(
    monkeypatch: pytest.MonkeyPatch, command: str, func_name: str, fallback: str
):
    from core import engine

    monkeypatch.setattr(engine, func_name, lambda: False)
    monkeypatch.setattr(engine, "last_operation_error", "设置壁纸失败（未知错误）")
    with pytest.raises(RuntimeError) as excinfo:
        engine._execute_ipc_wallpaper_command(command)
    assert "设置壁纸失败（未知错误）" in str(excinfo.value)


def test_ipc_wallpaper_command_failure_without_error_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    from core import engine

    monkeypatch.setattr(engine, "previous_wallpaper", lambda: False)
    monkeypatch.setattr(engine, "last_operation_error", "")
    with pytest.raises(RuntimeError, match="切换到上一张壁纸失败"):
        engine._execute_ipc_wallpaper_command("previous")


@pytest.mark.parametrize(
    ("command", "func_name"),
    [("previous", "previous_wallpaper"), ("next", "next_wallpaper"), ("random", "random_wallpaper")],
)
def test_ipc_wallpaper_command_success_returns_true(monkeypatch: pytest.MonkeyPatch, command: str, func_name: str):
    from core import engine

    monkeypatch.setattr(engine, func_name, lambda: True)
    assert engine._execute_ipc_wallpaper_command(command) is True


def test_ipc_wallpaper_command_raise_surfaces_via_existing_notify_path(
    monkeypatch: pytest.MonkeyPatch,
):
    """失败必须能到达 _notify_ipc_failure（worker 的 except 通道）。"""
    from core import engine

    monkeypatch.setattr(engine, "next_wallpaper", lambda: False)
    monkeypatch.setattr(engine, "last_operation_error", "设置壁纸失败（未知错误）")
    notified: list[str] = []
    monkeypatch.setattr(engine, "_notify_ipc_failure", lambda cmd, err: notified.append(err))
    try:
        engine._execute_ipc_wallpaper_command("next")
    except RuntimeError as exc:
        # 模拟 queue_ipc_wallpaper_command worker 的 except 分支
        engine._notify_ipc_failure("next", str(exc))
    assert notified == ["设置壁纸失败（未知错误）"]


# ---------------------------------------------------------------------------
# P0-1b: _notify_ipc_failure 不再用关键词白名单过滤
# ---------------------------------------------------------------------------


def test_notify_ipc_failure_passes_non_keyword_messages(monkeypatch: pytest.MonkeyPatch):
    from core import engine

    scheduled: list = []

    class _FakeRoot:
        def after(self, _ms, callback):
            scheduled.append(callback)

    shown: list[str] = []
    monkeypatch.setattr(engine, "root", _FakeRoot())
    monkeypatch.setattr(engine, "_show_tray_notification", lambda msg: shown.append(msg))

    # 旧白名单（没有/无/不存在/未找到/empty/not found）一条都不匹配这条消息
    engine._notify_ipc_failure("previous", "设置壁纸失败（未知错误）")
    assert len(scheduled) == 1
    assert shown == []  # 必须经 GUI 线程调度，而不是 worker 线程直弹
    scheduled[0]()
    assert shown == ["设置壁纸失败（未知错误）"]


def test_notify_ipc_failure_without_gui_channel_logs(monkeypatch: pytest.MonkeyPatch):
    from core import engine

    monkeypatch.setattr(engine, "root", None)
    logged: list[str] = []
    monkeypatch.setattr(engine, "log", lambda msg: logged.append(msg))
    engine._notify_ipc_failure("previous", "停止动态壁纸失败")
    assert any("停止动态壁纸失败" in msg for msg in logged)


# ---------------------------------------------------------------------------
# P0-2: configure_fit_mode 显式 bool 契约
# ---------------------------------------------------------------------------


class _FakeWinregOk:
    HKEY_CURRENT_USER = object()
    KEY_WRITE = 1
    REG_SZ = 1

    @staticmethod
    def OpenKey(*_args):
        return "key"

    @staticmethod
    def SetValueEx(_key, _name, *_args):
        return None

    @staticmethod
    def CloseKey(_key):
        return None


class _FakeWinregFailing(_FakeWinregOk):
    @staticmethod
    def SetValueEx(_key, _name, *_args):
        raise OSError("simulated registry failure")


def test_configure_fit_mode_registry_success_returns_true(monkeypatch: pytest.MonkeyPatch):
    from platform_adapters.backends.windows import integration

    monkeypatch.setattr(integration, "_set_position_via_com", lambda _mode: False)
    assert integration.configure_fit_mode("填充", _FakeWinregOk, None) is True


def test_configure_fit_mode_all_paths_failed_returns_false(monkeypatch: pytest.MonkeyPatch):
    from platform_adapters.backends.windows import integration

    monkeypatch.setattr(integration, "_set_position_via_com", lambda _mode: False)
    errors: list[str] = []
    assert integration.configure_fit_mode("填充", _FakeWinregFailing, errors.append) is False
    assert errors and "simulated registry failure" in errors[-1]


def test_configure_fit_mode_without_winreg_returns_false(monkeypatch: pytest.MonkeyPatch):
    from platform_adapters.backends.windows import integration

    monkeypatch.setattr(integration, "_set_position_via_com", lambda _mode: False)
    errors: list[str] = []
    assert integration.configure_fit_mode("填充", None, errors.append) is False
    assert errors and "注册表模块不可用" in errors[0]


def test_configure_fit_mode_com_success_skips_registry(monkeypatch: pytest.MonkeyPatch):
    from platform_adapters.backends.windows import integration

    class _BoomWinreg(_FakeWinregOk):
        @staticmethod
        def OpenKey(*_args):
            raise AssertionError("registry must not be touched when COM succeeds")

    monkeypatch.setattr(integration, "_set_position_via_com", lambda _mode: True)
    assert integration.configure_fit_mode("填充", _BoomWinreg, None) is True


# ---------------------------------------------------------------------------
# P0-2: WallpaperService 消费后端的显式 False（旧死代码分支激活）
# ---------------------------------------------------------------------------


class _FitBackend:
    def __init__(self, fit_result, current: str = "") -> None:
        self._fit_result = fit_result
        self.current = current
        self.calls: list[str] = []

    def get_current(self) -> str:
        self.calls.append("get")
        return self.current

    def configure_fit_mode(self, mode: str):
        self.calls.append("fit")
        return self._fit_result

    def set_wallpaper(self, path: str) -> None:
        self.calls.append("set")
        self.current = path


class _Library:
    def remember_wallpaper(self, path: str, **_kwargs) -> bool:
        return True

    def remember_current_without_reordering(self, path: str, **_kwargs) -> bool:
        return True


def _make_service(backend) -> tuple[WallpaperService, list[str]]:
    errors: list[str] = []
    service = WallpaperService(
        backend=backend,
        config=lambda: {"fit_mode": "填充"},
        library=_Library(),
        operation_lock=RLock(),
        stop_dynamic=lambda: False,
        set_error=errors.append,
        normalize_fit_mode=lambda value: value,
    )
    return service, errors


def test_wallpaper_service_fails_when_backend_fit_mode_returns_false(tmp_path: Path):
    image = tmp_path / "wall.jpg"
    image.write_bytes(b"img")
    service, errors = _make_service(_FitBackend(False))

    assert service.apply(str(image), "test") is False
    assert errors and "设置适应模式失败" in errors[0]
    assert "均未生效" in errors[0]


def test_wallpaper_service_tolerates_legacy_none_backend(tmp_path: Path):
    """None（其余平台/旧后端契约）不视为失败——只钉显式 False。"""
    image = tmp_path / "wall.jpg"
    image.write_bytes(b"img")
    backend = _FitBackend(None)
    service, errors = _make_service(backend)

    assert service.apply(str(image), "test") is True
    # apply 开始时会清空上一次的 last_operation_error（set_error("")）
    assert [entry for entry in errors if entry] == []
    assert backend.calls == ["get", "fit", "set"]


def test_bootstrap_module_adapter_propagates_fit_mode_result():
    from app.bootstrap import ModuleWallpaperBackend

    class _Module:
        @staticmethod
        def get_current_wallpaper_platform():
            return ""

        @staticmethod
        def configure_fit_mode(mode, winreg_module=None, log=None):
            return mode == "填充"

        @staticmethod
        def set_wallpaper_platform(_path):
            return None

    adapter = ModuleWallpaperBackend(_Module, fit_args=(None, None))
    assert adapter.configure_fit_mode("填充") is True
    assert adapter.configure_fit_mode("适应") is False


# ---------------------------------------------------------------------------
# P0-3: set_fit_mode 假成功修复
# ---------------------------------------------------------------------------


def test_set_fit_mode_failure_sets_error_and_returns_false(monkeypatch: pytest.MonkeyPatch):
    from core import engine

    monkeypatch.setattr(engine, "configure_fit_mode", lambda *_args: False)
    monkeypatch.setattr(engine, "last_operation_error", "")
    assert engine.set_fit_mode("填充") is False
    assert engine.last_operation_error
    assert "设置适应模式失败" in engine.last_operation_error


def test_set_fit_mode_exception_sets_error_and_returns_false(monkeypatch: pytest.MonkeyPatch):
    from core import engine

    def _boom(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "configure_fit_mode", _boom)
    monkeypatch.setattr(engine, "last_operation_error", "")
    assert engine.set_fit_mode("填充") is False
    assert "boom" in engine.last_operation_error


def test_set_fit_mode_reapply_failure_propagates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from core import engine

    image = tmp_path / "wall.jpg"
    image.write_bytes(b"img")
    monkeypatch.setattr(engine, "configure_fit_mode", lambda *_args: True)
    monkeypatch.setitem(engine.config, "current_wallpaper", str(image))
    monkeypatch.setattr(engine, "set_wallpaper_direct", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(engine, "last_operation_error", "")
    assert engine.set_fit_mode("填充") is False


def test_set_fit_mode_success_returns_true(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from core import engine

    image = tmp_path / "wall.jpg"
    image.write_bytes(b"img")
    monkeypatch.setattr(engine, "configure_fit_mode", lambda *_args: True)
    monkeypatch.setitem(engine.config, "current_wallpaper", str(image))
    monkeypatch.setattr(engine, "set_wallpaper_direct", lambda *_args, **_kwargs: True)
    assert engine.set_fit_mode("填充") is True


# ---------------------------------------------------------------------------
# P1-1: 右键菜单"永久不同步"自锁修复
# ---------------------------------------------------------------------------


def test_context_menu_not_synced_until_explorer_notified_this_session(
    monkeypatch: pytest.MonkeyPatch,
):
    from core import engine

    monkeypatch.setattr(engine, "IS_WINDOWS", True)
    monkeypatch.setattr(engine, "winreg", object())
    monkeypatch.setattr(engine, "_desired_context_menu_entries", lambda: [])
    monkeypatch.setattr(engine, "_stale_context_menu_paths", lambda: [])

    # 注册表视角完全一致（entries 为空 = 无需任何写入），但本会话从未
    # 成功通知过 Explorer → 必须视为未同步，触发一次幂等重注册+重通知。
    monkeypatch.setattr(engine, "_shell_association_notified", False)
    assert engine.is_context_menu_synced() is False

    monkeypatch.setattr(engine, "_shell_association_notified", True)
    assert engine.is_context_menu_synced() is True


def test_shell_association_notify_success_sets_session_flag(monkeypatch: pytest.MonkeyPatch):
    from core import engine

    calls: list[tuple] = []

    class _FakeShell32:
        def SHChangeNotify(self, *args):
            calls.append(args)

    class _FakeWindll:
        shell32 = _FakeShell32()

    class _FakeCtypes:
        windll = _FakeWindll()

    monkeypatch.setattr(engine, "IS_WINDOWS", True)
    monkeypatch.setattr(engine, "ctypes", _FakeCtypes())
    monkeypatch.setattr(engine, "_shell_association_notified", False)

    engine._notify_shell_association_changed()
    assert engine._shell_association_notified is True
    assert len(calls) == 1


def test_shell_association_notify_failure_keeps_flag_false(monkeypatch: pytest.MonkeyPatch):
    from core import engine

    class _FakeShell32:
        def SHChangeNotify(self, *_args):
            raise OSError("explorer gone")

    class _FakeWindll:
        shell32 = _FakeShell32()

    class _FakeCtypes:
        windll = _FakeWindll()

    monkeypatch.setattr(engine, "IS_WINDOWS", True)
    monkeypatch.setattr(engine, "ctypes", _FakeCtypes())
    monkeypatch.setattr(engine, "_shell_association_notified", False)
    logged: list[str] = []
    monkeypatch.setattr(engine, "log", lambda msg: logged.append(msg))

    engine._notify_shell_association_changed()
    assert engine._shell_association_notified is False
    assert any("刷新 Windows Shell 关联缓存失败" in msg for msg in logged)


# ---------------------------------------------------------------------------
# P1-3: Windows 多显示器能力口径
# ---------------------------------------------------------------------------


def test_windows_multi_monitor_capability_describes_null_monitor_reality():
    from platform_adapters.backends.windows import capabilities

    cap = capabilities.probe_capabilities()["multi_monitor_static"]
    assert cap["state"] == "supported"
    assert "all monitors set in one call" in cap["backend"]
    assert "monitorID=NULL" in cap["limitations"]
    assert "per-monitor selection is not wired" in cap["limitations"]


# ---------------------------------------------------------------------------
# P1-4: 未送达 IPC 动作的死信队列
# ---------------------------------------------------------------------------


def test_missed_command_journal_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from core import local_ipc

    journal = tmp_path / "missed_ipc_actions.json"
    monkeypatch.setattr(local_ipc, "MISSED_COMMANDS_FILE", str(journal))

    assert local_ipc.record_missed_command("previous") is True
    assert local_ipc.record_missed_command("set_wallpaper", "D:/壁纸.jpg") is True

    entries = local_ipc.drain_missed_commands()
    assert [entry["command"] for entry in entries] == ["previous", "set_wallpaper"]
    assert entries[1]["payload"] == "D:/壁纸.jpg"
    assert entries[0]["time"]
    # drain 之后清空且删除文件
    assert local_ipc.drain_missed_commands() == []
    assert not journal.exists()


def test_missed_command_journal_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from core import local_ipc

    monkeypatch.setattr(local_ipc, "MISSED_COMMANDS_FILE", str(tmp_path / "missed_ipc_actions.json"))
    for index in range(local_ipc.MISSED_COMMANDS_LIMIT + 5):
        local_ipc.record_missed_command("previous", index)
    entries = local_ipc.drain_missed_commands()
    assert len(entries) == local_ipc.MISSED_COMMANDS_LIMIT
    assert entries[-1]["payload"] == local_ipc.MISSED_COMMANDS_LIMIT + 4


def test_missed_command_journal_survives_corrupt_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from core import local_ipc

    journal = tmp_path / "missed_ipc_actions.json"
    journal.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(local_ipc, "MISSED_COMMANDS_FILE", str(journal))

    assert local_ipc.record_missed_command("next") is True
    entries = local_ipc.drain_missed_commands()
    assert [entry["command"] for entry in entries] == ["next"]


def test_dispatch_failure_records_dead_letter(monkeypatch: pytest.MonkeyPatch):
    from app import support
    from core import local_ipc

    recorded: list[tuple] = []
    monkeypatch.setattr(
        local_ipc, "record_missed_command", lambda command, payload=None: recorded.append((command, payload)) or True
    )
    monkeypatch.setattr(support, "_context_command_from_args", lambda _args: "previous")
    monkeypatch.setattr(support, "_context_payload_from_args", lambda _args: None)
    monkeypatch.setattr(support.single_instance, "read_identity", lambda: {})
    monkeypatch.setattr(local_ipc, "send_command", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(support.core, "IS_WINDOWS", False)
    logged: list[str] = []
    monkeypatch.setattr(support.core, "log", lambda msg: logged.append(msg))

    args = argparse.Namespace(
        previous=True,
        next=False,
        random=False,
        set_wallpaper=None,
        jump_to_wallpaper=False,
        quit=False,
        show=False,
        wait_for_exit=False,
        context_menu_dispatched_child=False,
    )
    assert support._dispatch_action_to_existing_instance(args) is False
    assert recorded == [("previous", None)]
    assert any("死信队列" in msg for msg in logged)


def test_dispatch_success_skips_dead_letter(monkeypatch: pytest.MonkeyPatch):
    from app import support
    from core import local_ipc

    recorded: list[tuple] = []
    monkeypatch.setattr(
        local_ipc, "record_missed_command", lambda command, payload=None: recorded.append((command, payload)) or True
    )
    monkeypatch.setattr(support, "_context_command_from_args", lambda _args: "previous")
    monkeypatch.setattr(support, "_context_payload_from_args", lambda _args: None)
    monkeypatch.setattr(local_ipc, "send_command", lambda *_args, **_kwargs: True)

    args = argparse.Namespace(
        previous=True,
        next=False,
        random=False,
        set_wallpaper=None,
        jump_to_wallpaper=False,
        quit=False,
        show=False,
        wait_for_exit=False,
        context_menu_dispatched_child=False,
    )
    assert support._dispatch_action_to_existing_instance(args) is True
    assert recorded == []


def test_surface_missed_ipc_actions_notifies_on_gui_thread(monkeypatch: pytest.MonkeyPatch):
    from core import engine
    from core import local_ipc

    monkeypatch.setattr(
        local_ipc,
        "drain_missed_commands",
        lambda: [
            {"command": "previous", "payload": None, "time": "2026-01-01T00:00:00"},
            {"command": "set_wallpaper", "payload": "D:/壁纸.jpg", "time": "2026-01-01T00:00:01"},
        ],
    )
    shown: list[str] = []
    monkeypatch.setattr(engine, "_show_tray_notification", lambda msg: shown.append(msg))
    logged: list[str] = []
    monkeypatch.setattr(engine, "log", lambda msg: logged.append(msg))

    assert engine.surface_missed_ipc_actions() == 2
    assert len(shown) == 1
    assert "2" in shown[0] and "桌面动作" in shown[0]
    assert any("previous" in msg for msg in logged)
    assert any("D:/壁纸.jpg" in msg for msg in logged)


def test_surface_missed_ipc_actions_silent_when_empty(monkeypatch: pytest.MonkeyPatch):
    from core import engine
    from core import local_ipc

    monkeypatch.setattr(local_ipc, "drain_missed_commands", lambda: [])
    shown: list[str] = []
    monkeypatch.setattr(engine, "_show_tray_notification", lambda msg: shown.append(msg))

    assert engine.surface_missed_ipc_actions() == 0
    assert shown == []


# ---------------------------------------------------------------------------
# P1-5: 安装器的适应方式捕获/回滚（源码级钉：CI 无 ISCC，无法编译验证）
# ---------------------------------------------------------------------------


def test_installer_captures_and_rolls_back_wallpaper_style():
    text = Path("packaging/windows/shangbackground.iss").read_text(encoding="utf-8")
    # 安装时捕获（ssInstall 早于任何文件复制）
    assert "procedure CaptureOriginalWallpaperStyle();" in text
    assert "if CurStep = ssInstall then" in text
    # 卸载时消费：仅在应用优雅退出未确认时回写原始值
    assert "procedure ConsumeOriginalWallpaperStyle(const ApplyValues: Boolean);" in text
    assert "ConsumeOriginalWallpaperStyle(not GracefulQuitConfirmed)" in text
    assert r"Control Panel\Desktop" in text
    assert "WallpaperStyle" in text and "TileWallpaper" in text


# ---------------------------------------------------------------------------
# P2 快赢项：VLC 静音保留音量（REVIEW 3.2 同源修复）
# ---------------------------------------------------------------------------


def test_windows_vlc_muting_preserves_configured_volume(monkeypatch: pytest.MonkeyPatch):
    from platform_adapters.backends.windows import video

    monkeypatch.setattr(video, "_candidate_paths", lambda *_args: ["/usr/bin/vlc"])
    result = video._vlc_command("clip.mp4", True, 61)
    assert result is not None
    _name, command, _ipc = result
    assert "--no-audio" in command
    # 61 * 256 / 100 = 156.16 → 156（VLC 0-1024 刻度，256 == 100%）
    assert "--volume=156" in command
    assert "--volume=0" not in command


def test_windows_vlc_unmuted_maps_volume_scale(monkeypatch: pytest.MonkeyPatch):
    from platform_adapters.backends.windows import video

    monkeypatch.setattr(video, "_candidate_paths", lambda *_args: ["/usr/bin/vlc"])
    _name, command, _ipc = video._vlc_command("clip.mp4", False, 50)
    assert "--volume=128" in command  # 50% → 128（256 的一半）
    assert "--no-audio" not in command


def test_cross_volume_alias_copy_logs_observability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """P1-2 回归钉（验收 S2）：跨卷整文件拷贝必须留下可诊断的 WARNING。

    变异实验证明删掉该日志不会导致任何测试失败（M10 存活）——此钉堵住
    "跨卷大图切换卡顿不可诊断"的静默回归通道。
    """
    import logging as _logging

    from platform_adapters.backends.windows import integration

    original = tmp_path / "壁纸 图.jpg"
    original.write_bytes(b"x" * 128)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(integration, "_unicode_alias_dir", lambda: str(cache_dir))

    def _no_hard_link(*_args, **_kwargs):
        raise OSError("cross-volume link")

    monkeypatch.setattr(integration.os, "link", _no_hard_link)

    with caplog.at_level(_logging.WARNING, logger="platform_adapters.windows_integration"):
        alias = integration._wallpaper_api_path(str(original))

    assert alias != str(original)
    assert alias.startswith(str(cache_dir))
    assert "跨卷拷贝" in caplog.text
    assert "128" in caplog.text  # 字节数必须出现在日志里
    assert "壁纸 图.jpg" in caplog.text
