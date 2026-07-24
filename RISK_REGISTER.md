# 风险登记簿

评分：概率 P（1-5）× 影响 I（1-5），按乘积降序。

| 排名 | 风险 | P | I | 分数 | 触发信号 | 缓解/回滚 |
|---:|---|---:|---:|---:|---|---|
| 1 | KDE Wayland 下 mpvpaper 多屏或层级异常 | 4 | 4 | 16 | 视频覆盖图标、只出现在一屏、退出残留 | 标 best-effort；真机脚本验证；失败回退 X11；未来 Plasma 插件 |
| 2 | Portal backend 缺失/旧版/用户拒绝 | 4 | 3 | 12 | `CreateSession`/`BindShortcuts` 非 0、接口不存在 | capability 显示未就绪；保留 X11 pynput；UI 给安装/授权提示 |
| 3 | bundled libmpv 与 FFmpeg/libplacebo 组合不一致 | 3 | 4 | 12 | 启动崩溃、符号缺失、硬解失败 | runtime manifest + hash + `--version`/加载探测；按目标架构独立 bundle |
| 4 | i18n UI 重建造成信号重复或窗口状态丢失 | 3 | 3 | 9 | 切换后重复回调、选项重置 | 单一订阅入口；关闭时 unsubscribe；保留现有重建函数；专项 GUI 回归 |
| 5 | Portal 线程在 D-Bus 请求中无法及时停止 | 2 | 4 | 8 | 退出后后台线程延迟、会话未 Close | daemon thread、stop flag、Session.Close；后续用预测 request path 消除竞态 |
| 6 | Windows WorkerW 回归 | 2 | 4 | 8 | 桌面右键/图标/窗口层级异常 | 只加 legacy adapter，不改 Windows video；Windows CI/真机回归清单 |
| 7 | macOS AVPlayer 被 mpv 抽象误判 | 2 | 3 | 6 | pause/property 返回不一致 | 可选操作返回 False；不要求 macOS 引入 libmpv；保持原 native backend |
| 8 | `.json` 被再次错误压缩 | 2 | 3 | 6 | build 时 magic bytes 为 gzip | `assert_plain_json_resources()` 早失败；loader 仅为旧包恢复 |
| 9 | HTML WebKitGTK 内存/进程压力 | 3 | 2 | 6 | RSS 增长、页面崩溃 | 保持 feature 可裁剪；限制自动启动；后续测量 1h/8h RSS |
| 10 | `dbus-next` 供应链/打包遗漏 | 2 | 3 | 6 | frozen 包 import 失败 | feature 动态模块显式包含；requirements 锁范围；构建 smoke |
| 11 | mpv JSON IPC 被同用户其他进程滥用 | 2 | 3 | 6 | 未授权控制播放 | socket 位于用户运行目录、随机/进程化名称、清理旧 socket；不暴露网络端口 |
| 12 | Qt/Python 版本组合变化 | 2 | 3 | 6 | PySide 导入或部署失败 | CI 固定支持矩阵；以 3.10+ 为最低；升级前跑完整 GUI smoke |
| 13 | XFCE/GNOME Wayland 被误报支持 | 2 | 2 | 4 | capability 显示 ready 但无法运行 | compositor token 白名单；GNOME 保持 unsupported 视频；文档明确边界 |
| 14 | 截图回归在不同主题/DPI 噪声过大 | 3 | 1 | 3 | 像素 diff 大量误报 | 固定主题、字体、DPI；关键区域阈值而非整图严格相等 |

## 跨平台回归清单

- Windows：WorkerW attach/detach、桌面右键、Explorer 重启、named pipe、开机自启、单实例。
- macOS：AVPlayer 桌面层、AppKit 权限、退出清理、签名/notarization、sandbox 路径。
- Linux：XDG autostart、tray、KDE static CLI/D-Bus、X11 xwinwrap、Wayland mpvpaper、Portal consent/清理。

## 性能基线建议

- libmpv ctypes：控制调用 P95 < 10 ms；观察回调不阻塞 GUI。
- mpvpaper/KWin：1080p30 与 4K60 分别记录 CPU/GPU、掉帧和 compositor frame time。
- WebKitGTK：空白页、动画页运行 1h/8h 的 RSS 与子进程数量。
- i18n：切换重建耗时与峰值 RSS，目标普通配置下 < 500 ms 且无重复 signal。
