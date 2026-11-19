// The visualiser as a screensaver (#176): the DOM and the timing. After
// `idle` seconds with no pointer, touch or key on the page, while a rig is
// running and the window is wide enough, a visualisation fills the window
// and moves with the guitar; any input puts the page back, and that input
// reaches nothing under it. Its settings are a line at the foot of the page,
// kept in this browser.
//
// Loaded beside app.js and apart from it: app.js only tells it, after each
// poll, whether a rig is running (the "sushi-rig:health" event), and after
// each pw-top sample how many xruns the rig has had (the "sushi-rig:xruns"
// event). The drawing (visualiser.js and butterchurn) is loaded the first
// time it starts. The decisions are rig.js's.

import * as R from "./rig.js";

const KEY = "sushi-rig.visualiser";
// Checked this often whether the page has been left long enough.
const CHECK_MS = 500;
// After the press that put the page back, nothing under the visualiser is
// given anything until no button or key is held and this long has passed
// with no press: the press's own release and click land on the visualiser.
const QUIET_MS = 350;
// And never longer than this after it went, whatever is held: a key whose
// release went to another window (Alt+Tab, Ctrl+T, Super) is never seen to
// come up, and the invisible visualiser would otherwise take every click
// until a reload (found in review).
const CLOSING_MAX_MS = 1500;

function readSettings() {
  try { return R.visualiserSettings(localStorage.getItem(KEY)); } catch { return R.visualiserSettings(null); }
}
function writeSettings(s) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* kept until a reload */ }
}

const reduced = matchMedia("(prefers-reduced-motion: reduce)");

const state = {
  settings: readSettings(),
  running: false,          // a rig, as the page's last poll said
  last: performance.now(), // the last input
  pointer: null,           // where the pointer last was, for R.pointerMoved
  phase: "off",            // off | starting | shown | closing
  held: new Set(),         // buttons and keys down while it is shown or closing
  quietTimer: null,
  closingSince: null,      // when it last went, for CLOSING_MAX_MS
  probe: null,             // {webgl, renderer}, once asked
  permission: null,        // the microphone's permission state, once asked
  roles: null,             // GET /machine/roles's roles, once answered
  session: null,           // visualiser.js's, once made
  loaded: false,
  why: null,
  pick: null,
  error: null,
  generation: 0,           // a start is abandoned when this moves on
  due: 0,                  // how often the timer found the page left long enough: for the tests
  attempts: 0,             // how often the gate was asked because it was due: for the tests
  xruns: null,             // the last sample's {count, run}, from app.js
  base: null,              // that, when the capture was opened: {count, run}
  tripped: null,           // why it stopped itself, until reloaded or switched on again
  broken: null,            // why it cannot be made here, the same
  lost: 0,                 // WebGL contexts the browser took away
};

// ---------------------------------------------------------------- what the page is

// WebGL 2 and its renderer's name, asked once, on a canvas of its own that
// is let go at once.
function probe() {
  if (state.probe) return state.probe;
  let gl = null;
  try { gl = document.createElement("canvas").getContext("webgl2"); } catch { gl = null; }
  if (!gl) return (state.probe = { webgl: false, renderer: null });
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
  const lose = gl.getExtension("WEBGL_lose_context");
  if (lose) lose.loseContext();
  return (state.probe = { webgl: true, renderer });
}

async function permission() {
  if (state.permission) return state.permission;
  try {
    const status = await navigator.permissions.query({ name: "microphone" });
    state.permission = status.state;
    status.onchange = () => { state.permission = status.state; drawSettings(); };
  } catch {
    state.permission = "unknown";
  }
  return state.permission;
}

// The gate as it stands. `full` asks WebGL and the permission too, which is
// done only when it would start, never on load.
async function gate(full) {
  const p = full ? probe() : state.probe || {};
  return R.visualiserGate({
    on: state.settings.on, stopped: state.tripped || state.broken, reducedMotion: reduced.matches,
    running: state.running, width: window.innerWidth, secure: window.isSecureContext,
    media: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
    webgl: p.webgl, renderer: p.renderer,
    permission: full ? await permission() : state.permission,
  });
}

async function guitar() {
  if (!state.roles) {
    try {
      const response = await fetch("/machine/roles");
      const body = await response.json();
      if (response.ok && body && Array.isArray(body.roles)) state.roles = body.roles;
    } catch { /* no machine file to read: the browser's choice, said on the line */ }
  }
  return R.guitarInput(state.roles);
}

// ---------------------------------------------------------------- the visualiser

const overlay = document.createElement("div");
overlay.className = "visualiser";
overlay.hidden = true;
overlay.setAttribute("aria-hidden", "true");
let canvas = document.createElement("canvas");
const hint = document.createElement("p");
hint.className = "visualiser-hint";
hint.textContent = "Touch, move or press a key to come back";
overlay.append(canvas, hint);

