
<h1 align="center">
  <img src="https://xxdz-official.github.io/ShangBackground/img/LOGO.png" width="80" height="80" alt="Logo"><br>
  上一个桌面背景 / ShangBackground
</h1>

<p align="center">
  <b>恢复经典"上一个桌面背景"右键菜单，支持多平台与现代化壁纸管理</b><br>
  <b>Restore the classic "Previous Desktop Background" menu with modern wallpaper management</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-v1.4.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/Windows-Stable-brightgreen" alt="Windows">
  <img src="https://img.shields.io/badge/Linux-Beta-orange" alt="Linux">
  <img src="https://img.shields.io/badge/macOS-Alpha-lightgrey" alt="macOS">
  <img src="https://img.shields.io/badge/License-GPLv3-blue" alt="License">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/PySide6-6.7+-41CD52?logo=qt&logoColor=white" alt="PySide6">
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

ShangBackground（上一个桌面背景）是一个跨平台桌面壁纸管理应用。Windows 版恢复经典的桌面右键"上一个桌面背景"菜单；Linux/macOS 版提供壁纸切换、托盘菜单与全局热键。

它不只是一个壁纸切换器，而是一个完整的壁纸工作站：

- 🖱️ **右键菜单集成** — Windows 原生注册表 shell verb；Linux & macOS 提供系统托盘右键菜单
- 🎬 **切换动画** — 多种壁纸切换动画，顺滑视觉过渡
- 🌐 **Bing 每日壁纸** — 自动同步，自适应分辨率
- 🎲 **概率权重** — 滑块 + 数值双控，精准分配随机壁纸权重
- ⌨️ **全局热键** — `Ctrl+Alt+N` 等；保存后立即注册，不再依赖右键菜单开关（Windows 含焦点防误触，Linux/macOS 暂无）
- 🌍 **双语界面** — 中英实时切换，无需重启
- 🎨 **主题与字体** — 自定义字体、主题颜色、应用内 DPI 调整
- 🔒 **单实例守护** — 进程级锁，重复启动自动唤起主窗口

---

## ✨ 功能特性 / Features

| 功能 | 说明 |
|---|---|
| 🖱️ 桌面右键菜单 | Windows 恢复经典"上一个桌面背景"菜单；Linux/macOS 使用托盘菜单 |
| 🎬 切换动画 | 多种壁纸切换动画 |
| 🎨 主题与字体 | 自定义字体、主题颜色、应用内 DPI 调整 |
| 🔔 更新渠道 | 支持多更新源切换 |
| 🌐 双语界面 | 中英实时切换，无需重启 |
| 🎲 概率分配 | 滑块+数值双控，壁纸随机权重精准分配 |
| 🧠 Bing 壁纸 | 自动同步 Bing 每日壁纸，自适应分辨率 |
| 🛡️ 单实例守护 | 进程级锁，重复启动自动唤起主窗口 |
| 🔄 退出还原 | 关闭程序自动恢复原始壁纸 |
| 🚀 开机自启 | Windows、Linux 与 macOS 开机启动 |
| 📦 配置迁移 | 首次启动自动迁移旧配置 |
| ⌨️ 全局热键 | 保存后立即注册；Windows 含焦点防误触，Linux/macOS 基于 pynput |
| 🎬 视频壁纸 | 基于 mpv（Windows 完整支持，Linux/macOS 部分） |
| 🧩 HTML 壁纸 | Windows WorkerW 嵌入 HTML 壁纸 |

---

## 🖥️ 平台支持 / Platform Support

| 平台 | 状态 | 说明 |
|---|---|---|
| Windows | ✅ Stable | 完整功能：注册表、右键菜单、托盘、自启、WorkerW HTML 壁纸 |
| Linux | 🧪 Beta | `gsettings` / `xfconf` / `feh` 三后端 |
| macOS | ⚠️ Alpha | `osascript` + `LaunchAgent`，欢迎反馈 |

### 功能对比矩阵

| 功能 | Windows | Linux | macOS |
|---|:---:|:---:|:---:|
| 桌面右键菜单 | ✅ | ❌ | ❌ |
| 切换动画 | ✅ | ✅ | ✅ |
| 主题与字体 | ✅ | ✅ | ✅ |
| 双语界面 | ✅ | ✅ | ✅ |
| 概率权重 | ✅ | ✅ | ✅ |
| Bing 壁纸 | ✅ | ✅ | ✅ |
| 单实例守护 | ✅ | ✅ | ✅ |
| 退出还原 | ✅ | ✅ | ✅ |
| 开机自启 | ✅ | ✅ | ✅ |
| 全局热键 | ✅（含焦点防误触） | 🟡（pynput，无焦点防误触） | 🟡（pynput，无焦点防误触） |
| HTML 壁纸 (WorkerW) | ✅ | ❌ | ❌ |
| 鼠标穿透 | ✅ | 🟡 | ❌ |
| 配置迁移 | ✅ | ✅ | ✅ |
| 视频壁纸 (mpv) | ✅ | 🟡 | 🟡 |

