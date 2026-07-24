from __future__ import annotations

from types import SimpleNamespace

from app.mpv_backend import LegacyModuleMpvBackend


def test_legacy_mpv_backend_normalizes_lifecycle_and_properties():
    calls: list[tuple] = []
    module = SimpleNamespace(
        start_video_wallpaper=lambda target, muted, volume: calls.append(("start", target, muted, volume)) or (True, "ok"),
        stop_video_wallpaper=lambda: calls.append(("stop",)),
        is_video_wallpaper_running=lambda: True,
        set_video_paused=lambda paused: calls.append(("pause", paused)) or True,
        set_video_volume=lambda muted, volume: calls.append(("volume", muted, volume)) or True,
        get_last_path=lambda: "/tmp/demo.mp4",
    )
    backend = LegacyModuleMpvBackend(module)

    result = backend.start("demo.mp4", muted=False, volume=150)

    assert result.ok is True
    assert result.message == "ok"
    assert backend.is_running() is True
    assert backend.pause(True) is True
    assert backend.set_property("volume", (False, -5)) is True
    assert backend.last_target() == "/tmp/demo.mp4"
    backend.stop()
    assert calls == [
        ("start", "demo.mp4", False, 100),
        ("pause", True),
        ("volume", False, 0),
        ("stop",),
    ]


def test_legacy_mpv_backend_optional_controls_degrade_to_false():
    module = SimpleNamespace(
        start_video_wallpaper=lambda *_args, **_kwargs: False,
        stop_video_wallpaper=lambda: None,
        is_video_wallpaper_running=lambda: False,
    )
    backend = LegacyModuleMpvBackend(module)

    assert backend.pause(False) is False
    assert backend.set_property("unknown", 1) is False
    assert backend.observe_property("time-pos", lambda *_args: None) is False
    assert backend.ipc(["get_property", "pause"]) is False
