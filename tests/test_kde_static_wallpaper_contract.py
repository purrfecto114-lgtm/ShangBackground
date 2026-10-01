"""KDE 静态壁纸契约（KDE_SUPPORT_PLAN 任务 3 步骤 3-4：按输出的设置策略）。

钉住两条路径的行为边界：

- **全输出路径** ``_set_kde_wallpaper``（plasma-apply-wallpaperimage 成功 +
  evaluateScript 回退）——回归红线：按输出任务不得改变其行为；
- **按输出路径** ``set_kde_wallpaper_for_screen``（新增公开后端能力，
  UI/ports 接线为后续工作）——先用只读探针建立 containment→screen 的
  显式映射，写入脚本只用 **id+screen 双重条件**定位探针命中的
  containment；无法映射时拒绝执行（不 spawn 写脚本、不触碰任何桌面，
  拒绝部分成功），错误与"命令缺失 / 无会话 bus / Plasma 拒绝"三种既有
  错误可区分。

风格对齐 tests/test_kde_restore_scope.py::_run_set：mock ``_run_args`` /
``shutil.which`` / ``_file_uri``，并钉住 ``DBUS_SESSION_BUS_ADDRESS``
（CI runner 无 bus 端点，v1.6.2 _file_uri 教训同款预防——不钉住则成功
路径被 D-Bus 前置检查误拒）。探针与写脚本共用 ``_run_plasma_script``，
按脚本内容路由 mock 返回（探针含 ``print("ID:"``、写脚本含
``SHANGBACKGROUND_KDE_SET_DONE``）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# 共享 mock 辅助
# ---------------------------------------------------------------------------


def _pin_dbus_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/shangbackground-test-bus")


# 探针输出范本：screen 0 与 screen 1 各一个 containment（org.kde.image）。
_PROBE_TWO_SCREENS = (
    "ID:12  SCREEN:0  PLUGIN:org.kde.image\n"
    "IMAGE:file:///home/u/a.jpg\n"
    "FILLMODE:2\n"
    "ID:13  SCREEN:1  PLUGIN:org.kde.image\n"
    "IMAGE:file:///home/u/b.jpg\n"
    "FILLMODE:1\n"
)


def _per_screen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    screen_index: object = 1,
    fill_mode: int | None = None,
    path: str = "/tmp/wp.png",
    probe_ok: bool = True,
    probe_out: str = _PROBE_TWO_SCREENS,
    write_ok: bool = True,
    write_out: str = "SHANGBACKGROUND_KDE_SET_DONE:1\n",
    bus: bool = True,
):
    """mock 掉外部命令与 Plasma 脚本通道，跑 set_kde_wallpaper_for_screen。

    返回 (结果, outcome, calls, run_args_calls)。calls 逐次记录
    ``_run_plasma_script`` 的脚本（kind=probe/write），run_args_calls 记录
    任何绕过脚本通道的直接命令 spawn。plasma-apply-wallpaperimage 与
    qdbus6 都在 mock PATH 中——前者在场也绝不能被按输出路径使用。
    """
    from platform_adapters.backends.linux import integration

    if bus:
        _pin_dbus_env(monkeypatch)
    else:
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    calls: list[dict[str, object]] = []

    def fake_plasma_script(script, timeout=10, allow_dbus_send=False):
        if "SHANGBACKGROUND_KDE_SET_DONE" in script:
            calls.append({"kind": "write", "script": script, "timeout": timeout})
            return (write_ok, write_out if write_ok else "", "" if write_ok else "qdbus: rejected by Plasma")
        if 'print("ID:"' in script:
            calls.append({"kind": "probe", "script": script, "timeout": timeout})
            return (probe_ok, probe_out if probe_ok else "", "" if probe_ok else "qdbus6: command not found")
        raise AssertionError(f"unexpected Plasma script spawned: {script[:120]!r}")

    run_args_calls: list[list[str]] = []

    def fake_run_args(cmd, timeout=10):
        run_args_calls.append(list(cmd))
        return (0, "", "")

    monkeypatch.setattr(integration, "_run_plasma_script", fake_plasma_script)
    monkeypatch.setattr(integration, "_run_args", fake_run_args)
    monkeypatch.setattr(
        integration.shutil,
        "which",
        lambda name: {
            "plasma-apply-wallpaperimage": "/usr/bin/plasma-apply-wallpaperimage",
            "qdbus6": "/usr/bin/qdbus6",
        }.get(name),
    )
    monkeypatch.setattr(integration, "_ensure_existing_file", lambda p: path)
    monkeypatch.setattr(integration, "_file_uri", lambda p: f"file://{p}")
    result = integration.set_kde_wallpaper_for_screen(path, screen_index, fill_mode=fill_mode)
    return result, integration.last_kde_set_outcome(), calls, run_args_calls


# ---------------------------------------------------------------------------
# 全输出契约：_set_kde_wallpaper 行为钉住（回归红线）
# ---------------------------------------------------------------------------


def _full_output(monkeypatch: pytest.MonkeyPatch, *, plasma_rc: int, script_ok: bool = True):
    """mock 掉外部命令，跑全输出 _set_kde_wallpaper 并记录 spawn。"""
    from platform_adapters.backends.linux import integration

    _pin_dbus_env(monkeypatch)
    spawn: list[list[str]] = []
    monkeypatch.setattr(
        integration.shutil,
        "which",
        lambda name: {
            "plasma-apply-wallpaperimage": "/usr/bin/plasma-apply-wallpaperimage",
            "qdbus6": "/usr/bin/qdbus6",
        }.get(name),
    )
    monkeypatch.setattr(
        integration,
        "_run_args",
        lambda cmd, timeout=8: spawn.append(list(cmd)) or (plasma_rc, "", ""),
    )
    monkeypatch.setattr(
        integration,
        "_run_plasma_script",
        lambda script, timeout=10, allow_dbus_send=False: (
            (True, "SHANGBACKGROUND_KDE_SET_DONE:2\n", "") if script_ok else (False, "", "qdbus: rejected by Plasma")
        ),
    )
    monkeypatch.setattr(integration, "_ensure_existing_file", lambda p: "/tmp/wp.png")
    monkeypatch.setattr(integration, "_file_uri", lambda p: f"file://{p}")
    monkeypatch.setattr(integration, "_verify_kde_wallpaper", lambda abs_path, timeout=0.5: (False, ""))
    result = integration._set_kde_wallpaper("/tmp/wp.png")
    return result, integration.last_kde_set_outcome(), spawn


def test_full_output_plasma_apply_success_is_pinned(monkeypatch: pytest.MonkeyPatch):
    """plasma-apply-wallpaperimage rc=0 → 接受；命令原样 spawn；method 记录。
    本任务不得动到全输出路径的任何行为。"""
    result, outcome, spawn = _full_output(monkeypatch, plasma_rc=0)
    ok, _message = result
    assert ok is True
    assert spawn == [["plasma-apply-wallpaperimage", "/tmp/wp.png"]]
    assert outcome["accepted"] is True
    assert outcome["verified"] is False  # 读回未确认是 Plasma 6 已知行为
    assert outcome["method"] == "plasma-apply-wallpaperimage"


def test_full_output_plasma_apply_failure_falls_back_to_script(monkeypatch: pytest.MonkeyPatch):
    """plasma-apply rc!=0 → 回退 evaluateScript（全 containment 脚本）仍成功。"""
    result, outcome, spawn = _full_output(monkeypatch, plasma_rc=1)
    ok, _message = result
    assert ok is True
    assert spawn == [["plasma-apply-wallpaperimage", "/tmp/wp.png"]]
    assert outcome["accepted"] is True
    assert outcome["method"] == "evaluateScript"
    assert "screen" not in str(outcome["method"])  # 全输出 method 不得与 per-screen 混淆


# ---------------------------------------------------------------------------
# per-screen：成功路径（显式映射写入）
# ---------------------------------------------------------------------------


def test_per_screen_success_writes_only_mapped_containment(monkeypatch: pytest.MonkeyPatch):
    """探针返回 screen=1 的 containment（ID 13）→ 写脚本只命中它：
    targets JSON 同时携带 id 与 screen（双重条件），未命中的 containment
    （ID 12）不得进入写脚本；DONE>=1 → 成功；outcome.method 可区分。"""
    result, outcome, calls, run_args_calls = _per_screen(monkeypatch, screen_index=1, fill_mode=2)
    ok, _message = result
    assert ok is True
    assert [c["kind"] for c in calls] == ["probe", "write"]
    write_script = str(calls[1]["script"])
    # 双重条件：id 与 screen 同时进入 targets JSON。
    assert '"id": 13' in write_script
    assert '"screen": 1' in write_script
    # 双重条件必须在脚本内同时生效（防探针与写入之间的竞态写错对象）。
    assert "Number(targets[t].id) === Number(d.id)" in write_script
    assert "Number(targets[t].screen) === sv" in write_script
    # 探针未命中的 containment 不得成为写入目标。
    assert '"id": 12' not in write_script
    assert '"screen": 0' not in write_script
    # 写入原语与 FillMode。
    assert 'writeConfig("Image"' in write_script
    assert 'writeConfig("FillMode", 2)' in write_script
    assert "reloadConfig()" in write_script
    # plasma-apply-wallpaperimage 在 PATH 中也不得被使用（全输出命令）。
    assert run_args_calls == []
    # 写入超时与全输出 evaluateScript 回退一致（4s）。
    assert calls[1]["timeout"] == 4
    assert outcome["accepted"] is True
    assert outcome["verified"] is False
    assert outcome["method"] == "evaluateScript(screen=1)"


def test_per_screen_success_without_fill_mode_omits_fillmode_write(monkeypatch: pytest.MonkeyPatch):
    """fill_mode=None：写脚本不携带 FillMode 行（缺省即缺省，与全输出
    _kde_set_script 同语义）。"""
    result, _outcome, calls, _run_args = _per_screen(monkeypatch, screen_index=1, fill_mode=None)
    assert result[0] is True
    write_script = str(calls[1]["script"])
    assert 'writeConfig("FillMode"' not in write_script
    assert 'writeConfig("Image"' in write_script


def test_per_screen_write_script_forbids_plasma_apply_command(monkeypatch: pytest.MonkeyPatch):
    """禁用 plasma-apply-wallpaperimage 的理由必须写进脚本注释（全输出
    命令，无法只改一个屏幕）——只走 evaluateScript。"""
    _result, _outcome, calls, _run_args = _per_screen(monkeypatch, screen_index=1)
    write_script = str(calls[1]["script"])
    assert "plasma-apply-wallpaperimage" in write_script
    assert "全输出" in write_script


# ---------------------------------------------------------------------------
# per-screen：拒绝部分成功（核心语义）
# ---------------------------------------------------------------------------


def test_per_screen_unmappable_screen_rejects_without_writing(monkeypatch: pytest.MonkeyPatch):
    """探针的 containment 全部 screen!=N → (False, 可操作错误)，且只有
    探针被 spawn——没有任何写脚本、没有任何直接命令、不触碰任何桌面。"""
    probe_out = "ID:12  SCREEN:0  PLUGIN:org.kde.image\nID:14  SCREEN:2  PLUGIN:org.kde.image\n"
    result, outcome, calls, run_args_calls = _per_screen(monkeypatch, probe_out=probe_out, screen_index=1)
    ok, message = result
    assert ok is False
    assert [c["kind"] for c in calls] == ["probe"]
    assert run_args_calls == []
    # 错误必须与三种既有错误可区分，且可操作（告知观察到的 screen 集）。
    assert "无法映射" in message
    assert "screen 1" in message
    assert "[0, 2]" in message
    assert "command not found" not in message
    assert "session bus unavailable" not in message
    assert "rejected by Plasma" not in message
    assert outcome["accepted"] is False
    assert outcome["verified"] is False


def test_per_screen_screen_match_without_valid_id_is_rejected(monkeypatch: pytest.MonkeyPatch):
    """探针命中 screen=1 但 id 无法解析——做不了 id+screen 双重定位，
    同样拒绝（宁可不写，不弱化双重条件）。"""
    probe_out = "ID:abc  SCREEN:1  PLUGIN:org.kde.image\n"
    result, outcome, calls, run_args_calls = _per_screen(monkeypatch, probe_out=probe_out, screen_index=1)
    ok, message = result
    assert ok is False
    assert [c["kind"] for c in calls] == ["probe"]
    assert run_args_calls == []
    assert "id" in message
    assert outcome["accepted"] is False


def test_per_screen_write_race_done_zero_rejects_partial_success(monkeypatch: pytest.MonkeyPatch):
    """探针命中 screen=1，但写脚本运行时 applied==0（探针与写入之间
    containment 全部消失的竞态）→ 判失败，不部分成功。"""
    result, outcome, calls, run_args_calls = _per_screen(
        monkeypatch, screen_index=1, write_out="SHANGBACKGROUND_KDE_SET_DONE:0\n"
    )
    ok, message = result
    assert ok is False
    assert [c["kind"] for c in calls] == ["probe", "write"]
    assert run_args_calls == []
    assert "部分成功" in message
    assert outcome["accepted"] is False
    assert outcome["verified"] is False
    assert outcome["method"] == "evaluateScript(screen=1)"


def test_per_screen_write_channel_rejection_reports_failure_without_fallback(monkeypatch: pytest.MonkeyPatch):
    """写通道被 Plasma 拒绝 → 结构化失败，绝不回退全输出命令。

    v1.6.4 验收轮（Task 30-b 变异 5）揭示的测试缺口：写通道拒绝路径
    此前零测试钉住——未来若有人加"拒绝时回退 plasma-apply 全输出兜底"
    （全输出命令会改动用户未请求的其它屏幕），本测试必须红。
    """
    result, outcome, calls, run_args_calls = _per_screen(monkeypatch, write_ok=False)
    ok, message = result
    assert ok is False
    # 探针 + uri/abs_path 两次写尝试，全部走 Plasma 脚本通道
    assert [c["kind"] for c in calls] == ["probe", "write", "write"]
    # 零直接命令 spawn——plasma-apply-wallpaperimage（全输出）绝不能被
    # 按输出路径当兜底使用
    assert run_args_calls == []
    assert outcome["accepted"] is False
    assert outcome["verified"] is False
    assert outcome["method"] == "evaluateScript(screen=1)"
    # 拒绝原因如实透传（校准 S3：uri/abs_path 两次尝试同因只记录一次）
    assert message.count("qdbus: rejected by Plasma") == 1


# ---------------------------------------------------------------------------
# per-screen：探针失败 / 前置检查 / 参数校验
# ---------------------------------------------------------------------------


def test_per_screen_probe_channel_failure_does_not_spawn_write(monkeypatch: pytest.MonkeyPatch):
    """脚本通道不可达（命令缺失类错误）→ (False, 如实透传)，不 spawn
    写脚本。"""
    result, outcome, calls, run_args_calls = _per_screen(monkeypatch, probe_ok=False)
    ok, message = result
    assert ok is False
    assert [c["kind"] for c in calls] == ["probe"]
    assert run_args_calls == []
    assert "command not found" in message
    assert outcome["accepted"] is False


def test_per_screen_probe_empty_output_does_not_spawn_write(monkeypatch: pytest.MonkeyPatch):
    """脚本 rc=0 但解析不出任何 containment → 无法建立映射，拒绝执行。"""
    result, outcome, calls, run_args_calls = _per_screen(monkeypatch, probe_ok=True, probe_out="")
    ok, message = result
    assert ok is False
    assert [c["kind"] for c in calls] == ["probe"]
    assert run_args_calls == []
    assert "containment" in message
    assert outcome["accepted"] is False


def test_per_screen_dbus_precheck_blocks_before_any_spawn(monkeypatch: pytest.MonkeyPatch):
    """无会话 bus → 连探针都不 spawn（前置检查先于任何外部命令）。"""
    result, outcome, calls, run_args_calls = _per_screen(monkeypatch, bus=False)
    ok, message = result
    assert ok is False
    assert calls == []
    assert run_args_calls == []
    assert "会话总线" in message
    assert "DBUS_SESSION_BUS_ADDRESS" in message
    assert "command not found" not in message
    assert outcome["accepted"] is False
    assert outcome["verified"] is False
    assert outcome["method"] == "session-bus-precheck(screen=1)"


@pytest.mark.parametrize("bad_index", [-1, "1", True, 1.5])
def test_per_screen_invalid_index_rejected_without_spawn(monkeypatch: pytest.MonkeyPatch, bad_index):
    """负数/非 int/bool/float 都是非法 screen_index → (False, 参数错误)，
    不 spawn 任何命令（连探针都不跑）。"""
    result, outcome, calls, run_args_calls = _per_screen(monkeypatch, screen_index=bad_index)
    ok, message = result
    assert ok is False
    assert calls == []
    assert run_args_calls == []
    assert "screen_index" in message
    assert outcome["accepted"] is False
    assert outcome["verified"] is False


# ---------------------------------------------------------------------------
# per-screen：注入防护
# ---------------------------------------------------------------------------


def test_per_screen_injection_sanitized_via_json_dumps(monkeypatch: pytest.MonkeyPatch):
    """路径含引号/反斜杠 → 写脚本内是 json.dumps 后的安全字面量
    （复用 _kde_set_script 的 %s + json.dumps 防注入范本）。"""
    nasty = '/tmp/wp"quote\\back.png'
    result, _outcome, calls, _run_args = _per_screen(monkeypatch, path=nasty, screen_index=1)
    ok, _message = result
    assert ok is True
    write_script = str(calls[1]["script"])
    assert json.dumps(f"file://{nasty}") in write_script
    # 原始未转义形态（带包裹引号）不得出现。
    assert f'"file://{nasty}"' not in write_script
    # targets 是 ints，经 json.dumps 注入为安全数组字面量。
    assert json.dumps([{"id": 13, "screen": 1}]) in write_script
