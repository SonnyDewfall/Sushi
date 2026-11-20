"""The plugin library (#32): every plugin the rig may load, as a list to pick from.

The library is the manifest, not the machine. `plugin-manifest.txt` lists every
URI the rig may use and `plugins/` holds their bundles (see `plugins.py`); a
plugin installed on this machine but not in the manifest is not the rig's, and
one in the manifest but missing from `plugins/` is still listed, saying so. So
only `plugins/` is read, never the system's LV2 paths: the list is what the rig
can load, not what this laptop happens to have.

Each entry carries the URI, a name to show, the port count, whether a component
profile exists (`tool/examples/profiles/`, see `profile.py`) and, when it does,
the profile's groups, which are what the Library mockup shows under a plugin.

## Read without lilv

lilv is a system package with no wheel (`probe.py`), and the agent runs where it
may not be installed, so the name and port count are read from the bundles'
Turtle files directly by `parse_turtle`: enough of Turtle 1.1 for what LV2
bundles are written in (prefixes, relative IRIs, literals, nested blank nodes,
collections), not a general RDF store. It reads a bundle the way lilv does for
these two facts -- `manifest.ttl`, then the `rdfs:seeAlso` data files of what it
declares `a lv2:Plugin` -- and takes the plugin's own `doap:name` and the number
of distinct `lv2:port` objects. It does not follow `owl:imports`, read
`lv2:extensionData`, or infer anything; lilv does none of that for a name either.

Checked against lilv 0.24.22 on every bundle of a `plugins/` assembled from
Ubuntu 24.04's packages (54 of the 55 URIs the manifest had then; the amp is
not packaged): every name and port count agreed (2026-11-02). The manifest's
56th, Rubber Band 4.0's Live shifter (#164), is built rather than packaged
(docs/reference/plugins.md). `test_library.py` repeats the
comparison wherever lilv and `plugins/` are both present.

Where a name cannot be read -- the bundle is not in `plugins/`, a data file is
missing or does not parse, a construct this reader does not know -- the entry
falls back to the URI's last segment, is marked `name_from: "uri"`, and its
`note` says why and that lilv (`sushi-rig probe`) reads the format in full. A
profile's restated name (`plugin.name`) comes before that fallback, marked
`"profile"`. Nothing falls back silently.

## Kinds

The Library drawer groups the plugins by what they do (#130). That is read
from each plugin's own LV2 classes, the `a lv2:DelayPlugin` its bundle
declares (`kind_of`, `KINDS`), never from its name: a plugin whose bundle says
none of the six kinds, or whose bundle is not in `plugins/`, has no kind and
is shown under "Other".

## Not every entry can be added

The neural amp is in the manifest because `plugins/` must carry it, but it runs
in Carla, not Sushi: Sushi's LV2 wrapper cannot set its model (`amp.py`). Added
to a Sushi chain it would load and pass audio with no model, which is the kind
of silent half-success this project has been bitten by, so its entry is
`addable: false` with the reason, and the agent refuses it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from .amp import PLUGIN_URIS as AMP_PLUGIN_URIS
from .plugins import ARCHIVE_DIR, read_manifest

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDF_TYPE = RDF + "type"
RDFS_SEE_ALSO = "http://www.w3.org/2000/01/rdf-schema#seeAlso"
LV2 = "http://lv2plug.in/ns/lv2core#"
LV2_PLUGIN = LV2 + "Plugin"
LV2_PORT = "http://lv2plug.in/ns/lv2core#port"
DOAP_NAME = "http://usefulinc.com/ns/doap#name"
XSD = "http://www.w3.org/2001/XMLSchema#"

MANIFEST_FILE = "plugin-manifest.txt"
PROFILES_DIR = Path("tool") / "examples" / "profiles"


class LibraryError(Exception):
    """The library cannot be built at all (the manifest is unreadable)."""


class TurtleError(ValueError):
    """Text that is not Turtle this reader can read, with where."""


# --------------------------------------------------------------------------- #
# Turtle, pure
# --------------------------------------------------------------------------- #

class IRI(str):
    """An absolute IRI."""


class BNode(str):
    """A blank node, by a label unique within one parse."""


class Literal(str):
    """A literal's lexical form, with its language tag or datatype if any."""

    lang: str | None
    datatype: str | None

    def __new__(cls, value: str, lang: str | None = None, datatype: str | None = None):
        self = super().__new__(cls, value)
        self.lang, self.datatype = lang, datatype
        return self


