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
  plugin from a chain does not hand its neighbours the wrong comments. An entry
  whose name changed but whose `uri` (or `path`, `uid`) did not is the same
  plugin renamed, and is updated in place, comment and layout and all. Any
  other list is matched by position, and so is one whose common names come in
  a different order (comments stay where they were written, not with the
  plugin that moved).
- A new list item is written in flow style (`- {name: x, uri: y}`) when the
  entry beside it is, and an empty mapping or list always is (`[]`).
- The layout and fold width of the file (indent, where dashes sit, 100 columns
  or its widest line) are found by reproducing the unchanged file with them
  (`_choose`), so an update to one value does not re-indent or re-fold others.

## Where a comment belongs

ruamel.yaml stores the comment lines *between* two entries on the entry
before them, so deleting that entry drops the comments that were written
above the next one, and inserting after it leaves the next one's comments
above the wrong entry. Both are repaired here (`_Update.remove`, `_Update.insert`): the text
between two entries moves with the entry it sits above, and a comment is
dropped only with the entry it was written above or beside. Two edges:

- the comments above the *first* entry of a list stay with the list when that
  entry goes (they are usually about the list, as a chain's heading is);
- a key inserted first in a mapping goes above the comment that was above the
  old first key, unless a blank line sets that comment apart;
- the first key of a mapping cannot be removed if comment lines sit below it,
  and the only entry of a list or mapping cannot be removed if comments follow
  it, because ruamel.yaml has nowhere to put them (it drops anything after an
  empty `[]`). Both are refused, loudly.

## What it refuses rather than does badly

- A file in which ruamel.yaml would drop a comment *before anything is changed*
  (it does: a comment line directly above a flow-style `- {...}` entry that
  has its own trailing `# comment`, in 0.19). The error names the comment. A
  silent loss is the thing this module exists to prevent.
- An update that leaves a comment written twice, or lost other than with an
  entry that was removed: the comments of the result are counted against
  those of the file, less the ones deliberately dropped.
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

# Layouts tried after the one measured, as (mapping, sequence, offset), when the
# measured one does not reproduce the file.
_LAYOUTS = [(2, 2, 0), (2, 4, 2), (4, 4, 0), (4, 6, 2), (2, 3, 1)]


def _layout(text: str) -> tuple[int, int, int]:
    """(mapping indent, sequence indent, sequence offset) a file looks to use.

    ruamel.yaml dumps every block with one indent, so the file's own is
    measured: the first list under a `key:` line says whether its dashes sit
    under the key (offset 0) or inside it; the first mapping under a `key:`
    line says the step. A line that is itself a list item (`- strip:`) is not a
    `key:` line, since what is nested below it steps from its text, not its
    dash. This is a first guess: `_choose` checks it by reproducing the file."""
    mapping, sequence, offset = 2, 2, 0
    found_seq = found_map = False
    previous: tuple[int, str] | None = None  # (indent, code) of the last real line
    for line in text.splitlines():
        code = line[:_comment_start(line)].rstrip()
        if not code.strip():
            continue
        stripped = code.strip()
        indent = len(code) - len(code.lstrip(" "))
        if previous is not None and previous[1].endswith(":") and not previous[1].startswith("- "):
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
    return mapping, max(sequence, offset + 2), max(offset, 0)


def _choose(text: str):
    """(a dumper, the loaded document) that write the file back as it was.

    Layout and width are properties of the file, not of ruamel.yaml, and a
    wrong guess re-indents or re-folds lines nobody asked to change. So the
    measured layout and a few likelier widths (100, where this tool folds, and
    the file's longest line, with and without comments) are tried against the
    unchanged document, widest first because that leaves a flow-style entry
    that grows room to, and the first to reproduce the text exactly is used;
    failing that, the one that differs from it least."""
    lines = text.splitlines()
    code = [l for l in lines if not l.lstrip().startswith("#")]
    widths = sorted({FRESH_WIDTH, max([FRESH_WIDTH] + [len(l) for l in lines]),
                     max([FRESH_WIDTH] + [len(l) for l in code])}, reverse=True)
    measured = _layout(text)
    doc = _yaml(*measured, FRESH_WIDTH).load(text)
    best = None
    for layout in [measured] + [l for l in _LAYOUTS if l != measured]:
        for width in widths:
            y = _yaml(*layout, width)
            out = _dump(y, doc).splitlines()
            if out == lines:
                return y, doc
            score = sum(a != b for a, b in zip(out, lines)) + abs(len(out) - len(lines))
            if best is None or score < best[0]:
                best = (score, y)
    return best[1], doc


# --------------------------------------------------------------------------- #
# Comments, counted from the text: how a lost or doubled one is noticed

def _comment_start(line: str) -> int | None:
    """Where the `# comment` in a line of YAML starts, if there is one. A `#`
    inside a quoted string, or glued to the text before it (`uri#fragment`), is
    not one."""
    quote = ""
    last = ""
    skip = False
    for i, char in enumerate(line):
        if skip:
            skip = False
            continue
        if quote:
            if quote == '"' and char == "\\":
                skip = True
            elif char == quote:
                quote = ""
        elif char in "\"'" and (last == "" or last in ":-,[{?"):
            quote = char
        elif char == "#" and (i == 0 or line[i - 1] in " \t"):
            return i
        if not quote and not char.isspace():
            last = char
    return None


def _comments(text: str) -> list[str]:
    """Every `# comment` in a YAML text, in order."""
    found = []
    for line in text.splitlines():
        start = _comment_start(line)
        if start is not None:
            found.append(line[start:].strip())
    return found


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


def _token_comments(t: Any, found: list[str]) -> None:
    """Add the comments in a ruamel.yaml comment slot (a token, a list of them, or None)."""
    if t is None:
        return
    if isinstance(t, list):
        for item in t:
            _token_comments(item, found)
    else:
        found.extend(_comments(" " * (t.column or 0) + t.value))


def _all_comments(node: Any) -> list[str]:
    """Every comment held anywhere under `node`, from ruamel.yaml's own storage."""
    found: list[str] = []
    ca = getattr(node, "ca", None)
    if ca is not None:
        for slots in ca.items.values():
            _token_comments(slots, found)
        _token_comments(ca.comment, found)
        _token_comments(ca.end, found)
    if _is_map(node):
        for v in node.values():
            found += _all_comments(v)
    elif isinstance(node, list):
        for v in node:
            found += _all_comments(v)
    return found


def _entry_comments(container: Any, ident: Any) -> list[str]:
    """Every comment that goes if `container[ident]` does: those under it, and
    the ones kept after it."""
    found = _all_comments(container[ident])
    _token_comments(container.ca.items.get(ident), found)
    if not _is_map(container) and ident == len(container) - 1 and not _is_block(container[ident]):
        _token_comments(container.ca.end, found)
    return found


def _subtract(whole: list[str], part: list[str]) -> list[str]:
    from collections import Counter

    return list((Counter(whole) - Counter(part)).elements())


def _lead(container: Any, parent: tuple[Any, Any] | None) -> list[Any] | None:
    """The comment tokens written above a mapping's first key, which
    ruamel.yaml keeps on the key above it (or on the file, for the top mapping)."""
    if parent is None:
        return container.ca.comment[1] if container.ca.comment else None
    holder, key = parent
    if not _is_map(holder):
        return None
    slots = holder.ca.items.get(key)
    return slots[3] if slots and isinstance(slots[3], list) else None


def _clear_lead(container: Any, parent: tuple[Any, Any] | None) -> None:
    if parent is None:
        container.ca.comment[1] = None
    else:
        parent[0].ca.items[parent[1]][3] = None


# --------------------------------------------------------------------------- #
# Updating a loaded file to hold new data

# What makes two chain entries with different names the same one, renamed: they
# name the same plugin. A chain is matched by `name`, so without this a rename
# is a removal and an insertion, and the comment written above the plugin goes.
IDENTITY_KEYS = ("uri", "path", "uid")


def _kind(value: Any) -> str:
    for kind, types in (("bool", bool), ("int", int), ("float", float), ("str", str)):
        if isinstance(value, types):
            return kind
    return type(value).__name__


def _build(value: Any, flow: bool) -> Any:
    """New data as ruamel nodes, in the style of where it is going. An empty
    mapping or list is always flow (`[]`): written as a block it has nowhere
    for the comment after it to go but between the key and its value."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    if isinstance(value, dict):
        node: Any = CommentedMap()
        for k, v in value.items():
            node[k] = _build(v, flow)
    elif isinstance(value, list):
        node = CommentedSeq(_build(v, flow) for v in value)
    else:
        return value
    node.fa.set_flow_style() if flow or len(node) == 0 else node.fa.set_block_style()
    return node


def _quoted_like(old: Any, new: Any) -> Any:
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString, SingleQuotedScalarString

    if type(new) is str and isinstance(old, (DoubleQuotedScalarString, SingleQuotedScalarString)):
        return type(old)(new)
    return new


def _same(old: Any, new: Any) -> bool:
    return _kind(old) == _kind(new) and old == new


def _flow_near(seq: Any, pos: int) -> bool:
    """Whether an entry put at `pos` is written like the neighbour it sits
    beside: the one before, else the one after."""
    for i in (pos - 1, pos):
        if 0 <= i < len(seq):
            return _is_flow(seq[i])
    return False


def _names(items: Any) -> list[str] | None:
    """The `name` of each item when every item has one and they differ, else None."""
    names = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return None
        names.append(item["name"])
    return names if len(set(names)) == len(names) else None


def _renamed(old: Any, new: Any) -> bool:
    return any(k in old and k in new and old[k] == new[k] for k in IDENTITY_KEYS)


class _Update:
    """One in-place update of a loaded document, and the comments it meant to drop."""

    def __init__(self) -> None:
        self.gone: list[str] = []

    def remove(self, container: Any, ident: Any, parent: tuple[Any, Any] | None = None) -> None:
        """Delete one entry; its comments go, the next entry's stay.

        `parent` is the (container, key) that holds `container`."""
        keys = _keys(container)
        pos = keys.index(ident)
        removed = _entry_comments(container, ident)
        _, rest = _split(_get_after(container, ident))
        eol = ""
        emptying = len(keys) == 1
        if pos > 0:
            previous = keys[pos - 1]
            eol, rest_before = _split(_get_after(container, previous))
            self.gone += _comments(rest_before)
        elif emptying:
            # What was written above the first entry goes with the list. What
            # follows the last entry has nowhere to go: ruamel.yaml drops
            # comments after an empty `[]`, so that is refused.
            lead = _lead(container, parent) if _is_map(container) else (
                container.ca.comment[1] if container.ca.comment else None)
            if lead:
                tokens: list[str] = []
                _token_comments(lead, tokens)
                self.gone += tokens
                if _is_map(container):
                    _clear_lead(container, parent)
                else:
                    container.ca.comment = None
            if _has_comment(rest):
                raise MergeError(f"cannot remove the only entry {ident!r} of a list or mapping while "
                                 "comments follow it: ruamel.yaml would drop them. Move or delete "
                                 "the comments, then retry")
        elif _has_comment(rest):
            if _is_map(container):
                raise MergeError(
                    f"cannot remove {ident!r}, the first key of a mapping, while comments sit below it: "
                    "ruamel.yaml has nowhere to keep them. Move or delete the comments, then retry")
            _add_leading(container, rest)
        self.gone += _subtract(removed, _comments(rest))
        # Delete first: the entry before it may now be the list's last, and what
        # follows a list's last entry is stored differently (see _set_after).
        del container[ident]
        if pos > 0:
            _set_after(container, keys[pos - 1], _join(eol, rest))
        elif emptying:
            container.fa.set_flow_style()  # `[]`, not an empty block with comments hanging off it
            if hasattr(container.ca, "end"):
                container.ca.end[:] = []

    def insert(self, container: Any, pos: int, ident: Any, node: Any,
               parent: tuple[Any, Any] | None = None) -> None:
        """Insert one entry; the comments written above the entry now after it stay above it."""
        keys = _keys(container)
        rest = ""
        if 0 < pos <= len(keys):
            previous = keys[pos - 1]
            eol, rest = _split(_get_after(container, previous))
            _set_after(container, previous, _join(eol, ""))
        elif pos == 0 and _is_map(container) and keys:
            # The comment above the old first key is about that key: it moves
            # to follow the new one, unless a blank line sets it apart (a
            # heading for the whole mapping, or the file).
            lead = _lead(container, parent)
            if lead and not lead[-1].value.endswith("\n\n"):
                rest = "".join(" " * t.column + t.value for t in lead)
                _clear_lead(container, parent)
        if _is_map(container):
            container.insert(pos, ident, node)
            key = ident
        else:
            container.insert(pos, node)
            key = pos
        if rest:
            _set_after(container, key, _join("", rest))

    def value(self, container: Any, ident: Any, new: Any, flow: bool) -> None:
        from ruamel.yaml.comments import CommentedMap, CommentedSeq

        old = container[ident]
        if isinstance(new, dict) and isinstance(old, CommentedMap):
            self.map(old, new, (container, ident))
        elif isinstance(new, list) and isinstance(old, CommentedSeq):
            self.seq(old, new, (container, ident))
        elif not isinstance(new, (dict, list)) and not isinstance(old, (CommentedMap, CommentedSeq)):
            if not _same(old, new):
                container[ident] = _quoted_like(old, new)
        else:
            container[ident] = _build(new, flow or _is_flow(old))

    def map(self, old: Any, new: dict, parent: tuple[Any, Any] | None = None) -> None:
        flow = _is_flow(old)
        for key in [k for k in old if k not in new]:
            self.remove(old, key, parent)
        previous = None
        for key, value in new.items():
            if key in old:
                self.value(old, key, value, flow)
            else:
                pos = _keys(old).index(previous) + 1 if previous is not None else 0
                self.insert(old, pos, key, _build(value, flow), parent)
            previous = key

    def item(self, seq: Any, i: int, new: Any) -> None:
        self.value(seq, i, new, _is_flow(seq[i]))

    def add(self, seq: Any, pos: int, new: Any) -> None:
        self.insert(seq, pos, pos, _build(new, _flow_near(seq, pos)))

    def seq(self, old: Any, new: list, parent: tuple[Any, Any] | None = None) -> None:
        import difflib

        old_names, new_names = _names(old), _names(new)
        if old_names is not None and new_names is not None:
            common = [n for n in old_names if n in new_names]
            if common == [n for n in new_names if n in old_names]:
                matcher = difflib.SequenceMatcher(None, old_names, new_names, autojunk=False)
                # Last block first, so the indexes of the blocks before it hold.
                for tag, i1, i2, j1, j2 in reversed(matcher.get_opcodes()):
                    if tag == "equal":
                        for k in range(i2 - i1):
                            self.item(old, i1 + k, new[j1 + k])
                    elif tag == "replace" and i2 - i1 == j2 - j1:
                        for k in reversed(range(i2 - i1)):
                            if _renamed(old[i1 + k], new[j1 + k]):
                                self.item(old, i1 + k, new[j1 + k])
                            else:
                                self.remove(old, i1 + k, parent)
                                self.add(old, i1 + k, new[j1 + k])
                    else:
                        for i in reversed(range(i1, i2)):
                            self.remove(old, i, parent)
                        for k, j in enumerate(range(j1, j2)):
                            self.add(old, i1 + k, new[j])
                return
        # By position: the comments stay where they were written.
        for i, item in enumerate(new):
            if i < len(old):
                self.item(old, i, item)
            else:
                self.add(old, i, item)
        for i in reversed(range(len(new), len(old))):
            self.remove(old, i, parent)


# --------------------------------------------------------------------------- #

def merged(text: str, data: dict) -> str:
    """`text` (a YAML mapping) updated to hold `data`, comments and layout kept.

    Raises `MergeError` for a text that is not a mapping or not YAML, for a
    comment ruamel.yaml could not keep, for an update that would duplicate or
    drop a comment other than with an entry that is removed, and for a result
    PyYAML would read as something other than `data` (rigfile.write_rig checks
    that last one)."""
    from collections import Counter

    from ruamel.yaml import YAMLError

    try:
        y, doc = _choose(text)
    except YAMLError as exc:
        raise MergeError(f"not YAML: {exc}") from exc
    if not _is_map(doc):
        raise MergeError("the top level is not a mapping")
    before = Counter(_comments(text))
    lost = before - Counter(_comments(_dump(y, doc)))
    if lost:
        raise MergeError(
            f"ruamel.yaml would drop {sum(lost.values())} comment(s) from this file before any change "
            f"is made, first {next(iter(lost))!r}; nothing was written. (A comment line directly above "
            "a flow-style `- {...}` entry that has its own trailing comment is one case.)")
    update = _Update()
    update.map(doc, _prepare(data))
    out = _dump(y, doc)
    expected = before - Counter(update.gone)
    after = Counter(_comments(out))
    if after != expected:
        extra, missing = after - expected, expected - after
        what = (f"{sum(extra.values())} comment(s) written more than once, first {next(iter(extra))!r}"
                if extra else f"{sum(missing.values())} comment(s) lost, first {next(iter(missing))!r}")
        raise MergeError(f"the update would leave {what}; nothing was written")
    return out
