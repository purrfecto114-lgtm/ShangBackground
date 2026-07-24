# i18n.py — Lightweight internationalization for ShangBackground
# Uses a simple dictionary-based approach. No Qt .ts/.qm overhead.
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import gzip
import json
import os
from threading import RLock
from typing import Optional
import weakref

from app.paths import LANG_DIR as _LANG_DIR

_CURRENT_LANG = "zh"
_TRANSLATIONS: dict[str, dict[str, str]] = {}
_LISTENER_LOCK = RLock()
_LISTENERS: dict[int, tuple[bool, object]] = {}
_NEXT_LISTENER_ID = 0

BASE_DIR = os.fspath(_LANG_DIR.parent)
LANG_DIR = os.fspath(_LANG_DIR)


@dataclass(frozen=True, slots=True)
class LanguageChangeEvent:
    """Notification emitted after the active JSON language changes."""

    previous: str
    current: str
    translations_loaded: bool


LanguageChangeListener = Callable[[LanguageChangeEvent], None]


def get_language() -> str:
    """Return current language code ('zh' or 'en')."""
    return _CURRENT_LANG


def _listener_entry(callback: LanguageChangeListener) -> tuple[bool, object]:
    """Keep functions strongly while weakly tracking bound Qt methods."""
    owner = getattr(callback, "__self__", None)
    if owner is not None and hasattr(callback, "__func__"):
        return True, weakref.WeakMethod(callback)  # type: ignore[arg-type]
    return False, callback

def subscribe_language_changes(callback: LanguageChangeListener) -> Callable[[], None]:
    """Subscribe to JSON-language changes and return an idempotent unsubscribe.

    Weak references avoid keeping closed Qt windows alive. The callback runs on
    the same thread that calls :func:`set_language` or :func:`load_language`, so
    UI callers should change language from the GUI thread.
    """
    if not callable(callback):
        raise TypeError("language change callback must be callable")
    global _NEXT_LISTENER_ID
    with _LISTENER_LOCK:
        _NEXT_LISTENER_ID += 1
        listener_id = _NEXT_LISTENER_ID
        _LISTENERS[listener_id] = _listener_entry(callback)

    def unsubscribe() -> None:
        with _LISTENER_LOCK:
            _LISTENERS.pop(listener_id, None)

    return unsubscribe


def _emit_language_change(previous: str, current: str, *, loaded: bool) -> None:
    event = LanguageChangeEvent(previous, current, bool(loaded))
    callbacks: list[LanguageChangeListener] = []
    stale: list[int] = []
    with _LISTENER_LOCK:
        for listener_id, (is_weak, entry) in _LISTENERS.items():
            callback = entry() if is_weak else entry
            if callback is None:
                stale.append(listener_id)
            else:
                callbacks.append(callback)  # type: ignore[arg-type]
        for listener_id in stale:
            _LISTENERS.pop(listener_id, None)
    for callback in callbacks:
        try:
            callback(event)
        except Exception:
            # Translation changes must never make the application unusable just
            # because an optional surface failed to refresh. UI owners log their
            # own failures and can rebuild on the next change.
            continue


def set_language(lang: str) -> None:
    """Set current language code and notify subscribers when it changes."""
    global _CURRENT_LANG
    if lang not in ("zh", "en"):
        return
    previous = _CURRENT_LANG
    _CURRENT_LANG = lang
    if previous != lang:
        _emit_language_change(previous, lang, loaded=lang == "zh" or bool(_TRANSLATIONS.get(lang)))


def load_language(lang: str) -> None:
    """Load a language file from lang/<lang>.json and activate it.

    Translation files are shipped as normal UTF-8 JSON. Some packaging steps in
    older builds accidentally gzip-compressed en.json while keeping the .json
    suffix, which made runtime language switching silently fall back to Chinese.
    The loader accepts both formats so source runs, VS Code runs and packaged
    runs behave the same. New builds reject such disguised gzip resources before
    freezing; this runtime probe remains as backward-compatible recovery.
    """
    global _CURRENT_LANG, _TRANSLATIONS
    if lang not in ("zh", "en"):
        return
    previous = _CURRENT_LANG
    loaded = lang == "zh"
    if lang == "zh":
        _TRANSLATIONS.setdefault("zh", {})
    else:
        path = os.path.join(LANG_DIR, f"{lang}.json")
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                if raw.startswith(b"\x1f\x8b"):
                    raw = gzip.decompress(raw)
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("translation root must be a JSON object")
                _TRANSLATIONS[lang] = {
                    str(key): str(value) for key, value in payload.items()
                }
                loaded = True
            except Exception:
                _TRANSLATIONS[lang] = {}
        else:
            _TRANSLATIONS[lang] = {}
    _CURRENT_LANG = lang
    if previous != lang or lang != "zh":
        _emit_language_change(previous, lang, loaded=loaded)


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
