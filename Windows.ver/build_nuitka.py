#!/usr/bin/env python3
"""Clean, reproducible Nuitka 4.1.3 builder for one ShangBackground platform tree."""
from __future__ import annotations

import argparse
import codecs
import ctypes
import datetime as dt
import json
import importlib.metadata
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import queue
import re
import time
from typing import Iterable

NUITKA_VERSION = "4.1.3"
APP_NAME = "ShangBackground"
PROFILES = ("lite", "full", "system")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default="full",
        help="lite=no HTML engine; full=Qt WebEngine; system=legacy alias of full",
    )
    parser.add_argument("--mode", choices=("standalone", "onefile"), default="standalone")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate source and print the command only")
    parser.add_argument("--jobs", type=int, default=2, help="C compiler jobs; conservative default for Qt WebEngine builds")
    parser.add_argument(
        "--windows-console-mode",
        choices=("disable", "force", "attach", "hide"),
        default="disable",
        help="Windows executable mode. 'disable' is the normal GUI build; use 'force' while debugging.",
    )
    parser.add_argument(
        "--build-heartbeat",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Print a liveness message when Nuitka produces no output (0 disables it).",
    )
    parser.add_argument(
        "--stall-warning",
        type=int,
        default=480,
        metavar="SECONDS",
        help="Warn after prolonged output silence; the build is never killed automatically (0 disables it).",
    )
    parser.add_argument(
        "--memory-warning-gb",
        type=float,
        default=8.0,
        metavar="GIB",
        help="Warn when available system memory is below this value. No low-memory mode is enabled.",
    )
    parser.add_argument(
        "--keep-build-dir",
        action="store_true",
        help="Keep Nuitka backend build directories after a successful build. Failed builds are always preserved.",
    )
    parser.add_argument(
        "--upx",
        action="store_true",
        help="Compress standalone/onefile binaries with UPX after the Nuitka build.",
    )
    parser.add_argument(
        "--upx-binary",
        default=None,
        metavar="PATH",
        help="Path to the UPX executable. Auto-detected from PATH if omitted.",
    )
    return parser.parse_args()


def effective_profile(profile: str) -> str:
    return "full" if profile == "system" else profile


def detect_target(project: Path) -> str:
    name = project.name.lower()
    if name.startswith("windows"):
        return "windows"
    if name.startswith("linux"):
        return "linux"
    if name.startswith("macos"):
        return "macos"
    raise RuntimeError(f"Cannot infer target platform from folder: {project}")


def current_host() -> str:
    return "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")


def ensure_host_matches(target: str, *, dry_run: bool) -> None:
    host = current_host()
    if host != target and not dry_run:
        raise RuntimeError(
            f"Nuitka builds are platform-specific: target={target}, current host={host}. "
            "Run this script on the matching operating system."
        )


STAGE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Environment validation", ("checking python", "nuitka version", "environment validation")),
    ("Python dependency analysis", ("nuitka-options:", "following all imports", "included module", "plugin")),
    ("Python level compilation", ("python level compilation", "optimizing module", "completed python level")),
    ("C source generation", ("generating c source", "source code generation", "code generation")),
    ("Backend C compilation", ("scons:", "backend c compiler", "compiling c", "cl.exe", "gcc", "clang")),
    ("Backend linking", ("linking", "link.exe", "creating library", "creating executable")),
    ("Standalone packaging", ("standalone", "copying dll", "copying data", "included data file")),
    ("Onefile packaging", ("onefile", "compression", "payload")),
)


def detect_stage(line: str, current: str) -> str:
    lowered = line.casefold()
    for stage, tokens in STAGE_PATTERNS:
        if any(token in lowered for token in tokens):
            return stage
    return current


