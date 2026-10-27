"""Table interaction: clipboard, fill, row actions and keyboard events."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    QTimer,
)
from PySide6.QtGui import QContextMenuEvent, QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableView

from ..document import COMMENTED
from ..table_text import CELLS_MIME, decode_cells, encode_tsv, increment_text
from .delegates import CellDelegate
from .table_model import EntityTableModel

if TYPE_CHECKING:
    from .main_window import MainWindow


ALL_TRIGGERS = QAbstractItemView.EditTrigger.AllEditTriggers


class TableView(QTableView):
    """The entity table, with xeb's clipboard and fill operations."""

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self.window_ = window
        self.setItemDelegate(CellDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        # drag a row header to reorder entities within the table, as in xeb
        header = self.verticalHeader()
        header.setSectionsMovable(True)
        header.sectionMoved.connect(self._on_row_moved)

    def _on_row_moved(self, _logical: int, old: int, new: int) -> None:
        header = self.verticalHeader()
        header.blockSignals(True)
        header.moveSection(new, old)  # the model reorders; undo the visual move
        header.blockSignals(False)
        table = self.table()
        if table is not None and table.editable:
            QTimer.singleShot(0, lambda: self._move_row(table, old, new))

    def _move_row(self, table: EntityTableModel, old: int, new: int) -> None:
        table.move_row(old, new)
        self.selectRow(new)

    def table(self) -> EntityTableModel | None:
        return cast(EntityTableModel | None, self.model())

    def edit(  # type: ignore[override]
        self,
        index: QModelIndex | QPersistentModelIndex,
        trigger: QAbstractItemView.EditTrigger = ALL_TRIGGERS,
        event: QEvent | None = None,
    ) -> bool:
        """As in xeb, double-clicking (or typing in) the X cell toggles it."""
        table = self.table()
        if (
            table is not None
            and table.editable
            and index.isValid()
            and table.columns[index.column()] == COMMENTED
            and trigger
            in (
                QAbstractItemView.EditTrigger.DoubleClicked,
                QAbstractItemView.EditTrigger.EditKeyPressed,
                QAbstractItemView.EditTrigger.AnyKeyPressed,
            )
        ):
            table.setData(index, "" if index.data() else "X")
            return False
        return super().edit(index, trigger, event)  # type: ignore[arg-type]

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.clear()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.window_.menu_edit.exec(event.globalPos())

    def _bounds(self) -> tuple[list[QModelIndex], int, int, int, int]:
        cells = self.selectedIndexes()
        rows = [c.row() for c in cells] or [0]
        cols = [c.column() for c in cells] or [0]
        return cells, min(rows), min(cols), max(rows), max(cols)

    def copy(self) -> None:
        cells, row0, col0, row1, col1 = self._bounds()
        if not cells:
            return
        data = [[""] * (col1 - col0 + 1) for _ in range(row1 - row0 + 1)]
        for cell in cells:
            data[cell.row() - row0][cell.column() - col0] = str(
                cell.data(Qt.ItemDataRole.EditRole) or ""
            )
        mime = QMimeData()
        mime.setData(CELLS_MIME, json.dumps(data).encode("utf-8"))
        mime.setText(encode_tsv(data))
        QApplication.clipboard().setMimeData(mime)

    def cut(self) -> None:
        self.copy()
        self.clear()

    def clear(self) -> None:
        table = self.table()
        cells = self.selectedIndexes()
        if table is not None and cells and table.editable:
            table.clear_indexes(cells)

    def paste(self) -> None:
        table = self.table()
        cells, row0, col0, row1, col1 = self._bounds()
        if table is None or not cells or not table.editable:
            return
        mime = QApplication.clipboard().mimeData()
        if mime is None:  # pyright: ignore[reportUnnecessaryComparison]
            return
        data = decode_cells(bytes(mime.data(CELLS_MIME).data()), mime.text())
        if not data:
            return
        if len(data) == 1 and len(data[0]) == 1:
            data = [data[0] * (col1 - col0 + 1) for _ in range(row1 - row0 + 1)]
        stack = table.model.stack
        stack.beginMacro("Paste from clipboard")
        if len(cells) == 1:
            # one cell selected: paste the whole clipboard, adding rows if needed
            missing = row0 + len(data) - table.rowCount()
            if missing > 0:
                table.insert_rows(table.rowCount(), missing)
            for r, row in enumerate(data):
                for c, value in enumerate(row):
                    if col0 + c < table.columnCount():
                        table.setData(table.index(row0 + r, col0 + c), value)
        else:
            # many cells selected: tile the clipboard over the selection
            rows, cols = len(data), max(len(r) for r in data)
            for cell in cells:
                row = data[(cell.row() - row0) % rows]
                column = (cell.column() - col0) % cols
                if column < len(row):
                    table.setData(cell, row[column])
        stack.endMacro()

    def fill(self, increment: bool = False) -> None:
        table = self.table()
        cells, row0, col0, row1, _ = self._bounds()
        if table is None or not cells or not table.editable:
            return
        stack = table.model.stack
        stack.beginMacro("Increment Cells" if increment else "Fill Cells")
        if row0 == row1:
            # one row: fill across from the leftmost cell
            source = str(table.index(row0, col0).data(Qt.ItemDataRole.EditRole) or "")
            for cell in cells:
                if cell.column() != col0:
                    step = cell.column() - col0
                    text = increment_text(source, step) if increment else source
                    table.setData(cell, text)
        else:
            # fill each column down from its top selected cell
            for column in sorted({c.column() for c in cells}):
                column_cells = [c for c in cells if c.column() == column]
                top = min(c.row() for c in column_cells)
                source = str(
                    table.index(top, column).data(Qt.ItemDataRole.EditRole) or ""
                )
                for cell in column_cells:
                    if cell.row() != top:
                        step = cell.row() - top
                        text = increment_text(source, step) if increment else source
                        table.setData(cell, text)
        stack.endMacro()

    def insert_row(self, under: bool = False) -> None:
        table = self.table()
        if table is None or not table.editable:
            return
        cells, row0, _, row1, _ = self._bounds()
        if cells:
            count = row1 - row0 + 1
            row = row1 + 1 if under else row0
        else:
            count = 1
            row = table.rowCount() if under else 0
        created = table.insert_rows(row, count)
        first = table.row_of(created[0]) if created else None
        if first is not None:
            self.selectRow(first)

    def remove_rows(self) -> None:
        table = self.table()
        cells = self.selectedIndexes()
        if table is not None and cells and table.editable:
            table.remove_rows([c.row() for c in cells])
