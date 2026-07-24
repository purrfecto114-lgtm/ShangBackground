# 阶段 1：现状盘点

本阶段目标：以源码为准识别真实架构边界，先纠正提示词中与项目现状不一致的部分，再决定最小改动面。

## 1. 关键事实校正

1. 项目已经有通用 `app.ports.MediaBackend`，但它只覆盖媒体类型级别的 `start/stop/is_running/set_option`；缺少 mpv 属性观察、原始 IPC 与统一暂停语义，因此新增的是**专用控制契约**而非重写整个媒体层。
2. `src/ui/main_window.py` 已存在 `_rebuild_ui_for_language_change()`。真实缺口是 `app.i18n` 没有可订阅的语言状态通知，导致其他可见窗口无法共享一致的刷新入口；不是“完全没有 UI 重渲代码”。
3. macOS 视频壁纸走 AppKit/AVPlayer 原生路径，并非 mpv。统一契约应允许非 mpv 原生播放器通过 legacy adapter 保持兼容，而不强迫 macOS 引入 libmpv。
4. KDE 静态壁纸已通过 `plasma-apply-wallpaperimage` 与 Plasma D-Bus 脚本支持，本次没有重写。

## 2. 三端视频实现差异

| 平台/会话 | 播放实现 | IPC/控制 | 生命周期所有者 | 错误传播与降级 |
|---|---|---|---|---|
| Windows | WorkerW 桌面嵌入；优先内部 ctypes/libmpv，保留外部 mpv/VLC 兼容路径 | libmpv 调用或 Windows named-pipe JSON IPC | 现有 Windows video 模块与 WorkerW 句柄 | 返回 `(ok, message)`；本次只经 `LegacyModuleMpvBackend` 归一化，不改 WorkerW |
| Linux X11 | `xwinwrap +` 内部 libmpv；失败后外部 `mpv --wid=WID` | Unix socket 上的 mpv JSON IPC；音量/暂停热更新 | Linux video 模块持有子进程与 IPC 文件 | 缺 xwinwrap/libmpv/mpv 时给可操作错误；保持原回退顺序 |
| Linux Wayland | `mpvpaper` 作为 layer-shell 客户端；本次将 KDE/Plasma 纳入可尝试会话 | mpvpaper 透传 `input-ipc-server` Unix socket | Linux video 模块持有 mpvpaper 进程 | 仅在 compositor/session 适配且存在 mpvpaper 时运行；否则明确降级到 X11/安装指引 |
| macOS | AppKit/AVPlayer 原生子窗口/桌面层 | 原生控制通道；不是 mpv JSON IPC | macOS video 模块 | 保持原生实现；统一 adapter 对缺失的 mpv 特有操作返回 `False` |

> 结论：统一的是调用契约和能力边界，不应把所有平台强行改造成同一种播放器实现。

## 3. 六个 build feature 与产物映射

| Feature | 动态模块/资源 | 额外依赖或原生运行时 | 禁用时效果 |
|---|---|---|---|
| `video` | `app.mpv_backend`、平台 video、`platform_adapters.video`；非 macOS 含 `app.libmpv_runtime` | `requirements/<target>-video.txt`；可选 `src/bin/<platform>/` 原生 libmpv bundle | 排除 video 与 libmpv 模块 |
| `html` | 平台 HTML、native runner、pywebview 原生后端链 | Linux 需 GTK3/WebKitGTK typelib；Windows/macOS 用原生 WebView | 排除 pywebview；始终排除 QtWebEngine/QML/Quick |
| `bing` | `services.bing`、`services.bing_sync` | 网络访问 | 排除 Bing 服务 |
| `hotkeys` | 平台 hotkeys、共享 binding parser；Linux 新增 portal backend | X11/macOS 用 `pynput`；Linux Wayland 用 `dbus-next` + portal | 排除所有热键模块及 Linux portal 依赖 |
| `updates` | `services.updates` | 网络访问/版本元数据 | 排除更新服务 |
| `fonts` | 根目录 `fonts/` 数据目录（存在时） | 字体文件 | 不复制字体目录 |

新增 `tests/test_build_feature_matrix.py` 穷举 64 个组合，验证 manifest 与包含/排除模块不冲突。

## 4. Linux 桌面能力矩阵

图例：✅ 已支持；🟡 最佳努力/需外部组件；❌ 当前不支持。

| 桌面 | 静态 X11 | 静态 Wayland | 视频 X11 | 视频 Wayland | 全局热键 X11 | 全局热键 Wayland |
|---|---:|---:|---:|---:|---:|---:|
| KDE Plasma | ✅ Plasma CLI/D-Bus | ✅ Plasma CLI/D-Bus | 🟡 xwinwrap + libmpv/mpv | 🟡 KWin layer-shell + mpvpaper | 🟡 pynput + 前台保护 | 🟡 XDG GlobalShortcuts Portal，需用户同意与后端 |
| GNOME | ✅ GSettings | ✅ GSettings | 🟡 xwinwrap + libmpv/mpv | ❌ 当前无 GNOME Shell 桌面层插件 | 🟡 pynput + 前台保护 | 🟡 Portal API 可用性取决于桌面/发行版后端 |
| XFCE | ✅ xfconf-query | 通常 N/A/实验性 | 🟡 xwinwrap + libmpv/mpv | ❌ 未实现通用 Wayland 层 | 🟡 pynput + 前台保护 | 🟡 取决于实际 Wayland compositor 与 portal backend |
| wlroots 系 | 桌面命令依发行版 | 桌面命令依 compositor | 🟡 | 🟡 mpvpaper | 🟡 | 🟡 Portal 后端存在时 |

## 5. JSON i18n 路径

| 环节 | 当前/修复后行为 | 边界 |
|---|---|---|
| 启动 | `init_i18n(config)` → `load_language(lang)` | 中文是默认键值，`en.json` 为 zh→en 字典 |
| Qt 自身翻译 | `support.py` 中 QLocale/QTranslator 仅服务 Qt 内置菜单 | 不参与项目 UI 文案，不得替换 `t()` |
| 资源读取 | UTF-8 JSON；遇 gzip magic byte 时恢复旧错误包 | 新构建在 freeze 前拒绝 gzip 内容继续使用 `.json` 后缀 |
| 状态切换 | `load_language` 更新 `_CURRENT_LANG/_TRANSLATIONS` 并发出 `LanguageChangeEvent` | `t(key, default=None)` 签名不变 |
| UI 重渲 | 主窗口订阅事件，复用既有 `_rebuild_ui_for_language_change()` | 事件在调用线程同步派发；UI 应从 GUI 线程切换语言 |
| 失败降级 | 字典为空，`t()` 回退中文键/`default`，界面显示资源加载失败状态 | 不因单个监听器异常使应用崩溃 |

本阶段产出：真实架构边界、能力矩阵、feature 映射和 i18n 数据流已固定，可由源码与新增测试复现。
