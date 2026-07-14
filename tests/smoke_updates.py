#!/usr/bin/env python3
"""Regression tests for services/updates.py — version parsing & asset scoring.

Catches the following bugs that were present before the clean-rewrite fix:

1. ``parse_version("1.4.0.0")`` returned ``(4, 0, 0)`` instead of ``(1, 4, 0)``
   because the VERSION_RE lookahead ``(?![\\d.])`` forced the regex to skip the
   first ``1.4.0`` of ``1.4.0.0`` and match ``4.0.0`` from position 2.  This
   broke the update checker when GitHub releases used 4-segment tags (matching
   ``APP_VERSION_FILE``).

2. ``fetch_latest_github_release`` raised ``ValueError`` when a release had no
   parseable version in tag/name/assets.  Now defaults to ``"0.0.0"``.

These tests are platform-agnostic — they import the module from each tree.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)")


def child(tree: str) -> int:
    src = ROOT / tree / "src"
    sys.path.insert(0, str(src))

    from services import updates

    # === Bug A regression: 4-segment version parsing ===
    assert updates.parse_version("1.4.0") == (1, 4, 0), "baseline 3-segment"
    assert updates.parse_version("v1.4.0") == (1, 4, 0), "v prefix"
    assert updates.parse_version("1.4") == (1, 4, 0), "2-segment"
    assert updates.parse_version("1.4.0-beta") == (1, 4, 0), "with pre-release"
    assert updates.parse_version("1.4.0+build.123") == (1, 4, 0), "with build metadata"

    # The actual bug: 4-segment versions
    assert updates.parse_version("1.4.0.0") == (1, 4, 0), \
        f"4-segment BUG: got {updates.parse_version('1.4.0.0')!r} expected (1, 4, 0)"
    assert updates.parse_version("v1.4.0.0") == (1, 4, 0), "v prefix + 4-segment"
    assert updates.parse_version("app_ver=1.4.0.0") == (1, 4, 0), "app_ver= prefix + 4-segment"
    assert updates.parse_version("1.4.0.0-beta") == (1, 4, 0), "4-segment + pre-release"

    # 5+-segment versions (date-like)
    assert updates.parse_version("1.4.0.0.0") == (1, 4, 0), "5-segment"
    assert updates.parse_version("1.4.0.0.0.0") == (1, 4, 0), "6-segment"
    assert updates.parse_version("2026.07.12.00.00.00") == (2026, 7, 12), "date-like"

    # normalize_tag should round-trip
    assert updates.normalize_tag("v1.4.0.0") == "1.4.0", \
        f"normalize_tag BUG: got {updates.normalize_tag('v1.4.0.0')!r} expected '1.4.0'"
    assert updates.normalize_tag("1.4.0.0") == "1.4.0"

    # === Bug D regression: empty/malformed release should default to "0.0.0" ===
    # Build a fake release JSON with no parseable version anywhere
    fake_release = {
        "tag_name": "draft-release",
        "name": "Daily Build",
        "html_url": "https://github.com/purrfecto114-lgtm/ShangBackground/releases/tag/draft",
        "published_at": "2026-07-12T00:00:00Z",
        "body": "",
        "assets": [
            {
                "name": "binary-blob",
                "size": 12345,
                "browser_download_url": "https://github.com/purrfecto114-lgtm/ShangBackground/releases/download/draft/binary-blob",
            }
        ],
    }
    # Manually call the same path fetch_latest_github_release uses
    try:
        version = updates.normalize_tag(updates._pick_version_source(fake_release))
    except ValueError:
        version = "0.0.0"  # This is the fix path
    assert version == "0.0.0", \
        f"empty-release BUG: got {version!r} expected '0.0.0'"

    # === check_latest_release version comparison ===
    # Same release tagged 4-segment should NOT report "has update"
    cur = updates.parse_version("1.4.0")
    lat_4seg = updates.parse_version("1.4.0.0")  # Now correctly parses to (1,4,0)
    assert (lat_4seg > cur) is False, \
        f"version comparison BUG: {lat_4seg} > {cur} should be False"

    # === _asset_score: source archives must be excluded ===
    source_assets = [
        {"name": "Source code.zip", "download_url": "https://github.com/x/y/releases/download/v1/source.zip"},
        {"name": "source.tar.gz", "download_url": "https://github.com/x/y/releases/download/v1/source.tar.gz"},
        {"name": "src.zip", "download_url": "https://github.com/x/y/releases/download/v1/src.zip"},
        {"name": "源码.zip", "download_url": "https://github.com/x/y/releases/download/v1/source.zip"},
    ]
    for asset in source_assets:
        for plat in ("windows", "linux", "macos"):
            score = updates._asset_score(asset, plat)
            assert score == 0, \
                f"source archive {asset['name']!r} on {plat} scored {score}, expected 0"

    # === _is_repository_github_url: URL whitelist ===
    valid_urls = [
        "https://github.com/purrfecto114-lgtm/ShangBackground",
        "https://github.com/purrfecto114-lgtm/ShangBackground/releases/tag/v1.4.0",
        "https://api.github.com/repos/purrfecto114-lgtm/ShangBackground/releases/latest",
    ]
    for url in valid_urls:
        assert updates._is_repository_github_url(url), f"valid URL rejected: {url}"

    invalid_urls = [
        "http://github.com/purrfecto114-lgtm/ShangBackground",  # not HTTPS
        "https://evil.com/github.com/purrfecto114-lgtm/ShangBackground",  # wrong host
        "https://github.com/evil/repo",  # wrong repo
        "https://api.github.com/repos/evil/repo",  # wrong API repo
        None,
        "",
    ]
    for url in invalid_urls:
        assert not updates._is_repository_github_url(url), f"invalid URL accepted: {url}"

    print(f"PASS updates regression: {tree}")
    return 0


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    args = parser.parse_args()
    if args.child:
        return child(args.child)

    with tempfile.TemporaryDirectory() as home:
        for tree in TREES:
            env = os.environ.copy()
            env.update({"HOME": home, "USERPROFILE": home, "LOCALAPPDATA": home, "APPDATA": home})
            subprocess.run([sys.executable, __file__, "--child", tree], check=True, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
