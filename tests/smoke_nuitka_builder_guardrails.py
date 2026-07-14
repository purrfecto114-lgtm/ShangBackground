from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDERS = (
    ROOT / "Windows.ver" / "build_nuitka.py",
    ROOT / "Linux.ver(beta)" / "build_nuitka.py",
    ROOT / "MacOS.ver(alpha)" / "build_nuitka.py",
)


def load_builder(path: Path):
    spec = importlib.util.spec_from_file_location(f"builder_{path.parent.name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    sources = [path.read_text(encoding="utf-8") for path in BUILDERS]
    assert len(set(sources)) == 1, "Platform builders must remain synchronized"
    assert '"--low-memory",' not in sources[0], "Automatic low-memory mode must remain removed"

    windows = load_builder(BUILDERS[0])
    command, _ = windows.build_args(
        ROOT / "Windows.ver",
        "windows",
        "full",
        "standalone",
        jobs=2,
        windows_console_mode="disable",
    )
    joined = " ".join(command)
    assert "--low-memory" not in joined
    assert "--company-name=XXDZ Studio" in command
    assert "--file-description=Previous Desktop Background" in command
    assert "--product-name=Previous Desktop Background" in command
    assert "--windows-console-mode=disable" in command

    one_line = (ROOT / "WINDOWS_NUITKA_4.1.3_ONE_LINE.txt").read_text(encoding="utf-8")
    assert "--low-memory" not in one_line
    assert "XXDZ Studio" in one_line
    assert "Previous Desktop Background" in one_line
    assert "XXDZ工作室" not in one_line
    assert "上一个桌面背景" not in one_line

    for platform_dir in ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)"):
        version_info = (ROOT / platform_dir / "src" / "main_version_info.txt").read_text(encoding="utf-8")
        assert "XXDZ工作室" not in version_info
        assert "上一个桌面背景" not in version_info
        assert "XXDZ Studio" in version_info
        assert "Previous Desktop Background" in version_info

    print("Nuitka builder guardrails: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
