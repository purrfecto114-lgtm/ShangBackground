#!/usr/bin/env python3
"""Verify that literal UI translation keys are complete and shared wording is stable."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)")


def literal_translation_keys(src: Path) -> set[str]:
    keys: set[str] = set()
    for path in src.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(module):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "t":
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
    return keys


def main() -> int:
    translations: dict[str, dict[str, str]] = {}
    used: dict[str, set[str]] = {}
    for tree in TREES:
        src = ROOT / tree / "src"
        translations[tree] = json.loads((src / "lang/en.json").read_text(encoding="utf-8"))
        used[tree] = literal_translation_keys(src)
        missing = sorted(used[tree] - set(translations[tree]))
        assert not missing, f"{tree}: untranslated literal keys: {missing}"

    common_used = set.intersection(*(used[tree] for tree in TREES))
    baseline = translations["Windows.ver"]
    drift = []
    for key in sorted(common_used):
        expected = baseline[key]
        for tree in TREES[1:]:
            if translations[tree][key] != expected:
                drift.append((tree, key, expected, translations[tree][key]))
    assert not drift, f"shared English translation drift: {drift[:20]}"
    print("PASS i18n completeness and shared wording parity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
