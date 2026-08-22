from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path
import struct
import zipfile

import pytest

from build_tools.buildlib import mpv_runtime


def _fake_pe(machine: int) -> bytes:
    payload = bytearray(0x86)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, machine)
    return bytes(payload)


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


def test_stable_windows_x86_asset_pattern_accepts_mingw_archive():
    release = {
        "assets": [
            {
                "name": "mpv-v0.41.0-i686-w64-mingw32.zip",
                "browser_download_url": "https://github.com/mpv-player/mpv/releases/download/v0.41.0/example.zip",
            }
        ]
    }
    selected = mpv_runtime.select_release_asset(release, "windows", "x86")
    assert selected["name"] == "mpv-v0.41.0-i686-w64-mingw32.zip"


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


def test_windows_installer_accepts_one_level_nested_mingw_artifact(tmp_path: Path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mpv.exe", _fake_pe(0x014C))

    archive = tmp_path / "mpv-v0.41.0-i686-w64-mingw32.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mpv-git-i686/mpv-v0.41.0-i686-w64-mingw32.zip", inner.getvalue())

    installed = mpv_runtime.install_downloaded_runtime(
        tmp_path,
        {"tag_name": "v0.41.0", "name": "v0.41.0"},
        {
            "name": archive.name,
            "browser_download_url": "https://github.com/mpv-player/mpv/releases/download/v0.41.0/" + archive.name,
            "size": archive.stat().st_size,
        },
        archive,
        target="windows",
        arch="x86",
        channel="stable",
        sha256="test",
    )

    assert (installed.path / "mpv.exe").is_file()


def test_nested_mpv_archive_keeps_safe_extraction_guards(tmp_path: Path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../mpv.exe", _fake_pe(0x014C))

    archive = tmp_path / "mpv-v0.41.0-i686-w64-mingw32.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mpv-v0.41.0-i686-w64-mingw32.zip", inner.getvalue())

    with pytest.raises(RuntimeError, match="Unsafe path"):
        mpv_runtime.install_downloaded_runtime(
            tmp_path,
            {"tag_name": "v0.41.0", "name": "v0.41.0"},
            {"name": archive.name, "browser_download_url": "https://github.com/example", "size": archive.stat().st_size},
            archive,
            target="windows",
            arch="x86",
            channel="stable",
            sha256="test",
        )


def test_runtime_manifest_must_list_every_flattened_dependency(tmp_path: Path):
    payload = tmp_path / "bin" / "mpv"
    payload.mkdir(parents=True)
    (payload / "mpv.exe").write_bytes(_fake_pe(0x8664))
    (payload / "avcodec-61.dll").write_bytes(_fake_pe(0x8664))
    (payload / "runtime.json").write_text(
        '{"files": {"mpv.exe": {"size": 134, "sha256": ""}}}\n',
        encoding="utf-8",
    )

    ok, errors = mpv_runtime.verify_bundled_runtime_output(tmp_path, "windows", "x86_64")

    assert not ok
    assert any("manifest file is not listed" in error for error in errors)


def test_flattened_runtime_accepts_manifested_exe_and_dependencies(tmp_path: Path):
    payload = tmp_path / "bin" / "mpv"
    payload.mkdir(parents=True)
    exe = payload / "mpv.exe"
    dependency = payload / "avcodec-61.dll"
    exe.write_bytes(_fake_pe(0x8664))
    dependency.write_bytes(_fake_pe(0x8664))
    records = {
        item.name: {"size": item.stat().st_size, "sha256": hashlib.sha256(item.read_bytes()).hexdigest()}
        for item in (exe, dependency)
    }
    (payload / "runtime.json").write_text(json.dumps({"files": records}) + "\n", encoding="utf-8")

    ok, errors = mpv_runtime.verify_bundled_runtime_output(tmp_path, "windows", "x86_64")

    assert ok, errors


def test_bundled_runtime_requires_flat_mpv_exe_and_matching_pe_architecture(tmp_path: Path):
    payload = tmp_path / "bin" / "mpv"
    payload.mkdir(parents=True)
    (payload / "nested").mkdir()
    (payload / "nested" / "mpv.exe").write_bytes(_fake_pe(0x014C))
    (payload / "runtime.json").write_text('{"files": {}}\n', encoding="utf-8")

    ok, errors = mpv_runtime.verify_bundled_runtime_output(tmp_path, "windows", "x86_64")

    assert not ok
    assert any("mpv.exe is missing" in error for error in errors)
