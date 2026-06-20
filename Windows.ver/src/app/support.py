# Auto-split support module. Do not run directly.
# ShangBackground PySide6 主入口
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path

from core import engine as core
from core import single_instance
from app.i18n import t, init_i18n, get_language
from app.paths import TRANSLATIONS_DIR, font_directories, image_path
# Load configured UI language before any translated constants/widgets are created.
init_i18n(core.config)

# ---------- 版本号 ----------
APP_VERSION = "1.3.6"
APP_ID = "xxdz.ShangBackground"
APP_PROCESS_NAME = "ShangBackground"
APP_DISPLAY_NAME = t("上一个桌面背景")
APP_ORGANIZATION = t("XXDZ工作室")
core.VERSION = APP_VERSION


def _set_windows_app_identity() -> None:
    """设置 Windows AppUserModelID，避免任务栏/通知区域沿用 python.exe 身份。"""
    if not core.IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(APP_DISPLAY_NAME)
    except Exception:
        pass



def _is_action_launch(args: argparse.Namespace) -> bool:
    return any([
        getattr(args, "previous", False),
        getattr(args, "next", False),
        getattr(args, "random", False),
        getattr(args, "show", False),
        bool(getattr(args, "set_wallpaper", None)),
        getattr(args, "jump_to_wallpaper", False),
    ])


