# ShangBackground 深度代码审查、mpv 内置化修复与二次返工报告

审查日期：2026-08-20  
项目版本：1.5.0  
审查方式：先代码调用链、再反向补测试；分入口/生命周期、mpv、平台后端、构建与发布、安全/IPC、UI/服务、测试可信度、依赖/冒烟等轨道交错审查，并执行第二轮反证式复查。

## 1. 结论摘要

项目的基础工程质量比“单看覆盖率”更好：进程身份校验、单实例 IPC token、受限命令集合、配置原子写入、发布脚本的不变量测试等均有较好的防护。但测试总覆盖率只有约 38%，且存在一批通过读取源码文本来断言行为的测试，因此“全绿”不能等价为跨平台行为已被验证。

本轮确认并修复了三类生产缺陷/可靠性问题：

1. Windows 官方 MinGW mpv 资产可能是“ZIP 内再套 ZIP”，原安装器只解一层，导致 x86/MinGW 资产无法真正内置；现允许**最多一层、最多 4 个候选、共享总解压预算**的受限二次解压，并复用路径穿越/成员/大小保护。
2. mpv 的 `volume` 与 `mute` 是独立属性。原实现静音时把 volume 同时置 0；Wayland/mpvpaper 还会传 `no-audio`，导致运行中取消静音可能无法恢复音轨。现 Windows、Linux X11、Wayland/mpvpaper、内部 libmpv 都保留用户音量，仅独立切换 mute。
3. 构建系统 `_python_probe()` 原无超时。一旦所选 Python 子进程异常卡住，preflight/self-test 可无限等待。第二轮返工加入 15 秒硬超时并转成可诊断的 `RuntimeError`。

同时修正了 Windows/Linux mpv 运行时文档漂移：Windows 1.5+ 明确以**打包的 `mpv.exe` + JSON IPC**为首选，旧 libmpv-only 仅作兼容回退；Linux X11/Wayland 保留其各自平台策略。

## 2. 联网交叉确认

- mpv 官方 GitHub Releases 在本次审查时仍把 **v0.41.0（2025-12-21）**标为 Latest；官方说明 release binary assets 是 CI builds，实用但不是全功能构建。
  - https://github.com/mpv-player/mpv/releases
- mpv 官方讨论 #17193 明确展示 MinGW/i686 CI 资产存在“外层 artifact ZIP → 内层 mpv ZIP → mpv.exe”的结构，这正是本轮安装器修复所针对的真实上游形态。
  - https://github.com/mpv-player/mpv/discussions/17193
- mpv stable manual 明确区分 `--volume=<value>`（软件音量）与 `--mute=<yes|no>`（静音状态），并推荐外部程序使用 `--input-ipc-server` JSON IPC；Windows 使用 named pipe。
  - https://mpv.io/manual/stable/
- mpvpaper 官方 README/manpage 说明 `-o/--mpv-options` 会透传 mpv 参数，并给出 `input-ipc-server` 控制方式，因此 Wayland 路径可以保留音轨并通过 IPC 切 mute。
  - https://github.com/GhostNaN/mpvpaper
- mpv 仓库说明默认构建许可证为 GPLv2-or-later，使用 `-Dgpl=false` 时可为 LGPLv2.1-or-later。若正式随应用分发 mpv 二进制，应把许可证/NOTICE/源码提供义务作为发布清单独立核对，而不能只看技术可打包性。
  - https://github.com/mpv-player/mpv

## 3. 主要代码修改

### 3.1 Windows mpv 官方资产内置化

文件：`build_tools/buildlib/mpv_runtime.py`

- 保留原有 HTTPS、下载体积、ZIP 成员数、解压总量、路径穿越和 symlink 防护。
- `_extract_zip_safely()` 现在返回实际展开字节数，供共享预算使用。
- 仅当外层没有 `mpv.exe` 时，才扫描名称包含 `mpv` 的内层 ZIP。
- 最多处理 4 个候选，仅展开一层，不递归无界解压。
- 外层 + 内层共用 `_MAX_EXPANDED_BYTES` 总预算。
- 内层仍走同一安全解压函数，因此 `../mpv.exe` 等 traversal payload 会拒绝。
- Windows 新 bundle 仍要求真正存在 `mpv.exe`，不会把 libmpv-only 目录误当作新的 Windows bundle。

