# Refactor Patches

补丁可按顺序应用，也可独立查看动机与回滚边界。

## 0001 — unified mpv backend contract

- 动机：通用 `MediaBackend` 不表达 pause/property observe/raw IPC。
- 文件：`src/app/mpv_backend.py`、`src/app/bootstrap.py`、专项测试。
- 改动：新增 ABC 与 legacy module adapter；现有平台实现不搬迁。
- 回滚：撤销 0001，`ModuleMediaBackend` 回到直接调用模块函数。
- 性能/UX：仅一次 Python adapter 调用；无可感知开销；错误结果统一。

## 0002 — deterministic JSON i18n and bundle resources

- 动机：语言资源加载与可见 UI 刷新缺少共享通知，错误 gzip 后缀应在构建期失败。
- 文件：`src/app/i18n.py`、`src/ui/main_window.py`、`build_tools/buildlib/bundle.py`、测试。
- 改动：语言事件订阅；复用现有 UI 重建；gzip magic 兼容旧包；freeze 前断言；64 组合 feature 测试。
- 回滚：撤销 0002；不会改变翻译文件格式，但会失去即时通知与构建期保护。
- 性能/UX：语言切换为低频同步重建；正常翻译查找路径不增加额外工作。

## 0003 — KDE Wayland video and portal hotkeys

- 动机：KDE Wayland 被错误排除在 layer-shell 探测外，热键只支持 X11。
- 文件：Linux capabilities/video/hotkeys、`portal_hotkeys.py`、requirements、测试。
- 改动：KDE/Plasma 可尝试 mpvpaper；Wayland 热键通过 XDG GlobalShortcuts；X11 路径不变。
- 回滚：撤销 0003 即恢复 KDE Wayland unsupported 与 X11-only 热键。
- 性能/UX：Portal 在 daemon asyncio 线程运行；首次绑定由 compositor 展示用户授权 UI。
