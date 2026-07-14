# ShangBackground v1.4.0 — 变更日志 / Changelog

> **发布版本 / Release**: v1.4.0（发布日期 2026‑07）
> **上一稳定版 / Previous**: v1.3.6
> **类型 / Type**: 功能 + 修复 + 重构（无破坏性 API 变更）

本文档是 ShangBackground v1.4.0 的完整变更说明，覆盖 Windows / Linux / macOS 三大平台树与共享工具链。代码层面的修复以 `Bug N fix` 标注，对应源码中 `# Bug N fix` 的同源注释。

---

## 🎯 主题 / Highlights

v1.4.0 是「**质量 + 可移植性 + 工具链**」主题的版本：

1. **质量硬化（Quality）**：从 v1.3.6 的 18 条审计与一组 UI 截图回归中发现并修复了 **22 个** Bug（`Bug 1`‑`Bug 22`）。
2. **跨平台一致性（Parity）**：把 Windows 平台作为产品基线，把 macOS/Linux 平台的命令行解析、状态码、IPC 协议向其对齐。
3. **HTML 壁纸（HTML wallpaper）**：引入 `platform_adapters/html_wallpaper.py` + `run_html_wallpaper.py` 的 v6 重构版（WorkerW 嵌入 + Chromium 抗冻结参数），以及跨平台首帧门控、桌面可见性探测、自动暂停。
4. **构建工具链（Build tooling）**：跨平台 Nuitka 构建驱动、独立的 `bin/` 目录用于打包 mpv、`app/version.py` 单一版本源、`app/log_setup.py` 统一日志（带 Qt message handler）、`platform_adapters/capabilities.py` 运行时能力探测。
5. **可观测性（Observability）**：在应用内「日志」页可直接看到 Qt 的 `qDebug/qWarning/qCritical`，文件日志按类型拆分为 `shangbackground.log` / `html_wallpaper.log` / `error.log`，并按需开启。
6. **分发（Distribution）**：3 个平台树各自有 `build_gui.py`（GUI 包装构建）+ `build_nuitka.py`（CLI 驱动），Windows 额外提供 Inno Setup 安装包脚本占位、`requirements-nuitka.txt` 锁定构建依赖。

---

## ✨ 新增功能 / New Features

### 全平台 / Cross‑platform

- **HTML 交互式壁纸**（HTML wallpaper）— 新增 `src/app/.../config.py` 中的 `MODE_KEYS = ["幻灯片放映", "图片", "视频", "纯色", "渐变", "HTML"]`，接受 `"html" / "HTML" / "网页"` 关键字切换。Windows 端通过 WorkerW 嵌入 `QtWebEngineWidgets` 渲染本地 `.html` 或远程 URL；Linux/macOS 在 Lite runtime 下通过占位实现保留入口。详见 `GETTING_MPV.md` 与 `platform_adapters/html_wallpaper.py`。
- **应用内日志页（In‑app log page）** — `app/log_setup.py` 提供：
  - 主日志 `shangbackground.log`（DEBUG/INFO，保留 7 天滚动）
  - HTML 壁纸专用日志 `html_wallpaper.log`
  - 错误专用日志 `error.log`（保留 14 天）
  - 内存环形缓冲 1000 条 → 设置页「日志」标签即时显示，无需读盘
  - Qt message handler：`qt.svg: Cannot open file ...` 等 Qt 内部告警会进同一日志系统
- **结构化版本元数据（Version metadata）** — 新增 `app/version.py`：
  - `APP_VERSION = "1.4.0"`
  - `APP_VERSION_TUPLE = (1, 4, 0, 0)`
  - `APP_VERSION_FILE = "1.4.0.0"`
  - 三个常量同时被 `main_version_info.txt`、`app/entry.py`、`services/updates.py` 引用，避免散落的版本字符串。
