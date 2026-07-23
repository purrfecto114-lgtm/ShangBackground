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
    assert "Build Windows setup.exe (Inno Setup)" in release_workflow
    assert "python build_tools/build.py installer" in release_workflow
    assert "Install Inno Setup (Windows)" in release_workflow
    assert "SHANGBACKGROUND_ISCC" in release_workflow


def test_expected_release_assets_include_windows_installer():
    assets = release.expected_release_assets()
    metadata = release.read_metadata()
    assert metadata.windows_installer("x86_64") in assets
