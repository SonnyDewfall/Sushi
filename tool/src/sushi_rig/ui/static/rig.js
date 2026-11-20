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
// The master's limiter (#137, rigfile.LIMITER): last on the master, the
// compiler's safety net, shown as one read-only line and never as a unit.
export const LIMITER = "master_limiter";
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
// "return" for the compiler's monitor path, "limiter" for the master's
// limiter (#137), null for a player's plugin. A
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
  if (track === MASTER && name === LIMITER) return "limiter";
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

// ---------------------------------------------------------------- a control of several parameters
// A profile's control (profile.py, "Controls"): one knob whose travel, 0 to
// 100 %, sets several of the plugin's parameters, each along a curve from
// its real value at 0 % (`at_0`) to the one at 100 % (`at_1`). The knob's
// own value is its travel, 0 to 1, shown as a percentage, so every gesture,
// step and typed value of a parameter's knob works on it unchanged. It is
// set by setting each parameter it drives (`mixTargets`), and shown from
// what those parameters are (`mixPosition`): where they are not on its
// curve, at the curve's nearest point, and nothing is sent until it is
// turned.

// How far from at_0 to at_1 a driven parameter is at travel m, on `curve`:
// profile.curve_at, the same three.
export const CURVES = ["linear", "sine", "cosine"];

export function curveAt(curve, m) {
  const x = clamp01(m);
  if (curve === "sine") return Math.sin(x * Math.PI / 2);
  if (curve === "cosine") return 1 - Math.cos(x * Math.PI / 2);
  if (curve === "linear") return x;
  // As profile.curve_at: a curve this does not know is not guessed at.
  // `makeMix` will not make a control of one, so this is a programming error.
  throw new Error(`unknown curve ${JSON.stringify(curve)}; one of ${CURVES.join(", ")}`);
}

export function makeMix(entry, params) {
  params = params || {};
  const drives = (entry.drives || []).map((d) => ({
    parameter: d.parameter, curve: d.curve, at_0: d.at_0, at_1: d.at_1,
    ctrl: makeControl(d.parameter, params[d.parameter], null),
  }));
  const unknown = drives.filter((d) => !CURVES.includes(d.curve)).map((d) => `${d.parameter}'s curve ${JSON.stringify(d.curve)}`);
  const missing = !drives.length || unknown.length > 0 || drives.some((d) => d.ctrl.missing);
  const notes = unknown.length ? [`not drawn: ${unknown.join(", ")} is not a curve this page knows`] : [];
  const lacking = drives.filter((d) => d.ctrl.missing).map((d) => d.parameter);
  if (lacking.length && lacking.length < drives.length) notes.push(`drives ${lacking.join(", ")}, which the running plugin lacks`);
  return {
    name: entry.name, kind: "mix", drives,
    label: entry.label || entry.name,
    description: entry.description || null,
    unit: "%", scale: "linear",
    control: entry.control === "fader" ? "fader" : "knob",
    min: 0, max: 100, hasRange: true, choices: null, integer: false,
    group: entry.group ?? null,
    visibility: entry.visibility || "primary",
    phone: isNumber(entry.phone) ? entry.phone : null,
    readOnly: drives.some((d) => d.ctrl.readOnly),
    missing, profiled: true, notes,
  };
}

// What each driven parameter is set to at travel v (0 to 1): `[{parameter,
// value}]`, each value Sushi's normalised one over that parameter's range.
export function mixTargets(mix, v) {
  return mix.drives.map((d) => ({
    parameter: d.parameter,
    value: normalisedOf(d.ctrl, d.at_0 + (d.at_1 - d.at_0) * curveAt(d.curve, v)),
  }));
}

// Off the curve by more than this (the largest difference, as a fraction of
// a parameter's range) is said beside the knob.
export const MIX_OFF_CURVE = 0.005;

// Where the knob is for the driven parameters' values (`values`, by
// parameter, normalised): the travel whose targets are nearest them (least
// squares, each parameter as a fraction of its range), and how far the
// furthest is from its target there (`off`). Null where any value is not
// known. Pure: a grid of a thousand steps, then a narrowing search around
// the best, so a value on the curve comes back to within float error.
export function mixPosition(mix, values) {
  if (!mix.drives.length) return null;
  const now = mix.drives.map((d) => (values ? values[d.parameter] : null));
  if (now.some((x) => !isNumber(x))) return null;
  const cost = (m) => mixTargets(mix, m).reduce((s, t, i) => s + (t.value - now[i]) ** 2, 0);
  let best = 0, bestCost = Infinity;
  const N = 1000;
  for (let i = 0; i <= N; i++) {
    const c = cost(i / N);
    if (c < bestCost) { best = i / N; bestCost = c; }
  }
  let lo = Math.max(0, best - 1 / N), hi = Math.min(1, best + 1 / N);
  for (let k = 0; k < 60; k++) {
    const a = lo + (hi - lo) / 3, b = hi - (hi - lo) / 3;
    if (cost(a) <= cost(b)) hi = b; else lo = a;
  }
  const m = cost((lo + hi) / 2) < bestCost ? (lo + hi) / 2 : best;
  const off = Math.max(...mixTargets(mix, m).map((t, i) => Math.abs(t.value - now[i])));
  return { v: m, off };
}

// Where a turn from `anchor` takes the levels (#154, review of PR #159). A
// rig saved off the curve (the hall at dry 0.81, early 0.16, tail 0.09) is
// shown at its nearest point m0; turning from there along the curve itself
// would jump every level to the curve at the first step. Instead each level
// goes from where it is to the curve's own end, at the pace the curve goes
// from m0 to that end: for m above m0, level = L + (C(1) - L) * s, where s
// = (C(m) - C(m0)) / (C(1) - C(m0)) runs 0 to 1 as the curve does; below,
// the same towards C(0). So m0 leaves the levels exactly as they are, 0 and
// 100 % are the curve's own ends, and each level moves one way only on each
// side of m0, as its curve does (turning up never raises dry, nor turning
// down a wet level, since C(1) is dry's floor and C(0) the wets'). On the
// curve, L is C(m0) and this is the curve. `anchor` is `{m0, levels}`, real
// values by parameter: `mixAnchor` makes one from what the parameters are.
export function mixAnchor(mix, values) {
  const at = mixPosition(mix, values);
  if (!at) return null;
  const levels = {};
  for (const d of mix.drives) levels[d.parameter] = realOf(d.ctrl, values[d.parameter]);
  return { m0: at.v, levels, off: at.off };
}

export function mixPath(mix, anchor, v) {
  if (!anchor) return mixTargets(mix, v);
  const m = clamp01(v), m0 = anchor.m0;
  return mix.drives.map((d) => {
    const C = (x) => d.at_0 + (d.at_1 - d.at_0) * curveAt(d.curve, x);
    const L = anchor.levels[d.parameter];
    const end = m >= m0 ? C(1) : C(0);
    const span = end - C(m0);
    const s = Math.abs(span) < 1e-12 ? (m === m0 ? 0 : 1) : (C(m) - C(m0)) / span;
    return { parameter: d.parameter, value: normalisedOf(d.ctrl, L + (end - L) * clamp01(s)) };
  });
}

// Whether `values` are where `mixPath(anchor, v)` put them: then the knob is
// at v, though the levels are off the curve.
export function onMixPath(mix, anchor, v, values) {
  if (!anchor || !isNumber(v)) return false;
  return mixPath(mix, anchor, v).every((t) => isNumber(values[t.parameter]) && Math.abs(values[t.parameter] - t.value) <= TOLERANCE);
}

// The knob's value in a /live answer (or null), for a card's summary.
export function mixValueOf(live, processor, mix) {
  const values = {};
  for (const d of mix.drives) values[d.parameter] = valueOf(live, processor, d.parameter);
  const at = mixPosition(mix, values);
  return at ? at.v : null;
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
    if (Array.isArray(entry.drives)) {
      // A control of several parameters: none of its own in Sushi.
      if (entry.visibility === "hidden") continue;
      const mix = makeMix(entry, params);
      if (mix.missing) missing.push(mix);
      else if (entry.visibility === "advanced") advanced.push(mix);
      else (byId.get(entry.group) || other).controls.push(mix);
      continue;
    }
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
// plugins as units and its send, return and limiter apart, since those are
// the compiler's, not the player's (they cannot be moved, removed, bypassed
// or retuned from here). A mono plugin's copy to stereo (role "stereo") is the
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
    let send = null, ret = null, limiter = null;
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
      else if (role === "limiter") limiter = unit;
      else units.push(unit);
    }
    units.forEach((u, i) => { u.monoNote = monoNote(units, i); });
    tracks.push({ name: track, isMaster: track === MASTER, units, send, ret, limiter, chain: [...chain] });
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
    .map((c) => `${c.label} ${formatValue(c, c.kind === "mix" ? mixValueOf(live, unit.name, c) : valueOf(live, unit.name, c.name))}`).join(" · ");
}

// The master's limiter (#137) as the Master tab's one line about it: what it
// is and that it is fixed, from what Sushi holds. Null where the master has
// none (a rig running from before it, or a v1 run): then the line says that
// a restart puts it in, since every compiled rig has one.
export const LIMITER_CEILING_DBFS = -1;
export function limiterLine(track, live) {
  if (!track || !track.isMaster) return null;
  if (!track.limiter) {
    return { present: false, text: "No limiter on this master: the rig was started before every rig had one. Restart it to put the limiter in." };
  }
  const bypassed = track.limiter.bypassed === true;
  const thresh = valueOf(live, track.limiter.name, "Thresh");
  // mda/Limiter's threshold is 10^(2*Thresh - 2): 0.975 is -1 dBFS.
  const at = isNumber(thresh) ? 20 * (2 * thresh - 2) : null;
  const where = at === null ? `${LIMITER_CEILING_DBFS} dBFS` : `${minus(at.toFixed(1))} dBFS`;
  return {
    present: true, bypassed,
    text: bypassed
      ? `Limiter: bypassed in Sushi, which the rig never does. Restart the rig to put it right.`
      : `Limiter: mda Limiter at ${where}, last on the master. A safety net the rig always has: it does not change the sound below ${where}, and is not set from here.`,
  };
}

export function channelLevels(model, live) {
  return model.tracks.filter((t) => t.send).map((t) => {
    const ctrl = t.send.panel.groups[0].controls[0];
    return { channel: t.name, text: formatValue(ctrl, valueOf(live, t.send.name, "gain")) };
  });
}

// ---------------------------------------------------------------- which rig is shown

// The live switches the page has seen, from one GET /live/log answer after
// another (`seen` is the last call's, null to begin; `log` the answer's
// body, or null when it was not read; `agent` /health's `agent.started_at`,
// which names the agent's own start). A live switch to another rig is
// already a new `config_name` in /health; this is for one /health cannot
// tell apart (#93 writes the state file's `switched_at`, which /health does
// not carry), and counts one only on a positive sign: an `op: "rig"` entry
// newer than every entry seen before it. Absent is never a sign: the log
// keeps its last 500 entries, so a rig entry is trimmed away by ordinary
// knob turns, and one /live/log that fails says nothing; either counted as
// "another rig" cleared the plugins and held the chain back (re-review of
// #135). Entries are numbered from `first`, the same number for the same
// entry across answers of one agent; an agent started again (its start not
// the one seen, or a log whose newest is older than one seen) begins again
// from 0 and keeps Sushi running, and any rig entry in its log happened
// since the page last saw the agent before, so it counts. An unread log
// changes nothing, the agent's start with it, so that a log read later
// from the new agent is still known as one. Returns {agent, max, count}.
export function switchesSeen(seen, log, agent = null) {
  if (!log || !Array.isArray(log.log)) return seen;
  const first = isNumber(log.first) ? log.first : 0;
  const last = first + log.log.length - 1;
  let rig = null;
  for (let i = log.log.length - 1; i >= 0; i--) {
    if (log.log[i] && log.log[i].op === "rig") { rig = first + i; break; }
  }
  if (!seen) return { agent, max: Math.max(rig ?? -1, last), count: 0 };
  if (agent !== seen.agent || last < seen.max) {
    return { agent, max: Math.max(rig ?? -1, last), count: seen.count + (rig !== null ? 1 : 0) };
  }
  if (rig !== null && rig > seen.max) return { agent, max: Math.max(rig, last), count: seen.count + 1 };
  return { agent, max: Math.max(seen.max, last), count: seen.count };
}

