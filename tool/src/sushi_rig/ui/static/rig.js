// The web UI's decisions (#31), pure: no DOM, no network, no clock but what
// is passed in. app.js draws what these return; test_ui.py imports this module
// into a bare page and asserts on what it returns. See sushi_rig/ui/__init__.py
// for why the UI is plain files.

// ---------------------------------------------------------------- the rig's names
// The compiler's names and uids, as rigfile.py has them (MASTER, RETURN,
// SEND_SUFFIX, SEND_UID, RETURN_UID). The agent's /live/plugins says each
// processor's role from Sushi's uid; these are only the fallback for a page
// that could not read it, the same name rule live.protected_processor applies.
export const MASTER = "master";
export const RETURN = "master_return";
export const SEND_SUFFIX = "_send";

// A parameter read back is "the same" within this, as the agent's undo
// decides it (agent.PARAMETER_TOLERANCE): Sushi holds a float32.
export const TOLERANCE = 1e-4;

// A send's gain is dB from -120 to +24 in Sushi 1.3.0's send_plugin.cpp
// (rigfile.LEVEL_RANGE), so the real value of its normalised gain is dB
// already; this entry gives it the unit no profile can, sends having none.
const SEND_GAIN = { name: "gain", label: "Level", unit: "dB", scale: "linear", visibility: "primary" };

export function roleOf(track, name, plugin) {
  if (plugin && "role" in plugin) return plugin.role || null;
  if (name === track + SEND_SUFFIX) return "send";
  if (track === MASTER && name === RETURN) return "return";
  return null;
}

// ---------------------------------------------------------------- values
// Sushi normalises every parameter linearly over its domain and ignores the
// LV2 logarithmic hint (panel.py, found on the rig), so a real value is
// min + v * (max - min) whatever the profile's scale. The scale changes how a
// drag maps to the value and how it is written, never this.

const isNumber = (x) => typeof x === "number" && Number.isFinite(x);
const clamp01 = (x) => Math.min(1, Math.max(0, x));

export function makeControl(name, info, entry) {
  const min = info && isNumber(info.min_domain_value) ? info.min_domain_value : null;
  const max = info && isNumber(info.max_domain_value) ? info.max_domain_value : null;
  const hasRange = min !== null && max !== null && max > min;
  const choices = entry && Array.isArray(entry.scale_points) && entry.scale_points.length ? entry.scale_points : null;
  let scale = (entry && entry.scale) || (entry && entry.toggled ? "toggle" : "linear");
  const notes = [];
  // A log or dB drag needs a positive minimum; a profile is loaded unvalidated,
  // so one that says log over a range reaching zero is drawn linear, and says so.
  if ((scale === "log") && !(hasRange && min > 0)) {
    notes.push("log scale over a range reaching zero: drawn linear");
    scale = "linear";
  }
  let control = entry && entry.control;
  if (!control) control = scale === "toggle" ? "toggle" : (scale === "enum" && choices ? "selector" : "knob");
  if (control === "selector" && !choices) control = "knob"; // nothing to select from
  if (!hasRange && info) notes.push("Sushi gave no range: the value is normalised, 0 to 1");
  return {
    name,
    label: (entry && entry.label) || name,
    description: (entry && entry.description) || null,
    unit: (entry && entry.unit) || null,
    scale, control, min, max, hasRange, choices,
    integer: !!(entry && entry.integer),
    group: entry ? (entry.group ?? null) : null,
    visibility: (entry && entry.visibility) || "primary",
    phone: entry && isNumber(entry.phone) ? entry.phone : null,
    readOnly: info ? info.automatable === false : false,
    missing: !info,
    profiled: !!entry,
    notes,
  };
}

export function realOf(ctrl, v) {
  return ctrl.hasRange ? ctrl.min + v * (ctrl.max - ctrl.min) : v;
}

export function normalisedOf(ctrl, real) {
  return ctrl.hasRange ? clamp01((real - ctrl.min) / (ctrl.max - ctrl.min)) : clamp01(real);
}

const logMapped = (ctrl) => (ctrl.scale === "log" || ctrl.scale === "db") && ctrl.hasRange && ctrl.min > 0;

// Where a knob points (0 to 1) for normalised value v, and back. Linear unless
// the profile's scale is log or dB over a positive range, where equal travel
// is an equal ratio: a wide gain's useful range is otherwise a sliver at the
// bottom (panel.py's LOG_SCALE_DOMAIN_THRESHOLD found the same).
export function positionOf(ctrl, v) {
  if (!logMapped(ctrl)) return clamp01(v);
  const real = Math.max(ctrl.min, realOf(ctrl, v));
  return clamp01(Math.log(real / ctrl.min) / Math.log(ctrl.max / ctrl.min));
}

