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

    On macOS we prefer `python3 -m pip install --user` because:
    - macOS ships its own Python; using `python3 -m` avoids PEP 668 issues
      with system pip wrappers.
    - `--user` keeps installs in the user site-packages, no sudo needed.
    """
    return [sys.executable, "-m", "pip", "install", "--user", *_dedupe_packages(packages)]


def _format_command(packages: Iterable[str]) -> str:
    try:
        return subprocess.list2cmdline(build_install_command(packages))
    except Exception:
        return " ".join(build_install_command(packages))


def _open_terminal_with_command(command_text: str) -> bool:
    """Open a new Terminal.app window with the install command pre-typed.
    Returns True on success."""
    if sys.platform != "darwin":
        return False
    try:
        # AppleScript to open Terminal and run the command.
        # Using `do script` (not `do shell script`) so a new Terminal window
        # stays open after the command finishes.
        script = (
            f'tell application "Terminal"\n'
            f'  activate\n'
            f'  do script "{command_text.replace(chr(34), chr(92) + chr(34))}"\n'
            f'end tell'
        )
        subprocess.Popen(["osascript", "-e", script])
        return True
    except Exception as exc:
        try:
            from app.log_setup import get_logger
            get_logger("dependencies").warning("open_terminal failed: %s", exc, exc_info=True)
        except Exception:
            pass
        return False


def _try_pyside_prompt(missing, packages) -> bool | None:
    """Show a PySide6 QMessageBox with the install command and two buttons:
    'Copy command' and 'Open Terminal'. Returns None (we never auto-install)."""
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
        t("请在终端执行以下命令安装（推荐 --user 模式）：") + "\n\n" +
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
            info = QMessageBox()
            info.setIcon(QMessageBox.Icon.Information)
            info.setWindowTitle(t("已复制"))
            info.setText(t("安装命令已复制到剪贴板，请粘贴到终端执行。"))
            info.exec()
        except Exception:
            pass
    elif clicked is open_term_btn:
        try:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(command_text)
        except Exception:
            pass
        if not _open_terminal_with_command(command_text):
            info = QMessageBox()
            info.setIcon(QMessageBox.Icon.Information)
            info.setWindowTitle(t("无法自动打开终端"))
            info.setText(
                t("无法自动打开终端。命令已复制到剪贴板，请手动打开 Terminal 并粘贴执行。")
            )
            info.exec()
    return None


def _prompt_in_terminal(missing, packages) -> bool | None:
    command_text = _format_command(packages)
    print(t("缺少运行依赖：") + ", ".join(dep["package"] for dep in missing))
    print(t("请在终端执行（推荐 --user 模式）："))
    print("  " + command_text)
    return None


def prompt_install_dependencies(_notifier, availability, parent=None, prefer_pyside=None):
    """依赖缺失时优先用 PySide6 弹窗（含复制/打开终端按钮），无 Qt 时
    回退到终端打印。`parent` / `prefer_pyside` 参数为了与 Linux 版签名兼容。"""
    missing = get_missing_dependencies(availability)
    if not missing:
        return True

    packages = [dep["package"] for dep in missing]
    required_missing = [dep for dep in missing if dep.get("required")]

    # Try PySide6 dialog first
    result = _try_pyside_prompt(missing, packages)
    if result is not None:
        return result

    # Fallback: terminal
    _prompt_in_terminal(missing, packages)
    if required_missing:
        print(t("缺少必需依赖：") + ", ".join(dep["package"] for dep in required_missing))
        return False
    return True
