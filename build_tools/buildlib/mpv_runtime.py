"""Versioned MPV runtime manager used by the rewritten build plan."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import struct
import sys
import tempfile
from typing import Callable, Iterable, Mapping
import urllib.parse
import urllib.request
import zipfile

from .cli import BuildHelpFormatter, print_banner, print_section
from .constants import PROJECT_ROOT, TARGETS, current_host, effective_profile

MPV_CHANNELS = ("stable", "development")
MPV_RUNTIME_MODES = ("auto", "bundled", "system")
MPV_ARCHES = ("x86_64", "arm64", "x86")
MPV_REPOSITORY = "mpv-player/mpv"
MPV_API_STABLE = f"https://api.github.com/repos/{MPV_REPOSITORY}/releases/latest"
MPV_API_DEVELOPMENT = f"https://api.github.com/repos/{MPV_REPOSITORY}/releases/tags/git-release"
_ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
_MAX_MEMBER_BYTES = 512 * 1024 * 1024
_MAX_RUNTIME_FILES = 20000
_MAX_RUNTIME_BYTES = 4 * 1024 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 10000
_RUNTIME_METADATA = "runtime.json"
_ACTIVE_FILE = "ACTIVE"


@dataclass(frozen=True, slots=True)
class InstalledMpvRuntime:
    target: str
    arch: str
    runtime_id: str
    path: Path
    channel: str
    version: str
    asset_name: str
    source_url: str
    sha256: str
    size_bytes: int
    installed_at: str = ""
    active: bool = False

    @property
    def label(self) -> str:
        marker = " *" if self.active else ""
        version = self.version or self.runtime_id
        return f"{version} [{self.arch}, {self.channel}]{marker}"


@dataclass(frozen=True, slots=True)
class MpvBuildSelection:
    requested_mode: str
    mode: str
    target: str
    arch: str
    runtime_id: str
    payload_dir: Path | None
    metadata: Mapping[str, object]
    reason: str = ""

    @property
    def output_tag(self) -> str:
        if self.mode == "bundled":
            return _safe_component(self.runtime_id or "bundled")
        return _safe_component(self.mode or "system")

    @property
    def description(self) -> str:
        if self.mode == "bundled" and self.payload_dir is not None:
            version = str(self.metadata.get("version") or self.runtime_id)
            return f"bundled:{version}:{self.arch}"
        return f"{self.mode}:{self.arch}"

    def manifest(self) -> dict[str, object]:
        return {
            "requested_mode": self.requested_mode,
            "mode": self.mode,
            "target": self.target,
            "arch": self.arch,
            "runtime_id": self.runtime_id or None,
            "version": self.metadata.get("version") if self.metadata else None,
            "channel": self.metadata.get("channel") if self.metadata else None,
            "asset_name": self.metadata.get("asset_name") if self.metadata else None,
            "source_url": self.metadata.get("source_url") if self.metadata else None,
            "sha256": self.metadata.get("sha256") if self.metadata else None,
            "reason": self.reason or None,
        }


def normalize_arch(value: str | None = None) -> str:
    raw = str(value or platform.machine() or "").strip().lower().replace("-", "_")
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
        "i386": "x86",
        "i486": "x86",
        "i586": "x86",
        "i686": "x86",
        "x86": "x86",
    }
    if raw == "auto" or not raw:
        raw = str(platform.machine() or "").strip().lower().replace("-", "_")
    normalized = aliases.get(raw)
    if normalized is None:
        raise ValueError(f"Unsupported CPU architecture: {value or platform.machine()!r}")
    return normalized


def runtime_arch_root(project: Path, target: str, arch: str) -> Path:
    if target not in TARGETS:
        raise ValueError(f"Unsupported target: {target}")
    return project / "src" / "bin" / "mpv" / target / normalize_arch(arch)


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip()).strip(".-")
    return cleaned[:80] or "unknown"


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_version(value: str) -> tuple[int, int, int, int]:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value))
    if match is None:
        return (0, 0, 0, 0)
    parts = [int(part or 0) for part in match.groups()]
    return tuple(parts)  # type: ignore[return-value]


def _runtime_sort_key(item: InstalledMpvRuntime) -> tuple[object, ...]:
    channel_rank = {"stable": 3, "development": 2, "manual": 1}.get(item.channel, 0)
    return (
        item.active,
        channel_rank,
        _semantic_version(item.version),
        item.installed_at,
        item.runtime_id,
    )


def _payload_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _runtime_has_payload(path: Path, target: str) -> bool:
    if not path.is_dir():
        return False
    names = {item.name.lower() for item in path.rglob("*") if item.is_file()}
    if target == "windows":
        return "mpv.exe" in names
    if target == "linux":
        return any(name.startswith("libmpv.so") for name in names) or "mpv" in names
    return any(name.startswith("libmpv") and name.endswith(".dylib") for name in names) or "mpv" in names


def _active_runtime_id(root: Path) -> str:
    try:
        return root.joinpath(_ACTIVE_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def installed_runtimes(project: Path, target: str, arch: str = "auto") -> tuple[InstalledMpvRuntime, ...]:
    arch = normalize_arch(arch)
    root = runtime_arch_root(project, target, arch)
    active_id = _active_runtime_id(root)
    result: list[InstalledMpvRuntime] = []
    if not root.is_dir():
        return ()
    for folder in root.iterdir():
        if not folder.is_dir() or not _runtime_has_payload(folder, target):
            continue
        metadata = _read_json(folder / _RUNTIME_METADATA)
        result.append(
            InstalledMpvRuntime(
                target=target,
                arch=arch,
                runtime_id=folder.name,
                path=folder,
                channel=str(metadata.get("channel") or "manual"),
                version=str(metadata.get("version") or folder.name),
                asset_name=str(metadata.get("asset_name") or ""),
                source_url=str(metadata.get("source_url") or ""),
                sha256=str(metadata.get("sha256") or ""),
                size_bytes=_payload_size(folder),
                installed_at=str(metadata.get("installed_at") or ""),
                active=folder.name == active_id,
            )
        )
    result.sort(key=_runtime_sort_key, reverse=True)
    return tuple(result)


def _direct_runtime_binary(path: Path, target: str) -> bool:
    """Return whether *path* itself contains the target runtime binary.

    This deliberately avoids recursive discovery so ``src/bin/mpv`` is not
    mistaken for one flat payload merely because it contains versioned runtime
    folders for another platform or architecture.
    """
    if not path.is_dir():
        return False
    names = {item.name.lower() for item in path.iterdir() if item.is_file()}
    if target == "windows":
        return "mpv.exe" in names
    if target == "linux":
        return any(name.startswith("libmpv.so") for name in names)
    return any(name.startswith("libmpv") and name.endswith(".dylib") for name in names)


def local_payload_dir(project: Path, target: str, arch: str = "auto") -> Path | None:
    """Find a manually supplied flat payload before consulting version metadata.

    Users commonly copy runtime files straight into ``src/bin/mpv`` (or a
    platform child) instead of the managed versioned layout.  Build selection
    must inspect those files first and only then consider downloads or system
    fallback.
    """
    arch = normalize_arch(arch)
    roots = (
        # Explicit flat payloads are checked before managed version folders.
        # Probing remains direct-only so nested payloads for another target are
        # never accepted accidentally.
        project / "src" / "bin" / "mpv" / target / arch,
        project / "src" / "bin" / "mpv" / target,
        project / "src" / "bin" / target / arch,
        project / "src" / "bin" / target,
        project / "src" / "bin" / "mpv",
        project / "src" / "bin",
        project / "bin" / "mpv" / target / arch,
        project / "bin" / "mpv" / target,
        project / "bin" / target / arch,
        project / "bin" / target,
        project / "bin" / "mpv",
        project / "bin",
    )
    seen: set[Path] = set()
    for root in roots:
        for candidate in (root, root / "bin"):
            normalized = candidate.absolute()
            if normalized in seen:
                continue
            seen.add(normalized)
            if _direct_runtime_binary(candidate, target):
                return candidate
    return None


def legacy_payload_dir(project: Path, target: str, arch: str = "auto") -> Path | None:
    """Backward-compatible alias for the flat local payload detector."""
    return local_payload_dir(project, target, arch)


def select_installed_runtime(
    project: Path,
    target: str,
    arch: str = "auto",
    version: str = "auto",
) -> InstalledMpvRuntime | None:
    runtimes = installed_runtimes(project, target, arch)
    if not runtimes:
        return None
    requested = str(version or "auto").strip()
    if requested in {"", "auto", "active"}:
        return next((item for item in runtimes if item.active), runtimes[0])
    lowered = requested.lower()
    for item in runtimes:
        if item.runtime_id.lower() == lowered or item.version.lower() == lowered:
            return item
    return None


def default_mpv_runtime_mode(profile: str) -> str:
    # Full builds prefer a verified bundled runtime when one is installed.
    # Lite builds stay small and use the system/external player unless the
    # caller explicitly asks for --mpv-runtime bundled.
    return "auto" if effective_profile(profile) == "full" else "system"


def resolve_build_runtime(
    project: Path,
    target: str,
    profile: str,
    features: Iterable[str],
    *,
    mode: str = "auto",
    arch: str = "auto",
    version: str = "auto",
    require_bundled: bool = False,
) -> MpvBuildSelection:
    requested = str(mode or default_mpv_runtime_mode(profile)).strip().lower()
    if requested not in MPV_RUNTIME_MODES:
        raise ValueError(f"Unsupported mpv runtime mode: {mode}")
    normalized_arch = normalize_arch(arch)
    selected_features = set(features)
    if "video" not in selected_features:
        return MpvBuildSelection(requested, "disabled", target, normalized_arch, "", None, {}, "video feature disabled")

    # macOS has a dedicated AVFoundation implementation.  Treating it like the
    # Windows/Linux libmpv backends produces misleading controls and packages.
    if target == "macos":
        if requested == "bundled":
            raise RuntimeError("macOS builds use the native AVFoundation backend; bundled mpv is not supported")
        return MpvBuildSelection(
            requested, "native", target, normalized_arch, "", None, {}, "native AVFoundation backend"
        )

    if requested == "system":
        return MpvBuildSelection(
            requested, "system", target, normalized_arch, "", None, {}, "external/system mpv fallback"
        )

    requested_version = str(version or "auto").strip()
    automatic_version = requested_version.lower() in {"", "auto", "active", "local", "legacy"}
    invalid_runtime_errors: list[str] = []

    # A flat /bin payload is an explicit local choice and takes precedence over
    # version metadata or network provisioning.
    local = local_payload_dir(project, target, normalized_arch) if automatic_version else None
    if local is not None:
        ok, errors = verify_runtime_directory(local, target, normalized_arch, verify_hashes=False)
        if ok:
            return MpvBuildSelection(
                requested,
                "bundled",
                target,
                normalized_arch,
                "local-bin",
                local,
                {"version": "local-bin", "channel": "manual"},
                "flat local bin payload",
            )
        invalid_runtime_errors.append(f"local-bin: {'; '.join(errors)}")

    if requested_version.lower() in {"", "auto", "active"}:
        candidates = list(installed_runtimes(project, target, normalized_arch))
    else:
        selected = select_installed_runtime(project, target, normalized_arch, requested_version)
        candidates = [selected] if selected is not None else []

    for installed in candidates:
        ok, errors = verify_runtime_directory(
            installed.path,
            target,
            normalized_arch,
            verify_hashes=False,
        )
        if not ok:
            invalid_runtime_errors.append(f"{installed.runtime_id}: {'; '.join(errors)}")
            continue
        metadata = _read_json(installed.path / _RUNTIME_METADATA)
        return MpvBuildSelection(
            requested,
            "bundled",
            target,
            normalized_arch,
            installed.runtime_id,
            installed.path,
            metadata,
            "versioned runtime",
        )

    detail = f" Invalid payloads: {' | '.join(invalid_runtime_errors)}" if invalid_runtime_errors else ""
    if target == "linux":
        if requested == "bundled" and require_bundled:
            raise RuntimeError(
                "Linux bundled video builds require a matching libmpv.so payload in "
                f"{project / 'src' / 'bin' / 'mpv'} or {runtime_arch_root(project, target, normalized_arch)}. "
                "Automatic Windows release downloads are not used for Linux; install/copy the distribution's libmpv runtime."
                + detail
            )
        # Linux packages normally resolve libmpv from the target distribution.
        return MpvBuildSelection(
            requested,
            "system",
            target,
            normalized_arch,
            "",
            None,
            {},
            "local libmpv not found; use target-system libmpv",
        )

    # Windows full/auto is self-contained. Ordinary builds never download
    # native code implicitly; the explicit ``mpv download`` command installs
    # and verifies the payload before this required selection is evaluated.
    if requested == "bundled" and require_bundled:
        root = runtime_arch_root(project, target, normalized_arch)
        raise RuntimeError(
            f"Bundled mpv runtime was requested but no valid matching payload is installed under {root} "
            f"or {project / 'src' / 'bin' / 'mpv'}. Run: python build_tools/build.py mpv download "
            f"--target {target} --arch {normalized_arch} --channel stable, "
            "or provide a locally verified runtime payload." + detail
        )
    if requested == "auto" and require_bundled and effective_profile(profile) == "full":
        root = runtime_arch_root(project, target, normalized_arch)
        raise RuntimeError(
            "A full Windows video build must contain a verified mpv runtime. "
            f"Checked flat bin payloads first, then {root}; automatic provisioning did not produce a valid runtime."
            + detail
        )
    reason = (
        "requested bundled runtime missing or invalid"
        if requested == "bundled"
        else "no valid bundled runtime installed"
    )
    return MpvBuildSelection(requested, "system", target, normalized_arch, "", None, {}, reason)


def auto_provision_build_runtime(
    project: Path,
    *,
    target: str,
    profile: str,
    features: Iterable[str],
    mode: str,
    version: str,
    arch: str,
    dry_run: bool,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Compatibility no-op. Network provisioning is explicit and auditable.

    Run ``python build_tools/build.py mpv download ...`` before a bundled
    Windows build. Builds never mutate source/runtime directories implicitly.
    """
    del project, target, profile, features, mode, version, arch, dry_run, progress


