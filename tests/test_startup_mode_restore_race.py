from __future__ import annotations

from pathlib import Path

from core import engine as core


def test_stale_startup_restore_does_not_override_newer_mode(monkeypatch):
    old_mode = core.config.get("mode")
    core.config["mode"] = "图片"
    try:
        monkeypatch.setattr(
            core,
            "start_video_wallpaper",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale restore must not start video")),
        )
        assert core.restore_configured_wallpaper_mode("视频") is True
        assert core.config["mode"] == "图片"
    finally:
        core.config["mode"] = old_mode


def test_current_video_startup_restore_uses_current_source(monkeypatch):
    old = dict(core.config)
    core.config["mode"] = "视频"
    core.config["video_file"] = "/tmp/current-video.mp4"
    calls = []
    try:
        monkeypatch.setattr(core, "start_video_wallpaper", lambda path=None: calls.append(path) or True)
        assert core.restore_configured_wallpaper_mode("视频") is True
        assert calls == ["/tmp/current-video.mp4"]
    finally:
        core.config.clear()
        core.config.update(old)


def test_current_html_startup_restore_accepts_url(monkeypatch):
    old = dict(core.config)
    core.config["mode"] = "HTML"
    core.config["html_file"] = ""
    core.config["html_url"] = "https://example.invalid/wallpaper"
    calls = []
    try:
        monkeypatch.setattr(core, "start_html_wallpaper", lambda path=None: calls.append(path) or True)
        assert core.restore_configured_wallpaper_mode("HTML") is True
        assert calls == ["https://example.invalid/wallpaper"]
    finally:
        core.config.clear()
        core.config.update(old)


def test_entry_delayed_restore_routes_through_atomic_core_guard():
    text = Path("src/app/entry.py").read_text(encoding="utf-8")
    block = text.split("_startup_mode = normalize_mode_key", 1)[1].split("# v1.4.6: 三档性能模式", 1)[0]
    assert block.count("core.restore_configured_wallpaper_mode(") == 3
    assert "core.start_video_wallpaper(" not in block
    assert "core.start_html_wallpaper(" not in block
    assert "core.start_slideshow(" not in block
    assert 'core.config.get("html_file") or core.config.get("html_url")' in block
