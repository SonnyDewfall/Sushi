"""Write a Python structure into a YAML file without losing what a person wrote there.

PyYAML reads comments and throws them away, so a file it dumps comes back bare.
This module is the other half: `merged(text, data)` loads a file with ruamel.yaml's
round-trip loader, updates it in place until it holds `data`, and dumps it, so
comments, blank lines and the order of keys a person chose survive a machine
write. `fresh(data)` is the same dump for a file that does not exist yet, laid
out the way PyYAML's `safe_dump(..., sort_keys=False, width=100)` laid out the
rig files already committed (keys in the dict's order, block style, lists at the
key's own indent).

It knows nothing about rigs. rigfile.write_rig is the rig-shaped caller.

## What an update does

- A scalar that is unchanged is left alone, so its quoting and its trailing
  comment stay; one that changed is replaced, keeping its quote style.
- A key missing from `data` is removed; a key new to the file is inserted after
  the key that precedes it in `data`. Keys already in the file stay in the
  order the file has them, however `data` orders them.
- A list of mappings that all have a `name` is matched by name, so removing one
  plugin from a chain does not hand its neighbours the wrong comments. Any
  other list is matched by position. A list whose common names come in a
  different order is matched by position as well (comments stay where they
  were written, not with the plugin that moved).
- A new list item is written in flow style (`- {name: x, uri: y}`) when its
  neighbour is, so a hand-written chain stays one line a plugin.

## Where a comment belongs

ruamel.yaml stores the comment lines *between* two entries on the entry
before them, so deleting that entry drops the comments that were written
above the next one, and inserting after it leaves the next one's comments
above the wrong entry. Both are repaired here (`_remove`, `_insert`): the text
between two entries moves with the entry it sits above, and a comment is
dropped only with the entry it was written above or beside. Two edges:

- the comments above the *first* entry of a list stay with the list when that
  entry goes (they are usually about the list, as a chain's heading is);
- the first key of a mapping cannot be removed if comment lines sit below it,
  because ruamel.yaml has nowhere to put them. That is refused, loudly.

## What it refuses rather than does badly

- A file in which ruamel.yaml would drop a comment *before anything is changed*
  (it does: a comment line directly above a flow-style `- {...}` entry that
  has its own trailing `# comment`, in 0.19). The error names the comment. A
  silent loss is the thing this module exists to prevent.
- Text that PyYAML would not read back as `data`. Rig files are read with
  PyYAML (YAML 1.1: `on` and `1:30` are not strings, `1e-05` is not a float),
  ruamel writes YAML 1.2, so strings PyYAML would misread are quoted, floats
  are written the PyYAML way, and the result is read back and compared
  before a caller is given it.
"""

from __future__ import annotations

import copy
import io
import re
from typing import Any

# Matches PyYAML's safe_dump(width=100) closely enough for a file that
# was never touched by hand. A file being updated keeps the width of its
# longest line instead (see `_layout`).
FRESH_WIDTH = 100


class MergeError(ValueError):
    """A file that cannot be updated without losing something, or text that would not read back."""


# --------------------------------------------------------------------------- #
# Dumping

def _yaml(mapping: int, sequence: int, offset: int, width: int):
    from ruamel.yaml import YAML
    from ruamel.yaml.representer import RoundTripRepresenter

    class Representer(RoundTripRepresenter):
        pass

    def represent_float(self, data: float):
        # PyYAML (YAML 1.1) reads `1e-05` as a string; it needs `1.0e-05`.
        if data != data:
            text = ".nan"
        elif data in (float("inf"), float("-inf")):
            text = ".inf" if data > 0 else "-.inf"
        else:
            text = repr(data).lower()
            if "." not in text and "e" in text:
                text = text.replace("e", ".0e", 1)
        return self.represent_scalar("tag:yaml.org,2002:float", text)

    Representer.add_representer(float, represent_float)
    y = YAML()
    y.Representer = Representer
    y.indent(mapping=mapping, sequence=sequence, offset=offset)
    y.width = width
    y.preserve_quotes = True
    y.default_flow_style = False
    return y


def _dump(y, data: Any) -> str:
    out = io.StringIO()
    y.dump(data, out)
    return out.getvalue()


