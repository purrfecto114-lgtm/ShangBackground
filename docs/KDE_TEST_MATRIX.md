# KDE/Plasma 真机验收矩阵（KDE_SUPPORT_PLAN 任务 5 步骤 1）

> **性质：** 人工验收文档。项目当前没有 KDE CI runner，本矩阵是能力状态
> 升级为 `supported` 的唯一证据来源（任务 5 步骤 3 的发布门禁以本表为准）。
> v1.6.2 交付了能力契约冻结与恢复范围降级声明；v1.6.3 交付了 schema=3
> 插件级恢复与 D-Bus 前置检查——本矩阵含两者的专项验证点。

## 一、环境矩阵

每个组合单独一行记录。执行者按手头环境挑行填写，不必凑满全部组合才能
提交结果——**未执行的组合保持空白，不要凭推断打勾**。

| # | Plasma 版本 | 会话 | 显示器 | 缩放 | 执行日期 | 执行人 | 结果 |
|---|---|---|---|---|---|---|---|
| 1 | Plasma 5.x | X11 | 单显示器 | 100% | | | |
| 2 | Plasma 5.x | X11 | 双显示器（异构分辨率） | 100%+100% | | | |
| 3 | Plasma 6.x | X11 | 单显示器 | 100% | | | |
| 4 | Plasma 6.x | X11 | 双显示器 | 100%+150% | | | |
| 5 | Plasma 6.x | Wayland | 单显示器 | 100% | | | |
| 6 | Plasma 6.x | Wayland | 双显示器 | 100%+150% | | | |
| 7 | Plasma 5.x | Wayland | 单显示器 | 100% | | | |

> Plasma 5 Wayland（第 7 行）为可选行：官方支持有限，若执行请如实记录
> 现象而不是硬套通过标准。

## 二、每个组合的检查项

### A. 静态壁纸基础（现有能力回归）

- [ ] 启动应用，选择本地图片设为壁纸，桌面立即生效
- [ ] **中文路径**：壁纸文件路径含中文/空格（如 `~/图片/我的 壁纸.jpg`），设置与读回均正常
- [ ] 填充模式切换（填充/适应/拉伸/居中/平铺）逐项生效
- [ ] 重启应用后 `current_wallpaper` 状态正确恢复
- [ ] 双显示器组合：主/副屏均显示壁纸（按输出设置的行为记录现象）

### B. 退出恢复事务（v1.6.2 范围 + v1.6.3 schema=3）

- [ ] 启动前是 `org.kde.image` 本地图片 → 退出后壁纸与 FillMode **逐项还原**
- [ ] 启动前是 `org.kde.slideshow` → 退出后 **slideshow 插件被重选**（v1.6.3 schema=3 专项；插件内部配置如轮播列表**不要求**还原——这是已声明的降级边界，验收时确认插件名回来了即可）
- [ ] 启动前是 `org.kde.color` 或第三方插件 → 退出后插件被重选（同上）
- [ ] 退出后 `~/.config/shangbackground/session_original_wallpaper.json`（Linux；或安装版对应用户数据目录）中 `schema` 字段为 **3** 且含 `backend_state.containments`（slideshow 场景）
- [ ] 恢复后 doctor 的 `kde-wallpaper-restore` 检查项输出与实际插件一致
- [ ] 异常退出（`kill -9`）后再次启动：会话文件仍在且能恢复（24 小时时效内）

### C. D-Bus 前置检查（v1.6.3 任务 3 步骤 2 专项）

- [ ] 在图形会话内：静态壁纸设置正常（无前置检查误拒）
- [ ] **无总线环境**（SSH 到会话外 / 清空 `DBUS_SESSION_BUS_ADDRESS` 且无 `$XDG_RUNTIME_DIR/bus`）：设置壁纸得到"会话总线不可用"的可操作错误，且**不出现** qdbus 进程被拉起（`ps` 验证）

### D. 能力与诊断口径（v1.6.2 契约 + v1.6.3 doctor 收敛）

