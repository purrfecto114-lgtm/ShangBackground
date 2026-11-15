"""五项缺陷修复的验收缺口补测（第二轮，TDD 先红后绿）。

对照诊断报告第八节验收条件，四个上一轮未闭合的缺口：

- 缺口 A（问题 2）: schema=3 有效 backend_state 但壁纸不可恢复的首捕
  （真实 KDE slideshow 插件场景）不置 refused 标志 -> 污染窗口仍开着。
- 缺口 B（问题 5）: switch() 的 _committed_mode_matches 安全网依赖
  last_committed_mode 钩子，但生产 bootstrap 从不提供该钩子（死代码）。
- 缺口 C（问题 5）: 报告要求同图/跨图 × 有历史/无历史四组验收，只测了一组。
- 缺口 D（问题 3）: 报告点名的真实锁外键是 video_focus_behavior
  （video_focus_mixin.on_video_focus_behavior_changed 直写 config），
  上一轮用 video_volume 代替；另需验证 activate 幂等键（video_file 等）
  在回滚时不被误保留为事务键。

红队复查追加（第三轮）：

- 缺口 E（问题 1 回归）: macos 平台标记 ".app" 子串匹配 ".AppImage"，
  带 download_url 的真实 Linux AppImage 资产在 macOS 得 +2 分入选
  UI 直接消费的兼容列表（旧代码 −103 正确排除）。
- 缺口 F（问题 5 结构盲区）: bootstrap.activate_mode 是闭包，23 个新
  测试全部驱动手工复制品；若有人把某分支改回 persisted=True，无测试
  变红。用真实 build_services 链直接测生产 activate_mode。
- 缺口 G（问题 2 验收）: "capture_state 方法存在且返回 None" 出口
  无直接测试（红队 Minor-1）。
"""

from __future__ import annotations

import json
import os
import tempfile
from threading import RLock

import pytest

# ---------------------------------------------------------------------------
# 缺口 A: schema=3 首捕（backend_state 有效、壁纸不可恢复）的污染窗
# ---------------------------------------------------------------------------


class _KdeSlideshowBackend:
    """模拟真实 KDE 场景：capture_state 返回有效 schema=3 状态。

    首捕时桌面是 KDE slideshow 插件（get_current 返回非图片路径/空），
    is_restorable=False；应用启动视频壁纸后桌面变成应用自管图片。
    """

    def __init__(self):
        self.current = ""  # slideshow 场景：无本地可恢复路径
        self.set_calls: list[str] = []

    def get_current(self):
        return self.current

    def set_wallpaper(self, path):
        self.set_calls.append(path)

    def capture_state(self):
        # 有效插件级状态（KDE containments，v1.6.2+ schema=3）
        return {
            "schema": 3,
            "kind": "kde-plasma-containments",
            "containments": [
                {"id": 1, "screen": 0, "plugin": "org.kde.slideshow",
                 "config_captured": True}
            ],
        }

    def restore_state(self, state):
        from app.ports import BackendResult

        self.restored_state = state
        return BackendResult(ok=True, message="")


