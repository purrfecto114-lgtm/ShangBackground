# Branch-local Bing worker with explicit dependencies.
from __future__ import annotations

import os

from core import engine as core
from app.i18n import t
from PySide6.QtCore import QObject, Signal

def _resolution_source_label(source: str) -> str:
    source = str(source or "").lower()
    if source in {"screen", "system", "detect", "native"}:
        return t("主屏检测")
    if source == "requested":
        return t("用户指定")
    if source == "fallback":
        return t("回退")
    return source or t("未知来源")


class BingSyncWorker(QObject):
    finished = Signal(bool, str, str)

    def __init__(self, resolution: str):
        super().__init__()
        self.resolution = resolution

    def run(self):
        try:
            from services.bing import BingDownloader
            cache_dir = core.config.get("bing_cache_dir") or os.path.join(core.DATA_DIR, "bing_wallpapers")
            downloader = BingDownloader(cache_dir=cache_dir)
            info = downloader.fetch_wallpaper_info(resolution=self.resolution)
            if not info:
                # 把 BingDownloader 内部最后一次异常原因带回到用户提示里，
                # 而不是只显示"获取必应壁纸信息失败"这种通用文案
                reason = getattr(downloader, "last_error", None)
                reason_text = f"：{reason}" if reason else ""
                self.finished.emit(False, t("获取必应壁纸信息失败") + reason_text, "")
                return
            path = downloader.download_wallpaper(info)
            if not path:
                reason = getattr(downloader, "last_error", None)
                reason_text = f"：{reason}" if reason else ""
                self.finished.emit(False, t("下载必应壁纸失败") + reason_text, "")
                return
            if not core.switch_wallpaper_mode(
                "图片", updates={"single_image": path}
            ):
                reason = getattr(core, "last_operation_error", "") or t("设置必应壁纸失败")
                self.finished.emit(False, reason, "")
                return
            success_msg = t("已设置必应壁纸")
            self.finished.emit(True, f"{success_msg}：{info.title} / {info.resolution}（{_resolution_source_label(info.resolution_source)}）", path)
        except Exception as e:
            self.finished.emit(False, f"同步必应壁纸失败：{e}", "")
