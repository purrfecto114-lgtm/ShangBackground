# Auto-split support module. Do not run directly.
# ShangBackground PySide6 主入口
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from core import engine as core
from core import single_instance
from app.i18n import t, init_i18n, get_language
from app.config import APP_VERSION as CONFIG_APP_VERSION
from app.paths import TRANSLATIONS_DIR, font_directories, image_path
# Load configured UI language before any translated constants/widgets are created.
init_i18n(core.config)

# ---------- 版本号 ----------
APP_VERSION = CONFIG_APP_VERSION
APP_ID = "xxdz.ShangBackground"
APP_PROCESS_NAME = "ShangBackground"
APP_DISPLAY_NAME = t("上一个桌面背景")
APP_ORGANIZATION = t("XXDZ工作室")
core.VERSION = APP_VERSION


def _is_action_launch(args: argparse.Namespace) -> bool:
    return any([
        getattr(args, "previous", False),
        getattr(args, "next", False),
        getattr(args, "random", False),
        getattr(args, "show", False),
        bool(getattr(args, "set_wallpaper", None)),
        getattr(args, "jump_to_wallpaper", False),
        getattr(args, "from_context_menu", False),
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
    parser.add_argument("--from-context-menu", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sync-context-on-start", action="store_true")
    parser.add_argument("--inherit-session-wallpaper", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_known_args()[0]


def _is_linux_wayland_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def _open_path_in_linux_file_manager(path: str) -> tuple[bool, str]:
    target = os.path.abspath(os.path.expanduser(str(path or "")))
    if not target:
        return False, "路径为空"
    folder = target if os.path.isdir(target) else os.path.dirname(target)
    if os.path.isfile(target) and shutil.which("dolphin"):
        try:
            subprocess.Popen(["dolphin", "--select", target])
            return True, ""
        except Exception as exc:
            last_error = f"dolphin --select 失败: {exc}"
    else:
        last_error = "未找到 Dolphin 或目标不是文件"
    if folder and os.path.isdir(folder):
        for opener in (("xdg-open", folder), ("gio", "open", folder)):
            if shutil.which(opener[0]):
                try:
                    subprocess.Popen(list(opener))
                    return True, ""
                except Exception as exc:
                    last_error = f"{' '.join(opener)} 失败: {exc}"
    return False, last_error


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

    # Bug 4 fix: 删除 Wayland 短路（之前会打开文件管理器代替 sidebar）。
    # WallpaperSidebar 现在在 Wayland 下使用 Qt.Popup 窗口标志工作正常。

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


def _context_command_from_args(args: argparse.Namespace) -> str | None:
    """Return the IPC command represented by CLI/context-menu action args."""
    if getattr(args, "previous", False):
        return "previous"
    if getattr(args, "next", False):
        return "next"
    if getattr(args, "random", False):
        return "random"
    target = getattr(args, "set_wallpaper", None)
    if target:
        return "set_wallpaper|" + str(target)
    if getattr(args, "jump_to_wallpaper", False):
        return "jump"
    if getattr(args, "show", False):
        return "show"
    return None


def _dispatch_action_to_existing_instance(args: argparse.Namespace) -> bool:
    """Forward a CLI/context-menu action to an already-running Windows GUI instance."""
    command = _context_command_from_args(args)
    if not command or not core.IS_WINDOWS:
        return False
    try:
        existing = core.find_existing_main_window(timeout=0.8)
        if not existing:
            return False
        ok = core.send_command_to_hwnd(existing, command)
        if ok:
            origin = "桌面右键菜单" if getattr(args, "from_context_menu", False) else "命令行"
            core.log(f"{origin}动作已转发到现有实例: {command}")
        return bool(ok)
    except Exception as exc:
        core.log(f"转发现有实例动作失败: {exc}")
        return False


def _handle_action_args(args: argparse.Namespace) -> bool:
    """在 PySide6 GUI 创建前处理右键菜单/命令行动作。

    Windows 桌面右键菜单在程序关闭时应启动主程序，而不是执行一次
    previous/next/random 后立即退出。因此 from-context-menu 的动作在
    未发现现有实例时交给 GUI 启动后的延迟队列处理。
    """
    if args.hide:
        core.hide_window = True
    origin = "桌面右键菜单" if getattr(args, "from_context_menu", False) else "命令行"
    command = _context_command_from_args(args)
    if getattr(args, "from_context_menu", False) and command and command != "show":
        core.pending_startup_context_command = command
        core.log(f"{origin}启动主程序并暂存动作: {command}")
        return False
    if command and command != "show":
        core.log(f"{origin}唤起程序动作: {command}")
    try:
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
    except Exception as exc:
        core.log_error(f"{origin}动作执行失败({command or 'unknown'})", exc)
        return True
    return False


# ---------- 单实例检测 ----------
# 单实例锁在 single_instance.py 中实现：用户级 PID 锁文件 + 本机回环端口锁，普通权限即可工作。
_SINGLE_INSTANCE_MUTEX_NAME = single_instance.APP_MUTEX_NAME




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
    from PySide6.QtGui import QFontDatabase
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
        "PySide6": PYSIDE_AVAILABLE,
        "psutil": getattr(core, "psutil", None) is not None,
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
        """应用自定义字体文件/目录；显示大小由程序内 DPI 统一控制.

        v1.4.7: 支持 font_weight (normal/medium/bold) 和 font_size (0=系统默认, 否则 px).
        """
        if app is None:
            return ""
        # v1.4.7: 读取字体粗细和大小配置
        font_weight_str = str(core.config.get("font_weight", "normal")).lower()
        from PySide6.QtGui import QFont
        weight_map = {
            "normal": QFont.Weight.Normal,
            "medium": QFont.Weight.Medium,
            "bold": QFont.Weight.Bold,
        }
        target_weight = weight_map.get(font_weight_str, QFont.Weight.Normal)
        target_size = 0
        try:
            target_size = int(core.config.get("font_size", 0))
        except Exception:
            target_size = 0

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
                    font.setWeight(target_weight)  # v1.4.7
                    if target_size > 0:
                        font.setPixelSize(target_size)  # v1.4.7
                    elif current_size > 0:
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
                font.setWeight(target_weight)  # v1.4.7
                if target_size > 0:
                    font.setPixelSize(target_size)  # v1.4.7
                elif current_size > 0:
                    font.setPointSize(current_size)
                app.setFont(font)
                return family
        return app.font().family()
