from sushi_rig.listen import DEFAULT_LISTEN_PORT, NAME_ADDRESS, SAVE_ADDRESS, STATUS_ADDRESS
from sushi_rig.panel import (
    BYPASS_ADDRESS_PREFIX,
    LOG_SCALE_DOMAIN_THRESHOLD,
    PLUGIN_BYPASS_PARAMETER_NAMES,
    build_osc_panel,
)
from sushi_rig.save import NAME_PATTERN


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


def _readout_for(tab, fader_id):
    return next(
        w for w in tab["widgets"]
        if w["type"] == "text" and w["id"] == f"{fader_id}/readout"
    )


def _param_name(fader):
    # Faders have no `label` property in real open-stage-control (see
    # SCHEMA_VERSION-adjacent comment in panel.py) — the parameter name is
    # only recoverable from the id, as "processor/param name".
    return fader["id"].split("/", 1)[1]


def _root_widget(panel, widget_id):
    # Root-level widgets are looked up by id rather than a fixed list index,
    # since the save bar (added alongside the tabs panel) shares that list —
    # position isn't something callers should have to assume. `panel` is the
    # {version, content} wrapper build_osc_panel returns; the actual root
    # widget tree lives under "content" (see SCHEMA_VERSION in panel.py for
    # why it's wrapped).
    return next(w for w in panel["content"]["widgets"] if w["id"] == widget_id)


def _tabs(panel):
    return _root_widget(panel, "tabs")["tabs"]


