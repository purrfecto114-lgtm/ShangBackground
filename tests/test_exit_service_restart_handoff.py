from __future__ import annotations

from app.exit_service import ExitService


def _service(events: list[str]) -> ExitService:
    def step(name: str, value=None):
        def _run():
            events.append(name)
            return value
        return _run

    return ExitService(
        request_cancel=step("cancel"),
        stop_slideshow=step("slideshow", True),
        stop_media=step("media", True),
        stop_hotkeys=step("hotkeys", True),
        restore_wallpaper=step("restore", True),
        has_restore_candidate=lambda: True,
        close_ipc=step("ipc"),
        release_single_instance=step("release", True),
    )


def test_restart_closes_ipc_but_keeps_singleton_until_process_exit():
    events: list[str] = []
    report = _service(events).run(
        reason="relaunch", restore_wallpaper=False, restarting=True
    )

    assert "ipc" in events
    assert "release" not in events
    release_step = next(step for step in report.steps if step.name == "release_single_instance")
    assert release_step.skipped
    assert release_step.detail == "restart handoff"


def test_normal_exit_still_releases_singleton_explicitly():
    events: list[str] = []
    report = _service(events).run(reason="user_exit", restore_wallpaper=False)

    assert events[-2:] == ["ipc", "release"]
    release_step = next(step for step in report.steps if step.name == "release_single_instance")
    assert release_step.attempted
    assert release_step.success
