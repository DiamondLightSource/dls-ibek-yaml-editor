import shutil
from pathlib import Path
from typing import Any

import pytest

from dls_ibek_yaml_editor import catalogue as cat
from dls_ibek_yaml_editor.catalogue import Catalogue, parse_dockerfile

from .conftest import DATA, DEFS


def test_modules(catalogue: Catalogue):
    assert set(catalogue.modules) == {
        "asyn",
        "autosave",
        "devIocStats",
        "epics",
        "ether_ip",
        "motor",
        "motorPLC",
        "pvlogging",
    }
    assert [t.name for t in catalogue.modules["motorPLC"]] == [
        "NX102_MotorController",
        "NX102_MotorAxis",
        "NX102_MotorAxisPot",
        "NX102_MotorAxisSpare",
    ]


def test_params(catalogue: Catalogue):
    axis = catalogue.get("motorPLC.NX102_MotorAxisPot")
    assert axis is not None
    assert axis.params["controller"].kind == "object"
    assert axis.params["controller"].required
    assert axis.params["current"].default == 1000
    assert axis.params["home_method"].choices[0] == "Negative Limit Falling Edge"
    controller = catalogue.get("motorPLC.NX102_MotorController")
    assert controller is not None
    assert controller.id_params == ["name"]


def test_later_module_replaces_earlier(tmp_path: Path, catalogue: Catalogue):
    override = tmp_path / "motorPLC.ibek.support.yaml"
    override.write_text(
        "module: motorPLC\nentity_models:\n"
        "  - name: Only\n    description: one\n    parameters: {}\n"
    )
    catalogue.add_files([override])
    assert [t.name for t in catalogue.modules["motorPLC"]] == ["Only"]


def test_bad_file_is_a_warning(tmp_path: Path, catalogue: Catalogue):
    bad = tmp_path / "bad.ibek.support.yaml"
    bad.write_text("module: bad\nentity_models: 3\n")
    catalogue.add_files([bad])
    assert "bad" not in catalogue.modules
    assert str(bad) in catalogue.warnings[0]


def test_parse_dockerfile():
    developer, folders = parse_dockerfile(DATA / "Dockerfile.dlsmotorplc")
    assert developer == "ghcr.io/epics-containers/ioc-asyn-developer:4.45ec3"
    assert Path("ibek-support-dls/ether_ip_dls") in folders
    assert Path("ibek-support-dls/motorPLC") in folders
    assert Path("ibek-support/ether_ip") not in folders  # chosen by path
    assert Path("ibek-support/_ansible") not in folders  # copied, not built


def test_from_generic_ioc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    checkout = tmp_path / "ioc-dlsmotorplc"
    shutil.copy(DATA / "Dockerfile.dlsmotorplc", checkout.parent / "Dockerfile")
    checkout.mkdir()
    shutil.move(checkout.parent / "Dockerfile", checkout / "Dockerfile")
    dls = checkout / "ibek-support-dls" / "motorPLC"
    dls.mkdir(parents=True)
    shutil.copy(DEFS / "motorPLC.ibek.support.yaml", dls)
    base = tmp_path / "base"
    base.mkdir()
    shutil.copy(DEFS / "iocStats.ibek.support.yaml", base)

    fetched: list[str] = []

    def fake_fetch(image: str, log: Any = print, refresh: bool = False) -> Path:
        fetched.append(image)
        return base

    monkeypatch.setattr(cat, "fetch_ibek_defs", fake_fetch)
    monkeypatch.setattr(cat, "git_describe", lambda _: "2026.9.1")
    result = cat.from_generic_ioc(checkout, "2026.10.1", log=lambda _: None)
    assert fetched == ["ghcr.io/epics-containers/ioc-asyn-runtime:4.45ec3"]
    assert {"devIocStats", "motorPLC"} <= set(result.modules)
    assert "2026.9.1" in result.warnings[0]  # tag drift is a warning only