// ---------------------------------------------------------------- the rack's tabs and rows (#135)
// The rack's centre shows one chain at a time, behind tabs, and a bypassed
// plugin as a slim row. What is a tab, which is open, where the amp goes,
// what a closed tab must still say, and how big a row is, are decided here;
// app.js draws them and holds what the player opened.

// The role the amp is in front of (patchbay.GUITAR_ROLE): the amp, the tuner
// and the recorder's dry tap all key off the channel that takes it.
export const GUITAR_ROLE = "guitar-in";

// The tabs: one per channel in the rig's order, then the master's, labelled
// Master. A rig of one channel has two, so the layout is the same on every
// rig of channels; a v1 run has its tracks and no Master, having no master.
export function rackTabs(model) {
  const tracks = (model && model.tracks) || [];
  return [...tracks.filter((t) => !t.isMaster), ...tracks.filter((t) => t.isMaster)]
    .map((t) => ({ id: t.name, label: t.isMaster ? "Master" : t.name, master: !!t.isMaster }));
}

// The tab that is open: the one wanted when the rig has it, else the first
// (the first channel), else none.
export function openTab(tabs, wanted) {
  if (tabs.some((t) => t.id === wanted)) return wanted;
  return tabs.length ? tabs[0].id : null;
}

// What the browser keeps of the open tab, and reads back: one record, the
// rig it was chosen on and the tab. Another rig's is not this rig's (a
// channel called vocal there says nothing of the one here), so a rig that
// changes opens on its first channel. Anything unreadable is nothing kept.
export function tabToRemember(rig, tab) {
  return JSON.stringify({ rig, tab });
}

export function rememberedTab(stored, rig) {
  if (typeof stored !== "string" || typeof rig !== "string") return null;
  let kept = null;
  try { kept = JSON.parse(stored); } catch { return null; }
  return kept && typeof kept === "object" && kept.rig === rig && typeof kept.tab === "string" ? kept.tab : null;
}

// The tab a key moves to from `current`, as a tablist's keys go: the arrows
// to the next and the one before, round the ends, Home and End to the ends.
// Null for any other key.
export function tabKey(tabs, current, key) {
  const i = tabs.findIndex((t) => t.id === current);
  if (!tabs.length || i < 0) return null;
  const to = key === "ArrowRight" ? (i + 1) % tabs.length
    : key === "ArrowLeft" ? (i + tabs.length - 1) % tabs.length
      : key === "Home" ? 0 : key === "End" ? tabs.length - 1 : null;
  return to === null ? null : tabs[to].id;
}

// The tab the amp's card is in: the channel the amp is in front of, which is
// the first channel the RUNNING rig plays from GUITAR_ROLE. What the running
// rig plays from each role is GET /machine/roles's `used_by` (`rolesAnswer`,
// {ok, body}), read from the running config's own wiring, so it does not
// change when Change input writes the file: until the restart the amp is
// still in front of the channel it was (review of #135). It can say only
// when the agent read the running config (`running` set, no `wiring_note`:
// otherwise every `used_by` is empty, and says nothing). Only when it
// cannot say (the answer failed, the running config was unread, or no such
// role) is the rig's file asked (`channelsAnswer`, GET
// /rigs/<rig>/channels's). A run that has nothing on that role, or no rig
// file at all (`filed` false: a v1 run), leaves the amp with the first
// channel. Null while an answer it needs is still to come: app.js then
// waits, a bounded while, rather than draw the card in one tab and then
// move it to another.
export function ampTab(tabs, rolesAnswer, channelsAnswer, filed) {
  const channels = tabs.filter((t) => !t.master);
  if (!tabs.length) return null;
  const first = (channels[0] || tabs[0]).id;
  if (!filed) return first;
  if (!rolesAnswer) return null;
  const said = rolesAnswer.ok && rolesAnswer.body.running && !rolesAnswer.body.wiring_note;
  const role = said ? (rolesAnswer.body.roles || []).find((r) => r && r.name === GUITAR_ROLE) : null;
  if (role) {
    const fed = channels.find((t) => (role.used_by || []).includes(t.id));
    return fed ? fed.id : first;
  }
  if (!channelsAnswer) return null;
  const inFile = channelsAnswer.ok ? channelsAnswer.body.channels || [] : [];
  const fed = inFile.find((c) => c.input === GUITAR_ROLE && channels.some((t) => t.id === c.name));
  return fed ? fed.name : first;
}

// Everything the amp's card says is wrong, for its tab's mark (`ampCard`'s
// view): what a closed tab would otherwise hide.
export function ampFaults(amp) {
  if (!amp) return [];
  if (amp.state === "failed") return [`The amp could not be read: ${amp.why}`];
  if (amp.state !== "up") return [];
  const out = [];
  if (amp.down) out.push("Carla is not running: restart the rig to bring the amp back.");
  if (amp.model && amp.model.problem) out.push(amp.model.problem);
  if (amp.model && amp.model.warning) out.push(amp.model.warning);
  if (amp.listProblem) out.push(`The amp's models could not be listed: ${amp.listProblem}`);
  return out;
}

// What each tab says of what is behind it, open or not: a closed panel must
// not hide what matters (principle 8). Two things do and are said nowhere
// else: a change the agent refused, marked beside its control (`failed`, the
// keys of the controls whose last change was refused, as app.js keys them),
// and anything wrong with the amp (`amp`, `ampCard`'s view, `ampFaults`), on
// its tab (`ampOn`). While the amp's tab is not known yet (`ampOn` null and
// the card held back), its faults are on every channel's tab, so that no
// tab hides them meanwhile. {tab id: the note, or null}.
export function tabMarks(tabs, model, { failed = [], amp = null, ampOn = null } = {}) {
  const refused = new Set();
  let ampRefused = false;
  for (const key of failed) {
    const [kind, name] = String(key).split("\u0000");
    if (kind === "a") ampRefused = true;
    else if (kind === "p" || kind === "b") refused.add(name);
  }
  const ampNotes = [...ampFaults(amp), ...(ampRefused ? ["A change to the amp was refused."] : [])];
  const tracks = new Map(((model && model.tracks) || []).map((t) => [t.name, t]));
  const out = {};
  for (const tab of tabs) {
    const notes = [];
    if (tab.id === ampOn || (ampOn === null && !tab.master)) notes.push(...ampNotes);
    const track = tracks.get(tab.id);
    for (const unit of track ? track.units : []) {
      if (refused.has(unit.name)) notes.push(`A change to ${unit.name} was refused.`);
    }
    out[tab.id] = notes.length ? notes.join(" ") : null;
  }
  return out;
}

// A plugin's row. Bypassed, it is minimised: a slim row with its name, its
// switch and its mono badge. `peeked` is a bypassed row the player opened to
// set it up without switching it on; `expanded` is the row showing all its
// controls (the name's button, as before #135). `bypassed` is true, false,
// or null where Sushi did not say, which is drawn as on: nothing is hidden
// on a guess.
//   minimised: the slim row.
//   all: every control is drawn (what the row holds under the slim one too,
//     so a bypass arriving only hides it and never rebuilds a control).
//   opened: what the name's button says of itself (aria-expanded): for a
//     bypassed row, that it is open at all; for one that is on, that all its
//     controls show.
export function unitRow(bypassed, { expanded = false, peeked = false } = {}) {
  const minimised = bypassed === true && !peeked;
  return { minimised, all: expanded, opened: bypassed === true ? peeked : expanded };
}

// The name's button, tapped. On a bypassed row it opens it, with all its
// controls, and closes it again to the slim row; on one that is on it shows
// all the controls or the main ones, as it did.
export function unitTapped(bypassed, { expanded = false, peeked = false } = {}) {
  if (bypassed === true) return peeked ? { expanded: false, peeked: false } : { expanded: true, peeked: true };
  return { expanded: !expanded, peeked: false };
}

// A bypass seen to change, from this page or anywhere (a pedal, another
// device). Switched on, the row opens as it was left, and is no longer one
// peeked into. Switched off, it minimises, unless the player is in the
// middle of it (`inUse`: a knob being dragged, a value half typed): that
// row stays open under their hand, as one tapped open does, until they close
// it or change tab.
export function bypassSeen(bypassed, { expanded = false, peeked = false } = {}, inUse = false) {
  if (bypassed === true) return { expanded, peeked: peeked || !!inUse };
  return { expanded, peeked: false };
}

// The tab changed: what was peeked into closes (`unitTapped`'s close).
export function tabLeft(expanded, peeked) {
  return { expanded: [...expanded].filter((name) => !peeked.includes(name)), peeked: [] };
}

// ---------------------------------------------------------------- the Library drawer (#130)
// Adding a plugin from the library, and taking one out. The agent decides
// (POST /live/add, /live/remove); these say what the drawer lists, where an
// add goes, what it is called, and what it would do to the chain's width,
// before anything is sent.

// The mono badge's own words, the same in the drawer as on a row.
export const MONO_TITLE = "mono: processes the left side and its output is copied to both";

// The library's plugins by kind, in the drawer's order (the answer's `kinds`,
// library.KINDS), then "Other" for every plugin whose bundle declares none of
// them or was not read: a kind is the plugin's own LV2 class, never a guess
// from its name. Within a group, by name. A group with nothing in it is left
// out. `library` is GET /library's body.
export function libraryGroups(library) {
  const kinds = (library && library.kinds) || [];
  const known = new Set(kinds.map((k) => k.id));
  const groups = [...kinds.map((k) => ({ id: k.id, label: k.label, entries: [] })), { id: null, label: "Other", entries: [] }];
  const byId = new Map(groups.map((g) => [g.id, g]));
  for (const entry of (library && library.plugins) || []) {
    byId.get(known.has(entry.kind) ? entry.kind : null).entries.push(entry);
  }
  const byName = (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) || a.uri.localeCompare(b.uri);
  for (const g of groups) g.entries.sort(byName);
  return groups.filter((g) => g.entries.length);
}

// Whether an entry is what the search asks for: every word of it in the
// plugin's name, its bundle or its group's name, in any case and order.
export function libraryMatch(entry, groupLabel, query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  const text = [entry.name, entry.bundle || "", groupLabel || ""].join(" ").toLowerCase();
  return words.every((w) => text.includes(w));
}

// What an entry shows: its name, its bundle (or that it is not in plugins/),
// a mono badge where plugin-ports.txt gives it one output, and why it cannot
// be added where the library says so (the amp). Anything else is sent, and
// the agent's refusal shown in place: the agent is what decides.
export function libraryEntry(entry) {
  const audio = entry.audio || null;
  return {
    uri: entry.uri, name: entry.name,
    bundle: entry.bundle || "not in plugins/",
    mono: !!audio && audio.out === 1,
    width: !audio ? null : audio.out === 1 ? "mono" : audio.out >= 2 ? "stereo" : null,
    addable: entry.addable !== false,
    why: entry.addable === false ? entry.why_not || "the library says it cannot be added" : null,
  };
}

// The places an add can go on a track's chain: at the start, and after each
// plugin shown (a mono plugin's copy is not shown, and the agent puts what
// follows it past the copy). The start is `first` and not `after`, since
// nothing precedes the first plugin; the agent keeps the master's return in
// front of it. `index` is where the new plugin lands among the units.
export function addSlots(track) {
  if (!track) return [];
  return [
    { key: "\u0000start", first: true, after: null, index: 0, label: "Add at the start…" },
    ...track.units.map((u, i) => ({ key: u.name, first: false, after: u.name, index: i + 1, label: `Add after ${u.name}…` })),
  ];
}