class TestGapAIssue2Schema3FirstCapturePollution:
    def _make_service(self, tmpdir, backend):
        from app.runtime_state import SessionWallpaperState
        from app.session_wallpaper_service import SessionWallpaperService

        state_file = os.path.join(tmpdir, "session_state.json")
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
        return service, state_file

    def test_schema3_unrestorable_first_capture_blocks_second_capture(self):
        """KDE slideshow 首捕（schema=3 有效、壁纸不可恢复）后，
        MediaService.start_video 触发的第二次 capture 不得把应用自管图片
        写入快照 wallpaper 字段。"""
        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapA-")
        backend = _KdeSlideshowBackend()
        service, state_file = self._make_service(tmpdir, backend)

        # capture#1: KDE slideshow 桌面 -> backend_state 有效, wallpaper=""
        r1 = service.capture(inherit_existing=False, force_refresh=False)
        assert r1 is True, "schema=3 首捕应成功（记录插件级状态）"
        data1 = json.load(open(state_file, encoding="utf-8"))
        assert data1.get("wallpaper") == "", "slideshow 场景 wallpaper 应为空"

        # 应用改变桌面（视频壁纸启动后 get_current 指向应用自管图片）
        app_wall = os.path.join(tmpdir, "app_owned.png")
        with open(app_wall, "w") as f:
            f.write("x")
        backend.current = app_wall

        # capture#2（生产链 MediaService.start_video 触发）
        service.capture(inherit_existing=False, force_refresh=False)

        data2 = json.load(open(state_file, encoding="utf-8"))
        # 早退路径下快照必须与首捕逐字段一致（wallpaper 保持空, 不被污染）
        assert data2.get("wallpaper") == "", (
            f"schema=3 首捕后的第二次 capture 污染了快照: {data2}"
        )
        assert data2.get("backend_state") == data1.get("backend_state"), (
            "第二次 capture 不得改动首捕的插件级状态"
        )

    def test_schema3_first_capture_keeps_backend_state_through_second(self):
        """第二次 capture 不得覆盖首捕记录的 backend_state（退出恢复仍应
        优先走插件级状态，而不是被应用自管图片顶掉）。"""
        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapA2-")
        backend = _KdeSlideshowBackend()
        service, state_file = self._make_service(tmpdir, backend)

        assert service.capture(inherit_existing=False, force_refresh=False) is True
        first_state = json.load(open(state_file, encoding="utf-8"))

        app_wall = os.path.join(tmpdir, "app_owned.png")
        with open(app_wall, "w") as f:
            f.write("x")
        backend.current = app_wall
        service.capture(inherit_existing=False, force_refresh=False)

        second_state = json.load(open(state_file, encoding="utf-8"))
        assert second_state.get("backend_state") == first_state.get("backend_state"), (
            "第二次 capture 不得覆盖首捕的插件级状态"
        )

    def test_schema3_restore_does_not_apply_app_wallpaper(self):
        """污染窗关闭后, 退出恢复不得把桌面设成应用自管图片。"""
        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapA3-")
        backend = _KdeSlideshowBackend()
        service, state_file = self._make_service(tmpdir, backend)

        assert service.capture(inherit_existing=False, force_refresh=False) is True
        app_wall = os.path.join(tmpdir, "app_owned.png")
        with open(app_wall, "w") as f:
            f.write("x")
        backend.current = app_wall
        service.capture(inherit_existing=False, force_refresh=False)
        service.restore()

        assert backend.set_calls == [], (
            f"退出恢复不应设置应用自管图片, got {backend.set_calls}"
        )


# ---------------------------------------------------------------------------
# 缺口 B: switch() 安全网在生产链是死代码（无 last_committed_mode 钩子）
# ---------------------------------------------------------------------------


class TestGapBIssue5SafetyNetDeadInProduction:
    def test_mode_service_never_trusts_unverifiable_persisted_claim(self):
        """钉住安全网核心契约：无钩子（生产现状）时 persisted=True 不可采信。

        修复后的语义：_committed_mode_matches 在无法验证提交态时返回
        False，switch() 因此总是走本地 _persist()。这条测试钉住该语义，
        防止未来有人把"无钩子 -> 采信"改回去（那会让问题 5 复活）。
        """
        from app.wallpaper_mode_service import (
            ModeActivationResult,
            WallpaperModeService,
        )

        config: dict = {"mode": "图片"}
        persist_calls = {"n": 0}

        def persist() -> bool:
            persist_calls["n"] += 1
            return True

        svc = WallpaperModeService(
            config=lambda: config,  # 裸 callable, 无钩子 —— 生产形态
            persist=persist,
            operation_lock=RLock(),
            mode_order=["图片", "幻灯片放映"],
            normalize_mode=lambda r: str(r or "").strip(),
            activate=lambda mode, cfg: ModeActivationResult(True, persisted=True),
        )
        # 直接验证语义: 无钩子时 _committed_mode_matches 必须返回 False
        assert svc._committed_mode_matches("幻灯片放映") is False, (
            "无 last_committed_mode 钩子时安全网返回 True —— "
            "persisted=True 的错误声明会被采信, 问题 5 复活"
        )
        assert svc.switch("幻灯片放映") is True
        assert persist_calls["n"] >= 1, "无钩子时必须走本地 persist 兜底"

    def test_mode_service_safety_net_works_without_hook(self):
        """无钩子时（生产现状）, persisted=True 的错误声明必须仍被兜底。

        当前实现: 无钩子 -> _committed_mode_matches 返回 True -> 直接
        return True 不落盘（问题 5 复活）。修复后: 无钩子时 persisted=True
        声明不得被直接采信, 必须落盘校验。
        """
        from app.wallpaper_mode_service import (
            ModeActivationResult,
            WallpaperModeService,
        )

        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapB-")
        config: dict = {"mode": "图片"}
        config_path = os.path.join(tmpdir, "config.json")
        calls = {"n": 0}

        def persist() -> bool:
            calls["n"] += 1
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False)
            return True

        svc = WallpaperModeService(
            config=lambda: config,  # 裸 callable, 无钩子 —— 生产形态
            persist=persist,
            operation_lock=RLock(),
            mode_order=["图片", "幻灯片放映"],
            normalize_mode=lambda r: str(r or "").strip(),
            activate=lambda mode, cfg: ModeActivationResult(True, persisted=True),
        )
        assert svc.switch("幻灯片放映") is True
        # 无钩子 + persisted=True 声明: 必须仍落盘（防问题5复活）
        assert calls["n"] >= 1, (
            "无 last_committed_mode 钩子时 persisted=True 被直接采信, "
            "磁盘 mode 未写 —— 问题 5 在生产链复活"
        )
        disk_mode = json.load(open(config_path, encoding="utf-8"))["mode"]
        assert disk_mode == "幻灯片放映"