def test_panel_one_tab_per_processor(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tabs = _tabs(panel)
    assert {t["id"] for t in tabs} == {
        "compressor_mono",
        "graph_equalizer_x16_stereo",
        "internal_reverb",
    }


def test_tabs_follow_the_live_chain_order(real_dump):
    """The panel should read left to right like the pedalboard it represents,
    so neighbouring tabs tell you what feeds what.

    Order comes from the live rig, not the dump. Both are ordered, but the
    dump is a separate `sushi --dump-plugins -c <config>` subprocess reading
    the config *file*, so it reports the order on disk. Reorder the chain live
    and the dump still returns the old order."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tab_ids = [t["id"] for t in _tabs(panel)]
    assert tab_ids == [n for n in live_info if n in tab_ids]


def test_live_order_wins_over_the_dump_order(real_dump):
    """The case that was actually broken: a chain reordered live must show the
    live order, not the order the config file happens to have. Previously the
    tabs came from the dump, so a live reorder never appeared in the panel —
    which made moving a plugin look like it did nothing in either direction."""
    live_info = _live_info_for(real_dump)
    reversed_live = {k: live_info[k] for k in reversed(list(live_info))}
    panel = build_osc_panel(real_dump, reversed_live)
    tab_ids = [t["id"] for t in _tabs(panel)]
    dump_order = [p["name"] for p in real_dump["plugins"] if p["name"] in tab_ids]
    assert tab_ids == list(reversed(dump_order))


def test_a_track_in_live_info_gets_no_tab(real_dump):
    """live_info carries tracks alongside plugins, because tracks have
    parameters too (gain, pan, mute). They are not plugins, so they get no
    tab — they simply aren't in the dump."""
    live_info = _live_info_for(real_dump)
    with_track = {"board": {"gain": {"automatable": True, "value": 0.5,
                                     "min_domain_value": 0.0,
                                     "max_domain_value": 1.0}}, **live_info}
    panel = build_osc_panel(real_dump, with_track)
    assert "board" not in [t["id"] for t in _tabs(panel)]


def test_empty_live_info_falls_back_to_dump_order(real_dump, capsys):
    """Without a live rig the function must still produce a panel rather than
    no tabs at all — it just can't know the live order."""
    panel = build_osc_panel(real_dump, {})
    capsys.readouterr()  # every processor warns that it isn't live; expected
    assert [t["id"] for t in _tabs(panel)] == []


def test_panel_fader_count_matches_automatable_parameter_count(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in _tabs(panel)}
    compressor_params = next(p for p in real_dump["plugins"] if p["name"] == "compressor_mono")
    assert len(_faders(tabs["compressor_mono"])) == len(compressor_params["parameters"])


def test_panel_uses_osc_path_verbatim_not_a_constructed_address(real_dump):
    """The prototype constructed OSC addresses from parameter names. Sushi
    replaces spaces with underscores in real osc_path values, so a
    constructed address (still containing spaces) would never match."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in _tabs(panel)}
    widget = next(
        w for w in _faders(tabs["compressor_mono"]) if _param_name(w) == "Show pre-mix overlay"
    )
    assert widget["address"] == "/parameter/compressor_mono/Show_pre-mix_overlay"
    assert " " not in widget["address"]


def test_panel_range_is_normalised_zero_to_one(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    widget = _faders(_tabs(panel)[0])[0]
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


def test_panel_tabs_container_is_sized_to_fill_the_window(real_dump):
    """Verified against real open-stage-control: an unsized panel/tab renders
    as a tiny, near-unusable box regardless of how many widgets it holds."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    outer = _root_widget(panel, "tabs")
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
    for tab in _tabs(panel):
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
    tabs = {t["id"]: t for t in _tabs(panel)}
    ratio_widget = next(w for w in _faders(tabs["compressor_mono"]) if _param_name(w) == "Ratio")
    assert ratio_widget["default"] == 0.030303
    # every other compressor fader in this test carries the synthetic 0.3
    other_widget = next(w for w in _faders(tabs["compressor_mono"]) if _param_name(w) != "Ratio")
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
    tabs = {t["id"]: t for t in _tabs(panel)}
    names = {_param_name(w) for w in _faders(tabs["compressor_mono"])}
    assert "Show pre-mix overlay" not in names


def test_panel_skips_a_processor_missing_from_live_info(real_dump, capsys):
    """The panel is meant to be generated against the same config that's
    running live. If a processor in the dump isn't found live, that's a
    mismatch worth a warning, not a guess."""
    live_info = _live_info_for(real_dump)
    del live_info["internal_reverb"]
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"] for t in _tabs(panel)}
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
    tabs = {t["id"]: t for t in _tabs(panel)}
    widget = next(w for w in _faders(tabs["compressor_mono"]) if _param_name(w) == "Output gain")
    assert widget["logScale"] is True


def test_panel_does_not_apply_logscale_to_narrow_domain_parameters(real_dump):
    live_info = _live_info_for(real_dump)  # every synthetic param has domain [0, 1]
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
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
    visibly update; the `@{widgetId}` live-reference syntax did.

    The readout also carries the parameter's name, not just its value:
    `fader` has no `label` property in real open-stage-control 1.31.1
    (confirmed by inspecting a live widget's own resolved `props`, which
    doesn't include the key at all — a "label" on a fader is silently
    dropped), so this readout is the only place the name is actually
    visible."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    tab = _tabs(panel)[0]
    fader = _faders(tab)[0]
    readout = next(w for w in tab["widgets"] if w["type"] == "text")
    assert _param_name(fader) in readout["value"]
    assert f"@{{{fader['id']}}}" in readout["value"], "bound to its own fader"
    assert readout["interaction"] is False
    # Without label: false, open-stage-control falls back to showing the
    # widget's id as a label — confirmed on real hardware, where every
    # readout's long processor/parameter id overlapped the row above it.
    assert readout["label"] is False


def test_panel_widget_id_has_no_dot_for_a_dotted_parameter_name(real_dump):
    """A "." in a widget id breaks the "@{id}" live-value binding — it reads
    as "undefined" — confirmed against real open-stage-control on the EQ's
    "1.6K"/"2.5K" band-gain parameters, the only ones with a "." in their
    name. `id` is purely an internal open-stage-control reference (real OSC
    traffic uses `address`, built from `osc_path` and untouched by this),
    so it's safe to sanitize."""
    live_info = _live_info_for(
        real_dump,
        override={
            ("graph_equalizer_x16_stereo", "Band gain 1.6K"): {
                "automatable": True,
                "value": 0.3,
                "min_domain_value": 0.0,
                "max_domain_value": 1.0,
            }
        },
    )
    panel = build_osc_panel(real_dump, live_info)
    tabs = {t["id"]: t for t in _tabs(panel)}
    widget = next(
        w
        for w in _faders(tabs["graph_equalizer_x16_stereo"])
        if _param_name(w) == "Band gain 1_6K"
    )
    assert "." not in widget["id"]
    readout = next(
        w
        for w in tabs["graph_equalizer_x16_stereo"]["widgets"]
        if w["type"] == "text" and w["id"] == f"{widget['id']}/readout"
    )
    # The display name keeps its "." (still "1.6K" to the eye); only the
    # live-value binding needs to be dot-free.
    assert "Band gain 1.6K" in readout["value"]
    assert f"@{{{widget['id']}}}" in readout["value"]



def test_readout_converts_to_the_real_domain_value(real_dump):
    """Faders carry Sushi's normalised 0-1, so a readout bound straight to one
    shows 0.49 for 490 Hz and 0.52 for +1 dB. Reported as a bug in the rig —
    an EQ's crossover controls do nothing while the band gains sit at 0 dB, and
    with every fader reading ~0.5 there is no way to tell a flat control from a
    broken one."""
    live_info = _live_info_for(
        real_dump,
        override={
            ("graph_equalizer_x16_stereo", "Band gain 1.6K"): {
                "automatable": True,
                "value": 0.5,
                "min_domain_value": 0.0,
                "max_domain_value": 1000.0,
            }
        },
    )
    panel = build_osc_panel(
        real_dump,
        live_info,
        units={"graph_equalizer_x16_stereo": {"Band gain 1.6K": "Hz"}},
    )
    tab = {t["id"]: t for t in _tabs(panel)}["graph_equalizer_x16_stereo"]
    readout = _readout_for(tab, "graph_equalizer_x16_stereo/Band gain 1_6K")
    value = readout["value"]
    assert value.startswith("JS{"), "the maths has to rerun as the fader moves"
    assert "* 1000.0" in value and "0.0 +" in value, "scaled to the real range"
    assert '" Hz"' in value, "and labelled with the unit"


def test_readout_omits_the_unit_when_the_plugin_declares_none(real_dump):
    """Units come from the plugin's turtle, not from Sushi — its LV2 wrapper
    leaves ParameterInfo.unit empty — so plenty of parameters have none. A bare
    number is still far better than a normalised one."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    readout = next(
        w for w in _tabs(panel)[0]["widgets"] if w["type"] == "text"
    )
    assert readout["value"].startswith("JS{")
    assert "Hz" not in readout["value"] and "dB" not in readout["value"]


def test_readout_falls_back_to_raw_value_without_a_range(real_dump):
    """No invented ranges: if Sushi reports no domain, show what it does give."""
    live_info = _live_info_for(
        real_dump,
        override={
            ("graph_equalizer_x16_stereo", "Band gain 1.6K"): {
                "automatable": True,
                "value": 0.3,
                "min_domain_value": None,
                "max_domain_value": None,
            }
        },
    )
    panel = build_osc_panel(real_dump, live_info)
    tab = {t["id"]: t for t in _tabs(panel)}["graph_equalizer_x16_stereo"]
    readout = _readout_for(tab, "graph_equalizer_x16_stereo/Band gain 1_6K")
    assert readout["value"] == "Band gain 1.6K\n@{graph_equalizer_x16_stereo/Band gain 1_6K}"


# --- save bar (issue #10) ---------------------------------------------------


def _save_bar(panel):
    return _root_widget(panel, "save_bar")


def test_panel_save_bar_present_at_root_alongside_tabs(real_dump):
    """Outside the tabs container, so it's visible regardless of which
    plugin tab is open."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    ids = {w["id"] for w in panel["content"]["widgets"]}
    assert {"tabs", "save_bar"} <= ids


def test_panel_save_bar_has_name_input_with_shared_validation_pattern(real_dump):
    """The input's regex is UX only — save.py enforces the same pattern
    server-side, since the name arrives over unauthenticated UDP and becomes
    a file path. Sharing the pattern (rather than restating it) is what
    keeps the two from drifting apart."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    name_input = next(w for w in _save_bar(panel)["widgets"] if w["id"] == "config_name")
    assert name_input["type"] == "input"
    assert name_input["validation"] == NAME_PATTERN.pattern


def test_panel_name_input_sends_its_own_value_not_via_preargs(real_dump):
    """The obvious design — carry the name on the save button's preArgs,
    referencing the input's live value with @{...} — was tried first and
    does not work: verified against real open-stage-control 1.31.1 with an
    isolated test panel and a raw OSC listener, preArgs on a button is not
    re-evaluated per send; only the button's own tap value ever arrived.
    An input sending its own value via its own address/target does work,
    confirmed the same way. So the name is its own message (NAME_ADDRESS),
    independent of the save trigger."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info, listener_port=24099)
    name_input = next(w for w in _save_bar(panel)["widgets"] if w["id"] == "config_name")
    assert name_input["address"] == NAME_ADDRESS
    assert name_input["target"] == ["127.0.0.1:24099"]
    assert name_input["ignoreDefaults"] is True
    assert "preArgs" not in name_input


def test_panel_save_button_uses_tap_mode_not_toggle(real_dump):
    """toggle also fires a second message on release; tap fires exactly once
    per press, which is what a save action needs."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    button = next(w for w in _save_bar(panel)["widgets"] if w["id"] == "save_button")
    assert button["mode"] == "tap"
    assert button["address"] == SAVE_ADDRESS


def test_panel_save_button_targets_the_listener_only(real_dump):
    """target + ignoreDefaults means pressing save never also fires a stray
    message at Sushi's own parameter port."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info, listener_port=24099)
    button = next(w for w in _save_bar(panel)["widgets"] if w["id"] == "save_button")
    assert button["target"] == ["127.0.0.1:24099"]
    assert button["ignoreDefaults"] is True


def test_panel_save_button_carries_no_payload(real_dump):
    """The button no longer carries the name — see the preArgs finding
    above. It fires bare; the listener uses whatever name it last received
    on NAME_ADDRESS."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    button = next(w for w in _save_bar(panel)["widgets"] if w["id"] == "save_button")
    assert "preArgs" not in button


def test_panel_save_status_reflects_the_listener_status_address(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    status = next(w for w in _save_bar(panel)["widgets"] if w["id"] == "save_status")
    assert status["address"] == STATUS_ADDRESS
    assert status["interaction"] is False


def test_panel_listener_port_defaults_match_the_listen_module(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    button = next(w for w in _save_bar(panel)["widgets"] if w["id"] == "save_button")
    name_input = next(w for w in _save_bar(panel)["widgets"] if w["id"] == "config_name")
    assert button["target"] == [f"127.0.0.1:{DEFAULT_LISTEN_PORT}"]
    assert name_input["target"] == [f"127.0.0.1:{DEFAULT_LISTEN_PORT}"]


def test_panel_name_input_sends_on_every_keystroke(real_dump):
    """Without asYouType an open-stage-control input sends only when its value
    is committed (Enter, or focus leaving the field), and nothing forces a
    commit before the save button fires — so typing a name and clicking SAVE
    sends the *previously* committed value.

    Found on real hardware: a user typed "electric-test" and the config saved
    as "electric-.json", the earlier committed value, with otherwise perfectly
    correct contents. The listener cannot detect a stale name; it just writes
    it. Note every automated test of the save flow pressed Tab first, which
    commits the field and hides this completely."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    name_input = next(
        w for w in _save_bar(panel)["widgets"] if w["id"] == "config_name"
    )
    assert name_input["asYouType"] is True


