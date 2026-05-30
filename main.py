# ShangBackground PySide6 主入口
# 旧版 Tkinter 界面已保留在 legacy_tk_main.py，可用 --legacy-tk 启动。
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import threading
import shutil
import subprocess
import plistlib
from pathlib import Path

import legacy_tk_main as core


def _run_legacy_tk() -> int:
    return core.main() or 0


def _is_action_launch(args: argparse.Namespace) -> bool:
    return any([
        getattr(args, "previous", False),
        getattr(args, "next", False),
        getattr(args, "random", False),
        getattr(args, "show", False),
        bool(getattr(args, "set_wallpaper", None)),
        getattr(args, "jump_to_wallpaper", False),
    ])


def _parse_early_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--legacy-tk", action="store_true", help="启动旧版 Tkinter 界面")
    parser.add_argument("--previous", action="store_true")
    parser.add_argument("--next", action="store_true")
    parser.add_argument("--random", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--hide", action="store_true")
    parser.add_argument("--jump-to-wallpaper", action="store_true")
    parser.add_argument("--set-wallpaper", dest="set_wallpaper")
    return parser.parse_known_args()[0]


def _open_sidebar_standalone() -> None:
    """
    独立进程模式（由 --jump-to-wallpaper 触发）：
    创建最小 QApplication → 显示 PySide6 侧边栏 → exec → 退出。
    此函数在 QApplication 创建之前可安全调用。
    """
    import sys as _sys
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    folder = core.config.get("slide_folder", "")
    current = core.config.get("current_wallpaper", "") or core.get_current_wallpaper()

    if core.IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("xxdz.ShangBackground")
        except Exception:
            pass
    _app = QApplication.instance() or QApplication(_sys.argv)
    icon_path = os.path.join(core.BASE_DIR, "img", "LOGO.ico")
    if os.path.exists(icon_path):
        _app.setWindowIcon(QIcon(icon_path))

    if not folder or not os.path.isdir(folder):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, "提示喵", "请先在软件中设置壁纸文件夹")
        return

    try:
        from wallpaper_sidebar import WallpaperSidebar

        def _switch(path: str) -> None:
            try:
                core.push_wallpaper(path)
                core.set_wallpaper_direct(path, "侧边栏切换")
            except Exception as exc:
                core.log(f"侧边栏切换壁纸失败: {exc}")

        sidebar_log = core.config.get("log_file_path") if core.config.get("log_enabled", False) else None
        sidebar = WallpaperSidebar(
            None, folder, current, sidebar_log,
            show_message=lambda t, m: None,
            switch_wallpaper=_switch,
        )
        # 侧边栏关闭时退出独立 QApplication
        sidebar.closed.connect(_app.quit)
        _app.exec()

    except Exception as exc:
        core.log(f"打开侧边栏失败: {exc}")
        import traceback
        core.log(traceback.format_exc())


def _handle_action_args(args: argparse.Namespace) -> bool:
    """在 PySide6 GUI 创建前处理右键菜单/命令行动作。"""
    if args.hide:
        core.hide_window = True
    if args.previous:
        core.previous_wallpaper()
        return True
    if args.next:
        core.next_wallpaper()
        return True
    if args.random:
        core.random_wallpaper()
        return True
    if args.set_wallpaper:
        target = args.set_wallpaper
        if os.path.isfile(target):
            core.push_wallpaper(target)
            core.set_wallpaper_direct(target, "命令行设置")
        else:
            core.log(f"壁纸文件不存在: {target}")
        return True
    if args.jump_to_wallpaper:
        _open_sidebar_standalone()
        return True
    if args.show and core.IS_WINDOWS:
        if core.activate_existing_instance(show_notice=False):
            return True
    return False


try:
    from PySide6.QtCore import QObject, QTimer, Qt, Signal, QSize, QPropertyAnimation, QEasingCurve, QUrl, QEvent, QRect, QPoint
    from PySide6.QtGui import QAction, QColor, QIcon, QPixmap, QDesktopServices, QPainter, QImageReader
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QColorDialog,
        QComboBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QSystemTrayIcon,
        QTabWidget,
        QTextEdit,
        QListWidget,
        QListWidgetItem,
        QListView,
        QProgressBar,
        QGraphicsOpacityEffect,
        QStackedWidget,
        QScroller,
        QScrollerProperties,
        QVBoxLayout,
        QWidget,
    )
    PYSIDE_AVAILABLE = True
except Exception as exc:  # pragma: no cover - 运行环境缺 PySide6 时回退
    PYSIDE_AVAILABLE = False
    PYSIDE_IMPORT_ERROR = exc




def _dependency_availability_for_pyside() -> dict:
    """供 PySide6 主入口使用的依赖可用性表。未列出的依赖由 dependency_prompt 自行探测。"""
    return {
        "PIL": getattr(core, "Image", None) is not None,
        "requests": getattr(core, "requests", None) is not None,
        "numpy": bool(getattr(core, "HAS_NUMPY", False)),
        "PySide6": PYSIDE_AVAILABLE,
        "psutil": getattr(core, "psutil", None) is not None,
        "httpx": importlib.util.find_spec("httpx") is not None,
    }

