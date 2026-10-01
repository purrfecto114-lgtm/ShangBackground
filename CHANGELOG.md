# Changelog

本文件记录 ShangBackground 的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [1.6.1] - 2026-10-01

v1.6.1 是对 v1.6.0 的地毯式复核发布：基于 4 路并行只读审计（架构 5.5 / 代码卫生 4.5 / UX 综合 6.5 / 工程化 3 阻断 / 生态对标 6 优势 18 差距）逐条验证后的修复批次。审计报告的 5 条事实性错误经复核修正后未按原文实施（详见各条目）。UX 两条硬阻断（破坏性按钮无视觉警示、侧边栏键盘不可达）并列第一优先修复。

### 修复

- **破坏性按钮与普通按钮视觉无差异（UX 硬阻断）** — `QPushButton[danger]` 选择器从未在任何样式表中定义：danger_bg 只是孤立的主题角色，「恢复出厂设置」（清空配置/历史/托盘菜单/外观偏好）渲染得和所有按钮一样，且在安全模式页占据左一主按钮位——看起来反而像推荐操作。现双主题分支补齐 danger 规则（亮 #cf222e / 暗 #da3633，白字对比度 5.36:1 / 4.61:1 实算过 WCAG），安全模式按钮重排为 [弹性][关闭][复制错误信息][恢复出厂]，破坏性动作推到最右端；新增「复制错误信息」按钮（此前页面要求"把错误发给开发者"却不给复制手段）。
- **侧边栏壁纸列表对键盘与屏幕阅读器完全不可达（UX 硬阻断）** — 核心动作「换一张壁纸」唯一触发路径是 mouseReleaseEvent 的曼哈顿距离门：ThumbnailItem 无焦点策略（默认 NoFocus）、无 keyPressEvent、无 accessibleName，全模块 897 行仅有的 1 次焦点 API 调用还是禁用方向（外点盾）。现 StrongFocus + accessibleName/Description、Enter/Space 发射同一 clicked 信号、焦点环样式、Tab 链、Up/Down 换焦（首尾环绕）、当前壁纸初始落焦（对鼠标用户零视觉增量）。
- **英文界面必应进度整体失效（P1）** — 进度解码判据 `t("进度") in message` 对硬编码中文消息前缀匹配：英文界面每条进度消息都被当成完成消息——进度条中途跳 100%、同步按钮中途复活（可重入并发下载）、worker 引用被提前清空。进度协议改语言无关哨兵 `\x00bing-progress:<pct>/<status>`（\x00 不会出现在任何用户可见文本），状态文本过 t()（英文用户此前看硬编码中文下载进度）。旧中文前缀格式已无发射点，一并移除。变异验证：回植旧判据 4 项测试转红。
- **视频模式 GUI 冻结（P1）** — 模式切换事务持操作锁 14-18 秒，GUI 以 700ms/40ms 双频轮询 is_running/set_option/last_target，带锁版本每次轮询排队等整个事务结束（实测单次停顿 2900ms）。三个热路径读方法去锁（探针/选项文件/目标读取均为独立于事务的纯读）。
- **右键换壁纸失败后应用不回来（P1，三平台）** — 右键脚本先杀主进程再设壁纸：set_wallpaper 失败时旧实现弹错误框后直接 return，用户被留在"应用被杀 + 壁纸未变 + 无提示"状态。现杀进程后全部逻辑包进失败保障 try 块，任何失败都重启应用并告知原因；另 save_diy 原子写（此前 open("w") 中途崩溃毁掉整个 DIY 列表）、load_config 逐候选容错 + .bak 兜底（损坏配置不再终结脚本）、移除零写入方的 TEMP_FILE 死守卫。
- **Windows mixin 629 行（72%）共享基类副本经 MRO 屏蔽共享实现（架构 🔴1）** — 暗色/主题修复改在共享基类永远到不了 Windows 用户，而唯一真机覆盖最充分的平台跑的正是副本。六个样式/图标方法（_init_icon/_render_svg_to_pixmap/_combo_popup_stylesheet/_extra_theme_qss/_rebuild_stylesheet/_settings_nav_stylesheet）统一回共享基类，真实平台差异（图标 .ico 优先、QSS 图标 URL 形态）转为显式 core.IS_WINDOWS 分支。Windows 侧逐字节等价证明：_rebuild_stylesheet 与 _settings_nav_stylesheet 统一后与旧副本字节相同；顺带修复共享暗色模板的五处暗/亮漂移（暗色下拉框块停留在旧版、item:selected 规则孤儿、按下内缩 1px、信息按钮色阶、QListView 弹窗行高 30px→28px 与 QMenu 弹窗一致——即审计 A5"像素一致实为两套数"的根因）。_is_desktop_foreground 是唯一真分歧对（Win32 窗口类 vs X11/Wayland 检测），保留覆写。main_window.py 8436→8143 行（净 -293；其中 mixin 统一 -696、死方法 -84、键盘/对比度/i18n/P1/P2 修复 +487）。
- **可靠性批次（P2×4）** — UpdateChecker 完成后显式 deleteLater（不再等 Python GC 释放 QThread 资源）；日志节流状态 512 键硬上限（洪峰新键注入快于 0.75s 窗口自然过期，无上限 dict 随唯一消息线性增长）；两处后台线程启动失败兜底（_core_busy 已置位而线程未起时界面永久"正在执行"，现直接经完成信号复位）；托盘气泡接线（engine 的 IPC 失败气泡读 core.tray_icon_obj，GUI 从不写入该全局，通知分支自引入起就是死代码）。
- **i18n 批次** — Windows 热键表把"上一张壁纸"动作标成 t("上一个桌面背景")（en.json 映射为应用名"ShangBackground"，英文用户把应用名当动作标签看）；热键录制反馈只写主窗口状态栏而用户在独立设置对话框里（录制开始现在同步更新对话框当前值标签，pynput 缺失改为对话框级警告）；6 处异常弹窗裸塞 str(e)/硬编码中文（保存必应/打开位置×3/右键菜单目标错误，现在带翻译引导语）；黑话清理（"pynput is missing"改组件级说明、XDG/Qt translation 文案去术语化）+ 删除混入语言文件的 docstring 死键。审计报告所称"3 个坏 i18n 键"经复核两处不成立（键存在且渲染正常），实际缺陷如上。
- **对比度与状态可见性批次** — muted 文本 #6b7280/#656d76/#777→#57606a（状态栏在实际底色 #f0f2f5 上 4.31:1 差 0.19 未过 AA，修复后 5.70:1）；近白主题灰阶选中态配深字（白字 on #8c959f 3.04:1→#24292f 4.82:1；暗色 on #8b8ba3 2.73:1→#1a1b2e 5.10:1）；滚动条手柄（默认白主题下 1.51:1 几乎不可见→#6e7781 4.05:1，暗色与侧栏同步）；禁用态加虚线边（纯灰度禁用对色盲不可辨）；状态栏 ElideMiddle→ElideRight（句子中间截断切掉动词，错误原因在句尾）。全部数值经 WCAG 公式实算并固化为 CI 回归测试。
- **CI 诚实化快赢** — requirements/test.txt 补 PySide6-Essentials/psutil（8 个测试含安全与 P0 验收项在 CI 恒 skip 从未执行）+ QT_QPA_PLATFORM=offscreen；Dependabot pip 目录从根（无 pyproject 依赖节，周更新空转数月）改 requirements 双目录；Pillow 下限 >=12.2（消除 34 条历史 GHSA 的理论解析面）；三处 backends 目录补包标记（命名空间包从覆盖率报告消失）；test_layering glob→rglob（约 2.1 万行零门禁区域纳入守护）+ core 禁 ui 导入 / app 禁模块级 ui 导入两条新 AST 守卫；xvfb 探针从存在性改功能性（缺 xauth 的环境此前硬失败而非跳过）。
- **死代码清理** — services/bing_sync.py（56 行，生产零引用：唯一可达路径是历史巨文件时代的 main.py 惰性导出垫片，活动实现在 services/bing.py）连同 bundle.py 两处打包引用、垫片表项与文本守护测试删除；main_window 10 个零引用方法（84 行，全仓标识符扫描含字符串形式引用验证）删除。

