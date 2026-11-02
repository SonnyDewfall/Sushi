// The web UI's DOM and network (#31). Every decision is rig.js's; this file
// asks the agent, hands the answers to rig.js, and draws what comes back.
// Two screens: the rack (S1, the default) and the stage screen (S5, #stage,
// and the default at phone width).

import * as R from "./rig.js";

// GET /live once a second, as the brief asks: often enough that a pedal's
// bypass shows on the card within a second, rare enough for a Pi's browser.
const POLL_MS = 1000;
// GET /rig/load this long after the last answer, and only while a rig is up:
// each is a pw-top run of about a second on the rig, so slower than /live,
// and load and xruns move slowly enough to read at this rate (#111).
const LOAD_MS = 2000;
// A failed read of which plugin each processor is, or of the profiles, is
// asked again this often, not on every poll: a session read is heavy.
const RETRY_MS = 10000;
// How long "Stop now" stays offered after Stop: stopping cuts the sound, so
// it takes two taps, and a first tap left alone is forgotten.
const CONFIRM_MS = 8000;
// Said wherever "sound not measured" is: what the figures are, and are not.
const SOUND_NOTE = "Nothing measures the rig's sound yet (#49). Load and xruns say whether the rig keeps up with its blocks, not whether it sounds right.";
// Pixels of drag for a knob's whole travel: a thumb's comfortable sweep.
const DRAG_PIXELS = 200;
// How long the first tap on "Locked" stays armed: unlocking takes two taps
// inside this, so a pocket or a stray palm cannot do it (S6: deliberate).
const UNLOCK_MS = 4000;
const LOCK_KEY = "sushi-rig.locked";
// Colours for a unit's swatch, from the mockups; chosen by the plugin, so a
// plugin keeps its colour wherever it sits. Decoration, nothing more.
const SWATCHES = ["#D9C04E", "#F08A50", "#B59CF2", "#8FC25A", "#A98BF0", "#6FC6D1", "#E07A73"];

// The lock is remembered in this browser only (it is the page's lock, see
// rig.js). Storage can be absent or refuse; then the page starts locked,
// which is the side to err on.
function readLocked() {
  try { return localStorage.getItem(LOCK_KEY) !== "off"; } catch { return true; }
}
function writeLocked(locked) {
  try { localStorage.setItem(LOCK_KEY, locked ? "on" : "off"); } catch { /* the page still works */ }
}

const state = {
  health: null, live: null, log: null,
  plugins: null, pluginsFor: null, pluginsError: null, pluginsAt: 0,
  profiles: null, profilesError: null, profilesAt: 0, profileProblems: [],
  names: null, namesAt: 0,   // plugin names by URI, from /library: decoration, so a failure is not shown
  locked: readLocked(), armed: false, armTimer: null,
  load: null, loadView: null, xruns: null, xrunsFor: null, loadTimer: null,  // GET /rig/load, every LOAD_MS
  rigs: null, rigsAt: 0,     // GET /rigs: kept once it answers, read again after a lifecycle command
  amp: null,                 // GET /amp, with /live: what the agent last sent Carla
  ampModels: null, ampModelsAt: 0,  // GET /amp/models: kept once it answers, read again after a model change
  ampChoice: null,           // the model picked in the amp card, awaiting its confirm
  ampPending: null,          // the model change in flight
  chosen: null,              // the rig picked in the picker; null follows the running rig
  pending: null,             // this page's lifecycle command in flight: {op, name}
  confirming: false, confirmTimer: null,  // Stop's second tap
  open: new Set(),           // units opened, by name
  failures: [],              // this page's refused changes, newest first
  failed: new Map(),         // control key -> the reason its last change was refused
  notice: null,
  pollSeq: 0, seq: 0, timer: null, polling: false,
  screen: null, regions: null, updaters: [], keys: new WeakMap(),
};

// ---------------------------------------------------------------- network

async function request(method, path, body) {
  try {
    const response = await fetch(path, {
      method, cache: "no-store",
      headers: body ? { "content-type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await response.json(); } catch { /* not JSON: said below */ }
    if (response.ok && data && data.ok !== false) return { ok: true, status: response.status, body: data };
    return {
      ok: false, status: response.status, body: data, code: data && data.code,
      reason: (data && data.reason) || `${response.status} ${response.statusText || "with no reason given"}`,
    };
  } catch (error) {
    return { ok: false, status: 0, body: null, reason: `the agent did not answer (${error.message})` };
  }
}

const sender = new R.Sender((call) => request("POST", call.path, call.json), {
  pollSeq: () => state.pollSeq,
  onFailure: (key, result) => {
    state.failed.set(key, result.reason);
    fail(`Not changed: ${key.split("\u0000").slice(1).join(" · ")}`, result);
    render();
  },
  onSent: (key) => { state.failed.delete(key); },
});

function fail(title, result, action = null) {
  state.failures.unshift({ title, reason: result.reason, code: result.code || null, action, at: new Date() });
  state.failures.length = Math.min(state.failures.length, 4);
}

// Which rig this is, beyond its processors' names: a switch can bring up a
// rig with the same tracks and names and a different plugin under one of
// them, by a restart (a new start time) or live (a new name, or a rig switch
// in the agent's log, which the live swap of #93 writes without a restart).
function rigIdentity(health, log) {
  const rig = health && health.ok ? health.body.rig || {} : {};
  let switched = null;
  const entries = (log && log.log) || [];
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].op === "rig") { switched = (log.first || 0) + i; break; }
  }
  return [rig.config_name ?? null, rig.rig ?? null, rig.started_at ?? null, switched];
}

async function refreshPlugins(live, identity) {
  const key = JSON.stringify([live.tracks, identity]);
  const retry = state.pluginsError && Date.now() - state.pluginsAt > RETRY_MS;
  if (key === state.pluginsFor && !retry) return;
  if (key !== state.pluginsFor) state.plugins = null; // never another rig's plugins, even for a moment
  const answer = await request("GET", "/live/plugins");
  state.pluginsFor = key;
  state.pluginsAt = Date.now();
  state.plugins = answer.ok ? answer.body.processors : null;
  state.pluginsError = answer.ok ? null : answer.reason;
}

