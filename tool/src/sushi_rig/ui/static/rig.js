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
// The compiler's copy to stereo after a mono plugin (stereo.py: COPY_SUFFIX),
// a name nothing else may end in. Not shown: it goes with its plugin.
export const STEREO_SUFFIX = "_to_stereo";
// Its uid in Sushi's session (stereo.py: COPY_UID): Sushi's own mono_summing.
export const COPY_UID = "sushi.testing.mono_summing";

// A parameter read back is "the same" within this, as the agent's undo
// decides it (agent.PARAMETER_TOLERANCE): Sushi holds a float32.
export const TOLERANCE = 1e-4;

// A send's gain is dB from -120 to +24 in Sushi 1.3.0's send_plugin.cpp
// (rigfile.LEVEL_RANGE), so the real value of its normalised gain is dB
// already; this entry gives it the unit no profile can, sends having none.
const SEND_GAIN = { name: "gain", label: "Level", unit: "dB", scale: "linear", visibility: "primary" };

// A processor's role: "stereo" for the compiler's copy to stereo, "send" or
// "return" for the compiler's monitor path, null for a player's plugin. A
// copy is recognised by whatever says so first -- its uid, the reserved
// suffix no other name may carry (stereo.reserved_problem), or a role of
// "stereo" -- before any role /live/plugins gives: on the rig (2026-11-06)
// copies were drawn as ordinary plugins, and a role of null must never be
// able to say a copy is a plugin.
export function isCopy(name, plugin) {
  return name.endsWith(STEREO_SUFFIX) || !!(plugin && (plugin.uid === COPY_UID || plugin.role === "stereo"));
}

export function roleOf(track, name, plugin) {
  if (isCopy(name, plugin)) return "stereo";
  if (plugin && "role" in plugin) return plugin.role || null;
  if (name === track + SEND_SUFFIX) return "send";
  if (track === MASTER && name === RETURN) return "return";
  return null;
}

