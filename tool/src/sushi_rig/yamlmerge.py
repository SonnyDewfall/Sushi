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
  plugin renamed, and is updated in place, comment and layout and all. A
  chain whose entries come in a different order is *moved*, not matched by
  position: each entry takes its comments with it (`_Update.reorder`, below).
  Any other list is matched by position, and its comments stay where they were
  written.
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
dropped only with the entry it was written above or beside. The edges:

- the comments above the *first* entry of a list stay with the list when that
  entry goes (they are usually about the list, as a chain's heading is);
- a key inserted first in a mapping goes above the comment that was above the
  old first key, unless a blank line sets that comment apart;
- when a named list is reordered, an entry moves with the comment lines above
  it, whatever is inside it and its trailing comment. What stays with the
  *position*: whatever follows the last entry,
  and a heading at the top of the list that a blank line sets apart from the
  first entry (a comment directly above the first entry, with no blank line,
  is taken to be about it and moves with it, or goes with it if it is the one
  removed; an insert at the front of a list, or a plain removal of its first
  entry, does not know which entry the comment is about, so it leaves all of it
  at the top). A blank line goes with the entry below it (the one that moved to the
  top loses it, and the entry it displaced takes it); a new entry has none;
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
  are written the PyYAML way, and `fresh` and `merged` both read their result
  back with PyYAML and compare it to `data` before returning it.
- A reorder it cannot do without guessing: a list in which comments sit in a
  slot of the list that it does not read. The error names the entry. (A comment
  after a `|` or `>` scalar used to be refused too; ruamel.yaml keeps it in the
  scalar's trailing-comment slot, but `_get_after` now knows that such a slot
  holds only lines after the scalar.)
- A file whose layout they cannot reproduce: if dumping the unchanged document
  does not give the file back exactly (a flow entry a person wrapped by hand,
  mixed indents), an update would re-lay-out lines nobody asked to touch, so
  it is refused with the first line that would move.
"""

from __future__ import annotations

import copy
import functools
import io
from typing import Any

# Matches PyYAML's safe_dump(width=100) closely enough for a file that
# was never touched by hand. A file being updated keeps the width of its
# longest line instead (see `_layout`).
FRESH_WIDTH = 100


class MergeError(ValueError):
    """A file that cannot be updated without losing something, or text that would not read back."""


# --------------------------------------------------------------------------- #
# Dumping

@functools.lru_cache(maxsize=None)
def _representer():
    """ruamel's round-trip representer, writing floats the way PyYAML reads them."""
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
    return Representer


def _yaml(mapping: int, sequence: int, offset: int, width: int):
    from ruamel.yaml import YAML

    y = YAML()
    y.Representer = _representer()
    y.indent(mapping=mapping, sequence=sequence, offset=offset)
    y.width = width
    y.preserve_quotes = True
    y.default_flow_style = False
    return y


def _dump(y, data: Any) -> str:
    out = io.StringIO()
    y.dump(data, out)
    return out.getvalue()


@functools.lru_cache(maxsize=None)
def _resolver():
    from yaml.resolver import Resolver

    return Resolver()


def _ambiguous(text: str) -> bool:
    """Whether PyYAML would read this string, written plain, as something else."""
    from yaml.nodes import ScalarNode

    return _resolver().resolve(ScalarNode, text, (True, False)) != "tag:yaml.org,2002:str"


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


def _read_back(text: str, data: Any) -> str:
    """`text`, after checking that PyYAML (what reads rig files) reads it back as `data`."""
    import yaml

    try:
        same = yaml.safe_load(text) == data
    except yaml.YAMLError:
        same = False
    if not same:
        raise MergeError("the text written would not read back as the data given")
    return text


def fresh(data: Any, header: str = "") -> str:
    """`data` as a new YAML file: block style, keys in `data`'s order.

    Raises `MergeError` if PyYAML would not read the text back as `data`."""
    return _read_back(header + _dump(_yaml(2, 2, 0, FRESH_WIDTH), _prepare(data)), data)


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
    """(a dumper, the loaded document, a problem or None): how to write the file back exactly as it was.

    Layout and width are properties of the file, not of ruamel.yaml, and a
    wrong guess re-indents or re-folds lines nobody asked to change. So the
    measured layout and a few likelier widths (100, where this tool folds, and
    the file's longest line, with and without comments) are tried against the
    unchanged document, widest first because that leaves a flow-style entry
    that grows room to, and the first to reproduce the text exactly is used.
    If none does, the dumper is the closest and the problem names the first line
    that would move: the update is refused then, since guessing would rewrite
    lines the caller never meant to change."""
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
                return y, doc, None
            moved = [i for i, (a, b) in enumerate(zip(out, lines)) if a != b]
            score = len(moved) + abs(len(out) - len(lines))
            if best is None or score < best[0]:
                best = (score, out, moved[0] if moved else min(len(out), len(lines)), y)
    score, out, first, y = best
    was = lines[first] if first < len(lines) else "(end of file)"
    now = out[first] if first < len(out) else "(end of file)"
    return y, doc, (
        f"this file's layout cannot be reproduced, so updating it would re-lay-out lines that did not "
        f"change (the closest try differs from it at {score} line(s), first at line {first + 1}: "
        f"{was!r} would become {now!r}); nothing was written. Write that part the way ruamel.yaml "
        "does (one indent throughout, flow entries on one line), then retry")


# --------------------------------------------------------------------------- #
# Comments, counted from the text: how a lost or doubled one is noticed

def _comment_start(line: str, begin: int = 0) -> int | None:
    """Where the `# comment` in a line of YAML starts, if there is one, looking
    from column `begin`. A `#` inside a quoted string, or glued to the text
    before it (`uri#fragment`), is not one."""
    quote = ""
    last = ""
    skip = False
    for i, char in enumerate(line):
        if i < begin:
            continue
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


def _inside_scalars(text: str) -> dict[int, int | None]:
    """For each line that continues a scalar begun on an earlier one (a `|` or
    `>` block, a quoted string or a plain scalar over several lines): None if
    the whole line is scalar, else the column its scalar ends at. A `#` there
    is text, not a comment, and a line scan cannot tell. ruamel.yaml's own
    parse says where each scalar starts and ends."""
    from ruamel.yaml import YAML, YAMLError
    from ruamel.yaml.events import ScalarEvent

    inside: dict[int, int | None] = {}
    try:
        for event in YAML().parse(text):
            if isinstance(event, ScalarEvent) and event.end_mark.line > event.start_mark.line:
                for line in range(event.start_mark.line + 1, event.end_mark.line):
                    inside[line] = None
                if event.end_mark.column:
                    inside[event.end_mark.line] = event.end_mark.column
    except YAMLError:
        pass
    return inside


def _comments(text: str, whole_document: bool = False) -> list[str]:
    """Every `# comment` in a YAML text, in order. For a whole document pass
    `whole_document=True`, so that a `#` inside a multi-line scalar is not
    taken for one; otherwise the text is comment lines alone."""
    inside = _inside_scalars(text) if whole_document else {}
    found = []
    for number, line in enumerate(text.splitlines()):
        if number in inside and inside[number] is None:
            continue
        start = _comment_start(line, inside.get(number) or 0)
        if start is not None:
            found.append(line[start:].strip())
    return found


# --------------------------------------------------------------------------- #
# The text between two entries, which ruamel.yaml keeps on the first of them

def _is_map(node: Any) -> bool:
    from ruamel.yaml.comments import CommentedMap

    return isinstance(node, CommentedMap)


def _is_collection(node: Any) -> bool:
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    return isinstance(node, (CommentedMap, CommentedSeq))


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


def _is_block_scalar(value: Any) -> bool:
    from ruamel.yaml.scalarstring import FoldedScalarString, LiteralScalarString

    return isinstance(value, (LiteralScalarString, FoldedScalarString))


def _get_after(container: Any, ident: Any) -> str:
    """The comment text kept after `container[ident]`: its trailing comment, a newline, and the
    lines after it (the comments above whatever follows). "" if there is none.

    After a `|` or `>` scalar there is no trailing comment to speak of: ruamel.yaml keeps the
    lines under it in the very slot it keeps an eol comment in, and the text it holds starts at
    the first of them, so the eol is empty and all of it is "after"."""
    holder, key = _leaf(container, ident)
    slots = holder.ca.items.get(key)
    token = slots[_after_index(holder)] if slots else None
    found = ""
    if token is not None and not isinstance(token, list):
        found = ("\n" + _lead_text([token])) if _is_block_scalar(holder[key]) else token.value
    # What follows a list's last entry, if it is not a scalar, is kept on the list,
    # when that entry has no comment of its own to hold it. The entry may end deeper
    # (in an empty `{}`), so the list asked about is looked at as well as the leaf's.
    for owner, owner_key in ((holder, key), (container, ident)):
        if _is_last_of_list(owner, owner_key) and owner.ca.end:
            end = "\n" + _lead_text(owner.ca.end)
            return found + _split(end)[1] if found else end
        if owner is container:
            break
    return found


def _set_after(container: Any, ident: Any, value: str | None) -> None:
    """Replace everything kept after `container[ident]` with `value` (None for nothing).

    That is all of it: the entry's trailing comment, the lines after, and, when the
    entry is a list's last and ends deeper than itself, the list's end comments
    (`ca.end`), which are cleared here. A caller that means to keep any of that
    reads it with `_get_after` first and includes it in `value`."""
    from ruamel.yaml.error import CommentMark
    from ruamel.yaml.tokens import CommentToken

    holder, key = _leaf(container, ident)
    index = _after_index(holder)
    if holder is not container and _is_last_of_list(container, ident) and container.ca.end:
        container.ca.end[:] = []  # all of it is `value`'s now, by the contract above
    if _is_last_of_list(holder, key) and _is_collection(holder[key]):
        # Mirror how ruamel.yaml reads it: a comment on the entry's own line
        # takes everything after it into one token; otherwise the lines after
        # the entry belong to the list's end.
        eol, rest = _split(value or "")
        holder.ca.end[:] = [] if eol else _tokens(rest)
        if not eol:
            value = None
    slots = holder.ca.items.get(key)
    if value is not None and _is_block_scalar(holder[key]):
        # Only lines after it: the token starts at the first of them, at its column.
        eol, rest = _split(value)
        # `_get_after` reports no eol after a block scalar, and every caller builds
        # `value` from what it reported, so there is none to lose here. A raise
        # rather than an assert: `python -O` strips asserts, and a comment
        # silently dropped is the failure this module exists to prevent.
        if eol:
            raise MergeError(f"an eol comment {eol!r} after a block scalar has nowhere to go")
        indent = len(rest) - len(rest.lstrip(" "))
        value = rest[indent:] if rest.strip() else (rest or None)
        if value is not None:
            if slots is None:
                slots = holder.ca.items[key] = [None, None, None, None]
            slots[index] = CommentToken(value, CommentMark(indent if rest.strip() else 0))
            return
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


def _slot_lead(seq: Any, i: int) -> str:
    """The comment lines ruamel.yaml keeps on a list as the "before" comment of entry `i`.

    It does so when what precedes the entry ends in an empty `{}` or `[]`, which
    has no trailing slot of its own to hold them."""
    slots = seq.ca.items.get(i)
    return _lead_text(slots[1]) if slots and isinstance(slots[1], list) else ""


def _gap(gaps: list[tuple[str, str]], pairs: list[int | None], j: int) -> str:
    """The blank lines to open the text above entry `j` of the reordered list with.

    A gap goes with the entry below it, so the blank line that set `c` apart is
    still there when `c` goes first elsewhere. Nothing is above the first entry,
    so what opens the old list stays at the top; the blank line the entry that
    moves up brings with it goes to the one that moved from the top, which has
    none of its own (and to nobody if there is no such entry). An entry new to
    the list has none."""
    if j == 0:
        return gaps[0][0]
    i = pairs[j]
    if i is None:
        return ""
    if i > 0:
        return gaps[i][0]
    first = pairs[0]
    return gaps[first][0] if first else ""


def _lead_text(tokens: list[Any]) -> str:
    """Comment tokens as the lines they were read from."""
    return "".join(" " * t.column + t.value for t in tokens)


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


def _unfold_lead(container: Any, parent: tuple[Any, Any] | None) -> None:
    """Split what ruamel.yaml folds into one token when the key above a collection has a
    comment of its own: `chain:  # the chain` then `  # about a` is read as the single
    token '# the chain\\n  # about a\\n', on the key, with nothing "above the first entry".
    The first line stays where it is (it is the key's); the rest becomes the lead, as
    it would be had the key no comment. Does nothing otherwise, and only once."""
    if parent is None or not _is_map(parent[0]) or not container.ca.comment:
        return
    eol = container.ca.comment[0]
    if eol is None or isinstance(eol, list) or container.ca.comment[1]:
        return
    first, rest = _split(eol.value)
    if not _has_comment(rest):
        return
    eol.value = first + "\n"  # the very token the key holds in its own slot
    tokens = _tokens(rest)
    container.ca.comment[1] = tokens
    parent[0].ca.items.setdefault(parent[1], [None, None, None, None])[3] = tokens


def _lead(container: Any, parent: tuple[Any, Any] | None) -> list[Any] | None:
    """The comment tokens written above a collection's first entry. For a list,
    for the top mapping, ruamel.yaml keeps them on the collection itself; for a
    mapping under a key, on that key (`parent` is the (container, key) holding
    it); for a mapping that is a list item there is no such place. When the key
    has a comment of its own they are folded into it, and are split off here."""
    _unfold_lead(container, parent)
    if parent is None or not _is_map(container):
        return container.ca.comment[1] if container.ca.comment else None
    holder, key = parent
    if not _is_map(holder):
        return None
    slots = holder.ca.items.get(key)
    return slots[3] if slots and isinstance(slots[3], list) else None


def _clear_lead(container: Any, parent: tuple[Any, Any] | None) -> None:
    if parent is None or not _is_map(container):
        if container.ca.comment:
            shared = container.ca.comment[1]
            container.ca.comment[1] = None
            # A list under a key has the very same tokens on that key as well, and
            # that is the copy ruamel.yaml writes: left there, the comment would
            # come back out after being cleared (or hide what is added in its place).
            if parent is not None and _is_map(parent[0]):
                slots = parent[0].ca.items.get(parent[1])
                if slots and shared is not None and slots[3] is shared:
                    slots[3] = None
    elif parent[0].ca.items.get(parent[1]):
        parent[0].ca.items[parent[1]][3] = None


def _own_lead(lead: list[Any]) -> list[Any]:
    """The tokens of a lead that are about the entry below: those after the last
    blank line. What a blank line sets apart is a heading for the whole collection."""
    last = max((i for i, t in enumerate(lead) if t.value.endswith("\n\n")), default=-1)
    return lead[last + 1:]


def _add_lead(container: Any, parent: tuple[Any, Any] | None, text: str) -> bool:
    """Put comment lines above a collection's first entry, after any already
    there. False if the collection has nowhere to keep them."""
    tokens = _tokens(text)
    if not tokens:
        return True
    existing = _lead(container, parent)
    if existing is not None:
        existing.extend(tokens)
    elif parent is None or not _is_map(container):
        if container.ca.comment is None:
            container.ca.comment = [None, tokens]
        else:
            container.ca.comment[1] = tokens
        if parent is not None and _is_map(parent[0]):
            # As the parser leaves a list under a key: the same tokens on the key
            # too. Once the key holds any comment slot of its own, ruamel.yaml
            # writes what is there and not what the list holds.
            parent[0].ca.items.setdefault(parent[1], [None, None, None, None])[3] = tokens
    elif _is_map(parent[0]):
        parent[0].ca.items.setdefault(parent[1], [None, None, None, None])[3] = tokens
    else:
        return False
    return True


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


def _pairing(old: list, new: list) -> list[int | None]:
    """For each entry of `new`, the index in `old` of the entry it is, else None (a new one).

    Same name, same entry, even when two plugins swap names and keep their uris:
    a name is what the player sees and calls a plugin by, a swap of names between
    two plugins is rare, and the comments follow the names. Of those left over, an old and a new entry are the
    same plugin renamed when they share an identity key (`IDENTITY_KEYS`), but
    only if that is the one candidate on both sides: two plugins on one uri,
    both renamed, cannot be told apart, and guessing would hand one the other's
    comments, so they are a removal and an insertion (comments dropped, and said
    so by the count check, never misattached)."""
    where = {item["name"]: i for i, item in enumerate(old)}
    pairs: list[int | None] = [where.get(item["name"]) for item in new]
    free_old = [i for i in range(len(old)) if i not in pairs]
    free_new = [j for j, p in enumerate(pairs) if p is None]
    for j in free_new:
        mates = [i for i in free_old if _renamed(old[i], new[j])]
        if len(mates) == 1 and sum(_renamed(old[mates[0]], new[k]) for k in free_new) == 1:
            pairs[j] = mates[0]
    return pairs


def _split_gap(text: str) -> tuple[str, str]:
    """(the blank lines the text between two entries opens with, the rest of it).

    Blank lines separate entries and belong to the gap, not to the entry below
    it, so when entries move the gaps keep theirs. The blank lines after a
    comment belong to that comment (they set it apart) and stay with it."""
    lines = text.splitlines(keepends=True)
    blank = 0
    while blank < len(lines) and not lines[blank].strip():
        blank += 1
    return "\n" * blank, "".join(lines[blank:])


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
            self._drop_lead(container, parent)
            if _has_comment(rest):
                raise MergeError(f"cannot remove the only entry {ident!r} of a list or mapping while "
                                 "comments follow it: ruamel.yaml would drop them. Move or delete "
                                 "the comments, then retry")
        else:
            # The comments above a list's first entry stay: they are usually its
            # heading. Above a mapping's first key they are about that key, so
            # they go with it, unless a blank line sets them apart as a heading.
            if _is_map(container):
                self._drop_lead(container, parent, own_only=True)
            if _has_comment(rest) and not _add_lead(container, parent, rest):
                raise MergeError(
                    f"cannot remove {ident!r}, the first key of a mapping, while comments sit below it: "
                    "this mapping has nowhere to keep them. Move or delete the comments, then retry")
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

    def _drop_lead(self, container: Any, parent: tuple[Any, Any] | None, own_only: bool = False) -> None:
        """Drop the comments above a collection's first entry: all, or only the
        ones about that entry, not a heading a blank line sets apart."""
        lead = _lead(container, parent)
        if lead:
            gone = _own_lead(lead) if own_only else lead
            tokens: list[str] = []
            _token_comments(gone, tokens)
            self.gone += tokens
            if gone is lead or len(gone) == len(lead):
                _clear_lead(container, parent)
            else:
                del lead[len(lead) - len(gone):]

    def insert(self, container: Any, pos: int, ident: Any, node: Any,
               parent: tuple[Any, Any] | None = None) -> None:
        """Insert one entry; the comments written above the entry now after it stay above it.

        At the front of a list the heading stays at the top and the new entry
        goes under it: a comment that was about the old first entry in
        particular cannot be told from a heading, and a heading is the commoner
        (a chain's). At the front of a mapping the comment above the old first
        key stays with that key, unless a blank line sets it apart."""
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
            own = _own_lead(lead) if lead else []
            if own:
                rest = _lead_text(own)
                del lead[len(lead) - len(own):]
                if not lead:
                    _clear_lead(container, parent)
        if _is_map(container):
            container.insert(pos, ident, node)
            key = ident
        else:
            container.insert(pos, node)
            key = pos
        if rest:
            _set_after(container, key, _join("", rest))

    def reorder(self, old: Any, new: list, pairs: list[int | None],
                parent: tuple[Any, Any] | None = None) -> None:
        """Put a name-matched list in the order of `new`, each entry keeping its comments.

        What belongs to an entry: the comment lines above it, and whatever is
        written inside it or beside it (its trailing comment). ruamel.yaml
        stores the lines above an entry on the entry *before* it, so a plain
        reorder would carry them to the wrong plugin; here they are taken off
        first, and put back above the entry they were written above, wherever
        it went. What stays with the *position*: a heading a blank line sets
        apart at the top, and whatever follows the last entry (it belongs to
        what comes after the list). A blank line between entries goes with the
        entry below it, `_gap` says how.

        An entry that is not in `new` goes with its comments, including the
        ones directly above it when it was the first (a plain `remove` leaves
        those at the top; here the entries are known, so they are about it);
        only a heading a blank line sets apart stays. Entries new to the list
        go where `new` puts them, with no comments. Refuses (`MergeError`) a list whose comments are kept
        somewhere this cannot read them back from.

        `pairs` is `_pairing(old, new)`: for each entry of `new`, the index of the
        old entry it is, or None."""
        kept = {i for i in pairs if i is not None}
        n = len(old)

        # What is written above each entry, and what follows the last. ruamel.yaml
        # keeps it on the entry before, except after an empty `{}` or `[]`, where
        # it is on the list, as that entry's "before" comment.
        after = [_split(_get_after(old, i)) for i in range(n)]
        for i in range(n):
            self._refuse_unreadable(old, i)
        before = [_slot_lead(old, i) for i in range(n)]
        lead = _lead(old, parent)
        own = _own_lead(lead) if lead else []
        leads = [_lead_text(own)] + [after[i - 1][1] + before[i] for i in range(1, n)]
        tail = after[-1][1]
        if own:
            del lead[len(lead) - len(own):]
            if not lead:
                _clear_lead(old, parent)
        # Leave on each entry only what is its own, its trailing comment.
        for i in range(n):
            _set_after(old, i, _join(after[i][0], ""))
        # An entry written in flow style keeps its trailing comment in the list's
        # slot for it, not in itself; those slots go along with the entries.
        slots = {i: [old.ca.items.pop(i)[0], None, None, None]
                 for i in range(n) if i in old.ca.items and old.ca.items[i][0] is not None}
        old.ca.items.clear()
        for i in range(n):
            if i not in kept:
                self.gone += _all_comments(old[i]) + _comments(leads[i]) \
                    + (_comments(after[i][0]) if i in slots else [])

        for j, i in enumerate(pairs):
            if i is not None:
                self.item(old, i, new[j])
        nodes = [old[i] for i in pairs if i is not None]
        for i in reversed(range(n)):
            del old[i]
        for k, node in enumerate(nodes):
            old.insert(k, node)
        for j, i in enumerate(pairs):
            if i is None:
                old.insert(j, _build(new[j], _flow_near(old, j)))
        for j, i in enumerate(pairs):
            if i in slots:
                old.ca.items[j] = slots[i]

        # Put the text back: above each entry the blank lines its gap had and the
        # comment lines it had; after the last, what followed the list.
        gaps = [_split_gap(text) for text in leads]
        above = [_gap(gaps, pairs, j) + (gaps[i][1] if i is not None else "")
                 for j, i in enumerate(pairs)]
        _add_lead(old, parent, above[0])
        # The lines above entry j go on the list as its "before" comment, which
        # ruamel.yaml writes whatever the entry before it ended in; on that entry
        # they would be a trailing comment, and for a flow entry that is written
        # wrongly when the list has a heading of its own.
        for j in range(1, len(pairs)):
            if above[j]:
                old.ca.items.setdefault(j, [None, None, None, None])[1] = _tokens(above[j])
        last = len(pairs) - 1
        _set_after(old, last, _join(_split(_get_after(old, last))[0], tail))
        # Read the tail back through ruamel's own storage: it is the one place the text
        # goes through `_set_after`'s rules (a list's end, a block scalar's slot) rather
        # than straight into tokens.
        if _split(_get_after(old, last))[1] != tail:
            raise MergeError("cannot reorder this list: what follows its last entry would not stay "
                             "where it was put. Move or delete it, then retry")

    @staticmethod
    def _refuse_unreadable(old: Any, i: int) -> None:
        """Refuse an entry whose comments sit in a slot of the list that `reorder` does not read."""
        slots = old.ca.items.get(i)
        if slots and (any(x is not None for x in slots[2:]) or (slots[0] is not None and _is_block(old[i]))
                      or (slots[1] is not None and not isinstance(slots[1], list)) or (i == 0 and slots[1])):
            raise MergeError(
                f"cannot reorder this list: entry {i + 1} has comments in a place this tool does not know "
                "how to move them from. Move the comments, then retry")

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
            pairs = _pairing(old, new)
            kept = [i for i in pairs if i is not None]
            in_place = pairs == list(range(len(old)))  # every entry the one at its own position
            renamed = any(i is not None and old_names[i] != new[j]["name"] for j, i in enumerate(pairs))
            # Out of order, or renamed beside an insertion or a removal: the diff below
            # works on names alone and would take a rename for a removal and an insertion.
            if kept != sorted(kept) or (renamed and not in_place):
                self.reorder(old, new, pairs, parent)
                return
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
        y, doc, layout_problem = _choose(text)
    except YAMLError as exc:
        raise MergeError(f"not YAML: {exc}") from exc
    if not _is_map(doc):
        raise MergeError("the top level is not a mapping")
    before = Counter(_comments(text, True))
    lost = before - Counter(_comments(_dump(y, doc), True))
    if lost:
        raise MergeError(
            f"ruamel.yaml would drop {sum(lost.values())} comment(s) from this file before any change "
            f"is made, first {next(iter(lost))!r}; nothing was written. (A comment line directly above "
            "a flow-style `- {...}` entry that has its own trailing comment is one case.)")
    if layout_problem:
        raise MergeError(layout_problem)
    update = _Update()
    update.map(doc, _prepare(data))
    out = _dump(y, doc)
    expected = before - Counter(update.gone)
    after = Counter(_comments(out, True))
    if after != expected:
        extra, missing = after - expected, expected - after
        what = (f"{sum(extra.values())} comment(s) written more than once, first {next(iter(extra))!r}"
                if extra else f"{sum(missing.values())} comment(s) lost, first {next(iter(missing))!r}")
        raise MergeError(f"the update would leave {what}; nothing was written")
    return _read_back(out, data)