// The profiles and the plugins' names are files, not the rig, and the library
// is built on its first request: neither may hold up the first sight of the
// rig, so they are fetched beside the poll, not in it. A fetch that failed is
// asked again after RETRY_MS; one that answered is kept, its loader problems
// (a profile that would not load) shown and not a reason to ask again.
const fetching = new Set();
function inBackground(name, fetch) {
  if (fetching.has(name)) return;
  fetching.add(name);
  // `request` never rejects, but the name is let go whatever happens, so a
  // throw anywhere in a fetch can never stop its retry for good.
  fetch()
    .then((fetched) => { if (fetched) render(); })
    .catch((error) => console.error(`fetching ${name}:`, error))
    .finally(() => fetching.delete(name));
}

async function refreshProfiles() {
  if (state.profiles) return false;
  if (state.profilesAt && Date.now() - state.profilesAt < RETRY_MS) return false;
  const answer = await request("GET", "/profiles");
  state.profilesAt = Date.now();
  if (answer.ok) {
    state.profiles = answer.body.profiles;
    state.profileProblems = answer.body.problems;
    state.profilesError = null;
  } else {
    state.profilesError = answer.reason;
  }
  return true;
}

async function refreshNames() {
  if (state.names) return false;
  if (state.namesAt && Date.now() - state.namesAt < RETRY_MS) return false;
  const answer = await request("GET", "/library");
  state.namesAt = Date.now();
  if (answer.ok) state.names = Object.fromEntries(answer.body.plugins.map((p) => [p.uri, p.name]));
  return answer.ok;
}

// config/rigs/, for the picker. Kept once it answers; asked again after
// RETRY_MS while it fails, and at once (`force`) after a start, stop or switch.
async function refreshRigs(force = false) {
  if (!force && state.rigs && state.rigs.ok) return false;
  if (!force && state.rigsAt && Date.now() - state.rigsAt < RETRY_MS) return false;
  state.rigs = await request("GET", "/rigs");
  state.rigsAt = Date.now();
  return true;
}

// amp/models, for the amp card's picker: once it answers, and again after a
// model change (which one is playing moves); after RETRY_MS while it fails.
async function refreshAmpModels(force = false) {
  if (!force && state.ampModels && state.ampModels.ok) return false;
  if (!force && state.ampModelsAt && Date.now() - state.ampModelsAt < RETRY_MS) return false;
  state.ampModels = await request("GET", "/amp/models");
  state.ampModelsAt = Date.now();
  return true;
}

async function poll() {
  if (state.polling) return;
  state.polling = true;
  clearTimeout(state.timer);
  const seq = ++state.pollSeq;
  try {
    // /health first, and /live only when it says a rig is up and still
    // (`R.readsLive`): a down rig is a state, and asking /live for it was a
    // console error a second (#111). The log answers whatever the rig.
    const health = await request("GET", "/health");
    const [live, log, amp] = await Promise.all([
      R.readsLive(health) ? request("GET", "/live") : null,
      health.ok ? request("GET", "/live/log") : null,
      // The amp is read when /live is, for the same reason: with no rig up
      // it can only be refused (#111).
      R.readsLive(health) ? request("GET", "/amp") : null,
    ]);
    state.health = health;
    state.live = live;
    state.amp = amp;
    state.log = log && log.ok ? log.body : null;
    state.seq = seq;
    if (live && live.ok) await refreshPlugins(live.body, rigIdentity(health, state.log));
    render();
    inBackground("profiles", refreshProfiles);
    inBackground("names", refreshNames);
    inBackground("rigs", refreshRigs);
    if (amp && amp.ok) inBackground("amp models", refreshAmpModels);
  } finally {
    state.polling = false;
    state.timer = setTimeout(poll, POLL_MS);
  }
}

// After a gesture that changes the rig's structure: read it now, not in a second.
function kick() {
  if (!state.polling) poll();
}

// GET /rig/load beside the poll, at its own pace, and only while a rig is up
// and still: with none, there is nothing to read and the tiles say so. The
// tiles are decided here, when an answer arrives, since "rising" compares it
// with the one before (`R.xrunsStep`, per node); the count starts again for
// each rig run (its name and start).
async function pollLoad() {
  clearTimeout(state.loadTimer);
  try {
    if (R.readsLive(state.health)) {
      const answer = await request("GET", "/rig/load");
      const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
      const run = JSON.stringify([rig.config_name ?? null, rig.started_at ?? null]);
      if (run !== state.xrunsFor) { state.xruns = null; state.xrunsFor = run; }
      state.load = answer;
      const step = answer.ok ? R.xrunsStep(state.xruns, answer.body) : null;
      if (step) state.xruns = step.seen;
      state.loadView = R.loadTiles(answer, true, step);
    } else {
      state.load = null;
      state.loadView = null;
    }
    render();
  } finally {
    state.loadTimer = setTimeout(pollLoad, LOAD_MS);
  }
}

// ---------------------------------------------------------------- DOM helpers

