"""Contract tests: wallpaper_cli failure guarantees (v1.6.1 P1-3).

The right-click handler kills the main application BEFORE applying the new
wallpaper. Before this hardening, any failure from that point (bad file,
unsupported desktop, half-written config) left the user in the worst state:
app killed, wallpaper unchanged, no relaunch, no explanation. These tests
pin the four guarantees across all three platform backends.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "platform_adapters" / "backends"
PLATFORMS = ("linux", "windows", "macos")


def _source(platform: str) -> str:
    return (SRC / platform / "wallpaper_cli.py").read_text(encoding="utf-8")


def test_save_diy_is_atomic_on_all_platforms():
    for platform in PLATFORMS:
        src = _source(platform)
        assert 'tmp_path = DIY_JSON + ".tmp"' in src, f"{platform}: no tmp write"
        assert "os.replace(tmp_path, DIY_JSON)" in src, f"{platform}: no atomic replace"
        # the old direct-write form must be gone
        assert 'with open(DIY_JSON, "w"' not in src, f"{platform}: direct write survived"


def test_load_config_tolerates_corrupt_candidates_and_tries_bak():
    for platform in PLATFORMS:
        src = _source(platform)
        assert 'CONFIG_PATH + ".bak"' in src, f"{platform}: .bak fallback missing"
        # per-candidate try (a corrupt first candidate must fall through)
        m = re.search(r"def load_config\(\):.*?return \{\}", src, re.S)
        assert m, f"{platform}: load_config not found"
        assert "except Exception" in m.group(0), f"{platform}: no per-candidate error tolerance"


def test_dead_temp_guard_removed():
    for platform in PLATFORMS:
        src = _source(platform)
        assert "TEMP_FILE" not in src, f"{platform}: dead TEMP_FILE guard survived"
        assert "temp_wallpaper_selection" not in src, f"{platform}: dead guard file name survived"


def test_post_kill_failures_restart_the_app_and_inform_the_user():
    """The structural guarantee: kill → try(<apply wallpaper + save>) →
    except(restart + dialog + return) → happy path (restart + success dialog)."""
    for platform in PLATFORMS:
        src = _source(platform)
        kill_pos = src.index("kill_all_main_processes()")
        # the try must come after the kill
        try_pos = src.index("    try:", kill_pos)
        except_pos = src.index("    except Exception as e:", try_pos)
        # restart inside the except handler
        restart_in_except = src.find("start_main_program()", except_pos)
        assert restart_in_except > 0 and restart_in_except < except_pos + 600, (
            f"{platform}: failure path does not restart the app"
        )
        # an error dialog in the failure path (platform-specific mechanism)
        failure_block = src[except_pos : restart_in_except + 400]
        assert any(marker in failure_block for marker in ("show_info_dialog", "MessageBoxW", "osascript")), (
            f"{platform}: failure path does not inform the user"
        )
        # the wallpaper application happens INSIDE the try (after kill!)
        set_pos = src.index("set_wallpaper(img)")
        assert try_pos < set_pos < except_pos, f"{platform}: set_wallpaper must run inside the post-kill try block"
        # success path still restarts afterwards (the call AFTER the
        # failure-path restart)
        success_restart = src.find("start_main_program()", restart_in_except + 1)
        assert success_restart > 0, f"{platform}: success path lost its restart"


def test_kill_still_precedes_apply():
    for platform in PLATFORMS:
        src = _source(platform)
        kill_pos = src.index("kill_all_main_processes()")
        set_pos = src.index("set_wallpaper(img)")
        assert kill_pos < set_pos, f"{platform}: kill must happen before applying"
