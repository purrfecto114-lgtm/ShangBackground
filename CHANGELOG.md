# Changelog

本文件记录 ShangBackground 的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [1.6.1] - 2026-09-24

### 修复

- **共享层平台反向依赖（P0）** — 架构文档规定 `core/`、`app/` 不得直接导入平台后端，但存在三处违反：`core/engine.py` 直连 Windows 后端私有函数 `_prime_explorer_wallpaper_host`，`app/diagnostics.py` 与 `app/config.py` 直连 Linux 后端 `session` 模块。现全部收敛：Windows/共享侧经 `platform_adapters.integration` 门面新增公共 `prime_desktop_wallpaper_host()`（三平台后端统一暴露，非 Windows 为 no-op）；会话探测经新建的 `platform_adapters/session.py` 门面（零 `app.*` 依赖，`app.config` 模块级导入不构成环；非 Linux 主机返回惰性默认值）。新增 `tests/test_layering.py`：AST 级依赖方向守护（含探测器自检），回归即 CI 失败。
- **发布源码包混入字节码（P0）** — CI 在打包前运行 pytest，`src/**/__pycache__/*.pyc` 随 `rglob("*")` 无过滤进入 zip/tar 发布包。`_zip_directory`/`_tar_directory` 现排除 `__pycache__` 目录与 `.pyc`/`.pyo` 后缀，并新增两个归档卫生回归测试（构造带 pycache 的迷你工作区，断言归档内零泄漏）。
- **模式切换补偿失败对用户不可见（P1）** — 新模式启动失败且旧模式恢复/旧配置持久化也失败时，补偿结果只进日志，调用方只拿到一句通用异常。新增 `ModeSwitchReport`/`ModeRollbackOutcome` 结构化报告：`_compensate` 返回回滚实况（配置是否恢复/旧模式是否恢复运行/是否落盘/错误清单），`WallpaperModeError` 携带 report，engine 门面镜像为 `core.last_mode_switch_report`（与 `last_operation_error` 同模式），主窗口失败警告现附带一行回滚摘要（如"配置已恢复；旧模式恢复失败；恢复结果保存失败"）。成功路径行为与返回类型完全不变。
- **HTML 来源校验注释与行为不符（P1）** — `source_validation.py` 注释声称"Reject non-standard ports (SSRF)"，实际分支为 `pass` 死代码。删除死代码并如实注明：自定义端口属有意允许（用户本机 webview + 本地开发服务器场景），结构性与危险模式校验（凭据注入/CRLF/UNC/SMB/设备名）保持不变；新增端口契约测试钉住真实行为。
- **内部参数错误静默退出（P2）** — `--internal-video-player` 与 `--build-verify-file` 缺参数时返回 2 但无任何输出，终端与崩溃日志均无法定位。现向 stderr 输出明确的中文错误说明。

### 变更

- **测试依赖单源固定（P0 部分）** — pytest/ruff 版本约束原先内联散落在 ci.yml（两处）与 release.yml（一处），易漂移。新增 `requirements/test.txt`（pytest>=8,<10 + ruff>=0.12,<1），三个 workflow 安装步骤统一改为 `-r requirements/test.txt`（pip 缓存键已自动覆盖）。pyright 有意不列入：CI 从不运行它，pyrightconfig.json 仅供本地 IDE，列入即伪门禁。
- **`--doctor`/`--doctor-json` 输出可操作修复指引（P2）** — `DiagnosticCheck` 新增 `hint` 字段：缺失依赖给出 `python -m pip install <package>`，可选命令缺失说明自动降级行为，macOS 系统框架缺失说明来源；human 报告在 WARN/FAIL 行下输出 `hint:`，JSON 载荷同步携带（新增字段，旧消费者不受影响）。
- **架构文档线程模型如实化（P1）** — `ARCHITECTURE.md` 原声明"长期 Worker 使用 `QObject.moveToThread(QThread)`；短时 Python 任务使用共享线程池"，与实现不符（全库 0 处 moveToThread、无共享线程池；实际为 `threading.Thread` worker + Qt Signal queued 回 UI，`services/updates.py` 为唯一 QThread 用户）。现改为如实描述现状 + 新增 Worker 硬性规则（线程内禁触 Qt 控件、回 UI 必经 Signal）+ moveToThread 列为需真机验收掩护的路线图目标，消除"文档撒谎"这一 P1 的实质。
- **PySide6 维持 6.11.1（评估结论）** — 6.11.2 已于 2026-08 发布，但检索未发现其修复影响本项目的安全或崩溃问题（项目仅用 QtWidgets/QtCore/QtGui 成熟面）；且 6.11.x 系列存在 teardown segfault 的第三方报告线索。无真机验收环境下盲升 patch 版本风险大于收益，维持 6.11.1，升级列入真机验收轮的专项检查项。
- **版本号 1.6.0 → 1.6.1** — v1.6.0 最终版从未发布（远程仅存在指向审计基线的 v1.6.0-rc1 标签），本轮全部修复随 1.6.1 发布；`src/app/version.py`、`src/main_version_info.txt`、README 徽章三处同步，`release.py metadata` 校验通过。合并回默认分支时 version.py 的变更将按设计自动触发发布流水线。

