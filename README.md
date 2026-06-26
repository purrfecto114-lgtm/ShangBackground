<h1 align="center">
  <img src="https://xxdz-official.github.io/ShangBackground/img/LOGO.png" width="80" height="80" alt="Logo"><br>
  上一个桌面背景 / ShangBackground
</h1>

<p align="center">
  <b>恢复经典"上一个桌面背景"右键菜单，支持多平台与现代化壁纸管理</b><br>
  <b>Restore the classic "Previous Desktop Background" menu with modern wallpaper management</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Windows-Stable-blue?logo=windows">
  <img src="https://img.shields.io/badge/Linux-Beta-orange?logo=linux">
  <img src="https://img.shields.io/badge/macOS-Alpha-lightgrey?logo=apple">
  <img src="https://img.shields.io/badge/License-GPLv3-blue">
  <img src="https://img.shields.io/badge/Python-3.x-blue?logo=python">
</p>

---

## ✨ 功能特性 / Features

| 功能 / Feature | 说明 / Description |
|---|---|
| 🖱️ 右键菜单 / Right-click Menu | 恢复 Windows 经典"上一个桌面背景"菜单 / Restore classic Windows menu |
| 🎬 切换动画 / Transitions | 多种壁纸切换动画 / Multiple wallpaper transition animations |
| 🎨 主题与字体 / Theming | **自定义字体、主题颜色、应用内 DPI 调整** / **Custom font, theme color, in-app DPI scaling** |
| 🔔 更新渠道 / Update Channels | **支持多更新源切换** / **Multiple update source switching** |
| 🌐 双语界面 / Bilingual UI | 中英实时切换，无需重启 / Switch between Chinese & English without restart |
| 🎲 概率分配 / Probability | 滑块+数值双控，壁纸随机权重精准分配 / Slider + numeric dual control for wallpaper weights |
| 🧠 Bing 壁纸 / Bing Wallpapers | 自动同步 Bing 每日壁纸，自适应分辨率 / Auto-sync Bing daily wallpapers with adaptive resolution |
| 🛡️ 单实例守护 / Single Instance | 进程级锁，重复启动自动唤起主窗口 / Process-level lock prevents duplicate instances |
| 🔄 退出还原 / Restore on Exit | 关闭程序自动恢复原始壁纸 / Auto-restore original wallpaper on exit |
| 🚀 开机自启 / Auto-start | 支持 Windows 与 macOS 开机启动 / Boot auto-start on Windows & macOS |
| 📦 配置迁移 / Config Migration | 首次启动自动迁移旧配置至 `%LOCALAPPDATA%` / Auto-migrate legacy configs on first run |

---

## 🖥️ 平台支持 / Platform Support

| 平台 / Platform | 状态 / Status | 说明 / Notes |
|---|---|---|
| Windows | ✅ Stable | 完整功能：注册表、右键菜单、托盘、自启 / Full features |
| Linux | 🧪 Beta | `gsettings` / `xfconf` / `feh` 三后端 / Three backends supported |
| macOS | ⚠️ Alpha | `osascript` + `LaunchAgent`，欢迎反馈 / Feedback welcome |

---

## 🚀 快速开始 / Quick Start

### Windows
1. 下载 [Release](https://github.com/purrfecto114-lgtm/ShangBackground/releases) 并解压至非系统目录
2. 运行 `ShangBackground.exe`
3. 桌面右键即可使用"上一个桌面背景"菜单

### macOS / Linux
```bash
git clone https://github.com/purrfecto114-lgtm/ShangBackground.git
cd "上一个桌面背景 - 源代码"
python3 -m pip install pillow requests numpy pystray psutil pyside6
python3 main.py
```

> 💡 `psutil` 为可选依赖，未安装时仅跳过旧进程清理。  
> 💡 `psutil` is optional; without it, old process cleanup is skipped.

---

## 👥 贡献者 / Contributors

| 贡献者 / Contributor | 贡献内容 / Contribution |
|---|---|
| [小小电子xxdz](https://space.bilibili.com/) | 项目创始人、Windows 原版 / Founder & original Windows version |
| [@purrfecto114-lgtm](https://github.com/purrfecto114-lgtm) | Fork 维护、PySide6 重构、Linux 支持 / Fork maintenance, PySide6 refactor, Linux support |

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

<p align="center">Made with ❤️ by ShangBackground Team</p>