def system_memory_bytes() -> tuple[int, int] | None:
    """Return (total, available) physical memory without adding a runtime dependency."""
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return int(status.ullTotalPhys), int(status.ullAvailPhys)

        if sys.platform.startswith("linux"):
            values: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0]) * 1024
            total = values.get("MemTotal")
            available = values.get("MemAvailable", values.get("MemFree"))
            return (total, available) if total and available is not None else None

        if sys.platform == "darwin":
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
            vm_stat = subprocess.check_output(["vm_stat"], text=True)
            page_match = re.search(r"page size of (\d+) bytes", vm_stat)
            page_size = int(page_match.group(1)) if page_match else 4096
            pages: dict[str, int] = {}
            for line in vm_stat.splitlines():
                match = re.match(r"([^:]+):\s+(\d+)\.", line)
                if match:
                    pages[match.group(1)] = int(match.group(2))
            available_pages = sum(
                pages.get(name, 0)
                for name in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")
            )
            return total, available_pages * page_size
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def warn_memory_pressure(profile: str, threshold_gib: float) -> None:
    memory = system_memory_bytes()
    if memory is None:
        print("[preflight] System memory information is unavailable; continuing without low-memory mode.")
        return
    total, available = memory
    total_gib = total / (1024 ** 3)
    available_gib = available / (1024 ** 3)
    print(f"[preflight] Memory: {available_gib:.1f} GiB available / {total_gib:.1f} GiB total.")
    threshold = max(0.0, float(threshold_gib))
    if effective_profile(profile) == "full" and threshold and available_gib < threshold:
        print(
            "WARNING: Available memory is below the configured warning threshold for the Qt WebEngine build.\n"
            "         --low-memory is intentionally NOT enabled because it increases compilation time.\n"
            "         Close memory-heavy applications or reduce --jobs before building. The build will continue.",
            flush=True,
        )


def terminate_process_tree(process: subprocess.Popen[bytes], *, force: bool) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            if force:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                process.terminate()
        else:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def run(
    command: Iterable[object],
    *,
    cwd: Path,
    heartbeat_seconds: int = 0,
    stall_warning_seconds: int = 0,
    log_file: Path | None = None,
    latest_log: Path | None = None,
) -> None:
    cmd = [str(part) for part in command]
    print("+", subprocess.list2cmdline(cmd), flush=True)
    if heartbeat_seconds <= 0 and stall_warning_seconds <= 0 and log_file is None:
        subprocess.run(cmd, cwd=cwd, check=True)
        return

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    assert process.stdout is not None

    chunks: queue.Queue[bytes | None] = queue.Queue()

    def read_chunks() -> None:
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                chunks.put(chunk)
        finally:
            chunks.put(None)

    reader = threading.Thread(target=read_chunks, name="nuitka-build-output", daemon=True)
    reader.start()

    started = time.monotonic()
    last_output = started
    last_heartbeat = started
    next_stall_warning = max(0, stall_warning_seconds)
    stage = "Nuitka startup"
    pending = ""
    last_console_line = ""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    stream = log_file.open("wb") if log_file is not None else None

    def emit_line(line: str) -> None:
        nonlocal stage, last_console_line
        cleaned = line.strip("\x00")
        if not cleaned:
            return
        detected = detect_stage(cleaned, stage)
        if detected != stage:
            stage = detected
            print(f"[stage] {stage}", flush=True)
        if cleaned != last_console_line:
            print(cleaned, flush=True)
            last_console_line = cleaned

    try:
        output_finished = False
        while not output_finished or process.poll() is None:
            try:
                chunk = chunks.get(timeout=1.0)
            except queue.Empty:
                chunk = b""
            now = time.monotonic()
            if chunk is None:
                output_finished = True
            elif chunk:
                last_output = now
                if stream is not None:
                    stream.write(chunk)
                    stream.flush()
                pending += decoder.decode(chunk)
                parts = re.split(r"\r\n|\r|\n", pending)
                pending = parts.pop()
                for part in parts:
                    emit_line(part)
                if len(pending) >= 2048:
                    emit_line(pending)
                    pending = ""

            silent_for = now - last_output
            elapsed = int(now - started)
            if heartbeat_seconds > 0 and silent_for >= heartbeat_seconds and now - last_heartbeat >= heartbeat_seconds:
                print(
                    f"[build] stage={stage!r}, pid={process.pid}, elapsed={elapsed}s, "
                    f"no output for {int(silent_for)}s; process is still present.",
                    flush=True,
                )
                last_heartbeat = now
            if (
                stall_warning_seconds > 0
                and silent_for >= next_stall_warning
                and process.poll() is None
            ):
                print(
                    f"WARNING: No build output for {int(silent_for)}s during {stage}. "
                    "This is a soft warning only; the build will not be terminated automatically.",
                    flush=True,
                )
                next_stall_warning += stall_warning_seconds

        pending += decoder.decode(b"", final=True)
        if pending:
            emit_line(pending)
        return_code = process.wait()
    except KeyboardInterrupt:
        print("\n[build] Cancellation requested; stopping the Nuitka process group...", flush=True)
        terminate_process_tree(process, force=False)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            print("[build] Graceful stop timed out; forcing process-tree termination.", flush=True)
            terminate_process_tree(process, force=True)
            process.wait()
        raise
    finally:
        if stream is not None:
            stream.close()
        reader.join(timeout=1)
        if latest_log is not None and log_file is not None and log_file.exists():
            latest_log.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(log_file, latest_log)

    if return_code != 0:
        detail = f" See build log: {log_file}" if log_file is not None else ""
        raise subprocess.CalledProcessError(return_code, cmd, output=detail)

