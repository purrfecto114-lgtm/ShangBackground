from __future__ import annotations

import ast
from pathlib import Path
from threading import RLock

import pytest

from app.wallpaper_mode_service import WallpaperModeError, WallpaperModeService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = PROJECT_ROOT / "src" / "ui" / "main_window.py"


def _method_source(method_name: str) -> str:
    text = MAIN_WINDOW.read_text(encoding="utf-8")
    tree = ast.parse(text)
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == method_name)
    lines = text.splitlines()
    return "\n".join(lines[method.lineno - 1 : method.end_lineno])


def test_gui_mode_combo_delegates_mode_commit_to_transaction_service():
    block = _method_source("on_mode_changed")
    assert "core.switch_wallpaper_mode(mode_key, updates=updates)" in block
    assert 'core.config["mode"] =' not in block
    assert "core.stop_video_wallpaper()" not in block
    assert "core.stop_slideshow()" not in block


def test_mode_worker_completion_reconciles_combo_with_committed_config():
    block = _method_source("_on_core_finished")
    assert "_sync_mode_ui_from_config()" in block


def test_failed_mode_activation_restores_previous_config_and_runtime():
    config = {"mode": "图片", "single_image": "before.jpg", "video_file": "candidate.mp4"}
    activations: list[str] = []
    persists: list[str] = []

    def activate(mode: str, _config):
        activations.append(mode)
        return mode != "视频"

    def persist() -> bool:
        persists.append(str(config["mode"]))
        return True

    service = WallpaperModeService(
        config=lambda: config,
        persist=persist,
        operation_lock=RLock(),
        mode_order=("图片", "视频", "HTML"),
        normalize_mode=lambda value: str(value),
        activate=activate,
    )

    with pytest.raises(WallpaperModeError):
        service.switch("视频")

    assert config["mode"] == "图片"
    assert activations == ["视频", "图片"]
    assert persists == ["图片"]


def test_shared_gui_has_no_direct_mode_assignment_outside_core_transaction():
    text = MAIN_WINDOW.read_text(encoding="utf-8")
    assert 'core.config["mode"] =' not in text


@pytest.mark.parametrize(
    "method_name,target",
    [
        ("start_slideshow_from_gui", "幻灯片放映"),
        ("choose_single_image", "图片"),
        ("start_video_wallpaper_from_gui", "视频"),
        ("_apply_static_wallpaper_item", "图片"),
        ("use_bing_cache_as_slideshow", "幻灯片放映"),
    ],
)
def test_direct_gui_mode_entrypoints_use_central_transaction(method_name: str, target: str):
    block = _method_source(method_name)
    assert "core.switch_wallpaper_mode(" in block
    assert f'"{target}"' in block
    assert "updates=" in block


def test_same_mode_staged_source_failure_restores_old_source_and_reactivates_old_runtime():
    config = {"mode": "视频", "video_file": "old.mp4"}
    activations: list[tuple[str, str]] = []
    persists: list[tuple[str, str]] = []

    def activate(mode: str, cfg):
        activations.append((mode, str(cfg.get("video_file", ""))))
        return cfg.get("video_file") != "broken.mp4"

    def persist() -> bool:
        persists.append((str(config.get("mode")), str(config.get("video_file"))))
        return True

    service = WallpaperModeService(
        config=lambda: config,
        persist=persist,
        operation_lock=RLock(),
        mode_order=("图片", "视频", "HTML"),
        normalize_mode=lambda value: str(value),
        activate=activate,
    )

    with pytest.raises(WallpaperModeError):
        service.switch("视频", updates={"video_file": "broken.mp4"})

    assert config == {"mode": "视频", "video_file": "old.mp4"}
    assert activations == [("视频", "broken.mp4"), ("视频", "old.mp4")]
    assert persists == [("视频", "old.mp4")]


def test_html_refresh_uses_compensated_mode_transaction_not_destructive_direct_restart():
    block = _method_source("_run_html_wallpaper_from_gui")
    assert 'core.switch_wallpaper_mode("HTML", updates=updates)' in block
    assert "core.restart_html_wallpaper" not in block
    assert "core.save_config()" not in block


def test_video_source_is_validated_without_persisting_before_mode_transaction():
    block = _method_source("start_video_wallpaper_from_gui")
    assert "_video_source.validate(" in block
    assert "_video_source.commit(" not in block
    assert 'updates={"video_file": result.value}' in block


# ---------------------------------------------------------------------------
# v1.6.1: structured ModeSwitchReport — compensation failures must reach the
# caller instead of vanishing into the log.
# ---------------------------------------------------------------------------


def test_failed_activation_report_describes_full_rollback_failure():
    """New mode fails, the old mode also fails to restart, and the rolled-back
    config also fails to persist: the raised error must carry a report that
    says exactly that."""
    from app.wallpaper_mode_service import ModeSwitchReport

    config = {"mode": "图片", "single_image": "before.jpg"}
    attempts: list[str] = []

    def activate(mode: str, _config):
        attempts.append(mode)
        if mode == "视频":
            raise RuntimeError("backend exploded")
        raise RuntimeError("rollback renderer exploded")

    def persist() -> bool:
        raise OSError("disk full")

    service = WallpaperModeService(
        config=lambda: config,
        persist=persist,
        operation_lock=RLock(),
        mode_order=("图片", "视频"),
        normalize_mode=lambda value: str(value),
        activate=activate,
    )

    with pytest.raises(WallpaperModeError) as exc_info:
        service.switch("视频")

    report = exc_info.value.report
    assert isinstance(report, ModeSwitchReport)
    assert report.mode == "视频"
    assert report.previous_mode == "图片"
    assert report.stage == "activate"
    assert "backend exploded" in report.error
    rollback = report.rollback
    assert rollback.config_restored is True
    assert rollback.runtime_restored is False
    assert rollback.persisted is False
    assert rollback.errors  # rollback failures are captured, not just logged
    summary = rollback.summary()
    assert "旧模式恢复失败" in summary
    assert "恢复结果保存失败" in summary