if PYSIDE_AVAILABLE:
    class PreviewCanvas(QFrame):
        """首页壁纸预览画布。

        只显示真实壁纸缩略图，不再把桌面示意图/文字遮罩叠到预览图上。
        画布尺寸由自身控制，路径、历史列表和按钮全部放在画布外部，避免挤压时互相覆盖。
        """

        PREVIEW_HEIGHT = 280

        def __init__(self, parent=None):
            super().__init__(parent)
            self._pixmap = QPixmap()
            self._caption = "实际壁纸预览"
            self.setMinimumSize(360, self.PREVIEW_HEIGHT)
            self.setMaximumHeight(self.PREVIEW_HEIGHT)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setToolTip("当前壁纸预览：不叠加文字或桌面示意图")

        def sizeHint(self):  # noqa: N802 - Qt API
            return QSize(500, self.PREVIEW_HEIGHT)

        def _load_scaled_pixmap(self, image_path: str) -> QPixmap:
            """按预览控件尺寸读取缩略图，避免每次刷新都把原图完整解码到界面线程。"""
            target = self.size().boundedTo(QSize(900, self.PREVIEW_HEIGHT))
            if target.width() <= 0 or target.height() <= 0:
                target = QSize(500, self.PREVIEW_HEIGHT)
            reader = QImageReader(image_path)
            reader.setAutoTransform(True)
            original = reader.size()
            if original.isValid():
                scaled = original.scaled(target, Qt.KeepAspectRatio)
                if scaled.isValid():
                    reader.setScaledSize(scaled)
            image = reader.read()
            return QPixmap.fromImage(image) if not image.isNull() else QPixmap()

        def set_preview(self, image_path: str = "", overlay_path: str = ""):
            # overlay_path 参数保留为兼容旧调用，但故意不再使用，避免文字/示意图压到壁纸预览上。
            if image_path and os.path.exists(image_path):
                self._pixmap = self._load_scaled_pixmap(image_path)
                self._caption = os.path.basename(image_path) or "实际壁纸预览"
            else:
                self._pixmap = QPixmap()
                self._caption = "暂无预览"
            self.update()

        def paintEvent(self, event):  # noqa: N802 - Qt API
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)

            rect = self.rect().adjusted(0, 0, -1, -1)
            painter.fillRect(rect, QColor("#f8fafc"))
            painter.setPen(QColor("#d8dee9"))
            painter.drawRoundedRect(rect, 12, 12)

            image_rect = rect.adjusted(14, 14, -14, -44)
            painter.fillRect(image_rect, QColor("#eef2f7"))
            painter.setPen(QColor("#e5e7eb"))
            painter.drawRoundedRect(image_rect, 8, 8)

            if not self._pixmap.isNull():
                scaled = self._pixmap if self._pixmap.size().boundedTo(image_rect.size()) == self._pixmap.size() else self._pixmap.scaled(image_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = image_rect.x() + (image_rect.width() - scaled.width()) // 2
                y = image_rect.y() + (image_rect.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
            else:
                painter.setPen(QColor("#6b7280"))
                painter.drawText(image_rect, Qt.AlignCenter, "暂无预览")

            caption_rect = rect.adjusted(14, rect.height() - 34, -14, -8)
            painter.setPen(QColor("#64748b"))
            metrics = painter.fontMetrics()
            caption = metrics.elidedText(self._caption, Qt.ElideMiddle, caption_rect.width())
            painter.drawText(caption_rect, Qt.AlignLeft | Qt.AlignVCenter, caption)

            painter.end()


    class QtRootShim(QObject):
        """给 legacy_tk_main 提供最小 root.after/deiconify 兼容层。"""

        def __init__(self, window: "ShangBackgroundWindow"):
            super().__init__(window)
            self.window = window
            self._timers: dict[str, QTimer] = {}
            self._seq = 0

        def after(self, ms: int, func=None, *args):
            self._seq += 1
            timer_id = f"qt-after-{self._seq}"
            timer = QTimer(self)
            timer.setSingleShot(True)

            def _fire():
                self._timers.pop(timer_id, None)
                if callable(func):
                    func(*args)

            timer.timeout.connect(_fire)
            self._timers[timer_id] = timer
            timer.start(max(0, int(ms)))
            return timer_id

        def after_cancel(self, timer_id):
            timer = self._timers.pop(str(timer_id), None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()

        def deiconify(self):
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()

        def state(self, value=None):
            if value == "normal":
                self.deiconify()
            return "normal"

        def lift(self):
            self.window.raise_()

        def focus_force(self):
            self.window.activateWindow()

        def winfo_id(self):
            return int(self.window.winId())

        def winfo_exists(self):
            return True

        def winfo_screenwidth(self):
            screen = QApplication.primaryScreen()
            return screen.geometry().width() if screen else 1920

        def winfo_screenheight(self):
            screen = QApplication.primaryScreen()
            return screen.geometry().height() if screen else 1080

        def quit(self):
            QApplication.instance().quit()

        def destroy(self):
            self.window.close()


    class BingSyncWorker(QObject):
        finished = Signal(bool, str, str)

        def __init__(self, resolution: str):
            super().__init__()
            self.resolution = resolution

        def run(self):
            try:
                from bing_downloader import BingDownloader
                downloader = BingDownloader()
                info = downloader.fetch_wallpaper_info(resolution=self.resolution)
                if not info:
                    self.finished.emit(False, "获取 Bing 壁纸信息失败", "")
                    return
                path = downloader.download_wallpaper(info)
                if not path:
                    self.finished.emit(False, "下载 Bing 壁纸失败", "")
                    return
                core.push_wallpaper(path)
                core.set_wallpaper_direct(path, "Bing 今日壁纸")
                self.finished.emit(True, f"已设置 Bing 壁纸：{info.title} / {info.resolution}（{info.resolution_source}）", path)
            except Exception as e:
                self.finished.emit(False, f"同步 Bing 壁纸失败：{e}", "")


    class ShangBackgroundWindow(QMainWindow):
        log_signal = Signal(str)
        bing_result_signal = Signal(bool, str, str)

        def __init__(self):
            super().__init__()
            self.setWindowTitle("上一个桌面背景")
            self.setMinimumSize(1020, 700)
            self._settings_dialog = None
            self._closing_for_exit = False
            self.tray: QSystemTrayIcon | None = None
            self._bing_worker_thread: threading.Thread | None = None
            self._animations = []
            self._first_show_anim = True
            self._last_preview_path = ""
            self._history_single_click_timer = QTimer(self)
            self._history_single_click_timer.setSingleShot(True)
            self._pending_history_item = None
            self._init_icon()
            self._apply_theme()
            self._build_ui()
            self._apply_button_sizes()
            self.log_signal.connect(self.append_log)
            self.bing_result_signal.connect(self._on_bing_finished)
            self._install_core_log_bridge()
            self.refresh_from_config()
            self.update_preview()
            self._preview_refresh_timer = QTimer(self)
            self._preview_refresh_timer.setInterval(1200)
            self._preview_refresh_timer.timeout.connect(self.update_preview_if_changed)
            self._preview_refresh_timer.start()
            QTimer.singleShot(450, self.apply_native_window_effect)
            QTimer.singleShot(700, self.maybe_show_auto_start_prompt)
            if core.config.get("tray_icon", True):
                self.create_or_update_tray()

        def _init_icon(self):
            self.icon_path = os.path.join(core.BASE_DIR, "img", "LOGO.ico")
            self.app_icon = QIcon(self.icon_path) if os.path.exists(self.icon_path) else QIcon()
            if not self.app_icon.isNull():
                QApplication.setWindowIcon(self.app_icon)
                self.setWindowIcon(self.app_icon)

        def _install_core_log_bridge(self):
            self._orig_log = core.log

            def _log(msg):
                self._orig_log(msg)
                try:
                    self.log_signal.emit(str(msg))
                except Exception:
                    pass

            core.log = _log

        def append_log(self, text: str):
            if hasattr(self, "log_box"):
                self.log_box.append(text)

        def _img_path(self, name: str) -> str:
            return os.path.join(core.BASE_DIR, "img", name)

        def _add_status_animation(self):
            """轻量状态淡入动画：只动画一个 QLabel 的 opacity，避免对壁纸预览做高频重绘。"""
            try:
                effect = QGraphicsOpacityEffect(self.status_label)
                self.status_label.setGraphicsEffect(effect)
                anim = QPropertyAnimation(effect, b"opacity", self)
                anim.setDuration(220)
                anim.setStartValue(0.35)
                anim.setEndValue(1.0)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                self._animations.append(anim)
                anim.start()
            except Exception:
                pass

        def set_status(self, text: str):
            self.status_label.setText(text)
            self._add_status_animation()

        def showEvent(self, event):
            super().showEvent(event)
            if not getattr(self, "_first_show_anim", False):
                return
            self._first_show_anim = False
            # 只做透明度动画，不再动画 geometry。部分窗口管理器会在首次显示后重新布局，
            # geometry 动画结束点会与系统最终位置打架，表现为标题/窗口整体错位。
            try:
                self.setWindowOpacity(0.0)
                fade = QPropertyAnimation(self, b"windowOpacity", self)
                fade.setDuration(220)
                fade.setStartValue(0.0)
                fade.setEndValue(1.0)
                fade.setEasingCurve(QEasingCurve.OutCubic)
                self._animations.append(fade)
                fade.start()
            except Exception:
                self.setWindowOpacity(1.0)

        def _apply_theme(self):
            """应用 UI 主题：始终使用 Qt 默认原生样式，不覆盖 QSS。"""
            app = QApplication.instance()
            if app is not None:
                # 只清除主窗口自身的样式，不触碰应用级全局样式表。
                self.setStyleSheet("")
            self._theme_stylesheet = ""
            self.setMinimumSize(1020, 700)
            if hasattr(self, "_apply_button_sizes"):
                self._apply_button_sizes()

        def apply_native_window_effect(self):
            """默认 Qt 原生主题不额外套玻璃效果。"""
            return

        def _enable_touch_scrolling(self, widget, *, horizontal: bool = False):
            """为可滚动控件启用单指惯性滑动；只触及 viewport，避免影响按钮点击。"""
            try:
                target = widget.viewport() if hasattr(widget, "viewport") else widget
                target.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
                QScroller.grabGesture(target, QScroller.ScrollerGestureType.TouchGesture)
                scroller = QScroller.scroller(target)
                props = scroller.scrollerProperties()
                props.setScrollMetric(QScrollerProperties.ScrollMetric.OvershootDragResistanceFactor, 0.18)
                props.setScrollMetric(QScrollerProperties.ScrollMetric.OvershootScrollDistanceFactor, 0.10)
                props.setScrollMetric(QScrollerProperties.ScrollMetric.DecelerationFactor, 0.10)
                if not horizontal:
                    props.setScrollMetric(QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy, QScrollerProperties.OvershootPolicy.OvershootAlwaysOff)
                props.setScrollMetric(QScrollerProperties.ScrollMetric.VerticalOvershootPolicy, QScrollerProperties.OvershootPolicy.OvershootWhenScrollable)
                scroller.setScrollerProperties(props)
            except Exception:
                pass

        def _apply_button_sizes(self):
            # 按钮缩小一号，最小高度34px
            for btn in self.findChildren(QPushButton):
                btn.setMinimumHeight(34)
                btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        def _build_ui(self):
            central = QWidget(self)
            self.setCentralWidget(central)
            layout = QVBoxLayout(central)

            header = QHBoxLayout()
            logo_path = self._img_path("txtlogo.png")
            if os.path.exists(logo_path):
                logo = QLabel()
                pix = QPixmap(logo_path)
                if not pix.isNull():
                    logo.setPixmap(pix.scaledToHeight(64, Qt.SmoothTransformation))
                    logo.setMaximumWidth(360)
                    logo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                    header.addWidget(logo)
                else:
                    header.addWidget(QLabel("上一个桌面背景"))
            else:
                title = QLabel("上一个桌面背景")
                title.setStyleSheet("font-size: 22px; font-weight: 700;")
                header.addWidget(title)
            header.addStretch(1)
            self.status_label = QLabel("PySide6 GUI 已启用")
            self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.status_label.setMinimumWidth(160)
            self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            header.addWidget(self.status_label, 1)
            layout.addLayout(header)

            self.tabs = QTabWidget()
            layout.addWidget(self.tabs, 1)
            self.wallpaper_tab_page = self._wallpaper_tab()
            self.tabs.addTab(self.wallpaper_tab_page, "首页")
            self.tabs.addTab(self._bing_tab(), "Bing 壁纸")
            self.tabs.addTab(self._about_tab(), "关于 / 资源")
            self.tabs.addTab(self._log_tab(), "日志")

        def _wallpaper_tab(self):
            page = QWidget()
            outer = QVBoxLayout(page)
            outer.setContentsMargins(0, 0, 0, 0)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            self._enable_touch_scrolling(scroll)
            outer.addWidget(scroll)

            body = QWidget()
            scroll.setWidget(body)
            root = QHBoxLayout(body)
            root.setContentsMargins(14, 14, 14, 14)
            root.setSpacing(16)
            left = QVBoxLayout()
            right = QVBoxLayout()
            left.setSpacing(12)
            right.setSpacing(12)
            root.addLayout(left, 4)
            root.addLayout(right, 5)

            mode_box = QGroupBox("壁纸模式")
            form = QFormLayout(mode_box)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(10)
            self.mode_combo = QComboBox()
            self.mode_combo.addItems(["幻灯片放映", "图片", "纯色", "渐变"])
            self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
            form.addRow("当前模式", self.mode_combo)
            self.fit_combo = QComboBox()
            self.fit_combo.addItems(["填充", "适应", "拉伸", "居中", "平铺"])
            self.fit_combo.currentTextChanged.connect(self.on_fit_changed)
            form.addRow("适应方式", self.fit_combo)
            left.addWidget(mode_box)

            slide_box = QGroupBox("幻灯片放映")
            slide_layout = QGridLayout(slide_box)
            slide_layout.setHorizontalSpacing(10)
            slide_layout.setVerticalSpacing(10)
            self.folder_edit = QLineEdit()
            self.folder_edit.setPlaceholderText("首次使用请先选择壁纸文件夹")
            self.btn_browse_folder = QPushButton("选择文件夹")
            btn_browse_folder = self.btn_browse_folder
            btn_browse_folder.clicked.connect(self.choose_folder)
            slide_layout.addWidget(QLabel("文件夹"), 0, 0)
            slide_layout.addWidget(self.folder_edit, 0, 1, 1, 2)
            slide_layout.addWidget(btn_browse_folder, 0, 3)
            self.seconds_spin = QSpinBox()
            self.seconds_spin.setRange(5, 24 * 3600)
            self.seconds_spin.setSuffix(" 秒")
            self.seconds_spin.valueChanged.connect(self.on_seconds_changed)
            slide_layout.addWidget(QLabel("间隔"), 1, 0)
            slide_layout.addWidget(self.seconds_spin, 1, 1)
            self.shuffle_check = QCheckBox("随机顺序")
            self.shuffle_check.toggled.connect(self.on_shuffle_changed)
            slide_layout.addWidget(self.shuffle_check, 1, 2, 1, 2)

            nav_row = QGridLayout()
            nav_row.setHorizontalSpacing(8)
            nav_row.setVerticalSpacing(8)
            self.btn_prev = btn_prev = QPushButton("上一张")
            self.btn_next = btn_next = QPushButton("下一张")
            self.btn_random = btn_random = QPushButton("随机")
            self.btn_start = btn_start = QPushButton("应用并播放")
            self.btn_stop = btn_stop = QPushButton("暂停")
            for btn in (btn_prev, btn_next, btn_random, btn_start, btn_stop):
                btn.setMinimumHeight(42)
            nav_row.addWidget(btn_prev, 0, 0)
            nav_row.addWidget(btn_next, 0, 1)
            nav_row.addWidget(btn_random, 0, 2)
            nav_row.addWidget(btn_start, 1, 0, 1, 2)
            nav_row.addWidget(btn_stop, 1, 2)
            btn_prev.clicked.connect(lambda: self.run_core(core.previous_wallpaper))
            btn_next.clicked.connect(lambda: self.run_core(core.next_wallpaper))
            btn_random.clicked.connect(lambda: self.run_core(core.random_wallpaper))
            btn_start.clicked.connect(lambda: self.run_core(core.start_slideshow))
            btn_stop.clicked.connect(lambda: self.run_core(core.stop_slideshow))
            slide_layout.addLayout(nav_row, 2, 0, 1, 4)
            self.slide_box = slide_box
            left.addWidget(slide_box)

            single_box = QGroupBox("单张图片")
            single_layout = QHBoxLayout(single_box)
            single_layout.setSpacing(10)
            self.single_edit = QLineEdit()
            self.single_edit.setPlaceholderText("选择一张图片作为桌面背景")
            self.btn_single = QPushButton("选择并设置")
            btn_single = self.btn_single
            btn_single.clicked.connect(self.choose_single_image)
            single_layout.addWidget(self.single_edit, 1)
            single_layout.addWidget(btn_single)
            self.single_box = single_box
            left.addWidget(single_box)

            color_box = QGroupBox("纯色 / 渐变")
            color_layout = QGridLayout(color_box)
            color_layout.setHorizontalSpacing(10)
            color_layout.setVerticalSpacing(10)
            self.solid_btn = QPushButton("选择纯色")
            self.grad1_btn = QPushButton("渐变颜色 1")
            self.grad2_btn = QPushButton("渐变颜色 2")
            self.angle_spin = QSpinBox()
            self.angle_spin.setRange(0, 360)
            self.angle_spin.setSuffix("°")
            self.solid_btn.clicked.connect(self.choose_solid_color)
            self.grad1_btn.clicked.connect(lambda: self.choose_gradient_color(1))
            self.grad2_btn.clicked.connect(lambda: self.choose_gradient_color(2))
            # 渐变角度不再实时触发渲染，改为手动点击"应用"按钮
            self.angle_apply_btn = QPushButton("应用")
            self.angle_apply_btn.setFixedWidth(60)
            self.angle_apply_btn.clicked.connect(self.on_gradient_apply)
            self.angle_spin.valueChanged.connect(self.on_gradient_changed)
            color_layout.addWidget(self.solid_btn, 0, 0)
            color_layout.addWidget(self.grad1_btn, 0, 1)
            color_layout.addWidget(self.grad2_btn, 0, 2)
            color_layout.addWidget(QLabel("渐变角度"), 1, 0)
            color_layout.addWidget(self.angle_spin, 1, 1)
            color_layout.addWidget(self.angle_apply_btn, 1, 2)
            self.color_box = color_box
            left.addWidget(color_box)

            # 首页右侧不再堆叠大量按钮：所有操作集中到左侧“快捷操作”，右侧只保留预览和历史缩略图。
            action_box = QGroupBox("快捷操作")
            action_layout = QVBoxLayout(action_box)
            action_layout.setContentsMargins(12, 18, 12, 12)
            action_layout.setSpacing(8)
            action_tabs = QTabWidget()
            action_tabs.setDocumentMode(True)
            action_layout.addWidget(action_tabs)

            quick_page = QWidget()
            quick_grid = QGridLayout(quick_page)
            quick_grid.setContentsMargins(4, 6, 4, 4)
            quick_grid.setHorizontalSpacing(10)
            quick_grid.setVerticalSpacing(10)
            btn_refresh = QPushButton("刷新预览")
            btn_refresh.clicked.connect(self.update_preview)
            btn_open_folder = QPushButton("打开当前文件夹")
            btn_open_folder.clicked.connect(self.open_current_folder)
            btn_sidebar = QPushButton("跳转到壁纸")
            btn_sidebar.clicked.connect(self.open_wallpaper_sidebar)
            self.settings_icon_btn = QPushButton("全局设置")
            self.settings_icon_btn.setToolTip("打开全局设置窗口")
            self.settings_icon_btn.setIconSize(QSize(24, 24))
            settings_icon = self._img_path("settings_icon.png")
            if os.path.exists(settings_icon):
                self.settings_icon_btn.setIcon(QIcon(settings_icon))
            self.settings_icon_btn.clicked.connect(self.open_global_settings_from_home)
            for btn in (btn_refresh, btn_open_folder, btn_sidebar, self.settings_icon_btn):
                btn.setMinimumHeight(44)
            quick_grid.addWidget(btn_refresh, 0, 0)
            quick_grid.addWidget(btn_open_folder, 0, 1)
            quick_grid.addWidget(btn_sidebar, 1, 0)
            quick_grid.addWidget(self.settings_icon_btn, 1, 1)
            action_tabs.addTab(quick_page, "常用")

            maint_page = QWidget()
            mh = QGridLayout(maint_page)
            mh.setContentsMargins(4, 6, 4, 4)
            mh.setHorizontalSpacing(10)
            mh.setVerticalSpacing(10)
            btn_save = QPushButton("保存配置")
            btn_save.clicked.connect(lambda: self.run_core(core.save_config))
            btn_admin_home = QPushButton("管理员重启")
            btn_admin_home.clicked.connect(self.restart_as_admin)
            btn_restore_home = QPushButton("恢复启动前壁纸")
            btn_restore_home.clicked.connect(lambda: self.run_core(core.restore_session_original_wallpaper))
            btn_legacy_home = QPushButton("旧版设置")
            btn_legacy_home.clicked.connect(self.start_legacy_process)
            btn_exit_home = QPushButton("退出程序")
            btn_exit_home.clicked.connect(self.exit_app)
            for btn in (btn_save, btn_admin_home, btn_restore_home, btn_legacy_home, btn_exit_home):
                btn.setMinimumHeight(44)
            mh.addWidget(btn_save, 0, 0)
            mh.addWidget(btn_restore_home, 0, 1)
            mh.addWidget(btn_admin_home, 1, 0)
            mh.addWidget(btn_legacy_home, 1, 1)
            mh.addWidget(btn_exit_home, 2, 0, 1, 2)
            action_tabs.addTab(maint_page, "维护")
            left.addWidget(action_box)
            left.addStretch(1)

            preview_box = QGroupBox("当前壁纸")
            preview_box.setMinimumWidth(400)
            pv_layout = QVBoxLayout(preview_box)
            pv_layout.setContentsMargins(12, 18, 12, 12)
            pv_layout.setSpacing(9)
            self.preview_canvas = PreviewCanvas()
            pv_layout.addWidget(self.preview_canvas)

            self.current_label = QLineEdit("")
            self.current_label.setReadOnly(True)
            self.current_label.setPlaceholderText("未检测到当前壁纸")
            self.current_label.setMinimumHeight(34)
            self.current_label.setToolTip("当前壁纸路径，可选中文本复制")
            pv_layout.addWidget(self.current_label)

            hist_row = QHBoxLayout()
            hist_row.setSpacing(8)
            hist_title = QLabel("之前使用过的壁纸（单击后应用，双击打开位置）")
            hist_title.setProperty("muted", True)
            hist_title.setStyleSheet("font-size: 12px;")
            hist_row.addWidget(hist_title)
            hist_row.addStretch(1)
            pv_layout.addLayout(hist_row)
            self.history_list = QListWidget()
            self.history_list.setObjectName("HistoryThumbs")
            self.history_list.setViewMode(QListView.ViewMode.IconMode)
            self.history_list.setFlow(QListView.Flow.LeftToRight)
            self.history_list.setResizeMode(QListView.ResizeMode.Adjust)
            self.history_list.setMovement(QListView.Movement.Static)
            self.history_list.setWrapping(False)
            self.history_list.setSpacing(8)
            self.history_list.setIconSize(QSize(112, 70))
            self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.history_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.history_list.setFixedHeight(106)
            self.history_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._history_single_click_timer.timeout.connect(self.apply_pending_history_item)
            self.history_list.itemClicked.connect(self.schedule_apply_history_item)
            self.history_list.itemDoubleClicked.connect(self.open_history_item_location)
            self._enable_touch_scrolling(self.history_list, horizontal=True)
            pv_layout.addWidget(self.history_list)

            right.addWidget(preview_box)

            # 右键菜单设置（从全局设置移到首页右侧）
            ctx_box = QGroupBox("右键菜单")
            ctx_layout = QVBoxLayout(ctx_box)
            ctx_layout.setContentsMargins(10, 18, 10, 10)
            ctx_layout.setSpacing(6)
            self.ctx_prev = QCheckBox()
            self.ctx_next = QCheckBox()
            self.ctx_random = QCheckBox()
            self.ctx_jump = QCheckBox()
            self.ctx_prev.toggled.connect(lambda v: self._update_ctx("ctx_last_wallpaper", v))
            self.ctx_next.toggled.connect(lambda v: self._update_ctx("ctx_next_wallpaper", v))
            self.ctx_random.toggled.connect(lambda v: self._update_ctx("ctx_random_wallpaper", v))
            self.ctx_jump.toggled.connect(lambda v: self._update_ctx("ctx_jump_to_wallpaper", v))
            for cb in (self.ctx_prev, self.ctx_next, self.ctx_random, self.ctx_jump):
                ctx_layout.addWidget(cb)
            self._refresh_context_shortcut_labels()
            btn_reg_ctx = QPushButton("注册右键菜单")
            btn_reg_ctx.clicked.connect(self.register_context_with_prompt)
            ctx_layout.addWidget(btn_reg_ctx)
            right.addWidget(ctx_box)

            right.addStretch(1)
            about_row = QHBoxLayout()
            about_row.addStretch(1)
            about_box = QVBoxLayout()
            about_box.setAlignment(Qt.AlignCenter)
            self.about_sprite_btn = QPushButton()
            self.about_sprite_btn.setToolTip("悬停播放，点击打开关于")
            self.about_sprite_btn.setFlat(True)
            self.about_sprite_btn.setFixedSize(56, 56)
            self.about_sprite_btn.clicked.connect(self.show_about_dialog)
            self.about_sprite_btn.installEventFilter(self)
            about_box.addWidget(self.about_sprite_btn, alignment=Qt.AlignCenter)
            bili_link = QLabel('<a href="https://space.bilibili.com/3461569935575626?spm_id_from=333.788">b站@小小电子xxdz</a>')
            bili_link.setOpenExternalLinks(True)
            bili_link.setAlignment(Qt.AlignCenter)
            bili_link.setStyleSheet("font-size: 12px;")
            about_box.addWidget(bili_link, alignment=Qt.AlignCenter)
            about_row.addLayout(about_box)
            right.addLayout(about_row)
            self._setup_about_sprite_animation()
            return page

        def _settings_tab(self):
            page = QWidget()
            root = QHBoxLayout(page)
            root.setContentsMargins(10, 10, 10, 10)
            root.setSpacing(12)

            nav = QListWidget()
            nav.setObjectName("SettingsNav")
            nav.setFixedWidth(190)
            nav.setSpacing(4)
            nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._enable_touch_scrolling(nav)
            root.addWidget(nav)

            stack = QStackedWidget()
            root.addWidget(stack, 1)

            def add_settings_page(title: str, widget: QWidget):
                item = QListWidgetItem(title)
                item.setSizeHint(QSize(170, 48))
                nav.addItem(item)
                stack.addWidget(widget)

            shell_page = QWidget()
            shell_layout = QVBoxLayout(shell_page)
            shell_layout.setContentsMargins(0, 0, 0, 0)
            shell_layout.setSpacing(12)

            runtime = QGroupBox("后台、托盘与主题")
            form = QFormLayout(runtime)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(10)
            self.bg_check = QCheckBox("关闭窗口时隐藏到托盘")
            self.bg_check.toggled.connect(self.on_background_changed)
            self.auto_start_check = QCheckBox("开机自启动")
            self.auto_start_check.setToolTip("启用后会在启动文件夹生成 ShangBackground.vbs，开机时自动后台启动。")
            self.auto_start_check.toggled.connect(self.on_auto_start_changed)
            self.tray_check = QCheckBox("显示系统托盘图标")
            self.tray_check.toggled.connect(self.on_tray_changed)
            self.tray_action = QComboBox()
            self.tray_action_map = {
                "下一张壁纸": "next",
                "上一张壁纸": "previous",
                "随机壁纸": "random",
                "打开主界面": "show",
                "跳转到当前壁纸": "jump",
                "无操作": "none",
            }
            for label, action in self.tray_action_map.items():
                self.tray_action.addItem(label, action)
            self.tray_action.currentIndexChanged.connect(self.on_tray_action_changed)
            self.tray_notify_check = QCheckBox("最小化到托盘时显示通知")
            self.tray_notify_check.toggled.connect(self.on_tray_notify_changed)
            form.addRow(self.bg_check)
            form.addRow(self.auto_start_check)
            form.addRow(self.tray_check)
            form.addRow("单击托盘图标", self.tray_action)
            form.addRow(self.tray_notify_check)
            shell_layout.addWidget(runtime)
            shell_layout.addStretch(1)
            add_settings_page("外观与后台", shell_page)

            tray_page = QWidget()
            tray_layout_outer = QVBoxLayout(tray_page)
            tray_layout_outer.setContentsMargins(0, 0, 0, 0)
            tray_layout_outer.setSpacing(12)
            tray_menu_box = QGroupBox("托盘右键菜单项")
            tray_menu_layout = QGridLayout(tray_menu_box)
            tray_menu_layout.setHorizontalSpacing(12)
            tray_menu_layout.setVerticalSpacing(10)
            self.tray_menu_labels = {
                "show": "打开主界面", "previous": "上一张", "next": "下一张", "random": "随机",
                "bing": "同步 Bing", "jump": "跳转壁纸", "about": "关于", "exit": "退出",
            }
            self.tray_menu_checks = {}
            for i, (action, label) in enumerate(self.tray_menu_labels.items()):
                cb = QCheckBox(label)
                cb.toggled.connect(self.on_tray_menu_changed)
                self.tray_menu_checks[action] = cb
                tray_menu_layout.addWidget(cb, i // 3, i % 3)
            tray_layout_outer.addWidget(tray_menu_box)
            tray_hint = QLabel("建议触屏设备保留“打开主界面”“跳转壁纸”和“退出”，减少托盘菜单层级。")
            tray_hint.setWordWrap(True)
            tray_hint.setProperty("muted", True)
            tray_layout_outer.addWidget(tray_hint)
            tray_layout_outer.addStretch(1)
            add_settings_page("托盘菜单", tray_page)

            shortcut_page = QWidget()
            shortcut_layout = QVBoxLayout(shortcut_page)
            shortcut_layout.setContentsMargins(0, 0, 0, 0)
            shortcut_layout.setSpacing(12)
            shortcut_box = QGroupBox("桌面右键菜单快捷键")
            shortcut_form = QFormLayout(shortcut_box)
            shortcut_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            shortcut_form.setHorizontalSpacing(14)
            shortcut_form.setVerticalSpacing(10)
            self.ctx_shortcut_edits = {}
            self.ctx_shortcut_current_labels = {}
            for action, label, default_key, _cfg_key, _widget_name in self._context_action_defs():
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(8)
                edit = QLineEdit(self._context_hotkey(action))
                edit.setPlaceholderText(default_key)
                edit.setMinimumWidth(150)
                edit.setToolTip("可填写单个字母/数字作为右键菜单助记键，也可填写 Ctrl+Alt+N 这类显示用组合键。")
                current = QLabel(self._context_hotkey_display(action))
                current.setProperty("muted", True)
                edit.editingFinished.connect(lambda action=action, edit=edit: self.on_context_hotkey_changed(action, edit))
                self.ctx_shortcut_edits[action] = edit
                self.ctx_shortcut_current_labels[action] = current
                row_layout.addWidget(edit, 1)
                row_layout.addWidget(current)
                shortcut_form.addRow(label, row)
            shortcut_layout.addWidget(shortcut_box)
            shortcut_hint = QLabel("修改后请点击首页“注册右键菜单”，Windows 资源管理器右键菜单才会同步新显示。")
            shortcut_hint.setWordWrap(True)
            shortcut_hint.setProperty("muted", True)
            shortcut_layout.addWidget(shortcut_hint)
            shortcut_layout.addStretch(1)
            add_settings_page("右键快捷键", shortcut_page)

            nav.currentRowChanged.connect(stack.setCurrentIndex)
            nav.setCurrentRow(0)
            return page

        def _bing_tab(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            info = QLabel("Bing 壁纸已并入播放模式：可同步 1-8 张到缓存目录，也可以把缓存目录设为幻灯片来源。")
            info.setWordWrap(True)
            layout.addWidget(info)

            cache_box = QGroupBox("缓存与同步")
            grid = QGridLayout(cache_box)
            self.bing_cache_edit = QLineEdit(core.config.get("bing_cache_dir", "") or "")
            self.bing_cache_edit.setPlaceholderText("首次使用请先选择或填写 Bing 壁纸缓存目录")
            btn_cache = QPushButton("选择缓存位置")
            btn_cache.clicked.connect(self.choose_bing_cache_dir)
            self.bing_resolution = QComboBox()
            self.bing_resolution.addItems(["auto", "1920x1080", "2560x1440", "3840x2160", "1366x768", "1920x1200"])
            self.bing_count_spin = QSpinBox()
            self.bing_count_spin.setRange(1, 8)
            self.bing_count_spin.setValue(int(core.config.get("bing_sync_count", 1)))
            self.bing_sync_btn = QPushButton("同步并设为壁纸")
            self.bing_sync_btn.clicked.connect(lambda: self.sync_bing_wallpaper(set_latest=True))
            self.bing_multi_btn = QPushButton("只同步多张")
            self.bing_multi_btn.clicked.connect(lambda: self.sync_bing_wallpaper(set_latest=False))
            self.bing_play_btn = QPushButton("设为幻灯片来源")
            self.bing_play_btn.clicked.connect(self.use_bing_cache_as_slideshow)
            self.bing_saveas_btn = QPushButton("另存当前选中")
            self.bing_saveas_btn.clicked.connect(self.save_selected_bing_as)
            grid.addWidget(QLabel("缓存目录"), 0, 0)
            grid.addWidget(self.bing_cache_edit, 0, 1, 1, 3)
            grid.addWidget(btn_cache, 0, 4)
            grid.addWidget(QLabel("分辨率"), 1, 0)
            grid.addWidget(self.bing_resolution, 1, 1)
            grid.addWidget(QLabel("同步张数"), 1, 2)
            grid.addWidget(self.bing_count_spin, 1, 3)
            grid.addWidget(self.bing_sync_btn, 2, 0)
            grid.addWidget(self.bing_multi_btn, 2, 1)
            grid.addWidget(self.bing_play_btn, 2, 2)
            grid.addWidget(self.bing_saveas_btn, 2, 3)
            layout.addWidget(cache_box)

            self.bing_progress = QProgressBar()
            self.bing_progress.setRange(0, 100)
            self.bing_progress.setValue(0)
            layout.addWidget(self.bing_progress)
            self.bing_status = QLabel("未同步")
            self.bing_status.setWordWrap(True)
            layout.addWidget(self.bing_status)
            self.bing_list = QListWidget()
            self.bing_list.itemSelectionChanged.connect(self.on_bing_selection_changed)
            layout.addWidget(self.bing_list, 1)
            self.refresh_bing_cache_list()
            return page

        def _about_tab(self):
            page = QWidget()
            layout = QVBoxLayout(page)

            title = QLabel("上一个桌面背景 v1.2.1")
            title.setStyleSheet("font-size: 24px; font-weight: 700;")
            layout.addWidget(title)

            desc = QLabel("一个用于快速切换、随机和管理桌面背景的小工具。")
            desc.setWordWrap(True)
            layout.addWidget(desc)

            links = QLabel(
                '原项目：<a href="https://github.com/xxdz-Official/ShangBackground">xxdz-Official/ShangBackground</a><br>'
                'github反馈地址：<a href="https://github.com/purrfecto114-lgtm/ShangBackground">purrfecto114-lgtm/ShangBackground</a><br>'
                '作者主页：<a href="https://space.bilibili.com/3461569935575626?spm_id_from=333.788">b站@小小电子xxdz</a><br>'
                '<a href="app://shishe">[施舍]</a>　'
                '<a href="app://about-window">关于图片</a>　'
                '<a href="app://about-dialog">关于窗口</a>'
            )
            links.setOpenExternalLinks(False)
            links.linkActivated.connect(self._handle_about_link)
            links.setWordWrap(True)
            layout.addWidget(links)

            note = QLabel("右键菜单命令会直接调用本程序的 --previous、--next、--random、--jump-to-wallpaper 和 --set-wallpaper 参数。")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addStretch(1)
            return page

        def _log_tab(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(10)

            path_box = QGroupBox("日志设置")
            path_grid = QGridLayout(path_box)
            path_grid.setHorizontalSpacing(10)
            path_grid.setVerticalSpacing(10)
            self.log_enabled_check = QCheckBox("记录日志到文件（默认关闭）")
            self.log_enabled_check.setChecked(bool(core.config.get("log_enabled", False)))
            self.log_enabled_check.toggled.connect(self.on_log_enabled_changed)
            self.log_path_edit = QLineEdit(core.config.get("log_file_path", "") or "")
            self.log_path_edit.setReadOnly(True)
            self.log_path_edit.setPlaceholderText("首次开启日志时请选择保存路径")
            btn_choose_log = QPushButton("选择日志路径")
            btn_choose_log.clicked.connect(self.choose_log_file_path)
            path_grid.addWidget(self.log_enabled_check, 0, 0, 1, 2)
            path_grid.addWidget(self.log_path_edit, 1, 0)
            path_grid.addWidget(btn_choose_log, 1, 1)
            layout.addWidget(path_box)

            controls = QHBoxLayout()
            btn_load = QPushButton("刷新日志")
            btn_load.clicked.connect(self.load_log_file)
            btn_clear_view = QPushButton("清空显示")
            btn_clear_view.clicked.connect(lambda: self.log_box.clear())
            btn_delete = QPushButton("清空/删除日志文件")
            btn_delete.clicked.connect(self.delete_log_file)
            btn_export = QPushButton("导出日志")
            btn_export.clicked.connect(self.export_log_file)
            for w in (btn_load, btn_clear_view, btn_delete, btn_export):
                controls.addWidget(w)
            controls.addStretch(1)
            layout.addLayout(controls)

            self.log_box = QTextEdit()
            self.log_box.setReadOnly(True)
            layout.addWidget(self.log_box, 1)
            self.load_log_file()
            return page

        def _setup_about_sprite_animation(self):
            """about.png 是三态竖排精灵图；移植旧版 Tkinter 的平滑 crossfade。"""
            if not hasattr(self, "about_sprite_btn"):
                return
            path = self._img_path("about.png")
            self._about_frames = []
            if os.path.exists(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    frame_h = pix.width()
                    count = max(1, pix.height() // frame_h)
                    for i in range(count):
                        frame = pix.copy(0, i * frame_h, pix.width(), frame_h).scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        self._about_frames.append(frame)
            if not self._about_frames:
                self.about_sprite_btn.setText("关于")
                return
            self._about_state = 0
            self._about_anim_step = 0
            self._about_anim_from = self._about_frames[0]
            self._about_anim_to = self._about_frames[0]
            self.about_sprite_btn.setIcon(QIcon(self._about_frames[0]))
            self.about_sprite_btn.setIconSize(QSize(48, 48))
            self._about_anim_timer = QTimer(self)
            self._about_anim_timer.setInterval(18)
            self._about_anim_timer.timeout.connect(self._advance_about_crossfade)

        def _blend_about_frames(self, start: QPixmap, target: QPixmap, ratio: float) -> QPixmap:
            out = QPixmap(start.size())
            out.fill(Qt.transparent)
            painter = QPainter(out)
            painter.setOpacity(1.0)
            painter.drawPixmap(0, 0, start)
            painter.setOpacity(max(0.0, min(1.0, ratio)))
            painter.drawPixmap(0, 0, target)
            painter.end()
            return out

        def _fade_about_sprite_to(self, state: int):
            if not getattr(self, "_about_frames", None):
                return
            state = max(0, min(state, len(self._about_frames) - 1))
            if state == getattr(self, "_about_state", 0) and not self._about_anim_timer.isActive():
                return
            self._about_anim_timer.stop()
            self._about_anim_from = self._about_frames[getattr(self, "_about_state", 0)]
            self._about_anim_to = self._about_frames[state]
            self._about_target_state = state
            self._about_anim_step = 0
            self._about_anim_timer.start()

        def _advance_about_crossfade(self):
            steps = 10
            self._about_anim_step += 1
            ratio = self._about_anim_step / steps
            if ratio >= 1:
                self._about_anim_timer.stop()
                self._about_state = getattr(self, "_about_target_state", 0)
                self.about_sprite_btn.setIcon(QIcon(self._about_frames[self._about_state]))
                return
            self.about_sprite_btn.setIcon(QIcon(self._blend_about_frames(self._about_anim_from, self._about_anim_to, ratio)))

        def eventFilter(self, obj, event):
            if getattr(self, "about_sprite_btn", None) is obj:
                if event.type() == QEvent.Type.Enter:
                    self._fade_about_sprite_to(1)
                elif event.type() == QEvent.Type.Leave:
                    self._fade_about_sprite_to(0)
                elif event.type() == QEvent.Type.MouseButtonPress:
                    self._fade_about_sprite_to(2)
                elif event.type() == QEvent.Type.MouseButtonRelease:
                    self._fade_about_sprite_to(1)
            return super().eventFilter(obj, event)

        def _app_command(self, *args: str) -> list[str]:
            """源码运行和 PyInstaller onedir 运行都能打开同一个入口。"""
            if getattr(sys, "frozen", False):
                return [sys.executable, *args]
            return [sys.executable, os.path.join(core.BASE_DIR, "main.py"), *args]

        def open_url(self, url: str):
            QDesktopServices.openUrl(QUrl(url))

        def _handle_about_link(self, link: str):
            if link == "app://shishe":
                self.open_shishe_image()
            elif link == "app://about-window":
                self.open_local_image("about-window.png")
            elif link == "app://about-dialog":
                self.show_about_dialog()
            else:
                self.open_url(link)

        def _default_log_path(self) -> str:
            return os.path.join(str(Path.home()), "ShangBackground_wallpaper_debug.log")

        def _log_file_path(self) -> str:
            return core.config.get("log_file_path", "") or ""

        def choose_log_file_path(self):
            default = self._log_file_path() or self._default_log_path()
            dest, _ = QFileDialog.getSaveFileName(self, "选择日志保存路径", default, "日志文件 (*.log *.txt);;所有文件 (*.*)")
            if not dest:
                return False
            core.config["log_file_path"] = dest
            core.save_config()
            if hasattr(self, "log_path_edit"):
                self.log_path_edit.setText(dest)
            self.set_status(f"日志路径已设置：{dest}")
            return True

        def on_log_enabled_changed(self, checked: bool):
            if checked and not self._log_file_path():
                if not self.choose_log_file_path():
                    self.log_enabled_check.blockSignals(True)
                    self.log_enabled_check.setChecked(False)
                    self.log_enabled_check.blockSignals(False)
                    core.config["log_enabled"] = False
                    core.save_config()
                    self.set_status("已取消开启日志")
                    return
            core.config["log_enabled"] = bool(checked)
            core.save_config()
            self.set_status("日志文件记录已开启" if checked else "日志文件记录已关闭")
            self.load_log_file()

        def load_log_file(self):
            if not hasattr(self, "log_box"):
                return
            path = self._log_file_path()
            self.log_box.clear()
            if hasattr(self, "log_path_edit"):
                self.log_path_edit.setText(path)
            if not core.config.get("log_enabled", False) and not path:
                self.log_box.setPlainText("日志默认关闭。需要记录文件日志时，请先开启日志并选择保存路径。")
                return
            if not path:
                self.log_box.setPlainText("尚未设置日志路径。")
                return
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        self.log_box.setPlainText(f.read()[-120000:])
                except Exception as e:
                    self.log_box.setPlainText(f"读取日志失败：{e}")
            else:
                self.log_box.setPlainText("暂无日志文件。开启日志后，新日志会写入所选路径。")

        def delete_log_file(self):
            path = self._log_file_path()
            if not path:
                QMessageBox.information(self, "日志", "尚未设置日志路径。")
                return
            try:
                if os.path.exists(path):
                    os.remove(path)
                if hasattr(self, "log_box"):
                    self.log_box.clear()
                self.set_status("日志文件已删除")
            except Exception as e:
                QMessageBox.warning(self, "日志", f"删除日志失败：{e}")

        def export_log_file(self):
            src = self._log_file_path()
            default = self._default_log_path()
            dest, _ = QFileDialog.getSaveFileName(self, "导出日志", default, "日志文件 (*.log *.txt);;所有文件 (*.*)")
            if not dest:
                return
            try:
                if src and os.path.exists(src):
                    shutil.copyfile(src, dest)
                else:
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(self.log_box.toPlainText() if hasattr(self, "log_box") else "")
                self.set_status(f"日志已导出：{dest}")
            except Exception as e:
                QMessageBox.warning(self, "日志", f"导出日志失败：{e}")

        def open_local_image(self, name: str):
            path = self._img_path(name)
            if os.path.exists(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            else:
                QMessageBox.warning(self, "资源缺失", f"找不到图片：{name}")

        def open_shishe_image(self):
            self.open_local_image("shishe.png")

        def refresh_from_config(self):
            cfg = core.config
            self.mode_combo.setCurrentText(cfg.get("mode", "幻灯片放映"))
            self.fit_combo.setCurrentText(cfg.get("fit_mode", "填充"))
            self.folder_edit.setText(cfg.get("slide_folder", ""))
            self.seconds_spin.setValue(int(cfg.get("slide_seconds", 300)))
            self.shuffle_check.setChecked(bool(cfg.get("shuffle", False)))
            self.single_edit.setText(cfg.get("single_image", ""))
            self.angle_spin.setValue(int(cfg.get("gradient_angle", 60)))
            self._paint_button(self.solid_btn, cfg.get("solid_color", "#4facfe"))
            self._paint_button(self.grad1_btn, cfg.get("solid_color", "#4facfe"))
            self._paint_button(self.grad2_btn, cfg.get("gradient_color2", "#00f2fe"))
            if hasattr(self, "ctx_prev"):
                ctx_widgets = (self.ctx_prev, self.ctx_next, self.ctx_random, self.ctx_jump)
                for widget in ctx_widgets:
                    widget.blockSignals(True)
                self.ctx_prev.setChecked(bool(cfg.get("ctx_last_wallpaper", False)))
                self.ctx_next.setChecked(bool(cfg.get("ctx_next_wallpaper", False)))
                self.ctx_random.setChecked(bool(cfg.get("ctx_random_wallpaper", False)))
                self.ctx_jump.setChecked(bool(cfg.get("ctx_jump_to_wallpaper", False)))
                for widget in ctx_widgets:
                    widget.blockSignals(False)
                self._refresh_context_shortcut_labels()
            if hasattr(self, "ctx_shortcut_edits"):
                for action, edit in self.ctx_shortcut_edits.items():
                    edit.blockSignals(True)
                    edit.setText(self._context_hotkey(action))
                    edit.blockSignals(False)
                self._refresh_context_shortcut_labels()

            # 全局设置页是按需创建的；首次启动时这些控件尚不存在，不能强行访问。
            settings_widgets = ("bg_check", "auto_start_check", "tray_check", "tray_notify_check")
            if all(hasattr(self, name) for name in settings_widgets):
                widgets = tuple(getattr(self, name) for name in settings_widgets)
                for widget in widgets:
                    widget.blockSignals(True)
                self.bg_check.setChecked(bool(cfg.get("run_in_background", True)))
                self.auto_start_check.setChecked(bool(cfg.get("auto_start", False)))
                self.tray_check.setChecked(bool(cfg.get("tray_icon", True)))
                self.tray_notify_check.setChecked(bool(cfg.get("tray_notify", True)))
                for widget in widgets:
                    widget.blockSignals(False)

            if hasattr(self, "tray_menu_checks"):
                menu_items = cfg.get("tray_menu_items") or ["show", "previous", "next", "random", "bing", "jump", "about", "exit"]
                if menu_items and isinstance(menu_items[0], dict):
                    menu_items = [item.get("action") for item in menu_items if item.get("enabled", True)]
                for action, cb in self.tray_menu_checks.items():
                    cb.blockSignals(True)
                    cb.setChecked(action in menu_items)
                    cb.blockSignals(False)

            if hasattr(self, "tray_action"):
                self.tray_action.blockSignals(True)
                wanted_action = cfg.get("tray_click_action", "next")
                idx = self.tray_action.findData(wanted_action)
                fallback = self.tray_action.findData("next")
                self.tray_action.setCurrentIndex(idx if idx >= 0 else fallback)
                self.tray_action.blockSignals(False)
            if hasattr(self, "bing_cache_edit"):
                self.bing_cache_edit.setText(cfg.get("bing_cache_dir", "") or "")
            self.update_control_states()

        def update_control_states(self):
            mode = core.config.get("mode", self.mode_combo.currentText())
            is_slide = mode == "幻灯片放映"
            is_image = mode == "图片"
            is_solid = mode == "纯色"
            is_gradient = mode == "渐变"

            for w in (self.folder_edit, self.btn_browse_folder, self.seconds_spin, self.shuffle_check,
                      self.btn_prev, self.btn_next, self.btn_random, self.btn_start, self.btn_stop):
                w.setEnabled(is_slide)
            self.single_edit.setEnabled(is_image)
            self.btn_single.setEnabled(is_image)
            self.solid_btn.setEnabled(is_solid)
            self.grad1_btn.setEnabled(is_gradient)
            self.grad2_btn.setEnabled(is_gradient)
            self.angle_spin.setEnabled(is_gradient)

            # 当前模式无关的区域标题也变灰，避免误以为设置会立即生效。
            self.slide_box.setEnabled(is_slide)
            self.single_box.setEnabled(is_image)
            self.color_box.setEnabled(is_solid or is_gradient)

        def run_core(self, fn, *args):
            try:
                result = fn(*args)
                core.save_config()
                self._schedule_preview_refresh()
                self.set_status("操作完成")
                return result
            except Exception as e:
                self.set_status("操作失败")
                QMessageBox.warning(self, "错误", str(e))
                core.log(f"PySide6 操作失败: {e}")
                return None

        def on_mode_changed(self, text):
            core.config["mode"] = text
            core.save_config()
            self.update_control_states()
            self.set_status("正在切换模式…")

            def _apply_mode():
                if text == "幻灯片放映":
                    self.run_core(core.restart_slideshow)
                elif text == "图片":
                    img = core.config.get("single_image")
                    if img and os.path.exists(img):
                        self.run_core(core.set_wallpaper, img, "切换单张图片模式")
                    else:
                        self._schedule_preview_refresh()
                elif text == "纯色":
                    self.run_core(core.apply_solid)
                elif text == "渐变":
                    self.apply_gradient_wallpaper()
                else:
                    self._schedule_preview_refresh()

            QTimer.singleShot(0, _apply_mode)

        def on_fit_changed(self, text):
            core.config["fit_mode"] = text
            self.run_core(core.set_fit_mode, text)

        def choose_folder(self):
            folder = QFileDialog.getExistingDirectory(self, "选择壁纸文件夹", self.folder_edit.text() or str(Path.home()))
            if not folder:
                return
            core.config["slide_folder"] = folder
            self.folder_edit.setText(folder)
            core.config["mode"] = "幻灯片放映"
            self.mode_combo.setCurrentText("幻灯片放映")
            core.save_config()
            self.run_core(core.restart_slideshow)

        def on_seconds_changed(self, value):
            core.config["slide_seconds"] = int(value)
            core.save_config()
            if core.config.get("mode") == "幻灯片放映":
                core.restart_slideshow()

        def on_shuffle_changed(self, checked):
            core.config["shuffle"] = bool(checked)
            core.save_config()
            if core.config.get("mode") == "幻灯片放映":
                core.restart_slideshow()

        def choose_single_image(self):
            path, _ = QFileDialog.getOpenFileName(self, "选择图片", str(Path.home()), "图片 (*.jpg *.jpeg *.png *.bmp *.gif)")
            if not path:
                return
            core.config["single_image"] = path
            core.config["mode"] = "图片"
            self.single_edit.setText(path)
            self.mode_combo.setCurrentText("图片")
            core.save_config()
            self.run_core(core.set_wallpaper, path, "单张图片")

        def choose_solid_color(self):
            color = QColorDialog.getColor(QColor(core.config.get("solid_color", "#4facfe")), self, "选择纯色")
            if not color.isValid():
                return
            value = color.name()
            core.config["solid_color"] = value
            self._paint_button(self.solid_btn, value)
            core.save_config()
            if core.config.get("mode") == "纯色":
                self.run_core(core.apply_solid)

        def choose_gradient_color(self, index: int):
            key = "solid_color" if index == 1 else "gradient_color2"
            color = QColorDialog.getColor(QColor(core.config.get(key, "#4facfe")), self, "选择渐变颜色")
            if not color.isValid():
                return
            core.config[key] = color.name()
            self._paint_button(self.grad1_btn if index == 1 else self.grad2_btn, color.name())
            core.save_config()
            if core.config.get("mode") == "渐变":
                self.apply_gradient_wallpaper()

        def on_gradient_changed(self, value):
            """渐变角度值变化时仅保存配置，不立即渲染，防止重复渲染卡顿。"""
            core.config["gradient_angle"] = int(value)
            core.save_config()

        def on_gradient_apply(self):
            """点击"应用"按钮后，才真正渲染渐变壁纸。"""
            if core.config.get("mode") == "渐变":
                self.apply_gradient_wallpaper()

        def apply_gradient_wallpaper(self):
            c1 = core.config.get("solid_color", "#4facfe")
            c2 = core.config.get("gradient_color2", "#00f2fe")
            angle = int(core.config.get("gradient_angle", 60))
            path = core.create_gradient_wallpaper(c1, c2, angle)
            if path:
                self.run_core(core.set_wallpaper_direct, path, "渐变")

        def _paint_button(self, btn: QPushButton, color: str):
            btn.setStyleSheet(f"QPushButton {{ background: {color}; border: 1px solid #94a3b8; border-radius: 6px; }}")

        def _context_action_defs(self):
            return [
                ("previous", "上一张壁纸", "U", "ctx_last_wallpaper", "ctx_prev"),
                ("next", "下一张壁纸", "N", "ctx_next_wallpaper", "ctx_next"),
                ("random", "随机壁纸", "3", "ctx_random_wallpaper", "ctx_random"),
                ("jump", "跳转到壁纸", "V", "ctx_jump_to_wallpaper", "ctx_jump"),
            ]

        def _context_hotkey(self, action: str) -> str:
            default_map = {item[0]: item[2] for item in self._context_action_defs()}
            return str(core.config.get(f"hotkey_{action}", default_map.get(action, "")) or "").strip()

        def _context_hotkey_display(self, action: str) -> str:
            raw = self._context_hotkey(action)
            if not raw:
                return "当前：无"
            parts = [p.strip() for p in raw.replace("-", "+").split("+") if p.strip()]
            names = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "meta": "Win"}
            display = "+".join(names.get(p.lower(), p.upper() if len(p) == 1 else p) for p in parts)
            return f"当前：{display}"

        def _context_checkbox_label(self, action: str, label: str) -> str:
            return f"{label}（{self._context_hotkey_display(action).replace('当前：', '')}）"

        def _refresh_context_shortcut_labels(self):
            for action, label, _default_key, _cfg_key, widget_name in self._context_action_defs():
                widget = getattr(self, widget_name, None)
                if widget is not None:
                    widget.setText(self._context_checkbox_label(action, label))
                current_labels = getattr(self, "ctx_shortcut_current_labels", {})
                if action in current_labels:
                    current_labels[action].setText(self._context_hotkey_display(action))

        def on_context_hotkey_changed(self, action: str, edit: QLineEdit):
            value = edit.text().strip().replace(" ", "")
            core.config[f"hotkey_{action}"] = value
            core.save_config()
            edit.setText(value)
            self._refresh_context_shortcut_labels()
            self.set_status("右键菜单快捷键已保存")

        def _update_ctx(self, key, value):
            core.config[key] = bool(value)
            # “设为壁纸”和“设置中心/个性化设置”两项已从右键菜单移除，保持关闭并在注册时清理旧注册表项。
            core.config["ctx_set_wallpaper"] = False
            core.config["ctx_global_settings"] = False
            core.config["ctx_personalize"] = False
            core.save_config()

        def register_context_with_prompt(self):
            if core.IS_WINDOWS and not core.is_windows_admin():
                ret = QMessageBox.question(
                    self,
                    "需要管理员权限",
                    "同步桌面右键菜单需要写入 HKEY_CLASSES_ROOT。是否以管理员身份重启并继续？",
                )
                if ret == QMessageBox.StandardButton.Yes:
                    self.restart_as_admin()
                return
            ok = core.register_context(show_admin_prompt=False)
            QMessageBox.information(self, "右键菜单", "同步完成" if ok else "同步失败或已跳过")

        def open_global_settings_from_home(self):
            """首页齿轮入口：使用独立窗口展示全局设置。"""
            dlg = getattr(self, "_settings_dialog", None)
            if dlg is not None:
                try:
                    dlg.show()
                    dlg.raise_()
                    dlg.activateWindow()
                    return
                except RuntimeError:
                    self._settings_dialog = None

            dialog = QDialog(self)
            self._settings_dialog = dialog
            dialog.setWindowTitle("全局设置")
            icon_path = self._img_path("settings_icon.png")
            if os.path.exists(icon_path):
                dialog.setWindowIcon(QIcon(icon_path))
            elif not getattr(self, "app_icon", QIcon()).isNull():
                dialog.setWindowIcon(self.app_icon)
            dialog.setModal(False)
            dialog.resize(860, 620)
            dialog.setMinimumSize(780, 560)
            if getattr(self, "_theme_stylesheet", ""):
                dialog.setStyleSheet(self._theme_stylesheet)
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(10, 10, 10, 10)
            settings_page = self._settings_tab()
            layout.addWidget(settings_page)
            self.refresh_from_config()
            dialog.destroyed.connect(lambda *_: setattr(self, "_settings_dialog", None))
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            try:
                effect = QGraphicsOpacityEffect(dialog)
                dialog.setGraphicsEffect(effect)
                anim = QPropertyAnimation(effect, b"opacity", dialog)
                anim.setDuration(180)
                anim.setStartValue(0.25)
                anim.setEndValue(1.0)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                self._animations.append(anim)
                anim.start()
            except Exception:
                pass
            self.set_status("已打开全局设置")

        def get_pyqt_startup_folder_path(self):
            try:
                return core.get_startup_folder_path_windows()
            except Exception:
                return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")

        def _startup_launch_command(self) -> str:
            if core.is_frozen():
                return f'"{sys.executable}" --hide'
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            launcher = pythonw if os.path.exists(pythonw) else sys.executable
            return f'"{launcher}" "{os.path.abspath(__file__)}" --hide'

        def set_auto_start(self, enable: bool):
            if core.IS_MACOS:
                agents_dir = os.path.expanduser("~/Library/LaunchAgents")
                plist_path = os.path.join(agents_dir, "com.xxdz.shangbackground.plist")
                if enable:
                    os.makedirs(agents_dir, exist_ok=True)
                    plist = {
                        "Label": "com.xxdz.shangbackground",
                        "ProgramArguments": [sys.executable, "--hide"] if core.is_frozen() else [sys.executable, os.path.abspath(__file__), "--hide"],
                        "RunAtLoad": True,
                        "WorkingDirectory": core.BASE_DIR,
                    }
                    with open(plist_path, "wb") as f:
                        plistlib.dump(plist, f)
                    subprocess.run(["launchctl", "unload", plist_path], capture_output=True, timeout=5)
                    subprocess.run(["launchctl", "load", plist_path], capture_output=True, timeout=5)
                    core.log(f"macOS 开机自启动已启用: {plist_path}")
                else:
                    subprocess.run(["launchctl", "unload", plist_path], capture_output=True, timeout=5)
                    if os.path.exists(plist_path):
                        os.remove(plist_path)
                    core.log("macOS 开机自启动已禁用")
                return

            if not core.IS_WINDOWS:
                raise RuntimeError("当前系统暂不支持通过 GUI 设置开机自启动")

            startup_folder = self.get_pyqt_startup_folder_path()
            vbs_path = os.path.join(startup_folder, core.STARTUP_VBS_NAME)
            legacy_vbs_paths = [
                os.path.join(startup_folder, name)
                for name in getattr(core, "LEGACY_STARTUP_VBS_NAMES", ["PowerOn.vbs"])
                if name != core.STARTUP_VBS_NAME
            ]
            if enable:
                os.makedirs(startup_folder, exist_ok=True)
                command = self._startup_launch_command().replace('"', '""')
                vbs_lines = [
                    "' 此文件仅用于开机自启动 ShangBackground。",
                    "' ShangBackground.vbs - 开机自启动时创建标志文件，然后隐藏启动主程序。",
                    "Dim flagFile",
                    'flagFile = CreateObject("WScript.Shell").ExpandEnvironmentStrings("%TEMP%") & "\\WallpaperHideFlag.tmp"',
                    "Dim objFSO",
                    "Set objFSO = CreateObject(\"Scripting.FileSystemObject\")",
                    "Dim objFile",
                    "Set objFile = objFSO.CreateTextFile(flagFile, True)",
                    "objFile.Write \"T\"",
                    "objFile.Close",
                    "Dim shell",
                    "Set shell = CreateObject(\"WScript.Shell\")",
                    f'shell.Run "{command}", 0, False',
                    "Set shell = Nothing",
                    "Set objFile = Nothing",
                    "Set objFSO = Nothing",
                ]
                with open(vbs_path, "w", encoding="gb2312", errors="ignore") as f:
                    f.write("\r\n".join(vbs_lines))
                for legacy_path in legacy_vbs_paths:
                    if os.path.exists(legacy_path):
                        try:
                            os.remove(legacy_path)
                            core.log(f"已删除旧启动 VBS: {legacy_path}")
                        except Exception as cleanup_error:
                            core.log(f"删除旧启动 VBS 失败: {cleanup_error}")
                core.log(f"开机自启动已启用：{vbs_path}")
            else:
                for path_to_remove in [vbs_path] + legacy_vbs_paths:
                    if path_to_remove and os.path.exists(path_to_remove):
                        os.remove(path_to_remove)
                        core.log(f"已删除启动文件夹中的 VBS: {path_to_remove}")
                core.log("开机自启动已禁用")

        def on_auto_start_changed(self, checked):
            try:
                self.set_auto_start(bool(checked))
                core.config["auto_start"] = bool(checked)
                core.config["auto_start_prompt_shown"] = True
                core.save_config()
                self.set_status("开机自启动已启用" if checked else "开机自启动已关闭")
            except Exception as e:
                if hasattr(self, "auto_start_check"):
                    self.auto_start_check.blockSignals(True)
                    self.auto_start_check.setChecked(not bool(checked))
                    self.auto_start_check.blockSignals(False)
                QMessageBox.warning(self, "开机自启动", f"设置开机自启动失败：{e}")

        def on_tray_notify_changed(self, checked):
            core.config["tray_notify"] = bool(checked)
            core.save_config()

        def maybe_show_auto_start_prompt(self):
            if core.config.get("auto_start_prompt_shown", False):
                return
            if core.hide_window:
                return
            self.show_auto_start_prompt()

        def show_auto_start_prompt(self):
            dialog = QDialog(self)
            dialog.setWindowTitle("开机自启动建议")
            dialog.setModal(True)
            dialog.setFixedSize(520, 300)
            if os.path.exists(self.icon_path):
                dialog.setWindowIcon(QIcon(self.icon_path))
            if getattr(self, "_theme_stylesheet", ""):
                dialog.setStyleSheet(self._theme_stylesheet)

            main = QVBoxLayout(dialog)
            main.setContentsMargins(22, 20, 22, 18)
            main.setSpacing(10)
            top_row = QHBoxLayout()
            hello_label = QLabel()
            hello_path = self._img_path("hello.png")
            if os.path.exists(hello_path):
                pix = QPixmap(hello_path)
                if not pix.isNull():
                    hello_label.setPixmap(pix.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            hello_label.setFixedSize(104, 104)
            hello_label.setAlignment(Qt.AlignCenter)
            top_row.addWidget(hello_label)
            title = QLabel("您是否想要开机自启动本工具？")
            title.setWordWrap(True)
            title.setStyleSheet("font-size: 18px; font-weight: 700;")
            top_row.addWidget(title, 1)
            main.addLayout(top_row)

            info = QTextEdit()
            info.setReadOnly(True)
            info.setFixedHeight(82)
            info.setText("开机自启动后，软件会后台运行，而且占用资源极少，基本不会影响开机速度 ヾ(≧▽≦*)o\n确定后，此操作可能会被杀毒软件拦截，您可以选择允许或加入白名单。")
            info.setStyleSheet("QTextEdit { border-radius: 8px; padding: 8px; font-size: 13px; }")
            main.addWidget(info)

            buttons = QHBoxLayout()
            buttons.addStretch(1)
            btn_yes = QPushButton("好哒")
            btn_no = QPushButton("不，并不再提示")
            btn_yes.setMinimumHeight(38)
            btn_no.setMinimumHeight(38)
            buttons.addWidget(btn_yes)
            buttons.addWidget(btn_no)
            main.addLayout(buttons)

            def accept_startup():
                try:
                    self.set_auto_start(True)
                    core.config["auto_start"] = True
                    core.config["auto_start_prompt_shown"] = True
                    core.save_config()
                    if hasattr(self, "auto_start_check"):
                        self.auto_start_check.blockSignals(True)
                        self.auto_start_check.setChecked(True)
                        self.auto_start_check.blockSignals(False)
                    self.set_status("开机自启动已启用")
                    dialog.accept()
                except Exception as e:
                    QMessageBox.warning(dialog, "开机自启动", f"设置开机自启动失败：{e}")

            def reject_startup():
                core.config["auto_start"] = False
                core.config["auto_start_prompt_shown"] = True
                core.save_config()
                if hasattr(self, "auto_start_check"):
                    self.auto_start_check.blockSignals(True)
                    self.auto_start_check.setChecked(False)
                    self.auto_start_check.blockSignals(False)
                self.set_status("已跳过开机自启动")
                dialog.reject()

            btn_yes.clicked.connect(accept_startup)
            btn_no.clicked.connect(reject_startup)
            dialog.exec()

        def on_background_changed(self, checked):
            core.config["run_in_background"] = bool(checked)
            core.save_config()

        def on_tray_changed(self, checked):
            core.config["tray_icon"] = bool(checked)
            core.save_config()
            if checked:
                self.create_or_update_tray()
            elif self.tray:
                self.tray.hide()
                self.tray.deleteLater()
                self.tray = None

        def on_tray_action_changed(self, index):
            action = self.tray_action.itemData(index) or "next"
            core.config["tray_click_action"] = action
            core.save_config()
            self.create_or_update_tray()

        def on_tray_menu_changed(self):
            if not hasattr(self, "tray_menu_checks"):
                return
            selected = [action for action, cb in self.tray_menu_checks.items() if cb.isChecked()]
            required = ["show", "exit"]
            for item in required:
                if item not in selected:
                    selected.append(item)
                    self.tray_menu_checks[item].setChecked(True)
            core.config["tray_menu_items"] = selected
            core.save_config()
            self.create_or_update_tray()

        def create_or_update_tray(self):
            if not QSystemTrayIcon.isSystemTrayAvailable():
                core.log("系统托盘不可用，已跳过")
                return
            if self.tray is None:
                icon = QIcon(self.icon_path) if os.path.exists(self.icon_path) else self.windowIcon()
                self.tray = QSystemTrayIcon(icon, self)
                self.tray.activated.connect(self.on_tray_activated)
            else:
                icon = QIcon(self.icon_path) if os.path.exists(self.icon_path) else self.windowIcon()
                self.tray.setIcon(icon)

            labels = {
                "show": "打开设置主界面",
                "previous": "上一张壁纸",
                "next": "下一张壁纸",
                "random": "随机壁纸",
                "bing": "同步 Bing 今日壁纸",
                "jump": "跳转到壁纸",
                "about": "关于",
                "exit": "退出程序",
            }
            defaults = ["show", "previous", "next", "random", "bing", "jump", "about", "exit"]
            actions = core.config.get("tray_menu_items") or defaults
            if isinstance(actions, list) and actions and isinstance(actions[0], dict):
                actions = [item.get("action") for item in actions if item.get("enabled", True)]
            actions = [a for a in actions if a in labels]
            if not actions:
                actions = defaults

            menu = QMenu()
            action_map = {
                "show": self.show_from_tray,
                "previous": lambda: self.run_core(core.previous_wallpaper),
                "next": lambda: self.run_core(core.next_wallpaper),
                "random": lambda: self.run_core(core.random_wallpaper),
                "bing": lambda: self.sync_bing_wallpaper(set_latest=True),
                "jump": self.open_wallpaper_sidebar,
                "about": lambda: QMessageBox.information(self, "关于", "上一个桌面背景\nPySide6 版"),
                "exit": self.exit_app,
            }
            for i, name in enumerate(actions):
                if i and name in {"about", "exit"}:
                    menu.addSeparator()
                menu.addAction(labels[name], action_map[name])
            self.tray.setContextMenu(menu)
            self.tray.setToolTip("上一个桌面背景")
            self.tray.show()

        def on_tray_activated(self, reason):
            if reason == QSystemTrayIcon.Trigger:
                action = core.config.get("tray_click_action", "next")
                if action == "none":
                    return
                if action == "show":
                    self.show_from_tray()
                elif action == "previous":
                    self.run_core(core.previous_wallpaper)
                elif action == "random":
                    self.run_core(core.random_wallpaper)
                elif action == "jump":
                    self.open_wallpaper_sidebar()
                else:
                    self.run_core(core.next_wallpaper)

        def show_from_tray(self):
            self.showNormal()
            self.raise_()
            self.activateWindow()

        def update_preview(self):
            path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
            self._last_preview_path = path or ""
            self.current_label.setText(path or "")
            self.current_label.setToolTip(path or "未检测到当前壁纸")
            self.preview_canvas.set_preview(path if path and os.path.exists(path) else "")
            self.refresh_history_list()

        def update_preview_if_changed(self):
            path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
            hist_len = len(core.config.get("history", []))
            if path != getattr(self, "_last_preview_path", "") or hist_len != getattr(self, "_last_history_len", -1):
                self.update_preview()

        def _schedule_preview_refresh(self):
            self.update_preview()
            for delay in (120, 450, 1000, 1800):
                QTimer.singleShot(delay, self.update_preview_if_changed)

        def refresh_history_list(self):
            if not hasattr(self, "history_list"):
                return
            selected = self.history_list.currentItem().data(Qt.UserRole) if self.history_list.currentItem() else None
            self.history_list.blockSignals(True)
            self.history_list.clear()
            seen = set()
            self._last_history_len = len(core.config.get("history", []))
            for path in core.config.get("history", [])[:8]:
                if not path or path in seen or not os.path.exists(path):
                    continue
                seen.add(path)
                item = QListWidgetItem()
                item.setToolTip(path)
                item.setData(Qt.UserRole, path)
                item.setSizeHint(QSize(118, 78))
                pix = self._load_icon_pixmap(path, QSize(108, 68))
                if not pix.isNull():
                    item.setIcon(QIcon(pix))
                self.history_list.addItem(item)
                if path == selected:
                    self.history_list.setCurrentItem(item)
            self.history_list.blockSignals(False)

        def _open_file_location(self, path: str):
            if not path or not os.path.exists(path):
                return
            try:
                if sys.platform.startswith("win"):
                    import subprocess
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                elif sys.platform == "darwin":
                    os.system(f'open -R "{path}"')
                else:
                    folder = os.path.dirname(path)
                    os.system(f'xdg-open "{folder}"')
            except Exception as e:
                QMessageBox.warning(self, "跳转失败", str(e))

        def _load_icon_pixmap(self, path: str, size: QSize) -> QPixmap:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            original = reader.size()
            if original.isValid():
                scaled = original.scaled(size, Qt.KeepAspectRatio)
                if scaled.isValid():
                    reader.setScaledSize(scaled)
            image = reader.read()
            return QPixmap.fromImage(image) if not image.isNull() else QPixmap()

        def open_selected_history_location(self):
            item = self.history_list.currentItem() if hasattr(self, "history_list") else None
            self.open_history_item_location(item)

        def open_history_item_location(self, item: QListWidgetItem):
            if hasattr(self, "_history_single_click_timer"):
                self._history_single_click_timer.stop()
            path = item.data(Qt.UserRole) if item else ""
            self._open_file_location(path)

        def schedule_apply_history_item(self, item: QListWidgetItem):
            self._pending_history_item = item
            self._history_single_click_timer.start(230)

        def apply_pending_history_item(self):
            item = getattr(self, "_pending_history_item", None)
            self._pending_history_item = None
            self.apply_history_item(item)

        def apply_history_item(self, item: QListWidgetItem):
            path = item.data(Qt.UserRole) if item else ""
            if path and os.path.exists(path):
                def _apply_from_history():
                    core.push_wallpaper(path)
                    return core.set_wallpaper_direct(path, "历史记录")
                self.run_core(_apply_from_history)

        def open_current_folder(self):
            path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
            folder = os.path.dirname(path) if path else core.config.get("slide_folder", "")
            if folder and os.path.isdir(folder):
                if sys.platform.startswith("win"):
                    os.startfile(folder)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    os.system(f'open "{folder}"')
                else:
                    os.system(f'xdg-open "{folder}"')


        def open_wallpaper_sidebar(self) -> None:
            """在主窗口进程内打开（或置顶复用）壁纸侧边栏。"""
            # 复用已存在的侧边栏
            sb = getattr(self, "_sidebar", None)
            if sb is not None:
                try:
                    if not sb._is_closing:
                        sb.raise_()
                        sb.activateWindow()
                        return
                except Exception:
                    pass
                self._sidebar = None

            from wallpaper_sidebar import WallpaperSidebar

            folder = core.config.get("slide_folder", "")
            current = core.config.get("current_wallpaper", "") or core.get_current_wallpaper()

            if not folder or not os.path.isdir(folder):
                QMessageBox.information(self, "提示喵", "请先在软件中设置壁纸文件夹")
                return

            def _switch(path: str) -> None:
                try:
                    core.push_wallpaper(path)
                    core.set_wallpaper_direct(path, "侧边栏切换")
                    QTimer.singleShot(50, self.update_preview)
                except Exception as exc:
                    core.log(f"侧边栏切换壁纸失败: {exc}")

            sidebar_log = self._log_file_path() if core.config.get("log_enabled", False) else None
            self._sidebar = WallpaperSidebar(
                None, folder, current, sidebar_log,
                show_message=lambda t, m: QMessageBox.information(self, t, m),
                switch_wallpaper=_switch,
            )
            self._sidebar.closed.connect(lambda: setattr(self, "_sidebar", None))
        def _bing_downloader(self):
            from bing_downloader import BingDownloader
            cache_dir = self.bing_cache_edit.text().strip()
            if not cache_dir:
                raise ValueError("请先填写或选择 Bing 壁纸缓存目录")
            core.config["bing_cache_dir"] = cache_dir
            core.config["bing_sync_count"] = int(self.bing_count_spin.value())
            core.save_config()
            return BingDownloader(cache_dir=cache_dir)

        def refresh_bing_cache_list(self):
            if not hasattr(self, "bing_list"):
                return
            self.bing_list.clear()
            cache_dir = core.config.get("bing_cache_dir", "") or ""
            if not cache_dir:
                if hasattr(self, "bing_status"):
                    self.bing_status.setText("首次使用请先选择 Bing 壁纸缓存目录")
                return
            try:
                from bing_downloader import BingDownloader
                for path in BingDownloader(cache_dir=cache_dir).get_cached_wallpapers():
                    item = QListWidgetItem(os.path.basename(path))
                    item.setData(Qt.UserRole, path)
                    self.bing_list.addItem(item)
            except Exception as e:
                core.log(f"刷新 Bing 缓存列表失败: {e}")

        def choose_bing_cache_dir(self):
            folder = QFileDialog.getExistingDirectory(self, "选择 Bing 壁纸缓存目录", self.bing_cache_edit.text() or str(Path.home()))
            if not folder:
                return
            self.bing_cache_edit.setText(folder)
            core.config["bing_cache_dir"] = folder
            core.save_config()
            self.refresh_bing_cache_list()

        def on_bing_selection_changed(self):
            item = self.bing_list.currentItem()
            if not item:
                return
            path = item.data(Qt.UserRole)
            if path and os.path.exists(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    old_current = core.config.get("current_wallpaper")
                    core.config["current_wallpaper"] = path
                    self.update_preview()
                    if old_current is not None:
                        core.config["current_wallpaper"] = old_current
                    self.current_label.setText(path)

        def use_bing_cache_as_slideshow(self):
            folder = self.bing_cache_edit.text().strip()
            if not folder or not os.path.isdir(folder):
                QMessageBox.warning(self, "Bing 壁纸", "请先选择或同步一个有效的缓存目录。")
                return
            core.config["slide_folder"] = folder
            core.config["mode"] = "幻灯片放映"
            self.folder_edit.setText(folder)
            self.mode_combo.setCurrentText("幻灯片放映")
            core.save_config()
            self.run_core(core.restart_slideshow)
            self.set_status("Bing 缓存已设为幻灯片来源")

        def save_selected_bing_as(self):
            item = self.bing_list.currentItem()
            if not item:
                QMessageBox.information(self, "Bing 壁纸", "请先在列表中选择一张已缓存的 Bing 壁纸。")
                return
            src = item.data(Qt.UserRole)
            if not src or not os.path.exists(src):
                QMessageBox.warning(self, "Bing 壁纸", "选中的缓存文件不存在。")
                return
            dst, _ = QFileDialog.getSaveFileName(self, "另存 Bing 壁纸", os.path.join(str(Path.home()), os.path.basename(src)), "JPEG 图片 (*.jpg);;所有文件 (*.*)")
            if not dst:
                return
            try:
                import shutil
                shutil.copy2(src, dst)
                self.set_status(f"已另存为：{dst}")
            except Exception as e:
                QMessageBox.warning(self, "另存失败", str(e))

        def sync_bing_wallpaper(self, set_latest: bool = True):
            cache_dir = self.bing_cache_edit.text().strip()
            if not cache_dir:
                QMessageBox.information(self, "Bing 壁纸", "首次使用 Bing 壁纸前，请先选择或填写缓存目录。")
                self.bing_cache_edit.setFocus()
                return
            resolution = self.bing_resolution.currentText().strip() or "auto"
            count = max(1, min(8, int(self.bing_count_spin.value())))
            self.bing_sync_btn.setEnabled(False)
            self.bing_multi_btn.setEnabled(False)
            self.bing_progress.setValue(0)
            self.bing_status.setText("正在同步 Bing 壁纸...")

            def _work():
                try:
                    downloader = self._bing_downloader()
                    paths = []
                    infos = downloader.fetch_history(days=count, resolution=resolution)
                    total = max(1, len(infos))
                    for idx, info in enumerate(infos, 1):
                        path = downloader.download_wallpaper(info)
                        if path:
                            paths.append(path)
                        self.bing_result_signal.emit(True, f"Bing 同步进度：{idx}/{total}", path or "")
                    if not paths:
                        self._emit_bing_result(False, "没有同步到 Bing 壁纸", "")
                        return
                    latest = paths[0]
                    if set_latest:
                        core.push_wallpaper(latest)
                        core.set_wallpaper_direct(latest, "Bing 壁纸")
                        self._emit_bing_result(True, f"已同步 {len(paths)} 张并设置最新 Bing 壁纸", latest)
                    else:
                        self._emit_bing_result(True, f"已同步 {len(paths)} 张 Bing 壁纸到缓存目录", latest)
                except Exception as e:
                    self._emit_bing_result(False, f"同步 Bing 壁纸失败：{e}", "")

            self._bing_worker_thread = threading.Thread(target=_work, daemon=True)
            self._bing_worker_thread.start()

        def _emit_bing_result(self, ok: bool, message: str, path: str):
            self.bing_result_signal.emit(ok, message, path)

        def _on_bing_finished(self, ok: bool, message: str, path: str):
            self.bing_sync_btn.setEnabled(True)
            self.bing_multi_btn.setEnabled(True)
            if "进度" in message:
                try:
                    done, total = message.split("：", 1)[1].split("/", 1)
                    self.bing_progress.setValue(int(int(done) / max(1, int(total)) * 100))
                except Exception:
                    pass
            else:
                self.bing_progress.setValue(100 if ok else 0)
            self.bing_status.setText(message + (f"\n{path}" if path else ""))
            self.set_status(message)
            self.refresh_bing_cache_list()
            self.update_preview()
            if not ok:
                QMessageBox.warning(self, "Bing 壁纸", message)

        def show_about_dialog(self):
            """关于对话框：参照旧版逻辑，包含 txtlogo 进入动画、版本信息、统计和链接。"""
            dlg = QDialog(self)
            dlg.setWindowTitle("关于 上一个桌面背景")
            dlg.setFixedSize(480, 520)
            dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(24, 20, 24, 20)
            layout.setSpacing(12)

            # txtlogo.png 进入动画：保留淡入，不再使用绝对坐标 move()，避免布局完成后标题错位。
            self._about_dlg_logo = QLabel(dlg)
            self._about_dlg_logo.setAlignment(Qt.AlignCenter)
            self._about_dlg_logo.setFixedHeight(86)
            txtlogo_path = self._img_path("txtlogo.png")
            if os.path.exists(txtlogo_path):
                pix = QPixmap(txtlogo_path)
                if not pix.isNull():
                    self._about_dlg_logo.setPixmap(pix.scaled(400, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    try:
                        effect = QGraphicsOpacityEffect(self._about_dlg_logo)
                        self._about_dlg_logo.setGraphicsEffect(effect)
                        self._logo_anim = QPropertyAnimation(effect, b"opacity", dlg)
                        self._logo_anim.setDuration(260)
                        self._logo_anim.setStartValue(0.15)
                        self._logo_anim.setEndValue(1.0)
                        self._logo_anim.setEasingCurve(QEasingCurve.OutCubic)
                        self._logo_anim.start()
                    except Exception:
                        pass
            layout.addWidget(self._about_dlg_logo)

            # about-window.png
            about_path = self._img_path("about-window.png")
            if os.path.exists(about_path):
                pix = QPixmap(about_path)
                if not pix.isNull():
                    img_label = QLabel(dlg)
                    img_label.setAlignment(Qt.AlignCenter)
                    img_label.setPixmap(pix.scaled(420, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    layout.addWidget(img_label)

            # 版本信息
            ver_label = QLabel(f"上一个桌面背景 v{core.VERSION}")
            ver_label.setAlignment(Qt.AlignCenter)
            ver_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
            layout.addWidget(ver_label)

            # 统计信息（参照旧版从 jsonbin 获取）
            stats_label = QLabel("正在获取统计数据...")
            stats_label.setAlignment(Qt.AlignCenter)
            stats_label.setProperty("muted", True)
            layout.addWidget(stats_label)
            self._fetch_about_stats(stats_label)

            # 链接
            link_label = QLabel(
                '原项目：<a href="https://github.com/xxdz-Official/ShangBackground">GitHub</a><br>'
                '反馈地址：<a href="https://github.com/purrfecto114-lgtm/ShangBackground">GitHub Fork</a><br>'
                '作者主页：b站@小小电子xxdz'
            )
            link_label.setOpenExternalLinks(True)
            link_label.setAlignment(Qt.AlignCenter)
            link_label.setWordWrap(True)
            layout.addWidget(link_label)

            # 关闭按钮
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(dlg.accept)
            layout.addWidget(close_btn)

            dlg.exec()

        def _fetch_about_stats(self, label: QLabel):
            """后台获取统计数据（参照旧版 jsonbin 逻辑）。"""
            try:
                import threading
                def _fetch():
                    try:
                        import httpx
                        r = httpx.get("https://api.jsonbin.io/v3/b/681a0b3b8a456b7eb09c5c3b/latest",
                                      headers={"X-Master-Key": "$2a$10$K7L1OJ45/4Y2nIvhRVpCe.FSmhDdWoXehVzJptJ/op0lDsvEb6zRe"},
                                      timeout=5)
                        if r.status_code == 200:
                            data = r.json().get("record", {})
                            users = data.get("global_users", "?")
                            uses = data.get("global_uses", "?")
                            today = data.get("today_uses", "?")
                            label.setText(f"全球用户：{users}  |  总使用次数：{uses}  |  今日使用：{today}")
                        else:
                            label.setText("统计数据暂不可用")
                    except Exception:
                        label.setText("统计数据暂不可用")
                t = threading.Thread(target=_fetch, daemon=True)
                t.start()
            except Exception:
                label.setText("统计数据暂不可用")

        def restart_as_admin(self):
            self._closing_for_exit = True
            if self.tray:
                self.tray.hide()
                self.tray.deleteLater()
                self.tray = None
                QApplication.processEvents()
            if core.restart_as_admin():
                core._do_exit(0)
            else:
                self._closing_for_exit = False
                QMessageBox.warning(self, "提权失败", "无法以管理员身份重启，请手动右键以管理员身份运行。")

        def start_legacy_process(self):
            """启动旧版 Tkinter 前关闭新版，避免两个实例同时改壁纸/抢托盘。"""
            try:
                import subprocess
                env = os.environ.copy()
                env["SHANGBACKGROUND_LEGACY_SECONDARY"] = "1"
                self._closing_for_exit = True
                core.stop_slideshow()
                if self.tray:
                    self.tray.hide()
                    self.tray.deleteLater()
                    self.tray = None
                    QApplication.processEvents()
                core.release_single_instance_mutex()
                subprocess.Popen(self._app_command("--legacy-tk"), env=env, cwd=core.BASE_DIR)
                QTimer.singleShot(120, QApplication.instance().quit)
            except Exception as e:
                self._closing_for_exit = False
                QMessageBox.warning(self, "启动失败", str(e))

        def exit_app(self):
            self._closing_for_exit = True
            if self.tray:
                self.tray.hide()
                self.tray.deleteLater()
                self.tray = None
                QApplication.processEvents()
            core.stop_slideshow()
            core.restore_session_original_wallpaper()
            core.release_single_instance_mutex()
            QApplication.instance().quit()

        def closeEvent(self, event):
            if core.config.get("run_in_background", True) and not self._closing_for_exit:
                event.ignore()
                self.hide()
                if self.tray and core.config.get("tray_notify", True):
                    self.tray.showMessage("上一个桌面背景", "已隐藏到系统托盘", QSystemTrayIcon.Information, 1500)
                return
            if self.tray:
                self.tray.hide()
                self.tray.deleteLater()
                self.tray = None
            core.restore_session_original_wallpaper()
            core.release_single_instance_mutex()
            event.accept()


def main() -> int:
    args = _parse_early_args()
    if args.legacy_tk:
        return _run_legacy_tk()

    if not PYSIDE_AVAILABLE:
        core.log(f"PySide6 不可用，回退到 Tkinter 界面: {PYSIDE_IMPORT_ERROR}")
        return _run_legacy_tk()

    is_action_launch = _is_action_launch(args)
    direct_action_launch = (args.previous or args.next or args.random or bool(args.set_wallpaper) or args.jump_to_wallpaper)
    if direct_action_launch and _handle_action_args(args):
        return 0

    if core.IS_WINDOWS:
        if not core.acquire_single_instance_mutex():
            core.log("检测到已有实例（互斥体），打开现有主界面并退出本次启动")
            core.activate_existing_instance(show_notice=not is_action_launch)
            return 0
        existing = core.find_existing_main_window(timeout=0.2)
        if existing:
            core.release_single_instance_mutex()
            core.activate_existing_instance(show_notice=not is_action_launch)
            return 0

    if _handle_action_args(args):
        core.release_single_instance_mutex()
        return 0

    if core.IS_WINDOWS:
        try:
            ctypes = __import__("ctypes")
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("xxdz.ShangBackground")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("ShangBackground")
    app.setApplicationDisplayName("上一个桌面背景")
    app.setDesktopFileName("ShangBackground")
    icon_path = os.path.join(core.BASE_DIR, "img", "LOGO.ico")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)

    try:
        from dependency_prompt import prompt_install_dependencies
        if not prompt_install_dependencies(None, _dependency_availability_for_pyside(), parent=None, prefer_pyside=True):
            core.release_single_instance_mutex()
            return 0
    except Exception as exc:
        core.log(f"PySide6 依赖检查跳过: {exc}")

    core.capture_session_original_wallpaper()
    window = ShangBackgroundWindow()
    core.root = QtRootShim(window)
    core.canvas = None
    if core.IS_WINDOWS:
        core.start_message_window()
    core.report_usage()

    if core.config.get("mode") == "幻灯片放映" and core.config.get("slide_folder"):
        core.start_slideshow()

    if core.hide_window or args.hide:
        window.hide()
    else:
        window.show()
    code = app.exec()
    if window.tray:
        window.tray.hide()
    core.stop_slideshow()
    if getattr(window, "_closing_for_exit", False):
        core.restore_session_original_wallpaper()
    core.release_single_instance_mutex()
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