### 文档

- **新增 `docs/REFACTOR_ROADMAP.md`** — `main_window.py`（约 8400 行）God Object 的四阶段拆分路线图：基于 v1.6.1 实测方法聚类给出各职责块行区间与独立性评估（含日志/About 区间嵌套的前置剥离标注），阶段 1（日志查看器/About 动画/SVG 渲染，约 -750 行）零风险可先行，阶段 4（core worker 调度/模式编排/退出流程）必须三平台真机冒烟掩护；每阶段的行为不变原则、Controller 边界规则与回滚策略成文。
- **`docs/BUILD_SYSTEM.md`** — 发布前最低验证补入 HTML 壁纸运行器自检命令：`PYTHONPATH=src python -m platform_adapters.native_html_runner --self-test`（含 Windows cmd/PowerShell 等价形式；原命令在仓库根目录因 `src/` 不在模块搜索路径而必然失败，且此前无任何文档给出可运行形式）。

## [1.6.0] - 2026-09-22

### 新增

- **mpv IPC 进度/状态上报与播放就绪验证** — 新增共享 JSON IPC 协议层（`src/platform_adapters/mpv_ipc.py`），Windows named pipe 与 Linux Unix socket 复用同一套实现；视频启动后经 IPC 确认媒体真实播放，消除“IPC 通道就绪但画面黑屏”，失败自动拆除并回退；`observe_property` 由 getattr 能力探测升级为真实现（平台通道 + 轮询回退观察器），播放进度、EOF 与状态可上报。Linux 视频扩展名对齐 Windows（补充 `.wmv`），播放器终止改用进程组 SIGTERM→SIGKILL 兜底，无 psutil 也不留孤儿进程。macOS 平台暂不适用（维持原行为）。

### 修复

- **视频路径绑定启动崩溃（P0）** — 主窗口构建时向 `bind_existing_file(..., suffixes=...)` 传入的 `suffixes` 在 `bind()` 中无对应形参，启动即 TypeError。现 `bind()` 支持 `suffixes` 关键字并转发到既有扩展名校验链（大小写不敏感，行为不变时保持默认）。
- **Windows 收藏右键菜单阻塞事件循环** — Windows 平台 mixin 中仍保留阻塞版 `menu.exec()`，属 v1.4.3 修复的回归；现删除阻塞版本，统一走共享实现的 `popup()` 异步菜单。
- **界面文案国际化补全** — en.json 补齐缺失键（Bing 同步提示、动画开关、静音/音量提示、失败提示、关于页链接等）；修正日志文案误包 `t()` 的方向错误；用户可见的硬编码中文统一包进 `t()`。
- **播放器崩溃后运行时 IPC 快速失败** — mpv 意外退出后音量/暂停/属性读取等运行时调用不再按启动期预算长时间重试打开 IPC 通道（GUI 线程每次调用最长冻结 ~6s → ≤0.5s），并自动清理死亡播放器的残留状态；就绪验证的单请求预算不再放大总截止时间；属性观察器注册消除并发同名替换下的线程复活竞态。

### 变更

- **全局热键默认开启** — 工厂默认、旧配置迁移、保存回退与热键服务读取四层一致改为默认启用；设置页文案同步去掉“默认关闭”表述。配套的简单热键焦点保护（`hotkey_focus_guard`）同步全平台默认开启。
- **构建钉版对齐** — `build_tools/requirements/build-pyinstaller.txt` 由 6.21.0 对齐到 `buildlib/constants.py` 的 **6.22.0**；清理 pyproject.toml 中空的 `per-file-ignores` 配置。

