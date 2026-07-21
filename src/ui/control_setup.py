"""Small, consistent UI contracts for names, help text, and text inputs."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QLineEdit, QWidget


def describe_control(
    widget: QWidget,
    *,
    name: str,
    description: str = "",
    object_name: str = "",
    tooltip: str = "",
) -> QWidget:
    """Apply the user-facing and test-facing identity of one control."""
    if object_name:
        widget.setObjectName(object_name)
    widget.setAccessibleName(name)
    if description:
        widget.setAccessibleDescription(description)
    if tooltip and not widget.toolTip():
        widget.setToolTip(tooltip)
    return widget


def configure_text_input(
    edit: QLineEdit,
    *,
    name: str,
    description: str = "",
    object_name: str = "",
    placeholder: str = "",
    clear_button: bool = True,
    read_only: bool = False,
) -> QLineEdit:
    """Configure one text field without binding it to business logic."""
    edit.setReadOnly(bool(read_only))
    describe_control(
        edit,
        name=name,
        description=description,
        object_name=object_name,
    )
    if placeholder:
        edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(bool(clear_button and not edit.isReadOnly()))
    edit.setProperty("validationState", "")
    edit.setProperty("baseAccessibleDescription", description)
    return edit


def set_text_input_validation(edit: QLineEdit, message: str = "") -> None:
    """Expose source validation consistently to users, accessibility tools and tests."""
    message = str(message or "").strip()
    edit.setProperty("validationState", "invalid" if message else "valid")
    base = str(edit.property("baseAccessibleDescription") or "").strip()
    edit.setAccessibleDescription(" ".join(part for part in (base, message) if part))
    edit.setToolTip(message or base)
    try:
        edit.style().unpolish(edit)
        edit.style().polish(edit)
        edit.update()
    except Exception:
        pass


def make_buddy_label(text: str, control: QWidget, *, name: str = "") -> QLabel:
    """Create a keyboard-accessible label associated with one input control."""
    label = QLabel(text)
    label.setBuddy(control)
    if name:
        label.setAccessibleName(name)
    return label
