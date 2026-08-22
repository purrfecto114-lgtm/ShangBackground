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
from core import local_ipc
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


def _open_path_in_linux_file_manager(path: str) -> tuple[bool, str]:
    """Open or reveal a path using the host Linux file manager."""
    target = os.path.abspath(os.path.expanduser(str(path or "")))
    if not target:
        return False, "路径为空"
    folder = target if os.path.isdir(target) else os.path.dirname(target)
    last_error = "未找到可用的文件管理器"
    if os.path.isfile(target) and shutil.which("dolphin"):
        try:
            subprocess.Popen(["dolphin", "--select", target])
            return True, ""
        except Exception as exc:
            last_error = f"dolphin --select 失败: {exc}"
    if folder and os.path.isdir(folder):
        for opener in (("xdg-open", folder), ("gio", "open", folder)):
            if shutil.which(opener[0]):
                try:
                    subprocess.Popen(list(opener))
                    return True, ""
                except Exception as exc:
                    last_error = f"{' '.join(opener)} 失败: {exc}"
    return False, last_error


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
        getattr(args, "settings", False),
        bool(getattr(args, "set_wallpaper", None)),
        getattr(args, "jump_to_wallpaper", False),
        getattr(args, "from_context_menu", False),
        getattr(args, "quit", False),
    ])


