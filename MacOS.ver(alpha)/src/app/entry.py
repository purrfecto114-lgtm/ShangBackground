# Explicit application entry; import-safe before GUI dependencies are installed.
from __future__ import annotations

import os
import signal
import sys

from core import engine as core
from app.config import UPDATE_CHECK_ON_STARTUP, UPDATE_CHECK_STARTUP_DELAY_MS, normalize_mode_key
from app.i18n import t
from app.support import (
    APP_DISPLAY_NAME,
    APP_ORGANIZATION,
    APP_PROCESS_NAME,
    PYSIDE_AVAILABLE,
    PYSIDE_IMPORT_ERROR,
    _dependency_availability_for_pyside,
    _handle_action_args,
    _install_qt_chinese_translator,
    _is_action_launch,
    _is_already_running,
    _parse_early_args,
    _release_singleton_mutex,
)
from app.startup import schedule_startup_tasks
from app.scaling import apply_dpi_environment, dpi_percent

if PYSIDE_AVAILABLE:
    from app.support import apply_application_font
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
else:  # pragma: no cover - names are guarded by PYSIDE_AVAILABLE in main()
    QApplication = QIcon = QMessageBox = QTimer = None

def main() -> int:
    args = _parse_early_args()

    # ---------- 系统版本检查（统一使用 PySide6 QMessageBox） ----------
    if sys.platform != "darwin":
        print("=" * 60, file=sys.stderr)
        print("WARNING: This version of ShangBackground is for macOS only.", file=sys.stderr)
        print(f"Detected system: {sys.platform}", file=sys.stderr)
        print("Continuing may cause errors. Please use the correct platform version.", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        if not PYSIDE_AVAILABLE:
            return 1
        _probe_app = QApplication.instance() or QApplication(sys.argv[:1])
        _install_qt_chinese_translator(_probe_app)
        _result = QMessageBox.question(
            None,
            "ShangBackground — " + str(t("系统不匹配")),
            str(t("当前版本仅适用于 macOS 系统。")) + "\n\n" +
            str(t("检测到当前系统非 macOS，继续运行可能导致异常。")) + "\n\n" +
            str(t("是否仍要继续运行？")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if _result != QMessageBox.StandardButton.Yes:
            return 1

    if not PYSIDE_AVAILABLE:
        print(f"PySide6 不可用：{PYSIDE_IMPORT_ERROR}")
        try:
            from app.dependencies import prompt_install_dependencies
            prompt_install_dependencies(None, _dependency_availability_for_pyside())
        except Exception as exc:
            print(f"依赖提示不可用：{exc}")
        return 2

    is_action_launch = _is_action_launch(args)
    direct_action_launch = (args.previous or args.next or args.random or bool(args.set_wallpaper) or args.jump_to_wallpaper)

    # ---------- 单实例检测（普通权限文件锁 + 回环端口辅助） ----------
    if not direct_action_launch:
        if _is_already_running():
            core.log("检测到已有实例，已阻止重复启动")
            if not is_action_launch:
                core.show_message(t("不要重复运行"), t("不要重复运行，已有主界面正在运行。"))
            return 0

    if _handle_action_args(args):
        core.release_single_instance_mutex()
        _release_singleton_mutex()
        return 0

    # 在窗口显示前同步记录启动前壁纸，避免幻灯片/Bing/视频启动任务抢先改变当前壁纸。
    startup_inherit_wallpaper = bool(getattr(args, "inherit_session_wallpaper", False))

    used_dpi = apply_dpi_environment(core.config)
    core.log(f"程序内 DPI 缩放: {dpi_percent(used_dpi)}%")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setApplicationName(APP_PROCESS_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setDesktopFileName(APP_PROCESS_NAME)
    _install_qt_chinese_translator(app)
    icon_name = "LOGO.ico" if core.IS_WINDOWS else "LOGO.png"
    icon_path = os.path.join(core.BASE_DIR, "img", icon_name)
    if not os.path.exists(icon_path):
        icon_path = os.path.join(core.BASE_DIR, "img", "LOGO.ico")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
    try:
        chosen_font = apply_application_font(app)
        core.log(f"界面字体: {chosen_font}")
    except Exception as exc:
        core.log(f"界面字体初始化失败: {exc}")

    # Heavy UI modules are imported only after dependency, platform, single-instance
    # and command-line action gates complete.  This keeps CLI actions and duplicate
    # launches fast while preserving the existing UI object graph.
    from ui.main_window import ShangBackgroundWindow
    from ui.qt_root_shim import QtRootShim

    window = ShangBackgroundWindow()
    window._startup_inherit_wallpaper = startup_inherit_wallpaper
    core.root = QtRootShim(window)
    core.canvas = None

    def _emergency_exit_cleanup(*_args):
        try:
            window._closing_for_exit = True
            window._perform_exit_cleanup_once(restore_wallpaper=True)
        except Exception as exc:
            try:
                core.log(f"退出兜底清理失败: {exc}")
            except Exception:
                pass

    try:
        app.aboutToQuit.connect(lambda: window._perform_exit_cleanup_once(restore_wallpaper=True))
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
    try:
        if not getattr(core, "session_original_wallpaper_captured", False):
            core.capture_session_original_wallpaper(inherit_existing=startup_inherit_wallpaper, force_refresh=not startup_inherit_wallpaper)
    except Exception as exc:
        core.log(f"启动前壁纸记录失败: {exc}")
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
        _startup_mode = normalize_mode_key(core.config.get("mode"))
        if _startup_mode == "幻灯片放映" and core.config.get("slide_folder"):
            def _startup_slideshow():
                core.stop_video_wallpaper()
                return core.start_slideshow(True)
            QTimer.singleShot(600, lambda: window._run_mode_transition(t("正在启动幻灯片放映…"), _startup_slideshow))
        elif _startup_mode == "视频" and core.config.get("video_file"):
            # 上次退出时是视频模式 → 重启后自动恢复视频壁纸。
            # 若上次播放进程意外残留（is_video_wallpaper_running 为 True），先停掉再重启，
            # 避免 PID 文件指向已死进程或 IPC socket 失效导致音量热更新失效。
            def _startup_video():
                if core.is_video_wallpaper_running():
                    core.stop_video_wallpaper()
                return core.start_video_wallpaper(core.config.get("video_file"))
            QTimer.singleShot(600, lambda: window._run_mode_transition(t("正在启动视频壁纸…"), _startup_video))

    performance_startup = bool(core.config.get("performance_mode", False))
    silent_update_on_start = bool(core.config.get("silent_update_check_on_startup", True))
    schedule_startup_tasks(
        _post_show_runtime_startup,
        getattr(window, "start_startup_update_check", None),
        runtime_delay_ms=950 if performance_startup else 700,
        update_delay_ms=max(2600 if performance_startup else 1800, UPDATE_CHECK_STARTUP_DELAY_MS),
        update_enabled=UPDATE_CHECK_ON_STARTUP and silent_update_on_start,
    )

    if core.hide_window or args.hide:
        window.hide()
    else:
        window.show()
    code = app.exec()
    if window.tray:
        window.tray.hide()
    window._perform_exit_cleanup_once(restore_wallpaper=True)
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
