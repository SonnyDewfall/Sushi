from sushi_rig.panel import build_osc_panel


def test_panel_one_tab_per_processor(real_dump):
    panel = build_osc_panel(real_dump)
    tabs = panel["widgets"][0]["tabs"]
    assert {t["id"] for t in tabs} == {
        "compressor_mono",
        "graph_equalizer_x16_stereo",
        "internal_reverb",
    }


def test_panel_widget_count_matches_parameter_count(real_dump):
    panel = build_osc_panel(real_dump)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    compressor_params = next(p for p in real_dump["plugins"] if p["name"] == "compressor_mono")
    assert len(tabs["compressor_mono"]["widgets"]) == len(compressor_params["parameters"])


def test_panel_uses_osc_path_verbatim_not_a_constructed_address(real_dump):
    """The prototype constructed OSC addresses from parameter names. Sushi
    replaces spaces with underscores in real osc_path values, so a
    constructed address (still containing spaces) would never match."""
    panel = build_osc_panel(real_dump)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    widget = next(
        w
        for w in tabs["compressor_mono"]["widgets"]
        if w["label"] == "Show pre-mix overlay"
    )
    assert widget["address"] == "/parameter/compressor_mono/Show_pre-mix_overlay"
    assert " " not in widget["address"]


def test_panel_range_is_normalised_zero_to_one(real_dump):
    panel = build_osc_panel(real_dump)
    widget = panel["widgets"][0]["tabs"][0]["widgets"][0]
    assert widget["range"] == {"min": 0, "max": 1}


def test_panel_has_no_invented_sendport_property(real_dump):
    """sendPort is not a real open-stage-control session property - verified
    by loading a generated panel directly into open-stage-control 1.31.1 and
    checking its properties-reference docs. The prototype this was ported
    from invented it. The real send target is set via open-stage-control's
    own --send CLI flag at launch time."""
    panel = build_osc_panel(real_dump)
    assert "sendPort" not in panel


def test_panel_outer_container_is_sized_to_fill_the_window(real_dump):
    """Verified against real open-stage-control: an unsized panel/tab renders
    as a tiny, near-unusable box regardless of how many widgets it holds."""
    panel = build_osc_panel(real_dump)
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
    panel = build_osc_panel(real_dump)
    for tab in panel["widgets"][0]["tabs"]:
        assert "layout" not in tab or tab["layout"] == "default"
        for widget in tab["widgets"]:
            assert "left" not in widget
            assert "top" not in widget