def requirement_file(project: Path, target: str, profile: str) -> Path:
    profile = effective_profile(profile)
    suffix = "" if profile == "lite" else "-full"
    return project / f"requirements-{target}{suffix}.txt"


def install_build_dependencies(project: Path, target: str, profile: str) -> None:
    runtime_req = requirement_file(project, target, profile)
    if not runtime_req.is_file():
        raise FileNotFoundError(runtime_req)
    run([sys.executable, "-m", "pip", "install", "-r", runtime_req], cwd=project)
    run([sys.executable, "-m", "pip", "install", "-r", project / "requirements-nuitka.txt"], cwd=project)


def validate_python_version() -> None:
    if sys.version_info < (3, 10) or sys.version_info >= (3, 14):
        raise RuntimeError(
            f"This reproducible Nuitka 4.1.3 build supports Python 3.10-3.13; found {sys.version.split()[0]}."
        )


def validate_source(project: Path) -> None:
    required = (
        project / "src",
        project / "src" / "main.pyw" if project.name.lower().startswith("windows") else project / "src" / "main.py",
        project / "src" / "img",
        project / "src" / "lang",
        project / "src" / "app" / "version.py",
    )
    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)
    errors: list[str] = []
    for path in sorted((project / "src").rglob("*.py")) + sorted((project / "src").rglob("*.pyw")):
        try:
            compile(path.read_bytes(), str(path), "exec")
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"{path.relative_to(project)}: {exc}")
    if errors:
        raise RuntimeError("Python source validation failed:\n" + "\n".join(errors))


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"Required build dependency is missing: {distribution}") from exc


def validate_environment(target: str, profile: str) -> None:
    actual = _version("Nuitka")
    if actual != NUITKA_VERSION:
        raise RuntimeError(f"Nuitka must be {NUITKA_VERSION}, found {actual}")
    essentials = _version("PySide6-Essentials")
    if effective_profile(profile) == "full":
        addons = _version("PySide6-Addons")
        if addons != essentials:
            raise RuntimeError(f"PySide6 version mismatch: Essentials={essentials}, Addons={addons}")
        try:
            __import__("PySide6.QtWebEngineCore")
            __import__("PySide6.QtWebEngineWidgets")
        except Exception as exc:
            raise RuntimeError("Qt WebEngine cannot be imported in the build environment") from exc
    if target == "linux" and shutil.which("patchelf") is None:
        raise RuntimeError(
            "patchelf is required for Linux standalone/onefile builds. Install it with the system package manager."
        )


