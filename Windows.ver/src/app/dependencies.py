from __future__ import annotations

import importlib.util
import subprocess
import sys
from typing import Iterable

from app.config import DEPENDENCIES
from app.i18n import t


def get_missing_dependencies(availability):
    missing = []
    availability = availability or {}
    for dep in DEPENDENCIES:
        module = dep["module"]
        installed = availability.get(module)
        if installed is None:
            installed = importlib.util.find_spec(module) is not None
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
    return [sys.executable, "-m", "pip", "install", *_dedupe_packages(packages)]


def _format_command(packages: Iterable[str]) -> str:
    try:
        return subprocess.list2cmdline(build_install_command(packages))
    except Exception:
        return " ".join(build_install_command(packages))




def _prompt_in_terminal(missing, packages) -> bool | None:
    command_text = _format_command(packages)
    print(t("缺少运行依赖：") + ", ".join(dep["package"] for dep in missing))
    print(t("可以在终端执行：") + command_text)
    return None


def prompt_install_dependencies(_notifier, availability):
    """依赖缺失时只输出终端提示；主 GUI 统一使用 PySide6 QMessageBox。"""
    missing = get_missing_dependencies(availability)
    if not missing:
        return True

    packages = [dep["package"] for dep in missing]
    required_missing = [dep for dep in missing if dep.get("required")]
    _prompt_in_terminal(missing, packages)
    if required_missing:
        print(t("缺少必需依赖：") + ", ".join(dep["package"] for dep in required_missing))
        return False
    return True
