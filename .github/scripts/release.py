#!/usr/bin/env python3
"""Release metadata, archive and checksum helpers for GitHub Actions.

This module intentionally uses only the Python standard library so the release
workflow can validate metadata before installing application dependencies.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_NAME = "ShangBackground"
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
TARGETS = ("windows", "linux", "macos")
ARCHES = ("x86_64", "arm64")

SOURCE_DIRECTORIES = ("src", "build_tools", "requirements", "docs", "fonts")
SOURCE_FILES = (
    ".gitignore",
    "LICENSE",
    "NOTICE",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "pyrightconfig.json",
)
EXCLUDED_NAMES = {
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build-generated",
    "build-logs",
    "build-pyinstaller",
    "dist-nuitka",
    "dist-pyinstaller",
    "dist-release",
    "tests",
    "VALIDATION_ARTIFACTS",
}
EXCLUDED_SUFFIXES = (".pyc", ".pyo")


class ReleaseError(RuntimeError):
    """Raised when release inputs are inconsistent or unsafe."""


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    version: str

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def windows_version(self) -> str:
        return f"{self.version}.0"

    @property
    def title(self) -> str:
        return f"{APP_NAME} {self.tag}"

    @property
    def source_archive(self) -> str:
        return f"{APP_NAME}-{self.tag}-source.zip"

    def binary_archive(self, target: str, arch: str) -> str:
        extension = "zip" if target == "windows" else "tar.gz"
        return f"{APP_NAME}-{self.tag}-{target}-{arch}.{extension}"


def _load_assignment(path: Path, variable: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == variable for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        raise ReleaseError(f"{variable} must be a string literal in {path.relative_to(PROJECT_ROOT)}")
    raise ReleaseError(f"{variable} is missing from {path.relative_to(PROJECT_ROOT)}")


def read_metadata() -> ReleaseMetadata:
    version = _load_assignment(PROJECT_ROOT / "src" / "app" / "version.py", "APP_VERSION")
    if not SEMVER_RE.fullmatch(version):
        raise ReleaseError(f"APP_VERSION must use strict major.minor.patch form, got {version!r}")
    return ReleaseMetadata(version)


def _run(command: list[str], *, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise ReleaseError(f"Command failed: {' '.join(command)}\n{detail}")
    return completed


def validate_metadata() -> ReleaseMetadata:
    metadata = read_metadata()
    version_info = (PROJECT_ROOT / "src" / "main_version_info.txt").read_text(encoding="utf-8-sig")
    tuple_text = ", ".join((*metadata.version.split("."), "0"))
    expected_fragments = (
        f"filevers=({tuple_text})",
        f"prodvers=({tuple_text})",
        f"StringStruct(u'FileVersion', u'{metadata.windows_version}')",
        f"StringStruct(u'ProductVersion', u'{metadata.windows_version}')",
    )
    missing = [fragment for fragment in expected_fragments if fragment not in version_info]
    if missing:
        raise ReleaseError(
            "src/main_version_info.txt does not match APP_VERSION; missing: " + ", ".join(missing)
        )

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    badge = f"version-{metadata.tag}-"
    if badge not in readme:
        raise ReleaseError(f"README.md version badge must contain {badge!r}")

    reported = _run([sys.executable, "src/main.py", "--version"]).stdout.strip()
    if reported != metadata.version:
        raise ReleaseError(f"Runtime reports {reported!r}, expected {metadata.version!r}")

    required = [
        PROJECT_ROOT / "src" / "main.pyw",
        PROJECT_ROOT / "build_tools" / "build.py",
        PROJECT_ROOT / "requirements",
        PROJECT_ROOT / "LICENSE",
        PROJECT_ROOT / "NOTICE",
    ]
    absent = [os.fspath(path.relative_to(PROJECT_ROOT)) for path in required if not path.exists()]
    if absent:
        raise ReleaseError("Required release inputs are missing: " + ", ".join(absent))
    return metadata


def write_github_output(path: Path, metadata: ReleaseMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"version={metadata.version}\n")
        stream.write(f"tag={metadata.tag}\n")
        stream.write(f"title={metadata.title}\n")
        stream.write(f"source_archive={metadata.source_archive}\n")


def _safe_relative(path: Path, root: Path) -> PurePosixPath:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ReleaseError(f"Path escapes release root: {path}") from error
    result = PurePosixPath(relative.as_posix())
    if result.is_absolute() or ".." in result.parts:
        raise ReleaseError(f"Unsafe archive path: {result}")
    return result


def _archive_mode(path: Path) -> int:
    mode = stat.S_IMODE(path.lstat().st_mode)
    if path.is_dir():
        return mode or 0o755
    return mode or 0o644


def _validate_symlink(path: Path, root: Path) -> None:
    if not path.is_symlink():
        return
    target = os.readlink(path)
    if os.path.isabs(target):
        raise ReleaseError(f"Archive contains an absolute symbolic link: {path} -> {target}")
    resolved = (path.parent / target).resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ReleaseError(f"Archive symbolic link escapes its bundle: {path} -> {target}") from error


def _zip_directory(source: Path, destination: Path, top_level: str) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*"), key=lambda item: item.as_posix().casefold()):
            _validate_symlink(path, source)
            relative = _safe_relative(path, source)
            archive_name = PurePosixPath(top_level) / relative
            if path.is_dir():
                info = zipfile.ZipInfo(f"{archive_name.as_posix().rstrip('/')}/")
                info.create_system = 3
                info.external_attr = (_archive_mode(path) | stat.S_IFDIR) << 16
                archive.writestr(info, b"")
            elif path.is_symlink():
                info = zipfile.ZipInfo(archive_name.as_posix())
                info.create_system = 3
                info.external_attr = (0o777 | stat.S_IFLNK) << 16
                archive.writestr(info, os.readlink(path).encode("utf-8"))
            elif path.is_file():
                info = zipfile.ZipInfo.from_file(path, arcname=archive_name.as_posix())
                info.create_system = 3
                info.external_attr = (_archive_mode(path) | stat.S_IFREG) << 16
                with path.open("rb") as stream:
                    archive.writestr(info, stream.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _tar_directory(source: Path, destination: Path, top_level: str) -> None:
    with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=9) as archive:
        root_info = archive.gettarinfo(os.fspath(source), arcname=top_level)
        archive.addfile(root_info)
        for path in sorted(source.rglob("*"), key=lambda item: item.as_posix().casefold()):
            _validate_symlink(path, source)
            relative = _safe_relative(path, source)
            archive.add(path, arcname=(PurePosixPath(top_level) / relative).as_posix(), recursive=False)


def _expected_bundle_root(source: Path, target: str) -> Path:
    if target == "windows":
        executable = source / APP_NAME / f"{APP_NAME}.exe"
    elif target == "macos":
        executable = source / f"{APP_NAME}.app" / "Contents" / "MacOS" / APP_NAME
    else:
        executable = source / APP_NAME / APP_NAME
    if not executable.is_file():
        raise ReleaseError(f"Expected packaged executable is missing: {executable}")
    return source


def package_binary(source: Path, output_dir: Path, target: str, arch: str) -> Path:
    metadata = validate_metadata()
    if target not in TARGETS:
        raise ReleaseError(f"Unsupported target: {target}")
    if arch not in ARCHES:
        raise ReleaseError(f"Unsupported architecture: {arch}")
    source = source.resolve()
    if not source.is_dir():
        raise ReleaseError(f"Build output is not a directory: {source}")
    _expected_bundle_root(source, target)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / metadata.binary_archive(target, arch)
    if destination.exists():
        destination.unlink()
    top_level = f"{APP_NAME}-{metadata.tag}-{target}-{arch}"
    if target == "windows":
        _zip_directory(source, destination, top_level)
    else:
        _tar_directory(source, destination, top_level)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ReleaseError(f"Binary archive was not created: {destination}")
    return destination


def _excluded_source_path(path: Path) -> bool:
    relative = _safe_relative(path, PROJECT_ROOT)
    if any(part in EXCLUDED_NAMES for part in relative.parts):
        return True
    return path.suffix.lower() in EXCLUDED_SUFFIXES


def _iter_source_files() -> list[Path]:
    selected: list[Path] = []
    for name in SOURCE_FILES:
        path = PROJECT_ROOT / name
        if not path.is_file():
            raise ReleaseError(f"Required source archive file is missing: {name}")
        selected.append(path)
    for name in SOURCE_DIRECTORIES:
        directory = PROJECT_ROOT / name
        if not directory.is_dir():
            raise ReleaseError(f"Required source archive directory is missing: {name}")
        for path in directory.rglob("*"):
            if path.is_file() and not _excluded_source_path(path):
                selected.append(path)
    return sorted(set(selected), key=lambda item: item.as_posix().casefold())


def create_source_archive(output_dir: Path) -> Path:
    metadata = validate_metadata()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / metadata.source_archive
    top_level = f"{APP_NAME}-{metadata.tag}"
    if destination.exists():
        destination.unlink()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in _iter_source_files():
            relative = _safe_relative(path, PROJECT_ROOT)
            info = zipfile.ZipInfo.from_file(path, arcname=(PurePosixPath(top_level) / relative).as_posix())
            info.create_system = 3
            info.external_attr = (_archive_mode(path) | stat.S_IFREG) << 16
            with path.open("rb") as stream:
                archive.writestr(info, stream.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ReleaseError(f"Source archive was not created: {destination}")
    return destination


def _validate_archive_member(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or path.is_absolute() or ".." in path.parts:
        raise ReleaseError(f"Unsafe source archive member: {name!r}")
    return path


def verify_source_archive(archive_path: Path) -> None:
    metadata = validate_metadata()
    expected_root = f"{APP_NAME}-{metadata.tag}"
    forbidden = EXCLUDED_NAMES
    required = {
        f"{expected_root}/src/main.py",
        f"{expected_root}/src/main.pyw",
        f"{expected_root}/src/app/version.py",
        f"{expected_root}/build_tools/build.py",
        f"{expected_root}/requirements/base.txt",
        f"{expected_root}/README.md",
        f"{expected_root}/LICENSE",
        f"{expected_root}/NOTICE",
    }
    with zipfile.ZipFile(archive_path) as archive:
        members = [_validate_archive_member(info.filename) for info in archive.infolist()]
        roots = {member.parts[0] for member in members if member.parts}
        if roots != {expected_root}:
            raise ReleaseError(f"Source archive must contain exactly one top-level directory: {expected_root}")
        member_names = {member.as_posix().rstrip("/") for member in members}
        missing = sorted(required - member_names)
        if missing:
            raise ReleaseError("Source archive is missing required entries: " + ", ".join(missing))
        for member in members:
            tail = member.parts[1:]
            if any(part in forbidden for part in tail):
                raise ReleaseError(f"Source archive contains a forbidden path: {member}")
            if member.suffix.lower() in EXCLUDED_SUFFIXES:
                raise ReleaseError(f"Source archive contains bytecode: {member}")

        with tempfile.TemporaryDirectory(prefix="shangbackground-source-") as temporary:
            extraction_root = Path(temporary)
            archive.extractall(extraction_root)
            project = extraction_root / expected_root
            _run([sys.executable, "-m", "compileall", "-q", "build_tools", "src"], cwd=project)
            reported = _run([sys.executable, "src/main.py", "--version"], cwd=project).stdout.strip()
            if reported != metadata.version:
                raise ReleaseError(f"Extracted source reports {reported!r}, expected {metadata.version!r}")
            _run([sys.executable, "build_tools/build.py", "self-test"], cwd=project)
            for target in TARGETS:
                for tool in ("pyinstaller", "nuitka"):
                    command = [
                        sys.executable,
                        "build_tools/build.py",
                        "--tool",
                        tool,
                        "--target",
                        target,
                        "--profile",
                        "lite",
                        "--mode",
                        "standalone",
                        "--mpv-runtime",
                        "system",
                        "--skip-install",
                        "--dry-run",
                    ]
                    _run(command, cwd=project)


def expected_release_assets() -> set[str]:
    metadata = validate_metadata()
    return {
        metadata.binary_archive("windows", "x86_64"),
        metadata.binary_archive("linux", "x86_64"),
        metadata.binary_archive("macos", "x86_64"),
        metadata.binary_archive("macos", "arm64"),
        metadata.source_archive,
    }


def write_checksums(directory: Path, *, require_complete: bool = True) -> Path:
    directory = directory.resolve()
    if not directory.is_dir():
        raise ReleaseError(f"Asset directory is missing: {directory}")
    checksum_path = directory / "SHA256SUMS.txt"
    files = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.name != checksum_path.name),
        key=lambda item: item.name.casefold(),
    )
    if require_complete:
        names = {path.name for path in files}
        expected = expected_release_assets()
        missing = sorted(expected - names)
        unexpected = sorted(names - expected)
        if missing or unexpected:
            details: list[str] = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unexpected:
                details.append("unexpected: " + ", ".join(unexpected))
            raise ReleaseError("Release asset set is incomplete (" + "; ".join(details) + ")")
    if not files:
        raise ReleaseError("No release assets were found")
    lines = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return checksum_path


def _print_result(path: Path | None = None, metadata: ReleaseMetadata | None = None) -> None:
    payload: dict[str, object] = {"ok": True}
    if metadata is not None:
        payload.update({"version": metadata.version, "tag": metadata.tag, "title": metadata.title})
    if path is not None:
        payload["path"] = os.fspath(path.resolve())
        payload["size_bytes"] = path.stat().st_size
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    metadata_parser = subparsers.add_parser("metadata", help="Validate and print release metadata")
    metadata_parser.add_argument("--github-output", type=Path)

    package_parser = subparsers.add_parser("package", help="Archive a validated native build")
    package_parser.add_argument("--target", choices=TARGETS, required=True)
    package_parser.add_argument("--arch", choices=ARCHES, required=True)
    package_parser.add_argument("--input", type=Path, required=True)
    package_parser.add_argument("--output-dir", type=Path, required=True)

    source_parser = subparsers.add_parser("source-archive", help="Create the curated source archive")
    source_parser.add_argument("--output-dir", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify-source-archive", help="Validate a source ZIP after extraction")
    verify_parser.add_argument("archive", type=Path)

    checksum_parser = subparsers.add_parser("checksums", help="Create SHA256SUMS.txt for release assets")
    checksum_parser.add_argument("directory", type=Path)
    checksum_parser.add_argument("--allow-partial", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "metadata":
            metadata = validate_metadata()
            if args.github_output:
                write_github_output(args.github_output, metadata)
            _print_result(metadata=metadata)
        elif args.command == "package":
            path = package_binary(args.input, args.output_dir, args.target, args.arch)
            _print_result(path=path)
        elif args.command == "source-archive":
            path = create_source_archive(args.output_dir)
            _print_result(path=path)
        elif args.command == "verify-source-archive":
            verify_source_archive(args.archive)
            _print_result(path=args.archive)
        elif args.command == "checksums":
            path = write_checksums(args.directory, require_complete=not args.allow_partial)
            _print_result(path=path)
        else:  # pragma: no cover - argparse prevents this branch.
            raise ReleaseError(f"Unknown command: {args.command}")
    except (OSError, ReleaseError, ValueError, zipfile.BadZipFile, tarfile.TarError) as error:
        print(f"release automation error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
