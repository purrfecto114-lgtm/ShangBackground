from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from .bundle import WEBVIEW_MODULES
from .constants import (
    NUITKA_VERSION,
    PROJECT_ROOT,
    PYINSTALLER_HOOKS_VERSION,
    PYINSTALLER_VERSION,
    PYSIDE6_ESSENTIALS_VERSION,
    PYWEBVIEW_VERSION,
    ensure_project_layout,
    host_target,
    python_executable,
    read_version,
)
from .plan import BuildPlan


def _python_probe(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [python_executable(), "-c", script, *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _module_exists(name: str) -> bool:
    script = (
        "import importlib.util,sys; name=sys.argv[1]; sys.exit(0 if importlib.util.find_spec(name) is not None else 1)"
    )
    return _python_probe(script, name).returncode == 0


def _selected_python_architecture() -> str:
    script = r"""
import platform
import struct

machine = platform.machine().strip().lower().replace('-', '_')
bits = struct.calcsize('P') * 8
if bits == 32:
    print('x86')
elif machine in {'arm64', 'aarch64'}:
    print('arm64')
elif machine in {'amd64', 'x64', 'x86_64'}:
    print('x86_64')
else:
    raise SystemExit(f'unsupported Python architecture: {machine or "unknown"}/{bits}')
"""
    result = _python_probe(script)
    if result.returncode != 0:
        raise RuntimeError(
            "Unable to determine selected build Python architecture: "
            + (result.stderr.strip() or result.stdout.strip() or "unknown error")
        )
    return result.stdout.strip()


def _distribution_version(name: str) -> str | None:
    script = (
        "import importlib.metadata,sys; "
        "name=sys.argv[1]; "
        "\ntry:\n print(importlib.metadata.version(name))\n"
        "except importlib.metadata.PackageNotFoundError:\n sys.exit(1)"
    )
    result = _python_probe(script, name)
    return result.stdout.strip() if result.returncode == 0 else None


def _linux_qt_plugin_path() -> Path | None:
    script = r"""
from pathlib import Path
import PySide6
print(Path(PySide6.__file__).resolve().parent / 'Qt' / 'plugins' / 'platforms' / 'libqxcb.so')
"""
    result = _python_probe(script)
    if result.returncode != 0:
        return None
    candidate = Path(result.stdout.strip())
    return candidate if candidate.is_file() else None


def _validate_linux_build_host(tool: str | None = None) -> None:
    """Fail before compilation when Linux release prerequisites are incomplete."""
    required = ["ldd", "xvfb-run", "Xvfb", "xauth"]
    if tool == "pyinstaller":
        required.extend(("objdump", "objcopy"))
    missing_commands = [name for name in required if not shutil.which(name)]
    if missing_commands:
        package_hint = ""
        if any(name in {"objdump", "objcopy"} for name in missing_commands):
            package_hint = " Install the distro binutils package."
        raise RuntimeError(
            "Linux release build tools are missing: " + ", ".join(missing_commands) + "." + package_hint
        )
    if tool == "nuitka" and not any(shutil.which(name) for name in ("gcc", "clang", "cc", "zig")):
        raise RuntimeError(
            "Nuitka Linux release builds require a C11-capable compiler "
            "(GCC, Clang, or Zig)"
        )
    ldd = shutil.which("ldd")
    assert ldd is not None  # checked in the required-command gate above
    plugin = _linux_qt_plugin_path()
    if plugin is None:
        raise RuntimeError("PySide6 Qt XCB platform plugin is missing from the selected build environment")
    result = subprocess.run(
        [ldd, os.fspath(plugin)],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Unable to inspect the Qt XCB platform plugin: " + result.stdout.strip())
    missing = sorted(
        {line.strip().split(" => ", 1)[0] for line in result.stdout.splitlines() if "=> not found" in line}
    )
    if missing:
        hint = ""
        if "libxcb-cursor.so.0" in missing:
            hint = (
                " Install the Qt XCB cursor runtime first "
                "(Debian/Ubuntu: libxcb-cursor0; Fedora/RHEL: xcb-util-cursor)."
            )
        raise RuntimeError("Linux Qt XCB build prerequisites are unresolved: " + ", ".join(missing) + "." + hint)


def preflight(plan: BuildPlan, *, dry_run: bool) -> tuple[str, ...]:
    ensure_project_layout()
    warnings: list[str] = []
    version_probe = _python_probe("import sys; print('.'.join(map(str, sys.version_info[:3])))")
    if version_probe.returncode != 0:
        raise RuntimeError("Unable to execute the selected build Python: " + version_probe.stderr.strip())
    try:
        python_version = tuple(int(part) for part in version_probe.stdout.strip().split(".")[:3])
    except ValueError as exc:
        raise RuntimeError(f"Unable to parse build Python version: {version_probe.stdout!r}") from exc
    if python_version < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")
    selected_arch = _selected_python_architecture()
    if not dry_run and plan.target == host_target() and selected_arch != plan.arch:
        raise RuntimeError(
            "Build architecture mismatch: "
            f"plan requests {plan.arch}, but {python_executable()} is {selected_arch}. "
            "Use a matching Python interpreter or choose --arch accordingly."
        )
    for source in (PROJECT_ROOT / "src" / "img", PROJECT_ROOT / "src" / "lang"):
        if not source.is_dir():
            raise RuntimeError(f"Missing resource directory: {source}")
    if plan.target != host_target():
        warnings.append(
            "cross-target dry-run only: PyInstaller and Nuitka release artifacts must be built on the target OS"
        )
    elif not dry_run:
        pip_check = subprocess.run(
            [python_executable(), "-m", "pip", "check"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if pip_check.returncode != 0:
            raise RuntimeError("Build environment dependency check failed: " + pip_check.stdout.strip())

        required_modules = ["PySide6", "PIL", "psutil"]
        if "hotkeys" in plan.features and plan.target != "windows":
            required_modules.append("pynput")
        if plan.target == "macos":
            required_modules.extend(("Cocoa", "Quartz"))
            if "video" in plan.features:
                required_modules.append("AVFoundation")
        missing_runtime = [name for name in required_modules if not _module_exists(name)]
        if missing_runtime:
            raise RuntimeError("Runtime dependencies are missing: " + ", ".join(missing_runtime))

        pyside_version = _distribution_version("PySide6-Essentials")
        if pyside_version != PYSIDE6_ESSENTIALS_VERSION:
            raise RuntimeError(
                "PySide6-Essentials version mismatch: "
                f"expected {PYSIDE6_ESSENTIALS_VERSION}, found {pyside_version or 'missing'}"
            )
        if "html" in plan.features:
            webview_version = _distribution_version("pywebview")
            if webview_version != PYWEBVIEW_VERSION:
                raise RuntimeError(
                    f"pywebview version mismatch: expected {PYWEBVIEW_VERSION}, found {webview_version or 'missing'}"
                )
        if plan.target == "linux":
            _validate_linux_build_host(plan.tool)
            warnings.append(
                "Linux release compatibility inherits the build host's glibc; build on the oldest "
                "distribution version you intend to support"
            )

        backend_module = "PyInstaller" if plan.tool == "pyinstaller" else "nuitka"
        if not _module_exists(backend_module):
            raise RuntimeError(f"Build backend is missing from {python_executable()}: {backend_module}")
        expected_version = PYINSTALLER_VERSION if plan.tool == "pyinstaller" else NUITKA_VERSION
        distribution = "PyInstaller" if plan.tool == "pyinstaller" else "Nuitka"
        actual_version = _distribution_version(distribution)
        if actual_version != expected_version:
            raise RuntimeError(
                f"{distribution} version mismatch in {python_executable()}: "
                f"expected {expected_version}, found {actual_version or 'missing'}"
            )
        if plan.tool == "pyinstaller":
            hooks_version = _distribution_version("pyinstaller-hooks-contrib")
            if hooks_version != PYINSTALLER_HOOKS_VERSION:
                raise RuntimeError(
                    "pyinstaller-hooks-contrib version mismatch: "
                    f"expected {PYINSTALLER_HOOKS_VERSION}, found {hooks_version or 'missing'}"
                )
        if "html" in plan.features:
            missing = [name for name in WEBVIEW_MODULES[plan.target] if not _module_exists(name)]
            if missing:
                raise RuntimeError("HTML runtime dependencies are missing: " + ", ".join(missing))
    if plan.mode == "onefile":
        warnings.append("onefile is a secondary release mode; validate standalone first")
    return tuple(warnings)


def _first_existing(candidates: tuple[Path, ...]) -> Path | None:
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def pyinstaller_executable(plan: BuildPlan) -> Path | None:
    root = plan.build_output_dir
    if plan.target == "macos":
        return _first_existing(
            (
                root / "ShangBackground.app" / "Contents" / "MacOS" / "ShangBackground",
                root / "ShangBackground" / "ShangBackground",
                root / "ShangBackground",
            )
        )
    name = "ShangBackground.exe" if plan.target == "windows" else "ShangBackground"
    if plan.mode == "onefile":
        return _first_existing((root / name,))
    return _first_existing((root / "ShangBackground" / name,))


def nuitka_executable(plan: BuildPlan) -> Path | None:
    root = plan.build_output_dir
    if plan.target == "macos":
        direct = root / "ShangBackground.app" / "Contents" / "MacOS" / "ShangBackground"
        if direct.is_file():
            return direct
        candidates = sorted(root.glob("*.app/Contents/MacOS/*"))
        return next((candidate for candidate in candidates if candidate.is_file()), None)
    name = "ShangBackground.exe" if plan.target == "windows" else "ShangBackground"
    if plan.mode == "onefile":
        return _first_existing((root / name,))
    candidates = sorted(root.glob("*.dist"))
    return next((candidate / name for candidate in candidates if (candidate / name).is_file()), None)


def _load_expected_manifest(plan: BuildPlan) -> dict[str, object]:
    try:
        payload = json.loads(plan.manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Generated build manifest is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Generated build manifest root is not an object")
    return payload


def _validate_manifest(candidate: Path, plan: BuildPlan, errors: list[str]) -> None:
    if not candidate.is_file():
        errors.append(f"missing bundled manifest: {candidate}")
        return
    try:
        actual = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot parse bundled manifest {candidate}: {exc}")
        return
    expected = _load_expected_manifest(plan)
    if actual != expected:
        errors.append("bundled build-features.json does not match the selected build plan")


def _validate_linux_shared_dependencies(root: Path, errors: list[str]) -> None:
    """Reject a Linux bundle whose Qt GUI plugin cannot load.

    Running ``ldd`` against the packaged plugin catches unresolved transitive
    dependencies, while the explicit cursor-library check prevents a build
    machine's system copy from making a non-self-contained bundle look healthy.
    Qt 6's XCB backend requires libxcb-cursor, and PyInstaller/Nuitka can only
    collect it when it is present during the build.
    """
    if not shutil.which("ldd"):
        errors.append("ldd is required to validate Linux native dependencies")
        return
    critical = sorted(root.rglob("libqxcb.so"))
    if not critical:
        errors.append("Qt XCB platform plugin is missing from the Linux bundle")
        return
    bundled_names = {item.name for item in root.rglob("libxcb-cursor.so*") if item.is_file()}
    if not any(name.startswith("libxcb-cursor.so.0") for name in bundled_names):
        errors.append(
            "Linux bundle is not self-contained: libxcb-cursor.so.0 is required by Qt XCB but was not collected"
        )
    for binary in critical:
        result = subprocess.run(
            ["ldd", os.fspath(binary)],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            errors.append(f"cannot inspect native dependencies for {binary}: {result.stdout.strip()}")
            continue
        missing = sorted(
            {line.strip().split(" => ", 1)[0] for line in result.stdout.splitlines() if "=> not found" in line}
        )
        if missing:
            errors.append(f"unresolved native dependencies for {binary.name}: " + ", ".join(missing))


def validate_pyinstaller_output(plan: BuildPlan, contents_directory: str) -> tuple[str, ...]:
    errors: list[str] = []
    executable = pyinstaller_executable(plan)
    if executable is None:
        errors.append(f"PyInstaller executable was not found under {plan.build_output_dir}")
        return tuple(errors)
    if plan.mode == "onefile":
        return tuple(errors)
    if plan.target == "macos":
        app = plan.build_output_dir / "ShangBackground.app"
        if not app.is_dir():
            errors.append(f"missing macOS app bundle: {app}")
            return tuple(errors)
        resource_roots = (
            app / "Contents" / "Frameworks",
            app / "Contents" / "Resources",
        )
        for relative in ("img", "lang"):
            if not any((root / relative).exists() for root in resource_roots):
                errors.append(f"missing bundled resource in macOS app: {relative}")
        manifest = next(
            (root / "build-features.json" for root in resource_roots if (root / "build-features.json").is_file()), None
        )
        if manifest is None:
            errors.append("missing bundled build-features.json in macOS app")
        else:
            _validate_manifest(manifest, plan, errors)
        return tuple(errors)
    app_root = plan.build_output_dir / "ShangBackground"
    internal = app_root / contents_directory
    if not internal.is_dir():
        errors.append(f"missing PyInstaller contents directory: {internal}")
        return tuple(errors)
    for relative in ("img", "lang"):
        if not (internal / relative).exists():
            errors.append(f"missing bundled resource: {internal / relative}")
    _validate_manifest(internal / "build-features.json", plan, errors)
    if "html" in plan.features:
        webview_dir = internal / "webview"
        if not webview_dir.is_dir():
            errors.append(f"missing pywebview package data under {contents_directory}")
    if plan.target == "linux":
        _validate_linux_shared_dependencies(app_root, errors)
    return tuple(errors)


def validate_nuitka_output(plan: BuildPlan) -> tuple[str, ...]:
    errors: list[str] = []
    executable = nuitka_executable(plan)
    if executable is None:
        errors.append(f"Nuitka executable was not found under {plan.build_output_dir}")
        return tuple(errors)
    if plan.mode == "onefile":
        return tuple(errors)
    if plan.target == "macos":
        app_root = executable.parents[2]
        resource_roots = (app_root / "Contents" / "Resources", app_root / "Contents" / "MacOS")
        dist = next((root for root in resource_roots if (root / "img").is_dir()), resource_roots[0])
    else:
        dist = executable.parent
    for relative in ("img", "lang"):
        if not (dist / relative).exists():
            errors.append(f"missing bundled resource: {dist / relative}")
    _validate_manifest(dist / "build-features.json", plan, errors)
    report = plan.build_output_dir / "compilation-report.xml"
    if not report.is_file():
        errors.append(f"missing Nuitka compilation report: {report}")
    else:
        try:
            report_root = ET.parse(report).getroot()
        except (OSError, ET.ParseError) as exc:
            errors.append(f"cannot parse Nuitka compilation report: {exc}")
        else:
            if report_root.attrib.get("completion") not in {None, "yes"}:
                errors.append("Nuitka compilation report is not complete: " + str(report_root.attrib.get("completion")))
            if "html" in plan.features:
                included_modules = {
                    str(node.attrib.get("name")) for node in report_root.iter("module") if node.attrib.get("name")
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
    if "html" in plan.features and not (dist / "webview").is_dir():
        errors.append("missing pywebview package data directory")
    if plan.target == "linux":
        _validate_linux_shared_dependencies(dist, errors)
    return tuple(errors)


def validate_frozen_runtime(plan: BuildPlan, executable: Path | None) -> tuple[str, ...]:
    errors: list[str] = []
    if executable is None or not executable.is_file():
        return ("frozen runtime executable is missing",)
    with tempfile.TemporaryDirectory(prefix="shangbackground-build-verify-") as temporary:
        temp_root = Path(temporary)
        report = temp_root / "verification.json"
        environment = os.environ.copy()
        for variable in (
            "PYTHONHOME",
            "PYTHONPATH",
            "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            "QML2_IMPORT_PATH",
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
        ):
            environment.pop(variable, None)
        environment.update(
            {
                "XDG_CONFIG_HOME": os.fspath(temp_root / "xdg"),
                "LOCALAPPDATA": os.fspath(temp_root / "localappdata"),
                "APPDATA": os.fspath(temp_root / "appdata"),
                "HOME": os.fspath(temp_root / "home"),
            }
        )
        command = [os.fspath(executable), "--build-verify-file", os.fspath(report)]
        if plan.target == "linux":
            xvfb_run = shutil.which("xvfb-run")
            if not xvfb_run:
                return ("xvfb-run is required for a real Qt XCB frozen-runtime smoke test",)
            environment["QT_QPA_PLATFORM"] = "xcb"
            command = [xvfb_run, "-a", *command]
        elif plan.target == "macos":
            environment["QT_QPA_PLATFORM"] = "offscreen"
        try:
            result = subprocess.run(
                command,
                cwd=executable.parent,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return (f"frozen runtime verification could not run: {exc}",)
        if result.returncode != 0:
            errors.append(
                f"frozen runtime verification exited with {result.returncode}: "
                + (result.stderr.strip() or result.stdout.strip() or "no output")
            )
        if not report.is_file():
            errors.append("frozen runtime did not create its verification report")
            return tuple(errors)
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"frozen runtime verification report is invalid: {exc}")
            return tuple(errors)
        if payload.get("schema") != 1:
            errors.append(f"unexpected frozen verification schema: {payload.get('schema')!r}")
        if payload.get("app_version") != read_version():
            errors.append(
                f"frozen runtime version mismatch: expected {read_version()}, found {payload.get('app_version')!r}"
            )
        if payload.get("platform") != plan.target:
            errors.append(
                f"frozen runtime platform mismatch: expected {plan.target}, found {payload.get('platform')!r}"
            )
        if payload.get("architecture") != plan.arch:
            errors.append(
                f"frozen runtime architecture mismatch: expected {plan.arch}, found {payload.get('architecture')!r}"
            )
        if payload.get("packaged") is not True:
            errors.append("frozen executable did not identify itself as a packaged runtime")
        resource_root = payload.get("resource_root")
        try:
            reported_root = Path(str(resource_root)).resolve(strict=True)
            # The "packaged application" root depends on the bundle layout:
            #   - Linux/Windows standalone: <root>/ShangBackground/  (executable.parent)
            #   - macOS .app bundle:        <root>/ShangBackground.app/
            #     The executable lives at Contents/MacOS/ShangBackground, but the
            #     runtime legitimately reports resource roots under
            #     Contents/Frameworks or Contents/Resources, which are siblings
            #     of Contents/MacOS. Walking up two levels gives us the .app
            #     bundle directory, which is the actual packaged application.
            if plan.target == "macos":
                packaged_root = executable.parents[2].resolve(strict=True)
            else:
                packaged_root = executable.parent.resolve(strict=True)
        except (OSError, RuntimeError):
            errors.append(f"frozen runtime resource root is invalid: {resource_root!r}")
        else:
            if reported_root != packaged_root and not reported_root.is_relative_to(packaged_root):
                errors.append(
                    f"frozen runtime resource root escapes the packaged application: {reported_root}"
                )
        enabled = payload.get("enabled_features")
        if not isinstance(enabled, list) or set(map(str, enabled)) != set(plan.features):
            errors.append(f"frozen runtime feature set mismatch: expected {sorted(plan.features)}, found {enabled!r}")
        expected_manifest = _load_expected_manifest(plan)
        if payload.get("html_runtime") != expected_manifest.get("html_runtime"):
            errors.append("frozen runtime HTML mode does not match the build manifest")
        expected_video = expected_manifest.get("video_runtime")
        if payload.get("video_runtime") != expected_video:
            errors.append("frozen runtime video mode does not match the build manifest")
        qt_smoke = payload.get("qt_smoke")
        if not isinstance(qt_smoke, dict) or qt_smoke.get("ok") is not True:
            errors.append(f"frozen Qt startup smoke test failed: {qt_smoke!r}")
        elif plan.target == "linux" and qt_smoke.get("platform_plugin") != "xcb":
            errors.append(
                "frozen Linux Qt smoke test did not load the XCB platform plugin: "
                f"{qt_smoke.get('platform_plugin')!r}"
            )
        diagnostics = payload.get("diagnostics")
        if not isinstance(diagnostics, dict) or diagnostics.get("healthy") is not True:
            errors.append("frozen runtime diagnostics reported missing required components")
    return tuple(errors)
