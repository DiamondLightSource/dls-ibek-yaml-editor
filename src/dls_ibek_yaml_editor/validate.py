"""Validate a document with ibek's own IOC model, as ``ibek runtime generate`` would."""

from __future__ import annotations

import contextlib
import io
import warnings
from dataclasses import dataclass
from typing import Any

from ibek.entity_factory import EntityFactory
from ibek.ioc import clear_entity_model_ids
from ibek.ioc_factory import IocFactory
from ibek.utils import UTILS
from pydantic import ValidationError

from .catalogue import Catalogue
from .document import Document

__all__ = ["Problem", "validate"]

JINJA_MISMATCH = "String should match pattern"


@dataclass
class Problem:
    entity: int | None  # index into the document's entities
    field: str | None
    message: str

    def __str__(self) -> str:
        where = "" if self.entity is None else f"entity {self.entity + 1}"
        if self.field:
            where += f" {self.field}"
        return f"{where.strip()}: {self.message}" if where else self.message


def validate(document: Document, catalogue: Catalogue, ioc_name: str) -> list[Problem]:
    """All problems ibek finds in ``document``; an empty list means valid."""
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), _quiet():
            factory = EntityFactory()
            models = factory.make_entity_models(catalogue.support_files)
            ioc_model = IocFactory().make_ioc_model(models)
    except Exception as exc:
        return [Problem(None, None, f"cannot build the ibek model: {exc}")]

    data = document.plain()
    data["ioc_name"] = ioc_name
    data.setdefault("description", "")
    UTILS.set_ioc_name(ioc_name)
    clear_entity_model_ids()
    try:
        with contextlib.redirect_stdout(output), _quiet():
            ioc_model(**data)
    except ValidationError as exc:
        return _without_jinja_noise([_problem(error) for error in exc.errors()])
    except Exception as exc:
        return [Problem(None, None, str(exc))]
    finally:
        clear_entity_model_ids()
    return []


def _without_jinja_noise(problems: list[Problem]) -> list[Problem]:
    """Drop "should match the Jinja pattern" when the field has a real error too.

    Typed parameters accept a value or a Jinja string, so a bad int is
    reported once for each half of that union.
    """
    located = [(p.entity, p.field) for p in problems]
    return [
        p
        for p in problems
        if not (
            p.message.startswith(JINJA_MISMATCH)
            and located.count((p.entity, p.field)) > 1
        )
    ]


@contextlib.contextmanager
def _quiet():
    """Hide ibek's own deprecation warnings (e.g. pydantic's model_fields)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        yield


def _problem(error: Any) -> Problem:
    loc = list(error.get("loc", ()))
    entity: int | None = None
    field: str | None = None
    if len(loc) >= 2 and loc[0] == "entities" and isinstance(loc[1], int):
        entity = loc[1]
        rest = [str(part) for part in loc[2:]]
        # loc[2] is the discriminator tag (the entity type), then the field
        if len(rest) >= 2:
            field = rest[1]
        elif len(rest) == 1 and "." not in rest[0]:
            field = rest[0]
    message = str(error.get("msg", error))
    return Problem(entity, field, message)