### 文档

- **仓库清理** — 按路线图“不留阶段性报告”原则，删除根目录的一次性审查报告与 TODO 账本两份文件（结论并入 CHANGELOG 与 ROADMAP）；删除 3 个零引用的 `img/` 图片，并以真实文件名恢复 GitHub Pages 站点引用的 `img/文字logo.png`（仓库中原为历史工具产生的 `#U` 转义损坏名）。
- **文档与现实对齐** — ROADMAP 勾销已完成的 CI dry-run 与 Wayland Portal 两项，并吸收二轮审查结论（mpv 真机验证矩阵、许可证清单、Qt 行为测试、broad except 审计）；GETTING_MPV 修正“Windows 发布包必须含 mpv.exe”与 release.yml 实际策略的矛盾，并记录播放就绪验证；PROJECT_STRUCTURE 移除指向不存在示例目录的断链引用；README 平台表反映 Wayland Portal 进展。

## [1.5.1] - 2026-08-22

本节补录 PR #2（`fix/mpv-menu-video-pr`）的全部变更与版本号提升。

### Added

- **托盘菜单“设置”入口** — 托盘菜单新增“设置”动作，可直接打开设置窗口。
- **桌面右键“设置”动作** — Windows 桌面右键菜单新增“设置”动作。
- **Windows 签名诊断** — 构建发布流程输出结构化的签名诊断结果（unsigned / signed / failed），便于排查发布产物的签名状态。

### Fixed

- **视频选择跨模式事务化** — 壁纸/视频选择在所有视频模式下按事务提交，失败即整体回滚，不再留下半应用的壁纸状态（PR #2）。
- **Windows mpv bundle 平铺校验** — 构建期校验 Windows mpv 运行时为平铺布局（`mpv.exe` 与 DLL 同层），防止嵌套目录产物进入发布。
- **CI 稳定性** — 移除未使用导入；未安装 PySide6 的环境中 Qt 相关测试自动跳过，与 CI 实际环境一致。

### Changed

- 版本升级到 **1.5.1**，Windows 文件版本同步更新。

## [1.5.0] - 2026-08-21

### Fixed

- **Windows 桌面右键 3–4 秒忙碌** — Explorer 入口新增纯 stdlib/Win32 快路径：运行中直接转发固定动作，冷启动立即脱离 Explorer 后再启动完整 GUI；启动竞态由脱离后的子进程延长 IPC 重试兜底。
- **Linux/macOS 首页泄漏 Windows 右键控件** — 非 Windows 不再创建 `ctx_*` 桌面 Shell 开关；真实全局热键继续只由独立的“全局热键设置”页管理。
- **全局热键保存误判失败** — GUI 不再调用已经不存在的 `core._parse_hotkey_string` / `core._pynput_hotkey_string`；Windows 与共享实现统一使用 `platform_adapters.hotkey_bindings.parse_hotkey`，右键菜单开关也不再触发无关的全局热键重注册。
- **Windows 新包不再接受 libmpv-only payload** — v1.5.0 构建入口要求 Windows MPV runtime 含 `mpv.exe`；旧 libmpv-only 安装仍可兼容运行，但不会再被构建成新的完整应用子进程播放方案。
- **MPV bundle 调用链过重/下载策略失真** — Windows 优先使用已验证的 `mpv.exe + JSON IPC`，旧 libmpv-only payload 仅作兼容回退；显式下载默认使用 mpv 官方 latest stable release 的 Windows 二进制资产，`development` 仅保留为最新 master CI 的显式选择。
- **MPV 升级残留原生 DLL** — Inno Setup 安装前定点清理产品自有 `bin\mpv`，避免新旧 native runtime 混装；损坏安装也不再因为主程序无法执行退出命令而完全阻断卸载。
- **命令行动作假成功** — 单实例 IPC 转发失败、动作执行异常和不存在的壁纸路径现在返回非零退出码，便于 Explorer/脚本准确判断结果。
- **Release 冻结程序冒烟测试可被静默绕过** — `--version` 失败或版本不匹配会直接阻断发布，并从 `src` 读取唯一版本源。
- **Inno Setup 空壳/错布局风险** — 安装器只接收构建器明确选择的一种冻结布局；构建清单会校验 freezer、Windows 目标和 x86_64 架构。
- **PyInstaller 升级残留** — 安装前清理产品自有 `_internal` 目录，避免旧 DLL/PYD 残留，并覆盖 PyInstaller → Nuitka 迁移路径。
- **安装后校验过弱** — Inno Setup 现在同时检查主程序和对应 `build-features.json`，并默认写安装日志。
- **性能档语义倒置** — 集中三档调度参数；“流畅”档现在确实比“均衡”档刷新更快并允许更大的缩略图解码/缓存预算，同时保持默认“均衡”档原有参数不变。
- **CI 安装重试与发布回退** — UPX/Inno Setup 安装步骤增加重试，UPX 下载增加 GitHub Release 回退路径，降低偶发网络抖动导致的构建失败（71c3f86）。

