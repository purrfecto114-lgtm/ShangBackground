from __future__ import annotations

import json
import re
from typing import Any
from urllib import request

import core_engine as core

# GitHub release endpoints (kept in sync with main.py usage)
GITHUB_REPO = "purrfecto114-lgtm/ShangBackground"
GITHUB_PROJECT_URL = f"https://github.com/{GITHUB_REPO}"
GITHUB_LATEST_RELEASE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
GITHUB_LATEST_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(value: str):
    import re as _re

    parts = _re.findall(r"\d+", str(value or ""))[:4]
    return tuple(int(p) for p in parts) if parts else (0,)


def _sync_legacy_update_state(latest_version: str, notes: str, assets: list[dict[str, Any]], has_update: bool, failed: bool = False):
    """Sync update metadata into `core` module so old code paths read the same state."""
    try:
        core.remote_version = latest_version or ""
        core.remote_release_notes = notes or ""
        first_asset = assets[0].get("download_url", "") if assets else ""
        core.remote_download_urls = {"GitHub Release": first_asset, "发布页": GITHUB_LATEST_RELEASE_URL}
        core.show_update_flag = bool(has_update)
        core.check_failed = bool(failed)
    except Exception:
        pass


def check_updates_headless(timeout: int = 12) -> tuple[bool, str, dict]:
    """Query GitHub Releases API and return (ok, message, info).

    This function is safe to call without any GUI available and also
    updates the `core` module state for compatibility.
    """
    try:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ShangBackground-Updater",
        }
        req = request.Request(GITHUB_LATEST_API_URL, headers=headers)
        with request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "0.0")
        latest_version = re.sub(r"^[vV]", "", tag)
        assets = []
        for asset in data.get("assets", []):
            assets.append({
                "name": asset.get("name", ""),
                "size": int(asset.get("size") or 0),
                "download_url": asset.get("browser_download_url", ""),
            })
        info = {
            "version": latest_version,
            "tag": tag,
            "name": data.get("name") or "",
            "url": data.get("html_url") or GITHUB_LATEST_RELEASE_URL,
            "project_url": GITHUB_PROJECT_URL,
            "published_at": data.get("published_at") or "",
            "body": data.get("body") or "",
            "assets": assets,
        }
        has_update = _parse_version(latest_version) > _parse_version(core.VERSION)
        _sync_legacy_update_state(latest_version, info["body"], assets, has_update)
        return True, ("发现新版本" if has_update else "当前已是最新版本"), info
    except Exception as exc:
        _sync_legacy_update_state("", "", [], False, failed=True)
        return False, f"检查更新失败：{exc}", {}


try:
    # QThread based UpdateChecker to integrate with Qt UI.
    from PySide6.QtCore import QThread, Signal


    class UpdateChecker(QThread):
        finished = Signal(bool, str, dict)

        def run(self):
            ok, msg, info = check_updates_headless()
            # emit values matching the original main.py contract
            self.finished.emit(ok, msg, info)

except Exception:
    # PySide6 not available; provide a threading-based shim (headless only).
    import threading


    class UpdateChecker:
        """Fallback updater: not a QThread and has no Qt Signal support.

        This is only suitable for non-GUI usage. The main GUI code imports the
        QThread-backed class and will not use this shim.
        """

        def __init__(self):
            self._thread = threading.Thread(target=self._run)
            self.finished = lambda ok, msg, info: None

        def _run(self):
            ok, msg, info = check_updates_headless()
            try:
                self.finished(ok, msg, info)
            except Exception:
                pass

        def start(self):
            self._thread.start()
