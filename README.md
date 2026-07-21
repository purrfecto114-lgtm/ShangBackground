<h1 align="center">
  <img src="https://xxdz-official.github.io/ShangBackground/img/LOGO.png" width="80" height="80" alt="ShangBackground Logo"><br>
  上一个桌面背景 / ShangBackground
</h1>

<p align="center">
  跨平台桌面壁纸管理器：静态壁纸、幻灯片、Bing、视频与交互式 HTML 壁纸。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-v1.4.2-0ea5e9?style=flat-square" alt="version">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/PySide6-6.11-41cd52?style=flat-square&logo=qt&logoColor=white" alt="PySide6">
  <img src="https://img.shields.io/badge/License-GPLv3-blue?style=flat-square" alt="GPLv3">
</p>

## 项目定位

ShangBackground 使用一份共享源码支持 Windows、Linux 和 macOS。Windows 版可恢复经典“上一个桌面背景”桌面右键菜单；Linux/macOS 提供托盘、壁纸切换和受支持会话中的全局热键，但不宣称文件管理器级右键菜单集成。

主要能力：

- 图片、幻灯片、纯色和渐变壁纸；
- Bing 每日壁纸、收藏、历史与概率权重；
- 可选直接 libmpv/外部 mpv 视频壁纸，以及三端系统原生 WebView HTML 壁纸；
- 全局热键、托盘、开机自启、幂等的启动前壁纸恢复与单实例守护；
- 中英界面、主题、字体、DPI 和可搜索设置；
- Nuitka/PyInstaller 统一构建，以及按功能勾选的模块化产物。

## UI 技术路线

项目主界面使用 **Qt Widgets**。HTML 壁纸运行在独立子进程，并强制调用操作系统提供的网页控件：

- Windows：WebView2；
- macOS：WKWebView；
- Linux：WebKitGTK（当前桌面嵌入限 X11）；
- 主 Widgets 进程不嵌入网页，也不随包携带 Qt WebEngine/Chromium。

细节见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 与 [`docs/HTML_WALLPAPER_PERFORMANCE.md`](docs/HTML_WALLPAPER_PERFORMANCE.md)。

## 平台状态

| 能力 | Windows | Linux | macOS |
|---|:---:|:---:|:---:|
| 静态壁纸、托盘、设置 | ✅ | ✅ | ✅ |
| 桌面右键菜单 | ✅ | — | — |
| 全局热键 | ✅ 原生 | 🟡 X11；Wayland 未接入 Portal | 🟡 需系统权限 |
| 视频壁纸 | 🟡 WorkerW | 🟡 X11/桌面环境相关 | 🟡 AppKit/系统环境相关 |
| HTML 壁纸 | 🟡 WorkerW | 🟡 X11 | 🟡 AppKit |
| 开机自启 | ✅ | ✅ | ✅ |

`✅` 表示主路径已实现；`🟡` 表示实现存在，但仍依赖桌面环境、权限或目标系统真机验证。非本机 dry-run 只能验证构建参数，不能代替原生验收。

## 快速开始

```bash
git clone https://github.com/purrfecto114-lgtm/ShangBackground.git
cd ShangBackground
python -m venv .venv
```

激活虚拟环境后，按当前平台安装完整运行依赖并启动：

```bash
# Windows
python -m pip install -r requirements/windows-full.txt

# Linux
python -m pip install -r requirements/linux-full.txt

# macOS
python -m pip install -r requirements/macos-full.txt

python src/main.py
```

当前发布源码包只保留应用源码和独立构建工具；内部测试树、审计脚本与阶段性验证产物不随包分发。

## 模块化构建

打开构建工作台（目标平台自动锁定为当前系统，包含命令预览、折叠高级选项、有界实时日志和完整进程树停止）：

```bash
python build_tools/build.py --gui
# 或窗口化入口
python build_tools/build_gui.py
```

CLI 使用分组帮助并显示默认值：

```bash
python build_tools/build.py --tool pyinstaller --help
python build_tools/build.py --tool nuitka --help
python build_tools/build.py mpv --help
```

可选模块：`video`、`html`、`bing`、`hotkeys`、`updates`、`fonts`。图片、幻灯片、纯色和渐变属于核心功能，始终保留。

Windows 自包含视频包需先显式下载或手动放置匹配架构的完整 libmpv 运行时：

```bash
python build_tools/build.py mpv download --target windows --arch x86_64 --channel stable
python build_tools/build.py mpv verify --target windows --arch x86_64
python build_tools/build.py mpv list --target windows --arch x86_64
```

下载内容按平台/架构/版本保存到 `src/bin/mpv/`，构建时只携带选中的一个版本。普通构建不会隐式联网下载原生代码。Linux 可使用本地或目标系统 libmpv，macOS 使用 AVFoundation。

```bash
# 查看功能列表
python build_tools/build.py --tool nuitka --list-features

# 仅核心功能
python build_tools/build.py --tool nuitka --profile full --features none --mode standalone

# 核心 + 视频 + Bing
python build_tools/build.py --tool pyinstaller --profile full --features video,bing --mode standalone

# 含 HTML：只打包当前平台的原生 WebView 桥接，不携带 Qt WebEngine
python build_tools/build.py --tool pyinstaller --profile full --features html --mode standalone

# 只检查其他平台的命令生成，不构建
python build_tools/build.py --tool pyinstaller --target windows --profile full \
  --mode standalone --skip-install --dry-run
```

`full` 默认启用全部可选模块；`lite` 默认关闭视频和 HTML。构建 GUI 与 CLI 都委托给同一份构建计划，GUI 不包含隐式参数。Target 锁定为当前系统，非本机参数检查只保留在 CLI `--dry-run`。自定义组合会生成 `build-features.json`，运行时据此隐藏未打包功能并跳过对应依赖检查。详见 [`docs/BUILD_SYSTEM.md`](docs/BUILD_SYSTEM.md)。

## 文档

- [`docs/UI_ARCHITECTURE.md`](docs/UI_ARCHITECTURE.md)：Widgets 与独立原生 WebView 的职责边界
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：Service、Port、RuntimeState 与平台后端
- [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)：目录职责和生成文件
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)：开发环境与日常流程
- [`docs/BUILD_SYSTEM.md`](docs/BUILD_SYSTEM.md)：统一、模块化构建
- [`docs/GETTING_MPV.md`](docs/GETTING_MPV.md)：libmpv/mpv 运行时与发布约束
- [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md)：发布与源码归档
- [`docs/ROADMAP.md`](docs/ROADMAP.md)：当前优先级
- [`CHANGELOG.md`](CHANGELOG.md)：可追踪的版本变更

## 仓库说明

根目录的 `index.html`、`v1.html` 与 `img/` 是 GitHub Pages 站点资源，因此保留。缓存、构建目录、阶段性验证报告、`BUILD-INFO.json` 和 `build-generated/` 不属于发布源码。

## 贡献与许可证

贡献规则见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。项目按 GPL-3.0 发布，第三方声明见 [`NOTICE`](NOTICE)。
