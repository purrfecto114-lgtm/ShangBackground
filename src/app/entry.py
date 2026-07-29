# Explicit application entry; import-safe before GUI dependencies are installed.
from __future__ import annotations

import os
import signal
import sys

from core import engine as core
from app.config import UPDATE_CHECK_ON_STARTUP, UPDATE_CHECK_STARTUP_DELAY_MS, is_supported_video_path, normalize_mode_key
from app.build_features import is_feature_enabled
from core.local_ipc import LocalCommandServer
from app.i18n import t
from app.support import (
    APP_DISPLAY_NAME,
    APP_ORGANIZATION,
    APP_PROCESS_NAME,
    PYSIDE_AVAILABLE,
    PYSIDE_IMPORT_ERROR,
    _dependency_availability_for_pyside,
    _dispatch_action_to_existing_instance,
    _handle_action_args,
    _install_qt_chinese_translator,
    _is_action_launch,
    _is_already_running,
    _parse_early_args,
    _release_singleton_mutex,
    _set_windows_app_identity,
)
from app.startup import schedule_startup_tasks
from app.scaling import apply_dpi_environment, dpi_percent
from app.log_setup import configure_logging, install_qt_message_handler
from app.storage import load_json_object

if PYSIDE_AVAILABLE:
    from app.support import apply_application_font
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
else:  # pragma: no cover - names are guarded by PYSIDE_AVAILABLE in main()
    QApplication = QIcon = QMessageBox = QTimer = None
    apply_application_font = None

def _read_log_enabled_from_config() -> bool:
    """Read the ``log_enabled`` flag from settings.json BEFORE the rest of
    ``core.engine`` is initialized, so we can pass it to ``configure_logging``
    and avoid writing any log files when the user has not opted in.

    Returns False on any error (default-off is the safe behavior).
    """
    config_path = getattr(core, "CONFIG_PATH", None)
    backup_path = getattr(core, "CONFIG_BACKUP_PATH", None)
    for candidate in (config_path, backup_path):
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            return bool(load_json_object(candidate).get("log_enabled", False))
        except Exception:
            continue
    return False