// The browser took the drawing away (WebGL context lost): the session has
// disposed of itself. The next start makes another on a fresh canvas; a
// second loss in the page's life leaves it off until a reload.
function lostContext() {
  state.session = null;
  state.lost += 1;
  const fresh = document.createElement("canvas");
  canvas.replaceWith(fresh);
  canvas = fresh;
  if (state.lost >= 2) {
    state.broken = "Off: the browser took the visualiser's graphics away twice (WebGL context lost). Reload the page to try again.";
  } else {
    state.error = "The browser took the visualiser's graphics away (WebGL context lost): it starts afresh next time.";
  }
  close(true);
  refreshWhy();
}

// Hear the input and draw, for start and for a tab shown again. False if
// the visualiser went meanwhile (the stream is let go then).
async function listenAndRun(V, generation) {
  const opened = await V.listen(state.settings, await guitar());
  if (generation !== state.generation || !state.session) {
    for (const t of opened.stream.getTracks()) t.stop();
    return false;
  }
  state.pick = opened.pick;
  state.permission = "granted";
  state.session.attach(opened);
  state.base = state.xruns;
  await state.session.run();
  return generation === state.generation;
}

async function start(now = false) {
  if (state.phase !== "off") return;
  const g = await gate(true);
  state.why = g.why;
  if (state.phase !== "off") return;
  if (!now && !R.idleDue(state.last, performance.now(), state.settings.idle)) { drawSettings(); return; }
  state.attempts += 1;
  if (!g.ok) { drawSettings(); return; }
  const generation = ++state.generation;
  state.phase = "starting";
  state.error = null;
  catchAll(true);
  if (!document.body.contains(overlay)) document.body.append(overlay);
  overlay.hidden = false;
  overlay.classList.remove("closing");
  overlay.classList.add("showing");
  try {
    const V = await import("./visualiser.js");
    state.loaded = true;
    if (generation !== state.generation) return;
    if (!state.session) state.session = await V.create(canvas, lostContext);
    if (generation !== state.generation) return;
    state.session.resize(window.innerWidth, window.innerHeight);
    state.phase = "shown";
    if (!document.hidden && !(await listenAndRun(V, generation))) return;
    requestAnimationFrame(() => { if (generation === state.generation) overlay.classList.add("in"); });
  } catch (error) {
    // Said on the line, and not tried again until the page has been left
    // for another idle spell: a refused permission is not asked for every
    // half second. One that cannot be made here at all is not tried again
    // until a reload or the box is ticked again.
    state.error = `The visualiser could not start: ${error && error.name ? `${error.name}: ` : ""}${error && error.message || error}`;
    if (error && error.name === "NotAllowedError") state.permission = "denied";
    if (error && error.hard) { state.broken = `Off: ${error.message}. Reload the page, or tick the box again, to try again.`; state.error = null; }
    state.last = performance.now();
    await close(true);
    refreshWhy();
  }
  drawSettings();
}

// Put the page back. `atOnce`: no press to wait out.
async function close(atOnce = false) {
  state.generation += 1;
  if (state.phase === "off") return;
  if (state.phase !== "closing") state.closingSince = performance.now();
  state.phase = "closing";
  overlay.classList.remove("in", "showing");
  overlay.classList.add("closing");
  if (atOnce) finish(); else quiet();
  if (state.session) await state.session.stop();
}

function finish() {
  clearTimeout(state.quietTimer);
  state.quietTimer = null;
  if (state.phase !== "closing") return;
  overlay.hidden = true;
  overlay.classList.remove("closing");
  state.phase = "off";
  state.held.clear();
  state.closingSince = null;
  catchAll(false);
  drawSettings();
}

function quiet() {
  clearTimeout(state.quietTimer);
  state.quietTimer = setTimeout(() => {
    const ceiling = state.closingSince != null && performance.now() - state.closingSince >= CLOSING_MAX_MS - QUIET_MS;
    if (state.held.size === 0 || ceiling) finish(); else quiet();
  }, QUIET_MS);
}

// ---------------------------------------------------------------- input

const PRESSES = ["pointerdown", "mousedown", "touchstart", "keydown"];
const RELEASES = ["pointerup", "pointercancel", "mouseup", "touchend", "touchcancel", "keyup", "lostpointercapture"];
const AFTER = ["click", "dblclick", "auxclick", "contextmenu", "keypress", "wheel", "pointermove", "mousemove", "touchmove"];
const ACTIVITY = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"];

function swallow(event) {
  if (event.cancelable) event.preventDefault();
  event.stopImmediatePropagation();
}