**二轮审查返工补录（随 v1.5.0 标签一并发布）：**

- **mpv 静音丢失用户音量** — 旧实现在 `muted=True` 时同时传 `--volume=0 --mute=yes`，导致 IPC 热取消静音后无法恢复用户保存的音量。Windows、Linux X11（xwinwrap+mpv）、Wayland（mpvpaper）和内部 libmpv 现在把 `volume` 与 `mute` 作为独立属性，静音时保留用户音量，热取消静音时只需 IPC `set_property mute false`。
- **mpvpaper 静音误禁用音轨** — 旧实现在静音时传 `no-audio`，这会禁用整个音轨；IPC `mute=false` 不足以重新启用音轨。现仅使用 `mute=yes`，保留音轨活跃。
- **mpv MinGW 嵌套 ZIP 资产无法内置** — 官方 MinGW/i686 artifact 的外层 ZIP 内含一个 `mpv-git-<date>-<hash>-i686.zip`，里面才是 `mpv.exe`。旧下载器只解一层，导致 x86/MinGW 资产无法真正内置。现支持受限的单层嵌套解压（最多 4 候选、共享总解压预算、复用路径穿越/符号链接防护）。
- **构建诊断无界等待** — `_python_probe()` 原无超时，Python 子进程异常卡住时 preflight/self-test 会无限等待。现增加 15 秒硬超时并转为可诊断的 `RuntimeError`。
- **卸载残留** — 单实例锁目录 `%LOCALAPPDATA%\ShangBackground-<hash>\` 在卸载时未被清理（哈希后缀导致硬编码路径失效）。现 `CurUninstallStepChanged` 扫描 `%LOCALAPPDATA%\ShangBackground-*` 并删除锁文件和目录；旧版 `%TEMP%\ShangBackground_session_wallpaper.json` 同步清理；数据目录增加 `dirifempty` 后备确保目录本身被删除。
- **Inno Setup 7 编译错误** — `[Code]` 段内多行 `Format()` 调用的数组参数换行到下一行，行首 `[ResultCode, ...]` 被 ISCC 7 误判为段头。现合并到同一行。`FindFirst` 返回 `Boolean` 而非 `Integer` 的类型修正也已应用。
- **快捷方式 tooltip 与标签不一致** — 旧 `Comment: "{#PRODUCT_NAME}"` 让悬停 tooltip 显示“Previous Desktop Background”而快捷方式标签是“ShangBackground”。现改为 `Comment: "{#APP_NAME} — {#PRODUCT_NAME}"`。
- **开始菜单快捷方式无可选项** — `DisableProgramGroupPage=yes` 强制隐藏“选择开始菜单文件夹”向导页。现改为 `no` 并新增显式 `startmenu` task，用户可在“附加快捷方式”分组中取消勾选。

### Changed

- 版本升级到 **1.5.0**；Windows 文件版本同步为 `1.5.0.0`。
- Inno Setup 流程明确要求 **Inno Setup 7**，使用 `SetupArchitecture=x64`，并停止自动搜索 Inno Setup 6。
- 安装器新增 `MinVersion=10.0.17763`，与 Qt/PySide 6.11 的 Windows 10 1809+ 支持范围对齐，避免旧系统“可安装但不可运行”。
- PyInstaller 构建固定版本从 6.21.0 更新到 **6.22.0**；Nuitka 4.1.3、PySide6-Essentials 6.11.1 保持不变。
- 收拢 Windows/macOS UI mixin 中与共享实现等价的图标缓存、侧边栏、Bing 另存和暗色模式覆盖，减少平台补丁分叉；删除 macOS 分支中未使用的 Windows Startup 辅助方法。
- 继续移除平台 mixin 中可由 AST 证明与共享实现行为等价的重复覆盖，并让 Windows 热键/右键菜单直接复用统一状态机，减少“某平台修了、另一份镜像没修”的回归面。

**二轮审查返工补录：**

- **文档与运行时规则统一** — Windows v1.5+ 明确以打包的 `mpv.exe + JSON IPC` 为首选，旧 libmpv-only 仅作兼容回退；Linux X11/Wayland 保留各自平台策略。`docs/ARCHITECTURE.md`、`docs/BUILD_SYSTEM.md`、`docs/GETTING_MPV.md`、`requirements/windows-video.txt` 同步更新。
- **测试可信度提升** — `test_video_system_mode.py` 从源码文本断言改为真实函数调用 + monkeypatch，验证 `_internal_libmpv_command()` 在 system/disabled 模式下真的返回 None；新增嵌套 ZIP、Wayland mpvpaper 静音、构建诊断超时等行为测试。

## [1.4.6] - 2026-07-29

### Fixed

- **Inno Setup 卸载器运行时错误** — 移除卸载阶段不支持的 `CreateOutputMsgPage`，改用静默模式安全的确认框。
- **卸载时主进程仍占用文件** — `--quit --wait-for-exit` 等待准确 PID 完成清理，IPC 启动窗口内重试且失败时中止卸载。
- **默认保留配置失效** — 用户数据删除改为受明确确认控制，静默卸载默认保留配置和日志。
- **启动项和右键菜单残留** — 统一清理当前及旧版 Run 值、产品专属右键菜单和重复的公共启动快捷方式。
- **误删通用启动脚本** — 仅在确认 `PowerOn.vbs` 属于 ShangBackground 时清理。
- **安装包空壳风险** — 拒绝不匹配或同时存在的 standalone 布局，dry-run 默认执行输入校验。

### Changed

- Windows Release 使用 Inno Setup 7 x64、Nuitka full standalone 和 UPX 5.2.0，并保留既有多平台发布门禁。

## [1.4.5] - 2026-07-26

### Fixed

- **卸载后VBS开机启动残留** — 改用 HKCU 注册表 Run 键替代 VBS 文件，Inno Setup 自动清理（`uninsdeletevalue`）
- **右键菜单/托盘无壁纸时无提示** — IPC 壁纸命令失败时显示托盘通知（"没有上一张壁纸"等）

### Added

- **Inno Setup 欢迎页** — 自定义中文欢迎文字，介绍应用功能
- **卸载界面可选删除用户配置** — 复选框默认不勾选，保护用户数据
- **注册表 Run 键开机启动** — 替代 VBS，启动更快（无 wscript.exe 中间进程），卸载自动清理

### Changed

- v1.4.4 已发布为正式 Release

## [1.4.4] - 2026-07-24

### Fixed

- **视频壁纸切换内存飙升/启动慢（根因修复）** — `--mpv-runtime system` 构建中，`_internal_libmpv_command` 仍尝试用内部 ctypes/libmpv 路径，导致**生成完整的打包可执行文件子进程**（~300MB+）仅为了播放视频。修复：当 `video_runtime_mode()` 为 `system` 或 `disabled` 时跳过内部 libmpv 路径，直接使用外部 mpv.exe（~30MB）。同时修复 Linux X11 后端相同问题。
- **托盘右键菜单1秒延迟（根因修复）** — 不再每次右键重建 QMenu，改为持久化 `QMenu` 实例 + `menu.clear()` 重填。预热改为 `show()`+`hide()` 强制创建原生窗口。
- **sidebar 点击外侧不缩回（Windows）** — `_OutsideClickShield` 移除 `WindowDoesNotAcceptFocus` 标志，提高 `windowOpacity` 从 0.001 到 0.01。`qApp` 事件过滤器新增 `MouseButtonRelease` 和 `NonClientAreaMouseButtonPress` 监听。
- **触摸滑动误触壁纸切换** — 新增 `_TouchScrollFilter` 事件过滤器，检查移动距离和 `QScroller` 状态。
- **收藏夹右键菜单阻塞事件循环** — `menu.exec()` 改为 `menu.popup()`（异步）。

### Changed

- v1.4.3 已发布为 prerelease。

## [1.4.3] - 2026-07-24

### Fixed

- **托盘右键菜单首次延迟** — 启动时预热线程菜单的 `sizeHint()` 和图标解码，消除首次右键的样式/图标惰性解析延迟（Qt Forum topic 123225）。
- **触摸滑动误触壁纸切换** — 新增 `_TouchScrollFilter` 事件过滤器，在 `MouseButtonRelease` 时检查移动距离（>10px）和 `QScroller` 状态（Dragging/Scrolling），抑制滑动产生的合成鼠标点击事件。
- **QScroller DragStartDistance** — 从 0.008 (8mm) 提高到 0.012 (12mm)，减少短距离滑动被误判为点击的概率。
- **收藏夹右键菜单阻塞事件循环** — `menu.exec()` 改为 `menu.popup()`（异步），避免模态嵌套事件循环在触摸事件吞没释放时冻结 UI。
- **误触后托盘/右键失效** — 根因是 `_core_busy` 门控在误触触发壁纸切换后静默拒绝后续操作；触摸误触修复后此问题不再出现。

### Performance

- **UPX LZMA 移除** — Nuitka UPX 插件硬编码 `--best --lzma`，LZMA 解压比 NRV2E 慢 ~10x。wrapper 脚本移除 `--lzma`，保留 `--best`（NRV2E >500 MB/s 解压）。
- **vcruntime DLL 排除** — UPX wrapper 自动跳过 vcruntime140.dll、ucrtbase.dll 等脆弱 DLL（压缩会导致崩溃）。
- **冻结产物冒烟测试** — Release workflow 新增 `--version` 冒烟步骤。

### Changed

- v1.4.2 Release 已发布为正式版（非 prerelease）。

## [1.4.2] - 2026-07-24

### Added

- 四套标准 GitHub Actions 工作流：CI、Build and release、CodeQL、Dependency review。
- Inno Setup Windows 安装包（`setup.exe`），内嵌用户许可协议，必须勾选"我接受协议"才能继续安装。
- `build_tools/build.py installer` 子命令，从已验证的 PyInstaller standalone 产物生成 `setup.exe`。
- `.github/scripts/release.py` 发布自动化脚本：版本一致性校验、源码归档、SHA256 校验和。
- Dependabot 同时维护 pip 依赖和 GitHub Actions 版本。
- macOS arm64（Apple Silicon）原生构建支持。

### Changed

- 统一 `src/` 架构，构建工具链重组到 `build_tools/buildlib/`。
- `lite` 配置默认关闭视频和 HTML 模块，减少产物体积。
- Release 资产从 5 个扩展到 7 个（新增 Windows `setup.exe`，架构覆盖 macOS arm64）。

### Fixed

- macOS frozen-runtime 验证：用 `.app` bundle 根目录而非 `Contents/MacOS` 作为 packaged application 根，修复 `Contents/Frameworks` 资源路径被误判为逃逸的问题。
- Windows publish 步骤：`os.replace` 在 WinError 5（杀毒软件锁文件）时重试 + copytree 回退。
- Linux Qt XCB 前置依赖：apt-get 安装 `libxcb-shape0` 等缺失库。
- Inno Setup 安装包：移除错误的 `PrepareToInstall` 检查（该钩子在文件复制前运行，导致安装总是失败），改为 `CurStepChanged(ssPostInstall)` 后置校验。

## [1.4.1] - 2026-07-15

### Changed

- 性能模式从布尔 `performance_mode` 改为三档 `performance_level`（`power_saver` / `balanced` / `performance`）。
- 启动任务延迟按性能档位分级，避免低端设备首帧卡顿。

### Fixed

- 修复幻灯片/Bing/视频启动任务抢占启动前壁纸记录的时序问题。
- 修复退出恢复在某些桌面环境下失败后未保留会话恢复文件的问题。

## [1.4.0] - 2026-07-01

### Added

- 三端系统原生 WebView HTML 壁纸：Windows WebView2、macOS WKWebView、Linux WebKitGTK。
- 模块化构建：`--features video,html,bing,hotkeys,updates,fonts` 按需勾选。
- Bing 每日壁纸、收藏、历史与概率权重。
- 全局热键、托盘、开机自启、单实例守护。

### Changed

- 主界面使用 Qt Widgets，HTML 壁纸运行在独立子进程，不嵌入 Qt WebEngine。
- 构建系统统一为 Nuitka/PyInstaller 双后端，共享同一份构建计划。
