# Main PySide6 window with explicit dependencies.
from __future__ import annotations
import os
import plistlib
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
from core import engine as core
from app.config import (
    DEFAULT_THEME_COLOR,
    MODE_KEYS,
    PLATFORM_ID,
    PLATFORM_LABEL,
    STYLE_KEYS,
    UPDATE_CHECK_ON_STARTUP,
    UPDATE_CHECK_TIMEOUT_SECONDS,
    get_video_filetypes,
    normalize_mode_key,
    normalize_style_key,
)
from app.i18n import LanguageChangeEvent, get_language, load_language, subscribe_language_changes, t
from app.build_features import is_feature_enabled
from app.system_info import collect_system_info, render_system_info
from app.config_normalization import normalize_runtime_config_in_place
from app.wallpaper_action_policy import wallpaper_action_availability
from app.paths import entry_script_path, image_path, app_executable_path
from app.support import (
    APP_DISPLAY_NAME,
    APP_ORGANIZATION,
    APP_PROCESS_NAME,
    APP_VERSION,
    apply_application_font,
    _open_path_in_linux_file_manager,
)
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImageReader, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QScroller,
    QScrollerProperties,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyleFactory,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from ui.preview_canvas import PreviewCanvas
from ui.settings_dialog import GlobalSettingsDialog
from ui.settings_navigation import SettingsNavigator
from ui.video_focus_mixin import VideoFocusMixin
from ui.source_inputs import SourceInputController
from ui.widgets import CompactSpinBox, ShangComboBox
from ui.platform_ui_policy import get_platform_ui_policy
from app.scaling import apply_dpi_environment, clamp_dpi_scale, dpi_percent
if is_feature_enabled("updates"):
    from services.updates import GITHUB_LATEST_RELEASE_URL, GITHUB_PROJECT_URL, UpdateChecker
else:
    GITHUB_LATEST_RELEASE_URL = "https://github.com/purrfecto114-lgtm/ShangBackground/releases/latest"
    GITHUB_PROJECT_URL = "https://github.com/purrfecto114-lgtm/ShangBackground"
    UpdateChecker = None
from ui.control_setup import configure_text_input, describe_control, make_buddy_label
from ui.dialog_style import show_info, show_warning
QWIDGETSIZE_MAX = 16777215


class _TouchScrollFilter(QObject):
    """Event filter that prevents touch-scroll misfires on QListWidget items.

    When QScroller grabs a touch gesture, Qt may still synthesize a mouse
    click event on finger release. If the finger moved during the scroll,
    this filter suppresses the ``itemClicked`` signal so the wallpaper is
    not accidentally switched.

    The filter tracks press position on MousePress and checks movement
    distance on MouseRelease. If the movement exceeds 10 pixels OR the
    QScroller is in Dragging/Scrolling state, the release is treated as
    a scroll gesture and the click is consumed.
    """

    _SCROLL_THRESHOLD_PX = 10

    def __init__(self, parent, widget):
        super().__init__(parent)
        self._widget = widget

    def eventFilter(self, obj, event):
        try:
            etype = event.type()
            if etype == QEvent.Type.MouseButtonPress:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                self._widget._touch_press_pos = pos
                if hasattr(self._widget, "indexAt"):
                    self._widget._touch_press_item = self._widget.indexAt(pos)
            elif etype == QEvent.Type.MouseButtonRelease:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                press_pos = getattr(self._widget, "_touch_press_pos", None)
                if press_pos is not None:
                    moved = (pos - press_pos).manhattanLength()
                    # Check QScroller state — if dragging/scrolling, suppress click
                    viewport = self._widget.viewport() if hasattr(self._widget, "viewport") else self._widget
                    try:
                        scroller = QScroller.scroller(viewport)
                        dragging = scroller.state() in (QScroller.State.Dragging, QScroller.State.Scrolling)
                    except Exception:
                        dragging = False
                    if moved > self._SCROLL_THRESHOLD_PX or dragging:
                        # Consume the release so itemClicked doesn't fire
                        self._widget._touch_press_pos = None
                        self._widget._touch_press_item = None
                        return True
                self._widget._touch_press_pos = None
        except Exception:
            pass
        return False


class _SharedShangBackgroundWindow(QMainWindow):
    bing_result_signal = Signal(bool, str, str)
    core_result_signal = Signal(bool, str, object)
    hotkey_recorded_signal = Signal(str, str)

    def __init__(self):
        super().__init__()
        normalize_runtime_config_in_place(core.config)
        self.setWindowTitle(APP_DISPLAY_NAME)
        # 保留系统标题栏的最大化/关闭按钮。页面内部已有滚动区域，最大化时不再硬性限制窗口尺寸。
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowCloseButtonHint)
        self.setMinimumSize(1120, 720)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        self._settings_dialog = None
        self.lang_combo = None
        self.header_lang_buttons = {}
        self._closing_for_exit = False
        self._exit_signals_disconnected = False
        self.tray: QSystemTrayIcon | None = None
        self._bing_worker_thread: threading.Thread | None = None
        self._startup_bing_automation_done = False
        self._startup_update_checker = None
        self._startup_update_result: dict | None = None
        self._core_worker_thread: threading.Thread | None = None
        self._core_busy = False
        self._pending_core_actions = deque(maxlen=4)
        self._icon_pixmap_cache = OrderedDict()
        self._last_tab_index = -1
        self._first_show_anim = True
        self._last_preview_path = ""
        self._history_single_click_timer = QTimer(self)
        self._history_single_click_timer.setSingleShot(True)
        self._pending_history_item = None
        self._bing_preview_timer = QTimer(self)
        self._bing_preview_timer.setSingleShot(True)
        self._bing_preview_timer.timeout.connect(self.apply_pending_bing_preview)
        self._pending_bing_path = ""
        self._current_operation_name = ""
        self._current_operation_cancel = threading.Event()
        self._operation_panel_expanded = False
        self._non_modal_dialogs = []
        self._refreshing_from_config = False
        self._status_full_text = ""
        self._status_reset_timer = QTimer(self)
        self._status_reset_timer.setSingleShot(True)
        self._status_reset_timer.timeout.connect(self._clear_status_if_idle)
        self._source_inputs = SourceInputController(
            parent=self,
            config=core.config,
            persist=core.save_config,
            set_status=self.set_status,
            show_warning=show_warning,
            translate=t,
        )
        self._init_icon()
        self._apply_theme()
        self._build_ui()
        self._i18n_unsubscribe = subscribe_language_changes(self._on_i18n_language_changed)
        # 配置已在 core.engine 导入时加载；这里在首帧显示前同步填充控件，
        # 避免先显示默认值/空白值再跳变，引发用户感知上的“首帧卡顿”。
        self.refresh_from_config()
        # v1.4.7: 移除应用内热键 (与打字冲突)
        # self._setup_app_shortcuts()
        self._apply_button_sizes()
        self.bing_result_signal.connect(self._on_bing_finished, Qt.ConnectionType.QueuedConnection)
        self.core_result_signal.connect(self._on_core_finished, Qt.ConnectionType.QueuedConnection)
        self.hotkey_recorded_signal.connect(self.set_context_hotkey, Qt.ConnectionType.QueuedConnection)
        self._preview_refresh_timer = QTimer(self)
        self._preview_refresh_timer.setInterval(self._preview_poll_interval())
        self._preview_refresh_timer.timeout.connect(self.update_preview_if_changed)
        self._preview_refresh_timer.start()
        self._video_focus_ducked = False
        self._video_focus_paused = False
        self._video_focus_pause_pending = False
        _saved_video_volume = max(0, min(100, int(core.config.get("video_volume", 100))))
        self._video_runtime_volume = 0 if bool(core.config.get("video_muted", True)) else _saved_video_volume
        self._video_volume_ramp_steps = deque()
        self._video_volume_ramp_callback = None
        self._video_volume_ramp_target = None
        self._video_volume_ramp_timer = QTimer(self)
        self._video_volume_ramp_timer.setInterval(40)
        self._video_volume_ramp_timer.timeout.connect(self._video_volume_ramp_tick)
        self._video_focus_timer = QTimer(self)
        self._video_focus_timer.setInterval(700)
        self._video_focus_timer.timeout.connect(self._apply_video_focus_policy_tick)
        if is_feature_enabled("video"):
            self._video_focus_timer.start()
        QTimer.singleShot(0, self._deferred_gui_startup)

    def _init_icon(self):
        icon_name = "LOGO.png"
        self.icon_path = os.path.join(core.BASE_DIR, "img", icon_name)
        if not os.path.exists(self.icon_path):
            self.icon_path = os.path.join(core.BASE_DIR, "img", "LOGO.ico")
        self.app_icon = QIcon(self.icon_path) if os.path.exists(self.icon_path) else QIcon()
        app = QApplication.instance()
        if app is not None:
            app.setOrganizationName(APP_ORGANIZATION)
            app.setApplicationName(APP_PROCESS_NAME)
            app.setApplicationDisplayName(APP_DISPLAY_NAME)
        if not self.app_icon.isNull():
            QApplication.setWindowIcon(self.app_icon)
            self.setWindowIcon(self.app_icon)

    def _img_path(self, name: str) -> str:
        return image_path(name)

    def _set_button_svg_icon(self, button, icon_name: str, size: int = 20):
        """给按钮设置统一 SVG 图标，记录以便暗色模式切换时刷新 SVG 颜色。

        Bug 3 fix: 直接用 ``QIcon(path)`` 会让 Qt 内部的 ``QSvgRenderer``
        缓存按文件路径 keyed — 第二次调用（暗色模式切换后）会拿到失效的
        renderer，触发 ``qt.svg: Cannot open file …`` 警告。这里改用显式
        ``QSvgRenderer`` + ``QPixmap`` 渲染路径，并按
        ``(path, theme_signature, size)`` 缓存像素图，绕过 Qt 的 SVG
        renderer 缓存。
        """
        try:
            path = self._img_path(icon_name)
            if os.path.exists(path):
                pix = self._render_svg_to_pixmap(path, size)
                if pix is not None and not pix.isNull():
                    button.setIcon(QIcon(pix))
                    button.setIconSize(QSize(size, size))
                    if not hasattr(self, "_svg_button_icons"):
                        self._svg_button_icons = {}
                    self._svg_button_icons[id(button)] = (button, path, size)
        except Exception:
            pass

    def _render_svg_to_pixmap(self, path: str, size: int):
        """Render an SVG file to a ``QPixmap`` of ``size x size`` device pixels.

        Bypasses Qt's ``QSvgRenderer`` path-keyed cache by reading the file
        bytes directly and rendering into a fresh ``QPixmap``.  Results are
        memoized per ``(path, theme_signature, size)``.

        Bug 18 fix: ``QSvgRenderer`` does NOT resolve the CSS keyword
        ``currentColor`` — it renders such strokes/fills as black, making
        icons invisible on dark backgrounds.  We now replace ``currentColor``
        in the SVG data with the actual theme foreground color before
        rendering, so icons are visible in both light and dark modes.
        """
        try:
            from PySide6.QtSvg import QSvgRenderer
            from PySide6.QtGui import QPixmap, QPainter, QGuiApplication
            from PySide6.QtCore import QByteArray, QRectF
        except Exception:
            return None
        sig = self._svg_theme_signature()
        # Bug 18 fix: get the current theme's foreground color for currentColor substitution.
        fg_color = self._svg_current_color()
        cache = getattr(self, "_svg_pixmap_cache", None)
        if cache is None:
            cache = {}
            self._svg_pixmap_cache = cache
        key = (path, sig, int(size), fg_color)
        cached = cache.get(key)
        if cached is not None:
            try:
                if not cached.isNull():
                    return cached
            except RuntimeError:
                cache.pop(key, None)
        try:
            with open(path, "rb") as f:
                data = f.read()
            # Bug 18 fix: Replace currentColor with the actual theme color so
            # QSvgRenderer (which doesn't support currentColor) renders correctly.
            if b"currentColor" in data and fg_color:
                data = data.replace(b"currentColor", fg_color.encode("utf-8"))
            renderer = QSvgRenderer(QByteArray(data))
            if not renderer.isValid():
                return None
            screen = QGuiApplication.primaryScreen()
            dpr = screen.devicePixelRatio() if screen else 1.0
            pix = QPixmap(int(size * dpr), int(size * dpr))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pad = max(1.0, float(size) * 0.08)
            renderer.render(painter, QRectF(pad, pad, max(1.0, float(size) - 2 * pad), max(1.0, float(size) - 2 * pad)))
            painter.end()
            cache[key] = pix
            if len(cache) > 64:
                oldest = next(iter(cache))
                cache.pop(oldest, None)
            return pix
        except Exception:
            return None

    def _svg_current_color(self) -> str:
        """Return the foreground color to substitute for ``currentColor`` in SVGs.

        Bug 18/22: QSvgRenderer doesn't understand ``currentColor``, so we
        manually replace it with the button text color. This matches the
        actual color seen by button labels (which depends on the theme
        color brightness), so icons stay visible in both light and dark
        modes and on both light and dark theme accents.
        """
        try:
            from PySide6.QtGui import QColor
            dark = self._theme_is_dark()
            accent = getattr(self, "_theme_color", core.config.get("theme_color", DEFAULT_THEME_COLOR)) or DEFAULT_THEME_COLOR
            qcolor = QColor(accent)
            if not qcolor.isValid():
                accent = DEFAULT_THEME_COLOR
                qcolor = QColor(accent)
            brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000
            if dark:
                if brightness >= 230:
                    return "#e6e6f0"
                return "#e0e0e0" if brightness >= 170 else "#ffffff"
            else:
                return "#1f2328" if brightness >= 170 else "#ffffff"
        except Exception:
            return "#24292f"

    def _svg_theme_signature(self) -> str:
        """Return a short signature that changes when SVG rendering should be
        invalidated (dark-mode toggle, theme color change, language switch).
        """
        try:
            # Bug 3 fix: use _theme_is_dark() method (the actual API) instead
            # of the non-existent _dark_mode attribute, so the signature
            # actually changes on dark-mode toggle and invalidates the cache.
            dark = bool(self._theme_is_dark()) if hasattr(self, "_theme_is_dark") else False
            accent = str(core.config.get("theme_color", "")) if hasattr(core, "config") else ""
            return f"{'d' if dark else 'l'}_{accent}"
        except Exception:
            return "default"

    def _refresh_svg_button_icons(self):
        """暗色模式切换后刷新所有 SVG 按钮图标，确保 currentColor 正确生效。

        Bug 3 fix: 清空本地像素图缓存并重新渲染，避免命中 Qt 的失效
        ``QSvgRenderer`` 缓存。
        """
        cache = getattr(self, "_svg_pixmap_cache", None)
        if cache is not None:
            cache.clear()
        icons = getattr(self, "_svg_button_icons", {})
        for btn_id, (button, path, size) in list(icons.items()):
            try:
                pix = self._render_svg_to_pixmap(path, size)
                if pix is not None and not pix.isNull():
                    button.setIcon(QIcon(pix))
                    button.setIconSize(QSize(size, size))
                else:
                    button.setIcon(QIcon(path))
                    button.setIconSize(QSize(size, size))
            except RuntimeError:
                icons.pop(btn_id, None)
            except Exception:
                pass

    def _is_qobject_alive(self, obj) -> bool:
        """Return False when a PySide wrapper points at an already-deleted C++ object."""
        if obj is None:
            return False
        try:
            import shiboken6
            if not shiboken6.isValid(obj):
                return False
        except RuntimeError:
            return False
        except Exception:
            pass
        try:
            obj.objectName()
            return True
        except RuntimeError:
            return False
        except Exception:
            return True

    def _animate_widget_flash(self, widget):
        """Refresh compact header controls without QGraphicsEffect side effects."""
        if not self._is_qobject_alive(widget):
            return
        try:
            widget.update()
        except Exception:
            pass

    def _animate_tab_page_switch(self, idx: int) -> None:
        """Page switches are intentionally repaint-only.

        Decorative page transitions were removed to keep tab changes deterministic
        on all three desktop builds and to avoid accumulating graphics-side state
        after repeated navigation.
        """
        self._last_tab_index = idx
        try:
            if hasattr(self, "tabs") and self.tabs is not None:
                page = self.tabs.widget(idx) if idx >= 0 else self.tabs.currentWidget()
                if page is not None:
                    page.setGraphicsEffect(None)
                    page.update()
        except Exception:
            pass

    def _sync_tab_page_animation_state(self) -> None:
        """Compatibility hook kept for settings toggles; no animations remain."""
        try:
            if hasattr(self, "tabs") and self.tabs is not None:
                page = self.tabs.currentWidget()
                if page is not None:
                    page.setGraphicsEffect(None)
                    page.update()
        except Exception:
            pass

    def _header_lang_button_style(self, selected: bool) -> str:
        qcolor = QColor(self._theme_color)
        brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000 if qcolor.isValid() else 80
        colors = self._theme_role_colors()
        if selected:
            text = "#24292f" if brightness >= 170 else "#ffffff"
            border = self._theme_color if brightness < 230 else ("#8c959f" if not self._theme_is_dark() else "#6d6d85")
            bg = self._theme_color if brightness < 235 else ("#eaeef2" if not self._theme_is_dark() else "#3a3a50")
            return (
                f"background: {bg}; color: {text}; border: 1px solid {border};"
                " border-radius: 6px; padding: 0; font-size: 12px; font-weight: 700;"
                " min-width: 31px; max-width: 31px; min-height: 24px; max-height: 24px;"
            )
        return (
            f"background: {colors['bg_input']}; color: {colors['fg_secondary']}; border: 1px solid {colors['border']};"
            " border-radius: 6px; padding: 0; font-size: 12px; font-weight: 600;"
            " min-width: 31px; max-width: 31px; min-height: 24px; max-height: 24px;"
        )

    def _refresh_header_language_buttons(self, active_lang: str | None = None):
        """Refresh compact header language switch after language/theme changes.

        These buttons use explicit per-state styles instead of inheriting only from
        the application QSS, so they must be repolished whenever the accent color
        or dark-mode palette changes.
        """
        active_lang = active_lang or core.config.get("language", get_language()) or "zh"
        for lang, btn in list(getattr(self, "header_lang_buttons", {}).items()):
            if not self._is_qobject_alive(btn):
                self.header_lang_buttons.pop(lang, None)
                continue
            selected = (lang == active_lang)
            try:
                btn.blockSignals(True)
                btn.setChecked(selected)
                btn.setStyleSheet(self._header_lang_button_style(selected))
                btn.style().unpolish(btn)
                btn.style().polish(btn)
                btn.update()
                btn.blockSignals(False)
            except RuntimeError:
                self.header_lang_buttons.pop(lang, None)
            except Exception:
                try:
                    btn.blockSignals(False)
                except Exception:
                    pass

        wrapper = getattr(self, "header_lang_switch", None)
        if self._is_qobject_alive(wrapper):
            try:
                wrapper.style().unpolish(wrapper)
                wrapper.style().polish(wrapper)
                wrapper.update()
            except Exception:
                pass

    def _set_combo_current_data(self, combo, value, default_index: int = 0):
        """Set a QComboBox by item data, not translated display text."""
        if not self._is_qobject_alive(combo):
            return
        try:
            idx = combo.findData(value)
            if idx < 0:
                idx = default_index
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        except RuntimeError:
            return
        except Exception:
            return

    def _prepare_combo_popup(self, combo: QComboBox) -> QComboBox:
        """Keep combo popups compact. ShangComboBox renders the popup as QMenu.

        Older builds installed a QListView as the native combo popup. On Windows
        this could produce oversized white panels and unnecessary top/bottom
        scroller arrows. The helper is intentionally light now so existing call
        sites remain compatible without reintroducing the native popup.
        """
        try:
            combo.setMaxVisibleItems(max(1, min(combo.count(), 12)))
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        except Exception:
            pass
        return combo

    def _prepare_popup_menu(self, menu: QMenu) -> QMenu:
        """Prepare a QMenu popup for consistent rendering.

        Note: We intentionally do NOT set WA_TranslucentBackground on Linux.
        KDE/GNOME/XFCE Qt themes already draw rounded corners and shadows for
        popup menus.  Forcing translucency + frameless flags breaks the native
        theme, causing asymmetric corners and missing shadows.  The QSS
        ``border-radius`` only controls the inner background, not the window
        shape.
        """
        if menu is None:
            return menu
        return menu

    def _constrain_combo_width(self, combo: QComboBox, min_width: int = 150, max_width: int = 260) -> QComboBox:
        """Prevent translated combo text from expanding the whole form."""
        try:
            combo.setMinimumWidth(int(min_width))
            combo.setMaximumWidth(int(max_width))
            combo.setMinimumContentsLength(10)
            combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        except Exception:
            pass
        return combo

    def _create_header_language_switch(self) -> QWidget:
        """Create the compact CN/EN switch placed immediately after txtlogo."""
        wrapper = QFrame()
        wrapper.setObjectName("HeaderLangSwitch")
        # The two captions are already self-explanatory.  Do not attach a
        # tooltip to the wrapper: on some Qt/Windows theme combinations an
        # inherited empty help bubble is rendered as a blank “说明” popup.
        wrapper.setToolTip("")
        wrapper.setStatusTip("")
        wrapper.setWhatsThis("")
        lay = QHBoxLayout(wrapper)
        lay.setContentsMargins(4, 0, 0, 0)
        lay.setSpacing(4)
        self.header_lang_buttons = {}
        for caption, lang in (("中", "zh"), ("EN", "en")):
            btn = QPushButton(caption)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip("")
            btn.setStatusTip("")
            btn.setWhatsThis("")
            btn.clicked.connect(lambda _checked=False, value=lang, button=btn: self._on_language_button_clicked(value, button))
            self.header_lang_buttons[lang] = btn
            lay.addWidget(btn)
        self._refresh_header_language_buttons(core.config.get("language", get_language()))
        return wrapper

    def _on_language_button_clicked(self, lang_data: str, button=None):
        if button is not None:
            self._animate_widget_flash(button)
        self._apply_language_change(lang_data, source=button)

    def _clear_settings_widget_refs(self):
        """Settings dialog owns these widgets; never keep stale PySide wrappers after it closes."""
        self._settings_dialog = None
        for attr in (
            "lang_combo", "theme_color_edit", "theme_color_preview", "font_path_edit",
            "dpi_scale_slider", "dpi_scale_value_label", "bg_check", "auto_start_check",
            "tray_check", "tray_action", "tray_notify_check", "_settings_nav",
            "_settings_navigator", "settings_search_edit", "settings_search_result_label",
        ):
            try:
                if hasattr(self, attr):
                    delattr(self, attr)
            except Exception:
                pass

    def _add_status_animation(self):
        """Keep status updates repaint-only; avoid opacity-effect painter warnings."""
        try:
            if hasattr(self, "status_label"):
                self.status_label.update()
        except Exception:
            pass

    def _apply_status_label_text(self, text: str | None = None):
        """Render a compact, non-jittering status message in the header.

        Long English messages used to push the right-side buttons or overflow the
        header.  Keep the full text in the tooltip and draw an elided one-line
        version in the label.
        """
        if not hasattr(self, "status_label"):
            return
        text = str(text if text is not None else getattr(self, "_status_full_text", "") or "")
        self._status_full_text = text
        try:
            # Use the label's actual rendered width as the eliding budget.
            # The old code capped at maximumWidth(360) which caused premature
            # "..." truncation.  Now that the label is Expanding with no hard
            # cap, width() reflects the true available horizontal space.
            available = max(120, self.status_label.width() - 12)
            display = self.status_label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, available)
        except Exception:
            display = text
        try:
            self.status_label.setText(display)
            self.status_label.setToolTip(text)
        except RuntimeError:
            pass

    def _clear_status_if_idle(self):
        if getattr(self, "_current_operation_name", ""):
            return
        self._apply_status_label_text(t("就绪"))
        self._add_status_animation()

    def set_status(self, text: str):
        self._apply_status_label_text(text)
        # Finish/error/info messages should not remain forever in the upper-right
        # corner.  Active operations are exempt and stay visible until they end.
        try:
            if getattr(self, "_current_operation_name", ""):
                self._status_reset_timer.stop()
            else:
                self._status_reset_timer.start(6500)
        except Exception:
            pass
        self._add_status_animation()

    def begin_operation(self, name: str, cancellable: bool = False):
        self._current_operation_name = name
        self._current_operation_cancel.clear()
        if hasattr(self, "cancel_operation_btn"):
            self.cancel_operation_btn.setEnabled(bool(cancellable))
        self.set_status(name)

    def finish_operation(self, text: str = t("操作完成")):
        self._current_operation_name = ""
        self._current_operation_cancel.clear()
        if hasattr(self, "cancel_operation_btn"):
            self.cancel_operation_btn.setEnabled(False)
        self.set_status(text)

    def _show_non_modal_warning(self, title: str, message: str):
        """显示非阻塞警告框，避免视频壁纸等后台错误把主界面卡住。"""
        from ui.dialog_style import show_non_modal_warning as _show_non_modal_warning_helper
        if not hasattr(self, "_non_modal_dialogs"):
            self._non_modal_dialogs = []
        _show_non_modal_warning_helper(self, title, message, tracker=self._non_modal_dialogs)

    def _run_mode_transition(self, label: str, worker_fn):
        """串行后台执行模式切换，避免 stop_video_wallpaper / stop_slideshow 阻塞 GUI。"""
        if self._core_busy:
            self.set_status(t("已有壁纸操作正在执行，请稍候…"))
            return None
        self._core_busy = True
        try:
            core.clear_cancel_operations()
        except Exception:
            pass
        self.begin_operation(label, cancellable=True)

        def _worker():
            """Run the worker in a background thread and emit a result signal.

            The worker_fn may return a boolean, a (success, message) tuple,
            or arbitrary data.  Handle tuples where the first element is a
            boolean to avoid mis-interpreting (False, message) as success.
            """
            try:
                if core.is_operation_cancelled():
                    self.core_result_signal.emit(False, t("操作已终止"), None)
                    return
                result = worker_fn()
                core.save_config()
                # Normalize (bool, message) tuple results
                if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
                    ok, msg = result
                    # When the boolean indicates failure, treat accordingly
                    if not ok:
                        reason = msg or getattr(core, "last_operation_error", "") or ""
                        message = str(reason) if reason else t("操作失败")
                        self.core_result_signal.emit(False, message, result)
                        return
                    # On success, use the second element as the result payload
                    result_payload = msg
                else:
                    ok = None
                    result_payload = result
                # A plain False result indicates failure
                if result is False:
                    reason = getattr(core, "last_operation_error", "") or ""
                    msg = t("操作失败") + (f"：{reason}" if reason else "")
                    self.core_result_signal.emit(False, msg, result)
                    return
                if core.is_operation_cancelled():
                    self.core_result_signal.emit(False, t("操作已终止"), result_payload)
                else:
                    self.core_result_signal.emit(True, t("操作完成"), result_payload)
            except Exception as exc:
                core.log_error("后台模式切换失败", exc)
                self.core_result_signal.emit(False, str(exc), None)
            finally:
                try:
                    core.clear_cancel_operations()
                except Exception:
                    pass

        self._core_worker_thread = threading.Thread(target=_worker, daemon=True)
        self._core_worker_thread.start()
        return None

    def _toggle_operation_panel(self):
        self._operation_panel_expanded = not getattr(self, "_operation_panel_expanded", False)
        if hasattr(self, "operation_panel"):
            self.operation_panel.setVisible(self._operation_panel_expanded)
        if hasattr(self, "operation_expand_btn"):
            self.operation_expand_btn.setToolTip(t("收起当前操作详情") if self._operation_panel_expanded else t("当前操作详情"))
            if self.operation_expand_btn.icon().isNull():
                self.operation_expand_btn.setText("i")

    def request_cancel_current_operation(self):
        self._current_operation_cancel.set()
        self.set_status(t("正在请求终止当前操作…"))
        if hasattr(self, "cancel_operation_btn"):
            self.cancel_operation_btn.setEnabled(False)
        if hasattr(self, "bing_status") and self._current_operation_name.startswith(t("正在同步必应")):
            self.bing_status.setText(t("正在请求终止当前同步…"))
        else:
            try:
                core.request_cancel_operations(t("用户请求"))
                threading.Thread(target=core.stop_slideshow, daemon=True).start()
                if core.is_video_wallpaper_running():
                    threading.Thread(target=core.stop_video_wallpaper, daemon=True).start()
            except Exception as exc:
                core.log_error("请求终止壁纸操作失败", exc)

    def _deferred_gui_startup(self):
        """窗口创建后只执行非关键后台任务；配置控件已在首帧前同步完成。"""
        if getattr(self, "_startup_gui_tasks_scheduled", False):
            return
        self._startup_gui_tasks_scheduled = True
        try:
            if getattr(core, "CONFIG_MIGRATION_PENDING", False) or core.config.get("__config_migration_pending__"):
                QTimer.singleShot(180, lambda: threading.Thread(target=core.flush_pending_config_migration, daemon=True).start())
        except Exception as exc:
            core.log_error("延迟保存配置迁移失败", exc)

        # v1.4.6: 三档性能模式 → 启动延迟
        level = self._perf_level()
        if level == "power_saver":
            _d_preview, _d_bing, _d_native, _d_tray, _d_autostart, _d_bingtask, _d_status = 400, 1200, 700, 950, 2400, 2800, 1700
        elif level == "performance":
            _d_preview, _d_bing, _d_native, _d_tray, _d_autostart, _d_bingtask, _d_status = 260, 900, 520, 720, 1900, 2200, 1300
        else:
            _d_preview, _d_bing, _d_native, _d_tray, _d_autostart, _d_bingtask, _d_status = 100, 320, 260, 420, 1500, 1250, 950

        def _later(delay_ms: int, callback):
            QTimer.singleShot(delay_ms, lambda: self._run_startup_callback(callback))

        self._schedule_preview_refresh(_d_preview)
        if is_feature_enabled("bing"):
            _later(_d_bing, self.refresh_bing_cache_list)
        _later(_d_native, self.apply_native_window_effect)
        _later(_d_tray, lambda: self.create_or_update_tray() if core.config.get("tray_icon", True) else None)
        _later(_d_autostart, self.maybe_show_auto_start_prompt)
        if is_feature_enabled("bing"):
            _later(_d_bingtask, self.run_bing_startup_tasks)
        _later(_d_status, lambda: self.set_status(t("欢迎使用")) if not self._current_operation_name else None)

    def _run_startup_callback(self, callback) -> None:
        if not callable(callback):
            return
        try:
            callback()
        except RuntimeError:
            pass
        except Exception as exc:
            try:
                core.log_error("启动任务执行失败", exc)
            except Exception:
                pass

    def _preview_poll_interval(self) -> int:
        # v1.4.6: 三档性能模式 → 预览轮询间隔
        level = self._perf_level()
        if level == "power_saver":
            return 4000
        if level == "performance":
            return 3000
        return 1200  # balanced (默认)

    def _perf_level(self) -> str:
        """返回当前性能模式: 'power_saver' / 'balanced' / 'performance'."""
        level = str(core.config.get("performance_level", "")).lower()
        if level in ("power_saver", "balanced", "performance"):
            return level
        if bool(core.config.get("performance_mode", False)):
            return "performance"
        return "balanced"

    def _apply_performance_mode_runtime(self) -> None:
        try:
            if hasattr(self, "_preview_refresh_timer"):
                self._preview_refresh_timer.setInterval(self._preview_poll_interval())
        except Exception:
            pass
        self._sync_tab_page_animation_state()

    def _refresh_shell_ui_later(self) -> None:
        # 仅刷新一次 (之前双发 0ms + 120ms 会导致任务栏重绘两次 → 卡顿).
        # refresh_shell_ui() 已改为异步 RDW_INVALIDATE (无 RDW_UPDATENOW), 不阻塞.
        try:
            QTimer.singleShot(0, core.refresh_shell_ui)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        # No first-show opacity animation: startup should be immediately usable and
        # consistent with the removed page transition policy.
        if getattr(self, "_first_show_anim", False):
            self._first_show_anim = False
            try:
                self.setWindowOpacity(1.0)
            except Exception:
                pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_status_label_text()

    def prepare_initial_geometry(self):
        """Start at the declared minimum size and center on the active screen."""
        try:
            min_size = self.minimumSize()
            self.resize(min_size)
            app = QApplication.instance()
            screen = None
            try:
                screen = self.screen() if self.screen() is not None else None
            except Exception:
                screen = None
            if screen is None and app is not None:
                screen = app.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            frame = self.frameGeometry()
            frame.setSize(min_size)
            frame.moveCenter(geo.center())
            self.move(frame.topLeft())
        except Exception:
            pass

    def _apply_theme(self):
        """应用 UI 主题：使用精心调校的 QSS 样式表美化界面，支持主题色。"""
        app = QApplication.instance()
        try:
            if app is not None:
                app.setStyle(QStyleFactory.create("Fusion"))
        except Exception:
            pass
        self.setMinimumSize(1120, 720)
        self._theme_color = core.config.get("theme_color", DEFAULT_THEME_COLOR) or DEFAULT_THEME_COLOR
        self._rebuild_stylesheet()

    def _stylesheet_font_family(self) -> str:
        """返回 QSS 使用的字体族列表，避免全局样式表覆盖用户选择的字体。"""
        app = QApplication.instance()
        primary = app.font().family() if app is not None else ""
        families = [
            primary,
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "SimHei",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
            "PingFang SC",
            "Segoe UI",
            "Arial",
        ]
        seen = []
        for family in families:
            family = str(family or "").replace('"', "").strip()
            if family and family not in seen:
                seen.append(family)
        return ", ".join(f'"{family}"' for family in seen) or '"Segoe UI"'


    def _theme_is_dark(self) -> bool:
        return bool(core.config.get("dark_mode", False))

    def _central_container_bg(self) -> str:
        """Return the exact bg color of #CentralContainer for the current theme.

        The sprite button sits directly on the CentralContainer surface (no
        QGroupBox wrapper), so its background must match the CentralContainer
        color exactly.  ``_theme_role_colors()["bg_main"]`` returns a slightly
        different shade that doesn't match the actual QSS, causing a visible
        color seam around the sprite button in both light and dark mode.
        """
        return "#252638" if self._theme_is_dark() else "#f0f2f5"

    def _animations_enabled(self) -> bool:
        """Return True when interface animations should be played.

        Controlled by the ``enable_animations`` config flag (default True to
        preserve the historical behaviour).  When False, every decorative
        animation in the app should short-circuit to the end state:

          * ``WallpaperSidebar.animate_in`` / ``animate_out`` jump to the
            final position instead of sliding.
          * ``_fade_about_sprite_to`` skips the crossfade and snaps to the
            target frame.
          * ``_sync_tab_page_animation_state`` is already a no-op.

        Functional animations driven by user input (spin-box arrows, progress
        bars, etc.) are not affected because they are not gated by this flag —
        only the decorative ones.
        """
        try:
            return bool(core.config.get("enable_animations", True))
        except Exception:
            return True

    def _theme_role_colors(self) -> dict[str, str]:
        """Return the exact colors used by the generated application QSS.

        Keeping programmatic/palette surfaces in lock-step with the stylesheet
        prevents a one-pixel halo around rounded controls after live theme
        changes.
        """
        if self._theme_is_dark():
            return {
                "bg_main": "#1a1b2e", "bg_widget": "#252638", "bg_input": "#2d2f42",
                "fg_primary": "#e8e8f0", "fg_secondary": "#c8c8d8", "fg_muted": "#8b8da0",
                "border": "#3d3e56", "note_bg": "#2d2f42", "danger_bg": "#3b1010",
            }
        return {
            "bg_main": "#ffffff", "bg_widget": "#f0f2f5", "bg_input": "#ffffff",
            "fg_primary": "#1f2328", "fg_secondary": "#656d76", "fg_muted": "#6b7280",
            "border": "#d8dee4", "note_bg": "#f6f8fa", "danger_bg": "#fff5f5",
        }


    def _combo_popup_stylesheet(self) -> str:
        """Use the same theme roles for ShangComboBox's custom QMenu popup."""
        colors = self._theme_role_colors()
        dark = self._theme_is_dark()
        accent = getattr(self, "_theme_color", core.config.get("theme_color", DEFAULT_THEME_COLOR)) or DEFAULT_THEME_COLOR
        qcolor = QColor(accent)
        if not qcolor.isValid():
            accent = DEFAULT_THEME_COLOR
            qcolor = QColor(accent)
        brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000
        if dark:
            hover = "#30304c"
            selected_bg = "#8b8ba3" if brightness >= 230 else accent
            selected_fg = "#ffffff"
        else:
            hover = "#f0f2f5"
            selected_bg = "#8c959f" if brightness >= 230 else accent
            selected_fg = "#ffffff" if brightness >= 230 or brightness < 170 else "#24292f"
        return (
            f"QMenu#ComboBoxMenu {{ background-color: {colors['bg_input']}; color: {colors['fg_primary']}; "
            f"border: 1px solid {colors['border']}; padding: 4px; }}"
            f"QMenu#ComboBoxMenu::item {{ padding: 7px 18px; border-radius: 6px; min-height: 24px; }}"
            f"QMenu#ComboBoxMenu::item:selected {{ background-color: {hover}; color: {colors['fg_primary']}; }}"
            f"QMenu#ComboBoxMenu::item:checked {{ background-color: {selected_bg}; color: {selected_fg}; font-weight: 600; }}"
            f"QMenu#ComboBoxMenu::item:disabled {{ color: {colors['fg_muted']}; }}"
            "QMenu#ComboBoxMenu::indicator { width: 0px; height: 0px; }"
        )

    def _apply_plain_background_palette(self, widget, color: str) -> None:
        """Set a local widget background with QPalette instead of child-affecting QSS."""
        if widget is None:
            return
        try:
            pal = widget.palette()
            qcolor = QColor(color)
            for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base):
                pal.setColor(role, qcolor)
            widget.setPalette(pal)
            widget.setAutoFillBackground(True)
            widget.update()
        except RuntimeError:
            pass
        except Exception:
            pass

    def _refresh_settings_dialog_surfaces(self) -> None:
        """Refresh settings backgrounds without painting beneath rounded pages.

        The dialog and scroll viewport are intentionally flat.  Only the page
        widget paints the rounded surface; otherwise palette auto-fill and QSS
        paint two anti-aliased edges on top of each other.
        """
        dlg = getattr(self, "_settings_dialog", None)
        if not self._is_qobject_alive(dlg):
            return
        dialog_bg = self._theme_role_colors()["bg_main"]
        self._apply_plain_background_palette(dlg, dialog_bg)
        try:
            for scroll in dlg.findChildren(QScrollArea):
                try:
                    scroll.setStyleSheet("")
                    scroll.setAutoFillBackground(False)
                    viewport = scroll.viewport()
                    if viewport is not None:
                        viewport.setStyleSheet("")
                        viewport.setAutoFillBackground(False)
                    child = scroll.widget()
                    if child is not None:
                        child.setAutoFillBackground(False)
                        child.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                        child.update()
                except RuntimeError:
                    continue
                except Exception:
                    continue
            dlg.update()
        except RuntimeError:
            self._settings_dialog = None
        except Exception:
            pass

    def _text_style(self, role: str = "primary", extra: str = "") -> str:
        colors = self._theme_role_colors()
        key = "fg_primary" if role == "primary" else "fg_muted" if role == "muted" else "fg_secondary"
        prefix = (extra.strip().rstrip(";") + "; ") if extra else ""
        return f"{prefix}color: {colors[key]};"

    def _surface_note_style(self, extra: str = "") -> str:
        colors = self._theme_role_colors()
        prefix = (extra.strip().rstrip(";") + "; ") if extra else ""
        return (f"{prefix}color: {colors['fg_secondary']}; background: {colors['note_bg']}; "
                f"border: 1px solid {colors['border']}; border-radius: 8px;")

    def _extra_theme_qss(self, dark: bool) -> str:
        if dark:
            bg_main = "#1a1b2e"
            bg_widget = "#252638"
            bg_input = "#2d2f42"
            fg_primary = "#e8e8f0"
            fg_muted = "#9b9bb0"
            border = "#3d3e56"
            hover = "#2e3045"
            disabled_bg = "#34354a"
            disabled_fg = "#6b6d84"
        else:
            bg_main = "#ffffff"
            bg_widget = "#ffffff"
            bg_input = "#ffffff"
            fg_primary = "#1f2328"
            fg_muted = "#656d76"
            border = "#d8dee4"
            hover = "#eef0f3"
            disabled_bg = "#e2e5ea"
            disabled_fg = "#9ca3ab"

        # Determine the directory containing SVG icons.  In source and packaged runs
        # ``core.BASE_DIR`` points at the resource root (containing the ``img``
        # folder); fallback to the directory of ``entry_script_path()`` if
        # ``BASE_DIR`` is missing.  Defining this here ensures it is available
        # when computing QSS icon URLs below.
        icon_dir = os.path.join(getattr(core, "BASE_DIR", os.path.dirname(entry_script_path())), "img")

        def qss_icon_url(filename: str) -> str:
            """
            Build a QSS-safe ``file://`` URL for the given icon without a
            cache-busting query string.

            In previous versions a query string was appended to the icon path
            to force Qt's ``QSvgRenderer`` to reload SVGs on theme changes.
            On some systems the QSS parser cannot resolve URLs containing
            query strings, causing missing checkbox and spin-box arrow icons.
            Additionally, resolving via :func:`image_qss_url` uses the global
            ``IMAGE_DIR`` which may not point at this platform's resource
            directory during development. To ensure icons are always found
            and loaded correctly we build an absolute path into the local
            ``img`` folder (``icon_dir``) and convert it to a QSS-safe
            ``file://`` URL using :func:`app.paths.qss_url_path` without
            specifying a cache-buster. This avoids percent-encoded query
            fragments while preserving proper URL escaping.
            """
            # Compose the full path to the icon inside this branch's img directory
            path = os.path.join(icon_dir, filename)
            # Lazily import inside the function to avoid circular imports at module import time.
            import app.paths as _paths  # type: ignore
            return _paths.qss_url_path(path)

        spin_up_fg_icon = "spin_arrow_up_light.svg" if dark else "spin_arrow_up_dark.svg"
        spin_down_fg_icon = "spin_arrow_down_light.svg" if dark else "spin_arrow_down_dark.svg"
        spin_up_disabled_name = "spin_arrow_up_disabled_dark.svg" if dark else "spin_arrow_up_disabled_light.svg"
        spin_down_disabled_name = "spin_arrow_down_disabled_dark.svg" if dark else "spin_arrow_down_disabled_light.svg"
        # Verify SVG files exist at build time; log a warning if not found.
        for _name in (spin_up_fg_icon, spin_down_fg_icon, spin_up_disabled_name,
                      spin_down_disabled_name, "checkbox_check.svg", "checkbox_dash.svg",
                      "checkbox_check_disabled.svg"):
            _f = os.path.join(icon_dir, _name)
            if not os.path.exists(_f):
                try:
                    import logging
                    logging.getLogger("core").warning(f"SVG icon not found: {_f}")
                except Exception:
                    pass
        spin_up_icon = qss_icon_url(spin_up_fg_icon)
        spin_down_icon = qss_icon_url(spin_down_fg_icon)
        spin_up_disabled_icon = qss_icon_url(spin_up_disabled_name)
        spin_down_disabled_icon = qss_icon_url(spin_down_disabled_name)
        checkbox_check_icon = qss_icon_url("checkbox_check.svg")
        checkbox_dash_icon = qss_icon_url("checkbox_dash.svg")
        checkbox_check_disabled_icon = qss_icon_url("checkbox_check_disabled.svg")
        qss = """
/* Extra cross-platform contrast fixes */
/* Unified dialog surfaces — keep selector list in sync with ui.dialog_style */

QDialog#GlobalSettingsDialog { background-color: __BG_MAIN__; }
QScrollArea#SettingsPageScroll,
QScrollArea#SettingsPageScroll > QWidget { border: none; background-color: transparent; }
QWidget#SettingsPageSurface { background-color: __BG_WIDGET__; border-radius: 12px; background-clip: padding; }
QWidget#scrollAreaWidgetContents, QWidget#MainTabSurface { background-color: transparent; }
/* Clip fills to the padding box so border and background anti-alias only once. */
QGroupBox, QPushButton, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QProgressBar, QListWidget, QTextEdit, QPlainTextEdit, QTableWidget,
QTreeWidget, QTableView, QTreeView, QToolTip { background-clip: padding; }
QTabWidget::pane { background-clip: padding; }
QMessageBox, QFileDialog, QColorDialog, QDialogButtonBox { background-color: __BG_WIDGET__; color: __FG_PRIMARY__; }
QDialog QLabel, QMessageBox QLabel, QFileDialog QLabel, QColorDialog QLabel { background-color: transparent; color: __FG_PRIMARY__; }
QDialogButtonBox QPushButton { background: %%tc%%; color: %%btn_text%%; border: 1px solid %%btn_border%%; border-radius: 6px; padding: 5px 14px; min-height: 28px; }
QDialogButtonBox QPushButton:hover:enabled { background: %%hover_c%%; }
QDialogButtonBox QPushButton:disabled { background: __DISABLED_BG__; color: __DISABLED_FG__; border-color: __BORDER__; }
/* Transparent generic frames avoid double rounded-corner bleed-through under QWidget surfaces.
   Components that need cards should use QGroupBox or an object-name-specific rule. */
QFrame { background-color: transparent; }
/* Unified dialog title roles — see ui.dialog_style.DIALOG_TITLE_STYLE */
QLabel[dialogTitle="true"] { font-size: 18px; font-weight: 700; background: transparent; }
QLabel[dialogHeroTitle="true"] { font-size: 22px; font-weight: 700; background: transparent; }
QLabel[dialogNote="true"] { font-size: 13px; background: transparent; color: __FG_MUTED__; }
QAbstractItemView { background-color: __BG_INPUT__; color: __FG_PRIMARY__; border: 1px solid __BORDER__; selection-background-color: %%visible_accent%%; selection-color: %%accent_text%%; }
QComboBox QAbstractItemView, QListView#ComboPopupView { background-color: __BG_INPUT__; color: __FG_PRIMARY__; border: 1px solid __BORDER__; border-radius: 8px; padding: 4px; outline: none; }
QComboBox QAbstractItemView::item, QListView#ComboPopupView::item { min-height: 30px; padding: 6px 12px; border-radius: 6px; }
QComboBox QAbstractItemView::item:hover, QListView#ComboPopupView::item:hover { background-color: __HOVER__; }
QComboBox QAbstractItemView::item:selected, QListView#ComboPopupView::item:selected { background-color: %%visible_accent%%; color: %%accent_text%%; }
QHeaderView::section { background-color: __HOVER__; color: __FG_PRIMARY__; border: 1px solid __BORDER__; padding: 6px 8px; font-weight: 600; }
QTableWidget, QTreeWidget, QTableView, QTreeView { background-color: __BG_INPUT__; color: __FG_PRIMARY__; gridline-color: __BORDER__; alternate-background-color: __BG_WIDGET__; border-radius: 6px; }
QSpinBox, QDoubleSpinBox {
border: 1px solid __BORDER__;
border-radius: 8px;
padding: 3px 24px 3px 10px;
background-color: __BG_INPUT__;
color: __FG_PRIMARY__;
font-size: 13px;
min-height: 28px;
min-width: 70px;
max-width: 118px;
}
QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid %%visible_accent%%; padding: 2px 23px 2px 9px; }
QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled { background-color: __DISABLED_BG__; color: __DISABLED_FG__; border-color: __BORDER__; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
subcontrol-origin: border;
subcontrol-position: top right;
width: 22px;
height: 15px;
margin-top: 1px;
margin-right: 1px;
border-left: 1px solid __BORDER__;
border-top-right-radius: 7px;
background-color: transparent;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
subcontrol-origin: border;
subcontrol-position: bottom right;
width: 22px;
height: 15px;
margin-bottom: 1px;
margin-right: 1px;
border-left: 1px solid __BORDER__;
border-bottom-right-radius: 7px;
background-color: transparent;
}
QSpinBox::up-button:hover:enabled, QDoubleSpinBox::up-button:hover:enabled { background-color: __HOVER__; }
QSpinBox::down-button:hover:enabled, QDoubleSpinBox::down-button:hover:enabled { background-color: __HOVER__; }
QSpinBox::up-button:pressed:enabled, QDoubleSpinBox::up-button:pressed:enabled,
QSpinBox::down-button:pressed:enabled, QDoubleSpinBox::down-button:pressed:enabled { background-color: __BORDER__; }
QSpinBox::up-button:disabled, QDoubleSpinBox::up-button:disabled,
QSpinBox::down-button:disabled, QDoubleSpinBox::down-button:disabled { background-color: __DISABLED_BG__; border-color: __BORDER__; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { width: 10px; height: 10px; margin: 0px; image: url("%%spin_up_icon%%"); }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { width: 10px; height: 10px; margin: 0px; image: url("%%spin_down_icon%%"); }
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled { image: url("%%spin_up_disabled_icon%%"); }
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled { image: url("%%spin_down_disabled_icon%%"); }
QSpinBox#CompactNumberSpin {
min-width: 64px;
max-width: 64px;
padding: 3px 22px 3px 8px;
}
QSpinBox#CompactNumberSpin:focus { padding: 2px 21px 2px 7px; }
QSpinBox#CompactNumberSpin::up-button { width: 22px; }
QSpinBox#CompactNumberSpin::down-button { width: 22px; }
QSpinBox#CompactNumberSpin::up-arrow, QSpinBox#CompactNumberSpin::down-arrow { width: 10px; height: 10px; margin: 0px; }
QCheckBox { spacing: 10px; font-size: 13px; font-weight: 400; min-height: 26px; background-color: transparent; color: __FG_PRIMARY__; }
QCheckBox:hover { font-size: 13px; font-weight: 400; }
QCheckBox:disabled { color: __DISABLED_FG__; }
QCheckBox::indicator {
width: 18px;
height: 18px;
border: 1.5px solid __BORDER__;
border-radius: 5px;
background-color: __BG_INPUT__;
}
QCheckBox::indicator:hover:enabled { border: 1.5px solid %%visible_accent%%; background-color: __HOVER__; }
QCheckBox::indicator:checked { border-color: %%visible_accent%%; background-color: %%visible_accent%%; image: url("%%checkbox_check_icon%%"); }
QCheckBox::indicator:checked:hover:enabled { border-color: %%pressed_c%%; background-color: %%pressed_c%%; }
QCheckBox::indicator:indeterminate { border-color: %%visible_accent%%; background-color: %%visible_accent%%; image: url("%%checkbox_dash_icon%%"); }
QCheckBox::indicator:disabled { border-color: __BORDER__; background-color: __DISABLED_BG__; }
QCheckBox::indicator:checked:disabled { border-color: __BORDER__; background-color: __DISABLED_BG__; image: url("%%checkbox_check_disabled_icon%%"); }
QSlider::groove:horizontal { height: 6px; background: __BORDER__; border-radius: 3px; }
QSlider::handle:horizontal { width: 18px; height: 18px; margin: -6px 0; border-radius: 9px; background: %%visible_accent%%; }
QSlider::handle:horizontal:hover { background: %%pressed_c%%; }
QToolTip { background-color: __BG_INPUT__; color: __FG_PRIMARY__; padding: 6px 10px; font-size: 12px; }
QWidget[settingsSearchMatch="true"] { border: 2px solid %%visible_accent%%; }
QMenu { background-color: __BG_WIDGET__; color: __FG_PRIMARY__; border: 1px solid __BORDER__; padding: 6px; }
QMenu::item { padding: 8px 24px; border-radius: 6px; }
QMenu::item:selected { background-color: __HOVER__; }
/* QMenu#ComboBoxMenu — popup rendered by ShangComboBox.showPopup().
   Keep padding / item height / radius in sync with the
   ``QComboBox QAbstractItemView`` block above so the two popup styles
   (QMenu-based and QListView-based) are pixel-identical. */
QMenu#ComboBoxMenu { padding: 6px; }
QMenu#ComboBoxMenu::item { min-width: 140px; min-height: 28px; padding: 4px 12px; border-radius: 6px; }
QMenu#ComboBoxMenu::item:selected { background-color: __HOVER__; color: __FG_PRIMARY__; }
QMenu#ComboBoxMenu::item:checked { background-color: %%visible_accent%%; color: %%accent_text%%; font-weight: 600; }
QMenu#ComboBoxMenu::item:disabled { color: __DISABLED_FG__; }
QMenu#ComboBoxMenu::indicator { width: 0px; height: 0px; }
QMenu::item:disabled { color: __DISABLED_FG__; }
QFrame#HeaderLangSwitch { background-color: transparent; }
QFormLayout { vertical-spacing: 10px; }
QGroupBox QFormLayout { vertical-spacing: 10px; }
QLabel[muted="true"] { color: __FG_MUTED__; }
"""
        return (qss.replace("__BG_MAIN__", bg_main).replace("__BG_WIDGET__", bg_widget).replace("__BG_INPUT__", bg_input)
                   .replace("__FG_PRIMARY__", fg_primary).replace("__FG_MUTED__", fg_muted)
                   .replace("__BORDER__", border).replace("__HOVER__", hover)
                   .replace("__DISABLED_BG__", disabled_bg).replace("__DISABLED_FG__", disabled_fg)
                   .replace("%%spin_up_icon%%", spin_up_icon).replace("%%spin_down_icon%%", spin_down_icon)
                   .replace("%%spin_up_disabled_icon%%", spin_up_disabled_icon).replace("%%spin_down_disabled_icon%%", spin_down_disabled_icon)
                   .replace("%%checkbox_check_icon%%", checkbox_check_icon).replace("%%checkbox_dash_icon%%", checkbox_dash_icon)
                   .replace("%%checkbox_check_disabled_icon%%", checkbox_check_disabled_icon))

    def _rebuild_stylesheet(self):
        """根据当前主题色和暗色模式重建 QSS 样式表。"""
        app = QApplication.instance()
        tc = self._theme_color
        dark = bool(core.config.get("dark_mode", False))
        from PySide6.QtGui import QColor
        base = QColor(tc)
        if not base.isValid():
            tc = DEFAULT_THEME_COLOR
            self._theme_color = tc
            base = QColor(tc)

        if dark:
            # ── 暗色模式配色：只换颜色，不动任何布局属性 ──
            bg_main = "#1a1b2e"
            bg_widget = "#252638"
            bg_input = "#2d2f42"
            fg_primary = "#e8e8f0"
            fg_secondary = "#c8c8d8"
            border_color = "#3d3e56"
            group_bg = "#252638"
            scroll_bg = "#141526"
            scroll_handle = "#3d3e56"
            scroll_handle_hover = "#5d5e76"
            theme_brightness = (base.red() * 299 + base.green() * 587 + base.blue() * 114) / 1000
            if theme_brightness >= 230:
                # Very light accent colors turn buttons white in dark mode; use a darkened accent-safe surface instead.
                tc_for_buttons = "#3a3a50"
                hover_c = "#45455f"
                pressed_c = "#50506a"
                btn_top = tc_for_buttons
                btn_hover_top = hover_c
                btn_text = "#e6e6f0"
                btn_border = "#5a5a73"
                visible_accent = "#8b8ba3"
                progress_chunk = visible_accent
                accent_text = "#ffffff"
            else:
                tc_for_buttons = tc
                hover_c = base.lighter(115).name()
                pressed_c = base.lighter(130).name()
                btn_top = base.name()
                btn_hover_top = base.lighter(110).name()
                btn_text = "#e0e0e0" if theme_brightness >= 170 else "#ffffff"
                btn_border = base.darker(118).name()
                visible_accent = tc
                progress_chunk = tc
                accent_text = "#ffffff"
            disabled_bg = "#34354a"
            disabled_text = "#6b6d84"
            muted_color = "#8b8da0"
            nav_hover = "#2a2b42"
            # ── 暗色模板：布局属性与亮色完全一致 ──
            _TPL = (
                "/* ── 暗色模式 ── */\n"
                f"QMainWindow, QDialog {{ background-color: {bg_main}; }}\n"
                f"QWidget {{ color: {fg_primary}; font-family: %%font_family%%; }}\n"
                f"#CentralContainer {{ background-color: {bg_widget}; }}\n"
                f"QLabel {{ background-color: transparent; color: {fg_primary}; }}\n"
                "\n"
                "/* 分组框样式 */\n"
                f"QGroupBox {{ font-weight: 600; font-size: 13px; border: 1px solid {border_color}; border-radius: 10px;"
                f" margin-top: 14px; padding: 18px 14px 14px 14px; background-color: {group_bg}; }}\n"
                f"QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left;"
                f" padding: 2px 12px; left: 12px; color: {fg_primary}; font-size: 13px; font-weight: 700; }}\n"
                "\n"
                "/* 按钮 */\n"
                f"QPushButton {{ background: %%tc%%; color: %%btn_text%%; border: 1px solid %%btn_border%%; border-radius: 7px;"
                f" padding: 6px 16px; font-size: 13px; font-weight: 500; min-height: 28px; }}\n"
                f"QPushButton:hover:enabled {{ background: %%hover_c%%; }}\n"
                f"QPushButton:pressed:enabled {{ background: %%pressed_c%%; padding-top: 7px; padding-bottom: 5px; }}\n"
                f"QPushButton:disabled {{ background: {disabled_bg}; border-color: {border_color}; color: {disabled_text}; }}\n"
                f"QPushButton[secondary=\"true\"] {{ background: %%tc%%; color: %%btn_text%%; border: 1px solid %%btn_border%%; }}\n"
                f"QPushButton[secondary=\"true\"]:hover:enabled {{ background: %%hover_c%%; }}\n"
                f"QPushButton[secondary=\"true\"]:pressed:enabled {{ background: %%pressed_c%%; padding-top: 7px; padding-bottom: 5px; }}\n"
                f"QPushButton[secondary=\"true\"]:disabled {{ background: {disabled_bg}; border-color: {border_color}; color: {disabled_text}; }}\n"
                f"QPushButton[settingsAction=\"true\"] {{ background: {bg_input}; color: {fg_primary}; border: 1px solid %%visible_accent%%; border-radius: 7px; padding: 6px 14px; font-size: 13px; font-weight: 500; min-height: 28px; }}\n"
                f"QPushButton[settingsAction=\"true\"]:hover:enabled {{ background: {nav_hover}; border-color: %%pressed_c%%; }}\n"
                f"QPushButton[settingsAction=\"true\"]:pressed:enabled {{ background: {nav_hover}; padding-top: 7px; padding-bottom: 5px; }}\n"
                f"QPushButton[settingsAction=\"true\"]:disabled {{ background: {disabled_bg}; border-color: {border_color}; color: {disabled_text}; }}\n"
                "\n"
                "/* 输入框 */\n"
                f"QLineEdit {{ border: 1px solid {border_color}; border-radius: 8px; padding: 6px 12px;"
                f" background-color: {bg_input}; color: {fg_primary}; font-size: 13px; min-height: 28px; }}\n"
                f"QLineEdit:focus {{ border-color: %%visible_accent%%; border-width: 2px; padding: 5px 11px; }}\n"
                "\n"
                "/* 下拉框 */\n"
                f"QComboBox {{ border: 1px solid {border_color}; border-radius: 8px; padding: 5px 12px;"
                f" background-color: {bg_input}; color: {fg_primary}; font-size: 13px; min-height: 28px; }}\n"
                f"QComboBox:focus {{ border-color: %%visible_accent%%; border-width: 2px; }}\n"
                f"QComboBox::drop-down {{ border: none; width: 24px; }}\n"
                "\n"
                "/* 复选框 */\n"
                f"QCheckBox {{ spacing: 8px; font-size: 13px; font-weight: 400; min-height: 24px; background-color: transparent; color: {fg_primary}; }}\n"
                f"QCheckBox:hover {{ font-size: 13px; font-weight: 400; }}\n"
                "\n"
                "/* 选项卡 */\n"
                f"QTabWidget::pane {{ border: 1px solid {border_color}; border-radius: 10px;"
                f" background-color: {bg_widget}; padding: 6px; }}\n"
                f"QTabBar::tab {{ padding: 8px 22px; font-size: 13px; font-weight: 500;"
                f" border-top-left-radius: 7px; border-top-right-radius: 7px; margin-right: 2px;"
                f" background-color: {bg_widget}; color: {fg_secondary}; border: 1px solid transparent; border-bottom: none; }}\n"
                f"QTabBar::tab:selected {{ background-color: {bg_widget}; color: {fg_primary};"
                f" border: 1px solid {border_color}; border-bottom: 2px solid %%visible_accent%%; }}\n"
                f"QTabBar::tab:hover:!selected {{ background-color: {nav_hover}; }}\n"
                "\n"
                "/* 进度条 */\n"
                f"QProgressBar {{ border: 1px solid {border_color}; border-radius: 8px; text-align: center;"
                f" background-color: {bg_input}; color: {fg_primary}; height: 20px; font-size: 12px; }}\n"
                f"QProgressBar::chunk {{ background-color: %%progress_chunk%%; border-radius: 6px; }}\n"
                "\n"
                "/* 列表视图 */\n"
                f"QListWidget {{ border: 1px solid {border_color}; border-radius: 8px;"
                f" background-color: {bg_widget}; color: {fg_primary}; padding: 4px; }}\n"
                f"QListWidget::item {{ padding: 6px 10px; border-radius: 6px; }}\n"
                f"QListWidget::item:hover {{ background: {nav_hover}; }}\n"
                f"QListWidget::item:selected {{ background: %%visible_accent%%; color: %%accent_text%%; }}\n"
                f"QComboBox QAbstractItemView::item:selected {{ background: %%visible_accent%%; color: %%accent_text%%; }}\n"
                f"QTextEdit selection, QLineEdit selection {{ background: %%visible_accent%%; color: %%accent_text%%; }}\n"
                "\n"
                "/* 上下文菜单 */\n"
                f"QMenu {{ background: {bg_widget}; color: {fg_primary}; border: 1px solid {border_color}; padding: 6px; }}\n"
                f"QMenu::item {{ padding: 8px 28px; border-radius: 6px; }}\n"
                f"QMenu::item:selected {{ background: {nav_hover}; }}\n"
                f"QMenu::separator {{ height: 1px; background: {border_color}; margin: 4px 12px; }}\n"
                "\n"
                "/* 滚动区域与滚动条 */\n"
                f"QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget, QStackedWidget {{ border: none; background-color: {bg_widget}; }}\n"
                f"QDialog#GlobalSettingsDialog {{ background-color: {bg_main}; }}\n"
                f"QScrollArea#SettingsPageScroll, QScrollArea#SettingsPageScroll > QWidget {{ border: none; background-color: transparent; }}\n"
                f"QWidget#SettingsPageSurface {{ background-color: {bg_widget}; border-radius: 12px; background-clip: padding; }}\n"
                f"QScrollBar:vertical {{ background: {scroll_bg}; width: 8px; margin: 0; border-radius: 4px; }}\n"
                f"QScrollBar::handle:vertical {{ background: %%scroll_handle%%; min-height: 30px; border-radius: 4px; }}\n"
                f"QScrollBar::handle:vertical:hover {{ background: %%scroll_handle_hover%%; }}\n"
                f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; background: transparent; }}\n"
                f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}\n"
                f"QScrollBar:horizontal {{ background: {scroll_bg}; height: 8px; margin: 0; border-radius: 4px; }}\n"
                f"QScrollBar::handle:horizontal {{ background: %%scroll_handle%%; min-width: 30px; border-radius: 4px; }}\n"
                f"QScrollBar::handle:horizontal:hover {{ background: %%scroll_handle_hover%%; }}\n"
                f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; background: transparent; }}\n"
                f"QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}\n"
                "\n"
                "/* 文本编辑框 */\n"
                f"QTextEdit, QPlainTextEdit {{ border: 1px solid {border_color}; border-radius: 8px;"
                f" background-color: {bg_input}; color: {fg_primary}; padding: 8px;"
                f" font-family: \"Cascadia Code\", \"Consolas\", \"Microsoft YaHei UI\", monospace;"
                f" font-size: 12px; }}\n"
                f"QPushButton#OperationInfoButton {{ background: transparent; color: {fg_secondary}; border: 1px solid {border_color};"
                f" border-radius: 13px; padding: 0; min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; }}\n"
                f"QPushButton#OperationInfoButton:hover {{ border-color: %%visible_accent%%; color: %%visible_accent%%; background: {bg_input}; }}\n"
                f"QPushButton#OperationInfoButton:pressed {{ background: {bg_input}; }}\n"
                f"QPushButton#CancelOperationButton {{ background: {bg_widget}; color: {fg_secondary}; border: 1px solid {border_color};"
                f" border-radius: 7px; padding: 5px 12px; min-height: 26px; }}\n"
                f"QPushButton#CancelOperationButton:hover:enabled {{ color: #f87171; border-color: #7f1d1d; background: #3b1010; }}\n"
                f"QPushButton#CancelOperationButton:pressed:enabled {{ background: #2d0a0a; }}\n"
                "/* 灰度提示 */\n"
                f"*[muted=\"true\"] {{ color: {muted_color}; }}\n"
            )
        else:
            hover_c = base.darker(108).name()
            pressed_c = base.darker(125).name()
            btn_top = base.lighter(115).name()
            btn_hover_top = base.lighter(125).name()
            theme_brightness = (base.red() * 299 + base.green() * 587 + base.blue() * 114) / 1000
            btn_border = "#d8dee4" if theme_brightness >= 230 else base.darker(115).name()
            btn_text = "#1f2328" if theme_brightness >= 170 else "#ffffff"
            visible_accent = "#8c959f" if theme_brightness >= 230 else tc
            scroll_handle = "#c0c8d0" if theme_brightness >= 230 else base.lighter(135).name()
            scroll_handle_hover = "#8c959f" if theme_brightness >= 230 else base.darker(105).name()
            progress_chunk = "#8c959f" if theme_brightness >= 230 else tc
            accent_text = "#ffffff" if theme_brightness >= 230 else btn_text

            _TPL = (
                "/* 全局字体与背景 */\n"
                "QMainWindow, QDialog { background-color: #ffffff; }\n"
                "QWidget { color: #1f2328; font-family: %%font_family%%; }\n"
                "#CentralContainer { background-color: #f0f2f5; }\n"
                "QLabel { background-color: transparent; color: #1f2328; }\n"
            "\n"
            "/* 分组框样式 */\n"
            "QGroupBox { font-weight: 600; font-size: 13px; border: 1px solid #d8dee4; border-radius: 10px;"
            " margin-top: 14px; padding: 18px 14px 14px 14px; background-color: #f6f8fa; }\n"
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;"
            " padding: 2px 12px; left: 12px; color: #1f2328; font-size: 13px; font-weight: 700; }\n"
            "\n"
            "/* 按钮样式 */\n"
            "QPushButton { background: %%tc%%;"
            " color: %%btn_text%%; border: 1px solid %%btn_border%%; border-radius: 7px;"
            " padding: 6px 16px; font-size: 13px; font-weight: 500; min-height: 28px; }\n"
            "QPushButton:hover:enabled { background: %%hover_c%%; }\n"
            "QPushButton:pressed:enabled { background: %%pressed_c%%; padding-top: 7px; padding-bottom: 5px; }\n"
            "QPushButton:disabled { background: #e2e5ea; border-color: #d0d6dc; color: #9ca3ab; }\n"
            "QPushButton[secondary=\"true\"] { background: %%tc%%; color: %%btn_text%%; border: 1px solid %%btn_border%%; }\n"
            "QPushButton[secondary=\"true\"]:hover:enabled { background: %%hover_c%%; }\n"
            "QPushButton[secondary=\"true\"]:pressed:enabled { background: %%pressed_c%%; padding-top: 7px; padding-bottom: 5px; }\n"
            "QPushButton[secondary=\"true\"]:disabled { background: #e2e5ea; border-color: #d0d6dc; color: #9ca3ab; }\n"
            "QPushButton[settingsAction=\"true\"] { background: #ffffff; color: #1f2328; border: 1px solid %%visible_accent%%; border-radius: 7px; padding: 6px 14px; font-size: 13px; font-weight: 500; min-height: 28px; }\n"
            "QPushButton[settingsAction=\"true\"]:hover:enabled { background: #f0f2f5; border-color: %%pressed_c%%; }\n"
            "QPushButton[settingsAction=\"true\"]:pressed:enabled { background: #e6e8ec; padding-top: 7px; padding-bottom: 5px; }\n"
            "QPushButton[settingsAction=\"true\"]:disabled { background: #f0f2f5; border-color: #d8dee4; color: #9ca3ab; }\n"
            "\n"
            "/* 输入框样式 */\n"
            "QLineEdit { border: 1px solid #d8dee4; border-radius: 8px; padding: 6px 12px;"
            " background-color: #ffffff; font-size: 13px; min-height: 28px; }\n"
            "QLineEdit:focus { border-color: %%visible_accent%%; border-width: 2px; padding: 5px 11px; }\n"
            "\n"
                            "/* 下拉框样式 — 与暗色分支保持一致：右侧 padding 留给 24px drop-down，\n"
                "   ::down-arrow 用统一 SVG 图标，跨平台外观一致。 */\n"
                "QComboBox { border: 1px solid #d8dee4; border-radius: 8px;"
                " padding: 4px 30px 4px 12px;"
                " background-color: #ffffff; font-size: 13px; min-height: 28px; }\n"
                "QComboBox:hover:enabled { border-color: %%hover_c%%; }\n"
                "QComboBox:focus { border-color: %%visible_accent%%; border-width: 2px;"
                " padding: 3px 29px 3px 11px; }\n"
                "QComboBox:on { border-color: %%visible_accent%%; }\n"
                "QComboBox::drop-down { subcontrol-origin: border; subcontrol-position: top right;"
                " width: 24px; border-left: none; border-top-right-radius: 8px;"
                " border-bottom-right-radius: 8px; background: transparent; }\n"
                "QComboBox::drop-down:hover { background-color: #eef0f3; }\n"
                "QComboBox::down-arrow { image: url(\"%%spin_down_icon%%\");"
                " width: 10px; height: 10px; }\n"
                "QComboBox::down-arrow:disabled { image: url(\"%%spin_down_disabled_icon%%\"); }\n"
            "\n"
            "/* 复选框 */\n"
            "QCheckBox { spacing: 8px; font-size: 13px; background-color: transparent; }\n"
            "\n"
            "/* 选项卡 */\n"
            "QTabWidget::pane { border: 1px solid #d8dee4; border-radius: 10px;"
            " background-color: #ffffff; padding: 6px; }\n"
            "QTabBar::tab { padding: 8px 22px; font-size: 13px; font-weight: 500;"
            " border-top-left-radius: 7px; border-top-right-radius: 7px; margin-right: 2px;"
            " background-color: #f6f8fa; color: #656d76; border: 1px solid transparent; border-bottom: none; }\n"
            "QTabBar::tab:selected { background-color: #ffffff; color: #1f2328;"
            " border: 1px solid #d8dee4; border-bottom: 2px solid %%visible_accent%%; }\n"
            "QTabBar::tab:hover:!selected { background-color: #eef0f3; }\n"
            "\n"
            "/* 进度条 */\n"
            "QProgressBar { border: 1px solid #d8dee4; border-radius: 8px; text-align: center;"
            " background-color: #f0f2f5; height: 20px; font-size: 12px; }\n"
            "QProgressBar::chunk { background-color: %%progress_chunk%%; border-radius: 6px; }\n"
            "\n"
            "/* 列表视图 */\n"
            "QListWidget { border: 1px solid #d8dee4; border-radius: 8px;"
            " background-color: #ffffff; padding: 4px; }\n"
            "QListWidget::item { padding: 6px 10px; border-radius: 6px; }\n"
            "QListWidget::item:hover { background: #eef0f3; }\n"
            "QListWidget::item:selected { background: %%visible_accent%%; color: %%accent_text%%; }\n"
            "QTextEdit selection, QLineEdit selection { background: %%visible_accent%%; color: %%accent_text%%; }\n"
            "\n"
            "/* 上下文菜单 */\n"
            "QMenu { background: #ffffff; color: #1f2328; border: 1px solid #d8dee4; padding: 6px; }\n"
            "QMenu::item { padding: 8px 28px; border-radius: 6px; }\n"
            "QMenu::item:selected { background: #eef0f3; }\n"
            "QMenu::separator { height: 1px; background: #e2e5ea; margin: 4px 12px; }\n"
            "\n"
            "/* 滚动区域与滚动条 */\n"
            "QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget, QStackedWidget { border: none; background-color: #f0f2f5; }\n"
                "QDialog#GlobalSettingsDialog { background-color: #ffffff; }\n"
                "QScrollArea#SettingsPageScroll, QScrollArea#SettingsPageScroll > QWidget { border: none; background-color: transparent; }\n"
                "QWidget#SettingsPageSurface { background-color: #ffffff; border-radius: 12px; background-clip: padding; }\n"
            "QScrollBar:vertical { background: #f0f2f5; width: 8px; margin: 0; border-radius: 4px; }\n"
            "QScrollBar::handle:vertical { background: %%scroll_handle%%; min-height: 30px; border-radius: 4px; }\n"
            "QScrollBar::handle:vertical:hover { background: %%scroll_handle_hover%%; }\n"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: transparent; }\n"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }\n"
            "QScrollBar:horizontal { background: #f0f2f5; height: 8px; margin: 0; border-radius: 4px; }\n"
            "QScrollBar::handle:horizontal { background: %%scroll_handle%%; min-width: 30px; border-radius: 4px; }\n"
            "QScrollBar::handle:horizontal:hover { background: %%scroll_handle_hover%%; }\n"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; background: transparent; }\n"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }\n"
            "\n"
            "/* 文本编辑框 */\n"
            "QTextEdit, QPlainTextEdit { border: 1px solid #d8dee4; border-radius: 8px;"
            " background-color: #ffffff; padding: 8px;"
            " font-family: \"Cascadia Code\", \"Consolas\", \"Microsoft YaHei UI\", monospace;"
            " font-size: 12px; }\n"
            "QPushButton#OperationInfoButton { background: transparent; color: #656d76; border: 1px solid #d8dee4;"
            " border-radius: 13px; padding: 0; min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; }\n"
            "QPushButton#OperationInfoButton:hover { border-color: %%visible_accent%%; color: %%visible_accent%%; background: #e6e8ec; }\n"
            "QPushButton#OperationInfoButton:pressed { background: #d8dee4; }\n"
            "QPushButton#CancelOperationButton { background: #ffffff; color: #656d76; border: 1px solid #d8dee4;"
            " border-radius: 7px; padding: 5px 12px; min-height: 26px; }\n"
            "QPushButton#CancelOperationButton:hover:enabled { color: #b42318; border-color: #f1aeb5; background: #fff5f5; }\n"
            "QPushButton#CancelOperationButton:pressed:enabled { background: #ffe3e3; }\n"
            "/* 灰度提示 */\n"
            "*[muted=\"true\"] { color: #6b7280; }\n"
        )
        stylesheet = (
            _TPL.replace("%%tc%%", tc_for_buttons if dark else tc)
            .replace("%%hover_c%%", hover_c)
            .replace("%%pressed_c%%", pressed_c)
            .replace("%%btn_top%%", btn_top)
            .replace("%%btn_hover_top%%", btn_hover_top)
            .replace("%%btn_border%%", btn_border)
            .replace("%%btn_text%%", btn_text)
            .replace("%%visible_accent%%", visible_accent)
            .replace("%%scroll_handle%%", scroll_handle)
            .replace("%%scroll_handle_hover%%", scroll_handle_hover)
            .replace("%%progress_chunk%%", progress_chunk)
            .replace("%%accent_text%%", accent_text)
            .replace("%%font_family%%", self._stylesheet_font_family())
        )
        stylesheet += self._extra_theme_qss(dark)
        stylesheet = (stylesheet
            .replace("%%tc%%", tc_for_buttons if dark else tc)
            .replace("%%hover_c%%", hover_c)
            .replace("%%pressed_c%%", pressed_c)
            .replace("%%btn_border%%", btn_border)
            .replace("%%btn_text%%", btn_text)
            .replace("%%visible_accent%%", visible_accent)
            .replace("%%accent_text%%", accent_text))
        self._theme_stylesheet = stylesheet
        if app is not None:
            app.setStyleSheet(stylesheet)
        # 精灵图按钮背景必须和当前页面背景一致，避免透明 PNG 边缘露出主题色。
        # 使用 _central_container_bg() 而非 _theme_role_colors()["bg_main"]，因为
        # 精灵图按钮直接挂在 CentralContainer 上，role_colors 的 bg_main 与实际
        # QSS 中 #CentralContainer 的背景色不完全一致，会在浅色/深色模式下都留下
        # 一圈可见的色差边缘。
        if hasattr(self, "about_sprite_btn"):
            sprite_bg = self._central_container_bg()
            self.about_sprite_btn.setStyleSheet(
                f"background-color: {sprite_bg}; border: 1px solid {sprite_bg}; border-radius: 8px;")
        self._refresh_styled_widgets()
        if hasattr(self, "_apply_button_sizes"):
            self._apply_button_sizes()
        if hasattr(self, "_refresh_color_buttons"):
            self._refresh_color_buttons()
        if hasattr(self, "_refresh_header_language_buttons"):
            self._refresh_header_language_buttons()
        # 实时换色时只刷新全局设置页表面色，避免局部 QSS 级联把按钮文字/背景冲掉。
        self._refresh_settings_dialog_surfaces()
        # 导航栏须在全局 stylesheet 落地后刷新，确保 #SettingsNav 的高优先级生效
        if hasattr(self, "_refresh_settings_nav_style"):
            self._refresh_settings_nav_style()
        if hasattr(self, "_apply_log_viewer_theme"):
            self._apply_log_viewer_theme()
            try:
                self._refresh_log_viewer()
            except Exception:
                pass

    def _refresh_styled_widgets(self):
        app = QApplication.instance()
        if app is None:
            return
        # 刷新关于标签页动态超链接颜色（暗色/亮色切换后 HTML 内联色需重建）
        if hasattr(self, "_about_links_label") and hasattr(self, "_about_links_html_fn"):
            try:
                _fg  = self._theme_role_colors()["fg_primary"]
                _lnk = "#8ab4f8" if self._theme_is_dark() else "#0969da"
                self._about_links_label.setText(self._about_links_html_fn(_fg, _lnk))
            except Exception:
                pass
        # 同步刷新“关于”对话框中的超链接颜色，避免主题切换后文字与背景同色
        if hasattr(self, "_about_dialog_links_label") and hasattr(self, "_about_dialog_links_html_fn"):
            try:
                _fg  = self._theme_role_colors()["fg_primary"]
                _lnk = "#8ab4f8" if self._theme_is_dark() else "#0969da"
                self._about_dialog_links_label.setText(self._about_dialog_links_html_fn(_fg, _lnk))
            except Exception:
                pass
        dark = bool(core.config.get("dark_mode", False))
        try:
            pal = app.palette()
            qcolor = QColor(self._theme_color)
            brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000 if qcolor.isValid() else 255
            highlight = QColor("#8c959f") if brightness >= 230 else qcolor
            pal.setColor(QPalette.ColorRole.Highlight, highlight)
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            if dark:
                # 设置暗色模式基础调色板
                pal.setColor(QPalette.ColorRole.Window, QColor("#252536"))
                pal.setColor(QPalette.ColorRole.WindowText, QColor("#e0e0e0"))
                pal.setColor(QPalette.ColorRole.Base, QColor("#2d2d3f"))
                pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#252536"))
                pal.setColor(QPalette.ColorRole.Text, QColor("#e0e0e0"))
                pal.setColor(QPalette.ColorRole.Button, QColor("#2d2d3f"))
                pal.setColor(QPalette.ColorRole.ButtonText, QColor("#e0e0e0"))
                pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#a7a7ba"))
                pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2d2d3f"))
                pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#e6e6f0"))
                pal.setColor(QPalette.ColorRole.Link, QColor(self._theme_color))
                for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
                    pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#8b8ba3"))
                for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
                    pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#3d3d55"))
            app.setPalette(pal)
        except Exception:
            pass
        try:
            # Iterate over all widgets and fully refresh their style.  On Qt
            # platforms where the style sheet changes the widget's box model,
            # unpolish/polish alone is insufficient; a StyleChange event
            # triggers recomputation of padding and margins.
            for widget in app.allWidgets():
                if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    try:
                        widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
                    except Exception:
                        pass
                try:
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
                    event = QEvent(QEvent.Type.StyleChange)
                    QApplication.sendEvent(widget, event)
                    widget.update()
                    widget.updateGeometry()
                except Exception:
                    continue
        except Exception:
            pass

    def apply_native_window_effect(self):
        """默认 Qt 原生主题不额外套玻璃效果。"""
        return

    def _enable_touch_scrolling(self, widget, *, horizontal: bool = False):
        """为可滚动控件启用单指惯性滑动；只触及 viewport，避免影响按钮点击。

        v1.4.3: 将 DragStartDistance 从 0.008 (8mm) 提高到 0.012 (12mm)，
        减少短距离滑动被误判为点击的概率。同时安装事件过滤器在释放时
        检查移动距离和 QScroller 状态，防止触摸滑动误触壁纸切换。
        """
        try:
            target = widget.viewport() if hasattr(widget, "viewport") else widget
            target.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
            QScroller.grabGesture(target, QScroller.ScrollerGestureType.TouchGesture)
            scroller = QScroller.scroller(target)
            props = scroller.scrollerProperties()
            props.setScrollMetric(QScrollerProperties.ScrollMetric.OvershootDragResistanceFactor, 0.18)
            props.setScrollMetric(QScrollerProperties.ScrollMetric.OvershootScrollDistanceFactor, 0.10)
            props.setScrollMetric(QScrollerProperties.ScrollMetric.DecelerationFactor, 0.10)
            try:
                # v1.4.3: 12mm threshold — 平衡滚动灵敏度和误触防护
                props.setScrollMetric(QScrollerProperties.ScrollMetric.DragStartDistance, 0.012)
                props.setScrollMetric(QScrollerProperties.ScrollMetric.FrameRate, QScrollerProperties.FrameRates.Fps60)
            except Exception:
                pass
            if not horizontal:
                props.setScrollMetric(QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy, QScrollerProperties.OvershootPolicy.OvershootAlwaysOff)
            props.setScrollMetric(QScrollerProperties.ScrollMetric.VerticalOvershootPolicy, QScrollerProperties.OvershootPolicy.OvershootWhenScrollable)
            scroller.setScrollerProperties(props)
            # Install event filter to track press/release positions for scroll-vs-click discrimination
            if not hasattr(widget, "_touch_press_pos"):
                widget._touch_press_pos = None
                widget._touch_press_item = None
                widget.installEventFilter(_TouchScrollFilter(self, widget))
        except Exception:
            pass

    def _apply_button_sizes(self):
        for btn in self.findChildren(QPushButton):
            if btn is getattr(self, "about_sprite_btn", None):
                continue
            if btn.property("colorButton"):
                if btn.minimumHeight() < 40:
                    btn.setMinimumHeight(40)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                continue
            if btn.property("settingsAction"):
                if btn.minimumHeight() < 30:
                    btn.setMinimumHeight(30)
                btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                continue
            if btn.property("wideAction"):
                if btn.minimumHeight() < 38:
                    btn.setMinimumHeight(38)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                continue
            if btn.minimumHeight() < 30:
                btn.setMinimumHeight(30)
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def _settings_nav_stylesheet(self, color=None) -> str:
        color = color or getattr(self, "_theme_color", core.config.get("theme_color", DEFAULT_THEME_COLOR))
        qcolor = QColor(color)
        if not qcolor.isValid():
            color = DEFAULT_THEME_COLOR
            qcolor = QColor(color)
        dark = bool(core.config.get("dark_mode", False))
        brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000
        if dark:
            bg = "#1a1b2e"
            border = "#3d3e56"
            item_fg = "#c8c8d8"
            # A white/very-light accent previously produced white text on a
            # white selected item in dark mode. Use the same contrast-safe
            # accent fallback as the main application stylesheet.
            selected_bg = "#3a3a50" if brightness >= 230 else color
            selected_text = "#e8e8f0" if brightness >= 230 else "#ffffff"
            selected_border = "#8b8ba3" if brightness >= 230 else color
            hover_bg = "#2d2d3f"
        else:
            bg = "#ffffff"
            border = "#d0d7de"
            item_fg = "#57606a"
            selected_text = "#24292f" if brightness >= 170 else "#ffffff"
            selected_bg = "#f6f8fa" if brightness >= 230 else color
            selected_border = "#8c959f" if brightness >= 230 else color
            hover_bg = "#eaeef2"
        return (
            f"QListWidget#SettingsNav {{ background-color: {bg}; border: 1px solid {border};"
            f" border-radius: 8px; padding: 6px; outline: none; background-clip: padding; }}"
            f"QListWidget#SettingsNav::item {{ padding: 10px 14px; border-radius: 6px;"
            f" color: {item_fg}; font-size: 13px; }}"
            f"QListWidget#SettingsNav::item:selected {{ background-color: {selected_bg}; color: {selected_text}; border: 1px solid {selected_border}; font-weight: 500; }}"
            f"QListWidget#SettingsNav::item:hover:!selected {{ background-color: {hover_bg}; }}"
        )

    def _refresh_settings_nav_style(self, nav_list=None):
        nav_list = nav_list or getattr(self, "_settings_nav", None)
        if nav_list is None and hasattr(self, "_settings_dialog") and self._settings_dialog is not None:
            try:
                nav_list = self._settings_dialog.findChild(QListWidget, "SettingsNav")
            except Exception:
                nav_list = None
        if nav_list is None:
            return
        try:
            nav_list.setStyleSheet(self._settings_nav_stylesheet())
        except RuntimeError:
            pass

    def _build_ui(self):
        central = QWidget(self)
        # 设置 objectName 以便 QSS 中通过 #CentralContainer 选择器为中央 widget 单独
        # 应用背景色，避免使用 QWidget 全局选择器造成的子控件布局污染。
        central.setObjectName("CentralContainer")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)
        logo_path = self._img_path("txtlogo.png")
        if os.path.exists(logo_path):
            logo = QLabel()
            pix = QPixmap(logo_path)
            if not pix.isNull():
                logo.setPixmap(pix.scaledToHeight(56, Qt.SmoothTransformation))
                logo.setMaximumWidth(320)
                logo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                header.addWidget(logo)
            else:
                header.addWidget(QLabel(t("上一个桌面背景")))
        else:
            title = QLabel(t("上一个桌面背景"))
            title.setStyleSheet(self._text_style("primary", "font-size: 22px; font-weight: 700;"))
            header.addWidget(title)
        self.header_lang_switch = self._create_header_language_switch()
        header.addWidget(self.header_lang_switch, 0, Qt.AlignVCenter)
        self.status_label = QLabel(t("正在初始化界面…"))
        self.status_label.setObjectName("HeaderStatusLabel")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_label.setMinimumWidth(120)
        # No hard maximumWidth cap: let the label absorb the available header
        # space so long status text is shown in full instead of being elided
        # to "..." while the left side still has empty room.
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.status_label.setWordWrap(False)
        self.status_label.setStyleSheet(self._text_style("muted", "font-size: 12px;"))
        header.addWidget(self.status_label, 1)
        self.operation_expand_btn = QPushButton()
        self.operation_expand_btn.setObjectName("OperationInfoButton")
        # Bug 12 fix: 使用 _set_button_svg_icon 代替直接 QIcon(path)，
        # 确保 info.svg 在暗色模式切换时重新渲染（避免图标颜色不可见）。
        info_icon = self._img_path("info.svg")
        if os.path.exists(info_icon):
            self._set_button_svg_icon(self.operation_expand_btn, "info.svg", size=18)
        else:
            self.operation_expand_btn.setText("i")
        # Bug 12 fix: 与 QSS (min/max 24px) 对齐，避免 setFixedSize(26) 与 QSS 24px 冲突。
        self.operation_expand_btn.setFixedSize(24, 24)
        self.operation_expand_btn.setToolTip(t("当前操作详情"))
        self.operation_expand_btn.clicked.connect(self._toggle_operation_panel)
        # Add the circular info button directly to the header (no QFrame wrapper).
        # The old ExpandButtonContainer QFrame introduced a visible square border
        # around the circular button once the header stretch was removed.
        header.addWidget(self.operation_expand_btn, 0, Qt.AlignVCenter)
        layout.addLayout(header)

        self.operation_panel = QFrame()
        self.operation_panel.setVisible(False)
        op_layout = QHBoxLayout(self.operation_panel)
        op_layout.setContentsMargins(0, 0, 0, 0)
        op_layout.addStretch(1)
        self.cancel_operation_btn = QPushButton(t("请求终止"))
        self.cancel_operation_btn.setObjectName("CancelOperationButton")
        self.cancel_operation_btn.setEnabled(False)
        self.cancel_operation_btn.clicked.connect(self.request_cancel_current_operation)
        op_layout.addWidget(self.cancel_operation_btn)
        layout.addWidget(self.operation_panel)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        def _on_tab_switch(idx):
            self._animate_tab_page_switch(idx)
        self.tabs.currentChanged.connect(_on_tab_switch)
        self.wallpaper_tab_page = self._wallpaper_tab()
        self.tabs.addTab(self.wallpaper_tab_page, t("首页"))
        if is_feature_enabled("bing"):
            self.tabs.addTab(self._bing_tab(), t("必应壁纸"))
        self.tabs.addTab(self._about_tab(), t("关于 / 资源"))
        # 日志页面移动到“全局设置”->“日志”中，因此此处不再添加日志标签页
        # self.tabs.addTab(self._log_tab(), t("日志"))
        self._last_tab_index = self.tabs.currentIndex()

    def _platform_ui_policy(self):
        return get_platform_ui_policy(PLATFORM_ID)

    def _create_home_restart_button(self) -> QPushButton:
        policy = self._platform_ui_policy()
        if policy.restart_action == "admin":
            button = QPushButton(t("重启程序") if core.is_windows_admin() else t("管理员重启"))
            button.setToolTip(t("已是管理员权限时执行普通重启；否则请求管理员权限重启。"))
            button.clicked.connect(self.restart_as_admin)
        else:
            button = QPushButton(t("重启程序"))
            button.clicked.connect(self.restart_program)
        button.setProperty("secondary", True)
        self._set_button_svg_icon(button, "restart.svg", size=20)
        return button

    def _add_home_platform_action_panel(self, right: QVBoxLayout) -> None:
        policy = self._platform_ui_policy()
        if not policy.show_desktop_context_menu and not is_feature_enabled("hotkeys"):
            return
        if policy.show_desktop_context_menu:
            panel = QGroupBox(t("右键菜单"))
            panel.setMinimumWidth(360)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(10, 18, 10, 10)
            layout.setSpacing(6)
        else:
            panel = QGroupBox(t("全局热键"))
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(10, 18, 10, 10)
            layout.setSpacing(6)

        self.ctx_prev = QCheckBox()
        self.ctx_next = QCheckBox()
        self.ctx_random = QCheckBox()
        self.ctx_jump = QCheckBox()
        self.ctx_prev.toggled.connect(lambda value: self._update_ctx("ctx_last_wallpaper", value))
        self.ctx_next.toggled.connect(lambda value: self._update_ctx("ctx_next_wallpaper", value))
        self.ctx_random.toggled.connect(lambda value: self._update_ctx("ctx_random_wallpaper", value))
        self.ctx_jump.toggled.connect(lambda value: self._update_ctx("ctx_jump_to_wallpaper", value))
        for checkbox in (self.ctx_prev, self.ctx_next, self.ctx_random, self.ctx_jump):
            checkbox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            layout.addWidget(checkbox)
        self._refresh_context_shortcut_labels()

        if policy.show_desktop_context_menu:
            sync_button = QPushButton(t("同步右键菜单"))
            sync_button.setProperty("secondary", True)
            sync_button.clicked.connect(self.register_context_with_prompt)
            layout.addWidget(sync_button)
        right.addWidget(panel)

    def _add_global_hotkey_settings_page(self, add_settings_page) -> None:
        policy = self._platform_ui_policy()
        shortcut_page = QWidget()
        shortcut_layout = QVBoxLayout(shortcut_page)
        shortcut_layout.setContentsMargins(0, 0, 0, 0)
        shortcut_layout.setSpacing(14)

        shortcut_box = QGroupBox(t("全局热键设置") if policy.show_hotkey_focus_guard else t("全局热键"))
        shortcut_form = QFormLayout(shortcut_box)
        shortcut_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        shortcut_form.setHorizontalSpacing(14)
        shortcut_form.setVerticalSpacing(12)
        self.ctx_shortcut_edits = {}
        self.ctx_shortcut_buttons = {}
        self.ctx_shortcut_current_labels = {}

        for action, label, _default_key, _cfg_key, _widget_name in self._context_action_defs():
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            record_button = QPushButton(t("录制"))
            record_button.setProperty("secondary", True)
            record_button.setMinimumWidth(80)
            record_button.clicked.connect(
                lambda checked=False, action=action: self.record_context_hotkey(action)
            )
            self.ctx_shortcut_buttons[action] = record_button
            row_layout.addWidget(record_button)
            clear_button = QPushButton(t("清除"))
            clear_button.setProperty("secondary", True)
            clear_button.setMaximumWidth(64)
            clear_button.clicked.connect(
                lambda checked=False, action=action: self.on_context_hotkey_clear(action)
            )
            row_layout.addWidget(clear_button)
            current = QLabel(self._context_hotkey_display(action))
            current.setProperty("muted", True)
            current.setWordWrap(True)
            self.ctx_shortcut_current_labels[action] = current
            row_layout.addWidget(current, 1)
            shortcut_form.addRow(label, row)
        shortcut_layout.addWidget(shortcut_box)

        global_box = QGroupBox(t("全局热键"))
        global_layout = QVBoxLayout(global_box)
        global_layout.setContentsMargins(10, 18, 10, 10)
        global_layout.setSpacing(8)
        self.global_hotkeys_enabled_check = QCheckBox(t("启用全局热键（默认关闭）"))
        self.global_hotkeys_enabled_check.setChecked(bool(core.config.get("global_hotkeys_enabled", False)))
        self.global_hotkeys_enabled_check.toggled.connect(self.on_global_hotkeys_enabled_changed)
        global_layout.addWidget(self.global_hotkeys_enabled_check)

        if policy.show_hotkey_focus_guard:
            self.hotkey_focus_guard_check = QCheckBox(
                t("启用聚焦位置检测，避免编辑文本、浏览文件或操作菜单时误触")
            )
            self.hotkey_focus_guard_check.setChecked(bool(core.config.get("hotkey_focus_guard", True)))
            self.hotkey_focus_guard_check.toggled.connect(self.on_hotkey_focus_guard_changed)
            global_layout.addWidget(self.hotkey_focus_guard_check)
        shortcut_layout.addWidget(global_box)

        self.app_sc_edits = {}
        self.app_sc_enabled_check = None
        shortcut_layout.addStretch(1)
        add_settings_page(t("全局热键"), shortcut_page)

    def _wallpaper_tab(self):
        page = QWidget()
        page.setObjectName("MainTabSurface")
        page.setAutoFillBackground(False)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Prevent horizontal scrollbars by always hiding them.  Without this,
        # long English strings or wide minimum sizes can push the page beyond
        # the visible area and produce an unwanted horizontal scrollbar.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        self._enable_touch_scrolling(scroll)
        # Fix 2.5: 安装事件过滤器，使 viewport 不再吞掉 PgUp/PgDown，
        # 让应用内"上一张/下一张"快捷键在鼠标悬停于滚动区域时也能触发。
        try:
            scroll.viewport().installEventFilter(self)
        except Exception:
            pass
        outer.addWidget(scroll)

        body = QWidget()
        body.setMinimumWidth(0)
        scroll.setWidget(body)
        root = QHBoxLayout(body)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Use real column widgets instead of adding bare layouts directly.
        # This lets the two columns shrink predictably when the scroll-area
        # viewport is narrow and prevents the right preview card from being
        # clipped by the vertical scrollbar.  On wide/fullscreen layouts the
        # right column is capped, so the left side remains the primary area.
        left_panel = QWidget()
        right_panel = QWidget()
        left_panel.setMinimumWidth(0)
        right_panel.setMinimumWidth(0)
        left_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # Bug 1 fix: 给两列都设上限，防止超宽窗口下某一列过度拉伸。
        left_panel.setMaximumWidth(715)
        right_panel.setMinimumWidth(365)
        right_panel.setMaximumWidth(565)

        left = QVBoxLayout(left_panel)
        right = QVBoxLayout(right_panel)
        left.setContentsMargins(0, 0, 0, 0)
        right.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        right.setSpacing(12)
        # Bug 1 fix: 3:2 比例代替旧的 5:3，让右列获得更多空间。
        root.addWidget(left_panel, 3)
        root.addWidget(right_panel, 2)
        self._home_left_panel = left_panel
        self._home_right_panel = right_panel

        mode_box = QGroupBox(t("壁纸模式"))
        form = QFormLayout(mode_box)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # Bug 1 fix: 缩小水平间距 (12→8) 让表单更紧凑，并启用 ExpandingFieldsGrow
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.mode_combo = ShangComboBox()
        describe_control(
            self.mode_combo,
            name=t("当前壁纸模式"),
            description=t("选择要配置和运行的壁纸类型。切换模式只改变相关控件的可用状态。"),
            object_name="WallpaperModeCombo",
        )
        for mode_key in MODE_KEYS:
            # HTML 模式在 UI 上直接显示 "HTML" 字样，而不是 "网页"。
            # 用户在模式选择下拉框中能看到具体的 HTML 选项，避免误以为该模式缺失。
            label_key = "HTML" if mode_key == "HTML" else mode_key
            self.mode_combo.addItem(t(label_key), mode_key)
        self._prepare_combo_popup(self.mode_combo)
        # Expand the maximum width of the mode selector to accommodate longer
        # translations and prevent truncation.
        self._constrain_combo_width(self.mode_combo, min_width=150, max_width=300)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        form.addRow(t("当前模式"), self.mode_combo)
        self.fit_combo = ShangComboBox()
        describe_control(
            self.fit_combo,
            name=t("壁纸适应方式"),
            description=t("控制静态图片在桌面上的缩放、居中或平铺方式。"),
            object_name="WallpaperFitCombo",
        )
        for style_key in STYLE_KEYS:
            self.fit_combo.addItem(t(style_key), style_key)
        self._prepare_combo_popup(self.fit_combo)
        self._constrain_combo_width(self.fit_combo, min_width=150, max_width=236)
        self.fit_combo.currentIndexChanged.connect(self.on_fit_changed)
        form.addRow(t("适应方式"), self.fit_combo)
        left.addWidget(mode_box)

        slide_box = QGroupBox(t("幻灯片放映"))
        slide_layout = QGridLayout(slide_box)
        slide_layout.setHorizontalSpacing(10)
        slide_layout.setVerticalSpacing(10)
        self.folder_edit = configure_text_input(
            QLineEdit(),
            name=t("幻灯片壁纸文件夹"),
            description=t("可直接输入现有图片文件夹；离开输入框时保存，应用并播放时再次校验。"),
            object_name="SlideshowFolderEdit",
            placeholder=t("首次使用请先选择壁纸文件夹"),
        )
        self._slide_folder_source = self._source_inputs.bind_existing_directory(
            self.folder_edit,
            key="slide_folder",
            label=t("幻灯片文件夹"),
            saved_text=t("幻灯片文件夹已保存"),
            cleared_text=t("已清除幻灯片文件夹"),
        )
        self.btn_browse_folder = QPushButton(t("选择文件夹"))
        describe_control(
            self.btn_browse_folder,
            name=t("选择幻灯片文件夹"),
            description=t("选择文件夹后立即保存并开始幻灯片放映。"),
            object_name="SlideshowBrowseButton",
        )
        self.btn_browse_folder.setProperty("secondary", True)
        btn_browse_folder = self.btn_browse_folder
        btn_browse_folder.clicked.connect(self.choose_folder)
        slide_layout.addWidget(make_buddy_label(t("文件夹"), self.folder_edit), 0, 0)
        slide_layout.addWidget(self.folder_edit, 0, 1, 1, 2)
        slide_layout.addWidget(btn_browse_folder, 0, 3)
        self.seconds_spin = QSpinBox()
        describe_control(
            self.seconds_spin,
            name=t("幻灯片切换间隔"),
            description=t("每张壁纸保持显示的秒数，范围为 5 秒到 24 小时。"),
            object_name="SlideshowIntervalSpin",
        )
        self.seconds_spin.setRange(5, 24 * 3600)
        self.seconds_spin.setSuffix(t(" 秒"))
        self.seconds_spin.valueChanged.connect(self.on_seconds_changed)
        slide_layout.addWidget(make_buddy_label(t("间隔"), self.seconds_spin), 1, 0)
        slide_layout.addWidget(self.seconds_spin, 1, 1)
        self.shuffle_check = QCheckBox(t("随机顺序"))
        self.shuffle_check.toggled.connect(self.on_shuffle_changed)
        slide_layout.addWidget(self.shuffle_check, 1, 2, 1, 2)

        nav_row = QGridLayout()
        nav_row.setHorizontalSpacing(8)
        nav_row.setVerticalSpacing(8)
        self.btn_prev = btn_prev = QPushButton(t("上一张"))
        self.btn_next = btn_next = QPushButton(t("下一张"))
        self.btn_random = btn_random = QPushButton(t("随机"))
        self.btn_random_prob = btn_random_prob = QPushButton(t("随机概率（百分比）"))
        btn_random_prob.setToolTip(t("打开百分比编辑器，为每张壁纸分配 0% 到 100% 的随机概率"))
        # Bug 21 fix: 该按钮文字较长（中文8字/英文7字），需要足够宽度避免被截断。
        btn_random_prob.setMinimumWidth(155)
        self.btn_start = btn_start = QPushButton(t("应用并播放"))
        self.btn_stop = btn_stop = QPushButton(t("暂停"))
        for btn in (btn_prev, btn_next, btn_random, btn_random_prob, btn_start, btn_stop):
            btn.setMinimumHeight(38)
        nav_row.addWidget(btn_prev, 0, 0)
        nav_row.addWidget(btn_next, 0, 1)
        nav_row.addWidget(btn_random, 0, 2)
        nav_row.addWidget(btn_random_prob, 0, 3)
        nav_row.addWidget(btn_start, 1, 0, 1, 2)
        nav_row.addWidget(btn_stop, 1, 2, 1, 2)
        btn_prev.clicked.connect(lambda: self.run_core(core.previous_wallpaper))
        btn_next.clicked.connect(lambda: self.run_core(core.next_wallpaper))
        btn_random.clicked.connect(lambda: self.run_core(core.random_wallpaper))
        btn_random_prob.clicked.connect(self.open_random_probability_settings)
        btn_start.clicked.connect(self.start_slideshow_from_gui)
        btn_stop.clicked.connect(lambda: self.run_core(core.stop_slideshow))
        slide_layout.addLayout(nav_row, 2, 0, 1, 4)
        self.slide_box = slide_box
        left.addWidget(slide_box)

        single_box = QGroupBox(t("单张图片"))
        single_layout = QHBoxLayout(single_box)
        single_layout.setSpacing(10)
        self.single_edit = configure_text_input(
            QLineEdit(),
            name=t("单张图片路径"),
            description=t("显示最近一次通过“选择并设置”应用的图片；图片选择由右侧按钮负责。"),
            object_name="SingleImageEdit",
            placeholder=t("选择一张图片作为桌面背景"),
            clear_button=False,
            read_only=True,
        )
        self.btn_single = QPushButton(t("选择并设置"))
        describe_control(
            self.btn_single,
            name=t("选择并设置单张图片"),
            description=t("打开图片选择器，选择成功后立即设置为桌面背景。"),
            object_name="SingleImageChooseButton",
        )
        self.btn_single.setProperty("secondary", True)
        btn_single = self.btn_single
        # Bug 21 fix: 设置最小宽度避免文字被截断。
        btn_single.setMinimumWidth(115)
        btn_single.clicked.connect(self.choose_single_image)
        single_layout.addWidget(self.single_edit, 1)
        single_layout.addWidget(btn_single)
        self.single_box = single_box
        left.addWidget(single_box)

        video_box = QGroupBox(t("视频壁纸"))
        video_layout = QGridLayout(video_box)
        video_layout.setHorizontalSpacing(10)
        video_layout.setVerticalSpacing(8)
        self.video_edit = configure_text_input(
            QLineEdit(),
            name=t("视频壁纸路径"),
            description=t("可直接输入现有本地视频文件；离开输入框时保存，播放时再次校验。"),
            object_name="VideoWallpaperEdit",
            placeholder=t("选择 mp4/mov/mkv/webm 视频作为桌面背景"),
        )
        self._video_source = self._source_inputs.bind_existing_file(
            self.video_edit,
            key="video_file",
            label=t("视频文件"),
            saved_text=t("视频来源已保存"),
            cleared_text=t("已清除视频来源"),
            on_changed=lambda _old, _new: self._refresh_video_volume_controls(),
        )
        self.video_browse_btn = QPushButton(t("选择视频"))
        self.video_browse_btn.setProperty("secondary", True)
        self.video_start_btn = QPushButton(t("播放视频"))
        self.video_stop_btn = QPushButton(t("停止视频"))
        self.video_muted_check = QCheckBox(t("静音播放"))
        self.video_browse_btn.clicked.connect(self.choose_video_file)
        self.video_start_btn.clicked.connect(self.start_video_wallpaper_from_gui)
        self.video_stop_btn.clicked.connect(lambda: self.run_core(core.stop_video_wallpaper))
        self.video_muted_check.toggled.connect(self.on_video_muted_changed)
        for _btn in (self.video_browse_btn, self.video_start_btn, self.video_stop_btn):
            _btn.setMinimumHeight(34)
        # ---- Dedicated volume control (sits next to 静音播放) ----
        # When the muted checkbox is checked, the slider is greyed out and
        # the saved volume is preserved so un-muting restores the previous
        # level immediately.  Mirrors the canonical DPI slider pattern at
        # `self.dpi_scale_slider` so styling and behaviour stay consistent.
        # 引导：滑块在“未选视频文件 / 静音”时灰显，避免用户误以为功能损坏。
        self.video_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.video_volume_slider.setRange(0, 100)
        self.video_volume_slider.setSingleStep(1)
        self.video_volume_slider.setPageStep(10)
        self.video_volume_slider.setValue(int(core.config.get("video_volume", 100)))
        self.video_volume_slider.setToolTip(
            t("视频音量") + "\n" + t("仅在已选择视频文件且未静音时可调整；调整时无需重启播放。")
        )
        self.video_volume_slider.valueChanged.connect(self.on_video_volume_changed)
        self.video_volume_value_label = QLabel(f"{self.video_volume_slider.value()}%")
        self.video_volume_value_label.setMinimumWidth(48)
        self.video_volume_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.video_volume_value_label.setToolTip(t("视频音量"))
        video_volume_row = QWidget()
        vvol_layout = QHBoxLayout(video_volume_row)
        vvol_layout.setContentsMargins(0, 0, 0, 0)
        vvol_layout.setSpacing(8)
        vvol_layout.addWidget(self.video_volume_slider, 1)
        vvol_layout.addWidget(self.video_volume_value_label)
        # 初始启用状态：仅在已选视频文件 + 未静音时可拖动
        _has_video_file = bool(core.config.get("video_file", ""))
        self.video_volume_slider.setEnabled(_has_video_file and not self.video_muted_check.isChecked())
        video_layout.addWidget(make_buddy_label(t("视频文件"), self.video_edit), 0, 0)
        video_layout.addWidget(self.video_edit, 0, 1, 1, 2)
        video_layout.addWidget(self.video_browse_btn, 0, 3)
        video_layout.addWidget(self.video_muted_check, 1, 1)
        video_layout.addWidget(self.video_start_btn, 1, 2)
        video_layout.addWidget(self.video_stop_btn, 1, 3)
        self.video_focus_behavior_combo = ShangComboBox()
        self.video_focus_behavior_combo.addItem(t("保持播放"), "none")
        self.video_focus_behavior_combo.addItem(t("桌面失焦时暂停"), "pause")
        self.video_focus_behavior_combo.addItem(t("桌面失焦时降低音量"), "duck")
        self._set_combo_current_data(
            self.video_focus_behavior_combo,
            core.config.get("video_focus_behavior", "none"),
            default_index=0,
        )
        self._constrain_combo_width(self.video_focus_behavior_combo, min_width=170, max_width=260)
        self._prepare_combo_popup(self.video_focus_behavior_combo)
        self.video_focus_behavior_combo.currentIndexChanged.connect(self.on_video_focus_behavior_changed)
        self.video_focus_behavior_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.video_focus_behavior_combo.setToolTip(
            t("当焦点不在桌面时，视频壁纸可以继续播放、暂停，或临时降低音量；回到桌面后自动恢复。")
        )
        video_layout.addWidget(make_buddy_label(t("音量"), self.video_volume_slider), 2, 0)
        video_layout.addWidget(video_volume_row, 2, 1, 1, 3)
        video_layout.addWidget(make_buddy_label(t("失焦处理"), self.video_focus_behavior_combo), 3, 0)
        video_layout.addWidget(self.video_focus_behavior_combo, 3, 1, 1, 3)
        self.video_box = video_box
        video_box.setVisible(is_feature_enabled("video"))
        left.addWidget(video_box)

        # ---- HTML 交互式壁纸 ----
        html_box = self._build_html_wallpaper_box()
        html_box.setVisible(is_feature_enabled("html"))
        left.addWidget(html_box)

        color_box = QGroupBox(t("纯色 / 渐变"))
        color_layout = QGridLayout(color_box)
        color_layout.setHorizontalSpacing(10)
        color_layout.setVerticalSpacing(10)
        color_layout.setColumnStretch(0, 0)
        color_layout.setColumnStretch(1, 1)
        color_layout.setColumnStretch(2, 1)
        self.solid_btn = QPushButton(t("选择纯色"))
        self.solid_btn.setProperty("colorButton", True)
        self.grad1_btn = QPushButton(t("渐变颜色 1"))
        self.grad1_btn.setProperty("colorButton", True)
        self.grad2_btn = QPushButton(t("渐变颜色 2"))
        self.grad2_btn.setProperty("colorButton", True)
        for _btn in (self.solid_btn, self.grad1_btn, self.grad2_btn):
            _btn.setMinimumHeight(40)
            _btn.setMinimumWidth(150)
            _btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.angle_spin = QSpinBox()
        self.angle_spin.setRange(0, 360)
        self.angle_spin.setSuffix("°")
        self.solid_btn.clicked.connect(self.choose_solid_color)
        self.grad1_btn.clicked.connect(lambda: self.choose_gradient_color(1))
        self.grad2_btn.clicked.connect(lambda: self.choose_gradient_color(2))
        self.angle_apply_btn = QPushButton(t("应用渐变"))
        self.angle_apply_btn.clicked.connect(self.on_gradient_apply)
        self.angle_spin.valueChanged.connect(self.on_gradient_changed)
        color_layout.addWidget(QLabel(t("纯色")), 0, 0)
        color_layout.addWidget(self.solid_btn, 0, 1, 1, 2)
        color_layout.addWidget(QLabel(t("渐变颜色")), 1, 0)
        color_layout.addWidget(self.grad1_btn, 1, 1)
        color_layout.addWidget(self.grad2_btn, 1, 2)
        color_layout.addWidget(QLabel(t("渐变角度")), 2, 0)
        color_layout.addWidget(self.angle_spin, 2, 1)
        color_layout.addWidget(self.angle_apply_btn, 2, 2)
        self.color_box = color_box
        left.addWidget(color_box)

        action_box = QGroupBox(t("快捷操作"))
        action_box.setMinimumWidth(360)
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
        btn_refresh = QPushButton(t("刷新预览"))
        btn_refresh.setProperty("secondary", True)
        # Bug 11/20 fix: 使用 12px 图标（原 16px→14px→12px），避免在最小窗口宽度下
        # 图标挤占文字空间导致按钮内容超出/被截断（英文 "Open wallpaper" 在 1020px 窗口下溢出）。
        # 同时设置较小的 iconSize 让 Qt 在按钮变窄时优先压缩图标而非截断文字。
        self._set_button_svg_icon(btn_refresh, "refresh.svg", size=20)
        btn_refresh.clicked.connect(self.update_preview)
        btn_open_folder = QPushButton(t("打开当前文件夹"))
        btn_open_folder.setProperty("secondary", True)
        self._set_button_svg_icon(btn_open_folder, "folder.svg", size=20)
        btn_open_folder.clicked.connect(self.open_current_folder)
        btn_sidebar = QPushButton(t("跳转到壁纸"))
        btn_sidebar.setProperty("secondary", True)
        self._set_button_svg_icon(btn_sidebar, "image.svg", size=20)
        btn_sidebar.clicked.connect(self.open_wallpaper_sidebar)
        self.settings_icon_btn = QPushButton(t("全局设置"))
        self.settings_icon_btn.setToolTip(t("打开全局设置窗口"))
        self._set_button_svg_icon(self.settings_icon_btn, "settings.svg", size=20)
        self.settings_icon_btn.clicked.connect(self.open_global_settings_from_home)
        btn_exit_home = QPushButton(t("退出程序"))
        btn_exit_home.setProperty("secondary", True)
        self._set_button_svg_icon(btn_exit_home, "power.svg", size=20)
        btn_exit_home.clicked.connect(self.exit_app)
        for btn in (btn_refresh, btn_open_folder, btn_sidebar, self.settings_icon_btn, btn_exit_home):
            btn.setMinimumHeight(40)
        quick_grid.addWidget(btn_refresh, 0, 0)
        quick_grid.addWidget(btn_open_folder, 0, 1)
        quick_grid.addWidget(btn_sidebar, 1, 0)
        quick_grid.addWidget(self.settings_icon_btn, 1, 1)
        quick_grid.addWidget(btn_exit_home, 2, 0, 1, 2)
        action_tabs.addTab(quick_page, t("常用"))

        maint_page = QWidget()
        mh = QGridLayout(maint_page)
        mh.setContentsMargins(4, 6, 4, 4)
        mh.setHorizontalSpacing(10)
        mh.setVerticalSpacing(10)
        btn_save = QPushButton(t("保存配置"))
        btn_save.setProperty("secondary", True)
        self._set_button_svg_icon(btn_save, "save.svg", size=20)
        btn_save.clicked.connect(lambda: self.run_core(core.save_config))
        btn_restart_home = self._create_home_restart_button()
        btn_restore_home = QPushButton(t("恢复启动前壁纸"))
        btn_restore_home.setProperty("secondary", True)
        self._set_button_svg_icon(btn_restore_home, "undo.svg", size=20)
        btn_restore_home.clicked.connect(lambda: self.run_core(core.restore_session_original_wallpaper))
        for btn in (btn_save, btn_restart_home, btn_restore_home):
            btn.setMinimumHeight(40)
        mh.addWidget(btn_save, 0, 0)
        mh.addWidget(btn_restart_home, 0, 1)
        mh.addWidget(btn_restore_home, 1, 0, 1, 2)
        action_tabs.addTab(maint_page, t("维护"))
        # Defer adding the quick actions box until after the preview box so it appears
        # between the preview and any additional panels.  The call to add `action_box`
        # happens later.
        left.addStretch(1)

        preview_box = QGroupBox(t("当前壁纸"))
        # Bug 1 fix: 放宽 preview_box 最小宽度并改为 Expanding/Expanding
        preview_box.setMinimumWidth(280)
        preview_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        pv_layout = QVBoxLayout(preview_box)
        pv_layout.setContentsMargins(12, 24, 12, 12)
        pv_layout.setSpacing(10)
        self.preview_canvas = PreviewCanvas()
        # Bug 1 fix: 预览画布用 stretch=1 让它优先填满垂直空间。
        pv_layout.addWidget(self.preview_canvas, 1)

        self.current_label = QLineEdit("")
        self.current_label.setReadOnly(True)
        self.current_label.setPlaceholderText(t("未检测到当前壁纸"))
        self.current_label.setMinimumHeight(34)
        self.current_label.setToolTip(t("当前壁纸路径，可选中文本复制"))
        # Bug 1 fix: 跳转按钮独立一行，让路径输入框获得全部宽度。
        current_path_row = QVBoxLayout()
        current_path_row.setContentsMargins(0, 0, 0, 0)
        current_path_row.setSpacing(6)
        current_path_row.addWidget(self.current_label)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        self.btn_jump_current_wallpaper = QPushButton(t("跳转到壁纸"))
        self.btn_jump_current_wallpaper.setProperty("secondary", True)
        self.btn_jump_current_wallpaper.setMinimumHeight(32)
        # Bug 21 fix: 增加最大宽度从 140 到 160，避免中文“跳转到壁纸”被截断。
        self.btn_jump_current_wallpaper.setMaximumWidth(160)
        self.btn_jump_current_wallpaper.setMinimumWidth(105)
        self.btn_jump_current_wallpaper.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_jump_current_wallpaper.clicked.connect(self.open_wallpaper_sidebar)
        btn_row.addWidget(self.btn_jump_current_wallpaper)
        current_path_row.addLayout(btn_row)
        pv_layout.addLayout(current_path_row)

        hist_row = QHBoxLayout()
        hist_row.setSpacing(8)
        hist_title = QLabel(t("最近使用的壁纸"))
        hist_title.setProperty("muted", True)
        hist_title.setStyleSheet("font-size: 12px;")
        # Enable word wrapping on this long label to prevent horizontal expansion
        hist_title.setWordWrap(True)
        hist_row.addWidget(hist_title)
        hist_row.addStretch(1)
        # v1.4.7: 收藏当前壁纸按钮
        self.btn_favorite_current = QPushButton(t("收藏当前"))
        self.btn_favorite_current.setProperty("secondary", True)
        # Bug 21 fix: 增加最大宽度从 100 到 120，避免中文被截断。
        self.btn_favorite_current.setMaximumWidth(120)
        self.btn_favorite_current.setMinimumWidth(90)
        self.btn_favorite_current.setToolTip(t("把当前壁纸加入收藏夹（收藏夹不会随历史滚动消失）"))
        self.btn_favorite_current.clicked.connect(self._toggle_favorite_current)
        hist_row.addWidget(self.btn_favorite_current)
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
        # v1.4.7: 收藏夹列表 (用户主动收藏的壁纸, 不随历史滚动消失)
        fav_row = QHBoxLayout()
        fav_row.setSpacing(8)
        fav_title = QLabel(t("收藏夹"))
        fav_title.setProperty("muted", True)
        fav_title.setStyleSheet("font-size: 12px;")
        fav_title.setWordWrap(True)
        fav_row.addWidget(fav_title)
        fav_row.addStretch(1)
        self.btn_clear_favorites = QPushButton(t("清空收藏"))
        self.btn_clear_favorites.setProperty("secondary", True)
        # Bug 21 fix: 增加最大宽度从 80 到 100，避免中文被截断。
        self.btn_clear_favorites.setMaximumWidth(100)
        self.btn_clear_favorites.setMinimumWidth(90)
        self.btn_clear_favorites.setToolTip(t("清空全部收藏（不会删除壁纸文件）"))
        self.btn_clear_favorites.clicked.connect(self._clear_all_favorites)
        fav_row.addWidget(self.btn_clear_favorites)
        pv_layout.addLayout(fav_row)
        self.favorites_list = QListWidget()
        self.favorites_list.setObjectName("FavoritesThumbs")
        self.favorites_list.setViewMode(QListView.ViewMode.IconMode)
        self.favorites_list.setFlow(QListView.Flow.LeftToRight)
        self.favorites_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.favorites_list.setMovement(QListView.Movement.Static)
        self.favorites_list.setWrapping(False)
        self.favorites_list.setSpacing(8)
        self.favorites_list.setIconSize(QSize(112, 70))
        self.favorites_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.favorites_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.favorites_list.setFixedHeight(106)
        self.favorites_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.favorites_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.favorites_list.itemClicked.connect(self._apply_favorite_item)
        self.favorites_list.itemDoubleClicked.connect(self.open_history_item_location_by_item)
        self.favorites_list.customContextMenuRequested.connect(self._show_favorite_context_menu)
        self._enable_touch_scrolling(self.favorites_list, horizontal=True)
        pv_layout.addWidget(self.favorites_list)

        right.addWidget(preview_box, 1)  # Bug 1 fix: stretch=1 让预览填满垂直空间
        # Insert quick actions after the preview to keep the layout compact
        right.addWidget(action_box)

        self._add_home_platform_action_panel(right)

        # Bug 1 fix: 删除 right.addStretch(1) —— 这个 stretch 会在 about_row 之前
        # 制造可见的空带。about_row 现在直接跟在 hotkey_box 之后，紧凑布局。
        about_row = QHBoxLayout()
        about_row.addStretch(1)
        about_box = QVBoxLayout()
        about_box.setAlignment(Qt.AlignCenter)
        self.about_sprite_btn = QPushButton()
        self.about_sprite_btn.setToolTip(t("悬停播放，点击打开关于"))
        self.about_sprite_btn.setFlat(True)
        self.about_sprite_btn.setFixedSize(80, 80)
        sprite_bg = self._central_container_bg()
        self.about_sprite_btn.setStyleSheet(
            f"background-color: {sprite_bg}; border: 1px solid {sprite_bg}; border-radius: 8px;")
        self.about_sprite_btn.clicked.connect(self.show_about_dialog)
        self.about_sprite_btn.installEventFilter(self)
        about_box.addWidget(self.about_sprite_btn, alignment=Qt.AlignCenter)
        bili_link = QLabel(f'<a href="https://space.bilibili.com/3461569935575626?spm_id_from=333.788">{t("b站@小小电子xxdz")}</a>')
        bili_link.setOpenExternalLinks(True)
        bili_link.setAlignment(Qt.AlignCenter)
        bili_link.setStyleSheet("font-size: 12px;")
        about_box.addWidget(bili_link, alignment=Qt.AlignCenter)
        about_row.addLayout(about_box)
        right.addLayout(about_row)
        self._setup_about_sprite_animation()
        return page

    def _settings_tab(self):
        """Build the global settings page with a safe fallback.

        The settings UI is rebuilt on demand when the global settings dialog is
        opened.  A single layout-parent mistake in this function can otherwise
        close the whole application.  Keep the real builder isolated and return
        a minimal diagnostics page if construction fails, so wallpaper playback
        and the main window remain usable.
        """
        try:
            return self._settings_tab_full()
        except Exception as exc:
            try:
                core.log("settings page build failed", level="ERROR", exc_info=True)
            except Exception:
                pass
            return self._settings_tab_fallback(exc)

    def _settings_tab_fallback(self, exc=None):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel(t("设置页加载失败，已进入安全模式"))
        title.setWordWrap(True)
        try:
            title.setStyleSheet("font-weight: 700; font-size: 16px;")
        except Exception:
            pass
        layout.addWidget(title)

        hint = QLabel(
            t("主程序不会因此退出。请把下面的错误信息发给开发者；也可以先使用恢复出厂设置排除配置损坏。")
        )
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        layout.addWidget(hint)

        detail = QTextEdit()
        detail.setReadOnly(True)
        detail.setMinimumHeight(180)
        detail.setPlainText(str(exc) if exc is not None else "")
        layout.addWidget(detail, 1)

        buttons = QHBoxLayout()
        reset_btn = QPushButton(t("恢复出厂设置"))
        reset_btn.setProperty("danger", True)
        reset_btn.clicked.connect(self.restore_factory_settings)
        buttons.addWidget(reset_btn)

        close_btn = QPushButton(t("关闭"))
        close_btn.setProperty("secondary", True)
        close_btn.clicked.connect(lambda: self._settings_dialog.close() if self._is_qobject_alive(getattr(self, "_settings_dialog", None)) else None)
        buttons.addWidget(close_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _settings_tab_full(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.settings_search_edit = QLineEdit()
        self.settings_search_edit.setObjectName("SettingsSearchEdit")
        self.settings_search_edit.setPlaceholderText(t("搜索设置、功能或关键词"))
        self.settings_search_edit.setClearButtonEnabled(True)
        self.settings_search_edit.setAccessibleName(t("搜索设置"))
        self.settings_search_edit.setAccessibleDescription(t("输入关键词筛选左侧设置页面和页面内控件。"))
        search_row.addWidget(self.settings_search_edit, 1)
        self.settings_search_result_label = QLabel()
        self.settings_search_result_label.setObjectName("SettingsSearchResult")
        self.settings_search_result_label.setProperty("muted", True)
        self.settings_search_result_label.setMinimumWidth(34)
        self.settings_search_result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        search_row.addWidget(self.settings_search_result_label)
        root.addLayout(search_row)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        nav = QListWidget()
        nav.setObjectName("SettingsNav")
        nav.setAccessibleName(t("设置分类"))
        nav.setFixedWidth(190)
        nav.setSpacing(4)
        self._settings_nav = nav
        self._refresh_settings_nav_style(nav)
        nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._enable_touch_scrolling(nav)
        body.addWidget(nav)

        stack = QStackedWidget()
        stack.setObjectName("SettingsPageStack")
        body.addWidget(stack, 1)

        def _on_settings_page_activated(scroll):
            if self._is_qobject_alive(scroll):
                scroll.update()
            def _deferred_refresh():
                if self._is_qobject_alive(stack) and self._is_qobject_alive(getattr(self, "_settings_dialog", None)):
                    self.refresh_from_config()
            QTimer.singleShot(50, _deferred_refresh)

        navigator = SettingsNavigator(
            nav,
            stack,
            empty_text=t("没有找到匹配的设置。请尝试更短或更通用的关键词。"),
            on_page_activated=_on_settings_page_activated,
        )
        navigator.bind_search(self.settings_search_edit, self.settings_search_result_label)
        self._settings_navigator = navigator

        def add_settings_page(title: str, widget: QWidget, keywords=()):
            navigator.add_page(title, widget, keywords=keywords)

        appearance_page = QWidget()
        appearance_layout = QVBoxLayout(appearance_page)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        appearance_layout.setSpacing(12)

        appearance_box = QGroupBox(t("外观与显示"))
        appearance_form = QFormLayout(appearance_box)
        appearance_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        appearance_form.setHorizontalSpacing(14)
        appearance_form.setVerticalSpacing(10)

        theme_color_row = QWidget()
        theme_color_layout = QHBoxLayout(theme_color_row)
        theme_color_layout.setContentsMargins(0, 0, 0, 0)
        theme_color_layout.setSpacing(8)
        self.theme_color_edit = configure_text_input(
            QLineEdit(self._theme_color if hasattr(self, "_theme_color") else core.config.get("theme_color", DEFAULT_THEME_COLOR)),
            name=t("界面主题色"),
            description=t("输入六位十六进制颜色，例如 #ffffff；点击应用后更新界面。"),
            object_name="ThemeColorEdit",
            placeholder="#ffffff",
        )
        self.theme_color_edit.setMaximumWidth(120)
        self.theme_color_preview = QLabel()
        self.theme_color_preview.setFixedSize(28, 28)
        self._update_theme_color_preview()
        theme_color_btn = QPushButton(t("选择颜色"))
        theme_color_btn.setProperty("secondary", True)
        theme_color_btn.setProperty("settingsAction", True)
        theme_color_btn.setMinimumWidth(86)
        theme_color_btn.setMaximumWidth(118)
        theme_color_btn.clicked.connect(self._choose_theme_color)
        theme_color_apply_btn = QPushButton(t("应用"))
        theme_color_apply_btn.setProperty("settingsAction", True)
        theme_color_apply_btn.setMinimumWidth(70)
        theme_color_apply_btn.setMaximumWidth(96)
        theme_color_apply_btn.clicked.connect(self._apply_theme_color)
        theme_color_layout.addWidget(self.theme_color_edit)
        theme_color_layout.addWidget(self.theme_color_preview)
        theme_color_layout.addWidget(theme_color_btn)
        theme_color_layout.addWidget(theme_color_apply_btn)
        theme_color_layout.addStretch(1)
        appearance_form.addRow(t("主题色"), theme_color_row)

        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(6)
        preset_colors = [
            (t("白"), "#ffffff"), (t("红"), "#d73a49"), (t("橙"), "#f97316"), (t("黄"), "#d4a72c"),
            (t("绿"), "#2da44e"), (t("青"), "#14b8a6"), (t("蓝"), "#0969da"), (t("紫"), "#8250df"),
        ]
        for name, hex_color in preset_colors:
            btn = QPushButton(name)
            btn.setFixedSize(46, 26)
            btn.setToolTip(hex_color)
            preset_qcolor = QColor(hex_color)
            preset_brightness = (preset_qcolor.red() * 299 + preset_qcolor.green() * 587 + preset_qcolor.blue() * 114) / 1000
            preset_text = "#24292f" if preset_brightness >= 170 else "#ffffff"
            preset_border = "#d0d7de" if preset_brightness >= 230 else preset_qcolor.darker(115).name()
            btn.setStyleSheet(
                f"background: {hex_color};"
                f" color: {preset_text}; border: 1px solid {preset_border};"
                f" border-radius: 4px; font-size: 11px; font-weight: 600;")
            btn.clicked.connect(lambda checked, c=hex_color: self._set_theme_color_preset(c))
            preset_layout.addWidget(btn)
        preset_layout.addStretch(1)
        appearance_form.addRow(t("预设配色"), preset_row)

        self.font_path_edit = configure_text_input(
            QLineEdit(core.config.get("font_path", "")),
            name=t("自定义字体路径"),
            description=t("可填写字体文件或字体文件夹；清空后恢复使用系统字体。"),
            object_name="FontPathEdit",
            placeholder=t("可填写字体文件或字体文件夹路径"),
        )
        font_btn = QPushButton(t("选择"))
        font_btn.setProperty("secondary", True)
        font_btn.setProperty("settingsAction", True)
        font_btn.setMinimumWidth(70)
        font_btn.setMaximumWidth(96)
        font_btn.clicked.connect(self.choose_font_path)
        font_row = QWidget()
        font_row_layout = QHBoxLayout(font_row)
        font_row_layout.setContentsMargins(0, 0, 0, 0)
        font_row_layout.setSpacing(8)
        font_row_layout.addWidget(self.font_path_edit, 1)
        font_row_layout.addWidget(font_btn)
        appearance_form.addRow(t("自定义字体"), font_row)

        # v1.4.7: 字体粗细下拉
        self.font_weight_combo = ShangComboBox()
        self.font_weight_combo.addItem(t("正常"), "normal")
        self.font_weight_combo.addItem(t("中等"), "medium")
        self.font_weight_combo.addItem(t("粗体"), "bold")
        self._prepare_combo_popup(self.font_weight_combo)
        _cur_fw = str(core.config.get("font_weight", "normal")).lower()
        _fw_idx = self.font_weight_combo.findData(_cur_fw)
        if _fw_idx >= 0:
            self.font_weight_combo.setCurrentIndex(_fw_idx)
        self.font_weight_combo.setToolTip(t("界面字体的粗细程度。需要重启程序完全生效。"))
        appearance_form.addRow(t("字体粗细"), self.font_weight_combo)

        # v1.4.7: 字体大小微调框 (0 = 跟随系统默认)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(0, 48)
        self.font_size_spin.setSingleStep(1)
        self.font_size_spin.setSuffix(" px")
        self.font_size_spin.setSpecialValueText(t("系统默认"))
        # Bug 14 fix: 全局 QSpinBox max-width:118px 会让英文 "System Default" 被截断。
        # 为该 spinbox 单独放宽宽度，确保特殊值文本完整显示。
        self.font_size_spin.setMinimumWidth(160)
        self.font_size_spin.setMaximumWidth(200)
        try:
            self.font_size_spin.setValue(int(core.config.get("font_size", 0)))
        except Exception:
            self.font_size_spin.setValue(0)
        self.font_size_spin.setToolTip(t("0 = 跟随系统默认；设置后所有界面文字使用此大小。需要重启程序完全生效。"))
        appearance_form.addRow(t("字体大小"), self.font_size_spin)

        dpi_row = QWidget()
        dpi_layout = QHBoxLayout(dpi_row)
        dpi_layout.setContentsMargins(0, 0, 0, 0)
        dpi_layout.setSpacing(10)
        self.dpi_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.dpi_scale_slider.setRange(75, 200)
        self.dpi_scale_slider.setSingleStep(5)
        self.dpi_scale_slider.setPageStep(10)
        self.dpi_scale_slider.setValue(dpi_percent(core.config.get("dpi_scale", 1.0)))
        self.dpi_scale_value_label = QLabel(f"{self.dpi_scale_slider.value()}%")
        self.dpi_scale_value_label.setMinimumWidth(54)
        self.dpi_scale_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.dpi_scale_slider.valueChanged.connect(self.on_dpi_scale_changed)
        dpi_layout.addWidget(self.dpi_scale_slider, 1)
        dpi_layout.addWidget(self.dpi_scale_value_label)
        appearance_form.addRow(t("程序内 DPI"), dpi_row)

        # ── 暗色模式开关 ──
        self.dark_mode_check = QCheckBox(t("暗色模式"))
        self.dark_mode_check.setChecked(bool(core.config.get("dark_mode", False)))
        self.dark_mode_check.toggled.connect(self._on_dark_mode_toggled)
        appearance_form.addRow(t("界面主题"), self.dark_mode_check)

        # ── 界面动画开关 (Bug 9 同期新增) ──
        # 默认开启以保留历史行为; 关闭后侧边栏滑入/滑出与"关于"按钮精灵
        # 渐变将跳过动画, 直接切换到终态. 不影响功能动画 (如进度条、微交互).
        self.animations_check = QCheckBox(t("启用界面动画"))
        self.animations_check.setChecked(bool(core.config.get("enable_animations", True)))
        self.animations_check.setToolTip(t(
            "关闭后将跳过侧边栏滑入/滑出、关于按钮渐变等装饰性动画，"
            "在低端机器或对动态效果敏感时建议关闭。"))
        appearance_form.addRow(t("界面动画"), self.animations_check)

        self.wallpaper_transition_check = QCheckBox(t("启用壁纸切换动画"))
        self.wallpaper_transition_check.setChecked(bool(core.config.get("wallpaper_transition_enabled", True)))
        self.wallpaper_transition_check.setToolTip(t(
            "关闭后图片壁纸直接切换到目标帧；开启后恢复 Windows 原生壁纸过渡效果。该开关会立即保存。"))
        self.wallpaper_transition_check.toggled.connect(self._on_wallpaper_transition_toggled)
        appearance_form.addRow(t("壁纸切换动画"), self.wallpaper_transition_check)

        # v1.4.6: 性能模式从布尔开关升级为三档下拉 (省电/平衡/性能)
        self.perf_mode_combo = ShangComboBox()
        self.perf_mode_combo.addItem(t("节能 · 降低后台检测频率"), "power_saver")
        self.perf_mode_combo.addItem(t("均衡 · 推荐"), "balanced")
        self.perf_mode_combo.addItem(t("流畅 · 提高动态壁纸响应"), "performance")
        self._prepare_combo_popup(self.perf_mode_combo)
        self._constrain_combo_width(self.perf_mode_combo, min_width=180, max_width=270)
        _cur_perf_level = self._perf_level()
        _perf_idx = self.perf_mode_combo.findData(_cur_perf_level)
        if _perf_idx >= 0:
            self.perf_mode_combo.setCurrentIndex(_perf_idx)
        self.perf_mode_combo.setToolTip(t("这是壁纸刷新/检测策略，不是系统电源计划。"))
        self.perf_mode_combo.currentIndexChanged.connect(self._on_performance_level_changed)
        appearance_form.addRow(t("壁纸调度策略"), self.perf_mode_combo)
        self.perf_mode_check = None  # 向后兼容

        display_buttons = QHBoxLayout()
        save_display_btn = QPushButton(t("保存并应用显示设置"))
        save_display_btn.setProperty("secondary", True)
        save_display_btn.setProperty("settingsAction", True)
        save_display_btn.setMinimumWidth(154)
        save_display_btn.setMaximumWidth(190)
        save_display_btn.clicked.connect(self.save_display_settings)
        reset_display_btn = QPushButton(t("重置外观与显示"))
        reset_display_btn.setProperty("secondary", True)
        reset_display_btn.setProperty("settingsAction", True)
        reset_display_btn.setMinimumWidth(140)
        reset_display_btn.setMaximumWidth(180)
        reset_display_btn.clicked.connect(self.reset_display_settings)
        display_buttons.addWidget(save_display_btn)
        display_buttons.addWidget(reset_display_btn)
        display_buttons.addStretch(1)
        appearance_layout.addWidget(appearance_box)
        appearance_layout.addLayout(display_buttons)
        appearance_layout.addStretch(1)

        # 修复 (v1.4.6): 移除全局设置中重复的语言选项.
        # 主 GUI 头部已有 "中/EN" 紧凑切换 (_create_header_language_switch),
        # 此处再放一个下拉是冗余, 且需要双向同步, 维护负担大.
        # 保留 self.lang_combo 属性为 None 以防其他代码引用时报错.
        self.lang_combo = None

        add_settings_page(t("外观与显示"), appearance_page)

        shell_page = QWidget()
        shell_layout = QVBoxLayout(shell_page)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(12)

        runtime = QGroupBox(t("后台与启动"))
        form = QFormLayout(runtime)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.bg_check = QCheckBox(t("关闭窗口时隐藏到托盘"))
        self.bg_check.toggled.connect(self.on_background_changed)
        self.auto_start_check = QCheckBox(t("开机自启动"))
        self.auto_start_check.setToolTip(t(self._platform_ui_policy().auto_start_tooltip_key))
        self.auto_start_check.toggled.connect(self.on_auto_start_changed)
        self.silent_update_check_on_startup_check = QCheckBox(t("启动时静默检查程序更新"))
        self.silent_update_check_on_startup_check.setToolTip(t("程序启动后延迟检查 GitHub Release；无更新或失败时只写入日志，不弹窗。"))
        self.silent_update_check_on_startup_check.toggled.connect(self.on_silent_update_check_on_startup_changed)
        self.silent_update_check_on_startup_check.setVisible(is_feature_enabled("updates"))
        self.tray_check = QCheckBox(t("显示系统托盘图标"))
        self.tray_check.toggled.connect(self.on_tray_changed)
        self.tray_action = ShangComboBox()
        self.tray_action_map = {
            t("下一张壁纸"): "next",
            t("上一张壁纸"): "previous",
            t("随机壁纸"): "random",
            t("打开主界面"): "show",
            t("跳转到当前壁纸"): "jump",
            t("无操作"): "none",
        }
        for label, action in self.tray_action_map.items():
            self.tray_action.addItem(label, action)
        self._prepare_combo_popup(self.tray_action)
        self._constrain_combo_width(self.tray_action, min_width=150, max_width=240)
        self.tray_action.currentIndexChanged.connect(self.on_tray_action_changed)
        self.tray_notify_check = QCheckBox(t("最小化到托盘时显示通知"))
        self.tray_notify_check.toggled.connect(self.on_tray_notify_changed)
        form.addRow(self.bg_check)
        form.addRow(self.auto_start_check)
        form.addRow(self.silent_update_check_on_startup_check)
        form.addRow(self.tray_check)
        form.addRow(t("单击托盘图标"), self.tray_action)
        form.addRow(self.tray_notify_check)

        shell_layout.addWidget(runtime)
        shell_layout.addStretch(1)
        add_settings_page(t("后台与启动"), shell_page)

        tray_page = QWidget()
        tray_layout_outer = QVBoxLayout(tray_page)
        tray_layout_outer.setContentsMargins(0, 0, 0, 0)
        tray_layout_outer.setSpacing(12)
        tray_menu_box = QGroupBox(t("托盘右键菜单项"))
        tray_menu_layout = QGridLayout(tray_menu_box)
        tray_menu_layout.setHorizontalSpacing(14)
        tray_menu_layout.setVerticalSpacing(12)
        self.tray_menu_labels = {
            "show": t("打开主界面"), "previous": t("上一张"), "next": t("下一张"), "random": t("随机"),
"bing": t("同步必应"), "jump": t("跳转壁纸"), "about": t("关于"), "exit": t("退出"),
        }
        if not is_feature_enabled("bing"):
            self.tray_menu_labels.pop("bing", None)
        self.tray_menu_checks = {}
        for i, (action, label) in enumerate(self.tray_menu_labels.items()):
            cb = QCheckBox(label)
            cb.toggled.connect(self.on_tray_menu_changed)
            self.tray_menu_checks[action] = cb
            tray_menu_layout.addWidget(cb, i // 3, i % 3)
        tray_layout_outer.addWidget(tray_menu_box)
        tray_layout_outer.addStretch(1)
        add_settings_page(t("托盘菜单"), tray_page)

        if is_feature_enabled("hotkeys"):
            self._add_global_hotkey_settings_page(add_settings_page)

        advanced_page = QWidget()
        advanced_layout = QVBoxLayout(advanced_page)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(12)

        # ── 分级重置选项 (v1.4.6) ──
        granular_box = QGroupBox(t("分级重置"))
        granular_layout = QVBoxLayout(granular_box)
        granular_layout.setContentsMargins(10, 18, 10, 10)
        granular_layout.setSpacing(8)

        granular_grid = QGridLayout()
        granular_grid.setHorizontalSpacing(10)
        granular_grid.setVerticalSpacing(8)

        def _make_reset_btn(label, tooltip, handler, danger=False):
            btn = QPushButton(label)
            btn.setProperty("secondary", True)
            if danger:
                btn.setProperty("danger", True)
            btn.setToolTip(tooltip)
            btn.clicked.connect(handler)
            return btn

        btn_reset_history = _make_reset_btn(t("重置壁纸历史"), t("清空已切换过的壁纸历史记录（不影响当前壁纸和文件夹）"), self._reset_history_only)
        btn_reset_hotkeys = _make_reset_btn(t("重置所有快捷键"), t("把全局热键恢复为默认值"), self._reset_hotkeys_only)
        btn_reset_appearance = _make_reset_btn(t("重置外观设置"), t("重置主题色、字体路径、程序内 DPI、暗色模式、性能模式"), self._reset_appearance_only)
        btn_reset_tray = _make_reset_btn(t("重置托盘菜单"), t("把托盘菜单项恢复为默认列表"), self._reset_tray_only)
        btn_clear_log = _make_reset_btn(t("清空实时日志"), t("清空内存中的实时日志缓冲区（不影响日志文件）"), self._reset_log_buffer_only)
        granular_grid.addWidget(btn_reset_history, 0, 0)
        granular_grid.addWidget(btn_reset_hotkeys, 0, 1)
        granular_grid.addWidget(btn_reset_appearance, 1, 0)
        granular_grid.addWidget(btn_reset_tray, 1, 1)
        granular_grid.addWidget(btn_clear_log, 2, 0)
        btn_clear_favorites = _make_reset_btn(
            t("清空收藏夹"),
            t("清空全部收藏的壁纸（不会删除壁纸文件）"),
            self._clear_all_favorites,
        )
        if self._platform_ui_policy().show_desktop_context_menu:
            btn_unregister_ctx = _make_reset_btn(
                t("注销桌面右键菜单"),
                t("从 Windows 桌面右键菜单移除本程序注册的项（无需管理员权限）"),
                self._unregister_context_menu_only,
            )
            granular_grid.addWidget(btn_unregister_ctx, 2, 1)
            granular_grid.addWidget(btn_clear_favorites, 3, 0)
        else:
            granular_grid.addWidget(btn_clear_favorites, 2, 1)
        granular_layout.addLayout(granular_grid)
        advanced_layout.addWidget(granular_box)

        reset_box = QGroupBox(t("恢复出厂设置"))
        reset_layout = QVBoxLayout(reset_box)
        reset_label = QLabel(t("恢复出厂设置会清空当前配置、历史记录、托盘菜单和外观偏好，不会删除本地壁纸文件。"))
        reset_label.setWordWrap(True)
        reset_label.setProperty("muted", True)
        reset_button = QPushButton(t("恢复出厂设置"))
        reset_button.setProperty("danger", True)
        reset_button.clicked.connect(self.restore_factory_settings)
        reset_layout.addWidget(reset_label)
        reset_layout.addWidget(reset_button, alignment=Qt.AlignLeft)
        advanced_layout.addWidget(reset_box)
        advanced_layout.addStretch(1)
        add_settings_page(t("高级"), advanced_page)

        # 将日志功能移动到全局设置中：添加日志页
        try:
            log_page = self._log_tab()
            add_settings_page(t("日志"), log_page)
        except Exception:
            # 如果日志页面创建失败则忽略
            pass

        navigator.reset()
        self.settings_search_result_label.setToolTip(t("当前可见设置分类数量"))
        return page

    def restore_factory_settings(self):
        reply = QMessageBox.question(
            self,
            t("恢复出厂设置"),
            t("确定要恢复出厂设置吗？这会清空当前配置和历史记录，但不会删除本地壁纸文件。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            try:
                core.request_cancel_operations(t("恢复出厂设置"))
                core.stop_slideshow()
                core.stop_video_wallpaper()
            except Exception as exc:
                core.log(f"恢复出厂设置前停止后台任务失败: {exc}")
            # Keep the currently displayed static image only long enough to
            # re-apply it after the reset.  The path is deliberately not written
            # back to the new factory config: settings/history are still cleared,
            # while the operating system's persistent picture-ratio state is
            # actually returned to the default instead of merely updating the UI.
            previous_wallpaper = str(core.config.get("current_wallpaper") or "").strip()
            if not previous_wallpaper or not os.path.isfile(previous_wallpaper):
                try:
                    previous_wallpaper = str(core.get_current_wallpaper() or "").strip()
                except Exception as exc:
                    core.log(f"恢复出厂设置前读取当前壁纸失败: {exc}")
                    previous_wallpaper = ""
            if previous_wallpaper and not os.path.isfile(previous_wallpaper):
                previous_wallpaper = ""

            defaults = core.get_default_config()
            core.config.clear()
            core.config.update(defaults)
            core.config.pop("__config_migration_pending__", None)
            # Factory reset must also reset the native desktop scaling state.
            # Clearing settings.json alone leaves the OS/desktop-environment
            # picture option at the previous value, so the UI says “填充” while
            # the actual desktop still uses the old ratio.
            core.configure_fit_mode(defaults.get("fit_mode", "填充"), core.winreg, core.log)
            if previous_wallpaper:
                try:
                    core.set_wallpaper_platform(previous_wallpaper)
                    core.refresh_shell_ui()
                except Exception as exc:
                    # Do not roll back a successful configuration reset when the
                    # desktop backend is temporarily unavailable; surface it in
                    # the log so the platform diagnostic can explain the limit.
                    core.log(f"恢复出厂比例后重新应用当前壁纸失败: {exc}")
            core.save_config()
            # A factory reset also removes persisted logs under the per-user
            # application-data directory. This is intentionally broader than
            # the granular "clear live log" action, which only clears memory.
            try:
                from app.log_setup import purge_log_files, set_file_logging_enabled
                _removed_logs, _failed_logs = purge_log_files()
                if bool(core.config.get("log_enabled", False)):
                    set_file_logging_enabled(True)
                if _failed_logs:
                    core.log(f"factory reset could not remove {_failed_logs} log item(s)", level="WARNING")
            except Exception as exc:
                core.log(f"factory reset log cleanup failed: {exc}", level="WARNING")
            load_language(core.config.get("language", "zh"))
            self._theme_color = core.config.get("theme_color", DEFAULT_THEME_COLOR)
            self._icon_pixmap_cache = OrderedDict()
            self._apply_performance_mode_runtime()
            self._rebuild_stylesheet()
            self._refresh_settings_nav_style()
            self._refresh_svg_button_icons()
            self.refresh_from_config()
            self.update_preview()
            self.create_or_update_tray() if core.config.get("tray_icon", True) else None
            self.set_status(t("已恢复出厂设置"))
            show_info(self, t("恢复出厂设置"), t("已恢复出厂设置。部分启动项、语言和 DPI 设置可能需要重启程序后完全生效。"))
        except Exception as exc:
            show_warning(self, t("恢复出厂设置"), t("恢复出厂设置失败：") + str(exc))

    # ── 分级重置方法 (v1.4.6) ──
    def _reset_history_only(self) -> None:
        try:
            reply = QMessageBox.question(self, t("重置壁纸历史"), t("确定清空壁纸历史记录吗？不影响当前壁纸和文件夹。"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            core.clear_wallpaper_history(reset_slideshow_position=True)
            self.refresh_history_list()
            self.set_status(t("壁纸历史已清空"))
        except Exception as exc:
            QMessageBox.warning(self, t("重置壁纸历史"), t("重置失败：") + str(exc))

    def _reset_hotkeys_only(self) -> None:
        try:
            reply = QMessageBox.question(self, t("重置快捷键"), t("确定把所有快捷键恢复为默认值吗？"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            defaults = core.get_default_config()
            for key in ("hotkey_previous", "hotkey_next", "hotkey_random", "hotkey_jump"):
                core.config[key] = defaults.get(key, "")
            core.config["app_shortcuts"] = dict(defaults.get("app_shortcuts", {}))
            core.save_config()
            try:
                if bool(core.config.get("global_hotkeys_enabled", False)):
                    core.refresh_global_hotkeys()
                else:
                    core.stop_global_hotkeys()
            except Exception as exc:
                core.log(f"刷新全局热键失败: {exc}")
            self._refresh_context_shortcut_labels()
            self.refresh_from_config()
            self.set_status(t("快捷键已重置为默认值"))
        except Exception as exc:
            QMessageBox.warning(self, t("重置快捷键"), t("重置失败：") + str(exc))

    def _reset_appearance_only(self) -> None:
        try:
            reply = QMessageBox.question(self, t("重置外观设置"), t("确定重置外观设置吗？包括主题色、字体路径、字体粗细、字体大小、程序内 DPI、暗色模式、性能模式。"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            defaults = core.get_default_config()
            # v1.4.0 修复: 之前 pop("font_size") 会删除新 font_size 键, 现在改为重置为默认值.
            # 同时补上 font_weight / font_size / performance_level (之前漏掉).
            for key in ("theme_color", "font_path", "font_weight", "font_size",
                        "dpi_scale", "dark_mode", "enable_animations",
                        "wallpaper_transition_enabled", "transition_effect",
                        "transition_duration_ms", "wallpaper_transition_policy_version",
                        "performance_mode", "performance_level"):
                if key in defaults:
                    core.config[key] = defaults.get(key)
            core.save_config()
            self._theme_color = core.config.get("theme_color", DEFAULT_THEME_COLOR)
            self._icon_pixmap_cache = OrderedDict()
            self._apply_performance_mode_runtime()
            self._rebuild_stylesheet()
            self._refresh_settings_nav_style()
            self._refresh_svg_button_icons()
            self.refresh_from_config()
            apply_dpi_environment(core.config)
            self.set_status(t("外观设置已重置"))
            QMessageBox.information(self, t("重置外观设置"), t("已重置主题色、字体路径、字体粗细、字体大小、程序内 DPI、暗色模式和性能模式。DPI 和字体设置需重启程序完全生效。"))
        except Exception as exc:
            QMessageBox.warning(self, t("重置外观设置"), t("重置失败：") + str(exc))

    def _reset_tray_only(self) -> None:
        try:
            reply = QMessageBox.question(self, t("重置托盘菜单"), t("确定把托盘菜单项恢复为默认列表吗？"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            defaults = core.get_default_config()
            core.config["tray_menu_items"] = list(defaults.get("tray_menu_items", []))
            core.config["tray_click_action"] = defaults.get("tray_click_action", "next")
            core.save_config()
            self.create_or_update_tray() if core.config.get("tray_icon", True) else None
            self.refresh_from_config()
            self.set_status(t("托盘菜单已重置"))
        except Exception as exc:
            QMessageBox.warning(self, t("重置托盘菜单"), t("重置失败：") + str(exc))

    def _reset_log_buffer_only(self) -> None:
        try:
            from app.log_setup import clear_recent_logs
            clear_recent_logs()
            self._refresh_log_viewer()
            self.set_status(t("实时日志缓冲区已清空"))
        except Exception as exc:
            QMessageBox.warning(self, t("清空实时日志"), t("清空失败：") + str(exc))

    def _unregister_context_menu_only(self) -> None:
        try:
            if not core.IS_WINDOWS:
                show_info(self, t("注销桌面右键菜单"), t("此功能仅在 Windows 上可用。"))
                return
            reply = QMessageBox.question(self, t("注销桌面右键菜单"), t("确定从 Windows 桌面右键菜单移除本程序注册的项吗？"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                for key in ("ctx_last_wallpaper", "ctx_next_wallpaper", "ctx_random_wallpaper", "ctx_jump_to_wallpaper"):
                    core.config[key] = False
                core.save_config()
                core.register_context(show_admin_prompt=False)
                self.set_status(t("桌面右键菜单已注销"))
                show_info(self, t("注销桌面右键菜单"), t("已从桌面右键菜单移除本程序注册的项。"))
            except Exception as exc:
                core.log(f"注销桌面右键菜单失败: {exc}", level="ERROR", exc_info=True)
                QMessageBox.warning(self, t("注销桌面右键菜单"), t("注销失败：") + str(exc))
        except Exception as exc:
            QMessageBox.warning(self, t("注销桌面右键菜单"), t("操作失败：") + str(exc))

    def choose_font_path(self):
        start = self.font_path_edit.text().strip() if hasattr(self, "font_path_edit") else ""
        if not start or not os.path.exists(start):
            start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, t("选择字体文件"), start, t("字体文件 (*.ttf *.ttc *.otf);;所有文件 (*.*)"))
        if not path:
            folder = QFileDialog.getExistingDirectory(self, t("或选择字体文件夹"), start)
            path = folder or ""
        if path and hasattr(self, "font_path_edit"):
            self.font_path_edit.setText(path)

    def on_dpi_scale_changed(self, value: int):
        if hasattr(self, "dpi_scale_value_label"):
            self.dpi_scale_value_label.setText(f"{int(value)}%")

    def _on_language_changed(self, index):
        """Handle language combo box changes from Settings."""
        sender = self.sender()
        combo = sender if isinstance(sender, QComboBox) else getattr(self, "lang_combo", None)
        if not self._is_qobject_alive(combo):
            if combo is getattr(self, "lang_combo", None):
                self.lang_combo = None
            return
        try:
            lang_data = combo.currentData()
        except RuntimeError:
            self.lang_combo = None
            return
        self._apply_language_change(lang_data, source=combo)

    def _apply_language_change(self, lang_data: str, source=None):
        if lang_data not in ("zh", "en"):
            return
        combo = getattr(self, "lang_combo", None)
        if self._is_qobject_alive(combo) and combo is not source:
            try:
                idx = combo.findData(lang_data)
                if idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)
            except RuntimeError:
                self.lang_combo = None
        previous_lang = core.config.get("language", "zh")
        core.config["language"] = lang_data
        core.save_config()
        load_language(lang_data)
        if lang_data == previous_lang:
            self._refresh_header_language_buttons(lang_data)

    def _on_i18n_language_changed(self, event: LanguageChangeEvent) -> None:
        """Re-render visible Qt surfaces after the JSON dictionary changes."""
        self._refresh_header_language_buttons(event.current)
        if event.current == event.previous:
            return
        self._rebuild_ui_for_language_change()
        self._schedule_preview_refresh(0)
        if not event.translations_loaded and event.current != "zh":
            self.set_status(t("语言资源加载失败，界面已回退到中文键值。"))
        else:
            self.set_status(t("界面语言已切换。少数系统菜单和后台组件会在重启程序后完全生效。"))

    def _rebuild_ui_for_language_change(self) -> None:
        """Recreate visible pages after changing the dictionary language.

        The UI is constructed in code rather than Qt Designer, so installing a
        translator alone cannot update already-created labels.  Rebuilding the
        central pages keeps runtime language switching usable without forcing a
        restart, while preserving the current tab and config-driven state.
        """
        try:
            current_tab = self.tabs.currentIndex() if hasattr(self, "tabs") and self.tabs is not None else 0
        except Exception:
            current_tab = 0
        old_central = self.centralWidget()
        try:
            self._clear_settings_widget_refs()
        except Exception:
            pass
        self._build_ui()
        self.refresh_from_config()
        try:
            if hasattr(self, "tabs") and self.tabs is not None:
                self.tabs.setCurrentIndex(max(0, min(current_tab, self.tabs.count() - 1)))
        except Exception:
            pass
        try:
            self._apply_button_sizes()
            self.update_preview()
        except Exception:
            pass
        if old_central is not None:
            old_central.deleteLater()
        # Bug 17 fix: 重建 UI 后 status_label 被重置为"正在初始化界面…"，
        # 但 _status_full_text 仍为旧值。如果不主动刷新，状态栏会一直停留在
        # "正在初始化界面…"而非"就绪"。这里显式调用 set_status 重置为"就绪"。
        try:
            self.set_status(t("就绪"))
        except Exception:
            pass

    def _on_wallpaper_transition_toggled(self, checked: bool) -> None:
        enabled = bool(checked)
        core.config["wallpaper_transition_enabled"] = enabled
        core.config["transition_effect"] = "system" if enabled else "none"
        core.config["transition_duration_ms"] = 300 if enabled else 0
        core.config["wallpaper_transition_policy_version"] = 1
        core.save_config()
        self.set_status(
            t("壁纸切换动画已开启，将在下一次切换时使用系统过渡效果")
            if enabled
            else t("壁纸切换动画已关闭，下一次切换将直接显示目标帧")
        )

    def save_display_settings(self):
        value = self.font_path_edit.text().strip() if hasattr(self, "font_path_edit") else ""
        old_scale = clamp_dpi_scale(core.config.get("dpi_scale", 1.0))
        new_scale = clamp_dpi_scale((self.dpi_scale_slider.value() if hasattr(self, "dpi_scale_slider") else 100) / 100.0)
        core.config["font_path"] = value
        core.config["dpi_scale"] = new_scale
        # v1.4.7: 保存字体粗细和大小
        if hasattr(self, "font_weight_combo") and self._is_qobject_alive(getattr(self, "font_weight_combo", None)):
            _fw = self.font_weight_combo.currentData()
            if _fw in ("normal", "medium", "bold"):
                core.config["font_weight"] = _fw
        if hasattr(self, "font_size_spin") and self._is_qobject_alive(getattr(self, "font_size_spin", None)):
            try:
                core.config["font_size"] = int(self.font_size_spin.value())
            except Exception:
                core.config["font_size"] = 0
        if hasattr(self, "dark_mode_check"):
            core.config["dark_mode"] = bool(self.dark_mode_check.isChecked())
        # Bug 9 同期: 保存界面动画开关
        if hasattr(self, "animations_check") and self._is_qobject_alive(getattr(self, "animations_check", None)):
            core.config["enable_animations"] = bool(self.animations_check.isChecked())
        if hasattr(self, "wallpaper_transition_check") and self._is_qobject_alive(getattr(self, "wallpaper_transition_check", None)):
            enabled = bool(self.wallpaper_transition_check.isChecked())
            core.config["wallpaper_transition_enabled"] = enabled
            core.config["transition_effect"] = "system" if enabled else "none"
            core.config["transition_duration_ms"] = 300 if enabled else 0
            core.config["wallpaper_transition_policy_version"] = 1
        core.save_config()
        family = apply_application_font(QApplication.instance())
        self._rebuild_stylesheet()
        self._refresh_settings_nav_style()
        apply_dpi_environment(core.config)
        self.set_status(t("显示设置已保存：") + f"DPI {dpi_percent(new_scale)}% / " + t("字体") + f" {family}")
        if abs(old_scale - new_scale) > 0.001:
            show_info(self, t("外观与显示"), t("程序内 DPI 已保存。Qt 需要在启动前读取 DPI 设置，请重启程序后完全生效。"))
        else:
            QMessageBox.information(self, t("外观与显示"), t("显示设置已保存。当前字体：") + f"{family}，DPI：{dpi_percent(new_scale)}%")

    def reset_display_settings(self):
        core.config["theme_color"] = DEFAULT_THEME_COLOR
        core.config["font_path"] = ""
        core.config["font_weight"] = "normal"  # v1.4.7
        core.config["font_size"] = 0  # v1.4.7
        core.config["dpi_scale"] = 1.0
        core.config["dark_mode"] = False
        core.config["enable_animations"] = True  # Bug 9 同期: 默认启用动画
        core.config["wallpaper_transition_enabled"] = True
        core.config["transition_effect"] = "system"
        core.config["transition_duration_ms"] = 300
        core.config["wallpaper_transition_policy_version"] = 1
        core.save_config()
        self._theme_color = core.config["theme_color"]
        if hasattr(self, "theme_color_edit"):
            self.theme_color_edit.setText(self._theme_color)
        if hasattr(self, "dpi_scale_slider"):
            self.dpi_scale_slider.setValue(100)
        if hasattr(self, "font_path_edit"):
            self.font_path_edit.setText("")
        if hasattr(self, "animations_check") and self._is_qobject_alive(getattr(self, "animations_check", None)):
            self.animations_check.blockSignals(True)
            self.animations_check.setChecked(True)
            self.animations_check.blockSignals(False)
        if hasattr(self, "wallpaper_transition_check") and self._is_qobject_alive(getattr(self, "wallpaper_transition_check", None)):
            self.wallpaper_transition_check.blockSignals(True)
            self.wallpaper_transition_check.setChecked(True)
            self.wallpaper_transition_check.blockSignals(False)
        apply_application_font(QApplication.instance())
        self._rebuild_stylesheet()
        self._update_theme_color_preview()
        self._refresh_settings_dialog_surfaces()
        self._refresh_settings_nav_style()
        apply_dpi_environment(core.config)
        self.set_status(t("外观与显示已重置"))
        QMessageBox.information(self, t("外观与显示"), t("已重置主题色、字体路径和程序内 DPI。若 DPI 曾改变，请重启程序确认效果。"))

    # ---------- 暗色模式相关方法 ----------
    def _on_dark_mode_toggled(self, checked: bool) -> None:
        """切换暗色模式并立即应用样式。"""
        core.config["dark_mode"] = bool(checked)
        self._rebuild_stylesheet()
        self._refresh_settings_nav_style()
        if hasattr(self, "_refresh_color_buttons"):
            self._refresh_color_buttons()
        if hasattr(self, "_refresh_styled_widgets"):
            self._refresh_styled_widgets()
        # 强制刷新 SVG 图标以适配暗色/亮色 currentColor
        self._refresh_svg_button_icons()
        self.set_status(t("暗色模式已开启") if checked else t("亮色模式已恢复"))

    def _on_performance_mode_toggled(self, checked: bool) -> None:
        """旧版布尔性能模式切换 (v1.4.6 起改为三档, 此方法仅向后兼容)."""
        core.config["performance_mode"] = bool(checked)
        core.config["performance_level"] = "performance" if checked else "balanced"
        core.save_config()
        self._apply_performance_mode_runtime()

    def _on_performance_level_changed(self, index: int) -> None:
        """v1.4.6: 三档性能模式切换."""
        try:
            combo = getattr(self, "perf_mode_combo", None)
            if not self._is_qobject_alive(combo):
                return
            level = combo.currentData()
            if level not in ("power_saver", "balanced", "performance"):
                return
            core.config["performance_level"] = level
            core.config["performance_mode"] = (level == "performance")
            core.save_config()
            self._apply_performance_mode_runtime()
            _status_map = {
                "power_saver": t("性能模式：节能（降低后台刷新频率）"),
                "balanced": t("性能模式：均衡（推荐）"),
                "performance": t("性能模式：流畅（更快刷新响应）"),
            }
            self.set_status(_status_map.get(level, t("性能模式已切换")))
        except Exception as exc:
            try:
                core.log(f"切换性能模式失败: {exc}", level="WARNING", exc_info=True)
            except Exception:
                pass

    # ---------- 主题色相关方法 ----------
    def _update_theme_color_preview(self):
        """更新主题色预览色块。"""
        if not hasattr(self, "theme_color_preview"):
            return
        color = self._theme_color if hasattr(self, "_theme_color") else core.config.get("theme_color", DEFAULT_THEME_COLOR)
        self.theme_color_preview.setStyleSheet(
            f"background-color: {color}; border: 2px solid #d0d7de; border-radius: 6px;")

    def _choose_theme_color(self):
        """打开颜色选择器选择主题色。"""
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        current = QColor(self._theme_color if hasattr(self, "_theme_color") else core.config.get("theme_color", DEFAULT_THEME_COLOR))
        color = QColorDialog.getColor(current, self, t("选择主题色"))
        if color.isValid():
            hex_color = color.name()
            if hasattr(self, "theme_color_edit"):
                self.theme_color_edit.setText(hex_color)
            self._theme_color = hex_color
            self._update_theme_color_preview()

    def _apply_theme_color(self):
        """应用主题色并保存到配置。"""
        if not hasattr(self, "theme_color_edit"):
            return
        from PySide6.QtGui import QColor
        hex_color = self.theme_color_edit.text().strip()
        if not hex_color:
            hex_color = DEFAULT_THEME_COLOR
        # 验证颜色有效性
        test = QColor(hex_color)
        if not test.isValid():
            QMessageBox.warning(self, t("主题色"), t("无效的颜色值：") + f"{hex_color}\n" + t("请使用 #RRGGBB 格式，如 #ffffff"))
            return
        self._theme_color = hex_color
        core.config["theme_color"] = hex_color
        core.save_config()
        self._rebuild_stylesheet()
        self._update_theme_color_preview()
        self._refresh_settings_nav_style()
        self.set_status(t("主题色已应用：") + f"{hex_color}")

    def _set_theme_color_preset(self, hex_color: str):
        """快捷设置预设主题色。"""
        if hasattr(self, "theme_color_edit"):
            self.theme_color_edit.setText(hex_color)
        self._theme_color = hex_color
        self._update_theme_color_preview()
        self._apply_theme_color()

    def _bing_tab(self):
        page = QWidget()
        page.setObjectName("MainTabSurface")
        page.setAutoFillBackground(False)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # 目标：在 1020x700 的默认窗口内尽量一页显示。顶部集中配置和操作，底部保留缓存列表与预览。
        panel = QGroupBox(t("必应壁纸"))
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 14, 10, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(5, 1)

        self.bing_cache_edit = configure_text_input(
            QLineEdit(core.config.get("bing_cache_dir", "") or ""),
            name=t("必应壁纸缓存目录"),
            description=t("可输入现有目录或由同步功能创建的新目录；离开输入框时保存。"),
            object_name="BingCacheDirectoryEdit",
            placeholder=t("请先选择用于保存必应壁纸的目录"),
        )
        self._bing_cache_source = self._source_inputs.bind_directory_target(
            self.bing_cache_edit,
            key="bing_cache_dir",
            label=t("必应缓存目录"),
            saved_text=t("必应缓存目录已保存"),
            cleared_text=t("已清除必应缓存目录"),
            on_changed=self._on_bing_cache_source_changed,
        )
        self.bing_cache_edit.setMinimumHeight(30)
        btn_cache = QPushButton(t("选择目录"))
        btn_cache.setProperty("secondary", True)
        btn_cache.setMinimumHeight(30)
        # Bug 21 fix: 增加最大宽度从 116 到 130，避免中文"选择目录"被截断。
        btn_cache.setMaximumWidth(130)
        btn_cache.setMinimumWidth(95)
        btn_cache.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn_cache.clicked.connect(self.choose_bing_cache_dir)

        self.bing_resolution = ShangComboBox()
        for _label, _value in (
            (t("自动（主屏）"), "auto"),
            ("1920×1080", "1920x1080"),
            ("2560×1440", "2560x1440"),
            ("3840×2160", "3840x2160"),
            ("1366×768", "1366x768"),
            ("1920×1200", "1920x1200"),
        ):
            self.bing_resolution.addItem(_label, _value)
        self._prepare_combo_popup(self.bing_resolution)
        self.bing_resolution.setMinimumContentsLength(23)
        self.bing_resolution.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        _resolution_text_width = max(
            self.bing_resolution.fontMetrics().horizontalAdvance(self.bing_resolution.itemText(i))
            for i in range(self.bing_resolution.count())
        )
        self.bing_resolution.setMinimumWidth(max(220, _resolution_text_width + 54))
        self.bing_resolution.setMaximumWidth(310)
        self.bing_resolution.setMinimumHeight(30)
        self.bing_resolution.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.bing_count_spin = CompactSpinBox(64)
        self.bing_count_spin.setRange(1, 16)
        self.bing_count_spin.setValue(min(16, int(core.config.get("bing_sync_count", 1))))

        self.bing_auto_update_check = QCheckBox(t("启动更新"))
        self.bing_auto_update_check.setToolTip(t("程序启动后自动同步指定数量的必应壁纸，并把最新一张设为桌面背景。"))
        self.bing_auto_update_count_spin = CompactSpinBox(64)
        self.bing_auto_update_count_spin.setRange(1, 16)
        self.bing_auto_update_count_spin.setValue(max(1, min(16, int(core.config.get("bing_auto_update_count", core.config.get("bing_sync_count", 1)) or 1))))

        self.bing_auto_delete_check = QCheckBox(t("启动删旧"))
        self.bing_auto_delete_check.setToolTip(t("程序启动后只删除必应缓存目录中最旧的指定数量图片；不会删除文件名不含 bing 的用户图片。"))
        self.bing_auto_delete_count_spin = CompactSpinBox(64)
        self.bing_auto_delete_count_spin.setRange(1, 200)
        self.bing_auto_delete_count_spin.setValue(max(1, min(200, int(core.config.get("bing_auto_delete_count", 1) or 1))))

        self.bing_auto_update_check.setChecked(bool(core.config.get("bing_auto_update_on_start", False)))
        self.bing_auto_delete_check.setChecked(bool(core.config.get("bing_auto_delete_on_start", False)))
        for _widget in (self.bing_auto_update_check, self.bing_auto_update_count_spin,
                         self.bing_auto_delete_check, self.bing_auto_delete_count_spin):
            if hasattr(_widget, "toggled"):
                _widget.toggled.connect(self.on_bing_auto_options_changed)
            else:
                _widget.valueChanged.connect(self.on_bing_auto_options_changed)

        grid.addWidget(QLabel(t("缓存目录")), 1, 0)
        grid.addWidget(self.bing_cache_edit, 1, 1, 1, 5)
        grid.addWidget(btn_cache, 1, 6)

        options_row = QWidget()
        options = QVBoxLayout(options_row)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(6)

        # 将分辨率、同步张数、启动更新、启动删旧四组控件合并到同一行，
        # 避免在窄窗口下占用两行垂直空间。窗口宽度不足时由 Qt 的
        # QHBoxLayout 自动收缩 spacing，而不是换行。
        option_line = QHBoxLayout()
        option_line.setContentsMargins(0, 0, 0, 0)
        option_line.setSpacing(8)

        lbl_resolution = QLabel(t("分辨率"))
        lbl_count = QLabel(t("同步张数"))
        lbl_count_unit = QLabel(t("张"))
        lbl_update_unit = QLabel(t("张"))
        lbl_delete_unit = QLabel(t("张"))
        for _lbl in (lbl_resolution, lbl_count, lbl_count_unit, lbl_update_unit, lbl_delete_unit):
            _lbl.setWordWrap(False)
            _lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        for _spin in (self.bing_count_spin, self.bing_auto_update_count_spin, self.bing_auto_delete_count_spin):
            _spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            _spin.setFixedWidth(64)

        self.bing_auto_update_check.setMinimumWidth(0)
        self.bing_auto_delete_check.setMinimumWidth(0)
        self.bing_auto_update_check.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.bing_auto_delete_check.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        option_line.addWidget(lbl_resolution)
        option_line.addWidget(self.bing_resolution)
        option_line.addSpacing(10)
        option_line.addWidget(lbl_count)
        option_line.addWidget(self.bing_count_spin)
        option_line.addWidget(lbl_count_unit)
        option_line.addSpacing(10)
        option_line.addWidget(self.bing_auto_update_check)
        option_line.addWidget(self.bing_auto_update_count_spin)
        option_line.addWidget(lbl_update_unit)
        option_line.addSpacing(10)
        option_line.addWidget(self.bing_auto_delete_check)
        option_line.addWidget(self.bing_auto_delete_count_spin)
        option_line.addWidget(lbl_delete_unit)
        option_line.addStretch(1)

        options.addLayout(option_line)
        grid.addWidget(options_row, 2, 0, 1, 7)

        self.bing_sync_btn = QPushButton(t("同步今日"))
        self.bing_sync_btn.setToolTip(t("下载最新必应壁纸，完成后立即设置为桌面背景。"))
        self.bing_sync_btn.clicked.connect(lambda: self.sync_bing_wallpaper(set_latest=True))
        self.bing_multi_btn = QPushButton(t("仅缓存"))
        self.bing_multi_btn.setToolTip(t("按同步张数下载壁纸到缓存目录，不改变桌面背景。"))
        self.bing_multi_btn.clicked.connect(lambda: self.sync_bing_wallpaper(set_latest=False))
        self.bing_continue_btn = QPushButton(t("继续更早"))
        self.bing_continue_btn.setToolTip(t("从上次同步进度继续获取更早的必应壁纸。"))
        self.bing_continue_btn.clicked.connect(lambda: self.sync_bing_wallpaper(set_latest=False, continue_from_saved=True))
        self.bing_play_btn = QPushButton(t("设为幻灯片"))
        self.bing_play_btn.setToolTip(t("把必应缓存目录设为幻灯片放映文件夹。"))
        self.bing_play_btn.clicked.connect(self.use_bing_cache_as_slideshow)
        self.bing_saveas_btn = QPushButton(t("另存选中"))
        self.bing_saveas_btn.setToolTip(t("把列表中选中的缓存壁纸另存到其他位置。"))
        self.bing_saveas_btn.clicked.connect(self.save_selected_bing_as)
        for _btn in (self.bing_sync_btn, self.bing_multi_btn, self.bing_continue_btn,
                      self.bing_play_btn, self.bing_saveas_btn):
            _btn.setMinimumHeight(30)
            _btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        for _btn in (self.bing_sync_btn, self.bing_multi_btn, self.bing_continue_btn,
                     self.bing_play_btn, self.bing_saveas_btn):
            action_layout.addWidget(_btn)
        grid.addWidget(action_row, 3, 0, 1, 7)

        status_row = QWidget()
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)
        self.bing_progress = QProgressBar()
        self.bing_progress.setRange(0, 100)
        self.bing_progress.setValue(0)
        self.bing_progress.setMinimumHeight(20)
        self.bing_progress.setMaximumHeight(24)
        self.bing_progress.setTextVisible(True)
        self.bing_status = QLabel(t("未同步；请先选择缓存目录。"))
        self.bing_status.setWordWrap(False)
        self.bing_status.setProperty("muted", True)
        self.bing_status.setMinimumWidth(260)
        self.bing_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.bing_progress.setMaximumWidth(360)
        status_layout.addWidget(self.bing_progress, 0)
        status_layout.addWidget(self.bing_status, 1)
        grid.addWidget(status_row, 4, 0, 1, 7)

        outer.addWidget(panel, 0)

        list_section = QWidget()
        list_layout = QHBoxLayout(list_section)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(10)

        list_left = QWidget()
        list_left_layout = QVBoxLayout(list_left)
        list_left_layout.setContentsMargins(0, 0, 0, 0)
        list_left_layout.setSpacing(4)
        list_header = QLabel(t("已缓存壁纸（选中可预览）"))
        list_header.setProperty("muted", True)
        list_left_layout.addWidget(list_header)
        self.bing_list = QListWidget()
        self.bing_list.setUniformItemSizes(True)
        self.bing_list.setMinimumHeight(220)
        self.bing_list.itemSelectionChanged.connect(self.on_bing_selection_changed)
        self._enable_touch_scrolling(self.bing_list)
        list_left_layout.addWidget(self.bing_list, 1)
        list_layout.addWidget(list_left, 3)

        self._bing_preview_label = QLabel()
        self._bing_preview_label.setAlignment(Qt.AlignCenter)
        self._bing_preview_label.setMinimumWidth(220)
        self._bing_preview_label.setMaximumWidth(300)
        self._bing_preview_label.setMinimumHeight(220)
        self._bing_preview_label.setText(t("选中一张\n壁纸预览"))
        self._bing_preview_label.setProperty("muted", True)
        self._bing_preview_label.setStyleSheet("border: 1px dashed palette(mid); border-radius: 8px;")
        list_layout.addWidget(self._bing_preview_label, 2)

        outer.addWidget(list_section, 1)
        return page

    def _about_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel(f"{APP_DISPLAY_NAME} v{APP_VERSION}")
        title.setStyleSheet(self._text_style("primary", "font-size: 24px; font-weight: 700;"))
        layout.addWidget(title)

        desc = QLabel(t("一个用于快速切换、随机和管理桌面背景的小工具。"))
        desc.setWordWrap(True)
        desc.setStyleSheet(self._text_style("muted", "font-size: 13px;"))
        layout.addWidget(desc)

        _fg  = self._theme_role_colors()["fg_primary"]
        _lnk = "#8ab4f8" if self._theme_is_dark() else "#0969da"
        links = QLabel()
        links.setOpenExternalLinks(False)
        links.linkActivated.connect(self._handle_about_link)
        links.setWordWrap(True)
        def _build_links_html(fg, lnk):
            def anchor(href, label):
                return f'<a href="{href}" style="color:{lnk}">{label}</a>'
            return (
                f'<span style="color:{fg}">原项目：</span>'
                + anchor("https://github.com/purrfecto114-lgtm/ShangBackground", "xxdz-Official/ShangBackground")
                + f'<br><span style="color:{fg}">GitHub反馈 / 统一更新源：</span>'
                + anchor("https://github.com/purrfecto114-lgtm/ShangBackground", "purrfecto114-lgtm/ShangBackground")
                + f'<br><span style="color:{fg}">作者主页：</span>'
                + anchor("https://space.bilibili.com/3461569935575626?spm_id_from=333.788", "b站@小小电子xxdz")
                + '<br>'
                + anchor("app://shishe", "[施舍]")
                + '　' + anchor("app://about-window", "关于图片")
                + '　' + anchor("app://about-dialog", "关于窗口")
            )
        links.setText(_build_links_html(_fg, _lnk))
        self._about_links_label = links
        self._about_links_html_fn = _build_links_html
        layout.addWidget(links)

        # 可复制的、隐私友好的运行环境摘要。外部程序版本只在用户点击刷新时探测，
        # 避免打开“关于”页时产生同步子进程和无意义延迟。
        sysinfo_box = QGroupBox(t("系统信息"))
        sysinfo_layout = QVBoxLayout(sysinfo_box)
        sysinfo_layout.setContentsMargins(10, 18, 10, 10)
        sysinfo_layout.setSpacing(6)
        self._sysinfo_text = QTextEdit()
        self._sysinfo_text.setReadOnly(True)
        self._sysinfo_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._sysinfo_text.setMaximumHeight(250)
        self._sysinfo_text.setPlainText(self._build_system_info_text())
        self._sysinfo_text.setStyleSheet("font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 12px;")
        sysinfo_layout.addWidget(self._sysinfo_text)
        sysinfo_actions = QHBoxLayout()
        refresh_sysinfo_btn = QPushButton(t("刷新系统信息"))
        refresh_sysinfo_btn.setProperty("secondary", True)
        refresh_sysinfo_btn.clicked.connect(self._refresh_system_info)
        copy_sysinfo_btn = QPushButton(t("复制系统信息"))
        copy_sysinfo_btn.setProperty("secondary", True)
        copy_sysinfo_btn.clicked.connect(self._copy_system_info)
        sysinfo_actions.addWidget(refresh_sysinfo_btn)
        sysinfo_actions.addWidget(copy_sysinfo_btn)
        sysinfo_actions.addStretch(1)
        sysinfo_layout.addLayout(sysinfo_actions)
        layout.addWidget(sysinfo_box)

        layout.addStretch(1)
        return page

    def _build_system_info_text(self, *, probe_external: bool = False) -> str:
        """Build a compact, privacy-aware diagnostic snapshot."""
        try:
            extra: list[str] = []
            screens = QApplication.screens()
            extra.append(f"Displays{' ' * 8} : {len(screens)}")
            for index, screen in enumerate(screens, start=1):
                geometry = screen.geometry()
                extra.append(
                    f"  Display {index:<2}     : {screen.name() or '-'} | "
                    f"{geometry.width()}x{geometry.height()} @ "
                    f"{geometry.x()},{geometry.y()} | DPR {screen.devicePixelRatio():.2f}"
                )
            return render_system_info(
                collect_system_info(core.config, probe_external=probe_external),
                extra_lines=tuple(extra),
            )
        except Exception as exc:
            try:
                core.log(f"构建系统信息失败: {exc}", level="WARNING")
            except Exception:
                pass
            return f"{APP_DISPLAY_NAME} {APP_VERSION}"

    def _refresh_system_info(self) -> None:
        try:
            text = self._build_system_info_text(probe_external=True)
            if hasattr(self, "_sysinfo_text"):
                self._sysinfo_text.setPlainText(text)
            self.set_status(t("系统信息已刷新"))
        except Exception as exc:
            self.set_status(t("刷新系统信息失败") + f": {exc}")

    def _copy_system_info(self) -> None:
        try:
            text = (
                self._sysinfo_text.toPlainText()
                if hasattr(self, "_sysinfo_text")
                else self._build_system_info_text()
            )
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
                self.set_status(t("系统信息已复制到剪贴板"))
            else:
                self.set_status(t("剪贴板不可用"))
        except Exception as exc:
            self.set_status(t("复制系统信息失败") + f": {exc}")

    def _log_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        path_box = QGroupBox(t("日志设置"))
        path_grid = QGridLayout(path_box)
        path_grid.setHorizontalSpacing(10)
        path_grid.setVerticalSpacing(10)
        # Stretch the first column so the log path edit expands and aligns nicely with the button.
        path_grid.setColumnStretch(0, 1)
        path_grid.setColumnStretch(1, 0)
        self.log_enabled_check = QCheckBox(t("记录日志到文件（默认关闭）"))
        self.log_enabled_check.setChecked(bool(core.config.get("log_enabled", False)))
        self.log_enabled_check.toggled.connect(self.on_log_enabled_changed)
        self.log_path_edit = QLineEdit(core.config.get("log_file_path", "") or "")
        self.log_path_edit.setReadOnly(True)
        configure_text_input(
            self.log_path_edit,
            name=t("日志文件路径"),
            description=t("日志开启后写入的文件位置；使用右侧按钮更改。"),
            object_name="LogFilePathEdit",
            placeholder=t("首次开启日志时请选择保存路径"),
            clear_button=False,
        )
        btn_choose_log = QPushButton(t("选择日志路径"))
        btn_choose_log.setProperty("secondary", True)
        btn_choose_log.clicked.connect(self.choose_log_file_path)
        path_grid.addWidget(self.log_enabled_check, 0, 0, 1, 2)
        path_grid.addWidget(self.log_path_edit, 1, 0)
        path_grid.addWidget(btn_choose_log, 1, 1)
        layout.addWidget(path_box)

        # ----- 实时日志查看器（基于内存环形缓冲区）-----
        # 历史实现只显示用户选定的 log_file_path 文件内容，但 Qt 内部警告
        # (qt.svg: Cannot open file ...) 走标准 logging，文件可能为空。
        # 新版改为从 app.log_setup.get_recent_logs() 实时拉取，自动刷新，
        # 支持级别过滤、关键字搜索和颜色分级显示。
        viewer_box = QGroupBox(t("实时日志（内存缓冲）"))
        viewer_layout = QVBoxLayout(viewer_box)
        viewer_layout.setContentsMargins(10, 18, 10, 10)
        viewer_layout.setSpacing(8)

        # 过滤栏拆成两行，避免在全局设置窄窗口中横向溢出。
        filter_row_top = QHBoxLayout()
        filter_row_top.setSpacing(8)
        filter_row_top.addWidget(QLabel(t("级别：")))
        self.log_level_filter = ShangComboBox()
        self.log_level_filter.addItem(t("全部"), "")
        self.log_level_filter.addItem(t("DEBUG"), "DEBUG")
        self.log_level_filter.addItem(t("INFO"), "INFO")
        self.log_level_filter.addItem(t("WARNING"), "WARNING")
        self.log_level_filter.addItem(t("ERROR"), "ERROR")
        self.log_level_filter.addItem(t("CRITICAL"), "CRITICAL")
        self.log_level_filter.setCurrentIndex(2)
        self.log_level_filter.currentIndexChanged.connect(self._refresh_log_viewer)
        self._prepare_combo_popup(self.log_level_filter)
        self.log_level_filter.setMinimumWidth(120)
        filter_row_top.addWidget(self.log_level_filter)

        filter_row_top.addWidget(QLabel(t("搜索：")))
        self.log_search_edit = QLineEdit()
        self.log_search_edit.setPlaceholderText(t("输入关键字过滤（不区分大小写）"))
        self.log_search_edit.setClearButtonEnabled(True)
        self.log_search_edit.textChanged.connect(self._refresh_log_viewer)
        filter_row_top.addWidget(self.log_search_edit, 1)
        viewer_layout.addLayout(filter_row_top)

        filter_row_actions = QHBoxLayout()
        filter_row_actions.setSpacing(8)
        self.log_auto_refresh_check = QCheckBox(t("自动刷新"))
        self.log_auto_refresh_check.setChecked(False)
        self.log_auto_refresh_check.toggled.connect(self._on_log_auto_refresh_toggled)
        filter_row_actions.addWidget(self.log_auto_refresh_check)

        btn_refresh_now = QPushButton(t("刷新"))
        btn_refresh_now.setProperty("secondary", True)
        btn_refresh_now.clicked.connect(self._refresh_log_viewer)
        filter_row_actions.addWidget(btn_refresh_now)

        btn_clear_view = QPushButton(t("清空缓冲区"))
        btn_clear_view.setProperty("secondary", True)
        btn_clear_view.setToolTip(t("清空内存中的实时日志显示（不影响日志文件）"))
        btn_clear_view.clicked.connect(self._clear_log_viewer)
        filter_row_actions.addWidget(btn_clear_view)

        btn_copy_log = QPushButton(t("复制日志"))
        btn_copy_log.setProperty("secondary", True)
        btn_copy_log.setToolTip(t("把当前显示的日志复制到剪贴板（纯文本格式）"))
        btn_copy_log.clicked.connect(self._copy_log_to_clipboard)
        filter_row_actions.addWidget(btn_copy_log)
        filter_row_actions.addStretch(1)
        viewer_layout.addLayout(filter_row_actions)

        # 日志显示区：用 QTextEdit + HTML 实现颜色分级
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.log_box.setMinimumHeight(220)
        self.log_box.setMaximumHeight(340)
        # 等宽字体 + 主题色板：跟随浅色/深色模式和边框色。
        self._apply_log_viewer_theme()
        viewer_layout.addWidget(self.log_box, 1)

        # 状态行：显示条目数 + 最新一条时间戳
        self.log_status_label = QLabel("")
        self.log_status_label.setProperty("muted", True)
        viewer_layout.addWidget(self.log_status_label)

        layout.addWidget(viewer_box, 1)

        # 保留文件操作按钮（导出/删除）供用户使用
        file_ops_row = QHBoxLayout()
        btn_load = QPushButton(t("从文件加载"))
        btn_load.setProperty("secondary", True)
        btn_load.setToolTip(t("读取日志文件并临时显示（期间自动刷新暂停，下次自动刷新恢复）"))
        btn_load.clicked.connect(self.load_log_file)
        btn_delete = QPushButton(t("删除日志文件"))
        btn_delete.setProperty("secondary", True)
        btn_delete.setToolTip(t("删除上面选定的日志文件（不影响内存实时日志）"))
        btn_delete.clicked.connect(self.delete_log_file)
        btn_export = QPushButton(t("导出实时日志"))
        btn_export.setProperty("secondary", True)
        btn_export.setToolTip(t("把当前内存中的实时日志导出为文件"))
        btn_export.clicked.connect(self.export_live_log)
        for w in (btn_load, btn_delete, btn_export):
            w.setMaximumWidth(160)
            file_ops_row.addWidget(w)
        file_ops_row.addStretch(1)
        layout.addLayout(file_ops_row)

        # 自动刷新定时器：每 1.5 秒拉一次最新日志
        self._log_refresh_timer = QTimer(self)
        self._log_refresh_timer.setInterval(1500)
        self._log_refresh_timer.timeout.connect(self._refresh_log_viewer)
        if self.log_auto_refresh_check.isChecked():
            self._log_refresh_timer.start()

        # 首次刷新
        self._refresh_log_viewer()
        return page

    def _log_viewer_palette(self) -> dict[str, str]:
        colors = self._theme_role_colors()
        dark = self._theme_is_dark()
        return {
            "bg": colors["bg_input"],
            "fg": colors["fg_primary"],
            "muted": colors["fg_muted"],
            "secondary": colors["fg_secondary"],
            "border": colors["border"],
            "row_alt": "#222235" if dark else "#f6f8fa",
            "debug": colors["fg_muted"],
            "info": colors["fg_primary"],
            "warning": "#f0a020" if dark else "#9a6700",
            "error": "#ff8a8a" if dark else "#cf222e",
            "critical": "#f0abfc" if dark else "#8250df",
            "warning_bg": "#3b2f10" if dark else "#fff8c5",
            "error_bg": "#3b1010" if dark else "#fff1f1",
            "critical_bg": "#3d0a3d" if dark else "#fbefff",
        }

    def _apply_log_viewer_theme(self) -> None:
        if not hasattr(self, "log_box"):
            return
        p = self._log_viewer_palette()
        self.log_box.setStyleSheet(
            "QTextEdit {"
            f"  background-color: {p['bg']};"
            f"  color: {p['fg']};"
            "  font-family: 'Cascadia Code', 'Consolas', 'Menlo', 'Microsoft YaHei UI', monospace;"
            "  font-size: 12px;"
            f"  border: 1px solid {p['border']};"
            "  border-radius: 6px;"
            "  padding: 6px;"
            "}"
        )

    def _log_level_to_color(self, level: str) -> str:
        """Map a log level name to a CSS color for HTML rendering."""
        p = self._log_viewer_palette()
        mapping = {
            "DEBUG": p["debug"],
            "INFO": p["info"],
            "WARNING": p["warning"],
            "ERROR": p["error"],
            "CRITICAL": p["critical"],
        }
        return mapping.get(level, p["info"])

    def _log_level_to_bg_color(self, level: str) -> str:
        """Optional row background tint for WARNING/ERROR/CRITICAL entries."""
        p = self._log_viewer_palette()
        mapping = {
            "WARNING": p["warning_bg"],
            "ERROR": p["error_bg"],
            "CRITICAL": p["critical_bg"],
        }
        return mapping.get(level, "transparent")

    def _refresh_log_viewer(self):
        """Pull latest entries from the in-memory ring buffer and render as HTML."""
        if not hasattr(self, "log_box"):
            return
        try:
            from app.log_setup import get_recent_logs
        except Exception:
            return
        try:
            min_level = self.log_level_filter.currentData() or None
        except Exception:
            min_level = None
        try:
            search = self.log_search_edit.text().strip() or None
        except Exception:
            search = None
        try:
            entries = get_recent_logs(limit=500, min_level=min_level, search=search)
        except Exception:
            entries = []
        # 构建 HTML：每条日志一行，级别带颜色，ERROR/CRITICAL 行带背景色
        palette = self._log_viewer_palette()
        html_parts = [
            f"<html><body style='margin:0;padding:0;font-family:Consolas,monospace;font-size:12px;background:{palette['bg']};color:{palette['fg']};'>"
        ]
        for entry in entries:
            level = str(entry.get("level", "INFO"))
            color = self._log_level_to_color(level)
            bg = self._log_level_to_bg_color(level)
            ts = str(entry.get("timestamp", ""))
            logger = str(entry.get("logger", ""))
            msg = str(entry.get("message", ""))
            # HTML 转义防止消息里的 < > & 破坏渲染
            import html as _html
            ts_e = _html.escape(ts)
            logger_e = _html.escape(logger)
            msg_e = _html.escape(msg)
            level_e = _html.escape(level)
            tb = entry.get("traceback")
            tb_html = ""
            if tb:
                tb_e = _html.escape(str(tb))
                tb_html = f"<pre style='margin:4px 0 0 16px;color:{palette['error']};font-size:11px;white-space:pre-wrap;'>{tb_e}</pre>"
            html_parts.append(
                f"<div style='padding:2px 4px;background:{bg};border-left:3px solid {color};'>"
                f"<span style='color:{palette['muted']};'>{ts_e}</span> "
                f"<span style='color:{color};font-weight:bold;'>[{level_e}]</span> "
                f"<span style='color:{palette['secondary']};'>[{logger_e}]</span> "
                f"<span style='color:{color};'>{msg_e}</span>"
                f"{tb_html}"
                f"</div>"
            )
        html_parts.append("</body></html>")
        # 记录当前滚动位置，刷新后恢复到底部（如果用户原本就在底部）
        was_at_bottom = False
        try:
            sb = self.log_box.verticalScrollBar()
            was_at_bottom = sb.value() >= sb.maximum() - 4
        except Exception:
            pass
        self.log_box.setHtml("".join(html_parts))
        if was_at_bottom:
            try:
                sb = self.log_box.verticalScrollBar()
                sb.setValue(sb.maximum())
            except Exception:
                pass
        # 更新状态行
        try:
            count = len(entries)
            latest_ts = entries[-1].get("timestamp", "") if entries else ""
            self.log_status_label.setText(
                t("共 {0} 条日志，最新：{1}").format(count, latest_ts)
                if entries else t("暂无日志")
            )
        except Exception:
            pass

    def _clear_log_viewer(self):
        """清空内存环形缓冲区 + 界面显示。"""
        try:
            from app.log_setup import clear_recent_logs
            clear_recent_logs()
        except Exception:
            pass
        if hasattr(self, "log_box"):
            self.log_box.clear()
        if hasattr(self, "log_status_label"):
            self.log_status_label.setText(t("已清空日志显示"))

    def _copy_log_to_clipboard(self) -> None:
        """v1.4.7: 把当前内存日志复制到剪贴板 (纯文本格式), 方便用户报告 bug."""
        try:
            from app.log_setup import get_recent_logs
            level_filter = ""
            search = ""
            if hasattr(self, "log_level_filter") and self._is_qobject_alive(self.log_level_filter):
                level_filter = self.log_level_filter.currentData() or ""
            if hasattr(self, "log_search_edit") and self._is_qobject_alive(self.log_search_edit):
                search = self.log_search_edit.text().strip()
            entries = get_recent_logs(limit=2000, min_level=level_filter, search=search)
            if not entries:
                self.set_status(t("没有日志可复制"))
                return
            lines = []
            for entry in entries:
                ts = entry.get("timestamp", "")
                level = entry.get("level", "")
                logger = entry.get("logger", "")
                msg = entry.get("message", "")
                line = f"[{ts}] [{level}] [{logger}] {msg}"
                tb = entry.get("traceback")
                if tb:
                    line += "\n" + tb
                lines.append(line)
            text = "\n".join(lines)
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
                self.set_status(t("已复制") + f" {len(lines)} " + t("条日志到剪贴板"))
            else:
                self.set_status(t("剪贴板不可用"))
        except Exception as exc:
            try:
                core.log(f"复制日志失败: {exc}", level="WARNING", exc_info=True)
            except Exception:
                pass
            self.set_status(t("复制日志失败") + f": {exc}")

    def _on_log_auto_refresh_toggled(self, checked: bool):
        timer = getattr(self, "_log_refresh_timer", None)
        if timer is None:
            return
        if checked:
            timer.start()
        else:
            timer.stop()

    def export_live_log(self):
        """导出当前实时日志（应用过滤条件）到用户选择的文件。"""
        try:
            from app.log_setup import get_recent_logs
        except Exception:
            return
        try:
            min_level = self.log_level_filter.currentData() or None
        except Exception:
            min_level = None
        try:
            search = self.log_search_edit.text().strip() or None
        except Exception:
            search = None
        entries = get_recent_logs(limit=10000, min_level=min_level, search=search)
        default = self._default_log_path()
        dest, _ = QFileDialog.getSaveFileName(
            self, t("导出实时日志"), default, t("日志文件 (*.log *.txt);;所有文件 (*.*)")
        )
        if not dest:
            return
        try:
            lines = []
            for entry in entries:
                ts = entry.get("timestamp", "")
                level = entry.get("level", "INFO")
                logger = entry.get("logger", "")
                msg = entry.get("message", "")
                line = f"[{ts}] [{level}] [{logger}] {msg}"
                tb = entry.get("traceback")
                if tb:
                    line += "\n" + str(tb)
                lines.append(line)
            with open(dest, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.set_status(t("已导出 {0} 条日志到：{1}").format(len(entries), dest))
        except Exception as e:
            QMessageBox.warning(self, t("导出日志"), t("导出失败：") + str(e))

    def _setup_about_sprite_animation(self):
        """about.png 是三态竖排精灵图，提供普通、悬停、按下三态动画。"""
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
                    frame = pix.copy(0, i * frame_h, pix.width(), frame_h).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self._about_frames.append(frame)
        if not self._about_frames:
            self.about_sprite_btn.setText(t("关于"))
            return
        self._about_state = 0
        self._about_anim_step = 0
        self._about_anim_from = self._about_frames[0]
        self._about_anim_to = self._about_frames[0]
        self.about_sprite_btn.setIcon(QIcon(self._about_frames[0]))
        self.about_sprite_btn.setIconSize(QSize(64, 64))
        self._about_anim_timer = QTimer(self)
        self._about_anim_timer.setInterval(24)
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
        # Bug 9 同期: 当用户在全局设置中关闭了界面动画时, 跳过 14 帧的
        # 交叉淡入, 直接切换到目标帧. 这避免了 ~336 ms 的动画延迟
        # (14 帧 × 24 ms), 让"关于"按钮的悬停/按下反馈即时呈现.
        if not self._animations_enabled():
            self._about_anim_timer.stop()
            self._about_state = state
            self._about_target_state = state
            self.about_sprite_btn.setIcon(QIcon(self._about_frames[state]))
            return
        self._about_anim_timer.stop()
        self._about_anim_from = self._about_frames[getattr(self, "_about_state", 0)]
        self._about_anim_to = self._about_frames[state]
        self._about_target_state = state
        self._about_anim_step = 0
        self._about_anim_timer.start()

    def _advance_about_crossfade(self):
        steps = 14
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
        # v1.4.7: 移除 PgUp/PgDown eventFilter 拦截块 (原为应用内热键让路,
        # 现已删除应用内热键, QScrollArea 默认翻页行为恢复).
        return super().eventFilter(obj, event)

    def _app_command(self, *args: str) -> list[str]:
        """源码运行和 PyInstaller onedir 运行都能打开同一个入口。"""
        if core.is_frozen():
            return [app_executable_path(), *args]
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
        dest, _ = QFileDialog.getSaveFileName(self, t("选择日志保存路径"), default, t("日志文件 (*.log *.txt);;所有文件 (*.*)"))
        if not dest:
            return False
        core.config["log_file_path"] = dest
        core.save_config()
        if hasattr(self, "log_path_edit"):
            self.log_path_edit.setText(dest)
        self.set_status(t("日志路径已设置：") + f"{dest}")
        return True

    def on_log_enabled_changed(self, checked: bool):
        if checked and not self._log_file_path():
            if not self.choose_log_file_path():
                self.log_enabled_check.blockSignals(True)
                self.log_enabled_check.setChecked(False)
                self.log_enabled_check.blockSignals(False)
                core.config["log_enabled"] = False
                core.save_config()
                # Bug 6 fix: ensure file handlers are detached if user cancels
                # the path picker while the checkbox was momentarily checked.
                try:
                    from app.log_setup import set_file_logging_enabled
                    set_file_logging_enabled(False)
                except Exception:
                    pass
                self.set_status(t("已取消开启日志"))
                return
        core.config["log_enabled"] = bool(checked)
        core.save_config()
        # Bug 6 fix: toggle the three rotating file handlers at runtime so
        # the change takes effect immediately without restarting the app.
        # Without this, ``core.log()`` calls would still go through the
        # standard logging root → file handlers → leak entries to disk even
        # when ``log_enabled`` is False.
        try:
            from app.log_setup import set_file_logging_enabled
            set_file_logging_enabled(bool(checked))
        except Exception as exc:
            try:
                core.log(f"切换文件日志记录失败: {exc}")
            except Exception:
                pass
        try:
            core.log(t("日志文件记录已开启") if checked else t("日志文件记录已关闭"))
        except Exception:
            pass
        self.set_status(t("日志文件记录已开启") if checked else t("日志文件记录已关闭"))
        self.load_log_file()

    def load_log_file(self):
        if not hasattr(self, "log_box"):
            return
        path = self._log_file_path()
        self.log_box.clear()
        if hasattr(self, "log_path_edit"):
            self.log_path_edit.setText(path)
        if not core.config.get("log_enabled", False) and not path:
            self.log_box.setPlainText(t("日志默认关闭。需要记录文件日志时，请先开启日志并选择保存路径。"))
            return
        if not path:
            self.log_box.setPlainText(t("尚未设置日志路径。"))
            return
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    self.log_box.setPlainText(f.read()[-120000:])
            except Exception as e:
                self.log_box.setPlainText(t("读取日志失败：") + str(e))
        else:
            self.log_box.setPlainText(t("暂无日志文件。开启日志后，新日志会写入所选路径。"))

    def delete_log_file(self):
        path = self._log_file_path()
        if not path:
            QMessageBox.information(self, t("日志"), t("尚未设置日志路径。"))
            return
        try:
            if os.path.exists(path):
                os.remove(path)
            if hasattr(self, "log_box"):
                self.log_box.clear()
            self.set_status(t("日志文件已删除"))
        except Exception as e:
            QMessageBox.warning(self, t("日志"), t("删除日志失败：") + str(e))

    def open_local_image(self, name: str):
        path = self._img_path(name)
        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.warning(self, t("资源缺失"), t("找不到图片：") + str(name))

    def open_shishe_image(self):
        self.open_local_image("shishe.png")

    def refresh_from_config(self):
        normalize_runtime_config_in_place(core.config)
        cfg = core.config
        signal_widgets = [
            self.mode_combo,
            self.fit_combo,
            self.seconds_spin,
            self.shuffle_check,
            self.angle_spin,
            getattr(self, "video_muted_check", None),
            getattr(self, "video_volume_slider", None),
            getattr(self, "tray_action", None),
            getattr(self, "bing_count_spin", None),
            getattr(self, "html_auto_pause_check", None),
            getattr(self, "html_frame_rate_combo", None),
            getattr(self, "global_hotkeys_enabled_check", None),
            getattr(self, "hotkey_focus_guard_check", None),
            getattr(self, "animations_check", None),
            getattr(self, "wallpaper_transition_check", None),
            getattr(self, "perf_mode_combo", None),
        ]
        previous_states = []
        self._refreshing_from_config = True
        for widget in signal_widgets:
            if widget is not None:
                try:
                    previous_states.append((widget, widget.blockSignals(True)))
                except Exception:
                    pass
        try:
            self._set_combo_current_data(self.mode_combo, normalize_mode_key(cfg.get("mode", "幻灯片放映")))
            self._set_combo_current_data(self.fit_combo, normalize_style_key(cfg.get("fit_mode", "填充")))
            self.folder_edit.setText(cfg.get("slide_folder", ""))
            self.seconds_spin.setValue(int(cfg.get("slide_seconds", 300)))
            self.shuffle_check.setChecked(bool(cfg.get("shuffle", False)))
            self.single_edit.setText(cfg.get("single_image", ""))
            if hasattr(self, "video_edit"):
                self.video_edit.setText(cfg.get("video_file", ""))
            if hasattr(self, "video_muted_check"):
                self.video_muted_check.setChecked(bool(cfg.get("video_muted", True)))
            if hasattr(self, "video_volume_slider"):
                try:
                    _vol = int(cfg.get("video_volume", 100))
                except (TypeError, ValueError):
                    _vol = 100
                _vol = max(0, min(100, _vol))
                self.video_volume_slider.setValue(_vol)
                if hasattr(self, "video_volume_value_label"):
                    self.video_volume_value_label.setText(f"{_vol}%")
            # 统一刷新音量滑块启用状态。
            # （依据 video_file / video_muted / mode 三个字段综合判断）
            self._refresh_video_volume_controls()
            self.angle_spin.setValue(int(cfg.get("gradient_angle", 60)))
            self._paint_button(self.solid_btn, cfg.get("solid_color", "#ffffff"))
            self._paint_button(self.grad1_btn, cfg.get("solid_color", "#ffffff"))
            self._paint_button(self.grad2_btn, cfg.get("gradient_color2", "#ffffff"))
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
            if hasattr(self, "ctx_shortcut_current_labels"):
                self._refresh_context_shortcut_labels()

            settings_widgets = ("bg_check", "auto_start_check", "silent_update_check_on_startup_check", "tray_check", "tray_notify_check")
            if all(hasattr(self, name) for name in settings_widgets):
                widgets = tuple(getattr(self, name) for name in settings_widgets)
                for widget in widgets:
                    widget.blockSignals(True)
                self.bg_check.setChecked(bool(cfg.get("run_in_background", True)))
                self.auto_start_check.setChecked(bool(cfg.get("auto_start", False)))
                self.silent_update_check_on_startup_check.setChecked(bool(cfg.get("silent_update_check_on_startup", True)))
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
                wanted_action = cfg.get("tray_click_action", "next")
                idx = self.tray_action.findData(wanted_action)
                fallback = self.tray_action.findData("next")
                self.tray_action.setCurrentIndex(idx if idx >= 0 else fallback)
            if hasattr(self, "bing_cache_edit"):
                self.bing_cache_edit.setText(cfg.get("bing_cache_dir", "") or "")
            if hasattr(self, "bing_count_spin"):
                self.bing_count_spin.setValue(int(cfg.get("bing_sync_count", 1)))
            if hasattr(self, "font_path_edit"):
                self.font_path_edit.setText(cfg.get("font_path", "") or "")
            # v1.4.7: 字体粗细和大小
            if hasattr(self, "font_weight_combo") and self._is_qobject_alive(getattr(self, "font_weight_combo", None)):
                self.font_weight_combo.blockSignals(True)
                _fw_r = str(cfg.get("font_weight", "normal")).lower()
                _fw_ridx = self.font_weight_combo.findData(_fw_r)
                if _fw_ridx >= 0:
                    self.font_weight_combo.setCurrentIndex(_fw_ridx)
                self.font_weight_combo.blockSignals(False)
            if hasattr(self, "font_size_spin") and self._is_qobject_alive(getattr(self, "font_size_spin", None)):
                self.font_size_spin.blockSignals(True)
                try:
                    self.font_size_spin.setValue(int(cfg.get("font_size", 0)))
                except Exception:
                    self.font_size_spin.setValue(0)
                self.font_size_spin.blockSignals(False)
            if hasattr(self, "theme_color_edit"):
                self.theme_color_edit.setText(getattr(self, "_theme_color", cfg.get("theme_color", DEFAULT_THEME_COLOR)))
                self._update_theme_color_preview()
            if hasattr(self, "dpi_scale_slider"):
                self.dpi_scale_slider.blockSignals(True)
                self.dpi_scale_slider.setValue(dpi_percent(cfg.get("dpi_scale", 1.0)))
                self.dpi_scale_slider.blockSignals(False)
                if hasattr(self, "dpi_scale_value_label"):
                    self.dpi_scale_value_label.setText(f"{self.dpi_scale_slider.value()}%")
            if hasattr(self, "dark_mode_check"):
                self.dark_mode_check.setChecked(bool(cfg.get("dark_mode", False)))
            if hasattr(self, "animations_check") and self._is_qobject_alive(getattr(self, "animations_check", None)):
                self.animations_check.setChecked(bool(cfg.get("enable_animations", True)))
            if hasattr(self, "wallpaper_transition_check") and self._is_qobject_alive(getattr(self, "wallpaper_transition_check", None)):
                self.wallpaper_transition_check.setChecked(bool(cfg.get("wallpaper_transition_enabled", True)))
            # v1.4.6: 性能模式从复选框改为三档下拉
            if hasattr(self, "global_hotkeys_enabled_check") and self._is_qobject_alive(getattr(self, "global_hotkeys_enabled_check", None)):
                self.global_hotkeys_enabled_check.setChecked(bool(cfg.get("global_hotkeys_enabled", False)))
            if hasattr(self, "hotkey_focus_guard_check") and self._is_qobject_alive(getattr(self, "hotkey_focus_guard_check", None)):
                self.hotkey_focus_guard_check.setChecked(bool(cfg.get("hotkey_focus_guard", True)))
            if hasattr(self, "perf_mode_combo") and self._is_qobject_alive(getattr(self, "perf_mode_combo", None)):
                _pl = self._perf_level()
                _pidx = self.perf_mode_combo.findData(_pl)
                if _pidx >= 0:
                    self.perf_mode_combo.setCurrentIndex(_pidx)
            # 刷新 HTML 输入框内容与 Windows 端同名选项
            if hasattr(self, "html_edit"):
                self.html_edit.setText(cfg.get("html_file", cfg.get("html_url", "")) or "")
            if hasattr(self, "html_auto_pause_check"):
                self.html_auto_pause_check.setChecked(bool(cfg.get("html_auto_pause", True)))
            if hasattr(self, "html_frame_rate_combo"):
                frame_rate = int(cfg.get("html_frame_rate", 30) or 0)
                frame_index = self.html_frame_rate_combo.findData(frame_rate)
                self.html_frame_rate_combo.setCurrentIndex(frame_index if frame_index >= 0 else self.html_frame_rate_combo.findData(30))
            self.update_control_states()
        finally:
            for widget, previous in reversed(previous_states):
                try:
                    widget.blockSignals(previous)
                except Exception:
                    pass
            self._refreshing_from_config = False
            # v1.4.7: 应用内热键已移除, 不再刷新 _refresh_app_shortcuts.

    def update_control_states(self):
        mode = normalize_mode_key(core.config.get("mode", self.mode_combo.currentData() if self._is_qobject_alive(self.mode_combo) else "幻灯片放映"))
        is_slide = mode == "幻灯片放映"
        is_image = mode == "图片"
        is_video = mode == "视频"
        is_solid = mode == "纯色"
        is_gradient = mode == "渐变"
        is_html = mode == "HTML"

        for w in (self.folder_edit, self.btn_browse_folder, self.seconds_spin, self.shuffle_check,
                  self.btn_prev, self.btn_next, self.btn_random, self.btn_random_prob, self.btn_start, self.btn_stop):
            w.setEnabled(is_slide)
        self.single_edit.setEnabled(is_image)
        self.btn_single.setEnabled(is_image)
        if hasattr(self, "video_box"):
            self.video_box.setEnabled(is_video)
            self.video_edit.setEnabled(is_video)
            self.video_browse_btn.setEnabled(is_video)
            self.video_start_btn.setEnabled(is_video)
            self.video_stop_btn.setEnabled(is_video)
            self.video_muted_check.setEnabled(is_video)
            # 音量滑块启用条件由 _refresh_video_volume_controls 统一管理：
            # (视频模式 OR 已选视频文件) AND 未静音
            self._refresh_video_volume_controls()
            if hasattr(self, "video_volume_value_label"):
                self.video_volume_value_label.setEnabled(is_video)
        self.solid_btn.setEnabled(is_solid)
        self.grad1_btn.setEnabled(is_gradient)
        self.grad2_btn.setEnabled(is_gradient)
        self.angle_spin.setEnabled(is_gradient)
        self.angle_apply_btn.setEnabled(is_gradient)

        # HTML 控件启用/禁用
        #
        # 配置类控件（路径输入框、浏览按钮、自动暂停/鼠标穿透复选框）
        # 始终保持可用，这样用户可以在切换到 HTML 模式之前就先把 HTML 文件
        # 或 URL、以及运行选项配置好——否则用户会以为这些控件"不起作用"。
        # 仅运行按钮跟随当前模式：应用/刷新按钮需要 HTML 模式；停止按钮
        # 在壁纸实际运行时才可用。
        html_running = False
        try:
            html_running = core.is_html_wallpaper_running()
        except Exception:
            pass
        if hasattr(self, "html_edit"):
            self.html_edit.setEnabled(True)
        if hasattr(self, "html_browse_btn"):
            self.html_browse_btn.setEnabled(True)
        if hasattr(self, "html_auto_pause_check"):
            self.html_auto_pause_check.setEnabled(True)
        if hasattr(self, "html_frame_rate_combo"):
            self.html_frame_rate_combo.setEnabled(True)
        if hasattr(self, "html_start_btn"):
            self.html_start_btn.setEnabled(is_html)
        if hasattr(self, "html_stop_btn"):
            self.html_stop_btn.setEnabled(is_html and html_running)
        if hasattr(self, "html_box"):
            self.html_box.setEnabled(True)

        self.slide_box.setEnabled(is_slide)
        self.single_box.setEnabled(is_image)
        if hasattr(self, "video_box"):
            self.video_box.setEnabled(is_video)
        # 色彩区域保持可见，只把当前模式不可用的按钮置灰，避免用户误以为配置丢失。
        self.color_box.setEnabled(True)
        self._refresh_color_buttons()

    def _core_operation_label(self, fn) -> str:
        name = getattr(fn, "__name__", "")
        labels = {
            "previous_wallpaper": t("上一张壁纸"),
            "next_wallpaper": t("下一张壁纸"),
            "random_wallpaper": t("随机壁纸"),
            "set_wallpaper": t("设置壁纸"),
            "set_wallpaper_direct": t("设置壁纸"),
            "start_slideshow": t("启动幻灯片放映"),
            "stop_slideshow": t("停止幻灯片放映"),
            "restart_slideshow": t("重启幻灯片放映"),
            "set_fit_mode": t("应用适应方式"),
            "apply_solid": t("应用纯色壁纸"),
            "start_video_wallpaper": t("启动视频壁纸"),
            "stop_video_wallpaper": t("停止视频壁纸"),
            "restore_session_original_wallpaper": t("恢复启动前壁纸"),
            "save_config": t("保存配置"),
        }
        return labels.get(name, t("操作"))

    def _run_core_sync(self, fn, *args):
        label = self._core_operation_label(fn)
        self.begin_operation(f"{t('正在执行')}：{label}")
        try:
            result = fn(*args)
            core.save_config()
            self._schedule_preview_refresh()
            self.finish_operation(t("操作完成"))
            return result
        except Exception as e:
            # 把异常原因带回状态栏，避免用户只看到"操作失败"
            self.finish_operation(t("操作失败") + f"：{e}")
            self._show_non_modal_warning(t("错误"), str(e))
            core.log_error("PySide6 操作失败", e)
            return None

    def run_core(self, fn, *args):
        """Run slow wallpaper operations off the GUI thread to keep PySide responsive."""
        name = getattr(fn, "__name__", t("操作"))
        label = self._core_operation_label(fn)
        async_safe = {"previous_wallpaper", "next_wallpaper", "random_wallpaper", "set_wallpaper", "set_wallpaper_direct", "start_slideshow", "stop_slideshow", "restart_slideshow", "set_fit_mode", "apply_solid", "start_video_wallpaper", "stop_video_wallpaper", "restore_session_original_wallpaper"}
        if name not in async_safe:
            return self._run_core_sync(fn, *args)
        if self._core_busy:
            if name in {"next_wallpaper", "random_wallpaper"}:
                try:
                    self._pending_core_actions.clear()
                except Exception:
                    pass
            self._pending_core_actions.append((fn, args))
            self.set_status(t("已有壁纸操作正在执行，已保留最新请求…"))
            return None
        self._core_busy = True
        try:
            core.clear_cancel_operations()
        except Exception:
            pass
        self.begin_operation(f"{t('正在执行')}：{label}", cancellable=True)

        def _worker():
            try:
                if core.is_operation_cancelled():
                    self.core_result_signal.emit(False, t("操作已终止"), None)
                    return
                result = fn(*args)
                core.save_config()
                if result is False:
                    # fn 返回 False 而非抛异常时，core.log 已记录原因；
                    # 通过 core.last_operation_error 把原因带回 GUI，避免用户
                    # 只看到"操作失败"通用提示
                    reason = getattr(core, "last_operation_error", "") or ""
                    msg = t("操作失败") + (f"：{reason}" if reason else "")
                    self.core_result_signal.emit(False, msg, result)
                    return
                if core.is_operation_cancelled():
                    self.core_result_signal.emit(False, t("操作已终止"), result)
                else:
                    self.core_result_signal.emit(True, t("操作完成"), result)
            except Exception as exc:
                core.log_error("后台壁纸操作失败", exc)
                self.core_result_signal.emit(False, str(exc), None)
            finally:
                try:
                    core.clear_cancel_operations()
                except Exception:
                    pass

        self._core_worker_thread = threading.Thread(target=_worker, daemon=True)
        self._core_worker_thread.start()
        return None

    def _on_core_finished(self, ok: bool, message: str, _result):
        self._core_busy = False
        self._core_worker_thread = None
        self._schedule_preview_refresh()
        # 壁纸启停后刷新控件状态——尤其是 HTML 停止/重启按钮，
        # 它们的 enabled 取决于壁纸是否正在运行。
        try:
            self.update_control_states()
        except Exception:
            pass
        if getattr(self, "_pending_static_wallpaper_list_reset", False):
            self._pending_static_wallpaper_list_reset = False
            QTimer.singleShot(0, self._clear_wallpaper_list_selection)
            QTimer.singleShot(120, self.refresh_history_list)
        cancelled = (not ok) and (t("终止") in str(message) or "cancel" in str(message).lower())
        # 当 worker 既没成功也没被取消时，message 可能为空（极端情况）。
        # 此时回退到 core.last_operation_error，避免用户只看到"操作失败"
        if ok or cancelled:
            self.finish_operation(message)
        else:
            fallback_reason = getattr(core, "last_operation_error", "") or ""
            self.finish_operation(message or (t("操作失败") + (f"：{fallback_reason}" if fallback_reason else "")))
        if not ok and not cancelled:
            self._show_non_modal_warning(t("错误"), message or t("操作失败"))
        pending_queue = getattr(self, "_pending_core_actions", None)
        if pending_queue and not self._core_busy:
            try:
                fn, args = pending_queue.popleft()
            except IndexError:
                fn = args = None
            if fn is not None:
                QTimer.singleShot(0, lambda fn=fn, args=args: self.run_core(fn, *args))


    def on_mode_changed(self, _index=None):
        if getattr(self, "_refreshing_from_config", False):
            return
        mode_key = normalize_mode_key(self.mode_combo.currentData() if self._is_qobject_alive(self.mode_combo) else _index)
        core.config["mode"] = mode_key
        core.save_config()
        self.update_control_states()
        if self.tray is not None:
            QTimer.singleShot(0, self.create_or_update_tray)
        self.set_status(t("正在切换模式…"))

        if mode_key == "幻灯片放映":
            folder = self._slide_folder_source.commit() if hasattr(self, "_slide_folder_source") else core.config.get("slide_folder")

            def _work():
                core.stop_video_wallpaper()
                if folder and os.path.isdir(folder):
                    return core.restart_slideshow()
                return True
            self._run_mode_transition(t("正在切换幻灯片放映…"), _work)
        elif mode_key == "图片":
            img = core.config.get("single_image")
            if img and os.path.exists(img):
                def _work():
                    core.stop_slideshow()
                    core.stop_video_wallpaper()
                    return core.set_wallpaper(img, t("切换单张图片模式"))
                self._run_mode_transition(t("正在切换单张图片…"), _work)
            else:
                def _work():
                    core.stop_slideshow()
                    core.stop_video_wallpaper()
                    return True
                self._run_mode_transition(t("正在停止动态壁纸…"), _work)
                self._schedule_preview_refresh()
        elif mode_key == "视频":
            video = self._video_source.commit() if hasattr(self, "_video_source") else core.config.get("video_file")
            if video and os.path.exists(video):
                def _work():
                    core.stop_slideshow()
                    core.stop_video_wallpaper()
                    return core.start_video_wallpaper(video)
                self._run_mode_transition(t("正在切换视频壁纸…"), _work)
            else:
                def _work():
                    core.stop_slideshow()
                    core.stop_video_wallpaper()
                    return True
                self._run_mode_transition(t("正在停止动态壁纸…"), _work)
                self._schedule_preview_refresh()
        elif mode_key == "HTML":
            path = self._html_source.commit() if hasattr(self, "_html_source") else core.config.get("html_file")
            if path:
                def _work():
                    core.stop_slideshow()
                    core.stop_video_wallpaper()
                    return core.start_html_wallpaper(path)
                self._run_mode_transition(t("正在切换 HTML 壁纸…"), _work)
            else:
                def _work():
                    core.stop_slideshow()
                    core.stop_video_wallpaper()
                    try:
                        core.stop_html_wallpaper()
                    except Exception:
                        pass
                    return True
                self._run_mode_transition(t("正在停止动态壁纸…"), _work)
                self._schedule_preview_refresh()
        elif mode_key == "纯色":
            def _work():
                core.stop_slideshow()
                core.stop_video_wallpaper()
                return core.apply_solid()
            self._run_mode_transition(t("正在切换纯色壁纸…"), _work)
        elif mode_key == "渐变":
            self.apply_gradient_wallpaper()
        else:
            self._schedule_preview_refresh()

    def _mode_display_label(self, mode_key: str) -> str:
        canonical = normalize_mode_key(mode_key)
        # HTML 模式在 UI 上直接显示 "HTML"，与模式选择下拉框一致。
        return t("HTML") if canonical == "HTML" else t(canonical)

    def _current_mode_key(self) -> str:
        try:
            if self._is_qobject_alive(getattr(self, "mode_combo", None)):
                data = self.mode_combo.currentData()
                if data:
                    return normalize_mode_key(data)
        except Exception:
            pass
        return normalize_mode_key(core.config.get("mode"))

    def switch_to_mode(self, mode_key: str = "next"):
        """Switch wallpaper mode from tray, global hotkey, IPC, or tests."""
        target = str(mode_key or "next").strip()
        if target.lower() in {"next", "cycle"}:
            return self.switch_to_next_mode()
        canonical = normalize_mode_key(target)
        if not canonical:
            canonical = "幻灯片放映"
        combo = getattr(self, "mode_combo", None)
        if self._is_qobject_alive(combo):
            idx = combo.findData(canonical)
            if idx >= 0:
                if combo.currentIndex() != idx:
                    combo.setCurrentIndex(idx)
                else:
                    self.on_mode_changed(idx)
                self.set_status(t("已切换到") + "：" + self._mode_display_label(canonical))
                return True
        ok = core.switch_wallpaper_mode(canonical)
        self.set_status((t("已切换到") if ok else t("切换失败")) + "：" + self._mode_display_label(canonical))
        return ok

    def switch_to_next_mode(self):
        order = []
        for item in MODE_KEYS:
            canonical = normalize_mode_key(item)
            if canonical and canonical not in order:
                order.append(canonical)
        if "HTML" not in order:
            order.append("HTML")
        current = normalize_mode_key(core.config.get("mode") or self._current_mode_key())
        try:
            idx = order.index(current)
        except ValueError:
            idx = -1
        return self.switch_to_mode(order[(idx + 1) % len(order)])

    def _populate_mode_submenu(self, menu: QMenu):
        menu.clear()
        menu.addAction(t("下一个模式"), self.switch_to_next_mode)
        menu.addSeparator()
        current = normalize_mode_key(core.config.get("mode") or self._current_mode_key())
        for item in MODE_KEYS:
            canonical = normalize_mode_key(item)
            action = menu.addAction(self._mode_display_label(canonical))
            action.setCheckable(True)
            action.setChecked(canonical == current)
            action.triggered.connect(lambda _checked=False, mode=canonical: self.switch_to_mode(mode))


    def on_fit_changed(self, _index=None):
        if getattr(self, "_refreshing_from_config", False):
            return
        fit_key = normalize_style_key(self.fit_combo.currentData() if self._is_qobject_alive(self.fit_combo) else _index)
        core.config["fit_mode"] = fit_key
        self.run_core(core.set_fit_mode, fit_key)

    def start_slideshow_from_gui(self):
        folder = self._slide_folder_source.commit(required=True, show_dialog=True)
        if not folder:
            return
        core.config["mode"] = "幻灯片放映"
        self._set_combo_current_data(self.mode_combo, "幻灯片放映")
        core.save_config()
        self.run_core(core.start_slideshow)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, t("选择壁纸文件夹"), self.folder_edit.text() or str(Path.home()))
        if not folder:
            return
        self.folder_edit.setText(folder)
        self.start_slideshow_from_gui()

    def on_seconds_changed(self, value):
        if getattr(self, "_refreshing_from_config", False):
            return
        core.config["slide_seconds"] = int(value)
        # v1.4.4: Debounce the config save + slideshow restart. QSpinBox emits
        # valueChanged on every click/keystroke, which previously caused
        # save_config() + restart_slideshow() to fire on every increment while
        # the user was still adjusting the value. This caused visible stutter.
        # Use a 500ms debounce timer so the restart only happens after the user
        # stops changing the value for half a second.
        if not hasattr(self, "_slide_interval_timer"):
            from PySide6.QtCore import QTimer as _QTimer
            self._slide_interval_timer = _QTimer(self)
            self._slide_interval_timer.setSingleShot(True)
            self._slide_interval_timer.setInterval(500)
            self._slide_interval_timer.timeout.connect(self._apply_slide_interval)
        self._slide_interval_timer.start()

    def _apply_slide_interval(self):
        """Actually save config and restart slideshow after debounce."""
        core.save_config()
        if normalize_mode_key(core.config.get("mode")) == "幻灯片放映":
            if core.config.get("slide_folder"):
                self.run_core(core.restart_slideshow)

    def on_shuffle_changed(self, checked):
        if getattr(self, "_refreshing_from_config", False):
            return
        core.config["shuffle"] = bool(checked)
        core.save_config()
        if normalize_mode_key(core.config.get("mode")) == "幻灯片放映":
            # Mirrors on_seconds_changed: only attempt to restart the slideshow
            # when a valid folder has been set.  Without this guard, toggling
            # the shuffle checkbox before choosing a wallpaper folder would
            # surface a generic "操作失败" error because restart_slideshow()
            # returns False when slide_folder is empty.
            if core.config.get("slide_folder"):
                self.run_core(core.restart_slideshow)

    def choose_single_image(self):
        path, _ = QFileDialog.getOpenFileName(self, t("选择图片"), str(Path.home()), t("图片文件 (*.jpg *.jpeg *.png *.bmp);;所有文件 (*.*)"))
        if not path:
            return
        core.config["single_image"] = path
        core.config["mode"] = "图片"
        self.single_edit.setText(path)
        self._set_combo_current_data(self.mode_combo, "图片")
        core.save_config()
        def _work():
            core.stop_slideshow()
            core.stop_video_wallpaper()
            return core.set_wallpaper(path, t("单张图片"))
        self._run_mode_transition(t("正在切换单张图片…"), _work)

    def choose_video_file(self):
        filters = ";;".join(f"{desc} ({ext})" for desc, ext in get_video_filetypes(t)) + ";;" + t("所有文件 (*.*)")
        path, _ = QFileDialog.getOpenFileName(self, t("选择视频"), self.video_edit.text() or str(Path.home()), filters)
        if not path:
            return
        self.video_edit.setText(path)
        self.start_video_wallpaper_from_gui()

    def _build_html_wallpaper_box(self) -> QGroupBox:
        html_box = QGroupBox(t("HTML 壁纸"))
        layout = QGridLayout(html_box)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)
        self.html_edit = configure_text_input(
            QLineEdit(core.config.get("html_file", "") or ""),
            name=t("HTML 壁纸来源"),
            description=t("可输入现有 HTML 文件、file URI 或 http/https 网址；离开输入框时保存，应用时再次校验。"),
            object_name="HtmlWallpaperSourceEdit",
            placeholder=t("选择本地 HTML 文件或输入 http(s) 网址"),
        )
        self._html_source = self._source_inputs.bind_html_source(
            self.html_edit,
            key="html_file",
            label=t("HTML 壁纸来源"),
            saved_text=t("HTML 壁纸来源已保存"),
            cleared_text=t("已清除 HTML 壁纸来源"),
        )
        self.html_browse_btn = QPushButton(t("选择 HTML"))
        self.html_browse_btn.setProperty("secondary", True)
        self.html_start_btn = QPushButton(t("应用 / 刷新 HTML"))
        self.html_stop_btn = QPushButton(t("停止 HTML"))
        self.html_auto_pause_check = QCheckBox(t("自动暂停"))
        self.html_frame_rate_combo = ShangComboBox()
        # Keep the selected FPS text visible under high DPI and translated UI.
        self.html_frame_rate_combo.setMinimumWidth(150)
        self.html_frame_rate_combo.setMaximumWidth(190)
        self.html_frame_rate_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for label, value in (
            (t("15 FPS"), 15),
            (t("24 FPS"), 24),
            (t("30 FPS（推荐）"), 30),
            (t("45 FPS"), 45),
            (t("60 FPS"), 60),
            (t("不限"), 0),
        ):
            self.html_frame_rate_combo.addItem(label, value)
        configured_frame_rate = int(core.config.get("html_frame_rate", 30) or 0)
        frame_index = self.html_frame_rate_combo.findData(configured_frame_rate)
        self.html_frame_rate_combo.setCurrentIndex(frame_index if frame_index >= 0 else self.html_frame_rate_combo.findData(30))
        self._prepare_combo_popup(self.html_frame_rate_combo)
        self.html_auto_pause_check.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.html_auto_pause_check.setChecked(bool(core.config.get("html_auto_pause", True)))
        self.html_auto_pause_check.setToolTip(
            t("所有显示器几乎被窗口遮挡时暂停 HTML 页面事件并隐藏原生渲染控件；桌面可见时恢复。")
        )
        self.html_frame_rate_combo.setToolTip(
            t("限制 requestAnimationFrame 驱动的 Canvas、WebGL 与页面动画帧率；默认 30 FPS。")
        )
        self.html_browse_btn.clicked.connect(self.choose_html_file)
        self.html_start_btn.clicked.connect(self.apply_html_wallpaper_from_gui)
        self.html_stop_btn.clicked.connect(lambda: self.run_core(core.stop_html_wallpaper))
        self.html_auto_pause_check.toggled.connect(self.on_html_auto_pause_changed)
        self.html_frame_rate_combo.currentIndexChanged.connect(self.on_html_frame_rate_changed)
        for button in (self.html_browse_btn, self.html_start_btn, self.html_stop_btn):
            button.setMinimumHeight(34)
        layout.addWidget(make_buddy_label(t("HTML 路径"), self.html_edit), 0, 0)
        layout.addWidget(self.html_edit, 0, 1, 1, 2)
        layout.addWidget(self.html_browse_btn, 0, 3)
        layout.addWidget(self.html_start_btn, 1, 1, 1, 2)
        layout.addWidget(self.html_stop_btn, 1, 3)
        options_widget = QWidget(html_box)
        options_layout = QHBoxLayout(options_widget)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(14)
        options_layout.addWidget(self.html_auto_pause_check)
        options_layout.addWidget(QLabel(t("帧率上限：")))
        options_layout.addWidget(self.html_frame_rate_combo)
        options_layout.addStretch(1)
        layout.addWidget(options_widget, 2, 1, 1, 3)
        self.html_box = html_box
        if getattr(core, "html_wallpaper", None) is None:
            for widget in (
                self.html_edit,
                self.html_browse_btn,
                self.html_start_btn,
                self.html_stop_btn,
                self.html_auto_pause_check,
                self.html_frame_rate_combo,
                ):
                widget.setEnabled(False)
        return html_box

    def _html_current_path_from_ui(self) -> str:
        if hasattr(self, "html_edit"):
            return self.html_edit.text().strip()
        return str(core.config.get("html_file", "") or core.config.get("html_url", "") or "").strip()

    def _html_runtime_options_from_ui(self) -> dict[str, object]:
        frame_rate = int(core.config.get("html_frame_rate", 30) or 0)
        if hasattr(self, "html_frame_rate_combo"):
            try:
                frame_rate = int(self.html_frame_rate_combo.currentData())
            except (TypeError, ValueError):
                frame_rate = 30
        if frame_rate not in {0, 15, 24, 30, 45, 60}:
            frame_rate = 30
        return {
            "auto_pause": bool(self.html_auto_pause_check.isChecked()) if hasattr(self, "html_auto_pause_check") else bool(core.config.get("html_auto_pause", True)),
            "frame_rate": frame_rate,
        }

    def _sync_html_runtime_options(self, options: dict[str, object] | None = None) -> None:
        options = options or {
            "auto_pause": bool(core.config.get("html_auto_pause", True)),
            "frame_rate": int(core.config.get("html_frame_rate", 30) or 0),
        }
        for runtime_key in ("auto_pause", "frame_rate"):
            try:
                value = options[runtime_key]
                if runtime_key != "frame_rate":
                    value = bool(value)
                core.html_wallpaper_runtime_set_option(runtime_key, value)
            except Exception as exc:
                core.log(f"同步 HTML 壁纸选项失败({runtime_key}): {exc}")

    def _save_html_wallpaper_config_from_ui(self, path: str | None = None) -> str:
        target = (path or self._html_current_path_from_ui()).strip()
        options = self._html_runtime_options_from_ui()
        if hasattr(self, "html_edit") and target:
            self.html_edit.setText(target)
        core.config["html_file"] = target
        core.config["mode"] = "HTML"
        core.config["html_auto_pause"] = options["auto_pause"]
        core.config["html_frame_rate"] = options["frame_rate"]
        self._set_combo_current_data(self.mode_combo, "HTML")
        core.save_config()
        self._sync_html_runtime_options(options)
        return target

    def _run_html_wallpaper_from_gui(self, path: str | None = None, *, restart: bool = False, status_text: str | None = None) -> None:
        if path is not None and hasattr(self, "html_edit"):
            self.html_edit.setText(str(path))
        target = self._html_source.commit(required=True, show_dialog=True)
        if not target:
            return
        target = self._save_html_wallpaper_config_from_ui(target)

        def _work():
            if restart:
                return core.restart_html_wallpaper(target)
            core.stop_slideshow()
            core.stop_video_wallpaper()
            return core.start_html_wallpaper(target)

        self._run_mode_transition(status_text or (t("正在重启 HTML 壁纸…") if restart else t("正在切换 HTML 壁纸…")), _work)

    def choose_html_file(self):
        """弹出文件选择对话框，让用户选择本地 HTML 文件并直接切换。"""
        filters = t("HTML 文件 (*.html *.htm)") + ";;" + t("所有文件 (*.*)")
        initial = self._html_current_path_from_ui()
        initial_dir = str(Path(initial).parent) if initial and Path(initial).is_file() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, t("选择 HTML 文件"), initial_dir, filters)
        if not path:
            return
        self._run_html_wallpaper_from_gui(path)

    def apply_html_wallpaper_from_gui(self):
        """Apply the visible source, refreshing the renderer when already active."""
        running = core.is_html_wallpaper_running()
        target = self._html_current_path_from_ui()
        self._run_html_wallpaper_from_gui(
            target,
            restart=running,
            status_text=t("正在刷新 HTML 壁纸…") if running else t("正在切换 HTML 壁纸…"),
        )

    def start_html_wallpaper_from_gui(self):
        self.apply_html_wallpaper_from_gui()

    def restart_html_wallpaper_from_gui(self):
        self.apply_html_wallpaper_from_gui()

    def _set_html_runtime_option_from_gui(self, config_key: str, runtime_key: str, checked: bool, *, restart_if_running: bool = False, restart_status: str | None = None) -> None:
        if getattr(self, "_refreshing_from_config", False):
            return
        core.config[config_key] = bool(checked)
        core.save_config()
        try:
            core.html_wallpaper_runtime_set_option(runtime_key, bool(checked))
        except Exception as exc:
            core.log(f"写入 HTML 壁纸选项失败({runtime_key}): {exc}")
        if restart_if_running:
            try:
                if core.is_html_wallpaper_running():
                    target = self._html_current_path_from_ui() or core.html_wallpaper_get_last_path()
                    if target:
                        self._run_html_wallpaper_from_gui(target, restart=True, status_text=restart_status)
            except Exception as exc:
                core.log(f"重启 HTML 壁纸以应用选项失败({runtime_key}): {exc}")

    def on_html_auto_pause_changed(self, checked):
        """自动暂停选项切换：立即写盘，并在 HTML 壁纸运行时热通知子进程。"""
        self._set_html_runtime_option_from_gui("html_auto_pause", "auto_pause", checked)

    def on_html_frame_rate_changed(self, _index: int = -1):
        """帧率上限切换：保存并热更新 requestAnimationFrame 调度器。"""
        if getattr(self, "_refreshing_from_config", False):
            return
        try:
            frame_rate = int(self.html_frame_rate_combo.currentData())
        except (AttributeError, TypeError, ValueError):
            frame_rate = 30
        if frame_rate not in {0, 15, 24, 30, 45, 60}:
            frame_rate = 30
        core.config["html_frame_rate"] = frame_rate
        core.save_config()
        try:
            core.html_wallpaper_runtime_set_option("frame_rate", frame_rate)
        except Exception as exc:
            core.log(f"写入 HTML 壁纸选项失败(frame_rate): {exc}")

    def start_video_wallpaper_from_gui(self):
        path = self._video_source.commit(required=True, show_dialog=True)
        if not path:
            return
        core.config["mode"] = "视频"
        self._set_combo_current_data(self.mode_combo, "视频")
        core.save_config()
        self._refresh_video_volume_controls()

        def _work():
            core.stop_slideshow()
            core.stop_video_wallpaper()
            return core.start_video_wallpaper(path)

        self._run_mode_transition(t("正在切换视频壁纸…"), _work)

    def _is_desktop_foreground(self) -> bool:
        """Return True when the desktop shell is the active foreground surface.

        Bug 5 fix: Linux 端现在通过 platform_adapters.integration.is_desktop_foreground()
        实际检测（X11 用 xdotool+xprop，Wayland 用 gdbus/qdbus）。之前总是返回 True
        会让"桌面失焦时暂停"视频策略和 HTML 自动暂停功能在 Linux 上完全失效。
        """
        try:
            if not core.IS_WINDOWS:
                # Bug 5 fix: 调用平台特定实现，而不是无条件返回 True。
                from platform_adapters import integration
                if hasattr(integration, "is_desktop_foreground"):
                    return bool(integration.is_desktop_foreground())
                return True
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            cls = (buf.value or "").lower()
            return cls in {"progman", "workerw", "shelldll_defview", "syslistview32"}
        except Exception:
            return True

    def _refresh_video_volume_controls(self):
        """根据当前配置同步音量滑块的启用状态。"""
        if not hasattr(self, "video_volume_slider"):
            return
        has_video_file = bool(core.config.get("video_file", ""))
        is_video_mode = normalize_mode_key(core.config.get("mode")) == "视频"
        muted = bool(core.config.get("video_muted", True))
        self.video_volume_slider.setEnabled((is_video_mode or has_video_file) and not muted)

    def on_video_muted_changed(self, checked):
        self._cancel_video_volume_ramp()
        self._video_focus_pause_pending = False
        self._video_focus_ducked = False
        core.config["video_muted"] = bool(checked)
        core.save_config()
        # 统一刷新滑块启用状态（静音时灰显）。
        self._refresh_video_volume_controls()
        # 优先尝试 IPC 热更新（不中断播放）；失败则回退到 stop+start 重启
        if normalize_mode_key(core.config.get("mode")) == "视频" and core.config.get("video_file"):
            volume = int(core.config.get("video_volume", 100))
            if core.is_video_wallpaper_running():
                if self._set_video_runtime_volume(bool(checked), volume):
                    # IPC 热更新成功，无需重启播放进程
                    return
            # IPC 不可用或失败：回退到重启
            self.run_core(core.start_video_wallpaper, core.config.get("video_file"))

    def on_video_volume_changed(self, value):
        # Persist the new volume and refresh the live percentage label.
        core.config["video_volume"] = int(value)
        if hasattr(self, "video_volume_value_label"):
            self.video_volume_value_label.setText(f"{int(value)}%")
        core.save_config()
        # 仅在视频模式下且未静音时尝试热更新
        if not (normalize_mode_key(core.config.get("mode")) == "视频"
                and core.config.get("video_file")
                and not core.config.get("video_muted", True)):
            return
        if not core.is_video_wallpaper_running():
            return
        # 焦点策略正在暂停、等待暂停或降音量时，只保存用户的新基准音量。
        # 立即向播放器写入会与渐弱/渐强定时器互相抢占；恢复桌面后会使用
        # 这里保存的最新值平滑回升。
        if any((
            bool(getattr(self, "_video_focus_paused", False)),
            bool(getattr(self, "_video_focus_pause_pending", False)),
            bool(getattr(self, "_video_focus_ducked", False)),
        )):
            return
        # 用户在恢复渐强过程中主动拖动滑块时，以用户输入为准并停止旧渐变。
        self._cancel_video_volume_ramp()
        # 拖动滑块时会产生大量 valueChanged 信号；用一个短定时器合并它们，
        # 避免每次微小拖动都触发 IPC 写入或重启。最后一次拖动 120ms 后再执行。
        if not hasattr(self, "_video_volume_ipc_timer"):
            self._video_volume_ipc_timer = QTimer(self)
            self._video_volume_ipc_timer.setSingleShot(True)
            self._video_volume_ipc_timer.timeout.connect(self._apply_video_volume_live)
        self._video_volume_ipc_timer.start(120)

    def _apply_video_volume_live(self):
        """实际向播放器发送音量变更：先试 IPC 热更新，失败则回退到重启。"""
        if not core.is_video_wallpaper_running():
            return
        volume = int(core.config.get("video_volume", 100))
        muted = bool(core.config.get("video_muted", True))
        if self._set_video_runtime_volume(muted, volume):
            return  # IPC 热更新成功
        # 回退：重启播放进程。重启会带来短暂闪烁，但保证音量最终生效。
        if core.config.get("video_file"):
            self.run_core(core.start_video_wallpaper, core.config.get("video_file"))

    def choose_solid_color(self):
        color = QColorDialog.getColor(QColor(core.config.get("solid_color", "#ffffff")), self, t("选择纯色"))
        if not color.isValid():
            return
        value = color.name()
        core.config["solid_color"] = value
        self._paint_button(self.solid_btn, value)
        core.save_config()
        if normalize_mode_key(core.config.get("mode")) == "纯色":
            self.run_core(core.apply_solid)

    def choose_gradient_color(self, index: int):
        key = "solid_color" if index == 1 else "gradient_color2"
        color = QColorDialog.getColor(QColor(core.config.get(key, "#ffffff")), self, t("选择渐变颜色"))
        if not color.isValid():
            return
        core.config[key] = color.name()
        self._paint_button(self.grad1_btn if index == 1 else self.grad2_btn, color.name())
        core.save_config()
        if normalize_mode_key(core.config.get("mode")) == "渐变":
            self.apply_gradient_wallpaper()

    def on_gradient_changed(self, value):
        core.config["gradient_angle"] = int(value)
        core.save_config()

    def on_gradient_apply(self):
        if normalize_mode_key(core.config.get("mode")) == "渐变":
            self.apply_gradient_wallpaper()

    def apply_gradient_wallpaper(self):
        def _work():
            core.stop_slideshow()
            core.stop_video_wallpaper()
            c1 = core.config.get("solid_color", "#ffffff")
            c2 = core.config.get("gradient_color2", "#ffffff")
            angle = int(core.config.get("gradient_angle", 60))
            path = core.create_gradient_wallpaper(c1, c2, angle)
            if path:
                return core.set_wallpaper_direct(path, t("渐变"))
            return False
        self._run_mode_transition(t("正在切换渐变壁纸…"), _work)

    def _refresh_color_buttons(self):
        if not all(hasattr(self, name) for name in ("solid_btn", "grad1_btn", "grad2_btn")):
            return
        self._paint_button(self.solid_btn, core.config.get("solid_color", "#ffffff"))
        self._paint_button(self.grad1_btn, core.config.get("solid_color", "#ffffff"))
        self._paint_button(self.grad2_btn, core.config.get("gradient_color2", "#ffffff"))

    def _paint_button(self, btn: QPushButton, color: str):
        qcolor = QColor(color if color else "#ffffff")
        if not qcolor.isValid():
            qcolor = QColor("#ffffff")
            color = "#ffffff"
        # 白色/浅色背景使用深色文字，解决默认纯色为白色时按钮文字不可读的问题。
        brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000
        text_color = "#24292f" if brightness >= 170 else "#ffffff"
        border = "#c9d1d9" if brightness >= 230 else qcolor.darker(115).name()
        hover_border = self._theme_color if hasattr(self, "_theme_color") else core.config.get("theme_color", DEFAULT_THEME_COLOR)
        btn.setStyleSheet(
            "QPushButton {"
            f" background: {qcolor.name()};"
            f" border: 1px solid {border};"
            f" border-radius: 6px; color: {text_color}; padding: 5px 12px; font-weight: 600; }}"
            f"QPushButton:hover:enabled {{ border: 1px solid {hover_border}; }}"
            "QPushButton:disabled { background: #eaeef2; border: 1px solid #d0d7de; color: #8c959f; }"
        )
        try:
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        except Exception:
            pass
        btn.update()

    def _context_action_defs(self):
        return [
            ("previous", t("上一张壁纸"), "Ctrl+Alt+U", "ctx_last_wallpaper", "ctx_prev"),
            ("next", t("下一张壁纸"), "Ctrl+Alt+N", "ctx_next_wallpaper", "ctx_next"),
            ("random", t("随机壁纸"), "Ctrl+Alt+R", "ctx_random_wallpaper", "ctx_random"),
            ("jump", t("跳转到壁纸"), "Ctrl+Alt+J", "ctx_jump_to_wallpaper", "ctx_jump"),
        ]

    # ------------------------------------------------------------------
    # v1.4.7: 移除全部应用内热键 (QShortcut) 相关方法 ——
    # _app_shortcut_defs/_app_shortcut_action/_app_shortcut_focus_guard/
    # _app_action_*/_setup_app_shortcuts/_refresh_app_shortcuts/
    # _on_app_sc_edited/_reset_app_shortcut/_on_app_sc_enabled_changed.
    # 原因: ApplicationShortcut 上下文 + 裸键与 QAction/菜单冲突,
    # 打字时 activatedAmbiguously 派发导致焦点被抢/应用卡死.
    # 全局热键 (Ctrl+Alt+...) 已足够且不与打字冲突.
    # ------------------------------------------------------------------

    def _context_hotkey(self, action: str) -> str:
        default_map = {item[0]: item[2] for item in self._context_action_defs()}
        return str(core.config.get(f"hotkey_{action}", default_map.get(action, "")) or "").strip()

    def _context_hotkey_display(self, action: str) -> str:
        raw = self._context_hotkey(action)
        if not raw:
            return t("当前：无")
        parts = [p.strip() for p in raw.replace("-", "+").split("+") if p.strip()]
        names = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "meta": "Win"}
        display = "+".join(names.get(p.lower(), p.upper() if len(p) == 1 else p) for p in parts)
        return f"当前：{display}"

    def _context_checkbox_label(self, action: str, label: str) -> str:
        return f"{label}（{self._context_hotkey_display(action).replace('当前：', '')}）"

    def _refresh_context_shortcut_labels(self):
        for action, label, _default_key, _cfg_key, widget_name in self._context_action_defs():
            widget = getattr(self, widget_name, None)
            if self._is_qobject_alive(widget):
                widget.setText(self._context_checkbox_label(action, label))
            current_labels = getattr(self, "ctx_shortcut_current_labels", {})
            if action in current_labels and self._is_qobject_alive(current_labels[action]):
                current_labels[action].setText(self._context_hotkey_display(action))


    def on_context_hotkey_changed(self, action: str, edit: QLineEdit):
        value = edit.text().strip().replace(" ", "")
        if value and self._warn_duplicate_context_hotkey(action, value):
            edit.setText("")
            return
        core.config[f"hotkey_{action}"] = value
        core.save_config()
        edit.setText(value)
        self._refresh_context_shortcut_labels()
        try:
            if bool(core.config.get("global_hotkeys_enabled", False)):
                core.refresh_global_hotkeys()
            else:
                core.stop_global_hotkeys()
        except Exception as exc:
            core.log(f"刷新全局热键失败: {exc}")
        self.set_status(t("全局热键已保存"))

    def _normalized_hotkey_for_compare(self, value: str) -> str:
        """Normalize a saved hotkey string for duplicate checks."""
        parts = [p.strip().lower() for p in str(value or "").replace("-", "+").split("+") if p.strip()]
        aliases = {"control": "ctrl", "meta": "win", "cmd": "win", "command": "win", "super": "win"}
        normalized = [aliases.get(p, p) for p in parts]
        return "+".join(normalized)

    def _duplicate_context_hotkey_action(self, action: str, seq_str: str) -> str | None:
        target = self._normalized_hotkey_for_compare(seq_str)
        if not target:
            return None
        for other_action, label, _default_key, _cfg_key, _widget_name in self._context_action_defs():
            if other_action == action:
                continue
            existing = self._normalized_hotkey_for_compare(core.config.get(f"hotkey_{other_action}", ""))
            if existing and existing == target:
                return label
        return None

    def _warn_duplicate_context_hotkey(self, action: str, seq_str: str) -> bool:
        duplicate_label = self._duplicate_context_hotkey_action(action, seq_str)
        if not duplicate_label:
            return False
        QMessageBox.warning(
            self,
            t("全局热键冲突"),
            t("该快捷键已被其他动作使用：") + str(duplicate_label),
        )
        return True

    def set_context_hotkey(self, action: str, seq_str: str) -> None:
        """Persist a recorded global hotkey and refresh the backend registration."""
        seq_str = str(seq_str or "").strip()
        if seq_str:
            try:
                parsed = core._pynput_hotkey_string(seq_str)
            except Exception:
                parsed = None
            if parsed is None:
                QMessageBox.warning(self, t("全局热键冲突"), t("请输入可注册的全局热键：例如 Ctrl+Alt+N；macOS/Linux 需要至少包含一个修饰键和一个非修饰键。"))
                return
            if self._warn_duplicate_context_hotkey(action, seq_str):
                return
        core.config[f"hotkey_{action}"] = seq_str
        core.save_config()
        self._refresh_context_shortcut_labels()
        try:
            if bool(core.config.get("global_hotkeys_enabled", False)):
                core.refresh_global_hotkeys()
            else:
                core.stop_global_hotkeys()
        except Exception as exc:
            core.log(f"刷新全局热键失败: {exc}")
        if seq_str:
            prefix = t("已录制全局热键：") if core.config.get("global_hotkeys_enabled", False) else t("已保存快捷键：")
            self.set_status(prefix + seq_str)
        else:
            self.set_status(t("已清除快捷键"))

    def record_context_hotkey(self, action: str) -> None:
        """Record a global hotkey by listening until all pressed keys are released."""
        try:
            from pynput import keyboard  # type: ignore
        except Exception:
            self.set_status(t("pynput 未安装，无法录制快捷键"))
            return

        self.set_status(t("录制中") + "…")

        def worker() -> None:
            keys_down: set[object] = set()
            recorded: list[object] = []

            def _remember(key):
                if key not in recorded:
                    recorded.append(key)

            def on_press(key):
                try:
                    keys_down.add(key)
                    _remember(key)
                except Exception:
                    pass

            def on_release(key):
                try:
                    keys_down.discard(key)
                except Exception:
                    pass
                if not keys_down:
                    return False

            with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
                listener.join()

            def key_name(item) -> str:
                try:
                    if hasattr(item, "char") and item.char:
                        return str(item.char).upper()
                    name = getattr(item, "name", "") or ""
                    mapping = {
                        "ctrl": "Ctrl", "ctrl_l": "Ctrl", "ctrl_r": "Ctrl", "control": "Ctrl",
                        "alt": "Alt", "alt_l": "Alt", "alt_r": "Alt", "alt_gr": "Alt",
                        "shift": "Shift", "shift_l": "Shift", "shift_r": "Shift",
                        "cmd": "Win", "cmd_l": "Win", "cmd_r": "Win", "meta": "Win", "super": "Win",
                    }
                    return mapping.get(name, name.upper() if name else "")
                except Exception:
                    return ""

            # Stable order for modifiers first, then non-modifier keys in press order.
            names = [key_name(k) for k in recorded]
            names = [n for n in names if n]
            ordered: list[str] = []
            for mod in ("Ctrl", "Alt", "Shift", "Win"):
                if mod in names and mod not in ordered:
                    ordered.append(mod)
            for name in names:
                if name not in ordered:
                    ordered.append(name)
            seq_str = "+".join(ordered)
            self.hotkey_recorded_signal.emit(action, seq_str)

        threading.Thread(target=worker, daemon=True).start()

    def on_context_hotkey_clear(self, action: str):
        """Clear the recorded global hotkey for one action."""
        self.set_context_hotkey(action, "")

    def on_global_hotkeys_enabled_changed(self, checked: bool) -> None:
        """Enable only when the current platform backend really registered."""
        core.config["global_hotkeys_enabled"] = bool(checked)
        core.save_config()
        registered = not checked
        try:
            if checked:
                registered = bool(core.refresh_global_hotkeys())
            else:
                core.stop_global_hotkeys()
        except Exception as exc:
            core.log(f"切换全局热键失败: {exc}")
            registered = False
        if checked and not registered:
            core.config["global_hotkeys_enabled"] = False
            core.save_config()
            try:
                self.global_hotkeys_enabled_check.blockSignals(True)
                self.global_hotkeys_enabled_check.setChecked(False)
            finally:
                self.global_hotkeys_enabled_check.blockSignals(False)
            self.set_status(t("当前会话无法注册全局热键，已保持关闭"))
            return
        self.set_status(t("全局热键已开启") if checked else t("全局热键已关闭"))

    def on_hotkey_focus_guard_changed(self, checked: bool) -> None:
        """Persist the cross-platform focus guard and refresh active hotkeys."""
        core.config["hotkey_focus_guard"] = bool(checked)
        core.save_config()
        try:
            if bool(core.config.get("global_hotkeys_enabled", False)):
                core.refresh_global_hotkeys()
        except Exception as exc:
            core.log(f"刷新全局热键失败: {exc}")
        self.set_status(t("聚焦位置检测已开启") if checked else t("聚焦位置检测已关闭"))

    def _update_ctx(self, key, value):
        core.config[key] = bool(value)
        core.save_config()
        try:
            if bool(core.config.get("global_hotkeys_enabled", False)):
                core.refresh_global_hotkeys()
            else:
                core.stop_global_hotkeys()
        except Exception as exc:
            core.log(f"刷新全局热键失败: {exc}")

    def ask_yes_no(self, title: str, text: str, *, default_yes: bool = True) -> bool:
        # Delegate to the shared ui.dialog_style helper to keep Yes/No
        # wording, icon, modality and QSS cascade identical across all
        # three platforms.
        from ui.dialog_style import ask_yes_no as _ask_yes_no_helper
        return _ask_yes_no_helper(self, title, text, default_yes=default_yes)

    def register_context_with_prompt(self):
        if not core.IS_WINDOWS:
            self.set_status(t("当前平台仅支持全局热键与托盘菜单，不提供桌面右键菜单同步。"))
            return False
        if core.IS_WINDOWS and not core.is_windows_admin():
            if self.ask_yes_no(
                t("需要管理员权限"),
                t("同步桌面右键菜单需要写入 HKEY_CLASSES_ROOT。是否以管理员身份重启并继续？"),
                default_yes=True,
            ):
                self.restart_as_admin(extra_args=["--sync-context-on-start"])
            return False
        return self.sync_context_menu(show_message=True)

    def sync_context_menu(self, show_message=False, only_if_needed=False):
        if not core.IS_WINDOWS:
            self.set_status(t("当前平台仅支持全局热键与托盘菜单，不提供桌面右键菜单同步。"))
            if show_message:
                QMessageBox.information(self, t("全局热键"), t("当前平台仅支持全局热键与托盘菜单，不提供桌面右键菜单同步。"))
            return False
        if only_if_needed and core.IS_WINDOWS:
            try:
                if core.is_context_menu_synced():
                    self.set_status(t("右键菜单已是最新，无需同步"))
                    if show_message:
                        QMessageBox.information(self, t("右键菜单"), t("右键菜单已是最新，无需同步"))
                    return True
            except Exception as exc:
                core.log(f"检查右键菜单同步状态失败: {exc}")
        ok = core.register_context(show_admin_prompt=False)
        # 失败时把 core.last_operation_error 一起带给用户，避免"同步失败或已跳过"
        # 这种不带原因的通用提示
        if ok:
            self.set_status(t("右键菜单已同步"))
        else:
            reason = getattr(core, "last_operation_error", "") or ""
            self.set_status(t("右键菜单同步失败或已跳过") + (f"：{reason}" if reason else ""))
        if show_message:
            if ok:
                QMessageBox.information(self, t("右键菜单"), t("同步完成"))
            else:
                reason = getattr(core, "last_operation_error", "") or ""
                QMessageBox.information(self, t("右键菜单"), t("同步失败或已跳过") + (f"\n\n{t('原因')}：{reason}" if reason else ""))
        return ok

    def open_global_settings_from_home(self):
        dlg = getattr(self, "_settings_dialog", None)
        if self._is_qobject_alive(dlg):
            try:
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()
                # 修复 (v1.4.6): 每次打开回到初始位置 —— 重置左侧导航选中第一项,
                # 并把所有页面的 QScrollArea 滚动条归零.
                self._reset_settings_dialog_to_initial_position()
                return
            except RuntimeError:
                self._clear_settings_widget_refs()
        else:
            self._clear_settings_widget_refs()

        dialog = GlobalSettingsDialog(
            self,
            autosave_text="",
            close_text="",
        )
        self._settings_dialog = dialog
        dialog.setWindowTitle(t("全局设置"))
        icon_path = self._img_path("settings.svg")
        if os.path.exists(icon_path):
            dialog.setWindowIcon(QIcon(icon_path))
        elif not getattr(self, "app_icon", QIcon()).isNull():
            dialog.setWindowIcon(self.app_icon)
        dialog.resize(980, 700)
        dialog.setMinimumSize(880, 620)
        settings_page = self._settings_tab()
        dialog.set_content(settings_page)
        self._refresh_settings_dialog_surfaces()
        self.refresh_from_config()
        dialog.about_to_close.connect(self._on_settings_dialog_about_to_close)
        dialog.destroyed.connect(lambda *_: self._clear_settings_widget_refs())
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        # 新建对话框后也回到初始位置
        self._reset_settings_dialog_to_initial_position()
        self.set_status(t("已打开全局设置"))

    def _on_settings_dialog_about_to_close(self) -> None:
        try:
            self._flush_settings_dialog_unsaved_changes()
            self.set_status(t("设置已保存"))
        except Exception as exc:
            core.log(f"flush settings dialog changes failed: {exc}", level="WARNING", exc_info=True)

    def _reset_settings_dialog_to_initial_position(self) -> None:
        """重置全局设置对话框到初始位置: 选中左侧导航第一项 + 所有页滚动条归零.

        修复 (v1.4.6): 之前对话框复用时保留上次选中页和滚动位置, 用户反馈
        "每次打开回到初始位置".
        """
        try:
            dlg = getattr(self, "_settings_dialog", None)
            if not self._is_qobject_alive(dlg):
                return
            navigator = getattr(self, "_settings_navigator", None)
            if navigator is not None:
                navigator.reset()
                return
            # 兼容设置页安全模式：没有导航控制器时回退到通用扫描。
            nav = getattr(self, "_settings_nav", None)
            if self._is_qobject_alive(nav):
                try:
                    nav.setCurrentRow(0)
                except Exception:
                    pass
            # 2. 重置所有 QScrollArea 滚动条归零
            from PySide6.QtWidgets import QScrollArea, QStackedWidget
            for scroll in dlg.findChildren(QScrollArea):
                try:
                    bar = scroll.verticalScrollBar()
                    if bar is not None:
                        bar.setValue(0)
                    hbar = scroll.horizontalScrollBar()
                    if hbar is not None:
                        hbar.setValue(0)
                except Exception:
                    pass
            # 3. 重置 QStackedWidget 到第一页
            for stack in dlg.findChildren(QStackedWidget):
                try:
                    stack.setCurrentIndex(0)
                    break  # 设置页只有一个 stack
                except Exception:
                    pass
        except Exception as exc:
            try:
                core.log(f"reset settings dialog position failed: {exc}", level="WARNING")
            except Exception:
                pass

    def _flush_settings_dialog_unsaved_changes(self):
        """遍历设置对话框内所有 editingFinished-only 的编辑项，强制 flush 到 config。

        覆盖范围：
        - 字体路径编辑框（font_path_edit）如果存在且与 config 不一致则保存；
        - 其它即时保存的控件（如 DPI 滑块）不需要处理。
        v1.4.7: 应用内热键已移除, 不再 flush app_sc_edits.
        """
        # 字体路径编辑框
        font_path_edit = getattr(self, "font_path_edit", None)
        if self._is_qobject_alive(font_path_edit):
            try:
                current_font = font_path_edit.text().strip()
                saved_font = str(core.config.get("font_path", "") or "").strip()
                if current_font != saved_font:
                    core.config["font_path"] = current_font
                    try:
                        core.save_config()
                    except Exception:
                        pass
            except Exception:
                pass

    # ---------- 随机概率（百分比） ----------
    def open_random_probability_settings(self):
        folder = self._slide_folder_source.commit(required=True) if hasattr(self, "_slide_folder_source") else core.config.get("slide_folder", "")
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(self, t("随机概率"), t("请先在幻灯片放映中选择有效的壁纸文件夹。"))
            return

        existing = getattr(self, "_random_probability_dialog", None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                self._random_probability_dialog = None

        try:
            from core import random_probability as random_copy
            from ui.probability_dialog import RandomProbabilityDialog
            images = random_copy.get_original_image_paths(folder)
        except Exception as exc:
            QMessageBox.warning(self, t("随机概率"), t("加载随机概率设置失败：") + str(exc))
            return

        if not images:
            QMessageBox.information(self, t("随机概率"), t("当前文件夹中没有可设置的壁纸图片。"))
            return

        def on_saved():
            self.set_status(t("随机壁纸百分比已保存"))

        dialog = RandomProbabilityDialog(
            self,
            folder,
            images,
            random_copy,
            translate=t,
            on_saved=on_saved,
            logger=core.log,
        )
        self._random_probability_dialog = dialog
        if not getattr(self, "app_icon", QIcon()).isNull():
            dialog.setWindowIcon(self.app_icon)
        # The application-level QSS already cascades onto this dialog;
        # copying _theme_stylesheet here used to override the per-widget
        # property-based styles (dialogTitle/dialogNote) applied inside
        # RandomProbabilityDialog._build_ui, which broke the unified
        # dialog style system.

        def cleanup_dialog(*_args):
            if getattr(self, "_random_probability_dialog", None) is dialog:
                self._random_probability_dialog = None

        dialog.destroyed.connect(cleanup_dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @staticmethod
    def _desktop_exec_quote_arg(value: str) -> str:
        text = str(value)
        text = text.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
        return f'"{text}"'

    def _linux_autostart_command(self) -> str:
        args = [sys.executable, "--hide"] if core.is_frozen() else [sys.executable, entry_script_path(), "--hide"]
        return " ".join(self._desktop_exec_quote_arg(arg) for arg in args)

    def set_auto_start(self, enable: bool):
        """Use the desktop-session XDG autostart mechanism on Linux.

        A GUI wallpaper manager needs the graphical session environment. A
        generic systemd user service may start without the compositor's
        DISPLAY/WAYLAND variables, so the portable login-time .desktop method
        is the primary and only advertised backend.
        """
        autostart_dir = os.path.expanduser("~/.config/autostart")
        desktop_path = os.path.join(autostart_dir, "shangbackground.desktop")
        # Clean up the legacy service used by earlier builds to avoid duplicate
        # launches. Failure to disable an absent user service is harmless.
        service_path = os.path.expanduser("~/.config/systemd/user/shangbackground.service")
        if enable:
            os.makedirs(autostart_dir, exist_ok=True)
            desktop_content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Version=1.0\n"
                "Name=ShangBackground\n"
                f"Exec={self._linux_autostart_command()}\n"
                "Terminal=false\n"
                "Hidden=false\n"
                "NoDisplay=false\n"
                "X-GNOME-Autostart-enabled=true\n"
                "Comment=Desktop wallpaper manager\n"
            )
            tmp_path = desktop_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(desktop_content)
            os.replace(tmp_path, desktop_path)
            try:
                os.chmod(desktop_path, 0o600)
            except OSError:
                pass
            if not os.path.isfile(desktop_path):
                raise RuntimeError("XDG autostart desktop file was not created")
            core.log(t("Linux XDG 开机自启动已启用"))
        else:
            if os.path.exists(desktop_path):
                os.remove(desktop_path)
            core.log(t("Linux XDG 开机自启动已禁用"))
        if os.path.exists(service_path):
            try:
                subprocess.run(
                    ["systemctl", "--user", "disable", "--now", "shangbackground.service"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                os.remove(service_path)
                subprocess.run(
                    ["systemctl", "--user", "daemon-reload"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
            except Exception as exc:
                core.log(f"清理旧 systemd 用户服务失败（不影响 XDG 自启动）: {exc}")

    def on_auto_start_changed(self, checked):
        try:
            self.set_auto_start(bool(checked))
            core.config["auto_start"] = bool(checked)
            core.config["auto_start_prompt_shown"] = True
            core.save_config()
            self.set_status(t("开机自启动已启用") if checked else t("开机自启动已关闭"))
        except Exception as e:
            if hasattr(self, "auto_start_check"):
                self.auto_start_check.blockSignals(True)
                self.auto_start_check.setChecked(not bool(checked))
                self.auto_start_check.blockSignals(False)
            QMessageBox.warning(self, t("开机自启动"), t("设置开机自启动失败：") + str(e))

    def maybe_show_auto_start_prompt(self):
        """Show the restored first-run auto-start question after first paint.

        Hidden/context-menu launches must never surface a modal dialog.  Closing
        the dialog with the window close button leaves the question pending for
        the next normal launch; either explicit answer records it permanently.
        """
        if core.config.get("auto_start_prompt_shown", False):
            return
        if core.config.get("auto_start", False):
            return
        if core.hide_window or not self.isVisible():
            return
        if getattr(self, "_auto_start_prompt_open", False):
            return
        self.show_auto_start_prompt()

    def show_auto_start_prompt(self):
        from ui.dialog_style import apply_dialog_title

        self._auto_start_prompt_open = True
        dialog = QDialog(self)
        dialog.setObjectName("AutoStartPrompt")
        dialog.setWindowTitle(t("开机自启动建议"))
        dialog.setModal(True)
        dialog.setMinimumWidth(500)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        if os.path.exists(self.icon_path):
            dialog.setWindowIcon(QIcon(self.icon_path))

        main = QVBoxLayout(dialog)
        main.setContentsMargins(24, 22, 24, 20)
        main.setSpacing(14)

        content_row = QHBoxLayout()
        content_row.setSpacing(18)
        logo = QLabel(dialog)
        logo.setObjectName("AutoStartLogo")
        logo.setFixedSize(88, 88)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_path = self._img_path("hello.png")
        pixmap = QPixmap(logo_path) if os.path.exists(logo_path) else QPixmap()
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(82, 82, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        content_row.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)

        copy_layout = QVBoxLayout()
        copy_layout.setSpacing(10)
        title = QLabel(t("您是否想要开机自启动本工具？"))
        title.setWordWrap(True)
        title.setProperty("dialogTitle", True)
        apply_dialog_title(title)
        copy_layout.addWidget(title)

        info = QLabel(t("开机自启动后，程序会在登录后静默启动并驻留系统托盘。以后可随时在“设置 → 后台与启动”中关闭。"))
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.setProperty("muted", True)
        copy_layout.addWidget(info)
        content_row.addLayout(copy_layout, 1)
        main.addLayout(content_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        btn_no = QPushButton(t("暂不启用，并不再提示"))
        btn_yes = QPushButton(t("启用"))
        btn_no.setMinimumHeight(36)
        btn_yes.setMinimumHeight(36)
        btn_yes.setDefault(True)
        buttons.addWidget(btn_no)
        buttons.addWidget(btn_yes)
        main.addLayout(buttons)

        def _sync_checkbox(enabled: bool) -> None:
            if not hasattr(self, "auto_start_check"):
                return
            try:
                self.auto_start_check.blockSignals(True)
                self.auto_start_check.setChecked(enabled)
            finally:
                self.auto_start_check.blockSignals(False)

        def accept_startup() -> None:
            try:
                self.set_auto_start(True)
                core.config["auto_start"] = True
                core.config["auto_start_prompt_shown"] = True
                core.save_config()
                _sync_checkbox(True)
                self.set_status(t("开机自启动已启用"))
                dialog.accept()
            except Exception as exc:
                QMessageBox.warning(dialog, t("开机自启动"), t("设置开机自启动失败：") + str(exc))

        def reject_startup() -> None:
            core.config["auto_start"] = False
            core.config["auto_start_prompt_shown"] = True
            core.save_config()
            _sync_checkbox(False)
            self.set_status(t("已跳过开机自启动"))
            dialog.reject()

        btn_yes.clicked.connect(accept_startup)
        btn_no.clicked.connect(reject_startup)
        try:
            dialog.exec()
        finally:
            self._auto_start_prompt_open = False
            dialog.deleteLater()

    def on_tray_notify_changed(self, checked):
        core.config["tray_notify"] = bool(checked)
        core.save_config()

    def on_silent_update_check_on_startup_changed(self, checked):
        core.config["silent_update_check_on_startup"] = bool(checked)
        core.save_config()
        self.set_status(t("启动静默更新检查已启用") if checked else t("启动静默更新检查已关闭"))

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
            self._refresh_shell_ui_later()

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

    def _tray_action_unavailable_message(self, action: str) -> str:
        availability = wallpaper_action_availability(core.config.get("mode"), action)
        if availability.reason == "requires_html":
            return t("此操作仅在 HTML 模式下可用")
        return t("此操作仅在幻灯片放映模式下可用")

    def _dispatch_tray_action(self, action: str) -> None:
        availability = wallpaper_action_availability(core.config.get("mode"), action)
        if not availability.allowed:
            message = self._tray_action_unavailable_message(action)
            self.set_status(message)
            try:
                if self.tray is not None and core.config.get("tray_notify", True):
                    self.tray.showMessage(
                        APP_DISPLAY_NAME,
                        message,
                        QSystemTrayIcon.MessageIcon.Information,
                        2500,
                    )
            except Exception:
                pass
            return
        callbacks = {
            "show": self.show_from_tray,
            # Tray clicks should release the native menu immediately.  Route
            # wallpaper actions through the existing coalescing worker instead
            # of entering the main-window operation queue from the menu callback.
            "previous": lambda: core.queue_ipc_wallpaper_command("previous"),
            "next": lambda: core.queue_ipc_wallpaper_command("next"),
            "random": lambda: core.queue_ipc_wallpaper_command("random"),
            "refresh_html": self.apply_html_wallpaper_from_gui,
            "bing": lambda: self.sync_bing_wallpaper(set_latest=True),
            "jump": self.open_wallpaper_sidebar,
            "about": self.show_about_dialog,
            "exit": self.exit_app,
        }
        if action == "bing" and not is_feature_enabled("bing"):
            return
        if action == "refresh_html" and not is_feature_enabled("html"):
            return
        callback = callbacks.get(action)
        if callback is not None:
            callback()

    def _refresh_tray_action_states(self) -> None:
        for action, menu_action in getattr(self, "_tray_mode_actions", {}).items():
            availability = wallpaper_action_availability(core.config.get("mode"), action)
            menu_action.setEnabled(availability.allowed)
            menu_action.setToolTip(
                "" if availability.allowed else self._tray_action_unavailable_message(action)
            )

    def create_or_update_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            core.log(t("系统托盘不可用，已跳过"))
            return
        icon = QIcon(self.icon_path) if os.path.exists(self.icon_path) else self.windowIcon()
        if self.tray is None:
            self.tray = QSystemTrayIcon(icon, self)
            self.tray.activated.connect(self.on_tray_activated)
        else:
            self.tray.setIcon(icon)
        labels = {
            "show": t("打开设置主界面"),
            "previous": t("上一张壁纸"),
            "next": t("下一张壁纸"),
            "random": t("随机壁纸"),
            "refresh_html": t("刷新 HTML 壁纸"),
            "bing": t("同步必应壁纸"),
            "jump": t("跳转到壁纸"),
            "about": t("关于"),
            "exit": t("退出程序"),
        }
        if not is_feature_enabled("bing"):
            labels.pop("bing", None)
        if not is_feature_enabled("html"):
            labels.pop("refresh_html", None)
        defaults = [name for name in ("show", "previous", "next", "random", "bing", "jump", "about", "exit") if name in labels]
        actions = core.config.get("tray_menu_items") or defaults
        if isinstance(actions, list) and actions and isinstance(actions[0], dict):
            actions = [item.get("action") for item in actions if item.get("enabled", True)]
        actions = [action for action in actions if action in labels and action != "refresh_html"]
        if is_feature_enabled("html") and normalize_mode_key(core.config.get("mode")) == "HTML":
            insert_at = next(
                (i for i, name in enumerate(actions) if name in {"bing", "jump", "about", "exit"}),
                len(actions),
            )
            actions.insert(insert_at, "refresh_html")
        if not actions:
            actions = defaults

        # v1.4.4: Reuse a persistent QMenu instead of rebuilding on every call.
        # Rebuilding forces Qt to re-resolve style/layout/icon metrics each time,
        # which is the primary cause of ~1s delay on right-click.
        # We only clear and repopulate actions; the QMenu object itself persists.
        if not hasattr(self, "_tray_menu") or self._tray_menu is None:
            self._tray_menu = QMenu()
            self._prepare_popup_menu(self._tray_menu)
            self._tray_menu.aboutToShow.connect(self._refresh_tray_action_states)
        menu = self._tray_menu
        # Clear existing actions and rebuild (cheaper than creating a new QMenu)
        menu.clear()
        self._tray_mode_actions = {}
        for index, name in enumerate(actions):
            if index and name in {"about", "exit"}:
                menu.addSeparator()
            action = menu.addAction(labels[name])
            action.triggered.connect(
                lambda _checked=False, name=name: self._dispatch_tray_action(name)
            )
            if name in {"previous", "next", "random", "refresh_html"}:
                self._tray_mode_actions[name] = action
        self._refresh_tray_action_states()
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(APP_DISPLAY_NAME)
        self.tray.show()
        self._refresh_shell_ui_later()
        # v1.4.4: Pre-warm by actually showing+hiding the menu, not just sizeHint().
        # sizeHint() forces layout but not native window creation; show/hide
        # forces the platform window to be created, which is the real bottleneck.
        # Only do this once per menu instance.
        if not getattr(self, "_tray_menu_prewarmed", False):
            QTimer.singleShot(500, lambda: self._prewarm_tray_menu(menu))

    def _prewarm_tray_menu(self, menu):
        """Force Qt to create the native menu window at startup.

        v1.4.4: The previous sizeHint() approach was insufficient because it
        only forces layout calculation, not native window creation. By actually
        showing the menu off-screen and immediately hiding it, we force the
        platform plugin to create the native popup window, resolve the style,
        and decode all icons. The first user-facing right-click then reuses
        this pre-created window for instant popup.
        """
        try:
            menu.sizeHint()
            # Show the menu at an off-screen position, then immediately hide.
            # This forces native window creation + style resolution.
            menu.move(-10000, -10000)
            menu.show()
            menu.hide()
            menu.move(0, 0)
            for action in menu.actions():
                if action.icon():
                    action.icon().availableSizes()
            self._tray_menu_prewarmed = True
        except Exception:
            pass

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            action = str(core.config.get("tray_click_action", "next") or "next")
            if action != "none":
                self._dispatch_tray_action(action)

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.update_preview_if_changed()
        self._refresh_shell_ui_later()

    def update_preview(self):
        path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
        if path and os.path.exists(path):
            self._last_preview_path = path
            self.current_label.setText(path)
            self.current_label.setToolTip(path)
            if str(path).lower().endswith((".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv")):
                self.preview_canvas.set_preview("")
                self.preview_canvas._caption = os.path.basename(path) + " · " + t("视频壁纸播放中")
                self.preview_canvas.update()
            else:
                self.preview_canvas.set_preview(path)
        else:
            self._last_preview_path = ""
            self.current_label.setText("")
            self.current_label.setToolTip(t("未检测到当前壁纸"))
            self.preview_canvas.set_preview("")
        self.refresh_history_list()

    def update_preview_if_changed(self):
        if not self.isVisible() or self.isMinimized():
            return
        path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
        hist_len = core.wallpaper_history_count()
        if path != getattr(self, "_last_preview_path", "") or hist_len != getattr(self, "_last_history_len", -1):
            self.update_preview()

    def _schedule_preview_refresh(self, initial_delay: int = 0):
        """分批刷新预览；性能模式下只做必要刷新，避免连续切换时堆积解码任务。

        v1.4.8: 平衡模式从 4 次延迟刷新减少为 2 次，降低 GUI 线程开销。
        """
        def _first_refresh():
            self.update_preview()
            level = self._perf_level()
            if level == "power_saver":
                delays = (800,)
            elif level == "performance":
                delays = (700,)
            else:
                delays = (300, 800)  # 平衡: 2 次（从 4 次精简）
            for delay in delays:
                QTimer.singleShot(delay, self.update_preview_if_changed)
        if initial_delay and initial_delay > 0:
            QTimer.singleShot(int(initial_delay), _first_refresh)
        else:
            _first_refresh()
    def _clear_wallpaper_list_selection(self) -> None:
        """Clear transient selection/focus without disabling keyboard focus permanently."""
        for widget_name in ("history_list", "favorites_list"):
            widget = getattr(self, widget_name, None)
            if widget is None:
                continue
            previous = widget.blockSignals(True)
            try:
                widget.clearSelection()
                widget.setCurrentRow(-1)
                widget.clearFocus()
            finally:
                widget.blockSignals(previous)

    def _scroll_history_to_latest(self) -> None:
        history = getattr(self, "history_list", None)
        if history is None or history.count() <= 0:
            return
        history.scrollToItem(history.item(0), QAbstractItemView.ScrollHint.PositionAtCenter)
        history.horizontalScrollBar().setValue(history.horizontalScrollBar().minimum())

    def refresh_history_list(self):
        if not hasattr(self, "history_list"):
            return
        previous_block_state = self.history_list.blockSignals(True)
        try:
            self.history_list.clear()
            self._last_history_len = core.wallpaper_history_count()
            for path in core.list_wallpaper_history(limit=8, existing_only=True):
                item = QListWidgetItem()
                item.setToolTip(path)
                item.setData(Qt.UserRole, path)
                item.setSizeHint(QSize(118, 78))
                pix = self._load_icon_pixmap(path, QSize(108, 68))
                if not pix.isNull():
                    item.setIcon(QIcon(pix))
                self.history_list.addItem(item)
            self.history_list.clearSelection()
            self.history_list.setCurrentRow(-1)
        finally:
            self.history_list.blockSignals(previous_block_state)
        self._refresh_favorites_list()
        self._clear_wallpaper_list_selection()
        self._scroll_history_to_latest()
        self._update_favorite_button_state()

    # ── v1.4.7: 收藏夹方法 ──
    def _refresh_favorites_list(self) -> None:
        if not hasattr(self, "favorites_list"):
            return
        previous_block_state = self.favorites_list.blockSignals(True)
        try:
            self.favorites_list.clear()
            for path in core.list_favorites(limit=50, existing_only=True):
                item = QListWidgetItem()
                item.setToolTip(path)
                item.setData(Qt.UserRole, path)
                item.setSizeHint(QSize(118, 78))
                pix = self._load_icon_pixmap(path, QSize(108, 68))
                if not pix.isNull():
                    item.setIcon(QIcon(pix))
                self.favorites_list.addItem(item)
        except Exception as exc:
            core.log(f"刷新收藏夹失败: {exc}", level="WARNING", exc_info=True)
        finally:
            self.favorites_list.blockSignals(previous_block_state)

    def _toggle_favorite_current(self) -> None:
        try:
            path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
            if not path or not os.path.isfile(path):
                self.set_status(t("当前无壁纸可收藏"))
                return
            is_now_favorite = core.toggle_favorite(path)
            self._refresh_favorites_list()
            self._update_favorite_button_state()
            self.set_status(t("已加入收藏夹") if is_now_favorite else t("已从收藏夹移除"))
        except Exception as exc:
            core.log(f"切换收藏失败: {exc}", level="WARNING", exc_info=True)
            QMessageBox.warning(self, t("收藏夹"), t("保存收藏失败：") + str(exc))

    def _update_favorite_button_state(self) -> None:
        try:
            if not hasattr(self, "btn_favorite_current"):
                return
            path = core.config.get("current_wallpaper") or ""
            if path and core.is_favorite(path):
                self.btn_favorite_current.setText(t("取消收藏"))
                self.btn_favorite_current.setToolTip(t("从收藏夹移除当前壁纸"))
            else:
                self.btn_favorite_current.setText(t("收藏当前"))
                self.btn_favorite_current.setToolTip(t("把当前壁纸加入收藏夹（收藏夹不会随历史滚动消失）"))
        except Exception as exc:
            core.log(f"更新收藏按钮状态失败: {exc}", level="WARNING")

    def _apply_static_wallpaper_item(self, path: str, source: str) -> None:
        """Apply a recent/favorite image as one serialized mode-transition transaction."""
        if not path or not os.path.exists(path):
            return
        self._set_combo_current_data(self.mode_combo, "图片")
        self._clear_wallpaper_list_selection()
        self._pending_static_wallpaper_list_reset = True

        def _work():
            core.config["mode"] = "图片"
            core.stop_slideshow()
            core.stop_video_wallpaper()
            core.stop_html_wallpaper()
            return core.set_wallpaper(path, source)

        self._run_mode_transition(t("正在切换单张图片…"), _work)

    def _apply_favorite_item(self, item) -> None:
        try:
            path = item.data(Qt.UserRole) if item else ""
            self._apply_static_wallpaper_item(path, t("收藏夹"))
        except Exception as exc:
            try:
                core.log(f"应用收藏壁纸失败: {exc}", level="WARNING")
            except Exception:
                pass

    def open_history_item_location_by_item(self, item) -> None:
        try:
            path = item.data(Qt.UserRole) if item else ""
            self._open_file_location(path)
        except Exception:
            pass

    def _show_favorite_context_menu(self, pos) -> None:
        try:
            from PySide6.QtWidgets import QMenu
            item = self.favorites_list.itemAt(pos)
            if not item:
                return
            path = item.data(Qt.UserRole) or ""
            menu = QMenu(self)
            self._prepare_popup_menu(menu)
            act_remove = menu.addAction(t("移除收藏"))
            act_open = menu.addAction(t("打开文件位置"))
            # v1.4.3: Use popup() instead of exec() to avoid blocking the event
            # loop. exec() opens a nested modal event loop that can freeze the
            # UI if a touch event swallows the release. popup() is async and
            # lets the event loop continue running. We track the selected action
            # via the triggered signal.
            global_pos = self.favorites_list.viewport().mapToGlobal(pos)
            _selected = {"action": None}
            def _on_triggered(action):
                _selected["action"] = action
            menu.triggered.connect(_on_triggered)
            menu.aboutToHide.connect(lambda: self._handle_favorite_context_result(_selected["action"], act_remove, act_open, path))
            menu.popup(global_pos)
        except Exception as exc:
            try:
                core.log(f"收藏右键菜单失败: {exc}", level="WARNING")
            except Exception:
                pass

    def _handle_favorite_context_result(self, action, act_remove, act_open, path):
        """Handle the result of the async favorite context menu (v1.4.3)."""
        try:
            if action == act_remove:
                if core.remove_favorite(path):
                    self._refresh_favorites_list()
                    self._update_favorite_button_state()
                    self.set_status(t("已从收藏夹移除"))
            elif action == act_open:
                self._open_file_location(path)
        except Exception as exc:
            core.log(f"移除收藏失败: {exc}", level="WARNING", exc_info=True)
            QMessageBox.warning(self, t("收藏夹"), t("移除收藏失败：") + str(exc))

    def _clear_all_favorites(self) -> None:
        try:
            reply = QMessageBox.question(
                self, t("清空收藏"),
                t("确定清空全部收藏吗？不会删除壁纸文件。"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            core.clear_favorites()
            self._refresh_favorites_list()
            self._update_favorite_button_state()
            self.set_status(t("收藏夹已清空"))
        except Exception as exc:
            QMessageBox.warning(self, t("清空收藏"), t("清空失败：") + str(exc))

    def _open_file_location(self, path: str):
        if not path or not os.path.exists(path):
            return
        ok, message = _open_path_in_linux_file_manager(path)
        if not ok:
            QMessageBox.warning(self, t("跳转失败"), message)

    def _load_icon_pixmap(self, path: str, size: QSize) -> QPixmap:
        try:
            stat = os.stat(path)
            cache_key = (path, int(stat.st_mtime), int(stat.st_size), int(size.width()), int(size.height()))
        except Exception:
            cache_key = (path, 0, 0, int(size.width()), int(size.height()))
        cache = getattr(self, "_icon_pixmap_cache", None)
        if cache is None:
            cache = OrderedDict()
            self._icon_pixmap_cache = cache
        elif not isinstance(cache, OrderedDict):
            cache = OrderedDict(cache)
            self._icon_pixmap_cache = cache
        cached = cache.get(cache_key)
        if cached is not None:
            cache.move_to_end(cache_key)
            return cached
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        _pl = self._perf_level()
        _alloc = 48 if _pl == "power_saver" else (64 if _pl == "performance" else 128)
        reader.setAllocationLimit(_alloc)
        original = reader.size()
        if original.isValid():
            scaled = original.scaled(size, Qt.KeepAspectRatio)
            if scaled.isValid():
                reader.setScaledSize(scaled)
        image = reader.read()
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        cache[cache_key] = pixmap
        cache.move_to_end(cache_key)
        _pl2 = self._perf_level()
        max_cache_items = 32 if _pl2 == "power_saver" else (64 if _pl2 == "performance" else 96)
        while len(cache) > max_cache_items:
            cache.popitem(last=False)
        return pixmap

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
        self._apply_static_wallpaper_item(path, t("历史记录"))

    def open_current_folder(self):
        path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
        target = path if path and os.path.exists(path) else core.config.get("slide_folder", "")
        if target:
            ok, message = _open_path_in_linux_file_manager(target)
            if not ok:
                QMessageBox.warning(self, t("跳转失败"), message)

    def open_wallpaper_sidebar(self) -> None:
        sb = getattr(self, "_sidebar", None)
        if sb is not None:
            try:
                if hasattr(sb, "_is_closing") and not sb._is_closing:
                    sb.raise_()
                    sb.activateWindow()
                    return
            except Exception:
                pass
            self._sidebar = None

        folder = core.config.get("slide_folder", "")
        current = core.config.get("current_wallpaper", "") or core.get_current_wallpaper()

        if not folder or not os.path.isdir(folder):
            show_info(self, t("提示"), t("请先在软件中设置壁纸文件夹"))
            return

        # Bug 4 fix: 删除 Wayland 短路（之前会打开 Dolphin 文件管理器代替 sidebar）。
        # 现在 WallpaperSidebar 在 Wayland 下使用 Qt.Popup 窗口标志，由合成器
        # 自动定位并处理外部点击关闭，不再需要 _OutsideClickShield（Wayland 不支持）。

        from ui.sidebar import WallpaperSidebar

        def _switch(path: str) -> None:
            try:
                core.set_wallpaper(path, t("侧边栏切换"))
                QTimer.singleShot(50, self.update_preview)
            except Exception as exc:
                core.log(f"侧边栏切换壁纸失败: {exc}")

        sidebar_log = self._log_file_path() if core.config.get("log_enabled", False) else None
        self._sidebar = WallpaperSidebar(
            self, folder, current, sidebar_log,
            show_message=lambda title, msg: show_info(self, title, msg),
            switch_wallpaper=_switch,
        )
        self._sidebar.closed.connect(lambda: setattr(self, "_sidebar", None))

    def on_bing_auto_options_changed(self, *args, save: bool = True):
        if not hasattr(self, "bing_auto_update_check"):
            return
        try:
            core.config["bing_auto_update_on_start"] = bool(self.bing_auto_update_check.isChecked())
            core.config["bing_auto_update_count"] = max(1, min(16, int(self.bing_auto_update_count_spin.value())))
            core.config["bing_auto_delete_on_start"] = bool(self.bing_auto_delete_check.isChecked())
            core.config["bing_auto_delete_count"] = max(1, min(200, int(self.bing_auto_delete_count_spin.value())))
            if save:
                core.save_config()
        except Exception as exc:
            core.log(f"保存必应启动选项失败: {exc}")

    def _delete_oldest_bing_cached(self, count: int) -> int:
        cache_dir = core.config.get("bing_cache_dir", "") or ""
        if not cache_dir or not os.path.isdir(cache_dir):
            return 0
        try:
            from services.bing import BingDownloader
            return BingDownloader(cache_dir=cache_dir).delete_oldest_cached_wallpapers(count=count, keyword="bing")
        except Exception as exc:
            core.log(f"自动删除必应缓存失败: {exc}")
            return 0

    def run_bing_startup_tasks(self):
        if getattr(self, "_startup_bing_automation_done", False):
            return
        self._startup_bing_automation_done = True
        cache_dir = core.config.get("bing_cache_dir", "") or ""
        do_delete = bool(core.config.get("bing_auto_delete_on_start", False))
        do_update = bool(core.config.get("bing_auto_update_on_start", False))
        if not (do_delete or do_update):
            return
        if not cache_dir:
            self.set_status(t("必应启动自动操作已跳过：未设置缓存目录"))
            return

        def _run_bing_startup_tasks():
            deleted = 0
            try:
                if do_delete:
                    count_delete = max(1, min(200, int(core.config.get("bing_auto_delete_count", 1) or 1)))
                    deleted = self._delete_oldest_bing_cached(count_delete)
                self.bing_result_signal.emit(True, f"启动时已自动删除 {deleted} 张最旧必应缓存壁纸" if do_delete else t("必应启动自动操作准备完成"), "")
                if do_update:
                    count = max(1, min(16, int(core.config.get("bing_auto_update_count", 1) or 1)))
                    QTimer.singleShot(0, lambda: self._start_bing_auto_update(cache_dir, count))
            except Exception as exc:
                self.bing_result_signal.emit(False, f"必应启动自动操作失败：{exc}", "")

        threading.Thread(target=_run_bing_startup_tasks, daemon=True).start()

    def _start_bing_auto_update(self, cache_dir: str, count: int):
        if hasattr(self, "bing_cache_edit"):
            self.bing_cache_edit.setText(cache_dir)
        if hasattr(self, "bing_count_spin"):
            self.bing_count_spin.setValue(count)
        self.sync_bing_wallpaper(set_latest=True, force_count=count)

    def _on_bing_cache_source_changed(self, _previous: str, current: str) -> None:
        core.config["bing_next_index"] = 0
        if current and os.path.isdir(current):
            self.refresh_bing_cache_list()

    def _bing_downloader(self):
        from services.bing import BingDownloader
        cache_dir = self._bing_cache_source.commit(required=True)
        if not cache_dir:
            raise ValueError(t("请先填写或选择有效的必应壁纸缓存目录"))
        core.config["bing_sync_count"] = int(self.bing_count_spin.value())
        if hasattr(self, "bing_auto_update_check"):
            self.on_bing_auto_options_changed(save=False)
        core.save_config()
        return BingDownloader(cache_dir=cache_dir)

    def refresh_bing_cache_list(self):
        if not hasattr(self, "bing_list"):
            return
        self.bing_list.clear()
        cache_dir = core.config.get("bing_cache_dir", "") or ""
        if not cache_dir:
            if hasattr(self, "bing_status"):
                self.bing_status.setText(t("首次使用请先选择必应壁纸缓存目录"))
            return
        try:
            from services.bing import BingDownloader
            for path in BingDownloader(cache_dir=cache_dir).get_cached_wallpapers():
                item = QListWidgetItem(os.path.basename(path))
                item.setData(Qt.UserRole, path)
                self.bing_list.addItem(item)
        except Exception as e:
            core.log(f"刷新必应缓存列表失败: {e}")

    def choose_bing_cache_dir(self):
        folder = QFileDialog.getExistingDirectory(self, t("选择必应壁纸缓存目录"), self.bing_cache_edit.text() or str(Path.home()))
        if not folder:
            return
        self.bing_cache_edit.setText(folder)
        if self._bing_cache_source.commit(required=True):
            self.refresh_bing_cache_list()

    def on_bing_selection_changed(self):
        item = self.bing_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        if path and os.path.exists(path):
            self._pending_bing_path = path
            self._bing_preview_timer.start(80)

    def apply_pending_bing_preview(self):
        path = getattr(self, "_pending_bing_path", "")
        if not path or not os.path.exists(path):
            return
        self.preview_canvas.set_preview(path)
        self.current_label.setText(path)
        self.current_label.setToolTip(path)
        if hasattr(self, "bing_status"):
            self.bing_status.setText(t("已选择预览：") + f"{path}")
        # 更新必应标签页内嵌缩略图预览
        lbl = getattr(self, "_bing_preview_label", None)
        if lbl is not None:
            try:
                from PySide6.QtGui import QPixmap
                px = QPixmap(path)
                if not px.isNull():
                    w, h = max(lbl.width() - 6, 60), max(lbl.height() - 6, 40)
                    lbl.setPixmap(px.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    lbl.clear()
                    lbl.setText(t("无法加载预览"))
            except Exception:
                lbl.clear()
                lbl.setText(t("预览失败"))

    def use_bing_cache_as_slideshow(self):
        folder = self._bing_cache_source.commit(required=True, show_dialog=True)
        if not folder or not os.path.isdir(folder):
            if folder:
                show_warning(self, t("必应壁纸"), t("请先同步壁纸，使缓存目录实际存在。"))
            return
        core.config["slide_folder"] = folder
        core.config["mode"] = "幻灯片放映"
        self.folder_edit.setText(folder)
        self._set_combo_current_data(self.mode_combo, "幻灯片放映")
        core.save_config()
        def _work():
            core.stop_video_wallpaper()
            return core.restart_slideshow()
        self._run_mode_transition(t("正在切换幻灯片放映…"), _work)
        self.set_status(t("必应缓存已设为幻灯片来源"))

    def save_selected_bing_as(self):
        item = self.bing_list.currentItem()
        if not item:
            show_info(self, t("必应壁纸"), t("请先在列表中选择一张已缓存的必应壁纸。"))
            return
        src = item.data(Qt.UserRole)
        if not src or not os.path.exists(src):
            show_warning(self, t("必应壁纸"), t("选中的缓存文件不存在。"))
            return
        dst, _ = QFileDialog.getSaveFileName(self, t("另存必应壁纸"), os.path.join(str(Path.home()), os.path.basename(src)), t("JPEG 图片 (*.jpg);;所有文件 (*.*)"))
        if not dst:
            return
        try:
            import shutil
            shutil.copy2(src, dst)
            self.set_status(t("已另存为：") + f"{dst}")
        except Exception as e:
            QMessageBox.warning(self, t("另存失败"), str(e))

    def sync_bing_wallpaper(self, set_latest: bool = True, continue_from_saved: bool = False, force_count: int | None = None):
        cache_dir = self._bing_cache_source.commit(required=True, show_dialog=True)
        if not cache_dir:
            return
        resolution = self.bing_resolution.currentData() or "auto"
        count = max(1, min(16, int(force_count if force_count is not None else self.bing_count_spin.value())))
        start_index = max(0, int(core.config.get("bing_next_index", 0))) if continue_from_saved else 0
        core.config["bing_cache_dir"] = cache_dir
        core.config["bing_sync_count"] = count
        core.save_config()

        for btn in (self.bing_sync_btn, self.bing_multi_btn, getattr(self, "bing_continue_btn", None)):
            if btn is not None:
                btn.setEnabled(False)
        self.bing_progress.setValue(0)
        mode_text = f"正在从第 {start_index + 1} 张开始继续同步必应壁纸..." if continue_from_saved else "正在同步必应壁纸..."
        self.bing_status.setText(mode_text)
        self.begin_operation(mode_text, cancellable=True)

        def _work():
            try:
                from services.bing import BingDownloader
                downloader = BingDownloader(cache_dir=cache_dir)
                paths = []
                seen_paths = set()
                infos = downloader.fetch_history(days=count, resolution=resolution, start_index=start_index)
                total = max(1, len(infos))
                # Bug 7/8 fix: 下载阶段占 0-80%，应用阶段占 80-100%。
                # 之前下载完后调用 set_wallpaper 是同步阻塞，期间进度条
                # 冻结在 100% (idx/total)，给用户"卡死"的错觉。现在通过
                # progress_cb 在应用阶段也发出中间进度，进度条会从 80%
                # 平滑过渡到 100%。
                DOWNLOAD_WEIGHT = 0.8
                APPLY_WEIGHT = 0.2
                for idx, info in enumerate(infos, 1):
                    if self._current_operation_cancel.is_set():
                        self._emit_bing_result(False, t("必应壁纸同步已终止"), "")
                        return
                    path = downloader.download_wallpaper(info)
                    if path and path not in seen_paths:
                        paths.append(path)
                        seen_paths.add(path)
                    # Bug 7/8 fix: 下载阶段进度通过信号转发到 GUI 线程，
                    # 消息格式 "必应同步进度：idx/total"，_on_bing_finished
                    # 会解析并设置进度条到 idx/total * 80% 区间。
                    # 不在子线程直接操作 GUI 控件（线程不安全）。
                    download_pct = int(idx / total * DOWNLOAD_WEIGHT * 100)
                    self.bing_result_signal.emit(
                        True,
                        f"必应用进度：{download_pct}/{idx}/{total}",
                        path or "",
                    )
                if not paths:
                    self._emit_bing_result(False, t("没有同步到必应壁纸"), "")
                    return

                next_index = start_index + len(infos)
                if next_index > int(core.config.get("bing_next_index", 0)) or not continue_from_saved:
                    core.config["bing_next_index"] = max(next_index, int(core.config.get("bing_next_index", 0)))
                    core.save_config()

                deleted = 0
                if core.config.get("bing_auto_cleanup", False):
                    deleted = downloader.cleanup_cached_wallpapers(max_count=count, keyword="bing")

                latest = paths[0]
                if self._current_operation_cancel.is_set():
                    self._emit_bing_result(False, t("必应壁纸同步已终止"), "")
                    return
                if set_latest:
                    # Bug 7/8 fix: progress_cb 让 set_wallpaper 在应用阶段
                    # （configure_fit_mode + set_wallpaper_platform + refresh_shell_ui）
                    # 也发出进度信号，从 80% 平滑过渡到 100%。
                    # 通过 bing_result_signal 转发到 GUI 线程，避免子线程
                    # 直接操作 GUI 控件。
                    apply_base = int(DOWNLOAD_WEIGHT * 100)  # 80
                    def _apply_cb(status_text: str, percent: float) -> None:
                        # percent 是 set_wallpaper_direct 内部的 0.0-1.0
                        # 进度，映射到全局 80-100% 区间。
                        global_pct = int(apply_base + percent * APPLY_WEIGHT * 100)
                        # 限制在 0-99 之间，100 留给最终完成消息
                        global_pct = max(0, min(99, global_pct))
                        # 通过信号转发到 GUI 线程，路径消息留空（避免被
                        # _on_bing_finished 当作下载进度解析）。
                        self.bing_result_signal.emit(
                            True,
                            f"应用进度：{global_pct}/{status_text}",
                            "",
                        )
                    core.set_wallpaper(latest, t("必应壁纸"), progress_cb=_apply_cb)
                    cleanup_note = f"；已自动删除 {deleted} 张过量 bing 缓存" if deleted else ""
                    self._emit_bing_result(True, f"已同步 {len(paths)} 张并设置最新必应壁纸{cleanup_note}，下次可从第 {core.config.get('bing_next_index', 0) + 1} 张继续", latest)
                else:
                    cleanup_note = f"；已自动删除 {deleted} 张过量 bing 缓存" if deleted else ""
                    self._emit_bing_result(True, f"已同步 {len(paths)} 张必应壁纸到缓存目录{cleanup_note}，下次可从第 {core.config.get('bing_next_index', 0) + 1} 张继续", latest)
            except Exception as e:
                self._emit_bing_result(False, f"同步必应壁纸失败：{e}", "")

        self._bing_worker_thread = threading.Thread(target=_work, daemon=True)
        self._bing_worker_thread.start()

    def _emit_bing_result(self, ok: bool, message: str, path: str):
        self.bing_result_signal.emit(ok, message, path)

    def _on_bing_finished(self, ok: bool, message: str, path: str):
        # Bug 7/8 fix: 支持三种进度消息格式：
        # 1. 旧格式 "必应同步进度：idx/total"（兼容老代码）→ 进度 = idx/total * 80%
        # 2. 新下载格式 "必应用进度：N/idx/total" → 进度 = N（0-80%）
        # 3. 新应用格式 "应用进度：N/状态" → 进度 = N（80-99%）
        # 完成消息不含 "进度" 关键字。
        is_progress = t("进度") in message
        if not is_progress:
            self._bing_worker_thread = None
            self.bing_sync_btn.setEnabled(True)
            self.bing_multi_btn.setEnabled(True)
            if hasattr(self, "bing_continue_btn"):
                self.bing_continue_btn.setEnabled(True)
            self.finish_operation(message)
        if is_progress:
            try:
                payload = message.split("：", 1)[1] if "：" in message else ""
                if message.startswith("必应用进度：") or message.startswith("应用进度："):
                    # 新格式：直接取第一个 / 前的数字作为百分比
                    pct_str = payload.split("/", 1)[0]
                    pct = int(pct_str)
                    self.bing_progress.setValue(max(0, min(99, pct)))
                else:
                    # 旧格式 "idx/total" → 0-80% 区间
                    done, total = payload.split("/", 1)
                    self.bing_progress.setValue(int(int(done) / max(1, int(total)) * 80))
            except Exception:
                pass
        else:
            self.bing_progress.setValue(100 if ok else 0)
        status_text = message + ((" · " + os.path.basename(path)) if path else "")
        self.bing_status.setText(status_text)
        self.bing_status.setToolTip(message + (("\n" + path) if path else ""))
        self.set_status(message)
        if is_progress:
            return
        self.refresh_bing_cache_list()
        self.update_preview()
        if not ok:
            self._show_non_modal_warning(t("必应壁纸"), message)

    def open_update_target(self):
        combo = getattr(self, "update_asset_combo", None)
        url = ""
        try:
            if combo is not None and combo.currentIndex() >= 0:
                url = str(combo.currentData() or "")
        except RuntimeError:
            url = ""
        url = url or getattr(self, "_latest_asset_url", "") or getattr(self, "_latest_release_url", "") or GITHUB_LATEST_RELEASE_URL
        QDesktopServices.openUrl(QUrl(url))

    def _format_release_asset_label(self, asset: dict) -> str:
        name = str(asset.get("name") or t("未命名附件"))
        size = int(asset.get("size") or 0)
        if size > 0:
            return f"{name}（{size / 1024 / 1024:.1f} MB）"
        return name

    def _update_asset_selector(self, info: dict) -> None:
        combo = getattr(self, "update_asset_combo", None)
        if combo is None:
            return
        try:
            combo.blockSignals(True)
            combo.clear()
            assets = list(info.get("compatible_assets") or info.get("assets") or [])
            for asset in assets:
                url = str(asset.get("download_url") or "")
                if not url:
                    continue
                combo.addItem(self._format_release_asset_label(asset), url)
            selected = info.get("selected_asset") or {}
            selected_url = str(selected.get("download_url") or "")
            if selected_url:
                idx = combo.findData(selected_url)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.setEnabled(combo.count() > 0)
            combo.setVisible(combo.count() > 0)
            label = getattr(self, "update_asset_label", None)
            if label is not None:
                label.setVisible(combo.count() > 0)
        finally:
            try:
                combo.blockSignals(False)
            except Exception:
                pass

    def open_project_homepage(self):
        QDesktopServices.openUrl(QUrl(GITHUB_PROJECT_URL))

    def _set_update_status_text(self, text: str):
        widget = getattr(self, "update_status_label", None)
        if widget is None:
            return
        full_text = str(text or "")
        self._last_update_status_full_text = full_text
        limit = 1800
        display_text = full_text
        too_long = len(full_text) > limit
        if too_long:
            display_text = full_text[:limit].rstrip() + "\n\n" + t("内容较多，已折叠显示。点击“查看详细”打开完整内容。")
        try:
            if isinstance(widget, QTextEdit):
                widget.setPlainText(display_text)
            else:
                widget.setText(display_text)
            detail_btn = getattr(self, "update_detail_btn", None)
            if detail_btn is not None:
                detail_btn.setVisible(too_long)
                detail_btn.setEnabled(too_long)
        except RuntimeError:
            pass

    def show_update_detail_dialog(self):
        text = getattr(self, "_last_update_status_full_text", "") or ""
        dlg = QDialog(self)
        dlg.setWindowTitle(t("更新详情"))
        dlg.resize(720, 560)
        dlg.setMinimumSize(620, 420)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        box = QTextEdit(dlg)
        box.setReadOnly(True)
        box.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        box.setPlainText(text)
        layout.addWidget(box, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton(t("关闭"))
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dlg.exec()

    def _remember_update_result(self, ok: bool, message: str, info: dict, *, source: str) -> None:
        self._startup_update_result = {
            "ok": bool(ok),
            "message": str(message or ""),
            "info": dict(info or {}),
            "source": source,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _format_update_status_text(self, ok: bool, message: str, info: dict, *, source: str = "manual") -> str:
        source_text = t("启动静默检查") if source == "startup" else t("手动检查")
        if not ok:
            return f"{source_text}：{message}"
        selected_asset = info.get("selected_asset") or {}
        compatible_assets = list(info.get("compatible_assets") or [])
        asset_text = ""
        if compatible_assets:
            lines = [self._format_release_asset_label(asset) for asset in compatible_assets[:12]]
            more = len(compatible_assets) - len(lines)
            if more > 0:
                lines.append(t("还有") + f" {more} " + t("个附件可在发布页查看"))
            asset_text = "\n" + t("可选附件：") + "\n- " + "\n- ".join(lines)
        elif selected_asset:
            asset_text = f"\n{t('附件：')}{self._format_release_asset_label(selected_asset)}"
        notes = (info.get("body") or "").strip()
        if len(notes) > 20000:
            notes = notes[:20000].rstrip() + "..."
        checked_at = ""
        remembered = getattr(self, "_startup_update_result", None) or {}
        if source == remembered.get("source"):
            checked_at = remembered.get("checked_at") or ""
        checked_line = f"\n{t('检查时间：')}{checked_at}" if checked_at else ""
        return (
            f"{source_text}：{message}{checked_line}\n"
            f"{t('更新源：')}GitHub Release\n"
            f"{t('当前版本：')}v{APP_VERSION}\n"
            f"{t('最新版本：')}{info.get('tag') or info.get('version')}\n"
            f"{t('发布名称：')}{info.get('name') or t('未命名')}{asset_text}\n\n"
            f"{notes or t('暂无更新说明')}"
        )

    def _apply_update_links(self, info: dict) -> None:
        self._latest_release_url = info.get("url") or GITHUB_LATEST_RELEASE_URL
        selected_asset = info.get("selected_asset") or {}
        self._latest_asset_url = selected_asset.get("download_url", "") if selected_asset else ""
        self._update_asset_selector(info)
        if hasattr(self, "update_download_btn"):
            has_asset = bool(self._latest_asset_url) or bool(getattr(self, "update_asset_combo", None) and self.update_asset_combo.count())
            self.update_download_btn.setEnabled(True)
            self.update_download_btn.setText(t("下载所选附件") if has_asset else t("打开发布页"))

    def start_update_check(self, button=None):
        if not is_feature_enabled("updates") or UpdateChecker is None:
            self._set_update_status_text(t("当前构建未包含更新检查功能"))
            return
        if button is not None:
            button.setEnabled(False)
        self.begin_operation(t("正在检查更新…"))
        self._set_update_status_text("正在检查 GitHub Release 更新源...")
        if hasattr(self, "update_download_btn"):
            self.update_download_btn.setEnabled(False)
        self._update_checker = UpdateChecker()
        self._update_checker.finished.connect(lambda ok, msg, info, button=button: self.on_update_checked(ok, msg, info, button))
        self._update_checker.start()

    def on_update_checked(self, ok: bool, message: str, info: dict, button=None):
        if button is not None:
            button.setEnabled(True)
        self.finish_operation(message)
        self._remember_update_result(ok, message, info, source="manual")
        if ok:
            self._apply_update_links(info)
        elif hasattr(self, "update_download_btn"):
            self.update_download_btn.setEnabled(True)
            self.update_download_btn.setText(t("打开发布页"))
        self._set_update_status_text(self._format_update_status_text(ok, message, info, source="manual"))

    def start_startup_update_check(self):
        """Run a silent, non-blocking update check after startup settles."""
        if not is_feature_enabled("updates") or UpdateChecker is None:
            return
        if not UPDATE_CHECK_ON_STARTUP or not bool(core.config.get("silent_update_check_on_startup", True)):
            return
        if getattr(self, "_startup_update_checker", None) is not None:
            return
        try:
            core.log("启动流程：静默后台检查更新")
            self._startup_update_checker = UpdateChecker(current_version=APP_VERSION, timeout=UPDATE_CHECK_TIMEOUT_SECONDS)
            self._startup_update_checker.finished.connect(self.on_startup_update_checked)
            self._startup_update_checker.start()
        except Exception as exc:
            try:
                core.log(f"启动静默更新检查跳过: {exc}")
            except Exception:
                pass

    def on_startup_update_checked(self, ok: bool, message: str, info: dict):
        self._startup_update_checker = None
        self._remember_update_result(ok, message, info, source="startup")
        if ok:
            self._apply_update_links(info)
        try:
            if ok and info.get("has_update"):
                version_label = info.get("tag") or info.get("version") or ""
                core.log(f"启动静默更新检查：发现新版本 {version_label}（{PLATFORM_LABEL}）")
                if not self._current_operation_name:
                    self.set_status(t("启动静默更新检查发现新版本"))
            else:
                core.log(f"启动静默更新检查：{message}")
        except Exception:
            pass
        if hasattr(self, "update_status_label"):
            self._set_update_status_text(self._format_update_status_text(ok, message, info, source="startup"))

    def show_about_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(t("关于 上一个桌面背景"))
        # Bug 15 fix: 760px 初始高度不足以容纳所有内容（logo+图+版本+更新框+链接+按钮），
        # 底部按钮会被裁切。提升到 820px 让初始布局完整可见。
        dlg.resize(720, 820)
        dlg.setMinimumSize(660, 640)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._about_dlg_logo = QLabel(dlg)
        self._about_dlg_logo.setAlignment(Qt.AlignCenter)
        self._about_dlg_logo.setFixedHeight(86)
        txtlogo_path = self._img_path("txtlogo.png")
        if os.path.exists(txtlogo_path):
            pix = QPixmap(txtlogo_path)
            if not pix.isNull():
                self._about_dlg_logo.setPixmap(pix.scaled(400, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(self._about_dlg_logo)

        about_path = self._img_path("about-window.png")
        if os.path.exists(about_path):
            pix = QPixmap(about_path)
            if not pix.isNull():
                img_label = QLabel(dlg)
                img_label.setAlignment(Qt.AlignCenter)
                img_label.setPixmap(pix.scaled(420, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                layout.addWidget(img_label)

        ver_label = QLabel(f"{APP_DISPLAY_NAME} v{APP_VERSION}")
        ver_label.setAlignment(Qt.AlignCenter)
        # Unified dialog title style — uses px (not pt) so DPI scaling does
        # not double-apply, matching the rest of the dialog hierarchy.
        from ui.dialog_style import apply_dialog_title
        ver_label.setProperty("dialogTitle", True)
        apply_dialog_title(ver_label)
        layout.addWidget(ver_label)

        update_box = QGroupBox(t("版本更新"))
        update_box.setVisible(is_feature_enabled("updates"))
        update_layout = QVBoxLayout(update_box)
        self.update_status_label = QTextEdit()
        self.update_status_label.setReadOnly(True)
        self.update_status_label.setMinimumHeight(130)
        self.update_status_label.setMaximumHeight(190)
        self.update_status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.update_status_label.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        remembered_update = getattr(self, "_startup_update_result", None)
        if remembered_update:
            self.update_status_label.setPlainText(self._format_update_status_text(
                remembered_update.get("ok", False),
                remembered_update.get("message", ""),
                remembered_update.get("info", {}),
                source=remembered_update.get("source", "startup"),
            ))
        else:
            self.update_status_label.setPlainText(t("尚未检查更新"))
        update_layout.addWidget(self.update_status_label)
        asset_row = QHBoxLayout()
        asset_row.setSpacing(8)
        self.update_asset_label = QLabel(t("更新附件"))
        self.update_asset_combo = ShangComboBox()
        self._prepare_combo_popup(self.update_asset_combo)
        self.update_asset_combo.setMinimumWidth(320)
        self.update_asset_label.setVisible(False)
        self.update_asset_combo.setVisible(False)
        asset_row.addWidget(self.update_asset_label)
        asset_row.addWidget(self.update_asset_combo, 1)
        update_layout.addLayout(asset_row)
        update_buttons = QHBoxLayout()
        update_buttons.setSpacing(8)
        check_btn = QPushButton(t("检查更新"))
        check_btn.clicked.connect(lambda: self.start_update_check(check_btn))
        self.update_download_btn = QPushButton(t("打开发布页"))
        self.update_download_btn.setProperty("secondary", True)
        self.update_download_btn.clicked.connect(self.open_update_target)
        project_btn = QPushButton(t("打开项目页"))
        project_btn.setProperty("secondary", True)
        project_btn.clicked.connect(self.open_project_homepage)
        self.update_detail_btn = QPushButton(t("查看详细"))
        self.update_detail_btn.setProperty("secondary", True)
        self.update_detail_btn.setVisible(False)
        self.update_detail_btn.clicked.connect(self.show_update_detail_dialog)
        update_buttons.addWidget(check_btn)
        update_buttons.addWidget(self.update_download_btn)
        update_buttons.addWidget(project_btn)
        update_buttons.addWidget(self.update_detail_btn)
        update_buttons.addStretch(1)
        update_layout.addLayout(update_buttons)
        # 重新走统一折叠逻辑，避免启动检查结果文字过长导致关于窗口布局被挤压。
        self._set_update_status_text(self.update_status_label.toPlainText())
        layout.addWidget(update_box)

        # Theme-aware link label — matches the About-tab implementation so
        # links stay readable in both light and dark mode.  Stored on self
        # so _refresh_styled_widgets can re-render on theme toggle.
        _fg = self._theme_role_colors()["fg_primary"]
        _lnk = "#8ab4f8" if self._theme_is_dark() else "#0969da"
        def _build_about_dialog_links_html(fg, lnk):
            return (
                f'<span style="color:{fg}">原项目：</span>'
                f'<a href="https://github.com/purrfecto114-lgtm/ShangBackground" style="color:{lnk}">GitHub</a><br>'
                f'<span style="color:{fg}">反馈地址：</span>'
                f'<a href="{GITHUB_PROJECT_URL}" style="color:{lnk}">GitHub / 更新源</a><br>'
                f'<span style="color:{fg}">作者主页：b站@小小电子xxdz</span>'
            )
        link_label = QLabel(_build_about_dialog_links_html(_fg, _lnk))
        link_label.setOpenExternalLinks(True)
        link_label.setAlignment(Qt.AlignCenter)
        link_label.setWordWrap(True)
        self._about_dialog_links_label = link_label
        self._about_dialog_links_html_fn = _build_about_dialog_links_html
        layout.addWidget(link_label)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton(t("关闭"))
        close_btn.setMinimumWidth(96)
        close_btn.clicked.connect(dlg.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        try:
            parent_center = self.frameGeometry().center()
            geo = dlg.frameGeometry()
            geo.moveCenter(parent_center)
            dlg.move(geo.topLeft())
        except Exception:
            pass

        dlg.exec()

    def _disconnect_own_signals(self):
        """Best-effort cleanup for self-owned Qt signal connections before exit."""
        unsubscribe = getattr(self, "_i18n_unsubscribe", None)
        self._i18n_unsubscribe = None
        if unsubscribe is not None:
            try:
                unsubscribe()
            except Exception:
                pass
        for signal, slot in (
            (self.bing_result_signal, self._on_bing_finished),
            (self.core_result_signal, self._on_core_finished),
            (self.hotkey_recorded_signal, self.set_context_hotkey),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _perform_exit_cleanup_once(
        self,
        *,
        restore_wallpaper: bool = True,
        restarting: bool = False,
        reason: str = "application_exit",
    ):
        if not self._exit_signals_disconnected:
            self._exit_signals_disconnected = True
            self._disconnect_own_signals()
        return core.perform_exit_cleanup(
            reason=reason,
            restore_wallpaper=restore_wallpaper,
            restarting=restarting,
        )

    def _restart_carry_over_args(self, extra_args=None) -> list[str]:
        """Keep only safe startup arguments when relaunching the normal Linux GUI."""
        skip_flags = {
            "--previous", "--next", "--random", "--show", "--hide",
            "--jump-to-wallpaper", "--sync-context-on-start", "--inherit-session-wallpaper",
            "--internal-video-player", "--muted",
        }
        skip_value_flags = {"--set-wallpaper"}
        result: list[str] = []
        skip_next = False
        for arg in sys.argv[1:]:
            if skip_next:
                skip_next = False
                continue
            if arg in skip_flags:
                continue
            if arg in skip_value_flags:
                skip_next = True
                continue
            if any(arg.startswith(flag + "=") for flag in skip_value_flags):
                continue
            result.append(arg)
        for arg in list(extra_args or []):
            if arg not in result:
                result.append(arg)
        if "--inherit-session-wallpaper" not in result:
            result.append("--inherit-session-wallpaper")
        return result

    def restart_program(self, extra_args=None):
        """Relaunch the Linux GUI without privilege escalation."""
        try:
            core.capture_session_original_wallpaper(inherit_existing=True, force_refresh=False)
            persist = getattr(core, "_persist_session_original_wallpaper", None)
            if callable(persist):
                persist()
        except Exception as exc:
            core.log(f"重启前保存启动前壁纸记录失败: {exc}")
        args = self._restart_carry_over_args(extra_args)
        cmd = self._app_command(*args)
        try:
            self._closing_for_exit = True
            if self.tray:
                self.tray.hide()
                self.tray.deleteLater()
                self.tray = None
                QApplication.processEvents()
                self._refresh_shell_ui_later()
            self._perform_exit_cleanup_once(
                restore_wallpaper=False, restarting=True, reason="restart"
            )
            subprocess.Popen(
                cmd,
                cwd=core.BASE_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.set_status(t("已请求重启程序"))
            QApplication.instance().quit()
        except Exception as exc:
            self._closing_for_exit = False
            QMessageBox.warning(self, t("重启程序"), t("重启程序失败：") + str(exc))

    def restart_as_admin(self, extra_args=None):
        QMessageBox.information(
            self,
            t("管理员重启"),
            t("Linux 版不提供 GUI 提权重启。需要权限操作时，请使用系统包管理器或终端完成。"),
        )

    def exit_app(self):
        self._closing_for_exit = True
        if self.tray:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None
            QApplication.processEvents()
            self._refresh_shell_ui_later()
        self._perform_exit_cleanup_once(
            restore_wallpaper=True, reason="user_exit"
        )
        QApplication.instance().quit()

    def closeEvent(self, event):
        if core.config.get("run_in_background", True) and not self._closing_for_exit:
            event.ignore()
            self.hide()
            if self.tray and core.config.get("tray_notify", True):
                self.tray.showMessage(APP_DISPLAY_NAME, t("已隐藏到系统托盘"), QSystemTrayIcon.Information, 1500)
            return
        if self.tray:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None
            self._refresh_shell_ui_later()
        self._perform_exit_cleanup_once(
            restore_wallpaper=True, reason="close_event"
        )
        event.accept()


class _LinuxMainWindowMixin:
    """Linux behavior is the shared baseline."""
    pass


class _WindowsMainWindowMixin:
    def _init_icon(self):
            icon_name = "LOGO.ico"
            self.icon_path = os.path.join(core.BASE_DIR, "img", icon_name)
            if not os.path.exists(self.icon_path):
                self.icon_path = os.path.join(core.BASE_DIR, "img", "LOGO.png")
            self.app_icon = QIcon(self.icon_path) if os.path.exists(self.icon_path) else QIcon()
            app = QApplication.instance()
            if app is not None:
                app.setOrganizationName(APP_ORGANIZATION)
                app.setApplicationName(APP_PROCESS_NAME)
                app.setApplicationDisplayName(APP_DISPLAY_NAME)
            if not self.app_icon.isNull():
                QApplication.setWindowIcon(self.app_icon)
                self.setWindowIcon(self.app_icon)

    def _set_button_svg_icon(self, button, icon_name: str, size: int = 20):
            """给按钮设置统一 SVG 图标，记录以便暗色模式切换时刷新 SVG 颜色。

            Bug 3 fix: 直接用 ``QIcon(path)`` 会让 Qt 内部的 ``QSvgRenderer``
            缓存按文件路径 keyed — 第二次调用（暗色模式切换后）会拿到失效的
            renderer，触发 ``qt.svg: Cannot open file …`` 警告。这里改用显式
            ``QSvgRenderer`` + ``QPixmap.loadFromData(...)`` 渲染路径，并按
            ``(path, theme_signature, size)`` 缓存像素图，绕过 Qt 的 SVG
            renderer 缓存。
            """
            try:
                path = self._img_path(icon_name)
                if os.path.exists(path):
                    pix = self._render_svg_to_pixmap(path, size)
                    if pix is not None and not pix.isNull():
                        button.setIcon(QIcon(pix))
                        button.setIconSize(QSize(size, size))
                        if not hasattr(self, "_svg_button_icons"):
                            self._svg_button_icons = {}
                        self._svg_button_icons[id(button)] = (button, path, size)
            except Exception:
                pass

    def _render_svg_to_pixmap(self, path: str, size: int):
            """Render an SVG file to a ``QPixmap`` of ``size x size`` device pixels.

            Bypasses Qt's ``QSvgRenderer`` path-keyed cache by reading the file
            bytes directly and rendering into a fresh ``QPixmap``.  Results are
            memoized per ``(path, theme_signature, size)`` so repeated calls for
            the same icon don't re-read the file.

            Bug 18 fix: ``QSvgRenderer`` does NOT resolve the CSS keyword
            ``currentColor`` — it renders such strokes/fills as black, making
            icons invisible on dark backgrounds.  We now replace ``currentColor``
            in the SVG data with the actual theme foreground color before
            rendering, so icons are visible in both light and dark modes.
            """
            try:
                from PySide6.QtSvg import QSvgRenderer
                from PySide6.QtGui import QPixmap, QPainter
                from PySide6.QtCore import QByteArray, QRectF
            except Exception:
                return None
            sig = self._svg_theme_signature()
            # Bug 18 fix: get the current theme's foreground color for currentColor substitution.
            fg_color = self._svg_current_color()
            cache = getattr(self, "_svg_pixmap_cache", None)
            if cache is None:
                cache = {}
                self._svg_pixmap_cache = cache
            key = (path, sig, int(size), fg_color)
            cached = cache.get(key)
            if cached is not None:
                try:
                    # QPixmap is implicitly shared in Qt; safe to reuse.
                    if not cached.isNull():
                        return cached
                except RuntimeError:
                    cache.pop(key, None)
            try:
                with open(path, "rb") as f:
                    data = f.read()
                # Bug 18 fix: Replace currentColor with the actual theme color so
                # QSvgRenderer (which doesn't support currentColor) renders correctly.
                if b"currentColor" in data and fg_color:
                    data = data.replace(b"currentColor", fg_color.encode("utf-8"))
                renderer = QSvgRenderer(QByteArray(data))
                if not renderer.isValid():
                    return None
                # Account for devicePixelRatio so the icon stays sharp on HiDPI.
                from PySide6.QtGui import QGuiApplication
                dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
                pix = QPixmap(int(size * dpr), int(size * dpr))
                pix.setDevicePixelRatio(dpr)
                pix.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pix)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                pad = max(1.0, float(size) * 0.08)
                renderer.render(painter, QRectF(pad, pad, max(1.0, float(size) - 2 * pad), max(1.0, float(size) - 2 * pad)))
                painter.end()
                cache[key] = pix
                # Bound cache size to avoid unbounded growth (max 64 entries).
                if len(cache) > 64:
                    # Drop oldest entry (dict preserves insertion order in Py3.7+).
                    oldest = next(iter(cache))
                    cache.pop(oldest, None)
                return pix
            except Exception:
                return None

    def _svg_theme_signature(self) -> str:
            """Return a short signature that changes when the SVG rendering should
            be invalidated (dark-mode toggle, theme color change, language switch).
            Used as part of the ``_svg_pixmap_cache`` key.
            """
            try:
                # Bug 3 fix: use _theme_is_dark() method (the actual API) instead
                # of the non-existent _dark_mode attribute, so the signature
                # actually changes on dark-mode toggle and invalidates the cache.
                dark = bool(self._theme_is_dark()) if hasattr(self, "_theme_is_dark") else False
                accent = str(core.config.get("theme_color", "")) if hasattr(core, "config") else ""
                return f"{'d' if dark else 'l'}_{accent}"
            except Exception:
                return "default"

    def _refresh_svg_button_icons(self):
            """暗色模式切换后刷新所有 SVG 按钮图标，确保 currentColor 正确生效。

            Bug 3 fix: 旧的实现 ``button.setIcon(QIcon(path))`` 在第二次调用时
            会命中 Qt 的 ``QSvgRenderer`` 失效缓存。这里清空本地像素图缓存并
            用 ``_render_svg_to_pixmap`` 重新渲染，保证暗色模式下 SVG 的
            ``currentColor`` 等主题相关样式重新生效。
            """
            # Clear the pixmap cache so all icons re-render with the new theme.
            cache = getattr(self, "_svg_pixmap_cache", None)
            if cache is not None:
                cache.clear()
            icons = getattr(self, "_svg_button_icons", {})
            for btn_id, (button, path, size) in list(icons.items()):
                try:
                    pix = self._render_svg_to_pixmap(path, size)
                    if pix is not None and not pix.isNull():
                        button.setIcon(QIcon(pix))
                        button.setIconSize(QSize(size, size))
                    else:
                        button.setIcon(QIcon(path))
                        button.setIconSize(QSize(size, size))
                except RuntimeError:
                    icons.pop(btn_id, None)
                except Exception:
                    pass

    def _prepare_popup_menu(self, menu: QMenu) -> QMenu:
            """Prepare a QMenu popup for consistent rendering.

            Note: On Windows we intentionally do NOT set WA_TranslucentBackground.
            The native DWM theme already draws rounded corners for popup menus,
            and forcing translucency breaks that (left/right corners become
            asymmetric and the system drop shadow disappears).  The QSS
            ``border-radius`` only controls the inner background, not the window
            shape.  This helper is kept as a no-op placeholder so call sites stay
            consistent across platforms; Linux/MacOS may extend it later if
            needed.
            """
            if menu is None:
                return menu
            return menu

    def _perf_level(self) -> str:
            """返回当前性能模式: 'power_saver' / 'balanced' / 'performance'.

            v1.4.6: 新增三档. 向后兼容旧 performance_mode 布尔:
            - 旧 performance_mode=True → 'performance'
            - 旧 performance_mode=False → 'balanced' (除非 performance_level 已设)
            """
            level = str(core.config.get("performance_level", "")).lower()
            if level in ("power_saver", "balanced", "performance"):
                return level
            # 旧配置兼容
            if bool(core.config.get("performance_mode", False)):
                return "performance"
            return "balanced"

    def _combo_popup_stylesheet(self) -> str:
            """Use the same theme roles for ShangComboBox's custom QMenu popup.

            Values are kept in lock-step with the ``QMenu#ComboBoxMenu`` block in
            the base stylesheet so the popup looks identical regardless of whether
            a given QComboBox subclass uses the QMenu-based or QListView-based
            popup.  See ``ShangComboBox.showPopup`` for the popup construction.
            """
            colors = self._theme_role_colors()
            dark = self._theme_is_dark()
            accent = getattr(self, "_theme_color", core.config.get("theme_color", DEFAULT_THEME_COLOR)) or DEFAULT_THEME_COLOR
            qcolor = QColor(accent)
            if not qcolor.isValid():
                accent = DEFAULT_THEME_COLOR
                qcolor = QColor(accent)
            brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000
            if dark:
                hover = "#30304c"
                selected_bg = "#8b8ba3" if brightness >= 230 else accent
                selected_fg = "#ffffff"
            else:
                hover = "#f0f2f5"
                selected_bg = "#8c959f" if brightness >= 230 else accent
                selected_fg = "#ffffff" if brightness >= 230 or brightness < 170 else "#24292f"
            return (
                f"QMenu#ComboBoxMenu {{ background-color: {colors['bg_input']}; color: {colors['fg_primary']}; "
                f"border: 1px solid {colors['border']}; padding: 6px; }}"
                # Match QComboBox QAbstractItemView::item { min-height: 28px; padding: 4px 12px; border-radius: 6px; }
                f"QMenu#ComboBoxMenu::item {{ padding: 4px 12px; border-radius: 6px; min-height: 28px; min-width: 140px; }}"
                f"QMenu#ComboBoxMenu::item:selected {{ background-color: {hover}; color: {colors['fg_primary']}; }}"
                f"QMenu#ComboBoxMenu::item:checked {{ background-color: {selected_bg}; color: {selected_fg}; font-weight: 600; }}"
                f"QMenu#ComboBoxMenu::item:disabled {{ color: {colors['fg_muted']}; }}"
                "QMenu#ComboBoxMenu::indicator { width: 0px; height: 0px; }"
            )

    def _extra_theme_qss(self, dark: bool) -> str:
            if dark:
                bg_main = "#1a1b2e"
                bg_widget = "#252638"
                bg_input = "#2d2f42"
                fg_primary = "#e8e8f0"
                fg_muted = "#9b9bb0"
                border = "#3d3e56"
                hover = "#2e3045"
                disabled_bg = "#34354a"
                disabled_fg = "#6b6d84"
            else:
                bg_main = "#ffffff"
                bg_widget = "#ffffff"
                bg_input = "#ffffff"
                fg_primary = "#1f2328"
                fg_muted = "#656d76"
                border = "#d8dee4"
                hover = "#eef0f3"
                disabled_bg = "#e2e5ea"
                disabled_fg = "#9ca3ab"

            # Determine the directory containing SVG icons.  In source and packaged runs
            # ``core.BASE_DIR`` points at the resource root (containing the ``img``
            # folder); fallback to the directory of ``entry_script_path()`` if
            # ``BASE_DIR`` is missing.  Defining this here ensures it is available
            # when computing QSS icon URLs below.
            icon_dir = os.path.join(getattr(core, "BASE_DIR", os.path.dirname(entry_script_path())), "img")
            def _svg_data_uri(filename: str) -> str:
                """Return a plain absolute filesystem path suitable for QSS ``url()``.

                On Windows returns ``D:/path/to/file.svg`` (drive-absolute, forward slashes).
                Qt QSS ``url()`` accepts this natively — no scheme prefix, no leading slash.
                """
                from pathlib import Path
                return Path(os.path.join(icon_dir, filename)).as_posix()

            spin_up_fg_icon = "spin_arrow_up_light.svg" if dark else "spin_arrow_up_dark.svg"
            spin_down_fg_icon = "spin_arrow_down_light.svg" if dark else "spin_arrow_down_dark.svg"
            spin_up_disabled_name = "spin_arrow_up_disabled_dark.svg" if dark else "spin_arrow_up_disabled_light.svg"
            spin_down_disabled_name = "spin_arrow_down_disabled_dark.svg" if dark else "spin_arrow_down_disabled_light.svg"
            # Verify SVG files exist at build time; log a warning if not found.
            for _name in (spin_up_fg_icon, spin_down_fg_icon, spin_up_disabled_name,
                          spin_down_disabled_name, "checkbox_check.svg", "checkbox_dash.svg",
                          "checkbox_check_disabled.svg"):
                _f = os.path.join(icon_dir, _name)
                if not os.path.exists(_f):
                    try:
                        import logging
                        logging.getLogger("core").warning(f"SVG icon not found: {_f}")
                    except Exception:
                        pass
            spin_up_icon = _svg_data_uri(spin_up_fg_icon)
            spin_down_icon = _svg_data_uri(spin_down_fg_icon)
            spin_up_disabled_icon = _svg_data_uri(spin_up_disabled_name)
            spin_down_disabled_icon = _svg_data_uri(spin_down_disabled_name)
            checkbox_check_icon = _svg_data_uri("checkbox_check.svg")
            checkbox_dash_icon = _svg_data_uri("checkbox_dash.svg")
            checkbox_check_disabled_icon = _svg_data_uri("checkbox_check_disabled.svg")
            qss = """
    /* Extra cross-platform contrast fixes */
    /* Unified dialog surfaces — keep selector list in sync with ui.dialog_style */

    QDialog#GlobalSettingsDialog { background-color: __BG_MAIN__; }
    QScrollArea#SettingsPageScroll,
    QScrollArea#SettingsPageScroll > QWidget { border: none; background-color: transparent; }
    QWidget#SettingsPageSurface { background-color: __BG_WIDGET__; border-radius: 12px; background-clip: padding; }
    QWidget#scrollAreaWidgetContents, QWidget#MainTabSurface { background-color: transparent; }
    /* Clip fills to the padding box so border and background anti-alias only once. */
    QGroupBox, QPushButton, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QProgressBar, QListWidget, QTextEdit, QPlainTextEdit, QTableWidget,
    QTreeWidget, QTableView, QTreeView, QToolTip { background-clip: padding; }
    QTabWidget::pane { background-clip: padding; }
    QMessageBox, QFileDialog, QColorDialog, QDialogButtonBox { background-color: __BG_WIDGET__; color: __FG_PRIMARY__; }
    QDialog QLabel, QMessageBox QLabel, QFileDialog QLabel, QColorDialog QLabel { background-color: transparent; color: __FG_PRIMARY__; }
    QDialogButtonBox QPushButton { background: %%tc%%; color: %%btn_text%%; border: 1px solid %%btn_border%%; border-radius: 6px; padding: 5px 14px; min-height: 28px; }
    QDialogButtonBox QPushButton:hover:enabled { background: %%hover_c%%; }
    QDialogButtonBox QPushButton:disabled { background: __DISABLED_BG__; color: __DISABLED_FG__; border-color: __BORDER__; }
    /* Transparent generic frames avoid double rounded-corner bleed-through under QWidget surfaces.
       Components that need cards should use QGroupBox or an object-name-specific rule. */
    QFrame { background-color: transparent; }
    /* Unified dialog title roles — see ui.dialog_style.DIALOG_TITLE_STYLE */
    QLabel[dialogTitle="true"] { font-size: 18px; font-weight: 700; background: transparent; }
    QLabel[dialogHeroTitle="true"] { font-size: 22px; font-weight: 700; background: transparent; }
    QLabel[dialogNote="true"] { font-size: 13px; background: transparent; color: __FG_MUTED__; }
    QAbstractItemView { background-color: __BG_INPUT__; color: __FG_PRIMARY__; border: 1px solid __BORDER__; selection-background-color: %%visible_accent%%; selection-color: %%accent_text%%; }
    /* QComboBox popup (QListView) — used only by non-ShangComboBox instances.
       ShangComboBox renders its popup as QMenu#ComboBoxMenu (see below). The two
       rule sets are kept visually consistent so users cannot tell which subclass
       a given combo uses. */
    QComboBox QAbstractItemView, QListView#ComboPopupView {
        background-color: __BG_INPUT__;
        color: __FG_PRIMARY__;
        border: 1px solid __BORDER__;
        border-radius: 8px;
        padding: 6px;
        outline: none;
    }
    QComboBox QAbstractItemView::item, QListView#ComboPopupView::item {
        min-height: 28px;
        padding: 4px 12px;
        border-radius: 6px;
    }
    QComboBox QAbstractItemView::item:hover, QListView#ComboPopupView::item:hover { background-color: __HOVER__; }
    QComboBox QAbstractItemView::item:selected, QListView#ComboPopupView::item:selected { background-color: %%visible_accent%%; color: %%accent_text%%; }
    QHeaderView::section { background-color: __HOVER__; color: __FG_PRIMARY__; border: 1px solid __BORDER__; padding: 6px 8px; font-weight: 600; }
    QTableWidget, QTreeWidget, QTableView, QTreeView { background-color: __BG_INPUT__; color: __FG_PRIMARY__; gridline-color: __BORDER__; alternate-background-color: __BG_WIDGET__; border-radius: 6px; }
    QSpinBox, QDoubleSpinBox {
    border: 1px solid __BORDER__;
    border-radius: 8px;
    padding: 3px 24px 3px 10px;
    background-color: __BG_INPUT__;
    color: __FG_PRIMARY__;
    font-size: 13px;
    min-height: 28px;
    min-width: 70px;
    max-width: 118px;
    }
    QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid %%visible_accent%%; padding: 2px 23px 2px 9px; }
    QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled { background-color: __DISABLED_BG__; color: __DISABLED_FG__; border-color: __BORDER__; }
    QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    height: 15px;
    margin-top: 1px;
    margin-right: 1px;
    border-left: 1px solid __BORDER__;
    border-top-right-radius: 7px;
    background-color: transparent;
    }
    QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    height: 15px;
    margin-bottom: 1px;
    margin-right: 1px;
    border-left: 1px solid __BORDER__;
    border-bottom-right-radius: 7px;
    background-color: transparent;
    }
    QSpinBox::up-button:hover:enabled, QDoubleSpinBox::up-button:hover:enabled { background-color: __HOVER__; }
    QSpinBox::down-button:hover:enabled, QDoubleSpinBox::down-button:hover:enabled { background-color: __HOVER__; }
    QSpinBox::up-button:pressed:enabled, QDoubleSpinBox::up-button:pressed:enabled,
    QSpinBox::down-button:pressed:enabled, QDoubleSpinBox::down-button:pressed:enabled { background-color: __BORDER__; }
    QSpinBox::up-button:disabled, QDoubleSpinBox::up-button:disabled,
    QSpinBox::down-button:disabled, QDoubleSpinBox::down-button:disabled { background-color: __DISABLED_BG__; border-color: __BORDER__; }
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { width: 10px; height: 10px; margin: 0px; image: url("%%spin_up_icon%%"); }
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { width: 10px; height: 10px; margin: 0px; image: url("%%spin_down_icon%%"); }
    QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled { image: url("%%spin_up_disabled_icon%%"); }
    QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled { image: url("%%spin_down_disabled_icon%%"); }
    QSpinBox#CompactNumberSpin {
    min-width: 64px;
    max-width: 64px;
    padding: 3px 22px 3px 8px;
    }
    QSpinBox#CompactNumberSpin:focus { padding: 2px 21px 2px 7px; }
    QSpinBox#CompactNumberSpin::up-button { width: 22px; }
    QSpinBox#CompactNumberSpin::down-button { width: 22px; }
    QSpinBox#CompactNumberSpin::up-arrow, QSpinBox#CompactNumberSpin::down-arrow { width: 10px; height: 10px; margin: 0px; }
    QCheckBox { spacing: 10px; font-size: 13px; font-weight: 400; min-height: 26px; background-color: transparent; color: __FG_PRIMARY__; }
    QCheckBox:hover { font-size: 13px; font-weight: 400; }
    QCheckBox:disabled { color: __DISABLED_FG__; }
    QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1.5px solid __BORDER__;
    border-radius: 5px;
    background-color: __BG_INPUT__;
    }
    QCheckBox::indicator:hover:enabled { border: 1.5px solid %%visible_accent%%; background-color: __HOVER__; }
    QCheckBox::indicator:checked { border-color: %%visible_accent%%; background-color: %%visible_accent%%; image: url("%%checkbox_check_icon%%"); }
    QCheckBox::indicator:checked:hover:enabled { border-color: %%pressed_c%%; background-color: %%pressed_c%%; }
    QCheckBox::indicator:indeterminate { border-color: %%visible_accent%%; background-color: %%visible_accent%%; image: url("%%checkbox_dash_icon%%"); }
    QCheckBox::indicator:disabled { border-color: __BORDER__; background-color: __DISABLED_BG__; }
    QCheckBox::indicator:checked:disabled { border-color: __BORDER__; background-color: __DISABLED_BG__; image: url("%%checkbox_check_disabled_icon%%"); }
    QSlider::groove:horizontal { height: 6px; background: __BORDER__; border-radius: 3px; }
    QSlider::handle:horizontal { width: 18px; height: 18px; margin: -6px 0; border-radius: 9px; background: %%visible_accent%%; }
    QSlider::handle:horizontal:hover { background: %%pressed_c%%; }
    QToolTip { background-color: __BG_INPUT__; color: __FG_PRIMARY__; padding: 6px 10px; font-size: 12px; }
QWidget[settingsSearchMatch="true"] { border: 2px solid %%visible_accent%%; }
    QMenu { background-color: __BG_WIDGET__; color: __FG_PRIMARY__; border: 1px solid __BORDER__; padding: 6px; }
    QMenu::item { padding: 8px 24px; border-radius: 6px; }
    QMenu::item:selected { background-color: __HOVER__; }
    /* QMenu#ComboBoxMenu — popup rendered by ShangComboBox.showPopup().
       Keep padding / item height / radius in sync with the
       ``QComboBox QAbstractItemView`` block above so the two popup styles
       (QMenu-based and QListView-based) are pixel-identical. */
    QMenu#ComboBoxMenu { padding: 6px; }
    QMenu#ComboBoxMenu::item { min-width: 140px; min-height: 28px; padding: 4px 12px; border-radius: 6px; }
    QMenu#ComboBoxMenu::item:selected { background-color: __HOVER__; color: __FG_PRIMARY__; }
    QMenu#ComboBoxMenu::item:checked { background-color: %%visible_accent%%; color: %%accent_text%%; font-weight: 600; }
    QMenu#ComboBoxMenu::item:disabled { color: __DISABLED_FG__; }
    QMenu#ComboBoxMenu::indicator { width: 0px; height: 0px; }
    QMenu::item:disabled { color: __DISABLED_FG__; }
    QFrame#HeaderLangSwitch { background-color: transparent; }
    QFormLayout { vertical-spacing: 10px; }
    QGroupBox QFormLayout { vertical-spacing: 10px; }
    QLabel[muted="true"] { color: __FG_MUTED__; }
    """
            return (qss.replace("__BG_MAIN__", bg_main).replace("__BG_WIDGET__", bg_widget).replace("__BG_INPUT__", bg_input)
                       .replace("__FG_PRIMARY__", fg_primary).replace("__FG_MUTED__", fg_muted)
                       .replace("__BORDER__", border).replace("__HOVER__", hover)
                       .replace("__DISABLED_BG__", disabled_bg).replace("__DISABLED_FG__", disabled_fg)
                       .replace("%%spin_up_icon%%", spin_up_icon).replace("%%spin_down_icon%%", spin_down_icon)
                       .replace("%%spin_up_disabled_icon%%", spin_up_disabled_icon).replace("%%spin_down_disabled_icon%%", spin_down_disabled_icon)
                       .replace("%%checkbox_check_icon%%", checkbox_check_icon).replace("%%checkbox_dash_icon%%", checkbox_dash_icon)
                       .replace("%%checkbox_check_disabled_icon%%", checkbox_check_disabled_icon))

    def _rebuild_stylesheet(self):
            """根据当前主题色和暗色模式重建 QSS 样式表。布局属性（padding/min-height/font-size）在暗色模式下保持不变。"""
            app = QApplication.instance()
            tc = self._theme_color
            dark = bool(core.config.get("dark_mode", False))
            from PySide6.QtGui import QColor
            base = QColor(tc)
            if not base.isValid():
                tc = DEFAULT_THEME_COLOR
                self._theme_color = tc
                base = QColor(tc)

            if dark:
                # ── 暗色模式配色：只换颜色，不动任何布局属性 ──
                bg_main = "#1a1b2e"
                bg_widget = "#252638"
                bg_input = "#2d2f42"
                fg_primary = "#e8e8f0"
                fg_secondary = "#c8c8d8"
                border_color = "#3d3e56"
                group_bg = "#252638"
                scroll_bg = "#141526"
                scroll_handle = "#3d3e56"
                scroll_handle_hover = "#5d5e76"
                theme_brightness = (base.red() * 299 + base.green() * 587 + base.blue() * 114) / 1000
                if theme_brightness >= 230:
                    # Very light accent colors turn buttons white in dark mode; use a darkened accent-safe surface instead.
                    tc_for_buttons = "#3a3a50"
                    hover_c = "#45455f"
                    pressed_c = "#50506a"
                    btn_top = tc_for_buttons
                    btn_hover_top = hover_c
                    btn_text = "#e6e6f0"
                    btn_border = "#5a5a73"
                    visible_accent = "#8b8ba3"
                    progress_chunk = visible_accent
                    accent_text = "#ffffff"
                else:
                    tc_for_buttons = tc
                    hover_c = base.lighter(115).name()
                    pressed_c = base.lighter(130).name()
                    btn_top = base.name()
                    btn_hover_top = base.lighter(110).name()
                    btn_text = "#e0e0e0" if theme_brightness >= 170 else "#ffffff"
                    btn_border = base.darker(118).name()
                    visible_accent = tc
                    progress_chunk = tc
                    accent_text = "#ffffff"
                disabled_bg = "#34354a"
                disabled_text = "#6b6d84"
                muted_color = "#8b8da0"
                nav_hover = "#2a2b42"
                # ── 暗色模板：布局属性与亮色完全一致 ──
                _TPL = (
                    "/* ── 暗色模式 ── */\n"
                    f"QMainWindow, QDialog {{ background-color: {bg_main}; }}\n"
                    f"QWidget {{ color: {fg_primary}; font-family: %%font_family%%; }}\n"
                    f"#CentralContainer {{ background-color: {bg_widget}; }}\n"
                    f"QLabel {{ background-color: transparent; color: {fg_primary}; }}\n"
                    "\n"
                    "/* 分组框样式 */\n"
                    f"QGroupBox {{ font-weight: 600; font-size: 13px; border: 1px solid {border_color}; border-radius: 10px;"
                    f" margin-top: 14px; padding: 18px 14px 14px 14px; background-color: {group_bg}; }}\n"
                    f"QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left;"
                    f" padding: 2px 12px; left: 12px; color: {fg_primary}; font-size: 13px; font-weight: 700; }}\n"
                    "\n"
                    "/* 按钮 */\n"
                    f"QPushButton {{ background: %%tc%%; color: %%btn_text%%; border: 1px solid %%btn_border%%; border-radius: 7px;"
                    f" padding: 6px 16px; font-size: 13px; font-weight: 500; min-height: 28px; }}\n"
                    f"QPushButton:hover:enabled {{ background: %%hover_c%%; }}\n"
                    f"QPushButton:pressed:enabled {{ background: %%pressed_c%%; padding-top: 7px; padding-bottom: 5px; }}\n"
                    f"QPushButton:disabled {{ background: {disabled_bg}; border-color: {border_color}; color: {disabled_text}; }}\n"
                    f"QPushButton[secondary=\"true\"] {{ background: %%tc%%; color: %%btn_text%%; border: 1px solid %%btn_border%%; }}\n"
                    f"QPushButton[secondary=\"true\"]:hover:enabled {{ background: %%hover_c%%; }}\n"
                    f"QPushButton[secondary=\"true\"]:pressed:enabled {{ background: %%pressed_c%%; padding-top: 7px; padding-bottom: 5px; }}\n"
                    f"QPushButton[secondary=\"true\"]:disabled {{ background: {disabled_bg}; border-color: {border_color}; color: {disabled_text}; }}\n"
                    f"QPushButton[settingsAction=\"true\"] {{ background: {bg_input}; color: {fg_primary}; border: 1px solid %%visible_accent%%; border-radius: 7px; padding: 6px 14px; font-size: 13px; font-weight: 500; min-height: 28px; }}\n"
                    f"QPushButton[settingsAction=\"true\"]:hover:enabled {{ background: {nav_hover}; border-color: %%pressed_c%%; }}\n"
                    f"QPushButton[settingsAction=\"true\"]:pressed:enabled {{ background: {nav_hover}; padding-top: 7px; padding-bottom: 5px; }}\n"
                    f"QPushButton[settingsAction=\"true\"]:disabled {{ background: {disabled_bg}; border-color: {border_color}; color: {disabled_text}; }}\n"
                    "\n"
                    "/* 输入框 */\n"
                    f"QLineEdit {{ border: 1px solid {border_color}; border-radius: 8px; padding: 6px 12px;"
                    f" background-color: {bg_input}; color: {fg_primary}; font-size: 13px; min-height: 28px; }}\n"
                    f"QLineEdit:focus {{ border-color: %%visible_accent%%; border-width: 2px; padding: 5px 11px; }}\n"
                    "\n"
                    "/* 下拉框 — 与基础 QSS 的 QComboBox 块保持一致：右侧 padding 留给 24px\n"
                    "   drop-down，避免长文本覆盖箭头；::down-arrow 用统一的 SVG 图标，跨平台外观一致。 */\n"
                    f"QComboBox {{ border: 1px solid {border_color}; border-radius: 8px;"
                    f" padding: 4px 30px 4px 12px;"
                    f" background-color: {bg_input}; color: {fg_primary}; font-size: 13px;"
                    f" min-height: 28px; }}\n"
                    f"QComboBox:hover:enabled {{ border-color: %%hover_c%%; }}\n"
                    f"QComboBox:focus {{ border-color: %%visible_accent%%; border-width: 2px;"
                    f" padding: 3px 29px 3px 11px; }}\n"
                    f"QComboBox:on {{ border-color: %%visible_accent%%; }}\n"
                    f"QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: top right;"
                    f" width: 24px; border-left: none; border-top-right-radius: 8px;"
                    f" border-bottom-right-radius: 8px; background: transparent; }}\n"
                    f"QComboBox::drop-down:hover {{ background-color: {nav_hover}; }}\n"
                    f"QComboBox::down-arrow {{ image: url(\"%%spin_down_icon%%\");"
                    f" width: 10px; height: 10px; }}\n"
                    f"QComboBox::down-arrow:disabled {{ image: url(\"%%spin_down_disabled_icon%%\"); }}\n"
                    "\n"
                    "/* 复选框 */\n"
                    f"QCheckBox {{ spacing: 8px; font-size: 13px; font-weight: 400; min-height: 24px; background-color: transparent; color: {fg_primary}; }}\n"
                    f"QCheckBox:hover {{ font-size: 13px; font-weight: 400; }}\n"
                    "\n"
                    "/* 选项卡 */\n"
                    f"QTabWidget::pane {{ border: 1px solid {border_color}; border-radius: 10px;"
                    f" background-color: {bg_widget}; padding: 6px; }}\n"
                    f"QTabBar::tab {{ padding: 8px 22px; font-size: 13px; font-weight: 500;"
                    f" border-top-left-radius: 7px; border-top-right-radius: 7px; margin-right: 2px;"
                    f" background-color: {bg_widget}; color: {fg_secondary}; border: 1px solid transparent; border-bottom: none; }}\n"
                    f"QTabBar::tab:selected {{ background-color: {bg_widget}; color: {fg_primary};"
                    f" border: 1px solid {border_color}; border-bottom: 2px solid %%visible_accent%%; }}\n"
                    f"QTabBar::tab:hover:!selected {{ background-color: {nav_hover}; }}\n"
                    "\n"
                    "/* 进度条 */\n"
                    f"QProgressBar {{ border: 1px solid {border_color}; border-radius: 8px; text-align: center;"
                    f" background-color: {bg_input}; color: {fg_primary}; height: 20px; font-size: 12px; }}\n"
                    f"QProgressBar::chunk {{ background-color: %%progress_chunk%%; border-radius: 6px; }}\n"
                    "\n"
                    "/* 列表视图 */\n"
                    f"QListWidget {{ border: 1px solid {border_color}; border-radius: 8px;"
                    f" background-color: {bg_widget}; color: {fg_primary}; padding: 4px; }}\n"
                    f"QListWidget::item {{ padding: 6px 10px; border-radius: 6px; }}\n"
                    f"QListWidget::item:hover {{ background: {nav_hover}; }}\n"
                    f"QListWidget::item:selected {{ background: %%visible_accent%%; color: %%accent_text%%; }}\n"
                    f"QTextEdit selection, QLineEdit selection {{ background: %%visible_accent%%; color: %%accent_text%%; }}\n"
                    "\n"
                    "/* 上下文菜单 */\n"
                    f"QMenu {{ background: {bg_widget}; color: {fg_primary}; border: 1px solid {border_color}; padding: 6px; }}\n"
                    f"QMenu::item {{ padding: 8px 28px; border-radius: 6px; }}\n"
                    f"QMenu::item:selected {{ background: {nav_hover}; }}\n"
                    f"QMenu::separator {{ height: 1px; background: {border_color}; margin: 4px 12px; }}\n"
                    "\n"
                    "/* 滚动区域与滚动条 */\n"
                    f"QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget, QStackedWidget {{ border: none; background-color: {bg_widget}; }}\n"
                    f"QDialog#GlobalSettingsDialog {{ background-color: {bg_main}; }}\n"
                    f"QScrollArea#SettingsPageScroll, QScrollArea#SettingsPageScroll > QWidget {{ border: none; background-color: transparent; }}\n"
                    f"QWidget#SettingsPageSurface {{ background-color: {bg_widget}; border-radius: 12px; background-clip: padding; }}\n"
                    f"QScrollBar:vertical {{ background: {scroll_bg}; width: 8px; margin: 0; border-radius: 4px; }}\n"
                    f"QScrollBar::handle:vertical {{ background: %%scroll_handle%%; min-height: 30px; border-radius: 4px; }}\n"
                    f"QScrollBar::handle:vertical:hover {{ background: %%scroll_handle_hover%%; }}\n"
                    f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; background: transparent; }}\n"
                    f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}\n"
                    f"QScrollBar:horizontal {{ background: {scroll_bg}; height: 8px; margin: 0; border-radius: 4px; }}\n"
                    f"QScrollBar::handle:horizontal {{ background: %%scroll_handle%%; min-width: 30px; border-radius: 4px; }}\n"
                    f"QScrollBar::handle:horizontal:hover {{ background: %%scroll_handle_hover%%; }}\n"
                    f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; background: transparent; }}\n"
                    f"QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}\n"
                    "\n"
                    "/* 文本编辑框 */\n"
                    f"QTextEdit, QPlainTextEdit {{ border: 1px solid {border_color}; border-radius: 8px;"
                    f" background-color: {bg_input}; color: {fg_primary}; padding: 8px;"
                    f" font-family: \"Cascadia Code\", \"Consolas\", \"Microsoft YaHei UI\", monospace;"
                    f" font-size: 12px; }}\n"
                    f"QPushButton#OperationInfoButton {{ background: transparent; color: {fg_secondary}; border: 1px solid {border_color};"
                    f" border-radius: 13px; padding: 0; min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; }}\n"
                    f"QPushButton#OperationInfoButton:hover {{ border-color: %%visible_accent%%; color: %%visible_accent%%; background: {bg_input}; }}\n"
                    f"QPushButton#OperationInfoButton:pressed {{ background: {bg_input}; }}\n"
                    f"QPushButton#CancelOperationButton {{ background: {bg_widget}; color: {fg_secondary}; border: 1px solid {border_color};"
                    f" border-radius: 7px; padding: 5px 12px; min-height: 26px; }}\n"
                    f"QPushButton#CancelOperationButton:hover:enabled {{ color: #f87171; border-color: #7f1d1d; background: #3b1010; }}\n"
                    f"QPushButton#CancelOperationButton:pressed:enabled {{ background: #2d0a0a; }}\n"
                    "/* 灰度提示 */\n"
                    f"*[muted=\"true\"] {{ color: {muted_color}; }}\n"
                )
            else:
                hover_c = base.darker(108).name()
                pressed_c = base.darker(125).name()
                btn_top = base.lighter(115).name()
                btn_hover_top = base.lighter(125).name()
                theme_brightness = (base.red() * 299 + base.green() * 587 + base.blue() * 114) / 1000
                btn_border = "#d8dee4" if theme_brightness >= 230 else base.darker(115).name()
                btn_text = "#1f2328" if theme_brightness >= 170 else "#ffffff"
                # 白色/浅色主题不能直接拿主题色当滚动条 hover，否则滚动条会"隐身"。
                visible_accent = "#8c959f" if theme_brightness >= 230 else tc
                scroll_handle = "#c0c8d0" if theme_brightness >= 230 else base.lighter(135).name()
                scroll_handle_hover = "#8c959f" if theme_brightness >= 230 else base.darker(105).name()
                progress_chunk = "#8c959f" if theme_brightness >= 230 else tc
                accent_text = "#ffffff" if theme_brightness >= 230 else btn_text

                _TPL = (
                    "/* 全局字体与背景 */\n"
                    "QMainWindow, QDialog { background-color: #ffffff; }\n"
                    "QWidget { color: #1f2328; font-family: %%font_family%%; }\n"
                    "#CentralContainer { background-color: #f0f2f5; }\n"
                    "QLabel { background-color: transparent; color: #1f2328; }\n"
                    "\n"
                    "/* 分组框样式 */\n"
                    "QGroupBox { font-weight: 600; font-size: 13px; border: 1px solid #d8dee4; border-radius: 10px;"
                    " margin-top: 14px; padding: 18px 14px 14px 14px; background-color: #f6f8fa; }\n"
                    "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;"
                    " padding: 2px 12px; left: 12px; color: #1f2328; font-size: 13px; font-weight: 700; }\n"
                    "\n"
                    "/* 按钮样式 */\n"
                    "QPushButton { background: %%tc%%;"
                    " color: %%btn_text%%; border: 1px solid %%btn_border%%; border-radius: 7px;"
                    " padding: 6px 16px; font-size: 13px; font-weight: 500; min-height: 28px; }\n"
                    "QPushButton:hover:enabled { background: %%hover_c%%; }\n"
                    "QPushButton:pressed:enabled { background: %%pressed_c%%; padding-top: 7px; padding-bottom: 5px; }\n"
                    "QPushButton:disabled { background: #e2e5ea; border-color: #d0d6dc; color: #9ca3ab; }\n"
                    "QPushButton[secondary=\"true\"] { background: %%tc%%; color: %%btn_text%%; border: 1px solid %%btn_border%%; }\n"
                    "QPushButton[secondary=\"true\"]:hover:enabled { background: %%hover_c%%; }\n"
                    "QPushButton[secondary=\"true\"]:pressed:enabled { background: %%pressed_c%%; padding-top: 6px; padding-bottom: 4px; }\n"
                    "QPushButton[secondary=\"true\"]:disabled { background: #e2e5ea; border-color: #d0d6dc; color: #9ca3ab; }\n"
                    "QPushButton[settingsAction=\"true\"] { background: #ffffff; color: #1f2328; border: 1px solid %%visible_accent%%; border-radius: 7px; padding: 6px 14px; font-size: 13px; font-weight: 500; min-height: 28px; }\n"
                    "QPushButton[settingsAction=\"true\"]:hover:enabled { background: #f0f2f5; border-color: %%pressed_c%%; }\n"
                    "QPushButton[settingsAction=\"true\"]:pressed:enabled { background: #e6e8ec; padding-top: 7px; padding-bottom: 5px; }\n"
                    "QPushButton[settingsAction=\"true\"]:disabled { background: #f0f2f5; border-color: #d8dee4; color: #9ca3ab; }\n"
                    "\n"
                    "/* 输入框样式 */\n"
                    "QLineEdit { border: 1px solid #d8dee4; border-radius: 8px; padding: 6px 12px;"
                    " background-color: #ffffff; font-size: 13px; min-height: 28px; }\n"
                    "QLineEdit:focus { border-color: %%visible_accent%%; border-width: 2px; padding: 5px 11px; }\n"
                    "\n"
                    "/* 下拉框样式 — 与暗色分支保持一致：右侧 padding 留给 24px drop-down，\n"
                    "   ::down-arrow 用统一 SVG 图标，跨平台外观一致。 */\n"
                    "QComboBox { border: 1px solid #d8dee4; border-radius: 8px;"
                    " padding: 4px 30px 4px 12px;"
                    " background-color: #ffffff; font-size: 13px; min-height: 28px; }\n"
                    "QComboBox:hover:enabled { border-color: %%hover_c%%; }\n"
                    "QComboBox:focus { border-color: %%visible_accent%%; border-width: 2px;"
                    " padding: 3px 29px 3px 11px; }\n"
                    "QComboBox:on { border-color: %%visible_accent%%; }\n"
                    "QComboBox::drop-down { subcontrol-origin: border; subcontrol-position: top right;"
                    " width: 24px; border-left: none; border-top-right-radius: 8px;"
                    " border-bottom-right-radius: 8px; background: transparent; }\n"
                    "QComboBox::drop-down:hover { background-color: #eef0f3; }\n"
                    "QComboBox::down-arrow { image: url(\"%%spin_down_icon%%\");"
                    " width: 10px; height: 10px; }\n"
                    "QComboBox::down-arrow:disabled { image: url(\"%%spin_down_disabled_icon%%\"); }\n"
                    "\n"
                    "/* 复选框 */\n"
                    "QCheckBox { spacing: 8px; font-size: 13px; background-color: transparent; }\n"
                    "\n"
                    "/* 选项卡 */\n"
                    "QTabWidget::pane { border: 1px solid #d8dee4; border-radius: 10px;"
                    " background-color: #ffffff; padding: 6px; }\n"
                    "QTabBar::tab { padding: 8px 22px; font-size: 13px; font-weight: 500;"
                    " border-top-left-radius: 7px; border-top-right-radius: 7px; margin-right: 2px;"
                    " background-color: #f6f8fa; color: #656d76; border: 1px solid transparent; border-bottom: none; }\n"
                    "QTabBar::tab:selected { background-color: #ffffff; color: #1f2328;"
                    " border: 1px solid #d8dee4; border-bottom: 2px solid %%visible_accent%%; }\n"
                    "QTabBar::tab:hover:!selected { background-color: #eef0f3; }\n"
                    "\n"
                    "/* 进度条 */\n"
                    "QProgressBar { border: 1px solid #d8dee4; border-radius: 8px; text-align: center;"
                    " background-color: #f0f2f5; height: 20px; font-size: 12px; }\n"
                    "QProgressBar::chunk { background-color: %%progress_chunk%%; border-radius: 6px; }\n"
                    "\n"
                    "/* 列表视图 */\n"
                    "QListWidget { border: 1px solid #d8dee4; border-radius: 8px;"
                    " background-color: #ffffff; padding: 4px; }\n"
                    "QListWidget::item { padding: 6px 10px; border-radius: 6px; }\n"
                    "QListWidget::item:hover { background: #eef0f3; }\n"
                    "QListWidget::item:selected { background: %%visible_accent%%; color: %%accent_text%%; }\n"
                    "QTextEdit selection, QLineEdit selection { background: %%visible_accent%%; color: %%accent_text%%; }\n"
                    "\n"
                    "/* 上下文菜单 */\n"
                    "QMenu { background: #ffffff; color: #1f2328; border: 1px solid #d8dee4; padding: 6px; }\n"
                    "QMenu::item { padding: 8px 28px; border-radius: 6px; }\n"
                    "QMenu::item:selected { background: #eef0f3; }\n"
                    "QMenu::separator { height: 1px; background: #e2e5ea; margin: 4px 12px; }\n"
                    "\n"
                    "/* 滚动区域与滚动条 */\n"
                    "QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget, QStackedWidget { border: none; background-color: #f0f2f5; }\n"
                    "QDialog#GlobalSettingsDialog { background-color: #ffffff; }\n"
                    "QScrollArea#SettingsPageScroll, QScrollArea#SettingsPageScroll > QWidget { border: none; background-color: transparent; }\n"
                    "QWidget#SettingsPageSurface { background-color: #ffffff; border-radius: 12px; background-clip: padding; }\n"
                    "QScrollBar:vertical { background: #f0f2f5; width: 8px; margin: 0; border-radius: 4px; }\n"
                    "QScrollBar::handle:vertical { background: %%scroll_handle%%; min-height: 30px; border-radius: 4px; }\n"
                    "QScrollBar::handle:vertical:hover { background: %%scroll_handle_hover%%; }\n"
                    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: transparent; }\n"
                    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }\n"
                    "QScrollBar:horizontal { background: #f0f2f5; height: 8px; margin: 0; border-radius: 4px; }\n"
                    "QScrollBar::handle:horizontal { background: %%scroll_handle%%; min-width: 30px; border-radius: 4px; }\n"
                    "QScrollBar::handle:horizontal:hover { background: %%scroll_handle_hover%%; }\n"
                    "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; background: transparent; }\n"
                    "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }\n"
                    "\n"
                    "/* 文本编辑框 */\n"
                    "QTextEdit, QPlainTextEdit { border: 1px solid #d8dee4; border-radius: 8px;"
                    " background-color: #ffffff; padding: 8px;"
                    " font-family: \"Cascadia Code\", \"Consolas\", \"Microsoft YaHei UI\", monospace;"
                    " font-size: 12px; }\n"
                    "QPushButton#OperationInfoButton { background: transparent; color: #656d76; border: 1px solid #d8dee4;"
                    " border-radius: 13px; padding: 0; min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; }\n"
                    "QPushButton#OperationInfoButton:hover { border-color: %%visible_accent%%; color: %%visible_accent%%; background: #f0f2f5; }\n"
                    "QPushButton#OperationInfoButton:pressed { background: #e6e8ec; }\n"
                    "QPushButton#CancelOperationButton { background: #ffffff; color: #656d76; border: 1px solid #d8dee4;"
                    " border-radius: 7px; padding: 5px 12px; min-height: 26px; }\n"
                    "QPushButton#CancelOperationButton:hover:enabled { color: #b42318; border-color: #f1aeb5; background: #fff5f5; }\n"
                    "QPushButton#CancelOperationButton:pressed:enabled { background: #ffe3e3; }\n"
                    "/* 灰度提示 */\n"
                    "*[muted=\"true\"] { color: #6b7280; }\n"
                )
            stylesheet = (
                _TPL.replace("%%tc%%", tc_for_buttons if dark else tc)
                .replace("%%hover_c%%", hover_c)
                .replace("%%pressed_c%%", pressed_c)
                .replace("%%btn_top%%", btn_top)
                .replace("%%btn_hover_top%%", btn_hover_top)
                .replace("%%btn_border%%", btn_border)
                .replace("%%btn_text%%", btn_text)
                .replace("%%visible_accent%%", visible_accent)
                .replace("%%scroll_handle%%", scroll_handle)
                .replace("%%scroll_handle_hover%%", scroll_handle_hover)
                .replace("%%progress_chunk%%", progress_chunk)
                .replace("%%accent_text%%", accent_text)
                .replace("%%font_family%%", self._stylesheet_font_family())
            )
            stylesheet += self._extra_theme_qss(dark)
            stylesheet = (stylesheet
                .replace("%%tc%%", tc_for_buttons if dark else tc)
                .replace("%%hover_c%%", hover_c)
                .replace("%%pressed_c%%", pressed_c)
                .replace("%%btn_border%%", btn_border)
                .replace("%%btn_text%%", btn_text)
                .replace("%%visible_accent%%", visible_accent)
                .replace("%%accent_text%%", accent_text))
            self._theme_stylesheet = stylesheet
            if app is not None:
                app.setStyleSheet(stylesheet)
            # 精灵图按钮背景必须和当前页面背景一致，避免透明 PNG 边缘露出主题色。
            # 使用 _central_container_bg() 而非 _theme_role_colors()["bg_main"]，因为
            # 精灵图按钮直接挂在 CentralContainer 上，role_colors 的 bg_main 与实际
            # QSS 中 #CentralContainer 的背景色不完全一致，会在浅色/深色模式下都留下
            # 一圈可见的色差边缘。
            if hasattr(self, "about_sprite_btn"):
                sprite_bg = self._central_container_bg()
                self.about_sprite_btn.setStyleSheet(
                    f"background-color: {sprite_bg}; border: 1px solid {sprite_bg}; border-radius: 8px;")
            self._refresh_styled_widgets()
            if hasattr(self, "_apply_button_sizes"):
                self._apply_button_sizes()
            if hasattr(self, "_refresh_color_buttons"):
                self._refresh_color_buttons()
            if hasattr(self, "_refresh_header_language_buttons"):
                self._refresh_header_language_buttons()
            # 实时换色时只刷新全局设置页表面色，避免局部 QSS 级联把按钮文字/背景冲掉。
            self._refresh_settings_dialog_surfaces()
            # 导航栏须在全局 stylesheet 落地后刷新，确保 #SettingsNav 的高优先级生效
            if hasattr(self, "_refresh_settings_nav_style"):
                self._refresh_settings_nav_style()
            if hasattr(self, "_apply_log_viewer_theme"):
                self._apply_log_viewer_theme()
                try:
                    self._refresh_log_viewer()
                except Exception:
                    pass

    def _settings_nav_stylesheet(self, color=None) -> str:
            color = color or getattr(self, "_theme_color", core.config.get("theme_color", DEFAULT_THEME_COLOR))
            qcolor = QColor(color)
            if not qcolor.isValid():
                color = DEFAULT_THEME_COLOR
                qcolor = QColor(color)
            dark = bool(core.config.get("dark_mode", False))
            brightness = (qcolor.red() * 299 + qcolor.green() * 587 + qcolor.blue() * 114) / 1000
            if dark:
                bg = "#1a1b2e"
                border = "#3d3e56"
                item_fg = "#c8c8d8"
                # A white/very-light accent previously produced white text on a
                # white selected item in dark mode. Use the same contrast-safe
                # accent fallback as the main application stylesheet.
                selected_bg = "#3a3a50" if brightness >= 230 else color
                selected_text = "#e8e8f0" if brightness >= 230 else "#ffffff"
                selected_border = "#8b8ba3" if brightness >= 230 else color
                hover_bg = "#30304c"
            else:
                bg = "#ffffff"
                border = "#d0d7de"
                item_fg = "#57606a"
                selected_text = "#24292f" if brightness >= 170 else "#ffffff"
                selected_bg = "#f6f8fa" if brightness >= 230 else color
                selected_border = "#8c959f" if brightness >= 230 else color
                hover_bg = "#eaeef2"
            return (
                f"QListWidget#SettingsNav {{ background-color: {bg}; border: 1px solid {border};"
                f" border-radius: 8px; padding: 6px; outline: none; background-clip: padding; }}"
                f"QListWidget#SettingsNav::item {{ padding: 10px 14px; border-radius: 6px;"
                f" color: {item_fg}; font-size: 13px; }}"
                f"QListWidget#SettingsNav::item:selected {{ background-color: {selected_bg}; color: {selected_text}; border: 1px solid {selected_border}; font-weight: 500; }}"
                f"QListWidget#SettingsNav::item:hover:!selected {{ background-color: {hover_bg}; }}"
            )



    def _reset_history_only(self) -> None:
            """仅清空壁纸历史记录."""
            try:
                reply = QMessageBox.question(
                    self, t("重置壁纸历史"),
                    t("确定清空壁纸历史记录吗？不影响当前壁纸和文件夹。"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                core.clear_wallpaper_history(reset_slideshow_position=True)
                self.refresh_history_list()
                self.set_status(t("壁纸历史已清空"))
            except Exception as exc:
                QMessageBox.warning(self, t("重置壁纸历史"), t("重置失败：") + str(exc))

    def _reset_hotkeys_only(self) -> None:
            """仅重置右键菜单热键和应用内热键到默认值."""
            try:
                reply = QMessageBox.question(
                    self, t("重置快捷键"),
                    t("确定把所有快捷键恢复为默认值吗？"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                defaults = core.get_default_config()
                for key in ("hotkey_previous", "hotkey_next", "hotkey_random", "hotkey_jump"):
                    core.config[key] = defaults.get(key, "")
                core.config["app_shortcuts"] = dict(defaults.get("app_shortcuts", {}))
                core.save_config()
                # 刷新全局热键注册
                try:
                    if bool(core.config.get("global_hotkeys_enabled", False)):
                        core.refresh_global_hotkeys()
                    else:
                        core.stop_global_hotkeys()
                except Exception as exc:
                    core.log(f"刷新全局热键失败: {exc}")
                # v1.4.7: 应用内热键已移除, 不再刷新 _refresh_app_shortcuts.
                # 刷新设置页 UI
                self._refresh_context_shortcut_labels()
                self.refresh_from_config()
                self.set_status(t("快捷键已重置为默认值"))
            except Exception as exc:
                QMessageBox.warning(self, t("重置快捷键"), t("重置失败：") + str(exc))

    def _reset_appearance_only(self) -> None:
            """仅重置外观: 主题色/字体/DPI/暗色/性能模式/字体粗细大小 (v1.4.0 修复)."""
            try:
                reply = QMessageBox.question(
                    self, t("重置外观设置"),
                    t("确定重置外观设置吗？包括主题色、字体路径、字体粗细、字体大小、程序内 DPI、暗色模式、性能模式。"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                defaults = core.get_default_config()
                # v1.4.0 修复: 之前 pop("font_size") 会删除新 font_size 键, 现在改为重置为默认值.
                # 同时补上 font_weight / performance_level (之前漏掉).
                for key in ("theme_color", "font_path", "font_weight", "font_size",
                            "dpi_scale", "dark_mode", "enable_animations",
                            "wallpaper_transition_enabled", "transition_effect",
                            "transition_duration_ms", "wallpaper_transition_policy_version",
                            "performance_mode", "performance_level"):
                    if key in defaults:
                        core.config[key] = defaults.get(key)
                core.save_config()
                self._theme_color = core.config.get("theme_color", DEFAULT_THEME_COLOR)
                self._icon_pixmap_cache = OrderedDict()
                self._apply_performance_mode_runtime()
                self._rebuild_stylesheet()
                self._refresh_settings_nav_style()
                self._refresh_svg_button_icons()
                self.refresh_from_config()
                apply_dpi_environment(core.config)
                self.set_status(t("外观设置已重置"))
                QMessageBox.information(self, t("重置外观设置"), t("已重置主题色、字体路径、字体粗细、字体大小、程序内 DPI、暗色模式和性能模式。DPI 和字体设置需重启程序完全生效。"))
            except Exception as exc:
                QMessageBox.warning(self, t("重置外观设置"), t("重置失败：") + str(exc))

    def _reset_tray_only(self) -> None:
            """仅重置托盘菜单项到默认列表."""
            try:
                reply = QMessageBox.question(
                    self, t("重置托盘菜单"),
                    t("确定把托盘菜单项恢复为默认列表吗？"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                defaults = core.get_default_config()
                core.config["tray_menu_items"] = list(defaults.get("tray_menu_items", []))
                core.config["tray_click_action"] = defaults.get("tray_click_action", "next")
                core.save_config()
                self.create_or_update_tray() if core.config.get("tray_icon", True) else None
                self.refresh_from_config()
                self.set_status(t("托盘菜单已重置"))
            except Exception as exc:
                QMessageBox.warning(self, t("重置托盘菜单"), t("重置失败：") + str(exc))

    def _reset_log_buffer_only(self) -> None:
            """仅清空内存实时日志缓冲区."""
            try:
                from app.log_setup import clear_recent_logs
                clear_recent_logs()
                self._refresh_log_viewer()
                self.set_status(t("实时日志缓冲区已清空"))
            except Exception as exc:
                QMessageBox.warning(self, t("清空实时日志"), t("清空失败：") + str(exc))

    def _unregister_context_menu_only(self) -> None:
            """仅注销桌面右键菜单注册表项 (v1.4.7: 无需 admin)."""
            try:
                if not core.IS_WINDOWS:
                    show_info(self, t("注销桌面右键菜单"), t("此功能仅在 Windows 上可用。"))
                    return
                reply = QMessageBox.question(
                    self, t("注销桌面右键菜单"),
                    t("确定从 Windows 桌面右键菜单移除本程序注册的项吗？"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                # v1.4.7: register_context 已改用 HKCU\Software\Classes, 无需 admin.
                try:
                    # 把所有 ctx_* 设为 False, 然后调用 register_context 清理注册表
                    for key in ("ctx_last_wallpaper", "ctx_next_wallpaper", "ctx_random_wallpaper", "ctx_jump_to_wallpaper"):
                        core.config[key] = False
                    core.save_config()
                    core.register_context(show_admin_prompt=False)
                    self.set_status(t("桌面右键菜单已注销"))
                    show_info(self, t("注销桌面右键菜单"), t("已从桌面右键菜单移除本程序注册的项。"))
                except Exception as exc:
                    core.log(f"注销桌面右键菜单失败: {exc}", level="ERROR", exc_info=True)
                    QMessageBox.warning(self, t("注销桌面右键菜单"), t("注销失败：") + str(exc))
            except Exception as exc:
                QMessageBox.warning(self, t("注销桌面右键菜单"), t("操作失败：") + str(exc))

    def _on_dark_mode_toggled(self, checked: bool) -> None:
            """切换暗色模式并立即应用样式，布局属性和按钮大小保持不变。"""
            core.config["dark_mode"] = bool(checked)
            self._rebuild_stylesheet()
            self._refresh_settings_nav_style()
            if hasattr(self, "_refresh_color_buttons"):
                self._refresh_color_buttons()
            if hasattr(self, "_refresh_styled_widgets"):
                self._refresh_styled_widgets()
            self._refresh_svg_button_icons()
            self.set_status(t("暗色模式已开启") if checked else t("亮色模式已恢复"))


    def choose_log_file_path(self):
            default = self._log_file_path() or self._default_log_path()
            dest, _ = QFileDialog.getSaveFileName(self, t("选择日志保存路径"), default, t("日志文件 (*.log *.txt);;所有文件 (*.*)"))
            if not dest:
                return False
            core.config["log_file_path"] = dest
            core.save_config()
            if hasattr(self, "log_path_edit"):
                self.log_path_edit.setText(dest)
            self.set_status(t("日志路径已设置：") + f"{dest}")
            try:
                if core.config.get("log_enabled", False):
                    core.log(t("日志路径已设置：") + f"{dest}")
            except Exception:
                pass
            return True



    def _is_desktop_foreground(self) -> bool:
            """Return True when the desktop shell is the active foreground surface.

            Windows 有真实桌面窗口类名；Linux/macOS 没有统一公共 API，这里保守返回
            True，避免在非 Windows 端误暂停用户视频。
            """
            try:
                if not core.IS_WINDOWS:
                    return True
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                if not hwnd:
                    return True
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, buf, 256)
                cls = (buf.value or "").lower()
                return cls in {"progman", "workerw", "shelldll_defview", "syslistview32"}
            except Exception:
                return True

    def _context_action_defs(self):
            # Keep GUI checkbox names identical to the actual Windows desktop
            # context-menu registry labels written by core.engine.
            return [
                ("previous", t("上一个桌面背景"), "PgUp", "ctx_last_wallpaper", "ctx_prev"),
                ("next", t("下一个桌面背景"), "PgDown", "ctx_next_wallpaper", "ctx_next"),
                ("random", t("随机一个桌面背景"), "R", "ctx_random_wallpaper", "ctx_random"),
                ("jump", t("跳转到壁纸"), "J", "ctx_jump_to_wallpaper", "ctx_jump"),
            ]

    def _refresh_context_shortcut_labels(self):
            """刷新右键菜单复选框文案 + 设置页快捷键当前值标签."""
            try:
                for action, label, _default_key, _cfg_key, widget_name in self._context_action_defs():
                    widget = getattr(self, widget_name, None)
                    if self._is_qobject_alive(widget):
                        try:
                            widget.setText(self._context_checkbox_label(action, label))
                        except Exception:
                            pass
                    current_labels = getattr(self, "ctx_shortcut_current_labels", {})
                    if action in current_labels and self._is_qobject_alive(current_labels[action]):
                        try:
                            current_labels[action].setText(self._context_hotkey_display(action))
                        except Exception:
                            pass
            except Exception as exc:
                try:
                    core.log(f"刷新右键菜单快捷键标签失败: {exc}", level="WARNING")
                except Exception:
                    pass

    def on_context_hotkey_clear(self, action: str):
            """清除按钮：清空已保存的右键菜单快捷键。"""
            self.set_context_hotkey(action, "")

    def _warn_duplicate_context_hotkey(self, action: str, seq_str: str) -> bool:
            duplicate_label = self._duplicate_context_hotkey_action(action, seq_str)
            if not duplicate_label:
                return False
            QMessageBox.warning(
                self,
                t("快捷键冲突"),
                t("该快捷键已被其他动作使用：") + str(duplicate_label),
            )
            return True

    def on_global_hotkeys_enabled_changed(self, checked: bool) -> None:
            """Enable/disable system-level global hotkey registration."""
            core.config["global_hotkeys_enabled"] = bool(checked)
            core.save_config()
            try:
                if checked:
                    core.refresh_global_hotkeys()
                else:
                    core.stop_global_hotkeys()
            except Exception as exc:
                core.log(f"切换全局热键失败: {exc}")
            self.set_status(t("全局热键已开启") if checked else t("全局热键已关闭"))

    def set_context_hotkey(self, action: str, seq_str: str) -> None:
            """Apply and persist a right-click menu shortcut hint.

            When global hotkeys are enabled, the same shortcut text is also validated
            and registered as a system-level hotkey; otherwise it is only shown in
            the desktop right-click menu label.
            """
            seq_str = str(seq_str or "").strip()
            if seq_str:
                global_hotkeys_on = bool(core.config.get("global_hotkeys_enabled", False))
                if global_hotkeys_on:
                    try:
                        parsed = core._parse_hotkey_string(seq_str)
                    except Exception:
                        parsed = None
                    if parsed is None:
                        QMessageBox.warning(
                            self,
                            t("全局热键冲突"),
                            t("当前已启用全局热键，请输入可注册的组合：Ctrl+Alt+N，或单独的 Ctrl / Alt / Shift / Win。若只想作为右键菜单提示，请先关闭全局热键。"),
                        )
                        return
                if self._warn_duplicate_context_hotkey(action, seq_str):
                    return
            core.config[f"hotkey_{action}"] = seq_str
            core.save_config()
            # Refresh labels in the settings UI
            self._refresh_context_shortcut_labels()
            # 仅在用户明确开启全局热键时注册系统级热键；否则只保存为右键菜单显示提示。
            try:
                if bool(core.config.get("global_hotkeys_enabled", False)):
                    core.refresh_global_hotkeys()
                else:
                    core.stop_global_hotkeys()
            except Exception as exc:
                core.log(f"刷新全局热键失败: {exc}")
            # Automatically sync the context menu to the registry if running as admin
            try:
                if core.IS_WINDOWS and core.is_windows_admin():
                    core.register_context(show_admin_prompt=False)
            except Exception as exc:
                core.log(f"自动同步右键菜单失败: {exc}")
            # Update status bar message
            if seq_str:
                self.set_status((t("已保存右键菜单快捷键 / 全局热键：") if core.config.get("global_hotkeys_enabled", False) else t("已保存右键菜单快捷键：")) + seq_str)
            else:
                self.set_status(t("已清除快捷键"))

    def record_context_hotkey(self, action: str) -> None:
            """Begin recording a shortcut for the given right-click menu action.

            A record button triggers this method. It uses the pynput library to
            capture all keys pressed until all keys are released. The value is saved
            as a right-click menu shortcut hint by default; only the optional global
            hotkey switch makes it system-wide.
            """
            try:
                from pynput import keyboard  # type: ignore
            except Exception:
                # Pynput is an optional dependency; without it we cannot record.
                self.set_status(t("pynput 未安装，无法录制快捷键"))
                return

            # Inform the user that recording has started
            self.set_status(t("录制中") + "…")

            def worker() -> None:
                keys_down: set[object] = set()
                recorded: set[object] = set()

                # Use nested functions so listener.stop() can be called by returning False
                def on_press(key):
                    try:
                        keys_down.add(key)
                        recorded.add(key)
                    except Exception:
                        pass

                def on_release(key):
                    try:
                        keys_down.discard(key)
                    except Exception:
                        pass
                    # Once all keys are released, stop the listener by returning False
                    if not keys_down:
                        return False

                # Run the keyboard listener; this blocks until on_release returns False
                with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
                    listener.join()

                # Convert recorded keys into a human-friendly combination string
                parts: list[str] = []
                try:
                    # Determine which modifier keys were pressed
                    def has_key(names: tuple[str, ...]) -> bool:
                        for item in recorded:
                            try:
                                name = getattr(item, 'name', None)
                                if name in names:
                                    return True
                            except Exception:
                                pass
                        return False
                    if has_key(('ctrl', 'ctrl_l', 'ctrl_r', 'control')):
                        parts.append('Ctrl')
                    if has_key(('alt', 'alt_l', 'alt_r', 'alt_gr')):
                        parts.append('Alt')
                    if has_key(('shift', 'shift_l', 'shift_r')):
                        parts.append('Shift')
                    if has_key(('cmd', 'cmd_l', 'cmd_r', 'meta')):
                        parts.append('Win')
                    # Append non-modifier keys (letters/digits/function keys)
                    for item in recorded:
                        # Skip if already accounted for as modifier
                        try:
                            name = getattr(item, 'name', None)
                            if name in ('ctrl', 'ctrl_l', 'ctrl_r', 'control', 'alt', 'alt_l', 'alt_r', 'alt_gr', 'shift', 'shift_l', 'shift_r', 'cmd', 'cmd_l', 'cmd_r', 'meta'):
                                continue
                            if hasattr(item, 'char') and item.char:
                                parts.append(item.char.upper())
                            elif name:
                                # For special keys like f1, space, etc., capitalize the name
                                parts.append(name.upper())
                        except Exception:
                            pass
                except Exception:
                    parts = []
                # Deduplicate while preserving order
                seq_parts: list[str] = []
                for part in parts:
                    if part not in seq_parts:
                        seq_parts.append(part)
                seq_str = "+".join(seq_parts)

                # Apply the new hotkey on the GUI thread
                self.hotkey_recorded_signal.emit(action, seq_str)

            import threading
            worker_thread = threading.Thread(target=worker, daemon=True)
            worker_thread.start()

    def _update_ctx(self, key, value):
            core.config[key] = bool(value)
            core.save_config()
            # 启用/禁用某个右键菜单项后，对应的全局热键也需要重新注册
            try:
                core.refresh_global_hotkeys()
            except Exception as exc:
                core.log(f"刷新全局热键失败: {exc}")

    def register_context_with_prompt(self):
            # v1.4.7: 右键菜单注册改用 HKCU\Software\Classes, 无需 admin.
            # 直接调用 sync_context_menu, 不再弹 UAC 提权提示.
            self.sync_context_menu(show_message=True)

    def sync_context_menu(self, show_message=False, only_if_needed=False):
            if only_if_needed and core.IS_WINDOWS:
                try:
                    if core.is_context_menu_synced():
                        self.set_status(t("右键菜单已是最新，无需同步"))
                        if show_message:
                            QMessageBox.information(self, t("右键菜单"), t("右键菜单已是最新，无需同步"))
                        return True
                except Exception as exc:
                    core.log(f"检查右键菜单同步状态失败: {exc}")
            ok = core.register_context(show_admin_prompt=False)
            # 失败时把 core.last_operation_error 一起带给用户，避免"同步失败或已跳过"
            # 这种不带原因的通用提示
            if ok:
                self.set_status(t("右键菜单已同步"))
            else:
                reason = getattr(core, "last_operation_error", "") or ""
                self.set_status(t("右键菜单同步失败或已跳过") + (f"：{reason}" if reason else ""))
            if show_message:
                if ok:
                    QMessageBox.information(self, t("右键菜单"), t("同步完成"))
                else:
                    reason = getattr(core, "last_operation_error", "") or ""
                    QMessageBox.information(self, t("右键菜单"), t("同步失败或已跳过") + (f"\n\n{t('原因')}：{reason}" if reason else ""))
            return ok

    def get_pyqt_startup_folder_path(self):
            try:
                return core.get_startup_folder_path_windows()
            except Exception:
                return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")

    def _startup_launch_command(self) -> str:
            if core.is_frozen():
                return f'"{app_executable_path()}" --hide'
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            launcher = pythonw if os.path.exists(pythonw) else sys.executable
            return f'"{launcher}" "{entry_script_path()}" --hide'

    def set_auto_start(self, enable: bool):
            """Windows branch only writes the Windows Startup-folder VBS launcher."""
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

    def _schedule_preview_refresh(self, initial_delay: int = 0):
            """分批刷新预览；性能模式下只做必要刷新，避免连续切换时堆积解码任务。

            v1.4.8: 平衡模式从 4 次延迟刷新 (120/450/1000/1800ms) 减少为 2 次
            (300/800ms)。旧版 4 次刷新在每次切换壁纸后会反复触发 update_preview →
            refresh_history_list → _load_icon_pixmap，即使有缓存也会在 GUI 线程
            上做 8 项缩略图的 QPixmap 构造和列表清空/重建，叠加起来造成明显卡顿。
            2 次刷新足以覆盖系统壁纸生效延迟，同时把 GUI 线程开销砍半。
            """
            def _first_refresh():
                self.update_preview()
                level = self._perf_level()
                if level == "power_saver":
                    delays = (800,)  # 省电: 只刷新一次, 延迟更长
                elif level == "performance":
                    delays = (700,)  # 性能: 只刷新一次
                else:
                    delays = (300, 800)  # 平衡: 2 次（从 4 次精简）
                for delay in delays:
                    QTimer.singleShot(delay, self.update_preview_if_changed)
            if initial_delay and initial_delay > 0:
                QTimer.singleShot(int(initial_delay), _first_refresh)
            else:
                _first_refresh()

    def _refresh_favorites_list(self) -> None:
            """刷新收藏夹 QListWidget 显示."""
            if not hasattr(self, "favorites_list"):
                return
            previous_block_state = self.favorites_list.blockSignals(True)
            try:
                self.favorites_list.clear()
                for path in core.list_favorites(limit=50, existing_only=True):
                    item = QListWidgetItem()
                    item.setToolTip(path)
                    item.setData(Qt.UserRole, path)
                    item.setSizeHint(QSize(118, 78))
                    pix = self._load_icon_pixmap(path, QSize(108, 68))
                    if not pix.isNull():
                        item.setIcon(QIcon(pix))
                    self.favorites_list.addItem(item)
            except Exception as exc:
                core.log(f"刷新收藏夹失败: {exc}", level="WARNING", exc_info=True)
            finally:
                self.favorites_list.blockSignals(previous_block_state)

    def _toggle_favorite_current(self) -> None:
            """切换当前壁纸的收藏状态."""
            try:
                path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
                if not path or not os.path.isfile(path):
                    self.set_status(t("当前无壁纸可收藏"))
                    return
                is_now_favorite = core.toggle_favorite(path)
                self._refresh_favorites_list()
                self._update_favorite_button_state()
                self.set_status(t("已加入收藏夹") if is_now_favorite else t("已从收藏夹移除"))
            except Exception as exc:
                core.log(f"切换收藏失败: {exc}", level="WARNING", exc_info=True)
                QMessageBox.warning(self, t("收藏夹"), t("保存收藏失败：") + str(exc))

    def _update_favorite_button_state(self) -> None:
            """根据当前壁纸是否已收藏, 更新按钮文案."""
            try:
                if not hasattr(self, "btn_favorite_current"):
                    return
                path = core.config.get("current_wallpaper") or ""
                if path and core.is_favorite(path):
                    self.btn_favorite_current.setText(t("取消收藏"))
                    self.btn_favorite_current.setToolTip(t("从收藏夹移除当前壁纸"))
                else:
                    self.btn_favorite_current.setText(t("收藏当前"))
                    self.btn_favorite_current.setToolTip(t("把当前壁纸加入收藏夹（收藏夹不会随历史滚动消失）"))
            except Exception as exc:
                core.log(f"更新收藏按钮状态失败: {exc}", level="WARNING")

    def _apply_favorite_item(self, item) -> None:
            """单击收藏项 → 作为完整图片模式事务应用."""
            try:
                path = item.data(Qt.UserRole) if item else ""
                self._apply_static_wallpaper_item(path, t("收藏夹"))
            except Exception as exc:
                try:
                    core.log(f"应用收藏壁纸失败: {exc}", level="WARNING")
                except Exception:
                    pass

    def open_history_item_location_by_item(self, item) -> None:
            """双击收藏/历史项 → 打开文件位置 (复用)."""
            try:
                path = item.data(Qt.UserRole) if item else ""
                self._open_file_location(path)
            except Exception:
                pass

    def _show_favorite_context_menu(self, pos) -> None:
            """右键收藏项 → 弹出菜单 (移除收藏/打开位置)."""
            try:
                from PySide6.QtWidgets import QMenu
                item = self.favorites_list.itemAt(pos)
                if not item:
                    return
                path = item.data(Qt.UserRole) or ""
                menu = QMenu(self)
                self._prepare_popup_menu(menu)
                act_remove = menu.addAction(t("移除收藏"))
                act_open = menu.addAction(t("打开文件位置"))
                action = menu.exec(self.favorites_list.viewport().mapToGlobal(pos))
                if action == act_remove:
                    try:
                        if core.remove_favorite(path):
                            self._refresh_favorites_list()
                            self._update_favorite_button_state()
                            self.set_status(t("已从收藏夹移除"))
                    except Exception as exc:
                        core.log(f"移除收藏失败: {exc}", level="WARNING", exc_info=True)
                        QMessageBox.warning(self, t("收藏夹"), t("移除收藏失败：") + str(exc))
                elif action == act_open:
                    self._open_file_location(path)
            except Exception as exc:
                try:
                    core.log(f"收藏右键菜单失败: {exc}", level="WARNING")
                except Exception:
                    pass

    def _clear_all_favorites(self) -> None:
            """清空全部收藏."""
            try:
                reply = QMessageBox.question(
                    self, t("清空收藏"),
                    t("确定清空全部收藏吗？不会删除壁纸文件。"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                core.clear_favorites()
                self._refresh_favorites_list()
                self._update_favorite_button_state()
                self.set_status(t("收藏夹已清空"))
            except Exception as exc:
                QMessageBox.warning(self, t("清空收藏"), t("清空失败：") + str(exc))

    def _open_file_location(self, path: str):
            if not path or not os.path.exists(path):
                return
            try:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            except Exception as e:
                QMessageBox.warning(self, t("跳转失败"), str(e))

    def _load_icon_pixmap(self, path: str, size: QSize) -> QPixmap:
            try:
                stat = os.stat(path)
                cache_key = (path, int(stat.st_mtime), int(stat.st_size), int(size.width()), int(size.height()))
            except Exception:
                cache_key = (path, 0, 0, int(size.width()), int(size.height()))
            cache = getattr(self, "_icon_pixmap_cache", None)
            if cache is None:
                cache = OrderedDict()
                self._icon_pixmap_cache = cache
            elif not isinstance(cache, OrderedDict):
                cache = OrderedDict(cache)
                self._icon_pixmap_cache = cache
            cached = cache.get(cache_key)
            if cached is not None:
                cache.move_to_end(cache_key)
                return cached
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            # v1.4.6: 三档性能模式 → 图像解码内存上限
            _level = self._perf_level()
            if _level == "power_saver":
                _alloc_limit = 48
            elif _level == "performance":
                _alloc_limit = 64
            else:
                _alloc_limit = 128
            reader.setAllocationLimit(_alloc_limit)
            original = reader.size()
            if original.isValid():
                scaled = original.scaled(size, Qt.KeepAspectRatio)
                if scaled.isValid():
                    reader.setScaledSize(scaled)
            image = reader.read()
            pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
            cache[cache_key] = pixmap
            cache.move_to_end(cache_key)
            # v1.4.6: 三档性能模式 → 缩略图缓存大小
            _level2 = self._perf_level()
            if _level2 == "power_saver":
                max_cache_items = 32
            elif _level2 == "performance":
                max_cache_items = 64
            else:
                max_cache_items = 96
            while len(cache) > max_cache_items:
                cache.popitem(last=False)
            return pixmap

    def open_current_folder(self):
            path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
            folder = os.path.dirname(path) if path else core.config.get("slide_folder", "")
            if folder and os.path.isdir(folder):
                os.startfile(folder)

    def open_wallpaper_sidebar(self) -> None:
            sb = getattr(self, "_sidebar", None)
            if sb is not None:
                try:
                    if hasattr(sb, "_is_closing") and not sb._is_closing:
                        sb.raise_()
                        sb.activateWindow()
                        return
                except Exception:
                    pass
                self._sidebar = None

            from ui.sidebar import WallpaperSidebar

            folder = core.config.get("slide_folder", "")
            current = core.config.get("current_wallpaper", "") or core.get_current_wallpaper()

            if not folder or not os.path.isdir(folder):
                show_info(self, t("提示"), t("请先在软件中设置壁纸文件夹"))
                return

            def _switch(path: str) -> None:
                try:
                    core.set_wallpaper(path, t("侧边栏切换"))
                    QTimer.singleShot(50, self.update_preview)
                except Exception as exc:
                    core.log(f"侧边栏切换壁纸失败: {exc}")

            sidebar_log = self._log_file_path() if core.config.get("log_enabled", False) else None
            self._sidebar = WallpaperSidebar(
                self, folder, current, sidebar_log,
                show_message=lambda title, msg: show_info(self, title, msg),
                switch_wallpaper=_switch,
            )
            self._sidebar.closed.connect(lambda: setattr(self, "_sidebar", None))

    def save_selected_bing_as(self):
            item = self.bing_list.currentItem()
            if not item:
                show_info(self, t("必应壁纸"), t("请先在列表中选择一张已缓存的必应壁纸。"))
                return
            src = item.data(Qt.UserRole)
            if not src or not os.path.exists(src):
                show_warning(self, t("必应壁纸"), t("选中的缓存文件不存在。"))
                return
            dst, _ = QFileDialog.getSaveFileName(self, t("另存必应壁纸"), os.path.join(str(Path.home()), os.path.basename(src)), t("JPEG 图片 (*.jpg);;所有文件 (*.*)"))
            if not dst:
                return
            try:
                shutil.copy2(src, dst)
                self.set_status(t("已另存为：") + f"{dst}")
            except Exception as e:
                QMessageBox.warning(self, t("另存失败"), str(e))

    def restart_as_admin(self, extra_args=None):
            self._closing_for_exit = True
            if core.restart_as_admin(extra_args=extra_args):
                if self.tray:
                    self.tray.hide()
                    self.tray.deleteLater()
                    self.tray = None
                    QApplication.processEvents()
                    self._refresh_shell_ui_later()
                core._do_exit(0)
            else:
                self._closing_for_exit = False
                show_warning(self, t("提权失败"), t("无法以管理员身份重启，请手动右键以管理员身份运行。"))


class _MacOSMainWindowMixin:
    def _prepare_popup_menu(self, menu: QMenu) -> QMenu:
            """Prepare a QMenu popup for consistent rendering.

            Note: We intentionally do NOT set WA_TranslucentBackground on macOS.
            The native Qt theme already draws rounded corners and shadows for
            popup menus.  Forcing translucency + frameless flags breaks the native
            theme, causing asymmetric corners and missing shadows.
            """
            if menu is None:
                return menu
            return menu



    def _on_dark_mode_toggled(self, checked: bool) -> None:
            """切换暗色模式并立即应用样式，布局属性和按钮大小保持不变。"""
            core.config["dark_mode"] = bool(checked)
            self._rebuild_stylesheet()
            self._refresh_settings_nav_style()
            if hasattr(self, "_refresh_color_buttons"):
                self._refresh_color_buttons()
            if hasattr(self, "_refresh_styled_widgets"):
                self._refresh_styled_widgets()
            self._refresh_svg_button_icons()
            self.set_status(t("暗色模式已开启") if checked else t("亮色模式已恢复"))

    def _on_performance_mode_toggled(self, checked: bool) -> None:
            core.config["performance_mode"] = bool(checked)
            core.config["performance_level"] = "performance" if checked else "balanced"
            core.save_config()
            self._apply_performance_mode_runtime()

    def _on_performance_level_changed(self, index: int) -> None:
            try:
                combo = getattr(self, "perf_mode_combo", None)
                if not self._is_qobject_alive(combo):
                    return
                level = combo.currentData()
                if level not in ("power_saver", "balanced", "performance"):
                    return
                core.config["performance_level"] = level
                core.config["performance_mode"] = (level == "performance")
                core.save_config()
                self._apply_performance_mode_runtime()
                _status_map = {
                    "power_saver": t("性能模式：节能（降低后台刷新频率）"),
                    "balanced": t("性能模式：均衡（推荐）"),
                    "performance": t("性能模式：流畅（更快刷新响应）"),
                }
                self.set_status(_status_map.get(level, t("性能模式已切换")))
            except Exception as exc:
                try:
                    core.log(f"切换性能模式失败: {exc}", level="WARNING", exc_info=True)
                except Exception:
                    pass

    def _is_desktop_foreground(self) -> bool:
            """Return True when the desktop shell is the active foreground surface.

            Bug 5 fix: macOS 端现在通过 platform_adapters.integration.is_desktop_foreground()
            实际检测（osascript 查询 System Events 前台进程名）。之前总是返回 True
            会让"桌面失焦时暂停"视频策略和 HTML 自动暂停功能在 macOS 上完全失效。
            """
            try:
                if not core.IS_WINDOWS:
                    # Bug 5 fix: 调用平台特定实现，而不是无条件返回 True。
                    from platform_adapters import integration
                    if hasattr(integration, "is_desktop_foreground"):
                        return bool(integration.is_desktop_foreground())
                    return True
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                if not hwnd:
                    return True
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, buf, 256)
                cls = (buf.value or "").lower()
                return cls in {"progman", "workerw", "shelldll_defview", "syslistview32"}
            except Exception:
                return True

    def get_pyqt_startup_folder_path(self):
            try:
                return core.get_startup_folder_path_windows()
            except Exception:
                return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")

    def _startup_launch_command(self) -> str:
            if core.is_frozen():
                return f'"{app_executable_path()}" --hide'
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            launcher = pythonw if os.path.exists(pythonw) else sys.executable
            return f'"{launcher}" "{entry_script_path()}" --hide'

    def set_auto_start(self, enable: bool):
            """macOS branch only writes the LaunchAgents plist."""
            agents_dir = os.path.expanduser("~/Library/LaunchAgents")
            plist_path = os.path.join(agents_dir, "com.xxdz.shangbackground.plist")
            label = "com.xxdz.shangbackground"
            if enable:
                os.makedirs(agents_dir, exist_ok=True)
                log_dir = os.path.expanduser("~/Library/Logs/ShangBackground")
                os.makedirs(log_dir, exist_ok=True)
                plist = {
                    "Label": label,
                    "ProgramArguments": [sys.executable, "--hide"] if core.is_frozen() else [sys.executable, entry_script_path(), "--hide"],
                    "RunAtLoad": True,
                    "WorkingDirectory": core.BASE_DIR,
                    "StandardOutPath": os.path.join(log_dir, "launch_stdout.log"),
                    "StandardErrorPath": os.path.join(log_dir, "launch_stderr.log"),
                }
                tmp_path = plist_path + ".tmp"
                with open(tmp_path, "wb") as f:
                    plistlib.dump(plist, f)
                os.replace(tmp_path, plist_path)
                os.chmod(plist_path, 0o600)
                subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                result = subprocess.run(
                    ["launchctl", "bootstrap", f"gui/{os.getuid()}", plist_path],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                    check=False,
                )
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout or f"launchctl exit {result.returncode}").strip())
                core.log(f"macOS 开机自启动已启用: {plist_path}")
            else:
                result = subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                    check=False,
                )
                if os.path.exists(plist_path):
                    os.remove(plist_path)
                # bootout commonly reports 'not found' when already disabled; only
                # surface other diagnostics after the file has been removed.
                detail = (result.stderr or result.stdout or "").lower()
                if result.returncode != 0 and detail and "not found" not in detail and "no such process" not in detail:
                    core.log(f"launchctl bootout 提示: {detail.strip()}")
                core.log("macOS 开机自启动已禁用")

    def _open_file_location(self, path: str):
            if not path or not os.path.exists(path):
                return
            try:
                subprocess.Popen(["open", "-R", path])
            except Exception as e:
                QMessageBox.warning(self, t("跳转失败"), str(e))

    def _load_icon_pixmap(self, path: str, size: QSize) -> QPixmap:
            try:
                stat = os.stat(path)
                cache_key = (path, int(stat.st_mtime), int(stat.st_size), int(size.width()), int(size.height()))
            except Exception:
                cache_key = (path, 0, 0, int(size.width()), int(size.height()))
            cache = getattr(self, "_icon_pixmap_cache", None)
            if cache is None:
                cache = OrderedDict()
                self._icon_pixmap_cache = cache
            elif not isinstance(cache, OrderedDict):
                cache = OrderedDict(cache)
                self._icon_pixmap_cache = cache
            cached = cache.get(cache_key)
            if cached is not None:
                cache.move_to_end(cache_key)
                return cached
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            # v1.4.6: 三档性能模式 → 图像解码内存上限
            _level = self._perf_level()
            if _level == "power_saver":
                _alloc_limit = 48
            elif _level == "performance":
                _alloc_limit = 64
            else:
                _alloc_limit = 128
            reader.setAllocationLimit(_alloc_limit)
            original = reader.size()
            if original.isValid():
                scaled = original.scaled(size, Qt.KeepAspectRatio)
                if scaled.isValid():
                    reader.setScaledSize(scaled)
            image = reader.read()
            pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
            cache[cache_key] = pixmap
            cache.move_to_end(cache_key)
            # v1.4.6: 三档性能模式 → 缩略图缓存大小
            _level2 = self._perf_level()
            if _level2 == "power_saver":
                max_cache_items = 32
            elif _level2 == "performance":
                max_cache_items = 64
            else:
                max_cache_items = 96
            while len(cache) > max_cache_items:
                cache.popitem(last=False)
            return pixmap

    def open_current_folder(self):
            path = core.config.get("current_wallpaper") or core.get_current_wallpaper()
            folder = os.path.dirname(path) if path else core.config.get("slide_folder", "")
            if folder and os.path.isdir(folder):
                subprocess.Popen(["open", folder])

    def open_wallpaper_sidebar(self) -> None:
            sb = getattr(self, "_sidebar", None)
            if sb is not None:
                try:
                    if hasattr(sb, "_is_closing") and not sb._is_closing:
                        sb.raise_()
                        sb.activateWindow()
                        return
                except Exception:
                    pass
                self._sidebar = None

            from ui.sidebar import WallpaperSidebar

            folder = core.config.get("slide_folder", "")
            current = core.config.get("current_wallpaper", "") or core.get_current_wallpaper()

            if not folder or not os.path.isdir(folder):
                show_info(self, t("提示"), t("请先在软件中设置壁纸文件夹"))
                return

            def _switch(path: str) -> None:
                try:
                    core.set_wallpaper(path, t("侧边栏切换"))
                    QTimer.singleShot(50, self.update_preview)
                except Exception as exc:
                    core.log(f"侧边栏切换壁纸失败: {exc}")

            sidebar_log = self._log_file_path() if core.config.get("log_enabled", False) else None
            self._sidebar = WallpaperSidebar(
                self, folder, current, sidebar_log,
                show_message=lambda title, msg: show_info(self, title, msg),
                switch_wallpaper=_switch,
            )
            self._sidebar.closed.connect(lambda: setattr(self, "_sidebar", None))

    def save_selected_bing_as(self):
            item = self.bing_list.currentItem()
            if not item:
                show_info(self, t("必应壁纸"), t("请先在列表中选择一张已缓存的必应壁纸。"))
                return
            src = item.data(Qt.UserRole)
            if not src or not os.path.exists(src):
                show_warning(self, t("必应壁纸"), t("选中的缓存文件不存在。"))
                return
            dst, _ = QFileDialog.getSaveFileName(self, t("另存必应壁纸"), os.path.join(str(Path.home()), os.path.basename(src)), t("JPEG 图片 (*.jpg);;所有文件 (*.*)"))
            if not dst:
                return
            try:
                shutil.copy2(src, dst)
                self.set_status(t("已另存为：") + f"{dst}")
            except Exception as e:
                QMessageBox.warning(self, t("另存失败"), str(e))

    def restart_as_admin(self, extra_args=None):
            QMessageBox.information(
                self,
                t("管理员重启"),
                t("macOS 版不提供 GUI 管理员提权重启。需要权限操作时，请使用系统设置或终端完成。"),
            )


if core.IS_WINDOWS:
    _PlatformMainWindowMixin = _WindowsMainWindowMixin
elif core.IS_MACOS:
    _PlatformMainWindowMixin = _MacOSMainWindowMixin
else:
    _PlatformMainWindowMixin = _LinuxMainWindowMixin


class ShangBackgroundWindow(_PlatformMainWindowMixin, VideoFocusMixin, _SharedShangBackgroundWindow):
    """Single shared main window with small platform-specific method overrides."""
    pass
