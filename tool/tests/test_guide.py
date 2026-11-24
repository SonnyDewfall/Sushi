"""The user guide as the agent serves it (#167): `guide.py`'s renderer, the
/help/ routes, what they refuse, and every link of every served page
followed.

Two kinds of guide are read. The checkout's own docs/guide/, read as it is
today and assumed nothing of beyond what it links to: the link check finds
its pages by crawling from the front page, and fails on a link or picture
that leads nowhere the agent serves. And a guide written here in a
temporary folder, for the renderer's cases and the path-safety ones, with a
secret beside it that nothing may serve.

What this does not show: the guide on a phone (test_ui_help.py has a phone's
width in Chromium, not a phone), or on the rig.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from sushi_rig import guide  # noqa: E402
from sushi_rig.agent import Agent  # noqa: E402
from sushi_rig.paths import checkout_root  # noqa: E402

from test_docs import anchors as docs_anchors, prose, _HEADING  # noqa: E402
from ui_fake import UiRig  # noqa: E402

GUIDE = checkout_root() / "docs" / "guide"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # enough of a PNG to be served as one
# What the files beside the guide hold: never in any answer.
KEPT_OUT = "kept-out-7f3a9c"


def render(text: str, pages=("README", "saving", "recording"), images=("rack.png",), back=None) -> guide.Renderer:
    r = guide.Renderer(set(pages), set(images), back)
    r.html = r.render(text)
    return r


# --------------------------------------------------------------------------- #
# The renderer, pure
# --------------------------------------------------------------------------- #

def test_headings_get_the_anchors_github_gives_them_repeats_numbered():
    r = render("# Saving and going back\n\n## The page says \"Agent not answering\"\n\n"
               "## Ask the rig: `doctor`\n\n## Notes\n\n### Notes\n")
    assert re.findall(r'<h(\d) id="([^"]+)">', r.html) == [
        ("1", "saving-and-going-back"), ("2", "the-page-says-agent-not-answering"),
        ("2", "ask-the-rig-doctor"), ("2", "notes"), ("3", "notes-1")]
    assert "<h2 id=\"ask-the-rig-doctor\">Ask the rig: <code>doctor</code></h2>" in r.html
    assert r.title == "Saving and going back"


def test_every_heading_of_the_guide_has_the_anchor_test_docs_checks_links_against():
    """test_docs.py holds every `page.md#heading` link to GitHub's anchors;
    the served page must give its headings the same ones, or a link that
    passes there leads to the top of the page here."""
    for path in sorted(GUIDE.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        served = set(re.findall(r'<h\d id="([^"]+)"', render(text).html))
        assert served == docs_anchors(text), path.name
        assert len([line for line in prose(text) if _HEADING.match(line)]) == len(served), path.name


def test_relative_links_are_rewritten_to_where_the_agent_serves_them_carrying_the_screen():
    r = render("[Save](saving.md), [there](saving.md#save-under-a-new-name), [all](README.md),\n"
               "[here](#top), [web](https://example.org/x), <http://127.0.0.1:8029/>\n\n"
               "![The rack](img/rack.png)", back="stage")
    hrefs = re.findall(r'href="([^"]+)"', r.html)
    assert hrefs == ["/help/saving?from=stage", "/help/saving?from=stage#save-under-a-new-name",
                     "/help/?from=stage", "#top", "https://example.org/x", "http://127.0.0.1:8029/"]
    assert '<img src="/help/img/rack.png" alt="The rack" loading="lazy">' in r.html
    assert r.missing == []
    assert "?from=" not in "".join(re.findall(r'href="([^"]+)"', render("[Save](saving.md)", back="nowhere").html)), \
        "a screen the page does not have is not carried"


def test_a_link_out_of_the_guide_is_its_text_and_where_the_file_is_not_a_dead_link():
    r = render("See the [reference](../reference/README.md) and [MIDI](../reference/midi.md#pads).")
    assert "<a " not in r.html
    assert ('reference <span class="elsewhere">(<code>docs/reference/README.md</code> in the checkout)</span>'
            in r.html)
    assert "<code>docs/reference/midi.md#pads</code>" in r.html
    assert r.missing == []


def test_a_link_or_picture_the_guide_does_not_have_is_text_and_listed_as_missing():
    r = render("[Gone](gone.md) and ![No such](img/nope.png) and [abs](/etc/passwd) and [run](javascript:alert(1))")
    assert "<a " not in r.html and "<img" not in r.html and "javascript" not in r.html
    assert "Gone" in r.html and '<span class="missing-image">[No such]</span>' in r.html
    assert r.missing == ["gone.md", "img/nope.png", "/etc/passwd", "javascript:alert(1)"]


def test_a_destination_s_parentheses_balance_as_commonmark_has_them():
    r = render("[Foo](https://x/Foo_(bar)) then [b](https://x/a_(b_(c))) then (see [Save](saving.md)) "
               "then [esc](https://x/a\\)b) then [t](saving.md \"Save\") then [angle](<https://x/a b>) "
               "then [open](https://x/(unclosed) end")
    hrefs = re.findall(r'href="([^"]+)"', r.html)
    assert hrefs == ["https://x/Foo_(bar)", "https://x/a_(b_(c))", "/help/saving", "https://x/a)b",
                     "/help/saving", "https://x/a b"]
    assert "(see <a" in r.html and "</a>) then" in r.html, "the parenthesis around a link is the text's"
    assert "[open](https://x/(unclosed) end" in r.html, "unbalanced: not a link, left as written"


@pytest.mark.parametrize("text", ["a \x00 b", "a \x007\x00 b", "\x000\x00", "[x](\x00) `\x00` **\x00**"])
def test_a_nul_in_a_page_is_shown_as_a_replacement_character_never_a_hang_or_a_crash(text):
    """A NUL once stood for a held piece of HTML: one in the page made the
    restore loop for good (in the agent's executor, which doctor, pw-top and
    the MIDI plan share) or look up a piece that was not there. Run in a
    daemon thread here, so a hang fails the test instead of stopping the
    suite."""
    import threading

    done: list = []
    worker = threading.Thread(target=lambda: done.append(render(text)), daemon=True)
    worker.start()
    worker.join(5)
    assert done, "the render did not return (or raised: see the thread's traceback above)"
    r = done[0]
    assert "\x00" not in r.html and "\ufffd" in r.html
    assert guide.Renderer(set(), set())._restore("a \x003\x00 b") == "a  b", "a slot never held is dropped"


def test_a_picture_is_the_guide_s_own_or_it_is_missing_and_one_on_the_web_is_never_fetched():
    r = render("<img src='img/rack.png' alt='single'> <img src=img/rack.png alt=bare width=3> "
               "<img alt=\"no src\"> <img src=\"\" alt=\"empty\"> <img src=\"#top\" alt=\"anchor\">\n\n"
               "![web](https://example.org/a.png) and ![](HTTP://example.org/b.png) and ![page](saving.md)")
    assert r.html.count('src="/help/img/rack.png"') == 2 and 'width="3"' in r.html
    assert 'src="#"' not in r.html and "example.org/a.png\"" not in r.html.replace('href="https://example.org/a.png"', "")
    assert "<img" not in r.html.split("<img src=\"/help/img/rack.png\" alt=\"bare\" width=\"3\" loading=\"lazy\">")[1]
    assert '<a class="remote-image" href="https://example.org/a.png">[web]</a>' in r.html
    assert '<span class="missing-image">[no src]</span>' in r.html and '<span class="missing-image">[page]</span>' in r.html
    assert r.missing == ["", "", "#top", "https://example.org/a.png", "HTTP://example.org/b.png", "saving.md"]


def test_inline_img_keeps_its_size_and_alt_loses_everything_else_and_other_html_is_text():
    r = render('<img src="img/rack.png" alt="The &quot;rack&quot;" width="320" onerror="alert(1)" style="x">\n\n'
               "A <script>alert(1)</script> and <b>bold</b> & `<name>.yaml`")
    assert '<img src="/help/img/rack.png" alt="The &quot;rack&quot;" width="320" loading="lazy">' in r.html
    assert "onerror" not in r.html and "style" not in r.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.html and "&lt;b&gt;bold&lt;/b&gt; &amp;" in r.html
    assert "<code>&lt;name&gt;.yaml</code>" in r.html


def test_lists_nest_by_indentation_and_hold_code_and_paragraphs():
    text = """1. **First** step
   over two lines.
