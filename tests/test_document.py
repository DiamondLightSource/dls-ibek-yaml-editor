import difflib
from pathlib import Path

from dls_ibek_yaml_editor.catalogue import Catalogue
from dls_ibek_yaml_editor.document import MISSING, Document, new_entity, text_to_value
from dls_ibek_yaml_editor.validate import validate

from .conftest import SERVICE

IOC_YAML = SERVICE / "config" / "ioc.yaml"


def _diff(before: str, after: str) -> list[str]:
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), n=0)
    return [line for line in lines if line[:1] in "+-" and line[:3] not in "+++---"]


def test_round_trip_is_identical():
    assert Document.load(IOC_YAML).dumps() == IOC_YAML.read_text()


def test_edit_changes_only_that_line(catalogue: Catalogue):
    document = Document.load(IOC_YAML)
    axis = document.of_type("motorPLC.NX102_MotorAxisPot")[0]
    document.set(axis, "MRES", "1e-05")
    assert _diff(IOC_YAML.read_text(), document.dumps()) == [
        "-    MRES: '7.8125e-06'",
        "+    MRES: '1e-05'",
    ]


def test_new_key_follows_parameter_order(catalogue: Catalogue):
    document = Document.load(IOC_YAML)
    axis = document.of_type("motorPLC.NX102_MotorAxisPot")[0]
    entity_type = catalogue.get("motorPLC.NX102_MotorAxisPot")
    document.set(axis, "VELO", "1.5", entity_type)
    keys = list(axis)
    assert keys.index("VELO") == keys.index("ADDR") + 1


def test_key_added_at_end_keeps_separator(catalogue: Catalogue):
    document = Document.load(IOC_YAML)
    spare = document.of_type("motorPLC.NX102_MotorAxisSpare")[0]
    document.set(spare, "zzz", 1)  # unknown key: appended after the last key
    assert _diff(IOC_YAML.read_text(), document.dumps()) == ["+    zzz: 1"]


def test_remove_key():
    document = Document.load(IOC_YAML)
    axis = document.of_type("motorPLC.NX102_MotorAxisPot")[1]
    old = document.set(axis, "pot_monitor_only", MISSING)
    assert old is True
    assert _diff(IOC_YAML.read_text(), document.dumps()) == [
        "-    pot_monitor_only: true",
    ]


def test_insert_and_move_keep_blank_lines():
    document = Document.load(IOC_YAML)
    entity = new_entity("epics.EpicsEnvSet")
    entity["name"] = "A"
    entity["value"] = "b"
    document.insert(1, entity)
    document.move(len(document.entities) - 1, 0)
    text = document.dumps()
    assert "  - type: epics.EpicsEnvSet\n    name: A\n    value: b\n\n" in text
    assert text.endswith("spare_no: 5\n")
    assert "entities:\n\n  - type: motorPLC.NX102_MotorAxisSpare\n" in text
    assert "spare_no: 6\n\n  - type: epics.EpicsEnvSet\n" in text


def test_new_document(tmp_path: Path):
    path = tmp_path / "config" / "ioc.yaml"
    document = Document.new(path, "https://example.com/schema.json", "test IOC")
    document.insert(0, new_entity("epics.EpicsEnvSet"))
    document.insert(1, new_entity("epics.EpicsEnvSet"))
    document.save()
    text = path.read_text()
    assert text.startswith("# yaml-language-server: $schema=https://example.com/")
    assert "ioc_name: \"{{ _global.get_env('IOC_NAME') }}\"" in text
    assert 'description: "test IOC"' in text
    assert "  - type: epics.EpicsEnvSet\n\n  - type: epics.EpicsEnvSet\n" in text
    assert Document.load(path).dumps() == text


def test_text_to_value(catalogue: Catalogue):
    axis = catalogue.get("motorPLC.NX102_MotorAxisPot")
    assert axis is not None
    assert text_to_value("", axis.params["ADDR"]) is MISSING
    assert text_to_value("3", axis.params["ADDR"]) == 3
    assert text_to_value("{{ n }}", axis.params["ADDR"]) == "{{ n }}"
    assert text_to_value("20", axis.params["DHLM"]) == "20"  # a str parameter
    assert text_to_value("true", axis.params["pot_monitor_only"]) is True


def test_ids(catalogue: Catalogue):
    assert Document.load(IOC_YAML).ids(catalogue) == ["MC1"]


def test_grouped_keeps_order_within_types():
    document = Document.load(IOC_YAML)
    first = document.types_in_order()
    assert document.grouped(first) == list(document.entities)  # already grouped
    order = [first[-1], *first[:-1]]  # spares first
    document.reorder(document.grouped(order))
    types = [document.entity_type(e) for e in document.entities]
    assert types[:6] == ["motorPLC.NX102_MotorAxisSpare"] * 6
    assert [e["spare_no"] for e in document.entities[:6]] == [1, 2, 3, 4, 5, 6]
    text = document.dumps()
    assert "spare_no: 6\n\n  - type: epics.EpicsEnvSet\n" in text
    assert text.endswith("    pot_monitor_only: true\n")  # no trailing blank


def _comment_out_first_axis(text: str) -> str:
    """fe99i with its first axis commented out as VS Code's Ctrl+/ does."""
    lines = text.splitlines(keepends=True)
    start = lines.index("  - type: motorPLC.NX102_MotorAxisPot\n")
    end = lines.index("\n", start)
    for n in range(start, end):
        lines[n] = "  # " + lines[n][2:]
    return "".join(lines)


def test_commented_entity_round_trip(catalogue: Catalogue):
    text = _comment_out_first_axis(IOC_YAML.read_text())
    document = Document(text)
    axes = document.of_type("motorPLC.NX102_MotorAxisPot")
    assert document.is_commented(axes[0])
    assert not document.is_commented(axes[1])
    assert document.dumps() == text
    # it is left out of validation and of the reference choices
    assert len(document.plain()["entities"]) == len(document.entities) - 1
    assert axes[0]["name"] not in document.ids(catalogue)
    assert validate(document, catalogue, "FE99I-MO-IOC-01") == []


def test_comment_and_uncomment(catalogue: Catalogue):
    document = Document.load(IOC_YAML)
    axis = document.of_type("motorPLC.NX102_MotorAxisPot")[0]
    document.set_commented(axis, True)
    assert document.dumps() == _comment_out_first_axis(IOC_YAML.read_text())
    document.set_commented(axis, False)
    assert document.dumps() == IOC_YAML.read_text()


def test_other_comments_are_left_alone():
    text = IOC_YAML.read_text().replace(
        "entities:\n", "entities:\n  # - type: not yaml: [\n  # just a note\n", 1
    )
    document = Document(text)
    assert not any(document.is_commented(e) for e in document.entities)
    assert document.dumps() == text


def test_problems_point_past_commented_entities(catalogue: Catalogue):
    document = Document(_comment_out_first_axis(IOC_YAML.read_text()))
    second = document.of_type("motorPLC.NX102_MotorAxisPot")[1]
    document.set(second, "ADDR", "x")
    problems = validate(document, catalogue, "FE99I-MO-IOC-01")
    assert [p.entity for p in problems] == [document.index_of(second)]


def test_comment_between_entities_is_kept():
    text = IOC_YAML.read_text().replace(
        "\n  - type: motorPLC.NX102_MotorAxisPot\n",
        "\n  # Axes\n  - type: motorPLC.NX102_MotorAxisPot\n",
        1,
    )
    assert Document(text).dumps() == text
