from __future__ import annotations

from app.context_menu_fastpath import command_from_argv, handle_context_menu_fastpath


def test_command_from_argv_accepts_settings():
    assert command_from_argv(["app.exe", "--from-context-menu", "--settings"]) == "settings"


def test_settings_uses_existing_instance_before_spawn():
    sent: list[str] = []
    result = handle_context_menu_fastpath(
        ["app.exe", "--from-context-menu", "--settings"],
        is_windows=True,
        sender=lambda command: sent.append(command) or True,
    )
    assert result == 0
    assert sent == ["settings"]


def test_context_fastpath_forwards_without_spawning():
    sent: list[str] = []
    spawned: list[list[str]] = []

    result = handle_context_menu_fastpath(
        ["ShangBackground.exe", "--from-context-menu", "--next"],
        is_windows=True,
        sender=lambda command: sent.append(command) or True,
        spawner=lambda argv: spawned.append(list(argv)) or True,
    )

    assert result == 0
    assert sent == ["next"]
    assert spawned == []


def test_context_fastpath_detaches_cold_start_when_no_instance():
    spawned: list[list[str]] = []

    result = handle_context_menu_fastpath(
        ["ShangBackground.exe", "--from-context-menu", "--random"],
        is_windows=True,
        sender=lambda _command: False,
        spawner=lambda argv: spawned.append(list(argv)) or True,
    )

    assert result == 0
    assert spawned == [["ShangBackground.exe", "--from-context-menu", "--random"]]


def test_context_fastpath_child_marker_prevents_recursive_detach():
    assert handle_context_menu_fastpath(
        [
            "ShangBackground.exe",
            "--from-context-menu",
            "--previous",
            "--context-menu-dispatched-child",
        ],
        is_windows=True,
        sender=lambda _command: (_ for _ in ()).throw(AssertionError("must not send")),
        spawner=lambda _argv: (_ for _ in ()).throw(AssertionError("must not spawn")),
    ) is None


def test_context_fastpath_is_windows_only():
    assert handle_context_menu_fastpath(
        ["main.py", "--from-context-menu", "--next"],
        is_windows=False,
        sender=lambda _command: True,
        spawner=lambda _argv: True,
    ) is None


def test_context_command_preserves_set_wallpaper_payload(tmp_path):
    target = tmp_path / "a wallpaper.jpg"
    assert command_from_argv(
        ["app.exe", "--from-context-menu", "--set-wallpaper", str(target)]
    ) == "set_wallpaper|" + str(target.absolute())


def test_path_payload_is_not_sent_over_legacy_fast_ipc(tmp_path):
    target = tmp_path / "wallpaper.jpg"
    sent: list[str] = []
    spawned: list[list[str]] = []
    result = handle_context_menu_fastpath(
        ["app.exe", "--from-context-menu", "--set-wallpaper", str(target)],
        is_windows=True,
        sender=lambda command: sent.append(command) or True,
        spawner=lambda argv: spawned.append(list(argv)) or True,
    )
    assert result == 0
    assert sent == []
    assert spawned


def test_relaunch_filters_internal_context_child_marker():
    from app.relaunch_service import RelaunchService

    service = RelaunchService(
        is_windows=True,
        is_frozen=lambda: True,
        executable_path=lambda: "C:/ShangBackground.exe",
        base_dir=lambda: "C:/",
        capture_session=lambda: None,
        persist_session=lambda: None,
        release_guard=lambda: None,
        cleanup_tray=lambda: None,
        recover_guard=lambda: None,
        log=lambda *_args, **_kwargs: None,
        argv=lambda: (
            "C:/ShangBackground.exe",
            "--from-context-menu",
            "--next",
            "--context-menu-dispatched-child",
        ),
    )
    assert service.filtered_restart_args() == ["--inherit-session-wallpaper"]


def test_ipc_set_wallpaper_routes_through_mode_transaction(monkeypatch, tmp_path):
    from core import engine

    target = tmp_path / "ipc wallpaper.jpg"
    target.write_bytes(b"image")
    calls = []
    monkeypatch.setattr(
        engine,
        "switch_wallpaper_mode",
        lambda mode, *, updates=None: calls.append((mode, dict(updates or {}))) or True,
    )

    assert engine._execute_ipc_wallpaper_command("set_wallpaper|" + str(target)) is True
    assert calls == [("图片", {"single_image": str(target.absolute())})]


def test_primary_local_ipc_set_wallpaper_uses_mode_transaction():
    from pathlib import Path

    text = Path("src/app/entry.py").read_text(encoding="utf-8")
    local_block = text.split('elif command == "set_wallpaper":', 1)[1].split(
        'elif command in {"previous", "next", "random"}:', 1
    )[0]
    assert "core.switch_wallpaper_mode" in local_block
    assert 'updates={"single_image": target}' in local_block
    assert "core.set_wallpaper(" not in local_block