def build_output_profile_name(base_name: str, selection: MpvBuildSelection) -> str:
    if selection.mode in {"disabled", "native"}:
        return base_name if selection.mode == "disabled" else f"{base_name}-mpv-native"
    return f"{base_name}-mpv-{selection.output_tag}"


def _api_url(channel: str) -> str:
    if channel == "stable":
        # GitHub's /releases/latest endpoint excludes prereleases and points at
        # mpv's current stable release, whose release page publishes CI-built
        # binary assets alongside the signed source tag.
        return MPV_API_STABLE
    if channel == "development":
        return MPV_API_DEVELOPMENT
    raise ValueError(f"Unsupported mpv channel: {channel}")


def _validate_https_url(url: str, *, hosts: set[str] | None = None) -> None:
    parsed = urllib.parse.urlparse(url)
    allowed = hosts or _ALLOWED_DOWNLOAD_HOSTS
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in allowed:
        raise RuntimeError(f"Refusing untrusted mpv download URL: {url}")


def _request(url: str) -> urllib.request.Request:
    _validate_https_url(url)
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ShangBackground-build-runtime/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def fetch_release(channel: str, *, opener: Callable[..., object] = urllib.request.urlopen) -> dict[str, object]:
    url = _api_url(channel)
    request = _request(url)
    with opener(request, timeout=30) as response:  # type: ignore[misc]
        final_url = str(response.geturl())
        _validate_https_url(final_url)
        raw = response.read(_MAX_METADATA_BYTES + 1)
    if len(raw) > _MAX_METADATA_BYTES:
        raise RuntimeError("mpv release metadata response is unexpectedly large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("GitHub returned invalid mpv release metadata") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("GitHub returned an unexpected mpv release payload")
    return dict(payload)


def _asset_patterns(target: str, arch: str) -> tuple[re.Pattern[str], ...]:
    arch = normalize_arch(arch)
    if target != "windows":
        raise RuntimeError(
            "Automatic mpv binary download is currently supported for Windows only. "
            "Linux builds should use a system package or a locally built libmpv; macOS uses AVFoundation."
        )
    # mpv publishes Windows CI-built archives on both stable release pages and
    # the ``git-release`` development prerelease. Keep filename matching
    # isolated here so a workflow naming change fails clearly instead of
    # silently selecting an unrelated release asset.
    names: dict[str, tuple[str, ...]] = {
        "x86_64": (
            r"x86_64-pc-windows-msvc\.zip",
            r"x86_64-w64-mingw32\.zip",
        ),
        "arm64": (
            r"aarch64-pc-windows-msvc\.zip",
            r"aarch64-w64-mingw32\.zip",
        ),
        "x86": (r"i686-w64-mingw32\.zip",),
    }
    return tuple(re.compile(rf"^mpv-.*-{suffix}$", re.IGNORECASE) for suffix in names[arch])


def select_release_asset(release: Mapping[str, object], target: str, arch: str) -> dict[str, object]:
    patterns = _asset_patterns(target, arch)
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("mpv release metadata does not contain assets")
    mapped_assets = [dict(item) for item in assets if isinstance(item, Mapping)]
    for pattern in patterns:
        candidates = [item for item in mapped_assets if pattern.match(str(item.get("name") or ""))]
        if not candidates:
            continue
        # Debug-symbol archives contain an extra marker and should never win.
        candidates.sort(
            key=lambda item: (
                "debug" in str(item.get("name") or "").lower(),
                str(item.get("name") or ""),
            )
        )
        return candidates[0]
    names = ", ".join(str(item.get("name")) for item in mapped_assets)
    raise RuntimeError(f"No supported mpv asset found for {target}/{normalize_arch(arch)}. Available: {names}")


def _download_to(
    url: str,
    destination: Path,
    *,
    expected_size: int = 0,
    progress: Callable[[int, int], None] | None = None,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> tuple[str, int]:
    _validate_https_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "ShangBackground-build-runtime/1"})
    hasher = hashlib.sha256()
    total = 0
    with opener(request, timeout=120) as response:  # type: ignore[misc]
        _validate_https_url(str(response.geturl()))
        header_size = int(response.headers.get("Content-Length") or 0)
        limit = expected_size or header_size
        if limit > _MAX_ARCHIVE_BYTES:
            raise RuntimeError(f"mpv archive is too large: {limit} bytes")
        with destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_ARCHIVE_BYTES:
                    raise RuntimeError("mpv archive exceeded the safety size limit")
                output.write(chunk)
                hasher.update(chunk)
                if progress is not None:
                    progress(total, limit)
    if expected_size and total != expected_size:
        raise RuntimeError(f"mpv archive size mismatch: expected {expected_size}, downloaded {total}")
    return hasher.hexdigest(), total