2. Fill in:
   - **Name**: what
     to call it.
   - **Input**
3. Then:

   ```bash
   sushi-rig agent
   ```

   (Add `--host`.)
4. Last.

- a lazy
continuation
- b
"""
    html = render(text).html
    assert html.count("<ol>") == 1 and html.count("<ul>") == 2
    assert "<li><p><strong>First</strong> step\nover two lines.</p></li>" in html
    assert "<ul><li><p><strong>Name</strong>: what\nto call it.</p></li><li><p><strong>Input</strong></p></li></ul>" in html
    assert '<pre><code class="language-bash">sushi-rig agent</code></pre>\n<p>(Add <code>--host</code>.)</p></li>' in html
    assert "<li><p>Last.</p></li></ol>" in html
    assert "<li><p>a lazy\ncontinuation</p></li><li><p>b</p></li></ul>" in html


def test_a_table_is_a_table_with_inline_markdown_in_its_cells_and_scrolls_on_its_own():
    html = render("| File | What |\n|---|---|\n| `master.wav` | **the mix** |\n| [Save](saving.md) | x |\n").html
    assert html.startswith('<div class="table-wrap"><table><thead><tr><th>File</th><th>What</th></tr></thead>')
    assert "<tr><td><code>master.wav</code></td><td><strong>the mix</strong></td></tr>" in html
    assert '<td><a href="/help/saving">Save</a></td>' in html


def test_code_is_never_read_as_markdown_or_html():
    html = render("```\n# not a heading\n[not](a link) <img src=x>\n```\n\nand `**not bold**`").html
    assert "<h1" not in html and "<a " not in html and "<img" not in html
    assert "# not a heading\n[not](a link) &lt;img src=x&gt;" in html
    assert "<code>**not bold**</code>" in html


def test_every_page_of_the_guide_renders_with_no_markdown_left_showing_and_nothing_missing():
    images = {p.name for p in (GUIDE / "img").iterdir()}
    pages = guide.page_names(GUIDE)
    assert {"README", "recording", "channel-from-another-rig", "playing-live"} <= pages
    for path in sorted(GUIDE.glob("*.md")):
        r = guide.Renderer(pages, images, "rack")
        html = r.render(path.read_text(encoding="utf-8"))
        assert r.missing == [], path.name
        shown = re.sub(r"<pre>.*?</pre>|<code>.*?</code>", "", html, flags=re.DOTALL)
        for mark in ("**", "](", "|---", "```", "&lt;img", "&lt;http", "\x00"):
            assert mark not in shown, f"{path.name} shows {mark!r}"
        assert not re.search(r"^\s*#", re.sub(r"<[^>]+>", "", shown), flags=re.MULTILINE), path.name
        assert r.title, path.name


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #

def agent(folder: Path | None = None) -> Agent:
    made = Agent(root=checkout_root(), live=UiRig(),
                 describe=lambda: {"verdict": "running", "running": True, "config_name": "x"})
    if folder is not None:
        made.guide_dir = folder
    return made


def fetch(made: Agent, *paths: str, raw: bool = False):
    """Each path's (status, headers, body). `raw` sends the path as written,
    over a socket, so no client tidies a `..` or a `%2e` out of it first."""
    async def go():
        async with TestClient(TestServer(made.app())) as client:
            out = []
            for path in paths:
                if raw:
                    out.append(await raw_get(client.server.host, client.server.port, path))
                else:
                    response = await client.get(path, allow_redirects=False)
                    out.append((response.status, dict(response.headers), await response.read()))
            return out
    try:
        return asyncio.run(go())
    finally:
        made._executor.shutdown(wait=True, cancel_futures=True)


async def raw_get(host: str, port: int, path: str):
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode("latin-1"))
    await writer.drain()
    data = await reader.read()
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
    return int(lines[0].split()[1]), headers, body


@pytest.fixture
def small(tmp_path):
    """A guide of two pages and a picture, a secret beside it, and links
    inside it that point out of it."""
    folder = tmp_path / "docs" / "guide"
    (folder / "img").mkdir(parents=True)
    (folder / "README.md").write_text("# The guide\n\nRead [Recording](recording.md#record-a-take).\n")
    (folder / "recording.md").write_text("# Recording\n\n## Record a take\n\n![Bar](img/bar.png)\n")
    (folder / "img" / "bar.png").write_bytes(PNG)
    (tmp_path / "secret.md").write_text(f"# {KEPT_OUT}\n")
    (tmp_path / "secret.png").write_bytes(KEPT_OUT.encode())
    (folder / "img" / "notes.txt").write_text(KEPT_OUT)
    (folder / "sneaky.md").symlink_to(tmp_path / "secret.md")
    (folder / "img" / "sneaky.png").symlink_to(tmp_path / "secret.png")
    return folder


def test_the_front_page_and_a_page_are_html_with_the_way_back_to_the_screen_they_came_from(small):
    (index, ih, ib), (page, ph, pb), (bare, _, bb), (odd, _, ob) = fetch(
        agent(small), "/help/?from=stage", "/help/recording?from=rack", "/help/recording", "/help/recording?from=x")
    assert index == page == bare == odd == 200
    assert ih["Content-Type"].startswith("text/html") and ih["Cache-Control"] == "no-cache"
    assert b"<title>The guide \xc2\xb7 sushi-rig guide</title>" in ib
    assert b'<a class="to-rig" href="/#stage">' in ib
    assert b'href="/help/recording?from=stage#record-a-take"' in ib
    assert b'aria-current="page">Guide contents' in ib, "the front page says it is the contents"
    assert b'<a class="to-rig" href="/#rack">' in pb and b'<a href="/help/?from=rack">Guide contents</a>' in pb
    assert b'<img src="/help/img/bar.png"' in pb
    assert b'<a class="to-rig" href="/">' in bb, "opened from nowhere: back to the page, which picks the screen"
    assert b'<a class="to-rig" href="/">' in ob and b"from=x" not in ob
    assert b'<link rel="stylesheet" href="/ui/guide.css">' in pb


def test_help_and_a_pages_file_name_send_the_browser_to_the_page(small):
    (folder, fh, _), (md, mh, _) = fetch(agent(small), "/help?from=stage", "/help/recording.md?from=rack")
    assert (folder, fh["Location"]) == (302, "/help/?from=stage")
    assert (md, mh["Location"]) == (302, "/help/recording?from=rack")


@pytest.mark.parametrize("asked, sent", [
    ("/help/README.md", "/help/"), ("/help/README", "/help/"), ("/help/readme.md?from=stage", "/help/?from=stage"),
    ("/help/Readme.MD", "/help/"), ("/help/Recording.md", "/help/recording"), ("/help/RECORDING", "/help/recording"),
    ("/help/Channel-From-Another-Rig.Md?from=rack", "/help/channel-from-another-rig?from=rack"), ("/help/nothing.md", "/help/nothing"),
])
def test_a_page_asked_for_by_its_file_name_or_in_another_case_is_sent_to_the_page(small, asked, sent):
    [(status, headers, _)] = fetch(agent(small), asked)
    assert (status, headers.get("Location")) == (302, sent)


@pytest.mark.parametrize("asked", ["/help/recording", "/help/", "/help/x_y.md", "/help/..md", "/help/a%20b.md"])
def test_a_page_as_served_or_no_page_s_name_is_not_sent_anywhere(small, asked):
    [(status, _, _)] = fetch(agent(small), asked)
    assert status in (200, 404)


def test_canonical_names_only_pages():
    assert [guide.canonical(n) for n in ("README.md", "README", "readme", "recording", "Recording.md", "x.y.md",
                                         "../a.md", "", ".md")] == [
        "README", "README", "README", None, "recording", None, None, None, None]


def test_a_picture_is_served_as_itself(small):
    [(status, headers, body)] = fetch(agent(small), "/help/img/bar.png")
    assert status == 200 and headers["Content-Type"] == "image/png" and body == PNG
    assert headers["Cache-Control"] == "no-cache"


def test_a_page_the_guide_has_not_is_an_html_404_with_the_way_back(small):
    [(status, headers, body)] = fetch(agent(small), "/help/nothing-here?from=stage")
    assert status == 404 and headers["Content-Type"].startswith("text/html")
    assert b"no page called <code>nothing-here</code>" in body and b'href="/#stage"' in body


def test_a_guide_that_is_not_there_says_where_it_was_looked_for(tmp_path):
    [(status, _, body)] = fetch(agent(tmp_path / "docs" / "guide"), "/help/")
    assert status == 404 and str(tmp_path / "docs" / "guide").encode() in body
    assert b"not beside this agent" in body


@pytest.mark.parametrize("path", [
    "/help/../secret", "/help/..%2fsecret", "/help/%2e%2e%2fsecret", "/help/%2e%2e/secret",
    "/help/..%2f..%2fsecret", "/help/sneaky", "/help/SECRET", "/help/.%2e/secret.md", "/help/../../secret.md",
    "/help/img/../../secret.png", "/help/img/..%2f..%2fsecret.png", "/help/img/%2e%2e%2f%2e%2e%2fsecret.png",
    "/help/img/sneaky.png", "/help/img/notes.txt", "/help/img/.%2e/.%2e/secret.png", "/help/img/",
    "/help/img/%2fetc%2fpasswd", "/help/%2fetc%2fpasswd", "/help/img/..\\..\\secret.png",
])
def test_nothing_outside_the_guide_is_served_however_the_path_is_written(small, path):
    for status, headers, body in fetch(agent(small), path, raw=True) + fetch(agent(small), path):
        if status == 302:
            # Another case of a page's name (/help/SECRET) is sent to that
            # name, lowercased: only ever to a page's name under /help/, which
            # is then refused like any other.
            where = headers["Location"]
            assert re.fullmatch(r"/help/[a-z0-9-]+", where), (path, where)
            [(status, _, body)] = fetch(agent(small), where)
        assert status in (400, 403, 404), (path, status)
        assert KEPT_OUT.encode() not in body and b"root:" not in body, path


@pytest.mark.parametrize("name", ["../secret", "..", "/etc/passwd", "sneaky", "Secret", "a/b", "", ".hidden", "x.md"])
def test_page_file_takes_a_name_never_a_path(small, name):
    assert guide.page_file(small, name) is None


@pytest.mark.parametrize("name", ["../../secret.png", "..", "sneaky.png", "notes.txt", "/etc/passwd", "a/bar.png",
                                  ".png", "bar.png/", "bar.PNG.txt"])
def test_image_file_takes_a_picture_s_name_never_a_path(small, name):
    assert guide.image_file(small, name) is None


def test_the_names_that_are_files_of_the_guide_are_found(small):
    assert guide.page_file(small, "recording") == (small / "recording.md").resolve()
    assert guide.page_file(small, "README") == (small / "README.md").resolve()
    assert guide.image_file(small, "bar.png") == (small / "img" / "bar.png").resolve()


def test_the_rigs_own_agent_reads_the_guide_of_the_checkout_it_runs_from():
    """The rig runs the agent from an editable install in the checkout
    (systemd/sushi-rig-agent.service: tool/.venv in the checkout), where the
    package is the checkout's own tool/src/sushi_rig: its guide is the
    checkout's, and served with no folder handed in."""
    unit = (checkout_root() / "systemd" / "sushi-rig-agent.service").read_text(encoding="utf-8")
    assert "ExecStart=@CHECKOUT@/tool/.venv/bin/sushi-rig agent" in unit
    assert Path(guide.__file__).resolve() == checkout_root() / "tool" / "src" / "sushi_rig" / "guide.py"
    [(status, _, body)] = fetch(agent(), "/help/")
    assert status == 200 and b"<h1>User guides</h1>" in body.replace(b' id="user-guides"', b"")


