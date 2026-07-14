#!/usr/bin/env python3
"""Repeatable, low-resource audit for the clean rewrite source tree."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TREES = {
    "windows": ROOT / "Windows.ver",
    "linux": ROOT / "Linux.ver(beta)",
    "macos": ROOT / "MacOS.ver(alpha)",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def check_python_and_json() -> None:
    for path in ROOT.rglob("*"):
        if any(part in {".git", "__pycache__", ".ruff_cache", "dist-nuitka"} for part in path.parts):
            continue
        if path.suffix.lower() in {".py", ".pyw"}:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        elif path.suffix.lower() == ".json":
            json.loads(path.read_text(encoding="utf-8"))


def check_builders() -> None:
    hashes = set()
    count = 0
    for target, tree in TREES.items():
        builder = tree / "build_nuitka.py"
        hashes.add(hashlib.sha256(builder.read_bytes()).hexdigest())

        # Exercise the public CLI once per platform. Source parsing is already
        # performed once by check_python_and_json(), so do not multiply it by 15.
        subprocess.run(
            [sys.executable, str(builder), "--profile", "full", "--mode", "standalone", "--dry-run"],
            cwd=tree,
            check=True,
            stdout=subprocess.DEVNULL,
        )

        spec = importlib.util.spec_from_file_location(f"clean_builder_{target}", builder)
        if spec is None or spec.loader is None:
            fail(f"cannot import builder: {builder}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modes = ("standalone", "onefile") if target != "macos" else ("standalone",)
        for profile in ("lite", "full", "system"):
            for mode in modes:
                command, out_dir = module.build_args(tree, target, profile, mode)
                command_text = " ".join(command)
                effective = "full" if profile == "system" else profile
                if effective not in out_dir.parts:
                    fail(f"wrong output profile for {target}/{profile}/{mode}")
                if effective == "full" and "PySide6.QtWebEngineWidgets" not in command_text:
                    fail(f"full profile misses WebEngine: {target}/{mode}")
                if effective == "lite" and "--nofollow-import-to=PySide6.QtWebEngineWidgets" not in command:
                    fail(f"lite profile does not exclude WebEngine: {target}/{mode}")
                count += 1
    if len(hashes) != 1:
        fail("build_nuitka.py differs across platform trees")
    if count != 15:
        fail(f"expected 15 generated build commands, got {count}")


def check_requirements() -> None:
    for target, tree in TREES.items():
        base = (tree / f"requirements-{target}.txt").read_text(encoding="utf-8")
        full = (tree / f"requirements-{target}-full.txt").read_text(encoding="utf-8")
        nuitka = (tree / "requirements-nuitka.txt").read_text(encoding="utf-8")
        if "PySide6-Essentials==6.11.1" not in base:
            fail(f"missing pinned Essentials: {target}")
        if "PySide6-Addons==6.11.1" not in full:
            fail(f"missing pinned Addons: {target}")
        if "Nuitka[app]==4.1.3" not in nuitka:
            fail(f"missing pinned Nuitka: {target}")


def check_forbidden_residue() -> None:
    forbidden = (
        "py" + "webview",
        "html_" + "backends",
        "PySide6-" + "WebEngine",
        "--upx-binary",
        "include-package=webview",
    )
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in {".git", "__pycache__", ".ruff_cache"} for part in path.parts):
            continue
        if path == Path(__file__).resolve():
            continue
        if path.suffix.lower() not in {".py", ".pyw", ".sh", ".bat", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in forbidden:
            if token in text:
                hits.append(f"{path.relative_to(ROOT)}: {token}")
    if hits:
        fail("forbidden residue:\n" + "\n".join(hits))
    if (ROOT / "Windows.ver" / "upx").exists():
        fail("UPX directory still exists")


def check_shell() -> None:
    for tree in (TREES["linux"], TREES["macos"]):
        for path in tree.glob("*.sh"):
            subprocess.run(["bash", "-n", str(path)], check=True)


def run_smokes() -> None:
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_html_adapter.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_bing.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_updates.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_rounding.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_cross_platform_parity.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_functional_matrix.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_platform_contracts.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_i18n.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_feasibility.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_requested_fixes.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "tests/smoke_linux_fit_backends.py")], check=True)


def main() -> int:
    check_python_and_json()
    check_requirements()
    check_forbidden_residue()
    check_builders()
    check_shell()
    run_smokes()
    print("PASS clean rewrite audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
