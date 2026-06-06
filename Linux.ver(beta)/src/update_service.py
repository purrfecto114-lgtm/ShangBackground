from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib import request

GITHUB_REPO = "purrfecto114-lgtm/ShangBackground"
GITHUB_PROJECT_URL = f"https://github.com/{GITHUB_REPO}"
GITHUB_LATEST_RELEASE_URL = f"{GITHUB_PROJECT_URL}/releases/latest"
GITHUB_LATEST_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


@dataclass(slots=True)
class ReleaseAsset:
    name: str = ""
    size: int = 0
    download_url: str = ""


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
        }


def parse_version(value: str | None) -> tuple[int, ...]:
    """Parse loose release tags such as v1.3.1-linux into comparable tuples."""
    parts = re.findall(r"\d+", str(value or ""))[:4]
    return tuple(int(part) for part in parts) if parts else (0,)


def normalize_tag(tag: str | None) -> str:
    return re.sub(r"^[vV]", "", str(tag or "0.0.0").strip()) or "0.0.0"


def fetch_latest_github_release(timeout: int = 12) -> ReleaseInfo:
    """Fetch the current GitHub Release. This is the single network update source."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ShangBackground-Updater",
    }
    req = request.Request(GITHUB_LATEST_API_URL, headers=headers)
    with request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    assets: list[dict[str, Any]] = []
    for asset in data.get("assets", []) or []:
        assets.append({
            "name": asset.get("name", "") or "",
            "size": int(asset.get("size") or 0),
            "download_url": asset.get("browser_download_url", "") or "",
        })

    tag = data.get("tag_name") or "0.0.0"
    return ReleaseInfo(
        version=normalize_tag(tag),
        tag=tag,
        name=data.get("name") or "",
        url=data.get("html_url") or GITHUB_LATEST_RELEASE_URL,
        project_url=GITHUB_PROJECT_URL,
        published_at=data.get("published_at") or "",
        body=data.get("body") or "",
        assets=assets,
    )


def check_latest_release(current_version: str, timeout: int = 12) -> tuple[bool, ReleaseInfo]:
    """Return (has_update, release_info) using GitHub Release as the unified update source."""
    info = fetch_latest_github_release(timeout=timeout)
    return parse_version(info.version) > parse_version(current_version), info


def sync_legacy_update_state(core_module: Any, info: ReleaseInfo | None, has_update: bool, failed: bool = False) -> None:
    """Keep legacy update globals alive for old UI/status code while using GitHub only."""
    try:
        if info is None:
            core_module.remote_version = ""
            core_module.remote_release_notes = ""
            core_module.remote_download_urls = {"GitHub Release": "", "发布页": GITHUB_LATEST_RELEASE_URL}
        else:
            first_asset = info.assets[0].get("download_url", "") if info.assets else ""
            core_module.remote_version = info.version or ""
            core_module.remote_release_notes = info.body or ""
            core_module.remote_download_urls = {"GitHub Release": first_asset, "发布页": info.url or GITHUB_LATEST_RELEASE_URL}
        core_module.show_update_flag = bool(has_update)
        core_module.check_failed = bool(failed)
    except Exception:
        # Update state is best-effort compatibility only; never break startup/update UI.
        pass
