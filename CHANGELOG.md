# Changelog

本文件记录 ShangBackground 的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [1.5.0] - 2026-08-09

### Fixed

- **Windows 桌面右键 3–4 秒忙碌** — Explorer 入口新增纯 stdlib/Win32 快路径：运行中直接转发固定动作，冷启动立即脱离 Explorer 后再启动完整 GUI；启动竞态由脱离后的子进程延长 IPC 重试兜底。
- **Linux/macOS 首页泄漏 Windows 右键控件** — 非 Windows 不再创建 `ctx_*` 桌面 Shell 开关；真实全局热键继续只由独立的“全局热键设置”页管理。
- **全局热键保存误判失败** — GUI 不再调用已经不存在的 `core._parse_hotkey_string` / `core._pynput_hotkey_string`；Windows 与共享实现统一使用 `platform_adapters.hotkey_bindings.parse_hotkey`，右键菜单开关也不再触发无关的全局热键重注册。
- **Windows 新包不再接受 libmpv-only payload** — v1.5.0 构建入口要求 Windows MPV runtime 含 `mpv.exe`；旧 libmpv-only 安装仍可兼容运行，但不会再被构建成新的完整应用子进程播放方案。
- **MPV bundle 调用链过重/下载策略失真** — Windows 优先使用已验证的 `mpv.exe + JSON IPC`，旧 libmpv-only payload 仅作兼容回退；显式下载默认使用 mpv 官方 latest stable release 的 Windows 二进制资产，`development` 仅保留为最新 master CI 的显式选择。
- **MPV 升级残留原生 DLL** — Inno Setup 安装前定点清理产品自有 `bin\mpv`，避免新旧 native runtime 混装；损坏安装也不再因为主程序无法执行退出命令而完全阻断卸载。
- **命令行动作假成功** — 单实例 IPC 转发失败、动作执行异常和不存在的壁纸路径现在返回非零退出码，便于 Explorer/脚本准确判断结果。
- **Release 冻结程序冒烟测试可被静默绕过** — `--version` 失败或版本不匹配会直接阻断发布，并从 `src` 读取唯一版本源。
- **Inno Setup 空壳/错布局风险** — 安装器只接收构建器明确选择的一种冻结布局；构建清单会校验 freezer、Windows 目标和 x86_64 架构。
- **PyInstaller 升级残留** — 安装前清理产品自有 `_internal` 目录，避免旧 DLL/PYD 残留，并覆盖 PyInstaller → Nuitka 迁移路径。
- **安装后校验过弱** — Inno Setup 现在同时检查主程序和对应 `build-features.json`，并默认写安装日志。
- **性能档语义倒置** — 集中三档调度参数；“流畅”档现在确实比“均衡”档刷新更快并允许更大的缩略图解码/缓存预算，同时保持默认“均衡”档原有参数不变。

### Changed

- 版本升级到 **1.5.0**；Windows 文件版本同步为 `1.5.0.0`。
- Inno Setup 流程明确要求 **Inno Setup 7**，使用 `SetupArchitecture=x64`，并停止自动搜索 Inno Setup 6。
- 安装器新增 `MinVersion=10.0.17763`，与 Qt/PySide 6.11 的 Windows 10 1809+ 支持范围对齐，避免旧系统“可安装但不可运行”。
- PyInstaller 构建固定版本从 6.21.0 更新到 **6.22.0**；Nuitka 4.1.3、PySide6-Essentials 6.11.1 保持不变。
- 收拢 Windows/macOS UI mixin 中与共享实现等价的图标缓存、侧边栏、Bing 另存和暗色模式覆盖，减少平台补丁分叉；删除 macOS 分支中未使用的 Windows Startup 辅助方法。
- 继续移除平台 mixin 中可由 AST 证明与共享实现行为等价的重复覆盖，并让 Windows 热键/右键菜单直接复用统一状态机，减少“某平台修了、另一份镜像没修”的回归面。