### 3.2 mpv 音量/静音语义统一

文件：

- `src/platform_adapters/backends/windows/video.py`
- `src/platform_adapters/backends/linux/video.py`
- `src/app/libmpv_runtime.py`

修复后：

- 启动时始终保存并传递 clamp 后的用户 volume。
- mute 独立传 `yes/no`。
- Windows live JSON IPC 先设置 volume，再设置 mute。
- Linux live JSON IPC 同样保持两属性独立。
- mpvpaper 不再因为“当前静音”传 `no-audio`；否则 audio track 被禁用后，单纯 IPC `mute=false` 不足以保证恢复。
- Windows CLI help 同步修正，不再声称 muted 时 volume 无效。

### 3.3 构建诊断不再无界等待

文件：`build_tools/buildlib/diagnostics.py`

- `_python_probe()` 增加 15 秒 timeout。
- `subprocess.TimeoutExpired` 转为有上下文的 `RuntimeError`，让 build CLI 的已有错误边界可以正常展示，而不是永久卡死。

### 3.4 文档与运行时规则统一

更新：

- `docs/ARCHITECTURE.md`
- `docs/BUILD_SYSTEM.md`
- `docs/GETTING_MPV.md`
- `requirements/windows-video.txt`

消除了“Windows/Linux 都优先内部 libmpv”与实际 Windows 1.5+ “mpv.exe + IPC 优先”之间的冲突，并记录 MinGW 受限双层 ZIP 处理规则。

## 4. 测试可信度审查

基线完整测试最初为 **243 passed, 2 skipped**，但一个典型反例是原 `tests/test_video_system_mode.py`：测试名声称验证 system 模式会跳过内部 libmpv，实际只检查 `video_runtime_mode()` 是否返回合法枚举，从未调用 Windows/Linux 的 `_internal_libmpv_*_command()`。因此全绿不能证明它声称的行为。

本轮把该测试改为直接调用真实后端函数，并新增：

- Windows/Linux system/disabled 模式真的禁止内部 libmpv fallback。
- Windows muted 启动仍保留指定 volume。
- Windows live IPC mute 不会把 volume 清零。
- Wayland/mpvpaper muted 启动不再使用 `no-audio`。
- 稳定版 Windows x86 MinGW 资产可被选择。
- 一层 nested MinGW ZIP 可安装 fake PE `mpv.exe`。
- nested traversal ZIP 仍被安全拒绝。
- build Python probe 有超时且给出明确错误。

最终：**252 passed, 2 skipped**；连续重复完整 pytest 也曾两次得到 **251 passed, 2 skipped**（在最后一个 timeout 测试加入前）。审查过程中曾出现一次组合运行的单点失败，随后 `pytest -x -vv`、连续两轮完整 pytest 及最终 coverage 运行均未复现，因此将其记录为未能复现的瞬态信号，而不掩盖也不伪报成已定位缺陷。

## 5. 覆盖率与结构性风险

最终 branch coverage：**38%**（17818 statements，10088 missed；4766 branches，541 partial）。这是当前最大的不确定性来源之一，尤其平台原生/UI/生命周期代码不能仅凭现有测试判定无缺陷。

至少 19 个测试文件使用 `read_text()` 或 `inspect.getsource` 一类源码文本断言。发布 workflow、Inno Setup、manifest 等“静态契约”用文本断言是合理的；但 UI 事务/退出/启动竞态/热键等行为关键测试若只检查源码片段，重构时很容易出现假阳性。建议有 Qt 测试环境后逐步替换为 service-level / event-loop-level 行为测试。

`src/ui/main_window.py` 是明显的维护性热点：`_SharedShangBackgroundWindow` 约 7233 行，`_WindowsMainWindowMixin` 约 909 行，单个 `_wallpaper_tab()` 约 548 行、`_settings_tab_full()` 约 419 行。现在直接大拆会在缺 PySide6/缺真实桌面环境的前提下放大回归风险，所以本轮没有进行高风险“大重写”。后续更稳妥的顺序应是先建立 Qt 行为测试，再按面板/view-model/service 边界渐进拆分。

