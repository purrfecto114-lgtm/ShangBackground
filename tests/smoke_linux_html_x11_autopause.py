#!/usr/bin/env python3
"""Real X11/Openbox QtWebEngine auto-pause/resume integration test.

The test launches an actual HTML wallpaper process on Xvfb, verifies that it
keeps rendering while the desktop is visible, freezes when an opaque window
covers the display, and resumes after that window is minimized.
"""
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
SRC = ROOT / "Linux.ver(beta)" / "src"
PYTHON = Path(sys.executable)


def wait_for(predicate, timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return
        time.sleep(0.2)
    raise AssertionError(f"timeout waiting for {description}; last={last!r}")


def terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.wait(timeout=3)


def main() -> int:
    required = ("Xvfb", "openbox", "xterm", "wmctrl", "xdpyinfo")
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        print("SKIP missing X11 test tools: " + ", ".join(missing))
        return 0

    display = ":96"
    with tempfile.TemporaryDirectory(prefix="shang-html-x11-") as td:
        home = Path(td)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        html_path = home / "中文可见桌面暂停测试.html"
        html_path.write_text(
            """<!doctype html><meta charset='utf-8'><title>桌面可见性测试</title>
<canvas id='c' width='640' height='360'></canvas><script>
let n=0,c=document.querySelector('#c'),x=c.getContext('2d');
function f(){n++;x.fillStyle=`hsl(${n%360} 70% 45%)`;x.fillRect(0,0,c.width,c.height);requestAnimationFrame(f)}f();
</script>""",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update({
            "DISPLAY": display,
            "XDG_SESSION_TYPE": "x11",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home),
            "APPDATA": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_RUNTIME_DIR": str(runtime),
            "QT_QPA_PLATFORM": "xcb",
            "QTWEBENGINE_DISABLE_SANDBOX": "1",  # root-only CI environment
            "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-gpu",
            "PYTHONPATH": str(SRC),
        })
        stub = os.environ.get("SHANG_XCB_STUB_DIR", "")
        if stub:
            env["LD_LIBRARY_PATH"] = stub + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

        xvfb = openbox = cover = None
        try:
            xvfb = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            wait_for(
                lambda: subprocess.run(["xdpyinfo", "-display", display], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0,
                8,
                "Xvfb",
            )
            openbox = subprocess.Popen(
                ["openbox"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )
            time.sleep(1.5)

            helper = f"""
import time
from platform_adapters import html_wallpaper as h
h.stop_html_wallpaper()
h.runtime_set_option('auto_pause', True)
h.runtime_set_option('gpu_enabled', False)
h.runtime_set_option('mouse_through', False)
ok,msg=h.start_html_wallpaper({str(html_path)!r})
print(ok, msg, flush=True)
if not ok: raise SystemExit(3)
time.sleep(30)
"""
            launcher = subprocess.Popen(
                [str(PYTHON), "-c", helper],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
            log_path = home / ".config" / "shangbackground" / "html_wallpaper_subprocess.log"

            def log_text() -> str:
                return log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""

            wait_for(lambda: "HTML loadFinished ok=True" in log_text(), 18, "HTML load")
            time.sleep(3.8)
            initial = log_text()
            assert "FROZEN (all displays covered)" not in initial, initial[-5000:]

            cover = subprocess.Popen(
                ["xterm", "-T", "ShangCoverageTest", "-geometry", "80x40+0+0", "-e", "sh", "-c", "sleep 25"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            wait_for(
                lambda: subprocess.run(
                    ["wmctrl", "-r", "ShangCoverageTest", "-e", "0,0,0,640,720"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode == 0,
                8,
                "partial cover window",
            )
            # A half-screen application still leaves visible desktop, so the
            # wallpaper must remain active even though another app has focus.
            time.sleep(4.0)
            assert "FROZEN (all displays covered)" not in log_text(), log_text()[-5000:]

            subprocess.run(
                ["wmctrl", "-r", "ShangCoverageTest", "-b", "add,fullscreen"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            wait_for(lambda: "FROZEN (all displays covered)" in log_text(), 10, "auto-pause freeze")

            subprocess.run(
                ["wmctrl", "-r", "ShangCoverageTest", "-b", "add,hidden"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            wait_for(lambda: "RESUMED (desktop visible)" in log_text(), 10, "auto-pause resume")
            final = log_text()
            assert "Traceback (most recent call last)" not in final
            assert "Fatal Python error" not in final

            sys.path.insert(0, str(SRC))
            old_env = os.environ.copy()
            os.environ.update(env)
            try:
                from platform_adapters import html_wallpaper as h
                h.stop_html_wallpaper()
            finally:
                os.environ.clear()
                os.environ.update(old_env)
            terminate(launcher)
            print("PASS real Linux X11 HTML auto-pause: visible -> frozen -> resumed")
            return 0
        finally:
            terminate(cover)
            terminate(openbox)
            terminate(xvfb)


if __name__ == "__main__":
    raise SystemExit(main())
