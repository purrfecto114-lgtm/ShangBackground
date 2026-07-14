from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, cast
from urllib import request
from urllib.parse import urlparse

from core import engine as core
from app.config import PLATFORM_ID, UPDATE_CHECK_TIMEOUT_SECONDS

GITHUB_REPO = "purrfecto114-lgtm/ShangBackground"
GITHUB_PROJECT_URL = f"https://github.com/{GITHUB_REPO}"
GITHUB_LATEST_RELEASE_URL = f"{GITHUB_PROJECT_URL}/releases/latest"
GITHUB_LATEST_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_API_VERSION = "2026-03-10"
MAX_RELEASE_JSON_BYTES = 2 * 1024 * 1024
VERSION_RE = re.compile(
    r"(?<!\d)[vV]?\s*(?:app[_\s-]*ver(?:sion)?\s*[:=]\s*)?"
    r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.\d+)*"
    r"(?:[-+][0-9A-Za-z][0-9A-Za-z._-]*)?(?![\d.])"
)

PLATFORM_ASSET_MARKERS: dict[str, tuple[str, ...]] = {
    "windows": ("windows", "win", "win64", "win32", "x64", "x86_64", ".exe", ".msi"),
    "linux": ("linux", "ubuntu", "debian", "appimage", "x86_64", "amd64", ".tar.gz", ".deb", ".rpm"),
    "macos": ("macos", "mac", "darwin", "osx", "x64", "arm64", ".dmg", ".pkg", ".app", ".zip"),
}
PACKAGE_EXTENSIONS = (".exe", ".msi", ".zip", ".7z", ".tar.gz", ".tar.xz", ".tgz", ".appimage", ".deb", ".rpm", ".dmg", ".pkg")
SOURCE_MARKERS = ("source", "src", "源码", "source code")


def _is_repository_github_url(value: str | None) -> bool:
    """Allow only HTTPS URLs for this repository or its GitHub API namespace."""
    if not value:
        return False
    parsed = urlparse(str(value))
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    repo_path = f"/{GITHUB_REPO.lower()}"
    if host == "github.com":
        return path.lower() == repo_path or path.lower().startswith(repo_path + "/")
    api_path = f"/repos/{GITHUB_REPO.lower()}"
    return host == "api.github.com" and (
        path.lower() == api_path or path.lower().startswith(api_path + "/")
    )


def _github_url_or_default(value: str | None, default: str) -> str:
    return str(value) if _is_repository_github_url(value) else default


@dataclass(slots=True)
class ReleaseInfo:
    version: str = "0.0.0"
    tag: str = ""
    name: str = ""
    url: str = GITHUB_LATEST_RELEASE_URL
    project_url: str = GITHUB_PROJECT_URL
    published_at: str = ""
    body: str = ""
    assets: list[dict[str, Any]] = field(default_factory=list)
    compatible_assets: list[dict[str, Any]] = field(default_factory=list)
    selected_asset: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tag": self.tag,
            "name": self.name,
            "url": self.url,
            "project_url": self.project_url,
            "published_at": self.published_at,
            "body": self.body,
            "assets": self.assets,
            "compatible_assets": self.compatible_assets,
            "selected_asset": self.selected_asset,
            "platform_id": PLATFORM_ID,
        }


def parse_version(value: str | None) -> tuple[int, int, int]:
    """Parse two-, three-, or four+-segment versions from release metadata.

    Extra segments (e.g. the 4th in ``1.4.0.0`` used by APP_VERSION_FILE) are
    ignored so a release tagged ``v1.4.0.0`` is correctly compared against a
    running ``1.4.0`` instead of being misparsed as ``4.0.0``.
    """
    text = str(value or "").strip()
    match = VERSION_RE.search(text)
    if not match:
        raise ValueError(
            "无法解析版本号，需要两段或三段式版本，如 1.3 / 1.3.0 / v1.3.0 / "
            f"app_ver=1.3：{text or '<empty>'}"
        )
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def normalize_tag(tag: str | None) -> str:
    major, minor, patch = parse_version(tag)
    return f"{major}.{minor}.{patch}"


