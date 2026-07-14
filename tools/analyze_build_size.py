#!/usr/bin/env python3
"""Report the largest files and directory groups in a standalone build."""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:8.1f} {unit}"
        value /= 1024
    return f"{value:8.1f} GiB"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()
    root = args.path.resolve()
    if not root.exists():
        parser.error(f"not found: {root}")

    files = [(path.stat().st_size, path) for path in root.rglob("*") if path.is_file()]
    total = sum(size for size, _ in files)
    print(f"Total: {human(total)} across {len(files)} files\n")
    print("Largest files:")
    for size, path in sorted(files, reverse=True)[: args.top]:
        print(f"{human(size)}  {path.relative_to(root)}")

    groups: dict[str, int] = defaultdict(int)
    for size, path in files:
        rel = path.relative_to(root)
        key = rel.parts[0] if rel.parts else "."
        groups[key] += size
    print("\nTop-level groups:")
    for key, size in sorted(groups.items(), key=lambda item: item[1], reverse=True):
        print(f"{human(size)}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
