from __future__ import annotations

from pathlib import Path

from core import engine as core


def test_browse_in_slideshow_keeps_mode_and_resets_timer(monkeypatch, tmp_path):
    target = tmp_path / "slide.jpg"
    target.write_bytes(b"image")
    old_mode = core.config.get("mode")
    core.config["mode"] = "幻灯片放映"
    calls = []
    try:
        monkeypatch.setattr(core, "set_wallpaper", lambda path, op="": calls.append(("apply", path, op)) or True)
        monkeypatch.setattr(core, "reset_slide_timer", lambda: calls.append(("reset",)) or True)
        monkeypatch.setattr(
            core,
            "switch_wallpaper_mode",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("slideshow browse must not change mode")),
        )

        assert core.apply_browsed_wallpaper(str(target), "browse") is True
        assert calls == [("apply", str(target.absolute()), "browse"), ("reset",)]
        assert core.config["mode"] == "幻灯片放映"
    finally:
        core.config["mode"] = old_mode


def test_browse_outside_slideshow_switches_transactionally_to_image(monkeypatch, tmp_path):
    target = tmp_path / "picked.jpg"
    target.write_bytes(b"image")
    old_mode = core.config.get("mode")
    core.config["mode"] = "视频"
    calls = []
    try:
        monkeypatch.setattr(
            core,
            "switch_wallpaper_mode",
            lambda mode, *, updates=None: calls.append((mode, dict(updates or {}))) or True,
        )
        monkeypatch.setattr(
            core,
            "set_wallpaper",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("non-slideshow browse must use mode transaction")),
        )

        assert core.apply_browsed_wallpaper(str(target), "browse") is True
        assert calls == [("图片", {"single_image": str(target.absolute())})]
    finally:
        core.config["mode"] = old_mode


def test_browse_missing_file_does_not_change_mode(monkeypatch, tmp_path):
    missing = tmp_path / "missing.jpg"
    old_mode = core.config.get("mode")
    core.config["mode"] = "HTML"
    try:
        monkeypatch.setattr(
            core,
            "switch_wallpaper_mode",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("missing file must not switch mode")),
        )
        assert core.apply_browsed_wallpaper(str(missing)) is False
        assert "不存在" in core.last_operation_error
        assert core.config["mode"] == "HTML"
    finally:
        core.config["mode"] = old_mode


def test_current_sidebar_routes_use_browse_transaction_facade():
    main_text = Path("src/ui/main_window.py").read_text(encoding="utf-8")
    support_text = Path("src/app/support.py").read_text(encoding="utf-8")
    assert 'core.apply_browsed_wallpaper(path, t("侧边栏切换"))' in main_text
    assert 'core.apply_browsed_wallpaper(path, t("侧边栏切换"))' in support_text
