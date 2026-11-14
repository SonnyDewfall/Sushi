"""The documentation's links lead somewhere, and the README stays a front page.

`README.md` is the front page and `docs/` holds the rest (the user guides,
the reference, the design pages), so nearly everything is reached by a
relative link. A page moved or a heading reworded breaks one without a word:
the link still renders, and leads nowhere. Every relative link and image in
`README.md` and under `docs/` is held here to a file that exists, and a link
to a heading (`page.md#heading`, or `#heading` within a page) to a heading
that page has, by the anchor GitHub gives it.

What this reads: `README.md` and the Markdown under `docs/`, and nothing
else. A link's target is only asked whether it exists, never opened, unless it
is one of those pages and the link names a heading in it. So a link into
`config/rigs/` would be checked without the folder being read
(rig_fixtures.py: no test reads it unmarked).

What it does not check: links out to the web (no network in the fast suite),
paths written as code (`tool/spikes/...` in backticks is prose, not a link),
and whether a page says anything true.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DOCS = ROOT / "docs"

# The README is a front page: what the rig is, what it needs, how to start
# it, and links. The detail it once held (1,950 lines of it) lives under
# docs/reference/, and this is what keeps it from growing back.
README_LIMIT = 200

PAGES = [README, *sorted(DOCS.rglob("*.md"))]

_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})")
_CODE = re.compile(r"`+[^`\n]*`+")
# [text](target) and ![alt](target); the target ends at the first space or
# closing bracket, which is all these pages use (no titles, no nested
# brackets in a target).
_INLINE = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(\s*<?([^)\s>]+)>?[^)]*\)")
_HTML = re.compile(r"<(img|a)\b[^>]*?\b(?:src|href)\s*=\s*\"([^\"]*)\"[^>]*>", re.IGNORECASE)
_ALT = re.compile(r"\balt\s*=\s*\"([^\"]*)\"", re.IGNORECASE)
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def prose(text: str) -> list[str]:
    """`text`'s lines with fenced code blanked: a link inside a fence is an
    example, and a `#` there is a comment, not a heading."""
    out, fence = [], None
    for line in text.splitlines():
        match = _FENCE.match(line)
        if fence is None and match:
            fence = match.group(2)[0]
            out.append("")
        elif fence is not None:
            if match and match.group(2)[0] == fence and line.strip().strip(fence) == "":
                fence = None
            out.append("")
        else:
            out.append(line)
    return out


def links(text: str) -> list[dict]:
    """Every link and image in `text`: `{line, target, image, alt}`, `line`
    where it starts. Fenced and inline code are not looked in. The whole text
    is searched at once, not line by line: a link's words wrap like any
    others, and one broken across two lines is still a link."""
    lines = [_CODE.sub(lambda m: " " * len(m.group(0)), line) for line in prose(text)]
    bare = "\n".join(lines)
    found = []
    for match in _INLINE.finditer(bare):
        found.append({"at": match.start(), "target": match.group(3), "image": bool(match.group(1)),
                      "alt": " ".join(match.group(2).split()) if match.group(1) else None})
    for match in _HTML.finditer(bare):
        image = match.group(1).lower() == "img"
        alt = _ALT.search(match.group(0))
        found.append({"at": match.start(), "target": match.group(2), "image": image,
                      "alt": (alt.group(1) if alt else "") if image else None})
    found.sort(key=lambda link: link["at"])
    return [{"line": bare.count("\n", 0, link.pop("at")) + 1, **link} for link in found]


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: its text without Markdown's marks,
    lowercased, punctuation dropped, each space a hyphen."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)   # a link is its text
    text = re.sub(r"[`*]", "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    """Every heading's anchor in `text`; a repeated heading gets -1, -2, as
    GitHub numbers them."""
    seen: dict[str, int] = {}
    out = set()
    for line in prose(text):
        match = _HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(2))
        count = seen.get(base, 0)
        seen[base] = count + 1
        out.add(base if count == 0 else f"{base}-{count}")
    return out


def problems(page: Path) -> list[str]:
    """What is wrong with `page`'s links, one line each, by line number."""
    said = []
    text = page.read_text(encoding="utf-8")
    for link in links(text):
        target = link["target"]
        where = f"{page.relative_to(ROOT)}:{link['line']}"
        if link["image"] and not (link["alt"] or "").strip():
            said.append(f"{where}: the image {target} has no alt text")
        if _EXTERNAL.match(target):
            continue
        path, _, anchor = target.partition("#")
        path = unquote(path)
        if path.startswith("/"):
            said.append(f"{where}: {target} is an absolute path; link relative to the page")
            continue
        file = (page.parent / path).resolve() if path else page
        if ROOT not in (file, *file.parents):
            said.append(f"{where}: {target} leads outside the repository")
            continue
        if not file.exists():
            said.append(f"{where}: {target} leads to {file.relative_to(ROOT)}, which does not exist")
            continue
        if anchor and file.suffix == ".md":
            if file not in PAGES:
                said.append(f"{where}: {target} names a heading of {file.relative_to(ROOT)}, "
                            "which is not one of the pages checked here")
            elif unquote(anchor).lower() not in anchors(file.read_text(encoding="utf-8")):
                said.append(f"{where}: {file.relative_to(ROOT)} has no heading whose anchor is #{anchor}")
    return said