def _pick_version_source(data: dict[str, Any]) -> str:
    for item in (data.get("tag_name"), data.get("name")):
        if item and VERSION_RE.search(str(item)):
            return str(item)
    for asset in data.get("assets", []) or []:
        if not isinstance(asset, dict):
            continue
        for key in ("name", "browser_download_url"):
            item = asset.get(key)
            if item and VERSION_RE.search(str(item)):
                return str(item)
    return str(data.get("tag_name") or "")


def _has_asset_marker(text: str, marker: str) -> bool:
    marker = marker.lower()
    if marker.startswith("."):
        return marker in text
    return re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", text) is not None


def _asset_ext_rank(text: str) -> int:
    """Prefer directly installable binaries, then normal archives, then package archives."""
    if text.endswith((".exe", ".msi", ".dmg", ".pkg", ".appimage", ".deb", ".rpm")):
        return 12
    if text.endswith(".zip"):
        return 10
    if text.endswith((".7z", ".tar.gz", ".tar.xz", ".tgz")):
        return 8
    return 0


def _asset_score(asset: dict[str, Any], platform_id: str = PLATFORM_ID) -> int:
    text = f"{asset.get('name', '')} {asset.get('download_url', '')}".lower()
    if not any(ext in text for ext in PACKAGE_EXTENSIONS):
        return 0
    if any(_has_asset_marker(text, marker) for marker in SOURCE_MARKERS):
        return 0

    own_markers = PLATFORM_ASSET_MARKERS.get(platform_id, ())
    own_hits = sum(_has_asset_marker(text, marker) for marker in own_markers)
    foreign_hits = sum(
        _has_asset_marker(text, marker)
        for other_platform, markers in PLATFORM_ASSET_MARKERS.items()
        if other_platform != platform_id
        for marker in markers
    )

    # A generic ShangBackground archive is still useful when the release only
    # publishes platform-neutral ZIP packages. It is not allowed to outrank an
    # explicitly platform-matched executable/archive.
    generic_project_hit = 1 if "shangbackground" in text and foreign_hits == 0 else 0
    if own_hits == 0 and not generic_project_hit:
        return 0
    return own_hits * 30 + generic_project_hit * 8 + _asset_ext_rank(text) - foreign_hits * 35


def _annotate_asset(asset: dict[str, Any], platform_id: str = PLATFORM_ID) -> dict[str, Any]:
    item = dict(asset)
    text = f"{item.get('name', '')} {item.get('download_url', '')}".lower()
    item["score"] = _asset_score(item, platform_id)
    item["asset_type"] = next((ext.lstrip(".") for ext in PACKAGE_EXTENSIONS if ext in text), "file")
    item["platform_match"] = item["score"] > 0
    return item


def select_compatible_assets(
    assets: list[dict[str, Any]], platform_id: str = PLATFORM_ID
) -> list[dict[str, Any]]:
    """Return all usable release assets for the current platform, sorted best-first.

    Future releases may publish several choices for one platform, for example a
    portable ZIP, an installer EXE/MSI, and an archive for advanced users. The UI
    consumes this list directly instead of hiding everything behind one guessed URL.
    """
    annotated = [_annotate_asset(asset, platform_id) for asset in assets]
    compatible = [asset for asset in annotated if int(asset.get("score") or 0) > 0]
    compatible.sort(key=lambda a: (int(a.get("score") or 0), int(a.get("size") or 0)), reverse=True)
    return compatible


def select_best_asset(
    assets: list[dict[str, Any]], platform_id: str = PLATFORM_ID
) -> dict[str, Any]:
    compatible = select_compatible_assets(assets, platform_id)
    return dict(compatible[0]) if compatible else {}


