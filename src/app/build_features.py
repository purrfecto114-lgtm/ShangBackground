"""Runtime view of the feature set selected by the build manifest.

Loose source runs keep developer-friendly defaults. Packaged builds fail closed:
a missing or malformed manifest enables only the core image-wallpaper feature set.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from app.paths import RESOURCE_ROOT, is_packaged_runtime

FEATURE_KEYS = ("video", "html", "bing", "hotkeys", "updates", "fonts")
_SOURCE_DEFAULTS = {key: True for key in FEATURE_KEYS}
_PACKAGED_DEFAULTS = {key: False for key in FEATURE_KEYS}
_DEFAULT_HTML_RUNTIME = "native"


def _manifest_path() -> Path:
    override = str(os.environ.get("SHANGBACKGROUND_BUILD_FEATURES_FILE", "")).strip()
    packaged_override = os.environ.get("SHANGBACKGROUND_ALLOW_PACKAGED_FEATURE_OVERRIDE") == "1"
    if override and (not is_packaged_runtime() or packaged_override):
        return Path(override).expanduser()
    return RESOURCE_ROOT / "build-features.json"


def _load_manifest() -> tuple[dict[str, bool], str, dict[str, object]]:
    packaged = is_packaged_runtime()
    values = dict(_PACKAGED_DEFAULTS if packaged else _SOURCE_DEFAULTS)
    runtime = "disabled" if packaged else _DEFAULT_HTML_RUNTIME
    video_runtime: dict[str, object] = {"mode": "disabled" if packaged else "source"}
    path = _manifest_path()
    if not path.is_file():
        return values, runtime, video_runtime
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise TypeError("manifest root is not an object")
        enabled = payload.get("enabled")
        if not isinstance(enabled, Mapping):
            raise TypeError("manifest enabled field is not an object")
        parsed = {key: bool(enabled.get(key, False)) for key in FEATURE_KEYS}
        candidate = str(payload.get("html_runtime", "disabled")).strip().lower()
        if parsed["html"] and candidate != "native":
            raise ValueError(f"unsupported HTML runtime: {candidate}")
        runtime = "native" if parsed["html"] else "disabled"
        raw_video = payload.get("video_runtime", {})
        if isinstance(raw_video, Mapping):
            video_runtime = dict(raw_video)
        else:
            video_runtime = {"mode": "disabled"}
        values = parsed
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        # Packaged builds remain core-only instead of accidentally exposing
        # code paths whose native payloads were not included.
        return values, runtime, video_runtime
    return values, runtime, video_runtime


BUILD_FEATURES, BUILD_HTML_RUNTIME, BUILD_VIDEO_RUNTIME = _load_manifest()


def is_feature_enabled(key: str) -> bool:
    if key not in FEATURE_KEYS:
        raise KeyError(f"Unknown build feature: {key}")
    return bool(BUILD_FEATURES.get(key, False))


def enabled_features() -> tuple[str, ...]:
    return tuple(key for key in FEATURE_KEYS if is_feature_enabled(key))


def html_runtime_name() -> str:
    if not is_feature_enabled("html"):
        return "disabled"
    if BUILD_HTML_RUNTIME != "native":
        raise RuntimeError(f"Unsupported packaged HTML runtime: {BUILD_HTML_RUNTIME}")
    return "native"


def video_runtime_mode() -> str:
    if not is_feature_enabled("video"):
        return "disabled"
    mode = str(BUILD_VIDEO_RUNTIME.get("mode") or "system").strip().lower()
    return mode if mode in {"bundled", "system", "native", "source"} else "disabled"
