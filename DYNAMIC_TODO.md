# DYNAMIC TODO

> 项目：ShangBackground 1.4.2 研究式重构
> 更新：2026-07-24（Europe/Helsinki）
> 状态说明：结论以源码、提交、测试与官方资料为准；真机 KDE 项目单独标注。

- [x] **阶段 1：现状盘点（约 30%）**
  - [x] 解压源码并确认项目根目录
  - [x] 盘点三端视频实现、IPC、生命周期与错误传播
  - [x] 盘点 6 个 build feature 与产物映射
  - [x] 盘点 KDE/GNOME/XFCE × X11/Wayland 能力矩阵
  - [x] 盘点 JSON i18n 加载、切换与 UI 重渲路径
  - 产出：`CURRENT_STATE_AUDIT.md`

- [x] **阶段 2：多角度辩证讨论（约 15%）**
  - [x] 6 个议题各给出至少 3 个方案、权衡、推荐与反方回应
  - [x] 以 2026-07 官方资料校验 mpv/KDE/Qt/打包/Portal/CI 状态
  - 产出：`DIALECTICS.md`、`WEB_VERIFICATION.md`

- [x] **阶段 3：最小可运行实施（约 30%）**
  - [x] 引入统一 `MpvBackend` 接口且不改写 Windows WorkerW 主路径
  - [x] 增补 KDE Wayland `mpvpaper`/layer-shell 能力探测与降级
  - [x] 增补 Wayland XDG GlobalShortcuts Portal 后端与 X11 回退
  - [x] 保留 JSON `t()` API，补语言变更事件并复用现有 UI 重建
  - [x] 构建期拒绝 gzip 伪装 `.json`，运行时保留旧包恢复能力
  - [x] 将新增模块联动至 feature bundle
  - [x] 形成三个可独立回滚提交
  - 产出：`REFACTOR_PATCHES/`

- [x] **阶段 4：测试与证据（约 20%）**
  - [x] 新增 mpv、能力探测、Portal、i18n、资源与 64 组合 feature 测试
  - [x] 完整 `pytest tests/`：115 passed
  - [x] `compileall`、build self-test、CLI 版本冒烟
  - [x] 尝试安装 Qt 依赖；沙箱软件源不可用，未伪造 GUI/KDE 实测
  - [x] 提供 KDE 真机复现与截图一键脚本
  - 产出：`TEST_REPORT.md`、`SCREENSHOTS/`、`scripts/repro/`

- [x] **阶段 5：自检与风险登记（约 5%）**
  - [x] 跨平台回归、性能、安全与发布链自检
  - [x] 生成风险登记、Web 核验与维护者执行摘要
  - [x] 打包修改后的源码与全部交付物
  - 产出：`RISK_REGISTER.md`、`EXECUTIVE_SUMMARY.md`、交付 zip