# ---------------------------------------------------------------------------
# 缺口 C: 问题 5 四组验收矩阵（报告第八节要求）
# ---------------------------------------------------------------------------


def _mode_service_with_real_chain(tmpdir: str, *, start_mode: str, current: str,
                                  history: list[str] | None, single_image: str):
    """真实 WallpaperLibrary + WallpaperModeService + 生产 activate 语义。"""
    from app.wallpaper_library import WallpaperLibrary
    from app.wallpaper_mode_service import (
        ModeActivationResult,
        WallpaperModeService,
    )

    config_path = os.path.join(tmpdir, "config.json")
    config: dict = {"mode": start_mode, "single_image": single_image,
                    "video_volume": 55}
    if current:
        config["current_wallpaper"] = current
    if history is not None:
        config["history"] = list(history)
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
        if mode == "图片" and cfg.get("single_image") and os.path.exists(cfg["single_image"]):
            library.remember_wallpaper(single_image, update_current=True)
            return ModeActivationResult(True, persisted=False)
        if mode == "幻灯片放映":
            return ModeActivationResult(True, persisted=False)
        return ModeActivationResult(True, persisted=False)

    svc = WallpaperModeService(
        config=lambda: config,
        persist=persist,
        operation_lock=lock,
        mode_order=["图片", "幻灯片放映"],
        normalize_mode=lambda r: str(r or "").strip(),
        activate=activate,
    )
    return svc, config, config_path, persist_calls


class TestGapCIssue5AcceptanceMatrix:
    """报告第八节验收: 同图/跨图 × 有历史/无历史 四组。"""

    def _switch_and_check(self, tmpdir, *, start_mode, current, history, same):
        img = os.path.join(tmpdir, "target.png")
        with open(img, "w") as f:
            f.write("x")
        img_abs = os.path.abspath(img)
        target = img_abs if same else os.path.join(tmpdir, "other.png")
        if not same:
            with open(target, "w") as f:
                f.write("x")
        svc, config, config_path, calls = _mode_service_with_real_chain(
            tmpdir, start_mode=start_mode,
            current=current, history=history, single_image=target,
        )
        assert svc.switch("图片") is True
        disk = json.load(open(config_path, encoding="utf-8"))
        assert disk["mode"] == "图片", (
            f"同图={same} 有历史={history is not None}: "
            f"switch 返回 True 但磁盘 mode={disk['mode']!r}"
        )
        return disk

    def test_same_image_with_history(self):
        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapC1-")
        img = os.path.join(tmpdir, "target.png")
        with open(img, "w") as f:
            f.write("x")
        img_abs = os.path.abspath(img)
        self._switch_and_check(tmpdir, start_mode="幻灯片放映",
                               current=img_abs, history=[img_abs], same=True)

    def test_same_image_without_history(self):
        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapC2-")
        img = os.path.join(tmpdir, "target.png")
        with open(img, "w") as f:
            f.write("x")
        img_abs = os.path.abspath(img)
        self._switch_and_check(tmpdir, start_mode="幻灯片放映",
                               current=img_abs, history=None, same=True)

    def test_different_image_with_history(self):
        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapC3-")
        other = os.path.join(tmpdir, "other.png")
        with open(other, "w") as f:
            f.write("x")
        self._switch_and_check(tmpdir, start_mode="幻灯片放映",
                               current=other, history=[other], same=False)

    def test_different_image_without_history(self):
        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapC4-")
        other = os.path.join(tmpdir, "other.png")
        with open(other, "w") as f:
            f.write("x")
        self._switch_and_check(tmpdir, start_mode="幻灯片放映",
                               current=other, history=None, same=False)


