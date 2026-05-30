"""
Bing 壁纸下载器 - 同步版，适合直接集成到 GUI 按钮。

修复点：
- resolution 不再只是文件名，而是参与 Bing 图片 URL 构造。
- 默认 resolution='auto'：检测系统主屏分辨率；检测失败回退 1920x1080。
- 下载失败时自动尝试 1920x1080、UHD、API 原始 URL。
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from urllib.parse import urljoin

try:
    import httpx
except ImportError:  # pragma: no cover - httpx 为可选依赖，缺失时降级
    httpx = None

try:
    from display_resolution import DEFAULT_RESOLUTION, choose_resolution
except ImportError:
    from .display_resolution import DEFAULT_RESOLUTION, choose_resolution


@dataclass
class WallpaperInfo:
    id: str
    title: str
    url: str
    copyright: str
    date: str
    resolution: str = DEFAULT_RESOLUTION
    urlbase: str = ""
    resolution_source: str = ""


class BingDownloader:
    API_URLS = [
        "https://www.bing.com/HPImageArchive.aspx",
        "https://cn.bing.com/HPImageArchive.aspx",
    ]
    IMAGE_BASES = ["https://www.bing.com", "https://cn.bing.com"]
    HEADERS = {
        "User-Agent": "ShangBackground/1.1",
        "Accept": "application/json,text/javascript,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://cn.bing.com/",
    }

    def __init__(self, cache_dir: str | None = None, fallback_resolution: str = DEFAULT_RESOLUTION):
        if cache_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            cache_dir = os.path.join(base_dir, "bing_wallpapers")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.fallback_resolution = fallback_resolution

    def _fetch_metadata(self, index: int, mkt: str) -> Optional[dict]:
        if httpx is None:
            print("httpx 未安装，无法获取 Bing 壁纸信息")
            return None
        params = {"format": "js", "idx": index, "n": 1, "mkt": mkt}
        last_error: Exception | None = None
        for api in self.API_URLS:
            try:
                with httpx.Client(headers=self.HEADERS, timeout=20, follow_redirects=True) as client:
                    response = client.get(api, params=params)
                    response.raise_for_status()
                    data = response.json()
                    images = data.get("images") or []
                    if images:
                        return images[0]
            except Exception as exc:
                last_error = exc
        print(f"获取壁纸信息失败: {last_error}")
        return None

    def _url_candidates(self, img: dict, resolution: str) -> list[str]:
        urlbase = img.get("urlbase") or ""
        raw = img.get("url") or ""
        candidates: list[str] = []
        if urlbase:
            for base in self.IMAGE_BASES:
                candidates.append(urljoin(base, f"{urlbase}_{resolution}.jpg"))
                if resolution != self.fallback_resolution:
                    candidates.append(urljoin(base, f"{urlbase}_{self.fallback_resolution}.jpg"))
                candidates.append(urljoin(base, f"{urlbase}_UHD.jpg"))
        if raw:
            for base in self.IMAGE_BASES:
                candidates.append(urljoin(base, raw))
        seen = set()
        return [u for u in candidates if u and not (u in seen or seen.add(u))]

    def fetch_wallpaper_info(self, index: int = 0, mkt: str = "zh-CN", resolution: str | None = "auto") -> Optional[WallpaperInfo]:
        res = choose_resolution(resolution, fallback=self.fallback_resolution)
        img = self._fetch_metadata(index, mkt)
        if not img:
            return None
        img_id = img.get("hsh", hashlib.md5(str(img.get("url", "")).encode()).hexdigest()[:16])
        candidates = self._url_candidates(img, res.resolution)
        return WallpaperInfo(
            id=f"{img_id}_{res.resolution}",
            title=img.get("title", "") or img.get("copyright", "Bing Wallpaper"),
            url=candidates[0] if candidates else "",
            copyright=img.get("copyright", ""),
            date=img.get("startdate", datetime.now().strftime("%Y%m%d")),
            resolution=res.resolution,
            urlbase=img.get("urlbase", ""),
            resolution_source=res.source,
        )

    def download_wallpaper(self, info: WallpaperInfo, resolution: str | None = None) -> Optional[str]:
        if resolution and resolution not in {info.resolution, "auto", "system", "detect", "native"}:
            # 允许调用方覆盖 info 的分辨率。
            updated = self.fetch_wallpaper_info(0, resolution=resolution)
            if updated:
                info = updated
        filename = f"bing_{info.date}_{info.resolution}.jpg"
        filepath = self.cache_dir / filename
        if filepath.exists() and filepath.stat().st_size > 1024:
            return str(filepath)

        if httpx is None:
            print("httpx 未安装，无法下载 Bing 壁纸")
            return None
        img_stub = {"urlbase": info.urlbase, "url": info.url}
        urls = self._url_candidates(img_stub, info.resolution) or [info.url]
        last_error: Exception | None = None
        with httpx.Client(headers=self.HEADERS, timeout=30, follow_redirects=True) as client:
            for url in urls:
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    ctype = response.headers.get("content-type", "")
                    if "image" not in ctype.lower() and not response.content.startswith(b"\xff\xd8"):
                        raise ValueError(f"响应不是图片: {ctype}")
                    filepath.write_bytes(response.content)
                    info.url = url
                    print(f"壁纸已下载: {filepath}")
                    return str(filepath)
                except Exception as exc:
                    last_error = exc
        print(f"下载壁纸失败: {last_error}")
        return None

    def fetch_and_download(self, index: int = 0, mkt: str = "zh-CN", resolution: str | None = "auto") -> Optional[str]:
        info = self.fetch_wallpaper_info(index, mkt, resolution)
        if info:
            return self.download_wallpaper(info)
        return None

    def fetch_history(self, days: int = 7, mkt: str = "zh-CN", resolution: str | None = "auto") -> List[WallpaperInfo]:
        wallpapers: list[WallpaperInfo] = []
        for i in range(min(days, 8)):
            info = self.fetch_wallpaper_info(i, mkt, resolution)
            if info:
                wallpapers.append(info)
        return wallpapers

    def prefetch_wallpapers(self, count: int = 7, mkt: str = "zh-CN", resolution: str | None = "auto") -> List[str]:
        paths: list[str] = []
        for wp in self.fetch_history(count, mkt, resolution):
            path = self.download_wallpaper(wp)
            if path:
                paths.append(path)
        return paths

    def get_cached_wallpapers(self) -> List[str]:
        if not self.cache_dir.exists():
            return []
        return sorted([
            str(f) for f in self.cache_dir.iterdir()
            if f.is_file() and f.suffix.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}
        ], reverse=True)

    def get_latest_cached(self) -> Optional[str]:
        cached = self.get_cached_wallpapers()
        return cached[0] if cached else None


_downloader = None


def get_downloader() -> BingDownloader:
    global _downloader
    if _downloader is None:
        _downloader = BingDownloader()
    return _downloader


if __name__ == "__main__":
    dl = BingDownloader()
    print("正在获取并下载今日 Bing 壁纸...")
    print(dl.fetch_and_download(resolution="auto"))