function h(tag, attrs = {}, ...children) {
  const el = tag === "svg" || tag === "circle" || tag === "path" || tag === "rect"
    ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

// Rebuild a region only when what it shows has changed, so a poll does not
// replace a button under a finger between its press and its release.
function patch(el, key, build) {
  if (state.keys.get(el) === key) return false;
  el.replaceChildren(...[build()].flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  state.keys.set(el, key);
  return true;
}

// The plugin's name to show under a unit: the library's (its bundle's own
// doap:name), else the profile's, else the URI's last part, else that Sushi's
// session did not say (then the unit has no profile either).
function pluginName(unit) {
  if (!unit.uri) return "plugin not known";
  const known = state.names && state.names[unit.uri];
  return known || (unit.profile && unit.profile.name) || unit.uri.split("/").filter(Boolean).pop();
}

function swatch(unit) {
  let hash = 0;
  for (const ch of unit.uri || unit.name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return SWATCHES[hash % SWATCHES.length];
}

const lockIcon = () => h("svg", { width: 14, height: 14, viewBox: "0 0 16 16", "aria-hidden": "true", class: "icon" },
  h("rect", { x: 3, y: 7, width: 10, height: 7, rx: 1.5 }), h("path", { d: "M5.5 7V5a2.5 2.5 0 0 1 5 0V7" }));

// ---------------------------------------------------------------- controls

function liveBody() { return state.live && state.live.ok ? state.live.body : null; }

function knob(unit, ctrl, size) {
  const key = `p\u0000${unit.name}\u0000${ctrl.name}`;
  const inert = ctrl.readOnly || ctrl.missing;
  const r = size === "big" ? 26 : 13, w = size === "big" ? 6 : 4, box = 2 * r + 2 * w;
  const sweep = 1.5 * Math.PI * r, round = 2 * Math.PI * r;
  const arc = h("circle", { cx: box / 2, cy: box / 2, r, class: "arc", "stroke-width": w, transform: `rotate(135 ${box / 2} ${box / 2})` });
  const value = h("span", { class: "value" });
  const note = h("span", { class: "note" });
  const el = h("div", {
    class: `knob k-${size}${inert ? " inert" : ""}`, role: "slider", tabindex: inert ? -1 : 0,
    "aria-label": `${unit.name} ${ctrl.label}`, "aria-valuemin": 0, "aria-valuemax": 100,
    "aria-readonly": inert ? "true" : null, title: ctrl.description || null,
  },
    h("svg", { width: box, height: box, viewBox: `0 0 ${box} ${box}`, "aria-hidden": "true" },
      h("circle", { cx: box / 2, cy: box / 2, r, class: "track", "stroke-width": w, "stroke-dasharray": `${sweep} ${round}`, transform: `rotate(135 ${box / 2} ${box / 2})` }),
      arc),
    h("span", { class: "text" }, value, h("span", { class: "label" }, ctrl.label), note));

  let current = null;
  const set = (v) => {
    v = Math.min(1, Math.max(0, v));
    sender.change(key, v, (x) => ({ path: "/live/parameter", json: { processor: unit.name, parameter: ctrl.name, value: x } }));
    draw(v);
  };
  const draw = (v, unsaved) => {
    current = v;
    const p = v === null ? 0 : R.positionOf(ctrl, v);
    arc.setAttribute("stroke-dasharray", `${p * sweep} ${round}`);
    const text = R.formatValue(ctrl, v);
    value.textContent = text;
    el.setAttribute("aria-valuenow", Math.round(p * 100));
    el.setAttribute("aria-valuetext", text);
    const failed = state.failed.get(key);
    const notes = [...ctrl.notes];
    if (ctrl.missing) notes.unshift("not on the running plugin");
    else if (ctrl.readOnly) notes.unshift("read-only");
    if (unsaved) notes.unshift(`was ${R.formatValue(ctrl, unsaved.from)}`);
    if (failed) notes.unshift(`refused: ${failed}`);
    note.textContent = notes.join(" · ");
    if (unsaved !== undefined) el.classList.toggle("unsaved", !!unsaved);
    el.classList.toggle("failed", !!failed);
  };

  if (!inert) {
    let drag = null;
    el.addEventListener("pointerdown", (e) => {
      el.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, p: R.positionOf(ctrl, current ?? 0) };
      e.preventDefault();
    });
    el.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const p = drag.p + ((e.clientX - drag.x) - (e.clientY - drag.y)) / DRAG_PIXELS;
      const v = R.valueAt(ctrl, p);
      if (v !== current) set(v);
    });
    const end = () => { drag = null; };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("keydown", (e) => {
      const step = R.stepOf(ctrl);
      const moves = { ArrowUp: step, ArrowRight: step, ArrowDown: -step, ArrowLeft: -step, PageUp: 10 * step, PageDown: -10 * step };
      let p;
      if (e.key in moves) p = R.positionOf(ctrl, current ?? 0) + moves[e.key];
      else if (e.key === "Home") p = 0;
      else if (e.key === "End") p = 1;
      else return;
      e.preventDefault();
      set(R.valueAt(ctrl, p));
    });
  }
  state.updaters.push((ctx) => draw(sender.displayed(key, R.valueOf(ctx.live, unit.name, ctrl.name), ctx.seq), ctx.unsaved.get(key) || null));
  return el;
}

function toggle(unit, ctrl) {
  const key = `p\u0000${unit.name}\u0000${ctrl.name}`;
  const value = h("span", { class: "value" });
  const el = h("button", { class: "switch", "aria-label": `${unit.name} ${ctrl.label}`, disabled: ctrl.readOnly || ctrl.missing },
    h("span", { class: "label" }, ctrl.label), value);
  let current = null;
  el.addEventListener("click", () => {
    const v = R.valueAt(ctrl, R.isOn(ctrl, current) ? 0 : 1);
    sender.change(key, v, (x) => ({ path: "/live/parameter", json: { processor: unit.name, parameter: ctrl.name, value: x } }));
    render();
  });
  state.updaters.push((ctx) => {
    current = sender.displayed(key, R.valueOf(ctx.live, unit.name, ctrl.name), ctx.seq);
    value.textContent = R.formatValue(ctrl, current);
    el.setAttribute("aria-pressed", String(R.isOn(ctrl, current)));
    el.classList.toggle("unsaved", ctx.unsaved.has(key));
    el.classList.toggle("failed", state.failed.has(key));
  });
  return el;
}

function selector(unit, ctrl) {
  const key = `p\u0000${unit.name}\u0000${ctrl.name}`;
  const select = h("select", { "aria-label": `${unit.name} ${ctrl.label}`, disabled: ctrl.readOnly || ctrl.missing },
    ctrl.choices.map((c) => h("option", { value: c.value }, c.label)));
  select.addEventListener("change", () => {
    const v = R.normalisedOf(ctrl, Number(select.value));
    sender.change(key, v, (x) => ({ path: "/live/parameter", json: { processor: unit.name, parameter: ctrl.name, value: x } }));
  });
  state.updaters.push((ctx) => {
    if (document.activeElement === select) return; // not while it is being chosen from
    const v = sender.displayed(key, R.valueOf(ctx.live, unit.name, ctrl.name), ctx.seq);
    // Only a choice the value is on: a value between two is shown as none
    // chosen, with the number in the title, rather than as the nearer one.
    const real = v === null ? null : R.realOf(ctrl, v);
    const on = real === null ? null : ctrl.choices.find((c) => Math.abs(c.value - real) < 1e-6 * Math.max(1, Math.abs(real)));
    select.value = on ? String(on.value) : "";
    select.title = on ? "" : `not on a choice: ${R.formatValue(ctrl, v)}`;
  });
  return h("label", { class: "selector" }, h("span", { class: "label" }, ctrl.label), select);
}

