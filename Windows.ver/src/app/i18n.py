# i18n.py — Lightweight internationalization for ShangBackground
# Uses a simple dictionary-based approach. No Qt .ts/.qm overhead.
from __future__ import annotations

import json
import os

from app.paths import LANG_DIR as _LANG_DIR
from typing import Optional

_CURRENT_LANG = "zh"
_TRANSLATIONS: dict[str, dict[str, str]] = {}

BASE_DIR = os.fspath(_LANG_DIR.parent)
LANG_DIR = os.fspath(_LANG_DIR)


def get_language() -> str:
    """Return current language code ('zh' or 'en')."""
    return _CURRENT_LANG


def set_language(lang: str) -> None:
    """Set current language code."""
    global _CURRENT_LANG
    if lang in ("zh", "en"):
        _CURRENT_LANG = lang


def load_language(lang: str) -> None:
    """Load a language file from lang/<lang>.json and activate it."""
    global _CURRENT_LANG, _TRANSLATIONS
    path = os.path.join(LANG_DIR, f"{lang}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _TRANSLATIONS[lang] = json.load(f)
        except Exception:
            _TRANSLATIONS[lang] = {}
    else:
        _TRANSLATIONS[lang] = {}
    _CURRENT_LANG = lang


def t(key: str, default: Optional[str] = None) -> str:
    """Translate a key to the current language.

    Falls back to the key itself if no translation found.
    For 'zh' language, returns the key as-is (Chinese is the default).
    """
    if _CURRENT_LANG == "zh":
        return default if default is not None else key
    trans = _TRANSLATIONS.get(_CURRENT_LANG, {})
    result = trans.get(key)
    if result is not None:
        return result
    return default if default is not None else key


def init_i18n(config: dict) -> None:
    """Initialize i18n from config dict. Call once at startup."""
    lang = config.get("language", "zh")
    load_language(lang)


# ── Convenience: translate STYLE_MAP keys ──────────────────────────────
