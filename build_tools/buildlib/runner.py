from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
from typing import Callable, Iterable

from .cli import print_section
from .constants import PROJECT_ROOT, python_executable


def display_command(command: Iterable[str]) -> str:
    values = [os.fspath(item) for item in command]
    return subprocess.list2cmdline(values) if os.name == "nt" else shlex.join(values)


def install_requirements(files: Iterable[Path], *, verbose: bool, dry_run: bool) -> None:
    requirements = tuple(dict.fromkeys(Path(item) for item in files))
    for requirement in requirements:
        if not requirement.is_file():
            raise RuntimeError(f"Missing requirement file: {requirement}")
    if not requirements:
        return
    # Resolve the application and selected builder in one pip transaction. This
    # avoids repeated dependency solving and prevents one requirement pass from
    # silently changing versions chosen by an earlier pass.
    command = [
        python_executable(),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
    ]
    if not verbose:
        command.append("--quiet")
    for requirement in requirements:
        command.extend(["-r", os.fspath(requirement)])
    print("  $", display_command(command), flush=True)
    if not dry_run:
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def _stop_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()


def run_build(
    command: list[str],
    *,
    target: str,
    dry_run: bool,
    env_updates: dict[str, str] | None = None,
    validator: Callable[[], Iterable[str]] | None = None,
) -> int:
    print("  $", display_command(command), flush=True)
    if dry_run:
        print("  Dry-run complete; no compiler process was started.", flush=True)
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = PROJECT_ROOT / "build-logs" / target
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{stamp}.log"
    latest = log_dir / "latest.log"
    environment = os.environ.copy()
    environment.update(env_updates or {})
    code = 1
    process: subprocess.Popen[str] | None = None
    with log_path.open("w", encoding="utf-8", buffering=1) as stream:
        stream.write("+ " + display_command(command) + "\n")
        try:
            if os.name == "nt":
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            else:
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=True,
                )
            if process.stdout is None:
                raise RuntimeError("build process stdout pipe was not created")
            for line in process.stdout:
                sys.stdout.write(line)
                stream.write(line)
            code = process.wait()
        except KeyboardInterrupt:
            if process is not None:
                _stop_tree(process)
                try:
                    process.wait(timeout=8)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            stream.write("\nBuild interrupted by user.\n")
            raise
        except Exception:
            if process is not None and process.poll() is None:
                _stop_tree(process)
                try:
                    process.wait(timeout=8)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            raise
        finally:
            stream.write(f"\nExit code: {code}\n")

    try:
        shutil.copyfile(log_path, latest)
    except OSError:
        pass
    if code:
        print_section(f"Build failed (exit code {code})")
        print(f"  Log: {log_path}")
        raise subprocess.CalledProcessError(code, command)
    if validator is not None:
        errors = tuple(str(item) for item in validator())
        if errors:
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write("\nBundle validation failed:\n")
                for error in errors:
                    stream.write(f"- {error}\n")
            try:
                shutil.copyfile(log_path, latest)
            except OSError:
                pass
            print_section("Bundle validation failed")
            for error in errors:
                print(f"  - {error}")
            print(f"  Log: {log_path}")
            raise RuntimeError("bundle validation failed; see messages above")
    print_section("Build completed")
    print(f"  Log: {log_path}")
    return 0