def read_version(project: Path) -> tuple[str, str]:
    namespace: dict[str, object] = {}
    exec((project / "src/app/version.py").read_text(encoding="utf-8"), namespace)
    return str(namespace["APP_VERSION"]), str(namespace["APP_VERSION_FILE"])


def has_payload_files(folder: Path, suffixes: tuple[str, ...] | None = None) -> bool:
    if not folder.is_dir():
        return False
    for path in folder.rglob("*"):
        if not path.is_file() or path.name.lower().startswith("readme"):
            continue
        if suffixes is None or path.suffix.lower() in suffixes:
            return True
    return False


def build_args(project: Path, target: str, profile: str, mode: str, jobs: int = 2, windows_console_mode: str = "disable") -> tuple[list[str], Path]:
    profile = effective_profile(profile)
    app_version, file_version = read_version(project)
    entry = project / "src" / ("main.pyw" if target == "windows" else "main.py")
    out_dir = project / "dist-nuitka" / profile / mode
    out_arg = out_dir.relative_to(project)
    report = out_arg / "compilation-report.xml"

    args = [
        sys.executable,
        "-m",
        "nuitka",
        f"--mode={mode}",
        "--assume-yes-for-downloads",
        f"--output-dir={out_arg}",
        f"--output-filename={APP_NAME}{'.exe' if target == 'windows' else ''}",
        "--enable-plugins=pyside6",
        f"--jobs={max(1, int(jobs))}",
        "--lto=no",
        f"--report={report}",
        "--noinclude-setuptools-mode=nofollow",
        "--noinclude-pytest-mode=nofollow",
        "--noinclude-pydoc-mode=nofollow",
        "--noinclude-IPython-mode=nofollow",
        "--include-data-dir=src/img=img",
        "--include-data-dir=src/lang=lang",
        "--include-module=PySide6.QtSvg",
        "--include-module=PySide6.QtSvgWidgets",
        "--include-module=ui.main_window",
        "--include-module=ui.preview_canvas",
        "--include-module=ui.qt_root_shim",
        "--include-module=ui.sidebar",
        "--include-module=ui.probability_dialog",
        "--include-module=ui.dialog_style",
        "--include-module=platform_adapters.video",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=PyQt5",
        "--nofollow-import-to=PyQt6",
        "--nofollow-import-to=PySide2",
        "--nofollow-import-to=matplotlib",
        "--nofollow-import-to=pandas",
        "--nofollow-import-to=scipy",
        "--nofollow-import-to=IPython",
        "--nofollow-import-to=notebook",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=requests",
        "--nofollow-import-to=httpx",
    ]
    if has_payload_files(project / "fonts", (".ttf", ".otf", ".ttc", ".woff", ".woff2")):
        args.append("--include-data-dir=fonts=fonts")
    if has_payload_files(project / "src/bin"):
        args.append("--include-data-dir=src/bin=bin")

    if profile == "full":
        args += [
            "--include-module=platform_adapters.run_html_wallpaper",
            "--include-module=PySide6.QtWebEngineCore",
            "--include-module=PySide6.QtWebEngineWidgets",
        ]
    else:
        args += [
            "--nofollow-import-to=platform_adapters.run_html_wallpaper",
            "--nofollow-import-to=PySide6.QtWebEngineCore",
            "--nofollow-import-to=PySide6.QtWebEngineQuick",
            "--nofollow-import-to=PySide6.QtWebEngineWidgets",
        ]

    if target == "windows":
        args += [
            f"--windows-console-mode={windows_console_mode}",
            "--windows-icon-from-ico=src/img/LOGO.ico",
            f"--file-version={file_version}",
            f"--product-version={file_version}",
            "--company-name=XXDZ Studio",
            "--file-description=Previous Desktop Background",
            "--product-name=Previous Desktop Background",
        ]
    elif target == "linux":
        if mode == "onefile":
            args.append("--linux-icon=src/img/LOGO.png")
    else:
        if mode != "standalone":
            raise RuntimeError("macOS app bundle builds use standalone mode")
        args += [
            "--macos-create-app-bundle",
            "--macos-app-icon=src/img/LOGO.icns",
            f"--macos-app-name={APP_NAME}",
            f"--macos-app-version={app_version}",
            "--macos-app-mode=gui",
            "--macos-prohibit-multiple-instances",
        ]
    args.append(str(entry.relative_to(project)))
    return args, out_dir