### 工程化

- **覆盖率基线** — pytest 全测试腿 --cov=src --cov=build_tools 报告模式（不设阈值）：数字先可见，再谈门槛。
- **pyright 顾问 job** — 非阻塞（continue-on-error）+ 产物上传。审计所称"sys.modules 门面是 pyright 未入 CI 的根因"经实测推翻（门面零错误），真实错误主体是 main_window/engine 动态属性模式（约 480），先可见再消化。
- **Ruff 门禁扩展** — E9+F → +A/T10/TID/YTT/ICN/INT/LOG/RSE/SLOT（当前零违规的最大集）；W/G/B/ASYNC/ISC/PYI 约 12 处手修、I/UP 自动修复战役另行排队。
- **macOS ad-hoc 签名** — 未签名 arm64 Mach-O 在 macOS 内核层直接 Killed: 9（Apple Silicon 上"能启动"的必要条件，非公证）。产物内嵌代码先签、主二进制次之、bundle 最后，严格校验后打包；README 补双平台未签名构建的打开指引（macOS 隐私与安全性→仍要打开 / Windows SmartScreen→更多信息→仍要运行）。完整签名/公证仍阻塞在证书（signing.py 具备 signtool+RFC3161 能力但无身份可用），Azure Trusted Signing 个人版为中期路径。
- **ruff format 基线** — 151 文件一次性格式归一（Windows mixin 方法体多一层缩进的沉疴一并清除），后续 diff 只剩真实改动。

### 已知未修复（下轮候选）

- 视觉系统级问题（四种白拼盘/默认纯白主题线框感/#ffffff 硬编码 ×75）需要设计决策而非 token 替换，未动。
- Windows per-monitor 静态壁纸（IDesktopWallpaper::SetWallpaper 官方原生支持，COM 后端已在但 monitorID 恒 NULL）——外部证据评为 P0 的功能缺口，属新功能非修复。
- 发布签名/公证（需证书）；PyInstaller 后端真实构建验证；24 个文本 grep 测试改 AST 结构断言（重构 REFACTOR_ROADMAP 后续阶段的硬前置）。
- Wayland 全屏自动暂停（外部证据：Lively/GNOME/KDE 三方均无干净解，不建议投入研发）。

## [1.6.0] - 2026-09-30

v1.6.0 是 v1.5.1 之后经四轮审计迭代打磨的合并发布。开发过程中的 1.6.1–1.6.4 中间版本号从未作为标签发布，其变更全部并入本节：v1.6.0 审查报告修复、v1.6.1 两轮审计（自主 5-agent 检测 + 用户报告合并处置）、v1.6.2（v1.6.1 报告两项必须修复 + CI 三平台全绿）、v1.6.3（KDE schema=3 插件级恢复 + doctor 口径统一 + D-Bus 前置检查）、v1.6.4（KDE 按显示器静态壁纸设置）与本轮静默失败审计（P0×3、P1×6、P2×6，详见各条目"静默失败审计"前缀）。

### 新增

