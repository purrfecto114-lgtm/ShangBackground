# Linux KDE/Plasma 支持实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不误宣称 KDE Wayland 已被通用支持的前提下，稳定交付 Plasma X11/Wayland 的静态壁纸、热键、视频能力探测和恢复事务，并为动态壁纸选择可验证的 Plasma 原生扩展路线。

**架构：** 保留 `platform_adapters.backends.linux` 作为系统边界；新增 KDE 能力探测与后端契约，使静态壁纸、会话恢复、视频壁纸和桌面层协议分别报告可验证状态。KDE Wayland 视频不再仅凭环境变量将 `mpvpaper` 标记为可用；只有检测到真实协议/后端并通过播放探针后才报告 ready。

**技术栈：** Python 3.10+、PySide6、Qt signals、KDE Plasma scripting/D-Bus、`plasma-apply-wallpaperimage`、mpv/IPC、Wayland layer-shell 或 Plasma 原生 wallpaper plugin、pytest。

**当前状态：** v1.6.1 审查轮次交付了源码审查、外部资料核验和本实现计划；任务 1–5 当时未动源码。

**v1.6.2 实施记录（2026-09）：**
- 任务 1（冻结 KDE 行为契约）已实现：`capabilities.py` 将 KDE Wayland 从
  wlroots 令牌集分离，`runtime_ready` 一律 False（best_effort 仅表示可显式
  尝试）；`video.py` 启动路径对 KDE 会话默认拒绝 mpvpaper，
  `SHANGBACKGROUND_ALLOW_MPVPAPER=1` 仅作为显式实验开关放行启动、不改能力
  声明；契约测试 `tests/test_kde_capability_contract.py`（9 项）+
  `tests/test_linux_wayland_backends.py` 断言翻转。
- 任务 2（插件状态保存/恢复 schema=3）**未实现**，按报告"二选一"选择了
  明确降级范围：`integration.kde_wallpaper_restore_scope()` 如实上报各
  containment 的 wallpaperPlugin，doctor 对 slideshow/color/第三方插件
  WARN + hint（见 `tests/test_kde_restore_scope.py`）。schema=3 仍为本
  计划任务 2 的后续目标。
- 任务 3–5 未动，复选框仍为待办。

**v1.6.3 批次 A 实施记录（Task 28-a）：**
- 任务 2（schema=3）已实现：`app/ports.py` 新增可选
  `StatefulWallpaperBackend` 协议（capture_state/restore_state）；
  `linux/integration.py` 新增 `capture_wallpaper_state()`（逐 containment
  保存 plugin/Image/FillMode，诚实规则：config_captured 仅限
  org.kde.image+读到 Image（主线程校准 R1：FillMode 可缺省——KConfig
  默认不写默认值键，缺省即缺省，恢复时不写该键，避免丢 Image），
  远程 URL 原样记录不伪造本地路径）与
  `restore_wallpaper_state()`（id→screen→全量兜底三档匹配，json.dumps
  防注入）；`bootstrap.py` 两个适配器 + `engine.py` 布线（Windows/macOS
  ImportError→None，行为不变）；`session_wallpaper_service.py` schema=3
  持久化/加载/恢复（slideshow 等非图片插件退出时可恢复插件名，内部配置
  如实声明不伪造）；schema=2 旧文件读取时按需转换（converted_from=2）。
  测试：`tests/test_kde_wallpaper_state.py`（21 项，含 R1 回归钉）+
  `tests/test_session_wallpaper_service.py`（16 项，该文件为本次新建）。
  步骤 5 的 commit 由主线程统一执行。
- 任务 3 步骤 1–2（D-Bus 前置检查）已实现：
  `_session_bus_endpoint_missing()` + `_run_plasma_script`/
  `_set_kde_wallpaper` 双入口前置检查——"无会话总线/命令缺失/Plasma 拒绝"
  三种错误可区分，无 bus 时不 spawn 任何外部命令；
  `tests/test_kde_restore_scope.py::_run_set` 补 D-Bus 环境钉住
  （CI runner 无 bus 端点，否则 rc=0 路径会被前置检查误拒）。
  步骤 3–4（按输出的恢复策略）仍为待办。

