<h1 align="center">
  <img src="https://xxdz-official.github.io/ShangBackground/img/LOGO.png" width="80" height="80" alt="Logo"><br>
  上一个桌面背景 / ShangBackground
</h1>

<p align="center">
  <b>恢复经典"上一个桌面背景"右键菜单，支持多平台与现代化壁纸管理</b><br>
  <b>Restore the classic "Previous Desktop Background" menu with modern wallpaper management</b>
</p>

<p align="center">
  ![Version](https://img.shields.io/badge/version-v1.4.0-blue)
  ![Windows](https://.shields.io/badge/Windows-Stable-brightgreen)
  ![Linux](https://img.shields.io/badge/Linux-Beta-orange)
  ![macOS](https://img.shields.io/badge/macOS-Alpha-lightgrey)
  ![License](https://img.shields.io/badge/License-GPLv3-blue)
  ![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
  ![PySide6](https://img.shields.io/badge/PySide6-6.7+-41CD52?logo=qt&logoColor=white)
</p>

<p align="center">
  <a href="#-功能特性--features">功能</a> ·
  <a href="#-平台支持--platform-support">平台</a> ·
  <a href="#-快速开始--quick-start">快速开始</a> ·
  <a href="#-从源码构建-nuitka--building-from-source">构建</a> ·
  <a href="#-v14-项目主页--project-hub">v1.4 主页</a> ·
  <a href="#-贡献者--contributors">贡献者</a>
</p>

---

## 📖 项目简介 / Overview

ShangBackground（上一个桌面背景）是一个跨平台桌面壁纸管理应用。Windows 版恢复经典的桌面右键"上一个桌面背景"菜单；Linux/macOS 版提供壁纸切换、托盘菜单与全局热键，不宣称桌面/文件管理器级右键菜单集成。

它不只是一个壁纸切换器，而是一个完整的壁纸工作站：

- 🖱️ **右键菜单集成** — Windows 原生注册表 shell verb；Linux & macOS 提供系统托盘右键菜单，不提供桌面/文件管理器级右键菜单
- 🎬 **切换动画** — 多种壁纸切换动画，顺滑视觉过渡
- 🌐 **Bing 每日壁纸** — 自动同步，自适应分辨率
- 🎲 **概率权重** — 滑块 + 数值双控，精准分配随机壁纸权重
- ⌨️ **全局热键** — `Ctrl+Alt+N` 等；保存后立即注册，不再依赖右键菜单开关（Windows 版带焦点防误触检测，Linux/macOS 暂无）
- 🌍 **双语界面** — 中英实时切换，无需重启
- 🎨 **主题与字体** — 自定义字体、主题颜色、应用内 DPI 调整
- 🔒 **单实例守护** — 进程级锁，重复启动自动唤起主窗口

> **v1.4.0** 新增 [Next.js 16 项目主页](#-v14-项目主页--project-hub)：18 条深度审计、可点击对比矩阵、下载趋势 sparkline、命令面板、PWA 离线支持。

---

## ✨ 功能特性 / Features

| 功能 / Feature | 说明 / Description |
|---|---|
| 🖱️ 桌面右键菜单 / Desktop Right-click Menu | Windows 恢复经典"上一个桌面背景"菜单；Linux/macOS 使用托盘菜单 / Windows restores the classic menu; Linux/macOS use the tray menu |
| 🎬 切换动画 / Transitions | 多种壁纸切换动画 / Multiple wallpaper transition animations |
| 🎨 主题与字体 / Theming | **自定义字体、主题颜色、应用内 DPI 调整** / **Custom font, theme color, in-app DPI scaling** |
| 🔔 更新渠道 / Update Channels | **支持多更新源切换** / **Multiple update source switching** |
| 🌐 双语界面 / Bilingual UI | 中英实时切换，无需重启 / Switch between Chinese & English without restart |
| 🎲 概率分配 / Probability | 滑块+数值双控，壁纸随机权重精准分配 / Slider + numeric dual control for wallpaper weights |
| 🧠 Bing 壁纸 / Bing Wallpapers | 自动同步 Bing 每日壁纸，自适应分辨率 / Auto-sync Bing daily wallpapers with adaptive resolution |
| 🛡️ 单实例守护 / Single Instance | 进程级锁，重复启动自动唤起主窗口 / Process-level lock prevents duplicate instances |
| 🔄 退出还原 / Restore on Exit | 关闭程序自动恢复原始壁纸 / Auto-restore original wallpaper on exit |
| 🚀 开机自启 / Auto-start | 支持 Windows、Linux 与 macOS 开机启动 / Boot auto-start on Windows, Linux & macOS |
| 📦 配置迁移 / Config Migration | 首次启动自动迁移旧配置至 `%LOCALAPPDATA%` / Auto-migrate legacy configs on first run |
| ⌨️ 全局热键 / Global Hotkeys | 保存后立即注册；Windows 含焦点防误触，Linux/macOS 基于 pynput / Registered immediately after saving; Windows includes focus guard, Linux/macOS use pynput |
| 🎬 视频壁纸 / Video Wallpaper | 基于 mpv 的视频壁纸（Windows 完整支持，Linux/macOS 部分） / mpv-based video wallpaper |
| 🧩 HTML 壁纸 / HTML Wallpaper | Windows WorkerW 嵌入 HTML 壁纸 / Windows-only WorkerW HTML wallpaper |

---

## 🖥️ 平台支持 / Platform Support

| 平台 / Platform | 状态 / Status | 说明 / Notes |
|---|---|---|
| Windows | ✅ Stable | 完整功能：注册表、右键菜单、托盘、自启、WorkerW HTML 壁纸 / Full features |
| Linux | 🧪 Beta | `gsettings` / `xfconf` / `feh` 三后端 / Three backends supported |
| macOS | ⚠️ Alpha | `osascript` + `LaunchAgent`，欢迎反馈 / Feedback welcome |

### 功能对比矩阵 / Feature Comparison Matrix

| 功能 / Feature | Windows | Linux | macOS |
|---|:---:|:---:|:---:|
| 桌面右键菜单 / Desktop right-click menu | ✅ | ❌ | ❌ |
| 切换动画 / Transitions | ✅ | ✅ | ✅ |
| 主题与字体 / Theming | ✅ | ✅ | ✅ |
| 双语界面 / Bilingual UI | ✅ | ✅ | ✅ |
| 概率权重 / Probability | ✅ | ✅ | ✅ |
| Bing 壁纸 / Bing wallpapers | ✅ | ✅ | ✅ |
| 单实例守护 / Single-instance | ✅ | ✅ | ✅ |
| 退出还原 / Restore on exit | ✅ | ✅ | ✅ |
| 开机自启 / Boot auto-start | ✅ | ✅ | ✅ |
| 全局热键 / Global hotkeys | ✅（含焦点防误触） | 🟡（pynput，无焦点防误触） | 🟡（pynput，无焦点防误触） |
| HTML 壁纸 (WorkerW) | ✅ | ❌ | ❌ |
| 鼠标穿透 / Mouse-through | ✅ | 🟡 | ❌ |
| 配置迁移 / Config migration | ✅ | ✅ | ✅ |
| 视频壁纸 (mpv) / Video wallpaper | ✅ | 🟡 | 🟡 |

Legend: ✅ 支持 / 🟡 部分 / ❌ 暂未

---

## 🚀 快速开始 / Quick Start

### Windows

1. 下载 [Release](https://github.com/purrfecto114-lgtm/ShangBackground/releases) 并解压至非系统目录
2. 运行 `ShangBackground.exe`
3. 桌面右键即可使用"上一个桌面背景"菜单

### Linux

```bash
# 克隆仓库并进入目录
git clone https://github.com/purrfecto114-lgtm/ShangBackground.git
cd "上一个桌面背景 - 源代码"

# 安装依赖（Linux）
python3 -m pip install -r "Linux.ver(beta)/requirements-linux.txt"

# 可选：安装 pynput 以启用全局热键
python3 -m pip install pynput || true

# 运行应用
python3 "Linux.ver(beta)/src/main.py"
```

### macOS

```bash
# 克隆仓库并进入目录
git clone https://github.com/purrfecto114-lgtm/ShangBackground.git
cd "上一个桌面背景 - 源代码"

# 安装依赖（macOS）
python3 -m pip install -r "MacOS.ver(alpha)/requirements-macos.txt"

# 可选：安装 pynput 以启用全局热键（需在系统设置中授予辅助功能权限）
python3 -m pip install pynput || true

# 运行应用
python3 "MacOS.ver(alpha)/src/main.py"
```

> 💡 `psutil` 与 `pynput` 为可选依赖：缺少 `psutil` 时仅跳过旧进程清理；缺少 `pynput` 时仅禁用全局热键功能。Linux/macOS 全局热键不做 Windows 版焦点防误触检测，请选择不易冲突的组合键。
> 💡 `psutil` and `pynput` are optional: without `psutil` old process cleanup is skipped; without `pynput` global hotkeys are disabled.

---

## 🔨 从源码构建 (Nuitka) / Building from Source

v1.4.0 引入了跨平台 Nuitka 构建工具，位于 `scripts/` 目录。

### 前置依赖 / Prerequisites

```bash
# 1. Python 3.10+
python --version

# 2. 安装构建依赖
pip install -r scripts/requirements-build.txt

# 3. C 编译器 (Nuitka 会自动下载 MinGW64)
#    Linux:  apt install gcc patchelf
#    macOS:  xcode-select --install
```

### 构建命令 / Build Commands

```bash
# ── Windows ────────────────────────────────────────────
# 独立文件夹 (→ 再用 Inno Setup 打包安装程序)
python scripts/build_nuitka.py --platform windows --standalone

# 单文件 exe (启动较慢，无需安装)
python scripts/build_nuitka.py --platform windows --onefile

# 启用 LTO (构建慢，二进制快)
python scripts/build_nuitka.py --platform windows --lto

# ── Linux ──────────────────────────────────────────────
python scripts/build_nuitka.py --platform linux
# → 生成 build/nuitka/linux/ShangBackground-1.4.0-linux-x64.tar.gz

# ── macOS ──────────────────────────────────────────────
python scripts/build_nuitka.py --platform macos
# → 生成 build/nuitka/macos/ShangBackground.app/  +  .dmg

# ── Dry run (只打印命令，不执行) ────────────────────────
python scripts/build_nuitka.py --platform linux --dry-run
```

### Windows 安装程序 (Inno Setup)

构建独立文件夹后，用 Inno Setup 编译安装程序：

```bash
# 需要 Inno Setup 6+ (https://jrsoftware.org/isdl.php)
iscc scripts/shangbackground.iss
# → build/nuitka/windows/ShangBackground-1.4.0-win64.exe
```

安装程序会：
- 打包整个 `.dist/` 文件夹
- 注册右键 shell verb "Set as ShangBackground wallpaper"
- 创建开始菜单 + 可选桌面快捷方式
- 支持中英双语安装向导

### 各平台原有构建脚本 / Per-platform legacy scripts

各平台目录下仍保留原有的 `.bat` / shell 构建脚本（已更新至 1.4.0）：

| 平台 | 脚本 | 说明 |
|------|------|------|
| Windows | `Windows.ver/build_windows_nuitka.bat` | Nuitka standalone + UPX |
| Windows | `Windows.ver/build_windows_onedir.bat` | Nuitka onedir |
| Windows | `Windows.ver/build_windows_pyside6_deploy.bat` | PySide6-deploy |

---

## 🌐 项目网站 / Project Website

> ℹ️ v1.4.0 早期草稿曾计划把项目网站改为 **Next.js 16** 单仓部署（位于本仓库根目录 `src/`），含 10 区段、18 条审计、命令面板、PWA 等。该计划**延期到 v1.4.1**——v1.4.0 仓库内**不包含** Next.js 源码。
>
> v1.4.0 仍使用位于 [xxdz-official.github.io/ShangBackground](https://xxdz-official.github.io/) 的传统静态 HTML 站点（`index.html` / `v1.html` / `gotoBV.html`）。这些 HTML 文件随上游 `purrfecto114-lgtm/ShangBackground` `main` 分支发布，不在本 v1.4.0 源码树内。

### v1.4.1 计划 / Planned for v1.4.1

- **10 个区段**：Hero · 功能 · 平台 · 下载 · 对比 · 审计 · 更新日志 · 架构 · 问题 · 贡献者
- **双语**（zh / en）即时切换
- **18 条深度审计**（5 安全 · 8 逻辑 · 5 性能）— 每条含漏洞代码片段 + 修复建议 + 可点击状态条（Open → Fixing → Fixed）
- **可点击对比矩阵** — 点击单元格跳转到相关审计发现
- **命令面板**（`⌘K`）— 全局搜索
- **键盘快捷键** — `T` 主题切换、`?` 帮助浮层
- **PWA** — 可安装，离线可用

### 主页技术栈（规划） / Hub Tech Stack (Planned)

| 技术 | 版本 |
|------|------|
| Next.js | 16 (App Router) |
| TypeScript | 5 |
| Tailwind CSS | 4 |
| shadcn/ui | New York |
| Framer Motion | 11 |
| Prisma | SQLite |

---

## 📂 项目结构 / Project Structure

```
ShangBackground-1.4.0/
├── Windows.ver/                # Windows 源码树
│   ├── src/                    #   PySide6 源码 (app/ core/ services/ platform_adapters/ ui/)
│   ├── fonts/                  #   字体（可选）
│   ├── build_gui.py            # 🆕 PySide6 GUI 构建器
│   ├── build_nuitka.py         # 🆕 Nuitka CLI 驱动
│   ├── build_windows_*.bat     #   原有构建脚本（已更新 1.4.0）
│   └── requirements-windows*.txt
├── Linux.ver(beta)/            # Linux 源码树（同构）
├── MacOS.ver(alpha)/           # macOS 源码树（同构）
├── img/                        # 共享图片资源
├── tests/                      # 🆕 19 个跨平台冒烟测试
├── tools/                      # 🆕 5 个工程化脚本
├── scripts/                    # ⚠️ 占位 — v1.4.0 尚未提供，v1.4.1 计划
│   └── README.md               #   详见 scripts/README.md
├── CHANGES-v1.4.0.md           # 🆕 v1.4.0 完整变更日志
├── CHANGES-html-fix-v*.md      # 🆕 HTML 壁纸专项历史日志
├── PROJECT_STRUCTURE.md        # 🆕 详细模块组织说明
├── GETTING_MPV.md              # 🆕 mpv 二进制打包说明
├── README.md                   # ← 本文件
├── LICENSE                     # GPLv3
└── NOTICE                      # 第三方声明
```

---

## 👥 贡献者 / Contributors

| 贡献者 / Contributor | 贡献内容 / Contribution |
|---|---|
| [小小电子xxdz](https://space.bilibili.com/) | 项目创始人、Windows 原版 / Founder & original Windows version |
| [@purrfecto114-lgtm](https://github.com/purrfecto114-lgtm) | Fork 维护、PySide6 重构、Linux 支持 / Fork maintenance, PySide6 refactor, Linux support |

---

## 🤝 贡献 / Contributing

欢迎贡献！项目追踪 18 条审计发现，许多是新贡献者的良好入口。

1. Fork 仓库
2. 创建功能分支：`git checkout -b feat/my-feature`
3. 使用约定式提交：`feat: add X`、`fix: resolve Y`
4. 提交 Pull Request

### 需要帮助的领域 / Areas needing help
- ⌨️ **Linux/macOS 全局热键完善** — 已初步实现（pynput），欢迎测试不同桌面环境与发行版
- 🍎 **macOS 鼠标穿透** — AUD-007（subprocess 无 NSWindows）
- 📊 **真实下载计数历史** — Prisma + 定时任务追踪
- 🌐 **更多 i18n 语言** — 欢迎日语、韩语、西班牙语

---

## ⚠️ 授权说明 / License

- **源代码 / Source Code**: [GNU General Public License v3.0](LICENSE) — 可自由修改与分发，衍生作品须保持相同许可 / Free to modify and distribute, derivative works must remain under the same license.
- **图像素材 / Image Assets**: `/img/` 目录下所有视觉素材由 **小小电子xxdz** 创作，**保留所有权利**，不包含在 GPLv3 许可范围内。
  All visual assets in `/img/` are created by **xxdz**, **all rights reserved**, **NOT** covered by GPLv3.

---

## 🔗 相关链接 / Links

- 🌐 官网 / Website: [xxdz-official.github.io](https://xxdz-official.github.io/)
- 📺 Bilibili: [小小电子xxdz](https://space.bilibili.com/)
- 💻 上游仓库 / Upstream: [xxdz-official/ShangBackground](https://github.com/xxdz-official/ShangBackground)
- 🍴 当前仓库 / Current Fork: [purrfecto114-lgtm/ShangBackground](https://github.com/purrfecto114-lgtm/ShangBackground)
- 📦 最新发布 / Latest Release: [v1.4.0](https://github.com/purrfecto114-lgtm/ShangBackground/releases/latest)
---
<p align="center">Made with ❤️ by ShangBackground Team · 上一个桌面背景</p>
