"""Settings-page registration, tokenized search, activation, and focus behavior."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

_QUERY_SPLIT_RE = re.compile(r"[\s,，。.!！?？;；:：、/\\|·—_\-]+")


@dataclass(slots=True)
class _WidgetRecord:
    widget: QWidget
    search_text: str


@dataclass(slots=True)
class _PageRecord:
    title: str
    search_text: str
    item: QListWidgetItem
    scroll: QScrollArea
    stack_index: int
    widgets: list[_WidgetRecord] = field(default_factory=list)


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("&", "").casefold().split())


def _query_tokens(query: str) -> tuple[str, ...]:
    tokens = tuple(token for token in (_normalize_text(part) for part in _QUERY_SPLIT_RE.split(str(query or ""))) if token)
    return tuple(dict.fromkeys(tokens))


def _single_widget_search_text(child: QWidget) -> str:
    parts: list[str] = []
    for value in (
        child.objectName(),
        child.accessibleName(),
        child.accessibleDescription(),
        child.toolTip(),
        child.statusTip(),
        child.whatsThis(),
    ):
        if value:
            parts.append(str(value))
    if isinstance(child, (QLabel, QAbstractButton)):
        text = child.text()
        if text:
            parts.append(text)
    elif isinstance(child, QGroupBox):
        if child.title():
            parts.append(child.title())
    if isinstance(child, QLineEdit) and child.placeholderText():
        parts.append(child.placeholderText())
    return _normalize_text(" ".join(parts))


def _widget_records(widget: QWidget) -> list[_WidgetRecord]:
    records: list[_WidgetRecord] = []
    for child in [widget, *widget.findChildren(QWidget)]:
        text = _single_widget_search_text(child)
        if text:
            records.append(_WidgetRecord(child, text))
    return records


class SettingsNavigator:
    """Own navigation/page synchronization and searchable control discovery."""

    def __init__(
        self,
        nav: QListWidget,
        stack: QStackedWidget,
        *,
        empty_text: str,
        on_page_activated=None,
    ) -> None:
        self.nav = nav
        self.stack = stack
        self._records: list[_PageRecord] = []
        self._on_page_activated = on_page_activated
        self._search_edit: QLineEdit | None = None
        self._result_label: QLabel | None = None
        self._matches_by_row: dict[int, list[QWidget]] = {}
        self._highlighted: QWidget | None = None
        self._first_match: tuple[int, QWidget] | None = None

        self.empty_page = QWidget()
        self.empty_page.setObjectName("SettingsSearchEmptyPage")
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(30, 30, 30, 30)
        empty_label = QLabel(empty_text)
        empty_label.setObjectName("SettingsSearchEmptyLabel")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setWordWrap(True)
        empty_label.setProperty("muted", True)
        empty_layout.addStretch(1)
        empty_layout.addWidget(empty_label)
        empty_layout.addStretch(1)
        self._empty_index = self.stack.addWidget(self.empty_page)

        self.nav.currentRowChanged.connect(self._activate_row)

    @property
    def page_count(self) -> int:
        return len(self._records)

    def bind_search(self, edit: QLineEdit, result_label: QLabel | None = None) -> None:
        self._search_edit = edit
        self._result_label = result_label
        edit.textChanged.connect(self.filter_pages)
        edit.returnPressed.connect(self.focus_first_match)
        self.filter_pages(edit.text())

    def add_page(self, title: str, widget: QWidget, *, keywords=()) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("SettingsPageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setAutoFillBackground(False)
        widget.setObjectName("SettingsPageSurface")
        widget.setAutoFillBackground(False)
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll.setWidget(widget)

        item = QListWidgetItem(title)
        item.setSizeHint(QSize(170, 48))
        item.setToolTip(title)
        self.nav.addItem(item)
        stack_index = self.stack.addWidget(scroll)
        widget_records = _widget_records(widget)
        searchable = _normalize_text(
            " ".join((title, *[str(value) for value in keywords], *[entry.search_text for entry in widget_records]))
        )
        self._records.append(_PageRecord(title, searchable, item, scroll, stack_index, widget_records))
        return scroll

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _set_highlight(self, widget: QWidget | None) -> None:
        if self._highlighted is widget:
            return
        if self._highlighted is not None:
            try:
                self._highlighted.setProperty("settingsSearchMatch", False)
                self._repolish(self._highlighted)
            except RuntimeError:
                pass
        self._highlighted = widget
        if widget is not None:
            widget.setProperty("settingsSearchMatch", True)
            self._repolish(widget)

    def _show_match_for_row(self, row: int, *, focus: bool = False) -> None:
        if not 0 <= row < len(self._records):
            self._set_highlight(None)
            return
        matches = self._matches_by_row.get(row, [])
        target = matches[0] if matches else None
        self._set_highlight(target)
        if target is None:
            return
        record = self._records[row]
        record.scroll.ensureWidgetVisible(target, 24, 24)
        if focus and target.focusPolicy() != Qt.FocusPolicy.NoFocus:
            target.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _activate_row(self, row: int) -> None:
        if not 0 <= row < len(self._records):
            return
        record = self._records[row]
        if record.item.isHidden():
            return
        self.stack.setCurrentIndex(record.stack_index)
        QTimer.singleShot(0, lambda row=row: self._show_match_for_row(row))
        if callable(self._on_page_activated):
            QTimer.singleShot(0, lambda record=record: self._on_page_activated(record.scroll))

    def filter_pages(self, query: str) -> int:
        tokens = _query_tokens(query)
        visible_rows: list[int] = []
        self._matches_by_row.clear()
        self._first_match = None

        for row, record in enumerate(self._records):
            visible = not tokens or all(token in record.search_text for token in tokens)
            record.item.setHidden(not visible)
            if not visible:
                continue
            visible_rows.append(row)
            if tokens:
                ranked: list[tuple[int, int, QWidget]] = []
                for position, entry in enumerate(record.widgets):
                    score = sum(token in entry.search_text for token in tokens)
                    if score:
                        ranked.append((-score, position, entry.widget))
                ranked.sort(key=lambda value: (value[0], value[1]))
                matches = [widget for _score, _position, widget in ranked]
                self._matches_by_row[row] = matches
                if self._first_match is None and matches:
                    self._first_match = (row, matches[0])

        count = len(visible_rows)
        if self._result_label is not None:
            self._result_label.setText(str(count))
            self._result_label.setAccessibleDescription(str(count))
        if count == 0:
            self._set_highlight(None)
            self.stack.setCurrentIndex(self._empty_index)
            self.nav.setCurrentRow(-1)
            return 0

        current = self.nav.currentRow()
        if current not in visible_rows:
            self.nav.setCurrentRow(visible_rows[0])
        else:
            self._activate_row(current)
        return count

    def focus_first_match(self) -> None:
        match = self._first_match
        if match is None:
            return
        row, target = match
        self.nav.setCurrentRow(row)
        self.stack.setCurrentIndex(self._records[row].stack_index)
        QTimer.singleShot(0, lambda row=row, target=target: self._focus_target(row, target))

    def _focus_target(self, row: int, target: QWidget) -> None:
        self._set_highlight(target)
        self._records[row].scroll.ensureWidgetVisible(target, 24, 24)
        if target.focusPolicy() != Qt.FocusPolicy.NoFocus:
            target.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def reset(self) -> None:
        if self._search_edit is not None:
            self._search_edit.clear()
        self._set_highlight(None)
        self.filter_pages("")
        for record in self._records:
            record.scroll.verticalScrollBar().setValue(0)
            record.scroll.horizontalScrollBar().setValue(0)
        if self._records:
            self.nav.setCurrentRow(0)
            self._activate_row(0)