// "mono" for a plugin the compiler put a copy to stereo after, and, where it
// follows a stereo plugin on the same chain with nothing mono between, that
// plugin's name: Sushi gives a mono plugin channel 0 alone (#119), so the
// stereo width made before it is collapsed there. `units` are a track's
// plugins in order, each with `name`, `mono` and `width` (from /live/plugins,
// from plugin-ports.txt). Null for a plugin that is not mono.
export function monoNote(units, index) {
  const unit = units[index];
  if (!unit.mono) return null;
  let after = null;
  for (let i = index - 1; i >= 0; i--) {
    if (units[i].mono) break;
    if (units[i].width === "stereo") { after = units[i].name; break; }
  }
  return after
    ? { badge: "mono", warn: true, title: `after ${after}: this collapses its stereo width` }
    : { badge: "mono", warn: false, title: "mono: processes the left side and its output is copied to both" };
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

// ---------------------------------------------------------------- setting a value (2026-11-06)
// How a knob, a fader or a value's text field turns a gesture into a value.
// On the rig a knob went from end to end in 200 px of travel in either
// direction, so a value could hardly be set, and only by dragging; these
// make a full sweep a long, deliberate drag, give the wheel and the keys a
// step each, take a value typed as text, and hold a whole number briefly so
// one can be landed on. All in the control's own terms: a position is the
// knob's travel (0 to 1, `positionOf`), a value is Sushi's normalised one,
// and a display value is what the text shows (dB for a `db` scale).

// Pixels of vertical drag for a knob's whole travel: long and deliberate, a
// thumb's sweep and more. Shift, or a second finger, divides by FINE_DIVISOR.
export const DRAG_PIXELS = 320;
export const FINE_DIVISOR = 10;
// A wheel notch, in pixels as Chromium reports one (`deltaMode` 0); a
// trackpad's small deltas add up to one. A notch of lines (`deltaMode` 1,
// Firefox's three) or a page (2) is converted first.
export const NOTCH_PIXELS = 100;
// The sticky band of a whole number, in display units: within SNAP_BAND of
// one, a drag or a scroll lands on it, and stays on it until it is HOLD_BAND
// away, so a whole number holds for a little further travel than it took.
export const SNAP_BAND = 0.15;
export const HOLD_BAND = 0.35;

const clampTo = (x, lo, hi) => Math.min(hi, Math.max(lo, x));

// What the text shows, as a number: the real value, or dB for a `db` scale
// (a linear gain shown as dB), or the normalised value for a parameter Sushi
// gave no range for. Null where there is none; -Infinity for a gain of 0.
export function displayOf(ctrl, v) {
  if (!isNumber(v)) return null;
  if (!ctrl.hasRange) return v;
  const real = realOf(ctrl, v);
  if (ctrl.scale === "db") return real <= 0 ? -Infinity : 20 * Math.log10(real);
  return real;
}

// And back: a display value as a normalised value, clamped to the range.
export function fromDisplay(ctrl, d) {
  if (!ctrl.hasRange) return clamp01(d);
  let real = ctrl.scale === "db" ? (d === -Infinity ? 0 : Math.pow(10, d / 20)) : d;
  real = clampTo(real, ctrl.min, ctrl.max);
  if ((ctrl.integer || ctrl.scale === "enum") && ctrl.hasRange) real = clampTo(Math.round(real), ctrl.min, ctrl.max);
  return normalisedOf(ctrl, real);
}

// The grid a drag or scroll is held to, in display units: whole numbers for
// dB, percent and plain numbers; 0.01 for a normalised 0-to-1 parameter
// with no unit (snapping one to 0 or 1 would be useless: it is quantised to
// what its text shows instead). Null for what is whole already (an integer,
// an enum, a toggle) or has choices.
export function snapGrid(ctrl) {
  if (ctrl.scale === "toggle" || ctrl.scale === "enum" || ctrl.integer || ctrl.choices) return null;
  const span = ctrl.hasRange ? ctrl.max - ctrl.min : 1;
  if (!ctrl.unit && ctrl.scale !== "db" && span <= 1) return 0.01;
  return 1;
}

// A value moved by a drag or a scroll, held to the grid: `{v, held}`, where
// `held` is the whole number it is held on (or null), carried from one step
// of the gesture to the next. Pure. Typed values never come through here
// (what is typed is exact), nor a fine drag (a tenth of the speed is for the
// values between whole numbers, which a snap would make unreachable).
export function sticky(ctrl, v, held = null) {
  const grid = snapGrid(ctrl);
  const d = displayOf(ctrl, v);
  if (grid === null || d === null || !Number.isFinite(d)) return { v, held: null };
  if (grid < 1) {
    const q = Math.round(d / grid) * grid;
    return { v: fromDisplay(ctrl, q), held: null };
  }
  if (held !== null && Math.abs(d - held) <= HOLD_BAND) return { v: fromDisplay(ctrl, held), held };
  const near = Math.round(d);
  if (Math.abs(d - near) <= SNAP_BAND) return { v: fromDisplay(ctrl, near), held: near };
  return { v, held: null };
}

// Where a vertical drag of `dy` pixels (down is positive, as the pointer
// reports it) takes a knob at position `p`: up raises it, a full sweep is
// DRAG_PIXELS, a tenth of that speed when `fine`. Pure.
export function dragPosition(p, dy, fine = false) {
  return clamp01(p - (dy / DRAG_PIXELS) / (fine ? FINE_DIVISOR : 1));
}

// One step's travel as a position, where steps are positions: one step of
// an integer or an enum, else a hundredth of the travel (a log or dB knob,
// whose equal travel is an equal ratio).
export function stepOf(ctrl) {
  if ((ctrl.integer || ctrl.scale === "enum") && ctrl.hasRange && !logMapped(ctrl)) return 1 / (ctrl.max - ctrl.min);
  return 0.01;
}

// A wheel notch or key press of a linear control, in display units: about
// 1 % of its range, rounded down to 1, 2 or 5 times a power of ten, so steps
// land on the values a player would type (a send's 144 dB steps 1 dB, the
// overdrive's 24 dB Level 0.2 dB, a 0-to-1 Drive 0.01, a 900 Hz Tone 5 Hz).
// The profiles give no step of their own. Null where steps are positions:
// a log or dB scale, an integer, an enum, a toggle, no range.
export function niceStep(ctrl) {
  if (!ctrl.hasRange || logMapped(ctrl) || ctrl.scale === "db" || ctrl.scale === "toggle"
    || ctrl.scale === "enum" || ctrl.integer || ctrl.choices) return null;
  const raw = (ctrl.max - ctrl.min) / 100;
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  const nice = [5, 2, 1].map((m) => m * power).find((m) => m <= raw * (1 + 1e-9));
  return nice || power;
}

const tidy = (x, size) => Number((Math.round(x / size) * size).toFixed(10));

// `x` put on the grid of `size` when it is within a thousandth of a step of
// a line: Sushi holds a value as a float32, so 7 reads back as 6.9999998,
// and a step taken from that by floor or ceil landed on 7 again (review,
// 2026-11-07: a slow wheel or the arrow keys stuck there for good). A
// thousandth of a step is far above float32's error on any range here
// (about 1e-5 of a unit on a send's 144 dB) and far below a step.
export function onGrid(x, size) {
  const k = Math.round(x / size);
  return Math.abs(x / size - k) <= 1e-3 ? tidy(k * size, size) : x;
}

// The value `steps` steps from `v` (negative is down), a tenth of a step
// each when `fine` (never less than one whole step of an integer). A linear
// control steps from the step grid (`niceStep`): a value typed off it moves
// to the next grid line in that direction, then by whole steps. The keys'
// stepping: exact, never snapped.
export function stepped(ctrl, v, steps, fine = false) {
  const start = isNumber(v) ? v : 0;
  const step = niceStep(ctrl);
  if (step === null) {
    const whole = (ctrl.integer || ctrl.scale === "enum") && ctrl.hasRange;
    const size = stepOf(ctrl) / (fine && !whole ? FINE_DIVISOR : 1);
    return valueAt(ctrl, positionOf(ctrl, start) + steps * size);
  }
  const size = step / (fine ? FINE_DIVISOR : 1);
  // In whole steps, never by dividing a grid value back (7.1 / 0.1 is
  // 70.999..., whose floor is a step short): on the grid within a
  // thousandth of a step (`onGrid`'s tolerance), else the grid line behind.
  const q = displayOf(ctrl, start) / size;
  const k = Math.round(q);
  const index = Math.abs(q - k) <= 1e-3 ? k : steps > 0 ? Math.floor(q) : Math.ceil(q);
  return normalisedOf(ctrl, clampTo(tidy((index + steps) * size, size), ctrl.min, ctrl.max));
}

// A scroll's notches: `stepped`, then, if that step crossed or reached a
// whole number in the units shown, the value lands on that whole number
// (the first one in the scroll's direction). So a notch always moves the
// value the way it was turned, and a scroll still stops on whole numbers
// it passes. Stateless: a notch starts from the value as it is, so no pause
// and no float32 read-back can make a notch undo itself (review,
// 2026-11-07: a slow scroll at 4.0 in 0.05 steps snapped back to 4.0 every
// time; and both the step and the whole number it starts from are read
// through `onGrid`, so a float32 read-back of a whole number is that whole
// number). `fine` is for the values between, and never snaps.
export function scrolled(ctrl, v, notches, fine = false) {
  const start = isNumber(v) ? v : 0;
  const raw = stepped(ctrl, start, notches, fine);
  if (fine || snapGrid(ctrl) !== 1) return raw;
  const d0 = onGrid(displayOf(ctrl, start), 1), d1 = displayOf(ctrl, raw);
  if (!Number.isFinite(d0) || !Number.isFinite(d1) || d0 === d1) return raw;
  const up = d1 > d0;
  const whole = up ? Math.floor(d0) + 1 : Math.ceil(d0) - 1;
  const crossed = up ? whole <= d1 + 1e-9 : whole >= d1 - 1e-9;
  return crossed ? fromDisplay(ctrl, whole) : raw;
}

// Whether a wheel event over a control is the control's (and so turns it)
// or the page's (and so scrolls it). Only a settable control's dial or
// fader takes it, and only when meant: it has the focus, or the pointer has
// rested on it WHEEL_REST_MS since it arrived and since the page last
// scrolled. Without the rest, a scroll down a rack of knobs stopped dead at
// the first knob under the pointer and turned it (review, 2026-11-07). Pure.
export const WHEEL_REST_MS = 300;
export function takesWheel({ settable, focused, now, enteredAt, scrolledAt }) {
  if (!settable) return false;
  if (focused) return true;
  if (!isNumber(enteredAt)) return false;
  return now - Math.max(enteredAt, isNumber(scrolledAt) ? scrolledAt : -Infinity) >= WHEEL_REST_MS;
}

// Whole notches from a wheel event, with what is left over for the next:
// `{notches, rest}`, up the page (negative deltaY) positive. A trackpad's
// small deltas add up; a single coarse event of at least half a notch (a
// mouse whose notch is under NOTCH_PIXELS, as Chromium on Linux reports
// some) counts as one, so no notch is ever lost.
export function wheelNotches(rest, deltaY, deltaMode = 0) {
  const px = deltaMode === 1 ? deltaY * (NOTCH_PIXELS / 3) : deltaMode === 2 ? deltaY * NOTCH_PIXELS * 10 : deltaY;
  const total = (isNumber(rest) ? rest : 0) - px;
  const notches = Math.trunc(total / NOTCH_PIXELS);
  if (notches !== 0) return { notches, rest: total - notches * NOTCH_PIXELS };
  if (Math.abs(px) >= NOTCH_PIXELS / 2) return { notches: px < 0 ? 1 : -1, rest: 0 };
  return { notches: 0, rest: total };
}

// A value typed into a control's text field: `{ok: true, v, clamped}` with
// the normalised value, clamped to the range, or `{ok: false, why}`. Lenient:
// with or without the unit, a typographic minus, a comma for a point, "inf"
// and "∞"; a choice by its label; a toggle by on and off. Exact, never
// snapped; an integer parameter is rounded, being one.
export function parseTyped(ctrl, text) {
  const raw = String(text ?? "").trim();
  const bad = { ok: false, why: `“${raw}” is not a value${ctrl.unit ? ` in ${ctrl.unit}` : ""}` };
  if (!raw) return { ok: false, why: "Type a value." };
  if (ctrl.choices) {
    const point = ctrl.choices.find((c) => String(c.label).toLowerCase() === raw.toLowerCase());
    if (point) return { ok: true, v: normalisedOf(ctrl, point.value), clamped: false };
  }
  if (ctrl.scale === "toggle") {
    if (/^(on|1|true)$/i.test(raw)) return { ok: true, v: normalisedOf(ctrl, ctrl.max ?? 1), clamped: false };
    if (/^(off|0|false)$/i.test(raw)) return { ok: true, v: normalisedOf(ctrl, ctrl.min ?? 0), clamped: false };
    return bad;
  }
  let t = raw.replace(/[−–]/g, "-").replace(/,/g, ".").replace(/\s+/g, "");
  const units = [ctrl.unit, ctrl.scale === "db" ? "dB" : null, "db"].filter(Boolean);
  for (const u of units) {
    if (t.toLowerCase().endsWith(u.toLowerCase())) { t = t.slice(0, t.length - u.length); break; }
  }
  let d;
  if (/^-(inf|infinity|∞)$/i.test(t)) d = -Infinity;
  else if (/^\+?(inf|infinity|∞)$/i.test(t)) d = Infinity;
  else if (/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(t)) d = Number(t);
  else return bad;
  let v;
  if (!ctrl.hasRange) v = clamp01(d);
  else if (ctrl.scale === "db") v = fromDisplay(ctrl, d);
  else v = fromDisplay(ctrl, clampTo(d, ctrl.min, ctrl.max));
  const back = displayOf(ctrl, v);
  const clamped = Number.isFinite(d) ? Math.abs(back - d) > 1e-9 * Math.max(1, Math.abs(d)) : true;
  return { ok: true, v, clamped };
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
// from here). A mono plugin's copy to stereo (role "stereo") is the
// compiler's too and is not shown at all: the agent moves and removes it
// with its plugin, so a unit's neighbours here are the neighbours a move
// steps past. `chain` keeps Sushi's whole chain, copies included, because
// the agent's log records chains that way.
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
      if (role === "stereo") continue;
      const uri = plugin ? plugin.uri || null : null;
      const profile = uri && profiles ? profiles[uri] || null : null;
      const unit = {
        name, track, role, uri,
        // A copy to stereo after it, on the chain or by the session's word.
        mono: chain.includes(name + STEREO_SUFFIX),
        width: plugin ? plugin.width || null : null,
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
    units.forEach((u, i) => { u.monoNote = monoNote(units, i); });
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
// A remove's shape: the processor in `args`, and in `previous` the track and
// the chain it was taken from. An undo of an add is one too (the agent logs
// an undo shaped as what it applied), so nothing here asks which it was.
const removeShaped = (e) => {
  const p = e.previous;
  return !!(p && !Array.isArray(p) && typeof p.track === "string" && Array.isArray(p.chain));
};
// Which track a logged move was on. The log does not say, so: a later
// remove of the processor says where it was; else where it is now; else the
// track that still holds another processor of the chain the move started
// from. Null only for a chain left with nothing of what it had.
function moveTrack(log, i, processor, previous, live) {
  for (let j = i + 1; j < log.length; j++) {
    const later = log[j];
    if (removeShaped(later) && (later.args || {}).processor === processor) return later.previous.track;
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

// What has changed since the rig came up or was last saved, from the agent's
// log, netted: a value moved and moved back is not a change. Each key's
// baseline is what the oldest change to it since then replaced; it is
// unsaved where the rig now differs. A save (`op: "save"`, POST /rig/save)
// is a baseline as the rig's start is: what it captured is in the file, so
// only what came after it counts, an undo after it included (undo passes
// over a save, so it puts back what the file does not have).
//
// The baseline is only what the agent saw. A change from the OSC panel, a
// MIDI mapping inside Sushi, or a `sushi-rig save` since, is not in the log,
// and the log is lost when the agent restarts; `partial` says when the log
// was trimmed past the rig's start, so the list may be short. Entries before
// the last rig switch, or older than the rig's start (a restart reloads its
// file), are another rig's; entries before the last save are in the file.
//
// The amp's changes (op "amp" and "amp_model") are compared with `amp`, GET
// /amp's answer: what the agent last sent Carla, which is all there is to
// compare with (amp_live.py). Without one they are left out, not guessed.
export function unsavedChanges(logAnswer, live, rig, amp = null, plugins = null) {
  const log = (logAnswer && logAnswer.log) || [];
  const first = (logAnswer && logAnswer.first) || 0;
  let start = 0;
  for (let i = log.length - 1; i >= 0; i--) if (log[i].op === "rig" || log[i].op === "save") { start = i + 1; break; }
  // The log's `at` is to the second, the rig's start finer: compare seconds.
  const since = rig && rig.started_at ? Math.floor(Date.parse(rig.started_at) / 1000) * 1000 : NaN;
  const keys = new Map();
  // Every name the log says is a copy to stereo (an add's `copy`, a
  // remove's `previous.copy`): a copy known only by its uid has left
  // /live/plugins once removed, and must not be listed as removed then.
  const copies = new Set();
  for (const e of log) {
    if (e.args && typeof e.args.copy === "string") copies.add(e.args.copy);
    if (e.previous && !Array.isArray(e.previous) && typeof e.previous.copy === "string") copies.add(e.previous.copy);
  }
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
    } else if (op === "add" || op === "remove") {
      // By shape, not by op: an add's undo is a remove and a remove's an add,
      // each logged in the shape of what it applied.
      if (removeShaped(e)) note(`c\u0000${e.previous.track}`, "chain", { track: e.previous.track }, e.previous.chain, e);
      else if (Array.isArray(e.previous) && typeof a.track === "string") note(`c\u0000${a.track}`, "chain", { track: a.track }, e.previous, e);
    } else if (op === "amp") note(`a\u0000${a.name}`, "amp", { name: a.name }, e.previous, e);
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
      if (now) items.push(...chainChanges(item, now, plugins, copies));
    }
  }
  // Trimmed (`first` > 0) with no rig switch kept and the oldest entry kept
  // from this rig's life: what came before it, a baseline among it, is gone.
  const oldest = log.length ? Date.parse(log[0].at) : NaN;
  const partial = first > 0 && start === 0 && log.length > 0
    && (Number.isNaN(since) || Number.isNaN(oldest) || oldest >= since);
  return { items, partial };
}

// What differs between a track's chain at its baseline and now, as the
// player made it: each plugin there now that was not ("added X to <track>"),
// each that was there and is not ("removed X from <track>"), and, when the plugins in
// both are in another order, the chain itself. A copy to stereo comes and
// goes with its plugin and is not a change of its own; nor are the send and
// return, which never come or go. Netted like the rest: added and removed
// again is nothing.
function chainChanges(item, now, plugins = null, copies = new Set()) {
  // A baseline the log does not have (a move Sushi did not say the chain of)
  // says nothing of what came or went: one row, the chain from "unknown",
  // never every plugin on the track as added (review of #32).
  if (!Array.isArray(item.from)) return sameChain(now, item.from) ? [] : [{ ...item, to: [...now] }];
  const from = item.from;
  const was = new Set(from), is = new Set(now);
  // A copy by `isCopy`, as everywhere: its uid where /live/plugins said it.
  const own = (n) => !copies.has(n) && !isCopy(n, plugins ? plugins[n] : null);
  const base = { track: item.track, source: item.source };
  const out = [
    ...now.filter((n) => own(n) && !was.has(n)).map((n) => ({ kind: "added", processor: n, ...base })),
    ...from.filter((n) => own(n) && !is.has(n)).map((n) => ({ kind: "removed", processor: n, ...base })),
  ];
  const kept = (chain) => chain.filter((n) => own(n) && was.has(n) && is.has(n));
  if (!sameChain(kept(from), kept(now))) {
    out.push({ kind: "chain", track: item.track, from: item.from, to: [...now], source: item.source });
  }
  return out;
}

// The entry an undo would take back, as the agent picks it (agent.pick_undo):
// the newest that is neither an undo nor a skip and has not been undone or
// skipped; a save and a take's start or stop are passed over, and a rig
// switch is a barrier. `{index,
// entry}`, `{barrier}` or null.
export function nextUndo(logAnswer) {
  const log = (logAnswer && logAnswer.log) || [];
  const first = (logAnswer && logAnswer.first) || 0;
  const handled = new Set();
  for (let i = log.length - 1; i >= 0; i--) {
    const e = log[i];
    if (e.op === "rig") return { barrier: e };
    if (e.op === "undo") handled.add(e.undoes);
    else if (e.op === "stale") handled.add(e.stale);
    else if (e.op === "save" || e.op === "record") continue;
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
    case "save": return `save to ${a.file}`;
    case "record": return `${a.action === "stop" ? "stop" : "record"} ${a.name}`;
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

// A child of the rig that exited, from the agent's crash record
// (`agent.supervisor.crashed`, supervisor.exit_record), as a problem a player
// can act on: which process, what that means, and what to do. On the rig on
// 2026-11-02 a killed tuner was in /health, `status` and the agent's terminal
// and nowhere on either screen. What each process is and what its exit means
// are the record's own (`label`, `means`, supervisor.CHILD_ROLES), not a copy
// kept here.
//
// `rig` is /health's rig. A crash outlives its rig in the record, so it is
// the running rig's only when its group and start are the running rig's
// (supervisor.same_run); one of an earlier run is said as past, quietly, with
// nothing to restart (`kind: "page"`, the muted box).
const capital = (text) => text ? text[0].toUpperCase() + text.slice(1) : text;

export function sameRun(record, rig) {
  return !!(rig && rig.running && record.pgid !== null && record.pgid !== undefined
    && record.pgid === rig.pgid && record.started_at === rig.started_at);
}

export function crashProblem(record, rig) {
  const what = capital(record.label || record.name);
  const how = record.signal ? `was killed by ${record.signal}` : `exited with status ${record.returncode}`;
  const where = record.config_name ? ` in ${record.config_name}` : "";
  const when = record.phase === "startup" ? " while it was starting" : "";
  const log = record.log ? ` Its log is ${record.log}.` : "";
  if (!sameRun(record, rig)) {
    return {
      kind: "page", title: `${what} exited in an earlier run`,
      reason: `${what} ${how}${when}${where}, a run that is no longer the one up.${log}`, at: record.at || null,
    };
  }
  const means = record.means ? `; ${record.means}` : "";
  return {
    title: `${what} exited`,
    reason: `${what} ${how}${when}${where}${means}. Restart the rig to bring it back: nothing restarts it on its own.${log}`,
    at: record.at || null,
  };
}

// The agent's own record of what last went wrong, the rig's children that
// exited and the supervisor's problems, and a MIDI listener that failed: all
// belong in the same glance as everything else (rule 2), and the crashes as
// loudly as the last error.
export function agentProblems(health) {
  if (!health || !health.ok) return [];
  const agent = health.body.agent || {};
  const rig = health.body.rig || {};
  const supervisor = agent.supervisor || {};
  const out = [];
  const crashes = (Array.isArray(supervisor.crashed) ? supervisor.crashed : [])
    .filter((record) => record && typeof record === "object").map((record) => crashProblem(record, rig));
  // The running rig's first, as loudly as the last error; earlier runs' last.
  out.push(...crashes.filter((c) => !c.kind));
  for (const p of Array.isArray(supervisor.problems) ? supervisor.problems : []) {
    if (p && typeof p === "object") out.push({ title: "The rig's supervisor", reason: p.message || p.kind || "a problem with no message", at: p.at || null });
  }
  if (agent.last_error) {
    const e = agent.last_error;
    out.push({ title: `Last error${e.command ? " (" + [].concat(e.command).join(" ") + ")" : ""}`, reason: e.message, at: e.at || null });
  }
  if (agent.midi && agent.midi.state === "failed") out.push({ title: "Pedals", reason: agent.midi.problem || "the MIDI listener failed", at: null });
  if (agent.stuck_live_calls) out.push({ title: "Sushi", reason: `${agent.stuck_live_calls} call(s) to Sushi never returned`, at: null });
  out.push(...crashes.filter((c) => c.kind));
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

const VERBS = { start: "Starting", stop: "Stopping", switch: "Switching to", restart: "Restarting" };

// The request for a lifecycle gesture. A start is a rig of channels
// (config/rigs/, `rig: true`) and headless: the page replaces the OSC panel,
// and a rig run with its panel cannot be switched live (#93).
export function lifecycleCall(op, name, { discard = false } = {}) {
  if (op === "start") return { path: "/rig/up", json: { config_name: name, rig: true, headless: true } };
  if (op === "stop") return { path: "/rig/down", json: {} };
  if (op === "switch") return { path: "/rig/switch", json: { config_name: name } };
  // Offered after an edit of the running rig's file (the rig files below):
  // the agent's restart brings the rig back from its file, as it was run.
  // It refuses one that would drop unsaved changes (409 `unsaved`) unless
  // told `discard`, which the page sends only from a second, explicit tap.
  if (op === "restart") return { path: "/rig/restart", json: discard ? { discard: true } : {} };
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
  if (op === "restart") {
    return answer.ok ? { ok: true, title: `Restarted ${name}`, text: `Restarted ${name} from its file.${warnings}` }
      : { ok: false, title: `Not restarted: ${name}`, reason: answer.reason };
  }
  return answer.ok ? { ok: true, title: "Stopped", text: `Stopped ${name || "the rig"}.` }
    : { ok: false, title: `Not stopped: ${name || "the rig"}`, reason: answer.reason };
}

// ---------------------------------------------------------------- saving (S4)
// POST /rig/save: the running rig into its own file of config/rigs, or "save
// as" a new one, which then is the running rig. The agent decides what can be
// saved and says why not (panel, v1, taken, a chain changed live...); these
// only say what the page offers and what an answer means.

// A rig's name, as save.validate_name checks it (save.NAME_PATTERN): it
// becomes config/rigs/<name>.yaml. test_ui.py pins the two as one rule.
export const NAME_PATTERN = "^[a-z0-9][a-z0-9_-]{0,63}$";
const NAME_RE = new RegExp(NAME_PATTERN);

// Why a save-as name will not do, or null. `running` is the running rig's
// name: saving "as" it is a plain save, which the Save button is for.
export function checkRigName(name, running = null) {
  const n = typeof name === "string" ? name : "";
  if (!n) return "Name the new rig: it becomes config/rigs/<name>.yaml.";
  if (!NAME_RE.test(n)) {
    return "A rig's name is lowercase letters, digits, '-' and '_', starting with a letter or digit, "
      + "at most 64 characters: it becomes config/rigs/<name>.yaml.";
  }
  if (running && n === running) return `${n} is the running rig: Save writes into it.`;
  return null;
}

export function saveCall(name = null) {
  return { path: "/rig/save", json: name ? { name } : {} };
}

// The newest save in the log, or null: `{file, rig, how, at, library}`.
export function lastSave(logAnswer) {
  const log = (logAnswer && logAnswer.log) || [];
  for (let i = log.length - 1; i >= 0; i--) {
    const e = log[i];
    if (e.op === "save") return { ...(e.args || {}), at: e.at };
  }
  return null;
}

// Since when the agent's log can say what changed: the newest of the last
// save, the rig's start and the agent's own start (a change made before the
// agent started is not in its log). An ISO time, or null.
export function seenSince(health, logAnswer) {
  const body = health && health.ok ? health.body : {};
  const last = lastSave(logAnswer);
  const times = [last && last.at, (body.rig || {}).started_at, (body.agent || {}).started_at]
    .filter((t) => typeof t === "string" && !Number.isNaN(Date.parse(t)));
  if (!times.length) return null;
  return times.reduce((a, b) => (Date.parse(b) > Date.parse(a) ? b : a));
}

// What the Save buttons offer. Both are offered whenever a rig that can be
// saved from here is up and nothing is running: the unsaved list is only
// what the agent saw (not the panel's changes, nor pedals mapped in Sushi,
// nor anything before the agent started, nor an amp change while GET /amp
// failed), so an empty list is no proof the file holds what is playing. Not
// offered, with why and how to save instead, for a v1 run (`rig.rig` null)
// or one with its panel (any mode but "headless", as `rig.runs_panel`
// reads it), both of which the agent refuses. `fmt` writes a time.
export function saveView(health, unsaved, pending, logAnswer = null, fmt = (iso) => iso) {
  const agentUp = !!(health && health.ok);
  const rig = agentUp ? health.body.rig || {} : {};
  const busy = agentUp ? (health.body.agent || {}).busy || null : null;
  const running = rig.running ? rig.config_name || null : null;
  const count = unsaved && unsaved.items ? unsaved.items.length : 0;
  const idle = !pending && !busy;
  const file = rig.running && rig.rig ? rig.rig : null;
  let blocked = null;
  if (!running) blocked = "No rig is running to save.";
  else if (!file) {
    blocked = `${running} is a v1 config, not a rig of channels started with --rig, so it is not saved from here. `
      + "Save it on the rig with: sushi-rig save <name> --rig <its rig.yaml>";
  } else if (rig.mode !== "headless") {
    blocked = "The rig runs with its panel, which owns the amp, so the agent does not know what the amp is at: "
      + "save with the panel's SAVE button, or start the rig from here (headless) to save from here.";
  }
  const since = seenSince(health, logAnswer);
  const off = !!blocked || !idle;
  const seen = since ? fmt(since) : "the agent started";
  return {
    running, file, blocked, since,
    save: { label: running ? `Save to ${running}` : "Save", disabled: off },
    saveAs: { label: "Save as…", disabled: off },
    revert: { label: "Revert…", disabled: off },
    // The quiet indicator on the Save button, the one sign of unsaved work
    // now the itemised list is gone (2026-11-06: labels that came and went
    // as values changed were what the player asked to be rid of). A count
    // in a box of its own width, always there, empty at none, so nothing
    // moves as it changes; its title says what it counts.
    dot: {
      count, text: count ? String(Math.min(count, 99)) + (count > 99 ? "+" : "") : "",
      title: (count === 0 ? `No unsaved changes seen since ${seen}.`
        : `${count} unsaved change${count === 1 ? "" : "s"} made through the agent since ${seen}.`)
        + (unsaved && unsaved.partial ? " The agent's log was trimmed since the rig came up, so older ones may be missing." : "")
        + " The agent does not see the panel's changes, pedals mapped in Sushi, or anything before it started, so Save is always offered.",
      spoken: count ? `, ${count} unsaved change${count === 1 ? "" : "s"}` : "",
    },
    // One note whatever the count, so the column does not reflow as values
    // change: what Save does, said once.
    note: blocked ? blocked
      : pending ? (pending.name ? `Saving as ${pending.name}…` : `Saving ${running}…`)
        : busy ? `${busy} is running: saving waits for it.`
          : `Save writes ${file} in place, keeping its comments. It is not committed: that is yours to do.`,
  };
}

// What git says of a save (`library`: take.library_state's `{commit,
// dirty}` and save.file_git_state's `{file, others}`, or null). The file is
// said from its own state; `dirty` is the whole checkout's, so it is not
// read as the file's.
export function commitText(library) {
  const tail = " sushi-rig does not commit: that is yours to do.";
  if (!library || !library.commit) {
    return "Not committed. Which commit it sits on is not known: git could not be read in the checkout." + tail;
  }
  const short = String(library.commit).slice(0, 7);
  const others = library.others === true ? " Other tracked files have uncommitted changes too." : "";
  switch (library.file) {
    case "untracked": return `Not committed: a new file git does not track yet, on commit ${short}.${others}${tail}`;
    case "changed": return `Not committed: changed on top of commit ${short}.${others}${tail}`;
    case "unchanged": return `Nothing to commit: the file is as commit ${short} has it.${others}`;
    case "ignored": return `Not committed: git ignores this file (on commit ${short}).${others}`;
    default: return `Not committed, on commit ${short}: whether this file differs from it could not be read.${tail}`;
  }
}

// The last save, for the line under the buttons, from its log entry: what
// git said *when it was saved*, since a commit made since is not seen.
export function lastSaveText(last, fmt = (iso) => iso) {
  if (!last) return null;
  const file = (last.library || {}).file;
  const then = file === "unchanged" ? "as its commit had it when saved" : file === "ignored" ? "ignored by git"
    : "not committed when saved";
  return { time: fmt(last.at), file: last.file, then };
}

// ---------------------------------------------------------------- revert to a previous save
// GET /rigs/<rig>/saves lists the revert points: every save from here
// (a snapshot of the file as it was written, saves.py) and every commit
// that changed the file. POST /rigs/<rig>/revert writes one back, byte for
// byte, and plays it on the running rig through the live switch. The only
// view of history the rack has (2026-11-06).

// How long ago, in words a glance can take: "just now", "5 min ago",
// "3 h ago", "2 days ago". Null for a time that cannot be read.
export function agoText(iso, now) {
  const then = Date.parse(iso);
  if (Number.isNaN(then) || !isNumber(now)) return null;
  const s = Math.max(0, Math.floor((now - then) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const hours = Math.round(m / 60);
  if (hours < 36) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export function savesCall(rig) {
  return { path: `/rigs/${encodeURIComponent(rig)}/saves` };
}

export function revertCall(rig, id, discard = false) {
  return { path: `/rigs/${encodeURIComponent(rig)}/revert`, json: discard ? { save: id, discard: true } : { save: id } };
}

// The list a "Revert…" opens, from GET /rigs/<rig>/saves: each point by
// time with how long ago, newest first as the agent gives them, the one the
// file is now marked. `fmt` writes a time, `now` is the page's clock.
export function revertRows(answer, now, fmt = (iso) => iso) {
  if (!answer) return { rows: [], loading: true, problem: null, notes: [], empty: false };
  if (!answer.ok) return { rows: [], loading: false, problem: `The saves could not be listed: ${answer.reason}`, notes: [], empty: false };
  const b = answer.body;
  const current = (b.current || {}).kept_as || null;
  const rows = (b.points || []).map((p) => {
    const ago = agoText(p.at, now);
    if (p.kind === "commit") {
      return { id: p.id, kind: "commit", title: `Commit ${String(p.sha).slice(0, 7)}`,
        sub: [p.subject, ago].filter(Boolean).join(" · "), when: fmt(p.at), ago, current: false };
    }
    return { id: p.id, kind: "save", title: `Saved ${fmt(p.at)}`,
      sub: [ago, p.how === "new" ? "saved as this rig" : null].filter(Boolean).join(" · "),
      when: fmt(p.at), ago, current: p.id === current };
  });
  const notes = [];
  if (b.git_note) notes.push(`No commits listed: ${b.git_note}.`);
  for (const problem of b.problems || []) notes.push(problem);
  notes.push(`The newest ${b.keep} saves of ${b.rig} are kept, in ${b.kept_in}/, which git ignores.`);
  return { rows, loading: false, problem: null, notes, empty: rows.length === 0 };
}

// The confirm for reverting to `row`: what it does and, named, what is lost.
// `count` is the unsaved changes the page saw (`unsavedChanges`); the agent
// decides, and refuses with `unsaved` if it saw some the page did not.
export function revertConfirm(row, answer, count, rig, fmt = (iso) => iso) {
  const b = (answer && answer.ok && answer.body) || {};
  const current = b.current || {};
  const what = row.kind === "commit" ? `commit ${row.title.slice(7)} (${row.ago || row.when})`
    : `the save of ${row.when} (${row.ago || "time not known"})`;
  const running = !!b.running;
  const lost = [];
  if (count > 0 && running) lost.push(`${count} unsaved change${count === 1 ? "" : "s"} playing now, made since the last save.`);
  if (current.kept_as === row.id) {
    lost.push("Nothing of the file: it is this save already.");
  } else if (current.kept_as) {
    const kept = (b.points || []).find((p) => p.id === current.kept_as);
    lost.push(`Nothing of the file: as it is now it is kept as the save of ${kept ? fmt(kept.at) : "earlier"}, so you can come back to it.`);
  } else if (current.git === "unchanged") {
    lost.push("Nothing of the file: as it is now it is as its commit has it, so you can come back to that.");
  } else {
    lost.push("The file as it is now: it is in no save or commit (it changed since, by an edit or by hand), so it is lost.");
  }
  return {
    title: `Revert ${rig} to ${what}?`,
    lost,
    plays: running
      ? "The running rig is switched to it: live where it can be, by a restart where it must be (the sound stops for a few seconds)."
      : `${rig} is not running: only its file changes.`,
    go: count > 0 && running ? `Revert, dropping ${count} unsaved change${count === 1 ? "" : "s"}` : "Revert now",
    discard: count > 0 && running,
  };
}

// What a revert answered: {ok, title, text, commit} or {ok: false, title,
// reason, code, written}: the file can be written and the rig not switched.
export function revertOutcome(rig, answer) {
  const b = answer.body || {};
  const what = (b.point && b.point.what) || "as it was";
  if (!answer.ok) {
    const title = b.written ? `Reverted the file, but ${rig} is not playing it` : `Not reverted: ${rig}`;
    return { ok: false, title, reason: answer.reason, code: answer.code || null, written: !!b.written };
  }
  const why = (b.why || []).map((r) => `${r.code}: ${r.detail}`).join("; ");
  const secs = isNumber(b.seconds) ? ` in ${b.seconds < 10 ? b.seconds.toFixed(1) : Math.round(b.seconds)} s` : "";
  let text = `${b.file} is ${what} again, byte for byte.`;
  if (!b.running) text += ` ${rig} is not running: it plays as reverted when it is next started.`;
  else if (b.how === "live") text += ` The rig was switched to it live${secs}: Sushi was not restarted.`;
  else text += ` The rig was switched to it by restart${secs}, because ${why || "the agent gave no reason"}.`;
  if (b.discarded) text += " The unsaved changes were dropped.";
  return { ok: true, title: `Reverted ${rig}`, text, commit: commitText(b.library) };
}

// What a save's answer says, for the page: `{ok: true, title, text, commit}`
// or `{ok: false, title, reason}` with the agent's reason as it gave it.
export function saveOutcome(name, answer) {
  const body = answer.body || {};
  if (!answer.ok) {
    // A save-as that wrote its file and could not make it the running rig
    // (`recorded: false`): the file is there, so "not saved" would be untrue.
    const written = body.recorded === false && body.file;
    const title = written ? `Written, but not running: ${body.file}` : name ? `Not saved as ${name}` : "Not saved";
    return { ok: false, title, reason: answer.reason, code: answer.code || null };
  }
  const amp = body.amp || {};
  const ampText = amp.saved ? ` The amp is saved as the agent last sent it (${modelName(amp.model)}): Carla cannot be read back.`
    : amp.why ? ` The amp is not saved: ${amp.why}.` : "";
  const title = body.how === "new" ? `Saved as ${body.rig}` : `Saved ${body.rig}`;
  const text = body.how === "new"
    ? `Wrote ${body.file}, a new rig, which is now the running rig. It starts without the comments of ${body.previous}, which keeps them.${ampText}`
    : `Wrote ${body.file} in place, keeping its comments.${ampText}`;
  return { ok: true, title, text, commit: commitText(body.library), file: body.file };
}

// ---------------------------------------------------------------- volume: the master's and each channel's (2026-11-06)
// One fader for a gain in dB, on both screens: the master's on each header
// (the master track's own gain: rigfile.py's master `level`, compiled to it
// as a channel's level is to its send's), and each channel's at the top of
// its row in the rack and in the stage's levels (its send's gain, the
// channel's `level:`, #101). Set through POST /live/parameter like any knob,
// so it is in the log, undone by Undo, saved as `level:`, and a live step
// of a switch, not a restart.
//
// Its range is clamped to what is safe to reach from a phone in the dark:
// -60 to +6 dB, and below that one notch of -∞, which is the gain's own
// floor (-120 dB on the -120 to +24 range a track's gain and a send's
// have: 10^-6, below hearing), not a mute parameter, so one parameter is
// the volume, one undo puts it back and one line of the file holds it.

export const VOLUME_MIN_DB = -60;
export const VOLUME_MAX_DB = 6;
// The fader's bottom notch, below VOLUME_MIN_DB: -∞.
export const VOLUME_OFF_SLIDER = VOLUME_MIN_DB - 1;
// One wheel notch or arrow key: half a dB, a tenth of that with shift.
export const VOLUME_STEP_DB = 0.5;
// A track's gain and a send's, as rigfile.LEVEL_RANGE records them, for a
// page that has not read the processor's parameter_info yet.
const GAIN_RANGE = { automatable: true, value: null, min_domain_value: -120, max_domain_value: 24 };

// A processor's `gain` as a volume control, its range from Sushi's
// parameter_info: the master track's, or a channel's send's.
export function gainControl(live, processor, label = "Volume") {
  const info = live && live.parameter_info && live.parameter_info[processor] && live.parameter_info[processor].gain;
  return makeControl("gain", info && isNumber(info.min_domain_value) ? info : GAIN_RANGE,
    { name: "gain", label, unit: "dB", scale: "linear", visibility: "primary" });
}

export function masterControl(live) {
  return gainControl(live, MASTER, "Master");
}

// The channels a MIDI blend drives, by channel, with the control that drives
// it (GET /rigs/<rig>/channels's `blended`, from rigfile.blend_controls):
// their volume follows the pedal. Shown, said, and not settable from here:
// a fader moved would be undone by the pedal's next move, and a save does
// not write a blended channel's gain as its level (rigfile.fold_state), so
// it would be a change that is neither kept nor heard for long.
export function blendedBy(channelsAnswer, channel) {
  const blended = channelsAnswer && channelsAnswer.ok ? channelsAnswer.body.blended || {} : {};
  return typeof blended[channel] === "string" ? blended[channel] : null;
}

// The master's value in dB, -Infinity at the gain's floor; null unknown.
export function volumeDb(ctrl, v) {
  if (!isNumber(v)) return null;
  const db = realOf(ctrl, v);
  return db <= ctrl.min + 1e-3 ? -Infinity : db;
}

// A dB value as the fader may hold it: clamped to -60 .. +6, or -∞.
export function volumeClamp(db) {
  if (db === -Infinity) return -Infinity;
  if (!isNumber(db)) return null;
  return clampTo(db, VOLUME_MIN_DB, VOLUME_MAX_DB);
}

// The normalised value that sets the master to `db` (-∞ is the floor).
export function volumeValueOf(ctrl, db) {
  return db === -Infinity ? 0 : normalisedOf(ctrl, clampTo(db, ctrl.min, ctrl.max));
}

export function volumeText(db) {
  if (db === null || db === undefined) return "—";
  if (db === -Infinity) return "−∞ dB";
  let text = db.toFixed(1);
  if (/^-0(\.0+)?$/.test(text)) text = text.slice(1);
  return minus(text) + " dB";
}

// The fader's position for a dB value, and back: one notch below the range
// is -∞, and only -∞; a value outside the range (a file may hold -80 or +10)
// sits at that end of it, its text saying what it is.
export function volumeSlider(db) {
  if (db === null || db === undefined || db === -Infinity) return VOLUME_OFF_SLIDER;
  return clampTo(db, VOLUME_MIN_DB, VOLUME_MAX_DB);
}

export function volumeOfSlider(x) {
  return x < VOLUME_MIN_DB - 0.5 ? -Infinity : clampTo(x, VOLUME_MIN_DB, VOLUME_MAX_DB);
}

// A dragged fader's dB, held to whole dB as a knob's are (`sticky`): `{db, held}`.
export function volumeSticky(db, held = null) {
  if (db === -Infinity || !isNumber(db)) return { db, held: null };
  if (held !== null && Math.abs(db - held) <= HOLD_BAND) return { db: held, held };
  const near = Math.round(db);
  if (Math.abs(db - near) <= SNAP_BAND) return { db: near, held: near };
  return { db, held: null };
}

// A volume `steps` notches from `db`, never against the direction turned
// (review, 2026-11-07: +10 dB a notch up was clamped to +6, -80 a notch up
// went to -∞). Inside -60 .. +6 it moves by VOLUME_STEP_DB, sticky to whole
// dB, and down from -60 is -∞, up from -∞ is -60. Outside the range (a file
// may hold +10 or -80) it steps toward the range, and away from it not at
// all, except down below -60, which is -∞. `{db, held}`.
export function volumeStepped(db, steps, fine = false, held = null) {
  if (db === -Infinity || db === null || db === undefined) {
    return steps > 0 ? { db: VOLUME_MIN_DB, held: null } : { db: -Infinity, held: null };
  }
  const size = VOLUME_STEP_DB / (fine ? FINE_DIVISOR : 1);
  const next = db + steps * size;
  if (db > VOLUME_MAX_DB) return { db: steps > 0 ? db : next, held: null };
  if (db < VOLUME_MIN_DB) return steps > 0 ? { db: next, held: null } : { db: -Infinity, held: null };
  if (next < VOLUME_MIN_DB - 1e-9) return { db: -Infinity, held: null };
  const kept = clampTo(next, VOLUME_MIN_DB, VOLUME_MAX_DB);
  return fine ? { db: kept, held: null } : volumeSticky(kept, held);  // fine is for the values between
}

// What is typed into the master's field: a number of dB (with or without
// the unit), or -∞ as "-inf", "−∞", "mute" or "off"; clamped to -60 .. +6.
// Exact, never snapped.
export function parseVolume(text) {
  const raw = String(text ?? "").trim();
  const t = raw.replace(/[−–]/g, "-").replace(/,/g, ".").replace(/\s+/g, "").replace(/db$/i, "");
  if (/^(-(inf|infinity|∞)|mute|off)$/i.test(t)) return { ok: true, db: -Infinity, clamped: false };
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return { ok: false, why: raw ? `“${raw}” is not a level in dB` : "Type a level in dB." };
  const db = Number(t);
  const kept = volumeClamp(db);
  return { ok: true, db: kept, clamped: kept !== db };
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
  // A model is named by its file, here as on the stage and in the log
  // (`modelName`); the name the file gives itself is its description
  // (`declared`), never its title: the two screens named one model two ways
  // on the rig (2026-11-02, amp_live.declared_name).
  const model = b.model ? {
    name: modelName(b.model.stored), stored: b.model.stored, description: b.model.description || null,
    declared: b.model.declared || null,
    problem: b.model.problem || null, warning: b.model.warning || null,
  } : null;
  const listed = models && models.ok ? models.body.models : [];
  const options = listed.map((m) => ({
    model: m.model, playing: !!m.playing, usable: !!m.usable && !m.playing,
    label: modelName(m.model) + (m.playing ? " (playing)" : !m.usable ? " (cannot be played here)" : ""),
    why: m.why || null, declared: m.declared || null,
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

// ---------------------------------------------------------------- recording (S7, S8)
// The record bar asks the agent (GET /record), which asks the recorder, and
// shows what it says: whether a take is running is the recorder's to decide,
// never this page's, so a page reloaded mid-take, or a second page, shows the
// take running (the reason #57 made RECORD and STOP two buttons, not a toggle).

// Seconds as a clock: 0:07, 1:47, 1:02:03. Null for no time.
export function clockText(seconds) {
  if (!isNumber(seconds) || seconds < 0) return null;
  const s = Math.floor(seconds);
  const pad = (n) => String(n).padStart(2, "0");
  if (s < 3600) return `${Math.floor(s / 60)}:${pad(s % 60)}`;
  return `${Math.floor(s / 3600)}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
}

// How long the running take has been going, now: what the recorder said
// (`elapsed`, by its own clock) plus how long ago it said it, by this page's.
// The page's clock only measures the gap between polls, so a phone whose
// clock is wrong still shows the take's length right.
export function elapsedNow(base, now) {
  if (!base || !isNumber(base.elapsed)) return null;
  return base.elapsed + Math.max(0, (now - base.at) / 1000);
}

export function recordCall(name) {
  const n = typeof name === "string" ? name.trim() : "";
  return { path: "/record/start", json: n ? { name: n } : {} };
}

export const STOP_CALL = { path: "/record/stop", json: {} };

// Why a take name will not do, or null. Empty is fine: the agent names the
// take (`default_name` below). The rule is a config's (take.check_name,
// NAME_PATTERN).
export function checkTakeName(name) {
  const n = typeof name === "string" ? name.trim() : "";
  if (!n) return null;
  if (!NAME_RE.test(n)) {
    return "A take's name is lowercase letters, digits, '-' and '_', starting with a letter or digit, at most 64 characters.";
  }
  return null;
}

// What the player has typed into the take's name, or "" when the field holds
// nothing of theirs: it is empty, or it shows the agent's default as it was
// put there (#134). Only what this returns is sent as a name and kept across
// polls; with "" the agent names the take, by its own count at that moment.
export function takeTyped(value, fallback) {
  const text = typeof value === "string" ? value : "";
  return !text.trim() || text.trim() === fallback ? "" : text;
}

function stemsText(stems) {
  const files = (stems || []).map((s) => s.file);
  return files.length ? `${files.length} stem${files.length === 1 ? "" : "s"}: ${files.join(", ")}` : "";
}

// The record bar, from /health, GET /record's answer (whose `names` are the
// takes already in the folder, and `default_name` what the agent would call
// a take started now without a name), the name typed and the start or stop
// in flight from this page. Record and
// Stop are both always there; each is enabled when it would be acted on.
// Neither is touched by the performance lock: recording is a stage action.
//
// `default` is the agent's name for the next take, `<rig>-<date>-take-<nn>`
// (#134): the field shows it until the player types, and it is the agent's
// to work out, never this page's, so two pages show the same one.
export function recordBar(health, answer, typed, pending) {
  const agentUp = !!(health && health.ok);
  const rig = agentUp ? health.body.rig || {} : {};
  const busy = agentUp ? (health.body.agent || {}).busy || null : null;
  const body = answer && answer.ok ? answer.body : null;
  const off = (reason) => ({
    state: "unavailable", title: "Recording is off", reason, name: null, stems: "",
    record: { label: "Record", disabled: true }, stop: { label: "Stop", disabled: true }, note: null,
  });
  if (!agentUp) return off("The agent is not answering.");
  if (!rig.running) return off("No rig is running to record.");
  if (busy) return { ...off(`${busy} is running: recording waits for it.`), state: "busy", title: "Record" };
  if (answer && !answer.ok) return off(`The recorder could not be asked: ${answer.reason}`);
  if (!body) return { ...off("Asking the recorder…"), state: "unknown", title: "Record" };
  if (body.state === "recording") {
    return {
      state: "recording", title: "Recording", reason: null, name: body.name, stems: stemsText(body.stems),
      record: { label: "Record", disabled: true },
      stop: { label: pending === "stop" || body.doing === "stopping" ? "Stopping…" : "Stop", disabled: pending === "stop" || body.doing === "stopping" },
      note: body.said && body.said !== `recording ${body.name}` ? body.said : null,
    };
  }
  if (body.state !== "idle") return off(body.reason || "The recorder did not say why.");
  const name = typeof typed === "string" ? typed.trim() : "";
  const problem = checkTakeName(name);
  const replaces = name && !problem && (body.names || []).includes(name);
  const starting = pending === "start" || body.doing === "starting";
  const fallback = typeof body.default_name === "string" && body.default_name ? body.default_name : null;
  return {
    state: "idle", title: "Record", reason: null, name: null, stems: stemsText(body.stems),
    default: fallback,
    record: { label: starting ? "Starting…" : "Record", disabled: !!problem || starting },
    stop: { label: "Stop", disabled: true },
    note: problem ? problem
      : replaces ? `Will replace the earlier ${name}, which moves to _archive/${name}/.`
        : name || fallback ? null : "No name: the agent names the take when it starts.",
    bad: !!problem,
  };
}

// What a start or a stop said, for the line under the buttons.
export function recordOutcome(op, answer) {
  const body = answer.body || {};
  if (op === "start") {
    if (!answer.ok) return { ok: false, title: "Not recording", reason: answer.reason, code: answer.code || null };
    return { ok: true, title: `Recording ${body.name}`, text: body.replaced ? body.said : null };
  }
  if (!answer.ok && !body.name) return { ok: false, title: "Not stopped", reason: answer.reason, code: answer.code || null };
  return { ok: answer.ok && !(body.problems || []).length, title: `Recorded ${body.name}`, text: takeVerdict(body) };
}

// A take's verdict in a line: `take check`'s first problem, or that it is
// whole. Lost audio and a take cut off are among the problems, said as
// `take check` says them, never smoothed into "fine".
export function takeVerdict(take) {
  const problems = take.problems || [];
  const len = clockText(take.seconds);
  const stems = (take.stems || []).length;
  if (!problems.length) {
    return `${len ? `${len} · ` : ""}Whole · ${stems} stem${stems === 1 ? "" : "s"} · nothing lost`;
  }
  const lost = isNumber(take.lost_ms) && take.lost_ms > 0 ? ` · ${take.lost_ms} ms of audio lost` : "";
  return `${len ? `${len} · ` : ""}${problems.length === 1 ? "1 problem" : `${problems.length} problems`}${lost}: ${problems[0]}`;
}

// GET /takes as the rack lists it: each take's name, length and verdict,
// the one being recorded said as that rather than as unfinished (it is,
// until it stops).
export function takeRows(answer, recording = null) {
  if (!answer) return { dir: null, rows: [], problem: null, more: 0, archived: 0 };
  if (!answer.ok) return { dir: null, rows: [], problem: `The takes could not be listed: ${answer.reason}`, more: 0, archived: 0 };
  const body = answer.body;
  const rows = (body.takes || []).map((t) => {
    if (t.name === recording) return { name: t.name, len: "", note: "Recording now", tone: "live", title: t.folder };
    if (t.take === false) return { name: t.name, len: "", note: `Not a take: ${(t.problems || [])[0] || "unreadable"}`, tone: "warn", title: t.folder };
    const problems = t.problems || [];
    return {
      name: t.name, len: clockText(t.seconds) || "length unknown",
      note: problems.length ? takeVerdict({ ...t, seconds: null }) : `Whole · ${(t.stems || []).length} stems · nothing lost`,
      tone: problems.length ? "warn" : "ok", title: t.folder,
    };
  });
  return {
    dir: body.takes_dir, rows, more: body.more || 0, archived: body.archived || 0,
    problem: body.exists === false ? "No takes yet: the folder is made by the first take." : null,
  };
}

// Answers to requests that may overlap, in order: `issue` numbers a request
// as it is sent, and `accept` says whether its answer is still news, false
// once an answer to a later request has been taken.
export class Latest {
  constructor() { this.issued = 0; this.applied = 0; }
  issue() { this.issued += 1; return this.issued; }
  accept(ticket) {
    if (ticket <= this.applied) return false;
    this.applied = ticket;
    return true;
  }
}

// ---------------------------------------------------------------- rig files (the New rig screen)
// GET /machine/roles, GET /rigs/<rig>/channels, POST /rigs/new, and
// POST /rigs/<rig>/channels/add and /channels/<channel>/input. Every rule
// about what a rig may be is the agent's compiler's (rig.compile_rig_draft):
// what the page says before sending is a hint, drawn from the agent's own
// answers -- the ceiling and each role's widths come from /machine/roles,
// which asked the compiler and the patchbay -- and the agent's refusal,
// shown in place, is the answer.

// The machine file's input roles for the pickers: every role not named as an
// output (the agent reads direction from the name, since the machine file
// does not say), each labelled with its ports.
export function roleChoices(answer) {
  if (!answer) return { inputs: [], problem: null, loading: true, ceiling: null, output: null, machine: null };
  if (!answer.ok) {
    return { inputs: [], problem: `The machine file's roles could not be read: ${answer.reason}`, loading: false,
      ceiling: null, output: null, machine: null };
  }
  const body = answer.body;
  const inputs = (body.roles || []).filter((r) => r.direction !== "output").map((r) => ({
    name: r.name, label: `${r.name} · ${r.ports.join(" + ")}`, ports: r.ports,
    monoToStereo: !!r.mono_to_stereo, widths: r.widths, usedBy: r.used_by || [],
  }));
  return {
    inputs, loading: false, ceiling: body.ceiling || null, output: body.output || null, machine: body.machine || null,
    problem: inputs.length ? null : `${body.machine} has no input roles: add one under roles: (sushi-rig does not edit it).`,
  };
}

// Mono and stereo for a role: each offered where the role allows it, with
// the patchbay's reason where not (a stereo channel on one port needs
// mono_to_stereo; one port into a mono channel, two into a stereo one).
export function widthOptions(choices, role) {
  const found = (choices.inputs || []).find((r) => r.name === role);
  return ["mono", "stereo"].map((width) => {
    if (!found) return { width, ok: false, why: "Choose an input first." };
    const said = found.widths[width] || {};
    return { width, ok: !!said.ok, why: said.ok ? null : said.why || "The agent did not say." };
  });
}

// A channel's name from its role, as the committed rigs name theirs
// (guitar-in plays the guitar channel), unique among `taken`.
export function suggestName(role, taken = []) {
  const base = String(role || "").replace(/-in$/, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^[-_]+/, "") || "channel";
  if (!taken.includes(base)) return base;
  for (let n = 2; ; n++) if (!taken.includes(`${base}-${n}`)) return `${base}-${n}`;
}

// A fresh channel for the form: the first input no channel uses yet (or the
// first), mono where it can be, named from its role.
export function newChannel(choices, channels = []) {
  const inputs = choices.inputs || [];
  const used = channels.map((c) => c.input);
  const role = (inputs.find((r) => !used.includes(r.name)) || inputs[0] || {}).name || "";
  const widths = widthOptions(choices, role);
  const width = (widths.find((w) => w.ok) || widths[0]).width;
  return { name: role ? suggestName(role, channels.map((c) => c.name)) : "", input: role, width, from: null };
}

// Why a channel's name will not do, as a hint (the agent's compiler decides):
// the naming rule a channel shares with a rig's, and a name used twice.
export function checkChannelName(name, others = []) {
  const n = typeof name === "string" ? name : "";
  if (!n) return "Name the channel: it is the track's name and its stem's file name in a take.";
  if (!NAME_RE.test(n)) return "A channel's name is lowercase letters, digits, '-' and '_', starting with a letter or digit.";
  if (others.includes(n)) return `Another channel is called ${n}.`;
  return null;
}

// What the New rig form offers: whether another channel can be added (and
// the compiler's reason when not), each channel's hint, and Create.
export function newRigView(draft, rolesAnswer, rigsAnswer, health, pending) {
  const choices = roleChoices(rolesAnswer);
  const agentUp = !!(health && health.ok);
  const busy = agentUp ? (health.body.agent || {}).busy || null : null;
  const rigs = rigsAnswer && rigsAnswer.ok ? rigsAnswer.body.rigs : [];
  const max = choices.ceiling ? choices.ceiling.channels : null;
  const count = draft.channels.length;
  let nameProblem = checkRigName(draft.name);
  if (!nameProblem && rigs.includes(draft.name)) nameProblem = `${draft.name} is already a rig in config/rigs/: choose another name.`;
  const channels = draft.channels.map((c, i) => {
    const others = draft.channels.filter((_, j) => j !== i).map((o) => o.name);
    const widths = widthOptions(choices, c.input);
    const width = widths.find((w) => w.width === c.width);
    const problem = checkChannelName(c.name, others) || (!c.input ? "Choose its input." : null)
      || (width && !width.ok ? `${c.width} on ${c.input}: ${width.why}` : null);
    return { problem, widths };
  });
  const blocked = !agentUp ? "The agent is not answering." : busy ? `${busy} is running: creating waits for it.` : null;
  return {
    choices, channels, nameProblem, blocked,
    count, max,
    countText: max === null ? `${count} channel${count === 1 ? "" : "s"}` : `${count} of at most ${max}`,
    add: max !== null && count >= max ? { refused: `No more channels: ${choices.ceiling.why}.` } : { refused: null },
    create: {
      disabled: !!pending || !!blocked || !!nameProblem || count === 0 || channels.some((c) => c.problem),
      label: pending ? "Creating…" : draft.name && !nameProblem ? `Create ${draft.name}` : "Create",
    },
  };
}

// "Start from": every channel of every rig of config/rigs, as GET
// /rigs/<rig>/channels gave them (`byRig`: rig -> that answer).
export function sourceChoices(rigsAnswer, byRig) {
  const rigs = rigsAnswer && rigsAnswer.ok ? rigsAnswer.body.rigs : [];
  return rigs.map((rig) => {
    const answer = byRig[rig];
    const channels = answer && answer.ok ? answer.body.channels : [];
    return {
      rig, problem: answer && !answer.ok ? answer.reason : null,
      options: channels.map((c) => ({
        rig, channel: c.name, value: `${rig}\u0000${c.name}`,
        label: `${rig} · ${c.name} (${c.plugins.length} plugin${c.plugins.length === 1 ? "" : "s"})`,
      })),
    };
  });
}

function channelJson(c) {
  const out = { name: c.name, input: c.input, width: c.width };
  if (c.from) out.from = { rig: c.from.rig, channel: c.from.channel };
  return out;
}

export function newRigCall(name, channels) {
  return { path: "/rigs/new", json: { name, channels: channels.map(channelJson) } };
}

export function addChannelCall(rig, channel) {
  return { path: `/rigs/${encodeURIComponent(rig)}/channels/add`, json: channelJson(channel) };
}

export function inputCall(rig, channel, change) {
  return { path: `/rigs/${encodeURIComponent(rig)}/channels/${encodeURIComponent(channel)}/input`, json: change };
}

// What a rig-file answer says: `{ok: true, title, text, commit, offer}` or
// `{ok: false, title, reason, hint}`, the agent's reason as it gave it and,
// for a role the machine file lacks, the lines it says to add. `offer` is
// the lifecycle gesture that plays the change: start or switch to a new
// rig, restart the running one after an edit of its file.
export function rigFileOutcome(op, what, answer) {
  const body = answer.body || {};
  const titles = {
    new: [`Made ${what}`, `Not made: ${what}`],
    add: [`Added ${what}`, `Not added: ${what}`],
    input: [`Changed ${what}`, `Not changed: ${what}`],
  }[op];
  if (!answer.ok) return { ok: false, title: titles[1], reason: answer.reason, hint: body.machine_hint || null, code: answer.code || null };
  let text, offer = null;
  if (op === "new") {
    text = `Wrote ${body.file}, a new rig with ${(body.channels || []).length} channel${(body.channels || []).length === 1 ? "" : "s"}.`;
    const copied = (body.channels || []).filter((c) => c.from).map((c) => `${c.name} from ${c.from.rig} · ${c.from.channel}`);
    if (copied.length) text += ` Chains copied: ${copied.join(", ")}.`;
    offer = { op: body.offer === "switch" ? "switch" : "start", name: body.rig,
      label: body.offer === "switch" ? `Switch to ${body.rig}` : `Start ${body.rig}` };
    // A new file can move every later rig's Program Change number: said,
    // with the rigs that moved, before and after.
    if (body.programs && body.programs.moved && body.programs.moved.length) {
      return { ok: true, title: titles[0], text, commit: commitText(body.library), offer, file: body.file,
        programs: { note: body.programs.note, rows: body.programs.moved.map((m) => ({ rig: m.rig, from: m.from, to: m.to })) } };
    }
  } else {
    text = `Wrote ${body.file} in place, keeping its comments.`;
    if (body.running) {
      const why = (body.why || []).map((r) => r.detail).join("; ");
      text += ` ${body.rig} keeps playing as it was until it restarts${why ? ` (${why})` : ""}.`;
      offer = { op: "restart", name: body.rig, label: `Restart ${body.rig} to play it` };
    } else {
      text += ` ${body.rig} is not the running rig: it plays as edited when it is next started or switched to.`;
    }
  }
  return { ok: true, title: titles[0], text, commit: commitText(body.library), offer, file: body.file };
}

// What the rack offers for the running rig's file: its channels as the file
// has them (GET /rigs/<rig>/channels), with each one's input and width.
// Only for a rig of channels; a v1 run has no rig file to edit.
//
// When there is no rig file to edit, `{blocked}` says why and what to do
// instead, so the INPUTS heading is always there: on the rig (2026-11-06) a
// v1 run drew nothing at all, and nothing said why a vocal channel could not
// be added.
export function wiringView(health, channelsAnswer, unsaved) {
  const agentUp = !!(health && health.ok);
  const rig = agentUp ? health.body.rig || {} : {};
  if (!agentUp) return { blocked: "The agent is not answering, so the rig's file cannot be read or edited." };
  if (!rig.running) {
    return { blocked: "No rig is running: start a rig of channels from the picker to add a channel or change an input, or use New rig…" };
  }
  if (!rig.rig) {
    const name = rig.config_name || "The running config";
    return { blocked: `${name} is a v1 config (started with \`sushi-rig up\` without \`--rig\`), which has no rig file to edit: `
      + "start it from the picker, or make a rig with New rig…" };
  }
  const name = rig.config_name;
  return {
    rig: name,
    problem: channelsAnswer && !channelsAnswer.ok ? `${name}'s channels could not be read: ${channelsAnswer.reason}` : null,
    channels: channelsAnswer && channelsAnswer.ok ? channelsAnswer.body.channels : [],
    file: channelsAnswer && channelsAnswer.ok ? channelsAnswer.body.file : null,
    // A hint, said whether or not there is unsaved work, so it does not
    // come and go as values change (2026-11-06): the agent refuses an edit
    // of the running rig's file while it saw unsaved work, since the
    // restart that plays an edit would drop it.
    unsaved: `An edit takes effect at a restart, which reloads the file: with unsaved changes on ${name}, save first.`,
  };
}