**v1.6.3 批次 B/主线程补充（Task 28-b + 28-c）：**
- 任务 5 步骤 3 的 doctor 口径收敛已实现：门面
  `platform_adapters.session.linux_video_wallpaper_capability()` 暴露
  `probe_capabilities()["video_wallpaper"]`，doctor 的
  “Wayland video embedding”检查改为消费该统一判定（KDE Wayland
  +mpvpaper 不再误显示 pass，改为 warn+实验开关指引；GNOME 等无桌面层
  后端的会话如实 warn）。测试：`tests/test_doctor_wayland_video.py`
  （11 项）。启动器侧统一（REFACTOR_ROADMAP“统一后端选择器”）仍以
  KDE 真机矩阵为前置条件，本批不动。
- doctor 的 kde-wallpaper-restore 提示文案同步 schema=3 口径（插件会被
  重选恢复，非图片插件内部配置回落默认值）。
- 任务 5 步骤 1 的矩阵文档已建：`docs/KDE_TEST_MATRIX.md`（真机验收
  项/命令级冒烟/证据记录表，供人工验收使用——项目无 KDE CI runner，
  发布门禁升级仍待真机结果）。
- 测试卫生：FakeProcess 假 pid 统一为 2^31-1（三平台均不可能是活进程，
  mock 漂移时不会误杀真实进程）。

---

### 任务 1：冻结当前 KDE 行为契约

**文件：**
- 修改：`src/platform_adapters/backends/linux/capabilities.py`
- 修改：`src/platform_adapters/backends/linux/integration.py`
- 修改：`tests/test_linux_wayland_backends.py`
- 创建：`tests/test_kde_capability_contract.py`

- [x] **步骤 1：编写失败的能力契约测试**

```python
def test_kde_wayland_does_not_claim_mpvpaper_as_verified_backend(monkeypatch):
    from platform_adapters.backends.linux import capabilities

    monkeypatch.setattr(capabilities, "_has", lambda name: name == "dbus_next")
    env = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}
    result = capabilities.probe_capabilities(env, which=lambda name: "/usr/bin/mpvpaper" if name == "mpvpaper" else None)
    assert result["video_wallpaper"]["runtime_ready"] is False
    assert result["video_wallpaper"]["state"] in {"best_effort", "unsupported"}
    assert "verified" not in str(result["video_wallpaper"]).lower()
```

- [x] **步骤 2：运行测试验证当前行为暴露问题**

运行：`PYTHONPATH=src python -m pytest tests/test_kde_capability_contract.py -q`

预期：当前 v1.6.1 失败，因为 KDE + `mpvpaper` 会被标记为 `runtime_ready=True`。

- [x] **步骤 3：实现最小状态修正**

将 KDE Wayland 的 `video_wallpaper.runtime_ready` 改为 `False`，除非存在独立的 Plasma/KWin 后端探针返回成功；保留 `best_effort` 状态和用户可见原因。

- [x] **步骤 4：运行测试验证通过**

运行：`PYTHONPATH=src python -m pytest tests/test_kde_capability_contract.py tests/test_linux_wayland_backends.py -q`

预期：新增契约测试和已有 Wayland 测试全部通过。

- [x] **步骤 5：Commit**

```bash
git add src/platform_adapters/backends/linux/capabilities.py src/platform_adapters/backends/linux/integration.py tests/test_kde_capability_contract.py tests/test_linux_wayland_backends.py
git commit -m "fix(linux): 不再把 KDE Wayland mpvpaper 标记为已验证"
```

### 任务 2：保存和恢复 KDE 壁纸插件状态

