from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
from types import FrameType
from typing import Callable, Iterable, Iterator

from .cli import print_section
from .constants import PROJECT_ROOT, ensure_build_python_environment


class BuildTerminated(RuntimeError):
    def __init__(self, signum: int) -> None:
        super().__init__(f"build terminated by signal {signum}")
        self.signum = signum


def display_command(command: Iterable[str]) -> str:
    values = [os.fspath(item) for item in command]
    return subprocess.list2cmdline(values) if os.name == "nt" else shlex.join(values)


def install_requirements(
    files: Iterable[Path],
    *,
    verbose: bool,
    dry_run: bool,
    report_path: Path | None = None,
) -> None:
    requirements = tuple(dict.fromkeys(Path(item) for item in files))
    for requirement in requirements:
        if not requirement.is_file():
            raise RuntimeError(f"Missing requirement file: {requirement}")
    if not requirements:
        return
    python = ensure_build_python_environment(dry_run=dry_run)
    command = [python, "-m", "pip", "install", "--disable-pip-version-check"]
    if not verbose:
        command.append("--quiet")
    if report_path is not None:
        if not dry_run:
            report_path.parent.mkdir(parents=True, exist_ok=True)
        command.extend(("--report", os.fspath(report_path)))
    for requirement in requirements:
        command.extend(["-r", os.fspath(requirement)])
    print("  $", display_command(command), flush=True)
    if dry_run:
        return
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    check = [python, "-m", "pip", "check"]
    print("  $", display_command(check), flush=True)
    subprocess.run(check, cwd=PROJECT_ROOT, check=True)


def _stop_tree(process: subprocess.Popen[str], *, grace_seconds: float = 2.0) -> None:
    """Stop the complete compiler tree and reap the direct child.

    A graceful termination is attempted first on POSIX. Processes that ignore
    SIGTERM are escalated to SIGKILL so an interrupted build cannot continue
    writing into the staging directory after the launcher has exited.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=max(grace_seconds, 1.0))
        except (subprocess.TimeoutExpired, OSError):
            process.kill()
            try:
                process.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                pass
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()
    try:
        process.wait(timeout=2)
    except (subprocess.TimeoutExpired, OSError):
        pass


@contextmanager
def _termination_forwarder(get_process: Callable[[], subprocess.Popen[str] | None]) -> Iterator[None]:
    if threading_unavailable_for_signals():
        yield
        return
    handled: list[int] = [int(signal.SIGTERM)]
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        handled.append(int(sigbreak))
    previous: dict[int, object] = {}

    def _handler(signum: int, _frame: FrameType | None) -> None:
        process = get_process()
        if process is not None:
            _stop_tree(process)
        raise BuildTerminated(signum)

    try:
        for signum in handled:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, _handler)
        yield
    finally:
        for signum, old in previous.items():
            signal.signal(signum, old)  # pyright: ignore[reportArgumentType]


def threading_unavailable_for_signals() -> bool:
    try:
        import threading

        return threading.current_thread() is not threading.main_thread()
    except Exception:
        return True


def run_build(
    command: list[str],
    *,
    target: str,
    dry_run: bool,
    env_updates: dict[str, str] | None = None,
    prepare: Callable[[], None] | None = None,
    validator: Callable[[], Iterable[str]] | None = None,
    publisher: Callable[[], None] | None = None,
    cleanup_failed: Callable[[], None] | None = None,
) -> int:
    print("  $", display_command(command), flush=True)
    if dry_run:
        print("  Dry-run complete; no compiler process was started.", flush=True)
        return 0
    if prepare is not None:
        prepare()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    log_dir = PROJECT_ROOT / "build-logs" / target
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{stamp}.log"
    latest = log_dir / "latest.log"
    environment = os.environ.copy()
    environment.update(env_updates or {})
    code = 1
    process: subprocess.Popen[str] | None = None
    succeeded = False
    try:
        with log_path.open("w", encoding="utf-8", buffering=1) as stream:
            stream.write("+ " + display_command(command) + "\n")
            try:
                with _termination_forwarder(lambda: process):
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
                    try:
                        for line in process.stdout:
                            sys.stdout.write(line)
                            stream.write(line)
                    except (OSError, ValueError) as exc:
                        stream.write(f"\nI/O error reading build output: {exc}\n")
                        sys.stderr.write(f"\nI/O error reading build output: {exc}\n")
                    code = process.wait()
            except KeyboardInterrupt:
                if process is not None:
                    _stop_tree(process)
                stream.write("\nBuild interrupted by user.\n")
                raise
            except BuildTerminated as exc:
                if process is not None and process.poll() is None:
                    _stop_tree(process)
                stream.write(f"\nBuild terminated by signal {exc.signum}.\n")
                raise SystemExit(128 + exc.signum) from exc
            except Exception:
                if process is not None and process.poll() is None:
                    _stop_tree(process)
                raise
            finally:
                stream.write(f"\nExit code: {code}\n")

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
                print_section("Bundle validation failed")
                for error in errors:
                    print(f"  - {error}")
                print(f"  Log: {log_path}")
                raise RuntimeError("bundle validation failed; see messages above")
        if publisher is not None:
            publisher()
        succeeded = True
        print_section("Build completed and published")
        print(f"  Log: {log_path}")
        return 0
    finally:
        if log_path.is_file():
            try:
                shutil.copyfile(log_path, latest)
            except OSError:
                pass
        if not succeeded and cleanup_failed is not None:
            cleanup_failed()
