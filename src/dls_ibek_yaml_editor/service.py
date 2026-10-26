"""Locate an IOC instance's files and the entities its generic IOC offers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ibek.pattern_cmds.schema import find_image

from . import catalogue as cat
from .document import Document
from .image import ImageRef, schema_url

__all__ = ["Instance", "load_catalogue", "open_document", "resolve"]


@dataclass
class Instance:
    """Where an ``ioc.yaml`` lives and which image it runs in."""

    ioc_yaml: Path | None
    service_dir: Path | None = None
    image: str | None = None

    @property
    def name(self) -> str:
        if self.service_dir is not None:
            return self.service_dir.name
        if self.ioc_yaml is not None:
            return self.ioc_yaml.parent.name
        return "ioc"


def _is_service(folder: Path) -> bool:
    return any(
        (folder / name).is_file()
        for name in ("values.yaml", "compose.yml", "compose.yaml")
    )


def resolve(target: Path | None) -> Instance:
    """Work out the instance from a service dir, its config dir or an ioc.yaml.

    A service dir need not have a ``config/ioc.yaml`` yet: that is a new
    instance, created on the first save.
    """
    if target is None:
        return Instance(None)
    target = target.resolve()
    if target.is_dir():
        if target.name == "config" and _is_service(target.parent):
            target = target.parent
        service_dir = target if _is_service(target) else None
        if service_dir is None and (target / "ioc.yaml").is_file():
            return resolve(target / "ioc.yaml")
        if service_dir is None:
            raise FileNotFoundError(
                f"{target} has no values.yaml or compose.yml, so is not an IOC "
                "service directory"
            )
        return Instance(
            service_dir / "config" / "ioc.yaml", service_dir, find_image(service_dir)
        )
    service_dir = None
    if target.parent.name == "config" and _is_service(target.parent.parent):
        service_dir = target.parent.parent
    image = find_image(service_dir) if service_dir else None
    return Instance(target, service_dir, image)


def load_catalogue(
    instance: Instance,
    image: str | None = None,
    generic_ioc: Path | None = None,
    defs: Path | None = None,
    log: cat.Log = print,
    refresh: bool = False,
) -> cat.Catalogue:
    """The entity catalogue for ``instance``.

    Precedence: ``defs`` folder, then a ``generic_ioc`` checkout, then the
    image (``image`` or the one pinned by the instance). The instance's own
    ``config/*.ibek.support.yaml`` files are always added on top.
    """
    image = image or instance.image
    if defs is not None:
        catalogue = cat.from_defs(defs)
    elif generic_ioc is not None:
        tag = ImageRef.parse(image).tag if image else None
        catalogue = cat.from_generic_ioc(generic_ioc, tag, log, refresh)
    elif image is not None:
        catalogue = cat.from_image(image, log, refresh)
    else:
        raise ValueError(
            "no image to read entities from: give a service directory with a "
            "values.yaml, or --image, --generic-ioc or --defs"
        )
    if instance.ioc_yaml is not None:
        local = sorted(instance.ioc_yaml.parent.glob(cat.SUPPORT_GLOB))
        catalogue.add_files(local)
    return catalogue


def open_document(instance: Instance, image: str | None = None) -> Document:
    """Load the instance's ioc.yaml, or start a new one if it does not exist.

    A new file's ``$schema`` header points at the schema published for the
    pinned image tag, so editors can validate it outside the container.
    """
    if instance.ioc_yaml is not None and instance.ioc_yaml.exists():
        return Document.load(instance.ioc_yaml)
    image = image or instance.image
    url = schema_url(image) if image else None
    return Document.new(instance.ioc_yaml, url, f"{instance.name} IOC")