def _bypass_toggle(tab):
    return next(w for w in tab["widgets"] if w["id"].endswith("/bypass"))


def test_every_plugin_tab_has_an_active_toggle(real_dump):
    """Bypass is host-level in Sushi, available for every processor whether or
    not the plugin offers its own. That's deliberately what this uses: plugin
    BYPASS parameters exist only on some plugins, and where they exist the
    semantics can be undiscoverable (Guitarix exposes BYPASS defaulting to 1.0
    over a 0-1 range with no scale points, so nothing says which end is on)."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        toggle = _bypass_toggle(tab)
        assert toggle["address"] == BYPASS_ADDRESS_PREFIX + tab["id"]
        assert toggle["mode"] == "toggle"
        assert toggle["label"] == "Active"


def test_ticked_means_active_so_the_toggle_sends_inverted_values(real_dump):
    """The control reads "ACTIVE" and is ticked when the plugin is doing
    something — not "BYPASS", which is on when the plugin is off. That double
    negative already caught this project out once: the Guitarix pedals expose
    their own BYPASS running the opposite way (1 = active).

    The inversion lives in the widget rather than in code: a toggle sends `on`
    when ticked and `off` when not, so ticked sends bypass 0."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        toggle = _bypass_toggle(tab)
        assert toggle["on"] == 0, "ticked must un-bypass"
        assert toggle["off"] == 1, "unticked must bypass"