# One alternation, tried in this order at each position. Long strings before
# short ones (`"""` would otherwise read as an empty string and a quote); the
# directives before language tags; numbers before names. A DECIMAL needs a
# digit after its point, which is what keeps `lv2:index 0 .` an integer and the
# end of a statement rather than `0.`. A prefixed name's local part may hold a
# '.' but not end with one, for the same reason.
_PN_LOCAL_CHAR = r"(?:[\w:%-]|\\[^\s])"
_TOKEN = re.compile(r"""
    (?P<ws>(?:\s+|\#[^\n]*)+)
  | <(?P<iri>[^<>"{}|^`\\\x00-\x20]*)>
  | (?P<string>\"\"\"(?:[^"\\]|\\.|"(?!""))*\"\"\"|'''(?:[^'\\]|\\.|'(?!''))*'''
                |"(?:[^"\\\n\r]|\\.)*"|'(?:[^'\\\n\r]|\\.)*')
  | (?P<directive>@prefix\b|@base\b)
  | @(?P<lang>[A-Za-z]+(?:-[A-Za-z0-9]+)*)
  | (?P<caret>\^\^)
  | _:(?P<bnode>[\w-]+(?:\.+[\w-]+)*)
  | (?P<number>[+-]?(?:\d+\.\d*[eE][+-]?\d+|\.\d+[eE][+-]?\d+|\d+[eE][+-]?\d+|\d*\.\d+|\d+))
  | (?P<pname>(?P<prefix>(?:[^\W\d_](?:[\w.-]*[\w-])?)?):
        (?P<local>(?:""" + _PN_LOCAL_CHAR + r"""(?:(?:""" + _PN_LOCAL_CHAR + r"""|\.)*"""
        + _PN_LOCAL_CHAR + r""")?)?))
  | (?P<word>[A-Za-z]+)
  | (?P<punct>[;,.\[\]()])
""", re.VERBOSE)

_ESCAPES = {"t": "\t", "b": "\b", "n": "\n", "r": "\r", "f": "\f", '"': '"', "'": "'", "\\": "\\"}
_UCHAR = re.compile(r"\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})|\\(.)", re.DOTALL)


def _unescape(text: str) -> str:
    def one(match: re.Match) -> str:
        if match.group(1) or match.group(2):
            return chr(int(match.group(1) or match.group(2), 16))
        char = match.group(3)
        if char not in _ESCAPES:
            raise TurtleError(f"unknown escape \\{char}")
        return _ESCAPES[char]
    return _UCHAR.sub(one, text)


def _tokens(text: str) -> list[tuple[str, Any, int]]:
    """`(kind, value, line)` for each token; whitespace and comments dropped.

    The line is counted as the text is consumed, over each token's own span.
    Counted from the start of the text for every token, it made reading a file
    quadratic: 131 KB took a second (found by review), and the library reads
    megabytes of Turtle before its first answer, under its lock.
    """
    out: list[tuple[str, Any, int]] = []
    pos = 0
    line = 1
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise TurtleError(f"line {line}: unexpected {text[pos:pos + 20]!r}")
        kind = match.lastgroup
        if kind in ("prefix", "local"):  # inner groups of a pname
            kind = "pname"
        if kind == "pname":
            out.append(("pname", (match.group("prefix"), match.group("local")), line))
        elif kind == "string":
            raw = match.group("string")
            quote = 3 if raw[:3] in ('"""', "'''") else 1
            try:
                value = _unescape(raw[quote:-quote])
            except TurtleError as exc:  # every TurtleError says where
                raise TurtleError(f"line {line}: {exc}") from None
            out.append(("string", value, line))
        elif kind != "ws":
            out.append((kind, match.group(kind), line))
        line += text.count("\n", pos, match.end())
        pos = match.end()
    return out


