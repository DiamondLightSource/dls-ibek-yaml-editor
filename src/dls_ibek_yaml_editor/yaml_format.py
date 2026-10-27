"""YAML text and ruamel comment-token handling used by Document.

The emitter uses two spaces before entity dashes. Input comment prefixes are
retained separately; they must never be used to locate emitted sequence items.
"""

import re
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.tokens import CommentToken

ENTITY_INDENT = "  "

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
    while end < len(lines) and (
        not re.match(r"^[^\s#]", lines[end]) or lines[end].startswith("- ")
    ):
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
    indent = min(items, key=len) if items else "  "
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


def is_blank(token: Any) -> bool:
    return isinstance(token, CommentToken) and token.value.strip() == ""


def normalise_separators(
    entities: CommentedSeq, separated: bool, trailing_blank: bool
) -> None:
    """Keep the blank lines between entities after the last key of each.

    ruamel attaches a blank line to the key before it, so a key added to
    the end of an entity, or a moved entity, would otherwise carry its
    separator to the wrong place. Untouched files come out unchanged.
    """
    final = len(entities) - 1
    for position, entity in enumerate(entities):
        if not isinstance(entity, CommentedMap) or not entity:
            continue
        keys = list(entity.keys())
        for key in keys[:-1]:
            tokens = entity.ca.items.get(key)
            if tokens and len(tokens) > 2 and is_blank(tokens[2]):
                tokens[2] = None
        last = entity.ca.items.setdefault(keys[-1], [None, None, None, None])
        blank = is_blank(last[2])
        if last[2] is not None and not blank:
            continue  # a comment before the next entity: keep it
        if position < final and separated and not blank:
            last[2] = CommentToken("\n\n", _mark(), None)
        elif position == final and blank and not trailing_blank:
            last[2] = None


def last_token(entity: Any) -> Any:
    if not isinstance(entity, CommentedMap) or not entity:
        return None
    tokens = entity.ca.items.get(list(entity.keys())[-1])
    return tokens[2] if tokens and len(tokens) > 2 else None


def _mark() -> Any:
    from ruamel.yaml.error import CommentMark

    return CommentMark(0)
