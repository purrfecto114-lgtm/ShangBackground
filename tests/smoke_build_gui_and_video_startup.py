#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TREES = {
    "Windows.ver": ("windows", True),
    "Linux.ver(beta)": ("linux", False),
    "MacOS.ver(alpha)": ("macos", False),
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for index, (tree, (platform_id, supports_wmv)) in enumerate(TREES.items()):
        source = ROOT / tree
        sys.path.insert(0, str(source / "src"))
        try:
            config = load_module(source / "src/app/config.py", f"config_{index}")
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                mp4 = folder / "startup.mp4"
                mp4.write_bytes(b"test")
                assert config.is_supported_video_path(mp4)
                unsupported = folder / "startup.txt"
                unsupported.write_bytes(b"test")
                assert not config.is_supported_video_path(unsupported)
                wmv = folder / "startup.wmv"
                wmv.write_bytes(b"test")
                assert config.is_supported_video_path(wmv) is supports_wmv
        finally:
            sys.path.pop(0)

        entry_text = (source / "src/app/entry.py").read_text(encoding="utf-8")
        assert "is_supported_video_path(_video_path)" in entry_text
        assert "set(get_video_filetypes())" not in entry_text

        gui = load_module(source / "build_gui.py", f"build_gui_{index}")
        assert gui.EXPECTED_PLATFORM == platform_id
        command = gui.build_command("full", "standalone", 2, install_dependencies=False)
        assert Path(command[1]).resolve() == (source / "build_nuitka.py").resolve()
        if platform_id == "macos":
            try:
                gui.build_command("full", "onefile", 2)
            except ValueError:
                pass
            else:
                raise AssertionError("macOS GUI must not offer unsupported onefile builds")
        else:
            assert "--mode" in gui.build_command("full", "onefile", 2)
        assert "--skip-install" in command
        assert "--target" not in command
        foreign_tree_names = set(TREES) - {tree}
        joined = " ".join(command)
        assert not any(name in joined for name in foreign_tree_names)
        if platform_id == "windows":
            assert command[-2:] == ["--windows-console-mode", "disable"]
        else:
            assert "--windows-console-mode" not in command

    print("PASS build GUI local-only commands and startup video validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
