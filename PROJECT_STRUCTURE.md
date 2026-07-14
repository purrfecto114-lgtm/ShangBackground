# ShangBackground 项目结构 / Project Structure

> 三大平台树 `Windows.ver/`、`Linux.ver(beta)/`、`MacOS.ver(alpha)/` 共享同一目录布局。下文以 `Windows.ver/` 为例；`Linux.ver(beta)/` 与 `MacOS.ver(alpha)/` 完全同构，仅 `platform_adapters/`、`build_*` 与 `requirements-*.txt` 不同。

---

## 顶层 / Top level

```
ShangBackground-1.4.0/
├── README.md                     # 项目总览（中英双语）
├── LICENSE                       # GPLv3
├── NOTICE                        # 第三方声明
├── GETTING_MPV.md                # mpv 二进制打包说明
├── PROJECT_STRUCTURE.md          # ← 本文件
├── CHANGES-v1.4.0.md             # v1.4.0 完整变更日志
├── CHANGES-html-fix-v*.md        # 历史 HTML 壁纸专项修复日志
│
├── Windows.ver/                  # Windows 源码树
├── Linux.ver(beta)/              # Linux 源码树
├── MacOS.ver(alpha)/             # macOS 源码树
│
├── tests/                        # 19 个跨平台冒烟测试
├── tools/                        # 5 个工程化脚本
├── img/                          # 共享图像资源
└── fonts/                        # （如有）共享字体
```

---

## 平台源码树 / Per‑platform source tree

以 `Windows.ver/` 为例：

```
Windows.ver/
├── README.md                     # 该平台的快速上手
├── README_BUILD_Windows.md       # 该平台的详细构建说明
│
├── build_gui.py                  # PySide6 GUI 构建器
├── build_gui.pyw                 # Windows 「无控制台」启动包装
├── build_nuitka.py               # 跨平台 Nuitka CLI 驱动
├── build_windows_nuitka.bat      # 原有 Nuitka standalone 脚本
├── build_windows_nuitka_onefile.bat
├── build_windows_onedir.bat
├── build_windows_onedir_noupx.bat
├── build_windows_pyside6_deploy.bat
│
├── requirements-windows.txt      # 运行时依赖（精简）
├── requirements-windows-full.txt # 含 mpv / QtWebEngine 的全量依赖
├── requirements-nuitka.txt       # 仅 Nuitka + zstandard
│
├── fonts/                        # 平台特定字体（可选）
└── src/
    ├── main.py                   # thin entry point + LEGACY_EXPORTS
    ├── main.pyw                  # 同上（Windows 无控制台）
    ├── main_version_info.txt     # Windows 资源文件版本信息
    │
    ├── app/                      # ① 应用层
    ├── core/                     # ② 核心引擎
    ├── services/                 # ③ 外部服务
    ├── platform_adapters/        # ④ 平台适配层
    ├── ui/                       # ⑤ 用户界面
    ├── lang/                     # 语言资源
    ├── img/                      # 平台特定图标
    └── bin/                      # 平台特定二进制（mpv 等）
```

---

## `src/app/` — 应用层

平台无关的应用骨架：版本元数据、路径解析、依赖探测、启动链、i18n 加载、跨平台工具方法。

