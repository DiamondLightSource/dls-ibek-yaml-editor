"""An ``ioc.yaml`` held as round-trip YAML to retain comments and scalar styles.

Comments, quoting, Jinja strings and the ``# yaml-language-server`` header all
are retained where the YAML emitter permits; formatting may be normalised.

A commented-out entity, as VS Code's Ctrl+/ writes it::

      # - type: motorPLC.NX102_MotorAxisPot
      #   name: MOT2

is loaded as a real entity marked commented (xeb's "X" column). On save its
lines are commented out again with the prefix they had.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from .catalogue import Catalogue, EntityType, Param
from .file_io import atomic_write_text
from .yaml_format import (
    ENTITY_INDENT,
    comment_entities,
    is_blank,
    last_token,
    normalise_separators,
    uncomment_entities,
)

__all__ = ["COMMENTED", "MISSING", "Document", "new_ioc_text", "text_to_value"]

# the pseudo-key of the "X" column: the entity is commented out
COMMENTED = "__commented__"

IOC_NAME_JINJA = "{{ _global.get_env('IOC_NAME') }}"


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def new_ioc_text(schema_url: str | None, description: str) -> str:
    """The text of a new, empty ``ioc.yaml``."""
    header = f"# yaml-language-server: $schema={schema_url}\n\n" if schema_url else ""
    description = json.dumps(description)
    return (
        f"{header}"
        f'ioc_name: "{IOC_NAME_JINJA}"\n\n'
        f"description: {description}\n\n"
        "entities:\n"
    )


def text_to_value(text: str, param: Param | None) -> Any:
    """Convert a cell's text to the value to store, MISSING to remove the key."""
    if text == "":
        return MISSING
    kind = param.kind if param else "str"
    if kind == "int":
        try:
            return int(text)
        except ValueError:
            return text  # jinja
    if kind == "float":
        try:
            return float(text)
        except ValueError:
            return text
    if kind == "bool":
        if text.lower() in ("true", "yes", "1"):
            return True
        if text.lower() in ("false", "no", "0"):
            return False
        return text
    if kind in ("dict", "list") and not text.lstrip().startswith("{{"):
        try:
            value = YAML(typ="safe").load(text)
        except YAMLError:  # malformed YAML remains editable for ibek to diagnose
            return text
        expected = dict if kind == "dict" else list
        if isinstance(value, expected):
            return value
    return text


