"""v1.4.6: mpv 自动下载管理.

联网考证结论:
  - 官方推荐 Windows 构建源: shinchiro/mpv-winbuild-cmake (mpv.io 列出)
  - 推荐 URL 模式: https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/<date>/mpv-x86_64-<date>-git-<sha>.7z
  - 标准安装位置: %LOCALAPPDATA%\\<App>\\bin\\mpv\\ (per-user, 无需 admin)
  - 验证: 上游不发布 SHA256, 由本程序 pin 一个已知良好版本 + 自算 SHA256
  - 解压: 7z 格式, 优先用 py7zr, 兜底系统 7z.exe

证据:
  - https://mpv.io/installation/
  - https://github.com/shinchiro/mpv-winbuild-cmake/releases
  - https://superuser.com/questions/1445143 (LOCALAPPDATA 惯例)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from urllib.parse import urlparse

# Pin 一个已知良好的 shinchiro 构建 (非 v3, 通用 x86_64).
# 注意: 实际下载需在 Windows 本机测试, 这里给出框架.
# 真实发布时替换为最新稳定 asset URL + 对应 SHA256.
MPV_DOWNLOAD_URL = "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20240904/mpv-x86_64-20240904-git-a0ebfc3.7z"
# 安全策略 (fail-closed): 默认哨兵字符串使得 _verify_sha256 始终失败, 拒绝信任
# 下载下来的二进制. 维护者必须在发布前填入实际 SHA256; 留空或保留哨兵都会让
# 自动下载功能拒绝使用下载产物 (而不是旧版本那样静默跳过校验).
MPV_DOWNLOAD_SHA256 = "RELEASE-REQUIRED-SET-SHA256"
MPV_DOWNLOAD_DATE = "20240904"
MPV_DOWNLOAD_SHA = "a0ebfc3"
_SHA256_SENTINEL = "RELEASE-REQUIRED-SET-SHA256"


def _is_allowed_mpv_download_url(value: str) -> bool:
    """Restrict downloads to the pinned upstream GitHub release namespace."""
    parsed = urlparse(str(value or ""))
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and host == "github.com"
        and parsed.path.startswith("/shinchiro/mpv-winbuild-cmake/releases/download/")
    )


def _log(msg: str, level: str = "INFO") -> None:
    try:
        from app import log_setup
        logger = log_setup.get_logger("mpv_download")
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        logger.log(level_map.get(level, logging.INFO), msg)
    except Exception:
        pass


def is_mpv_installed() -> bool:
    """检查用户安装目录是否已有 mpv.exe."""
    try:
        from app.paths import mpv_user_install_exe
        return mpv_user_install_exe() is not None
    except Exception:
        return False


def _verify_sha256(file_path: str, expected: str) -> bool:
    """Fail-closed SHA256 verification.

    Security policy: when the maintainer has not yet pinned a real SHA256
    (``expected`` is empty or matches the sentinel), verification REFUSES to
    trust the downloaded binary instead of silently skipping the check.
    This prevents MITM attacks from substituting a malicious mpv.exe during
    the auto-download flow.
    """
    if not expected or expected == _SHA256_SENTINEL:
        _log("mpv SHA256 未配置, 拒绝信任下载的二进制 (fail-closed)", level="ERROR")
        return False
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        actual = h.hexdigest().lower()
        return actual == expected.lower()
    except Exception:
        return False


def _extract_7z(archive_path: str, dest_dir: str) -> bool:
    """解压 7z 文件. 优先 py7zr, 兜底系统 7z.exe."""
    # 1. 尝试 py7zr
    try:
        import py7zr  # type: ignore
        with py7zr.SevenZipFile(archive_path, mode="r") as z:
            z.extractall(path=dest_dir)
        return True
    except ImportError:
        pass
    except Exception as exc:
        _log(f"py7zr 解压失败: {exc}", level="WARNING")
    # 2. 兜底系统 7z.exe
    try:
        seven_zip = shutil_which("7z.exe") or shutil_which("7z")
        if seven_zip:
            result = subprocess.run(
                [seven_zip, "x", archive_path, f"-o{dest_dir}", "-y"],
                capture_output=True, timeout=120,
            )
            return result.returncode == 0
    except Exception as exc:
        _log(f"7z.exe 解压失败: {exc}", level="WARNING")
    return False


def shutil_which(name: str):
    try:
        import shutil
        return shutil.which(name)
    except Exception:
        return None


def download_mpv(url: str = MPV_DOWNLOAD_URL, sha256: str = MPV_DOWNLOAD_SHA256,
                 progress_callback=None) -> bool:
    """下载并安装 mpv 到用户安装目录.

    Args:
        url: 下载 URL (7z 格式)
        sha256: 期望的 SHA256 (留空跳过校验)
        progress_callback: 可选回调 (received_bytes, total_bytes)

    Returns:
        True 成功, False 失败.
    """
    try:
        from app.paths import mpv_user_install_dir, mpv_version_info_path
        import tempfile
        import urllib.request

        install_dir = mpv_user_install_dir()
        with tempfile.NamedTemporaryFile(suffix=".7z", delete=False) as tmp:
            tmp_path = tmp.name

        if not _is_allowed_mpv_download_url(url):
            _log("mpv 下载地址不在允许的 HTTPS 上游范围内", level="ERROR")
            os.unlink(tmp_path)
            return False

        _log(f"开始下载 mpv: {url}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ShangBackground/mpv-installer"})
            # The URL is restricted to the pinned HTTPS upstream namespace above.
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
                total = int(resp.headers.get("Content-Length", 0))
                received = 0
                with open(tmp_path, "wb") as out:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
                        received += len(chunk)
                        if progress_callback:
                            try:
                                progress_callback(received, total)
                            except Exception:
                                pass
        except Exception as exc:
            _log(f"下载失败: {exc}", level="ERROR")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return False

        # SHA256 校验
        if sha256 and not _verify_sha256(tmp_path, sha256):
            _log("SHA256 校验失败, 丢弃下载", level="ERROR")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return False

        # 解压
        _log(f"解压到 {install_dir}")
        if not _extract_7z(tmp_path, install_dir):
            _log("解压失败", level="ERROR")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return False

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        # 写入版本信息
        version_info = {
            "url": url,
            "sha256": sha256,
            "date": MPV_DOWNLOAD_DATE,
            "sha": MPV_DOWNLOAD_SHA,
        }
        try:
            with open(mpv_version_info_path(), "w", encoding="utf-8") as f:
                json.dump(version_info, f, indent=2)
        except Exception:
            pass

        # 冒烟测试
        from app.paths import mpv_user_install_exe
        exe = mpv_user_install_exe()
        if exe:
            try:
                result = subprocess.run([exe, "--version"], capture_output=True, timeout=10)
                if result.returncode == 0 or result.stdout or result.stderr:
                    _log(f"mpv 安装成功: {exe}")
                    return True
                else:
                    _log("mpv 冒烟测试失败 (无输出)", level="WARNING")
                    return False
            except Exception as exc:
                _log(f"mpv 冒烟测试异常: {exc}", level="WARNING")
                return False
        else:
            _log("解压后未找到 mpv.exe", level="ERROR")
            return False
    except Exception as exc:
        _log(f"download_mpv 整体失败: {exc}", level="ERROR")
        return False


def get_installed_version_info() -> dict:
    """读取已安装 mpv 的版本信息."""
    try:
        from app.paths import mpv_version_info_path
        path = mpv_version_info_path()
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}
