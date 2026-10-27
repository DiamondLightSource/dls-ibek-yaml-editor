# pyright: reportPrivateUsage=false
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
    # Base and derived layers both contribute to the final filesystem.
    assert "blobs/sha256:base" in requested

    requested.clear()
    assert fetch_ibek_defs(IMAGE, log=lambda _: None) == defs
    assert requested == []  # cached tag: the registry isn't asked at all

    assert fetch_ibek_defs(IMAGE, log=lambda _: None, refresh=True) == defs
    assert requested == ["manifests/2026.10.1"]  # same digest: manifest only


def test_fetch_uses_cache_when_offline(monkeypatch: pytest.MonkeyPatch):
    assets = _layer([("epics/ibek-defs/a.ibek.support.yaml", b"module: a\n")])
    _fake_registry(monkeypatch, {"sha256:assets": assets, "sha256:base": _layer([])})
    defs = fetch_ibek_defs(IMAGE, log=lambda _: None)

    def offline(self: Any, path: str, headers: Any = None) -> Any:
        raise OSError("no network")

    monkeypatch.setattr(image.Registry, "get", offline)
    messages: list[str] = []
    assert fetch_ibek_defs(IMAGE, log=messages.append, refresh=True) == defs
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


@pytest.mark.parametrize("config", [None, "{broken", "[]", '{"auths": []}', "{}"])
def test_ci_credentials_without_docker_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str | None
):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    monkeypatch.setenv("CI_REGISTRY", "registry.example")
    monkeypatch.setenv("CI_REGISTRY_USER", "dummy")
    monkeypatch.setenv("CI_REGISTRY_PASSWORD", "dummy")
    if config is not None:
        (tmp_path / "config.json").write_text(config)
    assert image._docker_credentials("registry.example") == "Basic ZHVtbXk6ZHVtbXk="
    assert image._docker_credentials("another.example") is None


def test_docker_credentials_take_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    monkeypatch.setenv("CI_REGISTRY", "registry.example")
    monkeypatch.setenv("CI_REGISTRY_USER", "dummy")
    monkeypatch.setenv("CI_REGISTRY_PASSWORD", "dummy")
    (tmp_path / "config.json").write_text(
        json.dumps({"auths": {"https://registry.example/": {"auth": "docker-dummy"}}})
    )
    assert image._docker_credentials("registry.example") == "Basic docker-dummy"


def test_layers_merge_replace_delete_and_resolve_links(monkeypatch: pytest.MonkeyPatch):
    base = _layer(
        [
            ("epics/ibek-defs/base.ibek.support.yaml", b"base"),
            ("epics/ibek-defs/replaced.ibek.support.yaml", b"old"),
            ("epics/ibek-defs/deleted.ibek.support.yaml", b"deleted"),
            ("epics/source/linked.ibek.support.yaml", b"linked"),
        ]
    )
    assets = _layer(
        [
            ("epics/ibek-defs/new.ibek.support.yaml", b"new"),
            ("epics/ibek-defs/replaced.ibek.support.yaml", b"replacement"),
            ("epics/ibek-defs/.wh.deleted.ibek.support.yaml", b""),
            _link(
                "epics/ibek-defs/linked.ibek.support.yaml",
                "../source/linked.ibek.support.yaml",
            ),
        ]
    )
    _fake_registry(monkeypatch, {"sha256:base": base, "sha256:assets": assets})
    defs = fetch_ibek_defs(IMAGE, log=lambda _: None)
    assert {p.name: p.read_bytes() for p in defs.glob("*.yaml")} == {
        "base.ibek.support.yaml": b"base",
        "new.ibek.support.yaml": b"new",
        "replaced.ibek.support.yaml": b"replacement",
        "linked.ibek.support.yaml": b"linked",
    }


def test_opaque_directory_hides_lower_files(monkeypatch: pytest.MonkeyPatch):
    base = _layer([("epics/ibek-defs/base.ibek.support.yaml", b"base")])
    assets = _layer(
        [
            ("epics/ibek-defs/new.ibek.support.yaml", b"new"),
            ("epics/ibek-defs/.wh..wh..opq", b""),
        ]
    )
    _fake_registry(monkeypatch, {"sha256:base": base, "sha256:assets": assets})
    defs = fetch_ibek_defs(IMAGE, log=lambda _: None)
    assert [p.name for p in defs.glob("*.yaml")] == ["new.ibek.support.yaml"]