def _parse_early_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--previous", action="store_true")
    parser.add_argument("--next", action="store_true")
    parser.add_argument("--random", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--settings", action="store_true")
    parser.add_argument("--hide", action="store_true")
    parser.add_argument("--quit", action="store_true", help="Request a clean shutdown of the running instance")
    parser.add_argument("--wait-for-exit", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--jump-to-wallpaper", action="store_true")
    parser.add_argument("--set-wallpaper", dest="set_wallpaper")
    parser.add_argument("--from-context-menu", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--context-menu-dispatched-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sync-context-on-start", action="store_true")
    parser.add_argument("--inherit-session-wallpaper", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--relaunch-wait-pid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--relaunch-wait-created-at", type=float, default=0.0, help=argparse.SUPPRESS)
    return parser.parse_known_args()[0]


def _wait_for_relaunch_parent(args: argparse.Namespace, timeout: float = 30.0) -> bool:
    """Wait for the exact relaunching parent before touching singleton state."""
    pid = int(getattr(args, "relaunch_wait_pid", 0) or 0)
    if pid <= 0:
        return True
    expected_created_at = float(getattr(args, "relaunch_wait_created_at", 0.0) or 0.0)
    try:
        import psutil
    except ImportError:
        # psutil is unavailable: we cannot perform the PID-reuse identity check
        # (which requires create_time). Returning True immediately lets the
        # caller proceed with the relaunch handoff rather than blocking on an
        # os.kill poll that cannot distinguish PID reuse — a live process at
        # the same PID might be an unrelated new process, not our parent.
        return True

    # psutil is available — use it for a precise wait with identity check.
    try:
        process = psutil.Process(pid)
        if expected_created_at and abs(float(process.create_time()) - expected_created_at) > 1.0:
            return True  # PID reuse detected: create_time mismatch
        try:
            process.wait(timeout=max(0.1, float(timeout)))
            return True  # Parent exited cleanly
        except psutil.TimeoutExpired:
            return False  # Parent still alive after timeout
        except psutil.NoSuchProcess:
            return True  # Parent exited between checks
    except psutil.NoSuchProcess:
        return True  # Parent already gone
    except Exception as exc:
        # Unexpected psutil error — log and fall back to os.kill polling.
        core.log(f"psutil wait 降级到 os.kill 轮询: {exc}")

    # Best-effort fallback for unexpected psutil errors only (not ImportError).
    import time
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        except OSError:
            return True
        time.sleep(0.05)
    return False


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
                if not core.apply_browsed_wallpaper(path, t("侧边栏切换")):
                    reason = getattr(core, "last_operation_error", "") or t("切换壁纸失败")
                    core.log(f"侧边栏切换壁纸失败: {reason}")
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
    """Return the authenticated local-IPC command represented by CLI args."""
    if getattr(args, "previous", False):
        return "previous"
    if getattr(args, "next", False):
        return "next"
    if getattr(args, "random", False):
        return "random"
    if getattr(args, "set_wallpaper", None):
        return "set_wallpaper"
    if getattr(args, "jump_to_wallpaper", False):
        return "jump"
    if getattr(args, "quit", False):
        return "quit"
    if getattr(args, "show", False):
        return "show"
    if getattr(args, "settings", False):
        return "settings"
    return None


def _context_payload_from_args(args: argparse.Namespace):
    if getattr(args, "set_wallpaper", None):
        return os.path.abspath(os.path.expanduser(str(args.set_wallpaper)))
    return None


def _dispatch_action_to_existing_instance(args: argparse.Namespace) -> bool:
    """Forward a command to the primary GUI through authenticated local IPC.

    The shared QLocalSocket channel is used on every platform.  Windows keeps
    the legacy WM_COPYDATA path only as a compatibility fallback for an older
    already-running build.
    """
    import time

    command = _context_command_from_args(args) or "show"
    payload = _context_payload_from_args(args)
    wait_for_exit = command == "quit" and getattr(args, "wait_for_exit", False)
    # v1.4.4: Reduced from 3×140ms to 2×100ms. The local socket normally
    # answers in <5ms. If the main instance is running, it will respond
    # immediately. The previous 3×140ms=420ms + 500ms WM_COPYDATA fallback
    # = ~920ms was a significant contributor to the 3s perceived delay.
    # A detached Explorer child is allowed to wait for a primary instance that
    # has acquired the single-instance lock but is still constructing its GUI
    # and local IPC server. Explorer itself has already returned, so this retry
    # budget improves reliability without reintroducing shell latency.
    detached_context_child = bool(getattr(args, "context_menu_dispatched_child", False))
    attempts = 40 if (wait_for_exit or detached_context_child) else 2
    for _attempt in range(attempts):
        existing_identity = single_instance.read_identity()
        if local_ipc.send_command(
            command,
            payload,
            timeout_ms=250 if wait_for_exit else 100,
            identity=existing_identity,
        ):
            origin = "桌面右键菜单" if getattr(args, "from_context_menu", False) else "命令行"
            core.log(f"{origin}动作已转发到现有实例: {command}")
            if wait_for_exit:
                return _wait_for_process_exit(existing_identity)
            return True
        time.sleep(0.1 if wait_for_exit else (0.08 if detached_context_child else 0.04))

    # Backward compatibility with a pre-refactor Windows instance.
    # v1.4.4: Reduced timeout from 0.5s to 0.2s.
    if core.IS_WINDOWS:
        try:
            legacy = command
            if command == "set_wallpaper" and payload:
                legacy = "set_wallpaper|" + str(payload)
            existing = core.find_existing_main_window(timeout=0.2)
            return bool(existing and core.send_command_to_hwnd(existing, legacy))
        except Exception as exc:
            core.log(f"转发现有实例动作失败: {exc}")
    return False


def _wait_for_process_exit(identity: dict, timeout: float = 20.0) -> bool:
    """Wait for the exact primary process to terminate after accepting quit."""
    try:
        import psutil
    except Exception as exc:
        core.log(f"等待现有实例退出失败: psutil 不可用: {exc}")
        return False
    try:
        pid = int(identity.get("pid") or 0)
        created_at = float(identity.get("created_at") or 0.0)
        if pid <= 0:
            return False
        process = psutil.Process(pid)
        if created_at and abs(float(process.create_time()) - created_at) > 1.0:
            return True
        try:
            process.wait(timeout=max(0.1, float(timeout)))
            return True
        except psutil.TimeoutExpired:
            core.log(f"等待现有实例退出超时: pid={pid}")
            return False
    except psutil.NoSuchProcess:
        return True
    except Exception as exc:
        core.log(f"等待现有实例退出失败: {exc}")
        return False


def _handle_action_args(args: argparse.Namespace) -> int | None:
    """在 PySide6 GUI 创建前处理右键菜单/命令行动作。

    返回 ``None`` 表示继续启动 GUI；返回整数表示动作已经处理，且该
    整数应直接作为进程退出码。这样命令行/Explorer 调用不会把失败
    伪装成成功。

    Windows 桌面右键菜单在程序关闭时应启动主程序，而不是执行一次
    previous/next/random 后立即退出。因此 from-context-menu 的动作在
    未发现现有实例时交给 GUI 启动后的延迟队列处理。
    """
    if args.hide:
        core.hide_window = True
    origin = "桌面右键菜单" if getattr(args, "from_context_menu", False) else "命令行"
    command = _context_command_from_args(args)
    if getattr(args, "from_context_menu", False) and command and command != "show":
        # Explorer context-menu actions should cold-start into the tray rather
        # than flashing the full window. Preserve a set-wallpaper payload in
        # the same command format understood by the worker queue.
        pending_command = command
        payload = _context_payload_from_args(args)
        if command == "set_wallpaper" and payload:
            pending_command = "set_wallpaper|" + str(payload)
        core.pending_startup_context_command = pending_command
        core.hide_window = True
        core.log(f"{origin}最小化启动主程序并暂存动作: {pending_command}")
        return None
    if command and command != "show":
        core.log(f"{origin}唤起程序动作: {command}")
    if getattr(args, "quit", False):
        # No primary instance exists; there is nothing to stop.
        return 0
    try:
        if args.previous:
            core.previous_wallpaper()
            return 0
        if args.next:
            core.next_wallpaper()
            return 0
        if args.random:
            core.random_wallpaper()
            return 0
        if args.set_wallpaper:
            target = os.path.abspath(os.path.expanduser(str(args.set_wallpaper)))
            if not os.path.isfile(target):
                core.log(f"壁纸文件不存在: {target}")
                return 2
            # A direct set-wallpaper command is a mode change, not merely one
            # static frame painted on top of a still-running video/HTML backend.
            # Route it through the same transactional coordinator as the GUI so
            # dynamic renderers are stopped and failure can compensate back.
            if not core.switch_wallpaper_mode("图片", updates={"single_image": target}):
                return 1
            return 0
        if args.jump_to_wallpaper:
            _open_sidebar_standalone()
            return 0
        if args.show and core.IS_WINDOWS:
            if core.activate_existing_instance(show_notice=False):
                return 0
    except Exception as exc:
        core.log_error(f"{origin}动作执行失败({command or 'unknown'})", exc)
        return 1
    return None


# ---------- 单实例检测 ----------
# 单实例锁在 single_instance.py 中实现：用户级内核文件锁；跨进程命令使用带随机令牌的本地 IPC。
_SINGLE_INSTANCE_MUTEX_NAME = single_instance.APP_MUTEX_NAME


def _activate_existing_window() -> bool:
    """Ask the primary instance to show itself, with a Windows legacy fallback."""
    if local_ipc.send_command("show", timeout_ms=900):
        return True
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
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        if not user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 1)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
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
    from PySide6.QtGui import QFontDatabase
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


def apply_application_font(app) -> str:
    """Apply the configured application font when the Qt runtime is present.

    Keeping this symbol available even when PySide6 is missing prevents direct
    imports from failing with a misleading support-module error; the main
    entry point can then report the actual Qt dependency problem.
    """
    if not PYSIDE_AVAILABLE or app is None:
        return ""
    font_weight_str = str(core.config.get("font_weight", "normal")).lower()
    from PySide6.QtGui import QFont
    weight_map = {
        "normal": QFont.Weight.Normal,
        "medium": QFont.Weight.Medium,
        "bold": QFont.Weight.Bold,
    }
    target_weight = weight_map.get(font_weight_str, QFont.Weight.Normal)
    try:
        target_size = int(core.config.get("font_size", 0))
    except Exception:
        target_size = 0

    candidates = []
    candidates.extend(_iter_font_files(core.config.get("font_path", "")))
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
                font.setWeight(target_weight)
                if target_size > 0:
                    font.setPixelSize(target_size)
                elif current_size > 0:
                    font.setPointSize(current_size)
                app.setFont(font)
                return families[0]
        except Exception as exc:
            core.log(f"字体加载失败: {font_file.name}: {exc}")

    fallback = [
        core.config.get("font_family", ""), "Microsoft YaHei UI",
        "Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
        "Source Han Sans SC", "PingFang SC", "Segoe UI", "Arial",
    ]
    available = set(QFontDatabase.families())
    for family in fallback:
        if family and family in available:
            font = QFont(family)
            font.setWeight(target_weight)
            if target_size > 0:
                font.setPixelSize(target_size)
            elif current_size > 0:
                font.setPointSize(current_size)
            app.setFont(font)
            return family
    return app.font().family()
