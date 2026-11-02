"""The plugin library (#32): what `GET /library` lists, read without lilv.

The bundles under `fixtures/lv2/` are **captured, not written**: the `.ttl`
files of three bundles exactly as Ubuntu 24.04's packages install them
(guitarix-lv2 0.46.0+dfsg-1, rubberband-lv2 3.3.0+dfsg-2build1, mda-lv2
1.2.10-2build1), extracted from the .deb files on 2026-11-02, with the shared
libraries left out. One thing is cut down, and only this: `mda.lv2` keeps its
full `manifest.ttl` but only one of the plugin data files it points at
(`Delay.ttl`), so the other 35 mda plugins are declared with no data, which is
also what a bundle missing a file looks like. The expected names and port counts
below are what lilv 0.24.22 reported for the same files (run once, by hand, with
the packages above on `LV2_PATH`); the last test asks lilv again where it is
installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sushi_rig import library
from sushi_rig.library import (
    BNode,
    Literal,
    TurtleError,
    build_library,
    load_profiles,
    parse_turtle,
    plugins_described,
    read_bundle,
    read_library,
    scan_plugins,
    uri_segment,
)
from sushi_rig.paths import checkout_root
from sushi_rig.plugins import read_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "lv2"
ROOT = checkout_root()
MANIFEST = read_manifest((ROOT / "plugin-manifest.txt").read_text())
PROFILES = ROOT / "tool" / "examples" / "profiles"
GXTS9 = "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim"
NAM = "http://github.com/mikeoliphant/neural-amp-modeler-lv2"

LV2 = "http://lv2plug.in/ns/lv2core#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


# --------------------------------------------------------------------------- #
# Turtle, the part of it LV2 bundles use
# --------------------------------------------------------------------------- #

def test_prefixes_relative_iris_and_the_a_keyword():
    triples = parse_turtle(
        "@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n"
        "PREFIX ex: <http://example.org/>\n"
        "ex:p a lv2:Plugin ; <http://www.w3.org/2000/01/rdf-schema#seeAlso> <p.ttl> .\n",
        base="file:///b/x.lv2/manifest.ttl",
    )
    assert triples == [
        ("http://example.org/p", RDF_TYPE, LV2 + "Plugin"),
        ("http://example.org/p", "http://www.w3.org/2000/01/rdf-schema#seeAlso",
         "file:///b/x.lv2/p.ttl"),
    ]


def test_literals_numbers_and_the_full_stop_that_ends_a_statement():
    """`lv2:index 0 .` is the integer 0 and then the end: a decimal needs a
    digit after its point, which is what keeps the two apart."""
    triples = parse_turtle(
        '@prefix : <http://e/> .\n'
        ':s :name "Tube \\"Screamer\\""@en , \'\'\'two\nlines\'\'\' ;\n'
        '   :n 0 ; :m -1.5 ; :d 1e3 ; :b true ; :t "x"^^:type .\n'
        ':s :last 5.\n',
        base="file:///f.ttl",
    )
    objects = [o for _, _, o in triples]
    assert objects[0] == Literal('Tube "Screamer"') and objects[0].lang == "en"
    assert objects[1] == Literal("two\nlines")
    assert [str(o) for o in objects[2:]] == ["0", "-1.5", "1e3", "true", "x", "5"]
    assert objects[6].datatype == "http://e/type"


def test_blank_nodes_nest_and_collections_are_lists():
    triples = parse_turtle(
        "@prefix : <http://e/> .\n"
        ":p :port [ :index 0 ; :name \"In\" ] , [ :index 1 ] ; :list ( 1 2 ) .\n"
        "_:x :q :r .\n",
        base="file:///f.ttl",
    )
    ports = [o for s, p, o in triples if p == "http://e/port"]
    assert len(ports) == 2 and all(isinstance(o, BNode) for o in ports)
    assert ("http://e/q" in {p for _, p, _ in triples})
    names = [o for s, p, o in triples if s == ports[0] and p == "http://e/name"]
    assert names == [Literal("In")]


@pytest.mark.parametrize("text, why", [
    ("<http://e/s> <http://e/p> .", "object"),
    ("ex:s <http://e/p> <http://e/o> .", "prefix 'ex'"),
    ('<http://e/s> <http://e/p> "never closed .', "unexpected"),
    ("<http://e/s> <http://e/p> <http://e/o>", "'.'"),
])
def test_what_is_not_turtle_is_refused_with_where(text, why):
    with pytest.raises(TurtleError, match=why):
        parse_turtle(text, base="file:///f.ttl")


def test_a_plugin_is_what_is_typed_one_and_its_name_is_its_own():
    """gxts9.ttl names its *project* "Gxts9sim" in the same file; a reader
    that took the first doap:name it met would call the pedal that."""
    base = (FIXTURES / "gxts9.lv2" / "gxts9.ttl").as_uri()
    described = plugins_described(parse_turtle((FIXTURES / "gxts9.lv2" / "gxts9.ttl").read_text(), base))
    assert described == {GXTS9: {"name": "GxTubeScreamer", "ports": 6}}


def test_the_uris_last_segment_is_the_fallback_name():
    assert uri_segment(GXTS9) == "ts9sim"
    assert uri_segment("urn:ardour:a-reverb") == "a-reverb"
    assert uri_segment("http://drobilla.net/plugins/mda/Delay") == "Delay"
    assert uri_segment(NAM) == "neural-amp-modeler-lv2"
    assert uri_segment("http://e/x/") == "x"


# --------------------------------------------------------------------------- #
# Bundles, as installed
# --------------------------------------------------------------------------- #

def test_a_bundle_is_read_from_its_manifest_and_the_files_it_points_at():
    found, problems = read_bundle(FIXTURES / "gxts9.lv2")
    assert found == {GXTS9: {"name": "GxTubeScreamer", "ports": 6, "bundle": "gxts9.lv2"}}
    assert problems == []


def test_one_file_can_describe_several_plugins_sharing_ports():
    """Rubber Band's four plugins live in one file and share a latency port
    node; lilv counts 9, 8, 11 and 10 ports."""
    found, problems = read_bundle(FIXTURES / "rubberband.lv2")
    assert problems == []
    assert {u.rsplit("#", 1)[1]: (m["name"], m["ports"]) for u, m in found.items()} == {
        "mono": ("Rubber Band Mono Pitch Shifter", 9),
        "r3mono": ("Rubber Band R3 Mono Pitch Shifter", 8),
        "stereo": ("Rubber Band Stereo Pitch Shifter", 11),
        "r3stereo": ("Rubber Band R3 Stereo Pitch Shifter", 10),
    }


def test_a_plugin_whose_data_file_is_missing_is_found_without_a_name_and_said():
    found, problems = read_bundle(FIXTURES / "mda.lv2")
    assert found["http://drobilla.net/plugins/mda/Delay"] == {
        "name": "MDA Delay", "ports": 10, "bundle": "mda.lv2"}
    assert found["http://drobilla.net/plugins/mda/Combo"] == {
        "name": None, "ports": None, "bundle": "mda.lv2"}
    assert len(found) == 36
    assert any("Combo.ttl" in p and "cannot be read" in p for p in problems)
    assert len(problems) == 35, "one per missing file, not one per preset"


def test_a_data_file_outside_its_bundle_is_not_followed(tmp_path):
    bundle = tmp_path / "x.lv2"
    bundle.mkdir()
    (tmp_path / "elsewhere.ttl").write_text('<urn:x> <http://usefulinc.com/ns/doap#name> "No" .\n')
    (bundle / "manifest.ttl").write_text(
        "@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "<urn:x> a lv2:Plugin ; rdfs:seeAlso <../elsewhere.ttl> .\n")
    found, problems = read_bundle(bundle)
    assert found == {"urn:x": {"name": None, "ports": None, "bundle": "x.lv2"}}
    assert any("outside the bundle" in p for p in problems)


def test_a_file_that_does_not_parse_is_a_problem_not_a_crash(tmp_path):
    bundle = tmp_path / "x.lv2"
    bundle.mkdir()
    (bundle / "manifest.ttl").write_text("<urn:x> a .\n")
    found, problems = read_bundle(bundle)
    assert found == {}
    assert len(problems) == 1 and "manifest.ttl" in problems[0]


def test_only_the_folder_given_is_scanned(tmp_path):
    """plugins/ and nothing else: never the system's LV2 paths, so the library
    is what the rig can load, not what this machine happens to have."""
    found, problems = scan_plugins(FIXTURES)
    assert set(found) >= {GXTS9, "http://drobilla.net/plugins/mda/Delay"}
    assert {m["bundle"] for m in found.values()} == {"gxts9.lv2", "rubberband.lv2", "mda.lv2"}
    absent, problems = scan_plugins(tmp_path / "nope")
    assert absent == {}
    assert problems and "no plugins folder" in problems[0]


# --------------------------------------------------------------------------- #
# The library: manifest, bundles, profiles
# --------------------------------------------------------------------------- #

def test_every_manifest_uri_is_listed_once_in_manifest_order():
    entries = build_library(MANIFEST, {}, {})["plugins"]
    assert [e["uri"] for e in entries] == MANIFEST
    assert len(entries) == 55


def test_a_name_read_from_the_bundle_is_marked_as_such():
    found, _ = read_bundle(FIXTURES / "gxts9.lv2")
    entry = next(e for e in build_library(MANIFEST, found, {})["plugins"] if e["uri"] == GXTS9)
    assert (entry["name"], entry["name_from"], entry["ports"], entry["bundle"]) == \
        ("GxTubeScreamer", "plugin", 6, "gxts9.lv2")
    assert "note" not in entry


def test_a_name_that_could_not_be_read_falls_back_to_the_uri_and_says_why():
    found, _ = read_bundle(FIXTURES / "mda.lv2")
    entries = {e["uri"]: e for e in build_library(MANIFEST, found, {})["plugins"]}
    combo = entries["http://drobilla.net/plugins/mda/Combo"]
    assert (combo["name"], combo["name_from"]) == ("Combo", "uri")
    assert "lilv" in combo["note"] and "doap:name" in combo["note"]
    reverb = entries["urn:ardour:a-reverb"]
    assert (reverb["name"], reverb["name_from"], reverb["bundle"]) == ("a-reverb", "uri", None)
    assert "not found in plugins/" in reverb["note"]


def test_the_amp_is_listed_but_cannot_be_added_and_says_why():
    entry = next(e for e in build_library(MANIFEST, {}, {})["plugins"] if e["uri"] == NAM)
    assert entry["addable"] is False
    assert "Carla" in entry["why_not"]
    others = [e for e in build_library(MANIFEST, {}, {})["plugins"] if e["uri"] != NAM]
    assert all(e["addable"] is True and "why_not" not in e for e in others)


def test_the_one_profile_gives_its_plugin_groups():
    profiles, problems = load_profiles(PROFILES)
    assert problems == []
    assert set(profiles) == {GXTS9}
    entries = {e["uri"]: e for e in build_library(MANIFEST, {}, profiles)["plugins"]}
    assert entries[GXTS9]["profile"] is True
    assert entries[GXTS9]["groups"] == [
        {"id": "dirt", "label": "Drive and tone", "provenance": "draft"},
        {"id": "output", "label": "Output", "provenance": "draft"},
    ]
    assert (entries[GXTS9]["name"], entries[GXTS9]["name_from"]) == ("GxTubeScreamer", "profile"), \
        "with no bundle to read, the profile's restated name is the next best"
    assert all(e["profile"] is False and e["groups"] is None
               for u, e in entries.items() if u != GXTS9)


def test_a_profile_for_a_plugin_the_manifest_lacks_is_a_problem(tmp_path):
    (tmp_path / "x.yaml").write_text("plugin: {uri: 'urn:nope', name: Nope}\ngroups: []\n")
    (tmp_path / "bad.yaml").write_text("plugin: [\n")
    profiles, problems = load_profiles(tmp_path)
    assert set(profiles) == {"urn:nope"}
    assert len(problems) == 1 and "bad.yaml" in problems[0]
    result = build_library(MANIFEST, {}, profiles)
    assert any("urn:nope" in p and "not in the manifest" in p for p in result["problems"])


def test_read_library_against_the_real_manifest_and_profile(tmp_path):
    """The real files; the plugins folder is the captured fixtures, since this
    machine may have no plugins/ (it is untracked)."""
    result = read_library(ROOT, plugins_dir=FIXTURES)
    entries = {e["uri"]: e for e in result["plugins"]}
    assert len(entries) == 55
    assert entries[GXTS9]["name"] == "GxTubeScreamer" and entries[GXTS9]["name_from"] == "plugin"
    assert entries[GXTS9]["groups"][0]["id"] == "dirt"
    assert result["sources"]["plugins_dir"] == str(FIXTURES)
    assert all(not p.startswith("/usr/lib") for p in result["problems"])


def test_read_library_says_when_the_manifest_cannot_be_read(tmp_path):
    with pytest.raises(library.LibraryError, match="plugin-manifest.txt"):
        read_library(tmp_path)


# --------------------------------------------------------------------------- #
# Against lilv, where it is installed
# --------------------------------------------------------------------------- #

def test_the_reader_agrees_with_lilv_on_every_bundle_in_plugins():
    """Where lilv and the rig's plugins/ are both present (the container, the
    rig machine), every name and port count read here is lilv's."""
    lilv = pytest.importorskip("lilv", reason="lilv is a system package (python3-lilv)")
    plugins_dir = ROOT / "plugins"
    if not plugins_dir.is_dir():
        pytest.skip(f"{plugins_dir} is not present (it is untracked; see README)")
    from sushi_rig.plugins import _lv2_path

    with _lv2_path(str(plugins_dir)):
        world = lilv.World()
        world.load_all()
    # A for statement, not a comprehension: lilv's collection iterator has a
    # __next__ and no __iter__, and on Python 3.13 a comprehension over it
    # raises "'Iter' object is not iterable" where a loop does not (found
    # running this test).
    truth = {}
    for plugin in world.get_all_plugins():
        truth[str(plugin.get_uri())] = (str(plugin.get_name()), plugin.get_num_ports())
    found, _ = scan_plugins(plugins_dir)
    ours = {u: (m["name"], m["ports"]) for u, m in found.items()}
    assert ours == truth
