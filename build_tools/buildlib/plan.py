from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from typing import Iterable
import uuid

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
    arch: str
    features: frozenset[str]
    mpv: MpvBuildSelection
    variant: str
    generated_dir: Path
    manifest_path: Path
    staged_mpv_dir: Path | None

    @property
    def output_dir(self) -> Path:
        """Stable, published release location."""
        return PROJECT_ROOT / f"dist-{self.tool}" / self.target / self.variant / self.mode

    @property
    def build_output_dir(self) -> Path:
        """Ephemeral output used for the current build before validation."""
        return self.generated_dir / "staging" / self.mode


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


def create_plan(
    *,
    tool: str,
    target: str,
    profile: str,
    mode: str,
    jobs: int,
    features: Iterable[str],
    mpv_runtime: str,
    mpv_version: str,
    arch: str,
    dry_run: bool,
) -> BuildPlan:
    if target != host_target() and not dry_run:
        raise RuntimeError(f"Actual {target} builds must run on a {target} host")
    if target == "macos" and mode == "onefile":
        raise RuntimeError("macOS releases use standalone .app bundles")
    selected = frozenset(features)
    arch = normalize_arch(arch)
    require_bundled = "video" in selected and (
        mpv_runtime == "bundled"
        or (not dry_run and target == "windows" and profile == "full" and mpv_runtime == "auto")
    )
    mpv = resolve_build_runtime(
        PROJECT_ROOT,
        target,
        profile,
        selected,
        mode=mpv_runtime,
        arch=arch,
        version=mpv_version,
        require_bundled=require_bundled,
    )
    variant = f"{output_profile_name(profile, selected)}-{arch}"
    if mpv.mode == "bundled":
        variant += f"-mpv-{mpv.output_tag}"
    elif mpv.mode not in {"disabled", "native"}:
        variant += f"-mpv-{mpv.mode}"
    generated = PROJECT_ROOT / "build-generated" / tool / target / variant
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
    # A dry-run is a pure plan operation. It must not rewrite the manifest used
    # by a concurrent real build or leave generated state behind merely because
    # a command preview/self-test was requested.
    if not dry_run:
        generated.mkdir(parents=True, exist_ok=True)
        write_manifest(
            manifest,
            manifest_payload(target, profile, selected, tool=tool, arch=arch, video_runtime=mpv.manifest()),
        )
    return BuildPlan(tool, target, profile, mode, int(jobs), arch, selected, mpv, variant, generated, manifest, staged)


def _publication_backups(plan: BuildPlan) -> tuple[Path, ...]:
    final = plan.output_dir
    if not final.parent.is_dir():
        return ()
    return tuple(
        sorted(final.parent.glob(f".{final.name}.previous-*"), key=lambda item: item.stat().st_mtime, reverse=True)
    )


def recover_published_output(plan: BuildPlan) -> None:
    """Recover a release if a previous process died during directory exchange."""
    final = plan.output_dir
    backups = _publication_backups(plan)
    if not final.exists() and backups:
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(backups[0], final)
        backups = backups[1:]
    if final.exists():
        for backup in backups:
            shutil.rmtree(backup, ignore_errors=True)


def prepare_staging_output(plan: BuildPlan) -> None:
    """Guarantee that validators can only see files from this build attempt."""
    recover_published_output(plan)
    for path in (plan.build_output_dir, plan.generated_dir / "work", plan.generated_dir / "spec"):
        if path.exists():
            shutil.rmtree(path)
    plan.build_output_dir.parent.mkdir(parents=True, exist_ok=True)


def discard_staging_output(plan: BuildPlan) -> None:
    staging = plan.build_output_dir
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)


def publish_staging_output(plan: BuildPlan) -> None:
    """Replace the published artifact after validation, with crash recovery."""
    recover_published_output(plan)
    staging = plan.build_output_dir
    final = plan.output_dir
    if not staging.exists():
        raise RuntimeError(f"Validated staging output is missing: {staging}")
    final.parent.mkdir(parents=True, exist_ok=True)
    backup = final.parent / f".{final.name}.previous-{uuid.uuid4().hex}"
    moved_old = False
    try:
        if final.exists():
            os.replace(final, backup)
            moved_old = True
        os.replace(staging, final)
    except Exception:
        if moved_old and backup.exists() and not final.exists():
            os.replace(backup, final)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def relative(path: Path) -> str:
    try:
        return os.fspath(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return os.fspath(path)
