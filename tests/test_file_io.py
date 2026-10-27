"""A failed save must leave the original file and document path intact."""

import os
import stat
from pathlib import Path

import pytest

from dls_ibek_yaml_editor.document import Document
from dls_ibek_yaml_editor.file_io import atomic_write_text


def test_save_preserves_symlink_and_permissions(tmp_path: Path):
    target = tmp_path / "target.yaml"
    target.write_text("original")
    target.chmod(0o640)
    link = tmp_path / "ioc.yaml"
    link.symlink_to(target)
    atomic_write_text(link, "replacement")
    assert link.is_symlink()
    assert target.read_text() == "replacement"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_failed_save_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
):
    path = tmp_path / "ioc.yaml"
    original = "ioc_name: test\nentities: []\n"
    path.write_text(original)
    document = Document(original)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("simulated full disk")

    if failure == "write":
        monkeypatch.setattr(os, "fsync", fail)
    else:
        monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="simulated"):
        document.save(path)
    assert document.path is None
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]