function control(unit, ctrl, size) {
  if (ctrl.control === "toggle" || ctrl.scale === "toggle") return toggle(unit, ctrl);
  if (ctrl.control === "selector" && ctrl.choices) return selector(unit, ctrl);
  return knob(unit, ctrl, size);
}

function bypassButton(unit, size) {
  const key = `b\u0000${unit.name}`;
  const led = h("span", { class: "led" });
  const el = h("button", { class: `bypass b-${size}` }, led);
  let current = null;
  el.addEventListener("click", () => {
    if (current === null) return; // unknown: which way would be a guess
    sender.change(key, !current, (x) => ({ path: "/live/bypass", json: { processor: unit.name, bypassed: x } }));
    render();
  });
  state.updaters.push((ctx) => {
    const live = ctx.live.processors[unit.name];
    current = sender.displayed(key, live ? live.bypassed ?? null : null, ctx.seq);
    el.disabled = current === null;
    el.setAttribute("aria-pressed", String(current === false));
    el.setAttribute("aria-label", current === null
      ? `Sushi did not say whether ${unit.name} is bypassed`
      : (current ? `Turn on ${unit.name}` : `Bypass ${unit.name}`));
    el.classList.toggle("on", current === false);
    el.classList.toggle("failed", state.failed.has(key));
    const row = el.closest(".unit, .card");
    if (row) row.classList.toggle("bypassed", current === true);
  });
  return el;
}

// ---------------------------------------------------------------- shared regions

function lockButton() {
  const label = state.locked ? (state.armed ? "Tap again to unlock" : "Locked") : "Lock off · editing";
  return h("button", {
    class: `lock${state.locked ? " locked" : ""}${state.armed ? " armed" : ""}`,
    "aria-label": state.locked
      ? (state.armed ? "Tap again to unlock structural edits" : "Performance lock is on. Tap twice to unlock structural edits")
      : "Performance lock is off. Tap to lock",
    onclick: () => {
      if (!state.locked) { state.locked = true; writeLocked(true); }
      else if (!state.armed) {
        state.armed = true;
        state.armTimer = setTimeout(() => { state.armed = false; render(); }, UNLOCK_MS);
      } else {
        clearTimeout(state.armTimer);
        state.armed = false; state.locked = false; writeLocked(false);
      }
      render();
    },
  }, lockIcon(), label);
}

function statusLine(status) {
  return h("span", { class: `status tone-${status.tone}`, title: status.tone === "up" ? SOUND_NOTE : null },
    h("span", { class: "dot", "aria-hidden": "true" }), status.detail);
}

// Load and xruns from pw-top (GET /rig/load, `R.loadTiles`), and the amp.
// What is not known is said as such rather than left off: a screen that
// looked the same with these unknown would be lying by omission.
function tileViews(health) {
  const rig = health && health.ok ? health.body.rig || {} : {};
  const measured = state.loadView && R.readsLive(health) ? state.loadView : R.loadTiles(null, !!rig.running, null);
  return [...measured, { k: "AMP", v: rig.amp_model ? R.modelName(rig.amp_model) : "none", s: null, title: rig.amp_model || null, tone: "" }];
}

// The tiles in an element of their own, kept across the header's rebuilds:
// they change with every load sample, and were in the header's key, so the
// whole header, Lock button and Stage link included, was rebuilt every 2 s,
// losing a click between press and release, and keyboard focus (found by
// review).
function tilesRegion(el) {
  const views = tileViews(state.health);
  patch(el, JSON.stringify(views), () => views.map((t) => h("span", { class: `tile${t.tone ? " " + t.tone : ""}`, title: t.title },
    h("span", { class: "k" }, t.k), h("span", { class: "v" }, t.v), t.s ? h("span", { class: "s" }, t.s) : null)));
}

function problems() {
  const out = [];
  for (const p of R.agentProblems(state.health)) out.push({ kind: "agent", ...p });
  if (state.pluginsError) out.push({ kind: "page", title: "Which plugin each processor is", reason: `${state.pluginsError}: every card shows its parameters in a plain list, without profiles` });
  if (state.profilesError) out.push({ kind: "page", title: "Profiles could not be read", reason: `${state.profilesError}; asking again every ${RETRY_MS / 1000} s, and every card shows its parameters in a plain list meanwhile` });
  for (const problem of state.profileProblems) out.push({ kind: "page", title: "Profile", reason: problem });
  return out;
}

function problemsRegion(el) {
  const list = problems();
  const key = JSON.stringify([list, state.failures.map((f) => [f.title, f.reason, f.at.getTime()]), state.notice]);
  patch(el, key, () => [
    ...state.failures.map((f, i) => h("div", { class: "problem refused", role: "alert" },
      h("strong", {}, f.title), h("span", {}, f.reason),
      f.action ? h("button", { class: "act", onclick: () => { state.failures.splice(i, 1); f.action.run(); } }, f.action.label) : null,
      h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: () => { state.failures.splice(i, 1); render(); } }, "×"))),
    ...list.map((p) => h("div", { class: `problem ${p.kind}` }, h("strong", {}, p.title), h("span", {}, p.reason),
      p.at ? h("span", { class: "at" }, p.at) : null)),
    state.notice ? h("div", { class: "notice" }, state.notice,
      h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: () => { state.notice = null; render(); } }, "×")) : null,
  ]);
}

// ---------------------------------------------------------------- gestures

async function move(unit, direction) {
  const refusal = R.lockRefusal(state.locked, "move");
  if (refusal) { fail(`Not moved: ${unit.name}`, { reason: refusal }); render(); return; }
  const answer = await request("POST", "/live/move", { processor: unit.name, direction });
  if (!answer.ok) fail(`Not moved: ${unit.name} ${direction}`, answer);
  render();
  kick();
}

async function undo(skip = false) {
  const answer = await request("POST", "/live/undo", skip ? { skip: true } : {});
  if (!answer.ok) {
    fail(skip ? "Not skipped" : "Not undone", answer,
      answer.code === "stale" && !skip ? { label: "Skip it", run: () => undo(true) } : null);
  } else {
    state.notice = skip ? `Gave up on: ${R.describeEntry(answer.body.skipped)}` : `Undone: ${R.describeEntry(answer.body.undone)}`;
  }
  render();
  kick();
}

