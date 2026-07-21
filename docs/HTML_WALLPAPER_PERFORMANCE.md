# HTML 壁纸：体验、性能与安全边界

## 运行时

HTML 壁纸使用独立 pywebview 子进程：Windows WebView2、macOS WKWebView、Linux WebKitGTK（桌面嵌入当前限 X11）。主 Qt Widgets 进程不携带 Qt WebEngine/QML。

PyInstaller hook 与 Nuitka 构建适配器都只收集目标平台后端。Windows 必须包含 `winforms -> win32 + edgechromium` 以及 Python.NET/CLR 链；Nuitka 4.1.3 的内置 pywebview 插件会漏掉 `win32`，因此本项目在 HTML 构建中禁用该插件并显式加入完整链。Qt WebEngine/QML 在两个后端中均被排除。

## 性能行为

- 默认 30 FPS，可选 15、24、30、45、60 或不限；
- `requestAnimationFrame` 统一调度主要覆盖 Canvas、WebGL 和常规页面动画；
- Worker、频繁定时器和视频解码不保证受帧率限制；
- 配置 watcher 仍按固定间隔检查文件状态，但只有 mtime/大小变化时才重新读取和解析 JSON；
- 页面来源使用 SHA-256 派生的独立 WebView profile，避免不同远程或本地壁纸共享 Cookie、缓存和存储；
- 自动暂停采用多屏覆盖判定、滞回和页面加载后的状态重放；
- 页面暂停时取消底层帧请求、暂停 CSS 动画和媒体，并隐藏原生渲染控件。

## 输入策略

Windows WebView 位于桌面图标层下方，默认不额外设置鼠标穿透；Linux/macOS 的桌面级窗口由平台适配器自动采用必要的输入透明。界面不再暴露容易产生平台误解的统一“鼠标穿透”开关。

## 安全策略

- 只接受现有 `.html/.htm` 本地文件或格式正确的 HTTP(S) URL；
- 下载关闭、SSL 错误不可忽略、外部链接交给系统浏览器；
- 本地文件访问仅在来源确实为 `file:` 时打开；
- 日志描述会隐藏 URL 中的敏感查询参数；
- 打包功能清单缺失或损坏时不自动启用 HTML；
- Wayland 在没有明确实验性允许变量时拒绝启动不安全/不完整的桌面嵌入路径。

HTML 本身仍是主动内容。远程页面可执行脚本和发起网络请求，因此只应使用可信来源；需要离线、可重复发布时应优先使用经过审计的本地 HTML 资源。

## 真机验证

发布前至少覆盖：本地 Unicode/空格路径、远程 HTTPS、多个页面 profile 隔离、桌面图标点击、全屏遮挡暂停、多显示器、Explorer/Finder/桌面会话重启、WebView runtime 缺失和 helper 异常退出。
