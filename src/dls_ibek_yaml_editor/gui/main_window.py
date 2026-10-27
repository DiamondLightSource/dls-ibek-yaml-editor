"""The xeb-style main window."""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PySide6.QtCore import QEventLoop, QModelIndex, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QUndoView,
    QVBoxLayout,
    QWidget,
)
from ruamel.yaml.comments import CommentedMap

from ..catalogue import Catalogue
from ..document import COMMENTED, Document
from ..service import Instance, find_instance, load_catalogue, open_document
from ..validate import Problem, validate
from .commands import DocumentModel
from .table_model import EntityTableModel
from .table_view import TableView

TITLE = "ibek yaml editor"


@dataclass
class Sources:
    """Command line overrides for where entities come from."""

    image: str | None = None
    generic_ioc: Path | None = None
    defs: Path | None = None
    refresh: bool = False

    def given(self) -> bool:
        return any((self.image, self.generic_ioc, self.defs))


class TextDialog(QWidget):
    """A read-only text window, for errors with tracebacks."""

    def __init__(self, title: str, text: str):
        super().__init__()
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        edit = QPlainTextEdit(text)
        edit.setReadOnly(True)
        layout.addWidget(edit)
        self.resize(QSize(800, 400))


class MainWindow(QMainWindow):
    def __init__(self, sources: Sources | None = None):
        super().__init__()
        self.sources = sources or Sources()
        self.instance = Instance(None)
        self.model: DocumentModel | None = None
        self.tables: dict[str, EntityTableModel] = {}
        self.type_name: str | None = None
        self.problems: list[Problem] = []
        # the entity each problem was found on: positions change with edits
        self._problem_entities: list[CommentedMap | None] = []
        self._dialogs: list[QWidget] = []

        self.table_view = TableView(self)
        self.setCentralWidget(self.table_view)

        # the tables, in file order; drag to reorder, as in xeb
        self.type_list = QListWidget(self)
        self.type_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.type_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.type_list.currentTextChanged.connect(self._on_type_selected)
        self.type_list.model().rowsMoved.connect(self._on_types_dropped)
        self.dock_types = self._dock("Entity types", self.type_list)

        self.undo_view = QUndoView(self)
        self.dock_undo = self._dock("Undo stack", self.undo_view)

        self.problem_list = QListWidget(self)
        self.problem_list.itemActivated.connect(self._on_problem_clicked)
        self.problem_list.itemClicked.connect(self._on_problem_clicked)
        self.dock_problems = QDockWidget("Problems", self)
        self.dock_problems.setWidget(self.problem_list)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_problems)
        self.dock_problems.hide()

        self._make_menus()
        self.resize(QSize(1300, 650))
        self._update_title()

    def _dock(self, title: str, widget: QWidget) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setWidget(widget)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        return dock

    # ------------------------------------------------------------- menus
    def _action(
        self, menu: QMenu, text: str, slot: object, *keys: str | QKeySequence
    ) -> QAction:
        action = menu.addAction(text)
        action.triggered.connect(slot)  # type: ignore[arg-type]
        if keys:
            action.setShortcuts([QKeySequence(k) for k in keys])
        return action

    def _make_menus(self) -> None:
        bar = self.menuBar()
        file = bar.addMenu("&File")
        self._action(file, "New...", self.new, "Ctrl+N")
        self._action(file, "Open...", self.open_dialog, "Ctrl+O")
        self._action(file, "Reload", self.reload)
        self._action(file, "Save", self.save, "Ctrl+S")
        self._action(file, "Save As...", self.save_as)
        file.addSeparator()
        self._action(file, "Description...", self.edit_description)
        file.addSeparator()
        self._action(file, "Quit", self.close, "Ctrl+Q")

        edit = self.menu_edit = bar.addMenu("&Edit")
        view = self.table_view
        self._action(edit, "Insert Row", view.insert_row, "Ctrl+I")
        self._action(edit, "Insert Row Under", lambda: view.insert_row(True), "Ctrl+U")
        self._action(edit, "Remove Row", view.remove_rows)
        edit.addSeparator()
        self._action(edit, "Cut", view.cut, "Ctrl+X")
        self._action(edit, "Copy", view.copy, "Ctrl+C", "Ctrl+Ins")
        self._action(edit, "Paste", view.paste, "Ctrl+V", "Shift+Ins")
        self._action(edit, "Clear", view.clear, "Ctrl+D")
        edit.addSeparator()
        self._action(edit, "Fill Cells", view.fill, "Ctrl+L")
        self._action(
            edit, "Fill Cells and Increment", lambda: view.fill(True), "Ctrl+R"
        )
        edit.addSeparator()
        self._action(edit, "Undo", self._undo, "Ctrl+Z")
        self._action(edit, "Redo", self._redo, "Ctrl+Shift+Z", "Ctrl+Y")

        self.menu_components = bar.addMenu("&Components")
        self.menu_components.setToolTipsVisible(True)

        tools = bar.addMenu("&Tools")
        self._action(tools, "Validate", self.validate, "F5")

        windows = bar.addMenu("&View")
        for dock in (
            self.dock_types,
            self.dock_undo,
            self.dock_problems,
        ):
            windows.addAction(dock.toggleViewAction())

    def _populate_components(self) -> None:
        menu = self.menu_components
        menu.clear()
        self._action(menu, "Remove Table", self.remove_table)
        self._action(menu, "Move Table Up", lambda: self.move_table(-1))
        self._action(menu, "Move Table Down", lambda: self.move_table(1))
        menu.addSeparator()
        if self.model is None:
            return
        for module, types in self.model.catalogue.modules.items():
            sub = menu.addMenu(module)
            sub.setToolTipsVisible(True)
            for entity_type in sorted(types, key=lambda t: t.name.lower()):
                action = sub.addAction(entity_type.name)
                action.setToolTip(entity_type.description)
                name = entity_type.full_name
                action.triggered.connect(lambda _=False, n=name: self.show_type(n))

    def _undo(self) -> None:
        if self.model:
            self.model.stack.undo()

    def _redo(self) -> None:
        if self.model:
            self.model.stack.redo()

    # ------------------------------------------------------------ loading
    def _error(self, title: str, exc: BaseException) -> None:
        dialog = TextDialog(title, "".join(traceback.format_exception(exc)))
        self._dialogs.append(dialog)
        dialog.show()

    def _status(self, text: str) -> None:
        self.statusBar().showMessage(text)
        # Loading is synchronous: repaint so a slow fetch shows its progress,
        # but take no clicks or keys, so no file action can re-enter it.
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def load(self, target: Path | None, *, reload_catalogue: bool = False) -> bool:
        """Open a service dir, config dir or ioc.yaml; reload entities if needed."""
        try:
            instance = find_instance(target)
            same_image = (
                not reload_catalogue
                and self.model is not None
                and instance.image == self.instance.image
                and instance.ioc_yaml is not None
                and self.instance.ioc_yaml is not None
                and instance.ioc_yaml.parent == self.instance.ioc_yaml.parent
            )
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                if same_image and self.model is not None:
                    catalogue = self.model.catalogue
                else:
                    catalogue = load_catalogue(
                        instance,
                        self.sources.image,
                        self.sources.generic_ioc,
                        self.sources.defs,
                        log=self._status,
                        refresh=self.sources.refresh,
                    )
                document = open_document(instance, self.sources.image)
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as exc:
            self._error("Open error", exc)
            return False
        self.set_document(instance, catalogue, document)
        if catalogue.warnings:
            QMessageBox.warning(self, "Entity warnings", "\n".join(catalogue.warnings))
        return True

    def set_document(
        self, instance: Instance, catalogue: Catalogue, document: Document
    ) -> None:
        self.table_view.setModel(None)  # type: ignore[arg-type]
        for table in self.tables.values():
            table.deleteLater()
        if self.model is not None:
            self.model.deleteLater()
        self.type_name = None
        self.instance = instance
        self.model = DocumentModel(document, catalogue)
        self.model.setParent(self)
        self.model.stack.cleanChanged.connect(self._update_title)
        self.model.structureChanged.connect(self._on_structure_problems)
        self.model.valueChanged.connect(self._on_value_problems)
        self.model.structureChanged.connect(self._on_structure)
        self.undo_view.setStack(self.model.stack)
        self.tables = {}
        self._set_problems([], [])
        self.dock_problems.hide()
        self._populate_components()
        self._on_structure()
        unknown = [t for t in document.types_in_order() if catalogue.get(t) is None]
        source = catalogue.source
        self._status(f"{len(catalogue.types)} entity types from {source}")
        if unknown:
            QMessageBox.warning(
                self,
                "Unknown entity types",
                "These types are not provided by the generic IOC and are shown "
                "read-only:\n" + "\n".join(unknown),
            )
        if not self.model.is_grouped():
            # as in xeb, each type's entities are kept together
            self.model.set_type_order(self.model.type_order, "Group entities by type")
            QMessageBox.information(
                self,
                "Entities regrouped",
                "This file interleaves entity types. They have been grouped by "
                "type, in order of first appearance, which changes the startup "
                "order. Undo (Ctrl+Z) to see the original order; nothing is "
                "written until you save.",
            )
        if not document.entities:
            self.statusBar().showMessage(
                "New instance: add entities from the Components menu, then Save"
            )
        self._update_title()

    def _on_structure(self) -> None:
        if self.model is None:
            return
        document = self.model.document
        names = list(self.model.type_order)
        current = self.type_name
        self.type_list.blockSignals(True)
        self.type_list.clear()
        for name in names:
            item = QListWidgetItem(name)
            entity_type = self.model.catalogue.get(name)
            if entity_type is None:
                item.setForeground(Qt.GlobalColor.darkRed)
                item.setToolTip("Unknown to this generic IOC (read-only)")
            else:
                count = len(document.of_type(name))
                item.setText(f"{name} ({count})")
                item.setToolTip(entity_type.description)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.type_list.addItem(item)
        self.type_list.blockSignals(False)
        if current in names:
            self._select_type_item(current)
        elif names:
            self.show_type(names[0])
        else:
            self.type_name = None
            self.table_view.setModel(None)  # type: ignore[arg-type]
        self._update_title()

    def _select_type_item(self, name: str) -> None:
        for row in range(self.type_list.count()):
            item = self.type_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self.type_list.blockSignals(True)
                self.type_list.setCurrentRow(row)
                self.type_list.blockSignals(False)
                break

    def _on_type_selected(self, _text: str) -> None:
        item = self.type_list.currentItem()
        if item is not None:  # pyright: ignore[reportUnnecessaryComparison]
            self.show_type(item.data(Qt.ItemDataRole.UserRole))

    def show_type(self, name: str) -> None:
        if self.model is None:
            return
        if name not in self.model.type_order:
            self.model.add_table(name)
        table = self.tables.get(name)
        if table is None:
            table = EntityTableModel(self.model, name)
            self.tables[name] = table
            self._apply_problems(table)
        self.type_name = name
        self.table_view.setModel(table)
        self.table_view.resizeColumnsToContents()
        self._select_type_item(name)
        entity_type = self.model.catalogue.get(name)
        self.dock_types.setWindowTitle(f"Entity types: {name}")
        if entity_type:
            self.table_view.setToolTip(entity_type.description)

    def show_entity(self, entity: CommentedMap, key: str | None = None) -> None:
        if self.model is None:
            return
        self.show_type(self.model.document.entity_type(entity))
        table = self.table_view.table()
        row = table.row_of(entity) if table is not None else None
        if table is None or row is None:
            return
        column = table.columns.index(key) if key in table.columns else 0
        index = table.index(row, column)
        self.table_view.setCurrentIndex(index)
        self.table_view.scrollTo(index)

    def _on_types_dropped(
        self,
        _parent: QModelIndex,
        _start: int,
        _end: int,
        _dest: QModelIndex,
        _row: int,
    ) -> None:
        order = [
            self.type_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.type_list.count())
        ]
        # rebuilding the list mid-drop is unsafe, so apply it afterwards
        QTimer.singleShot(0, lambda: self._apply_type_order(order))

    def _apply_type_order(self, order: list[str]) -> None:
        if self.model is not None:
            self.model.set_type_order(order)

    def move_table(self, step: int) -> None:
        if self.model is None or self.type_name is None:
            return
        order = list(self.model.type_order)
        position = order.index(self.type_name)
        dest = position + step
        if 0 <= dest < len(order):
            order.insert(dest, order.pop(position))
            self.model.set_type_order(order)

    def remove_table(self) -> None:
        if self.model is None or self.type_name is None:
            return
        name = self.type_name
        self.type_name = None
        self.model.remove_table(name)

    # --------------------------------------------------------- validation
    def validate(self) -> list[Problem]:
        if self.model is None:
            return []
        problems = validate(
            self.model.document, self.model.catalogue, self.instance.name
        )
        entities = self.model.document.entities
        found = [
            entities[p.entity]
            if p.entity is not None and p.entity < len(entities)
            else None
            for p in problems
        ]
        self._set_problems(problems, found)
        self.dock_problems.setWindowTitle("Problems")
        self.dock_problems.show()
        return self.problems

    def _set_problems(
        self, problems: list[Problem], entities: list[CommentedMap | None]
    ) -> None:
        """Show ``problems``, each tied to the entity object it was found on."""
        self.problems = problems
        self._problem_entities = entities
        self._show_problems()

    def _show_problems(self) -> None:
        self.problem_list.clear()
        if self.model is not None:
            document = self.model.document
            for problem, entity in zip(
                self.problems, self._problem_entities, strict=True
            ):
                text = str(problem)
                if entity is not None:
                    # the entity's position now, not when validation ran
                    problem.entity = document.index_of(entity)
                    text = f"{document.entity_type(entity)} {problem}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, problem)
                self.problem_list.addItem(item)
            if not self.problems:
                self.problem_list.addItem("No problems: ibek accepts this ioc.yaml")
        for table in self.tables.values():
            self._apply_problems(table)
        self.table_view.viewport().update()

    def _mark_stale(self) -> None:
        if self.problems:
            self.dock_problems.setWindowTitle(
                "Problems (edited since validation: F5 to check again)"
            )

    def _on_value_problems(self, entity: object, key: str) -> None:
        """An edited field may be fixed: drop its problems and keep the rest."""
        if entity is None:
            return
        whole = key == COMMENTED  # a commented entity is no longer validated
        keep = [
            (p, e)
            for p, e in zip(self.problems, self._problem_entities, strict=True)
            if not (e is entity and (whole or p.field == key))
        ]
        self._set_problems([p for p, _ in keep], [e for _, e in keep])
        self._mark_stale()

    def _on_structure_problems(self) -> None:
        """Drop problems of removed entities; the others follow their entity."""
        if self.model is None:
            return
        present = {id(e) for e in self.model.document.entities}
        keep = [
            (p, e)
            for p, e in zip(self.problems, self._problem_entities, strict=True)
            if e is None or id(e) in present
        ]
        self._set_problems([p for p, _ in keep], [e for _, e in keep])
        self._mark_stale()

    def _apply_problems(self, table: EntityTableModel) -> None:
        table.problems = {}
        for problem, entity in zip(self.problems, self._problem_entities, strict=True):
            if entity is not None:
                key = (id(entity), problem.field or "")
                table.problems.setdefault(key, []).append(problem.message)
        table.headerDataChanged.emit(
            Qt.Orientation.Vertical, 0, max(table.rowCount() - 1, 0)
        )

    def _on_problem_clicked(self, item: QListWidgetItem) -> None:
        problem = item.data(Qt.ItemDataRole.UserRole)
        if self.model is None or not isinstance(problem, Problem):
            return
        for candidate, entity in zip(
            self.problems, self._problem_entities, strict=True
        ):
            if candidate is problem and entity is not None:
                self.show_entity(entity, problem.field)
                return

    # ------------------------------------------------------------- files
    def _update_title(self) -> None:
        path = self.instance.ioc_yaml
        name = str(path) if path else "<untitled>"
        if path is not None and not path.exists():
            name += " (new)"
        self.setWindowTitle(f"{TITLE} - {name}[*]")
        modified = self.model is not None and not self.model.stack.isClean()
        self.setWindowModified(modified)

    def _prompt_unsaved(self) -> bool:
        """True if it is OK to discard the current document."""
        if self.model is None or self.model.stack.isClean():
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved Changes",
            "The current file has unsaved changes, what would you like to do?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def new(self) -> None:
        if not self._prompt_unsaved():
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the IOC service directory (it must have a values.yaml)"
        )
        if not folder:
            return
        ioc_yaml = Path(folder) / "config" / "ioc.yaml"
        if ioc_yaml.exists():
            answer = QMessageBox.question(
                self,
                "Instance exists",
                f"{ioc_yaml} already exists. Open it instead?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.load(Path(folder))

    def open_dialog(self) -> None:
        if not self._prompt_unsaved():
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open ioc.yaml", "", "YAML files (*.yaml *.yml);;All files (*)"
        )
        if filename:
            self.load(Path(filename))

    def reload(self) -> None:
        if self.instance.ioc_yaml is not None and self._prompt_unsaved():
            target = self.instance.service_dir or self.instance.ioc_yaml
            self.load(target, reload_catalogue=True)

    def save(self) -> bool:
        if self.model is None:
            return False
        if self.instance.ioc_yaml is None:
            return self.save_as()
        return self._write(self.instance.ioc_yaml)

    def save_as(self) -> bool:
        if self.model is None:
            return False
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save ioc.yaml", str(self.instance.ioc_yaml or "ioc.yaml")
        )
        if not filename:
            return False
        return self._write(Path(filename))

    def _write(self, path: Path) -> bool:
        if self.model is None:
            return False
        problems = self.validate()
        if problems:
            answer = QMessageBox.warning(
                self,
                "Validation problems",
                f"ibek reports {len(problems)} problem(s), listed in the Problems "
                "panel. Save anyway?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save:
                return False
        try:
            self.model.document.save(path)
        except Exception as exc:
            self._error("Save error", exc)
            return False
        self.instance.ioc_yaml = path
        self.model.stack.setClean()
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}")
        return True

    def edit_description(self) -> None:
        if self.model is None:
            return
        current = str(self.model.document.data.get("description", ""))
        text, ok = QInputDialog.getText(
            self, "Description", "IOC description:", text=current
        )
        if ok and text != current:
            self.model.set_description(text)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._prompt_unsaved():
            event.accept()
        else:
            event.ignore()


def run(target: Path | None, sources: Sources) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(sources)
    window.show()
    if target is not None or sources.given():
        # once the window has been drawn, so it isn't black while loading.
        # With no target, an untitled instance gets the given entity source.
        QTimer.singleShot(0, lambda: window.load(target))
    return cast(QApplication, app).exec()
