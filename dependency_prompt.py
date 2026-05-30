from __future__ import annotations

import importlib.util
import queue
import subprocess
import sys
import threading
from typing import Iterable

from app_config import DEPENDENCIES, FONT_FAMILY

try:  # PySide6 是新版主界面的首选依赖提示驱动。
    from PySide6.QtCore import QObject, Qt, QTimer, Signal, QEasingCurve, QPropertyAnimation
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QScroller,
        QScrollerProperties,
        QSizePolicy,
        QGraphicsOpacityEffect,
        QVBoxLayout,
        QWidget,
    )

    PYSIDE_PROMPT_AVAILABLE = True
except Exception:  # pragma: no cover - PySide6 自身缺失时只能回退到 Tk 提示。
    PYSIDE_PROMPT_AVAILABLE = False


def get_missing_dependencies(availability):
    missing = []
    for dep in DEPENDENCIES:
        module = dep["module"]
        installed = availability.get(module)
        if installed is None:
            installed = importlib.util.find_spec(module) is not None
        if not installed:
            missing.append(dep)
    return missing


def build_install_command(packages: Iterable[str]):
    return [sys.executable, "-m", "pip", "install", *packages]


if PYSIDE_PROMPT_AVAILABLE:
    def _enable_touch_scrolling(widget):
        try:
            target = widget.viewport() if hasattr(widget, "viewport") else widget
            target.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
            QScroller.grabGesture(target, QScroller.ScrollerGestureType.TouchGesture)
            scroller = QScroller.scroller(target)
            props = scroller.scrollerProperties()
            props.setScrollMetric(QScrollerProperties.ScrollMetric.OvershootDragResistanceFactor, 0.18)
            props.setScrollMetric(QScrollerProperties.ScrollMetric.OvershootScrollDistanceFactor, 0.10)
            props.setScrollMetric(QScrollerProperties.ScrollMetric.DecelerationFactor, 0.10)
            scroller.setScrollerProperties(props)
        except Exception:
            pass


    def _fade_in_dialog(dialog):
        try:
            effect = QGraphicsOpacityEffect(dialog)
            dialog.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", dialog)
            anim.setDuration(180)
            anim.setStartValue(0.22)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            dialog._fade_anim = anim
            anim.start()
        except Exception:
            pass


    class _InstallSignals(QObject):
        log = Signal(str)
        done = Signal(int, str)


    class DependencyInstallDialog(QDialog):
        def __init__(self, packages, parent=None):
            super().__init__(parent)
            self.packages = list(packages)
            self.command = build_install_command(self.packages)
            self.returncode = None
            self.error = ""
            self._signals = _InstallSignals()
            self._signals.log.connect(self._append_log)
            self._signals.done.connect(self._finish)
            self._build_ui()

        def showEvent(self, event):  # noqa: N802 - Qt API
            super().showEvent(event)
            _fade_in_dialog(self)

        def _build_ui(self):
            self.setWindowTitle("安装运行依赖")
            self.resize(780, 480)
            self.setMinimumSize(680, 420)
            self.setStyleSheet(f"""
                QDialog {{ background: #f8fafc; color: #1f2937; font-family: '{FONT_FAMILY}'; }}
                QLabel#Title {{ font-size: 18px; font-weight: 700; }}
                QLabel#SubTitle {{ color: #64748b; }}
                QFrame#Card {{ background: #ffffff; border: 1px solid #e5e7eb; border-radius: 14px; }}
                QPlainTextEdit {{ background: #0f172a; color: #e5e7eb; border-radius: 10px; padding: 10px; }}
                QPushButton {{ min-height: 34px; border-radius: 8px; padding: 7px 14px; }}
                QPushButton#CloseBtn {{ background: #2563eb; color: #ffffff; border: 0; }}
                QPushButton#CloseBtn:disabled {{ background: #cbd5e1; color: #64748b; }}
                QProgressBar {{ border: 1px solid #cbd5e1; border-radius: 8px; text-align: center; min-height: 16px; }}
                QProgressBar::chunk {{ border-radius: 8px; background: #2563eb; }}
            """)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(12)

            title = QLabel("正在安装运行依赖")
            title.setObjectName("Title")
            subtitle = QLabel("安装过程会实时显示在下方。安装完成后请重新启动软件，让新依赖被 Python 正常加载。")
            subtitle.setObjectName("SubTitle")
            subtitle.setWordWrap(True)
            layout.addWidget(title)
            layout.addWidget(subtitle)

            card = QFrame()
            card.setObjectName("Card")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 14, 14, 14)
            card_layout.setSpacing(10)

            self.status_label = QLabel("准备执行：" + " ".join(self.command))
            self.status_label.setWordWrap(True)
            self.progress = QProgressBar()
            self.progress.setRange(0, 0)
            self.log_text = QPlainTextEdit()
            self.log_text.setReadOnly(True)
            self.log_text.setMinimumHeight(260)
            card_layout.addWidget(self.status_label)
            card_layout.addWidget(self.progress)
            card_layout.addWidget(self.log_text, 1)
            layout.addWidget(card, 1)

            row = QHBoxLayout()
            row.addStretch(1)
            self.close_btn = QPushButton("关闭")
            self.close_btn.setObjectName("CloseBtn")
            self.close_btn.setEnabled(False)
            self.close_btn.clicked.connect(self.accept)
            row.addWidget(self.close_btn)
            layout.addLayout(row)

        def start(self):
            self._append_log("$ " + " ".join(self.command) + "\n\n")
            threading.Thread(target=self._worker, daemon=True).start()

        def _worker(self):
            error = ""
            returncode = -1
            try:
                process = subprocess.Popen(
                    self.command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if process.stdout is not None:
                    for line in process.stdout:
                        self._signals.log.emit(line)
                returncode = process.wait()
            except Exception as exc:
                error = str(exc)
                self._signals.log.emit(f"\n安装进程启动失败：{exc}\n")
            self._signals.done.emit(returncode, error)

        def _append_log(self, message: str):
            self.log_text.moveCursor(QTextCursor.MoveOperation.End)
            self.log_text.insertPlainText(message)
            self.log_text.moveCursor(QTextCursor.MoveOperation.End)

        def _finish(self, returncode: int, error: str):
            self.returncode = returncode
            self.error = error
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            if returncode == 0:
                self.status_label.setText("依赖安装完成，请重新启动软件。")
                self._append_log("\n安装完成。\n")
            else:
                self.status_label.setText("依赖安装失败，请查看日志或复制命令手动执行。")
                self._append_log(f"\n安装失败，退出码：{returncode}\n")
            self.close_btn.setEnabled(True)


    class DependencyPromptDialog(QDialog):
        def __init__(self, missing, command_text: str, parent=None):
            super().__init__(parent)
            self.missing = missing
            self.command_text = command_text
            self.should_install = False
            self._build_ui()

        def showEvent(self, event):  # noqa: N802 - Qt API
            super().showEvent(event)
            _fade_in_dialog(self)

        def _build_ui(self):
            self.setWindowTitle("运行依赖检查")
            self.resize(680, 460)
            self.setMinimumSize(620, 400)
            self.setStyleSheet(f"""
                QDialog {{ background: #f8fafc; color: #1f2937; font-family: '{FONT_FAMILY}'; }}
                QLabel#Title {{ font-size: 19px; font-weight: 700; }}
                QLabel#Hint {{ color: #64748b; }}
                QFrame#DepCard {{ background: #ffffff; border: 1px solid #e5e7eb; border-radius: 14px; }}
                QLabel#RequiredBadge {{ background: #fee2e2; color: #991b1b; border-radius: 8px; padding: 2px 8px; }}
                QLabel#OptionalBadge {{ background: #e0f2fe; color: #075985; border-radius: 8px; padding: 2px 8px; }}
                QLabel#Command {{ background: #0f172a; color: #e5e7eb; border-radius: 10px; padding: 10px; }}
                QPushButton {{ min-height: 36px; border-radius: 9px; padding: 8px 16px; }}
                QPushButton#InstallBtn {{ background: #2563eb; color: #ffffff; border: 0; }}
            """)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(12)

            title = QLabel("检测到缺少运行依赖")
            title.setObjectName("Title")
            hint = QLabel("建议直接安装缺失依赖；如果处在离线环境，也可以复制下方命令到终端执行。")
            hint.setObjectName("Hint")
            hint.setWordWrap(True)
            layout.addWidget(title)
            layout.addWidget(hint)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            _enable_touch_scrolling(scroll)
            body = QWidget()
            body_layout = QVBoxLayout(body)
            body_layout.setContentsMargins(0, 0, 0, 0)
            body_layout.setSpacing(8)
            for dep in self.missing:
                card = QFrame()
                card.setObjectName("DepCard")
                row = QHBoxLayout(card)
                row.setContentsMargins(12, 10, 12, 10)
                row.setSpacing(10)
                name = QLabel(dep["package"])
                name.setStyleSheet("font-weight: 700; font-size: 14px;")
                desc = QLabel(dep["desc"])
                desc.setWordWrap(True)
                desc.setStyleSheet("color: #475569;")
                text_col = QVBoxLayout()
                text_col.addWidget(name)
                text_col.addWidget(desc)
                badge = QLabel("必需" if dep["required"] else "建议")
                badge.setObjectName("RequiredBadge" if dep["required"] else "OptionalBadge")
                row.addLayout(text_col, 1)
                row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
                body_layout.addWidget(card)
            body_layout.addStretch(1)
            scroll.setWidget(body)
            layout.addWidget(scroll, 1)

            cmd = QLabel(self.command_text)
            cmd.setObjectName("Command")
            cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cmd.setWordWrap(True)
            layout.addWidget(cmd)

            buttons = QDialogButtonBox()
            self.install_btn = buttons.addButton("立即安装", QDialogButtonBox.ButtonRole.AcceptRole)
            self.install_btn.setObjectName("InstallBtn")
            buttons.addButton("稍后手动安装", QDialogButtonBox.ButtonRole.RejectRole)
            buttons.accepted.connect(self._accept_install)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _accept_install(self):
            self.should_install = True
            self.accept()


def _ensure_qapplication():
    if not PYSIDE_PROMPT_AVAILABLE:
        return None
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        app.setApplicationName("ShangBackground")
    return app


def show_install_log_window(parent, packages):
    if PYSIDE_PROMPT_AVAILABLE:
        _ensure_qapplication()
        dialog = DependencyInstallDialog(packages, parent if hasattr(parent, "winId") else None)
        QTimer.singleShot(0, dialog.start)
        dialog.exec()
        return dialog.returncode == 0, " ".join(dialog.command)
    return _show_install_log_window_tk(parent, packages)


def _show_install_log_window_tk(parent, packages):
    import tkinter as tk
    from tkinter import ttk

    cmd = build_install_command(packages)
    output_queue = queue.Queue()
    result = {"returncode": None, "error": ""}

    win = tk.Toplevel(parent) if parent is not None else tk.Toplevel()
    win.title("安装运行依赖")
    win.geometry("720x420")
    win.minsize(620, 360)
    win.transient(parent)
    win.grab_set()

    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)

    status_var = tk.StringVar(value="正在安装依赖...")
    ttk.Label(container, textvariable=status_var).pack(anchor="w", pady=(0, 8))

    text_frame = ttk.Frame(container)
    text_frame.pack(fill="both", expand=True)

    log_text = tk.Text(text_frame, wrap="word", height=16, state="disabled")
    scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=log_text.yview)
    log_text.configure(yscrollcommand=scrollbar.set)
    log_text.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    close_btn = ttk.Button(container, text="关闭", command=win.destroy, state="disabled")
    close_btn.pack(anchor="e", pady=(10, 0))

    def append_log(message):
        log_text.configure(state="normal")
        log_text.insert("end", message)
        log_text.see("end")
        log_text.configure(state="disabled")

    append_log("$ " + " ".join(cmd) + "\n\n")

    def worker():
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            if process.stdout is not None:
                for line in process.stdout:
                    output_queue.put(("log", line))
            result["returncode"] = process.wait()
        except Exception as exc:
            result["returncode"] = -1
            result["error"] = str(exc)
            output_queue.put(("log", f"\n安装进程启动失败：{exc}\n"))
        finally:
            output_queue.put(("done", None))

    def poll_output():
        while True:
            try:
                kind, payload = output_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                append_log(payload)
            elif kind == "done":
                if result["returncode"] == 0:
                    status_var.set("依赖安装完成，请重新启动软件。")
                    append_log("\n安装完成。\n")
                else:
                    status_var.set("依赖安装失败，请查看日志。")
                    append_log(f"\n安装失败，退出码：{result['returncode']}\n")
                close_btn.configure(state="normal")
                return
        win.after(80, poll_output)

    threading.Thread(target=worker, daemon=True).start()
    win.after(80, poll_output)
    win.wait_window()
    return result["returncode"] == 0, " ".join(cmd)


