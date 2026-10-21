"""The entity types an IOC instance can instantiate, read from support YAML.

The YAML is parsed with ibek's own ``Support`` model, so shared anchors and
parameter types behave exactly as they do when ibek builds the IOC.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ibek.parameters import EnumParam
from ibek.support import Support
from ruamel.yaml import YAML

from .image import ImageRef, cached_files, fetch_ibek_defs

__all__ = [
    "Catalogue",
    "EntityType",
    "Param",
    "from_defs",
    "from_generic_ioc",
    "from_image",
]

SUPPORT_GLOB = "*.ibek.support.yaml"
Log = Callable[[str], Any]


@dataclass
class Param:
    name: str
    kind: str  # str int float bool enum object id dict list
    description: str
    default: Any = None
    choices: list[str] = field(default_factory=list[str])

    @property
    def required(self) -> bool:
        return self.default is None

    def default_text(self) -> str:
        if self.default is None:
            return ""
        return str(self.default)


@dataclass
class EntityType:
    module: str
    name: str
    description: str
    params: dict[str, Param]

    @property
    def full_name(self) -> str:
        return f"{self.module}.{self.name}"

    @property
    def id_params(self) -> list[str]:
        return [p.name for p in self.params.values() if p.kind == "id"]


@dataclass
class Catalogue:
    """Every entity type available, keyed on ``module.name``."""

    types: dict[str, EntityType] = field(default_factory=dict[str, EntityType])
    files: dict[str, Path] = field(default_factory=dict[str, Path])  # module->yaml
    source: str = ""
    warnings: list[str] = field(default_factory=list[str])

    @property
    def modules(self) -> dict[str, list[EntityType]]:
        result: dict[str, list[EntityType]] = {}
        for entity_type in self.types.values():
            result.setdefault(entity_type.module, []).append(entity_type)
        return dict(sorted(result.items(), key=lambda item: item[0].lower()))

    def get(self, full_name: str) -> EntityType | None:
        return self.types.get(full_name)

    def add_files(self, paths: Iterable[Path]) -> None:
        """Add support YAML files; a module overrides an earlier one."""
        for path in paths:
            try:
                support = load_support(path)
            except Exception as exc:
                self.warnings.append(f"{path}: {exc}")
                continue
            # a later definition of a module replaces the whole module
            for key in [k for k, t in self.types.items() if t.module == support.module]:
                del self.types[key]
            self.files[support.module] = path
            for model in support.entity_models:
                entity_type = EntityType(
                    module=support.module,
                    name=model.name,
                    description=model.description.strip(),
                    params={
                        name: _make_param(name, param)
                        for name, param in model.parameters.items()
                    },
                )
                self.types[entity_type.full_name] = entity_type

    @property
    def support_files(self) -> list[Path]:
        return list(self.files.values())


def load_support(path: Path) -> Support:
    data = YAML(typ="safe").load(path)
    return Support(**data)


def _make_param(name: str, param: Any) -> Param:
    choices: list[str] = []
    if isinstance(param, EnumParam):
        choices = [str(key) for key in param.values]
    elif param.type == "bool":
        choices = ["True", "False"]
    return Param(
        name=name,
        kind=param.type,
        description=param.description.strip(),
        default=getattr(param, "default", None),
        choices=choices,
    )


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #
def from_defs(folder: Path) -> Catalogue:
    catalogue = Catalogue(source=str(folder))
    catalogue.add_files(sorted(folder.glob(SUPPORT_GLOB)))
    return catalogue


def from_image(image: str, log: Log = print) -> Catalogue:
    defs = fetch_ibek_defs(image, log)
    catalogue = Catalogue(source=image)
    catalogue.add_files(cached_files(defs))
    return catalogue


_ARG = re.compile(r"^\s*ARG\s+(\w+)(?:=(\S*))?", re.MULTILINE)
_COPY = re.compile(r"^\s*COPY\s+(?!--)(\S+)\s+(\S+)", re.MULTILINE)
_ANSIBLE = re.compile(r"^\s*RUN\s+ansible\.sh\s+(\S+)", re.MULTILINE)


def _expand(value: str, args: dict[str, str]) -> str:
    return re.sub(
        r"\$\{(\w+)\}|\$(\w+)",
        lambda m: args.get(m.group(1) or m.group(2), ""),
        value,
    )


def parse_dockerfile(dockerfile: Path) -> tuple[str | None, list[Path]]:
    """The developer base image and the support folders a Dockerfile installs.

    Only folders that are ``COPY``'d and then built with ``ansible.sh`` count,
    taken by path: ``ibek-support-dls/ether_ip_dls`` is not
    ``ibek-support/ether_ip``.
    """
    text = dockerfile.read_text()
    args: dict[str, str] = {}
    for match in _ARG.finditer(text):
        args.setdefault(match.group(1), _expand(match.group(2) or "", args))
    copies: dict[str, Path] = {}
    folders: list[Path] = []
    events: list[tuple[int, str | tuple[str, str]]] = [
        (m.start(), (m.group(1), m.group(2))) for m in _COPY.finditer(text)
    ]
    events += [(m.start(), m.group(1)) for m in _ANSIBLE.finditer(text)]
    for _, value in sorted(events, key=lambda event: event[0]):
        if isinstance(value, tuple):
            source, dest = value
            copies[Path(dest.rstrip("/")).name] = Path(source.rstrip("/"))
        elif value in copies:
            folders.append(copies[value])
    return args.get("DEVELOPER") or None, folders


def git_describe(folder: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(folder), "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def from_generic_ioc(
    folder: Path, expected_tag: str | None = None, log: Log = print
) -> Catalogue:
    """Entities of a generic IOC checkout, for unreleased or dev versions.

    The base image's definitions are pulled from its registry (its runtime
    image), then the checkout's own support YAMLs are layered on top.
    """
    folder = folder.resolve()
    dockerfile = folder / "Dockerfile"
    if not dockerfile.is_file():
        raise FileNotFoundError(f"no Dockerfile in {folder}")
    catalogue = Catalogue(source=str(folder))

    described = git_describe(folder)
    if expected_tag and described and described != expected_tag:
        message = (
            f"{folder.name} checkout is at {described}, "
            f"but the instance pins {expected_tag}"
        )
        catalogue.warnings.append(message)
        log(f"WARNING: {message}")

    developer, folders = parse_dockerfile(dockerfile)
    if developer:
        runtime = ImageRef.parse(developer).with_target("runtime").image
        try:
            catalogue.add_files(cached_files(fetch_ibek_defs(runtime, log)))
        except Exception as exc:
            message = f"base image {runtime}: {exc}"
            catalogue.warnings.append(message)
            log(f"WARNING: {message}")

    epics = folder / "ibek-support" / "_ansible" / "epics.ibek.support.yaml"
    paths = [epics] if epics.is_file() else []
    for support in folders:
        paths.extend(sorted((folder / support).glob(SUPPORT_GLOB)))
    catalogue.add_files(paths)
    return catalogue
