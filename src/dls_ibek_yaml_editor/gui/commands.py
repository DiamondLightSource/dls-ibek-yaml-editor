"""The undoable edits, and the Qt-facing owner of a Document."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack
from ruamel.yaml.comments import CommentedMap

from ..catalogue import Catalogue
from ..document import (
    COMMENTED,
    MISSING,
    Document,
    new_entity,
    text_to_value,
    value_to_text,
)


class DocumentModel(QObject):
    """A Document plus its catalogue and undo stack; all edits go through here."""

    valueChanged = Signal(object, str)  # entity, key
    structureChanged = Signal()  # entities added, removed or moved

    def __init__(self, document: Document, catalogue: Catalogue):
        super().__init__()
        self.document = document
        self.catalogue = catalogue
        self.stack = QUndoStack(self)
        # The tables, in order. As in xeb, each type's entities are kept
        # together and the tables' order is the file order.
        self.type_order: list[str] = document.types_in_order()
        self.structureChanged.connect(self._sync_types)

    def _sync_types(self) -> None:
        # an undo can bring back a type whose table was removed
        for name in self.document.types_in_order():
            if name not in self.type_order:
                self.type_order.append(name)

    def is_grouped(self) -> bool:
        return list(self.document.entities) == self.document.grouped(self.type_order)

    def insert_position(self, type_name: str) -> int:
        """Where a first entity of ``type_name`` goes: after the earlier tables."""
        before = set(self.type_order[: self.type_order.index(type_name)])
        types = [self.document.entity_type(e) for e in self.document.entities]
        return sum(1 for name in types if name in before)

    # ---------------------------------------------------------------- edits
    def set_text(self, entity: CommentedMap, key: str, text: str) -> bool:
        entity_type = self.catalogue.get(self.document.entity_type(entity))
        param = entity_type.params.get(key) if entity_type else None
        if key == COMMENTED:
            commented = bool(text.strip())
            if commented == self.document.is_commented(entity):
                return False
            self.stack.push(SetCommented(self, entity, commented))
            return True
        value = text_to_value(text, param)
        current = self.document.get(entity, key)
        if value_to_text(current) == value_to_text(value):
            return False  # unchanged: keep the original quoting
        self.stack.push(SetValue(self, entity, key, value))
        return True

    def insert(self, index: int, type_name: str) -> CommentedMap:
        entity = new_entity(type_name)
        self.stack.push(InsertEntity(self, index, entity))
        return entity

    def remove(self, entity: CommentedMap) -> None:
        self.stack.push(RemoveEntity(self, entity))

    def move(self, source: int, dest: int) -> None:
        if source != dest:
            self.stack.push(MoveEntity(self, source, dest))

    def set_description(self, text: str) -> None:
        self.stack.push(SetDescription(self, text))

    def add_table(self, type_name: str) -> None:
        """Show an (empty) table for a type, as xeb's Components menu does."""
        if type_name not in self.type_order:
            self.type_order.append(type_name)
            self.structureChanged.emit()

    def set_type_order(self, order: list[str], text: str = "Move Table") -> None:
        """Reorder the tables, regrouping the entities to match."""
        if order != self.type_order or not self.is_grouped():
            self.stack.push(Reorder(self, order, text))

    def remove_table(self, type_name: str) -> None:
        entities = self.document.of_type(type_name)
        self.stack.beginMacro(f"Remove Table {type_name}")
        for entity in entities:
            self.remove(entity)
        order = [name for name in self.type_order if name != type_name]
        self.stack.push(Reorder(self, order, "Remove Table"))
        self.stack.endMacro()