// A processor name from a plugin's, as the agent derives one
// (agent.derive_name): lower case, each run of anything but a-z and 0-9 one
// "_", at most 64 characters.
export function deriveName(pluginName) {
  const name = String(pluginName || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return name.slice(0, 64).replace(/_+$/, "");
}

// The name an add sends, or null to let the agent name it. The agent names
// a plugin from its library name (`deriveName`) when none is given, and
// refuses (409) a name already on the rig: Sushi's names are one namespace,
// tracks included, and a mono plugin's copy (`<name>_to_stereo`) needs its
// own. So the derived name when it is free, and sent as null; else that name
// and a number (`mda_delay_2`), the first free. `live` is GET /live's body;
// `also`, names taken that it may not show yet (this page's adds in flight).
// `shown` is the name either way, for the slot to say what is being added.
export function addName(entry, live, also = []) {
  const taken = new Set(also);
  for (const [track, chain] of Object.entries((live && live.tracks) || {})) {
    taken.add(track);
    for (const name of chain) taken.add(name);
  }
  const mono = libraryEntry(entry).mono;
  const ok = (name) => NAME_RE.test(name) && !name.endsWith(STEREO_SUFFIX)
    && !taken.has(name) && !(mono && taken.has(name + STEREO_SUFFIX));
  let base = deriveName(entry.name);
  // The suffix is the compiler's, which no plugin of the player's may end in.
  if (base.endsWith(STEREO_SUFFIX)) base = base.slice(0, -STEREO_SUFFIX.length).replace(/_+$/, "");
  if (!NAME_RE.test(base)) base = "plugin";
  if (ok(base) && base === deriveName(entry.name)) return { name: null, shown: base };
  if (ok(base)) return { name: base, shown: base };
  for (let n = 2; ; n++) {
    const suffix = `_${n}`;
    const name = base.slice(0, 64 - suffix.length).replace(/_+$/, "") + suffix;
    if (ok(name)) return { name, shown: name };
  }
}

// The request an add from `slot` (`addSlots`) makes.
export function addCall(track, slot, entry, name) {
  const json = { track, uri: entry.uri };
  if (name) json.name = name;
  if (slot.first) json.first = true;
  else json.after = slot.after;
  return { path: "/live/add", json };
}

// What adding `entry` at `slot` would do to the chain's stereo width, said
// before it is added (#119: Sushi gives a mono plugin channel 0 alone): a
// mono plugin after a stereo one collapses that one's width, and a stereo
// plugin put in front of a mono one has its own collapsed there. Each is a
// width collapsed that is not collapsed now; null when there is none. The
// words are the rack's badge's (`monoNote`).
export function addWarning(track, slot, entry, shown = "the new plugin") {
  if (!track || !slot) return null;
  const view = libraryEntry(entry);
  const added = { name: shown, mono: view.mono, width: view.width, added: true };
  const units = [...track.units.slice(0, slot.index), added, ...track.units.slice(slot.index)];
  const before = new Map(track.units.map((u, i) => [u.name, monoNote(track.units, i)]));
  for (let i = 0; i < units.length; i++) {
    const note = monoNote(units, i);
    if (!note || !note.warn) continue;
    if (units[i].added) return { warn: true, text: note.title };
    const was = before.get(units[i].name);
    if (!was || !was.warn) return { warn: true, text: `${units[i].name} comes after it and collapses its stereo width` };
  }
  return null;
}

// Why the drawer cannot add anything now, or null: no rig to add to (the
// agent's routes need a running rig, and a command running holds them), the place it was opened for gone (the
// plugin it was to follow removed, here or from elsewhere), or the lock.
// The drawer still opens and lists everything, to browse.
export function addBlocked({ health, track, slot, locked }) {
  if (!health || !health.ok) return "The agent is not answering, so nothing can be added.";
  const rig = health.body.rig || {}, agent = health.body.agent || {};
  if (agent.busy) return `${agent.busy} is running: adding waits until it is done.`;
  if (!rig.running) return "No rig is running, so nothing can be added: start one under RIGS.";
  if (!track) return "That chain is not on the rig now: close this and open another tab's.";
  if (slot && !slot.first && !track.units.some((u) => u.name === slot.after)) {
    return `${slot.after} is no longer on ${track.name}: close this and choose another place.`;
  }
  return lockRefusal(locked, "add");
}

// What an add's answer says, for its slot: the plugin added, or the agent's
// reason for refusing it, shown in place.
export function addOutcome(shown, answer) {
  if (answer.ok) return { ok: true, name: answer.body.args ? answer.body.args.name : shown };
  // The agent's words for a name taken tell its client to give one, which
  // the page has already done: what the player can do is try again.
  if (answer.code === "name_taken") {
    return { ok: false, reason: `Not added: the name ${shown} was taken on the rig as this was sent, from somewhere else. Try again: another is chosen for you.` };
  }
  return { ok: false, reason: `Not added: ${answer.reason}` };
}

// The request a remove makes, and whether a unit offers one: only a
// player's plugin, never the compiler's send, return or copy to stereo (the
// agent refuses them too), none of which is a unit on the rack.
export function removeCall(name) {
  return { path: "/live/remove", json: { processor: name } };
}

export function removable(unit) {
  return !!unit && unit.role === null && !isCopy(unit.name, null);
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
    else if (op === "parameters") {
      // Several of one processor's at once (a profile's control, #154): each
      // is a parameter of its own here, as if set one by one.
      for (const parameter of Object.keys(a.parameters || {})) {
        const was = e.previous && typeof e.previous === "object" ? e.previous[parameter] : null;
        note(`p\u0000${a.processor}\u0000${parameter}`, "parameter", { processor: a.processor, parameter }, was ?? null, e);
      }
    }
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
    case "parameters": return `${a.processor} · ${Object.keys(a.parameters || {}).join(", ")}`;
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

// The stage's rig dropdown (#182), in place of its list: the picker's rows
// (`rigPicker`'s `view`) as a native select's options, the running rig
// selected, else the one chosen to start, else a prompt. It stands in the
// stage's title, so the prompt is the screen's title (`rigStatus`'s,
// `statusTitle`: "Rig down", "Agent not answering", a rig running from
// outside config/rigs/); `title` is its tooltip, the whole name, since a
// long one is cut short on a phone. Off while locked, while a command runs
// (this page's or another's), with no agent or with no rigs. The actions
// shown under it are the picker's less "Switch to", which choosing in the
// dropdown does (`rigSelected`).
export function rigSelect(view, health, locked, statusTitle = null) {
  const agentUp = !!(health && health.ok);
  const rig = agentUp ? health.body.rig || {} : {};
  const running = rig.running ? rig.config_name || null : null;
  const listed = !!running && view.rows.some((r) => r.name === running);
  const chosen = view.rows.find((r) => r.chosen);
  const value = listed ? running : !running && chosen ? chosen.name : "";
  const why = locked ? lockRefusal(true, "stage_switch") : !agentUp ? view.problem : view.busy ? `${view.busy}: wait for it to finish.` : null;
  const prompt = value ? null : running ? `${running} (not in config/rigs/)` : statusTitle || "Choose a rig…";
  const shown = value || prompt;
  return {
    value,
    running,
    prompt,
    title: why ? `${shown}. ${why}` : shown,
    options: view.rows.map((r) => ({ value: r.name, label: r.name })),
    disabled: locked || !agentUp || !!view.busy || !view.rows.length,
    why,
    actions: view.actions.filter((a) => a.op !== "switch"),
  };
}

// What choosing `name` in the dropdown does: switch to it, with a rig up
// (the picker's Switch: live where it can, a restart where it must), or,
// with none, choose it for Start. Nothing for the rig already shown, or
// while the dropdown is off.
export function rigSelected(select, name) {
  if (!name || select.disabled || name === select.value) return null;
  return select.running ? { op: "switch", name } : { op: "choose", name };
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

// ---------------------------------------------------------------- deleting a rig (#129)
// POST /rigs/<rig>/delete moves the file into config/rigs/.deleted/, out of
// the picker and the Program Change list, and POST /rigs/<rig>/restore puts
// it back. The agent decides (the running rig is refused, a name must be
// plain); these say what the rack's picker offers and what an answer means.

// Which row is the running rig's file: the agent's own answer in GET /rigs
// (`running`), the rig it refuses to delete, never a guess from a name.
export function runningListed(rigsAnswer) {
  return rigsAnswer && rigsAnswer.ok && typeof rigsAnswer.body.running === "string" ? rigsAnswer.body.running : null;
}

// What the page reads of the run to know when to ask GET /rigs again: the
// running rig can change under the page (a pedal, another page).
export function runKeyOf(health) {
  const rig = health && health.ok ? health.body.rig || {} : {};
  return JSON.stringify([!!rig.running, rig.rig || null, rig.config_name || null, rig.started_at || null]);
}

// Each row's Delete: off, with the reason, on the running rig's row.
export function deleteAction(name, running, busy = false) {
  if (name === running) {
    return { name, disabled: true, label: "Delete", aria: `Delete ${name}: not while it is the running rig`,
      why: `${name} is the running rig, so it cannot be deleted: switch to another rig, or stop it, first.` };
  }
  return { name, disabled: !!busy, label: "Delete", aria: `Delete ${name}…`, why: null };
}

export function deleteCall(rig, dryRun = false) {
  return { path: `/rigs/${encodeURIComponent(rig)}/delete`, json: dryRun ? { dry_run: true } : {} };
}

export function restoreCall(rig, entry = null) {
  return { path: `/rigs/${encodeURIComponent(rig)}/restore`, json: entry ? { entry } : {} };
}

// "electric-delay moves from program 6 to 5", for each rig whose number
// moves; none when none does.
export function programMoves(programs) {
  return programs && Array.isArray(programs.moved)
    ? programs.moved.map((m) => `${m.rig} moves from program ${m.from} to ${m.to}`) : [];
}

// What git holds of the file, in a sentence: a committed rig comes back
// with a `git checkout` too, a rig never committed only from the bin.
function binGitText(git, note) {
  if (!git) return `${note || "What git holds of it is not known"}.`;
  if (!git.committed) return "It is not committed, so the bin holds the only copy.";
  return `It is committed, so \`${git.restore}\` restores it too`
    + (git.same === false ? ", as it was last committed: the changes since are only in the bin." : ".");
}

// A refusal that may not hold a moment later: the agent busy with another
// command, or not answering. Trying again is offered, in place.
function passing(answer) {
  return answer.code === "busy" || !answer.status;
}

// The confirm, from the agent's dry run (`answer`, null while it is asked).
export function deleteConfirm(rig, answer) {
  if (!answer) return { rig, loading: true, go: `Delete ${rig}` };
  if (!answer.ok) return { rig, refused: answer.reason, code: answer.code || null, retry: passing(answer), go: `Delete ${rig}` };
  const b = answer.body || {};
  const moves = programMoves(b.programs);
  return {
    rig, file: b.file, title: `Delete ${b.file}?`,
    lines: [
      "It moves to config/rigs/.deleted/, out of the picker and the Program Change list, and Undo puts it back. Nothing is committed.",
      binGitText(b.git, b.git_note),
    ],
    moves,
    programsNote: moves.length ? "Program Change numbers follow config/rigs by file name, so a pedal preset set for a number then selects the rig listed against it after." : null,
    go: `Delete ${rig}`,
  };
}

// What a delete said: `{ok, title, text, binned}` with what Undo needs, or
// `{ok: false, title, reason, code, retry}`.
export function deletedOutcome(rig, answer) {
  if (!answer.ok) return { ok: false, title: `Not deleted: ${rig}`, reason: answer.reason, code: answer.code || null, retry: passing(answer) };
  const b = answer.body || {};
  const moves = programMoves(b.programs);
  return {
    ok: true, title: `Deleted ${rig}`,
    text: `${b.file} is now ${b.binned.file}. ${binGitText(b.git, b.git_note)}${moves.length ? ` ${moves.join("; ")}.` : ""}`,
    binned: { rig, entry: b.binned.entry, file: b.binned.file },
  };
}

// What an Undo said. Refused, `keep` is whether its Undo stays offered:
// only for a refusal that may pass (busy, no answer). A file of that name
// there again is said once, with the reason, and the Undo goes: the rig is
// in the picker again, and the binned file stays in config/rigs/.deleted/.
export function restoreOutcome(rig, answer) {
  if (!answer.ok) {
    return { ok: false, title: `Not put back: ${rig}`, reason: answer.reason, code: answer.code || null, keep: passing(answer) };
  }
  const b = answer.body || {};
  const moves = programMoves(b.programs);
  return { ok: true, title: `Put back ${rig}`, text: `${b.file} is back from ${b.from}.${moves.length ? ` ${moves.join("; ")}.` : ""}` };
}

// The Undos offered: a few, newest first, held by this page only, for
// NOTICE_MS each (`noticesLeft`). Until #189 they were kept in this browser
// across reloads (`sushi-rig.binned`), and never left; that record is
// removed when the page loads and never read. A deleted rig is still in
// config/rigs/.deleted/ after its Undo has gone, to move back by hand.
export const KEEP_UNDOS = 5;
export const RETIRED_KEYS = ["sushi-rig.binned"];

export function addOffer(offers, binned) {
  return [binned, ...offers.filter((o) => o.entry !== binned.entry)].slice(0, KEEP_UNDOS);
}

export function dropOffer(offers, entry) {
  return offers.filter((o) => o.entry !== entry);
}

// The Undos still worth offering, by what the agent says is in its bin now
// (GET /rigs/bin): an entry gone from it, or whose rig is in the picker
// again, is not. Nothing is offered until the agent has said.
export function checkedOffers(offers, binAnswer) {
  if (!binAnswer || !binAnswer.ok) return [];
  const there = new Map((binAnswer.body.entries || []).map((e) => [e.entry, e]));
  return offers.filter((o) => there.has(o.entry) && there.get(o.entry).rig === o.rig && !there.get(o.entry).back);
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
// -60 to +6 dB for a channel and -60 to 0 dB for the master (#137: its gain
// comes after the limiter), and below that one notch of -∞, which is the gain's own
// floor (-120 dB on the -120 to +24 range a track's gain and a send's
// have: 10^-6, below hearing), not a mute parameter, so one parameter is
// the volume, one undo puts it back and one line of the file holds it.

export const VOLUME_MIN_DB = -60;
export const VOLUME_MAX_DB = 6;
// The master's top (#137, rigfile.MASTER_LEVEL_MAX): unity. Sushi applies the
// master's gain after its limiter, so above 0 dB it would lift what the
// limiter let through; the agent refuses more. A channel's volume keeps +6.
export const MASTER_VOLUME_MAX_DB = 0;
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

// A dB value as the fader may hold it: clamped to -60 .. `max` (+6, or the
// master's 0), or -∞.
export function volumeClamp(db, max = VOLUME_MAX_DB) {
  if (db === -Infinity) return -Infinity;
  if (!isNumber(db)) return null;
  return clampTo(db, VOLUME_MIN_DB, max);
}

// The normalised value that sets the master to `db` (-∞ is the floor).
export function volumeValueOf(ctrl, db) {
  return db === -Infinity ? 0 : normalisedOf(ctrl, clampTo(db, ctrl.min, ctrl.max));
}

// What a fader says of a level above its top (#137: a master from before
// the cap, at what its file said), or null: it is not set from here, and
// only coming down moves it.
export function overTopNote(db, max) {
  if (!isNumber(db) || db <= max + 1e-6) return null;
  return `at ${volumeText(db)}, over the ${volumeText(max)} it now stops at (the rig was started before the cap): ` +
    `up does nothing, and any move down brings it to ${volumeText(max)} or below`;
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
export function volumeSlider(db, max = VOLUME_MAX_DB) {
  if (db === null || db === undefined || db === -Infinity) return VOLUME_OFF_SLIDER;
  return clampTo(db, VOLUME_MIN_DB, max);
}

export function volumeOfSlider(x, max = VOLUME_MAX_DB) {
  return x < VOLUME_MIN_DB - 0.5 ? -Infinity : clampTo(x, VOLUME_MIN_DB, max);
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
// all, except down below -60, which is -∞. `{db, held}`, and `unchanged`
// where a step up from above the range leaves it where it is: then nothing
// is sent (review of PR #172: one notch up sent the clamped top, and a master
// at +6 dB from before the cap dropped 6 dB on a scroll up). With `hard`, a
// top nothing above may be set (the master's 0 dB, #137), a step down from
// above it goes to the top, the first level the agent will take.
export function volumeStepped(db, steps, fine = false, held = null, max = VOLUME_MAX_DB, hard = false) {
  if (db === -Infinity || db === null || db === undefined) {
    return steps > 0 ? { db: VOLUME_MIN_DB, held: null } : { db: -Infinity, held: null };
  }
  const size = VOLUME_STEP_DB / (fine ? FINE_DIVISOR : 1);
  const next = db + steps * size;
  if (db > max) {
    if (steps > 0) return { db, held: null, unchanged: true };
    return { db: hard ? max : next, held: null };
  }
  if (db < VOLUME_MIN_DB) return steps > 0 ? { db: next, held: null } : { db: -Infinity, held: null };
  if (next < VOLUME_MIN_DB - 1e-9) return { db: -Infinity, held: null };
  const kept = clampTo(next, VOLUME_MIN_DB, max);
  return fine ? { db: kept, held: null } : volumeSticky(kept, held);  // fine is for the values between
}

// What is typed into the master's field: a number of dB (with or without
// the unit), or -∞ as "-inf", "−∞", "mute" or "off"; clamped to -60 .. `max`
// (+6, or the master's 0). Exact, never snapped.
export function parseVolume(text, max = VOLUME_MAX_DB) {
  const raw = String(text ?? "").trim();
  const t = raw.replace(/[−–]/g, "-").replace(/,/g, ".").replace(/\s+/g, "").replace(/db$/i, "");
  if (/^(-(inf|infinity|∞)|mute|off)$/i.test(t)) return { ok: true, db: -Infinity, clamped: false };
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return { ok: false, why: raw ? `“${raw}” is not a level in dB` : "Type a level in dB." };
  const db = Number(t);
  const kept = volumeClamp(db, max);
  return { ok: true, db: kept, clamped: kept !== db };
}

// The stage's −0.5 dB and +0.5 dB buttons (#182), for a phone, where the
// fader is hard to move by half a dB. One press is one step of exactly
// VOLUME_STEP_DB from the level rounded to 0.1 dB, never held to whole dB
// as a wheel notch is: Sushi holds a gain as a 32-bit float, so 0 dB reads
// back as −0.0000029 dB, and steps taken from what it reads would drift
// and, at the master's top, send a step to where it already is (review of
// PR #184). Held VOLUME_HOLD_MS it repeats every VOLUME_REPEAT_MS, at most
// VOLUME_REPEAT_MAX times a hold, so a release the page never hears cannot
// run a volume to its end. The ends are `volumeStepped`'s: up from -∞ is
// -60, down from -60 is -∞, the top (`max`) is never passed, and over it
// (#137) up does nothing and down goes to the top (`hard`, the master) or
// a step toward it (a channel). `{db, unchanged}`: with `unchanged`
// (within VOLUME_SAME_DB of where it is), nothing is to be sent.
export const VOLUME_HOLD_MS = 400;
export const VOLUME_REPEAT_MS = 100;
export const VOLUME_REPEAT_MAX = 12;
export const VOLUME_SAME_DB = 1e-4;
export function volumeNudged(db, direction, max = VOLUME_MAX_DB, hard = false) {
  if (db === null || db === undefined || Number.isNaN(db)) return { db: null, unchanged: true };
  const from = isNumber(db) ? Math.round(db * 10) / 10 : db;
  const edge = volumeStepped(from, direction, false, null, max, hard);
  let out = edge.db;
  if (!edge.unchanged && isNumber(from) && from >= VOLUME_MIN_DB && from <= max) {
    const next = Number((from + Math.sign(direction) * VOLUME_STEP_DB).toFixed(1));
    out = next < VOLUME_MIN_DB - 1e-9 ? -Infinity : Math.min(max, next);
  }
  const same = out === db || (isNumber(out) && isNumber(db) && Math.abs(out - db) < VOLUME_SAME_DB);
  return { db: out, unchanged: !!edge.unchanged || same };
}

// ---------------------------------------------------------------- the lock
// Sound changes (a knob, a bypass) are allowed locked; structure (add, remove,
// move) is refused, with the reason (the UX doc's rule 4). This is the page's
// lock: the agent has no performance lock yet (#29), so another client is
// not held by it.
const STRUCTURAL = { move: "reordering", add: "adding a plugin", remove: "removing a plugin",
  load_preset: "loading a channel preset", pedal: "assigning a pedal" };

export function lockRefusal(locked, op) {
  if (locked && op === "amp_model") {
    return "Locked: changing the amp's model cuts the sound while the amp restarts, which the lock refuses. Unlock to change it.";
  }
  // The stage's own (#182): its volumes and its rig dropdown, which a thumb
  // knocks mid-song. The rack's volumes and picker are not behind the lock.
  if (locked && op === "stage_volume") return "Locked: the stage's volumes do not move while locked, so a stray thumb cannot change them. Unlock to set them.";
  if (locked && op === "stage_switch") return "Locked: switching rigs is off while locked. Unlock to switch.";
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
    // Said on the picker too, as `why` when nothing more pressing is; kept
    // apart so the amp's tab can mark it (`ampFaults`).
    listProblem: models && !models.ok ? models.reason : null,
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
// `run()` names the rig the page shows: a change is sent only to the rig it
// was made on, and one still waiting when that changes is dropped, never
// sent to the next rig (#135's review), whose plugin may share the name.
export class Sender {
  constructor(post, { pollSeq = () => 0, onFailure = () => {}, onSent = () => {}, run = () => null } = {}) {
    this.post = post;
    this.pollSeq = pollSeq;
    this.run = run;
    this.onFailure = onFailure;
    this.onSent = onSent;
    this.state = new Map();
  }

  change(key, value, body) {
    let s = this.state.get(key);
    if (!s) { s = { want: value, body, inFlight: false, confirmedAt: null }; this.state.set(key, s); }
    s.want = value; s.body = body; s.confirmedAt = null; s.run = this.run();
    if (!s.inFlight) this._send(key, s);
  }

  async _send(key, s) {
    if (s.run !== this.run()) { this.state.delete(key); return; }  // made on a rig no longer the one shown
    s.inFlight = true;
    const value = s.want;
    const result = await this.post(s.body(value));
    s.inFlight = false;
    if (this.state.get(key) !== s) return;
    if (!result.ok) {
      // Said either way. A newer value asked for while this one was in
      // flight is still sent (review of PR #159: it was dropped, and the
      // control showed the rig's value with the player's last move lost);
      // with none, the control goes back to showing the rig's.
      this.onFailure(key, result);
      if (this.state.get(key) !== s) return;
      if (s.want !== value) { this._send(key, s); return; }
      this.state.delete(key);
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
  // The take's folder, where the takes are looked at now that the rack does
  // not list them (#189); a take with problems is not ok, so it is shown as
  // the refusal colour, with `take check`'s first problem. A stop that
  // failed (502, the take stopped) is never called whole: its verdict is
  // said only when `take check` found problems, and its reason only when it
  // is not the read-back failure a problem already says (`summary_error`).
  const problems = (body.problems || []).length;
  const folder = typeof body.folder === "string" && body.folder ? body.folder : null;
  if (!answer.ok) {
    return { ok: false, title: `Recorded ${body.name}`, text: problems ? takeVerdict(body) : null,
      reason: problems && body.summary_error ? null : answer.reason, folder };
  }
  return { ok: !problems, title: `Recorded ${body.name}`, text: takeVerdict(body), reason: null, folder };
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

// The rack's line naming the takes folder (#189), from /health's
// `agent.takes`: the folder, and Open folder only when the agent says this
// page is on its own machine (`can_open`: the request came from a loopback
// peer, the one POST /takes/open serves), so a phone sees the path and no
// button. `open` is this page's open in flight (`pending`) and what the last
// one said (`result`, `openFolderOutcome`).
export function takesFolder(health, open = {}) {
  const takes = health && health.ok ? (health.body.agent || {}).takes : null;
  const result = open.result || null;
  if (!takes || typeof takes.dir !== "string" || !takes.dir) return { dir: null, button: null, result };
  return {
    dir: takes.dir,
    button: takes.can_open === true ? { label: open.pending ? "Opening…" : "Open folder", disabled: !!open.pending } : null,
    result,
  };
}

export const OPEN_FOLDER_CALL = { path: "/takes/open", json: {} };

// What an open said: the agent's own sentence, or its reason for refusing
// (no opener installed, no folder yet, the opener's failure), said as it is.
export function openFolderOutcome(answer) {
  const body = answer.body || {};
  if (!answer.ok) return { ok: false, title: "Not opened", reason: answer.reason, code: answer.code || null };
  return { ok: true, title: "Opened the takes folder", text: body.said || null };
}

// ---------------------------------------------------------------- notices that go by themselves (#189)
// What says something worked leaves after NOTICE_MS: a rig deleted or put
// back, its Undo offer, the takes folder opened, a whole take stopped. A
// refusal stays until it is dismissed, so an error is never gone unread.
// Each notice carries `at`, the page's monotonic clock (`performance.now`)
// when it was shown, so a wall clock stepped back cannot hold one; the page
// sets one timer for the soonest due (`nextNoticeIn`) and, when it fires,
// drops what is done (`noticesLeft`) and sets it again for the rest.
export const NOTICE_MS = 15000;

// Whether notice `n` (`{ok, at}`) has had its time: only one that worked, and
// only once it has been shown NOTICE_MS.
export function noticeDone(n, now) {
  return !!n && n.ok === true && typeof n.at === "number" && now - n.at >= NOTICE_MS;
}

// How long until the soonest of `notices` (each `{ok, at}`, or null) has had
// its time, in ms and never below 0; null when none of them will go.
export function nextNoticeIn(notices, now) {
  const due = notices.filter((n) => !!n && n.ok === true && typeof n.at === "number")
    .map((n) => Math.max(0, NOTICE_MS - (now - n.at)));
  return due.length ? Math.min(...due) : null;
}

// The rig picker's notices still to show: the delete's or Undo's outcome, and
// the Undos offered, each gone once its time is up (an offer is one that
// worked, so it goes as an outcome that worked does), except the Undo in
// flight (`restoring`), which stays until its answer says what happened.
export function noticesLeft({ said = null, offers = [], restoring = null }, now) {
  return {
    said: noticeDone(said, now) ? null : said,
    offers: offers.filter((o) => o.entry === restoring || !noticeDone({ ok: true, at: o.at }, now)),
  };
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
// And the channel presets of config/channels/ (#127, `presetsAnswer`, GET
// /presets), first, as a group of their own; one that cannot be loaded is
// there and off, with why.
export function sourceChoices(rigsAnswer, byRig, presetsAnswer = null) {
  const rigs = rigsAnswer && rigsAnswer.ok ? rigsAnswer.body.rigs : [];
  const groups = rigs.map((rig) => {
    const answer = byRig[rig];
    const channels = answer && answer.ok ? answer.body.channels : [];
    return {
      rig, label: answer && !answer.ok ? `${rig} (could not be read)` : rig,
      problem: answer && !answer.ok ? answer.reason : null,
      options: channels.map((c) => ({
        rig, channel: c.name, value: `${rig}\u0000${c.name}`, disabled: false, title: null,
        label: `${rig} · ${c.name} (${c.plugins.length} plugin${c.plugins.length === 1 ? "" : "s"})`,
      })),
    };
  });
  const presets = presetChoices(presetsAnswer);
  if (!presets.length) return groups;
  return [{ rig: null, label: "Channel presets", problem: null,
    options: presets.map((p) => ({ preset: p.name, value: sourceValue({ preset: p.name }), label: `preset ${p.label}`,
      disabled: !p.ok, title: p.problem })) }, ...groups];
}

function channelJson(c) {
  const out = { name: c.name, input: c.input, width: c.width };
  if (c.from && c.from.preset) out.from = { preset: c.from.preset };
  else if (c.from) out.from = { rig: c.from.rig, channel: c.from.channel };
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
// What the agent said of the amp for each channel a preset started (#127):
// a second guitar on another input is told it has no amp.
function ampNotes(sources) {
  const notes = (sources || []).map((f) => f && f.amp && f.amp.note).filter(Boolean);
  return notes.length ? ` ${notes.map((n) => `${n}.`).join(" ")}` : "";
}

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
    const copied = (body.channels || []).filter((c) => c.from).map((c) => (c.from.preset
      ? `${c.name} from preset ${c.from.preset}` : `${c.name} from ${c.from.rig} · ${c.from.channel}`));
    if (copied.length) text += ` Chains copied: ${copied.join(", ")}.`;
    text += ampNotes((body.channels || []).map((c) => c.from));
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
    if (body.change && body.change.from && body.change.from.preset) {
      text += ` Its chain and level are channel preset ${body.change.from.preset}'s.` + ampNotes([body.change.from]);
    }
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

// ---------------------------------------------------------------- channel presets (#127)
// A channel's sound saved by name in config/channels/ (GET /presets), for
// "Start from" (Add channel, New rig) and a strip's Preset… panel: Save
// channel as…, and Load onto a channel of the running rig.

// The presets as the pickers offer them: each with a label of its own, and
// one that cannot be loaded (it does not parse or compile) offered off, its
// reason as its title.
export function presetChoices(answer) {
  const list = answer && answer.ok ? answer.body.presets || [] : [];
  return list.map((p) => {
    const plugins = p.plugins || [];
    return {
      name: p.name, ok: !!p.ok, problem: p.problem || null, width: p.width || null, plugins,
      amp: p.heard_through_amp || null,
      label: p.ok ? `${p.name} (${plugins.length} plugin${plugins.length === 1 ? "" : "s"}, ${p.width})`
        : `${p.name} (cannot be loaded)`,
    };
  });
}

// "Start from"'s value for a preset: a rig's channel is `rig\u0000channel`,
// and no rig's name starts with \u0001.
export const PRESET_MARK = "\u0001";

export function sourceValue(from) {
  if (!from) return "";
  if (from.preset) return `${PRESET_MARK}${from.preset}`;
  return `${from.rig}\u0000${from.channel}`;
}

export function sourceFrom(value) {
  if (!value) return null;
  if (value.startsWith(PRESET_MARK)) return { preset: value.slice(PRESET_MARK.length) };
  const [rig, channel] = value.split("\u0000");
  return { rig, channel };
}

// The width a channel takes from the preset it starts from: the preset's
// preferred one. Where the input cannot give it, the form says why and
// Create (or Add) waits, as for any width (the compiler's refusal, said
// before anything is written).
export function presetWidth(presetsAnswer, from, width) {
  if (!from || !from.preset) return width;
  const found = presetChoices(presetsAnswer).find((p) => p.name === from.preset);
  return found && found.width ? found.width : width;
}

// Why a preset's name will not do, as a hint (the agent decides), and
// whether a preset has it already, which Save channel as… replaces only
// when asked.
export function checkPresetName(name, presetsAnswer) {
  const n = typeof name === "string" ? name : "";
  if (!n) return { problem: "Name the preset: it is the file config/channels/<name>.yaml.", taken: false };
  if (!NAME_RE.test(n)) return { problem: "A preset's name is lowercase letters, digits, '-' and '_', starting with a letter or digit.", taken: false };
  const taken = presetChoices(presetsAnswer).some((p) => p.name === n);
  return { problem: null, taken };
}

export function presetSaveCall(channel, name, replace = false) {
  return { path: `/rig/channels/${encodeURIComponent(channel)}/save-as`, json: replace ? { name, replace: true } : { name } };
}

export function presetLoadCall(channel, preset, { discard = false, dropMidi = false } = {}) {
  const json = { preset };
  if (discard) json.discard = true;
  if (dropMidi) json.drop_midi = true;
  return { path: `/rig/channels/${encodeURIComponent(channel)}/load`, json };
}

// What a save or a load said: `{ok, title, text, notes, offer}`, the
// agent's reason as it gave it. A refusal that a second tap answers offers
// it: a name taken (replace it), unsaved work (drop it), MIDI mappings a load
// would orphan (drop them, each named). `asked` is what the call already
// said yes to, so a second refusal keeps the first answer's yes.
export function presetOutcome(op, channel, name, answer, asked = {}) {
  const body = answer.body || {};
  if (op === "save") {
    if (!answer.ok) {
      return { ok: false, title: `Not saved: ${channel} as ${name}`, reason: answer.reason, notes: [],
        offer: answer.code === "taken" ? { label: `Replace ${name}`, replace: true } : null };
    }
    const p = body.preset || {};
    const notes = [];
    if (p.heard_through_amp) notes.push(`Saved with the amp in front of it; the preset does not carry the amp, and says so where it is loaded without one.`);
    return { ok: true, title: `Saved ${channel} as ${name}`, notes, offer: null,
      text: `Wrote ${body.file}${body.how === "updated" ? " in place, keeping its comments" : ""}: ${channel} as it plays now, `
        + `${(p.plugins || []).length} plugin${(p.plugins || []).length === 1 ? "" : "s"}. The rig is unchanged, and nothing is committed.` };
  }
  if (!answer.ok) {
    const out = { ok: false, title: `Not loaded: ${name} onto ${channel}`, reason: answer.reason, notes: [], offer: null };
    if (body.midi_note) out.notes.push(`${body.midi_note}.`);
    if (answer.code === "unsaved") out.offer = { ...asked, label: "Load anyway, dropping the unsaved changes", discard: true };
    if (answer.code === "midi") {
      const mappings = body.mappings || [];
      out.notes = mappings.map((m) => `${m.control}: ${m.binding} ${m.target}. ${m.why}.`);
      out.offer = { ...asked, label: `Load, dropping ${mappings.length} MIDI mapping${mappings.length === 1 ? "" : "s"}`, dropMidi: true };
    }
    return out;
  }
  const notes = [];
  if (body.amp && body.amp.note) notes.push(`${body.amp.note}.`);
  if (body.width) notes.push(`${body.width}.`);
  for (const f of body.followed || []) notes.push(`MIDI ${f.control} follows its plugin: ${f.from} is now ${f.to}.`);
  for (const d of body.dropped || []) notes.push(`Dropped MIDI ${d.control} (${d.binding} ${d.target}).`);
  if (body.kept_before && body.kept_before.id) notes.push("The rig's file as it was before is kept: Revert… goes back to it.");
  if (body.provenance_note) notes.push(`Where the chain came from is not written in the file: ${body.provenance_note}.`);
  if (body.discard && body.discard.note) notes.push(`${body.discard.note}.`);
  const how = body.how === "live" ? "Played live, without a restart."
    : `Played by a restart${(body.why || []).length ? ` (${body.why.map((r) => r.detail).join("; ")})` : ""}.`;
  return { ok: true, title: `Loaded ${name} onto ${channel}`, notes, offer: null,
    text: `${how} Wrote ${body.file} in place, with ${name}'s chain under ${channel}'s own names; not committed. `
      + "Undo stops here, as after a switch." };
}

// ---------------------------------------------------------------- the user guide (#167)
// The agent serves docs/guide/ at /help/ (sushi_rig/guide.py). A Help link
// on each screen opens it, and a feature with a guide of its own links to
// that guide, or to the heading in it: page and anchor as docs/guide/ names
// them (the anchor GitHub gives the heading), which test_guide.py holds to
// the guide as served. `from` is the screen the link is on, so the guide's
// "Back to the rig" comes back to it.

export const HELP = {
  guide: { page: "README", label: "Help" },
  stage: { page: "playing-live", label: "Help" },
  stageRigs: { page: "playing-live", anchor: "switch-to-another-rig", label: "About switching rigs" },
  rigs: { page: "building-a-rig", label: "About rigs" },
  newRig: { page: "building-a-rig", anchor: "make-a-new-rig", label: "Help" },
  record: { page: "recording", label: "About recording" },
  takes: { page: "recording", anchor: "find-your-takes", label: "About takes" },
  save: { page: "saving", label: "About saving" },
  presets: { page: "channel-presets", label: "About presets" },
  amp: { page: "shaping-a-sound", anchor: "set-the-amp", label: "About the amp" },
};

// The screens the guide can go back to, by the page's screen (`currentScreen`).
const HELP_FROM = { stage: "stage", rack: "rack", newrig: "new-rig" };

// Where feature `which` of HELP is served, opened from `screen`, and what
// the link says: `{href, label, aria}`. The README is the guide's front
// page, /help/. Unknown features and screens are a mistake to see, not a
// link to guess at: null.
export function helpLink(which, screen) {
  const entry = HELP[which];
  if (!entry) return null;
  const path = entry.page === "README" ? "/help/" : `/help/${encodeURIComponent(entry.page)}`;
  const from = HELP_FROM[screen];
  const href = `${path}${from ? `?from=${from}` : ""}${entry.anchor ? `#${entry.anchor}` : ""}`;
  const aria = entry.label === "Help" ? (entry.page === "README" ? "Help: the user guide" : `Help: ${entry.page.replace(/-/g, " ")}`)
    : `${entry.label}, in the user guide`;
  return { href, label: entry.label, aria };
}

// ---------------------------------------------------------------- the visualiser (#176)
// A screensaver on the laptop: after a while with no pointer, touch or key
// on the page, a MilkDrop visualisation (butterchurn, vendored) fills the
// window and moves with the guitar. The browser hears the guitar itself,
// through PipeWire's PulseAudio server (getUserMedia): nothing is added to
// the signal's path, though the server's capture stream does a little work
// in Sushi's cycle (visualiser.js says what that risks, and what watches
// it). screensaver.js is the DOM, visualiser.js the drawing; these are its
// decisions.

export const VISUALISER = {
  idleDefault: 10,     // seconds without input before it starts
  idleMin: 3,
  idleMax: 600,
  minWidth: 900,       // CSS px: a phone's stage screen is never covered
  fps: 30,             // a frame at most this often: the GPU is shared with nothing, the CPU with Sushi
  maxPixels: 1280 * 800,  // the drawing buffer at most this big, scaled up to the window
  // The machine file's role the guitar comes in on: patchbay.GUITAR_ROLE,
  // held to it by test_ui_visualiser.py.
  guitarRole: "guitar-in",
  // Each look's time when they come in a random order (#196): somewhere in
  // these seconds, never the same twice running.
  lookMin: 30,
  lookMax: 60,
  // A flash or strobe on a loud attack comes at most this often: never
  // closer than this many seconds, nor this many frames (frames are at least
  // 31.3 ms apart at the 30 fps cap, so 16 are at least 0.5 s), so no second
  // ever holds more than two, under the photosensitivity guidance of three
  // (WCAG 2.3.1). At 14 frames a second could hold three (frames 0, 14, 28).
  // Both, since butterchurn's own clock is its estimate of the frame rate,
  // not the wall.
  flashGap: 0.55,
  flashFrames: 16,
};

// The idle time a field was given, in whole seconds within the bounds, or
// null for anything that is not a number.
export function visualiserIdle(value) {
  const n = typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" ? Number(value) : NaN;
  if (!Number.isFinite(n)) return null;
  return Math.min(VISUALISER.idleMax, Math.max(VISUALISER.idleMin, Math.round(n)));
}

// The settings kept in this browser (localStorage), from what was kept:
// `{on, idle, input: {deviceId, label} | null, channel: 0 | 1 | null, look}`,
// `look` one of `lookChoices()`'s values (random unless one was picked).
// On unless turned off; anything unreadable is the default, never a throw.
export function visualiserSettings(text) {
  const out = { on: true, idle: VISUALISER.idleDefault, input: null, channel: null, look: "random" };
  let kept = null;
  try { kept = JSON.parse(text); } catch { return out; }
  if (!kept || typeof kept !== "object" || Array.isArray(kept)) return out;
  if (kept.on === false) out.on = false;
  out.idle = visualiserIdle(kept.idle) ?? out.idle;
  if (kept.input && typeof kept.input.deviceId === "string" && kept.input.deviceId)
    out.input = { deviceId: kept.input.deviceId, label: typeof kept.input.label === "string" ? kept.input.label : "" };
  if (kept.channel === 0 || kept.channel === 1) out.channel = kept.channel;
  out.look = lookChoice(kept.look);
  return out;
}

// Whether /health says a rig is running: the visualiser runs only then.
export function visualiserRigRunning(health) {
  return !!(health && health.ok && health.body && (health.body.rig || {}).running);
}

// A WebGL renderer's name that is software: drawing in it takes the CPU,
// which Sushi needs. Measured 2026-11-18 in Chromium on SwiftShader: 1.6 cores
// of four at 640 by 360, 30 frames a second (tool/spikes/visualiser_audio/).
export function softwareRenderer(name) {
  return typeof name === "string" && /swiftshader|llvmpipe|softpipe|software|basic render/i.test(name);
}

// Whether the visualiser may start now, and if not, why, in words for the
// settings line: `{ok, why}`. `why` is null only when ok. The order is what
// the player can do least about first.
export function visualiserGate(s) {
  const no = (why) => ({ ok: false, why });
  if (!s.on) return no("Off.");
  if (s.stopped) return no(s.stopped);
  if (s.reducedMotion) return no("Off while the system asks for reduced motion.");
  if (!s.secure || !s.media)
    return no("Only on the rig's own laptop, at http://localhost: a browser elsewhere cannot hear the rig.");
  if (s.webgl === false) return no("This browser has no WebGL 2, which the visualiser draws with.");
  if (softwareRenderer(s.renderer))
    return no(`This browser draws WebGL in software (${s.renderer}), which would take the CPU Sushi needs.`);
  if (s.permission === "denied")
    return no("The browser was told not to let this page hear its inputs: allow the microphone for this page in the browser's site settings.");
  if (!(s.width >= VISUALISER.minWidth)) return no(`Only in a window at least ${VISUALISER.minWidth} px wide.`);
  if (!s.running) return no("Starts only while a rig is running.");
  return { ok: true, why: null };
}

// Whether the rig's xruns rose while the visualiser listened: `base` is the
// page's count when the capture was opened, `seen` the latest, each
// `{count, run}` (app.js's, from pw-top; `run` is the rig run they count
// for). The reason to stop, in words for the line; "rebase" when `base`
// cannot be compared (none yet, or another run); null when nothing rose.
// Any rise counts, whatever its cause: the capture stream is in Sushi's
// cycle, and a glitch while it is there is not worth a second.
export function visualiserTripped(base, seen) {
  if (!seen || typeof seen.count !== "number") return null;
  if (!base || typeof base.count !== "number" || base.run !== seen.run) return "rebase";
  if (seen.count <= base.count) return null;
  return `Stopped: the rig glitched while the visualiser was listening (xruns went from ${base.count} to ${seen.count}). `
    + "It stays off until this page is reloaded or the Visualiser box is ticked again.";
}

// Whether `idle` seconds have passed since the last input at `last` (ms).
export function idleDue(last, now, idle) {
  return now - last >= idle * 1000;
}

// Whether a frame is due at `now`, the last having been drawn at `last` (ms),
// at most `fps` a second. A small allowance, so a 60 Hz display's frames at
// 16.7 ms land on every other one rather than drifting.
export function frameDue(last, now, fps = VISUALISER.fps) {
  return last == null || now - last >= 1000 / fps - 2;
}

// The drawing buffer for a window of `width` by `height` CSS px: the window
// itself, or scaled down to `maxPixels`, whole pixels, never under 1.
export function drawSize(width, height, maxPixels = VISUALISER.maxPixels) {
  const w = Math.max(1, Math.round(width)), h = Math.max(1, Math.round(height));
  const scale = Math.min(1, Math.sqrt(maxPixels / (w * h)));
  return { width: Math.max(1, Math.floor(w * scale)), height: Math.max(1, Math.floor(h * scale)) };
}

// Where the guitar is, from GET /machine/roles's roles: the device as the
// browser names it and which of the two channels the browser is given.
// `{device, channel, port}`, or null. The browser's capture is stereo, and
// PipeWire hands it a multichannel input's first two channels in order
// (measured 2026-11-18 on PipeWire 1.0.5: AUX0 left, AUX1 right), so AUX0
// or FL is 0, AUX1 or FR is 1; any other is null, both channels heard.
export function guitarInput(roles) {
  const role = Array.isArray(roles) ? roles.find((r) => r && r.name === VISUALISER.guitarRole) : null;
  const port = role && Array.isArray(role.ports) && typeof role.ports[0] === "string" ? role.ports[0] : null;
  const at = port ? port.lastIndexOf(":") : -1;
  if (at <= 0) return null;
  const name = port.slice(at + 1).replace(/^(capture|input)_/, "").toUpperCase();
  const channel = name === "AUX0" || name === "FL" ? 0 : name === "AUX1" || name === "FR" ? 1 : null;
  return { device: port.slice(0, at), channel, port };
}

// Which input to listen to, from the browser's inputs (enumerateDevices'
// `audioinput`s: `{deviceId, label}`), the one chosen here before
// (`settings.input`, `settings.channel`) and the guitar's (`guitarInput`):
// `{deviceId, label, channel, how}`. A choice made here wins while that
// input is there; then the guitar's device by name (its label, or its label
// with more after a space, as PipeWire's "Audient iD14 Multichannel" is the
// JACK client "Audient iD14"; never a "Monitor of"); then the browser's
// default, both channels. `how` says which, for the settings line.
export function pickInput(devices, settings, guitar) {
  const inputs = (devices || []).filter((d) => d && typeof d.deviceId === "string" && d.deviceId);
  const kept = settings && settings.input ? inputs.find((d) => d.deviceId === settings.input.deviceId) : null;
  if (kept) return { deviceId: kept.deviceId, label: kept.label || settings.input.label, channel: settings.channel ?? null, how: "chosen" };
  if (guitar && guitar.device) {
    const named = inputs.find((d) => typeof d.label === "string" && !/^monitor of /i.test(d.label)
      && (d.label === guitar.device || d.label.startsWith(`${guitar.device} `)));
    if (named) return { deviceId: named.deviceId, label: named.label, channel: guitar.channel, how: "guitar" };
  }
  const first = inputs.find((d) => d.deviceId === "default") || inputs[0] || null;
  return { deviceId: first ? first.deviceId : null, label: first ? first.label : "", channel: null, how: "default" };
}

// How an input is said on the settings line.
export function inputWords(pick) {
  if (!pick || !pick.deviceId) return "no input yet";
  const label = pick.label || (pick.deviceId === "default" ? "the browser's default input" : "an input");
  const side = pick.channel === 0 ? ", left channel" : pick.channel === 1 ? ", right channel" : "";
  const how = pick.how === "guitar" ? ` (the machine file's ${VISUALISER.guitarRole})` : pick.how === "default" ? " (the browser's choice)" : "";
  return `${label}${side}${how}`;
}

// Whether a pointer move is a move: the same place again is not (a browser
// may send one when what is under a still pointer changes, as the
// visualiser appearing does).
export function pointerMoved(before, now) {
  return !before || before.x !== now.x || before.y !== now.y;
}

// ---------------------------------------------------------------- the visualiser's looks (#196)
// What the visualiser shows: butterchurn's looks (visualiser-presets.js, all
// written for the rig) and the page's own equaliser looks (visualiser-eq.js,
// drawn from the capture's spectrum). Named here, not there, since the
// settings line offers them before either file is loaded; the tests hold
// the two files to these names. `mono`: black and white, or one colour.

export const VISUALISER_LOOKS = [
  { name: "Embers", kind: "milkdrop", mono: false },
  { name: "Tide", kind: "milkdrop", mono: false },
  { name: "Bloom", kind: "milkdrop", mono: false },
  { name: "Tunnel", kind: "milkdrop", mono: true },
  { name: "Shutter", kind: "milkdrop", mono: true },
  { name: "Smoke", kind: "milkdrop", mono: true },
  { name: "Mirror", kind: "milkdrop", mono: true },
  { name: "Storm", kind: "milkdrop", mono: true },
  { name: "Eclipse", kind: "milkdrop", mono: true },
  { name: "Bars", kind: "eq", mono: false },
  { name: "Curve", kind: "eq", mono: false },
  { name: "Aurora", kind: "eq", mono: false },
  { name: "Mono bars", kind: "eq", mono: true },
];

// The Look choice on the settings line: three random selections, then each
// look by name, which holds it. `{value, text}`.
export function lookChoices() {
  return [
    { value: "random", text: "random" },
    { value: "random-mono", text: "random, monochrome" },
    { value: "random-eq", text: "random, equalisers" },
    ...VISUALISER_LOOKS.map((l) => ({ value: l.name, text: l.name })),
  ];
}

// A kept choice, or "random" for anything that is not one.
export function lookChoice(value) {
  return lookChoices().some((c) => c.value === value) ? value : "random";
}

// The looks a choice draws from, by name: one, for a look held by name.
export function lookPool(choice) {
  const c = lookChoice(choice);
  if (c === "random") return VISUALISER_LOOKS.map((l) => l.name);
  if (c === "random-mono") return VISUALISER_LOOKS.filter((l) => l.mono).map((l) => l.name);
  if (c === "random-eq") return VISUALISER_LOOKS.filter((l) => l.kind === "eq").map((l) => l.name);
  return [c];
}

// A random number generator from a seed (mulberry32): the same seed, the
// same numbers, so the order and the variations can be tested. Each call
// gives a number in [0, 1).
export function seededRandom(seed) {
  let s = (Math.floor(Number(seed)) >>> 0) || 0x9e3779b9;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// The next look from `pool`, never `current` while there is another.
export function nextLook(pool, current, rng) {
  const names = Array.isArray(pool) ? pool : [];
  const others = names.filter((n) => n !== current);
  const from = others.length ? others : names;
  if (!from.length) return null;
  return from[Math.min(from.length - 1, Math.floor(rng() * from.length))];
}

// How long a look is shown, in seconds, a tenth at most: between
// VISUALISER.lookMin and lookMax.
export function lookSeconds(rng) {
  const s = VISUALISER.lookMin + rng() * (VISUALISER.lookMax - VISUALISER.lookMin);
  return Math.min(VISUALISER.lookMax, Math.round(s * 10) / 10);
}

// A look's variation each time it comes on: for each `[low, high]` of its
// `bounds` (its colour and motion, as each look declares them), a value
// within them. `{}` for none.
export function lookVariation(bounds, rng) {
  const out = {};
  for (const [key, range] of Object.entries(bounds || {})) {
    const [lo, hi] = range;
    out[key] = Math.min(hi, Math.max(lo, Math.round((lo + rng() * (hi - lo)) * 1e4) / 1e4));
  }
  return out;
}

// ---------------------------------------------------------------- the equaliser looks (#196)
// Bars, Curve, Aurora and Mono bars, drawn from the capture's spectrum
// (two AnalyserNodes on it). Read on a log frequency axis with a fixed width
// per octave: eight octaves, from 46.875 Hz (12 kHz / 256) to 12 kHz, each
// with a colour of its own, the same in every look, so where a colour is and
// how bright it is says what range is sounding. No number or label is drawn.

export const VISUALISER_EQ = {
  low: 12000 / 256,  // 46.875 Hz: below the low E (82 Hz) and drop tunings
  high: 12000,
  octaves: 8,
  // Below `split` the long FFT (8192 points: 5.9 Hz a bin at 48 kHz, 171 ms
  // a window), for the low bands' resolution; from it up the short one (4096
  // points: 85 ms), so a pick attack shows in a frame or two.
  split: 750,
  longFft: 8192,
  shortFft: 4096,
  span: 54,      // dB from the top of the display to its foot
  // The foot never below this (dB, a band's power), however low the top:
  // mains hum at -60 dBFS peak, with its second and third harmonics, read at
  // most -74 dB in a band (measured in Chromium at 50 and 60 Hz), and with
  // the foot at the top's floor less the span (-104) it lit half the
  // display in silence (PR #201's review). Hum at -50 dBFS peak (-64) shows.
  bottom: -70,
  floor: -40,    // the top never falls below this (dB): a quiet input reads over at least 30 dB
  topFall: 3,    // dB a second the top falls back after the loudest band
  attack: 0.015, // seconds: a level rises at once
  release: 0.5,  // and falls back over about this (PR #201: 0.2 swung choked staccato bright to dark)
  hold: 0.8,     // seconds a peak cap waits
  capFall: 0.4,  // then falls, display heights a second
  glowRise: 1.2, // Aurora's lingering glow: seconds to build on a sustained level
  glowFall: 2.5, // and to fade
  // The equaliser canvas is drawn at this fraction of the drawing buffer's
  // width and height, and scaled up by CSS: its lines and bars lose little,
  // and it is drawn in a quarter of the time (PR #201: Curve took 15 ms a
  // frame in software at full size).
  scale: 0.5,
  // If the median of its last frames took longer than this (ms), the
  // equaliser looks draw at `slowFps`, not VISUALISER.fps.
  slowFrameMs: 10,
  slowFps: 15,
};

// The equaliser canvas's size for a drawing buffer of `width` by `height`.
export function eqDrawSize(width, height) {
  const s = VISUALISER_EQ.scale;
  return { width: Math.max(1, Math.round(width * s)), height: Math.max(1, Math.round(height * s)) };
}

// The frame rate the equaliser looks draw at, from how long their recent
// frames took (ms): the usual, or half of it if the median is over
// VISUALISER_EQ.slowFrameMs. Fewer than ten frames: the usual.
export function eqFps(times) {
  const E = VISUALISER_EQ;
  const t = (Array.isArray(times) ? times : []).filter(Number.isFinite).slice().sort((a, b) => a - b);
  if (t.length < 10) return VISUALISER.fps;
  return t[Math.floor(t.length / 2)] > E.slowFrameMs ? E.slowFps : VISUALISER.fps;
}

// Each octave's colour, from the bottom: what it is called on the help page,
// and what sounds there on a guitar. The same in Bars, Curve and Aurora.
export const EQ_COLOURS = [
  { name: "deep red", rgb: [178, 0, 46], range: "the lowest notes: the low E and drop tunings, boom and thump" },
  { name: "red", rgb: [255, 48, 24], range: "the low strings' fundamentals" },
  { name: "orange", rgb: [255, 134, 0], range: "low-mids, mud, and the high strings' open notes" },
  { name: "yellow", rgb: [246, 220, 0], range: "boxiness, and notes high up the neck" },
  { name: "green", rgb: [56, 214, 74], range: "the mids: honk and nasal tones" },
  { name: "cyan", rgb: [0, 206, 224], range: "upper mids: bite, where a guitar cuts through a band" },
  { name: "blue", rgb: [46, 104, 255], range: "presence: pick attack and edge, where fizz starts" },
  { name: "violet", rgb: [168, 72, 255], range: "fizz, hiss and air" },
];

// Where `freq` (Hz) sits across the display, 0 at its left to 1 at its
// right, each octave the same width; held to the ends.
export function eqX(freq) {
  const E = VISUALISER_EQ;
  if (!(freq > 0)) return 0;
  return Math.min(1, Math.max(0, Math.log2(freq / E.low) / E.octaves));
}

// Which octave `freq` is in, 0 (deep red) to 7 (violet); held to the ends.
export function eqOctave(freq) {
  return Math.min(VISUALISER_EQ.octaves - 1, Math.floor(eqX(freq) * VISUALISER_EQ.octaves));
}

// The display's bands for a context at `sampleRate`, `perOctave` to an
// octave (6 for Bars: two to a third-octave), from the bottom: each
// `{lo, hi, centre, octave, x0, x1, fft, bins}`, `fft` "long" or "short",
// and `bins` its FFT's bins as `[index, weight]`: the share of that bin's
// width inside the band, so the bands share out every bin's power once.
export function eqBands(sampleRate, perOctave) {
  const E = VISUALISER_EQ;
  const n = E.octaves * perOctave;
  const bands = [];
  for (let i = 0; i < n; i += 1) {
    const lo = E.low * 2 ** (i / perOctave), hi = E.low * 2 ** ((i + 1) / perOctave);
    const fft = hi <= E.split * (1 + 1e-9) ? "long" : "short";
    const df = sampleRate / (fft === "long" ? E.longFft : E.shortFft);
    const bins = [];
    for (let k = Math.floor(lo / df + 0.5); k <= Math.floor(hi / df + 0.5); k += 1) {
      const overlap = Math.min(hi, (k + 0.5) * df) - Math.max(lo, (k - 0.5) * df);
      if (overlap > 0) bins.push([k, overlap / df]);
    }
    bands.push({ lo, hi, centre: Math.sqrt(lo * hi), octave: Math.floor(i / perOctave), x0: i / n, x1: (i + 1) / n, fft, bins });
  }
  return bands;
}

// A band's level in dB from its FFT's (AnalyserNode.getFloatFrequencyData,
// dB a bin): the power of its bins, weighted, summed. -200 for none.
export function eqBandDb(db, band) {
  let power = 0;
  for (const [k, w] of band.bins) {
    const v = db[k];
    if (Number.isFinite(v)) power += w * 10 ** (v / 10);
  }
  return power > 0 ? Math.max(-200, 10 * Math.log10(power)) : -200;
}

// The top of the display (dB): up at once to the loudest band, back down
// slowly, never under VISUALISER_EQ.floor, so a quiet input still reads.
export function eqTop(top, loudest, dt) {
  const E = VISUALISER_EQ;
  const fallen = (Number.isFinite(top) ? top : E.floor) - E.topFall * Math.max(0, dt);
  return Math.max(E.floor, Number.isFinite(loudest) ? Math.max(loudest, fallen) : fallen);
}

// A band's height on the display, 0 to 1, from its dB and the top: the
// foot is the span below the top, but never under VISUALISER_EQ.bottom, so
// hum and the input's own noise stay dark.
export function eqLevel(db, top) {
  const E = VISUALISER_EQ;
  const foot = Math.max(top - E.span, E.bottom);
  return Math.min(1, Math.max(0, (db - foot) / Math.max(1, top - foot)));
}

// A level after `dt` seconds moving to `target`: quickly up, slower down.
export function eqSmooth(prev, target, dt, attack = VISUALISER_EQ.attack, release = VISUALISER_EQ.release) {
  const p = Number.isFinite(prev) ? prev : 0;
  const tau = target > p ? attack : release;
  return target + (p - target) * Math.exp(-Math.max(0, dt) / tau);
}

// A peak cap `{value, held}` after `dt` seconds with the bar at `level`: up
// with it at once, held a while, then falling back slowly, never under it.
export function eqPeak(peak, level, dt) {
  const E = VISUALISER_EQ;
  const p = peak && Number.isFinite(peak.value) ? peak : { value: 0, held: 0 };
  if (level >= p.value) return { value: level, held: 0 };
  const held = p.held + Math.max(0, dt);
  const value = held > E.hold ? Math.max(level, p.value - E.capFall * Math.max(0, dt)) : p.value;
  return { value, held };
}

// Aurora's lingering glow after `dt` seconds with the band at `level`: it
// builds only on a level that stays, and fades slowly, so a short note
// barely shows in it and a build-up (mud) does.
export function eqGlow(prev, level, dt) {
  const E = VISUALISER_EQ;
  const p = Number.isFinite(prev) ? prev : 0;
  const tau = level > p ? E.glowRise : E.glowFall;
  return level + (p - level) * Math.exp(-Math.max(0, dt) / tau);
}

// ---------------------------------------------------------------- where a phone opens it (#181)

// The rack's "Agent on …" line: where a phone opens the page when the agent
// listens on the network, and that it is this machine's alone when it does
// not. `listen` is /health's `agent.listen` ({host, port, network, urls});
// null, from an agent older than #181 or none answering, keeps the line as
// it was. `here` is the page's own location.host. Returns `{text, urls,
// after}`: app.js draws `text`, each of `urls` ({url, label}) as a link with
// its interface, then `after`.
export function agentLine(listen, here) {
  const same = "Phone and laptop see the same rig.";
  if (!listen || typeof listen !== "object") return { text: `Agent on ${here}. ${same}`, urls: [], after: "" };
  if (!listen.network) {
    return { text: `Agent on ${here}, for this machine only: a phone cannot reach it. For one, start the agent with sushi-rig agent --phone.`,
      urls: [], after: "" };
  }
  const urls = (Array.isArray(listen.urls) ? listen.urls : []).filter((u) => u && typeof u.url === "string")
    .map((u) => ({ url: u.url, label: [u.kind, u.interface].filter(Boolean).join(", ") }));
  if (!urls.length) {
    return { text: `Agent on ${here}, open to the network, but this machine has no network address: join Wi-Fi or the phone's hotspot.`,
      urls: [], after: "" };
  }
  return { text: `Agent on ${here}. On a phone on the same network, open`, urls, after: `${same} No password: anyone on the network can change it.` };
}

// ---------------------------------------------------------------- pedals (#192)
// Assigning a device control (a footswitch, the expression pedal) to a target
// on the rack: an effect's on/off (`bypass`), a channel's on/off (`mute`) or
// volume, an effect's parameter. What a control may take is GET /midi's
// `takes` (rigfile.takes on the agent); whether the target names anything the
// rig has is the compiler's, said when POST /rig/midi/assign refuses. Nothing
// here is a learn: the board is never asked what it sends.

// How long a control's light stays on after a message from it is seen, and
// how recent a message must be to light it on the first sight of it.
export const PEDAL_LIT_MS = 1500;
const PEDAL_LIT_AGO = 1.5;

// A control's name as the board's own label: "c" is C, "pedal" stays.
export function pedalName(control) {
  return typeof control === "string" && control.length === 1 ? control.toUpperCase() : String(control);
}

// A target as the summary says it.
export function pedalSaid(binding, target) {
  if (binding === "bypass") return `${target} on/off`;
  if (binding === "mute") return `${target} on/off`;
  if (binding === "volume") return `${target} volume`;
  if (binding === "parameter") return String(target);
  if (binding === "blend") return Array.isArray(target) ? `blend ${target[0]} → ${target[1]}` : "a blend";
  return "nothing";
}

function sameTarget(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// GET /midi's answer as the rack uses it: the device the running rig's map is
// for (its `controller`, else the machine's one device with a type), its
// controls, and the assignments as `{control, binding, target}`. `choose`
// lists the typed devices when the map names none and there are several:
// the page asks which (`chosen`, the player's pick, else the first) and
// sends it with the assignment (`ask`), as the agent would otherwise ask.
export function pedalBoard(answer, chosen = null) {
  if (!answer || !answer.ok) return { ok: false, problem: answer ? answer.reason : null, controller: null, choose: [], ask: false, controls: [], assignments: [] };
  const body = answer.body || {};
  const controllers = Array.isArray(body.controllers) ? body.controllers : [];
  const map = body.assignments || null;
  const typed = controllers.filter((c) => c.type);
  const ask = !(map && map.controller) && typed.length > 1;
  const named = map && map.controller ? map.controller : typed.length === 1 ? typed[0].name
    : ask ? (typed.some((c) => c.name === chosen) ? chosen : typed[0].name) : null;
  const device = controllers.find((c) => c.name === named) || null;
  const assignments = [];
  for (const entry of (map && map.controls) || []) {
    const binding = Object.keys(PEDAL_WORDS).find((k) => k in entry);
    if (binding) assignments.push({ control: entry.control, binding, target: entry[binding] });
  }
  return {
    ok: true, rig: body.rig || null, problem: body.machine_problem || body.rig_problem || null,
    controller: device ? device.name : null, mapController: map ? map.controller || null : null,
    choose: ask ? typed.map((c) => c.name) : [], ask,
    controls: device ? device.controls.map((c) => ({ name: c.name, kind: c.kind, takes: c.takes || [], job: c.job || null })) : [],
    assignments,
  };
}

const PEDAL_WORDS = { bypass: true, mute: true, volume: true, parameter: true, blend: true };

// The control on a target, or null.
export function pedalOn(board, binding, target) {
  const found = board.assignments.find((a) => a.binding === binding && sameTarget(a.target, target));
  return found ? found.control : null;
}

// What a control does on this rig, or null.
export function pedalUse(board, control) {
  return board.assignments.find((a) => a.control === control) || null;
}

// The choices for a target: none, then each control that can take its
// binding (never one with a job: A and B step rigs on every rig), each saying
// what it does now when it does something else.
export function pedalOptions(board, binding, target) {
  const options = [{ value: "", label: "none" }];
  for (const c of board.controls) {
    if (c.job || !c.takes.includes(binding)) continue;
    const use = pedalUse(board, c.name);
    const elsewhere = use && !(use.binding === binding && sameTarget(use.target, target));
    options.push({ value: c.name, label: elsewhere ? `${pedalName(c.name)} (now ${pedalSaid(use.binding, use.target)})` : pedalName(c.name) });
  }
  return options;
}

// The parameters of a unit a pedal may sweep, in its panel's order and by
// its labels: every control of one parameter that Sushi lets be set, the
// advanced ones after. Not a profile's control of several parameters (a
// pedal drives one), nor one the profile hides (a plugin's own BYPASS).
export function pedalParameters(panel) {
  if (!panel) return [];
  return [...primaryControls(panel), ...(panel.advanced || [])]
    .filter((c) => c.kind !== "mix" && !c.readOnly && !c.missing && typeof c.name === "string")
    .map((c) => ({ value: c.name, label: c.label && c.label !== c.name ? `${c.label} (${c.name})` : c.name }));
}

// Whether a binding can be offered at all on this board.
export function pedalTakes(board, binding) {
  return board.controls.some((c) => !c.job && c.takes.includes(binding));
}

// The request for an assignment, or for taking a control off (`control`
// with no binding). `controller` only when the page had to ask which.
export function pedalCall(control, binding, target, controller = null) {
  const json = binding ? { control, [binding]: target } : { control, none: true };
  if (controller) json.controller = controller;
  return { path: "/rig/midi/assign", json };
}

// What choosing `control` (or "" for none) for a target does: the call to
// make, and the confirm to ask first when the control is in use elsewhere
// (it moves) or the target loses another control. Null when nothing changes.
export function pedalChoice(board, binding, target, control, controller = null) {
  const now = pedalOn(board, binding, target);
  if ((control || null) === now) return null;
  if (!control) return { call: pedalCall(now, null, null), confirm: null, what: `${pedalName(now)} off ${pedalSaid(binding, target)}` };
  const use = pedalUse(board, control);
  const said = pedalSaid(binding, target);
  const confirm = use ? `${pedalName(control)} is on ${pedalSaid(use.binding, use.target)}. Move it to ${said}?` : null;
  return { call: pedalCall(control, binding, target, controller), confirm, what: `${pedalName(control)}: ${said}`,
    moveLabel: use ? `Move ${pedalName(control)}` : null };
}

// The device's state as the page says it, from /health's `agent.midi.feeds`
// for its controller: listening, not connected (absent), waiting (the rig's
// map needs it and it is not plugged in), or what else the agent says.
export function pedalDeviceState(health, controller) {
  const midi = health && health.ok ? ((health.body.agent || {}).midi || null) : null;
  if (!midi) return { state: "unknown", text: "not known: the agent did not answer" };
  const feed = (midi.feeds || []).find((f) => f.controller === controller);
  if (!feed) {
    if (midi.state === "off") return { state: "off", text: `not listened to: ${midi.reason || "no rig is up"}` };
    return { state: midi.state || "unknown", text: midi.problem || "not listened to on this rig" };
  }
  if (feed.state === "listening") return { state: "listening", text: "listening" };
  if (feed.state === "absent") return { state: "absent", text: "not connected" };
  if (feed.state === "waiting") return { state: "waiting", text: "waiting: this rig needs it, and it is not plugged in" };
  if (feed.state === "opening") return { state: "opening", text: "opening its port" };
  return { state: feed.state || "unknown", text: feed.problem || feed.state || "not known" };
}

// The lights: a control is lit for PEDAL_LIT_MS after its count is seen to
// change, or on the first sight of one heard within PEDAL_LIT_AGO seconds.
// `held` is the last call's answer ({control: {count, until}}), `now` ms.
// Counted, not timed by the agent's clock, so a phone's clock does not matter.
export function pedalLights(held, health, controller, now) {
  const midi = health && health.ok ? ((health.body.agent || {}).midi || null) : null;
  const seen = midi && midi.seen && controller ? midi.seen[controller] || {} : {};
  const out = {};
  for (const [control, s] of Object.entries(seen)) {
    const before = held ? held[control] : undefined;
    let until = before ? before.until : 0;
    if (before ? s.count !== before.count : s.ago <= PEDAL_LIT_AGO) until = now + PEDAL_LIT_MS;
    out[control] = { count: s.count, value: s.value, until };
  }
  return out;
}

// The summary: each control of the device in its order, what it does on this
// rig (a job is said as such), and its light and, for the pedal, its value.
export function pedalSummary(board, lights, now) {
  return board.controls.map((c) => {
    const use = pedalUse(board, c.name);
    const light = lights ? lights[c.name] : null;
    const does = c.job ? (c.job === "previous-rig" ? "previous rig" : c.job === "next-rig" ? "next rig" : c.job)
      : use ? pedalSaid(use.binding, use.target) : "nothing on this rig";
    return { control: c.name, name: pedalName(c.name), does, assigned: !!use, job: !!c.job,
      lit: !!light && light.until > now, value: c.kind === "expression" && light ? light.value : null,
      heard: !!light };
  });
}

// What an assignment said, for the box under the summary.
export function pedalOutcome(what, answer) {
  const body = answer.body || {};
  if (!answer.ok) {
    return { ok: false, title: `Not assigned: ${what}`, reason: answer.reason, code: answer.code || null,
      controllers: Array.isArray(body.controllers) ? body.controllers : null };
  }
  const change = body.change || {};
  let text = `Wrote ${body.file} in place, keeping its comments.`;
  if (change.moved) text += ` ${pedalName(change.control)} came off ${change.moved.said}.`;
  for (const r of change.replaced || []) text += ` ${pedalName(r.control)} came off it.`;
  if (change.controller_set) text += ` The rig's map is now written for ${change.controller}.`;
  let offer = null;
  if (body.how === "live") text += " It plays now.";
  else {
    const why = (body.why || []).map((r) => r.detail).join("; ");
    text += ` It plays when ${body.rig} restarts${why ? ` (${why})` : ""}; until then the pedals do what they did.`;
    offer = { op: "restart", name: body.rig, label: `Restart ${body.rig} to play it` };
  }
  return { ok: true, title: `Assigned ${what}`, text, commit: commitText(body.library), offer, file: body.file };
}