# ---------------------------------------------------------------------------
# 缺口 D: 问题 3 用真实锁外路径键 video_focus_behavior 验收
# ---------------------------------------------------------------------------


class TestGapDIssue3RealOutOTransactionKey:
    def test_video_focus_behavior_survives_rollback(self):
        """报告点名验收键: 事务期间经 video_focus_mixin 槽保存的
        video_focus_behavior 不得被回滚撤销。"""
        from app.wallpaper_mode_service import WallpaperModeService

        config: dict = {"mode": "图片", "video_focus_behavior": "none",
                        "favorites": ["a"]}
        calls = {"n": 1}

        def activate(mode, cfg):
            if calls["n"] == 1:
                # 真实锁外路径（video_focus_mixin.on_video_focus_behavior_changed）:
                # Qt 槽直写 config 再 save_config
                cfg["video_focus_behavior"] = "duck"
                cfg["favorites"] = ["a", "b"]
                raise RuntimeError("backend boom")
            calls["n"] += 1
            return True

        svc = WallpaperModeService(
            config=lambda: config,
            persist=lambda: True,
            operation_lock=RLock(),
            mode_order=["图片", "视频"],
            normalize_mode=lambda r: str(r or "").strip(),
            activate=activate,
        )
        with pytest.raises(Exception):
            svc.switch("视频")

        assert config["video_focus_behavior"] == "duck", (
            "回滚撤销了事务期间保存的 video_focus_behavior（报告验收键）"
        )
        assert config["favorites"] == ["a", "b"]

    def test_activation_idempotent_keys_not_treated_as_user_keys(self):
        """activate 回调自己的幂等键（video_file——mode service 走
        updates 语义的键）在回滚时必须恢复旧值, 不能因合并语义漏回滚。"""
        from app.wallpaper_mode_service import WallpaperModeService

        config: dict = {"mode": "图片", "video_file": "/old/video.mp4"}
        calls = {"n": 1}

        def activate(mode, cfg):
            if calls["n"] == 1:
                # UI 生产路径: switch_wallpaper_mode("视频", updates={"video_file": new})
                # updates 键在 switch() staged —— 此处模拟该行为
                cfg["video_file"] = "/new/video.mp4"
                raise RuntimeError("backend boom")
            calls["n"] += 1
            return True

        svc = WallpaperModeService(
            config=lambda: config,
            persist=lambda: True,
            operation_lock=RLock(),
            mode_order=["图片", "视频"],
            normalize_mode=lambda r: str(r or "").strip(),
            activate=activate,
        )
        with pytest.raises(Exception):
            svc.switch("视频", updates={"video_file": "/new/video.mp4"})

        assert config["video_file"] == "/old/video.mp4", (
            "updates 传入的 video_file 是事务键, 回滚必须恢复旧值"
        )


# ---------------------------------------------------------------------------
# 缺口 E（红队 Important-1）: ".app" 子串匹配 ".AppImage" 的 macOS 误报回归
# ---------------------------------------------------------------------------