// Start, stop and switch (#111). One at a time: while one runs, from this
// page or another, the picker offers nothing (the agent would answer 409
// busy) and says what is running. The answer's reason is shown on failure,
// and a switch says how it was made and why.
async function lifecycle(op, name) {
  if (state.pending) return;
  clearTimeout(state.confirmTimer);
  state.confirming = false;
  state.pending = { op, name };
  render();
  const call = R.lifecycleCall(op, name);
  let answer;
  try {
    answer = await request("POST", call.path, call.json);
  } finally {
    state.pending = null;
  }
  const said = R.lifecycleOutcome(op, name, answer);
  if (said.ok) {
    state.notice = said.text;
    // Follow the rig that is up now; after a stop, keep the one stopped, so
    // starting it again is one tap.
    state.chosen = op === "stop" ? name : null;
  } else {
    fail(said.title, { reason: said.reason, code: answer.code });
  }
  render();
  kick();
  refreshRigs(true).then(render);
}

// A model change restarts the amp, so it takes a confirm that says the
// sound will cut out (R.MODEL_WARNING), and is refused while locked.
async function changeAmpModel(model) {
  const refusal = R.lockRefusal(state.locked, "amp_model");
  if (refusal) { fail(`Amp model not changed: ${R.modelName(model)}`, { reason: refusal }); render(); return; }
  if (state.ampPending) return;
  state.ampPending = model;
  state.ampChoice = null;
  render();
  let answer;
  try {
    answer = await request("POST", R.ampModelCall(model).path, R.ampModelCall(model).json);
  } finally {
    state.ampPending = null;
  }
  const said = R.ampModelOutcome(model, answer);
  if (said.ok) state.notice = said.text;
  else fail(said.title, { reason: said.reason, code: answer.code });
  render();
  kick();
  refreshAmpModels(true).then(render);
}

function armStop() {
  state.confirming = true;
  clearTimeout(state.confirmTimer);
  state.confirmTimer = setTimeout(() => { state.confirming = false; render(); }, CONFIRM_MS);
  render();
}

// The picker: config/rigs/ as a list, the running rig marked, one chosen, and
// what can be done with it (`R.rigPicker`). The rack's left column, as the
// mockups' S1 has it; on the stage, below the cards and only while unlocked.
function rigsRegion(el) {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  const running = rig.running ? rig.config_name : null;
  const view = R.rigPicker(state.health, state.rigs, state.chosen ?? running, state.pending);
  const stop = view.actions.find((a) => a.op === "stop" && !a.disabled);
  const confirming = state.confirming && !!stop;
  patch(el, JSON.stringify([view, confirming]), () => [
    h("h2", {}, "RIGS"),
    view.problem ? h("p", { class: "rig-problem" }, view.problem) : null,
    view.rows.length ? h("div", { class: "rig-list", role: "group", "aria-label": "Rigs in config/rigs" },
      view.rows.map((r) => h("button", {
        class: `rig${r.up ? " up" : ""}${r.chosen ? " chosen" : ""}`, "aria-pressed": String(r.chosen),
        onclick: () => { state.chosen = r.name; state.confirming = false; render(); },
      }, h("span", { class: "n" }, r.name), r.up ? h("span", { class: "tag" }, "up now") : null))) : null,
    view.busy ? h("p", { class: "rig-busy", role: "status" }, view.busy) : null,
    confirming
      ? h("div", { class: "confirm", role: "group", "aria-label": "Confirm stopping the rig" },
        h("p", {}, `Stop ${stop.name || "the rig"}? The sound stops until a rig is started again.`),
        h("button", { class: "rig-act danger", onclick: () => lifecycle("stop", stop.name) }, "Stop now"),
        h("button", { class: "rig-act", onclick: () => { clearTimeout(state.confirmTimer); state.confirming = false; render(); } }, "Cancel"))
      : h("div", { class: "rig-actions" }, view.actions.map((a) => h("button", {
        class: `rig-act${a.primary ? " primary" : ""}`, disabled: a.disabled,
        onclick: () => (a.op === "stop" ? armStop() : lifecycle(a.op, a.name)),
      }, a.label))),
  ]);
}

// ---------------------------------------------------------------- the amp

// One of the amp's parameters as a fader, in its own unit (dB), sent as it
// moves through the same Sender as a knob: one request in flight, the
// newest wins, a refusal shown beside it with the agent's reason.
function ampFader(f) {
  const key = `a\u0000${f.name}`;
  const input = h("input", {
    type: "range", min: f.min, max: f.max, step: f.step, disabled: f.disabled,
    "aria-label": `amp ${f.label}`,
  });
  const value = h("span", { class: "value" });
  const note = h("span", { class: "note" });
  const draw = (v, unsaved) => {
    if (document.activeElement !== input || !sender.busy(key)) input.value = String(v ?? (f.min + f.max) / 2);
    const text = R.ampText(f, v);
    value.textContent = text;
    input.setAttribute("aria-valuetext", text);
    const failed = state.failed.get(key);
    const notes = [];
    if (failed) notes.push(`refused: ${failed}`);
    if (unsaved) notes.push(`was ${R.ampText(f, unsaved.from)}`);
    if (v === null) notes.push("never sent: at the plugin's own default, which is not known here");
    note.textContent = notes.join(" · ");
    el.classList.toggle("unsaved", !!unsaved);
    el.classList.toggle("failed", !!failed);
  };
  input.addEventListener("input", () => {
    const v = Number(input.value);
    sender.change(key, v, (x) => R.ampParameterCall(f.name, x));
    draw(v, null);
  });
  const el = h("label", { class: "fader" }, h("span", { class: "label" }, f.label), input, value, note);
  state.updaters.push((ctx) => {
    const body = state.amp && state.amp.ok ? state.amp.body : null;
    draw(sender.displayed(key, R.ampValueOf(body, f.name), ctx.seq), ctx.unsaved.get(key) || null);
  });
  return el;
}

