# Web Verification

> 抓取时间：2026-07-24（Europe/Helsinki）  
> 方法：优先官方项目文档、发布页与 KDE/Qt/GitHub 官方资料；第三方材料只用于补充，不作为核心决策唯一依据。

| Query / 核验项 | 官方 URL | 关键结论 | 对本项目的决策影响 |
|---|---|---|---|
| mpv latest stable release | https://github.com/mpv-player/mpv/releases | 最新稳定版为 v0.41.0（2025-12-21）；发布页提示 FFmpeg ≥6.1、libplacebo ≥6.338.2 | bundle 元数据应记录完整版本与依赖；不能只校验文件存在 |
| mpv embedding / JSON IPC | https://mpv.io/manual/stable/ | 嵌入应用推荐 libmpv；外部进程控制使用 JSON IPC；IPC 不是安全边界 | `MpvBackend` 同时保留 ctypes 与 IPC；socket/pipe 必须限制为本用户路径 |
| XDG GlobalShortcuts frontend v2 | https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html | `CreateSession`/`BindShortcuts`/`Activated`；绑定通常触发用户配置 UI | Wayland 热键必须异步，不可阻塞 Qt；用户拒绝是正常降级 |
| XDG shortcuts trigger syntax | https://specifications.freedesktop.org/shortcuts/latest/ | `CTRL+ALT+key`，modifier 为 CTRL/ALT/SHIFT/NUM/LOGO，键名来自 xkbcommon | 新增 `to_xdg_shortcut()`，不使用 Gtk accelerator 字符串 |
| Portal backend selection | https://flatpak.github.io/xdg-desktop-portal/docs/portals.conf.html | 实际实现由桌面/发行版的 portals.conf 选择 | capability 只能标 best-effort，不能仅凭 KDE 名称宣称 backend 一定存在 |
| KDE Plasma GlobalShortcuts support | https://kde.org/announcements/plasma/5/5.27.0/ | Plasma 5.27 Wayland 官方宣布支持 Global Shortcuts portal | KDE 5.27+/6 优先 Portal，而非 pynput 或自建 KWin 键盘钩子 |
| KWin scripting API | https://develop.kde.org/docs/plasma/kwin/api/ | KWin 脚本提供窗口/工作区自动化 API，但不是媒体壁纸解码 API | 不把视频解码塞进 scripted effect；保留为未来专用插件研究项 |
| KWin layer-shell guidance | https://mail.kde.org/pipermail/kwin/2024-August/005326.html | KDE/KWin 开发者建议第三方 dock 使用 layer-shell/layer-shell-qt，说明 KWin 支持该模型 | 将 KDE/Plasma 纳入 mpvpaper layer-shell 可尝试会话，但仍要求真机验证 |
| PySide6 6.11.1 | https://doc.qt.io/qtforpython-6/release_notes/pyside6_release_notes.html | 6.11.1 已发布，包含线程亲和与部署修复；最低 Python 版本在 6.11 系列为 3.10 | 事件回调保持 GUI 线程；构建文档固定 PySide6 版本而非假设系统 Qt |
| PyInstaller native binaries | https://pyinstaller.org/en/stable/spec-files.html | 共享库应作为 binaries/`--add-binary` 明确收集 | libmpv 不应伪装为 data；维持平台原生 runtime 清单 |
| Nuitka DLL handling | https://nuitka.net/user-documentation/user-manual.html | 动态加载 DLL 需要 package configuration；DLL 不是普通 data file | 继续使用 Nuitka package config 描述 libmpv payload |
| Dependabot version updates | https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file | 依赖更新由 ecosystem/directory/schedule 显式配置 | 建议 requirements 与 GitHub Actions 分组更新并保留 CI 门禁 |
| GitHub Actions Dependabot | https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuring-dependabot-version-updates | `github-actions` ecosystem 可更新 action refs | CI action 更新与 Python 依赖分开审查，避免一次 PR 混合供应链变化 |

## 无法完全由网页替代的验证

- `mpvpaper` 在具体 KWin/Plasma 版本上的多屏、桌面图标层级、锁屏切换和退出清理必须在真机验证。
- Portal 是否实际可用取决于 `xdg-desktop-portal`、`xdg-desktop-portal-kde`、会话总线和发行版配置；代码只能做运行时探测与错误降级。
- PySide6 Wayland 窗口层级不能证明第三方 layer-shell 进程行为；本项目 Wayland 视频由 mpvpaper 负责，不由普通 Qt 窗口冒充桌面层。