def test_installed_without_the_checkout_every_help_page_says_where_it_looked_and_why(tmp_path):
    """The package installed as a wheel would install it: a copy in a
    venv's site-packages, with no checkout around it, run in a fresh
    interpreter that sees only that copy. Its guide is <venv>/lib/docs/guide,
    which is not there, and every Help link's page says so (guide.py: the
    links are kept, not hidden)."""
    import json
    import shutil
    import subprocess
    import sys

    site = tmp_path / "venv" / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "site-packages"
    shutil.copytree(checkout_root() / "tool" / "src" / "sushi_rig", site / "sushi_rig",
                    ignore=shutil.ignore_patterns("__pycache__"))
    script = f"""
import asyncio, json, sys
sys.path.insert(0, {str(site)!r})
from aiohttp.test_utils import TestClient, TestServer
import sushi_rig.guide as guide
from sushi_rig.agent import Agent

async def go():
    made = Agent(describe=lambda: {{"running": False}})
    out = {{"file": guide.__file__, "guide_dir": str(guide.guide_dir()), "pages": {{}}}}
    async with TestClient(TestServer(made.app())) as client:
        for path in ("/help/?from=stage", "/help/recording?from=rack", "/help/img/rack.png", "/"):
            r = await client.get(path, allow_redirects=False)
            out["pages"][path] = [r.status, (await r.read()).decode("utf-8", "replace")]
    made._executor.shutdown(wait=True, cancel_futures=True)
    return out

print(json.dumps(asyncio.run(go())))
"""
    done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60,
                          cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    said = json.loads(done.stdout.strip().splitlines()[-1])
    assert said["file"] == str(site / "sushi_rig" / "guide.py"), "the copy ran, not the checkout"
    assert said["guide_dir"] == str(tmp_path / "venv" / "lib" / "docs" / "guide")
    for path, back in (("/help/?from=stage", "/#stage"), ("/help/recording?from=rack", "/#rack")):
        status, body = said["pages"][path]
        assert status == 404, path
        assert "not beside this agent" in body and str(tmp_path / "venv" / "lib" / "docs" / "guide") in body
        assert "pip install -e" in body and f'href="{back}"' in body, path
    assert said["pages"]["/help/img/rack.png"][0] == 404
    status, body = said["pages"]["/"]
    assert status == 200 and 'src="/ui/app.js"' in body, "the page itself, and its Help links, are still served"