export function valueAt(ctrl, position) {
  const p = clamp01(position);
  if (ctrl.scale === "toggle") return normalisedOf(ctrl, p >= 0.5 ? ctrl.max ?? 1 : ctrl.min ?? 0);
  let real = logMapped(ctrl) ? ctrl.min * Math.pow(ctrl.max / ctrl.min, p) : realOf(ctrl, p);
  if ((ctrl.integer || ctrl.scale === "enum") && ctrl.hasRange) real = Math.round(real);
  return normalisedOf(ctrl, real);
}

// Whether a two-state control (a toggle, by scale or by the profile's
// control) is on: past half its travel, as a click flips it. Not from the
// text, which is a number for a toggle control on a linear scale.
export function isOn(ctrl, v) {
  return isNumber(v) && positionOf(ctrl, v) >= 0.5;
}

// One key press's travel: one step of an integer control, else a hundredth.
export function stepOf(ctrl) {
  if ((ctrl.integer || ctrl.scale === "enum") && ctrl.hasRange && !logMapped(ctrl)) return 1 / (ctrl.max - ctrl.min);
  return 0.01;
}

const minus = (text) => text.replace(/^-/, "−");

export function formatValue(ctrl, v) {
  if (ctrl.missing || !isNumber(v)) return "—";
  if (!ctrl.hasRange) return v.toFixed(3);
  const real = realOf(ctrl, v);
  if (ctrl.scale === "toggle") return real >= (ctrl.min + ctrl.max) / 2 ? "on" : "off";
  if (ctrl.choices) {
    const point = ctrl.choices.find((c) => Math.abs(c.value - real) < 1e-6 * Math.max(1, Math.abs(real)));
    if (point) return String(point.label);
  }
  if (ctrl.scale === "db") {
    if (real <= 0) return "−∞ dB";
    return minus((20 * Math.log10(real)).toFixed(1)) + " dB";
  }
  const span = ctrl.max - ctrl.min;
  // As panel.py's readout: enough digits to see a change, not so many that it
  // jitters. dB spans ~48 and wants a decimal, Hz spans thousands and wants none.
  const decimals = ctrl.integer || ctrl.scale === "enum" ? 0 : span >= 100 ? 0 : span >= 10 ? 1 : 2;
  let text = real.toFixed(decimals);
  if (/^-0(\.0+)?$/.test(text)) text = text.slice(1); // never "-0.0"
  return minus(text) + (ctrl.unit ? " " + ctrl.unit : "");
}

// ---------------------------------------------------------------- what a card shows

// A processor's controls. With a profile: its groups, its primary controls in
// them, `advanced` for the rest, `hidden` left out as the profile decided, and
// two lists the profile did not foresee, each said rather than dropped: what
// the profile names that the running plugin lacks (`missing`), and what the
// plugin has that the profile does not name (in `advanced`, marked). Without
// one: every parameter Sushi reports, in its own order, read-only ones too.
export function unitPanel(params, profile) {
  params = params || {};
  if (!profile) {
    const controls = Object.entries(params).map(([n, info]) => makeControl(n, info, null));
    return { mode: "plain", groups: [{ id: null, label: null, controls }], advanced: [], missing: [] };
  }
  const groups = profile.groups.map((g) => ({ id: g.id, label: g.label, controls: [] }));
  const other = { id: null, label: "Other", controls: [] };
  const byId = new Map(groups.map((g) => [g.id, g]));
  const advanced = [], missing = [], listed = new Set();
  for (const entry of profile.parameters) {
    listed.add(entry.name);
    if (entry.visibility === "hidden") continue;
    const ctrl = makeControl(entry.name, params[entry.name], entry);
    if (ctrl.missing) missing.push(ctrl);
    else if (entry.visibility === "advanced") advanced.push(ctrl);
    else (byId.get(entry.group) || other).controls.push(ctrl);
  }
  for (const [n, info] of Object.entries(params)) {
    if (listed.has(n)) continue;
    const ctrl = makeControl(n, info, null);
    ctrl.notes.push("not in the profile");
    advanced.push(ctrl);
  }
  if (other.controls.length) groups.push(other);
  return { mode: "profile", groups: groups.filter((g) => g.controls.length), advanced, missing };
}

export function primaryControls(panel) {
  return panel.groups.flatMap((g) => g.controls);
}

