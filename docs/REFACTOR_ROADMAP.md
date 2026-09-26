# main_window.py 拆分路线图（v1.6.1 制定）

## 现状

`src/ui/main_window.py` 约 8400 行，是项目公认的 God Object。实际内部结构
比行数暗示的更有层次：

| 区间（约） | 内容 |
|---|---|
| 101–151 | `_TouchScrollFilter`（触摸滚动事件过滤器） |
| 152–7441 | `_SharedShangBackgroundWindow(QMainWindow)` — 共享基类，全部主要逻辑 |
| 7443–7445 | `_LinuxMainWindowMixin` — 空基线（Linux 行为即共享行为） |
| 7447–~8327 | `_WindowsMainWindowMixin` — 图标、样式表、自启动等 Windows 覆盖 |
| ~8328–8340+ | macOS mixin（`set_auto_start` / `restart_as_admin` 等 Cocoa 路径）与最终组合类 |

共享基类内部的可辨识职责块（按方法聚类，行号为 v1.6.1 快照）：

| 职责块 | 约行区间 | 规模 | 独立性评估 |
|---|---|---|---|
| SVG 图标渲染 | 241–428 | ~190 | 高：纯 QPixmap 计算，无业务状态 |
| 主题/样式表 | 906–1824 | ~900 | 高：颜色/QSS 计算，读少量主题配置 |
| UI 构建 + 标签页 | 1825–3872 | ~2050 | 中：Widget 构建，靠 `self` 属性互通 |
| 恢复出厂/局部重置 | 3053–3240 | ~190 | 中：跨块协调（历史/热键/外观/托盘/日志/右键菜单） |
| 语言切换与重建 | 3255–3341 | ~90 | 中：触发整窗重建 |
| 日志查看器 | 3873–4448 | ~575 | 高：文件读取 + 自身刷新，几乎不碰其他块；注：区间内嵌有 About 动画（4252–4322）与通用 eventFilter/open_url（4323–4355），提取前需先剥离 |
| About 精灵图动画 | 4252–4322 | ~70 | 高：纯展示动画（嵌在日志区间内，独立提取） |
| core worker 调度 | 4689–4836 | ~150 | 低：与 engine 事务核心交互 |
| 模式切换编排 | 4837–4972 | ~140 | 低：同上，事务边界敏感 |
| 各模式 GUI 处理器 | 4979–5478 | ~500 | 中：`core.switch_wallpaper_mode` 的参数收集器 |
| 热键录制/冲突检测 | 5479–5700 | ~220 | 中：pynput/Portal 交互 + 设置持久化 |
| 设置对话框管理 | 5739–5918 | ~180 | 中 |
| 自启动 | 5918–6113 | ~200 | 中：三平台差异大（mixin 已分层） |
| 托盘 | 6113–6320 | ~210 | 中 |
| 预览/历史/收藏 | 6320–6662 | ~340 | 中 |
| Bing | 6662–6995 | ~330 | 中：worker + 列表 + 启动任务 |
| 更新检查 UI | 6995–7194 | ~200 | 中：与 `services/updates.py` 对接 |
| 退出流程 | 7328–7441 | ~115 | 低：退出事务（ExitService 编排），风险敏感 |

## 为什么现在不做全量拆分（本轮决策记录）

1. **无真机 GUI 验收手段**：本项目当前修复轮次在无显示环境执行，8400 行
   规模的成员迁移一旦破坏信号连接或 `self` 属性契约，静态检查与无头测试
   无法覆盖 Widget 运行时行为；回归会直接打到用户脸上。
2. **测试基线的价值高于结构美观**：现有 330+ 项测试（含模式切换事务、
   IPC、退出事务）在当前文件布局上是绿的。大规模移动代码会使"测试通过"
   与"行为不变"之间的置信链断裂。
3. **拆分本身的收益是渐进的**：本路线图的阶段 1 只动"高独立性"块，即使
   只完成阶段 1，文件也可缩减约 750 行并建立可复用的拆分模式。

## 拆分原则（每一阶段都必须遵守）

1. **行为不变**：只移动代码 + 显式传参/信号连接，不顺手"改进"逻辑。
2. **Controller 不持有 Widget 父引用**：提取块通过 Qt Signal 回报、通过
   构造参数接收所需 Widget 引用，禁止 `window.xxx` 反向抓取。
