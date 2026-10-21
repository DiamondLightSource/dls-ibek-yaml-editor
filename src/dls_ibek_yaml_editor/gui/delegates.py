"""Cell editors: combos for enums, bools and object references."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLineEdit,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from .table_model import EntityTableModel

Index = QModelIndex | QPersistentModelIndex


class CellDelegate(QStyledItemDelegate):
    def createEditor(
        self, parent: QWidget, option: QStyleOptionViewItem, index: Index
    ) -> QWidget:
        model = cast(EntityTableModel, index.model())
        param = model.param(index.column())
        if param is not None and param.choices:
            combo = QComboBox(parent)
            combo.addItem("")
            combo.addItems(param.choices)
            combo.setToolTip("empty: use the default")
            return combo
        if param is not None and param.kind == "object":
            combo = QComboBox(parent)
            combo.setEditable(True)  # Jinja or a forward reference still allowed
            combo.addItem("")
            combo.addItems(model.model.document.ids(model.model.catalogue))
            return combo
        editor = QLineEdit(parent)
        if param is not None and param.default is not None:
            editor.setPlaceholderText(param.default_text())
        return editor

    def setEditorData(self, editor: QWidget, index: Index) -> None:
        text = str(index.data(Qt.ItemDataRole.EditRole) or "")
        if isinstance(editor, QComboBox):
            position = editor.findText(text)
            if position < 0 and editor.isEditable():
                editor.setEditText(text)
            else:
                editor.setCurrentIndex(max(position, 0))
            if not editor.isEditable():
                editor.showPopup()
        elif isinstance(editor, QLineEdit):
            editor.setText(text)

    def setModelData(
        self, editor: QWidget, model: QAbstractItemModel, index: Index
    ) -> None:
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)
        elif isinstance(editor, QLineEdit):
            model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)
