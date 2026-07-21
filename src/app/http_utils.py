"""Dependency-free helpers for bounded HTTP responses."""
from __future__ import annotations

import json
from typing import Any, BinaryIO, Callable


class HttpResponseError(ValueError):
    """Raised when a remote response violates an application safety bound."""


def _content_length(response: Any) -> int | None:
    try:
        raw = response.headers.get("content-length")
        return int(raw) if raw is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def read_limited(response: BinaryIO, max_bytes: int) -> bytes:
    """Read at most *max_bytes* and fail if the response is larger."""
    declared = _content_length(response)
    if declared is not None and declared > max_bytes:
        raise HttpResponseError(f"远程响应过大: {declared} bytes")
    payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise HttpResponseError(f"远程响应超过 {max_bytes} bytes 限制")
    return payload


def read_json_object(response: BinaryIO, max_bytes: int) -> dict[str, Any]:
    """Read a bounded UTF-8 JSON object from an HTTP response."""
    payload = read_limited(response, max_bytes)
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpResponseError(f"远程 JSON 格式无效: {exc}") from exc
    if not isinstance(data, dict):
        raise HttpResponseError("远程 JSON 根节点必须是对象")
    return data


def validate_final_url(
    response: Any,
    allowed: Callable[[str], bool],
    *,
    fallback_url: str = "",
) -> str:
    """Validate the URL after redirects and return it.

    ``urllib`` responses expose ``geturl()``.  The fallback keeps custom test
    openers and minimal file-like adapters compatible while still validating
    the exact requested URL.
    """
    try:
        final_url = str(response.geturl())
    except (AttributeError, TypeError):
        final_url = str(fallback_url or "")
    if not final_url or not allowed(final_url):
        raise HttpResponseError(f"重定向目标不在允许列表中: {final_url or '<unknown>'}")
    return final_url