@pytest.mark.parametrize(
    "entries",
    [
        [("epics/ibek-defs/ioc.schema.json", b"{}")],
        [_link("epics/ibek-defs/missing.ibek.support.yaml", "/missing")],
        [_link("epics/ibek-defs/cycle.ibek.support.yaml", "cycle.ibek.support.yaml")],
    ],
)
def test_incomplete_definitions_fail(
    monkeypatch: pytest.MonkeyPatch, entries: list[Any]
):
    _fake_registry(
        monkeypatch, {"sha256:base": _layer([]), "sha256:assets": _layer(entries)}
    )
    with pytest.raises(image.ImageError):
        fetch_ibek_defs(IMAGE, log=lambda _: None)


@pytest.mark.parametrize("failure", ["download", "publish"])
def test_failed_refresh_keeps_old_snapshot(
    monkeypatch: pytest.MonkeyPatch, failure: str
):
    _fake_registry(
        monkeypatch,
        {
            "sha256:base": _layer([]),
            "sha256:assets": _layer([("epics/ibek-defs/a.ibek.support.yaml", b"old")]),
        },
    )
    old = fetch_ibek_defs(IMAGE, log=lambda _: None)
    original_manifest = image.Registry.manifest

    def changed(self: image.Registry) -> tuple[str, dict[str, Any]]:
        _, manifest = original_manifest(self)
        return "sha256:new", manifest

    monkeypatch.setattr(image.Registry, "manifest", changed)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("simulated failure")

    if failure == "download":
        monkeypatch.setattr(image.Registry, "get", fail)
        monkeypatch.setattr(
            image.Registry,
            "manifest",
            lambda _: ("sha256:new", {"layers": [{"digest": "new"}]}),
        )
    else:
        monkeypatch.setattr(image, "atomic_write_text", fail)
    messages: list[str] = []
    assert fetch_ibek_defs(IMAGE, log=messages.append, refresh=True) == old
    assert (old / "a.ibek.support.yaml").read_bytes() == b"old"
    assert fetch_ibek_defs(IMAGE, log=lambda _: None) == old
    assert "cached" in messages[1]


def test_registry_authentication_retries_challenge(monkeypatch: pytest.MonkeyPatch):
    import urllib.error
    from email.message import Message

    calls: list[tuple[str, str | None]] = []
    headers = Message()
    headers["WWW-Authenticate"] = (
        'Bearer realm="https://auth.example/token",service="registry.example"'
    )

    def open_url(
        url: str, headers: Any = None, auth: str | None = None
    ) -> FakeResponse:
        calls.append((url, auth))
        if len(calls) == 1:
            challenge = Message()
            challenge["WWW-Authenticate"] = (
                'Bearer realm="https://auth.example/token",service="registry.example"'
            )
            raise urllib.error.HTTPError(url, 401, "auth", challenge, None)
        if url.startswith("https://auth.example"):
            return FakeResponse(b'{"token":"dummy-token"}')
        assert auth == "Bearer dummy-token"
        return FakeResponse(b"ok")

    monkeypatch.setattr(image, "_open", open_url)
    monkeypatch.setattr(image, "_docker_credentials", lambda _: "Basic dummy")
    registry = image.Registry(ImageRef.parse("registry.example/team/ioc:1"))
    with registry.get("manifests/1") as response:
        assert response.read() == b"ok"
    assert calls[1][1] == "Basic dummy"
    assert "scope=repository%3Ateam%2Fioc%3Apull" in calls[1][0]


def test_authorization_is_not_forwarded_on_redirect(monkeypatch: pytest.MonkeyPatch):
    import urllib.request
    from http.client import HTTPMessage

    def open_request(request: urllib.request.Request, **kwargs: Any) -> FakeResponse:
        assert request.get_header("Authorization") == "Bearer dummy"
        redirected = urllib.request.HTTPRedirectHandler().redirect_request(
            request,
            io.BytesIO(),
            302,
            "redirect",
            HTTPMessage(),
            "https://storage.example/blob",
        )
        assert redirected is not None
        assert redirected.get_header("Authorization") is None
        return FakeResponse(b"ok")

    monkeypatch.setattr(image.urllib.request, "urlopen", open_request)
    monkeypatch.setattr(image, "_ssl_context", lambda: None)
    with image._open("https://registry.example/blob", auth="Bearer dummy") as response:
        assert response.read() == b"ok"


