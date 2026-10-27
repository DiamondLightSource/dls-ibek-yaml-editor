"""Read the support-file view of an image without extracting its filesystem.

Layers are applied oldest first. Only support YAML, definition entries and
links are retained, but every member is visited to honour replacements and
whiteouts. Paths are container paths, never host extraction destinations.
"""

import posixpath
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

DEFS_DIR = "epics/ibek-defs/"


class LayerError(ValueError):
    """The image cannot provide a complete, unambiguous definition catalogue."""


@dataclass(frozen=True)
class Link:
    target: str


def container_path(name: str) -> str:
    return posixpath.normpath("/" + name.lstrip("/")).lstrip("/")


class DefinitionLayers:
    """The relevant entries of the merged image filesystem."""

    def __init__(self) -> None:
        self.entries: dict[str, bytes | Link] = {}

    def _remove(self, path: str, *, children_only: bool = False) -> None:
        prefix = path + "/" if path else ""
        for name in list(self.entries):
            if name.startswith(prefix) or (name == path and not children_only):
                del self.entries[name]

    def apply(self, stream: BinaryIO) -> None:
        # Whiteouts remove lower layers only, regardless of tar member order.
        additions: dict[str, bytes | Link] = {}
        with tarfile.open(fileobj=stream, mode="r|*") as tar:
            for member in tar:
                name = container_path(member.name)
                parent, leaf = posixpath.split(name)
                if leaf == ".wh..wh..opq":
                    self._remove(parent, children_only=True)
                    continue
                if leaf.startswith(".wh."):
                    self._remove(posixpath.join(parent, leaf[4:]))
                    continue
                if member.isdir():
                    # A directory can replace an earlier file/link at this path.
                    self.entries.pop(name, None)
                    additions.pop(name, None)
                    continue
                self._remove(name)
                for old in list(additions):
                    if old == name or old.startswith(name + "/"):
                        del additions[old]
                if member.issym():
                    target = member.linkname
                    if not target.startswith("/"):
                        target = posixpath.join(parent, target)
                    additions[name] = Link(container_path(target))
                elif member.islnk():
                    target = container_path(member.linkname)
                    value = additions.get(target, self.entries.get(target))
                    if isinstance(value, bytes):
                        additions[name] = value  # a hard link keeps the current bytes
                    elif name.startswith(DEFS_DIR) or name.endswith(
                        ".ibek.support.yaml"
                    ):
                        raise LayerError(f"unresolved hard link: {name} -> {target}")
                elif member.isfile() and (
                    name.startswith(DEFS_DIR) or name.endswith(".ibek.support.yaml")
                ):
                    source = tar.extractfile(member)
                    if source is not None:
                        additions[name] = source.read()
        self.entries.update(additions)

    def _resolve(self, name: str, visited: frozenset[str] = frozenset()) -> bytes:
        if name in visited:
            raise LayerError(f"cyclic definition link: {name}")
        value = self.entries.get(name)
        if isinstance(value, bytes):
            return value
        if isinstance(value, Link):
            return self._resolve(value.target, visited | {name})
        # Resolve a symlink in a parent directory of a file target.
        parts = name.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            link = self.entries.get(parent)
            if isinstance(link, Link):
                target = posixpath.join(link.target, *parts[index:])
                return self._resolve(target, visited | {name})
        raise LayerError(f"missing definition link target: {name}")

    def write(self, destination: Path) -> list[str]:
        # Enumerating a symlinked definitions directory requires a full view
        # of unrelated files; diagnose that layout rather than omit entries.
        for parent in ("epics", DEFS_DIR.rstrip("/")):
            if isinstance(self.entries.get(parent), Link):
                raise LayerError(
                    f"symlinked definitions directory is unsupported: {parent}"
                )
        names = sorted(
            name
            for name in self.entries
            if name.startswith(DEFS_DIR)
            and "/" not in name[len(DEFS_DIR) :]
            and name.endswith(".ibek.support.yaml")
        )
        if not names:
            raise LayerError("image has no support YAML in /epics/ibek-defs")
        for name in names:
            (destination / posixpath.basename(name)).write_bytes(self._resolve(name))
        return [posixpath.basename(name) for name in names]