- **运行时能力探测（Runtime capability probing）** — `platform_adapters/capabilities.py` 在不触发原生副作用的前提下，返回三平台 × 四种壁纸模式（`static_wallpaper` / `video_wallpaper` / `html_wallpaper` / `mouse_through`）的 `state ∈ {supported, best_effort, unsupported, unknown}`、`runtime_ready` 与 backend 描述。
- **打包 mpv 到 `bin/`** — `src/bin/` 目录用于放置平台对应的 mpv 二进制；`app/paths.mpv_bundled_exe()` 解析顺序：bundled → `shutil.which` → Windows 注册表 / 常见安装目录 → VLC 兜底。详见 `GETTING_MPV.md`。
- **GUI 构建器（`build_gui.py`）** — 每个平台树根目录的 `build_gui.py` 在不依赖任何 IDE 的前提下提供「Configure Project → Build → Run」的图形化入口，含源/构建两类运行模式切换、C 编译器探测、依赖检查。
- **CLI 构建驱动（`build_nuitka.py`）** — 每个平台树根目录的 `build_nuitka.py` 暴露 `--platform {windows,linux,macos}` × `--standalone / --onefile` × `--lto` × `--dry-run`，生成 `build/nuitka/<platform>/ShangBackground-1.4.0-*.{tar.gz,app,exe}`。

### Windows

- **WorkerW 嵌入 HTML 壁纸** — 完整支持本地 `.html` / 远程 URL 作为桌面背景。WorkerW 探测在 `platform_adapters/html_wallpaper.py` 中独立模块化；首次可见到壁纸的「首帧门控」通过 `smoke_html_first_frame_gate.py` 回归。
- **右键菜单项可见性解耦全局热键** — 修复了「关闭全局热键却连带关掉右键菜单项」的反向耦合（详见 `Bug 4`）。现右键菜单显示开关与全局热键开关彼此独立。
- **右键菜单命令 IPC 转发** — 新增 `app.support._dispatch_action_to_existing_instance()`：右键 `previous / next / random / set_wallpaper / jump / show` 命令在主进程已运行时通过 WM_COPYDATA 转发，避免重复启动 EXE；新增 `--from-context-menu` 隐藏参数用于区分用户主动启动与右键调用。
- **mpv 自动下载**（`app/mpv_download.py`）— 在 Windows 端若未检测到系统 mpv 且 `bin/mpv.exe` 也未就位，提示用户下载；下载源可配置（默认 GitHub mirror）。

### Linux

- **Wayland/全局热键提示** — `en.json` 新增「Linux 桌面环境可能需要允许键盘监听；Wayland 下可用性取决于桌面环境」的中英文提示。
- **HTML 壁纸 `capabilities` 探测** — `Linux.ver(beta)/src/platform_adapters/capabilities.py` 新增 `x11_display_available` / `webengine_wheels_present` 探测，避免在缺失 X server 的 headless 环境上崩溃。
- **X11 HTML 壁纸自动暂停（auto‑pause）** — `platform_adapters/desktop_visibility.py` + `html_wallpaper.py` 协作：当 24×14 网格中桌面可见面积 < 5% 时暂停 WebEngine 渲染，恢复后自动继续；避免后台空转烧 GPU。
- **中文文件名 HTML 真实链路测试** — `tests/smoke_linux_html_unicode_runtime.py` 在 Xvfb/Openbox 下用中文命名的本地 HTML 文件跑完整生命周期。

### macOS

- **HTML 壁纸 Lite runtime** — 通过 `capabilities.py` 标记 `html_wallpaper: best_effort`，UI 中可选择但未实现 WorkerW 嵌入（macOS 不支持 WorkerW 概念，依赖社区后续贡献）。
- **AVFoundation 依赖校验** — `platform_adapters/video.py` 启动前显式检查 `AVFoundation / AppKit / Quartz` 的 `importlib.util.find_spec()`，缺则直接 fail‑fast 并提示安装 `pyobjc-framework-AVFoundation/Cocoa/Quartz`，避免失败时报一个无上下文的边框窗口。
- **Unix socket IPC 路径加固** — socket 路径从 `/tmp/shangbg-avplayer-<pid>.sock` 改为 `${XDG_RUNTIME_DIR | TMPDIR | ~/Library/...}/ShangBackground/shangbg-avplayer-<random>.sock`，目录权限 0o700、socket 权限 0o600。
- **AVPlayer 实时音量控制路径** — `--volume-ipc` 标志从 main.py 解析、传入 `run_player(..., volume_ipc=...)`、再由 `_volume_ipc_server` 监听 socket 并把 `setMuted_/setVolume_` 命令转发到 AVPlayer 集合。
- **en.json macOS 辅助功能权限提示** — 新增「macOS 可能需要在系统设置中授予辅助功能权限」中英文。

