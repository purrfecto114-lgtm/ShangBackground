"""Persistence policy for dismissing optional dependency warnings.

The dismissal is deliberately scoped to the current application version and
exact missing-dependency set.  A new release or a changed dependency state
therefore prompts again.  Required dependencies can never be suppressed.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.version import APP_VERSION

CONFIG_KEY = "ignored_dependency_warning_signature"


def dependency_warning_signature(missing: Iterable[Mapping[str, Any]]) -> str:
    """Return a stable signature for one app version and dependency set."""
    identities = sorted(
        {
            f"{str(dep.get('module') or '').strip()}:{str(dep.get('package') or '').strip()}"
            for dep in missing
            if str(dep.get("module") or dep.get("package") or "").strip()
        }
    )
    return f"{APP_VERSION}|" + "|".join(identities)


def _contains_required(missing: Iterable[Mapping[str, Any]]) -> bool:
    return any(bool(dep.get("required")) for dep in missing)


def dependency_warning_is_suppressed(missing: Iterable[Mapping[str, Any]]) -> bool:
    """Return whether this optional warning was dismissed by the user."""
    items = tuple(missing)
    if not items or _contains_required(items):
        return False
    try:
        from core import engine as core

        stored = str(core.config.get(CONFIG_KEY, "") or "")
        return stored == dependency_warning_signature(items)
    except Exception:
        return False


def suppress_dependency_warning(missing: Iterable[Mapping[str, Any]]) -> bool:
    """Persist dismissal for this optional dependency set.

    Factory reset clears the setting because its default value is an empty
    string.  Persistence errors are logged and reported as ``False`` while the
    current dialog may still close normally.
    """
    items = tuple(missing)
    if not items or _contains_required(items):
        return False
    try:
        from core import engine as core

        core.config[CONFIG_KEY] = dependency_warning_signature(items)
        core.save_config()
        return True
    except Exception as exc:
        try:
            from app.log_setup import get_logger

            get_logger("dependencies").warning(
                "failed to persist dependency-warning dismissal: %s", exc, exc_info=True
            )
        except Exception:
            pass
        return False
