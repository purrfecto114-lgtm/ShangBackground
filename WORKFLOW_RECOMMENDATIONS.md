# GitHub Actions / Dependabot 审计建议

- 审计日期：2026-07-24
- 状态：**仅建议，未实施**
- 已核对文件：`.github/workflows/ci.yml`、`release.yml`、`codeql.yml`、`dependency-review.yml`、`.github/dependabot.yml`

## 执行摘要

Workflow 的权限和并发控制总体合理，当前使用的 `actions/checkout@v7`、`actions/setup-python@v7`、`actions/upload-artifact@v7`、`actions/download-artifact@v8` 也是截至审计日有效的现行大版本，不需要降级。主要缺口不是 action 版本，而是 CI 只跑 mock/纯 Python 测试，没有真正安装并启动 GUI/runtime；Release 对冻结产物、Linux 共享库 ABI、自包含性与供应链证明的验证不足。

## P0：建议优先实施

### 1. CI 增加真实 Linux GUI/runtime 冒烟

现状：

- `ci.yml` 的 tests job 安装了 Xvfb，但仅安装 pytest，然后直接运行测试。
- 未安装 PySide6、Pillow、`dbus-next`、`pynput` 或目标 feature 依赖。
- Xvfb 实际未被使用。

建议：

- 保留轻量矩阵；额外增加一个 Linux runtime lane，安装基础 GUI + hotkeys 依赖。
- 执行 `xvfb-run -a python src/main.py --doctor-json`。
- 增加一个可自动退出的 GUI service smoke，验证 `QApplication`、主窗口构造、托盘初始化与退出清理。
- Wayland Portal 不应在普通 Xvfb lane 中伪测；另行提供 KDE/Wayland 真机或容器 job。

### 2. Release 对冻结产物做启动验证

现状：Release 构建前的 native tests 同样只安装 pytest；构建后直接归档，没有对最终可执行文件执行 `--version`、`--doctor-json` 或 GUI 冒烟。

建议：

- 构建完成后，从 `.dist`/`.app`/Windows standalone 中运行最终入口的 `--version` 和 `--doctor-json`。
- Linux 用 Xvfb 执行最小 GUI 启停。
- 失败时上传 doctor JSON、播放器日志、依赖扫描结果和 build manifest。
- 把冻结运行时验证封装进 build tool，避免 Workflow 与本地逻辑分叉。

### 3. “full” 产物语义与 mpv runtime 保持一致

现状：`release.yml` 的 full profile 对全部平台使用 `--mpv-runtime system`，注释还说明 Windows 用户必须自行安装 MPV。

问题：产物名为 full，但视频核心 runtime 可能不自包含，用户预期容易错位。

建议二选一：

- Windows full 正式 bundle 经过校验的 libmpv runtime；或
- 将产物/feature 明确命名为 external-mpv，并在安装器和首启 doctor 中显著提示。

### 4. Linux ABI/SONAME 兼容性验证

现状：Linux release 在 Ubuntu 24.04 构建并使用系统 `libmpv2`。这可能使产物依赖构建机的 SONAME、glibc 或其他共享库版本。

建议：

- 在最老支持发行版容器上构建或至少运行产物；
- 对最终二进制执行 `ldd`/`readelf -d` 并把结果作为制品；
- 使用干净容器执行启动冒烟；
- 或 bundle 经过许可证与 ABI 审核的原生 runtime，并验证 RPATH/加载顺序。

### 5. 移除重复安装与脆弱的 x86_64 硬编码

现状：

- `libmpv2` 在 Linux prerequisite 列表和后续独立 step 中重复安装。
- `libxcb-cursor.so.0` 从 `/usr/lib/x86_64-linux-gnu/` 硬编码复制。
- 找不到 standalone 目录时仅 warning，可能产出损坏包。

建议：

- 删除重复 `libmpv2` step。
- 将共享库解析和复制移入 build tool，用 `ldconfig -p`、`dpkg -L` 或实际 ELF 依赖解析定位。
- 缺少必要库时 fail fast。
- 归档前运行 `ldd`，拒绝包含 `not found` 的产物。

### 6. 添加 Workflow 静态检查和 action 固定策略

建议：

- CI 增加 `actionlint`。
- 对第三方/官方 action 采用完整 commit SHA 固定，并在行尾注释可读版本；让 Dependabot 更新 SHA。
- 不建议为了“看起来稳定”退回旧 major。审计日官方 release 页面确认 checkout v7、setup-python v7、upload-artifact v7、download-artifact v8 均存在且为当前系列。

## P1：建议后续实施

### 7. 统一 CI 与 Release 的质量门禁

现状：release 由 `src/app/version.py` 变更触发，自己重跑一部分检查，但没有复用 CI 中的 Ruff、compileall、全构建计划和未来 GUI smoke。

建议：

- 抽取 reusable workflow（`workflow_call`）；
- CI 和 Release 调用同一套 gate；
- Release 只在统一 gate 成功后构建/发布。

### 8. 生成 SBOM、provenance 与签名证据

建议：

- 为每个平台产物生成 SPDX/CycloneDX SBOM；
- 使用 GitHub artifact attestations 建立 build provenance；
- 实施时再按最小权限增加 `id-token: write`、`attestations: write`；
- 产物发布页同时附 SHA-256 与验证说明。

### 9. 失败证据留存

建议在失败时上传：

- pytest JUnit/XML；
- `--doctor-json`；
- frozen-runtime smoke 输出；
- `ldd`/`otool -L`/Windows DLL 扫描；
- Nuitka/PyInstaller 构建报告；
- mpv/Portal/GUI 日志。

### 10. Dependabot 分组细化

现状：pip 的所有依赖被放入一个 `python-runtime` 全匹配组，大型更新 PR 难以定位回归。

建议拆为：

- runtime：PySide6、Pillow、psutil、pynput、dbus-next、PyGObject 等；
- build：Nuitka、PyInstaller、installer/packaging 工具；
- test/quality：pytest、ruff、类型检查工具。

保留 GitHub Actions 独立分组。

## 已确认合理、无需立即修改

- Workflow 顶层 `contents: read`，发布权限没有长期扩大；
- CI/CodeQL/dependency-review 的 concurrency 设置合理；
- `persist-credentials: false` 已使用；
- CodeQL v4 和 dependency-review v5 属当前系列；
- artifact v8 的 digest mismatch 默认报错属于安全增强，不建议关闭。

## 官方核验来源

抓取日期：2026-07-24。

- checkout releases：https://github.com/actions/checkout/releases
- setup-python releases：https://github.com/actions/setup-python/releases
- upload-artifact releases：https://github.com/actions/upload-artifact/releases
- download-artifact releases：https://github.com/actions/download-artifact/releases
- CodeQL Action releases：https://github.com/github/codeql-action/releases
- Artifact attestations：https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations
