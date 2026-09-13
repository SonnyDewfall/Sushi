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


def test_panel_sends_to_requested_osc_port(real_dump):
    panel = build_osc_panel(real_dump, osc_port=9999)
    assert panel["sendPort"] == "9999"
