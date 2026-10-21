"""An ``ioc.yaml`` held as round-trip YAML, so saving changes only what was edited.

Comments, quoting, Jinja strings and the ``# yaml-language-server`` header all
survive a load/save cycle unchanged.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.tokens import CommentToken

from .catalogue import Catalogue, EntityType, Param

__all__ = ["MISSING", "Document", "new_ioc_text", "text_to_value"]

IOC_NAME_JINJA = "{{ _global.get_env('IOC_NAME') }}"


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


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
    return text


def value_to_text(value: Any) -> str:
    if value is MISSING or value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    return str(value)


def _is_blank(token: Any) -> bool:
    return isinstance(token, CommentToken) and token.value.strip() == ""


class Document:
    """An ``ioc.yaml``: header fields plus an ordered list of entities."""

    def __init__(self, text: str, path: Path | None = None):
        self.path = path
        self._yaml = _yaml()
        data = self._yaml.load(text)
        if not isinstance(data, CommentedMap):
            raise ValueError(f"{path or 'ioc.yaml'} is not a YAML mapping")
        self.data: CommentedMap = data
        entities = data.get("entities")
        if not isinstance(entities, CommentedSeq):
            entities = CommentedSeq()
            data["entities"] = entities
        entities.fa.set_block_style()
        # blank lines between entities: keep whatever the file did
        self.separated = any(
            _is_blank(_last_token(e)) for e in entities[:-1] if isinstance(e, dict)
        )
        if len(entities) < 2:
            self.separated = True
        self.trailing_blank = bool(entities) and _is_blank(_last_token(entities[-1]))

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
        self._normalise_separators()
        stream = io.StringIO()
        self._yaml.dump(self.data, stream)
        return stream.getvalue()

    def save(self, path: Path | None = None) -> None:
        path = path or self.path
        if path is None:
            raise ValueError("no file name")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.dumps())
        self.path = path

    def _normalise_separators(self) -> None:
        """Keep the blank lines between entities after the last key of each.

        ruamel attaches a blank line to the key before it, so a key added to
        the end of an entity, or a moved entity, would otherwise carry its
        separator to the wrong place. Untouched files come out unchanged.
        """
        entities = self.entities
        final = len(entities) - 1
        for position, entity in enumerate(entities):
            if not isinstance(entity, CommentedMap) or not entity:
                continue
            keys = list(entity.keys())
            for key in keys[:-1]:
                tokens = entity.ca.items.get(key)
                if tokens and len(tokens) > 2 and _is_blank(tokens[2]):
                    tokens[2] = None
            last = entity.ca.items.setdefault(keys[-1], [None, None, None, None])
            blank = _is_blank(last[2])
            if position < final and self.separated and not blank:
                last[2] = CommentToken("\n\n", _mark(), None)
            elif position == final and blank and not self.trailing_blank:
                last[2] = None

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
            entity_type = catalogue.get(self.entity_type(entity))
            if entity_type is None:
                continue
            for name in entity_type.id_params:
                value = entity.get(name)
                if value not in (None, ""):
                    result.append(str(value))
        return result

    def plain(self) -> dict[str, Any]:
        """The document as plain Python containers."""
        return json.loads(json.dumps(self.data, default=str))


def new_entity(full_name: str) -> CommentedMap:
    entity = CommentedMap()
    entity["type"] = full_name
    return entity


def _last_token(entity: Any) -> Any:
    if not isinstance(entity, CommentedMap) or not entity:
        return None
    tokens = entity.ca.items.get(list(entity.keys())[-1])
    return tokens[2] if tokens and len(tokens) > 2 else None


def _mark() -> Any:
    from ruamel.yaml.error import CommentMark

    return CommentMark(0)
