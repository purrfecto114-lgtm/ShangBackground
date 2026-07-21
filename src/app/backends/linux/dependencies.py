from __future__ import annotations

import importlib.util
import os
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from app.config import DEPENDENCIES
from app.i18n import t
from app.dependency_warning import (
    dependency_warning_is_suppressed,
    suppress_dependency_warning,
)

def _module_available(module: str) -> bool:
    """Return whether a Python module and its required native runtime are usable.

    A discoverable ``gi`` package is not sufficient for HTML wallpaper: the
    GTK 3 and WebKitGTK 4.1 typelibs must also import successfully.  This avoids
    presenting a false "dependencies installed" result after only PyGObject is
    present.
    """
    if module == "gi":
        try:
            import gi

            gi.require_version("Gtk", "3.0")
            gi.require_version("WebKit2", "4.1")
            from gi.repository import Gtk, WebKit2  # noqa: F401

            return True
        except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
            return False
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        return False


# 当前 Linux 分支只面向 Linux；这里按主流发行版家族做系统包安装建议。
# 覆盖全球排名前 3 的 Linux 发行版：Ubuntu/Debian、Fedora、Arch Linux，同时保留 openSUSE/Alpine 回退。
TOP_LINUX_FAMILIES = ("ubuntu", "debian", "fedora", "arch")

# Heuristic mapping from /etc/os-release ID/ID_LIKE tokens to package manager
PACKAGE_MANAGER_BY_FAMILY = {
    # Debian/Ubuntu family
    "ubuntu": "apt",
    "linuxmint": "apt",
    "pop": "apt",
    "elementary": "apt",
    "zorin": "apt",
    "debian": "apt",
    "raspbian": "apt",
    # Fedora / RHEL family
    "fedora": "dnf",
    "rhel": "dnf",
    "centos": "dnf",
    "rocky": "dnf",
    "almalinux": "dnf",
    # Arch family
    "arch": "pacman",
    "manjaro": "pacman",
    "endeavouros": "pacman",
    # openSUSE / SUSE
    "opensuse": "zypper",
    "suse": "zypper",
    # Alpine uses apk
    "alpine": "apk",
}

SYSTEM_PACKAGE_MAP = {
    "apt": {
        # Debian/Ubuntu use python3-pil for Pillow; python3-pillow is not reliable across releases.
        "pillow": ["python3-pil"],
        # PySide6 is split into Qt modules on Debian/Ubuntu family systems.
        "PySide6-Essentials": [
            "python3-pyside6.qtcore",
            "python3-pyside6.qtgui",
            "python3-pyside6.qtwidgets",
            "python3-pyside6.qtsvg",
        ],
        "PySide6": [
            "python3-pyside6.qtcore",
            "python3-pyside6.qtgui",
            "python3-pyside6.qtwidgets",
            "python3-pyside6.qtsvg",
        ],
        "PyGObject": ["python3-gi", "python3-gi-cairo", "gir1.2-gtk-3.0", "gir1.2-webkit2-4.1"],
        "psutil": ["python3-psutil"],
    },
    "dnf": {
        "pillow": ["python3-pillow"],
        "PySide6-Essentials": ["python3-pyside6"],
        "PySide6": ["python3-pyside6"],
        "PyGObject": ["python3-gobject", "python3-cairo", "gtk3", "webkit2gtk4.1"],
        "psutil": ["python3-psutil"],
    },
    "pacman": {
        "pillow": ["python-pillow"],
        "PySide6-Essentials": ["pyside6"],
        "PySide6": ["pyside6"],
        "PyGObject": ["python-gobject", "python-cairo", "gtk3", "webkit2gtk-4.1"],
        "psutil": ["python-psutil"],
    },
    "zypper": {
        "pillow": ["python3-pillow"],
        "PySide6-Essentials": ["python3-pyside6"],
        "PySide6": ["python3-pyside6"],
        "PyGObject": ["python3-gobject", "python3-gobject-Gdk", "python3-cairo", "gtk3", "webkit2gtk4"],
        "psutil": ["python3-psutil"],
    },
    "apk": {
        "pillow": ["py3-pillow"],
        "PySide6-Essentials": ["py3-pyside6"],
        "PySide6": ["py3-pyside6"],
        "PyGObject": ["py3-gobject3", "py3-cairo", "gtk+3.0", "webkit2gtk-4.1"],
        "psutil": ["py3-psutil"],
    },
}