def value_to_text(value: Any) -> str:
    if value is MISSING or value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class Document:
    """An ``ioc.yaml``: header fields plus an ordered list of entities."""

    def __init__(self, text: str, path: Path | None = None):
        self.path = path
        self._yaml = _yaml()
        text, self.indent, prefixes = uncomment_entities(text)
        data = self._yaml.load(text)
        if not isinstance(data, CommentedMap):
            raise ValueError(f"{path or 'ioc.yaml'} is not a YAML mapping")
        self.data: CommentedMap = data
        entities = data.get("entities")
        if entities is None:
            entities = CommentedSeq()
            data["entities"] = entities
        if not isinstance(entities, CommentedSeq):
            raise ValueError(
                "entities must be a YAML sequence (or null for an empty IOC)"
            )
        for index, entity in enumerate(entities):
            if not isinstance(entity, CommentedMap):
                raise ValueError(f"entity {index + 1} must be a YAML mapping")
            if not isinstance(entity.get("type"), str) or not entity["type"].strip():
                raise ValueError(
                    f"entity {index + 1} must have a non-empty string type"
                )
        for key in ("ioc_name", "description"):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"{key} must be a string")
        entities.fa.set_block_style()
        # commented-out entities, by id(), with their comment prefix
        self._commented: dict[int, tuple[CommentedMap, str]] = {
            id(entities[i]): (entities[i], prefix)
            for i, prefix in prefixes.items()
            if i < len(entities)
        }
        # blank lines between entities: keep whatever the file did
        self.separated = any(
            is_blank(last_token(e)) for e in entities[:-1] if isinstance(e, dict)
        )
        if len(entities) < 2:
            self.separated = True
        self.trailing_blank = bool(entities) and is_blank(last_token(entities[-1]))

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, path: Path) -> Document:
        return cls(path.read_text(), path)

    @classmethod
    def new(
        cls, path: Path | None, schema_url: str | None, description: str
    ) -> Document:
        return cls(new_ioc_text(schema_url, description), path)

    def dumps(self) -> str:
        normalise_separators(self.entities, self.separated, self.trailing_blank)
        stream = io.StringIO()
        self._yaml.dump(self.data, stream)
        prefixes = {
            i: self._commented[id(e)][1]
            for i, e in enumerate(self.entities)
            if id(e) in self._commented
        }
        return comment_entities(stream.getvalue(), ENTITY_INDENT, prefixes)

    def save(self, path: Path | None = None) -> None:
        path = path or self.path
        if path is None:
            raise ValueError("no file name")
        atomic_write_text(path, self.dumps())
        self.path = path

    # ------------------------------------------------------------- content
    @property
    def entities(self) -> CommentedSeq:
        return self.data["entities"]

    def entity_type(self, entity: Any) -> str:
        return str(entity.get("type", "")) if isinstance(entity, dict) else ""

    def index_of(self, entity: CommentedMap) -> int:
        for index, candidate in enumerate(self.entities):
            if candidate is entity:
                return index
        raise ValueError("entity is not in this document")

    def of_type(self, full_name: str) -> list[CommentedMap]:
        return [e for e in self.entities if self.entity_type(e) == full_name]

    def types_in_order(self) -> list[str]:
        seen: dict[str, None] = {}
        for entity in self.entities:
            seen.setdefault(self.entity_type(entity), None)
        return list(seen)

    def is_commented(self, entity: CommentedMap) -> bool:
        return id(entity) in self._commented

    def set_commented(self, entity: CommentedMap, commented: bool) -> None:
        if commented:
            prefix = self._commented.get(id(entity), (entity, self.indent + "# "))[1]
            self._commented[id(entity)] = (entity, prefix)
        else:
            self._commented.pop(id(entity), None)

    def active(self) -> list[int]:
        """The positions of the entities that aren't commented out."""
        return [i for i, e in enumerate(self.entities) if not self.is_commented(e)]

    def get(self, entity: CommentedMap, key: str) -> Any:
        return entity.get(key, MISSING)

    def set(
        self,
        entity: CommentedMap,
        key: str,
        value: Any,
        entity_type: EntityType | None = None,
    ) -> Any:
        """Set (or with MISSING remove) a key; returns the previous value."""
        old = entity.get(key, MISSING)
        if value is MISSING:
            if key in entity:
                del entity[key]
            return old
        if key in entity or entity_type is None:
            entity[key] = value
            return old
        # keep keys in the support YAML's parameter order, after "type"
        order = ["type", *entity_type.params]
        rank = {name: i for i, name in enumerate(order)}
        mine = rank.get(key, len(order))
        position = len(entity)
        for i, existing in enumerate(entity):
            if rank.get(existing, len(order)) > mine:
                position = i
                break
        entity.insert(position, key, value)
        return old

    def insert(self, index: int, entity: CommentedMap) -> None:
        self.entities.insert(index, entity)

    def remove(self, index: int) -> CommentedMap:
        return self.entities.pop(index)

    def move(self, source: int, dest: int) -> None:
        entity = self.entities.pop(source)
        self.entities.insert(dest, entity)

    def grouped(self, type_order: list[str]) -> list[CommentedMap]:
        """The entities regrouped by type, types in ``type_order``.

        The sort is stable, so entities keep their order within each type.
        Types missing from ``type_order`` go last, in file order.
        """
        rank = {name: i for i, name in enumerate(type_order)}
        return sorted(
            self.entities,
            key=lambda e: rank.get(self.entity_type(e), len(rank)),
        )

    def reorder(self, entities: list[CommentedMap]) -> None:
        """Replace the entity order with ``entities`` (the same objects)."""
        seq = self.entities
        del seq[:]
        seq.extend(entities)

    def ids(self, catalogue: Catalogue) -> list[str]:
        """Every id defined in the document, for object-reference choices."""
        result: list[str] = []
        for entity in self.entities:
            if self.is_commented(entity):
                continue
            entity_type = catalogue.get(self.entity_type(entity))
            if entity_type is None:
                continue
            for name in entity_type.id_params:
                value = entity.get(name)
                if value not in (None, ""):
                    result.append(str(value))
        return result

    def plain(self) -> dict[str, Any]:
        """The document as plain Python containers, without commented entities."""
        data = json.loads(json.dumps(self.data, default=str))
        data["entities"] = [data["entities"][i] for i in self.active()]
        return data


def new_entity(full_name: str) -> CommentedMap:
    entity = CommentedMap()
    entity["type"] = full_name
    return entity
