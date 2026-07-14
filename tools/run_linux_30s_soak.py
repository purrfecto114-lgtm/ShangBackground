#!/usr/bin/env python3
"""Launch the real Linux application under Xvfb/Openbox for a 30-second soak.

The script does not mock application code. It prepares an isolated safe config,
waits for the real main window, records resource samples, captures a screenshot,
keeps the GUI alive for at least 30 seconds, then sends SIGTERM and verifies
normal cleanup. KDE-specific services are intentionally not started or tested.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Linux.ver(beta)" / "src"
ARTIFACTS = ROOT / "VALIDATION_ARTIFACTS"


def terminate(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.wait(timeout=5)


def wait_x(display: str, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if subprocess.run(
            ["xdpyinfo", "-display", display],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0:
            return
        time.sleep(0.1)
    raise RuntimeError("Xvfb did not become ready")


def main() -> int:
    required = ("Xvfb", "openbox", "xdpyinfo", "wmctrl", "scrot")
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        raise RuntimeError("missing Linux soak tools: " + ", ".join(missing))

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    display = ":98"
    screenshot = ARTIFACTS / "linux_full_app_30s.png"
    runtime_log = ARTIFACTS / "linux_full_app_30s_runtime.log"
    result_path = ARTIFACTS / "linux_full_app_30s_result.json"

    with tempfile.TemporaryDirectory(prefix="shang-30s-soak-") as td:
        home = Path(td)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        env = os.environ.copy()
        env.update(
            {
                "DISPLAY": display,
                "XDG_SESSION_TYPE": "x11",
                "XDG_CURRENT_DESKTOP": "Openbox",
                "HOME": str(home),
                "USERPROFILE": str(home),
                "LOCALAPPDATA": str(home),
                "APPDATA": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_RUNTIME_DIR": str(runtime),
                "QT_QPA_PLATFORM": "xcb",
                "QTWEBENGINE_DISABLE_SANDBOX": "1",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-gpu",
                "PYTHONPATH": str(SRC),
                "PYTHONUNBUFFERED": "1",
            }
        )
        stub = os.environ.get("SHANG_XCB_STUB_DIR", "")
        if stub:
            env["LD_LIBRARY_PATH"] = stub + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

        xvfb = openbox = app_proc = None
        try:
            xvfb = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1440x900x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            wait_x(display)
            openbox = subprocess.Popen(
                ["openbox"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )
            time.sleep(1.3)

            # Prepare a safe, non-networked default config through the product's
            # own config serializer before launching the real entry point.
            prep = """
from core import engine as c
cfg=c.get_default_config()
cfg.update({
 'tray_icon': False,
 'run_in_background': False,
 'auto_start': False,
 'auto_start_prompt_shown': True,
 'silent_update_check_on_startup': False,
 'bing_auto_update_on_start': False,
 'bing_auto_delete_on_start': False,
 'global_hotkeys_enabled': False,
 'mode': '图片',
 'single_image': '',
 'current_wallpaper': '',
 'html_file': '',
 'video_file': '',
 'log_enabled': False,
})
c.config.clear(); c.config.update(cfg); c.save_config()
print(c.CONFIG_PATH)
"""
            prep_proc = subprocess.run(
                [sys.executable, "-c", prep],
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
            )
            if prep_proc.returncode != 0:
                raise RuntimeError("failed to prepare isolated config: " + prep_proc.stderr)

            log_handle = runtime_log.open("w", encoding="utf-8", newline="\n")
            launch_started = time.monotonic()
            app_proc = subprocess.Popen(
                [sys.executable, str(SRC / "main.py")],
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            ps_proc = psutil.Process(app_proc.pid)
            ps_proc.cpu_percent(None)

            window_seen = False
            screenshot_taken = False
            samples: list[dict[str, float]] = []
            deadline = launch_started + 30.0
            while time.monotonic() < deadline:
                if app_proc.poll() is not None:
                    log_handle.flush()
                    raise RuntimeError(f"application exited before 30 seconds with code {app_proc.returncode}")
                now = time.monotonic()
                if not window_seen:
                    wm = subprocess.run(
                        ["wmctrl", "-lx"],
                        env=env,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        capture_output=True,
                        check=False,
                    )
                    window_seen = ("ShangBackground" in wm.stdout or "上一个桌面背景" in wm.stdout)
                if window_seen and not screenshot_taken and now - launch_started >= 5.0:
                    shot = subprocess.run(
                        ["scrot", str(screenshot)],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    screenshot_taken = shot.returncode == 0 and screenshot.is_file()
                try:
                    mem = ps_proc.memory_info().rss / (1024 * 1024)
                    cpu = ps_proc.cpu_percent(None)
                    samples.append({"t": round(now - launch_started, 3), "rss_mib": round(mem, 3), "cpu_percent": round(cpu, 3)})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                time.sleep(0.25)

            elapsed_before_signal = time.monotonic() - launch_started
            if elapsed_before_signal < 30.0:
                raise AssertionError(f"soak duration too short: {elapsed_before_signal:.3f}s")
            app_proc.send_signal(signal.SIGTERM)
            try:
                return_code = app_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                terminate(app_proc)
                return_code = app_proc.returncode
            log_handle.flush()
            log_handle.close()

            text = runtime_log.read_text(encoding="utf-8", errors="replace")
            fatal_markers = (
                "Traceback (most recent call last)",
                "Fatal Python error",
                "Segmentation fault",
                "core dumped",
                "XIO:  fatal IO error",
            )
            found_fatal = [marker for marker in fatal_markers if marker in text]
            if return_code != 0:
                raise AssertionError(f"application cleanup exit code was {return_code}")
            if not window_seen:
                raise AssertionError("main window was not observed by wmctrl")
            if not screenshot_taken:
                raise AssertionError("30-second soak screenshot was not captured")
            if found_fatal:
                raise AssertionError("fatal runtime markers: " + ", ".join(found_fatal))
            if psutil.pid_exists(app_proc.pid):
                raise AssertionError("application PID remained alive after cleanup")

            result = {
                "status": "PASS",
                "platform": "Linux X11/Openbox",
                "duration_before_sigterm_seconds": round(elapsed_before_signal, 3),
                "exit_code": return_code,
                "window_seen": window_seen,
                "screenshot": str(screenshot.relative_to(ROOT)),
                "runtime_log": str(runtime_log.relative_to(ROOT)),
                "sample_count": len(samples),
                "peak_rss_mib": max((sample["rss_mib"] for sample in samples), default=0.0),
                "mean_rss_mib": round(sum(sample["rss_mib"] for sample in samples) / max(1, len(samples)), 3),
                "peak_cpu_percent": max((sample["cpu_percent"] for sample in samples), default=0.0),
                "fatal_markers": found_fatal,
                "kde_full_adaptation": "deferred_by_user_instruction",
                "environment_note": "xcb-cursor test stub may be used only when the container lacks the distro runtime library",
                "samples": samples,
            }
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in result.items() if k != "samples"}, ensure_ascii=False, sort_keys=True))
            print("PASS real Linux application 30-second soak")
            return 0
        finally:
            if app_proc is not None and app_proc.poll() is None:
                terminate(app_proc)
            terminate(openbox)
            terminate(xvfb)


if __name__ == "__main__":
    raise SystemExit(main())
