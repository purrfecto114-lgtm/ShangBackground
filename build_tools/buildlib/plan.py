from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from typing import Iterable

from .constants import PROJECT_ROOT, host_target, normalize_arch
from .features import manifest_payload, output_profile_name, write_manifest
from .mpv_runtime import MpvBuildSelection, resolve_build_runtime, verify_runtime_directory


@dataclass(frozen=True, slots=True)
class BuildPlan:
    tool: str
    target: str
    profile: str
    mode: str
    jobs: int
    features: frozenset[str]
    mpv: MpvBuildSelection
    variant: str
    generated_dir: Path
    manifest_path: Path
    staged_mpv_dir: Path | None

    @property
    def output_dir(self) -> Path:
        return PROJECT_ROOT / f"dist-{self.tool}" / self.target / self.variant / self.mode


def _copy_verified_payload(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_symlink():
            raise RuntimeError(f"MPV payload contains a symbolic link: {relative}")
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        else:
            raise RuntimeError(f"MPV payload contains an unsupported file: {relative}")


def create_plan(*, tool: str, target: str, profile: str, mode: str, jobs: int, features: Iterable[str], mpv_runtime: str, mpv_version: str, mpv_arch: str, dry_run: bool) -> BuildPlan:
    if target != host_target() and not dry_run:
        raise RuntimeError(f"Actual {target} builds must run on a {target} host")
    if target == "macos" and mode == "onefile":
        raise RuntimeError("macOS releases use standalone .app bundles")
    selected = frozenset(features)
    arch = normalize_arch(mpv_arch)
    require_bundled = "video" in selected and (
        mpv_runtime == "bundled" or (not dry_run and target == "windows" and profile == "full" and mpv_runtime == "auto")
    )
    mpv = resolve_build_runtime(
        PROJECT_ROOT, target, profile, selected, mode=mpv_runtime, arch=arch,
        version=mpv_version, require_bundled=require_bundled,
    )
    variant = output_profile_name(profile, selected)
    if mpv.mode == "bundled":
        variant += f"-mpv-{mpv.output_tag}"
    elif mpv.mode not in {"disabled", "native"}:
        variant += f"-mpv-{mpv.mode}"
    generated = PROJECT_ROOT / "build-generated" / tool / target / variant
    generated.mkdir(parents=True, exist_ok=True)
    manifest = generated / "build-features.json"
    staged: Path | None = None
    if mpv.mode == "bundled" and mpv.payload_dir is not None:
        ok, errors = verify_runtime_directory(mpv.payload_dir, target, arch, verify_hashes=False)
        if not ok:
            raise RuntimeError("Invalid MPV payload: " + "; ".join(errors))
        if tool == "nuitka":
            staged = generated / "python" / "shangbackground_native_runtime" / "payload"
            if not dry_run:
                _copy_verified_payload(mpv.payload_dir, staged)
    write_manifest(manifest, manifest_payload(target, profile, selected, tool=tool, video_runtime=mpv.manifest()))
    return BuildPlan(tool, target, profile, mode, int(jobs), selected, mpv, variant, generated, manifest, staged)


def relative(path: Path) -> str:
    try:
        return os.fspath(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return os.fspath(path)
