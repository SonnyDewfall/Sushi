"""The user guide, served by the agent as HTML (#167): `docs/guide/*.md` at
`/help/`, its pictures at `/help/img/`.

## Where it is read from

`docs/guide/` of the checkout this package runs from (`paths.checkout_root`),
which is how the rig runs the agent: an editable install in the checkout's
own `tool/.venv`, started by the systemd unit from the checkout
(`systemd/sushi-rig-agent.service`). A wheel installed anywhere else carries
no `docs/`, and the agent then says where it looked instead of serving a
page. Read on every request, as `/profiles` is: a `git pull` shows on a
reload, without restarting the agent.

## A small renderer, no dependency

Markdown is rendered here rather than by a package: the agent has no
Markdown library among its dependencies, the rig would need a reinstall to
gain one, and the guide is the project's own writing, using a known handful
of Markdown. What is rendered is what the guide uses: ATX headings (with the
anchor GitHub gives them, so `page.md#heading` links keep working), lists
nested by indentation, fenced code, pipe tables, block quotes, a rule,
paragraphs, `code`, **strong** and *emphasis* (asterisks only), links,
images, `<https://...>` autolinks and inline `<img>`. Any other HTML is
shown as text, escaped. `test_guide.py` renders every page and fails on
Markdown left showing.

## Links

Relative links are rewritten to where the agent serves them: `saving.md#x`
to `/help/saving#x`, `README.md` to `/help/`, `img/rack.png` to
`/help/img/rack.png`. A link out of the guide (`../reference/agent.md`) is
not served, since only `docs/guide/` is, so it is shown as its text and the
file's place in the checkout, not as a link that leads nowhere. Links out to
the web are kept as they are.

Every link to a page carries `?from=`, the screen the guide was opened from
(`stage`, `rack` or `new-rig`), so "Back to the rig" goes back to that
screen however many pages were read in between.

## What can be served

A page is a name of lowercase letters, digits and hyphens with a `.md` file
in the guide's folder; a picture is a plain file name with an image's
extension in its `img/` folder. Anything else is refused before the disk is
looked at, and what is found is resolved (symlinks followed) and must still
be inside the folder.
"""

from __future__ import annotations

import html
import posixpath
import re
from pathlib import Path
from urllib.parse import quote, unquote

from .paths import checkout_root

# Where the guide lives in a checkout, relative to its root.
GUIDE_PATH = Path("docs") / "guide"
# The guide's front page, served at /help/.
INDEX = "README"
# Where the agent serves it.
PREFIX = "/help/"

PAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
IMAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*\.(png|jpe?g|gif|webp)$")

# The screens "Back to the rig" can go back to, by `from`, and where each is.
BACK = {"stage": "/#stage", "rack": "/#rack", "new-rig": "/#new-rig"}


def guide_dir() -> Path:
    """`docs/guide/` of the checkout this package runs from."""
    return checkout_root() / GUIDE_PATH


def back_from(value: str | None) -> str | None:
    """The screen a `from` names, or None for anything else. Pure."""
    return value if value in BACK else None


def back_href(back: str | None) -> str:
    """Where "Back to the rig" goes: the screen named, else the page, which
    opens the stage on a phone and the rack on a laptop. Pure."""
    return BACK.get(back or "", "/")


def page_href(name: str, back: str | None, anchor: str = "") -> str:
    """Where the agent serves guide page `name` (`INDEX` is /help/). Pure."""
    path = PREFIX if name == INDEX else PREFIX + quote(name)
    query = f"?from={back}" if back_from(back) else ""
    return path + query + (f"#{anchor}" if anchor else "")


# --------------------------------------------------------------------------- #
# Finding a file, safely
# --------------------------------------------------------------------------- #

def _inside(folder: Path, path: Path) -> Path | None:
    try:
        real = path.resolve(strict=True)
        root = folder.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return real if real.is_file() and real.is_relative_to(root) else None


def page_file(folder: Path, name: str) -> Path | None:
    """Guide page `name`'s file (`recording` is recording.md), or None: a
    name that is not one, or no such file under `folder`."""
    if name != INDEX and not PAGE_NAME.match(name):
        return None
    return _inside(folder, folder / f"{name}.md")


def image_file(folder: Path, name: str) -> Path | None:
    """Picture `name` in the guide's img/, or None."""
    if not IMAGE_NAME.match(name):
        return None
    return _inside(folder / "img", folder / "img" / name)


def page_names(folder: Path) -> set[str]:
    """Every page the guide has, by name (`INDEX` among them)."""
    if not folder.is_dir():
        return set()
    return {p.stem for p in folder.glob("*.md") if p.stem == INDEX or PAGE_NAME.match(p.stem)}