| 文件 | 职责 |
|------|------|
| `__init__.py` | 标记 `app` 为包；版本号由 `version.py` 注入。 |
| `version.py` | **单一版本源** — `APP_VERSION`、`APP_VERSION_TUPLE`、`APP_VERSION_FILE`。`main_version_info.txt` 与 `services/updates.py` 都从这里取值。 |
| `config.py` | 配置 schema 验证、`MODE_KEYS`（`幻灯片放映/图片/视频/纯色/渐变/HTML`）、`STYLE_MAP`、视频扩展名白名单、`is_supported_video_path()` 等。 |
| `paths.py` | 资源根目录解析（`RESOURCE_ROOT`）、用户数据目录（`user_data_dir`）、可执行文件路径（`app_executable_path`）、bundled mpv 解析（`mpv_bundled_exe()`）、`QUrl ↔ local file` 双向转换（Bug 9/10 双重保险）。 |
| `entry.py` | 启动链：解析参数 → 探测 `log_enabled` → 初始化 logging → 加载 config → 安装 Qt message handler → 探测单实例 → 创建主窗口。新增 `_dispatch_action_to_existing_instance()` IPC 转发。 |
| `log_setup.py` | 统一日志：主日志 / HTML 壁纸日志 / 错误日志、内存环形缓冲、Qt message handler。 |
| `dependencies.py` | 启动期依赖探测，缺失时弹安装对话框；`_module_available()` 兼容「父包缺失」场景；`pip install --user` 命令构造（Windows 优先 `py -m pip`）。 |
| `i18n.py` | 加载 `lang/<lang>.json`，**同时支持 gzip 压缩 JSON 与普通 JSON**（v1.4.0 修复旧打包步骤漏压成 `.json.gz` 的 bug）。 |
| `scaling.py` | 应用内 DPI 调整（`Qt.AA_EnableHighDpiScaling` / `setHighDpiScaleFactorRoundingPolicy`）。 |
| `startup.py` | 单实例唤起（`bring_to_front` / `flash_window`）、开机自启、首次启动检测。 |
| `support.py` | CLI 解析、`_context_command_from_args()`、`_dispatch_action_to_existing_instance()`、`get_logger()` 兼容垫片。 |
| `mpv_download.py` | **仅 Windows** — 探测并提示用户下载 mpv 二进制。 |

---

## `src/core/` — 核心引擎

应用核心：单实例守护、壁纸概率模型、显示枚举、调度引擎。

| 文件 | 职责 |
|------|------|
| `display.py` | 多显示器枚举（基于 Qt `QGuiApplication.screens()`），与主屏检测。 |
| `engine.py` | 主调度引擎：加载配置、监听文件系统变化、应用状态机、跨平台调用 `platform_adapters`。 |
| `probability_math.py` | 概率权重四舍五入与归一化（`smoke_rounding.py` 覆盖）。 |
| `random_probability.py` | `random.json` 加载、目录扫描、缓存层（`_CACHE_LOCK` + `_WEIGHT_CACHE` + `_IMAGE_CACHE`，mtime 失效），Bug 22 优化后重复拖入 < 1 ms。 |
| `single_instance.py` | 进程级锁（`flock`/`LockFileEx`/命名管道），单实例守护 + WM_COPYDATA 转发。**v1.4.0** 用户 ID 摘要从 `sha1` 改为 `sha256`。 |

---

## `src/services/` — 外部服务

与外部世界（HTTP / API / 远端）通信的服务，全部带超时与降级。

| 文件 | 职责 |
|------|------|
| `bing.py` | stdlib Bing 每日壁纸下载器（无需 `requests`/`httpx`）。 |
| `bing_sync.py` | BingSyncWorker（Qt 线程），UI 调用的接口。 |
| `updates.py` | GitHub Release 自动检查；**网络可达性预检**（`socket.create_connection(('8.8.8.8', 53), timeout=0.5)`，Bug 8）；版本解析支持 2 段 / 3 段 / `v` 前缀 / 4 段 `APP_VERSION_FILE` 兼容。 |

---

## `src/platform_adapters/` — 平台适配层

**所有平台相关代码都集中在这里。** 任何修改 Windows 注册表、`gsettings`、`osascript` 的代码都只允许出现在本目录；上层 `core`/`ui` 不准直接调用系统命令。

| 文件 | 职责 |
|------|------|
| `integration.py` | 静态壁纸设置：Windows 走 `IDesktopWallpaper` COM → 失败回退 `SystemParametersInfoW`；Linux 走 `gsettings` / `xfconf` / `feh`；macOS 走 `osascript`。 |
| `video.py` | 视频壁纸：Windows 走 mpv + WorkerW；Linux 走 `xwinwrap` + mpv（Wayland `mpvpaper`）；macOS 走 AVFoundation (`pyobjc`) + Unix socket 实时音量控制（`--volume-ipc`）。 |
| `wallpaper_cli.py` | 各平台 CLI 兜底（Windows `powershell` / Linux `feh` / macOS `osascript`）。 |
| `capabilities.py` | **v1.4.0 新增** — 运行时能力探测（`state ∈ {supported, best_effort, unsupported, unknown}`、`runtime_ready`、backend 描述），不触发原生副作用。 |
| `desktop_visibility.py` | **v1.4.0 新增** — 桌面可见性 24×14 网格估算，用于 HTML 壁纸 auto‑pause。 |
| `html_wallpaper.py` | **v1.4.0 新增** — HTML 壁纸调度（启动子进程 / 停止 / GPU 切换 / 暂停 / 恢复）。 |
| `run_html_wallpaper.py` | **v1.4.0 新增** — HTML 壁纸子进程入口（main 进程通过 `--internal-html-wallpaper-runner` 派发）。 |