# -- the checker itself: it must find links, or it passes by finding nothing --

def test_links_are_found_in_prose_and_not_in_code():
    text = "\n".join([
        "See [the agent](agent.md#the-routes) and ![a rack](img/rack.png).",
        "`[not a link](nowhere.md)` is code.",
        "```bash",
        "echo '[nor this](nowhere.md)'  # not a heading",
        "```",
        '<img src="img/stage.png" alt="the stage" width="300">',
        "<https://example.invalid/> and [out](https://example.invalid/x.md#y)",
        "A link whose words wrap, [the library and chain",
        "edits](agent.md#the-library-and-chain-edits), is one link.",
    ])
    assert [(l["line"], l["target"], l["image"], l["alt"]) for l in links(text)] == [
        (1, "agent.md#the-routes", False, None), (1, "img/rack.png", True, "a rack"),
        (6, "img/stage.png", True, "the stage"), (7, "https://example.invalid/x.md#y", False, None),
        (8, "agent.md#the-library-and-chain-edits", False, None),
    ]


@pytest.mark.parametrize("heading, anchor", [
    ("The routes", "the-routes"),
    ("Checking a config: `verify`", "checking-a-config-verify"),
    ("`LV2_PATH`", "lv2_path"),
    ("What it needs, and what it refuses", "what-it-needs-and-what-it-refuses"),
    ("The panel's record bar", "the-panels-record-bar"),
    ("v1: a rig.yaml of tracks", "v1-a-rigyaml-of-tracks"),
    ("Do the fix (once on each laptop)", "do-the-fix-once-on-each-laptop"),
])
def test_a_heading_gets_the_anchor_github_gives_it(heading, anchor):
    assert slug(heading) == anchor


def test_a_repeated_heading_is_numbered_and_a_fenced_hash_is_no_heading():
    text = "# One\n\n## Notes\n\n```\n# a comment\n```\n\n## Notes\n"
    assert anchors(text) == {"one", "notes", "notes-1"}


def test_a_broken_link_a_missing_heading_and_a_bare_image_are_each_said(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    page = docs / "page.md"
    other = docs / "other.md"
    other.write_text("# Other\n\n## Here\n")
    page.write_text("[ok](other.md#here) [gone](missing.md) [no heading](other.md#there)\n"
                    "![](other.md) [up](../../outside.md) [self](#nowhere)\n# Page\n")
    monkeypatch.setattr("test_docs.ROOT", tmp_path)
    monkeypatch.setattr("test_docs.PAGES", [page, other])
    assert problems(page) == [
        "docs/page.md:1: missing.md leads to docs/missing.md, which does not exist",
        "docs/page.md:1: docs/other.md has no heading whose anchor is #there",
        "docs/page.md:2: the image other.md has no alt text",
        "docs/page.md:2: ../../outside.md leads outside the repository",
        "docs/page.md:2: docs/page.md has no heading whose anchor is #nowhere",
    ]


# -- the pages -----------------------------------------------------------------

def test_the_pages_are_there_to_check():
    """The front page, the three indexes it leads to, and links to follow: a
    checker pointed at the wrong folder would pass every test below."""
    for index in ("guide", "reference", "design"):
        assert DOCS / index / "README.md" in PAGES
    assert len(links(README.read_text(encoding="utf-8"))) >= 20


@pytest.mark.parametrize("page", PAGES, ids=lambda page: str(page.relative_to(ROOT)))
def test_every_relative_link_and_image_leads_somewhere(page):
    said = problems(page)
    assert not said, "\n".join(said)


def test_the_front_page_links_to_every_guide_and_reference_page():
    """A page nothing on the front page leads to is a page nobody finds."""
    targets = {(README.parent / unquote(l["target"].partition("#")[0])).resolve()
               for l in links(README.read_text(encoding="utf-8")) if not _EXTERNAL.match(l["target"])}
    pages = [p for folder in ("guide", "reference") for p in sorted((DOCS / folder).glob("*.md"))]
    missing = [str(p.relative_to(ROOT)) for p in pages if p not in targets and p.name != "README.md"]
    assert not missing, f"README.md does not link to: {', '.join(missing)}"


def test_the_readme_is_a_front_page():
    lines = README.read_text(encoding="utf-8").count("\n")
    assert lines < README_LIMIT, (
        f"README.md is {lines} lines; it is the front page and stays under {README_LIMIT}. "
        "Put the detail in a page under docs/ and link to it")
