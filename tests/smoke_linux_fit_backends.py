#!/usr/bin/env python3
"""Linux desktop backend routing and fit-mode command contracts."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Linux.ver(beta)" / "src"
sys.path.insert(0, str(SRC))

from platform_adapters import integration as mod  # noqa: E402


def main() -> int:
    # Mode semantics must stay stable across every Linux fallback.
    assert mod._xfce_image_style_value("居中") == 1
    assert mod._xfce_image_style_value("平铺") == 2
    assert mod._xfce_image_style_value("拉伸") == 3
    assert mod._xfce_image_style_value("适应") == 4
    assert mod._xfce_image_style_value("填充") == 5
    assert mod._pcmanfm_mode("/usr/bin/pcmanfm", "填充") == "crop"
    assert mod._pcmanfm_mode("/usr/bin/pcmanfm-qt", "填充") == "fit"

    commands: list[list[str]] = []

    def fake_run(args: list[str], timeout: int = 10):
        del timeout
        commands.append(list(args))
        return 0, "", ""

    with tempfile.TemporaryDirectory() as td:
        image = Path(td) / "中文比例测试.png"
        image.write_bytes(b"x")

        # feh mappings mirror Windows semantics: fill crops, fit letterboxes,
        # stretch ignores ratio, center and tile are literal.
        with patch.object(mod.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}" if name == "feh" else None), patch.object(mod, "_run_args", side_effect=fake_run):
            expected = {
                "填充": "--bg-fill",
                "适应": "--bg-max",
                "拉伸": "--bg-scale",
                "居中": "--bg-center",
                "平铺": "--bg-tile",
            }
            for mode, option in expected.items():
                commands.clear()
                mod._LINUX_FIT_MODE = mode
                ok, _detail = mod._set_feh_or_nitrogen_wallpaper(str(image))
                assert ok and commands == [["feh", option, str(image.resolve())]], (mode, commands)

        # Nitrogen uses its documented zoom-fill/scaled/auto/centered/tiled modes.
        with patch.object(mod.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}" if name == "nitrogen" else None), patch.object(mod, "_run_args", side_effect=fake_run):
            expected = {
                "填充": "--set-zoom-fill",
                "适应": "--set-scaled",
                "拉伸": "--set-auto",
                "居中": "--set-centered",
                "平铺": "--set-tiled",
            }
            for mode, option in expected.items():
                commands.clear()
                mod._LINUX_FIT_MODE = mode
                ok, _detail = mod._set_feh_or_nitrogen_wallpaper(str(image))
                assert ok and commands == [["nitrogen", option, "--save", str(image.resolve())]], (mode, commands)

        # Session routing must not accept an unrelated gsettings write on Xfce.
        calls: list[str] = []
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE", "DESKTOP_SESSION": "xfce"}, clear=False), patch.object(mod, "_set_xfce_wallpaper", side_effect=lambda _p: (calls.append("xfce") or True, "ok")), patch.object(mod, "_set_gnome_wallpaper", side_effect=lambda _p: (calls.append("gnome") or True, "ok")), patch.object(mod, "_set_pcmanfm_wallpaper", side_effect=lambda _p: (calls.append("pcman") or True, "ok")), patch.object(mod, "_set_feh_or_nitrogen_wallpaper", side_effect=lambda _p: (calls.append("fallback") or True, "ok")):
            mod._set_linux_wallpaper(str(image))
        assert calls == ["xfce"], calls

        # Generic Openbox should use explicit setters, not a coincidentally
        # installed gsettings binary that does not own the desktop surface.
        calls.clear()
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "Openbox", "DESKTOP_SESSION": "openbox", "KDE_FULL_SESSION": "", "WAYLAND_DISPLAY": ""}, clear=False), patch.object(mod.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"gsettings", "feh"} else None), patch.object(mod, "_set_gnome_wallpaper", side_effect=lambda _p: (calls.append("gnome") or True, "ok")), patch.object(mod, "_set_pcmanfm_wallpaper", side_effect=lambda _p: (calls.append("pcman") or False, "missing")), patch.object(mod, "_set_feh_or_nitrogen_wallpaper", side_effect=lambda _p: (calls.append("feh") or True, "ok")):
            mod._set_linux_wallpaper(str(image))
        assert calls == ["pcman", "feh"], calls

        # Xfce config writes every discovered monitor/workspace image-style key.
        listed = "\n".join([
            "/backdrop/screen0/monitorHDMI-1/workspace0/last-image",
            "/backdrop/screen0/monitorHDMI-1/workspace0/image-style",
            "/backdrop/screen0/monitorDP-1/workspace0/last-image",
        ])
        commands.clear()

        def xfce_run(args: list[str], timeout: int = 10):
            del timeout
            commands.append(list(args))
            if args[-1] == "-l":
                return 0, listed, ""
            return 0, "", ""

        with patch.object(mod.shutil, "which", return_value="/usr/bin/xfconf-query"), patch.object(mod, "_run_args", side_effect=xfce_run):
            ok, detail = mod._set_xfce_fit_mode("填充")
        assert ok and "updated" in detail
        writes = [cmd for cmd in commands if "-s" in cmd and cmd[-1] == "5"]
        assert any(any("monitorHDMI-1/workspace0/image-style" in part for part in cmd) for cmd in writes)
        assert any(any("monitorDP-1/workspace0/image-style" in part for part in cmd) for cmd in writes)

    print("PASS Linux fit/backend routing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