// The rack: one entry per track in Sushi's order, each with its chain's
// plugins as units and its send and return apart, since those are the
// compiler's, not the player's (they cannot be moved, removed or bypassed
// from here).
export function rackModel(live, plugins, profiles) {
  const info = (live && live.parameter_info) || {};
  const processors = (live && live.processors) || {};
  const tracks = [];
  for (const [track, chain] of Object.entries((live && live.tracks) || {})) {
    const units = [];
    let send = null, ret = null;
    for (const name of chain) {
      const plugin = plugins ? plugins[name] || null : null;
      const role = roleOf(track, name, plugin);
      const uri = plugin ? plugin.uri || null : null;
      const profile = uri && profiles ? profiles[uri] || null : null;
      const unit = {
        name, track, role, uri,
        profile: profile ? { file: profile.file, name: profile.name } : null,
        bypassed: processors[name] ? processors[name].bypassed ?? null : null,
        panel: role === "send"
          ? { mode: "send", groups: [{ id: null, label: null, controls: [makeControl("gain", (info[name] || {}).gain, SEND_GAIN)] }], advanced: [], missing: [] }
          : unitPanel(info[name], profile),
      };
      if (role === "send") send = unit;
      else if (role === "return") ret = unit;
      else units.push(unit);
    }
    tracks.push({ name: track, isMaster: track === MASTER, units, send, ret, chain: [...chain] });
  }
  return { tracks };
}

// Whether a unit may be asked to move a step. Among the plugins only: the
// send and return are not offered (the agent refuses them anyway), and the
// ends of the run of plugins stop there. Whatever this offers, the agent
// decides; a refusal comes back with its reason.
export function canMove(track, index, direction) {
  return direction === "up" ? index > 0 : index < track.units.length - 1;
}

export function valueOf(live, processor, parameter) {
  const p = live && live.parameter_info && live.parameter_info[processor];
  return p && p[parameter] && isNumber(p[parameter].value) ? p[parameter].value : null;
}

// A card's one line on the phone: the profile's phone priorities first (the
// `phone` judgement, 1 most important), else the first settable parameters.
export function summaryOf(unit, live, count = 3) {
  let controls = [...primaryControls(unit.panel), ...unit.panel.advanced].filter((c) => !c.missing && !c.readOnly);
  if (unit.panel.mode === "profile") {
    const ranked = controls.filter((c) => c.phone !== null && c.visibility === "primary");
    if (ranked.length) controls = ranked.sort((a, b) => a.phone - b.phone);
  }
  return controls.slice(0, count)
    .map((c) => `${c.label} ${formatValue(c, valueOf(live, unit.name, c.name))}`).join(" · ");
}

export function channelLevels(model, live) {
  return model.tracks.filter((t) => t.send).map((t) => {
    const ctrl = t.send.panel.groups[0].controls[0];
    return { channel: t.name, text: formatValue(ctrl, valueOf(live, t.send.name, "gain")) };
  });
}

// ---------------------------------------------------------------- unsaved

const sameChain = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => x === b[i]);
// Which track a logged move was on. The log does not say, so: a later
// remove of the processor says where it was; else where it is now; else the
// track that still holds another processor of the chain the move started
// from. Null only for a chain left with nothing of what it had.
function moveTrack(log, i, processor, previous, live) {
  for (let j = i + 1; j < log.length; j++) {
    const later = log[j];
    if (later.op === "remove" && (later.args || {}).processor === processor && later.previous && later.previous.track) {
      return later.previous.track;
    }
  }
  const now = trackOf(live, processor);
  if (now) return now;
  const others = Array.isArray(previous) ? previous.filter((p) => p !== processor) : [];
  for (const [t, chain] of Object.entries((live && live.tracks) || {})) {
    if (others.some((p) => chain.includes(p))) return t;
  }
  return null;
}

const trackOf = (live, processor) => {
  for (const [t, chain] of Object.entries((live && live.tracks) || {})) if (chain.includes(processor)) return t;
  return null;
};