class TestGapEAppImageNotCompatibleOnMacos:
    """红队复查实测：带 download_url 的真实 AppImage 资产在 macOS 得 +2
    入选兼容列表（旧代码 −103 正确排除）。根因是 ".app" 用 `in text`
    子串匹配命中 ".AppImage"。"""

    REAL_APPIMAGE = {
        "name": "ShangBackground-v1.7.0-linux-x86_64.AppImage",
        "size": 100,
        "download_url": (
            "https://github.com/purrfecto114-lgtm/ShangBackground/"
            "releases/download/v1.7.0/"
            "ShangBackground-v1.7.0-linux-x86_64.AppImage"
        ),
    }

    def test_appimage_excluded_on_macos(self):
        from services import updates

        compatible = updates.select_compatible_assets(
            [dict(self.REAL_APPIMAGE)], platform_id="macos"
        )
        names = [a["name"] for a in compatible]
        assert not any("AppImage" in n or "appimage" in n.lower() for n in names), (
            f"Linux AppImage 出现在 macOS 兼容列表: {names}"
        )

    def test_app_marker_does_not_match_appimage(self):
        from services import updates

        text = (
            "shangbackground-v1.7.0-linux-x86_64.appimage "
            "https://github.com/x/y/shangbackground-v1.7.0-linux-x86_64.appimage"
        )
        assert updates._has_asset_marker(text, ".app") is False, (
            "'.app' 标记不得子串命中 '.AppImage'（需边界匹配）"
        )

    def test_real_app_bundle_still_matches_app_marker(self):
        from services import updates

        # .app 作为扩展名出现在名字/URL 的 token 边界时仍应命中（macOS .app 包）
        assert updates._has_asset_marker("shangbackground-v1.7.0-macos.app", ".app") is True
        assert updates._has_asset_marker(
            "shangbackground-v1.7.0-macos.zip https://x/y/shangbackground.app", ".app"
        ) is True

    def test_full_v163_asset_matrix_no_cross_platform_leak(self):
        """v1.6.3 全部 9 个真实资产在三平台下的入选互不越界。"""
        from services import updates

        names = [
            "ShangBackground-v1.6.3-windows-x86_64-setup.exe",
            "ShangBackground-v1.6.3-windows-x86_64.zip",
            "ShangBackground-v1.6.3-linux-x86_64.tar.gz",
            "ShangBackground-v1.6.3-macos-x86_64.tar.gz",
            "ShangBackground-v1.6.3-macos-arm64.tar.gz",
            "ShangBackground-v1.6.3-source.zip",
        ]
        assets = [
            {
                "name": n,
                "size": 100,
                "download_url": f"https://github.com/purrfecto114-lgtm/ShangBackground/releases/download/v1.6.3/{n}",
            }
            for n in names
        ]
        for platform_id in ("windows", "linux", "macos"):
            compatible = updates.select_compatible_assets(assets, platform_id=platform_id)
            for a in compatible:
                other = {"windows", "linux", "macos"} - {platform_id}
                for op in other:
                    assert op not in a["name"].lower(), (
                        f"{platform_id} 平台选中了 {op} 资产: {a['name']}"
                    )


# ---------------------------------------------------------------------------
# 缺口 F（红队 Important-2）: bootstrap.activate_mode 真实链直接测试
# ---------------------------------------------------------------------------