---

## 🐛 修复的 Bug / Bug Fixes

> Bug 编号与源码中 `# Bug N fix` 注释同源。Linux 与 macOS 树的部分修复由共享代码自动应用，因此同一 Bug 在三平台均得到修复。

| 编号 | 平台 | 描述 |
|------|------|------|
| Bug 1  | All | 旧版 5:3 布局在超宽窗口下某一列过度拉伸；改为 3:2 比例 + 两列最大宽 + `Expanding/Expanding` stretch。 |
| Bug 2  | Win | 右键菜单显示开关原本是单复选框，现拆分为「启用/禁用全局右键菜单」与「启用/禁用全局热键」两个独立开关。 |
| Bug 3  | All | `QIcon(path)` 在第二次调用时旧 `QSvgRenderer` 缓存导致主题色不切换；改用 `_set_button_svg_icon()` 包装，每次重新解析并替换 `currentColor`（见 Bug 18）。 |
| Bug 4  | Linux | `app.support` 中 Wayland 短路会把文件管理器带前台而不是唤起 sidebar；删除该短路，改走统一焦点提升路径。 |
| Bug 5  | All | 主屏检测在锁屏/最小化状态下错误返回 off‑screen 坐标；增加 `app.startup` 的 fallback。 |
| Bug 6  | All | `log_enabled` 默认开启时即使未勾选也生成日志文件；改为读取 `settings.json` 决定是否 attach 文件 handler，`force=True` 防止 `core.engine` 早期 `load_config()` 抢占配置。 |
| Bug 7  | All | 「重置外观」按钮只重置了部分键，遗漏 `font_weight / font_size / performance_level`；`Bug 21` 修复后才完整。 |
| Bug 8  | All | 自动更新检查在离线 / 私网环境会卡住 UI 3 秒；`updates.py` 加 `socket.create_connection(('8.8.8.8', 53), timeout=0.5)` 的网络可达性预检。 |
| Bug 9  | All | 旧版 `QUrl.fromLocalFile(...).toString()` 在非 ASCII 路径下产生双重 percent‑encoding；改为 `QUrl.fromLocalFile(...).toLocalFile()` + 显式 percent‑encode 一次。 |
| Bug 10 | All | Bug 9 修复的回归 — `toLocalFile()` 在 Windows 共享路径上反向不工作；回退方案：判断是否在 PySide6 ≥ 6.7 上使用 `toEncoded(QUrl.FormattingOptions.FullyDecoded)`，否则保留 `toLocalFile()`。 |
| Bug 11 | All | 工具栏图标 16px 在最小窗口宽度下裁切；改为 12px；图标按钮 hover 高亮颜色从 `currentColor` 透明度 0.1 改为 0.15。 |
| Bug 12 | All | 旧版用 `setIconSize` + 直接 `QIcon(path)` 多次刷新；改为 `_set_button_svg_icon` + `QSS min/max 24px` 对齐。 |
| Bug 13 | All | 「请求终止」按钮在英文/长翻译下被挤变形；启用自动换行 + `stretch=1`。 |
| Bug 14 | All | 全局 `QSpinBox { max-width: 118px }` 让英文 `System Default` 被截断；改为 130px 并按 locale 切两套 QSS。 |
| Bug 15 | All | `requirements-nuitka.txt` 与运行时 `requirements-linux.txt` 冲突；v1.4.0 拆出 `requirements-*-full.txt`（含 mpv/QtWebEngine 构建依赖）与 `requirements-nuitka.txt`（仅 Nuitka）。 |
| Bug 16 | All | 旧版 `QComboBox` 弹出原点用 `bottomLeft()`，在右侧边缘会被裁切；改为 `mapToGlobal(rect().bottomLeft())` + 边界回退。 |
| Bug 17 | All | 重建 UI 后 `status_label` 被重置为「正在初始化界面…」并卡死；现 `recreate_ui()` 后 `QTimer.singleShot(0, ...)` 异步刷新最终状态。 |
| Bug 18 | All | `QSvgRenderer` 不解析 `currentColor` CSS 关键字；新增 `_resolve_svg_current_color()` 注入主题色 hex。 |
| Bug 19 | All | `app/entry.py` 启动阶段 import `core.engine` 会触发 `load_config()` → `log()` → 死锁；改为先 `_read_log_enabled_from_config()` 再 `configure_logging(force=True)`。 |
| Bug 20 | macOS | `subprocess` 启动 mpv 时未设置 `LSUIElement`，Dock 出现图标；改为 `LSBackgroundOnly=True`。 |
| Bug 21 | All | 中文字符串在多种控件（按钮、SpinBox、ComboBox）下被截断；按中英文字符宽度分别计算最小/最大宽度；引入 14 / 16 / 18 / 22 / 24 / 28 / 32 多档 `Bug 21` 修正行。 |
| Bug 22 | All | 拖入文件夹时 `random_probability` 重新解析耗时 200+ ms；新增 mtime 缓存 + 线程锁，重复拖入同一目录降为 < 1 ms。 |