def _prompt_with_pyside(missing, packages, parent=None):
    _ensure_qapplication()
    command_text = f"{sys.executable} -m pip install {' '.join(packages)}"
    prompt = DependencyPromptDialog(missing, command_text, parent if hasattr(parent, "winId") else None)
    prompt.exec()
    if prompt.should_install:
        success, cmd_text = show_install_log_window(parent, packages)
        if success:
            QMessageBox.information(parent, "安装完成", "依赖安装完成，请重新启动软件。")
            return False
        QMessageBox.critical(parent, "安装失败", f"依赖安装失败，请查看日志窗口，或在终端手动执行：\n\n{cmd_text}")
        return False
    QMessageBox.information(parent, "手动安装依赖", f"可以在终端执行：\n\n{command_text}")
    return None


def _prompt_with_tk(messagebox, missing, packages, parent=None):
    required_missing = [dep for dep in missing if dep["required"]]
    details = "\n".join(
        f"- {dep['package']}：{dep['desc']}" + ("（必需）" if dep["required"] else "（建议）")
        for dep in missing
    )
    cmd_text = f"{sys.executable} -m pip install {' '.join(packages)}"
    msg = (
        "检测到软件运行依赖未安装：\n\n"
        f"{details}\n\n"
        "是否现在自动安装？\n\n"
        f"将执行：\n{cmd_text}"
    )
    try:
        should_install = messagebox.askyesno("安装运行依赖", msg)
    except Exception:
        should_install = False

    if should_install:
        success, cmd_text = _show_install_log_window_tk(parent, packages)
        if success:
            messagebox.showinfo("安装完成", "依赖安装完成，请重新启动软件。")
            return False
        messagebox.showerror("安装失败", f"依赖安装失败，请查看日志窗口，或在终端手动执行：\n\n{cmd_text}")
    else:
        messagebox.showinfo("手动安装依赖", f"可以在终端执行：\n\n{cmd_text}")

    if required_missing:
        messagebox.showerror("缺少必需依赖", "缺少必需依赖，软件无法继续启动。")
        return False
    return True


def prompt_install_dependencies(messagebox, availability, parent=None, prefer_pyside=None):
    missing = get_missing_dependencies(availability)
    if not missing:
        return True

    packages = [dep["package"] for dep in missing]
    required_missing = [dep for dep in missing if dep["required"]]

    use_pyside = bool(prefer_pyside) if prefer_pyside is not None else (PYSIDE_PROMPT_AVAILABLE and QApplication.instance() is not None)

    if use_pyside and PYSIDE_PROMPT_AVAILABLE:
        result = _prompt_with_pyside(missing, packages, parent=parent)
        if result is False:
            return False
        if required_missing:
            QMessageBox.critical(parent, "缺少必需依赖", "缺少必需依赖，软件无法继续启动。")
            return False
        return True

    return _prompt_with_tk(messagebox, missing, packages, parent=parent)