- **IPC 未送达动作死信队列（静默失败审计 P1-4）** — 冷启动右键子进程在 40 次重试后仍无法把动作送达主实例时，旧实现只剩一条日志——用户点完没有任何反馈。`local_ipc` 新增 `record_missed_command`/`drain_missed_commands`（有界 20 条、原子写、损坏文件容错），`support` 转发全链失败时写入；主实例下次启动 1.2 秒后由 `engine.surface_missed_ipc_actions()` 逐条记日志并托盘播报。含 en.json 播报键。
- **安装器卸载回滚壁纸适应方式（静默失败审计 P1-5）** — Inno Setup 脚本在 ssInstall（任何文件复制之前）捕获 `HKCU\Control Panel\Desktop` 的 WallpaperStyle/TileWallpaper 存入产品自有键；卸载时若应用优雅退出已确认（`--quit --wait-for-exit` 的退出事务已用会话快照还原过）则仅丢弃备份，退出未确认时才回写原始值；两种路径都消费备份键，防陈旧备份影响未来重装。升级重装保留最早的原始值。CI 无 ISCC，源码级契约由测试钉住；真机安装/卸载验证列入验收清单。
- **KDE 按显示器（per-screen）静态壁纸设置（计划任务 3 步骤 3）** — `linux/integration.py` 新增模块级公开函数 `set_kde_wallpaper_for_screen(path, screen_index, *, fill_mode)`（纯新增 165 行，全输出路径 `_set_kde_wallpaper` 零改动）：前置检查链固定为 screen_index 参数校验 → 文件存在 → 会话总线前置检查 → 只读探针（复用 schema=3 的 `_kde_capture_state_script` 建立 containment→screen 显式映射）→ 写入脚本用 **id+screen 双重条件**只定位探针命中的 containment（防探针与写入之间桌面配置变化的竞态）。**拒绝部分成功**是核心语义：请求的 screen 无法映射到任何 containment（或命中者缺少可解析 id、做不了双重定位）时返回结构化失败，不 spawn 写脚本、不触碰任何桌面；写入时 `applied=0` 同样判失败。`plasma-apply-wallpaperimage` 是全输出命令，按输出路径禁用（只走 evaluateScript）。错误信息与"命令缺失 / 无会话 bus / Plasma 拒绝"可区分；`last_kde_set_outcome()` 可观测（method 含 `evaluateScript(screen=N)`）。UI/ports 接线为后续工作（当前无生产调用方）。测试：`tests/test_kde_static_wallpaper_contract.py`（新建 17 项：全输出行为钉住 2 + per-screen 15，含验收 S1 补的写通道拒绝钉）+ `tests/test_linux_wayland_backends.py`（+2 项：能力口径 + 公开交付面签名契约）。
- **`multi_monitor_static` 能力口径 KDE 分支收敛** — `linux/capabilities.py`：KDE 会话的按显示器就绪门槛与实现对齐——只有 qdbus6/qdbus（Plasma scripting 通道）在场才报告 `runtime_ready`，`plasma-apply-wallpaperimage` 在场只代表全输出可用（limitations 明示"无法映射的 screen 会被拒绝而不是部分成功"），避免向 plasma-apply-only 用户错误开放按显示器选择。非 KDE 桌面维持既有口径不变。
- **KDE 插件级壁纸状态保存/恢复（schema=3，计划任务 2）** — `app/ports.py` 新增可选 `StatefulWallpaperBackend` 协议（`capture_state()`/`restore_state()`，独立于 `WallpaperBackend` 以免破坏 runtime_checkable 检查）；Linux 后端新增 `capture_wallpaper_state()`（Plasma scripting 逐 containment 读取 id/screen/plugin/Image/FillMode，Image 读取复用 `_kde_read_wallpaper_values` 已验证的组回退链）与 `restore_wallpaper_state()`（id→screen→全量兜底三档匹配，`json.dumps` 防注入，复用 `last_kde_set_outcome` 的 accepted/verified 区分）；`bootstrap.py` 两个适配器与 `engine.py` 布线（Windows/macOS `ImportError`→None，行为与 v1.6.2 逐字节一致）；`SessionWallpaperService` 升级 schema=3：slideshow/远程壁纸等"路径不可恢复"场景现在也会记录插件状态（`wallpaper` 字段保持"本地可恢复路径"语义，不可恢复时留空不伪造），退出恢复优先走 `restore_state`，失败且路径可恢复时诚实降级回路径恢复；schema=2 旧文件读取时按需转换（`converted_from=2`，仅在后端支持状态恢复时）。诚实边界：`config_captured=True` 仅限 `org.kde.image`+读到 Image（R1 校准：FillMode 可缺省——KConfig 默认不写默认值键，缺省即缺省，恢复时不写该键）；非图片插件只保插件名，内部配置不伪造；远程 URL 原样记录在 `image_uri` 不转本地路径。测试：`tests/test_kde_wallpaper_state.py`（21 项，含 R1 回归钉）+ `tests/test_session_wallpaper_service.py`（16 项，新建）。
- **KDE D-Bus 会话总线前置检查（计划任务 3 步骤 1–2）** — `DBUS_SESSION_BUS_ADDRESS` 与 `$XDG_RUNTIME_DIR/bus` 均不可用时，`_run_plasma_script` 与 `_set_kde_wallpaper` 在 spawn 任何外部命令之前返回可操作错误——"无会话总线/命令缺失（command not found）/Plasma 拒绝（rc!=0 stderr）"三种失败从此可区分，不再把"不在图形会话内"误报成命令问题。`tests/test_kde_restore_scope.py::_run_set` 补 D-Bus 环境钉住（CI runner 无 bus 端点，否则 rc=0 路径会被前置检查误拒——v1.6.2 `_file_uri` 教训的同款跨平台预防）。
- **doctor "Wayland video embedding" 检查消费统一能力判定（计划任务 5 步骤 3 doctor 侧）** — 原检查只看 mpvpaper 命令存在性，KDE Wayland 装了 mpvpaper 就显示 pass，与 v1.6.2 能力口径（KDE 不默认 ready）分裂。新增门面 `platform_adapters.session.linux_video_wallpaper_capability()`（惰性暴露 `probe_capabilities()["video_wallpaper"]`），doctor 改为消费该判定：wlroots+mpvpaper → pass；KDE+mpvpaper → warn + `SHANGBACKGROUND_ALLOW_MPVPAPER` 实验开关指引；wlroots 未装 → 安装指引；GNOME 等 → "当前 Wayland 桌面无受支持的视频壁纸层"；探测异常 → warn "capability probe unavailable"。这是 REFACTOR_ROADMAP"统一后端选择器"方向的诊断消费面切面——启动器侧统一仍以 KDE 真机矩阵为前置条件，本版不动。测试：`tests/test_doctor_wayland_video.py`（11 项，含端到端实测：旧口径误显 pass 的 KDE Wayland+mpvpaper 场景现在如实 warn）。
- **mpv IPC 进度/状态上报与播放就绪验证** — 新增共享 JSON IPC 协议层（`src/platform_adapters/mpv_ipc.py`），Windows named pipe 与 Linux Unix socket 复用同一套实现；视频启动后经 IPC 确认媒体真实播放，消除“IPC 通道就绪但画面黑屏”，失败自动拆除并回退；`observe_property` 由 getattr 能力探测升级为真实现（平台通道 + 轮询回退观察器），播放进度、EOF 与状态可上报。Linux 视频扩展名对齐 Windows（补充 `.wmv`），播放器终止改用进程组 SIGTERM→SIGKILL 兜底，无 psutil 也不留孤儿进程。macOS 平台暂不适用（维持原行为）。

