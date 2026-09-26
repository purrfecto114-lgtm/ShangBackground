"""KDE 壁纸恢复范围降级声明（v1.6.2 审查必须修复项 2）。

REVIEW_REPORT_V1.6.1 指出：``WallpaperBackend`` 只有 get_current/set_wallpaper
接口，``SessionWallpaperService``（schema=2）仅保存一个本地壁纸路径；KDE 设置
脚本又无条件写 ``org.kde.image``——用户原本使用 slideshow/color/第三方插件时，
退出恢复会丢失插件和配置。

本批次选择报告给出的"明确降级范围"路线（另一路线 schema=3 插件级恢复
见 docs/KDE_SUPPORT_PLAN.md 任务 2）：
- integration 层如实上报每个 containment 的 wallpaperPlugin；
- 诊断（doctor）对超范围插件显式 WARN + hint；
- README/CHANGELOG 声明恢复范围限定为"本地静态图片"。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# integration 层：kde_wallpaper_restore_scope()
# ---------------------------------------------------------------------------


def _scope(monkeypatch: pytest.MonkeyPatch, *, kde: bool, script_ok: bool = True, out: str = ""):
    from platform_adapters.backends.linux import integration

    monkeypatch.setattr(integration, "_is_kde_session", lambda: kde)
    monkeypatch.setattr(
        integration,
        "_run_plasma_script",
        lambda _script, timeout=8, allow_dbus_send=False: (script_ok, out, "" if script_ok else "qdbus not found"),
    )
    return integration.kde_wallpaper_restore_scope()


def test_scope_not_applicable_outside_kde(monkeypatch: pytest.MonkeyPatch):
    result = _scope(monkeypatch, kde=False)
    assert result["applicable"] is False


def test_scope_all_image_plugins_are_restorable(monkeypatch: pytest.MonkeyPatch):
    result = _scope(monkeypatch, kde=True, out="PLUGIN:org.kde.image\nPLUGIN:org.kde.image\n")
    assert result["applicable"] is True
    assert result["reachable"] is True
    assert result["restorable"] is True
    assert result["plugins"] == ["org.kde.image"]


def test_scope_slideshow_plugin_reported_as_out_of_range(monkeypatch: pytest.MonkeyPatch):
    result = _scope(monkeypatch, kde=True, out="PLUGIN:org.kde.image\nPLUGIN:org.kde.slideshow\n")
    assert result["restorable"] is False
    assert "org.kde.slideshow" in result["plugins"]
    assert "org.kde.slideshow" in result["detail"]


def test_scope_color_plugin_reported_as_out_of_range(monkeypatch: pytest.MonkeyPatch):
    result = _scope(monkeypatch, kde=True, out="PLUGIN:org.kde.color\n")
    assert result["restorable"] is False
    assert result["plugins"] == ["org.kde.color"]


def test_scope_unreachable_plasma_is_reported_not_crashed(monkeypatch: pytest.MonkeyPatch):
    result = _scope(monkeypatch, kde=True, script_ok=False)
    assert result["applicable"] is True
    assert result["reachable"] is False
    assert "qdbus not found" in result["detail"]


def test_scope_empty_plugin_list_is_not_restorable(monkeypatch: pytest.MonkeyPatch):
    """无任何 containment 上报插件：不能凭空宣称可恢复。"""
    result = _scope(monkeypatch, kde=True, out="")
    assert result["reachable"] is True
    assert result["restorable"] is False


# ---------------------------------------------------------------------------
# 诊断层：doctor 集成
# ---------------------------------------------------------------------------


def _doctor_checks(monkeypatch: pytest.MonkeyPatch, scope_result: dict):
    """在 Linux 分支跑 collect_diagnostics 并提取 kde-wallpaper-restore 检查。"""
    import app.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "PLATFORM_ID", "linux", raising=False)
    monkeypatch.setattr(
        "platform_adapters.session.kde_wallpaper_restore_scope",
        lambda: scope_result,
    )
    monkeypatch.setattr("platform_adapters.session.detect_session_type", lambda env=None: "wayland")
    report = diagnostics.collect_diagnostics()
    return [c for c in report.checks if c.name == "kde-wallpaper-restore"]


def test_doctor_passes_when_all_plugins_restorable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    checks = _doctor_checks(
        monkeypatch,
        {"applicable": True, "reachable": True, "plugins": ["org.kde.image"], "restorable": True, "detail": "wallpaper plugins: org.kde.image"},
    )
    assert len(checks) == 1
    assert checks[0].status == "pass"
    assert "本地静态图片" in checks[0].detail


def test_doctor_warns_when_plugin_out_of_range(monkeypatch: pytest.MonkeyPatch):
    checks = _doctor_checks(
        monkeypatch,
        {"applicable": True, "reachable": True, "plugins": ["org.kde.slideshow"], "restorable": False, "detail": "wallpaper plugins: org.kde.slideshow"},
    )
    assert checks[0].status == "warn"
    assert "超出恢复范围" in checks[0].detail
    assert checks[0].hint  # 必须给出可操作提示
    assert "不会被还原" in checks[0].hint


def test_doctor_warns_when_plasma_unreachable(monkeypatch: pytest.MonkeyPatch):
    checks = _doctor_checks(
        monkeypatch,
        {"applicable": True, "reachable": False, "plugins": [], "restorable": False, "detail": "qdbus not found"},
    )
    assert checks[0].status == "warn"
    assert "无法读取" in checks[0].detail


def test_doctor_skips_check_outside_kde(monkeypatch: pytest.MonkeyPatch):
    checks = _doctor_checks(
        monkeypatch,
        {"applicable": False, "reachable": False, "plugins": [], "restorable": False, "detail": "not a KDE session"},
    )
    assert checks == []


# ---------------------------------------------------------------------------
# v1.6.2 审查建议项 1：accepted 与 verified 分离可观测
# ---------------------------------------------------------------------------


def _run_set(monkeypatch: pytest.MonkeyPatch, *, rc: int, readback_same: bool):
    """mock 掉外部命令与读回，跑 _set_kde_wallpaper 并返回 (结果, outcome)。"""
    from platform_adapters.backends.linux import integration

    monkeypatch.setattr(integration.shutil, "which", lambda name: "/usr/bin/plasma-apply-wallpaperimage" if name == "plasma-apply-wallpaperimage" else None)
    monkeypatch.setattr(integration, "_run_args", lambda cmd, timeout=8: (rc, "", ""))
    monkeypatch.setattr(integration, "_ensure_existing_file", lambda path: "/tmp/wp.png")
    monkeypatch.setattr(
        integration,
        "_verify_kde_wallpaper",
        lambda abs_path, timeout=0.5: ((True, abs_path) if readback_same else (False, "")),
    )
    result = integration._set_kde_wallpaper("/tmp/wp.png")
    return result, integration.last_kde_set_outcome()


def test_set_outcome_separates_accepted_from_verified(monkeypatch: pytest.MonkeyPatch):
    """Plasma 6 已知行为：rc=0（接受）但读回空（未确认）——两种状态必须分开可查。"""
    result, outcome = _run_set(monkeypatch, rc=0, readback_same=False)
    assert result[0] is True
    assert outcome["accepted"] is True
    assert outcome["verified"] is False
    assert outcome["method"] == "plasma-apply-wallpaperimage"
    assert "unconfirmed" in str(outcome["detail"])


def test_set_outcome_records_full_success(monkeypatch: pytest.MonkeyPatch):
    result, outcome = _run_set(monkeypatch, rc=0, readback_same=True)
    assert result[0] is True
    assert outcome["accepted"] is True
    assert outcome["verified"] is True
    assert "confirmed" in str(outcome["detail"])


def test_set_outcome_records_rejection(monkeypatch: pytest.MonkeyPatch):
    result, outcome = _run_set(monkeypatch, rc=1, readback_same=False)
    assert result[0] is False
    assert outcome["accepted"] is False
    assert outcome["verified"] is False


def test_last_kde_set_outcome_returns_copy(monkeypatch: pytest.MonkeyPatch):
    """返回副本：外部修改不得污染模块内部状态。"""
    from platform_adapters.backends.linux import integration

    snapshot = integration.last_kde_set_outcome()
    snapshot["accepted"] = "tampered"
    assert integration.last_kde_set_outcome() != snapshot or integration.last_kde_set_outcome()["accepted"] != "tampered"


# ---------------------------------------------------------------------------
# session 门面：非 Linux 惰性默认 + Linux 惰性转发
# ---------------------------------------------------------------------------


def test_session_facade_exposes_scope_safely():
    """门面函数存在于两种平台实现中，且不抛异常。"""
    from platform_adapters import session as session_facade

    result = session_facade.kde_wallpaper_restore_scope()
    assert isinstance(result, dict)
    assert "applicable" in result
    # 沙箱/CI 的 Linux runner 上可能并非 KDE 会话：只要求如实上报。
    assert result["applicable"] in (True, False)


def test_session_facade_scope_addition_keeps_layering_guard():
    """新增门面函数不得破坏 test_layering 的 app.* 导入禁令：AST 级复核
    （与 test_layering.py::test_session_facade_does_not_import_app_config
    同源同法，防止本文件的新增绕过守护）。"""
    import ast

    source = (Path(__file__).resolve().parents[1] / "src" / "platform_adapters" / "session.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.")
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("app.")