def test_successful_refresh_retains_previous_catalogue_paths(
    monkeypatch: pytest.MonkeyPatch,
):
    _fake_registry(
        monkeypatch,
        {
            "sha256:base": _layer([]),
            "sha256:assets": _layer([("epics/ibek-defs/a.ibek.support.yaml", b"old")]),
        },
    )
    old = fetch_ibek_defs(IMAGE, log=lambda _: None)
    _fake_registry(
        monkeypatch,
        {
            "sha256:base": _layer([]),
            "sha256:assets": _layer([("epics/ibek-defs/a.ibek.support.yaml", b"new")]),
        },
    )
    original = image.Registry.manifest

    def changed(self: image.Registry) -> tuple[str, dict[str, Any]]:
        _, manifest = original(self)
        return "sha256:new", manifest

    monkeypatch.setattr(image.Registry, "manifest", changed)
    new = fetch_ibek_defs(IMAGE, log=lambda _: None, refresh=True)
    assert new != old
    assert (old / "a.ibek.support.yaml").read_bytes() == b"old"
    assert (new / "a.ibek.support.yaml").read_bytes() == b"new"
    assert fetch_ibek_defs(IMAGE, log=lambda _: None) == new


def test_deleted_cache_file_is_refetched(monkeypatch: pytest.MonkeyPatch):
    calls = _fake_registry(
        monkeypatch,
        {
            "sha256:base": _layer([]),
            "sha256:assets": _layer([("epics/ibek-defs/a.ibek.support.yaml", b"data")]),
        },
    )
    old = fetch_ibek_defs(IMAGE, log=lambda _: None)
    (old / "a.ibek.support.yaml").unlink()
    calls.clear()
    new = fetch_ibek_defs(IMAGE, log=lambda _: None)
    assert calls
    assert (new / "a.ibek.support.yaml").read_bytes() == b"data"


def _legacy_cache(files: list[str]) -> Path:
    """A cache folder as the editor wrote it before snapshots."""
    cache = image._cache_dir(ImageRef.parse(IMAGE))  # pyright: ignore[reportPrivateUsage]
    cache.mkdir(parents=True)
    for name in files:
        (cache / name).write_text("module: a\n")
    meta = {"image": IMAGE, "digest": "sha256:old", "files": files}
    (cache / ".meta.json").write_text(json.dumps(meta))
    return cache


def test_legacy_cache_is_used_without_the_registry(monkeypatch: pytest.MonkeyPatch):
    cache = _legacy_cache(["a.ibek.support.yaml", "ioc.schema.json"])

    def offline(self: Any, path: str, headers: Any = None) -> Any:
        raise AssertionError("the registry must not be asked")

    monkeypatch.setattr(image.Registry, "get", offline)
    assert fetch_ibek_defs(IMAGE, log=lambda _: None) == cache


def test_legacy_cache_is_the_offline_fallback_for_refresh(
    monkeypatch: pytest.MonkeyPatch,
):
    cache = _legacy_cache(["a.ibek.support.yaml"])

    def offline(self: Any, path: str, headers: Any = None) -> Any:
        raise OSError("no network")

    monkeypatch.setattr(image.Registry, "get", offline)
    messages: list[str] = []
    assert fetch_ibek_defs(IMAGE, log=messages.append, refresh=True) == cache
    assert "cached" in messages[0]


def test_incomplete_legacy_cache_is_ignored(monkeypatch: pytest.MonkeyPatch):
    cache = _legacy_cache(["a.ibek.support.yaml"])
    (cache / "a.ibek.support.yaml").unlink()

    def offline(self: Any, path: str, headers: Any = None) -> Any:
        raise OSError("no network")

    monkeypatch.setattr(image.Registry, "get", offline)
    with pytest.raises(image.ImageError):
        fetch_ibek_defs(IMAGE, log=lambda _: None)