// What has changed since the rig came up, from the agent's log, netted: a
// value moved and moved back is not a change. Each key's baseline is what the
// oldest change to it replaced; it is unsaved where the rig now differs.
//
// The baseline is only what the agent saw. A change from the OSC panel, a
// MIDI mapping inside Sushi, or a `sushi-rig save` since, is not in the log,
// and the log is lost when the agent restarts; `partial` says when the log
// was trimmed past the rig's start, so the list may be short. Entries before
// the last rig switch, or older than the rig's start (a restart reloads its
// file), are another rig's.
//
// The amp's changes (op "amp" and "amp_model") are compared with `amp`, GET
// /amp's answer: what the agent last sent Carla, which is all there is to
// compare with (amp_live.py). Without one they are left out, not guessed.
export function unsavedChanges(logAnswer, live, rig, amp = null) {
  const log = (logAnswer && logAnswer.log) || [];
  const first = (logAnswer && logAnswer.first) || 0;
  let start = 0;
  for (let i = log.length - 1; i >= 0; i--) if (log[i].op === "rig") { start = i + 1; break; }
  // The log's `at` is to the second, the rig's start finer: compare seconds.
  const since = rig && rig.started_at ? Math.floor(Date.parse(rig.started_at) / 1000) * 1000 : NaN;
  const keys = new Map();
  const note = (key, kind, fields, previous, entry) => {
    if (!keys.has(key)) keys.set(key, { kind, ...fields, from: previous });
    keys.get(key).source = entry.source || null;
  };
  for (let i = start; i < log.length; i++) {
    const e = log[i];
    const at = Date.parse(e.at);
    if (!Number.isNaN(since) && !Number.isNaN(at) && at < since) continue;
    const op = e.op === "undo" ? e.args.of : e.op;
    const a = e.args || {};
    if (op === "parameter") note(`p\u0000${a.processor}\u0000${a.parameter}`, "parameter", { processor: a.processor, parameter: a.parameter }, e.previous, e);
    else if (op === "bypass") note(`b\u0000${a.processor}`, "bypass", { processor: a.processor }, e.previous, e);
    else if (op === "move") {
      // The move's own `previous` is the chain before it; only its track's
      // name has to be found, and the processor may be gone since.
      const t = moveTrack(log, i, a.processor, e.previous, live);
      if (t) note(`c\u0000${t}`, "chain", { track: t }, e.previous, e);
    } else if (op === "add") note(`c\u0000${a.track}`, "chain", { track: a.track }, e.previous, e);
    else if (op === "remove" && e.previous && e.previous.track) note(`c\u0000${e.previous.track}`, "chain", { track: e.previous.track }, e.previous.chain, e);
    else if (op === "amp") note(`a\u0000${a.name}`, "amp", { name: a.name }, e.previous, e);
    else if (op === "amp_model") note("m", "amp_model", {}, e.previous, e);
  }
  const items = [];
  for (const item of keys.values()) {
    if (item.kind === "parameter") {
      const now = valueOf(live, item.processor, item.parameter);
      if (now === null) continue; // gone from the rig: its track's chain says so
      if (!isNumber(item.from) || Math.abs(now - item.from) > TOLERANCE) items.push({ ...item, to: now });
    } else if (item.kind === "bypass") {
      const p = live && live.processors && live.processors[item.processor];
      if (!p) continue;
      const now = p.bypassed ?? null;
      if (now !== item.from) items.push({ ...item, to: now });
    } else if (item.kind === "amp") {
      const now = ampValueOf(amp, item.name);
      if (now === null) continue; // no amp read: not said either way
      if (!isNumber(item.from) || Math.abs(now - item.from) > TOLERANCE) items.push({ ...item, to: now });
    } else if (item.kind === "amp_model") {
      const now = amp && amp.model ? amp.model.stored : null;
      if (now !== null && now !== item.from) items.push({ ...item, to: now });
    } else {
      const now = live && live.tracks ? live.tracks[item.track] : undefined;
      if (now && !sameChain(now, item.from)) items.push({ ...item, to: [...now] });
    }
  }
  // Trimmed (`first` > 0) with no rig switch kept and the oldest entry kept
  // from this rig's life: what came before it, a baseline among it, is gone.
  const oldest = log.length ? Date.parse(log[0].at) : NaN;
  const partial = first > 0 && start === 0 && log.length > 0
    && (Number.isNaN(since) || Number.isNaN(oldest) || oldest >= since);
  return { items, partial };
}

// The entry an undo would take back, as the agent picks it (agent.pick_undo):
// the newest that is neither an undo nor a skip and has not been undone or
// skipped; a rig switch is a barrier. `{index, entry}`, `{barrier}` or null.
export function nextUndo(logAnswer) {
  const log = (logAnswer && logAnswer.log) || [];
  const first = (logAnswer && logAnswer.first) || 0;
  const handled = new Set();
  for (let i = log.length - 1; i >= 0; i--) {
    const e = log[i];
    if (e.op === "rig") return { barrier: e };
    if (e.op === "undo") handled.add(e.undoes);
    else if (e.op === "stale") handled.add(e.stale);
    else if (!handled.has(first + i)) return { index: first + i, entry: e };
  }
  return null;
}

export function describeEntry(entry) {
  const a = entry.args || {};
  switch (entry.op) {
    case "parameter": return `${a.processor} · ${a.parameter}`;
    case "bypass": return `${a.bypassed ? "bypass" : "turn on"} ${a.processor}`;
    case "move": return `move ${a.processor} ${a.direction}`;
    case "add": return `add ${a.name}`;
    case "remove": return `remove ${a.processor}`;
    case "amp": return `amp · ${a.name}`;
    case "amp_model": return `amp model ${modelName(a.model)}`;
    default: return entry.op;
  }
}

// ---------------------------------------------------------------- the rig's state