_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _resolved(base: str, raw: str) -> str:
    """IRI `raw` against `base`, as serd resolves it. An absolute one is
    itself: `urljoin` drops an empty fragment, and under an `@base` of the
    same scheme it made `<http://lv2plug.in/ns/lv2core#>` the prefix
    `...lv2core`, so `lv2:Plugin` read as `...lv2corePlugin` and a bundle's
    whole manifest declared nothing (review of PR #198). A relative one
    ending in `#` keeps it for the same reason."""
    if _SCHEME.match(raw):
        return raw
    joined = urljoin(base, raw)
    if raw.endswith("#") and not joined.endswith("#"):
        joined += "#"
    return joined


class _Parser:
    """Recursive descent over the token list, appending triples as it goes."""

    def __init__(self, tokens: list[tuple[str, Any, int]], base: str, blank_prefix: str = ""):
        self.tokens = tokens
        self.blank_prefix = blank_prefix
        self.i = 0
        self.base = base
        self.prefixes: dict[str, str] = {}
        self.triples: list[tuple[str, str, str]] = []
        self.blanks = 0

    # -- the token stream
    def peek(self) -> tuple[str, Any, int]:
        return self.tokens[self.i] if self.i < len(self.tokens) else ("end", None, self._last_line())

    def _last_line(self) -> int:
        return self.tokens[-1][2] if self.tokens else 1

    def take(self) -> tuple[str, Any, int]:
        token = self.peek()
        self.i += 1
        return token

    def expect_punct(self, char: str) -> None:
        kind, value, line = self.take()
        if (kind, value) != ("punct", char):
            raise TurtleError(f"line {line}: expected {char!r}, found {value!r}")

    def at_punct(self, char: str) -> bool:
        kind, value, _ = self.peek()
        return kind == "punct" and value == char

    # -- terms
    def iri(self, raw: str) -> IRI:
        return IRI(_resolved(self.base, raw))

    def pname(self, prefix: str, local: str, line: int) -> IRI:
        if prefix not in self.prefixes:
            raise TurtleError(f"line {line}: prefix {prefix!r} is not declared")
        return IRI(self.prefixes[prefix] + re.sub(r"\\(.)", r"\1", local))

    def fresh(self) -> BNode:
        self.blanks += 1
        return BNode(f"{self.blank_prefix}b{self.blanks}")

    def labelled(self, label: str) -> BNode:
        return BNode(f"{self.blank_prefix}_{label}")

    # -- grammar
    def document(self) -> list[tuple[str, str, str]]:
        while self.peek()[0] != "end":
            self.statement()
        return self.triples

    def statement(self) -> None:
        kind, value, line = self.peek()
        if kind == "directive" or (kind == "word" and value.upper() in ("PREFIX", "BASE")):
            self.take()
            sparql = kind == "word"
            if value.lower().lstrip("@") == "prefix":
                pkind, pvalue, pline = self.take()
                if pkind != "pname" or pvalue[1]:
                    raise TurtleError(f"line {pline}: a prefix declaration needs 'name:'")
                ikind, ivalue, iline = self.take()
                if ikind != "iri":
                    raise TurtleError(f"line {iline}: a prefix needs an <IRI>")
                self.prefixes[pvalue[0]] = self.iri(ivalue)
            else:
                ikind, ivalue, iline = self.take()
                if ikind != "iri":
                    raise TurtleError(f"line {iline}: a base needs an <IRI>")
                self.base = self.iri(ivalue)
            if not sparql:
                self.expect_punct(".")
            return
        if self.at_punct("["):
            subject = self.blank_property_list()
            if not self.at_punct("."):
                self.predicate_object_list(subject)
        else:
            subject = self.subject()
            self.predicate_object_list(subject)
        self.expect_punct(".")

    def subject(self) -> str:
        kind, value, line = self.take()
        if kind == "iri":
            return self.iri(value)
        if kind == "pname":
            return self.pname(*value, line)
        if kind == "bnode":
            return self.labelled(value)
        if kind == "punct" and value == "(":
            self.i -= 1
            return self.collection()
        raise TurtleError(f"line {line}: expected a subject, found {value!r}")

    def predicate_object_list(self, subject: str) -> None:
        while True:
            predicate = self.verb()
            self.object_list(subject, predicate)
            if not self.at_punct(";"):
                return
            while self.at_punct(";"):
                self.take()
            # A trailing ';' before '.' or ']' is allowed.
            if self.at_punct(".") or self.at_punct("]") or self.peek()[0] == "end":
                return

    def verb(self) -> str:
        kind, value, line = self.take()
        if kind == "word" and value == "a":
            return IRI(RDF_TYPE)
        if kind == "iri":
            return self.iri(value)
        if kind == "pname":
            return self.pname(*value, line)
        raise TurtleError(f"line {line}: expected a predicate, found {value!r}")

    def object_list(self, subject: str, predicate: str) -> None:
        self.triples.append((subject, predicate, self.object()))
        while self.at_punct(","):
            self.take()
            self.triples.append((subject, predicate, self.object()))

    def object(self) -> str:
        kind, value, line = self.peek()
        if kind == "punct" and value == "[":
            return self.blank_property_list()
        if kind == "punct" and value == "(":
            return self.collection()
        self.take()
        if kind == "iri":
            return self.iri(value)
        if kind == "pname":
            return self.pname(*value, line)
        if kind == "bnode":
            return self.labelled(value)
        if kind == "number":
            datatype = "decimal" if "." in value and "e" not in value.lower() else (
                "double" if "e" in value.lower() else "integer")
            return Literal(value, datatype=XSD + datatype)
        if kind == "word" and value in ("true", "false"):
            return Literal(value, datatype=XSD + "boolean")
        if kind == "string":
            nkind, nvalue, nline = self.peek()
            if nkind == "lang":
                self.take()
                return Literal(value, lang=nvalue)
            if nkind == "caret":
                self.take()
                dkind, dvalue, dline = self.take()
                if dkind == "iri":
                    return Literal(value, datatype=self.iri(dvalue))
                if dkind == "pname":
                    return Literal(value, datatype=self.pname(*dvalue, dline))
                raise TurtleError(f"line {dline}: a datatype needs an IRI")
            return Literal(value)
        raise TurtleError(f"line {line}: expected an object, found {value!r}")

    def blank_property_list(self) -> BNode:
        self.expect_punct("[")
        node = self.fresh()
        if not self.at_punct("]"):
            self.predicate_object_list(node)
        self.expect_punct("]")
        return node

    def collection(self) -> str:
        self.expect_punct("(")
        items = []
        while not self.at_punct(")"):
            if self.peek()[0] == "end":
                raise TurtleError(f"line {self._last_line()}: a collection is never closed")
            items.append(self.object())
        self.take()
        if not items:
            return IRI(RDF + "nil")
        head = node = self.fresh()
        for index, item in enumerate(items):
            self.triples.append((node, RDF + "first", item))
            rest = self.fresh() if index < len(items) - 1 else IRI(RDF + "nil")
            self.triples.append((node, RDF + "rest", rest))
            node = rest
        return head


