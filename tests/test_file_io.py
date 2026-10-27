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


def test_new_file_follows_umask(tmp_path: Path):
    old = os.umask(0o002)
    try:
        path = tmp_path / "config" / "ioc.yaml"
        atomic_write_text(path, "new")
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o664


def test_file_of_another_user_is_written_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "ioc.yaml"
    path.write_text("original")
    inode = path.stat().st_ino
    monkeypatch.setattr(os, "geteuid", lambda: path.stat().st_uid + 1)
    atomic_write_text(path, "replacement")
    assert path.read_text() == "replacement"
    assert path.stat().st_ino == inode  # same file, so its owner is unchanged


def test_unwritable_directory_falls_back_to_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "ioc.yaml"
    path.write_text("original")

    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("no write access to the directory")

    monkeypatch.setattr("tempfile.mkstemp", refuse)
    atomic_write_text(path, "replacement")
    assert path.read_text() == "replacement"
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "new.yaml", "new")