### 修复

- **右键/热键切换壁纸失败零反馈（静默失败审计 P0-1）** — `_execute_ipc_wallpaper_command` 对 previous/next/random 丢弃返回值、恒返回 True：`WallpaperService.apply()` 失败（COM 失败、目标文件被删、别名拷贝失败）时桌面纹丝不动且无任何提示，而 GUI 按钮同场景会弹错误框，两条路径体验不一致。现失败时抛 RuntimeError 走既有 worker 异常通道，与 `set_wallpaper|` 分支和 GUI 路径对齐。
- **IPC 失败通知被关键词白名单过滤（静默失败审计 P0-1b）** — `_notify_ipc_failure` 只对含"没有/无/不存在/未找到/empty/not found"的消息弹托盘通知，"设置壁纸失败（未知错误）""停止动态壁纸失败"这类真实失败一条都匹配不上。白名单移除：每条 IPC 命令失败都来自用户主动操作（桌面右键/热键），均为用户有意义信号。
- **适应方式失败信号被后端吞掉、服务层异常分支成死代码（静默失败审计 P0-2）** — Windows `configure_fit_mode` 恒返回 None 且吞异常，`WallpaperService` 的 `_fail("设置适应模式失败")` 分支永不可达，用户看到假成功。契约显式化：COM 或注册表至少一条路径成功返回 True、两条都失败返回 False（`ports.py` 协议注解与 `bootstrap.py` 两个适配器透传返回值；其余平台后端返回 None 维持旧语义、不视为失败），服务层把显式 False 转为用户可见失败。
- **set_fit_mode 假成功（静默失败审计 P0-3）** — 异常只记日志、不设 `last_operation_error`、隐式返回 None，而 GUI worker 只判 `result is False`，None 不命中——用户改"填充/适应/平铺"失败却看到"操作完成"。现失败路径设置错误原因并返回 False；重应用当前壁纸失败同样传播。
- **右键菜单"注册成功但永不显示且重启应用无法自愈"（静默失败审计 P1-1）** — SHChangeNotify 失败只记日志，`is_context_menu_synced()` 只看注册表，`sync_context_menu(only_if_needed=True)` 判"已同步"直接短路，三者叠加形成永久不同步稳态。新增进程级 `_shell_association_notified` 标志：本会话未成功通知过 Explorer 即视为未同步，每次应用启动的首次同步都会执行幂等重注册+重通知，跨重启自愈。
- **VLC 后端静音清零音量（REVIEW 3.2 同源遗留，审计 P2）** — `_vlc_command` 静音时仍传 `--volume=0`；与 mpv 侧修法同语义，静音时保留已保存音量（0-100 → 0-256 VLC 刻度映射不变），解除静音立即恢复。
- **Linux/macOS CLI help 与音量语义冲突（审计 P2）** — `--volume` 的 help 仍写 "only effective when --muted is not set"，与 mpv 修正后的"静音保留音量"语义矛盾，两处文案更新。
- **signtool 输出解码不确定（审计 P2）** — `run_signtool` 的 `text=True` 未指定编码，中文 Windows 上按 locale（GBK）解码可抛 UnicodeDecodeError；显式 `encoding="utf-8", errors="replace"`，与其余构建工具一致，诊断信息永不因解码失败丢失。
- **per-screen 写通道拒绝时错误报文重复两遍（验收 S3）** — uri 与 abs_path 两次尝试收到相同拒绝原因时只记录一次（同通道同错误的重复报文对用户没有信息增量），有测试钉住 `count == 1`。
- **schema=3 捕获的 FillMode 严格门槛会丢 Image（主线程校准 R1）** — 初版实现把 `config_captured=True` 定义为"org.kde.image + Image + FillMode 为 int"，但 Plasma 的 KConfig 默认不写默认值键：FillMode 处于默认值的常见形态下，捕获会拒绝完整配置、恢复时只写插件名——**比 schema=2 更差**（图片本身丢失）。放宽为"org.kde.image + 读到 Image"，`fill_mode=None` 语义为"恢复时不写该键、Plasma 自行使用默认值"（缺省即缺省，不伪造值）。含捕获/恢复两侧回归钉。
- **测试 FakeProcess 假 pid 可能撞真实进程（v1.6.2 验收轮遗留隐患）** — 测试硬编码的假 pid（9876/2469/2468/9753/8642/4321/12345）在 mock 漂移时会经 `os.kill`/`_reap_child`/状态文件消费方打到真实进程。统一替换为 `2**31-1`（Linux pid_max ≤ 4194304、macOS ≤ 99998、Windows 实际分配远低于 2^31，三平台均不可能是活进程；POSIX 上 `os.kill` 对它确定抛 `ProcessLookupError`）。
- **KDE Wayland 视频能力探测误报（审查必须修复项 1）** — `capabilities.py` 原先只要桌面令牌含 `kde`/`plasma` 且 PATH 中有 `mpvpaper`，就把视频壁纸标为 `runtime_ready=True`。但 mpvpaper 官方定位是 wlroots 系合成器（Sway/Hyprland 等，以 wlroots 为依赖），把 KWin 会话按同一后端标记为 ready 属于把"命令存在"当成"后端可运行"。现 KDE Wayland 一律 `runtime_ready=False`：有 mpvpaper 时 `state=best_effort`（backend 如实注明"experimental, untested on KWin"与实验开关用法），无则 `unsupported`；`_layer_shell_session` 令牌集移除 kde/plasma，新增 `_kde_session` 分流；sway/hyprland 等 wlroots 会话行为不变。启动路径（`video.py`）同步收紧：KDE 会话默认拒绝 mpvpaper，`SHANGBACKGROUND_ALLOW_MPVPAPER=1` 只作为显式实验开关放行启动尝试且不改变能力声明（错误信息指导用户如何开启）。新增契约测试 `tests/test_kde_capability_contract.py`（9 项：报告原型断言/开关不改声明/wlroots 不受影响/静态与热键不殃及/X11 不受影响等），`test_linux_wayland_backends.py` 与 `test_platform_runtime_regressions.py` 中 4 处旧"KDE 自动 ready"断言翻转为失败契约（mpvpaper 参数类测试改用 sway 会话验证，与桌面令牌解耦）。
- **KDE 退出恢复的插件丢失未告知用户（审查必须修复项 2，选"明确降级范围"路线）** — `SessionWallpaperService`（schema=2）仅保存本地壁纸路径，设置脚本无条件写 `org.kde.image`：用户原本使用 slideshow/color/第三方 Plasma 插件时，退出恢复会静默丢失插件与配置。按报告给出的二选一，本版选择明确降级而非实现 schema=3：新增 `integration.kde_wallpaper_restore_scope()`（经 session 门面暴露，惰性加载），读取每个 containment 的 `wallpaperPlugin`；`--doctor` 对超范围插件显式 WARN + hint（"slideshow/color/第三方壁纸插件的配置不会被还原；如需保留请自行记录插件设置"），全部为 `org.kde.image` 时 PASS，Plasma 不可达时 WARN。完整插件级恢复（schema=3）保留为 `docs/KDE_SUPPORT_PLAN.md` 任务 2 的后续目标。新增 `tests/test_kde_restore_scope.py`（16 项）。
- **Windows 测试矩阵失败（CI）** — `_terminate_process_tree` 中两处 POSIX-only 泄漏：`os.getpgid(pid)` 抛 `AttributeError`（非 `OSError`，穿透 `except OSError`）；同函数后续的 `signal.SIGKILL` 在 Windows 的 signal 模块中不存在（实参在调用点求值，先于 own_group 检查）——getpgid 报错在执行序上先炸，掩盖了后者（验收轮子进程模拟实证）。CI 在 Windows 3.10/3.13 上运行 Linux 失败路径的跨平台模拟测试时即崩。分别改为 `hasattr` 守卫与 `getattr(signal, "SIGKILL", signal.SIGTERM)` 占位（Windows 上 own_group 恒为 False，信号永不发送）。
- **macOS 测试矩阵失败（CI）** — `test_linux_ipc_readiness_end_to_end_over_real_unix_socket` 用 pytest `tmp_path` 拼 socket 路径，GitHub 托管 runner 上（`/private/var/folders/…` 嵌套目录）超过 macOS AF_UNIX 的 104 字节 `sun_path` 上限，`bind` 抛 "AF_UNIX path too long"。现超限（>95 字节）时回退到系统级短临时目录并在 finally 自行清理，极端环境 skip。
- **Dependency review 工作流红叉（CI）** — 仓库未启用 Dependency graph 时 `dependency-review-action` 以 error 终止，无法与"发现高危依赖"区分，PR 被无关红叉阻塞。现先用 SBOM 端点探测（200=已启用）：未启用时输出明确的 `::warning::` 并跳过审查（管理员在 Settings → Code security 启用后自动恢复生效，无需改工作流）。
- **共享层平台反向依赖（P0）** — 架构文档规定 `core/`、`app/` 不得直接导入平台后端，但存在三处违反：`core/engine.py` 直连 Windows 后端私有函数 `_prime_explorer_wallpaper_host`，`app/diagnostics.py` 与 `app/config.py` 直连 Linux 后端 `session` 模块。现全部收敛：Windows/共享侧经 `platform_adapters.integration` 门面新增公共 `prime_desktop_wallpaper_host()`（三平台后端统一暴露，非 Windows 为 no-op）；会话探测经新建的 `platform_adapters/session.py` 门面（零 `app.*` 依赖，`app.config` 模块级导入不构成环；非 Linux 主机返回惰性默认值）。新增 `tests/test_layering.py`：AST 级依赖方向守护（含探测器自检），回归即 CI 失败。
- **发布源码包混入字节码（P0）** — CI 在打包前运行 pytest，`src/**/__pycache__/*.pyc` 随 `rglob("*")` 无过滤进入 zip/tar 发布包。`_zip_directory`/`_tar_directory` 现排除 `__pycache__` 目录与 `.pyc`/`.pyo` 后缀，并新增两个归档卫生回归测试（构造带 pycache 的迷你工作区，断言归档内零泄漏）。
- **模式切换补偿失败对用户不可见（P1）** — 新模式启动失败且旧模式恢复/旧配置持久化也失败时，补偿结果只进日志，调用方只拿到一句通用异常。新增 `ModeSwitchReport`/`ModeRollbackOutcome` 结构化报告：`_compensate` 返回回滚实况（配置是否恢复/旧模式是否恢复运行/是否落盘/错误清单），`WallpaperModeError` 携带 report，engine 门面镜像为 `core.last_mode_switch_report`（与 `last_operation_error` 同模式），主窗口失败警告现附带一行回滚摘要（如"配置已恢复；旧模式恢复失败；恢复结果保存失败"）。成功路径行为与返回类型完全不变。
- **HTML 来源校验注释与行为不符（P1）** — `source_validation.py` 注释声称"Reject non-standard ports (SSRF)"，实际分支为 `pass` 死代码。删除死代码并如实注明：自定义端口属有意允许（用户本机 webview + 本地开发服务器场景），结构性与危险模式校验（凭据注入/CRLF/UNC/SMB/设备名）保持不变；新增端口契约测试钉住真实行为。
- **内部参数错误静默退出（P2）** — `--internal-video-player` 与 `--build-verify-file` 缺参数时返回 2 但无任何输出，终端与崩溃日志均无法定位。现向 stderr 输出明确的中文错误说明。
- **Windows 单实例互斥检测不可靠（第一轮审计）** — `ctypes.windll.kernel32.GetLastError()` 可能被中间 ctypes 调用污染，漏检 `ERROR_ALREADY_EXISTS` 时第二个进程自认持有互斥体，穿透文件锁回退并产生双主实例。改用 `WinDLL(use_last_error=True)` + `ctypes.get_last_error()` 的文档化用法。
- **Windows 设置变更原生过滤器悬挂指针（第一轮审计）** — `QAbstractNativeEventFilter` 安装后仅由局部变量持有，`del` 后 C++ 对象可被回收，事件循环携带已安装 filter 运行属 use-after-free。改为类属性 owner slot 持有（行为级探针实证引用与派发链存活）；`message_offset` 由硬编码 8 改为 `ctypes.sizeof(ctypes.c_void_p)`（x86 构建可移植）；退出路径 `del app` 纯表达式安全化，销毁顺序修正为 shim 随帧 teardown 先行、QApplication 由解释器收尾。"外部程序修改壁纸 → 历史记录"链路由此稳定。
- **GNOME Shell.Eval 输出解析零匹配（第一轮审计）** — 原判定与 `gdbus call` 实际输出格式（`(true, '…')` 等四种变体）不符，该路径在 GNOME 上永不生效。改为 `startswith("(true")` + 第二元素判定，并以四种真实格式的断言钉住。
- **二次启动转发命令可无限挂起（第一轮审计）** — `SendMessageW` 无超时，主实例 GUI 线程忙于退出事务或长切换时，转发托盘/右键动作的第二次启动整体挂起。改用 `SendMessageTimeoutW` + `SMTO_ABORTIFHUNG`（与右键快速路径同模式），1 秒预算，超时记日志返回失败。
- **QtRootShim.after() 序号竞态丢失回调（第一轮审计）** — IPC 回调/幻灯片/热键线程并发调用 `after()` 时，无锁的 `self._seq += 1` 可发出重复计时器 id，`_schedule_after` 因此丢弃其中一个回调（表现为"置前窗口"偶发需要点两次）。加 `threading.Lock` 保护发号。
- **Portal 全局热键授权被拒后状态失真（第一轮审计）** — `BindShortcuts` 才是实际请求授权的调用，其拒绝异常发生在 `start()` 返回 True 之后，服务一直认为热键已激活但永不触发。现捕获绑定失败、置 `_available=False` 并让 `is_running()`/`last_error` 反映真实状态。
- **worker 线程内 `QTimer.singleShot` 永不触发（v1.6.0 既有 bug，验收轮实证）** — PySide6 6.11.1 中非 GUI 线程调用 `QTimer.singleShot` 为静默 no-op，"启动时自动更新必应"自 v1.6.0 起从未生效。改为专用 `Signal` + `QueuedConnection` 派发回 UI 线程执行。
- **幻灯片启动先停后验的副作用（第一轮审计）** — 参数校验（模式/文件夹/图片列表）发生在停止当前动态壁纸之后，校验失败也会把正在播放的视频壁纸停掉，且无失败日志。现先校验后停止并记录根因；调用方通用补偿可能端到端重启旧动态模式的边界已在注释中如实标注（后续改进项）。
- **共享配置归一化的空配置空窗期（第一轮审计）** — `clear()+update()` 会瞬间清空共享 dict，`_config_lock` 之外的并发读者可观察到（并序列化）空配置。改为逐键 diff 应用；验收注明这是 lost-update 竞态的部分缓解，彻底关闭需要直接写键的调用方同样持锁。
- **代码签名时间戳走明文 HTTP（第一轮审计）** — RFC3161 时间戳端点由 `http://timestamp.digicert.com` 改为 HTTPS，消除中间人阻断时间戳请求或重放过期 token 的干扰向量。
- **构建工具可诊断性（第一轮审计）** — 安装器源目录与变体不匹配时列出实际存在的 standalone 输出（原先只有一句 missing）；`--upx` 用于 PyInstaller 后端时显式报错（原先静默产出未压缩构建）；CI 发布资产选择正则改两段式（`-x64` 优先）并修正零匹配后误选，`api.github.com` 调用统一携带 `GITHUB_TOKEN` 认证（规避共享 runner IP 的匿名限流）。
- **视频路径绑定启动崩溃（P0）** — 主窗口构建时向 `bind_existing_file(..., suffixes=...)` 传入的 `suffixes` 在 `bind()` 中无对应形参，启动即 TypeError。现 `bind()` 支持 `suffixes` 关键字并转发到既有扩展名校验链（大小写不敏感，行为不变时保持默认）。
- **Windows 收藏右键菜单阻塞事件循环** — Windows 平台 mixin 中仍保留阻塞版 `menu.exec()`，属 v1.4.3 修复的回归；现删除阻塞版本，统一走共享实现的 `popup()` 异步菜单。
- **界面文案国际化补全** — en.json 补齐缺失键（Bing 同步提示、动画开关、静音/音量提示、失败提示、关于页链接等）；修正日志文案误包 `t()` 的方向错误；用户可见的硬编码中文统一包进 `t()`。
- **播放器崩溃后运行时 IPC 快速失败** — mpv 意外退出后音量/暂停/属性读取等运行时调用不再按启动期预算长时间重试打开 IPC 通道（GUI 线程每次调用最长冻结 ~6s → ≤0.5s），并自动清理死亡播放器的残留状态；就绪验证的单请求预算不再放大总截止时间；属性观察器注册消除并发同名替换下的线程复活竞态。