// The rack's amp card: the model and what it declares, the panel's faders,
// Quality shown and not on a fader, and the model picker with its warning.
function ampRegion(el, rebuilt) {
  const view = R.ampCard(state.amp, state.ampModels, { locked: state.locked, pending: state.ampPending });
  if (rebuilt) state.keys.delete(el);
  patch(el, JSON.stringify([view, state.ampChoice]), () => {
    if (view.state === "hidden") return [];
    if (view.state !== "up") {
      return [h("h2", {}, "AMP"), h("p", { class: view.state === "failed" ? "missing" : "amp-none" }, view.why)];
    }
    const chosen = state.ampChoice && view.picker.options.find((o) => o.model === state.ampChoice);
    const select = h("select", { "aria-label": "Amp model", disabled: !view.picker.canChange },
      h("option", { value: "" }, "Choose another model…"),
      view.picker.options.map((o) => h("option", { value: o.model, disabled: !o.usable, title: o.why }, o.label)));
    select.value = chosen ? chosen.model : "";
    select.addEventListener("change", () => { state.ampChoice = select.value || null; render(); });
    return [
      h("div", { class: "amp-head" },
        h("span", { class: "swatch", style: "background:#E07A73" }),
        h("div", { class: "who" }, h("h2", {}, "AMP"), h("span", { class: "amp-model" }, view.model ? view.model.name : "no model"),
          h("span", { class: "sub" }, view.known === "panel" ? view.note : "in Carla · values are what was last sent, not read back"))),
      view.model && view.model.problem ? h("p", { class: "missing" }, view.model.problem) : null,
      view.model && view.model.warning ? h("p", { class: "warn" }, view.model.warning) : null,
      view.model && view.model.description ? h("details", { class: "amp-about" }, h("summary", {}, "What the model declares"),
        h("pre", {}, view.model.description)) : null,
      view.down ? h("p", { class: "missing" }, "Carla is not running: restart the rig to bring the amp back.") : null,
      h("div", { class: "faders" }, view.faders.map(ampFader),
        view.shown.map((p) => h("div", { class: "shown", title: p.note }, h("span", { class: "label" }, p.name), h("span", { class: "value" }, p.text),
          p.note ? h("span", { class: "note" }, p.note) : null))),
      h("div", { class: "amp-picker" },
        h("label", { class: "selector" }, h("span", { class: "label" }, "Model"), select),
        // The warning is said once: under the picker, or in the confirm.
        view.picker.why ? h("p", { class: "sub" }, view.picker.why)
          : !(chosen && view.picker.canChange) ? h("p", { class: "sub" }, view.picker.warning) : null,
        chosen && view.picker.canChange ? h("div", { class: "confirm", role: "group", "aria-label": "Confirm the model change" },
          h("p", {}, `Load ${chosen.label}? ${view.picker.warning}`),
          h("button", { class: "rig-act danger", onclick: () => changeAmpModel(chosen.model) }, `Load ${chosen.label}`),
          h("button", { class: "rig-act", onclick: () => { state.ampChoice = null; render(); } }, "Cancel")) : null),
    ];
  });
}

// The stage's amp: the model's name and the output level only, hidden
// while the lock is on (as the rig picker is).
function stageAmpRegion(el, rebuilt) {
  if (state.locked) { patch(el, "locked", () => []); return; }
  const view = R.stageAmp(state.amp);
  if (rebuilt) state.keys.delete(el);
  patch(el, JSON.stringify(view), () => view ? [
    h("h2", {}, "AMP"), h("span", { class: "amp-model" }, view.name),
    view.output ? ampFader(view.output) : null,
  ] : []);
}

// ---------------------------------------------------------------- the rack (S1)

function rackFrame() {
  const regions = {
    header: h("header", { class: "bar" }),
    problems: h("div", { class: "problems", "aria-live": "polite" }),
    tiles: h("span", { class: "tile-row" }),
    rigs: h("section", { class: "rigs", "aria-label": "Rigs" }),
    channels: h("div", { class: "channel-list" }),
    amp: h("section", { class: "amp-card", "aria-label": "Amp" }),
    rack: h("main", { class: "rack" }),
    changes: h("aside", { class: "changes", "aria-label": "Unsaved changes" }),
  };
  // The amp first: it is in front of Sushi, between the guitar and the board.
  const frame = h("div", { class: "screen rack-screen" }, regions.header, regions.problems,
    h("div", { class: "rack-body" }, h("nav", { class: "channels", "aria-label": "Rigs and channels" }, regions.rigs, regions.channels),
      h("div", { class: "rack-main" }, regions.amp, regions.rack), regions.changes));
  return { frame, regions };
}

function unitRow(track, unit, index) {
  const open = state.open.has(unit.name);
  const panel = unit.panel;
  const primary = R.primaryControls(panel);
  // Closed, a row shows what fits beside its name; open, everything.
  const shown = open ? [] : primary.slice(0, 6);
  const sub = [
    pluginName(unit),
    unit.profile ? "profile" : "no profile: every parameter",
  ].join(" · ");
  const moveButton = (direction) => {
    const possible = R.canMove(track, index, direction);
    return h("button", {
      class: `step${state.locked ? " locked" : ""}`, disabled: !possible,
      "aria-label": `Move ${unit.name} ${direction}${state.locked ? " (locked)" : ""}`,
      title: state.locked ? "Locked: reordering changes the rig's structure" : null,
      onclick: () => move(unit, direction),
    }, state.locked ? lockIcon() : (direction === "up" ? "▲" : "▼"));
  };
  const row = h("article", { class: `unit${open ? " open" : ""}`, "data-unit": unit.name },
    h("div", { class: "steps" }, moveButton("up"), moveButton("down")),
    h("span", { class: "swatch", style: `background:${swatch(unit)}` }),
    h("div", { class: "who" },
      h("button", { class: "name", "aria-expanded": String(open), onclick: () => { open ? state.open.delete(unit.name) : state.open.add(unit.name); render(); } }, unit.name),
      h("span", { class: "sub" }, sub)),
    h("div", { class: "knobs" }, shown.map((c) => control(unit, c, "small")),
      !open && primary.length > shown.length ? h("span", { class: "more" }, `+${primary.length - shown.length} more`) : null),
    bypassButton(unit, "small"));
  if (open) row.append(detail(unit, "big"));
  return row;
}

function detail(unit, size) {
  const panel = unit.panel;
  return h("div", { class: "detail" },
    panel.groups.map((g) => h("section", { class: "group" }, g.label ? h("h3", {}, g.label) : null,
      h("div", { class: "knobs" }, g.controls.map((c) => control(unit, c, size))))),
    panel.advanced.length ? h("section", { class: "group advanced" }, h("h3", {}, "More"),
      h("div", { class: "knobs" }, panel.advanced.map((c) => control(unit, c, "small")))) : null,
    panel.missing.length ? h("p", { class: "missing" },
      `In the profile but not on the running plugin: ${panel.missing.map((c) => c.label).join(", ")}`) : null);
}

