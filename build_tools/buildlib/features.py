from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

FEATURE_KEYS = ("video", "html", "bing", "hotkeys", "updates", "fonts")


@dataclass(frozen=True, slots=True)
class BuildFeature:
    key: str
    label: str


_LABELS = {
    "video": "Video wallpaper / libmpv",
    "html": "HTML wallpaper / system-native WebView",
    "bing": "Bing wallpaper services",
    "hotkeys": "Global hotkeys",
    "updates": "Update checks",
    "fonts": "Bundled fonts",
}
FEATURES = tuple(BuildFeature(key, _LABELS[key]) for key in FEATURE_KEYS)


def default_features(profile: str) -> frozenset[str]:
    selected = set(FEATURE_KEYS)
    if profile == "lite":
        selected.difference_update({"video", "html"})
    return frozenset(selected)


def _tokens(values: Sequence[str] | str | None) -> list[str]:
    if values is None:
        return []
    raw = [values] if isinstance(values, str) else list(values)
    return [part.strip().lower() for item in raw for part in str(item).split(",") if part.strip()]


def parse_feature_set(values: Sequence[str] | str | None) -> frozenset[str] | None:
    tokens = _tokens(values)
    if not tokens:
        return None
    result: set[str] = set()
    for token in tokens:
        if token == "all":
            result.update(FEATURE_KEYS)
        elif token == "none":
            result.clear()
        elif token in FEATURE_KEYS:
            result.add(token)
        else:
            raise ValueError(f"Unknown build feature: {token}")
    return frozenset(result)


def resolve_features(profile: str, features=None, exclude_features=None) -> frozenset[str]:
    explicit = parse_feature_set(features)
    selected = set(default_features(profile) if explicit is None else explicit)
    excluded = parse_feature_set(exclude_features)
    if excluded:
        selected.difference_update(excluded)
    return frozenset(selected)


def feature_summary(features: Iterable[str]) -> str:
    selected = set(features)
    return ", ".join(key for key in FEATURE_KEYS if key in selected) or "core-only"


def output_profile_name(profile: str, features: Iterable[str]) -> str:
    selected = frozenset(features)
    name = profile
    if selected != default_features(profile):
        digest = hashlib.sha256(",".join(sorted(selected)).encode()).hexdigest()[:8]
        name = f"{profile}-custom-{digest}"
    if "html" in selected:
        name += "-html-native"
    return name


def requirement_files(project: Path, target: str, features: Iterable[str]) -> tuple[Path, ...]:
    selected = set(features)
    result = [project / "requirements" / f"{target}.txt"]
    if "html" in selected:
        result.append(project / "requirements" / "html-native.txt")
    if "hotkeys" in selected and target != "windows":
        result.append(project / "requirements" / "hotkeys.txt")
    if "video" in selected:
        candidate = project / "requirements" / f"{target}-video.txt"
        if candidate.is_file():
            result.append(candidate)
    return tuple(dict.fromkeys(result))


def manifest_payload(
    target: str,
    profile: str,
    features: Iterable[str],
    *,
    tool: str,
    arch: str,
    video_runtime: Mapping[str, object],
) -> dict[str, object]:
    selected = set(features)
    return {
        "schema": 3,
        "tool": tool,
        "target": target,
        "arch": arch,
        "profile": profile,
        "enabled": {key: key in selected for key in FEATURE_KEYS},
        "html_runtime": "native" if "html" in selected else "disabled",
        "video_runtime": dict(video_runtime),
    }


def write_manifest(path: Path, payload: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