def parse_turtle(text: str, base: str, blank_prefix: str = "") -> list[tuple[str, str, str]]:
    """The triples of a Turtle document, in order: `(subject, predicate,
    object)`, where an IRI is `IRI` (relative ones resolved against `base`),
    a blank node `BNode` and a literal `Literal`. `TurtleError`, with the line,
    for what it cannot read. Pure.

    A blank node is scoped to its document, so the triples of two parses can be
    put together only if their blank nodes cannot meet: `blank_prefix` starts
    every blank node's label, and each document read together needs its own.
    Without it two files' anonymous ports were the same two nodes, and a plugin
    with two in each file had two (found by review).
    """
    return _Parser(_tokens(text), base, blank_prefix).document()


def plugins_described(triples: list[tuple[str, str, str]]) -> dict[str, dict[str, Any]]:
    """`{uri: {"name", "ports", "classes"}}` for every subject typed `lv2:Plugin`.

    The name is the subject's own `doap:name` -- a bundle's file often names the
    project too, in the same file -- preferring one with no language tag, then
    English, as lilv's `get_name` does; None if it has none. `ports` counts
    distinct `lv2:port` objects (Rubber Band's plugins share one latency port
    node), None if it declares none, since a plugin with no ports at all is not
    something these files describe. `classes` are its LV2 classes besides
    `lv2:Plugin`, by local name (`["DelayPlugin"]`), and `[]` for none. Pure.
    """
    # Indexed in one pass and looked up per plugin: a scan of every triple per
    # plugin was plugins x triples (found by review).
    index: dict[str, dict[str, list[str]]] = {}
    plugins: list[str] = []
    for s, p, o in triples:
        index.setdefault(s, {}).setdefault(p, []).append(o)
        if p == RDF_TYPE and o == LV2_PLUGIN and isinstance(s, IRI):
            plugins.append(s)
    described: dict[str, dict[str, Any]] = {}
    for uri in dict.fromkeys(plugins):
        about = index.get(uri, {})
        names = [o for o in about.get(DOAP_NAME, []) if isinstance(o, Literal)]
        names.sort(key=lambda n: (n.lang is not None, n.lang != "en"))
        ports = set(about.get(LV2_PORT, []))
        # The plugin's own LV2 classes (`a lv2:DelayPlugin`), by their local
        # names, sorted: what its kind is read from (`kind_of`). lv2:Plugin
        # itself says nothing of what it does, and a class outside LV2's own
        # vocabulary (doap:Project, which DISTRHO's carry) is not a kind.
        classes = sorted({str(o)[len(LV2):] for o in about.get(RDF_TYPE, [])
                          if isinstance(o, IRI) and o.startswith(LV2) and o != LV2_PLUGIN})
        described[str(uri)] = {
            "name": str(names[0]) if names else None,
            "ports": len(ports) or None,
            "classes": classes,
        }
    return described


