# 应用架构

## 依赖方向

```text
Qt Widgets Presentation
        ↓
Application Services / Policies / Ports
        ↓
Repositories / network / IPC / platform backends
        ↓
platform_adapters/backends/{windows,linux,macos}
```

`app.bootstrap` 组装平台实现。共享层不得直接导入其他平台 Backend。

## 核心边界

- Repository 负责配置、历史、收藏和原子持久化；
- `RuntimeState` 持有进程、计时器、取消状态和共享壁纸操作锁；
- `WallpaperService`、`SlideshowService`、`MediaService`、`HotkeyService` 等承载行为；
- `core.engine` 是迁移兼容门面，新业务不继续堆入其中。

## 平台层

原生 API、系统命令和权限语义只存在于匹配平台目录。不支持的能力返回结构化失败，不伪装成功。Windows/Linux/macOS 产物必须在目标系统构建；异平台只允许 dry-run 检查命令。

## Linux 多显示器静态壁纸（按输出设置）

Linux/KDE 静态壁纸有两条路径（`platform_adapters.backends.linux.integration`）：

- **全输出路径** `_set_kde_wallpaper`：优先 `plasma-apply-wallpaperimage`，回退 Plasma scripting 遍历全部 containment。`plasma-apply-wallpaperimage` 是 Plasma 的全输出命令——一次调用作用于所有屏幕，因此只能用于“对全部输出设置”，无法表达“只改某一个显示器”。
- **按输出路径** `set_kde_wallpaper_for_screen`（v1.6.4，KDE_SUPPORT_PLAN 任务 3）：先跑只读探针建立 containment→screen 的显式映射，然后生成一段 evaluateScript 写入脚本，用 **id 与 screen 双重条件**只定位探针命中的 containment——防止探针与写入之间 Plasma 桌面配置变化导致写错对象。

**拒绝部分成功**是按输出路径的核心语义：探针无法把请求的 screen 映射到任何 containment（或命中者缺少可解析的 id、做不了双重定位）时，直接返回结构化失败——不生成写脚本、不触碰任何桌面；写入脚本运行时报告 `applied=0`（探针命中但写入时全部消失）同样判失败。错误信息与“命令缺失 / 无会话 bus / Plasma 拒绝”可区分。路径值经 `json.dumps` 注入脚本（防注入）；前置检查链顺序固定（screen_index 参数校验 → 文件存在 → 会话 bus → 探针），无 bus 时不 spawn 任何外部命令。

能力口径与行为对齐（`capabilities.probe_capabilities()["multi_monitor_static"]`）：KDE 会话只有 qdbus6/qdbus（Plasma scripting 通道）在场时才报告 `runtime_ready`——`plasma-apply-wallpaperimage` 在场只代表全输出可用。Windows 走 IDesktopWallpaper API（当前包装器不传 monitor ID，一次调用设置全部输出；该 API 支持按 monitor 定位，是未来按显示器设置的扩展点）；macOS 为同图全屏（NSScreen 遍历）。

UI/ports 层的按显示器选择接线是后续工作（当前无生产调用方）。

## 视频壁纸

Windows 新发布包优先使用已验证的内置 `mpv.exe + JSON IPC`，把 WorkerW 句柄通过
`--wid` 交给独立 mpv 进程；旧的 libmpv-only 运行时只保留为兼容回退。Linux X11
可使用内置 libmpv helper 或 `xwinwrap + mpv`，Wayland 的 layer-shell 会话使用
`mpvpaper`；`system/disabled` 构建模式不会启动内部 libmpv helper。macOS 使用原生
AVFoundation/AppKit 视频路径。所有可热控的 mpv 路径都把 `volume` 与 `mute` 作为
独立状态，通过 IPC 更新暂停、静音和音量，避免静音时丢失用户保存的音量。

## HTML 壁纸

HTML 只有一条运行链：`platform_adapters.native_html_runner` 强制选择 WebView2、WKWebView 或 WebKitGTK，平台 `native_webview_desktop` 负责 WorkerW、NSWindow 或 X11 桌面层。

构建器显式排除 QtQml、QtQuick、QtWebEngine 和 pywebview 的非目标平台后端。产物归一化和体积诊断会拒绝禁止载荷。Linux 当前仅支持 X11 桌面嵌入；Wayland 不宣称支持。

## UI 与线程

- View 到 Controller 使用语义明确的 Qt Signal；
- 后台 Worker 现状（v1.6.1 如实化，替代原先与实现不符的声明）：
  - 更新检查（`services/updates.py`）继承 `QThread`，经 `finished` Signal 回 UI；
  - 长时核心操作（模式切换事务、Bing 同步、壁纸启停）运行在 `threading.Thread`（daemon）中，Worker 体内不触碰任何 Qt 控件，结果一律经 `core_result_signal`、`bing_result_signal` 等 Qt Signal（跨线程即 queued）回到主线程；
  - IPC 命令合并 worker、Win32 消息循环、进程清理与 fire-and-forget 后台任务属于非 Qt 关注点，允许使用 Python 线程；
- Worker 线程的硬性规则（新增代码必须遵守）：Worker 线程内禁止直接操作 Qt 控件；回到 UI 必须经 Qt Signal 或主线程调度；
- 目标态（路线图，未达成）：长期 Worker 迁移到 `QObject.moveToThread(QThread)` 以获得 `quit()`/`wait()`/`finished` 生命周期管理；迁移必须逐场景进行并伴随真机回归验收，不一次性全量替换；
- 不新增全局万能事件总线；
- 主 Widgets UI 不嵌入网页控件。

## 门禁

发布前的独立静态审计检查依赖方向、Repository/Service 边界、生成文件、文档链接、翻译，以及被删除的嵌入式浏览器源码是否回归。审计记录不混入精简源码包。

## 启动前壁纸快照

`SessionWallpaperService` 在应用改变桌面前捕获一次原始壁纸和 Windows 样式。该快照在整个进程会话内保持有效：GUI 手动恢复是可重复的幂等操作，不消费快照；最终退出恢复成功后才清理内存和持久化会话文件。若最终恢复失败，快照保留供下一次退出或崩溃恢复重试。
