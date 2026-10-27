import subprocess
import sys
from pathlib import Path

import pytest

from dls_ibek_yaml_editor import __version__
from dls_ibek_yaml_editor.__main__ import main

from .conftest import DEFS


def test_cli_version():
    cmd = [sys.executable, "-m", "dls_ibek_yaml_editor", "--version"]
    assert subprocess.check_output(cmd).decode().strip() == __version__


def test_list_entities(service: Path, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        main([str(service), "--defs", str(DEFS), "--list-entities"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "motorPLC  (" in out
    assert "  NX102_MotorAxisPot\n" in out
    assert "    controller: object = required\n" in out


def test_validate_ok(service: Path, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        main([str(service), "--defs", str(DEFS), "--validate"])
    assert exit_info.value.code == 0
    assert "0 problem(s)" in capsys.readouterr().err


def test_validate_bad(service: Path, capsys: pytest.CaptureFixture[str]):
    ioc_yaml = service / "config" / "ioc.yaml"
    ioc_yaml.write_text(
        ioc_yaml.read_text().replace("controller: MC1", "controller: X")
    )
    with pytest.raises(SystemExit) as exit_info:
        main([str(service), "--defs", str(DEFS), "--validate"])
    assert exit_info.value.code == 1
    assert "object X not found" in capsys.readouterr().out


def test_validate_malformed_entities_fails_without_rewriting(
    service: Path, capsys: pytest.CaptureFixture[str]
):
    path = service / "config" / "ioc.yaml"
    original = "entities: {type: epics.EpicsEnvSet}\n"
    path.write_text(original)
    with pytest.raises(SystemExit) as exit_info:
        main([str(service), "--defs", str(DEFS), "--validate"])
    assert exit_info.value.code == 1
    assert "entities must be a YAML sequence" in capsys.readouterr().err
    assert path.read_text() == original