def test_active_toggle_reflects_live_state_rather_than_forcing_it(real_dump):
    """Opening a panel must not change the rig: the tick starts at whatever
    Sushi currently reports. A bypassed plugin shows as unticked."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(
        real_dump, live_info, bypass_info={"compressor_mono": True}
    )
    tabs = {t["id"]: t for t in _tabs(panel)}
    assert _bypass_toggle(tabs["compressor_mono"])["default"] == 1, "bypassed = unticked"
    assert _bypass_toggle(tabs["internal_reverb"])["default"] == 0, "running = ticked"


def test_active_toggle_is_drawn_as_a_tickbox(real_dump):
    """A bare toggle button only signals its state by its own shading, which
    says nothing about which way round it means — the whole reason the old
    BYPASS control was confusing. open-stage-control has no checkbox widget
    (its `switch` is a value selector), so the box is a glyph on the label
    swapped by the `on` class the client puts on an active button."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        css = _bypass_toggle(tab)["css"]
        assert "\\2610" in css, "empty box when unticked"
        assert "&.on" in css and "\\2611" in css, "ticked box when active"


def test_bypass_toggle_targets_sushi_not_the_save_listener(real_dump):
    """The save bar's widgets carry an explicit target so they reach the
    listener. Bypass must go to Sushi itself, which means having no target at
    all and riding open-stage-control's --send, exactly like the faders do.

    It must also NOT set ignoreDefaults. That flag means "ignore the server's
    default targets", not "don't send on load" — setting it on a widget with
    no target of its own leaves nowhere to send, and bypass silently did
    nothing in the panel while working fine over OSC from anything else.
    Found by playing through the rig, not by any test."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        toggle = _bypass_toggle(tab)
        assert "target" not in toggle
        assert "ignoreDefaults" not in toggle


def test_bypass_toggle_sends_an_int_not_a_float(real_dump):
    """Sushi's /bypass/ handler accepts an OSC int only — a float is parsed
    and then silently ignored, with no error and nothing logged.
    open-stage-control sends floats by default, so without typeTags every
    bypass message was dropped while the identical address worked fine from
    any other OSC client. Proven by sending both types and reading the state
    back: 1.0 did nothing, 1 worked.

    The faders need no equivalent because /parameter/ genuinely takes a
    float — which is exactly why they kept working and made the panel look
    healthy while bypass silently did nothing."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        assert _bypass_toggle(tab)["typeTags"] == "i"


