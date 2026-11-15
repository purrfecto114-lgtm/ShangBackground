"""五项已报告缺陷的回归测试（先红后绿）。

来源：诊断报告《ShangBackground_三端漂移与五项问题复查终稿》(2026-11-13)，
本轮独立复现全部成立后，按 TDD 流程先写失败测试再修复。

- 问题 1（P1）: Intel Mac 更新器排除正确 x86_64 包、选中 arm64 包。
- 问题 2（P1）: capture 降级路径的可达污染窗口——应用自管图片被记为
  “启动前原壁纸”，退出恢复到错误状态。
- 问题 3（P0）: 模式切换失败回滚撤销用户在事务期间保存的设置。
- 问题 4（P1）: 音量 IPC 无应答时整条同步链阻塞调用线程整个 timeout。
- 问题 5（P0）: 同图模式切换返回成功但磁盘 mode 从未落盘。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from threading import RLock
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# 问题 1: 资产评分的平台/架构标记语义
# ---------------------------------------------------------------------------


class TestIssue1AssetArchScoring:
    """macos + x86_64 主机必须选中 x86_64 包，而不是 arm64 包。"""

    def _assets(self) -> list[dict]:
        # v1.6.3 线上真实资产名（GitHub releases/latest，2026-11 核实）
        return [
            {"name": "ShangBackground-v1.6.3-windows-x86_64-setup.exe", "size": 1,
             "download_url": "https://github.com/purrfecto114-lgtm/ShangBackground/x.exe"},
            {"name": "ShangBackground-v1.6.3-windows-x86_64.zip", "size": 1,
             "download_url": "https://github.com/purrfecto114-lgtm/ShangBackground/x.zip"},
            {"name": "ShangBackground-v1.6.3-linux-x86_64.tar.gz", "size": 1,
             "download_url": "https://github.com/purrfecto114-lgtm/ShangBackground/x.tar.gz"},
            {"name": "ShangBackground-v1.6.3-macos-x86_64.tar.gz", "size": 1,
             "download_url": "https://github.com/purrfecto114-lgtm/ShangBackground/m1.tar.gz"},
            {"name": "ShangBackground-v1.6.3-macos-arm64.tar.gz", "size": 1,
             "download_url": "https://github.com/purrfecto114-lgtm/ShangBackground/m2.tar.gz"},
        ]

    def test_intel_mac_x86_64_asset_is_selected(self):
        from services import updates

        with patch.object(updates, "_detect_host_arch", return_value="x86_64"):
            compatible = updates.select_compatible_assets(self._assets(), platform_id="macos")
        names = [a["name"] for a in compatible]
        assert any("macos-x86_64" in n for n in names), (
            f"Intel Mac 必须能选中 x86_64 包, got {names}"
        )

    def test_intel_mac_x86_64_asset_outranks_arm64(self):
        from services import updates

        with patch.object(updates, "_detect_host_arch", return_value="x86_64"):
            compatible = updates.select_compatible_assets(self._assets(), platform_id="macos")
        scores = {a["name"]: a["score"] for a in compatible}
        x64 = [s for n, s in scores.items() if "x86_64" in n]
        arm = [s for n, s in scores.items() if "arm64" in n]
        if x64 and arm:
            assert max(x64) > max(arm), f"x86_64 分数必须高于 arm64: {scores}"

    def test_arm_mac_arm64_asset_is_selected(self):
        from services import updates

        with patch.object(updates, "_detect_host_arch", return_value="arm64"):
            compatible = updates.select_compatible_assets(self._assets(), platform_id="macos")
        names = [a["name"] for a in compatible]
        assert any("macos-arm64" in n for n in names), (
            f"Apple Silicon 必须能选中 arm64 包, got {names}"
        )

    def test_platform_markers_do_not_include_arch_words(self):
        """架构词不得同时出现在平台标记与架构标记（避免双计分）。"""
        from services import updates

        arch_words = {"x86_64", "x64", "amd64", "arm64", "aarch64", "x86"}
        for platform, markers in updates.PLATFORM_ASSET_MARKERS.items():
            leaked = arch_words.intersection(markers)
            assert not leaked, (
                f"PLATFORM_ASSET_MARKERS[{platform}] 混入架构标记: {leaked}"
            )


# ---------------------------------------------------------------------------
# 问题 5: 同图模式切换必须落盘 mode
# ---------------------------------------------------------------------------


def _make_mode_service(tmpdir: str, *, start_mode: str, current: str):
    """真实 WallpaperLibrary + WallpaperModeService, 磁盘为临时 JSON。"""
    from app.wallpaper_library import WallpaperLibrary
    from app.wallpaper_mode_service import (
        ModeActivationResult,
        WallpaperModeService,
    )

    config_path = os.path.join(tmpdir, "config.json")
    img = os.path.join(tmpdir, "same.png")
    with open(img, "w") as f:
        f.write("x")
    img_abs = os.path.abspath(img)
    config: dict = {
        "mode": start_mode,
        "current_wallpaper": current,
        "history": [current] if current else [],
        "single_image": img,
        "video_volume": 55,
        "favorites": ["a"],
    }
    persist_calls = {"n": 0}

    def persist() -> bool:
        persist_calls["n"] += 1
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    lock = RLock()
    library = WallpaperLibrary(lambda: config, persist=persist, lock=lock)

    def activate(mode, cfg):
        # 复刻 bootstrap.activate_mode 的"图片"分支（v1.6.4 修复后）:
        # record 返回值被忽略、apply 无条件 True, 但 persisted=False 交给
        # mode service 事务性落盘。
        if mode == "图片" and cfg.get("single_image") and os.path.exists(cfg["single_image"]):
            library.remember_wallpaper(img_abs, update_current=True)
            return ModeActivationResult(True, persisted=False)
        return ModeActivationResult(True, persisted=False)

    svc = WallpaperModeService(
        config=lambda: config,
        persist=persist,
        operation_lock=lock,
        mode_order=["图片", "幻灯片放映", "视频", "HTML"],
        normalize_mode=lambda r: str(r or "").strip(),
        activate=activate,
    )
    return svc, config, config_path, persist_calls


class TestIssue5SameImageSwitchMustPersist:
    def test_same_image_switch_persists_mode_to_disk(self):
        tmpdir = tempfile.mkdtemp(prefix="sb-t-issue5-")
        img = os.path.join(tmpdir, "same.png")
        with open(img, "w") as f:
            f.write("x")
        img_abs = os.path.abspath(img)
        svc, config, config_path, calls = _make_mode_service(
            tmpdir, start_mode="幻灯片放映", current=img_abs
        )
        assert svc.switch("图片") is True
        disk_mode = json.load(open(config_path, encoding="utf-8"))["mode"]
        assert disk_mode == "图片", (
            f"switch 返回 True 但磁盘 mode={disk_mode!r}（内存={config['mode']!r}）"
        )

    def test_same_image_switch_calls_persist(self):
        tmpdir = tempfile.mkdtemp(prefix="sb-t-issue5b-")
        img = os.path.join(tmpdir, "same.png")
        with open(img, "w") as f:
            f.write("x")
        img_abs = os.path.abspath(img)
        svc, config, config_path, calls = _make_mode_service(
            tmpdir, start_mode="幻灯片放映", current=img_abs
        )
        assert svc.switch("图片") is True
        assert calls["n"] >= 1, "同图切换返回成功时必须至少调用一次 persist"

    def test_legacy_persisted_true_claim_is_caught_by_safety_net(self):
        """防御层：即使激活回调错误声明 persisted=True, provider 暴露的
        last_committed_mode 不一致时也必须补写, 而不是直接返回成功。"""
        from app.wallpaper_mode_service import (
            ModeActivationResult,
            WallpaperModeService,
        )

        tmpdir = tempfile.mkdtemp(prefix="sb-t-issue5c-")
        config: dict = {"mode": "图片"}
        config_path = os.path.join(tmpdir, "config.json")
        # 磁盘初值：停在旧模式（模拟"activation 声称已持久化但实际没写"）
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"mode": "图片"}, f, ensure_ascii=False)
        calls = {"n": 0}

        def persist() -> bool:
            calls["n"] += 1
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False)
            return True

        class Provider:
            last_committed_mode = lambda: "幻灯片放映"  # noqa: E731 - 磁盘还停在旧模式

        svc = WallpaperModeService(
            config=lambda: config,
            persist=persist,
            operation_lock=RLock(),
            mode_order=["图片", "幻灯片放映"],
            normalize_mode=lambda r: str(r or "").strip(),
            activate=lambda mode, cfg: ModeActivationResult(True, persisted=True),
        )
        # 手动挂上 provider 钩子（构造函数只接受 config callable）
        # 钩子返回磁盘的真实旧值（图片），与 selected(幻灯片放映) 不一致
        # -> safety net 必须识破 persisted=True 的错误声明并补写
        svc._config_provider = _ProviderWithHook(config, "图片")
        assert svc.switch("幻灯片放映") is True
        disk_mode = json.load(open(config_path, encoding="utf-8"))["mode"]
        assert disk_mode == "幻灯片放映", (
            f"persisted=True 声明被采信但磁盘 mode={disk_mode!r}"
        )


class _ProviderWithHook:
    """config callable + last_committed_mode 钩子（engine facade 形态）。"""

    def __init__(self, config: dict, committed_mode: str) -> None:
        self._config = config
        self._committed = committed_mode

    def __call__(self):
        return self._config

    def last_committed_mode(self):
        return self._committed


# ---------------------------------------------------------------------------
# 问题 3: 失败回滚不得吞掉事务期间的锁外用户写入
# ---------------------------------------------------------------------------


class TestIssue3RollbackMustNotSwallowUserWrites:
    def test_out_of_transaction_writes_survive_rollback(self):
        from app.wallpaper_mode_service import WallpaperModeService

        tmpdir = tempfile.mkdtemp(prefix="sb-t-issue3-")
        config: dict = {
            "mode": "图片",
            "video_volume": 55,
            "favorites": ["a"],
        }
        config_path = os.path.join(tmpdir, "config.json")

        def persist() -> bool:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True

        calls = {"n": 0}

        def activate(mode, cfg):
            calls["n"] += 1
            if calls["n"] == 1:
                # 事务进行中，用户经锁外路径（video_focus_mixin 等）保存设置
                cfg["video_volume"] = 88
                cfg["favorites"] = ["a", "b"]
                cfg["new_user_setting"] = "keep-me"
                raise RuntimeError("backend boom")
            return True

        svc = WallpaperModeService(
            config=lambda: config,
            persist=persist,
            operation_lock=RLock(),
            mode_order=["图片", "幻灯片放映", "视频", "HTML"],
            normalize_mode=lambda r: str(r or "").strip(),
            activate=activate,
        )
        with pytest.raises(Exception):
            svc.switch("视频")

        assert config["video_volume"] == 88, "回滚不得撤销事务期间保存的音量"
        assert config["favorites"] == ["a", "b"], "回滚不得撤销事务期间保存的收藏"
        assert config.get("new_user_setting") == "keep-me", (
            "回滚不得删除事务期间新增的用户设置键"
        )


# ---------------------------------------------------------------------------
# 问题 2: capture 防污染——已拒绝记录后不得把应用自管图片记为原壁纸
# ---------------------------------------------------------------------------


class TestIssue2SnapshotNotPollutedByAppWallpaper:
    def _make_service(self, tmpdir: str):
        from app.runtime_state import SessionWallpaperState
        from app.session_wallpaper_service import SessionWallpaperService

        class FakeBackend:
            """无 capture_state —— 对应 KDE capture_state 失败降级出口。"""

            def __init__(self):
                self.current = ""
                self.set_calls: list[str] = []

            def get_current(self):
                return self.current

            def set_wallpaper(self, path):
                self.set_calls.append(path)

        state_file = os.path.join(tmpdir, "session_state.json")
        backend = FakeBackend()
        config: dict = {"mode": "图片"}

        service = SessionWallpaperService(
            state=SessionWallpaperState(),
            backend=backend,
            config=lambda: config,
            persist_config=lambda: True,
            primary_file=lambda: state_file,
            legacy_files=lambda: (),
            platform_name=lambda: "linux",
            app_base_dir=lambda: tmpdir,
            get_style=lambda: {},
            restore_style=lambda _s: None,
            stop_dynamic=lambda: True,
            refresh_shell=lambda: None,
            operation_lock=RLock(),
        )
        return service, backend, state_file

    def test_app_owned_wallpaper_not_recorded_as_original(self):
        tmpdir = tempfile.mkdtemp(prefix="sb-t-issue2-")
        service, backend, state_file = self._make_service(tmpdir)

        # capture#1: 原壁纸不可知（get_current=''）→ 拒绝记录
        assert service.capture(inherit_existing=False, force_refresh=False) is False

        # 应用改变桌面（壁纸变为应用自管图片）
        app_wall = os.path.join(tmpdir, "app_owned.png")
        with open(app_wall, "w") as f:
            f.write("x")
        backend.current = app_wall

        # capture#2（MediaService.start_video/start_html 触发的生产链）:
        # 修复后必须拒绝把应用自管图片记为"启动前原壁纸"
        result2 = service.capture(inherit_existing=False, force_refresh=False)
        assert result2 is False, "污染窗口内第二次 capture 必须拒绝记录"
        # 拒绝路径下快照文件必须不存在（首捕未落盘, 二捕被守卫拦截）
        assert not os.path.exists(state_file), (
            "拒绝记录时不应留下可能误导恢复逻辑的快照文件"
        )

    def test_restore_after_pollution_window_does_not_apply_app_wallpaper(self):
        tmpdir = tempfile.mkdtemp(prefix="sb-t-issue2b-")
        service, backend, state_file = self._make_service(tmpdir)
        assert service.capture(inherit_existing=False, force_refresh=False) is False

        app_wall = os.path.join(tmpdir, "app_owned.png")
        with open(app_wall, "w") as f:
            f.write("x")
        backend.current = app_wall
        service.capture(inherit_existing=False, force_refresh=False)

        service.restore()
        assert backend.set_calls == [], (
            f"退出恢复不应恢复到应用自管图片, got {backend.set_calls}"
        )


# ---------------------------------------------------------------------------
# 问题 4: IPC 无应答不得阻塞调用线程整个 timeout（同步链必须可中断/降超时）
# ---------------------------------------------------------------------------


class TestIssue4IpcTimeoutBudget:
    def test_runtime_volume_ipc_timeout_is_capped(self):
        """运行时音量 IPC 的默认超时必须显著低于 3.0s（UI 阻塞预算）。"""
        from platform_adapters.backends.linux import video as linux_video

        # 修复目标: 运行时音量链的超时预算被降到 ~0.5s 量级
        assert linux_video._RUNTIME_IPC_TIMEOUT <= 0.8, (
            f"运行时音量 IPC 超时 {linux_video._RUNTIME_IPC_TIMEOUT} 过长, 主线程可被阻塞 3s+"
        )

    def test_mpv_ipc_client_dead_after_timeout_speeds_up_second_call(self):
        """同一条连接上, 一次应答超时后, 后续请求不得再次等满整个 timeout。

        当前实现: 超时不置 dead, set_video_volume 的两次 set_property
        (volume+mute) 各自等满 3s。修复后第二次必须快速失败。
        """
        import socket as _socket

        from platform_adapters.mpv_ipc import MpvIpcClient

        sock_dir = tempfile.mkdtemp(prefix="sb-t-issue4-")
        sock_path = os.path.join(sock_dir, "ipc.sock")
        srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)

        server_done = []

        import threading

        def dead_server():
            conn, _ = srv.accept()
            conn.settimeout(0.5)
            try:
                while True:
                    data = conn.recv(65536)
                    if not data:
                        break
                    # 收下但永不回应（mpv 挂起语义）。
                    # 注意 socket.timeout 是 OSError 子类, 必须先捕获并 continue,
                    # 服务器保持存活才能复现"通道在但无应答"。
            except TimeoutError:
                pass  # pragma: no cover - loop handles timeouts
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
            server_done.append(True)

        # recv 超时必须 continue 而非 break：改用显式循环
        def dead_server_keepalive():
            conn, _ = srv.accept()
            conn.settimeout(0.5)
            alive = True
            while alive:
                try:
                    data = conn.recv(65536)
                    if not data:
                        alive = False
                except TimeoutError:
                    continue
                except OSError:
                    alive = False
            try:
                conn.close()
            except OSError:
                pass

        t = threading.Thread(target=dead_server_keepalive, daemon=True)
        t.start()

        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(0.25)
        sock.connect(sock_path)

        def read_bytes(timeout):
            sock.settimeout(max(0.01, float(timeout)))
            try:
                chunk = sock.recv(65536)
            except _socket.timeout:
                return None
            return chunk if chunk else b""

        client = MpvIpcClient(
            write_bytes=lambda p: (sock.sendall(p) or len(p)),
            read_bytes=read_bytes,
            close=sock.close,
        )
        try:
            budget = 3.0
            t0 = time.perf_counter()
            ok1, _ = client.request(["set_property", "volume", 50], timeout=budget)
            first = time.perf_counter() - t0
            t0 = time.perf_counter()
            ok2, _ = client.request(["set_property", "mute", False], timeout=budget)
            second = time.perf_counter() - t0
            total = first + second
            assert total < budget * 1.5, (
                f"两次 set_property 总阻塞 {total:.2f}s ≈ 双倍超时; "
                "第一次超时后连接必须判死, 第二次快速失败"
            )
        finally:
            try:
                sock.close()
            except OSError:
                pass
            srv.close()