图例：✅ 支持 · 🟡 部分 · ❌ 暂未

---

## 🚀 快速开始 / Quick Start

### Windows

1. 下载 [Release](https://github.com/purrfecto114-lgtm/ShangBackground/releases) 并解压至非系统目录
2. 运行 `ShangBackground.exe`
3. 桌面右键即可使用"上一个桌面背景"菜单

### Linux

```bash
git clone https://github.com/purrfecto114-lgtm/ShangBackground.git
cd "上一个桌面背景 - 源代码"

python3 -m pip install -r "Linux.ver(beta)/requirements-linux.txt"
python3 -m pip install pynput || true  # 可选：全局热键

python3 "Linux.ver(beta)/src/main.py"
```

### macOS

```bash
git clone https://github.com/purrfecto114-lgtm/ShangBackground.git
cd "上一个桌面背景 - 源代码"

python3 -m pip install -r "MacOS.ver(alpha)/requirements-macos.txt"
python3 -m pip install pynput || true  # 可选：全局热键（需授予辅助功能权限）

python3 "MacOS.ver(alpha)/src/main.py"
```

> 💡 `psutil` 与 `pynput` 为可选依赖：缺少 `psutil` 时仅跳过旧进程清理；缺少 `pynput` 时仅禁用全局热键。

---

## 🔨 从源码构建 (Nuitka) / Building from Source

### 前置依赖

```bash
# Python 3.10+
python --version

# 安装构建依赖
pip install -r scripts/requirements-build.txt

# C 编译器
# Linux: apt install gcc patchelf
# macOS: xcode-select --install
# Windows: Nuitka 自动下载 Zig
```

---

## 📂 项目结构 / Project Structure

```
ShangBackground-1.4.0/
├── Windows.ver/                # Windows 源码树
│   ├── src/                    # PySide6 源码
│   ├── build_gui.py            # GUI 构建器
│   ├── build_nuitka.py         # Nuitka CLI 驱动
│   └── requirements-windows*.txt
├── Linux.ver(beta)/            # Linux 源码树
├── MacOS.ver(alpha)/           # macOS 源码树
├── tests/                      # 18 个冒烟测试
├── tools/                      # 5 个工程化脚本
├── CHANGES-v1.4.0.md           # 完整变更日志
├── GETTING_MPV.md              # mpv 打包说明
├── README.md
├── LICENSE                     # GPLv3
└── NOTICE                      # 第三方声明
```

---

## 👥 贡献者 / Contributors

| 贡献者 | 贡献内容 |
|---|---|
| [小小电子xxdz](https://space.bilibili.com/) | 项目创始人、Windows 原版 |
| [@purrfecto114-lgtm](https://github.com/purrfecto114-lgtm) | Fork 维护、PySide6 重构、Linux 支持、v1.4.0 质量硬化 |

---

## 🤝 贡献 / Contributing

1. Fork 仓库
2. 创建分支：`git checkout -b feat/my-feature`
3. 约定式提交：`feat: add X`、`fix: resolve Y`
4. 提交 Pull Request

### 需要帮助的领域
- ⌨️ Linux/macOS 全局热键完善
- 🍎 macOS 鼠标穿透（AUD-007）
- 🌐 更多 i18n 语言（日语、韩语、西班牙语）

---

## ⚠️ 授权说明 / License

- **源代码**: [GPLv3](LICENSE) — 可自由修改与分发，衍生作品须保持相同许可
- **图像素材** (`/img/`): 由 **小小电子xxdz** 创作，**保留所有权利**，不包含在 GPLv3 范围内

---

## 🔗 相关链接 / Links

- 🌐 官网: [xxdz-official.github.io](https://xxdz-official.github.io/)
- 📺 Bilibili: [小小电子xxdz](https://space.bilibili.com/)
- 💻 上游仓库: [xxdz-official/ShangBackground](https://github.com/xxdz-official/ShangBackground)
- 🍴 当前仓库: [purrfecto114-lgtm/ShangBackground](https://github.com/purrfecto114-lgtm/ShangBackground)
- 📦 最新发布: [v1.4.0](https://github.com/purrfecto114-lgtm/ShangBackground/releases/latest)

---

<p align="center">Made with ❤️ by ShangBackground Team · 上一个桌面背景</p>