@dataclass(slots=True)
class InstallPlan:
    command: list[str]
    display_command: str
    manager: str
    distro: str
    system_packages: list[str]
    python_packages: list[str]
    note: str = ""
    followup_commands: tuple[tuple[str, ...], ...] = ()

    def commands(self) -> tuple[tuple[str, ...], ...]:
        primary = (tuple(self.command),) if self.command else ()
        return primary + self.followup_commands


def _read_os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    data[key] = value.strip().strip('"')
            if data:
                break
        except OSError:
            continue
    return data


def detect_linux_family() -> tuple[str, str]:
    """Return (distro_id, package_manager)."""
    data = _read_os_release()
    distro_id = (data.get("ID") or "linux").lower()
    candidates = [distro_id]
    candidates.extend(part.lower() for part in (data.get("ID_LIKE") or "").split())
    for family in candidates:
        manager = PACKAGE_MANAGER_BY_FAMILY.get(family)
        if manager and shutil.which(manager):
            return distro_id, manager
    for manager in ("apt", "dnf", "pacman", "zypper", "apk"):
        if shutil.which(manager):
            return distro_id, manager
    return distro_id, "pip"


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


def _command_display(command: Iterable[str]) -> str:
    try:
        return subprocess.list2cmdline([str(part) for part in command])
    except Exception:
        return " ".join(str(part) for part in command)


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _privilege_prefix() -> list[str]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    if shutil.which("pkexec"):
        return ["pkexec"]
    if shutil.which("sudo"):
        return ["sudo"]
    return []


def _package_manager_command(manager: str, packages: list[str]) -> list[str]:
    prefix = _privilege_prefix()
    if manager == "apt":
        return prefix + ["apt", "install", "-y", *packages]
    if manager == "dnf":
        return prefix + ["dnf", "install", "-y", *packages]
    if manager == "pacman":
        return prefix + ["pacman", "-S", "--needed", "--noconfirm", *packages]
    if manager == "zypper":
        return prefix + ["zypper", "install", "-y", *packages]
    if manager == "apk":
        return prefix + ["apk", "add", *packages]
    # Unknown Linux: do not invent distro package names; fall back to pip with PyPI names.
    return [sys.executable, "-m", "pip", "install", "--user", *packages]


def build_install_plan(packages: Iterable[str]) -> InstallPlan:
    python_packages = _dedupe(packages)
    distro, manager = detect_linux_family()
    system_packages: list[str] = []
    note = ""

    package_map = SYSTEM_PACKAGE_MAP.get(manager, {})
    if package_map:
        unmapped: list[str] = []
        for package in python_packages:
            mapped = package_map.get(package)
            if mapped:
                system_packages.extend(mapped)
            else:
                unmapped.append(package)
        system_packages = _dedupe(system_packages)
        if unmapped:
            note = (
                t("以下依赖已映射到系统包，将使用 {manager} 安装。").format(manager=manager)
                + "\n"
                + t("其余 Python 依赖将在系统包安装成功后通过当前解释器的 pip 安装。")
            )
        else:
            if manager == "apt":
                note = t("如 apt 提示找不到包，请先执行：sudo apt update，并确认已启用 universe/main 仓库。")
            elif manager == "dnf":
                note = t("Fedora/RHEL 系使用 dnf 安装发行版维护的 Python 包。")
            elif manager == "pacman":
                note = t("Arch 系使用 pacman 安装官方仓库包；如使用 AUR 变体，请按发行版文档调整。")
            elif manager == "zypper":
                note = t("openSUSE/SUSE 系使用 zypper 安装发行版维护的 Python 包。")
            elif manager == "apk":
                note = t("Alpine 系使用 apk 安装 py3-* Python 包。")

    command = _package_manager_command(manager, system_packages if system_packages else python_packages)
    mapped_names = {name for name in python_packages if package_map.get(name)}
    unmapped = [name for name in python_packages if name not in mapped_names] if system_packages else []
    followups: tuple[tuple[str, ...], ...] = ()
    if unmapped:
        followups = ((sys.executable, "-m", "pip", "install", "--user", *unmapped),)
    all_commands = [command, *[list(item) for item in followups]]
    display = " &&\n".join(_command_display(item) for item in all_commands if item)
    return InstallPlan(
        command=command,
        display_command=display,
        manager=manager,
        distro=distro,
        system_packages=system_packages,
        python_packages=python_packages,
        note=note,
        followup_commands=followups,
    )


