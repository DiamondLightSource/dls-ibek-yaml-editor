# pyright: reportPrivateUsage=false
"""GUI tests; skipped where Qt cannot load (e.g. no libGL)."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError as exc:  # pragma: no cover - depends on the system
    if os.getenv("DLS_IBEK_YAML_EDITOR_REQUIRE_GUI"):
        raise
    pytest.skip(f"Qt is not usable here: {exc}", allow_module_level=True)

from dls_ibek_yaml_editor.gui.main_window import MainWindow, Sources

from .conftest import DEFS


@pytest.fixture(scope="session")
def application() -> Iterator[QApplication]:
    app = QApplication([])
    yield app
    app.clipboard().clear()
    app.processEvents()


@pytest.fixture
def window(
    monkeypatch: pytest.MonkeyPatch, application: QApplication
) -> Iterator[MainWindow]:
    app = application
    # never block on a dialog
    for name in ("warning", "question", "information"):
        monkeypatch.setattr(
            QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.Save
        )
    win = MainWindow(Sources(defs=DEFS))
    yield win
    win.model = None  # skip the unsaved-changes prompt
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _select(window: MainWindow, cells: list[tuple[int, int]]) -> None:
    view = window.table_view
    view.setCurrentIndex(view.model().index(*cells[0]))
    view.clearSelection()
    selection = view.selectionModel()
    for row, column in cells:
        index = view.model().index(row, column)
        selection.select(index, QItemSelectionModel.SelectionFlag.Select)


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


def test_x_column_disables_a_row(window: MainWindow, service: Path):
    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    index = table.index(0, 0)
    assert table.headerData(0, Qt.Orientation.Horizontal) == "X"
    assert index.data() == ""
    table.setData(index, "X")
    assert index.data() == "X"
    document = window.model.document  # type: ignore[union-attr]
    assert document.is_commented(table.entity(0))
    assert "  # - type: motorPLC.NX102_MotorAxisPot\n" in document.dumps()
    _select(window, [(0, 0)])
    window.table_view.clear()
    assert not document.is_commented(table.entity(0))
    window.model.stack.undo()  # type: ignore[union-attr]
    assert index.data() == "X"


def test_double_click_toggles_x(window: MainWindow, service: Path):
    from PySide6.QtWidgets import QAbstractItemView

    window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    index = table.index(0, 0)
    double = QAbstractItemView.EditTrigger.DoubleClicked
    assert not window.table_view.edit(index, double, None)  # no editor opens
    assert index.data() == "X"
    window.table_view.edit(index, double, None)
    assert index.data() == ""


@pytest.mark.parametrize(
    "field,value",
    [
        ("pot_monitor_only", "{{ enabled }}"),
        ("home_method", "{{ method }}"),
        ("home_method", "unlisted existing value"),
    ],
)
def test_delegate_commit_preserves_unlisted_values(
    window: MainWindow, service: Path, field: str, value: str
):
    from PySide6.QtWidgets import QStyleOptionViewItem

    from dls_ibek_yaml_editor.gui.delegates import CellDelegate

    assert window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    index = table.index(0, _column(window, field))
    table.setData(index, value)
    delegate = CellDelegate(window)
    editor = delegate.createEditor(window, QStyleOptionViewItem(), index)
    delegate.setEditorData(editor, index)
    delegate.setModelData(editor, table, index)
    assert table.entity(0)[field] == value


def test_failed_reload_keeps_document_usable(
    window: MainWindow, service: Path, monkeypatch: pytest.MonkeyPatch
):
    assert window.load(service)
    model = window.model
    assert model is not None
    path = service / "config" / "ioc.yaml"
    path.write_text("entities: [broken")
    errors: list[str] = []
    monkeypatch.setattr(window, "_error", lambda title, exc: errors.append(str(exc)))
    window.reload()
    assert errors
    assert window.model is model
    window.show_type("epics.EpicsEnvSet")
    table = window.table_view.table()
    assert table is not None
    table.setData(table.index(0, _column(window, "value")), "changed")
    assert window.isWindowModified()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    assert not window._prompt_unsaved()
    assert window.save()
    assert "changed" in path.read_text()
    assert model.stack.isClean()


@pytest.mark.parametrize("change", ["move", "insert", "remove", "edit", "undo"])
def test_problems_follow_their_entity_after_edits(
    window: MainWindow, service: Path, change: str
):
    assert window.load(service)
    model = window.model
    assert model is not None
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    addr = _column(window, "ADDR")
    bad = table.entity(0)
    table.setData(table.index(0, addr), "bad")
    assert len(window.validate()) == 1
    if change == "move":
        table.move_row(0, 1)
    elif change == "insert":
        table.insert_rows(0, 1)
    elif change == "remove":
        table.remove_rows([0])
    elif change == "undo":
        model.stack.undo()
    else:
        table.setData(table.index(0, addr), "1")
    fixed = change in ("remove", "undo", "edit")
    assert len(window.problems) == (0 if fixed else 1)
    if not fixed:
        # the problem stays on the same entity, wherever it now is
        row = table.row_of(bad)
        assert row is not None
        assert table.index(row, addr).data(Qt.ItemDataRole.BackgroundRole)
        assert window.problems[0].entity == model.document.index_of(bad)
        window.table_view.clearSelection()
        window._on_problem_clicked(window.problem_list.item(0))  # pyright: ignore[reportPrivateUsage]
        assert window.table_view.currentIndex().row() == row
        assert "F5" in window.dock_problems.windowTitle()


def test_editing_one_field_keeps_other_problems(window: MainWindow, service: Path):
    assert window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisPot")
    table = window.table_view.table()
    assert table is not None
    table.setData(table.index(0, _column(window, "ADDR")), "bad")
    table.setData(table.index(1, _column(window, "ADDR")), "bad")
    assert len(window.validate()) == 2
    table.setData(table.index(0, _column(window, "ADDR")), "1")
    assert [p.entity for p in window.problems] == [
        window.model.document.index_of(table.entity(1))  # type: ignore[union-attr]
    ]


def test_copy_paste_preserves_backslashes_and_control_characters(
    window: MainWindow, service: Path
):
    assert window.load(service)
    window.show_type("epics.EpicsEnvSet")
    table = window.table_view.table()
    assert table is not None
    column = _column(window, "value")
    value = r"C:\new\test" + "\nline\tvalue"
    table.setData(table.index(0, column), value)
    _select(window, [(0, column)])
    window.table_view.copy()
    _select(window, [(1, column)])
    window.table_view.paste()
    assert table.entity(1)["value"] == value
    table.model.stack.undo()
    assert table.entity(1)["value"] != value


@pytest.mark.parametrize(
    "answer,expected",
    [
        (QMessageBox.StandardButton.Cancel, False),
        (QMessageBox.StandardButton.Discard, True),
        (QMessageBox.StandardButton.Save, True),
    ],
)
def test_unsaved_dialog_choices(
    window: MainWindow,
    service: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: QMessageBox.StandardButton,
    expected: bool,
):
    assert window.load(service)
    model = window.model
    assert model is not None
    model.set_description("edited")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answer)
    assert window._prompt_unsaved() is expected
    assert model.stack.isClean() is (answer == QMessageBox.StandardButton.Save)


def test_failed_save_does_not_allow_close(
    window: MainWindow, service: Path, monkeypatch: pytest.MonkeyPatch
):
    assert window.load(service)
    model = window.model
    assert model is not None
    model.set_description("edited")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(model.document, "save", fail)
    monkeypatch.setattr(window, "_error", lambda *args: None)
    assert not window._prompt_unsaved()  # fixture chooses Save
    assert not model.stack.isClean()


def test_clear_distinguishes_missing_null_and_empty(window: MainWindow, service: Path):
    from dls_ibek_yaml_editor.document import MISSING

    assert window.load(service)
    model = window.model
    assert model is not None
    entity = model.document.entities[0]
    for original in (None, ""):
        entity["value"] = original
        assert model.set_text(entity, "value", "")
        assert model.document.get(entity, "value") is MISSING
        model.stack.undo()
        assert entity["value"] == original
    del entity["value"]
    assert not model.set_text(entity, "value", "")


def test_keyboard_commits_cell_editor(window: MainWindow, service: Path):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    assert window.load(service)
    window.show_type("epics.EpicsEnvSet")
    window.show()
    view = window.table_view
    table = view.table()
    assert table is not None
    index = table.index(0, _column(window, "value"))
    view.setCurrentIndex(index)
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_F2)
    QApplication.processEvents()
    editor = view.findChild(QLineEdit)
    assert editor is not None
    editor.selectAll()
    QTest.keyClicks(editor, "keyboard edit")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert table.entity(0)["value"] == "keyboard edit"


@pytest.mark.parametrize("value", [None, ""])
def test_opening_editor_does_not_remove_explicit_empty_value(
    window: MainWindow, service: Path, value: str | None
):
    from PySide6.QtWidgets import QStyleOptionViewItem

    from dls_ibek_yaml_editor.gui.delegates import CellDelegate

    assert window.load(service)
    window.show_type("epics.EpicsEnvSet")
    table = window.table_view.table()
    assert table is not None
    table.entity(0)["value"] = value
    index = table.index(0, _column(window, "value"))
    delegate = CellDelegate(window)
    editor = delegate.createEditor(window, QStyleOptionViewItem(), index)
    delegate.setEditorData(editor, index)
    delegate.setModelData(editor, table, index)
    assert "value" in table.entity(0)
    assert table.entity(0)["value"] == value


def test_paste_into_irregular_selection_leaves_other_cells(
    window: MainWindow, service: Path
):
    assert window.load(service)
    window.show_type("motorPLC.NX102_MotorAxisSpare")
    table = window.table_view.table()
    assert table is not None
    column = _column(window, "spare_no")
    _select(window, [(0, column), (2, column)])
    QApplication.clipboard().setText("9")
    window.table_view.paste()
    assert [table.entity(i)["spare_no"] for i in range(3)] == [9, 2, 9]
    table.model.stack.undo()
    assert [table.entity(i)["spare_no"] for i in range(3)] == [1, 2, 3]


def test_status_repaints_without_user_input(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
):
    from PySide6.QtCore import QEventLoop

    calls: list[object] = []
    monkeypatch.setattr(QApplication, "processEvents", lambda *a: calls.append(a))
    window._status("Fetching ibek definitions ...")  # pyright: ignore[reportPrivateUsage]
    assert window.statusBar().currentMessage() == "Fetching ibek definitions ..."
    assert calls == [(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents,)]
