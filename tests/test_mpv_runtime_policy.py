from __future__ import annotations

from pathlib import Path


from build_tools.buildlib import mpv_runtime


def test_mpv_download_defaults_to_official_stable_release_channel():
    args = mpv_runtime.parse_args(["download", "--target", "windows", "--arch", "x86_64"])
    assert args.channel == "stable"
    assert mpv_runtime._api_url("stable").endswith("/releases/latest")
    assert mpv_runtime._api_url("development").endswith("/releases/tags/git-release")


def test_asset_patterns_cover_stable_and_development_windows_names():
    stable = {
        "assets": [
            {"name": "mpv-v0.41.0-x86_64-pc-windows-msvc.zip", "browser_download_url": "https://github.com/example"},
        ]
    }
    development = {
        "assets": [
            {"name": "mpv-v0.41.0-dev-g513d3407d-31293954660-x86_64-pc-windows-msvc.zip", "browser_download_url": "https://github.com/example"},
        ]
    }
    assert mpv_runtime.select_release_asset(stable, "windows", "x86_64")["name"].endswith("msvc.zip")
    assert mpv_runtime.select_release_asset(development, "windows", "x86_64")["name"].endswith("msvc.zip")


def test_windows_flat_payload_can_use_mpv_executable(tmp_path: Path):
    payload = tmp_path / "src" / "bin" / "mpv" / "windows" / "x86_64"
    payload.mkdir(parents=True)
    (payload / "mpv.exe").write_bytes(b"MZ")
    assert mpv_runtime.local_payload_dir(tmp_path, "windows", "x86_64") == payload


def test_windows_new_bundle_rejects_libmpv_only_flat_payload(tmp_path: Path):
    payload = tmp_path / "src" / "bin" / "mpv" / "windows" / "x86_64"
    payload.mkdir(parents=True)
    (payload / "libmpv-2.dll").write_bytes(b"MZ")
    assert mpv_runtime.local_payload_dir(tmp_path, "windows", "x86_64") is None