- [ ] `--doctor-json` 中 `Wayland video embedding` 检查项：KDE Wayland + 已装 mpvpaper → **warn** 且 hint 含 `SHANGBACKGROUND_ALLOW_MPVPAPER`（不得为 pass——这是 v1.6.3 修复的分裂点）
- [ ] 同上场景：设置视频壁纸默认被拒并给出实验开关指引；`SHANGBACKGROUND_ALLOW_MPVPAPER=1` 后允许尝试（结果如实记录：黑屏/无首帧/可用）
- [ ] X11 会话：`Wayland video embedding` 检查项**不存在**（不误报），`X11 desktop video embedding` 按实际存在性报告

### E. 热键与单实例（门禁项）

- [ ] 全局热键注册并在桌面外窗口被正确抑制（Wayland 走 XDG GlobalShortcuts portal；拒绝授权时的提示如实出现）
- [ ] 第二实例启动被拒并转发（不出现双托盘/双壁纸进程）

### F. 动态壁纸（仅记录，非门禁）

- [ ] （KDE Wayland）`SHANGBACKGROUND_ALLOW_MPVPAPER=1` 下 mpvpaper 行为：协议（layer-shell）、目标输出、**实际首帧截图**三要素记录在案
- [ ] （Plasma X11）xwinwrap + mpv 路径行为记录

## 三、命令级冒烟（每个环境跑一遍，输出贴进证据表）

> 命令事实核查（v1.6.3）：本仓库 CLI 面只有 `--version` / `--doctor` /
> `--doctor-json` / `--build-verify-file` 与右键菜单文件参数模式——
> **没有** `set-wallpaper`/`get-wallpaper` 子命令，不要凭想象补命令。
> 源码模式用 `python src/...`；安装包模式替换为实际可执行文件路径。

```bash
# 1. 版本与诊断
python src/main.py --version            # 预期输出 1.6.3
python src/main.py --doctor-json > doctor-<环境编号>.json
#   核对项：static wallpaper backend / kde-wallpaper-restore /
#           Wayland video embedding（Wayland 会话）/ X11 desktop video embedding（X11 会话）

# 2. 静态壁纸设置（右键菜单处理器 = 文件参数模式）
python src/platform_adapters/wallpaper_cli.py "/绝对路径/测试图.jpg"
#   读回验证：重跑 --doctor-json 看 kde-wallpaper-restore 的插件/图片探测，
#   以及 Plasma 自身的读回：
qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript \
  'var d=desktops()[0]; d.currentConfigGroup=Array("Wallpaper","org.kde.image","General"); print(d.readConfig("Image",""))'

# 3. 模式切换回滚（GUI 操作）
#    静态 → 视频（预期失败于 KDE Wayland，记录文案）→ 静态
#    静态 → 幻灯片 → 静态：确认回滚后壁纸未变

# 4. 退出恢复
#    正常退出 → 确认桌面回到启动前壁纸/插件 → 贴会话文件内容：
#    ~/.config/shangbackground/session_original_wallpaper.json
#    （schema 字段预期 3，slideshow 场景含 backend_state.containments）
```

## 四、证据记录表

| 环境编号 | 证据项 | 位置/说明 |
|---|---|---|
| | doctor JSON | |
| | 会话文件内容（schema=3） | |
| | 恢复后桌面截图 | |
| | 动态壁纸首帧截图 | |
| | 异常现象与日志 | |

## 五、发布门禁规则（任务 5 步骤 3）

1. **静态壁纸 + 恢复事务 + 热键 + 单实例**在 Plasma 6 X11 与 Plasma 6
   Wayland 至少各一个真实环境全部通过 → 对应能力状态方可标
   `supported`（当前 KDE 静态壁纸在 capabilities.py 中为 `best_effort`）。
2. **动态壁纸单独发布**：静态通过不自动升级动态状态；KDE Wayland 动态
   在获得带首帧证据的真机验证前保持"实验开关 + 未验证"声明。
3. 任何组合出现"程序声称成功但桌面未变"的诚实性问题 → 该组合记 FAIL，
   不允许用"重试一次就好了"替代根因记录。
4. 本表结果同步回 `docs/KDE_SUPPORT_PLAN.md` 的实施记录；能力口径变更
   须附本表环境编号。

---

**与 1.1.x 系列审查报告的衔接：** 若未来收到针对 KDE 路径的审查报告，
以本表的实际证据为准校准报告断言；报告与真机冲突时，先复核报告的复现
步骤，再下结论。
