from pathlib import Path

import pytest

from dls_ibek_yaml_editor.catalogue import Catalogue
from dls_ibek_yaml_editor.document import Document
from dls_ibek_yaml_editor.service import load_catalogue, open_document, resolve
from dls_ibek_yaml_editor.validate import validate

from .conftest import DEFS, IMAGE


@pytest.mark.parametrize("sub", ["", "config", "config/ioc.yaml"])
def test_resolve(service: Path, sub: str):
    instance = resolve(service / sub if sub else service)
    assert instance.service_dir == service.resolve()
    assert instance.ioc_yaml == service.resolve() / "config" / "ioc.yaml"
    assert instance.image == IMAGE
    assert instance.name == "fe99i-mo-ioc-01"


def test_resolve_not_a_service(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        resolve(tmp_path)


def test_new_instance(new_service: Path):
    instance = resolve(new_service)
    assert instance.ioc_yaml is not None and not instance.ioc_yaml.exists()
    document = open_document(instance)
    document.save()
    text = instance.ioc_yaml.read_text()
    assert "/-/releases/2026.10.1/downloads/ibek.ioc.schema.json" in text
    assert 'description: "fe99z-mo-ioc-01 IOC"' in text


def test_instance_support_yaml_is_added(service: Path):
    (service / "config" / "extra.ibek.support.yaml").write_text(
        "module: extra\nentity_models:\n"
        "  - name: Thing\n    description: a thing\n    parameters: {}\n"
    )
    catalogue = load_catalogue(resolve(service), defs=DEFS)
    assert "extra.Thing" in catalogue.types


def test_no_image(tmp_path: Path):
    with pytest.raises(ValueError):
        load_catalogue(resolve(None))


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
