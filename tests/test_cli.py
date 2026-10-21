import subprocess
import sys

from dls_ibek_yaml_editor import __version__


def test_cli_version():
    cmd = [sys.executable, "-m", "dls_ibek_yaml_editor", "--version"]
    assert subprocess.check_output(cmd).decode().strip() == __version__