def _ambiguous(text: str) -> bool:
    """Whether PyYAML would read this string, written plain, as something else."""
    import yaml
    from yaml.nodes import ScalarNode
    from yaml.resolver import Resolver

    tag = Resolver().resolve(ScalarNode, text, (True, False))
    return tag != "tag:yaml.org,2002:str"


def _prepare(value: Any) -> Any:
    """A copy of `value` in which every string PyYAML would misread is quoted."""
    from ruamel.yaml.scalarstring import SingleQuotedScalarString

    if isinstance(value, dict):
        return {_prepare(k): _prepare(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_prepare(v) for v in value]
    if type(value) is str and _ambiguous(value):
        return SingleQuotedScalarString(value)
    return copy.copy(value)


def fresh(data: Any, header: str = "") -> str:
    """`data` as a new YAML file: block style, keys in `data`'s order."""
    return header + _dump(_yaml(2, 2, 0, FRESH_WIDTH), _prepare(data))


# --------------------------------------------------------------------------- #
# Reading a file's layout, so an update writes it back the way it was written

def _layout(text: str) -> tuple[int, int, int, int]:
    """(mapping indent, sequence indent, sequence offset, width) of a file.

    ruamel.yaml dumps every block with one indent, so the file's own is
    measured: the first list under a key says whether its dashes sit under the
    key (offset 0) or inside it; the first nested mapping says the step."""
    mapping, sequence, offset = 2, 2, 0
    found_seq = found_map = False
    previous: tuple[int, str] | None = None  # (indent, text) of the last real line
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if previous is not None and previous[1].endswith(":"):
            if stripped.startswith("- ") and not found_seq:
                found_seq = True
                offset = indent - previous[0]
                sequence = offset + 2
            elif not stripped.startswith("-") and indent > previous[0] and not found_map:
                found_map = True
                mapping = indent - previous[0]
        previous = (indent, stripped)
        if found_seq and found_map:
            break
    width = max([FRESH_WIDTH] + [len(line) for line in text.splitlines()])
    return mapping, max(sequence, offset + 2), max(offset, 0), width


# --------------------------------------------------------------------------- #
# Comments, counted from the text: how a lost one is noticed

def _comments(text: str) -> list[str]:
    """Every `# comment` in a YAML text, in order. A `#` inside a quoted
    string, or glued to the text before it (`uri#fragment`), is not one."""
    found = []
    for line in text.splitlines():
        quote = ""
        last = ""
        for i, char in enumerate(line):
            if quote:
                if quote == '"' and char == "\\":
                    continue
                if char == quote:
                    quote = ""
            elif char in "\"'" and (last == "" or last in ":-,[{?"):
                quote = char
            elif char == "#" and (i == 0 or line[i - 1] in " \t"):
                found.append(line[i:].strip())
                break
            if not quote and not char.isspace():
                last = char
    return found


# --------------------------------------------------------------------------- #
# The text between two entries, which ruamel.yaml keeps on the first of them

def _is_map(node: Any) -> bool:
    from ruamel.yaml.comments import CommentedMap

    return isinstance(node, CommentedMap)


def _is_block(node: Any) -> bool:
    """A non-empty mapping or list written one entry a line."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    return isinstance(node, (CommentedMap, CommentedSeq)) and len(node) > 0 \
        and node.fa.flow_style() is not True


def _is_flow(node: Any) -> bool:
    return getattr(getattr(node, "fa", None), "flow_style", lambda: None)() is True


def _keys(container: Any) -> list[Any]:
    return list(container.keys()) if _is_map(container) else list(range(len(container)))


def _leaf(container: Any, ident: Any) -> tuple[Any, Any]:
    """The (container, key) that holds the comment text following `container[ident]`:
    the entry itself, or, for a block mapping or list, its last entry's last entry."""
    while _is_block(container[ident]):
        container = container[ident]
        ident = _keys(container)[-1]
    return container, ident


def _after_index(container: Any) -> int:
    return 2 if _is_map(container) else 0  # where ruamel.yaml keeps it, per kind


def _is_last_of_list(holder: Any, key: Any) -> bool:
    return not _is_map(holder) and key == len(holder) - 1


def _get_after(container: Any, ident: Any) -> str:
    holder, key = _leaf(container, ident)
    slots = holder.ca.items.get(key)
    token = slots[_after_index(holder)] if slots else None
    if token is not None and not isinstance(token, list):
        return token.value
    if _is_last_of_list(holder, key) and holder.ca.end:
        # What follows a list's last entry is kept on the list, when that entry
        # has no comment of its own to hold it. Each token starts at its column.
        return "\n" + "".join(" " * t.column + t.value for t in holder.ca.end)
    return ""


def _set_after(container: Any, ident: Any, value: str | None) -> None:
    from ruamel.yaml.error import CommentMark
    from ruamel.yaml.tokens import CommentToken

    holder, key = _leaf(container, ident)
    index = _after_index(holder)
    if _is_last_of_list(holder, key):
        # Mirror how ruamel.yaml reads it: a comment on the entry's own line
        # takes everything after it into one token; otherwise the lines after
        # the entry belong to the list's end.
        eol, rest = _split(value or "")
        holder.ca.end[:] = [] if eol else _tokens(rest)
        if not eol:
            value = None
    slots = holder.ca.items.get(key)
    if value is None:
        if slots:
            slots[index] = None
            if not any(slots):
                del holder.ca.items[key]
        return
    if slots is None:
        slots = holder.ca.items[key] = [None, None, None, None]
    if slots[index] is not None and not isinstance(slots[index], list):
        slots[index].value = value
    else:
        slots[index] = CommentToken(value, CommentMark(0))


def _split(value: str) -> tuple[str, str]:
    """(the comment on the entry's own line, the lines after it)."""
    eol, _, rest = value.partition("\n")
    return eol, rest


def _join(eol: str, rest: str) -> str | None:
    return None if not eol and not rest else eol + "\n" + rest


def _has_comment(text: str) -> bool:
    return any(line.strip().startswith("#") for line in text.splitlines())


def _tokens(text: str) -> list[Any]:
    """Comment lines as the tokens ruamel.yaml keeps them in: one a line, a
    blank line joined to the one before it."""
    from ruamel.yaml.error import CommentMark
    from ruamel.yaml.tokens import CommentToken

    tokens: list[Any] = []
    for line in text.splitlines():
        if not line.strip():
            if tokens:
                tokens[-1].value += "\n"
            else:
                tokens.append(CommentToken("\n", CommentMark(0)))
        else:
            tokens.append(CommentToken(line.strip() + "\n", CommentMark(len(line) - len(line.lstrip()))))
    return tokens


def _add_leading(seq: Any, rest: str) -> None:
    """Put comment lines above a list's first entry (where its heading lives)."""
    tokens = _tokens(rest)
    if not tokens:
        return
    if seq.ca.comment is None:
        seq.ca.comment = [None, tokens]
    elif seq.ca.comment[1] is None:
        seq.ca.comment[1] = tokens
    else:
        seq.ca.comment[1].extend(tokens)


def _remove(container: Any, ident: Any) -> None:
    """Delete one entry; its comments go, the next entry's stay."""
    keys = _keys(container)
    pos = keys.index(ident)
    _, rest = _split(_get_after(container, ident))
    if pos > 0:
        previous = keys[pos - 1]
        eol, _ = _split(_get_after(container, previous))
        _set_after(container, previous, _join(eol, rest))
    elif _has_comment(rest):
        if _is_map(container):
            raise MergeError(
                f"cannot remove {ident!r}, the first key of a mapping, while comments sit below it: "
                "ruamel.yaml has nowhere to keep them. Move or delete the comments, then retry")
        _add_leading(container, rest)
    del container[ident]
    if len(container) == 0 and not _is_map(container):
        container.fa.set_flow_style()  # `[]`, not an empty block with comments hanging off it


def _insert(container: Any, pos: int, ident: Any, node: Any) -> None:
    """Insert one entry; the comments written above the entry now after it stay above it."""
    keys = _keys(container)
    rest = ""
    if 0 < pos <= len(keys):
        previous = keys[pos - 1]
        eol, rest = _split(_get_after(container, previous))
        _set_after(container, previous, _join(eol, ""))
    if _is_map(container):
        container.insert(pos, ident, node)
        key = ident
    else:
        container.insert(pos, node)
        key = pos
    if rest:
        _set_after(container, key, _join("", rest))


# --------------------------------------------------------------------------- #
# Updating a loaded file to hold new data

def _kind(value: Any) -> str:
    for kind, types in (("bool", bool), ("int", int), ("float", float), ("str", str)):
        if isinstance(value, types):
            return kind
    return type(value).__name__


def _build(value: Any, flow: bool) -> Any:
    """New data as ruamel nodes, in the style of where it is going."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    if isinstance(value, dict):
        node: Any = CommentedMap()
        for k, v in value.items():
            node[k] = _build(v, flow)
    elif isinstance(value, list):
        node = CommentedSeq(_build(v, flow) for v in value)
    else:
        return value
    node.fa.set_flow_style() if flow else node.fa.set_block_style()
    return node


def _quoted_like(old: Any, new: Any) -> Any:
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString, SingleQuotedScalarString

    if type(new) is str and isinstance(old, (DoubleQuotedScalarString, SingleQuotedScalarString)):
        return type(old)(new)
    return new


def _same(old: Any, new: Any) -> bool:
    return _kind(old) == _kind(new) and old == new


def _set_value(container: Any, ident: Any, new: Any, flow: bool) -> None:
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    old = container[ident]
    if isinstance(new, dict) and isinstance(old, CommentedMap):
        _update_map(old, new)
    elif isinstance(new, list) and isinstance(old, CommentedSeq):
        _update_seq(old, new)
    elif not isinstance(new, (dict, list)) and not isinstance(old, (CommentedMap, CommentedSeq)):
        if not _same(old, new):
            container[ident] = _quoted_like(old, new)
    else:
        container[ident] = _build(new, flow or _is_flow(old))


def _update_map(old: Any, new: dict) -> None:
    flow = _is_flow(old)
    for key in [k for k in old if k not in new]:
        _remove(old, key)
    previous = None
    for key, value in new.items():
        if key in old:
            _set_value(old, key, value, flow)
        else:
            pos = _keys(old).index(previous) + 1 if previous is not None else 0
            _insert(old, pos, key, _build(value, flow))
        previous = key


def _names(items: Any) -> list[str] | None:
    """The `name` of each item when every item has one and they differ, else None."""
    names = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return None
        names.append(item["name"])
    return names if len(set(names)) == len(names) else None


def _update_seq(old: Any, new: list) -> None:
    flow_items = any(_is_flow(item) for item in old)
    old_names, new_names = _names(old), _names(new)
    if old_names is not None and new_names is not None:
        for i in reversed(range(len(old))):
            if old_names[i] not in new_names:
                _remove(old, i)
        kept = [n for n in old_names if n in new_names]
        if kept == [n for n in new_names if n in kept]:
            for i, item in enumerate(new):
                if i < len(old) and old[i]["name"] == item["name"]:
                    _update_item(old, i, item)
                else:
                    _insert(old, i, i, _build(item, flow_items))
            return
    # By position: the comments stay where they were written.
    for i, item in enumerate(new):
        if i < len(old):
            _update_item(old, i, item)
        else:
            _insert(old, i, i, _build(item, flow_items))
    for i in reversed(range(len(new), len(old))):
        _remove(old, i)


def _update_item(seq: Any, i: int, new: Any) -> None:
    _set_value(seq, i, new, _is_flow(seq[i]))


# --------------------------------------------------------------------------- #

def merged(text: str, data: dict) -> str:
    """`text` (a YAML mapping) updated to hold `data`, comments and layout kept.

    Raises `MergeError` for a text that is not a mapping or not YAML, for a
    comment ruamel.yaml could not keep, and for a result PyYAML would read
    as something other than `data`."""
    from ruamel.yaml import YAMLError

    mapping, sequence, offset, width = _layout(text)
    y = _yaml(mapping, sequence, offset, width)
    try:
        doc = y.load(text)
    except YAMLError as exc:
        raise MergeError(f"not YAML: {exc}") from exc
    if not _is_map(doc):
        raise MergeError("the top level is not a mapping")
    before = _comments(text)
    lost = list(before)
    for comment in _comments(_dump(y, doc)):
        if comment in lost:
            lost.remove(comment)
    if lost:
        raise MergeError(
            f"ruamel.yaml would drop {len(lost)} comment(s) from this file before any change is made, "
            f"first {lost[0]!r}; nothing was written. (A comment line directly above a flow-style "
            "`- {...}` entry that has its own trailing comment is one case.)")
    _update_map(doc, _prepare(data))
    return _dump(y, doc)
