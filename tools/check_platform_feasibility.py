#!/usr/bin/env python3
"""Print the feasibility matrix for each source tree without native side effects."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = {"windows":"Windows.ver", "linux":"Linux.ver(beta)", "macos":"MacOS.ver(alpha)"}


def load_probe(tree: str):
    path = ROOT / tree / "src" / "platform_adapters" / "capabilities.py"
    spec = importlib.util.spec_from_file_location("shang_capabilities_" + tree.replace(".", "_").replace("(", "_").replace(")", "_"), path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.probe_capabilities


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true')
    args=parser.parse_args()
    data={name:load_probe(tree)() for name,tree in TREES.items()}
    if args.json:
        print(json.dumps(data,ensure_ascii=False,indent=2))
        return 0
    features=sorted(set().union(*(caps.keys() for caps in data.values())))
    print('| 功能 | Windows | Linux（当前会话） | macOS |')
    print('|---|---|---|---|')
    for feature in features:
        row=[]
        for platform in ('windows','linux','macos'):
            item=data[platform].get(feature,{})
            state=item.get('state','unknown')
            ready='ready' if item.get('runtime_ready') else 'not-ready'
            row.append(f"{state} / {ready}")
        print(f"| {feature} | {row[0]} | {row[1]} | {row[2]} |")
    return 0

if __name__=='__main__':
    raise SystemExit(main())
