# Changelog

本文件记录 ShangBackground 的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [1.4.2] - 2026-07-23

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