def main() -> int:
    args = _parse_early_args()
    # Initialize structured logging as early as possible.
    try:
        _log_level = "DEBUG" if (getattr(args, "verbose", False) or getattr(args, "debug", False)) else "INFO"
    except Exception:
        _log_level = "INFO"
    # Bug 6 fix: only attach file handlers when the user has explicitly opted
    # in via the "记录日志到文件" setting.  Default is False → no log files on
    # disk, only the in-memory ring buffer (for the in-app log page) and the
    # optional console handler.
    #
    # ``force=True`` keeps startup deterministic if another import configured
    # the logging subsystem before explicit application bootstrap.
    _files_enabled = _read_log_enabled_from_config()
    try:
        configure_logging(level=_log_level, files_enabled=_files_enabled, force=True)
    except Exception as _log_exc:
        sys.stderr.write(f"[entry] logging init failed: {_log_exc}\n")

    # Explicit bootstrap: importing core.engine no longer reads or migrates config.
    core.initialize_application()

    # Shared source tree: platform selection follows the current host.

    if not PYSIDE_AVAILABLE:
        print(f"PySide6 不可用：{PYSIDE_IMPORT_ERROR}")
        try:
            from app.dependencies import prompt_install_dependencies
            prompt_install_dependencies(None, _dependency_availability_for_pyside())
        except Exception as exc:
            print(f"依赖提示不可用：{exc}")
        return 2

    is_action_launch = _is_action_launch(args)
    direct_action_launch = any((
        args.previous, args.next, args.random, bool(args.set_wallpaper),
        args.jump_to_wallpaper, args.show, getattr(args, "quit", False),
    ))

    # One cross-platform lock and one authenticated local command channel.
    if _is_already_running():
        core.log("检测到已有实例，转发动作并退出")
        forwarded = _dispatch_action_to_existing_instance(args)
        if not forwarded:
            core.log("现有实例尚未接受本地 IPC 命令；未在第二进程执行破坏性动作")
            if not direct_action_launch and not is_action_launch:
                core.show_message(t("不要重复运行"), t("不要重复运行，已有主界面正在运行。"))
        if getattr(args, "quit", False) and getattr(args, "wait_for_exit", False):
            return 0 if forwarded else 1
        return 0

    if _handle_action_args(args):
        core.release_single_instance_mutex()
        _release_singleton_mutex()
        return 0

    # 在窗口显示前同步记录启动前壁纸，避免幻灯片/Bing/视频启动任务抢先改变当前壁纸。
    startup_inherit_wallpaper = bool(getattr(args, "inherit_session_wallpaper", False))

    _set_windows_app_identity()
    used_dpi = apply_dpi_environment(core.config)
    core.log(f"程序内 DPI 缩放: {dpi_percent(used_dpi)}%")
    # Bug 6 fix: sync file-logging runtime state with the explicitly loaded config.
    # ``configure_logging(force=True)`` above already set the
    # correct ``files_enabled`` from settings.json, but we call
    # ``set_file_logging_enabled`` here too as a belt-and-suspenders measure
    # to guarantee the handler state matches ``log_enabled`` even if some
    # intermediate code path toggled it.
    try:
        from app.log_setup import set_file_logging_enabled
        set_file_logging_enabled(bool(core.config.get("log_enabled", False)))
    except Exception:
        pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setApplicationName(APP_PROCESS_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setDesktopFileName(APP_PROCESS_NAME)
    # Install Qt message handler so Qt's own debug/warning/critical messages
    # (e.g. `qt.svg: Cannot open file ...`) are routed into our logging system
    # and show up in the in-app log page instead of being lost to stderr.
    try:
        install_qt_message_handler()
    except Exception as _qt_handler_exc:
        sys.stderr.write(f"[entry] Qt message handler install failed: {_qt_handler_exc}\n")
    _install_qt_chinese_translator(app)
    icon_name = "LOGO.ico" if core.IS_WINDOWS else "LOGO.png"
    icon_path = os.path.join(core.BASE_DIR, "img", icon_name)
    if not os.path.exists(icon_path):
        icon_path = os.path.join(core.BASE_DIR, "img", "LOGO.ico")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
    try:
        if apply_application_font is None:
            raise RuntimeError("application font helper is unavailable")
        chosen_font = apply_application_font(app)
        core.log(f"界面字体: {chosen_font}")
    except Exception as exc:
        core.log(f"界面字体初始化失败: {exc}")

    # Capture the pre-launch desktop before a cold context-menu command can
    # change it. This preserves correct exit restoration even though the action
    # is intentionally executed before the heavy main-window import/build.
    try:
        if not getattr(core, "session_original_wallpaper_captured", False):
            core.capture_session_original_wallpaper(
                inherit_existing=startup_inherit_wallpaper,
                force_refresh=not startup_inherit_wallpaper,
            )
    except Exception as exc:
        core.log(f"启动前壁纸记录失败: {exc}")

    # Previous/next/random/set commands do not need a window. Start their
    # coalescing worker now, in parallel with UI construction, so Explorer's
    # cold-start context menu does not wait for the full Widgets tree.
    cold_context_action_started = False
    pending_context_command = str(
        getattr(core, "pending_startup_context_command", "") or ""
    )
    if pending_context_command and pending_context_command not in {"jump", "show"}:
        core.pending_startup_context_command = None
        core.queue_ipc_wallpaper_command(pending_context_command)
        cold_context_action_started = True
        core.log(f"冷启动壁纸动作已优先提交: {pending_context_command}")

    # Heavy UI modules are imported only after dependency, platform, single-instance
    # and command-line action gates complete.  This keeps CLI actions and duplicate
    # launches fast while preserving the existing UI object graph.
    from ui.main_window import ShangBackgroundWindow
    from ui.qt_root_shim import QtRootShim

    window = ShangBackgroundWindow()
    window._startup_inherit_wallpaper = startup_inherit_wallpaper
    if cold_context_action_started:
        window.set_status(t("已优先响应桌面右键菜单动作"))
    core.root = QtRootShim(window)
    core.canvas = getattr(window, "preview_canvas", None)

    def _handle_local_command(command: str, payload) -> None:
        """Run authenticated IPC commands on the Qt main thread."""
        def _run() -> None:
            try:
                if command == "show":
                    window.showNormal()
                    window.raise_()
                    window.activateWindow()
                elif command == "quit":
                    window.exit_app()
                elif command == "jump":
                    window.open_wallpaper_sidebar()
                elif command == "set_wallpaper":
                    target = os.path.abspath(os.path.expanduser(str(payload or "")))
                    if os.path.isfile(target):
                        core.set_wallpaper(target, t("命令行设置"))
                    else:
                        core.log(f"拒绝不存在的 IPC 壁纸路径: {target}")
                elif command in {"previous", "next", "random"}:
                    core.queue_ipc_wallpaper_command(command)
            except Exception as exc:
                core.log_error(f"本地 IPC 命令执行失败({command})", exc)
        QTimer.singleShot(0, _run)

    local_server = LocalCommandServer(_handle_local_command, app)
    if not local_server.start():
        core.log("本地 IPC 服务启动失败；单实例锁仍有效，但跨进程控制不可用")
    window._local_command_server = local_server
    core.configure_exit_runtime(close_ipc=local_server.close)

    # Cold-started desktop context-menu actions get first event-loop priority.
    # They run before dependency prompts, update checks, hotkey registration and
    # dynamic-wallpaper restoration, while the main window remains hidden.
    pending_context_command = getattr(core, "pending_startup_context_command", None)
    if pending_context_command:
        def _run_pending_context_command_early():
            try:
                command_text = str(getattr(core, "pending_startup_context_command", "") or "")
                core.pending_startup_context_command = None
                if not command_text:
                    return
                if command_text == "jump":
                    window.open_wallpaper_sidebar()
                elif command_text == "show":
                    window.showNormal()
                    window.raise_()
                    window.activateWindow()
                else:
                    core.queue_ipc_wallpaper_command(command_text)
                window.set_status(t("已响应桌面右键菜单动作"))
            except Exception as exc:
                core.log_error("启动后执行桌面右键菜单动作失败", exc)
        QTimer.singleShot(0, _run_pending_context_command_early)

    def _emergency_exit_cleanup(*_args):
        try:
            window._closing_for_exit = True
            window._perform_exit_cleanup_once(
                restore_wallpaper=True, reason="signal_or_about_to_quit"
            )
        except Exception as exc:
            try:
                core.log(f"退出兜底清理失败: {exc}")
            except Exception:
                pass

    try:
        app.aboutToQuit.connect(_emergency_exit_cleanup)
    except Exception:
        pass
    for _sig_name in ("SIGINT", "SIGTERM"):
        try:
            _sig = getattr(signal, _sig_name)
            _old_handler = signal.getsignal(_sig)
            def _handler(signum, frame, _old_handler=_old_handler):
                _emergency_exit_cleanup()
                if callable(_old_handler) and _old_handler not in (signal.SIG_DFL, signal.SIG_IGN):
                    try:
                        _old_handler(signum, frame)
                    except Exception:
                        pass
                try:
                    app.quit()
                except Exception:
                    pass
            signal.signal(_sig, _handler)
        except Exception:
            pass
    def _post_show_runtime_startup():
        # 先让主窗口完成首帧显示；依赖检查、IPC、统计和幻灯片启动都延后，避免抢占 GUI 启动。
        try:
            from app.dependencies import prompt_install_dependencies
            if not prompt_install_dependencies(None, _dependency_availability_for_pyside()):
                window.exit_app()
                return
        except Exception as exc:
            core.log(f"PySide6 依赖检查跳过: {exc}")


        if getattr(args, "sync_context_on_start", False) and core.IS_WINDOWS:
            QTimer.singleShot(250, lambda: window.sync_context_menu(show_message=True, only_if_needed=True))
        if core.IS_WINDOWS:
            QTimer.singleShot(100, core.start_message_window)
        QTimer.singleShot(180, core.report_usage)
        # 初始化全局热键；未选入构建功能时不触发对应后端。
        if is_feature_enabled("hotkeys"):
            QTimer.singleShot(500, lambda: core.refresh_global_hotkeys())
        _startup_mode = normalize_mode_key(core.config.get("mode"))
        if _startup_mode == "幻灯片放映" and core.config.get("slide_folder"):
            def _startup_slideshow():
                core.stop_video_wallpaper()
                return core.start_slideshow(True)
            QTimer.singleShot(600, lambda: window._run_mode_transition(t("正在启动幻灯片放映…"), _startup_slideshow))
        elif is_feature_enabled("video") and _startup_mode == "视频" and core.config.get("video_file"):
            # 上次退出时是视频模式 → 重启后自动恢复视频壁纸。
            # 启动前先做轻量校验，避免无效路径/扩展名在首屏阶段弹错并打断 GUI。
            _video_path = str(core.config.get("video_file") or "")
            if is_supported_video_path(_video_path):
                def _startup_video():
                    if core.is_video_wallpaper_running():
                        core.stop_video_wallpaper()
                    return core.start_video_wallpaper(core.config.get("video_file"))
                QTimer.singleShot(600, lambda: window._run_mode_transition(t("正在启动视频壁纸…"), _startup_video))
            else:
                core.log("跳过启动恢复视频壁纸：视频文件无效或格式不支持")
        elif is_feature_enabled("html") and _startup_mode == "HTML" and core.config.get("html_file"):
            # 上次退出时是 HTML 模式 → 重启后自动恢复 HTML 壁纸。
            # 同样先清理可能残留的旧子进程，再启动新的渲染进程。
            def _startup_html():
                try:
                    if core.is_html_wallpaper_running():
                        core.stop_html_wallpaper()
                except Exception as exc:
                    core.log(f"清理残留 HTML 壁纸进程失败: {exc}")
                return core.start_html_wallpaper(core.config.get("html_file"))
            QTimer.singleShot(600, lambda: window._run_mode_transition(t("正在启动 HTML 壁纸…"), _startup_html))

    # v1.4.6: 三档性能模式 → 启动任务延迟
    _perf_level = str(core.config.get("performance_level", "")).lower()
    if _perf_level not in ("power_saver", "balanced", "performance"):
        # 向后兼容旧 performance_mode 布尔
        _perf_level = "performance" if bool(core.config.get("performance_mode", False)) else "balanced"
    if _perf_level == "power_saver":
        _runtime_delay, _update_delay = 1400, 3400
    elif _perf_level == "performance":
        _runtime_delay, _update_delay = 950, 2600
    else:
        _runtime_delay, _update_delay = 700, 1800
    silent_update_on_start = bool(core.config.get("silent_update_check_on_startup", True))
    schedule_startup_tasks(
        _post_show_runtime_startup,
        getattr(window, "start_startup_update_check", None),
        runtime_delay_ms=_runtime_delay,
        update_delay_ms=max(_update_delay, UPDATE_CHECK_STARTUP_DELAY_MS),
        update_enabled=is_feature_enabled("updates") and UPDATE_CHECK_ON_STARTUP and silent_update_on_start,
    )

    if core.hide_window or args.hide:
        window.hide()
    else:
        try:
            window.prepare_initial_geometry()
        except Exception:
            pass
        window.show()
    code = app.exec()
    if window.tray:
        window.tray.hide()
    window._perform_exit_cleanup_once(
        restore_wallpaper=True, reason="event_loop_return"
    )
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
