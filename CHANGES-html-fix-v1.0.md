# ShangBackground HTML 壁纸 — 历史变更日志 / Historical HTML Wallpaper Changelog

> 本目录是 HTML 壁纸子系统的专项变更日志，按里程碑版本号命名。
> 1.4.0 起，HTML 壁纸与主版本同步发布，主变更日志见 [`CHANGES-v1.4.0.md`](./CHANGES-v1.4.0.md)。

---

## v1.0 — 初始嵌入（v3 .. v5 演进期）

> 时段：v1.3.x

- **v3**：在 `platform_adapters/html_wallpaper.py` 中以 300 行 `if/else` 实现 Windows WorkerW 嵌入。WorkerW 探测使用 `EnumWindows` + `FindWindowW("WorkerW", ...)`，失败回退 `Progman + 0x052C` 消息。
- **v4**：把 Chromium 抗冻结参数集中到 `_apply_chromium_anti_freeze_flags()`；新增 `qt.svg` 警告收集到 `error.log`。
- **v5**：拆分 HTML 壁纸调度与 HTML 壁纸子进程入口；新增 `--internal-html-wallpaper-runner` 内部标志。
- **v6（v1.4.0）**：见 `CHANGES-v1.4.0.md` 的「HTML 交互式壁纸」与「Bug 1‑22」章节。

---

## 跨平台首帧门控 / First‑frame gate

> 平台：`platform_adapters/html_wallpaper.py::wait_for_first_frame()`

- **v1.0**：QTimer 1.5 秒硬等待 — 用户在低性能机器上看到白屏或黑屏。
- **v1.1（计划）**：QWebEnginePage `loadFinished` 信号 + 1.0s 上限；缺失时回退 QTimer。
- **v1.1.1（计划）**：截图对比首帧 hash 与上次的差，超过阈值才视为「可显示」。

详见 `tests/smoke_html_first_frame_gate.py`。

---

## 桌面可见性自动暂停 / Desktop visibility auto‑pause

> 平台：`platform_adapters/desktop_visibility.py` + `platform_adapters/html_wallpaper.py`

- **v1.0（v1.4.0 引入）**：24×14 网格估算桌面可见面积，> 5% 继续渲染；OPAQUE_ALPHA_THRESHOLD=0.95 过滤半透明窗口。
- **v1.0.1（计划）**：Hysteresis 防止可见面积在阈值附近抖动时频繁启停。
- **v1.1（计划）**：Windows 接入 `DWM_THUMBNAIL` API，估算误差从 5% 降到 1%。

详见 `tests/smoke_linux_html_x11_autopause.py`、`tests/smoke_linux_x11_visibility.py`。

---

## 中文字符 / Unicode 文件名支持

> 平台：`platform_adapters/run_html_wallpaper.py` + `app/paths.py`

- **v1.0（v1.4.0 引入）**：在 `_resolve_local_path()` 中先把路径喂给 `QUrl.fromLocalFile`，再把 `toString()` 直接交给 `QWebEngineView.setUrl()`。Bug 9/10 修复后，Windows 共享路径、中文路径、含 `&` 的路径均能正确加载。
- **v1.0.1（计划）**：增加 `os.path.normpath` 后的回退路径，在 PySide6 6.5 以下仍可工作。

详见 `tests/smoke_linux_html_unicode_runtime.py`。

---

## 已知限制 / Known Limitations

- **Wayland HTML 壁纸**：Wayland 协议不允许第三方应用直接嵌入到桌面图层；当前仅 X11 支持。HTML 壁纸在 Wayland 下作为「普通窗口」渲染，无法被桌面图标覆盖。
- **macOS HTML 壁纸**：macOS 没有 WorkerW 概念，UI 入口保留但实现标记为 `best_effort`。需要社区贡献者提供 `NSWindow` 子类化 + `setLevel:` 低于图标的方案。
- **Electron 应用作为壁纸**：如果用户把 `https://example.com` 设为壁纸，Electron 应用的反调试与硬件加速可能与 Chromium 抗冻结参数冲突；请用户改用静态 HTML 链接。

---

<p align="center">此目录是历史档案；新变更请直接写入 <a href="./CHANGES-v1.4.0.md"><code>CHANGES-v1.4.0.md</code></a>。</p>