def _try_import_pyside() -> tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any]:
    try:
        from PySide6.QtCore import QTimer  # pyright: ignore[reportMissingImports]
        from PySide6.QtWidgets import (  # pyright: ignore[reportMissingImports]
            QApplication,
            QDialog,
            QDialogButtonBox,
            QLabel,
            QMessageBox,
            QPlainTextEdit,
            QPushButton,
            QVBoxLayout,
        )
        return QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTimer, QVBoxLayout
    except Exception:
        return (None, None, None, None, None, None, None, None, None)


def _install_with_pyside(parent, plan: InstallPlan) -> bool:
    QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTimer, QVBoxLayout = _try_import_pyside()
    if QDialog is None:
        return False

    app = QApplication.instance()
    if app is None:
        return False


    if plan.command and os.path.basename(str(plan.command[0])) in {"pkexec", "sudo"}:
        answer = QMessageBox.question(
            parent,
            t("确认提升权限"),
            t("即将通过系统提权工具安装运行依赖。请确认你信任本软件和下列命令：")
            + "\n\n" + plan.display_command,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

    output_queue: queue.Queue[tuple[str, str | int | None]] = queue.Queue()
    result: dict[str, int | None] = {"returncode": None}

    dlg = QDialog(parent)
    dlg.setWindowTitle(t("安装运行依赖"))
    dlg.resize(760, 460)
    layout = QVBoxLayout(dlg)
    label = QLabel(t("正在执行依赖安装命令：") + "\n" + plan.display_command)
    label.setWordWrap(True)
    layout.addWidget(label)
    log_box = QPlainTextEdit()
    log_box.setReadOnly(True)
    log_box.setPlainText("$ " + plan.display_command + "\n\n")
    layout.addWidget(log_box)
    close_btn = QPushButton(t("关闭"))
    close_btn.setEnabled(False)
    close_btn.clicked.connect(dlg.accept)  # pylint: disable=no-member
    layout.addWidget(close_btn)

    def append_log(text: str) -> None:
        log_box.appendPlainText(text.rstrip("\n"))
        log_box.verticalScrollBar().setValue(log_box.verticalScrollBar().maximum())

    def worker() -> None:
        rc = -1
        try:
            for index, command in enumerate(plan.commands(), start=1):
                if index > 1:
                    output_queue.put(("log", "\n$ " + _command_display(command) + "\n"))
                proc = subprocess.Popen(
                    list(command),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if proc.stdout is not None:
                    for line in proc.stdout:
                        output_queue.put(("log", line))
                rc = proc.wait()
                if rc != 0:
                    break
        except Exception as exc:
            output_queue.put(("log", "\n" + t("安装进程启动失败：") + str(exc) + "\n"))
        result["returncode"] = rc
        output_queue.put(("done", rc))

    def poll_output() -> None:
        while True:
            try:
                kind, payload = output_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                append_log(str(payload))
            elif kind == "done":
                rc = int(payload if payload is not None else -1)
                if rc == 0:
                    append_log("\n" + t("安装完成。"))
                    label.setText(t("依赖安装完成，请重新启动软件。"))
                else:
                    append_log("\n" + t("安装失败，退出码：") + str(rc))
                    label.setText(t("依赖安装失败，请复制命令手动执行。"))
                close_btn.setEnabled(True)
                return
        QTimer.singleShot(100, poll_output)

    threading.Thread(target=worker, daemon=True).start()
    QTimer.singleShot(100, poll_output)
    dlg.exec()
    return result.get("returncode") == 0


def _prompt_with_pyside(parent, missing, plan: InstallPlan) -> bool | None:
    QApplication, _QDialog, _QDialogButtonBox, _QLabel, QMessageBox, _QPlainTextEdit, _QPushButton, _QTimer, _QVBoxLayout = _try_import_pyside()
    if QMessageBox is None or QApplication.instance() is None:
        return None

    names = "\n".join(
        f"- {dep['package']}：{dep['desc']}" + (t("（必需）") if dep.get("required") else t("（建议）"))
        for dep in missing
    )
    extra = ("\n\n" + plan.note) if plan.note else ""
    text = (
        t("检测到缺少运行依赖：") + "\n\n" + names + "\n\n" +
        t("当前 Linux 发行版：") + f" {plan.distro}  /  {plan.manager}\n" +
        t("将执行：") + "\n" + plan.display_command + extra + "\n\n" +
        t("是否现在自动安装？")
    )
    prompt = QMessageBox(parent)
    prompt.setIcon(QMessageBox.Icon.Warning)
    prompt.setWindowTitle(t("运行依赖检查"))
    prompt.setText(text)
    prompt.setStandardButtons(
        QMessageBox.StandardButton.Yes
        | QMessageBox.StandardButton.No
        | QMessageBox.StandardButton.Cancel
    )
    prompt.setDefaultButton(QMessageBox.StandardButton.No)
    suppress_btn = None
    if not any(dep.get("required") for dep in missing):
        suppress_btn = prompt.addButton(t("不再提醒"), QMessageBox.ButtonRole.ActionRole)
    prompt.exec()
    clicked = prompt.clickedButton()
    if suppress_btn is not None and clicked is suppress_btn:
        suppress_dependency_warning(missing)
        return True
    answer = prompt.standardButton(clicked)
    if answer == QMessageBox.StandardButton.Cancel:
        return False
    if answer == QMessageBox.StandardButton.Yes:
        ok = _install_with_pyside(parent, plan)
        if ok:
            QMessageBox.information(parent, t("安装完成"), t("依赖安装完成，请重新启动软件。"))
            return False
        QMessageBox.warning(parent, t("安装失败"), t("依赖安装失败，请在终端手动执行：") + "\n\n" + plan.display_command)
        return False
    # "No" 分支：显示手动安装命令，并提供"复制到剪贴板"按钮
    manual_msg = QMessageBox(parent)
    manual_msg.setIcon(QMessageBox.Icon.Information)
    manual_msg.setWindowTitle(t("手动安装依赖"))
    manual_msg.setText(t("可以在终端执行：") + "\n\n" + plan.display_command + (f"\n\n{plan.note}" if plan.note else ""))
    copy_btn = manual_msg.addButton(t("复制命令"), QMessageBox.ButtonRole.AcceptRole)
    manual_msg.addButton(t("关闭"), QMessageBox.ButtonRole.RejectRole)
    manual_msg.setDefaultButton(copy_btn)
    manual_msg.exec()
    if manual_msg.clickedButton() is copy_btn:
        try:
            from PySide6.QtGui import QGuiApplication  # pyright: ignore[reportMissingImports]
            QGuiApplication.clipboard().setText(plan.display_command)
            # 简短确认
            confirm = QMessageBox(parent)
            confirm.setIcon(QMessageBox.Icon.Information)
            confirm.setWindowTitle(t("已复制"))
            confirm.setText(t("安装命令已复制到剪贴板，请粘贴到终端执行。"))
            confirm.exec()
        except Exception:
            pass
    return None


def _prompt_in_terminal(missing, plan: InstallPlan) -> None:
    print(t("缺少运行依赖："))
    for dep in missing:
        marker = t("必需") if dep.get("required") else t("建议")
        print(f"- {dep['package']} [{marker}] {dep['desc']}")
    print(t("当前 Linux 发行版：") + f" {plan.distro} / {plan.manager}")
    if plan.note:
        print(plan.note)
    print(t("可以在终端执行："))
    print(plan.display_command)


def prompt_install_dependencies(_notifier, availability, parent=None, prefer_pyside=None):
    """Check runtime deps and show one native PySide dialog on Linux."""
    missing = get_missing_dependencies(availability)
    if not missing:
        return True

    packages = [dep["package"] for dep in missing]
    required_missing = [dep for dep in missing if dep.get("required")]
    if not required_missing and dependency_warning_is_suppressed(missing):
        return True
    plan = build_install_plan(packages)

    if prefer_pyside is not False:
        result = _prompt_with_pyside(parent, missing, plan)
        if result is False:
            return False
        if result is True:
            return True
    else:
        _prompt_in_terminal(missing, plan)

    if prefer_pyside is False:
        pass
    elif not required_missing:
        return True
    else:
        _prompt_in_terminal(missing, plan)

    if required_missing:
        print(t("缺少必需依赖：") + ", ".join(dep["package"] for dep in required_missing))
        return False
    return True
