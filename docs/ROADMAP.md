# 优化路线图

路线图只记录当前未完成工作。已完成内容进入 [`CHANGELOG.md`](../CHANGELOG.md)，不再把阶段交接报告长期留在仓库。

## P0：原生发布闭环

- 在 Windows、Linux X11、macOS 真机分别验证 full standalone；
- 覆盖静态壁纸、视频、HTML、桌面图标输入、热键、托盘、退出恢复和重启；
- 在干净机器检查 Qt Widgets 插件、三端系统 WebView、libmpv/mpv、暂停淡入淡出、中文/空格路径；
- macOS 完成签名、隔离属性和 Spaces 行为验证；
- 明确记录 Wayland 当前不支持的能力，不用模拟结果代替真实 Portal。

完成标准：每个平台都有可复现的构建命令、原生验收记录和已知限制。

## P1：降低 MainWindow 职责

按风险从低到高提取：

1. About / Log 页面；
2. Bing 页面与同步 Controller；
3. Wallpaper 页面；
4. 页面刷新、配置绑定和异步 Worker；
5. 托盘动作与主窗口页面共享同一 Action/Policy。

原则：保持现有 Widgets 外观，不进行 UI 框架重写；每次只迁移一个职责并下调架构预算。

## P2：构建与发布一致性

- 提取 Nuitka/PyInstaller 共用 preflight 和 BuildRequest；
- 统一 artifact manifest、SHA-256、构建命令与环境元数据；
- 对模块化组合增加最小运行自检；
- GitHub Actions 恢复后，再启用两工具 × 三平台 dry-run 与各平台原生 standalone；
- standalone 作为发布基线，onefile 只在前者通过后评估。

## P3：平台能力

- 研究并实现 Wayland XDG GlobalShortcuts Portal；
- 继续收敛 Linux 桌面环境探测和 X11 失败诊断；
- 完善 macOS 权限说明、签名和窗口层级测试；
- 将 Capability 显示与实际 Backend 探测统一，避免文案漂移。

## P4：性能与可维护性

- 测量启动导入、MainWindow 构造、冷启动右键动作、文件扫描和动态运行时启动；
- 对非关键页面延迟创建；
- 为大型列表引入 Model/Delegate，避免重复创建控件；
- 清理仍有版本阶段含义但无运行价值的注释；
- 定期运行翻译死键审计，并保持用户提示与真实能力一致。

## 每次发布最低验证

涉及构建时执行两种构建器的本机 dry-run；涉及原生能力时必须在目标系统补充真实验收。内部自动化测试与审计记录应作为独立验证材料保存，不混入精简源码 ZIP。
