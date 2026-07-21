# UI 架构：Qt Widgets + 独立原生 WebView

## 职责

主窗口、设置、托盘和对话框全部使用 Qt Widgets。HTML 壁纸不嵌入主窗口，而是由独立子进程调用系统网页控件：Windows WebView2、macOS WKWebView、Linux WebKitGTK。

这条边界用于隔离网页崩溃、窗口句柄、桌面层挂载、进程回收和平台权限。`src/ui/` 只发起 Service 调用，不直接操作 WebView 原生对象。

## 禁止边界

- 不在 Widgets 页面中嵌入网页或第二套声明式 UI；
- 不把平台窗口 API 放进共享 UI；
- 不向网页暴露高权限 Python 对象；
- 不随包携带 Qt WebEngine、Chromium 或对应 QML 树；
- 不让 HTML helper 绕过 `MediaService` 的生命周期管理。

## 页面通信

宿主只注入只读的 `window.shangbg` 信息及 pause/resume/options 事件。网页不能直接读取配置文件、执行系统命令或调用任意 Python 方法。

## 新增 UI

新增设置页面优先提取 Controller/Service，而不是继续扩大 `main_window.py`。新增 HTML 能力应扩展公共事件协议，并分别验证三端原生适配器。
