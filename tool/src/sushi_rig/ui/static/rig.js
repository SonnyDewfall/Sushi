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
export function unsavedChanges(logAnswer, live, rig) {
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
      const t = trackOf(live, a.processor);
      if (t) note(`c\u0000${t}`, "chain", { track: t }, e.previous, e);
    } else if (op === "add") note(`c\u0000${a.track}`, "chain", { track: a.track }, e.previous, e);
    else if (op === "remove" && e.previous && e.previous.track) note(`c\u0000${e.previous.track}`, "chain", { track: e.previous.track }, e.previous.chain, e);
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

// The state line. `tone` is down, busy or up, and never "measured": green is
// for a rig whose sound was measured (principle 8, #49), and nothing measures
// it yet, so an up rig is shown as up and unmeasured, not as fine.
// `health` and `live` are the page's last answers: {ok, status, body, reason}.
export function rigStatus(health, live) {
  if (!health || !health.ok) {
    return { tone: "down", title: "Agent not answering", detail: (health && health.reason) || "no answer yet", measured: false };
  }
  const rig = health.body.rig || {};
  const agent = health.body.agent || {};
  const name = rig.config_name || "rig";
  if (agent.busy) return { tone: "busy", title: name, detail: `${agent.busy} is running`, measured: false };
  if (!rig.running) return { tone: "down", title: "Rig down", detail: rig.message || rig.verdict || "not running", measured: false };
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

// ---------------------------------------------------------------- the lock
// Sound changes (a knob, a bypass) are allowed locked; structure (add, remove,
// move) is refused, with the reason (the UX doc's rule 4). This is the page's
// lock: the agent has no performance lock yet (#29), so another client is
// not held by it.
const STRUCTURAL = { move: "reordering", add: "adding a plugin", remove: "removing a plugin" };

export function lockRefusal(locked, op) {
  if (!locked || !(op in STRUCTURAL)) return null;
  return `Locked: ${STRUCTURAL[op]} changes the rig's structure, which the lock refuses. Unlock to edit it.`;
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