def directory_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def prune_qtwebengine_locales(artifact: Path) -> tuple[int, int]:
    """Remove only unneeded locale packs; never delete core Qt WebEngine resources."""
    keep = {"en-US.pak", "zh-CN.pak", "zh-TW.pak"}
    removed = 0
    saved = 0
    if not artifact.is_dir():
        return removed, saved
    for locale_dir in artifact.rglob("qtwebengine_locales"):
        if not locale_dir.is_dir():
            continue
        for pack in locale_dir.glob("*.pak"):
            if pack.name in keep:
                continue
            try:
                saved += pack.stat().st_size
                pack.unlink()
                removed += 1
            except OSError:
                pass
    return removed, saved


def upx_compress(artifact: Path, target: str, upx_binary: str | None) -> tuple[int, int]:
    """Compress binaries inside the artifact with UPX.

    Returns (files_processed, bytes_saved).  On Windows only .exe and .dll
    are compressed (.pyd is skipped because UPX can interfere with the Python
    extension loader).  On Linux/macOS the main executable plus every .so /
    .dylib is compressed.
    """
    upx = upx_binary or shutil.which("upx") or shutil.which("upx-ucl")
    if not upx:
        print("upx: executable not found; skipping (pass --upx-binary or install UPX)")
        return 0, 0
    candidates: list[Path] = []
    if artifact.is_file():
        candidates = [artifact]
    else:
        main_exe = artifact / APP_NAME
        if main_exe.is_file():
            candidates.append(main_exe)
        if target == "windows":
            candidates.extend(p for p in artifact.rglob("*.exe") if p != main_exe)
            candidates.extend(artifact.rglob("*.dll"))
        else:
            candidates.extend(artifact.rglob("*.so"))
            candidates.extend(artifact.rglob("*.dylib"))
    processed = 0
    saved = 0
    for binary in candidates:
        before = binary.stat().st_size
        try:
            subprocess.run(
                [upx, "--best", "--lzma", "--quiet", "--overwrite", str(binary)],
                check=False, capture_output=True,
            )
            after = binary.stat().st_size
            if after < before:
                saved += before - after
                processed += 1
        except Exception:
            pass
    return processed, saved


def normalize_output(project: Path, target: str, profile: str, mode: str, out_dir: Path, *, upx: bool = False, upx_binary: str | None = None) -> Path:
    profile = effective_profile(profile)
    if target == "macos":
        candidates = sorted(out_dir.glob("*.app"))
        if not candidates:
            raise RuntimeError(f"No .app bundle found under {out_dir}")
        artifact = candidates[0]
        desired = out_dir / f"{APP_NAME}.app"
        if artifact != desired:
            if desired.exists():
                shutil.rmtree(desired)
            artifact.rename(desired)
        artifact = desired
    elif mode == "standalone":
        candidates = sorted(out_dir.glob("*.dist"))
        if not candidates:
            raise RuntimeError(f"No standalone .dist directory found under {out_dir}")
        artifact = candidates[0]
        desired = out_dir / APP_NAME
        if artifact != desired:
            if desired.exists():
                shutil.rmtree(desired)
            artifact.rename(desired)
        artifact = desired
    else:
        suffix = ".exe" if target == "windows" else ""
        artifact = out_dir / f"{APP_NAME}{suffix}"
        if not artifact.exists():
            raise RuntimeError(f"Onefile output not found: {artifact}")

    if profile == "full" and mode == "standalone":
        removed, saved = prune_qtwebengine_locales(artifact)
        if removed:
            print(f"Pruned Qt WebEngine locales: {removed} files, saved {human_size(saved)}")

    if upx:
        n, saved = upx_compress(artifact, target, upx_binary)
        if n:
            print(f"upx: compressed {n} binaries, saved {human_size(saved)}")

    if target == "linux":
        executable = artifact / APP_NAME if artifact.is_dir() else artifact
        executable.chmod(executable.stat().st_mode | 0o111)
        bundled_mpv = artifact / "bin/mpv" if artifact.is_dir() else None
        if bundled_mpv and bundled_mpv.is_file():
            bundled_mpv.chmod(bundled_mpv.stat().st_mode | 0o111)
        machine = platform.machine().lower().replace("amd64", "x86_64")
        archive = project / f"{APP_NAME}-linux-{machine}-{profile}-{mode}.tar.gz"
        if archive.exists():
            archive.unlink()
        with tarfile.open(archive, "w:gz") as archive_file:
            archive_file.add(artifact, arcname=artifact.name)
        print(f"Archive: {archive}")

    print(f"Build output: {artifact}")
    print(f"Build size: {human_size(directory_size(artifact))}")
    print(f"Nuitka report: {out_dir / 'compilation-report.xml'}")
    return artifact