function renderRack(regions, model, unsaved, status) {
  patch(regions.header, JSON.stringify([status, state.locked, state.armed]), () => [
    h("svg", { width: 28, height: 28, viewBox: "0 0 28 28", "aria-hidden": "true", class: "mark" }, h("path", { d: "M5 18a9 9 0 0 1 18 0" }), h("path", { d: "M10 18a4 4 0 0 1 8 0" })),
    h("h1", { class: "rig-name" }, status.title), statusLine(status),
    h("span", { class: "spacer" }), regions.tiles, h("a", { class: "go", href: "#stage" }, "Stage"), lockButton(),
  ]);
  tilesRegion(regions.tiles);
  problemsRegion(regions.problems);
  const live = liveBody();
  // With no rig, the message depends on the tone (changing, or down), so
  // the tone is in the key, or it would never be redrawn (found by review).
  const structure = model ? JSON.stringify([model.tracks.map((t) => [t.name, t.chain, t.units.map((u) => [u.uri, u.profile, u.panel])]), state.locked, [...state.open], !!state.names]) : JSON.stringify(["none", status.tone]);
  const rebuilt = patch(regions.rack, structure, () => {
    state.updaters = [];
    if (!model) {
      return h("p", { class: "empty" }, status.tone === "busy" ? "The rig is changing; the rack comes back when it is up."
        : status.tone === "off" ? "No rig to show: nothing is running in Sushi. Choose one under RIGS to start it."
          : "No rig to show: nothing is running in Sushi.");
    }
    return model.tracks.map((track) => h("section", { class: "track", id: `t-${track.name}` },
      h("div", { class: "track-head" }, h("span", { class: "ring", "aria-hidden": "true" }), h("h2", {}, track.name),
        h("span", { class: "hint" }, track.units.length ? `${track.units.length} plugin${track.units.length === 1 ? "" : "s"} · open a unit for all its controls` : "no plugins")),
      track.units.map((u, i) => unitRow(track, u, i)),
      track.send ? h("div", { class: "send" }, h("span", { class: "k" }, "SEND TO MASTER"), control(track.send, track.send.panel.groups[0].controls[0], "small")) : null));
  });
  // After the rack, whose rebuild empties the updaters: the amp's faders are
  // rebuilt with it, so theirs are there again.
  ampRegion(regions.amp, rebuilt);
  rigsRegion(regions.rigs);
  patch(regions.channels, model ? JSON.stringify(model.tracks.map((t) => [t.name, t.units.length])) : "none", () => [
    h("h2", {}, "CHANNELS"),
    model ? model.tracks.map((t) => h("a", { class: "channel", href: `#t-${t.name}` }, h("span", { class: "n" }, t.name),
      h("span", { class: "s" }, t.isMaster ? `master · ${t.units.length} plugin${t.units.length === 1 ? "" : "s"}` : `${t.units.length} plugin${t.units.length === 1 ? "" : "s"}`))) : null,
    h("p", { class: "agent" }, `Agent on ${location.host}. Phone and laptop see the same rig.`),
  ]);
  renderChanges(regions.changes, model, unsaved, live);
}

function findControl(model, processor, parameter) {
  for (const t of model.tracks) for (const u of [...t.units, t.send].filter(Boolean)) {
    if (u.name !== processor) continue;
    for (const c of [...R.primaryControls(u.panel), ...u.panel.advanced]) if (c.name === parameter) return { track: t.name, ctrl: c };
    return { track: t.name, ctrl: null };
  }
  return { track: null, ctrl: null };
}

function changeText(model, item) {
  if (item.kind === "parameter") {
    const { track, ctrl } = findControl(model, item.processor, item.parameter);
    const show = (v) => (ctrl ? R.formatValue(ctrl, v) : (typeof v === "number" ? v.toFixed(3) : "unknown"));
    return { where: [track, item.processor, ctrl ? ctrl.label : item.parameter].filter(Boolean).join(" · "), from: show(item.from), to: show(item.to) };
  }
  if (item.kind === "amp") {
    const param = (state.amp && state.amp.ok ? state.amp.body.parameters || [] : []).find((p) => p.name === item.name) || {};
    return { where: `amp · ${item.name}`, from: R.ampText(param, item.from), to: R.ampText(param, item.to) };
  }
  if (item.kind === "amp_model") return { where: "amp · model", from: R.modelName(item.from), to: R.modelName(item.to) };
  if (item.kind === "bypass") {
    const word = (b) => (b === null || b === undefined ? "unknown" : b ? "bypassed" : "on");
    return { where: `${findControl(model, item.processor).track || "?"} · ${item.processor}`, from: word(item.from), to: word(item.to) };
  }
  return { where: `${item.track} · chain`, from: (item.from || []).join(", ") || "unknown", to: item.to.join(", ") };
}

function renderChanges(el, model, unsaved, live) {
  const next = R.nextUndo(state.log);
  const rows = model ? unsaved.items.map((i) => ({ ...changeText(model, i), pedal: i.source === "midi" })) : [];
  const undoLabel = !state.log ? "Undo: the log could not be read"
    : !next ? "Nothing to undo" : next.barrier ? "Nothing to undo past the rig switch" : `Undo · ${R.describeEntry(next.entry)}`;
  patch(el, JSON.stringify([rows, unsaved.partial, undoLabel]), () => [
    h("h2", {}, rows.length === 1 ? "1 unsaved change" : `${rows.length} unsaved changes`),
    h("p", { class: "sub" }, "made through the agent since the rig came up"),
    rows.map((r) => h("div", { class: "change" }, h("span", { class: "where" }, r.where, r.pedal ? h("span", { class: "badge" }, "pedal") : null),
      h("span", { class: "diff" }, h("s", {}, r.from), " to ", h("b", {}, r.to)))),
    unsaved.partial ? h("p", { class: "warn" }, "The agent's log was trimmed since the rig came up, so older changes may be missing here.") : null,
    h("button", { class: "undo", disabled: !next || !!next.barrier, onclick: () => undo(false) }, undoLabel),
    h("p", { class: "sub" }, "Saving is not on this screen yet (S4). On the rig, ", h("code", {}, "sushi-rig save"), " writes the file."),
  ]);
}

