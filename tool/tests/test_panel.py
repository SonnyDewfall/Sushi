from sushi_rig.panel import build_osc_panel


def _live_info_for(real_dump, *, override=None):
    """Synthesize live_info matching real_dump's processors/params, all
    automatable with value 0.3 by default, so tests can assert against a
    known, deliberately non-0.5 value. `override` patches specific entries
    to (processor, param) -> {"automatable": ..., "value": ...}."""
    info = {}
    for plugin in real_dump["plugins"]:
        info[plugin["name"]] = {
            p["name"]: {"automatable": True, "value": 0.3} for p in plugin["parameters"]
        }
    for (proc, param), patch in (override or {}).items():
        info[proc][param] = patch
    return info


def test_panel_one_tab_per_processor(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tabs = panel["widgets"][0]["tabs"]
    assert {t["id"] for t in tabs} == {
        "compressor_mono",
        "graph_equalizer_x16_stereo",
        "internal_reverb",
    }


def test_panel_widget_count_matches_automatable_parameter_count(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    compressor_params = next(p for p in real_dump["plugins"] if p["name"] == "compressor_mono")
    assert len(tabs["compressor_mono"]["widgets"]) == len(compressor_params["parameters"])


def test_panel_uses_osc_path_verbatim_not_a_constructed_address(real_dump):
    """The prototype constructed OSC addresses from parameter names. Sushi
    replaces spaces with underscores in real osc_path values, so a
    constructed address (still containing spaces) would never match."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    widget = next(
        w
        for w in tabs["compressor_mono"]["widgets"]
        if w["label"] == "Show pre-mix overlay"
    )
    assert widget["address"] == "/parameter/compressor_mono/Show_pre-mix_overlay"
    assert " " not in widget["address"]


def test_panel_range_is_normalised_zero_to_one(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    widget = panel["widgets"][0]["tabs"][0]["widgets"][0]
    assert widget["range"] == {"min": 0, "max": 1}


def test_panel_has_no_invented_sendport_property(real_dump):
    """sendPort is not a real open-stage-control session property - verified
    by loading a generated panel directly into open-stage-control 1.31.1 and
    checking its properties-reference docs. The prototype this was ported
    from invented it. The real send target is set via open-stage-control's
    own --send CLI flag at launch time."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    assert "sendPort" not in panel


def test_panel_outer_container_is_sized_to_fill_the_window(real_dump):
    """Verified against real open-stage-control: an unsized panel/tab renders
    as a tiny, near-unusable box regardless of how many widgets it holds."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    outer = panel["widgets"][0]
    assert outer["width"] == "100%"
    assert outer["height"] == "100%"


def test_panel_widgets_have_no_manual_pixel_coordinates(real_dump):
    """Manual left/top placement (what the prototype did) doesn't adapt to
    however many parameters a plugin has. Plain flow layout (the default,
    left unset here) wraps same-sized widgets automatically instead.

    layout: "grid" was tried and rejected: verified against real
    open-stage-control that it collapses every fader inside it to a sliver,
    while the exact same fader outside a grid container renders at its full
    set height."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in panel["widgets"][0]["tabs"]:
        assert "layout" not in tab or tab["layout"] == "default"
        for widget in tab["widgets"]:
            assert "left" not in widget
            assert "top" not in widget


def test_panel_fader_default_is_the_live_current_value_not_a_guessed_constant(real_dump):
    """The defect that actually happened on real hardware: every fader used
    to default to a hardcoded 0.5 normalised, regardless of the parameter's
    real domain. LSP's linear-amplitude gain parameters (compressor/chorus
    Input gain, Output gain, Makeup gain — domain up to 1000; every graphic
    EQ band gain — domain up to ~63) have their sane default sitting near
    the *bottom* of that range. 0.5 normalised there is 500x amplification -
    instant clipping, confirmed on real hardware. The fix: use whatever
    Sushi actually has loaded right now."""
    live_info = _live_info_for(
        real_dump,
        override={("compressor_mono", "Ratio"): {"automatable": True, "value": 0.030303}},
    )
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    ratio_widget = next(w for w in tabs["compressor_mono"]["widgets"] if w["label"] == "Ratio")
    assert ratio_widget["default"] == 0.030303
    # every other compressor widget in this test carries the synthetic 0.3
    other_widget = next(
        w for w in tabs["compressor_mono"]["widgets"] if w["label"] != "Ratio"
    )
    assert other_widget["default"] == 0.3


def test_panel_drops_non_automatable_parameters(real_dump):
    """Read-only meters ("Latency OUT", *_meter, *_visibility) are outputs,
    not controls. Presenting them as draggable faders is misleading and,
    per the automatable-filtering finding in live.py, potentially not
    harmless to set at all — they should not appear in the panel."""
    live_info = _live_info_for(
        real_dump,
        override={
            ("compressor_mono", "Show pre-mix overlay"): {"automatable": False, "value": 0.0}
        },
    )
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    labels = {w["label"] for w in tabs["compressor_mono"]["widgets"]}
    assert "Show pre-mix overlay" not in labels


def test_panel_skips_a_processor_missing_from_live_info(real_dump, capsys):
    """The panel is meant to be generated against the same config that's
    running live. If a processor in the dump isn't found live, that's a
    mismatch worth a warning, not a guess."""
    live_info = _live_info_for(real_dump)
    del live_info["internal_reverb"]
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"] for t in panel["widgets"][0]["tabs"]}
    assert "internal_reverb" not in tabs
    assert "internal_reverb" in capsys.readouterr().err