def _zip_member_path(member: zipfile.ZipInfo) -> Path:
    name = member.filename.replace("\\", "/")
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Unsafe path in mpv archive: {member.filename}")
    # Reject symbolic links. Windows builds should not need them, and following
    # links here would make extraction semantics platform dependent.
    mode = (member.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        raise RuntimeError(f"Symlink is not allowed in mpv archive: {member.filename}")
    return path


def _extract_zip_safely(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        if len(members) > _MAX_ARCHIVE_MEMBERS:
            raise RuntimeError("mpv archive contains too many files")
        oversized = [member.filename for member in members if int(member.file_size) > _MAX_MEMBER_BYTES]
        if oversized:
            raise RuntimeError(f"mpv archive member exceeds the safety limit: {oversized[0]}")
        expanded = sum(max(0, int(member.file_size)) for member in members)
        if expanded > _MAX_EXPANDED_BYTES:
            raise RuntimeError("mpv archive expands beyond the safety limit")
        for member in members:
            relative = _zip_member_path(member)
            if not relative.parts:
                continue
            target = destination / relative
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _pe_machine(path: Path) -> int | None:
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                return None
            stream.seek(0x3C)
            offset_raw = stream.read(4)
            if len(offset_raw) != 4:
                return None
            offset = struct.unpack("<I", offset_raw)[0]
            stream.seek(offset)
            if stream.read(4) != b"PE\0\0":
                return None
            machine_raw = stream.read(2)
            return struct.unpack("<H", machine_raw)[0] if len(machine_raw) == 2 else None
    except OSError:
        return None


def _elf_machine(path: Path) -> int | None:
    """Return the ELF e_machine value, or ``None`` for a malformed file."""
    try:
        with path.open("rb") as stream:
            header = stream.read(20)
        if len(header) < 20 or header[:4] != b"\x7fELF":
            return None
        data_encoding = header[5]
        if data_encoding == 1:
            byte_order = "little"
        elif data_encoding == 2:
            byte_order = "big"
        else:
            return None
        return int.from_bytes(header[18:20], byte_order)
    except OSError:
        return None


def _manifest_relative_path(raw: object) -> PurePosixPath | None:
    value = str(raw or "").replace("\\", "/")
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        return None
    return relative


def verify_runtime_directory(
    path: Path,
    target: str,
    arch: str,
    *,
    verify_hashes: bool = True,
) -> tuple[bool, tuple[str, ...]]:
    """Validate runtime structure and, when available, its file manifest.

    Normal builds use ``verify_hashes=False`` to avoid re-hashing a large mpv
    payload on every invocation. The explicit ``mpv verify`` command performs
    the full integrity check.
    """
    errors: list[str] = []
    arch = normalize_arch(arch)
    if not path.is_dir():
        return False, (f"runtime directory does not exist: {path}",)
    entries = list(path.rglob("*"))
    links = [item for item in entries if item.is_symlink()]
    if links:
        errors.append(f"symbolic links are not allowed: {links[0].relative_to(path)}")
    files = [item for item in entries if item.is_file() and not item.is_symlink()]
    if len(files) > _MAX_RUNTIME_FILES:
        errors.append(f"runtime contains too many files: {len(files)}")
    try:
        total_size = sum(item.stat().st_size for item in files)
    except OSError as exc:
        errors.append(f"unable to inspect runtime files: {exc}")
        total_size = 0
    if total_size > _MAX_RUNTIME_BYTES:
        errors.append(f"runtime is unexpectedly large: {total_size} bytes")
    if target == "windows":
        executables = [item for item in files if item.name.lower() == "mpv.exe"]
        if not executables:
            errors.append(
                "mpv.exe is missing; v1.5.0 Windows bundles require the executable + JSON IPC runtime "
                "instead of generating a new full-application libmpv child bundle"
            )
        expected_machine = {"x86_64": 0x8664, "arm64": 0xAA64, "x86": 0x014C}[arch]
        native_files = [item for item in files if item.suffix.lower() in {".dll", ".exe"}]
        for binary in native_files:
            machine = _pe_machine(binary)
            if machine is None:
                errors.append(f"invalid PE binary: {binary.name}")
            elif machine != expected_machine:
                errors.append(
                    f"architecture mismatch: {binary.name} machine=0x{machine:04x}, expected=0x{expected_machine:04x}"
                )
    elif target == "linux":
        libraries = [item for item in files if item.name.startswith("libmpv.so")]
        if not libraries:
            errors.append("libmpv.so is missing")
        expected_machine = {"x86_64": 62, "arm64": 183, "x86": 3}[arch]
        native_files = [item for item in files if item.name == "mpv" or ".so" in item.name]
        for binary in native_files:
            machine = _elf_machine(binary)
            if machine is None:
                errors.append(f"invalid ELF binary: {binary.name}")
            elif machine != expected_machine:
                errors.append(
                    f"architecture mismatch: {binary.name} ELF machine={machine}, expected={expected_machine}"
                )
    else:
        if not any(
            item.is_file() and item.name.startswith("libmpv") and item.suffix == ".dylib" for item in path.rglob("*")
        ):
            errors.append("libmpv dylib is missing")

    metadata_path = path / _RUNTIME_METADATA
    metadata = _read_json(metadata_path) if metadata_path.is_file() else {}
    manifest = metadata.get("files")
    if isinstance(manifest, Mapping):
        for raw_relative, raw_record in manifest.items():
            relative = _manifest_relative_path(raw_relative)
            if relative is None:
                errors.append(f"unsafe runtime manifest path: {raw_relative!r}")
                continue
            file_path = path.joinpath(*relative.parts)
            if not file_path.is_file():
                errors.append(f"manifest file is missing: {relative.as_posix()}")
                continue
            if not isinstance(raw_record, Mapping):
                errors.append(f"invalid manifest entry: {relative.as_posix()}")
                continue
            expected_size = raw_record.get("size")
            if isinstance(expected_size, int) and file_path.stat().st_size != expected_size:
                errors.append(
                    f"size mismatch: {relative.as_posix()} expected={expected_size} actual={file_path.stat().st_size}"
                )
            expected_digest = str(raw_record.get("sha256") or "").lower()
            if verify_hashes and expected_digest and _file_sha256(file_path).lower() != expected_digest:
                errors.append(f"SHA-256 mismatch: {relative.as_posix()}")
    return not errors, tuple(errors)


def _runtime_id(release: Mapping[str, object], asset_name: str, channel: str) -> tuple[str, str]:
    tag = str(release.get("tag_name") or "").strip()
    name = str(release.get("name") or "").strip()
    if channel == "stable":
        version = tag or name or "stable"
        return _safe_component(version), version
    match = re.search(r"-g([0-9a-f]{7,40})(?:-|\.)", asset_name, re.IGNORECASE)
    commit = match.group(1) if match else ""
    version_match = re.search(r"mpv-(v?\d+\.\d+\.\d+(?:-dev)?(?:-g[0-9a-f]+)?)", asset_name, re.IGNORECASE)
    version = version_match.group(1) if version_match else (name or tag or "development")
    runtime_id = (
        f"git-{commit[:12]}" if commit else f"development-{hashlib.sha256(asset_name.encode()).hexdigest()[:10]}"
    )
    return _safe_component(runtime_id), version


def _copy_runtime_payload(extracted: Path, destination: Path, target: str) -> None:
    if target != "windows":
        raise RuntimeError("Automatic payload reduction is implemented for Windows archives only")
    _ = [
        item
        for item in extracted.rglob("*")
        if item.is_file() and item.name.lower() in {"libmpv-2.dll", "mpv-2.dll", "libmpv.dll"}
    ]
    exe_candidates = [
        item for item in extracted.rglob("*") if item.is_file() and item.name.lower() == "mpv.exe"
    ]
    if not exe_candidates:
        raise RuntimeError(
            "Downloaded Windows archive does not contain mpv.exe; refusing to create a libmpv-only v1.5.0 bundle"
        )
    # Preserve only runtime binaries.  Archives normally keep these beside
    # libmpv, but recursive discovery also handles a future nested ``bin``
    # folder.  Files are flattened because Windows resolves sibling DLLs most
    # reliably from one directory.  Duplicate names must be byte-identical.
    copied: dict[str, Path] = {}
    for item in extracted.rglob("*"):
        if not item.is_file():
            continue
        lower = item.name.lower()
        is_runtime_binary = item.suffix.lower() == ".dll" or lower == "mpv.exe"
        if not is_runtime_binary or lower.endswith(("-debug.exe", "-debug.dll")):
            continue
        destination_file = destination / item.name
        existing = copied.get(lower)
        if existing is not None:
            if _file_sha256(existing) != _file_sha256(item):
                raise RuntimeError(f"mpv archive contains conflicting runtime files named {item.name}")
            continue
        shutil.copy2(item, destination_file)
        copied[lower] = destination_file
    for item in extracted.rglob("*"):
        if not item.is_file():
            continue
        lower = item.name.lower()
        if (
            any(token in lower for token in ("license", "copying", "copyright"))
            and item.stat().st_size <= 2 * 1024 * 1024
        ):
            target_path = destination / "licenses" / item.name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if not target_path.exists():
                shutil.copy2(item, target_path)


def install_downloaded_runtime(
    project: Path,
    release: Mapping[str, object],
    asset: Mapping[str, object],
    archive: Path,
    *,
    target: str,
    arch: str,
    channel: str,
    sha256: str,
    force: bool = False,
) -> InstalledMpvRuntime:
    arch = normalize_arch(arch)
    asset_name = str(asset.get("name") or archive.name)
    runtime_id, version = _runtime_id(release, asset_name, channel)
    root = runtime_arch_root(project, target, arch)
    destination = root / runtime_id
    root.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        ok, errors = verify_runtime_directory(destination, target, arch)
        if ok:
            (root / _ACTIVE_FILE).write_text(runtime_id + "\n", encoding="utf-8")
            selected = select_installed_runtime(project, target, arch, runtime_id)
            if selected is not None:
                return selected
        raise RuntimeError(f"Runtime already exists but is invalid: {destination}: {'; '.join(errors)}")

    with tempfile.TemporaryDirectory(prefix="shangbackground-mpv-extract-") as temp_name:
        temp = Path(temp_name)
        extracted = temp / "extracted"
        staged = temp / runtime_id
        extracted.mkdir()
        staged.mkdir()
        _extract_zip_safely(archive, extracted)
        _copy_runtime_payload(extracted, staged, target)
        ok, errors = verify_runtime_directory(staged, target, arch)
        if not ok:
            raise RuntimeError("Downloaded mpv runtime failed verification: " + "; ".join(errors))
        files = {
            str(item.relative_to(staged)).replace(os.sep, "/"): {
                "size": item.stat().st_size,
                "sha256": _file_sha256(item),
            }
            for item in staged.rglob("*")
            if item.is_file()
        }
        metadata = {
            "schema_version": 1,
            "provider": MPV_REPOSITORY,
            "channel": channel,
            "release_tag": release.get("tag_name"),
            "release_name": release.get("name"),
            "version": version,
            "runtime_id": runtime_id,
            "target": target,
            "arch": arch,
            "asset_name": asset_name,
            "source_url": asset.get("browser_download_url"),
            "archive_size": asset.get("size"),
            "sha256": sha256,
            "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "files": files,
        }
        (staged / _RUNTIME_METADATA).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(os.fspath(staged), os.fspath(destination))
    (root / _ACTIVE_FILE).write_text(runtime_id + "\n", encoding="utf-8")
    selected = select_installed_runtime(project, target, arch, runtime_id)
    if selected is None:
        raise RuntimeError("Installed mpv runtime could not be rediscovered")
    return selected


def download_runtime(
    project: Path,
    *,
    target: str,
    arch: str = "auto",
    channel: str = "stable",
    force: bool = False,
    expected_sha256: str = "",
    progress: Callable[[int, int], None] | None = None,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> InstalledMpvRuntime:
    arch = normalize_arch(arch)
    release = fetch_release(channel, opener=opener)
    asset = select_release_asset(release, target, arch)
    url = str(asset.get("browser_download_url") or "")
    if not url:
        raise RuntimeError("Selected mpv asset has no download URL")
    raw_expected_size = asset.get("size")
    try:
        expected_size = int(raw_expected_size) if isinstance(raw_expected_size, (int, str)) else 0
    except (TypeError, ValueError, OverflowError):
        expected_size = 0
    expected_digest = str(asset.get("digest") or "")
    pinned_digest = str(expected_sha256 or "").strip().lower()
    if pinned_digest and not re.fullmatch(r"[0-9a-f]{64}", pinned_digest):
        raise ValueError("expected_sha256 must contain exactly 64 hexadecimal characters")
    with tempfile.TemporaryDirectory(prefix="shangbackground-mpv-download-") as temp_name:
        archive = Path(temp_name) / str(asset.get("name") or "mpv.zip")
        sha256, _size = _download_to(
            url,
            archive,
            expected_size=expected_size,
            progress=progress,
            opener=opener,
        )
        if expected_digest.startswith("sha256:") and sha256.lower() != expected_digest.split(":", 1)[1].lower():
            raise RuntimeError("mpv archive SHA-256 does not match GitHub release metadata")
        if pinned_digest and sha256.lower() != pinned_digest:
            raise RuntimeError("mpv archive SHA-256 does not match the user-pinned digest")
        return install_downloaded_runtime(
            project,
            release,
            asset,
            archive,
            target=target,
            arch=arch,
            channel=channel,
            sha256=sha256,
            force=force,
        )


def activate_runtime(project: Path, target: str, arch: str, version: str) -> InstalledMpvRuntime:
    arch = normalize_arch(arch)
    selected = select_installed_runtime(project, target, arch, version)
    if selected is None:
        raise RuntimeError(f"mpv runtime not found: {target}/{arch}/{version}")
    root = runtime_arch_root(project, target, arch)
    root.mkdir(parents=True, exist_ok=True)
    (root / _ACTIVE_FILE).write_text(selected.runtime_id + "\n", encoding="utf-8")
    refreshed = select_installed_runtime(project, target, arch, selected.runtime_id)
    return refreshed or selected


def prune_runtimes(project: Path, target: str, arch: str, keep: int = 2) -> tuple[Path, ...]:
    """Keep the newest inactive runtimes plus every explicitly active runtime."""
    keep = max(1, int(keep))
    runtimes = list(installed_runtimes(project, target, arch))
    protected = {item.runtime_id for item in runtimes if item.active}
    newest_inactive = [item for item in runtimes if not item.active][:keep]
    protected.update(item.runtime_id for item in newest_inactive)
    removed: list[Path] = []
    for item in runtimes:
        if item.runtime_id in protected:
            continue
        shutil.rmtree(item.path)
        removed.append(item.path)
    return tuple(removed)


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _progress_printer(downloaded: int, total: int) -> None:
    if total:
        percent = min(100.0, downloaded * 100.0 / total)
        print(
            f"\rDownloading mpv: {percent:5.1f}% ({_human_size(downloaded)}/{_human_size(total)})", end="", flush=True
        )
    else:
        print(f"\rDownloading mpv: {_human_size(downloaded)}", end="", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build.py mpv",
        description="Manage versioned MPV runtimes used by ShangBackground builds.",
        formatter_class=BuildHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_location(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--target", choices=TARGETS, default=current_host())
        subparser.add_argument("--arch", choices=("auto", *MPV_ARCHES), default="auto")

    list_parser = subparsers.add_parser("list", help="List installed versioned runtimes.", formatter_class=BuildHelpFormatter)
    add_location(list_parser)

    path_parser = subparsers.add_parser("path", help="Print the active runtime path.", formatter_class=BuildHelpFormatter)
    add_location(path_parser)
    path_parser.add_argument("--version", default="auto")

    download_parser = subparsers.add_parser(
        "download",
        help="Download and install an mpv first-party Windows runtime from an official release.",
        formatter_class=BuildHelpFormatter,
    )
    add_location(download_parser)
    download_parser.add_argument("--channel", choices=MPV_CHANNELS, default="stable")
    download_parser.add_argument("--force", action="store_true")
    download_parser.add_argument(
        "--sha256",
        default="",
        metavar="HEX",
        help="Require the downloaded archive to match this SHA-256 digest.",
    )
    download_parser.add_argument("--prune", type=int, default=2, metavar="COUNT")

    verify_parser = subparsers.add_parser("verify", help="Verify installed runtime structure and CPU architecture.", formatter_class=BuildHelpFormatter)
    add_location(verify_parser)
    verify_parser.add_argument("--version", default="auto")

    activate_parser = subparsers.add_parser("activate", help="Select which installed version is bundled by default.", formatter_class=BuildHelpFormatter)
    add_location(activate_parser)
    activate_parser.add_argument("version")

    prune_parser = subparsers.add_parser("prune", help="Remove old inactive runtime versions.", formatter_class=BuildHelpFormatter)
    add_location(prune_parser)
    prune_parser.add_argument("--keep", type=int, default=2)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project = PROJECT_ROOT
    target = str(args.target)
    arch = normalize_arch(args.arch)
    try:
        if args.command == "list":
            print_banner("Installed MPV runtimes", f"{target} · {arch}")
            local = local_payload_dir(project, target, arch)
            runtimes = installed_runtimes(project, target, arch)
            if local is not None:
                print(f"{'local-bin':24} {'manual flat payload':40} {_human_size(_payload_size(local)):>10}  {local}")
            if not runtimes and local is None:
                print(f"No mpv runtime found in flat bin locations or under {runtime_arch_root(project, target, arch)}")
                return 0
            for item in runtimes:
                print(f"{item.runtime_id:24} {item.label:40} {_human_size(item.size_bytes):>10}  {item.path}")
            return 0
        if args.command == "path":
            requested = str(args.version or "auto").strip().lower()
            local = (
                local_payload_dir(project, target, arch)
                if requested in {"", "auto", "local", "local-bin", "legacy"}
                else None
            )
            if local is not None:
                print(local)
                return 0
            selected = select_installed_runtime(project, target, arch, args.version)
            if selected is None:
                return 1
            print(selected.path)
            return 0
        if args.command == "download":
            print_banner("Prepare MPV runtime", f"{target} · {arch} · {args.channel}")
            print(f"Resolving mpv {args.channel} runtime channel...")
            installed = download_runtime(
                project,
                target=target,
                arch=arch,
                channel=args.channel,
                force=bool(args.force),
                expected_sha256=str(args.sha256 or ""),
                progress=_progress_printer,
            )
            print()
            print_section("Runtime installed")
            print(f"  Release: {installed.label}")
            print(f"  Path: {installed.path}")
            if int(args.prune) > 0:
                removed = prune_runtimes(project, target, arch, keep=int(args.prune))
                for path in removed:
                    print(f"Removed old runtime: {path}")
            return 0
        if args.command == "verify":
            requested = str(args.version or "auto").strip().lower()
            local = (
                local_payload_dir(project, target, arch)
                if requested in {"", "auto", "local", "local-bin", "legacy"}
                else None
            )
            if local is not None:
                ok, errors = verify_runtime_directory(local, target, arch)
                if not ok:
                    for error in errors:
                        print(f"ERROR: {error}")
                    return 2
                print(f"OK: local flat payload at {local}")
                return 0
            selected = select_installed_runtime(project, target, arch, args.version)
            if selected is None:
                raise RuntimeError(f"No matching runtime installed for {target}/{arch}/{args.version}")
            ok, errors = verify_runtime_directory(selected.path, target, arch)
            if not ok:
                for error in errors:
                    print(f"ERROR: {error}")
                return 2
            print(f"OK: {selected.label} at {selected.path}")
            return 0
        if args.command == "activate":
            selected = activate_runtime(project, target, arch, args.version)
            print(f"Active runtime: {selected.label} at {selected.path}")
            return 0
        if args.command == "prune":
            removed = prune_runtimes(project, target, arch, keep=int(args.keep))
            for path in removed:
                print(f"Removed: {path}")
            print(f"Removed {len(removed)} runtime(s).")
            return 0
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(f"mpv runtime error: {exc}", file=sys.stderr)
        return 2
    return 2


__all__ = [
    "InstalledMpvRuntime",
    "MpvBuildSelection",
    "MPV_ARCHES",
    "MPV_CHANNELS",
    "MPV_RUNTIME_MODES",
    "activate_runtime",
    "build_output_profile_name",
    "default_mpv_runtime_mode",
    "download_runtime",
    "installed_runtimes",
    "legacy_payload_dir",
    "local_payload_dir",
    "main",
    "normalize_arch",
    "prune_runtimes",
    "resolve_build_runtime",
    "auto_provision_build_runtime",
    "runtime_arch_root",
    "select_installed_runtime",
    "select_release_asset",
    "verify_runtime_directory",
]