// ---------------------------------------------------------------- the stage (S5)

function stageFrame() {
  const regions = {
    header: h("header", { class: "stage-head" }),
    problems: h("div", { class: "problems", "aria-live": "polite" }),
    levels: h("div", { class: "levels" }),
    amp: h("section", { class: "stage-amp", "aria-label": "Amp" }),
    cards: h("main", { class: "cards" }),
    tiles: h("div", { class: "tiles" }),
    rigs: h("section", { class: "rigs stage-rigs", "aria-label": "Rigs" }),
  };
  const frame = h("div", { class: "screen stage-screen" }, regions.header, regions.problems, regions.levels, regions.amp, regions.cards, regions.rigs,
    h("nav", { class: "stage-nav" }, h("a", { href: "#stage", class: "here", "aria-current": "page" }, "Stage"), h("a", { href: "#rack" }, "Rack")));
  return { frame, regions };
}

function card(unit) {
  const open = state.open.has(unit.name);
  const vals = h("span", { class: "vals" });
  state.updaters.push((ctx) => { vals.textContent = R.summaryOf(unit, ctx.live) || (unit.profile ? "" : "no profile"); });
  const el = h("article", { class: `card${open ? " open" : ""}`, "data-unit": unit.name },
    h("span", { class: "swatch", style: `background:${swatch(unit)}` }),
    h("button", { class: "open-card", "aria-expanded": String(open), onclick: () => { open ? state.open.delete(unit.name) : state.open.add(unit.name); render(); } },
      h("span", { class: "name" }, unit.name), vals),
    bypassButton(unit, "big"));
  if (open) {
    el.append(h("div", { class: "detail" }, h("div", { class: "knobs" }, R.primaryControls(unit.panel).map((c) => control(unit, c, "big")))));
  }
  return el;
}

function renderStage(regions, model, status) {
  patch(regions.header, JSON.stringify([status, state.locked, state.armed]), () => [
    h("div", { class: "row" }, h("span", { class: `dot tone-${status.tone}`, "aria-hidden": "true" }), h("h1", { class: "rig-name" }, status.title), h("span", { class: "spacer" }), lockButton()),
    h("div", { class: `detail-line tone-${status.tone}`, title: status.tone === "up" ? SOUND_NOTE : null }, status.detail),
    regions.tiles,
  ]);
  tilesRegion(regions.tiles);
  problemsRegion(regions.problems);
  const live = liveBody();
  const levels = model ? R.channelLevels(model, live) : [];
  patch(regions.levels, JSON.stringify(levels), () => levels.length
    ? [h("span", { class: "k" }, "CHANNELS"), ...levels.map((l) => h("span", { class: "level" }, h("span", { class: "n" }, l.channel), h("span", { class: "v" }, l.text)))]
    : []);
  const structure = model ? JSON.stringify([model.tracks.map((t) => [t.name, t.chain, t.units.map((u) => [u.uri, u.profile, u.panel])]), state.locked, [...state.open]]) : JSON.stringify(["none", status.tone, state.locked]);
  const lockNote = () => h("p", { class: `lock-note${state.locked ? " locked" : ""}` }, state.locked ? lockIcon() : null,
    state.locked
      ? "Add, remove and reorder are off while locked, and start, stop and switch are hidden. Knobs and bypass still work. This is the page's lock: the agent does not enforce one yet."
      : "Lock off: the rack can reorder the rig, and the rig can be started, stopped or switched below.");
  const rebuilt = patch(regions.cards, structure, () => {
    state.updaters = [];
    if (!model) {
      return [h("p", { class: "empty" }, status.tone === "busy" ? "The rig is changing; the cards come back when it is up."
        : "No rig to show: nothing is running in Sushi."), lockNote()];
    }
    return [
      ...model.tracks.filter((t) => t.units.length).map((t) => h("section", { class: "track" }, h("h2", {}, t.name), t.units.map(card))),
      lockNote(),
    ];
  });
  stageAmpRegion(regions.amp, rebuilt);
  // Hidden, not disabled, while locked: on stage a Stop within reach of a
  // thumb is the mistake the lock is there to prevent.
  if (state.locked) patch(regions.rigs, "locked", () => []);
  else rigsRegion(regions.rigs);
}

// ---------------------------------------------------------------- render

function currentScreen() {
  if (location.hash === "#stage") return "stage";
  if (location.hash === "" && matchMedia("(max-width: 640px)").matches) return "stage";
  return "rack";
}

function render() {
  const app = document.getElementById("app");
  try {
    const screen = currentScreen();
    if (state.screen !== screen) {
      const built = screen === "stage" ? stageFrame() : rackFrame();
      app.replaceChildren(built.frame);
      state.screen = screen;
      state.regions = built.regions;
      state.updaters = [];
    }
    const live = liveBody();
    const model = live ? R.rackModel(live, state.plugins, state.profiles) : null;
    const rig = state.health && state.health.ok ? state.health.body.rig : null;
    const amp = state.amp && state.amp.ok ? state.amp.body : null;
    const unsaved = live ? R.unsavedChanges(state.log, live, rig, amp) : { items: [], partial: false };
    const status = R.rigStatus(state.health, state.live);
    document.title = `${status.title} · sushi-rig`;
    if (screen === "stage") renderStage(state.regions, model, status);
    else renderRack(state.regions, model, unsaved, status);
    if (live) {
      const keyed = new Map();
      for (const item of unsaved.items) {
        if (item.kind === "parameter") keyed.set(`p\u0000${item.processor}\u0000${item.parameter}`, item);
        else if (item.kind === "bypass") keyed.set(`b\u0000${item.processor}`, item);
        else if (item.kind === "amp") keyed.set(`a\u0000${item.name}`, item);
      }
      const ctx = { live, seq: state.seq, unsaved: keyed };
      for (const update of state.updaters) update(ctx);
    }
  } catch (error) {
    // Loud: a page that drew nothing would read as a rig with nothing on it.
    app.replaceChildren(h("div", { class: "problem refused", role: "alert" },
      h("strong", {}, "The page failed to draw the rig"), h("span", {}, String(error && error.stack || error))));
    state.screen = null;
  }
}

// A channel's link is a hash too; only a change of screen closes what was open.
window.addEventListener("hashchange", () => { if (currentScreen() !== state.screen) state.open.clear(); render(); });
poll().then(pollLoad);