源码中 broad `except Exception` 数量很多（扫描约 818 处），多数与跨平台兼容/GUI 容错有关，但会压低可诊断性。建议未来把平台探测的“预期失败”收窄到 `OSError` / `ImportError` / `subprocess` 等明确异常，并将真正未知异常至少写入诊断日志。

## 6. 安全与生命周期检查中确认较好的部分

- `process_state.py` 不只记 PID，还校验 create time / executable / username / cmdline，终止前再次核验，降低 PID reuse 误杀风险。
- `single_instance.py` 使用 per-user OS lock，IPC token 为随机值；Unix runtime dir/lock 权限有收紧。
- `local_ipc.py` 有 token 鉴权、`hmac.compare_digest`、命令 allowlist、消息大小上限；QLocalServer 使用用户访问限制。
- Windows legacy fast IPC 只承载固定、无 payload 的兼容命令；带壁纸路径的命令走认证路径。
- AST 级扫描未发现 Python `eval` / `exec` builtin、`os.system()` 或 `subprocess(..., shell=True)` 的危险调用；Qt 的 `.exec()` 对话框/事件循环不属于 Python builtin `exec`。

这些是“已检查后未发现需要本轮修改”的部分，不应与形式化安全审计或恶意输入 fuzzing 等同。

## 7. 冒烟与构建验证

成功：

- `python -m compileall -q src build_tools tests`
- `coverage run --branch -m pytest -q` → 252 passed, 2 skipped
- `python build_tools/build.py self-test` → passed
- `PYTHONPATH=src python src/main.py --version` → 1.5.0
- `PYTHONPATH=src python src/main.py --doctor` → headless 路径可运行并正确报告当前环境缺项
- `python build_tools/build.py mpv --help` → 可用
- PyInstaller Linux full standalone `--dry-run` → 生成完整计划，明确未启动 compiler

未能完成、但已实际尝试：

- 新建 `.venv-review` 后尝试安装 `PySide6/ruff/pyright/bandit` 等依赖/工具；当前容器访问 PyPI 时 DNS 解析失败。
- 使用项目自己的 `build.py mpv download --target windows --arch x86_64 --channel stable` 尝试获取真实官方 mpv；当前容器访问 GitHub 下载时同样 DNS 失败。
- 因此没有把真实 mpv 二进制偷偷塞入项目，也没有声称真实播放器冒烟已通过。
- 当前 Linux doctor 同时报告 PySide6、mpv/libmpv、xwinwrap、pynput、native webview/GTK 等缺失，所以 GUI/真实桌面播放只能在具备这些依赖的 Linux 主机以及 Windows/macOS 实机继续验证。

## 8. 下一阶段建议（按风险/收益排序）

1. 在 Windows x64、Windows x86、Linux X11、至少一个 wlroots Wayland 实机上执行真实 mpv 下载、bundle、播放、mute/unmute、pause/resume、退出清理测试；Windows 优先用官方 v0.41.0 stable CI asset，开发版仅作额外兼容验证。
2. 为“真实下载后的 archive layout”增加 CI fixture 或缓存样本（不建议把大型 mpv binary 直接提交仓库），避免上游资产结构再次变化时静默失效。
3. 补 PySide6 可运行的 UI 事务/退出/竞态行为测试，逐步替换行为关键的源码文本断言。
4. 渐进拆分 `main_window.py`，优先抽出 wallpaper/settings/bing/log 面板和长事务方法；不要一次性重写 7000+ 行类。
5. 收窄 broad exception，并将平台探测错误结构化写入 doctor/build logs。
6. 正式发布内置 mpv 前，给发布 checklist 增加许可证/NOTICE/源码义务核对，并保存所用 asset 的 SHA-256 与上游 release/tag 信息。

## 9. 本次输出

- `TODO_REVIEW.md`：完成后的审查任务账本。
- `REVIEW_REPORT.md`：本报告。
- 修订源码压缩包：由最终交付步骤生成。
- Git diff patch：由最终交付步骤生成，便于与原始 ZIP 基线逐项审查。
