# ShangBackground v1.6.1 审查报告

## 结论（一句话）

v1.6.1 的架构分层、源码包清洁度和构建自检修复可信，但 KDE Wayland 动态壁纸仍被能力探测过度标记，KDE 非图片壁纸的会话恢复也尚未闭环，因此当前版本不应宣称“完整 KDE 支持”。

## 审查范围与方法

- 对 `ShangBackground-v1.6.1-source.zip` 解包后重新浏览 `src/`、`tests/`、`build_tools/`、`docs/`、CI 配置和版本变更记录。
- 用 superpowers 的 systematic-debugging、chinese-code-review、writing-plans 和 verification-before-completion 方法分别做根因分析、分级、计划和新鲜证据核验。
- 对 v1.6.0 → v1.6.1 的修复逐项对照；不把 CHANGELOG 当作运行证据。
- 通过网页搜索复核 mpvpaper、KDE Plasma scripting/wallpaper plugin 和 Qt 线程模型的官方资料。

## 已被新鲜证据确认的修复

| 项目 | 证据 | 判断 |
|---|---|---|
| 共享层不再直接依赖具体平台后端 | AST 扫描 `src/core`、`src/app`（排除既有 `bootstrap.py`）得到 `shared_layer_backend_import_violations: []` | 已修复 |
| 发布源码包过滤字节码 | ZIP 共 329 个条目；`*.pyc` 和 `__pycache__/` 均为 0；包含 45 个测试 Python 文件 | 已修复 |
| Python 语法 | `python -m compileall -q src build_tools tests`，退出码 0 | 通过 |
| 构建工具自检 | `python build_tools/build.py self-test` 输出 `Build-tool self-test passed.` | 通过 |
| 版本入口 | `python src/main.py --version` 输出 `1.6.1` | 通过 |
| 测试依赖声明 | `requirements/test.txt` 声明 `pytest>=8,<10`，CI 也执行 `python -m pytest -q` | 依赖已声明 |

## 必须修复

### [必须修复] KDE Wayland 的视频能力探测是误报

`src/platform_adapters/backends/linux/capabilities.py` 的逻辑在 Wayland 会话中只要桌面令牌包含 `kde`/`plasma` 且找到 `mpvpaper`，就返回：

```text
state=best_effort
runtime_ready=True
backend=mpvpaper layer-shell (KWin/wlroots best effort)
```

新鲜的直接探针复现了这个结果。该状态足以让上层把“命令存在”当成“后端可运行”，但没有验证 KWin 是否提供兼容 layer-shell、播放器是否能创建桌面层、是否有首帧或输出映射。

mpvpaper 官方项目把自身定义为面向 wlroots compositor（例如 Sway）的程序，并以 wlroots 为依赖；因此不能把普通 KDE/KWin 会话按同一后端标记为 ready。genui{"citation":{"ref":"turn5search0"}}

处理要求：

1. KDE Wayland 在没有独立、成功的 KWin/Plasma 后端探针时，`runtime_ready` 必须为 `False`。
2. `best_effort` 只能表示可尝试，不得被 UI 或发布门禁当作已支持。
3. `SHANGBACKGROUND_ALLOW_MPVPAPER=1` 只能是显式实验开关，不能改变默认能力声明。
4. 现有 `tests/test_linux_wayland_backends.py` 对 KDE+mpvpaper 断言 `runtime_ready=True`，需要先改成失败契约，再实现修正。

### [必须修复或明确降级范围] KDE 原有 wallpaper plugin/config 无法完整恢复

当前 `WallpaperBackend` 只有 `get_current()`、`set_wallpaper()` 和比例配置接口；`SessionWallpaperService` 持久化 schema=2，仅保存一个本地壁纸路径和 style。

KDE 读取脚本虽然打印了 `wallpaperPlugin`，但 `_get_kde_wallpaper()` 只返回第一个可用本地文件。设置脚本又无条件执行：

```javascript
d.wallpaperPlugin = "org.kde.image";
d.currentConfigGroup = Array("Wallpaper", "org.kde.image", "General");
```

因此，用户原本使用 `org.kde.slideshow`、`org.kde.color`、第三方 Plasma wallpaper plugin 或没有本地 `Image` 的状态时，退出恢复会丢失插件和配置。KDE 官方脚本 API 明确把每个 desktop 视为带 wallpaper plugin 的 containment，并提供 `wallpaperPlugin`、`currentConfigGroup`、`readConfig`/`writeConfig` 等能力；官方示例也展示了 `org.kde.image` 只是其中一种插件。genui{"citation":{"ref":"turn6search6"}}

二选一：

- 实现 schema=3，按 containment 保存 plugin、配置组、Image/FillMode 和输出关联，并对未知/远程状态安全降级；或
- 明确把当前功能范围限定为“本地静态图片恢复”，并在 UI、README、诊断和发布说明中说明非图片插件不会恢复。

## 建议修改

### [建议修改] 把“命令返回 0”和“桌面已显示”分成两种状态

`_set_kde_wallpaper()` 把 `plasma-apply-wallpaperimage` 或 `evaluateScript` 的返回码作为最终真相；读回失败仍返回成功。这个策略能避免 Plasma 6 的已知读回空值问题，但会把“命令接受请求”和“所有输出已显示目标图片”混为一谈。建议返回结构化结果，例如 `accepted`、`verified`、`diagnostics`、`outputs`，并让 UI 分开显示。

### [建议修改] 能力探测与启动路径共用同一个后端选择器

当前 `capabilities.py`、`video.py` 和 `integration.py` 各自判断 KDE/Wayland。建议先产生统一的 `CapabilityStatus`，再由启动器消费；这样不会出现诊断说 ready、启动器只说“可尝试”的分裂状态。

### [建议修改] 先做 KDE 静态壁纸和恢复，再单独发布动态壁纸

静态图片、热键、单实例和退出恢复可独立验收；动态视频必须有 Plasma/KWin 真机证据，不能因 `mpvpaper` 在 PATH 中而自动升级。

### [仅供参考] 线程模型文档目前比旧版本诚实

v1.6.1 已把长时工作描述为 `threading.Thread + Qt Signal`，而不是声称所有对象都通过 `moveToThread`。如果未来重构为 QObject worker，应遵循 Qt 官方的 worker + `moveToThread` + queued signal/slot 模式。genui{"citation":{"refs":["turn6search3","turn6search8"]}}

## 测试边界

本运行环境没有 `pytest`，执行 `python -m pytest -q` 的原始结果是：

```text
/opt/codex/.../python: No module named pytest
```

这不是项目测试失败，也不是测试通过；它只说明当前审查容器没有安装 `requirements/test.txt`。因此本报告没有声称全量 pytest 通过。

当前环境同时没有 `XDG_SESSION_TYPE`、`DISPLAY`、`WAYLAND_DISPLAY` 或 D-Bus session bus，无法进行 Plasma X11/Wayland 真机冒烟。KDE 结论来自源码、模拟探针和官方资料，不能替代以下真机矩阵：

- Plasma 5/X11；
- Plasma 6/X11；
- Plasma 6/Wayland；
- 单/双显示器，100%/150% 缩放；
- 本地图片、中文路径、slideshow/color/第三方 plugin；
- 启动、模式切换、崩溃/重启继承和正常退出恢复；
- 动态后端实际首帧、输出选择、暂停/音量 IPC 和停止清理。

## 交付物

- KDE 实现计划：`docs/KDE_SUPPORT_PLAN.md`
- 本审查报告：`docs/REVIEW_REPORT_V1.6.1.md`

计划文件中的任务 1–5 仍是待办，未在本次审查中实现或提交代码。
