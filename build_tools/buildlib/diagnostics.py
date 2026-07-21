from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET

from .bundle import WEBVIEW_MODULES
from .constants import PROJECT_ROOT, ensure_project_layout, host_target
from .plan import BuildPlan


def _module_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def preflight(plan: BuildPlan, *, dry_run: bool) -> tuple[str, ...]:
    ensure_project_layout()
    warnings: list[str] = []
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")
    for source in (PROJECT_ROOT / "src" / "img", PROJECT_ROOT / "src" / "lang"):
        if not source.is_dir():
            raise RuntimeError(f"Missing resource directory: {source}")
    if plan.target != host_target():
        warnings.append("cross-target dry-run: dependency imports and native files are not executable on this host")
    elif not dry_run:
        backend_module = "PyInstaller" if plan.tool == "pyinstaller" else "nuitka"
        if not _module_exists(backend_module):
            raise RuntimeError(f"Build backend is missing: {backend_module}")
        if "html" in plan.features:
            missing = [name for name in WEBVIEW_MODULES[plan.target] if not _module_exists(name)]
            if missing:
                raise RuntimeError("HTML runtime dependencies are missing: " + ", ".join(missing))
    if plan.mode == "onefile":
        warnings.append("onefile is a secondary release mode; validate standalone first")
    return tuple(warnings)


def validate_pyinstaller_output(plan: BuildPlan, contents_directory: str) -> tuple[str, ...]:
    errors: list[str] = []
    if plan.mode == "onefile":
        candidates = list(plan.output_dir.glob("ShangBackground*"))
        if not any(item.is_file() for item in candidates):
            errors.append("PyInstaller onefile executable was not found")
        return tuple(errors)
    app_root = plan.output_dir / "ShangBackground"
    executable = app_root / ("ShangBackground.exe" if plan.target == "windows" else "ShangBackground")
    if not executable.is_file():
        errors.append(f"missing executable: {executable}")
    internal = app_root / contents_directory
    if not internal.is_dir():
        errors.append(f"missing PyInstaller contents directory: {internal}")
        return tuple(errors)
    for relative in ("img", "lang", "build-features.json"):
        if not (internal / relative).exists():
            errors.append(f"missing bundled resource: {internal / relative}")
    if "html" in plan.features:
        webview_dir = internal / "webview"
        if not webview_dir.is_dir():
            errors.append(f"missing pywebview package data under {contents_directory}")
    return tuple(errors)


def validate_nuitka_output(plan: BuildPlan) -> tuple[str, ...]:
    errors: list[str] = []
    if plan.mode == "onefile":
        candidates = list(plan.output_dir.glob("ShangBackground*"))
        if not any(item.is_file() for item in candidates):
            errors.append("Nuitka onefile executable was not found")
        return tuple(errors)
    dist_dirs = sorted(plan.output_dir.glob("*.dist"))
    if not dist_dirs:
        errors.append(f"Nuitka standalone .dist directory was not found under {plan.output_dir}")
        return tuple(errors)
    executable_name = "ShangBackground.exe" if plan.target == "windows" else "ShangBackground"
    matching = [candidate for candidate in dist_dirs if (candidate / executable_name).is_file()]
    if not matching:
        errors.append(
            f"no Nuitka .dist directory contains {executable_name}: "
            + ", ".join(str(item) for item in dist_dirs)
        )
        return tuple(errors)
    dist = matching[0]
    for relative in ("img", "lang", "build-features.json"):
        if not (dist / relative).exists():
            errors.append(f"missing bundled resource: {dist / relative}")
    if "html" in plan.features:
        report = plan.output_dir / "compilation-report.xml"
        if not report.is_file():
            errors.append(f"missing Nuitka compilation report: {report}")
        else:
            try:
                report_root = ET.parse(report).getroot()
            except (OSError, ET.ParseError) as exc:
                errors.append(f"cannot parse Nuitka compilation report: {exc}")
            else:
                if report_root.attrib.get("completion") not in {None, "yes"}:
                    errors.append(
                        "Nuitka compilation report is not complete: "
                        + str(report_root.attrib.get("completion"))
                    )
                included_modules = {
                    str(node.attrib.get("name"))
                    for node in report_root.iter("module")
                    if node.attrib.get("name")
                }
                required_modules = {
                    "windows": (
                        "webview.platforms.winforms",
                        "webview.platforms.win32",
                        "webview.platforms.edgechromium",
                    ),
                    "linux": ("webview.platforms.gtk",),
                    "macos": ("webview.platforms.cocoa",),
                }[plan.target]
                for module in required_modules:
                    if module not in included_modules:
                        errors.append(f"Nuitka report does not contain required HTML module: {module}")
        # Package data should materialize even though compiled Python modules do
        # not remain as source files in a standalone distribution.
        if not (dist / "webview").is_dir():
            errors.append("missing pywebview package data directory")
    return tuple(errors)