### 平台子集差异

| 模块 | Windows | Linux | macOS |
|------|:---:|:---:|:---:|
| `integration.py` | COM + SPI 兜底 | gsettings/xfconf/feh | osascript |
| `video.py` | mpv + WorkerW | mpv + xwinwrap/mpvpaper | AVFoundation + mpv 兜底 |
| `wallpaper_cli.py` | powershell | feh | osascript |
| `capabilities.py` | ✅ | ✅ | ✅ |
| `desktop_visibility.py` | ✅ | ✅ | ✅ |
| `html_wallpaper.py` | ✅（WorkerW 嵌入） | ✅（X11 嵌入 + auto‑pause） | 🟡（best_effort） |
| `run_html_wallpaper.py` | ✅ | ✅ | 🟡 |

---

## `src/ui/` — 用户界面

| 文件 | 职责 |
|------|------|
| `main_window.py` | 主窗口（`ShangBackgroundWindow`）。Bug 1‑22 的 UI 修正绝大多数落在这里。 |
| `sidebar.py` | 侧边栏（壁纸历史 / 收藏 / 模式切换）。 |
| `preview_canvas.py` | 预览画布（`PreviewCanvas`）。 |
| `probability_dialog.py` | 概率权重滑块 + 数值双控对话框。 |
| `qt_root_shim.py` | Qt root shim（`QtRootShim`），用于在无 Qt 主循环场景下做最小化 root 探测。 |
| `dialog_style.py` | 共享对话框样式（按钮、SpinBox、ComboBox）。 |

---

## `src/lang/` — 国际化资源

```
lang/
├── en.json        # 英文（约 350+ 条键）
└── zh.json        # 简体中文
```

`en.json` 在 v1.4.0 中：
- 新增 HTML 壁纸相关条目（HTML 壁纸、HTML 壁纸模块不可用、HTML 文件过滤器等）
- 新增上下文菜单 IPC 相关条目
- 新增全局热键开关独立提示
- 现有条目措辞精简（避免按钮被截断，Bug 21）

`i18n.py` 同时支持 gzip 压缩 JSON 与普通 JSON。

---

## `src/bin/` — 平台二进制

放置平台特定的二进制（主要是 mpv）。`app/paths.mpv_bundled_exe()` 解析顺序：

1. `<RESOURCE_ROOT>/bin/<name>` — bundled（首选）
2. `shutil.which("mpv")` — 系统 PATH
3. **Windows only** — 注册表 `App Paths` / `open-command` 条目
4. **Windows only** — 常见安装目录（`Program Files`、scoop 等）
5. **Windows only** — VLC 兜底

详见 [`GETTING_MPV.md`](./GETTING_MPV.md)。

---

## `tests/` 与 `tools/`

详见 [`CHANGES-v1.4.0.md`](./CHANGES-v1.4.0.md#-测试--tests) 的「测试」与「工具」章节。

- `tests/` — 19 个冒烟测试，CI 友好，无需 GUI
- `tools/` — 5 个工程化脚本（构建大小分析、平台可行性检查、Linux 30s 浸入等）

---

## 添加新模块的约定 / Conventions for new modules

1. **任何平台相关代码** → `platform_adapters/`
2. **任何外部 HTTP/服务调用** → `services/`
3. **任何启动期逻辑** → `app/`
4. **任何核心状态机 / 调度** → `core/`
5. **任何 Qt 控件 / 样式** → `ui/`
6. **任何新 i18n 键** — 同时添加到 `lang/zh.json` 与 `lang/en.json`，并跑 `tests/smoke_i18n.py` 验证完整性
7. **任何新功能** — 至少配 1 个 `tests/smoke_*.py` + 在 `CHANGES-v*.md` 增补条目
8. **任何新二进制依赖** — 添加到 `bin/` 目录并更新 `GETTING_MPV.md` 之类的打包说明

---

<p align="center">📂 文档与代码不一致时，以代码为准并提 PR / Issue。</p>
