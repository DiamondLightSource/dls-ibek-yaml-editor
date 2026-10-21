import shutil
from pathlib import Path

import pytest

from dls_ibek_yaml_editor.catalogue import Catalogue, from_defs

DATA = Path(__file__).parent / "data"
DEFS = DATA / "defs"
SERVICE = DATA / "fe99i-mo-ioc-01"
IMAGE = (
    "gitlab.diamond.ac.uk:5050/controls/containers/iocs/ioc-dlsmotorplc/"
    "ioc-dlsmotorplc-linux-runtime:2026.10.1"
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DLS_IBEK_YAML_EDITOR_CACHE", str(tmp_path / "cache"))


@pytest.fixture
def catalogue() -> Catalogue:
    return from_defs(DEFS)


@pytest.fixture
def service(tmp_path: Path) -> Path:
    """A writable copy of the fe99i service directory."""
    dest = tmp_path / "fe99i-mo-ioc-01"
    shutil.copytree(SERVICE, dest)
    return dest


@pytest.fixture
def new_service(tmp_path: Path) -> Path:
    """A service directory with a values.yaml but no config/ioc.yaml yet."""
    dest = tmp_path / "fe99z-mo-ioc-01"
    dest.mkdir()
    shutil.copy(SERVICE / "values.yaml", dest / "values.yaml")
    return dest