## [1.4.6] - 2026-07-29

### Fixed

- **Inno Setup 卸载器运行时错误** — 移除卸载阶段不支持的 `CreateOutputMsgPage`，改用静默模式安全的确认框。
- **卸载时主进程仍占用文件** — `--quit --wait-for-exit` 等待准确 PID 完成清理，IPC 启动窗口内重试且失败时中止卸载。
- **默认保留配置失效** — 用户数据删除改为受明确确认控制，静默卸载默认保留配置和日志。
- **启动项和右键菜单残留** — 统一清理当前及旧版 Run 值、产品专属右键菜单和重复的公共启动快捷方式。
- **误删通用启动脚本** — 仅在确认 `PowerOn.vbs` 属于 ShangBackground 时清理。
- **安装包空壳风险** — 拒绝不匹配或同时存在的 standalone 布局，dry-run 默认执行输入校验。

### Changed

- Windows Release 使用 Inno Setup 7 x64、Nuitka full standalone 和 UPX 5.2.0，并保留既有多平台发布门禁。

## [1.4.5] - 2026-07-26

### Fixed

- **卸载后VBS开机启动残留** — 改用 HKCU 注册表 Run 键替代 VBS 文件，Inno Setup 自动清理（`uninsdeletevalue`）
- **右键菜单/托盘无壁纸时无提示** — IPC 壁纸命令失败时显示托盘通知（"没有上一张壁纸"等）

### Added

- **Inno Setup 欢迎页** — 自定义中文欢迎文字，介绍应用功能
- **卸载界面可选删除用户配置** — 复选框默认不勾选，保护用户数据
- **注册表 Run 键开机启动** — 替代 VBS，启动更快（无 wscript.exe 中间进程），卸载自动清理

### Changed

- v1.4.4 已发布为正式 Release

## [1.4.4] - 2026-07-24

### Fixed

- **视频壁纸切换内存飙升/启动慢（根因修复）** — `--mpv-runtime system` 构建中，`_internal_libmpv_command` 仍尝试用内部 ctypes/libmpv 路径，导致**生成完整的打包可执行文件子进程**（~300MB+）仅为了播放视频。修复：当 `video_runtime_mode()` 为 `system` 或 `disabled` 时跳过内部 libmpv 路径，直接使用外部 mpv.exe（~30MB）。同时修复 Linux X11 后端相同问题。
- **托盘右键菜单1秒延迟（根因修复）** — 不再每次右键重建 QMenu，改为持久化 `QMenu` 实例 + `menu.clear()` 重填。预热改为 `show()`+`hide()` 强制创建原生窗口。
- **sidebar 点击外侧不缩回（Windows）** — `_OutsideClickShield` 移除 `WindowDoesNotAcceptFocus` 标志，提高 `windowOpacity` 从 0.001 到 0.01。`qApp` 事件过滤器新增 `MouseButtonRelease` 和 `NonClientAreaMouseButtonPress` 监听。
- **触摸滑动误触壁纸切换** — 新增 `_TouchScrollFilter` 事件过滤器，检查移动距离和 `QScroller` 状态。
- **收藏夹右键菜单阻塞事件循环** — `menu.exec()` 改为 `menu.popup()`（异步）。

### Changed

- v1.4.3 已发布为 prerelease。

## [1.4.3] - 2026-07-24

### Fixed

- **托盘右键菜单首次延迟** — 启动时预热线程菜单的 `sizeHint()` 和图标解码，消除首次右键的样式/图标惰性解析延迟（Qt Forum topic 123225）。
- **触摸滑动误触壁纸切换** — 新增 `_TouchScrollFilter` 事件过滤器，在 `MouseButtonRelease` 时检查移动距离（>10px）和 `QScroller` 状态（Dragging/Scrolling），抑制滑动产生的合成鼠标点击事件。
- **QScroller DragStartDistance** — 从 0.008 (8mm) 提高到 0.012 (12mm)，减少短距离滑动被误判为点击的概率。
- **收藏夹右键菜单阻塞事件循环** — `menu.exec()` 改为 `menu.popup()`（异步），避免模态嵌套事件循环在触摸事件吞没释放时冻结 UI。
- **误触后托盘/右键失效** — 根因是 `_core_busy` 门控在误触触发壁纸切换后静默拒绝后续操作；触摸误触修复后此问题不再出现。

