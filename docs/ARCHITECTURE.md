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
- 长期 Worker 使用 `QObject.moveToThread(QThread)`；
- 短时 Python 任务使用共享线程池；
- 不新增全局万能事件总线；
- 主 Widgets UI 不嵌入网页控件。

## 门禁

发布前的独立静态审计检查依赖方向、Repository/Service 边界、生成文件、文档链接、翻译，以及被删除的嵌入式浏览器源码是否回归。审计记录不混入精简源码包。

## 启动前壁纸快照

`SessionWallpaperService` 在应用改变桌面前捕获一次原始壁纸和 Windows 样式。该快照在整个进程会话内保持有效：GUI 手动恢复是可重复的幂等操作，不消费快照；最终退出恢复成功后才清理内存和持久化会话文件。若最终恢复失败，快照保留供下一次退出或崩溃恢复重试。