def uri_segment(uri: str) -> str:
    """The last segment of a URI, for a name when none can be read: after the
    last '#', '/' or ':', ignoring a trailing separator."""
    return re.split(r"[#/:]", uri.rstrip("#/:"))[-1] or uri


# --------------------------------------------------------------------------- #
# Bundles: effects (reading files), nothing else
# --------------------------------------------------------------------------- #

def _read_turtle(path: Path, problems: list[str], blank_prefix: str = "") -> list[tuple[str, str, str]]:
    # `path` must be absolute (`read_bundle` resolves its bundle): `as_uri`
    # raises ValueError for a relative one, which escaped as a 500.
    try:
        data = path.read_bytes()
    except OSError as exc:
        problems.append(f"{path.parent.name}/{path.name} cannot be read: {exc}")
        return []
    # A byte-order mark is dropped, as serd drops it; a byte that is not
    # UTF-8 (a Latin-1 "é" in a literal) is replaced rather than losing the
    # whole file, since serd says so and lilv still loads the bundle (both
    # found by review of PR #198, against lilv 0.24.22).
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        text = data.decode("utf-8-sig", errors="replace")
        problems.append(f"{path.parent.name}/{path.name} is not UTF-8 throughout ({exc.reason} at byte "
                        f"{exc.start}): read with what could not be decoded replaced")
    try:
        return parse_turtle(text, path.as_uri(), blank_prefix)
    except TurtleError as exc:
        problems.append(f"{path.parent.name}/{path.name} is not Turtle this reader can read: {exc}")
    return []


