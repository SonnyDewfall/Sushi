from sushi_rig.panel import LOG_SCALE_DOMAIN_THRESHOLD, build_osc_panel


def _live_info_for(real_dump, *, override=None):
    """Synthesize live_info matching real_dump's processors/params, all
    automatable with value 0.3 and a narrow [0, 1] domain by default, so
    tests can assert against known, deliberately non-0.5, non-wide-domain
    values. `override` patches specific entries to
    (processor, param) -> {"automatable", "value", "min_domain_value"?,
    "max_domain_value"?}."""
    info = {}
    for plugin in real_dump["plugins"]:
        info[plugin["name"]] = {
            p["name"]: {
                "automatable": True,
                "value": 0.3,
                "min_domain_value": 0.0,
                "max_domain_value": 1.0,
            }
            for p in plugin["parameters"]
        }
    for (proc, param), patch in (override or {}).items():
        info[proc][param] = patch
    return info


def _faders(tab):
    return [w for w in tab["widgets"] if w["type"] == "fader"]


def test_panel_one_tab_per_processor(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tabs = panel["widgets"][0]["tabs"]
    assert {t["id"] for t in tabs} == {
        "compressor_mono",
        "graph_equalizer_x16_stereo",
        "internal_reverb",
    }


def test_panel_fader_count_matches_automatable_parameter_count(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    compressor_params = next(p for p in real_dump["plugins"] if p["name"] == "compressor_mono")
    assert len(_faders(tabs["compressor_mono"])) == len(compressor_params["parameters"])


def test_panel_uses_osc_path_verbatim_not_a_constructed_address(real_dump):
    """The prototype constructed OSC addresses from parameter names. Sushi
    replaces spaces with underscores in real osc_path values, so a
    constructed address (still containing spaces) would never match."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    widget = next(
        w for w in _faders(tabs["compressor_mono"]) if w["label"] == "Show pre-mix overlay"
    )
    assert widget["address"] == "/parameter/compressor_mono/Show_pre-mix_overlay"
    assert " " not in widget["address"]


def test_panel_range_is_normalised_zero_to_one(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    widget = _faders(panel["widgets"][0]["tabs"][0])[0]
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
        override={
            ("compressor_mono", "Ratio"): {
                "automatable": True,
                "value": 0.030303,
                "min_domain_value": 1.0,
                "max_domain_value": 100.0,
            }
        },
    )
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    ratio_widget = next(w for w in _faders(tabs["compressor_mono"]) if w["label"] == "Ratio")
    assert ratio_widget["default"] == 0.030303
    # every other compressor fader in this test carries the synthetic 0.3
    other_widget = next(w for w in _faders(tabs["compressor_mono"]) if w["label"] != "Ratio")
    assert other_widget["default"] == 0.3


def test_panel_drops_non_automatable_parameters(real_dump):
    """Read-only meters ("Latency OUT", *_meter, *_visibility) are outputs,
    not controls. Presenting them as draggable faders is misleading and,
    per the automatable-filtering finding in live.py, potentially not
    harmless to set at all — they should not appear in the panel."""
    live_info = _live_info_for(
        real_dump,
        override={
            ("compressor_mono", "Show pre-mix overlay"): {
                "automatable": False,
                "value": 0.0,
                "min_domain_value": 0.0,
                "max_domain_value": 1.0,
            }
        },
    )
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    labels = {w["label"] for w in _faders(tabs["compressor_mono"])}
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


def test_panel_applies_logscale_to_wide_domain_parameters(real_dump):
    """The second real bug found on hardware: even with a safe starting
    value, a linear-response fader over a domain like [~0.016, ~63] (any
    graphic EQ band gain) squeezes the entire useful +/-12dB range into the
    bottom ~6% of the fader's travel. logScale redistributes drag
    resolution toward the low end without changing the underlying [0,1]
    value sent to Sushi — verified by reading open-stage-control's own
    mapToScale() source and by dragging two otherwise-identical faders the
    same distance (linear read 0.25, logScale read 0.09)."""
    live_info = _live_info_for(
        real_dump,
        override={
            ("compressor_mono", "Output gain"): {
                "automatable": True,
                "value": 0.001,
                "min_domain_value": 0.0,
                "max_domain_value": 1000.0,
            }
        },
    )
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in panel["widgets"][0]["tabs"]}
    widget = next(w for w in _faders(tabs["compressor_mono"]) if w["label"] == "Output gain")
    assert widget["logScale"] is True


def test_panel_does_not_apply_logscale_to_narrow_domain_parameters(real_dump):
    live_info = _live_info_for(real_dump)  # every synthetic param has domain [0, 1]
    panel = build_osc_panel(real_dump, live_info)
    for tab in panel["widgets"][0]["tabs"]:
        for widget in _faders(tab):
            assert "logScale" not in widget


def test_panel_logscale_threshold_is_a_domain_ratio_not_hardcoded_per_parameter():
    """Documents the threshold as a real, named constant rather than a magic
    number scattered through build_osc_panel."""
    assert LOG_SCALE_DOMAIN_THRESHOLD == 10.0


def test_panel_each_fader_has_a_paired_value_readout(real_dump):
    """So the operator can see what value they're actually about to send,
    not just feel it via drag position. A bare shared `id` between a fader
    and a differently-typed widget (the docs describe this as a "clone"
    mechanism) was tried first against real open-stage-control and did not
    visibly update; the `@{widgetId}` live-reference syntax did."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tab = panel["widgets"][0]["tabs"][0]
    fader = _faders(tab)[0]
    readout = next(w for w in tab["widgets"] if w["type"] == "text")
    assert readout["value"] == f"@{{{fader['id']}}}"
    assert readout["interaction"] is False
    # Without label: false, open-stage-control falls back to showing the
    # widget's id as a label — confirmed on real hardware, where every
    # readout's long processor/parameter id overlapped the row above it.
    assert readout["label"] is False
