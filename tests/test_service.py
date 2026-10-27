from pathlib import Path

import pytest

from dls_ibek_yaml_editor.service import find_instance, load_catalogue, open_document

from .conftest import DEFS, IMAGE


@pytest.mark.parametrize("sub", ["", "config", "config/ioc.yaml"])
def test_find_instance(service: Path, sub: str):
    instance = find_instance(service / sub if sub else service)
    assert instance.service_dir == service.resolve()
    assert instance.ioc_yaml == service.resolve() / "config" / "ioc.yaml"
    assert instance.image == IMAGE
    assert instance.name == "fe99i-mo-ioc-01"


def test_find_instance_not_a_service(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        find_instance(tmp_path)


def test_new_instance(new_service: Path):
    instance = find_instance(new_service)
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
    catalogue = load_catalogue(find_instance(service), defs=DEFS)
    assert "extra.Thing" in catalogue.types


def test_no_image(tmp_path: Path):
    with pytest.raises(ValueError):
        load_catalogue(find_instance(None))
