"""doctor "Wayland video embedding" 口径统一（v1.6.3，KDE_SUPPORT_PLAN 任务 5）。

v1.6.2 的问题：diagnostics.py 自行以 mpvpaper 命令存在性判定
"Wayland video embedding"——KDE Wayland 装了 mpvpaper 的用户看到 PASS，
而能力口径（capabilities.py）是 KDE 不默认 runtime_ready（仅
SHANGBACKGROUND_ALLOW_MPVPAPER=1 实验放行）；GNOME Wayland 同样
unsupported 却 PASS。诊断与能力声明分裂。

v1.6.3 修复：doctor 不再重判，经 session 门面的
linux_video_wallpaper_capability() 消费 probe_capabilities() 的
"video_wallpaper" 子表渲染。本文件钉死以下契约：
1. runtime_ready → pass（detail 携带 backend 串）；
2. 其余 → warn（可选项不 fail），hint 按 state/backend 区分：
   KDE 有 mpvpaper（实验开关指引）/ KDE 无后端 / wlroots 未装
   mpvpaper（安装指引）/ GNOME 等其它（无桌面层）；
3. 空 dict（探测异常防御）→ warn "capability probe unavailable"；
4. x11 会话不产生本检查（回归：x11 分支保持 xwinwrap 口径）；
5. 门面透传：返回 video_wallpaper 子表的副本，形状含
   state/runtime_ready 键。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# probe_capabilities() "video_wallpaper" 子表的四种真实形态（与
# capabilities.py wayland 分支逐字段对齐，避免测试凭空编造口径）。
_WLROOTS_READY = {
    "state": "best_effort",
    "runtime_ready": True,
    "backend": "mpvpaper layer-shell (wlroots compositors)",
    "limitations": "X11 uses third-party embedding; Wayland requires a compositor-specific desktop-layer protocol.",
}
_WLROOTS_NO_MPVPAPER = {
    "state": "best_effort",
    "runtime_ready": False,
    "backend": "mpvpaper layer-shell (wlroots compositors)",
    "limitations": "X11 uses third-party embedding; Wayland requires a compositor-specific desktop-layer protocol.",
}
_KDE_MPVPAPER = {
    "state": "best_effort",
    "runtime_ready": False,
    "backend": "mpvpaper (experimental, untested on KWin; opt-in via SHANGBACKGROUND_ALLOW_MPVPAPER=1)",
    "limitations": (
        "X11 uses third-party embedding; Wayland requires a compositor-specific desktop-layer protocol."
        " KDE/KWin sessions are NOT auto-marked ready: mpvpaper targets wlroots"
        " compositors and KWin compatibility is untested; set"
        " SHANGBACKGROUND_ALLOW_MPVPAPER=1 to opt in explicitly."
    ),
}
_KDE_NO_MPVPAPER = {
    "state": "unsupported",
    "runtime_ready": False,
    "backend": "no supported KDE/KWin video wallpaper backend",
    "limitations": (
        "X11 uses third-party embedding; Wayland requires a compositor-specific desktop-layer protocol."
        " KDE/KWin sessions are NOT auto-marked ready."
    ),
}
_GNOME_UNSUPPORTED = {
    "state": "unsupported",
    "runtime_ready": False,
    "backend": "no compatible Wayland desktop-layer backend",
    "limitations": "X11 uses third-party embedding; Wayland requires a compositor-specific desktop-layer protocol.",
}


def _doctor_checks(monkeypatch: pytest.MonkeyPatch, capability: dict, *, session: str = "wayland"):
    """在 Linux 分支跑 collect_diagnostics 并提取 "Wayland video embedding" 检查。

    对齐 test_kde_restore_scope.py 的 _doctor_checks 模式：monkeypatch
    门面函数 + detect_session_type（函数级 import 在调用时取属性，故
    门面 monkeypatch 生效）；PLATFORM_ID 强制 linux 以覆盖非 Linux CI 矩阵。
    """
    import app.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "PLATFORM_ID", "linux", raising=False)
    monkeypatch.setattr("platform_adapters.session.detect_session_type", lambda env=None: session)
    monkeypatch.setattr(
        "platform_adapters.session.linux_video_wallpaper_capability",
        lambda: capability,
    )
    report = diagnostics.collect_diagnostics()
    return [c for c in report.checks if c.name == "Wayland video embedding"]


# ---------------------------------------------------------------------------
# 诊断层：状态映射
# ---------------------------------------------------------------------------


def test_doctor_passes_when_wlroots_runtime_ready(monkeypatch: pytest.MonkeyPatch):
    """wlroots 会话 + mpvpaper：能力口径 ready，doctor 同步 pass。"""
    checks = _doctor_checks(monkeypatch, _WLROOTS_READY)
    assert len(checks) == 1
    assert checks[0].status == "pass"
    assert "mpvpaper layer-shell" in checks[0].detail


def test_doctor_warns_on_kde_best_effort_with_mpvpaper(monkeypatch: pytest.MonkeyPatch):
    """KDE Wayland 有 mpvpaper：不得 pass（与 v1.6.2 能力口径收敛），指向实验开关。"""
    checks = _doctor_checks(monkeypatch, _KDE_MPVPAPER)
    assert checks[0].status == "warn"
    assert "untested" in checks[0].detail  # detail 携带能力判定的 backend 串
    assert "SHANGBACKGROUND_ALLOW_MPVPAPER" in checks[0].hint


def test_doctor_warns_on_kde_without_mpvpaper(monkeypatch: pytest.MonkeyPatch):
    """KDE Wayland 无 mpvpaper：unsupported，如实告知无受支持后端。"""
    checks = _doctor_checks(monkeypatch, _KDE_NO_MPVPAPER)
    assert checks[0].status == "warn"
    assert "无受支持" in checks[0].hint


def test_doctor_warns_on_wlroots_without_mpvpaper(monkeypatch: pytest.MonkeyPatch):
    """wlroots 会话未装 mpvpaper：给安装指引，而不是 KDE 实验开关指引。"""
    checks = _doctor_checks(monkeypatch, _WLROOTS_NO_MPVPAPER)
    assert checks[0].status == "warn"
    assert "安装 mpvpaper" in checks[0].hint


def test_doctor_warns_on_gnome_unsupported(monkeypatch: pytest.MonkeyPatch):
    """GNOME 等其它 Wayland 桌面：无受支持的视频壁纸层。"""
    checks = _doctor_checks(monkeypatch, _GNOME_UNSUPPORTED)
    assert checks[0].status == "warn"
    assert "当前 Wayland 桌面无受支持" in checks[0].hint


def test_doctor_warns_when_capability_probe_unavailable(monkeypatch: pytest.MonkeyPatch):
    """异常防御：能力子表缺失/异常不得崩溃，也不得渲染为 pass。"""
    checks = _doctor_checks(monkeypatch, {})
    assert checks[0].status == "warn"
    assert "capability probe unavailable" in checks[0].detail


def test_doctor_check_stays_optional(monkeypatch: pytest.MonkeyPatch):
    """可选项语义保持：即使 warn 也不把 --doctor 退出码打成 fail。"""
    checks = _doctor_checks(monkeypatch, _KDE_MPVPAPER)
    assert checks[0].required is False


# ---------------------------------------------------------------------------
# 诊断层：x11 回归
# ---------------------------------------------------------------------------


def test_x11_session_has_no_wayland_video_check(monkeypatch: pytest.MonkeyPatch):
    """回归：x11 分支保持 xwinwrap 口径，不新增 Wayland 检查。"""
    import app.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "PLATFORM_ID", "linux", raising=False)
    monkeypatch.setattr("platform_adapters.session.detect_session_type", lambda env=None: "x11")
    monkeypatch.setattr(
        "platform_adapters.session.linux_video_wallpaper_capability",
        lambda: _WLROOTS_READY,  # 即使能力 ready 也不得在 x11 分支出现本检查
    )
    report = diagnostics.collect_diagnostics()
    names = [c.name for c in report.checks]
    assert "Wayland video embedding" not in names
    assert "X11 desktop video embedding" in names  # 证明确实走了 x11 分支


# ---------------------------------------------------------------------------
# session 门面：能力判定透传
# ---------------------------------------------------------------------------


def test_session_facade_capability_shape():
    """真跑不 mock：沙箱 Linux 无 wayland 会话，state 来自真实环境——
    只断言形状与键存在，不断言具体值（非 Linux 主机则验证惰性默认形状）。"""
    from platform_adapters import session as session_facade

    result = session_facade.linux_video_wallpaper_capability()
    assert isinstance(result, dict)
    assert "state" in result
    assert "runtime_ready" in result


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux 后端透传仅 Linux 主机可用")
def test_session_facade_passes_through_video_wallpaper_subdict(monkeypatch: pytest.MonkeyPatch):
    """透传：只取 probe_capabilities() 的 video_wallpaper 子表，且返回副本。"""
    from platform_adapters import session as session_facade
    from platform_adapters.backends.linux import capabilities

    video = {
        "state": "best_effort",
        "runtime_ready": True,
        "backend": "mpvpaper layer-shell (wlroots compositors)",
        "limitations": "x",
    }
    canned = {
        "video_wallpaper": video,
        "static_wallpaper": {"state": "supported", "runtime_ready": True, "backend": "plasma", "limitations": "l"},
    }
    monkeypatch.setattr(capabilities, "probe_capabilities", lambda env=None, which=None: canned)
    result = session_facade.linux_video_wallpaper_capability()
    assert result == video
    result["backend"] = "tampered"
    assert video["backend"] == "mpvpaper layer-shell (wlroots compositors)"  # 副本，不污染能力表


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux 后端透传仅 Linux 主机可用")
def test_session_facade_returns_empty_dict_on_malformed_probe(monkeypatch: pytest.MonkeyPatch):
    """异常防御：probe 返回缺 video_wallpaper 键时回退为空 dict（不抛异常）。"""
    from platform_adapters import session as session_facade
    from platform_adapters.backends.linux import capabilities

    monkeypatch.setattr(capabilities, "probe_capabilities", lambda env=None, which=None: {"static_wallpaper": {}})
    assert session_facade.linux_video_wallpaper_capability() == {}
    monkeypatch.setattr(
        capabilities, "probe_capabilities", lambda env=None, which=None: {"video_wallpaper": "corrupted"}
    )
    assert session_facade.linux_video_wallpaper_capability() == {}
