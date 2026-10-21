"""GUI tests; skipped where Qt cannot load (e.g. no libGL)."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError as exc:  # pragma: no cover - depends on the system
    pytest.skip(f"Qt is not usable here: {exc}", allow_module_level=True)

from dls_ibek_yaml_editor.gui.main_window import MainWindow, Sources

from .conftest import DEFS


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> Iterator[MainWindow]:
    app = QApplication.instance() or QApplication([])
    # never block on a dialog
    for name in ("warning", "question", "information"):
        monkeypatch.setattr(
            QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.Save
        )
    win = MainWindow(Sources(defs=DEFS))
    yield win
    win.model = None  # skip the unsaved-changes prompt
    win.close()
    app.processEvents()


def _select(window: MainWindow, cells: list[tuple[int, int]]) -> None:
    view = window.table_view
    view.clearSelection()
    selection = view.selectionModel()
    for row, column in cells:
        index = view.model().index(row, column)
        selection.select(index, QItemSelectionModel.SelectionFlag.Select)
    view.setCurrentIndex(view.model().index(*cells[0]))
    view.selectionModel().select(
        view.model().index(*cells[0]), QItemSelectionModel.SelectionFlag.Select
    )


def _column(window: MainWindow, name: str) -> int:
    table = window.table_view.table()
    assert table is not None
    return table.columns.index(name)


def test_open_shows_types_in_file_order(window: MainWindow, service: Path):
    assert window.load(service)
    names = [
        window.type_list.item(i).data(256) for i in range(window.type_list.count())
    ]
    assert names[0] == "epics.EpicsEnvSet"
    assert names[-1] == "motorPLC.NX102_MotorAxisSpare"
    assert len(window.model.type_order) == 6  # type: ignore[union-attr]
    assert window.windowTitle().endswith("ioc.yaml[*]")


def test_edit_undo_save(window: MainWindow, service: Path):
    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    index = table.index(0, _column(window, "MRES"))
    assert index.data() == "7.8125e-06"
    table.setData(index, "1e-05")
    assert index.data() == "1e-05"
    assert window.isWindowModified()
    window.model.stack.undo()  # type: ignore[union-attr]
    assert index.data() == "7.8125e-06"
    window.model.stack.redo()  # type: ignore[union-attr]
    assert window.save()
    text = (service / "config" / "ioc.yaml").read_text()
    assert "MRES: '1e-05'" in text
    assert not window.isWindowModified()


def test_defaults_are_shown_not_written(window: MainWindow, service: Path):
    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    index = table.index(0, _column(window, "VELO"))
    assert index.data() == "0.5"  # the default, shown in grey
    assert index.data(2) == ""  # EditRole: nothing in the file


def test_fill_and_increment(window: MainWindow, service: Path):
    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisSpare")
    column = _column(window, "spare_no")
    _select(window, [(row, column) for row in range(6)])
    window.table_view.fill(increment=True)
    table = window.table_view.table()
    assert table is not None
    assert [table.index(r, column).data() for r in range(6)] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
    ]
    table.setData(table.index(0, column), "7")
    window.table_view.fill(increment=True)
    assert table.index(5, column).data() == "12"


def test_copy_paste(window: MainWindow, service: Path):
    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisSpare")
    table = window.table_view.table()
    assert table is not None
    column = _column(window, "spare_no")
    QApplication.clipboard().setText("10\n11\n12")
    _select(window, [(0, column)])
    window.table_view.paste()
    assert [table.index(r, column).data() for r in range(4)] == ["10", "11", "12", "4"]


def test_insert_and_remove_rows(window: MainWindow, service: Path):
    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisSpare")
    table = window.table_view.table()
    assert table is not None
    _select(window, [(5, 0)])
    window.table_view.insert_row(under=True)
    assert table.rowCount() == 7
    document = window.model.document  # type: ignore[union-attr]
    assert document.entity_type(document.entities[-1]) == table.type_name
    _select(window, [(6, 0)])
    window.table_view.remove_rows()
    assert table.rowCount() == 6


def test_move_table_regroups_the_file(window: MainWindow, service: Path):
    window.load(service)
    model = window.model
    assert model is not None
    window.show_type("ether_ip.EtherIPInit")
    window.move_table(1)  # after the controller: its port is not defined yet
    types = [model.document.entity_type(e) for e in model.document.entities]
    assert (
        types.index("ether_ip.EtherIPInit")
        == types.index("motorPLC.NX102_MotorController") + 1
    )
    model.stack.undo()
    assert model.document.dumps() == (service / "config" / "ioc.yaml").read_text()


def test_controller_after_axes_is_a_problem(window: MainWindow, service: Path):
    window.load(service)
    model = window.model
    assert model is not None
    order = [t for t in model.type_order if t != "motorPLC.NX102_MotorController"]
    model.set_type_order([*order, "motorPLC.NX102_MotorController"])
    problems = window.validate()
    assert any("object MC1 not found" in p.message for p in problems)


def test_move_row_within_table(window: MainWindow, service: Path):
    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    first = table.entity(0)
    table.move_row(0, 1)
    assert table.entity(1) is first


def test_remove_table_and_undo(window: MainWindow, service: Path):
    window.load(service)
    model = window.model
    assert model is not None
    window.show_type("motorPLC.NX102_MotorAxisSpare")
    window.remove_table()
    assert "motorPLC.NX102_MotorAxisSpare" not in model.type_order
    assert not model.document.of_type("motorPLC.NX102_MotorAxisSpare")
    model.stack.undo()
    assert model.type_order[-1] == "motorPLC.NX102_MotorAxisSpare"
    assert model.document.dumps() == (service / "config" / "ioc.yaml").read_text()


def test_interleaved_file_is_grouped_on_load(window: MainWindow, service: Path):
    ioc_yaml = service / "config" / "ioc.yaml"
    text = ioc_yaml.read_text()
    # put an EpicsEnvSet at the end, after the motor entities
    ioc_yaml.write_text(
        text + "\n  - type: epics.EpicsEnvSet\n    name: X\n    value: 'y'\n"
    )
    window.load(service)
    model = window.model
    assert model is not None
    types = [model.document.entity_type(e) for e in model.document.entities]
    assert types[:3] == ["epics.EpicsEnvSet"] * 3
    assert window.isWindowModified()  # an undoable change, not yet saved
    model.stack.undo()
    assert model.document.entity_type(model.document.entities[-1]) == (
        "epics.EpicsEnvSet"
    )


def test_new_table_goes_after_earlier_tables(window: MainWindow, service: Path):
    window.load(service)
    model = window.model
    assert model is not None
    window.show_type("epics.StartupCommand")
    order = list(model.type_order)
    order.remove("epics.StartupCommand")
    order.insert(1, "epics.StartupCommand")  # second table
    model.set_type_order(order)
    window.show_type("epics.StartupCommand")
    window.table_view.insert_row()
    types = [model.document.entity_type(e) for e in model.document.entities]
    assert types.index("epics.StartupCommand") == 2  # after the 2 EpicsEnvSets


def test_validation_marks_cells(window: MainWindow, service: Path):
    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    table.setData(table.index(0, _column(window, "ADDR")), "x")
    problems = window.validate()
    assert len(problems) == 1
    assert table.index(0, _column(window, "ADDR")).data(8) is not None  # background


def test_new_instance(window: MainWindow, new_service: Path):
    assert window.load(new_service)
    assert "(new)" in window.windowTitle()
    window.show_type("epics.EpicsEnvSet")
    window.table_view.insert_row()
    table = window.table_view.table()
    assert table is not None
    table.setData(table.index(0, _column(window, "name")), "EPICS_TZ")
    table.setData(table.index(0, _column(window, "value")), "GMT0BST")
    assert window.save()
    text = (new_service / "config" / "ioc.yaml").read_text()
    assert "/-/releases/2026.10.1/downloads/ibek.ioc.schema.json" in text
    assert (
        "  - type: epics.EpicsEnvSet\n    name: EPICS_TZ\n    value: GMT0BST\n" in text
    )


def test_object_reference_choices(window: MainWindow, service: Path):
    from dls_ibek_yaml_editor.gui.delegates import CellDelegate

    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    index = table.index(0, _column(window, "controller"))
    editor: Any = CellDelegate().createEditor(window, None, index)  # type: ignore[arg-type]
    assert [editor.itemText(i) for i in range(editor.count())] == ["", "MC1"]
