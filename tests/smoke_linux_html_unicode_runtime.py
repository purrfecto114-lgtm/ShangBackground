#!/usr/bin/env python3
"""Real Linux QtWebEngine lifecycle test using a Chinese-named local HTML file."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Linux.ver(beta)" / "src"


def child() -> int:
    sys.path.insert(0, str(SRC))
    from platform_adapters import html_wallpaper as html

    html.stop_html_wallpaper()
    local = Path(os.environ["SHANG_HTML_TEST_FILE"])
    html.runtime_set_option("auto_pause", True)
    html.runtime_set_option("gpu_enabled", False)
    html.runtime_set_option("mouse_through", False)
    ok, message = html.start_html_wallpaper(str(local))
    assert ok, message
    assert html.is_html_wallpaper_running(), message
    pid = 0
    try:
        deadline = time.monotonic() + 15.0
        log_text = ""
        while time.monotonic() < deadline:
            log_path = Path(html.get_subprocess_log_path())
            if log_path.exists():
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
            loaded = "HTML loadFinished ok=True" in log_text
            visibility_checked = "auto-pause: desktop coverage unavailable; keeping HTML active" in log_text
            first_frame_visible = "HTML surface visible after " in log_text
            if loaded and visibility_checked and first_frame_visible:
                break
            assert html.is_html_wallpaper_running(), log_text[-4000:]
            time.sleep(0.25)
        assert "HTML loadFinished ok=True" in log_text, log_text[-5000:]
        assert "auto-pause: desktop coverage unavailable; keeping HTML active" in log_text, log_text[-5000:]
        assert "HTML surface visible after " in log_text, log_text[-5000:]
        assert "Traceback (most recent call last)" not in log_text
        assert "Fatal Python error" not in log_text

        state = html._read_state()
        assert int(state.get("pid", 0)) > 0
        assert state.get("path") == str(local), state
        pid = int(state["pid"])

        # Keep the renderer alive long enough to cross multiple option/visibility
        # polling intervals; unsupported/offscreen visibility must not freeze/crash it.
        time.sleep(6.5)
        assert html.is_html_wallpaper_running()
    finally:
        html.stop_html_wallpaper()
    if pid:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and html._is_process_alive(pid):
            time.sleep(0.1)
        assert not html._is_process_alive(pid)
    assert not html.is_html_wallpaper_running()
    print(json.dumps({"path": str(local), "pid": pid, "log": html.get_subprocess_log_path()}, ensure_ascii=False))
    return 0


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shang-html-linux-") as td:
        home = Path(td)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        html_path = home / "中文动态壁纸_完整测试.html"
        html_path.write_text(
            """<!doctype html><html><head><meta charset='utf-8'><title>中文动态壁纸运行测试</title></head>
<body><canvas id='c' width='320' height='180'></canvas><script>
let n=0; const c=document.getElementById('c'), x=c.getContext('2d');
function tick(){n++;x.fillStyle=`hsl(${n%360} 50% 45%)`;x.fillRect(0,0,c.width,c.height);requestAnimationFrame(tick)}
requestAnimationFrame(tick); document.body.dataset.ready='是';
</script></body></html>""",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update({
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home),
            "APPDATA": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_RUNTIME_DIR": str(runtime),
            "QT_QPA_PLATFORM": "offscreen",
            # The CI container runs as root. These flags are test-environment
            # requirements only; the application does not disable the sandbox.
            "QTWEBENGINE_DISABLE_SANDBOX": "1",
            "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-gpu --disable-software-rasterizer",
            "SHANG_HTML_TEST_FILE": str(html_path),
        })
        result = subprocess.run(
            [sys.executable, __file__, "--child"],
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=40,
            check=False,
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            return result.returncode or 1
        print(result.stdout.strip())
        print("PASS real Linux QtWebEngine Unicode HTML lifecycle")
        return 0


if __name__ == "__main__":
    if "--child" in sys.argv:
        raise SystemExit(child())
    raise SystemExit(main())
