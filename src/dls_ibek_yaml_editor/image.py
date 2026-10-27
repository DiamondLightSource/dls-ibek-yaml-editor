"""Fetch image support definitions over the registry API and cache snapshots.

All layers are merged in image order to preserve inherited definitions,
replacements and deletions. Complete immutable snapshots are published via
an atomic pointer so failed refreshes leave the last good cache usable.
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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import truststore
from ibek.pattern_cmds.schema import parse_image_ref, published_schema_url

from .file_io import atomic_write_text
from .image_layers import DEFS_DIR, DefinitionLayers

__all__ = [
    "DEFS_DIR",
    "ImageError",
    "ImageRef",
    "fetch_ibek_defs",
    "schema_url",
]

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
TIMEOUT = 10


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
    try:
        return urllib.request.urlopen(  # noqa: S310
            request, timeout=TIMEOUT, context=_ssl_context()
        )
    except urllib.error.HTTPError as exc:
        exc.close()  # callers use status/headers, never the response body
        raise


def _docker_credentials(registry: str) -> str | None:
    """Basic credentials for ``registry`` from ``~/.docker/config.json``."""
    config = Path(os.getenv("DOCKER_CONFIG", Path.home() / ".docker"))
    try:
        data = json.loads((config / "config.json").read_text())
        auths = data.get("auths", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        auths = {}
    if not isinstance(auths, dict):
        auths = {}
    for key, value in auths.items():
        if (
            key.removeprefix("https://").rstrip("/") == registry
            and isinstance(value, dict)
            and isinstance(value.get("auth"), str)
        ):
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
            exc.close()  # also close responses supplied by alternative transports
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


def _read_meta(cache: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads((cache / ".meta.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("layout_version") != 2:
        return None
    files = meta.get("files")
    if not isinstance(files, list) or not files:
        return None
    if not all(
        isinstance(name, str)
        and Path(name).name == name
        and name.endswith(".ibek.support.yaml")
        and (cache / name).is_file()
        for name in files
    ):
        return None
    return meta


def fetch_ibek_defs(
    image: str, log: Callable[[str], None] = print, refresh: bool = False
) -> Path:
    """Return a local folder holding ``image``'s ``/epics/ibek-defs`` files.

    A cached tag is used without asking the registry: release tags don't
    change, and the registry may be slow or unreachable from a workstation.
    ``refresh`` (or a ``latest`` tag) checks the registry for a new digest.

    Raises ImageError when nothing can be fetched and nothing is cached.
    """
    ref = ImageRef.parse(image)
    root = _cache_dir(ref)
    cache = root
    try:
        name = (root / ".current").read_text().strip()
        if Path(name).name == name and name.startswith("snapshot-"):
            cache = root / name
    except OSError:
        pass
    meta = _read_meta(cache)
    if meta is not None and not refresh and ref.tag != "latest":
        return cache
    registry = Registry(ref)
    try:
        digest, manifest = registry.manifest()
    except (urllib.error.URLError, OSError, ValueError, KeyError, ImageError) as exc:
        if meta is not None:
            log(f"Using cached ibek definitions for {image} ({exc})")
            return cache
        raise ImageError(f"cannot read the manifest of {image}: {exc}") from exc

    if meta is not None and meta.get("digest") == digest:
        return cache

    log(f"Fetching ibek definitions from {image} ...")
    try:
        root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=root, prefix="snapshot-"))
        published = False
        try:
            layers = DefinitionLayers()
            for layer in manifest.get("layers", []):
                with registry.get(f"blobs/{layer['digest']}") as response:
                    layers.apply(response)
            found = layers.write(tmp)
            (tmp / ".meta.json").write_text(
                json.dumps(
                    {
                        "image": image,
                        "digest": digest,
                        "files": found,
                        "layout_version": 2,
                    }
                )
            )
            atomic_write_text(root / ".current", tmp.name)
            published = True
            return tmp
        finally:
            if not published:
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