def test_failed_activation_report_describes_successful_rollback():
    config = {"mode": "图片", "single_image": "before.jpg"}

    def activate(mode: str, _config):
        return mode != "视频"

    service = WallpaperModeService(
        config=lambda: config,
        persist=lambda: True,
        operation_lock=RLock(),
        mode_order=("图片", "视频"),
        normalize_mode=lambda value: str(value),
        activate=activate,
    )

    with pytest.raises(WallpaperModeError) as exc_info:
        service.switch("视频")

    rollback = exc_info.value.report.rollback
    assert rollback.config_restored is True
    assert rollback.runtime_restored is True
    assert rollback.persisted is True
    assert rollback.errors == ()
    assert "旧模式已恢复运行" in rollback.summary()
    assert "恢复结果已保存" in rollback.summary()


def test_report_marks_unknown_previous_mode_as_not_attempted():
    """Switching away from a mode that is not in the active order (e.g. a
    stale config value) must report runtime_restored=None (no rollback was
    possible), not a silent False."""
    config = {"mode": "远古模式"}

    service = WallpaperModeService(
        config=lambda: config,
        persist=lambda: True,
        operation_lock=RLock(),
        mode_order=("图片", "视频"),
        normalize_mode=lambda value: str(value),
        activate=lambda mode, _config: mode != "视频",
    )

    with pytest.raises(WallpaperModeError) as exc_info:
        service.switch("视频")

    rollback = exc_info.value.report.rollback
    assert rollback.runtime_restored is None
    assert "无旧模式需要恢复" in rollback.summary()


def test_persist_stage_failure_is_reported_as_persist_stage():
    """A mode that activates fine but fails to save must be reported as
    stage="persist" so the UI can explain the desktop already changed."""
    config = {"mode": "图片"}

    service = WallpaperModeService(
        config=lambda: config,
        persist=lambda: False,
        operation_lock=RLock(),
        mode_order=("图片", "视频"),
        normalize_mode=lambda value: str(value),
        activate=lambda mode, _config: True,
    )

    with pytest.raises(WallpaperModeError) as exc_info:
        service.switch("视频")

    report = exc_info.value.report
    assert report.stage == "persist"


def test_wallpaper_mode_error_report_is_optional_for_backward_compat():
    from app.wallpaper_mode_service import WallpaperModeError as error_type

    assert error_type("plain failure").report is None


def test_engine_facade_exposes_structured_report_to_gui():
    """The engine facade must mirror the report the same way it mirrors
    last_operation_error (source-level contract, same style as the other
    facade pins)."""
    engine_text = (PROJECT_ROOT / "src" / "core" / "engine.py").read_text(encoding="utf-8")
    facade_block = engine_text.split("def switch_wallpaper_mode(", 1)[1].split("def ", 1)[0]
    assert "_set_last_mode_switch_report(None)" in facade_block
    assert "_set_last_mode_switch_report(report)" in facade_block
    assert "last_mode_switch_report" in engine_text


def test_gui_failure_warning_includes_rollback_summary():
    """_on_core_finished must surface the rollback summary in the warning
    dialog (source-level contract)."""
    block = _method_source("_on_core_finished")
    assert "last_mode_switch_report" in block
    assert "rollback.summary()" in block


def test_rollback_report_handles_production_mode_activation_result_type():
    """16-b M1 regression: production activate callbacks (bootstrap.activate_mode)
    return ModeActivationResult — a dataclass that is always truthy without
    __bool__. A failed rollback re-activation returning ModeActivationResult
    (ok=False) must be reported as runtime_restored=False, not silently
    reported as "旧模式已恢复运行"."""
    from app.wallpaper_mode_service import ModeActivationResult

    config = {"mode": "视频", "video_file": "old.mp4"}
    calls: list[str] = []

    def activate(mode: str, _config):
        calls.append(mode)
        if mode == "视频" and _config.get("video_file") == "broken.mp4":
            return ModeActivationResult(ok=False)
        if mode == "视频":
            # Rollback re-activation also fails in production shape.
            return ModeActivationResult(ok=False)
        return True

    service = WallpaperModeService(
        config=lambda: config,
        persist=lambda: True,
        operation_lock=RLock(),
        mode_order=("图片", "视频"),
        normalize_mode=lambda value: str(value),
        activate=activate,
    )

    with pytest.raises(WallpaperModeError) as exc_info:
        service.switch("视频", updates={"video_file": "broken.mp4"})

    rollback = exc_info.value.report.rollback
    assert rollback.runtime_restored is False
    assert "旧模式恢复失败" in rollback.summary()
    assert rollback.errors  # the failed re-activation is captured


def test_mode_activation_result_bool_mirrors_ok():
    """Defense-in-depth: ModeActivationResult truthiness must follow ok, so no
    future bare truthiness check can misread a failed activation."""
    from app.wallpaper_mode_service import ModeActivationResult

    assert bool(ModeActivationResult(ok=True)) is True
    assert bool(ModeActivationResult(ok=False)) is False
