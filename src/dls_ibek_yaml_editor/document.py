"""An ``ioc.yaml`` held as round-trip YAML, so saving changes only what was edited.

Comments, quoting, Jinja strings and the ``# yaml-language-server`` header all
survive a load/save cycle unchanged.

A commented-out entity, as VS Code's Ctrl+/ writes it::

      # - type: motorPLC.NX102_MotorAxisPot
      #   name: MOT2

is loaded as a real entity marked commented (xeb's "X" column). On save its
lines are commented out again with the prefix they had.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.tokens import CommentToken

from .catalogue import Catalogue, EntityType, Param

__all__ = ["COMMENTED", "MISSING", "Document", "new_ioc_text", "text_to_value"]

# the pseudo-key of the "X" column: the entity is commented out
COMMENTED = "__commented__"

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


# "  # - type: x": a commented-out entity's first line; group 1 is its prefix
_COMMENTED_START = re.compile(r"^( *# ?)- type:\s*\S")
_ITEM_START = re.compile(r"^( *)- ")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _entities_section(lines: list[str]) -> tuple[int, int]:
    """The line range after ``entities:``, up to the next top-level key."""
    starts = [n for n, line in enumerate(lines) if re.match(r"^entities\s*:", line)]
    if not starts:
        return len(lines), len(lines)
    start = starts[0]
    end = start + 1
    while end < len(lines) and not re.match(r"^[^\s#]", lines[end]):
        end += 1
    return start + 1, end


def _parses_as_entity(lines: list[str]) -> bool:
    try:
        data = YAML(typ="safe").load("".join(lines))
    except Exception:
        return False
    return (
        isinstance(data, list)
        and len(data) == 1
        and isinstance(data[0], dict)
        and isinstance(data[0].get("type"), str)
    )


def uncomment_entities(text: str) -> tuple[str, str, dict[int, str]]:
    """Uncomment the commented-out entities in ``text``.

    Returns the new text, the indent of the entity items, and the position in
    ``entities`` of each uncommented entity with its comment prefix. A comment
    that isn't a whole entity is left alone.
    """
    lines = text.splitlines(keepends=True)
    start, end = _entities_section(lines)
    section = lines[start:end]
    items = [m.group(1) for line in section if (m := _ITEM_START.match(line))]
    indent = items[0] if items else "  "
    result: list[str] = []
    commented: dict[int, str] = {}
    count = 0
    i = 0
    while i < len(section):
        line = section[i]
        match = _COMMENTED_START.match(line)
        if match:
            prefix = match.group(1)
            block = [line[len(prefix) :]]
            j = i + 1
            while j < len(section):
                rest = section[j][len(prefix) :]
                if not section[j].startswith(prefix) or not rest.startswith(" "):
                    break
                block.append(rest)
                j += 1
            if _parses_as_entity(block):
                commented[count] = prefix
                count += 1
                result.extend(indent + part for part in block)
                i = j
                continue
        if line.startswith(indent + "- "):
            count += 1
        result.append(line)
        i += 1
    return "".join(lines[:start] + result + lines[end:]), indent, commented


def comment_entities(text: str, indent: str, prefixes: dict[int, str]) -> str:
    """Comment out the entities at the positions in ``prefixes``."""
    lines = text.splitlines(keepends=True)
    start, end = _entities_section(lines)
    count = -1
    prefix: str | None = None
    block: list[int] = []

    def close() -> None:
        while block and not lines[block[-1]].strip():
            block.pop()  # the blank separator after the entity
        for n in block:
            line = lines[n]
            if line.strip():
                lines[n] = prefix + line[len(indent) :] if prefix else line
            else:
                lines[n] = (prefix or "").rstrip() + line.lstrip(" ")
        block.clear()

    for n in range(start, end):
        line = lines[n]
        if line.startswith(indent + "- "):
            close()
            count += 1
            prefix = prefixes.get(count)
        elif line.strip() and _indent(line) <= len(indent):
            close()  # a comment between entities, or past the last one
            prefix = None
        if prefix is not None:
            block.append(n)
    close()
    return "".join(lines)


def _is_blank(token: Any) -> bool:
    return isinstance(token, CommentToken) and token.value.strip() == ""


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
        if not isinstance(entities, CommentedSeq):
            entities = CommentedSeq()
            data["entities"] = entities
        entities.fa.set_block_style()
        # commented-out entities, by id(), with their comment prefix
        self._commented: dict[int, tuple[CommentedMap, str]] = {
            id(entities[i]): (entities[i], prefix)
            for i, prefix in prefixes.items()
            if i < len(entities)
        }
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
        prefixes = {
            i: self._commented[id(e)][1]
            for i, e in enumerate(self.entities)
            if id(e) in self._commented
        }
        return comment_entities(stream.getvalue(), self.indent, prefixes)

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
            if last[2] is not None and not blank:
                continue  # a comment before the next entity: keep it
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


def _last_token(entity: Any) -> Any:
    if not isinstance(entity, CommentedMap) or not entity:
        return None
    tokens = entity.ca.items.get(list(entity.keys())[-1])
    return tokens[2] if tokens and len(tokens) > 2 else None


def _mark() -> Any:
    from ruamel.yaml.error import CommentMark

    return CommentMark(0)
