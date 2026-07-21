"""Unified dialog / message-box style helpers.

This module is the single source of truth for prompt-box styling across
ShangBackground on Windows, Linux and macOS.  Keeping the helpers in one
place prevents the historical "four different stylesheet-propagation
strategies" drift documented in the code audit:

  * some dialogs called ``setStyleSheet(self._theme_stylesheet)`` explicitly
  * some relied on the application-level QSS cascade
  * some passed ``parent=None`` and got the native look
  * some had no parent *and* no application context

Every helper here goes through the application-level cascade by default,
which is enough because ``ShangBackgroundWindow._rebuild_stylesheet()``
already installs ``QMessageBox / QFileDialog / QColorDialog / QDialogButtonBox``
rules globally.  Callers therefore do *not* need to copy the theme
stylesheet onto individual dialog instances any more.

The module also standardises the visual hierarchy of dialog titles and
body notes.  Previously the codebase used five different title sizes
(``14pt``, ``18px``, ``19px``, ``22px``, ``24px``) and two different
weight tokens (``bold`` vs ``700``).  They are now funnelled through
:func:`dialog_title_style` and :func:`dialog_note_style`.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

try:  # ``t`` lives in app.i18n and is intentionally optional at import time.
    from app.i18n import t as _t
except Exception:  # pragma: no cover - defensive import for early-boot paths
    def _t(key: str, default: Optional[str] = None) -> str:
        return default if default is not None else key


# ---------------------------------------------------------------------------
# Unified style constants
# ---------------------------------------------------------------------------

#: Canonical title style for every dialog/page title.
#: Replaces the historical mix of ``14pt``/``18px``/``19px``/``22px``/``24px``
#: and ``bold``/``700``.  ``px`` is used (not ``pt``) so the size does not
#: scale twice when the system DPI is already applied via ``dpi_scale``.
DIALOG_TITLE_STYLE: str = "font-size: 18px; font-weight: 700;"

#: Canonical style for secondary description text inside dialogs.
DIALOG_NOTE_STYLE: str = "font-size: 13px;"

#: Canonical style for large "hero" titles (about dialog, settings page hero).
#: Distinct from :data:`DIALOG_TITLE_STYLE` so hero blocks keep visual weight
#: without re-introducing the ``pt``-vs-``px`` drift.
DIALOG_HERO_TITLE_STYLE: str = "font-size: 22px; font-weight: 700;"


def dialog_title_style(extra: str = "") -> str:
    """Return the canonical dialog-title QSS, optionally prefixed with extras."""
    extras = (extra or "").strip()
    if extras and not extras.endswith(";"):
        extras += ";"
    return f"{extras}{DIALOG_TITLE_STYLE}" if extras else DIALOG_TITLE_STYLE


def dialog_note_style(extra: str = "") -> str:
    """Return the canonical dialog-note QSS, optionally prefixed with extras."""
    extras = (extra or "").strip()
    if extras and not extras.endswith(";"):
        extras += ";"
    return f"{extras}{DIALOG_NOTE_STYLE}" if extras else DIALOG_NOTE_STYLE


def apply_dialog_title(widget, extra: str = "") -> None:
    """Apply the canonical dialog title style to ``widget``.

    Silently ignores ``None`` or already-destroyed Qt objects so callers can
    use it unconditionally inside ``try/except`` blocks.
    """
    try:
        if widget is None:
            return
        widget.setStyleSheet(dialog_title_style(extra))
    except Exception:
        pass


def apply_dialog_note(widget, extra: str = "") -> None:
    """Apply the canonical dialog note style to ``widget``."""
    try:
        if widget is None:
            return
        widget.setStyleSheet(dialog_note_style(extra))
    except Exception:
        pass


def apply_dialog_hero_title(widget, extra: str = "") -> None:
    """Apply the canonical dialog hero-title style to ``widget``."""
    try:
        if widget is None:
            return
        extras = (extra or "").strip()
        if extras and not extras.endswith(";"):
            extras += ";"
        widget.setStyleSheet(f"{extras}{DIALOG_HERO_TITLE_STYLE}" if extras else DIALOG_HERO_TITLE_STYLE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

def _icon_for_level(level: str) -> QMessageBox.Icon:
    level_name = (level or "info").lower()
    if level_name in {"warning", "warn"}:
        return QMessageBox.Icon.Warning
    if level_name in {"error", "critical"}:
        return QMessageBox.Icon.Critical
    if level_name in {"question", "yesno"}:
        return QMessageBox.Icon.Question
    return QMessageBox.Icon.Information


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def show_message(
    parent: Optional[QWidget],
    title: str,
    text: str,
    level: str = "info",
) -> None:
    """Show a modal message box with the canonical severity icon.

    Consolidates the ``QMessageBox.information/warning/critical`` static
    helpers and the historical ``QtRootShim.show_message`` path.  The
    dialog inherits the application-level QSS automatically; callers do
    *not* need to copy ``_theme_stylesheet`` onto it.

    Parameters
    ----------
    parent:
        Parent widget.  May be ``None`` for very-early-boot callers, but
        passing the main window gives proper modality and centering.
    title:
        Window title.  Falls back to the application display name when empty.
    text:
        Body text.
    level:
        One of ``"info"``, ``"warning"``/``"warn"``, ``"error"``/``"critical"``,
        ``"question"``.  Defaults to ``"info"``.
    """
    box = QMessageBox(parent)
    app = QApplication.instance()
    if not title:
        title = app.applicationName() if app is not None else "ShangBackground"
    box.setWindowTitle(str(title))
    box.setText(str(text or ""))
    box.setIcon(_icon_for_level(level))
    box.exec()


def show_info(parent: Optional[QWidget], title: str, text: str) -> None:
    """Shortcut for :func:`show_message` with ``level="info"``."""
    show_message(parent, title, text, level="info")


def show_warning(parent: Optional[QWidget], title: str, text: str) -> None:
    """Shortcut for :func:`show_message` with ``level="warning"``."""
    show_message(parent, title, text, level="warning")


def show_error(parent: Optional[QWidget], title: str, text: str) -> None:
    """Shortcut for :func:`show_message` with ``level="error"``."""
    show_message(parent, title, text, level="error")


def ask_yes_no(
    parent: Optional[QWidget],
    title: str,
    text: str,
    *,
    default_yes: bool = True,
    yes_label: Optional[str] = None,
    no_label: Optional[str] = None,
) -> bool:
    """Show a Yes/No question dialog and return ``True`` when Yes is clicked.

    The Yes/No labels default to the localised ``"是"`` / ``"否"``.  Callers
    can override them for context-specific wording (e.g. ``"继续"``/``"取消"``).
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(str(title))
    box.setText(str(text))
    yes_text = yes_label if yes_label is not None else _t("是")
    no_text = no_label if no_label is not None else _t("否")
    yes_btn = box.addButton(yes_text, QMessageBox.ButtonRole.YesRole)
    no_btn = box.addButton(no_text, QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(yes_btn if default_yes else no_btn)
    box.exec()
    return box.clickedButton() is yes_btn


def show_non_modal_warning(
    parent: Optional[QWidget],
    title: str,
    message: str,
    *,
    tracker: Optional[list] = None,
) -> Optional[QMessageBox]:
    """Show a *non-modal* warning box so background errors do not block the UI.

    Mirrors the historical ``ShangBackgroundWindow._show_non_modal_warning``
    helper, but is safe to call from any parent (including ``None``).  The
    created ``QMessageBox`` is returned so callers can attach extra signals
    or close it programmatically.

    Parameters
    ----------
    parent:
        Parent widget, usually the main window.
    title:
        Window title.
    message:
        Body text.
    tracker:
        Optional list the dialog is appended to (and removed from on
        destroy).  Mirrors the historical ``self._non_modal_dialogs`` list
        on ``ShangBackgroundWindow``; pass ``None`` to skip tracking.
    """
    try:
        dlg = QMessageBox(parent)
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setWindowTitle(str(title))
        dlg.setText(str(message))
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.setModal(False)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        if tracker is not None:
            tracker.append(dlg)

            def _forget(*_args, _dlg=dlg, _tracker=tracker):
                try:
                    _tracker.remove(_dlg)
                except Exception:
                    pass
            dlg.destroyed.connect(_forget)
        dlg.show()
        return dlg
    except Exception:
        # Last-resort fallback: a blocking warning so the user still sees
        # the message even if non-modal display failed for any reason.
        QMessageBox.warning(parent, title, str(message))
        return None


__all__ = [
    "DIALOG_TITLE_STYLE",
    "DIALOG_NOTE_STYLE",
    "DIALOG_HERO_TITLE_STYLE",
    "dialog_title_style",
    "dialog_note_style",
    "apply_dialog_title",
    "apply_dialog_note",
    "apply_dialog_hero_title",
    "show_message",
    "show_info",
    "show_warning",
    "show_error",
    "ask_yes_no",
    "show_non_modal_warning",
]