function heldKey(event) {
  if (event.type.startsWith("key")) return `key:${event.code || event.key}`;
  return "pointer";
}

// A move to where the pointer already was is not input (R.pointerMoved).
// Asked once an event: both listeners below see a move, and the second to
// ask would find the pointer already there.
let lastAsked = { event: null, moved: false };
function moved(event) {
  if (event.type !== "pointermove" && event.type !== "mousemove") return true;
  if (event === lastAsked.event) return lastAsked.moved;
  const at = { x: event.screenX, y: event.screenY };
  const yes = R.pointerMoved(state.pointer, at);
  state.pointer = at;
  lastAsked = { event, moved: yes };
  return yes;
}

// Always listening, and passive, so scrolling and touch are never held up:
// only when the page was last touched.
function activity(event) {
  if (moved(event)) state.last = performance.now();
}
for (const type of ACTIVITY) window.addEventListener(type, activity, { capture: true, passive: true });

// While the visualiser is up (starting, shown or closing): everything, so
// nothing under it is given anything, and the first input of all puts the
// page back. Added only then, since a listener that can cancel a touch or a
// wheel makes the browser wait on the page before scrolling.
function caught(event) {
  if (!moved(event)) return;
  state.last = performance.now();
  swallow(event);
  if (PRESSES.includes(event.type)) state.held.add(heldKey(event));
  if (RELEASES.includes(event.type)) {
    if (event.type.startsWith("key")) state.held.delete(heldKey(event));
    else if (!event.type.startsWith("touch") || event.touches.length === 0) state.held.delete("pointer");
  }
  if (state.phase === "shown" || state.phase === "starting") close();
  // A press or release keeps it catching a while more; moving does not, so a
  // pointer kept moving cannot hold the page behind it.
  else if (PRESSES.includes(event.type) || RELEASES.includes(event.type) || event.type === "click") quiet();
}

let catching = false;
function catchAll(on) {
  if (on === catching) return;
  catching = on;
  for (const type of [...PRESSES, ...RELEASES, ...AFTER]) {
    if (on) window.addEventListener(type, caught, { capture: true, passive: false });
    else window.removeEventListener(type, caught, { capture: true });
  }
}

// Whatever was held, its release will not come here: the window lost the
// keyboard and pointer, or the tab went.
function letGo() {
  state.held.clear();
  if (state.phase === "closing") quiet();
}
window.addEventListener("blur", letGo);

setInterval(() => {
  if (state.phase === "off" && !document.hidden && R.idleDue(state.last, performance.now(), state.settings.idle)) {
    state.due += 1;
    start();
  }
}, CHECK_MS);

// A hidden tab draws nothing and hears nothing: the capture is let go, and
// opened again when the tab is shown.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) letGo();
  if (!state.session || state.phase !== "shown") return;
  if (document.hidden) { state.session.stop(); return; }
  const generation = state.generation;
  import("./visualiser.js").then((V) => listenAndRun(V, generation)).catch((error) => {
    state.error = `The visualiser could not hear the input again: ${error && error.message || error}`;
    close(true);
  });
});

window.addEventListener("resize", () => {
  if (state.phase !== "shown") { refreshWhy(); return; }
  if (window.innerWidth < R.VISUALISER.minWidth) { close(true); return; }
  state.session.resize(window.innerWidth, window.innerHeight);
});

reduced.addEventListener("change", () => { if (reduced.matches) close(true); refreshWhy(); });

document.addEventListener("sushi-rig:health", (event) => {
  const running = !!(event.detail && event.detail.running);
  if (running !== state.running) {
    state.running = running;
    if (!running) close(true);
    refreshWhy();
  }
});

// The rig's xruns, after each pw-top sample. If they rise while the capture
// is open, the capture and the visualiser stop and stay stopped: the
// capture stream is a node in Sushi's cycle (visualiser.js says how), so a
// glitch while it listens is taken as its doing, whether or not it was.
document.addEventListener("sushi-rig:xruns", (event) => {
  const seen = event.detail && typeof event.detail.count === "number" ? { count: event.detail.count, run: event.detail.run } : null;
  state.xruns = seen;
  if (!seen || !(state.phase === "shown" || state.phase === "starting") || !state.session || !state.session.stats().listening) return;
  const why = R.visualiserTripped(state.base, seen);
  if (why === "rebase") { state.base = seen; return; }
  if (why) {
    state.tripped = why;
    close(true);
    refreshWhy();
  }
});

// ---------------------------------------------------------------- the settings line