**文件：**
- 修改：`src/app/ports.py`
- 修改：`src/app/session_wallpaper_service.py`
- 修改：`src/platform_adapters/backends/linux/integration.py`
- 修改：`src/core/engine.py`
- 测试：`tests/test_session_wallpaper_service.py`
- 创建：`tests/test_kde_wallpaper_state.py`

- [x] **步骤 1：编写失败测试，覆盖非图片插件**（v1.6.3 批次 A：
  `tests/test_kde_wallpaper_state.py::test_kde_snapshot_preserves_plugin_and_config`
  等全套；接口名按实施规格定为 `capture_wallpaper_state`/`restore_wallpaper_state`）


```python
def test_kde_snapshot_preserves_plugin_and_config():
    from platform_adapters.backends.linux import integration

    state = integration.get_wallpaper_state_platform()
    assert state["plugin"]
    assert isinstance(state["config"], dict)
```

- [x] **步骤 2：运行测试验证当前接口缺失**

运行：`PYTHONPATH=src python -m pytest tests/test_kde_wallpaper_state.py -q`

预期：失败并报告 `get_wallpaper_state_platform` 尚未定义。

- [x] **步骤 3：实现状态端口**（实施为可选协议 `StatefulWallpaperBackend`，
  不动 `WallpaperBackend` 的 runtime_checkable 契约）

为 `WallpaperBackend` 增加可选的 `capture_state()` / `restore_state()` 能力；KDE 实现通过 Plasma scripting 读取每个 containment 的 plugin、Image、FillMode 和必要的 config group。旧的仅路径接口继续保留作为兼容回退。

- [x] **步骤 4：加入 schema 迁移测试**（`converted_from=2` 转换 +
  远程/未知来源不伪造为本地文件）

使用 `schema=3` 保存 KDE 状态；读取 `schema=2` 时把旧路径转换成 `org.kde.image` 状态；非本地图片、远程来源和未知插件不得被伪造为可恢复本地文件。

- [ ] **步骤 5：运行通过并提交**（commit 由主线程统一执行）

运行：`PYTHONPATH=src python -m pytest tests/test_session_wallpaper_service.py tests/test_kde_wallpaper_state.py -q`（v1.6.3 实测：37 passed——R1 校准补钉后 21+16）

```bash
git add src/app/ports.py src/app/session_wallpaper_service.py src/platform_adapters/backends/linux/integration.py src/core/engine.py tests/test_session_wallpaper_service.py tests/test_kde_wallpaper_state.py
git commit -m "feat(kde): 保存并恢复 Plasma 壁纸插件状态"
```

### 任务 3：稳定 Plasma 静态壁纸和多显示器行为

**文件：**
- 修改：`src/platform_adapters/backends/linux/integration.py`
- 修改：`src/platform_adapters/backends/linux/capabilities.py`
- 修改：`docs/ARCHITECTURE.md`
- 测试：`tests/test_linux_wayland_backends.py`
- 创建：`tests/test_kde_static_wallpaper_contract.py`

- [x] **步骤 1：测试 D-Bus、命令和无会话三种路径**（v1.6.3 批次 A：
  `test_dbus_precheck_blocks_before_spawning_commands` 三情形 +
  `_run_set` 命令成功/拒绝路径 + `test_restore_failure_reports_plasma_rejection`；
  plasma-apply 成功路径由 `tests/test_kde_restore_scope.py` 既有 outcome 测试覆盖）

测试必须分别模拟：`plasma-apply-wallpaperimage` 成功、qdbus 脚本成功、D-Bus 不可用。每个测试断言返回值、诊断信息和是否允许回滚。

- [x] **步骤 2：实现会话 bus 前置检查**（`_session_bus_endpoint_missing()` +
  `_run_plasma_script`/`_set_kde_wallpaper` 双入口；无 bus 不 spawn 任何命令）

- [ ] **步骤 3：实现按输出的恢复策略**

静态图片可对全部输出设置；如果用户选择按显示器设置，必须使用明确的 containment/output 映射，并在无法映射时拒绝部分成功。

