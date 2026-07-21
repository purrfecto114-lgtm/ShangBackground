"""Lazy feature-gated runtime modules used by the legacy engine facade."""
from __future__ import annotations

from importlib import import_module
from types import ModuleType

from app.build_features import is_feature_enabled


def _load(feature: str, module: str) -> ModuleType | None:
    if not is_feature_enabled(feature):
        return None
    try:
        return import_module(module)
    except Exception:
        return None


video_wallpaper = _load("video", "platform_adapters.video")
html_wallpaper = _load("html", "platform_adapters.html_wallpaper")
hotkey_backend_module = _load("hotkeys", "platform_adapters.hotkeys")