export function duration(seconds) {
  if (!isNumber(seconds) || seconds < 0) return null;
  const s = Math.floor(seconds);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

// Whether to ask /live at all (#111). Only for a rig the agent says is up and
// not changing: with none up, /live can only answer 503, and asking once a
// second filled the console with errors (110 in one session on the rig
// laptop) that buried any real one. A rig going up or down is 409 busy, the
// same noise. Asked whenever the rig is up, so a /live failure then is the
// error it is, and is shown as one (`rigStatus`).
export function readsLive(health) {
  if (!health || !health.ok) return false;
  const rig = health.body.rig || {};
  const agent = health.body.agent || {};
  return !!rig.running && !agent.busy;
}

// The state line. `tone` is off, down, busy or up, and never "measured":
// green is for a rig whose sound was measured (principle 8, #49), and
// nothing measures it yet, so an up rig is shown as up and unmeasured, not as
// fine. `off` is a rig that is simply not running, a state the player chose;
// `down` is a failure: an agent not answering, or a rig up whose Sushi does
// not (#111: the two looked the same, and the first is no error).
// `health` and `live` are the page's last answers: {ok, status, body, reason};
// `live` is null when it was not asked (`readsLive`).
export function rigStatus(health, live) {
  if (!health || !health.ok) {
    return { tone: "down", title: "Agent not answering", detail: (health && health.reason) || "no answer yet", measured: false };
  }
  const rig = health.body.rig || {};
  const agent = health.body.agent || {};
  const name = rig.config_name || "rig";
  if (agent.busy === "amp_model") return { tone: "busy", title: name, detail: "The amp is restarting on another model: its sound is cut until it is back", measured: false };
  if (agent.busy) return { tone: "busy", title: name, detail: `${agent.busy} is running`, measured: false };
  if (!rig.running) return { tone: "off", title: "Rig down", detail: rig.message || rig.verdict || "not running", measured: false };
  if (live && !live.ok) return { tone: "down", title: name, detail: `Up, but Sushi did not answer: ${live.reason}`, measured: false };
  const up = duration(rig.uptime_seconds);
  return { tone: "up", title: name, detail: `Up${up ? " " + up : ""} · sound not measured`, measured: false };
}

// The agent's own record of what last went wrong, and a MIDI listener that
// failed: both belong in the same glance as everything else (rule 2).
export function agentProblems(health) {
  if (!health || !health.ok) return [];
  const agent = health.body.agent || {};
  const out = [];
  if (agent.last_error) {
    const e = agent.last_error;
    out.push({ title: `Last error${e.command ? " (" + [].concat(e.command).join(" ") + ")" : ""}`, reason: e.message, at: e.at || null });
  }
  if (agent.midi && agent.midi.state === "failed") out.push({ title: "Pedals", reason: agent.midi.problem || "the MIDI listener failed", at: null });
  if (agent.stuck_live_calls) out.push({ title: "Sushi", reason: `${agent.stuck_live_calls} call(s) to Sushi never returned`, at: null });
  return out;
}

// ---------------------------------------------------------------- load and xruns

// The xruns the page has seen happen, counted per node by name, from one
// GET /rig/load answer after another: `seen` is the last step's (null to
// begin), and the answer is {seen, added}, `added` what this sample added.
//
// pw-top's counts are cumulative since each node appeared and never reset,
// so what is new is each node's rise since its last sample. A node absent
// from a sample keeps its last count and adds nothing; back again, it is
// compared with that, not taken as new (one sum over every node did that,
// and showed a node's whole lifetime as new and rising: found by review). A
// count that falls is a node that appeared afresh, all of whose xruns
// happened since. A node's first sample is its baseline, adding nothing.
export function xrunsStep(seen, body) {
  const last = { ...((seen && seen.last) || {}) };
  let count = (seen && seen.count) || 0;
  let added = 0;
  for (const n of (body && body.nodes) || []) {
    if (!isNumber(n.xruns)) continue;
    const before = last[n.name];
    if (isNumber(before)) added += n.xruns >= before ? n.xruns - before : n.xruns;
    last[n.name] = n.xruns;
  }
  count += added;
  return { seen: { last, count }, added };
}

// The LOAD and XRUNS tiles from GET /rig/load's answer (`top.load_report`),
// as {k, v, s, title, tone}; tone is "", "warn", "over" or "failed". `up` is
// whether a rig is up; `xruns` is this answer's `xrunsStep`, or null.
//
// Load is the sum of the rig's nodes' B/Q, the fraction of each block they
// used, as `sushi-rig top` totals it; never 0 % unless pw-top measured 0.
// Xruns: how many since the page began watching, and "rising" when the last
// sample added some: what predicts trouble (top.py) is a count that climbs,
// not its size.
export function loadTiles(answer, up, xruns) {
  const none = (s, title, tone = "") => [
    { k: "LOAD", v: "—", s, title, tone },
    { k: "XRUNS", v: "—", s, title, tone },
  ];
  if (!up) return none("rig down", "Read from pw-top while a rig is up");
  if (!answer) return none("reading", "Asking the agent for a pw-top sample");
  if (!answer.ok) return none("unreadable", `pw-top could not be read: ${answer.reason}`, "failed");
  const b = answer.body;
  const budget = b.budget || {};
  const notes = [];
  if ((b.missing || []).length) notes.push(`pw-top does not list ${b.missing.join(", ")}`);
  if ((b.drivers || []).length > 1) notes.push(`split across ${b.drivers.length} clocks (${b.drivers.join(", ")}): not one graph cycle`);
  const unmeasured = (b.nodes || []).filter((n) => n.load === null).map((n) => n.name);
  if (unmeasured.length) notes.push(`no timing for ${unmeasured.join(", ")}`);
  let load;
  if (b.total_load === null || b.total_load === undefined) {
    load = { k: "LOAD", v: "—", s: "not measured", tone: "warn" };
  } else {
    const tone = b.total_load >= 1 ? "over" : b.total_load >= b.warn_at ? "warn" : "";
    const block = isNumber(budget.block_ms) ? `of ${budget.block_ms.toFixed(1)} ms` : "no block size";
    load = { k: "LOAD", v: `${Math.round(b.total_load * 100)}%`, s: tone === "over" ? "over budget" : block, tone };
  }
  if (notes.length && !load.tone) load.tone = "warn";
  const perNode = (b.nodes || []).map((n) => `${n.name} ${n.load === null ? "—" : Math.round(n.load * 100) + "%"}`).join(", ");
  load.title = [perNode || "no rig node in pw-top", budget.quantum ? `quantum ${budget.quantum} @ ${budget.rate} Hz` : null,
    budget.driver ? `clocked by ${budget.driver}` : null, ...notes].filter(Boolean).join(" · ");
  if (!(b.nodes || []).length) load.s = "no rig nodes";
  const total = (b.nodes || []).reduce((sum, n) => sum + (isNumber(n.xruns) ? n.xruns : 0), 0);
  const rising = !!(xruns && xruns.added > 0);
  const xrun = {
    k: "XRUNS", v: String(xruns ? xruns.seen.count : 0), s: rising ? "rising" : "since opened",
    tone: rising ? "warn" : "",
    title: `${total} in all since each node appeared (pw-top never resets them): ` +
      ((b.nodes || []).map((n) => `${n.name} ${n.xruns}`).join(", ") || "no rig nodes"),
  };
  return [load, xrun];
}

// ---------------------------------------------------------------- start, stop, switch

const VERBS = { start: "Starting", stop: "Stopping", switch: "Switching to" };

// The request for a lifecycle gesture. A start is a rig of channels
// (config/rigs/, `rig: true`) and headless: the page replaces the OSC panel,
// and a rig run with its panel cannot be switched live (#93).
export function lifecycleCall(op, name) {
  if (op === "start") return { path: "/rig/up", json: { config_name: name, rig: true, headless: true } };
  if (op === "stop") return { path: "/rig/down", json: {} };
  if (op === "switch") return { path: "/rig/switch", json: { config_name: name } };
  throw new Error(`unknown lifecycle gesture ${op}`);
}

// The rig picker: the rigs of config/rigs/ (GET /rigs's answer), which one is
// up (from /health) and which is chosen, and what can be done. `pending` is this page's
// command in flight ({op, name}) or null; the agent's own `busy` covers one
// another client started. Either way nothing is offered while one runs (the
// agent would answer 409 busy), and what is running is said.
export function rigPicker(health, rigsAnswer, chosen, pending) {
  const agentUp = !!(health && health.ok);
  const rig = agentUp ? health.body.rig || {} : {};
  const busy = agentUp ? (health.body.agent || {}).busy || null : null;
  const running = rig.running ? rig.config_name || null : null;
  const names = rigsAnswer && rigsAnswer.ok ? rigsAnswer.body.rigs : [];
  const pick = chosen && names.includes(chosen) ? chosen : null;
  const view = {
    rows: names.map((name) => ({ name, up: name === running, chosen: name === pick })),
    problem: rigsAnswer && !rigsAnswer.ok ? `config/rigs/ could not be listed: ${rigsAnswer.reason}` : null,
    busy: pending ? `${VERBS[pending.op]} ${pending.name || "the rig"}…` : busy ? `${busy} is running` : null,
    actions: [],
  };
  if (!agentUp) { view.problem = view.problem || "The agent is not answering, so no rig can be started or stopped."; return view; }
  const idle = !pending && !busy;
  if (!rig.running) {
    view.actions.push({ op: "start", name: pick, label: pick ? `Start ${pick}` : "Choose a rig to start", disabled: !idle || !pick, primary: true });
  } else {
    if (pick && pick !== running) view.actions.push({ op: "switch", name: pick, label: `Switch to ${pick}`, disabled: !idle, primary: true });
    view.actions.push({ op: "stop", name: running, label: `Stop ${running || "the rig"}`, disabled: !idle, primary: false });
  }
  return view;
}

// What a lifecycle answer says, for the page to show: {ok, title, text} for
// a notice, or {ok: false, title, reason} for a failure, the agent's reason
// as it gave it. A switch says how it was made and, for a restart, why.
export function lifecycleOutcome(op, name, answer) {
  const body = answer.body || {};
  const why = (body.why || []).map((r) => `${r.code}: ${r.detail}`).join("; ");
  const secs = isNumber(body.seconds) ? ` in ${body.seconds < 10 ? body.seconds.toFixed(1) : Math.round(body.seconds)} s` : "";
  if (op === "switch") {
    if (answer.ok) {
      return body.how === "live"
        ? { ok: true, title: `Switched to ${name}`, text: `Switched to ${name} live${secs}: Sushi was not restarted.` }
        : { ok: true, title: `Switched to ${name}`, text: `Switched to ${name} by restart${secs}, because ${why || "the agent gave no reason"}.` };
    }
    return { ok: false, title: `Not switched to ${name}${body.how === "restart" ? " (by restart)" : ""}`,
      reason: answer.reason + (body.how === "restart" && why ? `. It restarted because ${why}` : "") };
  }
  const warnings = (body.warnings || []).length ? ` Warnings: ${body.warnings.join("; ")}` : "";
  if (op === "start") {
    return answer.ok ? { ok: true, title: `Started ${name}`, text: `Started ${name}, headless.${warnings}` }
      : { ok: false, title: `Not started: ${name}`, reason: answer.reason };
  }
  return answer.ok ? { ok: true, title: "Stopped", text: `Stopped ${name || "the rig"}.` }
    : { ok: false, title: `Not stopped: ${name || "the rig"}`, reason: answer.reason };
}

// ---------------------------------------------------------------- the lock
// Sound changes (a knob, a bypass) are allowed locked; structure (add, remove,
// move) is refused, with the reason (the UX doc's rule 4). This is the page's
// lock: the agent has no performance lock yet (#29), so another client is
// not held by it.
const STRUCTURAL = { move: "reordering", add: "adding a plugin", remove: "removing a plugin" };

export function lockRefusal(locked, op) {
  if (locked && op === "amp_model") {
    return "Locked: changing the amp's model cuts the sound while the amp restarts, which the lock refuses. Unlock to change it.";
  }
  if (!locked || !(op in STRUCTURAL)) return null;
  return `Locked: ${STRUCTURAL[op]} changes the rig's structure, which the lock refuses. Unlock to edit it.`;
}

// ---------------------------------------------------------------- the amp
// GET /amp is what the agent last SENT Carla (amp_live.py): Carla cannot be
// read back, so nothing here says what the amp "is at", only what was sent,
// and a value never sent is said as not sent rather than as a default.

export const MODEL_WARNING = "Changing the model restarts the amp: the sound cuts out for a moment while it does. The rest of the rig keeps playing.";
const QUALITY_NOTE = "not on a fader: it trades CPU for accuracy (a lite or a full path), not tone";

export function modelName(stored) {
  if (typeof stored !== "string" || !stored) return "none";
  return stored.split("/").pop().replace(/\.nam$/, "");
}

export function ampValueOf(amp, name) {
  const p = amp && (amp.parameters || []).find((x) => x.name === name);
  return p && isNumber(p.value) ? p.value : null;
}

// A value in the parameter's own unit: a decimal for dB's -20 to +20, two
// for Quality's 0 to 1, as panel.py's readout chooses by span.
export function ampText(param, v) {
  if (!isNumber(v)) return "not sent";
  const span = isNumber(param.max) && isNumber(param.min) ? param.max - param.min : 1;
  let text = v.toFixed(span >= 10 ? 1 : 2);
  if (/^-0(\.0+)?$/.test(text)) text = text.slice(1);
  return minus(text) + (param.unit ? " " + param.unit : "");
}

// What the rack's amp card shows (`answer` is GET /amp's, `models` GET
// /amp/models's, both {ok, status, body, reason, code} or null). `state`:
// "hidden" (no rig, or one changing), "none" (a rig with no amp, said why),
// "failed" (the read failed, said why), "up". Faders for the panel
// parameters, Quality shown and not on one; and the model picker, with why
// it cannot be used when it cannot.
export function ampCard(answer, models, { locked = false, pending = null } = {}) {
  if (!answer) return { state: "hidden" };
  if (!answer.ok) {
    if (answer.code === "no_rig" || answer.code === "busy") return { state: "hidden" };
    if (answer.code === "no_amp") return { state: "none", why: answer.reason };
    return { state: "failed", why: answer.reason };
  }
  const b = answer.body;
  const panel = b.known === "panel";
  const down = !!(b.carla && b.carla.alive === false);
  const writable = !panel && !down;
  const params = b.parameters || [];
  const faders = params.filter((p) => p.panel).map((p) => ({
    name: p.name, label: p.name, min: p.min, max: p.max, unit: p.unit,
    step: isNumber(p.max) && isNumber(p.min) && p.max - p.min >= 10 ? 0.1 : 0.01, disabled: !writable,
  }));
  const shown = params.filter((p) => !p.panel).map((p) => ({
    name: p.name, text: ampText(p, p.value), note: p.name === "Quality" ? QUALITY_NOTE : null,
  }));
  const model = b.model ? {
    name: b.model.name, stored: b.model.stored, description: b.model.description || null,
    problem: b.model.problem || null, warning: b.model.warning || null,
  } : null;
  const listed = models && models.ok ? models.body.models : [];
  const options = listed.map((m) => ({
    model: m.model, playing: !!m.playing, usable: !!m.usable && !m.playing,
    label: m.name + (m.playing ? " (playing)" : !m.usable ? " (cannot be played here)" : ""),
    why: m.why || null,
  }));
  let why = null;
  if (panel) why = "The panel is running and owns the amp: change it there, or run the rig headless.";
  else if (down) why = "Carla is not running, so the amp cannot be changed: restart the rig.";
  else if (!b.held) why = "This agent did not start the running rig, so it cannot restart the amp alone: start the rig from here to change its model.";
  else if (locked) why = lockRefusal(true, "amp_model");
  else if (pending) why = `Loading ${modelName(pending)}…`;
  else if (models && !models.ok) why = `amp/models could not be listed: ${models.reason}`;
  return {
    state: "up", known: b.known, note: b.known_note, panel, down, model, faders, shown,
    override: b.override || null,
    picker: { options, canChange: !why, why, warning: MODEL_WARNING },
  };
}

// The stage screen's amp: the model's name and the output level, nothing
// else, and only while the lock is off (app.js hides it while locked, as it
// hides the rig picker). Null when there is no amp to show.
export function stageAmp(answer) {
  const card = ampCard(answer, null);
  if (card.state !== "up") return null;
  const output = card.faders.find((f) => f.name === "Output Lvl") || null;
  return { name: card.model ? card.model.name : "no model", output, known: card.known };
}

export function ampParameterCall(name, value) {
  return { path: "/amp/parameter", json: { name, value } };
}

export function ampModelCall(model) {
  return { path: "/amp/model", json: { model } };
}

// What a model change answered, for the page: {ok, text} or {ok: false,
// title, reason}, the agent's words, and whether the previous model is back.
export function ampModelOutcome(model, answer) {
  const body = answer.body || {};
  if (answer.ok) {
    if (body.changed === false) return { ok: true, text: body.message || `${modelName(model)} is already playing.` };
    return { ok: true, text: body.message || `The amp now plays ${modelName(model)}.` };
  }
  const back = body.restored === true ? " The previous model was brought back."
    : body.restored === false ? " The amp did not come back: restart the rig." : "";
  return { ok: false, title: `Amp model not changed: ${modelName(model)}`, reason: answer.reason + (answer.reason.includes("brought back") ? "" : back) };
}

// ---------------------------------------------------------------- sending

// Sends a control's changes as they happen, one request in flight per control:
// a drag makes changes faster than a round trip, and the newest wins, so
// while one is in flight only the latest is kept to send next. The value
// shown is the one being sent until a poll that began after the agent
// confirmed it, so an older poll cannot pull the knob back mid-gesture.
// A refused change is dropped, and its reason given to `onFailure`: the
// knob then shows the rig's value with the reason beside it, not quietly.
export class Sender {
  constructor(post, { pollSeq = () => 0, onFailure = () => {}, onSent = () => {} } = {}) {
    this.post = post;
    this.pollSeq = pollSeq;
    this.onFailure = onFailure;
    this.onSent = onSent;
    this.state = new Map();
  }

  change(key, value, body) {
    let s = this.state.get(key);
    if (!s) { s = { want: value, body, inFlight: false, confirmedAt: null }; this.state.set(key, s); }
    s.want = value; s.body = body; s.confirmedAt = null;
    if (!s.inFlight) this._send(key, s);
  }

  async _send(key, s) {
    s.inFlight = true;
    const value = s.want;
    const result = await this.post(s.body(value));
    s.inFlight = false;
    if (this.state.get(key) !== s) return;
    if (!result.ok) {
      this.state.delete(key);
      this.onFailure(key, result);
      return;
    }
    this.onSent(key, result);
    if (s.want !== value) { this._send(key, s); return; }
    s.confirmedAt = this.pollSeq();
  }

  displayed(key, live, seq) {
    const s = this.state.get(key);
    if (!s) return live;
    if (s.confirmedAt !== null && seq > s.confirmedAt) { this.state.delete(key); return live; }
    return s.want;
  }

  busy(key) {
    return this.state.has(key);
  }
}