- [ ] **步骤 4：运行测试和静态检查**

运行：`PYTHONPATH=src python -m pytest tests/test_linux_wayland_backends.py tests/test_kde_static_wallpaper_contract.py -q`；预期 0 failures。

### 任务 4：选择 KDE Wayland 动态壁纸路线

**文件：**
- 创建：`src/platform_adapters/backends/linux/kde_dynamic.py`
- 修改：`src/platform_adapters/backends/linux/video.py`
- 修改：`src/platform_adapters/backends/linux/capabilities.py`
- 修改：`requirements/linux-video.txt`
- 创建：`tests/test_kde_dynamic_backend.py`
- 修改：`docs/GETTING_MPV.md`

- [ ] **步骤 1：先写后端选择测试**

测试三种结果：Plasma 原生 wallpaper plugin 可用、仅有 wlroots layer-shell、无后端。KDE 会话不得自动选择 wlroots 专用程序作为“已支持”后端。

- [ ] **步骤 2：定义 Plasma 原生后端协议**

后端接口固定为：`probe() -> CapabilityStatus`、`start(path, options) -> BackendResult`、`stop() -> BackendResult`、`set_option()`、`is_running()`。启动必须验证窗口/插件已注册，停止必须等待资源释放。

- [ ] **步骤 3：实现最小可交付范围**

第一版只交付 Plasma Wayland 的能力探测和静态降级；动态视频若没有经过真机协议验证，必须返回结构化“不支持”，不能通过 `SHANGBACKGROUND_ALLOW_MPVPAPER=1` 绕过为默认支持。

- [ ] **步骤 4：运行后端契约测试**

运行：`PYTHONPATH=src python -m pytest tests/test_kde_dynamic_backend.py tests/test_platform_runtime_regressions.py -q`。

### 任务 5：KDE 真机验收和发布门禁

**文件：**
- 修改：`.github/workflows/ci.yml`
- 修改：`docs/RELEASE_PROCESS.md`
- 创建：`docs/KDE_TEST_MATRIX.md`
- 测试：`tests/test_build_feature_matrix.py`

- [ ] **步骤 1：建立矩阵**

覆盖 Plasma 5/X11、Plasma 6/X11、Plasma 6/Wayland、单显示器、双显示器、缩放 100%/150%、本地图片、中文路径、原生插件和退出恢复。

- [ ] **步骤 2：加入命令级冒烟**

每个 KDE runner 执行 `--version`、`--doctor-json`、静态壁纸设置/读回、模式切换回滚、退出恢复；动态后端必须记录协议、输出和实际首帧。

- [ ] **步骤 3：发布门禁**

只有静态壁纸、恢复事务、热键和单实例在 Plasma X11/Wayland 均通过时，才将对应能力标为 supported；动态壁纸单独发布，不因静态通过而自动升级状态。

同时统一 doctor 口径：既有检查项 "Wayland video embedding"（mpvpaper 命令存在性）在 KDE Wayland 会话显示 pass，与 v1.6.2 的能力口径（KDE 不默认 ready）不一致，应在本任务一并收敛。

- [ ] **步骤 4：运行完整验证**

运行：`python -m compileall -q src build_tools tests`、`python build_tools/build.py self-test`、`python -m pytest -q`，并保存 KDE 真机日志和视频首帧证据。

---

## 复查清单

- [ ] 共享层没有新增 `platform_adapters.backends.*` 直接导入。
- [ ] KDE Wayland 不再仅凭桌面变量和 `mpvpaper` 宣称已支持。
- [ ] KDE 原有 wallpaper plugin/config 可以在退出时恢复。
- [ ] D-Bus 不可用、命令缺失和后端拒绝均有不同诊断。
- [ ] 静态、视频、HTML、热键能力状态互相独立。
- [ ] 所有“通过/完成”结论都有新鲜命令或真机证据。