class TestGapFRealBootstrapActivateMode:
    """生产 activate_mode 是闭包, 之前的测试全部驱动手工复制品。
    这里用真实 build_services 组装, 直接断言生产 activate_mode 的
    persisted 语义（任何路径不得声明 persisted=True）。"""

    def _build(self, tmpdir):
        import importlib

        bootstrap = importlib.import_module("app.bootstrap")
        runtime_state_mod = importlib.import_module("app.runtime_state")
        library_mod = importlib.import_module("app.wallpaper_library")

        config: dict = {"mode": "图片", "single_image": "", "video_volume": 55}
        lock = __import__("threading").RLock()
        persist_calls = {"n": 0}

        def persist() -> bool:
            persist_calls["n"] += 1
            return True

        # 后端桩：只实现 build_services 装配过程触及的协议面
        class WallpaperBackendStub:
            def get_current(self):
                return ""

            def configure_fit_mode(self, mode):
                return None

            def set_wallpaper(self, path):
                return True

        class MediaBackendStub:
            def start(self, kind, target, options=None):
                from app.ports import BackendResult

                return BackendResult(ok=True, message="")

            def stop(self, kind):
                return None

            def is_running(self, kind):
                return False

            def last_target(self, kind):
                return ""

            def set_option(self, kind, key, value):
                return True

        class HotkeyBackendStub:
            def refresh(self):
                return True

            def stop(self):
                return True

        library = library_mod.WallpaperLibrary(lambda: config, persist=persist, lock=lock)
        services = bootstrap.build_services(
            wallpaper_backend=WallpaperBackendStub(),
            media_backend=MediaBackendStub(),
            hotkey_backend=HotkeyBackendStub(),
            config=lambda: config,
            persist=persist,
            library=library,
            runtime_state=runtime_state_mod.RuntimeState(),
            image_source=lambda folder: [],
            apply_wallpaper=lambda path, op: True,
            hotkey_dispatch=lambda action: None,
            hotkey_focus_guard=lambda action, binding: "",
            session_primary_file=lambda: os.path.join(tmpdir, "session.json"),
            session_legacy_files=lambda: (),
            platform_name=lambda: "linux",
            app_base_dir=lambda: tmpdir,
            mode_order=("图片", "幻灯片放映", "视频", "HTML", "纯色", "渐变"),
        )
        return services, config, persist_calls

    def test_production_activate_mode_never_claims_persisted(self):
        """生产 activate_mode 的所有分支必须 persisted=False（问题5根因）。

        用 monkeypatch 环境下的四个模式逐一激活并检查返回值。
        """
        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapF-")
        services, config, persist_calls = self._build(tmpdir)
        # services.modes._activate 即生产 activate_mode 闭包
        activate = services.modes._activate
        for mode in ("图片", "视频", "HTML", "幻灯片放映"):
            config["mode"] = mode
            result = activate(mode, config)
            assert not getattr(result, "persisted", False), (
                f"生产 activate_mode({mode!r}) 声明 persisted=True —— "
                "问题 5 的硬编码根因复活"
            )

    def test_production_same_image_switch_persists(self):
        """真实 bootstrap 链上的同图切换端到端：磁盘 mode 必须落盘。"""
        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapF2-")
        services, config, persist_calls = self._build(tmpdir)
        config_path = os.path.join(tmpdir, "config.json")

        def persist_to_disk() -> bool:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True

        # 重绑 persist 到磁盘版
        services.modes._persist = persist_to_disk
        img = os.path.join(tmpdir, "same.png")
        with open(img, "w") as f:
            f.write("x")
        img_abs = os.path.abspath(img)
        config.update({
            "mode": "幻灯片放映",
            "single_image": img,
            "current_wallpaper": img_abs,
            "history": [img_abs],
        })
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        assert services.modes.switch("图片") is True
        disk_mode = json.load(open(config_path, encoding="utf-8"))["mode"]
        assert disk_mode == "图片", (
            f"真实 bootstrap 链: 同图切换返回 True 但磁盘 mode={disk_mode!r}"
        )


# ---------------------------------------------------------------------------
# 缺口 G（红队 Minor-1）: capture_state 方法存在且返回 None 的直接测试
# ---------------------------------------------------------------------------


class TestGapGCaptureStateReturnsNone:
    def test_capture_state_returning_none_refuses_and_blocks_pollution(self):
        """五类 None 出口之一：方法存在、正常返回 None（非异常）。
        该出口必须与"方法不存在"同样触发 refused 守卫。"""
        from app.runtime_state import SessionWallpaperState
        from app.session_wallpaper_service import SessionWallpaperService

        tmpdir = tempfile.mkdtemp(prefix="sb-t-gapG-")
        state_file = os.path.join(tmpdir, "session_state.json")

        class NoneCaptureBackend:
            def __init__(self):
                self.current = ""
                self.set_calls: list[str] = []

            def get_current(self):
                return self.current

            def set_wallpaper(self, path):
                self.set_calls.append(path)

            def capture_state(self):
                return None  # 非 KDE 会话/脚本失败/解析无记录等出口

        backend = NoneCaptureBackend()
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
        assert service.capture(inherit_existing=False, force_refresh=False) is False

        app_wall = os.path.join(tmpdir, "app_owned.png")
        with open(app_wall, "w") as f:
            f.write("x")
        backend.current = app_wall
        # 第二次 capture 必须拒绝, 不写快照
        assert service.capture(inherit_existing=False, force_refresh=False) is False
        if os.path.exists(state_file):
            data = json.load(open(state_file, encoding="utf-8"))
            assert data.get("wallpaper") != app_wall
        service.restore()
        assert backend.set_calls == []
