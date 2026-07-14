#!/usr/bin/env python3
"""Real X11/Openbox test for HTML wallpaper desktop-coverage auto-pause."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("Xvfb", "openbox", "xterm", "wmctrl", "xprop")


def _run(args: list[str], env: dict[str, str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, env=env, text=True, capture_output=True, check=check, timeout=10)


def _window_ids(env: dict[str, str]) -> set[str]:
    result = _run(["wmctrl", "-l"], env, check=False)
    return {line.split()[0] for line in result.stdout.splitlines() if line.strip()}


def _launch_window(env: dict[str, str], x: int, y: int, width: int, height: int):
    before = _window_ids(env)
    proc = subprocess.Popen(
        ["xterm", "-e", "sh", "-c", "while :; do sleep 3600; done"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    wid = ""
    while time.monotonic() < deadline:
        created = _window_ids(env) - before
        if created:
            wid = sorted(created)[-1]
            break
        time.sleep(0.1)
    if not wid:
        proc.terminate()
        raise AssertionError("xterm did not create an EWMH client window")
    _run(["wmctrl", "-ir", wid, "-b", "remove,maximized_vert,maximized_horz"], env, check=False)
    time.sleep(0.15)
    _run(["wmctrl", "-ir", wid, "-e", f"0,{x},{y},{width},{height}"], env)
    time.sleep(0.35)
    return proc, wid


def _stop(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    missing = [name for name in REQUIRED if not shutil.which(name)]
    if missing:
        print("SKIP real X11 coverage test; missing:", ", ".join(missing))
        return 0

    display_number = next((n for n in range(90, 120) if not Path(f"/tmp/.X{n}-lock").exists()), None)
    if display_number is None:
        print("SKIP real X11 coverage test; no free display number")
        return 0

    display = f":{display_number}"
    xvfb = openbox = first = second = None
    with tempfile.TemporaryDirectory() as temp:
        env = os.environ.copy()
        env.update({
            "DISPLAY": display,
            "XDG_SESSION_TYPE": "x11",
            "HOME": temp,
            "XDG_CONFIG_HOME": str(Path(temp) / ".config"),
        })
        original_display = os.environ.get("DISPLAY")
        original_session = os.environ.get("XDG_SESSION_TYPE")
        os.environ["DISPLAY"] = display
        os.environ["XDG_SESSION_TYPE"] = "x11"
        try:
            xvfb = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(0.45)
            openbox = subprocess.Popen(
                ["openbox"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )
            time.sleep(0.8)

            sys.path.insert(0, str(ROOT / "Linux.ver(beta)" / "src"))
            from platform_adapters.desktop_visibility import (
                Rect,
                _linux_x11_covering_rects,
                coverage_ratios,
                desktop_visible_from_rects,
            )

            screens = [Rect(0, 0, 1280, 720)]

            def state() -> tuple[bool | None, float | None]:
                windows = _linux_x11_covering_rects(screens)
                assert windows is not None, "X11 enumeration unexpectedly unavailable"
                ratios = coverage_ratios(screens, windows)
                return desktop_visible_from_rects(screens, windows), ratios[0]

            visible, ratio = state()
            assert visible is True and ratio == 0.0, (visible, ratio)

            first, first_id = _launch_window(env, 0, 0, 700, 720)
            visible, ratio = state()
            assert visible is True and 0.45 < float(ratio) < 0.70, (visible, ratio)
            _stop(first); first = None
            time.sleep(0.3)

            first, first_id = _launch_window(env, 0, 0, 640, 720)
            second, second_id = _launch_window(env, 640, 0, 640, 720)
            visible, ratio = state()
            assert visible is False and float(ratio) >= 0.95, (visible, ratio)
            _stop(first); first = None
            _stop(second); second = None
            time.sleep(0.3)

            first, first_id = _launch_window(env, 0, 0, 1280, 720)
            visible, ratio = state()
            assert visible is False and float(ratio) >= 0.95, (visible, ratio)

            # A minimized window no longer covers the desktop.
            _run(["wmctrl", "-ir", first_id, "-b", "add,hidden"], env)
            time.sleep(0.35)
            visible, ratio = state()
            assert visible is True and ratio == 0.0, (visible, ratio)
            _stop(first); first = None
            time.sleep(0.3)

            # A globally translucent full-screen window leaves the wallpaper
            # visibly contributing, so conservative auto-pause stays active.
            first, first_id = _launch_window(env, 0, 0, 1280, 720)
            half_alpha = str(0x7FFFFFFF)
            _run(
                ["xprop", "-id", first_id, "-f", "_NET_WM_WINDOW_OPACITY", "32c", "-set", "_NET_WM_WINDOW_OPACITY", half_alpha],
                env,
            )
            time.sleep(0.25)
            visible, ratio = state()
            assert visible is True and ratio == 0.0, (visible, ratio)

            print("PASS real Linux X11 coverage: partial, tiled, full, minimized, translucent")
            return 0
        finally:
            for proc in (first, second, openbox, xvfb):
                _stop(proc)
            if original_display is None:
                os.environ.pop("DISPLAY", None)
            else:
                os.environ["DISPLAY"] = original_display
            if original_session is None:
                os.environ.pop("XDG_SESSION_TYPE", None)
            else:
                os.environ["XDG_SESSION_TYPE"] = original_session


if __name__ == "__main__":
    raise SystemExit(main())