### Performance

- **UPX LZMA 移除** — Nuitka UPX 插件硬编码 `--best --lzma`，LZMA 解压比 NRV2E 慢 ~10x。wrapper 脚本移除 `--lzma`，保留 `--best`（NRV2E >500 MB/s 解压）。
- **vcruntime DLL 排除** — UPX wrapper 自动跳过 vcruntime140.dll、ucrtbase.dll 等脆弱 DLL（压缩会导致崩溃）。
- **冻结产物冒烟测试** — Release workflow 新增 `--version` 冒烟步骤。

### Changed

- v1.4.2 Release 已发布为正式版（非 prerelease）。

## [1.4.2] - 2026-07-24

### Added

- 四套标准 GitHub Actions 工作流：CI、Build and release、CodeQL、Dependency review。
- Inno Setup Windows 安装包（`setup.exe`），内嵌用户许可协议，必须勾选"我接受协议"才能继续安装。
- `build_tools/build.py installer` 子命令，从已验证的 PyInstaller standalone 产物生成 `setup.exe`。
- `.github/scripts/release.py` 发布自动化脚本：版本一致性校验、源码归档、SHA256 校验和。
- Dependabot 同时维护 pip 依赖和 GitHub Actions 版本。
- macOS arm64（Apple Silicon）原生构建支持。

### Changed

- 统一 `src/` 架构，构建工具链重组到 `build_tools/buildlib/`。
- `lite` 配置默认关闭视频和 HTML 模块，减少产物体积。
- Release 资产从 5 个扩展到 7 个（新增 Windows `setup.exe`，架构覆盖 macOS arm64）。

### Fixed

- macOS frozen-runtime 验证：用 `.app` bundle 根目录而非 `Contents/MacOS` 作为 packaged application 根，修复 `Contents/Frameworks` 资源路径被误判为逃逸的问题。
- Windows publish 步骤：`os.replace` 在 WinError 5（杀毒软件锁文件）时重试 + copytree 回退。
- Linux Qt XCB 前置依赖：apt-get 安装 `libxcb-shape0` 等缺失库。
- Inno Setup 安装包：移除错误的 `PrepareToInstall` 检查（该钩子在文件复制前运行，导致安装总是失败），改为 `CurStepChanged(ssPostInstall)` 后置校验。

## [1.4.1] - 2026-07-15

### Changed

- 性能模式从布尔 `performance_mode` 改为三档 `performance_level`（`power_saver` / `balanced` / `performance`）。
- 启动任务延迟按性能档位分级，避免低端设备首帧卡顿。

### Fixed

- 修复幻灯片/Bing/视频启动任务抢占启动前壁纸记录的时序问题。
- 修复退出恢复在某些桌面环境下失败后未保留会话恢复文件的问题。

## [1.4.0] - 2026-07-01

### Added

- 三端系统原生 WebView HTML 壁纸：Windows WebView2、macOS WKWebView、Linux WebKitGTK。
- 模块化构建：`--features video,html,bing,hotkeys,updates,fonts` 按需勾选。
- Bing 每日壁纸、收藏、历史与概率权重。
- 全局热键、托盘、开机自启、单实例守护。

### Changed

- 主界面使用 Qt Widgets，HTML 壁纸运行在独立子进程，不嵌入 Qt WebEngine。
- 构建系统统一为 Nuitka/PyInstaller 双后端，共享同一份构建计划。
