"""One entity type as a table: rows are its entities, columns its parameters."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtGui import QBrush, QColor, QFont
from ruamel.yaml.comments import CommentedMap

from ..catalogue import EntityType, Param
from ..document import MISSING, value_to_text
from .commands import DocumentModel

Index = QModelIndex | QPersistentModelIndex

REQUIRED = QColor(255, 200, 200)
PROBLEM = QColor(255, 170, 120)
DEFAULT = QColor(140, 140, 140)
ROOT = QModelIndex()


class EntityTableModel(QAbstractTableModel):
    def __init__(self, model: DocumentModel, type_name: str):
        super().__init__()
        self.model = model
        self.type_name = type_name
        self.entity_type: EntityType | None = model.catalogue.get(type_name)
        self.rows: list[CommentedMap] = []
        self.columns: list[str] = []
        # (id(entity), field or "") -> messages, set by the validation dock
        self.problems: dict[tuple[int, str], list[str]] = {}
        self._refresh()
        model.structureChanged.connect(self._on_structure)
        model.valueChanged.connect(self._on_value)

    @property
    def editable(self) -> bool:
        return self.entity_type is not None

    def _refresh(self) -> None:
        self.rows = self.model.document.of_type(self.type_name)
        if self.entity_type is not None:
            self.columns = list(self.entity_type.params)
        else:
            keys: dict[str, None] = {}
            for entity in self.rows:
                for key in entity:
                    if key != "type":
                        keys.setdefault(str(key), None)
            self.columns = list(keys)

    def _on_structure(self) -> None:
        self.beginResetModel()
        self._refresh()
        self.endResetModel()

    def _on_value(self, entity: object, key: str) -> None:
        for row, candidate in enumerate(self.rows):
            if candidate is entity:
                if key in self.columns:
                    index = self.index(row, self.columns.index(key))
                    self.dataChanged.emit(index, index)
                else:
                    self._on_structure()
                return

    # --------------------------------------------------------------- access
    def row_of(self, entity: CommentedMap) -> int | None:
        """The row showing ``entity`` (by identity: new entities compare equal)."""
        for row, candidate in enumerate(self.rows):
            if candidate is entity:
                return row
        return None

    def entity(self, row: int) -> CommentedMap:
        return self.rows[row]

    def param(self, column: int) -> Param | None:
        if self.entity_type is None:
            return None
        return self.entity_type.params.get(self.columns[column])

    # ------------------------------------------------------------ Qt model
    def rowCount(self, parent: Index = ROOT) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: Index = ROOT) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        entity = self.rows[index.row()]
        key = self.columns[index.column()]
        value = entity.get(key, MISSING)
        param = self.param(index.column())
        problems = self.problems.get((id(entity), key), [])
        if role == Qt.ItemDataRole.DisplayRole:
            if value is MISSING:
                return param.default_text() if param else ""
            return value_to_text(value)
        if role == Qt.ItemDataRole.EditRole:
            return value_to_text(value)
        if role == Qt.ItemDataRole.ForegroundRole:
            if value is MISSING:
                return QBrush(DEFAULT)
        if role == Qt.ItemDataRole.FontRole and value is MISSING:
            font = QFont()
            font.setItalic(True)
            return font
        if role == Qt.ItemDataRole.BackgroundRole:
            if problems:
                return QBrush(PROBLEM)
            if value is MISSING and param is not None and param.required:
                return QBrush(REQUIRED)
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [*problems]
            if param is not None:
                lines.append(param.description)
                if value is MISSING and param.default is not None:
                    lines.append(f"(default: {param.default_text()!r})")
                elif value is MISSING and param.required:
                    lines.append("(required)")
            return "\n".join(lines) or None
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal:
            if section >= len(self.columns):
                return None
            if role == Qt.ItemDataRole.DisplayRole:
                return self.columns[section]
            param = self.param(section)
            if role == Qt.ItemDataRole.ToolTipRole and param is not None:
                text = f"{param.name} ({param.kind})\n{param.description}"
                if param.required:
                    text += "\nrequired"
                else:
                    text += f"\ndefault: {param.default_text()!r}"
                if param.choices:
                    text += "\nchoices: " + ", ".join(param.choices)
                return text
            if role == Qt.ItemDataRole.FontRole and param is not None:
                if param.required:
                    font = QFont()
                    font.setBold(True)
                    return font
            return None
        if section >= len(self.rows):
            return None
        messages = self.problems.get((id(self.rows[section]), ""), [])
        if role == Qt.ItemDataRole.ToolTipRole and messages:
            return "\n".join(messages)
        if role == Qt.ItemDataRole.BackgroundRole and messages:
            return QBrush(PROBLEM)
        return super().headerData(section, orientation, role)

    def flags(self, index: Index) -> Qt.ItemFlag:
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(
        self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or not self.editable:
            return False
        entity = self.rows[index.row()]
        key = self.columns[index.column()]
        text = "" if value is None else str(value)
        self.model.set_text(entity, key, text)
        return True

    # -------------------------------------------------------- row editing
    def insert_rows(self, row: int, count: int) -> list[CommentedMap]:
        """Insert ``count`` new entities before table row ``row``.

        In the file they go just before that row's entity, or after the last
        entity of this type when appending. A first entity goes after the
        entities of the tables listed before this one.
        """
        document = self.model.document
        if row < len(self.rows):
            position = document.index_of(self.rows[row])
        elif self.rows:
            position = document.index_of(self.rows[-1]) + 1
        else:
            position = self.model.insert_position(self.type_name)
        created: list[CommentedMap] = []
        self.model.stack.beginMacro(f"Insert {count} {self.type_name}")
        for offset in range(count):
            created.append(self.model.insert(position + offset, self.type_name))
        self.model.stack.endMacro()
        return created

    def move_row(self, source: int, dest: int) -> None:
        """Move row ``source`` to row ``dest``, i.e. within this type's group."""
        if source == dest or not (0 <= source < len(self.rows)):
            return
        if not (0 <= dest < len(self.rows)):
            return
        document = self.model.document
        self.model.move(
            document.index_of(self.rows[source]), document.index_of(self.rows[dest])
        )

    def remove_rows(self, rows: list[int]) -> None:
        entities = [self.rows[row] for row in sorted(set(rows))]
        self.model.stack.beginMacro(f"Remove {len(entities)} {self.type_name}")
        for entity in entities:
            self.model.remove(entity)
        self.model.stack.endMacro()

    def clear_indexes(self, indexes: list[QModelIndex]) -> None:
        self.model.stack.beginMacro("Clear cells")
        for index in indexes:
            self.setData(index, "")
        self.model.stack.endMacro()
