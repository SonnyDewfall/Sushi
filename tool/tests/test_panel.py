from sushi_rig.listen import DEFAULT_LISTEN_PORT, NAME_ADDRESS, SAVE_ADDRESS, STATUS_ADDRESS
from sushi_rig.panel import (
    BYPASS_ADDRESS_PREFIX,
    LOG_SCALE_DOMAIN_THRESHOLD,
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
    assert readout["value"] == f"{_param_name(fader)}\n@{{{fader['id']}}}"
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
    assert readout["value"] == f"Band gain 1.6K\n@{{{widget['id']}}}"


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


def test_every_plugin_tab_has_a_bypass_toggle(real_dump):
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


def test_bypass_toggle_reflects_live_state_rather_than_forcing_it(real_dump):
    """Opening a panel must not change the rig. The toggle starts at whatever
    Sushi currently reports, and ignoreDefaults stops it sending on load —
    otherwise opening the panel would silently un-bypass a pedal that a saved
    config deliberately had switched off."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(
        real_dump, live_info, bypass_info={"compressor_mono": True}
    )
    tabs = {t["id"]: t for t in _tabs(panel)}
    assert _bypass_toggle(tabs["compressor_mono"])["default"] == 1
    assert _bypass_toggle(tabs["internal_reverb"])["default"] == 0
    for tab in _tabs(panel):
        assert _bypass_toggle(tab)["ignoreDefaults"] is True


def test_bypass_toggle_targets_sushi_not_the_save_listener(real_dump):
    """The save bar's widgets carry an explicit target so they reach the
    listener. Bypass must go to Sushi itself, which means having no target at
    all and riding open-stage-control's --send, exactly like the faders do."""
    live_info = _live_info_for(real_dump)
    panel = build_osc_panel(real_dump, live_info)
    for tab in _tabs(panel):
        assert "target" not in _bypass_toggle(tab)


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