def read_bundle(bundle: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """`({uri: {"name", "ports", "bundle"}}, problems)` for one LV2 bundle.

    `manifest.ttl` says what the bundle declares; the plugin data files are the
    `rdfs:seeAlso` objects of the subjects it declares `a lv2:Plugin`, which is
    where lilv looks for a plugin's data. Only those: mda's manifest also points
    at a presets file per preset, which hold no name. A data file outside the
    bundle is not followed (a bundle is one folder, and `plugins/` is copied
    bundle by bundle). Every file that cannot be read is a problem, said once.
    """
    problems: list[str] = []
    name = bundle.name
    # Resolved first: a relative plugins folder (a relative --root, a test's
    # cwd) crashed the build in `as_uri` (found by review).
    bundle = root = bundle.resolve()
    manifest = _read_turtle(bundle / "manifest.ttl", problems, "f0.")
    declared = plugins_described(manifest)
    data: list[Path] = []
    files: dict[str, list[Path | None]] = {}  # each plugin's data files; None for one not followed
    for s, p, o in manifest:
        if p != RDFS_SEE_ALSO or str(s) not in declared or not isinstance(o, IRI):
            continue
        parsed = urlparse(o)
        path = Path(unquote(parsed.path)) if parsed.scheme == "file" else None
        if path is None or root not in path.resolve().parents:
            problems.append(f"{name}: {o} is outside the bundle, so it is not read")
            files.setdefault(str(s), []).append(None)
            continue
        files.setdefault(str(s), []).append(path)
        if path not in data:
            data.append(path)
    triples = list(manifest)
    unread: set[Path] = set()
    for number, path in enumerate(data, 1):
        said = len(problems)
        triples += _read_turtle(path, problems, f"f{number}.")
        if len(problems) > said:
            unread.add(path)
    described = plugins_described(triples)
    found = {uri: {**described[uri], "bundle": name} for uri in declared}
    # A plugin whose own data was not all read has classes not known (None),
    # which is not the same as classes it does not declare ([]).
    for uri, paths in files.items():
        if any(path is None or path in unread for path in paths):
            found[uri]["classes"] = None
    return found, problems


def scan_plugins(plugins_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Every plugin in the bundles directly under `plugins_dir`, and the
    problems met. That folder only -- `plugins/` is flat (see `plugins.py`) --
    and never `archive/`, which holds what is off the path. A URI two bundles
    declare is the first's, in name order, and said."""
    if not plugins_dir.is_dir():
        return {}, [f"no plugins folder at {plugins_dir}, so no names or port counts "
                    "could be read: `sushi-rig plugins assemble` builds it"]
    found: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for bundle in sorted(plugins_dir.iterdir()):
        if not bundle.is_dir() or bundle.name == ARCHIVE_DIR:
            continue
        if not (bundle / "manifest.ttl").exists():
            # Said, not passed over: skipped silently, its plugins were
            # reported "not found in plugins/", which points at the wrong cause.
            problems.append(f"{bundle.name} in {plugins_dir} has no manifest.ttl, so it is not an "
                            "LV2 bundle lilv or Sushi can read: copy it again from its package")
            continue
        plugins, said = read_bundle(bundle)
        problems += said
        for uri, meta in plugins.items():
            if uri in found:
                problems.append(f"{uri} is declared by {found[uri]['bundle']} and {bundle.name}; "
                                f"listed from {found[uri]['bundle']}")
                continue
            found[uri] = meta
    return found, problems


LV2_BINARY = LV2 + "binary"


def manifest_plugins(folder: Path) -> tuple[dict[str, tuple[Path, Path | None]], list[Path]]:
    """`({uri: (bundle, binary)}, unread)` for every plugin a bundle directly
    under `folder` declares in its `manifest.ttl`, without lilv: how lilv
    finds which URIs an LV2 directory provides. `binary` is the file its
    `lv2:binary` names there (the LV2 spec puts it in the manifest), None
    where the manifest names none. `unread` is each bundle whose manifest
    could not be read or parsed, which may provide what this does not see.
    Only the manifests are read, not the plugins' data, so it is quick
    enough to run before every start (`plugins.preflight`). A URI two
    bundles declare is the first's, in name order. A folder that is not
    there declares nothing.

    Agreed with lilv 0.24.22 on all 693 URIs of a 274-bundle /usr/lib/lv2
    (2026-11-19), in 0.2 s against lilv's 4.5 s."""
    found: dict[str, tuple[Path, Path | None]] = {}
    unread: list[Path] = []
    if not folder.is_dir():
        return found, unread
    for bundle in sorted(folder.iterdir()):
        manifest = bundle / "manifest.ttl"
        if not bundle.is_dir() or not manifest.is_file():
            continue
        problems: list[str] = []
        triples = _read_turtle(manifest.resolve(), problems, "f0.")
        if problems and not triples:
            unread.append(bundle)
            continue
        binaries = {str(s): o for s, p, o in triples if p == LV2_BINARY and isinstance(o, IRI)}
        for uri in plugins_described(triples):
            binary = binaries.get(uri)
            path = (Path(unquote(urlparse(binary).path)) if binary and urlparse(binary).scheme == "file"
                    else None)
            found.setdefault(uri, (bundle, path))
    return found, unread


def declared_plugins(folder: Path, unread: list[Path] | None = None) -> dict[str, Path]:
    """`{uri: bundle}` of `manifest_plugins`; each bundle it could not read
    is appended to `unread` when given."""
    found, missed = manifest_plugins(folder)
    if unread is not None:
        unread.extend(missed)
    return {uri: bundle for uri, (bundle, _) in found.items()}


# --------------------------------------------------------------------------- #
# Profiles
# --------------------------------------------------------------------------- #

def load_profiles(directory: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """`({uri: profile}, problems)` for every `*.yaml` under `directory`.

    Loaded, not validated: `profile.validate` checks a profile against probe's
    facts, which need lilv. A file that does not load is a problem, not an
    absent profile; two for one URI keep the first, in name order, and say so.
    """
    from .profile import ProfileError, load_profile

    profiles: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    if not directory.is_dir():
        return profiles, [f"no profiles folder at {directory}"]
    for path in sorted(directory.glob("*.yaml")):
        try:
            loaded = load_profile(path)
        except ProfileError as exc:
            problems.append(f"profile {path.name} cannot be loaded: {exc}")
            continue
        uri = (loaded.get("plugin") or {}).get("uri") if isinstance(loaded.get("plugin"), dict) else None
        if not isinstance(uri, str) or not uri:
            problems.append(f"profile {path.name} names no plugin.uri")
            continue
        if uri in profiles:
            problems.append(f"profiles {profiles[uri]['_file']} and {path.name} are both for {uri}; "
                            f"using {profiles[uri]['_file']}")
            continue
        profiles[uri] = {**loaded, "_file": path.name}
    return profiles, problems


# --------------------------------------------------------------------------- #
# The library, pure
# --------------------------------------------------------------------------- #

# The kinds the Library drawer groups plugins by (#130), in the order it shows
# them, each with the LV2 classes (lv2core's, by local name) that make a
# plugin that kind. A plugin's kind is its own bundle's word for what it is,
# never a guess from its name: one that declares none of these (a pitch
# shifter, an instrument, a filter, a simulator) or whose bundle was not read
# has no kind, and the page lists it under "Other". Filters are not EQ here:
# lv2:EQPlugin is a kind of lv2:FilterPlugin, not the other way round, and a
# resonant filter or a vocoder is not what a player looks for under EQ.
KINDS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("drive", "Drive", frozenset({"DistortionPlugin", "WaveshaperPlugin"})),
    ("mod", "Mod", frozenset({"ModulatorPlugin", "ChorusPlugin", "FlangerPlugin", "PhaserPlugin"})),
    ("delay", "Delay", frozenset({"DelayPlugin"})),
    ("reverb", "Reverb", frozenset({"ReverbPlugin"})),
    ("dynamics", "Dynamics", frozenset({"DynamicsPlugin", "CompressorPlugin", "LimiterPlugin", "GatePlugin",
                                        "ExpanderPlugin", "EnvelopePlugin"})),
    ("eq", "EQ", frozenset({"EQPlugin", "ParaEQPlugin", "MultiEQPlugin"})),
)


def kind_of(classes: list[str] | None) -> str | None:
    """The kind (`KINDS`' id) a plugin's LV2 classes make it, or None.

    lv2:ReverbPlugin is a kind of lv2:DelayPlugin in LV2's own hierarchy, and
    a reverb's bundle may declare both: reverb wins, as the narrower. Any other
    plugin that declares two kinds takes the first in `KINDS`' order. Pure."""
    held = set(classes or [])
    if "ReverbPlugin" in held:
        return "reverb"
    return next((kind for kind, _label, made_of in KINDS if held & made_of), None)

def _groups(profile: dict[str, Any]) -> list[dict[str, Any]]:
    groups = profile.get("groups")
    if not isinstance(groups, list):
        return []
    return [
        {key: group[key] for key in ("id", "label", "provenance") if key in group}
        for group in groups if isinstance(group, dict)
    ]


def build_library(
    manifest: list[str],
    found: dict[str, dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    problems: list[str] | None = None,
    audio: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`{"plugins": [entry...], "problems": [...]}`, one entry per manifest URI
    in manifest order. Pure: `found` is `scan_plugins`'s, `profiles`
    `load_profiles`'s.

    An entry: `uri`; `name`, with `name_from` saying where it came from
    (`"plugin"`, its bundle's `doap:name`; `"profile"`, the profile's restated
    name; `"uri"`, the URI's last segment) and a `note` whenever it is not the
    plugin's own; `ports` and `bundle` (None where not read); `profile` and
    `groups` (None without a profile); `classes`, the plugin's LV2 classes
    (None where its bundle was not read), and `kind`, the `KINDS` id they make
    it (`kind_of`, None for none); `addable`, and `why_not` when false. The
    answer's `kinds` are `KINDS`' ids and labels in the drawer's order.

    With `audio`, the committed table of audio ports (`stereo.load_ports`),
    each entry has `audio`: `{"in", "out"}`, or None for a plugin the table
    does not list. That is what a live add decides a copy to stereo on.
    """
    problems = list(problems or [])
    entries = []
    for uri in manifest:
        meta = found.get(uri)
        profile = profiles.get(uri)
        entry: dict[str, Any] = {"uri": uri}
        restated = (profile or {}).get("plugin", {}).get("name") if profile else None
        if meta is not None and meta.get("name"):
            entry["name"], entry["name_from"] = meta["name"], "plugin"
        else:
            if meta is None:
                why = "not found in plugins/ (`sushi-rig plugins check` lists what is missing)"
            else:
                # Found but nameless: this reader is the limit, not the plugin,
                # and lilv is what reads the format in full.
                why = (f"no doap:name in {meta['bundle']}'s Turtle as read without lilv "
                       "(a data file missing or unreadable: see problems); lilv "
                       "(`sushi-rig probe`) reads the plugin's data in full")
            if isinstance(restated, str) and restated:
                entry["name"], entry["name_from"] = restated, "profile"
                entry["note"] = f"{why}; the name is the profile's"
            else:
                entry["name"], entry["name_from"] = uri_segment(uri), "uri"
                entry["note"] = f"{why}; the name is the URI's last segment"
        entry["ports"] = meta.get("ports") if meta else None
        entry["bundle"] = meta.get("bundle") if meta else None
        # None where the bundle was not read: not known, which is not "none".
        entry["classes"] = meta.get("classes") if meta else None
        entry["kind"] = kind_of(entry["classes"])
        if audio is not None:
            counts = audio.get(uri)
            entry["audio"] = {"in": counts.inputs, "out": counts.outputs} if counts else None
        entry["profile"] = profile is not None
        entry["groups"] = _groups(profile) if profile is not None else None
        if uri in AMP_PLUGIN_URIS.values():
            entry["addable"] = False
            entry["why_not"] = ("the amp runs in Carla, not Sushi: Sushi cannot set its model "
                                "(see amp.py), so on a Sushi chain it would pass audio with no model")
        else:
            entry["addable"] = True
        entries.append(entry)
    listed = set(manifest)
    for uri, profile in profiles.items():
        if uri not in listed:
            problems.append(f"profile {profile.get('_file', '?')} is for {uri}, which is not in the manifest")
    return {"plugins": entries, "problems": problems,
            "kinds": [{"id": kind, "label": label} for kind, label, _classes in KINDS]}


def read_library(root: Path, plugins_dir: Path | None = None) -> dict[str, Any]:
    """The library of the checkout at `root`: its manifest, its `plugins/` (or
    `plugins_dir`) and its profiles. `LibraryError` if the manifest cannot be
    read, since without it there is no library; everything else that cannot be
    read is in `problems`."""
    manifest_path = root / MANIFEST_FILE
    try:
        manifest = read_manifest(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise LibraryError(f"cannot read {manifest_path}: {exc}") from exc
    plugins_dir = plugins_dir if plugins_dir is not None else root / "plugins"
    profiles_dir = root / PROFILES_DIR
    found, problems = scan_plugins(plugins_dir)
    profiles, profile_problems = load_profiles(profiles_dir)
    from .stereo import PortsError, load_ports

    try:
        audio = load_ports(root)
    except PortsError as exc:
        audio, problems = {}, problems + exc.problems
    result = build_library(manifest, found, profiles, problems + profile_problems, audio)
    result["sources"] = {
        "manifest": str(manifest_path),
        "plugins_dir": str(plugins_dir),
        "profiles_dir": str(profiles_dir),
    }
    return result