### 变更

- **Windows 多显示器能力口径与实现对齐（静默失败审计 P1-3）** — `multi_monitor_static` 的 backend 原声明 "IDesktopWallpaper monitor API"，暗示按显示器独立控制；实现实为 monitorID=NULL 的一次性全输出设置（包装器未接线按显示器选择）。backend/limitations 改为如实描述：同图铺满所有屏、per-monitor 选择未接线、旧注册表回退无此能力——与 macOS 侧同一口径（Linux KDE 侧已于上一批次收敛）。
- **跨卷非 ASCII 壁纸别名拷贝可观测（静默失败审计 P1-2）** — 别名按（路径, 大小, mtime）缓存，跨卷整文件拷贝只发生在每个文件版本首次而非每次切换；但首拷在壁纸 worker 线程、set_wallpaper 之前同步执行，大图的切换延迟无法诊断。现记录字节数与耗时的 WARNING 日志。
- **NOTICE 补第三方运行时二进制条目（审计遗留 §8.6）** — 明确发行产物不分发 mpv/VLC/ffmpeg 二进制，及未来若捆绑 mpv 时的 GPLv2+/FFmpeg 许可义务（许可证文本、源代码提供方式、NOTICE 补录）。
- **CI 已知限制如实注记（审计 P2）** — ci.yml 的构建计划校验步骤与 `docs/RELEASE_PROCESS.md` 注明：PyInstaller 仅 dry-run 计划校验，真实产物 + `validate_frozen_runtime` 仅 Nuitka 路径覆盖，两条路径产物一致性未经 CI 实证。
- **KDE 静态壁纸设置结果的结构化可观测（审查建议项 1，轻量版）** — `_set_kde_wallpaper` 把"命令返回 0"（accepted）与"读回确认"（verified）合并为单一成功返回，掩盖了 Plasma 6 已知的 readConfig 空值行为。新增 `last_kde_set_outcome()` 事后查询（accepted/verified/method/detail 四字段），不改变 `set_wallpaper` 的 `(bool, str)` 公共契约（engine→UI 调用链零改动），诊断与日志可据此区分"Plasma 接受了请求但读回未验证"与"读回确认一致"。配套 4 项行为测试。
- **测试依赖单源固定（P0 部分）** — pytest/ruff 版本约束原先内联散落在 ci.yml（两处）与 release.yml（一处），易漂移。新增 `requirements/test.txt`（pytest>=8,<10 + ruff>=0.12,<1），三个 workflow 安装步骤统一改为 `-r requirements/test.txt`（pip 缓存键已自动覆盖）。pyright 有意不列入：CI 从不运行它，pyrightconfig.json 仅供本地 IDE，列入即伪门禁。
- **`--doctor`/`--doctor-json` 输出可操作修复指引（P2）** — `DiagnosticCheck` 新增 `hint` 字段：缺失依赖给出 `python -m pip install <package>`，可选命令缺失说明自动降级行为，macOS 系统框架缺失说明来源；human 报告在 WARN/FAIL 行下输出 `hint:`，JSON 载荷同步携带（新增字段，旧消费者不受影响）。
- **架构文档线程模型如实化（P1）** — `ARCHITECTURE.md` 原声明"长期 Worker 使用 `QObject.moveToThread(QThread)`；短时 Python 任务使用共享线程池"，与实现不符（全库 0 处 moveToThread、无共享线程池；实际为 `threading.Thread` worker + Qt Signal queued 回 UI，`services/updates.py` 为唯一 QThread 用户）。现改为如实描述现状 + 新增 Worker 硬性规则（线程内禁触 Qt 控件、回 UI 必经 Signal）+ moveToThread 列为需真机验收掩护的路线图目标，消除"文档撒谎"这一 P1 的实质。
- **PySide6 维持 6.11.1（评估结论）** — 6.11.2 已于 2026-08 发布，但检索未发现其修复影响本项目的安全或崩溃问题（项目仅用 QtWidgets/QtCore/QtGui 成熟面）；且 6.11.x 系列存在 teardown segfault 的第三方报告线索。无真机验收环境下盲升 patch 版本风险大于收益，维持 6.11.1，升级列入真机验收轮的专项检查项。
- **全局热键默认开启** — 工厂默认、旧配置迁移、保存回退与热键服务读取四层一致改为默认启用；设置页文案同步去掉“默认关闭”表述。配套的简单热键焦点保护（`hotkey_focus_guard`）同步全平台默认开启。
- **构建钉版对齐** — `build_tools/requirements/build-pyinstaller.txt` 由 6.21.0 对齐到 `buildlib/constants.py` 的 **6.22.0**；清理 pyproject.toml 中空的 `per-file-ignores` 配置。