3. **每块迁移伴随回归测试**：现有源码契约测试（如 `_method_source` 文本钉）
   随代码同步更新；每阶段结束跑全量 pytest + 目标平台真机冒烟
   （启动 → 切换三种模式 → 打开设置 → 托盘 → 退出恢复壁纸）。
4. **一个 PR 一个块**：可回滚、可审阅。

## 阶段计划

### 阶段 1 — 零风险展示块（预计 -750 行）

- **1a. 日志查看器** → `src/ui/controllers/log_viewer.py`：
  移动 3873–4448 的 `_log_tab` 一族（构建/读取/刷新/导出/清理/自动刷新/
  剪贴板/日志文件管理）。接口：`LogViewerController(...)`，对 `main_window`
  只暴露 `refresh()`、`clear()` 与 `export_requested` 信号。**前置剥离**：
  区间内嵌有 About 动画（4252–4322）与通用 `eventFilter`/`open_url`/
  `_handle_about_link`（4323–4355），须随 1b 或先行单独摘出，禁止连带迁移。
- **1b. About 页动画** → 并入 1a 同级 `about_sprite.py`：4252–4322 的
  精灵图混合/交叉淡入。
- **1c. SVG 图标渲染** → `src/ui/icon_rendering.py`：241–428。
  模块级函数化（`render_svg_to_pixmap(path, color)`），无状态。

### 阶段 2 — 主题系统（预计 -900 行，中风险）

- 906–1824 的颜色角色、QSS 生成、`_rebuild_stylesheet` 提取为
  `src/ui/theme_manager.py`（`ThemeManager(window)` 只依赖窗口的少数主题
  配置 getter）。
- Windows/macOS mixin 中的 `_extra_theme_qss` 覆盖同步迁移。
- 风险点：`_refresh_styled_widgets`（1643）遍历窗口控件树——保留在
  main_window，ThemeManager 只负责生成 QSS 字符串。

### 阶段 3 — 独立功能 Controller（预计 -1300 行，中风险）

- **3a. Bing**：6662–6995 → `bing_controller.py`（worker 编排 + 结果信号）。
- **3b. 热键设置**：5479–5700 → `hotkey_controller.py`。
- **3c. 更新检查 UI**：6995–7194 → `update_controller.py`
  （对接 `services/updates.py` 的 UpdateChecker）。
- **3d. 托盘**：6113–6320 → `tray_controller.py`。

### 阶段 4 — 高风险核心（仅在真机验收轮之后）

- core worker 调度（4689–4836）与模式切换编排（4837–4972）：与
  `WallpaperModeService` 事务边界强耦合，迁移必须在 Windows/Linux 真机
  冒烟掩护下进行。
- 退出流程（7328–7441）：与 `ExitService` 事务顺序耦合，单独最后处理。

### 完成态目标

`main_window.py` 保留：`_build_ui` 的 Widget 构建、Signal/Slot 连接、
Controller 生命周期管理、`update_control_states` 一类的状态展示 —— 约
3000–4000 行，符合"窗口即布局与装配"的边界。

## 门禁与前置条件

- 阶段 1/2 的每一步：pytest 全绿 + 至少一个桌面环境（Windows 或 Linux X11）
  真机冒烟通过。
- 阶段 3：Windows + Linux 双平台冒烟。
- 阶段 4：三平台矩阵冒烟（含 macOS 权限弹窗路径）。
- 任何阶段失败即回滚该 PR，不携带半迁移状态进入下一阶段。

## 后续架构项（v1.6.2 审查建议，未排期）

- **Linux 后端选择器统一**（REVIEW_REPORT_V1.6.1 建议项 2）：当前
  `capabilities.py`（能力探测）、`video.py::_wayland_layer_shell_session`
  （启动路径）与 `integration.py::_is_kde_session`（静态壁纸链）各自判定
  KDE/Wayland。目标：先产生统一的 `CapabilityStatus`，再由启动器消费，
  消除"诊断说 ready、启动器只说可尝试"的分裂状态。前置条件：KDE 真机
  矩阵（`docs/KDE_SUPPORT_PLAN.md` 任务 5）提供各状态的实测锚点。
- **KDE 插件级会话恢复（schema=3）**：按 containment 保存 plugin、
  配置组、Image/FillMode 与输出关联（`docs/KDE_SUPPORT_PLAN.md` 任务 2）。
  v1.6.2 已交付降级声明与 doctor 探测，schema=3 为其替代目标。