def cleanup_backend_directories(out_dir: Path) -> None:
    removed: list[Path] = []
    for pattern in ("*.build", "*.onefile-build", "*.dist"):
        for path in out_dir.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path)
    if removed:
        print("Removed successful-build backend directories:")
        for path in removed:
            print(f"  - {path}")


def write_build_metadata(project: Path, command: list[str], log_dir: Path, target: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "command.txt").write_text(subprocess.list2cmdline(command) + "\n", encoding="utf-8")
    memory = system_memory_bytes()
    metadata = {
        "created_at": dt.datetime.now().astimezone().isoformat(),
        "target": target,
        "host": current_host(),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "command": command,
        "memory_total_bytes": memory[0] if memory else None,
        "memory_available_bytes": memory[1] if memory else None,
    }
    (log_dir / "environment.json").write_text(
        json.dumps(metadata, ensure_ascii=True, indent=2) + "\n",
        encoding="ascii",
    )


def write_windows_one_line(project: Path, command: list[str]) -> None:
    if detect_target(project) != "windows":
        return
    portable = ["py", "-3.13", *command[1:]]
    destination = project.parent / f"WINDOWS_NUITKA_{NUITKA_VERSION}_ONE_LINE.txt"
    destination.write_text(subprocess.list2cmdline(portable) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    project = Path(__file__).resolve().parent
    target = detect_target(project)
    ensure_host_matches(target, dry_run=args.dry_run)
    validate_python_version()
    validate_source(project)
    if args.profile == "system":
        print("WARNING: 'system' is a compatibility alias of 'full' and uses Qt WebEngine.")
    command, out_dir = build_args(project, target, args.profile, args.mode, args.jobs, args.windows_console_mode)
    write_windows_one_line(project, command)
    if args.dry_run:
        print(subprocess.list2cmdline(command))
        return 0
    if not args.skip_install:
        install_build_dependencies(project, target, args.profile)
    validate_environment(target, args.profile)
    warn_memory_pressure(args.profile, args.memory_warning_gb)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = project / "build-logs" / f"{stamp}-{effective_profile(args.profile)}-{args.mode}"
    build_log = log_dir / "nuitka-build.log"
    latest_log = project / "build-logs" / "latest.log"
    write_build_metadata(project, command, log_dir, target)
    print(f"Build diagnostics: {log_dir}")
    run(
        command,
        cwd=project,
        heartbeat_seconds=max(0, args.build_heartbeat),
        stall_warning_seconds=max(0, args.stall_warning),
        log_file=build_log,
        latest_log=latest_log,
    )
    normalize_output(project, target, args.profile, args.mode, out_dir, upx=args.upx, upx_binary=args.upx_binary)
    if not args.keep_build_dir:
        cleanup_backend_directories(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