### 文档

- **CHANGELOG 版本段合并** — 开发过程中的 1.6.1–1.6.4 中间版本号从未作为标签发布（远程仅有指向审计基线的 v1.6.0-rc1），四轮审计迭代的全部变更并入本节随 1.6.0 正式发布；`docs/REVIEW_REPORT_V1.6.1.md` 等审查报告保留原文件名作为历史依据。
- **en.json 新增死信队列播报键 + i18n 静态完整性回归钉（审计 P1-6）** — `tests/test_i18n_runtime.py` 新增 AST 级比对：源码中全部静态 `t("字面量")` 键必须收录进 en.json，防英文界面漏显中文回退（14 键缺口在 v1.6.0 原批次已补齐，本钉防回归）。
- **静默失败审计回归测试** — 新建 `tests/test_silent_failure_regressions.py`（36 项）：IPC 失败传播三命令参数化、通知无白名单、fit_mode 契约四态（注册表成功/全失败/无 winreg/COM 短路）、服务层显式 False 与 None 容忍、bootstrap 透传、set_fit_mode 失败/异常/重应用传播/成功、会话通知标志三态、能力口径、死信队列往返/上限/损坏容错/转发失败与成功、播报含 GUI 线程调度、安装器源码契约、VLC 静音/非静音音量映射。
- **新增 `docs/ARCHITECTURE.md` "Linux 多显示器静态壁纸（按输出设置）"章节** — 全输出 vs 按输出两条路径的差异、拒绝部分成功语义、plasma-apply-wallpaperimage 为何只能用于全输出、前置检查链顺序、能力口径对齐说明；Windows 侧如实注明当前 IDesktopWallpaper 包装器不传 monitor ID（一次调用设置全部输出，该 API 支持按 monitor 定位是未来扩展点）——修正了原先"使用 monitor API"的过度声明措辞（验收 S2）。
- **`docs/KDE_SUPPORT_PLAN.md`** — 勾选任务 3 步骤 3–4 复选框 + 批次 A（30-a 实施记录，含 3 处偏离规格的决策文档化）与批次 B（30-b 验收 ACCEPT + S 类建议处置）记录。
- **README "Linux/KDE 恢复范围声明"** — 新增按显示器设置后端能力条目（明确 UI 尚未接线，应用界面当前仍是一次设置全部输出——不宣称用户已可按屏选择）。
- **新增 `docs/KDE_TEST_MATRIX.md`（计划任务 5 步骤 1）** — Plasma 5/6 × X11/Wayland × 单/双显示器 × 100%/150% 缩放的环境矩阵，含静态壁纸基础（中文路径/填充模式/双屏）、退出恢复事务（schema=3 专项：slideshow 插件重选验证、会话文件 schema 字段核对）、D-Bus 前置检查专项（无总线环境不拉起 qdbus）、能力与诊断口径（v1.6.3 修复的分裂点验证）、热键与单实例门禁项、动态壁纸证据记录（协议/输出/首帧截图三要素）；命令级冒烟只引用真实存在的 CLI 面（`--version`/`--doctor-json`/右键菜单文件参数模式——**没有** `set-wallpaper` 子命令，已事实核查），发布门禁规则明确"静态通过不自动升级动态状态"。项目无 KDE CI runner，本矩阵是能力状态升级为 `supported` 的唯一证据来源。
- **`docs/KDE_SUPPORT_PLAN.md`** — 任务 2 步骤 1–4、任务 3 步骤 1–2、任务 5 步骤 1 与 doctor 口径收敛的实施记录（含 R1 校准与诚实边界）；任务 3 步骤 3–4（按输出的恢复策略）与任务 4（KDE 动态壁纸路线）仍为待办。
- **README 恢复范围声明同步 schema=3 口径** — "退出恢复仅支持本地静态图片"更新为"插件级状态恢复（v1.6.3 schema=3）"：插件被重选、`org.kde.image` 完整恢复、非图片插件内部配置回落默认值的边界如实标注；doctor 对 KDE Wayland 视频场景不因 mpvpaper 已安装而显示 pass 的行为一并注明。
- **doctor `kde-wallpaper-restore` 提示文案同步 schema=3 口径** — 超范围插件场景的 hint 从"配置不会被还原（完整插件恢复见计划任务 2）"更新为"插件本身会被恢复，但内部配置不被保存、会回落插件默认值"。
- **入库 v1.6.1 审查报告与 KDE 实现计划** — `docs/REVIEW_REPORT_V1.6.1.md`（审查结论与证据）与 `docs/KDE_SUPPORT_PLAN.md`（任务 1–5 计划）随版本入库；计划文件的任务 1（冻结 KDE 行为契约）已勾选并附 v1.6.2 实施记录（任务 2 的 schema=3 路线与任务 3–5 仍为待办）。
- **README 恢复范围声明（审查必须修复项 2 的文档面）** — Linux/KDE 平台说明明确标注："退出恢复仅支持本地静态图片（org.kde.image）；slideshow/color/第三方壁纸插件的配置不会被还原"。
- **新增 `docs/REFACTOR_ROADMAP.md`** — `main_window.py`（约 8400 行）God Object 的四阶段拆分路线图：基于 v1.6.1 实测方法聚类给出各职责块行区间与独立性评估（含日志/About 区间嵌套的前置剥离标注），阶段 1（日志查看器/About 动画/SVG 渲染，约 -750 行）零风险可先行，阶段 4（core worker 调度/模式编排/退出流程）必须三平台真机冒烟掩护；每阶段的行为不变原则、Controller 边界规则与回滚策略成文。
- **`docs/BUILD_SYSTEM.md`** — 发布前最低验证补入 HTML 壁纸运行器自检命令：`PYTHONPATH=src python -m platform_adapters.native_html_runner --self-test`（含 Windows cmd/PowerShell 等价形式；原命令在仓库根目录因 `src/` 不在模块搜索路径而必然失败，且此前无任何文档给出可运行形式）。
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