---

## ⚡ 性能与重构 / Performance & Refactor

- **`random_probability.py`**：`_CACHE_LOCK` + `_IMAGE_CACHE` + `_WEIGHT_CACHE` 减少重复 JSON 解析与目录扫描；mtime 失效自动重建。
- **`single_instance.py`**：用户 ID 摘要从 `sha1` 改为 `sha256`，避免碰撞（详见 Windows / Linux / macOS 平台 advisory）。
- **`paths.py`**：`QUrl → local file` 解析统一到 `_resolve_svg_current_color()` + `Bug 9/10` 双保险。
- **`core/engine.py`**：导入时不再直接 `from app.config import APP_VERSION`，改为 `from app import config as _cfg` + 动态取 `config.APP_VERSION`，与 `version.py` 单一源保持一致。
- **`app/entry.py`**：启动链路显式分阶段 — 解析参数 → 探测 `log_enabled` → 初始化 logging → 加载 config → 安装 Qt message handler → 探测单实例 → 创建主窗口。新增 `_dispatch_action_to_existing_instance()` 取代硬编码的 4 行 if/else。

---

## 🛠️ 构建与打包 / Build & Distribution

### 新增 / Added

- **`<platform>/build_nuitka.py`**：跨平台 CLI 驱动，支持 `--platform`、`--standalone` / `--onefile`、`--lto`、`--dry-run`，自动探测 C 编译器（Windows: Nuitka 自带 MinGW64；Linux: `gcc` + `patchelf`；macOS: `xcode-select`）。
- **`<platform>/build_gui.py`**：在原生 PySide6 窗口内引导用户完成 Configure → Build → Run；自动写入 `.nuitka-build/` 状态。
- **`<platform>/requirements-*-full.txt`**：含 `mpv` / `QtWebEngine` / 平台可选依赖。
- **`<platform>/requirements-nuitka.txt`**：Nuitka + `zstandard` 锁定。

### 更新 / Updated

- **Windows `build_windows_nuitka.bat`**：加 `--include-data-dir=src/bin=bin` 与 `--include-module=PySide6.QtWebEngineCore/Widgets`。
- **Windows `build_windows_onedir.bat`**：加 `--add-data "src/bin;bin"`。
- **Linux `build_linux_nuitka.sh`**：加 `--include-data-dir=src/bin=bin`、`--include-module=platform_adapters.run_html_wallpaper`。
- **macOS `build_macos_onedir.sh`**：加 `--add-data "src/bin:bin"`。