# --------------------------------------------------------------------------- #
# Every link of the guide, as served, followed
# --------------------------------------------------------------------------- #

class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a" and "href" in attrs:
            self.links.append(("a", attrs["href"]))
        if tag in ("img", "link") and (attrs.get("src") or attrs.get("href")):
            self.links.append((tag, attrs.get("src") or attrs["href"]))


@pytest.mark.parametrize("screen", ["stage", "rack"])
def test_every_link_inside_the_served_guide_resolves_and_keeps_the_screen(screen):
    """From the front page, every page reachable is fetched, and every link
    and picture on each: same-origin ones must answer 200, a link to a
    heading must find that heading's id on its page, and every link to a
    page carries `from`, so the way back still leads to the screen the
    guide was opened from. Every page of docs/guide/ must be reached."""
    made = agent()

    async def go():
        async with TestClient(TestServer(made.app())) as client:
            pages: dict[str, _Links] = {}
            todo = [f"/help/?from={screen}"]
            checked: dict[str, int] = {}
            problems = []
            while todo:
                url = todo.pop()
                path = urlsplit(url).path
                if path in pages:
                    continue
                response = await client.get(url, allow_redirects=False)
                if response.status != 200:
                    problems.append(f"{url}: {response.status}")
                    continue
                parsed = _Links()
                parsed.feed(await response.text())
                pages[path] = parsed
                for tag, href in parsed.links:
                    parts = urlsplit(href)
                    if parts.scheme:
                        assert parts.scheme in ("http", "https"), href
                        continue
                    if href.startswith("#"):
                        if href[1:] not in parsed.ids:
                            problems.append(f"{path}: no heading {href}")
                        continue
                    if parts.path.startswith("/help/") and not parts.path.startswith("/help/img/"):
                        if parse_qs(parts.query).get("from") != [screen]:
                            problems.append(f"{path}: {href} does not carry from={screen}")
                        todo.append(href)
                    elif parts.path != "/" or tag != "a":
                        if parts.path not in checked:
                            asked = await client.get(parts.path, allow_redirects=False)
                            checked[parts.path] = asked.status
                        if checked[parts.path] != 200:
                            problems.append(f"{path}: {href} answers {checked[parts.path]}")
                    else:
                        assert href == {"stage": "/#stage", "rack": "/#rack"}[screen], href
            # Anchors into other pages, now every page is read.
            for path, parsed in pages.items():
                for tag, href in parsed.links:
                    parts = urlsplit(href)
                    if parts.fragment and parts.path in pages and parts.fragment not in pages[parts.path].ids:
                        problems.append(f"{path}: {href}: {parts.path} has no heading #{parts.fragment}")
            return pages, checked, problems

    try:
        pages, checked, problems = asyncio.run(go())
    finally:
        made._executor.shutdown(wait=True, cancel_futures=True)
    assert problems == []
    expected = {"/help/"} | {f"/help/{p.stem}" for p in GUIDE.glob("*.md") if p.stem != "README"}
    assert set(pages) == expected, "every page of the guide is linked from the guide"
    pictures = {f"/help/img/{p.name}" for p in (GUIDE / "img").iterdir()}
    assert pictures <= set(checked), "every picture of the guide is shown on a page"
    assert checked.get("/ui/guide.css") == 200