def fetch_latest_github_release(timeout: int | None = None) -> ReleaseInfo:
    timeout_seconds = int(timeout or UPDATE_CHECK_TIMEOUT_SECONDS or 8)
    req = request.Request(
        GITHUB_LATEST_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ShangBackground-Updater",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
    )
    if not _is_repository_github_url(GITHUB_LATEST_API_URL):
        raise ValueError("GitHub Release API URL 不在允许列表中")
    # The URL is restricted to the repository HTTPS allowlist immediately above.
    with request.urlopen(req, timeout=timeout_seconds) as response:  # nosec B310
        payload = response.read(MAX_RELEASE_JSON_BYTES + 1)
    if len(payload) > MAX_RELEASE_JSON_BYTES:
        raise ValueError("GitHub Release 响应过大")

    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GitHub Release 响应格式无效")

    assets: list[dict[str, Any]] = []
    for asset in data.get("assets", []) or []:
        if not isinstance(asset, dict):
            continue
        download_url = str(asset.get("browser_download_url") or "")
        if not _is_repository_github_url(download_url):
            continue
        assets.append(
            {
                "name": str(asset.get("name") or ""),
                "size": int(asset.get("size") or 0),
                "download_url": download_url,
            }
        )
    compatible_assets = select_compatible_assets(assets)

    # Defensive: if the release JSON lacks any parseable version (e.g. a draft
    # release or one tagged with a non-numeric name), default to "0.0.0" so
    # check_latest_release reports "no update" instead of raising ValueError
    # and showing the user a confusing "无法解析版本号" message.
    try:
        version = normalize_tag(_pick_version_source(data))
    except ValueError:
        version = "0.0.0"

    return ReleaseInfo(
        version=version,
        tag=str(data.get("tag_name") or ""),
        name=str(data.get("name") or ""),
        url=_github_url_or_default(data.get("html_url"), GITHUB_LATEST_RELEASE_URL),
        published_at=str(data.get("published_at") or ""),
        body=str(data.get("body") or ""),
        assets=[_annotate_asset(asset) for asset in assets],
        compatible_assets=compatible_assets,
        selected_asset=dict(compatible_assets[0]) if compatible_assets else {},
    )


def check_latest_release(
    current_version: str, timeout: int | None = None
) -> tuple[bool, ReleaseInfo]:
    info = fetch_latest_github_release(timeout=timeout)
    return parse_version(info.version) > parse_version(current_version), info


def check_updates_headless(
    current_version: str | None = None, timeout: int | None = None
) -> tuple[bool, str, dict[str, Any]]:
    try:
        installed_version = current_version or getattr(core, "VERSION", "1.4.0")
        has_update, info = check_latest_release(installed_version, timeout=timeout)
        info_dict = info.as_dict()
        info_dict["has_update"] = has_update
        return True, ("发现新版本" if has_update else "当前已是最新版本"), info_dict
    except Exception as exc:
        return False, f"检查更新失败：{exc}", {}


try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover - import-safe headless fallback
    QThread = None
    Signal = None


class _ImmediateSignal:
    def __init__(self) -> None:
        self._callbacks: list[Any] = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


_BaseUpdateChecker = cast(type, QThread if QThread is not None else object)


class UpdateChecker(_BaseUpdateChecker):
    """Update worker with one public class for both Qt and headless contexts."""

    if QThread is not None and Signal is not None:
        finished = Signal(bool, str, dict)

    def __init__(
        self,
        current_version: str | None = None,
        timeout: int | None = None,
        parent=None,
    ) -> None:
        if QThread is not None:
            super().__init__(parent)
        else:
            del parent
            self.finished = _ImmediateSignal()
        self.current_version = current_version
        self.timeout = timeout

    def start(self) -> None:
        if QThread is not None:
            super().start()
        else:
            self.run()

    def run(self) -> None:
        ok, message, info = check_updates_headless(self.current_version, self.timeout)
        self.finished.emit(ok, message, info)