class SetValue(QUndoCommand):
    def __init__(
        self, model: DocumentModel, entity: CommentedMap, key: str, value: Any
    ):
        name = entity.get("name") or entity.get("type", "")
        verb = "Clear" if value is MISSING else "Set"
        super().__init__(f"{verb} {key} of {name}")
        self.model = model
        self.entity = entity
        self.key = key
        self.value = value
        self.old: Any = entity.get(key, MISSING)
        self.position = list(entity).index(key) if key in entity else None

    def redo(self) -> None:
        document = self.model.document
        entity_type = self.model.catalogue.get(document.entity_type(self.entity))
        document.set(self.entity, self.key, self.value, entity_type)
        self.model.valueChanged.emit(self.entity, self.key)

    def undo(self) -> None:
        if self.old is MISSING:
            self.entity.pop(self.key, None)
        elif self.key in self.entity:
            self.entity[self.key] = self.old
        else:
            position = self.position if self.position is not None else len(self.entity)
            self.entity.insert(position, self.key, self.old)
        self.model.valueChanged.emit(self.entity, self.key)


class SetCommented(QUndoCommand):
    def __init__(self, model: DocumentModel, entity: CommentedMap, commented: bool):
        name = entity.get("name") or entity.get("type", "")
        super().__init__(f"{'Comment out' if commented else 'Uncomment'} {name}")
        self.model = model
        self.entity = entity
        self.commented = commented

    def _apply(self, commented: bool) -> None:
        self.model.document.set_commented(self.entity, commented)
        self.model.valueChanged.emit(self.entity, COMMENTED)

    def redo(self) -> None:
        self._apply(self.commented)

    def undo(self) -> None:
        self._apply(not self.commented)


class InsertEntity(QUndoCommand):
    def __init__(self, model: DocumentModel, index: int, entity: CommentedMap):
        super().__init__(f"Insert {entity.get('type')}")
        self.model = model
        self.index = index
        self.entity = entity

    def redo(self) -> None:
        self.model.document.insert(self.index, self.entity)
        self.model.structureChanged.emit()

    def undo(self) -> None:
        document = self.model.document
        document.remove(document.index_of(self.entity))
        self.model.structureChanged.emit()


class RemoveEntity(QUndoCommand):
    def __init__(self, model: DocumentModel, entity: CommentedMap):
        super().__init__(f"Remove {entity.get('name') or entity.get('type')}")
        self.model = model
        self.entity = entity
        self.index = model.document.index_of(entity)

    def redo(self) -> None:
        self.model.document.remove(self.index)
        self.model.structureChanged.emit()

    def undo(self) -> None:
        self.model.document.insert(self.index, self.entity)
        self.model.structureChanged.emit()


class MoveEntity(QUndoCommand):
    def __init__(self, model: DocumentModel, source: int, dest: int):
        entity = model.document.entities[source]
        super().__init__(f"Move {entity.get('name') or entity.get('type')}")
        self.model = model
        self.source = source
        self.dest = dest

    def redo(self) -> None:
        self.model.document.move(self.source, self.dest)
        self.model.structureChanged.emit()

    def undo(self) -> None:
        self.model.document.move(self.dest, self.source)
        self.model.structureChanged.emit()


class SetDescription(QUndoCommand):
    def __init__(self, model: DocumentModel, text: str):
        super().__init__("Set description")
        self.model = model
        self.new_text = text
        self.old = model.document.data.get("description", "")

    def redo(self) -> None:
        self.model.document.data["description"] = self.new_text

    def undo(self) -> None:
        self.model.document.data["description"] = self.old


class Reorder(QUndoCommand):
    """Set the table order and regroup the entities by it."""

    def __init__(self, model: DocumentModel, order: list[str], text: str):
        super().__init__(text)
        self.model = model
        self.order = list(order)
        self.old_order = list(model.type_order)
        self.old_entities = list(model.document.entities)

    def redo(self) -> None:
        self.model.type_order[:] = self.order
        document = self.model.document
        document.reorder(document.grouped(self.order))
        self.model.structureChanged.emit()

    def undo(self) -> None:
        self.model.type_order[:] = self.old_order
        self.model.document.reorder(self.old_entities)
        self.model.structureChanged.emit()