const footer = document.createElement("footer");
footer.className = "visualiser-settings";
footer.setAttribute("aria-label", "Visualiser");
const onBox = Object.assign(document.createElement("input"), { type: "checkbox", id: "visualiser-on" });
const idleBox = Object.assign(document.createElement("input"), {
  type: "number", id: "visualiser-idle", min: R.VISUALISER.idleMin, max: R.VISUALISER.idleMax, step: 1,
});
const inputPick = Object.assign(document.createElement("select"), { id: "visualiser-input" });
const channelPick = Object.assign(document.createElement("select"), { id: "visualiser-channel" });
const showNow = Object.assign(document.createElement("button"), { type: "button", className: "visualiser-now", textContent: "Show now" });
const note = Object.assign(document.createElement("span"), { className: "note visualiser-note" });
note.setAttribute("aria-live", "polite");

function label(text, control, after = "") {
  const el = document.createElement("label");
  el.append(text ? `${text} ` : "", control, after ? ` ${after}` : "");
  return el;
}
for (const [value, text] of [["", "both channels"], ["0", "left channel"], ["1", "right channel"]]) {
  channelPick.append(Object.assign(document.createElement("option"), { value, textContent: text }));
}
footer.append(label("", onBox, "Visualiser"), label("after", idleBox, "s without a touch"),
  label("listening to", inputPick), channelPick, showNow, note);

let devices = [];

// The browser's inputs, by name: it gives names only once this page may
// hear one, so until the visualiser has run once the list is "automatic".
async function fillInputs() {
  try {
    const all = await navigator.mediaDevices.enumerateDevices();
    devices = all.filter((d) => d.kind === "audioinput").map((d) => ({ deviceId: d.deviceId, label: d.label }));
  } catch { /* the list stays as it was */ }
  drawSettings();
}

// The line's reason, again, after anything it depends on changed.
function refreshWhy() {
  gate(false).then((g) => { state.why = g.why; drawSettings(); });
}

function drawSettings() {
  const s = state.settings;
  onBox.checked = s.on;
  if (document.activeElement !== idleBox) idleBox.value = String(s.idle);
  const options = [["", "automatic"], ...devices.filter((d) => d.label).map((d) => [d.deviceId, d.label])];
  if (s.input && !options.some(([id]) => id === s.input.deviceId)) options.push([s.input.deviceId, s.input.label || "the input chosen before"]);
  const shownKey = JSON.stringify(options);
  if (inputPick.dataset.options !== shownKey) {
    inputPick.replaceChildren(...options.map(([value, text]) => Object.assign(document.createElement("option"), { value, textContent: text })));
    inputPick.dataset.options = shownKey;
  }
  inputPick.value = s.input ? s.input.deviceId : "";
  channelPick.value = s.channel == null ? "" : String(s.channel);
  channelPick.disabled = !s.input;
  const words = [];
  if (state.error) words.push(state.error);
  else if (state.why) words.push(state.why);
  if (state.pick) words.push(`Heard last: ${R.inputWords(state.pick)}.`);
  else if (s.on) words.push("The first time it starts, the browser asks to hear an input: allow it.");
  note.textContent = words.join(" ");
  footer.dataset.phase = state.phase;
}

function changed(update) {
  state.settings = { ...state.settings, ...update };
  writeSettings(state.settings);
  state.error = null;
  state.last = performance.now();
  refreshWhy();
}

onBox.addEventListener("change", () => {
  // Ticked again: what stopped it is forgiven, as a reload would.
  if (onBox.checked) { state.tripped = null; state.broken = null; state.lost = 0; }
  changed({ on: onBox.checked });
  if (!onBox.checked) close(true);
});
idleBox.addEventListener("change", () => {
  const idle = R.visualiserIdle(idleBox.value);
  if (idle != null) changed({ idle }); else drawSettings();
  idleBox.value = String(state.settings.idle);
});
inputPick.addEventListener("focus", fillInputs);
inputPick.addEventListener("change", () => {
  const d = devices.find((x) => x.deviceId === inputPick.value);
  changed(inputPick.value ? { input: { deviceId: inputPick.value, label: d ? d.label : "" } } : { input: null, channel: null });
});
channelPick.addEventListener("change", () => changed({ channel: channelPick.value === "" ? null : Number(channelPick.value) }));
// After the click is over, so its own release does not put the page back.
showNow.addEventListener("click", () => setTimeout(() => start(true), QUIET_MS));

document.body.append(footer);
refreshWhy();

// For the browser tests: what it is doing. Read-only, and nothing else.
window.sushiRigVisualiser = () => ({
  phase: state.phase, why: state.why, error: state.error, loaded: state.loaded, running: state.running,
  idle: state.settings.idle, on: state.settings.on, pick: state.pick, held: [...state.held],
  due: state.due, attempts: state.attempts, tripped: state.tripped, broken: state.broken, lost: state.lost,
  session: state.session ? state.session.stats() : null,
});
