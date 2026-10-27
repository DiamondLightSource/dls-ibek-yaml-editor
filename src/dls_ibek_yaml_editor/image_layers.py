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

    def apply(self, stream: BinaryIO, stop_when_complete: bool = False) -> None:
        """Apply one layer on top of the entries so far.

        ``stop_when_complete`` stops reading once the definitions folder has
        gone by and every definition resolves, so only the head of a large
        layer is downloaded. Only use it for a layer that alone holds the
        whole definitions folder: entries after the stop are not seen.
        """
        # Whiteouts remove lower layers only, regardless of tar member order.
        additions: dict[str, bytes | Link] = {}
        seen_defs = passed = changed = False
        with tarfile.open(fileobj=stream, mode="r|*") as tar:
            for member in tar:
                if stop_when_complete:
                    inside = container_path(member.name).startswith(DEFS_DIR)
                    seen_defs |= inside
                    passed |= seen_defs and not inside
                    if passed and changed:
                        changed = False
                        if _complete({**self.entries, **additions}):
                            break
                before = len(additions)
                self._apply_member(tar, member, additions)
                changed |= len(additions) != before
        self.entries.update(additions)

    def _apply_member(
        self,
        tar: tarfile.TarFile,
        member: tarfile.TarInfo,
        additions: dict[str, bytes | Link],
    ) -> None:
        name = container_path(member.name)
        parent, leaf = posixpath.split(name)
        if leaf == ".wh..wh..opq":
            self._remove(parent, children_only=True)
            return
        if leaf.startswith(".wh."):
            self._remove(posixpath.join(parent, leaf[4:]))
            return
        if member.isdir():
            # A directory can replace an earlier file/link at this path.
            self.entries.pop(name, None)
            additions.pop(name, None)
            return
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
            elif name.startswith(DEFS_DIR) or name.endswith(".ibek.support.yaml"):
                raise LayerError(f"unresolved hard link: {name} -> {target}")
        elif member.isfile() and (
            name.startswith(DEFS_DIR) or name.endswith(".ibek.support.yaml")
        ):
            source = tar.extractfile(member)
            if source is not None:
                additions[name] = source.read()

    def _resolve(self, name: str) -> bytes:
        return _resolve(self.entries, name)

    def write(self, destination: Path) -> list[str]:
        # Enumerating a symlinked definitions directory requires a full view
        # of unrelated files; diagnose that layout rather than omit entries.
        for parent in ("epics", DEFS_DIR.rstrip("/")):
            if isinstance(self.entries.get(parent), Link):
                raise LayerError(
                    f"symlinked definitions directory is unsupported: {parent}"
                )
        names = _definition_names(self.entries)
        if not names:
            raise LayerError("image has no support YAML in /epics/ibek-defs")
        for name in names:
            (destination / posixpath.basename(name)).write_bytes(self._resolve(name))
        return [posixpath.basename(name) for name in names]


def _definition_names(entries: dict[str, bytes | Link]) -> list[str]:
    return sorted(
        name
        for name in entries
        if name.startswith(DEFS_DIR)
        and "/" not in name[len(DEFS_DIR) :]
        and name.endswith(".ibek.support.yaml")
    )


def _resolve(
    entries: dict[str, bytes | Link], name: str, visited: frozenset[str] = frozenset()
) -> bytes:
    if name in visited:
        raise LayerError(f"cyclic definition link: {name}")
    value = entries.get(name)
    if isinstance(value, bytes):
        return value
    if isinstance(value, Link):
        return _resolve(entries, value.target, visited | {name})
    # Resolve a symlink in a parent directory of a file target.
    parts = name.split("/")
    for index in range(1, len(parts)):
        parent = "/".join(parts[:index])
        link = entries.get(parent)
        if isinstance(link, Link):
            target = posixpath.join(link.target, *parts[index:])
            return _resolve(entries, target, visited | {name})
    raise LayerError(f"missing definition link target: {name}")


def _complete(entries: dict[str, bytes | Link]) -> bool:
    """Every definition resolves within ``entries``."""
    names = _definition_names(entries)
    try:
        for name in names:
            _resolve(entries, name)
    except LayerError:
        return False
    return bool(names)
