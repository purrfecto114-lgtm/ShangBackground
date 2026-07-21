"""Bindings that make editable source fields honest, validated and persistent."""
from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import QLineEdit, QWidget

from app.source_validation import (
    SourceValidation,
    validate_directory_target,
    validate_existing_directory,
    validate_existing_file,
    validate_html_source,
)
from ui.control_setup import set_text_input_validation


Validator = Callable[..., SourceValidation]
ChangedCallback = Callable[[str, str], None]


@dataclass
class SourceBinding:
    """Bind one line edit to one validated configuration value."""

    edit: QLineEdit
    config: MutableMapping[str, Any]
    key: str
    label: str
    validator: Validator
    persist: Callable[[], object]
    set_status: Callable[[str], None]
    show_warning: Callable[[QWidget, str, str], None]
    parent: QWidget
    translate: Callable[[str], str]
    saved_text: str = ""
    cleared_text: str = ""
    on_changed: ChangedCallback | None = None

    def commit(self, *, required: bool = False, show_dialog: bool = False) -> str:
        result = self.validator(self.edit.text(), optional=not required)
        if not result.valid:
            self._show_error(result, show_dialog=show_dialog)
            return ""
        set_text_input_validation(self.edit)
        if self.edit.text().strip() != result.value:
            self.edit.setText(result.value)
        previous = str(self.config.get(self.key, "") or "")
        if previous == result.value:
            return result.value
        self.config[self.key] = result.value
        if self.on_changed is not None:
            self.on_changed(previous, result.value)
        self.persist()
        message = self.saved_text if result.value else self.cleared_text
        if message:
            self.set_status(message)
        return result.value

    def _show_error(self, result: SourceValidation, *, show_dialog: bool) -> None:
        message = self._error_message(result)
        set_text_input_validation(self.edit, message)
        self.set_status(message)
        if show_dialog:
            self.show_warning(self.parent, self.label, message)
        try:
            self.edit.setFocus()
            self.edit.selectAll()
        except Exception:
            pass

    def _error_message(self, result: SourceValidation) -> str:
        t = self.translate
        messages = {
            "empty": t("请先填写或选择{label}。"),
            "not_found": t("找不到{label}：{value}"),
            "not_directory": t("{label}必须是文件夹：{value}"),
            "not_file": t("{label}必须是文件：{value}"),
            "unsupported_type": t("{label}的文件类型不受支持：{value}"),
            "invalid_url": t("{label}的网址格式无效：{value}"),
            "parent_not_found": t("{label}的上级目录不存在：{value}"),
            "parent_not_directory": t("{label}的上级路径不是文件夹：{value}"),
        }
        template = messages.get(result.error, t("{label}无效：{value}"))
        return template.format(label=self.label, value=result.value)


class SourceInputController:
    """Create source bindings while keeping config and dialogs outside widgets."""

    def __init__(
        self,
        *,
        parent: QWidget,
        config: MutableMapping[str, Any],
        persist: Callable[[], object],
        set_status: Callable[[str], None],
        show_warning: Callable[[QWidget, str, str], None],
        translate: Callable[[str], str],
    ) -> None:
        self._parent = parent
        self._config = config
        self._persist = persist
        self._set_status = set_status
        self._show_warning = show_warning
        self._translate = translate

    def bind(
        self,
        edit: QLineEdit,
        *,
        key: str,
        label: str,
        validator: Validator,
        saved_text: str = "",
        cleared_text: str = "",
        on_changed: ChangedCallback | None = None,
    ) -> SourceBinding:
        binding = SourceBinding(
            edit=edit,
            config=self._config,
            key=key,
            label=label,
            validator=validator,
            persist=self._persist,
            set_status=self._set_status,
            show_warning=self._show_warning,
            parent=self._parent,
            translate=self._translate,
            saved_text=saved_text,
            cleared_text=cleared_text,
            on_changed=on_changed,
        )
        edit.editingFinished.connect(binding.commit)
        return binding

    def bind_existing_directory(self, edit: QLineEdit, **kwargs) -> SourceBinding:
        return self.bind(edit, validator=validate_existing_directory, **kwargs)

    def bind_existing_file(self, edit: QLineEdit, **kwargs) -> SourceBinding:
        return self.bind(edit, validator=validate_existing_file, **kwargs)

    def bind_html_source(self, edit: QLineEdit, **kwargs) -> SourceBinding:
        return self.bind(edit, validator=validate_html_source, **kwargs)

    def bind_directory_target(self, edit: QLineEdit, **kwargs) -> SourceBinding:
        return self.bind(edit, validator=validate_directory_target, **kwargs)
