from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import re
import sys
import tarfile
import zipfile

import pytest


SCRIPT = Path(".github/scripts/release.py").resolve()
SPEC = importlib.util.spec_from_file_location("shangbackground_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def test_release_metadata_matches_project_files():
    metadata = release.validate_metadata()

    assert re.fullmatch(r"\d+\.\d+\.\d+", metadata.version)
    assert metadata.tag == f"v{metadata.version}"
    assert metadata.source_archive.endswith("-source.zip")
    assert metadata.windows_installer("x86_64") == f"ShangBackground-{metadata.tag}-windows-x86_64-setup.exe"


def test_linux_binary_archive_preserves_bundle_layout(tmp_path: Path):
    source = tmp_path / "standalone"
    executable = source / "ShangBackground" / "ShangBackground"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")
    executable.chmod(0o755)
    (executable.parent / "_internal").mkdir()
    (executable.parent / "_internal" / "data.txt").write_text("payload", encoding="utf-8")

    archive_path = release.package_binary(source, tmp_path / "assets", "linux", "x86_64")

    assert archive_path.name == release.read_metadata().binary_archive("linux", "x86_64")
    with tarfile.open(archive_path, "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
        root = f"ShangBackground-{release.read_metadata().tag}-linux-x86_64"
        assert f"{root}/ShangBackground/ShangBackground" in names
        assert f"{root}/ShangBackground/_internal/data.txt" in names


def test_windows_binary_archive_requires_expected_executable(tmp_path: Path):
    source = tmp_path / "standalone"
    source.mkdir()

    with pytest.raises(release.ReleaseError, match="Expected packaged executable"):
        release.package_binary(source, tmp_path / "assets", "windows", "x86_64")


def test_source_archive_is_curated_and_single_root(tmp_path: Path):
    archive_path = release.create_source_archive(tmp_path)
    metadata = release.read_metadata()

    with zipfile.ZipFile(archive_path) as archive:
        names = {name.rstrip("/") for name in archive.namelist()}
    roots = {name.split("/", 1)[0] for name in names}
    root = f"ShangBackground-{metadata.tag}"

    assert roots == {root}
    assert f"{root}/src/app/version.py" in names
    assert f"{root}/build_tools/build.py" in names
    assert f"{root}/tests" not in names
    assert not any("/__pycache__/" in f"/{name}/" for name in names)
    assert not any(name.startswith(f"{root}/.github/") for name in names)


def test_checksums_are_sorted_and_correct(tmp_path: Path):
    first = tmp_path / "b.bin"
    second = tmp_path / "a.bin"
    first.write_bytes(b"beta")
    second.write_bytes(b"alpha")

    checksum_path = release.write_checksums(tmp_path, require_complete=False)
    lines = checksum_path.read_text(encoding="utf-8").splitlines()

    assert [line.split("  ", 1)[1] for line in lines] == ["a.bin", "b.bin"]
    assert lines[0].startswith(hashlib.sha256(b"alpha").hexdigest())
    assert lines[1].startswith(hashlib.sha256(b"beta").hexdigest())


def test_standard_workflows_use_pinned_major_actions_and_minimal_permissions():
    workflows = Path(".github/workflows")
    ci = (workflows / "ci.yml").read_text(encoding="utf-8")
    release_workflow = (workflows / "release.yml").read_text(encoding="utf-8")
    codeql = (workflows / "codeql.yml").read_text(encoding="utf-8")
    dependency_review = (workflows / "dependency-review.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v7" in ci
    assert "actions/setup-python@v7" in ci
    assert "permissions:\n  contents: read" in ci
    assert "actions/upload-artifact@v7" in release_workflow
    assert "actions/download-artifact@v8" in release_workflow
    assert "contents: write" in release_workflow
    assert "github/codeql-action/init@v4" in codeql
    assert "github/codeql-action/analyze@v4" in codeql
    assert "actions/dependency-review-action@v5" in dependency_review


def test_release_workflow_builds_windows_installer():
    """``release.yml`` must produce the Inno Setup ``setup.exe`` on Windows
    so it can be included as a release asset alongside the binary archive."""
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Build Windows setup.exe" in release_workflow
    assert "python build_tools/build.py installer" in release_workflow
    assert "Install Inno Setup" in release_workflow
    assert "SHANGBACKGROUND_ISCC" in release_workflow
    # The installer step must NOT pin a hard-coded Inno Setup download URL,
    # which 404s whenever JR Software ships a new version. Use Chocolatey
    # (pre-installed on GitHub Actions Windows runners) instead.
    assert "files.jrsoftware.org/is/6/innosetup-" not in release_workflow
    assert "choco install innosetup" in release_workflow


def test_release_workflow_uses_nuitka_full_with_upx():
    """``release.yml`` must use Nuitka (not PyInstaller) with the full profile
    and UPX compression for all three platforms. The full profile includes
    all features (video, html, bing, hotkeys, updates, fonts); UPX reduces
    binary size 30-60% on Windows/Linux."""
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "--tool nuitka" in release_workflow
    assert "--profile full" in release_workflow
    assert "--upx" in release_workflow
    assert "--mpv-runtime system" in release_workflow
    # The release archive path must point to dist-nuitka, not dist-pyinstaller.
    assert "dist-nuitka/" in release_workflow
    assert "dist-pyinstaller/" not in release_workflow


def test_release_workflow_installs_upx():
    """``release.yml`` must install UPX on both Windows (via Chocolatey) and
    Linux (via apt) so the Nuitka --upx flag has a binary to invoke."""
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    # Windows: Chocolatey installs upx alongside innosetup.
    assert "choco install innosetup upx" in release_workflow
    assert "SHANGBACKGROUND_UPX_BINARY" in release_workflow
    # Linux: apt installs upx-ucl (the Debian/Ubuntu package name for UPX).
    assert "upx-ucl" in release_workflow


def test_release_workflow_installs_libmpv_on_linux():
    """``release.yml`` must install libmpv on Linux so the full video feature
    can link against the system MPV runtime during the frozen-runtime check.
    libmpv2 is included in the main apt-get install step (not a separate
    redundant step)."""
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "libmpv2" in release_workflow
    # The redundant "Install libmpv" step was removed; libmpv2 is now
    # installed in the main Linux packaging prerequisites step.
    assert "Install libmpv (Linux, full video feature)" not in release_workflow


def test_release_workflow_does_not_force_prerelease():
    """``release.yml`` must NOT pass --prerelease so releases are full by default."""
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "--prerelease" not in release_workflow


def test_release_workflow_smoke_tests_frozen_binary():
    """``release.yml`` must run the frozen binary with --version after the
    build to verify it actually starts and reports the correct version.
    This catches broken Nuitka builds that compile but fail at startup."""
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Smoke-test frozen binary" in release_workflow
    assert "--version" in release_workflow


def test_release_workflow_uses_ldconfig_for_libxcb_cursor():
    """``release.yml`` must use ldconfig to locate libxcb-cursor.so.0
    (not a hard-coded /usr/lib/x86_64-linux-gnu/ path) so the post-build
    copy works on both amd64 and arm64."""
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "ldconfig -p" in release_workflow
    # The old hard-coded path must NOT be present.
    assert "cp -v /usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0" not in release_workflow


def test_release_workflow_installs_xdg_desktop_portal():
    """``release.yml`` must install xdg-desktop-portal on Linux so the
    Wayland GlobalShortcuts portal backend is available for testing."""
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "xdg-desktop-portal" in release_workflow


def test_release_workflow_installs_linux_qt_xcb_prerequisites():
    """``release.yml`` must install the full Qt XCB runtime library set on
    Linux so the frozen-runtime smoke test does not fail on missing
    ``libxcb-shape.so.0`` or related shared libraries."""
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Install Linux packaging prerequisites" in release_workflow
    assert "libxcb-shape0" in release_workflow
    assert "libxcb-cursor0" in release_workflow
    assert "libxkbcommon-x11-0" in release_workflow


def test_expected_release_assets_include_windows_installer():
    assets = release.expected_release_assets()
    metadata = release.read_metadata()
    assert metadata.windows_installer("x86_64") in assets