def _parse_early_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--previous", action="store_true")
    parser.add_argument("--next", action="store_true")
    parser.add_argument("--random", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--hide", action="store_true")
    parser.add_argument("--jump-to-wallpaper", action="store_true")
    parser.add_argument("--set-wallpaper", dest="set_wallpaper")
    parser.add_argument("--sync-context-on-start", action="store_true")
    parser.add_argument("--inherit-session-wallpaper", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_known_args()[0]


def _open_sidebar_standalone() -> None:
    """
    独立进程模式（由 --jump-to-wallpaper 触发）：
    创建最小 QApplication → 显示 PySide6 侧边栏 → exec → 退出。
    此函数在 QApplication 创建之前可安全调用。
    """
    import sys as _sys
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    folder = core.config.get("slide_folder", "")
    current = core.config.get("current_wallpaper", "") or core.get_current_wallpaper()

    _set_windows_app_identity()
    _app = QApplication.instance() or QApplication(_sys.argv)
    _app.setOrganizationName(APP_ORGANIZATION)
    _app.setApplicationName(APP_PROCESS_NAME)
    _app.setApplicationDisplayName(APP_DISPLAY_NAME)
    _install_qt_chinese_translator(_app)
    icon_path = image_path("LOGO.ico")
    if os.path.exists(icon_path):
        _app.setWindowIcon(QIcon(icon_path))

    if not folder or not os.path.isdir(folder):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, t("提示"), t("请先在软件中设置壁纸文件夹"))
        return

    try:
        from ui.sidebar import WallpaperSidebar

        def _switch(path: str) -> None:
            try:
                core.set_wallpaper(path, t("侧边栏切换"))
            except Exception as exc:
                core.log(f"侧边栏切换壁纸失败: {exc}")

        sidebar_log = core.config.get("log_file_path") if core.config.get("log_enabled", False) else None
        sidebar = WallpaperSidebar(
            None, folder, current, sidebar_log,
            show_message=lambda t, m: None,
            switch_wallpaper=_switch,
        )
        # 侧边栏关闭时退出独立 QApplication
        sidebar.closed.connect(_app.quit)
        _app.exec()

    except Exception as exc:
        core.log(f"打开侧边栏失败: {exc}")
        import traceback
        core.log(traceback.format_exc())


def _handle_action_args(args: argparse.Namespace) -> bool:
    """在 PySide6 GUI 创建前处理右键菜单/命令行动作。"""
    if args.hide:
        core.hide_window = True
    if args.previous:
        core.previous_wallpaper()
        return True
    if args.next:
        core.next_wallpaper()
        return True
    if args.random:
        core.random_wallpaper()
        return True
    if args.set_wallpaper:
        target = args.set_wallpaper
        if os.path.isfile(target):
            core.set_wallpaper(target, t("命令行设置"))
        else:
            core.log(f"壁纸文件不存在: {target}")
        return True
    if args.jump_to_wallpaper:
        _open_sidebar_standalone()
        return True
    if args.show and core.IS_WINDOWS:
        if core.activate_existing_instance(show_notice=False):
            return True
    return False


# ---------- 单实例检测 ----------
# 单实例锁在 single_instance.py 中实现：用户级 PID 锁文件 + 本机回环端口锁，普通权限即可工作。
_SINGLE_INSTANCE_MUTEX_NAME = single_instance.APP_MUTEX_NAME


def _activate_existing_window() -> bool:
    """激活已运行的主窗口；若窗口被隐藏到托盘则强制显示。"""
    if not core.IS_WINDOWS:
        return False
    try:
        if core.activate_existing_instance(show_notice=False):
            return True
    except Exception:
        pass
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(core.WND_CLASS_NAME, None)
        if not hwnd:
            hwnd = user32.FindWindowW(None, APP_DISPLAY_NAME)
        if not hwnd:
            hwnd = user32.FindWindowW(None, "ShangBackground")
        if not hwnd:
            return False
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        attached = False
        if current_thread != target_thread:
            try:
                attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
            except Exception:
                attached = False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if not user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        if attached:
            try:
                user32.AttachThreadInput(current_thread, target_thread, False)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _is_already_running() -> bool:
    """普通权限单实例检测。"""
    return not single_instance.acquire()


def _release_singleton_mutex():
    """释放单实例守卫。"""
    try:
        single_instance.release()
    except Exception:
        pass


PYSIDE_IMPORT_ERROR = None

try:
    from PySide6.QtCore import QTranslator, QLibraryInfo, QLocale
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import (
        QApplication,
    )
    PYSIDE_AVAILABLE = True
except Exception as exc:  # pragma: no cover - 运行环境缺 PySide6 时回退
    PYSIDE_AVAILABLE = False
    PYSIDE_IMPORT_ERROR = exc


_QT_TRANSLATORS = []


def _install_qt_chinese_translator(app) -> None:
    """Load Qt Chinese translations only when the app UI is Chinese."""
    if not PYSIDE_AVAILABLE or app is None or get_language() != "zh":
        return
    try:
        QLocale.setDefault(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
    except Exception:
        pass
    try:
        paths = []
        try:
            paths.append(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
        except Exception:
            pass
        paths.extend([
            os.fspath(TRANSLATIONS_DIR),
        ])
        for base_name in ("qtbase_zh_CN", "qt_zh_CN"):
            for path in paths:
                if not path:
                    continue
                translator = QTranslator(app)
                if translator.load(base_name, path):
                    app.installTranslator(translator)
                    _QT_TRANSLATORS.append(translator)
                    break
    except Exception as exc:
        try:
            core.log(f"Qt 中文翻译加载失败: {exc}")
        except Exception:
            pass


def _dependency_availability_for_pyside() -> dict:
    """供 PySide6 主入口使用的依赖可用性表。未列出的依赖由 app.dependencies 自行探测。"""
    return {
        "PIL": getattr(core, "Image", None) is not None,
        "requests": getattr(core, "requests", None) is not None,
        "PySide6": PYSIDE_AVAILABLE,
        "psutil": getattr(core, "psutil", None) is not None,
        "httpx": importlib.util.find_spec("httpx") is not None,
    }


if PYSIDE_AVAILABLE:






    def _iter_font_files(path: str):
        if not path:
            return []
        target = Path(path).expanduser()
        if target.is_file() and target.suffix.lower() in {".ttf", ".ttc", ".otf"}:
            return [target]
        if target.is_dir():
            files = []
            for suffix in ("*.ttf", "*.ttc", "*.otf"):
                files.extend(target.glob(suffix))
            return sorted(files)
        return []


    def apply_application_font(app: QApplication) -> str:
        """应用自定义字体文件/目录；显示大小由程序内 DPI 统一控制。"""
        if app is None:
            return ""
        candidates = []
        custom_path = core.config.get("font_path", "")
        candidates.extend(_iter_font_files(custom_path))
        for font_dir in font_directories():
            candidates.extend(_iter_font_files(os.fspath(font_dir)))

        current_size = app.font().pointSize() if app.font().pointSize() > 0 else -1
        for font_file in candidates:
            try:
                font_id = QFontDatabase.addApplicationFont(str(font_file))
                if font_id < 0:
                    continue
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    font = QFont(families[0])
                    if current_size > 0:
                        font.setPointSize(current_size)
                    app.setFont(font)
                    return families[0]
            except Exception as exc:
                core.log(f"字体加载失败: {font_file.name}: {exc}")

        fallback = [
            core.config.get("font_family", ""),
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "SimHei",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
            "PingFang SC",
            "Segoe UI",
            "Arial",
        ]
        available = set(QFontDatabase.families())
        for family in fallback:
            if family and family in available:
                font = QFont(family)
                if current_size > 0:
                    font.setPointSize(current_size)
                app.setFont(font)
                return family
        return app.font().family()
