from __future__ import annotations

import importlib.util
import subprocess
import sys
from typing import Iterable

from app.config import DEPENDENCIES
from app.i18n import t


def _module_available(module: str) -> bool:
    """Return False for missing top-level packages and missing submodules.

    importlib.util.find_spec("pkg.submodule") can raise ModuleNotFoundError
    when the parent package itself is absent. Dependency probing should report
    the package as missing, not crash the startup dependency dialog.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        return False


def get_missing_dependencies(availability):
    missing = []
    availability = availability or {}
    for dep in DEPENDENCIES:
        module = dep["module"]
        installed = availability.get(module)
        if installed is None:
            installed = _module_available(module)
        if not installed:
            missing.append(dep)
    return missing


def _dedupe_packages(packages: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for package in packages:
        package = str(package or "").strip()
        if package and package not in seen:
            seen.add(package)
            result.append(package)
    return result


def build_install_command(packages: Iterable[str]):
    """Return the full pip install command as a list.

    On Windows we prefer `py -m pip install --user` because:
    - `py` (the Python launcher) is more reliable than `python` on PATH.
    - `--user` avoids system-wide installs and permission issues.
    """
    py = "py" if sys.platform.startswith("win") else sys.executable
    return [py, "-m", "pip", "install", "--user", *_dedupe_packages(packages)]


def _format_command(packages: Iterable[str]) -> str:
    try:
        return subprocess.list2cmdline(build_install_command(packages))
    except Exception:
        return " ".join(build_install_command(packages))


def _open_terminal_with_command(command_text: str) -> bool:
    """Open a new terminal window (cmd.exe or pwsh.exe) with the install
    command pre-typed and ready to run. Returns True on success."""
    if not sys.platform.startswith("win"):
        return False
    try:
        # Prefer Windows Terminal (wt.exe) if available; fall back to cmd.exe.
        wt = shutil_which("wt.exe") if shutil_which else None
        # Quote the command for safe shell injection. Use ^& to chain in cmd.
        # In wt.exe we can pass `cmd /k "<command>"`.
        if wt:
            subprocess.Popen(
                [wt, "cmd.exe", "/k", command_text],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        else:
            subprocess.Popen(
                ["cmd.exe", "/k", command_text],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        return True
    except Exception as exc:
        try:
            from app.log_setup import get_logger
            get_logger("dependencies").warning("open_terminal failed: %s", exc, exc_info=True)
        except Exception:
            pass
        return False


def shutil_which(name: str):
    """Lazy shutil.which to keep top-level imports minimal."""
    try:
        import shutil
        return shutil.which(name)
    except Exception:
        return None


def _try_pyside_prompt(missing, packages) -> bool | None:
    """Show a PySide6 QMessageBox with the install command and two buttons:
    'Copy command' and 'Open terminal'. Returns None (we never auto-install).
    """
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception:
        return None

    app = QApplication.instance()
    if app is None:
        return None

    command_text = _format_command(packages)
    names = "\n".join(
        f"  • {dep['package']} — {dep['desc']}" + (t("（必需）") if dep.get("required") else t("（可选）"))
        for dep in missing
    )

    msg = QMessageBox()
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setWindowTitle(t("运行依赖缺失"))
    msg.setText(
        t("检测到缺少运行依赖：") + "\n\n" + names + "\n\n" +
        t("请在终端执行以下命令安装（推荐 --user 模式，避免权限问题）：") + "\n\n" +
        command_text
    )
    copy_btn = msg.addButton(t("复制命令"), QMessageBox.ButtonRole.AcceptRole)
    open_term_btn = msg.addButton(t("打开终端并粘贴"), QMessageBox.ButtonRole.ActionRole)
    msg.addButton(t("关闭"), QMessageBox.ButtonRole.RejectRole)
    msg.setDefaultButton(copy_btn)

    msg.exec()
    clicked = msg.clickedButton()

    if clicked is copy_btn:
        try:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(command_text)
            # Show a tiny confirmation
            info = QMessageBox()
            info.setIcon(QMessageBox.Icon.Information)
            info.setWindowTitle(t("已复制"))
            info.setText(t("安装命令已复制到剪贴板，请粘贴到终端执行。"))
            info.exec()
        except Exception as exc:
            try:
                from app.log_setup import get_logger
                get_logger("dependencies").warning("clipboard copy failed: %s", exc, exc_info=True)
            except Exception:
                pass
    elif clicked is open_term_btn:
        # Copy first so user can paste in the new terminal
        try:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(command_text)
        except Exception:
            pass
        if not _open_terminal_with_command(command_text):
            # Fallback: show a message telling user to copy and run manually
            info = QMessageBox()
            info.setIcon(QMessageBox.Icon.Information)
            info.setWindowTitle(t("无法自动打开终端"))
            info.setText(
                t("无法自动打开终端。命令已复制到剪贴板，请手动打开 cmd/PowerShell 并粘贴执行。")
            )
            info.exec()
    return None


def _prompt_in_gui_fallback(missing, packages) -> bool | None:
    """Best-effort tkinter notice when Qt itself is missing.

    This intentionally uses tkinter only as a last-resort message box so users
    who launch the .pyw/.exe are not left with a silent exit.
    """
    try:
        import tkinter as tk
        from tkinter import messagebox

        command_text = _format_command(packages)
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "ShangBackground 依赖缺失",
            t("缺少运行依赖：") + "\n"
            + ", ".join(dep["package"] for dep in missing)
            + "\n\n"
            + t("请在终端执行（推荐 --user 模式）：") + "\n"
            + command_text,
        )
        root.destroy()
        return None
    except Exception:
        return None


def _prompt_in_terminal(missing, packages) -> bool | None:
    command_text = _format_command(packages)
    print(t("缺少运行依赖：") + ", ".join(dep["package"] for dep in missing))
    print(t("请在终端执行（推荐 --user 模式）："))
    print("  " + command_text)
    return None


def prompt_install_dependencies(_notifier, availability, parent=None, prefer_pyside=None):
    """依赖缺失时优先用 PySide6 弹窗（含复制/打开终端按钮），无 Qt 时
    回退到 tkinter，最后回退到终端打印。

    `parent` / `prefer_pyside` 为三端入口兼容参数；Windows 当前不需要
    使用它们，但保留签名可以避免平台入口同步时出现 TypeError。
    """
    del parent, prefer_pyside
    missing = get_missing_dependencies(availability)
    if not missing:
        return True

    packages = [dep["package"] for dep in missing]
    required_missing = [dep for dep in missing if dep.get("required")]

    # Try PySide6 dialog first (with copy + open-terminal buttons)
    result = _try_pyside_prompt(missing, packages)
    if result is not None:
        return result

    # Fallback chain: tkinter → terminal
    _prompt_in_terminal(missing, packages)
    _prompt_in_gui_fallback(missing, packages)
    if required_missing:
        print(t("缺少必需依赖：") + ", ".join(dep["package"] for dep in required_missing))
        return False
    return True