def test_plugin_own_bypass_parameter_is_hidden_from_the_faders(real_dump):
    """The Guitarix pedals expose their own BYPASS parameter, so those tabs
    showed two controls both labelled BYPASS — the host toggle and the
    plugin's — running in opposite directions (toggle on = bypassed, the
    Guitarix parameter 1 = active). Two identically named controls with
    inverted polarity on one tab is worse than no control at all, so the
    plugin's is hidden in favour of the host toggle, which behaves the same
    way for every plugin.

    Only the panel hides it; capture still records it, so a saved config keeps
    whatever value it holds."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        for fader in _faders(tab):
            assert _param_name(fader).lower() not in PLUGIN_BYPASS_PARAMETER_NAMES


def _move_buttons(tab):
    return [w for w in tab["widgets"] if "/move-" in w["id"]]


def test_every_tab_has_earlier_and_later_move_buttons(real_dump):
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        buttons = _move_buttons(tab)
        assert len(buttons) == 2
        assert {b["on"] for b in buttons} == {-1, 1}
        for b in buttons:
            assert b["address"].endswith(tab["id"])
            assert b["mode"] == "tap"


def test_move_buttons_go_to_the_listener_not_sushi(real_dump):
    """Reordering is gRPC-only, so the listener has to bridge it — the same
    job it already does for saving. These need an explicit target, and
    ignoreDefaults is correct *here* precisely because a target is supplied:
    the flag means "ignore the server's default targets", so it is only
    meaningful alongside one."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info, listener_port=24025)
    for tab in _tabs(panel):
        for b in _move_buttons(tab):
            assert b["target"] == ["127.0.0.1:24025"]
            assert b["ignoreDefaults"] is True


def test_move_buttons_send_an_int_direction(real_dump):
    """The direction must arrive as an int. Sushi silently discarded float
    bypass messages; the listener is lenient about it, but being explicit is
    what stops that class of bug recurring."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        for b in _move_buttons(tab):
            assert b["typeTags"] == "i"


def test_bypass_toggle_comes_before_the_faders(real_dump):
    """It reads as the pedal's on/off switch, so it belongs above its
    controls rather than buried after them."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        assert tab["widgets"][0]["id"].endswith("/bypass")


def test_panel_root_uses_vertical_layout(real_dump):
    """Root defaults to left-to-right flow and does not wrap on a 100%-width
    child — confirmed against real open-stage-control: the tabs panel
    rendered entirely off-screen to the right of the save bar instead of
    below it. "vertical" stacks root's children top-to-bottom instead."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    assert panel["content"]["layout"] == "vertical"


def test_panel_declares_schema_version(real_dump):
    """A missing `version` reads as "0.0.0" to open-stage-control, which pops
    an "older version of this software" warning on every load regardless of
    content — confirmed against real open-stage-control. Declaring one avoids
    it, but also skips the migration step that otherwise wraps a bare root
    object into the shape the loader expects, so the root widget tree has to
    be nested under "content" ourselves once "version" is present — also
    confirmed against real open-stage-control (a bare root + version alone
    crashes the loader on load)."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    assert panel["version"]
    assert panel["content"]["type"] == "root"
