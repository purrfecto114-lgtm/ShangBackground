# 贡献指南

感谢您考虑为 ShangBackground 贡献代码！本文档说明开发流程和提交规范。

## 开发环境

```bash
git clone https://github.com/purrfecto114-lgtm/ShangBackground.git
cd ShangBackground
python -m venv .venv

# Windows
.venv\Scripts\activate && pip install -r requirements/windows-full.txt

# Linux
source .venv/bin/activate && pip install -r requirements/linux-full.txt

# macOS
source .venv/bin/activate && pip install -r requirements/macos-full.txt
```

要求 Python 3.10+，推荐 3.12 或 3.13。

## 日常开发流程

1. ** Fork 并克隆仓库**，创建功能分支：`git checkout -b feature/my-feature`。
2. **安装开发依赖**：`pip install pytest ruff`。
3. **本地验证**：提交前运行以下检查，确保与 CI 一致：
   ```bash
   # 语法 + 风格检查（与 .github/workflows/ci.yml 的 quality job 一致）
   python -m ruff check build_tools src tests .github/scripts
   python -m compileall -q build_tools src tests .github/scripts

   # 构建系统自检
   python build_tools/build.py self-test

   # 单元测试
   python -m pytest -q
   ```
4. **提交**：使用规范的 commit message（见下文）。
5. **推送并创建 Pull Request**：PR 会自动触发 CI 和 Dependency review。

## Commit Message 规范

格式：`<type>(<scope>): <subject>`

- **type**：`feat`（新功能）、`fix`（修复）、`refactor`（重构）、`docs`（文档）、`test`（测试）、`ci`（CI/CD）、`chore`（杂项）。
- **scope**：可选，受影响的模块，如 `installer`、`workflows`、`build-tools`。
- **subject**：祈使句，首字母小写，不加句号。

示例：
```
feat(installer): add Inno Setup Windows setup.exe with mandatory license agreement
fix(ci): repair Windows/Linux/macOS CI failures after installer integration
docs: update RELEASE_PROCESS with manual installer build instructions
```

## 版本发布

版本号定义在 `src/app/version.py` 的 `APP_VERSION`，必须使用严格的 `major.minor.patch` 格式。发布前需同时更新：

1. `src/app/version.py` 的 `APP_VERSION`；
2. `src/main_version_info.txt` 的 Windows 四段版本；
3. `README.md` 的版本徽章；
4. `CHANGELOG.md` 的新版本条目。

将变更合入 `main` 后，`release.yml` 工作流会自动：
- 校验四处版本一致性；
- 在 Windows/Linux/macOS 原生 runner 上构建 Nuitka full standalone，Windows/Linux 强制使用 UPX；
- 使用 Inno Setup 7 x64 生成 Windows `setup.exe`；
- 创建 `v<major.minor.patch>` Tag 和 GitHub Release；
- 生成 `SHA256SUMS.txt` 校验和文件。

完整流程见 [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md)。

## 代码风格

- 行宽上限 120 字符（`pyproject.toml` 中 `[tool.ruff] line-length = 120`）。
- Ruff 启用规则：`E9, F63, F7, F82, F`（语法错误、未使用变量、重新定义等）。
- 类型标注：新代码必须使用 `from __future__ import annotations` 并标注公共 API。
- 不要提交 `__pycache__/`、`dist-*/`、`build-generated/` 等生成产物（已 in `.gitignore`）。

## 平台验收边界

异平台 dry-run 只能验证构建参数生成，不能替代真机验收。以下场景必须在对应操作系统原生验证：

- Windows：Explorer/WorkerW 集成、桌面右键菜单、退出壁纸恢复；
- Linux：X11/Wayland 桌面差异、Qt XCB 插件加载；
- macOS：辅助功能权限、AppKit、Gatekeeper；
- 视频/HTML helper：GPU、系统 WebView、MPV 运行时。

## 报告问题

- Bug 报告请使用 [GitHub Issues](https://github.com/purrfecto114-lgtm/ShangBackground/issues) 的 Bug Report 模板。
- 安全漏洞请按 [`SECURITY.md`](SECURITY.md) 私密报告，不要在公开 Issue 中披露。
