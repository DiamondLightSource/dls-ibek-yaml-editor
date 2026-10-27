"""ibek integration and validation error locations."""

from pathlib import Path

import pytest

from dls_ibek_yaml_editor.catalogue import Catalogue
from dls_ibek_yaml_editor.document import Document
from dls_ibek_yaml_editor.validate import validate


def test_valid(service: Path, catalogue: Catalogue):
    document = Document.load(service / "config" / "ioc.yaml")
    assert validate(document, catalogue, "FE99I-MO-IOC-01") == []


def test_problems_are_located(service: Path, catalogue: Catalogue):
    document = Document.load(service / "config" / "ioc.yaml")
    axis = document.of_type("motorPLC.NX102_MotorAxisPot")[0]
    axis["controller"] = "NOPE"
    spare = document.of_type("motorPLC.NX102_MotorAxisSpare")[0]
    del spare["ADDR"]
    problems = validate(document, catalogue, "FE99I-MO-IOC-01")
    located = {(p.entity, p.field) for p in problems}
    assert (document.index_of(spare), "ADDR") in located
    assert any(p.entity == document.index_of(axis) for p in problems)
    assert any("NOPE" in p.message for p in problems)


def test_bad_int_is_reported_once(service: Path, catalogue: Catalogue):
    document = Document.load(service / "config" / "ioc.yaml")
    axis = document.of_type("motorPLC.NX102_MotorAxisPot")[0]
    axis["ADDR"] = "x"
    problems = validate(document, catalogue, "FE99I-MO-IOC-01")
    assert [(p.field, p.message) for p in problems] == [
        (
            "ADDR",
            "Input should be a valid integer, unable to parse string as an integer",
        )
    ]


def test_validation_restores_ioc_name_after_failure(
    catalogue: Catalogue, monkeypatch: pytest.MonkeyPatch
):
    from ibek.utils import UTILS

    from dls_ibek_yaml_editor import validate as validation

    monkeypatch.setattr(UTILS, "ioc_name", "previous")

    def fail(*args: object) -> None:
        raise ValueError("model failure")

    monkeypatch.setattr(validation.EntityFactory, "make_entity_models", fail)
    problems = validate(Document("entities: []\n"), catalogue, "new")
    assert "model failure" in problems[0].message
    assert UTILS.ioc_name == "previous"


def test_repeated_validation_does_not_retain_ids(service: Path, catalogue: Catalogue):
    document = Document.load(service / "config" / "ioc.yaml")
    assert validate(document, catalogue, "one") == []
    assert validate(document, catalogue, "two") == []