### 资源文件修复 / Resource file fix

- `Windows.ver/src/main_version_info.txt`、`Linux.ver(beta)/src/main_version_info.txt`、`MacOS.ver(alpha)/src/main_version_info.txt` 的 `filevers / prodvers` 元组从 `(1, 4, 4, 0)` 修正为 `(1, 4, 0, 0)`，与 `StringStruct(u'FileVersion', u'1.4.0.0')` 对齐。`FileDescription` / `ProductName` / `CompanyName` 同步回中文（与历史 1.3.6 一致）。

---

## 🌐 国际化 / i18n

- `en.json` 总翻译键数从 1.3.6 的约 280 条扩展到 **> 350 条**。
- 新增的英文条目覆盖 HTML 壁纸、上下文菜单 IPC、全局热键开关、视频音量调整、macOS/Linux 权限提示等。
- `app/i18n.py` 加载器现同时接受 **gzip 压缩的 JSON 与普通 JSON**（历史打包步骤偶发把 `en.json` 压成 `.json.gz` 但保留 `.json` 后缀，v1.3.6 静默回退到中文）。

---

## 🧪 测试 / Tests

新增 `tests/` 目录的 19 个冒烟脚本（无需 GUI 即可运行，CI 友好）：

| 脚本 | 覆盖 |
|------|------|
| `smoke_bing.py` | 标准库 Bing 下载器 + 缓存保护 |
| `smoke_build_gui_and_video_startup.py` | 三大平台 GUI/视频启动参数 |
| `smoke_cross_platform_parity.py` | 跨平台行为一致性（Windows 为基线） |
| `smoke_feasibility.py` | `capabilities.py` 探测矩阵 |
| `smoke_functional_matrix.py` | 共享 wallpaper / 持久化 / 适配器契约 |
| `smoke_html_adapter.py` | HTML 适配器三条控制路径 |
| `smoke_html_first_frame_gate.py` | 跨平台 HTML 首帧门控 |
| `smoke_i18n.py` | 字面量翻译键完整性 + 共享措辞稳定 |
| `smoke_linux_dynamic_ui.py` | Xvfb/Openbox 真实 PySide6 UI 自动化 |
| `smoke_linux_fit_backends.py` | Linux fit‑mode 后端路由 |
| `smoke_linux_html_unicode_runtime.py` | Xvfb/Openbox + 中文 HTML 文件 |
| `smoke_linux_html_x11_autopause.py` | X11 auto‑pause 真实集成 |
| `smoke_linux_x11_visibility.py` | desktop visibility 网格 |
| `smoke_nuitka_builder_guardrails.py` | Nuitka 构建器护栏（资源文件、include‑data‑dir） |
| `smoke_platform_contracts.py` | 平台契约（CLI / IPC / 状态码） |
| `smoke_requested_fixes.py` | 22 条 Bug 修复回归 |
| `smoke_rounding.py` | 概率权重四舍五入边界 |
| `smoke_updates.py` | 更新解析 + 网络可达性预检 |

---

## 🔧 工具 / Tools

新增 `tools/` 目录的 5 个工程化脚本：

| 脚本 | 用途 |
|------|------|
| `analyze_build_size.py` | 报告独立构建的最大文件 / 目录组 |
| `audit_clean_rewrite.py` | 可重复、低资源的 clean‑rewrite 审计 |
| `benchmark_unicode_paths.py` | 基准测试 ASCII / CJK 壁纸路径在应用侧热路径的耗时 |
| `check_platform_feasibility.py` | 不触发原生副作用地打印三平台能力矩阵 |
| `run_linux_30s_soak.py` | Xvfb/Openbox 30 秒真实应用浸入测试（采样 CPU/内存、截屏） |

---

## ⚠️ 破坏性变更 / Breaking Changes

**无破坏性变更。** 桌面应用行为与 v1.3.6 v6‑step2 完全一致；本版本聚焦质量硬化、跨平台一致性、HTML 壁纸、构建工具链。

但有以下**注意事项**：

