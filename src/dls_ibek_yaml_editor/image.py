"""Fetch a generic IOC image's ``/epics/ibek-defs`` straight from its registry.

A generic IOC's runtime image carries every ``*.ibek.support.yaml`` it was
built with, including those inherited from its base image, in
``/epics/ibek-defs``. This module reads them over the OCI distribution API
without docker or podman:

1. get a pull token (anonymous, or from ``~/.docker/config.json``)
2. read the manifest and the image config
3. stream the layer that ``COPY``'d ``/assets`` through gunzip/untar, stopping
   as soon as the ``epics/ibek-defs/`` entries have gone by

The result is cached per image under ``~/.cache/dls-ibek-yaml-editor`` and
keyed on the manifest digest, so later runs need one small request, or none
at all when the registry cannot be reached.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import ssl
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import truststore
from ibek.pattern_cmds.schema import parse_image_ref, published_schema_url

__all__ = [
    "DEFS_DIR",
    "ImageError",
    "ImageRef",
    "fetch_ibek_defs",
    "schema_url",
]

DEFS_DIR = "epics/ibek-defs/"
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
IMAGE_SUFFIX = re.compile(r"(-linux|-rtems-beatnik)?(-developer|-runtime)$")
TIMEOUT = 30


class ImageError(Exception):
    """The ibek definitions could not be fetched and nothing is cached."""


@dataclass(frozen=True)
class ImageRef:
    """A parsed image reference, normalised for the registry API."""

    image: str
    registry: str
    repository: str
    tag: str

    @classmethod
    def parse(cls, image: str) -> ImageRef:
        try:
            ref = parse_image_ref(image)
        except Exception as exc:
            raise ImageError(str(exc)) from exc
        host = ref.host or "docker.io"
        path = list(ref.path)
        if host == "docker.io":
            host = "registry-1.docker.io"
            if len(path) == 1:
                path.insert(0, "library")
        return cls(image.strip(), host, "/".join(path), ref.tag)

    @property
    def project_path(self) -> str:
        """The source project, i.e. the repository less its image name.

        ``controls/containers/iocs/ioc-x/ioc-x-linux-runtime`` ->
        ``controls/containers/iocs/ioc-x``
        """
        parts = self.repository.split("/")
        base = IMAGE_SUFFIX.sub("", parts[-1])
        if len(parts) > 1 and parts[-2] == base:
            return "/".join(parts[:-1])
        return "/".join([*parts[:-1], base])

    def with_target(self, target: str) -> ImageRef:
        """The same image with its -developer/-runtime suffix replaced."""
        parts = self.repository.split("/")
        name = re.sub(r"-(developer|runtime)$", f"-{target}", parts[-1])
        repository = "/".join([*parts[:-1], name])
        image = f"{self.registry}/{repository}:{self.tag}"
        return ImageRef(image, self.registry, repository, self.tag)


def schema_url(image: str) -> str | None:
    """The URL of the ``ibek.ioc.schema.json`` published for ``image``'s tag.

    GitHub generic IOCs attach it to a GitHub release, GitLab ones link it
    from a GitLab release (see the ``publish_schema`` CI job).
    """
    try:
        ref = ImageRef.parse(image)
    except ImageError:
        return None
    host = ref.registry.partition(":")[0]
    if "gitlab" in host.split("."):
        return (
            f"https://{host}/{ref.project_path}/-/releases/{ref.tag}"
            "/downloads/ibek.ioc.schema.json"
        )
    try:
        return published_schema_url(image)
    except Exception:
        return None


def cache_root() -> Path:
    default = Path.home() / ".cache" / "dls-ibek-yaml-editor"
    return Path(os.getenv("DLS_IBEK_YAML_EDITOR_CACHE", default))


def _cache_dir(ref: ImageRef) -> Path:
    host = ref.registry.replace(":", "_")
    return cache_root().joinpath("defs", host, *ref.repository.split("/"), ref.tag)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _ssl_context() -> ssl.SSLContext:
    # the OS trust store, as ibek does: uv's python can't find RHEL's CAs
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _open(
    url: str, headers: dict[str, str] | None = None, auth: str | None = None
) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    if auth:
        # unredirected: blobs redirect to object storage, which rejects it
        request.add_unredirected_header("Authorization", auth)
    return urllib.request.urlopen(  # noqa: S310
        request, timeout=TIMEOUT, context=_ssl_context()
    )


def _docker_credentials(registry: str) -> str | None:
    """Basic credentials for ``registry`` from ``~/.docker/config.json``."""
    config = Path(os.getenv("DOCKER_CONFIG", Path.home() / ".docker"))
    try:
        auths = json.loads((config / "config.json").read_text()).get("auths", {})
    except (OSError, ValueError):
        return None
    for key, value in auths.items():
        if key.removeprefix("https://").rstrip("/") == registry and "auth" in value:
            return f"Basic {value['auth']}"
    user, password = os.getenv("CI_REGISTRY_USER"), os.getenv("CI_REGISTRY_PASSWORD")
    if user and password and os.getenv("CI_REGISTRY") == registry:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return f"Basic {token}"
    return None


class Registry:
    """A minimal pull-only client for the OCI distribution API."""

    def __init__(self, ref: ImageRef):
        self.ref = ref
        self.base = f"https://{ref.registry}/v2/{ref.repository}"
        self._auth: str | None = None
        self._authenticated = False

    def _token(self, challenge: str) -> str | None:
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        if not challenge.lower().startswith("bearer") or "realm" not in params:
            return _docker_credentials(self.ref.registry)
        query = urllib.parse.urlencode(
            {
                "service": params.get("service", ""),
                "scope": f"repository:{self.ref.repository}:pull",
            }
        )
        credentials = _docker_credentials(self.ref.registry)
        with _open(f"{params['realm']}?{query}", auth=credentials) as response:
            body = json.loads(response.read())
        token = body.get("token") or body.get("access_token")
        return f"Bearer {token}" if token else None

    def get(self, path: str, headers: dict[str, str] | None = None) -> Any:
        url = f"{self.base}/{path}"
        try:
            return _open(url, headers, self._auth)
        except urllib.error.HTTPError as exc:
            if exc.code != 401 or self._authenticated:
                raise
            self._authenticated = True
            self._auth = self._token(exc.headers.get("WWW-Authenticate", ""))
            return _open(url, headers, self._auth)

    def manifest(self) -> tuple[str, dict[str, Any]]:
        """The image manifest and its digest, choosing linux/amd64 from an index."""
        reference = self.ref.tag
        for _ in range(2):
            with self.get(f"manifests/{reference}", {"Accept": MANIFEST_TYPES}) as r:
                digest = r.headers.get("Docker-Content-Digest", "")
                manifest = json.loads(r.read())
            if manifest.get("mediaType") not in INDEX_TYPES:
                return digest or reference, manifest
            choices = manifest["manifests"]
            reference = next(
                (
                    m["digest"]
                    for m in choices
                    if m.get("platform", {}).get("os") == "linux"
                    and m.get("platform", {}).get("architecture") == "amd64"
                ),
                choices[0]["digest"],
            )
        raise ImageError(f"{self.ref.image}: nested image index")

    def blob_json(self, digest: str) -> dict[str, Any]:
        with self.get(f"blobs/{digest}") as response:
            return json.loads(response.read())


# --------------------------------------------------------------------------- #
# layer scanning
# --------------------------------------------------------------------------- #
def _layer_order(manifest: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """Layer digests, the one that ``COPY``'d ``/assets`` first, then newest first.

    ``history`` has one entry per Dockerfile step; the ones without
    ``empty_layer`` correspond, in order, to ``manifest["layers"]``.
    """
    layers = [layer["digest"] for layer in manifest.get("layers", [])]
    steps = [h for h in config.get("history", []) if not h.get("empty_layer")]
    preferred = [
        digest
        for digest, step in zip(layers, steps, strict=False)
        if "/assets" in step.get("created_by", "")
        or "ibek-defs" in step.get("created_by", "")
    ]
    rest = [d for d in reversed(layers) if d not in preferred]
    return preferred + rest


def _member_path(name: str) -> str:
    return name.removeprefix("./").lstrip("/")


def _scan_layer(registry: Registry, digest: str, dest: Path) -> list[str]:
    """Extract ``epics/ibek-defs/*`` files from one layer into ``dest``.

    ibek-defs entries are mostly symlinks into the support module sources
    (``/epics/generic-source/ibek-support/<module>/...``), which come earlier
    in the same layer, so every support YAML is held on to until the links are
    resolved. Reading stops once the definitions folder has been passed and
    every link is resolved, so only the head of a large layer is downloaded.
    """
    seen: dict[str, bytes] = {}  # every support YAML in the layer so far
    links: dict[str, str] = {}  # defs file name -> link target path
    found: list[str] = []
    passed = False
    with registry.get(f"blobs/{digest}") as response:
        with tarfile.open(fileobj=response, mode="r|*") as tar:
            for member in tar:
                name = _member_path(member.name)
                inside = name.startswith(DEFS_DIR)
                if not inside and found:
                    passed = True
                file_name = PurePosixPath(name).name
                if member.isfile() and (inside or name.endswith(".ibek.support.yaml")):
                    extracted = tar.extractfile(member)
                    data = extracted.read() if extracted else b""
                    seen[name] = data
                if inside and "/" not in name[len(DEFS_DIR) :]:
                    if file_name.startswith(".wh.") or not file_name:
                        pass
                    elif member.isfile():
                        (dest / file_name).write_bytes(seen[name])
                        found.append(file_name)
                    elif member.issym() or member.islnk():
                        target = member.linkname
                        if member.issym() and not target.startswith("/"):
                            target = str(PurePosixPath(name).parent / target)
                        links[file_name] = _member_path(target)
                        found.append(file_name)
                for link_name, target in list(links.items()):
                    if target in seen:
                        (dest / link_name).write_bytes(seen[target])
                        del links[link_name]
                if passed and not links:
                    break
    # links to files outside this layer (e.g. in a base layer) are dropped
    return [f for f in found if f not in links]


def _read_meta(cache: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads((cache / ".meta.json").read_text())
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def fetch_ibek_defs(image: str, log: Any = print) -> Path:
    """Return a local folder holding ``image``'s ``/epics/ibek-defs`` files.

    Raises ImageError when nothing can be fetched and nothing is cached.
    """
    ref = ImageRef.parse(image)
    cache = _cache_dir(ref)
    meta = _read_meta(cache)
    registry = Registry(ref)
    try:
        digest, manifest = registry.manifest()
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        if meta is not None:
            log(f"Using cached ibek definitions for {image} ({exc})")
            return cache
        raise ImageError(f"cannot read the manifest of {image}: {exc}") from exc

    if meta is not None and meta.get("digest") == digest:
        return cache

    log(f"Fetching ibek definitions from {image} ...")
    try:
        config = registry.blob_json(manifest["config"]["digest"])
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=cache.parent, prefix=".fetch-"))
        try:
            found: list[str] = []
            for layer in _layer_order(manifest, config):
                found = _scan_layer(registry, layer, tmp)
                if found:
                    break
            if not found:
                raise ImageError(f"{image} has no /{DEFS_DIR.rstrip('/')} folder")
            (tmp / ".meta.json").write_text(
                json.dumps({"image": image, "digest": digest, "files": found})
            )
            if cache.exists():
                shutil.rmtree(cache)
            tmp.rename(cache)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except (
        urllib.error.URLError,
        OSError,
        ValueError,
        KeyError,
        tarfile.TarError,
    ) as e:
        if meta is not None:
            log(f"Using cached ibek definitions for {image} ({e})")
            return cache
        raise ImageError(f"cannot fetch ibek definitions from {image}: {e}") from e
    return cache


def cached_files(defs: Path) -> list[Path]:
    """The support YAML files in a folder of ibek definitions, sorted."""
    return sorted(defs.glob("*.ibek.support.yaml"))
