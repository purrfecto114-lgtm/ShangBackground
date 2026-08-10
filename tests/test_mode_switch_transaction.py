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
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
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
    assert 'core.switch_wallpaper_mode(' in block
    assert f'"{target}"' in block
    assert 'updates=' in block


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