- **不再有集中的「旧」`scripts/build_nuitka.py` 与 `scripts/shangbackground.iss`**：每个平台树自带 `build_nuitka.py` 与 `build_gui.py`，按需进入对应平台目录构建。Inno Setup 安装包脚本（`scripts/shangbackground.iss`）未随 v1.4.0 发行，将在 v1.4.1 提供。
- **`main.py` 不再是"god file"**：原 v1.3.6 的 main.py 中定义的 `PreviewCanvas / QtRootShim / BingSyncWorker / ShangBackgroundWindow` 等类已迁移到 `ui/` 与 `services/` 子模块；v1.4.0 通过 `__getattr__` 惰性转发保持向后兼容（已恢复）。
- **根目录的 `index.html` / `v1.html` / `gotoBV.html` 站点页面**：在 1.4.0 中不再随源码发布；网站仍托管在 `xxdz-official.github.io/ShangBackground`。这部分请从上游 `purrfecto114-lgtm/ShangBackground` 仓库 `main` 分支获取。
- **`PROJECT_STRUCTURE.md` 新增**：被三大平台 README 引用，详尽描述 `app / core / services / platform_adapters / ui` 五层模块组织。

---

## 🔗 已知问题 / Known Issues

- **macOS HTML 壁纸**：当前为 `best_effort`，UI 中可见但未实现 WorkerW 嵌入（macOS 没有 WorkerW 概念）。需要社区贡献者提供基于 `NSWindow` 子类化 + `setLevel:` 低于图标的方案（参见 AUD‑007 / AUD‑018）。
- **Linux 全局热键焦点防误触**：当前基于 `pynput` 不区分前台应用，桌面/游戏全屏时可能误触发。Windows 版的「焦点防误触检测」暂未移植，欢迎针对 GNOME/KDE 提供补丁。
- **跨平台磁盘配额**：macOS sandbox 下 `~/Library/Containers/...` 路径在不同签名级别下表现不同；v1.4.0 默认走 `~/Library/Application Support/ShangBackground/`。
- **`bug6` 文件日志强制刷新**：每次写日志后 `flush()`，对 SD 卡用户可能增加少量写入放大；后续版本考虑 `BufferedWriter` + 定时 flush。

---

## 📦 升级指南 / Upgrade Notes

从 v1.3.6 升级到 v1.4.0：

1. **拉取新源码并覆盖安装**：`git pull` / 下载 Release zip。
2. **保留配置**：`%LOCALAPPDATA%/ShangBackground/`（Windows）/ `~/.config/ShangBackground/`（Linux）/ `~/Library/Application Support/ShangBackground/`（macOS）下的 `settings.json` 与 `random.json` 兼容，无需迁移。
3. **可选启用新功能**：
   - 打开设置 → 外观 → 「界面动画」（v1.4.0 新增 Bug 9 同期）
   - 打开设置 → 通用 → 「日志到文件」（v1.4.0 默认关闭，开启后可在「日志」标签实时查看）
   - 打开设置 → 全局热键 → 选择 HTML 壁纸（Windows only）或视频壁纸模式
4. **构建**：如使用 `requirements-windows.txt` / `requirements-linux.txt` / `requirements-macos.txt` 旧名称未变，**新增 `requirements-*-full.txt`** 用于把 mpv/QtWebEngine 一起打包。
5. **审计 / 测试**（贡献者）：`python -m tools.audit_clean_rewrite.py` + `python -m tests.smoke_cross_platform_parity` 验证改动未破坏跨平台行为。

---

## 🙏 致谢 / Credits

- **小小电子xxdz** — 项目创始人与 Windows 原版
- **[@purrfecto114-lgtm](https://github.com/purrfecto114-lgtm)** — Fork 维护、PySide6 重构、Linux 支持、v1.4.0 质量硬化
- **所有提交过 Issue / PR 的贡献者** — 见 `https://github.com/purrfecto114-lgtm/ShangBackground/graphs/contributors`

---

<p align="center">Made with ❤️ by ShangBackground Team · 上一个桌面背景 · v1.4.0</p>
