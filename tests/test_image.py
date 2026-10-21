import io
import json
import os
import tarfile
from pathlib import Path
from typing import Any

import pytest

from dls_ibek_yaml_editor import image
from dls_ibek_yaml_editor.image import ImageRef, fetch_ibek_defs, schema_url

from .conftest import IMAGE


def test_parse_gitlab_ref():
    ref = ImageRef.parse(IMAGE)
    assert ref.registry == "gitlab.diamond.ac.uk:5050"
    assert ref.repository == (
        "controls/containers/iocs/ioc-dlsmotorplc/ioc-dlsmotorplc-linux-runtime"
    )
    assert ref.tag == "2026.10.1"
    assert ref.project_path == "controls/containers/iocs/ioc-dlsmotorplc"


def test_parse_docker_hub_ref():
    ref = ImageRef.parse("ubuntu:24.04")
    assert ref.registry == "registry-1.docker.io"
    assert ref.repository == "library/ubuntu"


def test_with_target():
    ref = ImageRef.parse("ghcr.io/epics-containers/ioc-asyn-developer:4.45ec3")
    assert ref.with_target("runtime").image == (
        "ghcr.io/epics-containers/ioc-asyn-runtime:4.45ec3"
    )


def test_schema_urls():
    assert schema_url(IMAGE) == (
        "https://gitlab.diamond.ac.uk/controls/containers/iocs/ioc-dlsmotorplc"
        "/-/releases/2026.10.1/downloads/ibek.ioc.schema.json"
    )
    assert schema_url("ghcr.io/epics-containers/ioc-asyn-runtime:4.45ec3") == (
        "https://github.com/epics-containers/ioc-asyn/releases/download/4.45ec3/"
        "ibek.ioc.schema.json"
    )


def _layer(entries: list[tarfile.TarInfo | tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for entry in entries:
            if isinstance(entry, tarfile.TarInfo):
                tar.addfile(entry)
            else:
                name, data = entry
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _link(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, headers: dict[str, str] | None = None):
        super().__init__(data)
        self.headers = headers or {}


def _fake_registry(
    monkeypatch: pytest.MonkeyPatch, blobs: dict[str, bytes]
) -> list[str]:
    """Serve a one-image registry; returns the list of requested paths."""
    requested: list[str] = []
    manifest = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": "sha256:config"},
        "layers": [{"digest": "sha256:base"}, {"digest": "sha256:assets"}],
    }
    config = {
        "history": [
            {"created_by": "FROM base"},
            {"created_by": "ARG X", "empty_layer": True},
            {"created_by": "COPY /assets / # buildkit"},
        ]
    }

    def get(self: Any, path: str, headers: Any = None) -> FakeResponse:
        requested.append(path)
        if path.startswith("manifests/"):
            return FakeResponse(
                json.dumps(manifest).encode(),
                {"Docker-Content-Digest": "sha256:manifest"},
            )
        digest = path.removeprefix("blobs/")
        if digest == "sha256:config":
            return FakeResponse(json.dumps(config).encode())
        return FakeResponse(blobs[digest])

    monkeypatch.setattr(image.Registry, "get", get)
    return requested


def test_fetch_resolves_symlinks_and_caches(monkeypatch: pytest.MonkeyPatch):
    source = "epics/generic-source/ibek-support/motorPLC/motorPLC.ibek.support.yaml"
    assets = _layer(
        [
            (source, b"module: motorPLC\n"),
            ("epics/ibek-defs/ioc.schema.json", b"{}"),
            _link("epics/ibek-defs/motorPLC.ibek.support.yaml", "/" + source),
            ("epics/runtime/after.txt", b"x"),
        ]
    )
    requested = _fake_registry(
        monkeypatch, {"sha256:assets": assets, "sha256:base": _layer([])}
    )
    defs = fetch_ibek_defs(IMAGE, log=lambda _: None)
    assert (defs / "motorPLC.ibek.support.yaml").read_bytes() == b"module: motorPLC\n"
    # the COPY /assets layer is chosen from the history, so the base is not read
    assert "blobs/sha256:base" not in requested

    requested.clear()
    assert fetch_ibek_defs(IMAGE, log=lambda _: None) == defs
    assert requested == ["manifests/2026.10.1"]  # cache hit: manifest only


def test_fetch_uses_cache_when_offline(monkeypatch: pytest.MonkeyPatch):
    assets = _layer([("epics/ibek-defs/a.ibek.support.yaml", b"module: a\n")])
    _fake_registry(monkeypatch, {"sha256:assets": assets})
    defs = fetch_ibek_defs(IMAGE, log=lambda _: None)

    def offline(self: Any, path: str, headers: Any = None) -> Any:
        raise OSError("no network")

    monkeypatch.setattr(image.Registry, "get", offline)
    messages: list[str] = []
    assert fetch_ibek_defs(IMAGE, log=messages.append) == defs
    assert "cached" in messages[0]


def test_fetch_offline_without_cache_fails(monkeypatch: pytest.MonkeyPatch):
    def offline(self: Any, path: str, headers: Any = None) -> Any:
        raise OSError("no network")

    monkeypatch.setattr(image.Registry, "get", offline)
    with pytest.raises(image.ImageError):
        fetch_ibek_defs(IMAGE, log=lambda _: None)


@pytest.mark.skipif(
    not os.getenv("DLS_IBEK_YAML_EDITOR_LIVE"),
    reason="set DLS_IBEK_YAML_EDITOR_LIVE=1 to test against the real registry",
)
def test_live_registry():
    defs = fetch_ibek_defs(IMAGE, log=lambda _: None)
    names = {p.name for p in Path(defs).glob("*.ibek.support.yaml")}
    assert {"motorPLC.ibek.support.yaml", "iocStats.ibek.support.yaml"} <= names