# --------------------------------------------------------------------------- #
# Anchors, as GitHub gives them (test_docs.py's `slug`, kept to it by a test)
# --------------------------------------------------------------------------- #

def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: its text without Markdown's marks,
    lowercased, punctuation dropped, each space a hyphen. Pure."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"[`*]", "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+-]*)\s*$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^( *)([-*+]|\d{1,9}[.)])( +|$)(.*)$")
_RULE = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_WEB = re.compile(r"^(https?|mailto):", re.IGNORECASE)  # the only schemes a link keeps
_LINK = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_AUTOLINK = re.compile(r"<((?:https?|mailto):[^\s<>]+)>", re.IGNORECASE)
_IMG_TAG = re.compile(r"<img\b([^<>]*?)/?>", re.IGNORECASE)
_ATTR = re.compile(r"([A-Za-z-]+)\s*=\s*\"([^\"]*)\"")


class Renderer:
    """One page's Markdown to HTML. `pages` is the guide's page names, for
    knowing which `.md` links lead to a page served; `images` its picture
    names; `back` the screen the guide was opened from, carried on every
    link to a page."""

    def __init__(self, pages: set[str], images: set[str], back: str | None = None):
        self.pages = pages
        self.images = images
        self.back = back_from(back)
        self.title: str | None = None
        self._ids: dict[str, int] = {}
        self._held: list[str] = []
        # Links and pictures that lead nowhere the agent serves, as written:
        # shown as their text, and listed here for the tests to fail on.
        self.missing: list[str] = []

    # -- where a link goes ----------------------------------------------------

    def target(self, raw: str) -> tuple[str, str | None]:
        """("href", the URL to use) for a link the page can follow, or
        ("outside", the file's path in the checkout) for one out of the
        guide, or ("missing", what it named)."""
        if _EXTERNAL.match(raw):
            return ("href", raw) if _WEB.match(raw) else ("missing", raw)
        path, _, anchor = raw.partition("#")
        if not path:
            return "href", f"#{anchor}"
        path = unquote(path)
        if path.startswith("/"):
            return "missing", path
        place = posixpath.normpath(path)
        if place.startswith("img/") and place.count("/") == 1:
            name = place[4:]
            return ("href", PREFIX + "img/" + quote(name)) if name in self.images else ("missing", place)
        if "/" not in place and place.endswith(".md") and place[:-3] in self.pages:
            return "href", page_href(place[:-3], self.back, anchor)
        if not (place.startswith("../") or place == ".."):
            return "missing", place  # in the guide's folder, and not a page or picture it has
        whole = posixpath.normpath(posixpath.join(GUIDE_PATH.as_posix(), place))
        if whole.startswith("../") or whole == "..":
            return "missing", place
        return "outside", whole + (f"#{anchor}" if anchor else "")

    # -- inline ---------------------------------------------------------------

    def _hold(self, made: str) -> str:
        self._held.append(made)
        return f"\x00{len(self._held) - 1}\x00"

    def _restore(self, text: str) -> str:
        while "\x00" in text:
            text = re.sub(r"\x00(\d+)\x00", lambda m: self._held[int(m.group(1))], text)
        return text

    def _image(self, src: str, alt: str, extra: dict[str, str] | None = None) -> str:
        kind, where = self.target(src)
        if kind != "href":
            self.missing.append(src)
            return f'<span class="missing-image">[{html.escape(alt)}]</span>'
        attrs = {"src": where, "alt": alt, **(extra or {})}
        attrs.setdefault("loading", "lazy")
        return "<img " + " ".join(f'{k}="{html.escape(v, quote=True)}"' for k, v in attrs.items()) + ">"

    def _img_tag(self, match: re.Match) -> str:
        attrs = {k.lower(): html.unescape(v) for k, v in _ATTR.findall(match.group(1))}
        extra = {k: attrs[k] for k in ("width", "height") if k in attrs and attrs[k].isdigit()}
        return self._hold(self._image(attrs.get("src", ""), attrs.get("alt", ""), extra))

    def _link(self, match: re.Match) -> str:
        image, text, raw = match.group(1), match.group(2), match.group(3)
        if image:
            # Its code spans are already held: restored, then as plain text.
            alt = html.unescape(re.sub(r"<[^>]+>", "", self._restore(text)))
            return self._hold(self._image(raw, " ".join(alt.split())))
        inner = self.inline(text)
        kind, where = self.target(raw)
        if kind == "href":
            return self._hold(f'<a href="{html.escape(where, quote=True)}">{inner}</a>')
        if kind == "outside":
            return self._hold(f'{inner} <span class="elsewhere">(<code>{html.escape(where)}</code> '
                              "in the checkout)</span>")
        self.missing.append(raw)
        return self._hold(inner)

    def inline(self, text: str) -> str:
        """`text` (a paragraph, a cell, a heading) as inline HTML."""
        # Code first: nothing inside it is Markdown or HTML.
        def code(m: re.Match) -> str:
            return self._hold(f"<code>{html.escape(m.group(2).strip() or m.group(2))}</code>")

        text = re.sub(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", code, text, flags=re.DOTALL)
        text = _IMG_TAG.sub(self._img_tag, text)
        text = _AUTOLINK.sub(lambda m: self._hold(
            f'<a href="{html.escape(m.group(1), quote=True)}">{html.escape(m.group(1))}</a>'), text)
        text = _LINK.sub(self._link, text)
        text = html.escape(text, quote=False)
        text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text, flags=re.DOTALL)
        text = re.sub(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])", r"<em>\1</em>", text, flags=re.DOTALL)
        return self._restore(text)

    # -- blocks ---------------------------------------------------------------

    def render(self, text: str) -> str:
        """The page as HTML; `title` is its first heading's text after."""
        return self.blocks(text.expandtabs(4).splitlines())

    def blocks(self, lines: list[str]) -> str:
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            fence = _FENCE.match(line)
            if fence:
                i = self._code(lines, i, fence, out)
                continue
            heading = _HEADING.match(line)
            if heading:
                out.append(self._heading(len(heading.group(1)), heading.group(2)))
                i += 1
                continue
            if _RULE.match(line):
                out.append("<hr>")
                i += 1
                continue
            if _ITEM.match(line):
                i = self._list(lines, i, out)
                continue
            if _QUOTE.match(line):
                quoted = []
                while i < len(lines) and lines[i].strip() and (_QUOTE.match(lines[i]) or quoted):
                    quote_line = _QUOTE.match(lines[i])
                    quoted.append(quote_line.group(1) if quote_line else lines[i])
                    i += 1
                out.append(f"<blockquote>{self.blocks(quoted)}</blockquote>")
                continue
            if line.lstrip().startswith("|") and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
                i = self._table(lines, i, out)
                continue
            para = []
            while i < len(lines) and lines[i].strip() and not self._interrupts(lines[i], lines, i):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{self.inline(chr(10).join(para))}</p>")
        return "\n".join(out)

    def _interrupts(self, line: str, lines: list[str], i: int) -> bool:
        """Whether `line` starts a block of its own, ending a paragraph."""
        return bool(_FENCE.match(line) or _HEADING.match(line) or _ITEM.match(line) or _QUOTE.match(line)
                    or (line.lstrip().startswith("|") and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1])))

    def _heading(self, level: int, text: str) -> str:
        base = slug(text)
        count = self._ids.get(base, 0)
        self._ids[base] = count + 1
        anchor = base if count == 0 else f"{base}-{count}"
        inner = self.inline(text)
        if self.title is None:
            self.title = html.unescape(re.sub(r"<[^>]+>", "", inner))
        return f'<h{level} id="{html.escape(anchor, quote=True)}">{inner}</h{level}>'

    def _code(self, lines: list[str], i: int, fence: re.Match, out: list[str]) -> int:
        indent, mark, lang = len(fence.group(1)), fence.group(2), fence.group(3)
        body = []
        i += 1
        while i < len(lines):
            close = _FENCE.match(lines[i])
            if close and close.group(2)[0] == mark[0] and len(close.group(2)) >= len(mark) and not close.group(3):
                i += 1
                break
            line = lines[i]
            strip = min(indent, len(line) - len(line.lstrip(" ")))
            body.append(line[strip:])
            i += 1
        kind = f' class="language-{html.escape(lang, quote=True)}"' if lang else ""
        out.append(f"<pre><code{kind}>{html.escape(chr(10).join(body))}</code></pre>")
        return i

    def _table(self, lines: list[str], i: int, out: list[str]) -> int:
        def cells(line: str) -> list[str]:
            line = line.strip()
            if line.startswith("|"):
                line = line[1:]
            if line.endswith("|") and not line.endswith("\\|"):
                line = line[:-1]
            return [c.strip() for c in re.split(r"(?<!\\)\|", line)]

        head = cells(lines[i])
        aligns = []
        for spec in cells(lines[i + 1]):
            left, right = spec.startswith(":"), spec.endswith(":")
            aligns.append("center" if left and right else "right" if right else "left" if left else None)
        i += 2
        rows = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            rows.append(cells(lines[i]))
            i += 1

        def row(values: list[str], tag: str) -> str:
            made = []
            for n in range(len(head)):
                value = values[n] if n < len(values) else ""
                align = aligns[n] if n < len(aligns) and aligns[n] else None
                style = f' style="text-align:{align}"' if align else ""
                made.append(f"<{tag}{style}>{self.inline(value.replace(chr(92) + '|', '|'))}</{tag}>")
            return "<tr>" + "".join(made) + "</tr>"

        body = "".join(row(r, "td") for r in rows)
        out.append(f'<div class="table-wrap"><table><thead>{row(head, "th")}</thead>'
                   f"<tbody>{body}</tbody></table></div>")
        return i

    def _list(self, lines: list[str], i: int, out: list[str]) -> int:
        first = _ITEM.match(lines[i])
        indent = len(first.group(1))
        ordered = first.group(2)[0].isdigit()
        start = int(first.group(2)[:-1]) if ordered else None
        items: list[str] = []
        while i < len(lines):
            item = _ITEM.match(lines[i])
            if not item or len(item.group(1)) != indent or item.group(2)[0].isdigit() != ordered:
                break
            content = len(item.group(1)) + len(item.group(2)) + max(1, min(len(item.group(3)), 4))
            body = [item.group(4)]
            i += 1
            while i < len(lines):
                line = lines[i]
                if not line.strip():
                    # A blank line ends the item unless what follows is indented into it.
                    j = i
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and len(lines[j]) - len(lines[j].lstrip(" ")) >= content:
                        body.extend([""] * (j - i))
                        i = j
                        continue
                    break
                lead = len(line) - len(line.lstrip(" "))
                if lead >= content:
                    body.append(line[content:])
                elif _ITEM.match(line) or _FENCE.match(line) or _HEADING.match(line):
                    break  # the next item, or a block of the parent's
                elif body[-1].strip() and not _FENCE.match(body[-1]):
                    body.append(line.strip())  # a lazy continuation of the paragraph
                else:
                    break
                i += 1
            items.append(f"<li>{self.blocks(body)}</li>")
            # A blank line between items: step over it if the list goes on.
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            nxt = _ITEM.match(lines[j]) if j < len(lines) else None
            if nxt and len(nxt.group(1)) == indent and nxt.group(2)[0].isdigit() == ordered:
                i = j
            else:
                break
        if ordered:
            attr = f' start="{start}"' if start not in (None, 1) else ""
            out.append(f"<ol{attr}>{''.join(items)}</ol>")
        else:
            out.append(f"<ul>{''.join(items)}</ul>")
        return i


# --------------------------------------------------------------------------- #
# The page around it
# --------------------------------------------------------------------------- #

def page(body: str, title: str, *, back: str | None, index: bool) -> str:
    """A whole HTML page: the bar with the way back to the rig and to the
    guide's contents, the rendered guide, and the way back again under it."""
    to_rig = html.escape(back_href(back_from(back)), quote=True)
    contents = html.escape(page_href(INDEX, back), quote=True)
    bar_contents = ('<span class="here" aria-current="page">Guide contents</span>' if index
                    else f'<a href="{contents}">Guide contents</a>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{html.escape(title)} · sushi-rig guide</title>
<link rel="stylesheet" href="/ui/guide.css">
</head>
<body class="guide-page">
<nav class="guide-bar" aria-label="Guide">
<a class="to-rig" href="{to_rig}">&larr; Back to the rig</a>
{bar_contents}
</nav>
<main class="guide">
{body}
</main>
<nav class="guide-foot" aria-label="Guide, again">
<a class="to-rig" href="{to_rig}">&larr; Back to the rig</a>
{'' if index else f'<a href="{contents}">Guide contents</a>'}
</nav>
</body>
</html>
"""


def render_page(folder: Path, name: str, back: str | None = None) -> str | None:
    """Guide page `name` of `folder` as a whole HTML page, or None if the
    guide has no such page."""
    path = page_file(folder, name)
    if path is None:
        return None
    images = {p.name for p in (folder / "img").glob("*") if IMAGE_NAME.match(p.name)} if (folder / "img").is_dir() else set()
    renderer = Renderer(page_names(folder), images, back)
    body = renderer.render(path.read_text(encoding="utf-8"))
    return page(body, renderer.title or name, back=back, index=name == INDEX)


def not_found(folder: Path, name: str, back: str | None = None) -> str:
    """The page for a guide page there is not, or a guide not there at all."""
    if not folder.is_dir():
        said = (f"<p>The guide is not beside this agent: it is read from <code>{html.escape(str(folder))}</code>, "
                "which is not there. The agent serves the guide from the checkout it runs from; "
                "an agent installed some other way has none.</p>")
    else:
        said = f"<p>The guide has no page called <code>{html.escape(name)}</code>.</p>"
    return page(f"<h1>Not in the guide</h1>\n{said}", "Not in the guide", back=back, index=False)
