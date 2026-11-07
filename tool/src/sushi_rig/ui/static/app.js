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
// How long the first tap on "Locked" stays armed: unlocking takes two taps
// inside this, so a pocket or a stray palm cannot do it (S6: deliberate).
const UNLOCK_MS = 4000;
const LOCK_KEY = "sushi-rig.locked";
// amp/models is listed again this often while the page is open, so a model
// copied in shows in the picker without a reload; a failed listing is asked
// for again after RETRY_MS.
const MODELS_MS = 30000;
// GET /takes this often while the rack is shown (only the rack lists the
// takes, and each answer checks takes on the machine playing the audio), and
// at once when a take starts or stops, here or from another page.
const TAKES_MS = 10000;
// The running take's elapsed time is redrawn this often between polls.
const TICK_MS = 250;
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
  ampUpdaters: [],           // the amp region's own, emptied whenever it is rebuilt
  masterUpdaters: [],        // the master fader's, made once with each screen's header
  levelUpdaters: [],         // the stage's channel levels', emptied whenever that region is rebuilt
  stripUpdaters: [],         // the rack's channel strips' volumes, likewise
  chosen: null,              // the rig picked in the picker; null follows the running rig
  pending: null,             // this page's lifecycle command in flight: {op, name}
  saving: null,              // this page's save in flight: {name}, null for the running rig's own file
  saveAsOpen: false, saveAsName: "",  // the Save as… form, and what is typed in it
  saveResult: null,          // what the last save or revert from this page said (R.saveOutcome, R.revertOutcome)
  revert: { open: false, rig: null, list: null, choice: null, pending: false },  // Revert…: GET /rigs/<rig>/saves, the point chosen
  confirming: false, confirmTimer: null,  // Stop's second tap
  record: null, recordBase: null,  // GET /record, with /live; and {elapsed, at} of the take running
  takes: null, takesAt: 0,   // GET /takes, on the rack only: every TAKES_MS, and after a start or stop
  takesOrder: new R.Latest(),  // so an older /takes answer never replaces a newer one
  takeName: "",              // what is typed in the record bar's name
  recordPending: null,       // "start" or "stop", from this page, in flight
  recordResult: null,        // what the last start or stop from this page said (R.recordOutcome)
  // The New rig screen and the rack's channel edits (the rig files):
  roles: null, rolesAt: 0, rolesFor: null,  // GET /machine/roles; read again when the run changes
  rigChannels: {},           // GET /rigs/<rig>/channels, by rig: "start from", and the running rig's inputs
  newRig: { name: "", channels: [], touched: false, pending: false, result: null, addRefused: null },
  wiring: freshWiring(),     // the rack's channel edits: see freshWiring
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

// amp/models, for the amp card's picker: every MODELS_MS (a model copied in
// while the page is open shows without a reload), at once after a model
// change (which one is playing moves), after RETRY_MS while it fails.
async function refreshAmpModels(force = false) {
  const wait = state.ampModels && state.ampModels.ok ? MODELS_MS : RETRY_MS;
  if (!force && state.ampModelsAt && Date.now() - state.ampModelsAt < wait) return false;
  state.ampModels = await request("GET", "/amp/models");
  state.ampModelsAt = Date.now();
  return true;
}

// The machine file's roles (GET /machine/roles), for the New rig screen and
// the rack's channel edits: kept once it answers, and read again when the
// run changes (which tracks use each role is the running rig's), after a
// write, and after RETRY_MS while it fails.
async function refreshRoles(force = false) {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  const run = JSON.stringify([rig.running ? rig.config_name : null, rig.started_at ?? null]);
  if (run !== state.rolesFor) force = true;
  if (!force && state.roles && state.roles.ok) return false;
  if (!force && state.rolesAt && Date.now() - state.rolesAt < RETRY_MS) return false;
  state.rolesFor = run;
  state.rolesAt = Date.now();
  state.roles = await request("GET", "/machine/roles");
  return true;
}

// One rig file's channels (GET /rigs/<rig>/channels), kept by rig; asked
// again after RETRY_MS while it fails, and at once (`force`) after a write.
async function refreshRigChannels(name, force = false) {
  const had = state.rigChannels[name];
  if (!force && had && (had.ok || Date.now() - had.at < RETRY_MS)) return false;
  const answer = await request("GET", `/rigs/${encodeURIComponent(name)}/channels`);
  state.rigChannels[name] = { ...answer, at: Date.now() };
  return true;
}

// Every rig's channels, for "start from".
async function refreshAllRigChannels(force = false) {
  const rigs = state.rigs && state.rigs.ok ? state.rigs.body.rigs : [];
  const fetched = await Promise.all(rigs.map((name) => refreshRigChannels(name, force)));
  return fetched.some(Boolean);
}

// The running rig's own file, for the rack's inputs: read again when the run
// changes (a restart after an edit, a switch), which also closes the forms,
// since they were opened against the run before.
async function refreshRunningChannels() {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  if (!rig.running || !rig.rig) return false;
  const run = JSON.stringify([rig.config_name, rig.started_at ?? null, rig.rig]);
  const changed = run !== state.wiring.for;
  if (changed) {
    // The forms were opened against the run before, so they close; a write
    // in flight, and what the last one said, are kept, since they are about
    // the file and not the run (review, 2026-11-06: a poll landing mid-write
    // replaced the object the write's answer went to, and it was never shown).
    const was = state.wiring;
    const sameRig = was.for !== null && JSON.parse(was.for)[0] === rig.config_name;
    // A result shown is kept without its restart offer, which was for the
    // run before: the run has started again since, from the file.
    const result = sameRig && was.result ? { ...was.result, offer: null } : null;
    state.wiring = { ...freshWiring(), for: run, pending: was.pending, result };
  }
  return refreshRigChannels(rig.config_name, changed);
}

// Whether what the forms are built from has all arrived: the roles, the
// rigs, and every rig's channels ("start from"). The forms are drawn once it
// has, so they are not rebuilt under a finger as each answer comes in.
function filesReady({ sources = true } = {}) {
  if (!sources) return !!state.roles;
  const rigs = state.rigs && state.rigs.ok ? state.rigs.body.rigs : null;
  return !!state.roles && !!rigs && rigs.every((name) => name in state.rigChannels);
}

// The rack's channel edits, as they start and as a new run resets them:
// which channel's input is being changed (and to what), the Add channel
// draft, the write in flight, what the last one said, and the restart confirm.
function freshWiring() {
  return { for: null, editing: null, change: null, adding: null, pending: false, result: null, restartConfirm: false };
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
    const [live, log, amp, record] = await Promise.all([
      R.readsLive(health) ? request("GET", "/live") : null,
      health.ok ? request("GET", "/live/log") : null,
      // The amp is read when /live is, for the same reason: with no rig up
      // it can only be refused (#111).
      R.readsLive(health) ? request("GET", "/amp") : null,
      // And the recorder: with no rig up there is none (R.recordBar says so).
      R.readsLive(health) ? request("GET", "/record") : null,
    ]);
    state.health = health;
    state.live = live;
    state.amp = amp;
    noteRecord(record);
    state.log = log && log.ok ? log.body : null;
    state.seq = seq;
    if (live && live.ok) await refreshPlugins(live.body, rigIdentity(health, state.log));
    render();
    inBackground("profiles", refreshProfiles);
    inBackground("names", refreshNames);
    inBackground("rigs", refreshRigs);
    if (amp && amp.ok) inBackground("amp models", refreshAmpModels);
    if (state.screen === "rack") inBackground("takes", refreshTakes);
    if (state.screen === "newrig") {
      inBackground("roles", refreshRoles);
      inBackground("rig channels", refreshAllRigChannels);
    }
    // The stage reads the running rig's file too: which channels a blend drives.
    if (state.screen === "stage" && health.ok && (health.body.rig || {}).running && (health.body.rig || {}).rig) {
      inBackground("running channels", refreshRunningChannels);
    }
    if (state.screen === "rack" && health.ok && (health.body.rig || {}).running && (health.body.rig || {}).rig) {
      inBackground("roles", refreshRoles);
      inBackground("running channels", refreshRunningChannels);
      // Every rig's channels only while Add channel is open: "start from".
      if (state.wiring.adding) inBackground("rig channels", refreshAllRigChannels);
    }
  } finally {
    state.polling = false;
    state.timer = setTimeout(poll, POLL_MS);
  }
}

// GET /takes every TAKES_MS, or now (`force`) after a take starts or stops,
// and only while the rack, which lists them, is shown. Requests can overlap
// (a forced one while a periodic one is out), so each is numbered and an
// answer older than one already shown is dropped.
async function refreshTakes(force = false) {
  if (state.screen !== "rack") return false;
  if (!force && state.takesAt && Date.now() - state.takesAt < TAKES_MS) return false;
  state.takesAt = Date.now();
  const ticket = state.takesOrder.issue();
  const answer = await request("GET", "/takes");
  if (!state.takesOrder.accept(ticket)) return false;
  state.takes = answer;
  return true;
}

// GET /record's answer, and the base the elapsed time counts on from.
function noteRecord(record) {
  const was = state.recordBase ? state.recordBase.name : null;
  state.record = record;
  const body = record && record.ok ? record.body : null;
  state.recordBase = body && body.state === "recording" ? { elapsed: body.elapsed, at: Date.now(), name: body.name } : null;
  // A take started or stopped, here or from another page: the list is
  // read again now, so a take just stopped is not shown as unfinished.
  const now = state.recordBase ? state.recordBase.name : null;
  if (now !== was) refreshTakes(true).then((fetched) => { if (fetched) render(); });
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

// A value as text, in the control's units: the field under a knob, beside a
// fader, on the stage's levels and the master (2026-11-06). Clicking it
// selects what is there, so a value is typed over, not into. Enter or
// leaving the field commits; Escape puts the value back. `commit(text)`
// decides: `{ok: true, text}` (what to show now) or `{ok: false, why}`,
// refused in place, nothing sent. `show(text)` is the poll's: it never
// writes into a field that has the focus, so a value being typed is not
// pulled from under the fingers once a second.
function valueField(aria, commit, name = null) {
  const input = h("input", {
    type: "text", class: "value-field", name, "aria-label": aria, autocomplete: "off", spellcheck: "false",
    autocapitalize: "none", inputmode: "decimal", enterkeyhint: "done",
  });
  let shown = "";
  let selecting = false;
  // Whether the player has typed since the field took the focus. Only typed
  // text is ever committed: a field left untouched while the value moved
  // under it (the wheel, a pedal, another page) shows the value again on
  // blur or Enter, and never sends the text it had (review, 2026-11-07: a
  // blur committed the stale text and undid the scroll).
  let dirty = false;
  const clearBad = () => { input.classList.remove("bad"); input.removeAttribute("aria-invalid"); input.title = ""; };
  const refresh = () => { clearBad(); input.value = shown; dirty = false; };
  input.addEventListener("focus", () => { selecting = true; dirty = false; input.select(); });
  input.addEventListener("input", () => { dirty = true; });
  // A click's mouseup would put the caret where it landed, undoing the select.
  input.addEventListener("mouseup", (event) => { if (selecting) { event.preventDefault(); selecting = false; } });
  input.addEventListener("pointerdown", (event) => event.stopPropagation());
  const done = (said) => {
    if (said.ok) { clearBad(); shown = said.text; input.value = said.text; dirty = false; return true; }
    input.classList.add("bad");
    input.setAttribute("aria-invalid", "true");
    input.title = said.why;
    return false;
  };
  input.addEventListener("keydown", (event) => {
    event.stopPropagation();  // a knob's arrow keys are the dial's, not the field's
    if (event.key === "Enter") {
      event.preventDefault();
      if (dirty) done(commit(input.value));
      else refresh();
      input.select();
    } else if (event.key === "Escape") {
      event.preventDefault();
      refresh();
      input.blur();
    }
  });
  input.addEventListener("blur", () => {
    selecting = false;
    // Leaving commits what was typed; what cannot be read, or nothing typed,
    // is the value as it is now.
    if (!dirty || !done(commit(input.value))) refresh();
  });
  return {
    input,
    // The control's own gestures (its dial, fader or wheel) take the value
    // from the field: leaving it commits what was typed, as a blur does,
    // and the field then follows the value again.
    release() { if (document.activeElement === input) input.blur(); },
    show(text) {
      shown = text;
      if (document.activeElement !== input) { input.value = text; clearBad(); }
    },
  };
}

// The gestures that set a value, the same on a knob's dial, a fader and a
// level: a vertical drag with the pointer captured (`R.dragPosition`, fine
// with shift or a second finger), the wheel (`R.scrolled`), and the keys
// (`R.stepped`). `get()` is the value now (normalised), `set(v)` sends it.
// The wheel is taken only over `target` itself (the dial, the fader), only
// when `settable()`, and only when meant (`R.takesWheel`): otherwise it is
// the page's, and scrolls it.
function bindGestures(target, ctrl, get, set, { drag: draggable = true, field = null, settable = () => true } = {}) {
  const release = (event) => { if (field && event.target !== field.input) field.release(); };
  for (const type of ["pointerdown", "keydown"]) target.addEventListener(type, release);
  let wheelRest = 0;
  wheelControl(target, settable, (event) => {
    release(event);
    const { notches, rest } = R.wheelNotches(wheelRest, event.deltaY, event.deltaMode);
    wheelRest = rest;
    if (!notches) return;
    const v = R.scrolled(ctrl, get(), notches, event.shiftKey);
    if (v !== get()) set(v);
  });
  let drag = null;
  if (draggable) {
    target.addEventListener("pointerdown", (event) => {
      if (drag) {
        // A second finger on the control while the first drags: fine.
        drag.pointers.add(event.pointerId);
        event.preventDefault();
        return;
      }
      if (event.button !== undefined && event.button > 0) return;
      target.setPointerCapture(event.pointerId);
      drag = { id: event.pointerId, y: event.clientY, p: R.positionOf(ctrl, get() ?? 0), held: null, pointers: new Set([event.pointerId]) };
      target.focus({ preventScroll: true });
      event.preventDefault();
    });
    target.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const dy = event.clientY - drag.y;
      drag.y = event.clientY;
      if (!dy) return;
      const fine = event.shiftKey || drag.pointers.size > 1;
      drag.p = R.dragPosition(drag.p, dy, fine);
      const raw = R.valueAt(ctrl, drag.p);
      const held = fine ? { v: raw, held: null } : R.sticky(ctrl, raw, drag.held);
      drag.held = held.held;
      if (held.v !== get()) set(held.v);
    });
    const end = (event) => {
      if (!drag) return;
      drag.pointers.delete(event.pointerId);
      if (event.pointerId === drag.id) drag = null;
    };
    target.addEventListener("pointerup", end);
    target.addEventListener("pointercancel", end);
  }
  target.addEventListener("keydown", (event) => {
    const steps = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 10, PageDown: -10 };
    let v;
    if (event.key in steps) v = R.stepped(ctrl, get() ?? 0, steps[event.key], event.shiftKey);
    else if (event.key === "Home") v = R.valueAt(ctrl, 0);
    else if (event.key === "End") v = R.valueAt(ctrl, 1);
    else return;
    event.preventDefault();
    set(v);
  });
}

// When the page last scrolled, for `R.takesWheel`: a wheel that is scrolling
// the page carries on scrolling it over a control the pointer passes.
let pageScrolledAt = -Infinity;
window.addEventListener("scroll", () => { pageScrolledAt = Date.now(); }, { capture: true, passive: true });

// The wheel over `el`, given to `turn(event)` when `R.takesWheel` says it is
// the control's, and left to the page (no preventDefault) when not.
function wheelControl(el, settable, turn) {
  let enteredAt = null;
  el.addEventListener("pointerenter", () => { enteredAt = Date.now(); });
  el.addEventListener("pointerleave", () => { enteredAt = null; });
  el.addEventListener("wheel", (event) => {
    const now = Date.now();
    if (enteredAt === null) enteredAt = now;  // arrived under a still pointer: the rest starts now
    if (!R.takesWheel({ settable: settable(), focused: document.activeElement === el, now, enteredAt, scrolledAt: pageScrolledAt })) {
      return;
    }
    event.preventDefault();
    turn(event);
  }, { passive: false });
}

// A typed value for a control, for `valueField`: parsed, clamped, sent.
function typedInto(ctrl, set, format = (v) => R.formatValue(ctrl, v)) {
  return (text) => {
    const said = R.parseTyped(ctrl, text);
    if (!said.ok) return said;
    set(said.v);
    return { ok: true, text: format(said.v) };
  };
}

function knob(unit, ctrl, size) {
  const key = `p\u0000${unit.name}\u0000${ctrl.name}`;
  const inert = ctrl.readOnly || ctrl.missing;
  const r = size === "big" ? 26 : 13, w = size === "big" ? 6 : 4, box = 2 * r + 2 * w;
  const sweep = 1.5 * Math.PI * r, round = 2 * Math.PI * r;
  const arc = h("circle", { cx: box / 2, cy: box / 2, r, class: "arc", "stroke-width": w, transform: `rotate(135 ${box / 2} ${box / 2})` });
  const note = h("span", { class: "note" });
  const dial = h("span", {
    class: "dial", role: "slider", tabindex: inert ? -1 : 0,
    "aria-label": `${unit.name} ${ctrl.label}`, "aria-valuemin": 0, "aria-valuemax": 100,
    "aria-readonly": inert ? "true" : null,
  }, h("svg", { width: box, height: box, viewBox: `0 0 ${box} ${box}`, "aria-hidden": "true" },
    h("circle", { cx: box / 2, cy: box / 2, r, class: "track", "stroke-width": w, "stroke-dasharray": `${sweep} ${round}`, transform: `rotate(135 ${box / 2} ${box / 2})` }),
    arc));
  let current = null;
  const set = (v) => {
    v = Math.min(1, Math.max(0, v));
    sender.change(key, v, (x) => ({ path: "/live/parameter", json: { processor: unit.name, parameter: ctrl.name, value: x } }));
    draw(v);
  };
  const field = valueField(`${unit.name} ${ctrl.label} value`, typedInto(ctrl, set));
  if (inert) field.input.readOnly = true;
  const el = h("div", { class: `knob k-${size}${inert ? " inert" : ""}`, "data-control": key, title: ctrl.description || null },
    dial, h("span", { class: "text" }, field.input, h("span", { class: "label", title: ctrl.label }, ctrl.label), note));
  // Every note is said in one line that is always there, so a refusal
  // arriving or going does not move anything (2026-11-06); the whole of it
  // is in its title.
  const draw = (v) => {
    current = v;
    const p = v === null ? 0 : R.positionOf(ctrl, v);
    arc.setAttribute("stroke-dasharray", `${p * sweep} ${round}`);
    const text = R.formatValue(ctrl, v);
    field.show(text);
    dial.setAttribute("aria-valuenow", Math.round(p * 100));
    dial.setAttribute("aria-valuetext", text);
    const failed = state.failed.get(key);
    const notes = [...ctrl.notes];
    if (ctrl.missing) notes.unshift("not on the running plugin");
    else if (ctrl.readOnly) notes.unshift("read-only");
    if (failed) notes.unshift(`refused: ${failed}`);
    note.textContent = notes.join(" · ");
    note.title = note.textContent;
    el.classList.toggle("failed", !!failed);
  };
  if (!inert) bindGestures(dial, ctrl, () => current, set, { field });
  state.updaters.push((ctx) => draw(sender.displayed(key, R.valueOf(ctx.live, unit.name, ctrl.name), ctx.seq)));
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

// Save (S4): the running rig into its own file, or as a new one (`name`),
// which then is the running rig. The agent writes the file and does not
// commit it; what it wrote, the commit it sits on, or why it refused, is
// shown under the buttons. Allowed locked: a save changes neither the sound
// nor the rig's structure.
async function save(name = null) {
  if (state.saving) return;
  state.saving = { name };
  render();
  const call = R.saveCall(name);
  let answer;
  try {
    answer = await request("POST", call.path, call.json);
  } finally {
    state.saving = null;
  }
  state.saveResult = R.saveOutcome(name, answer);
  if (answer.ok && name) { state.saveAsOpen = false; state.saveAsName = ""; }
  render();
  kick();
  // A save-as adds a rig to config/rigs/, which the picker lists.
  if (name) refreshRigs(true).then(render);
  if (state.revert.open) refreshSaves();
}

// RECORD and STOP (S7). Each is one tap, locked or not: recording is a
// stage action, and Stop takes no confirm, since stopping a take loses
// nothing. What the recorder says is shown under the buttons, and the bar
// follows GET /record, not this page's idea of whether a take runs.
async function recordStart() {
  if (state.recordPending) return;
  const problem = R.checkTakeName(state.takeName);
  if (problem) { state.recordResult = { ok: false, title: "Not recording", reason: problem }; render(); return; }
  state.recordPending = "start";
  render();
  const call = R.recordCall(state.takeName);
  let answer;
  try {
    answer = await request("POST", call.path, call.json);
  } finally {
    state.recordPending = null;
  }
  const said = R.recordOutcome("start", answer);
  // A take that started is said by the bar itself; only one that replaced
  // an earlier take, or was refused, needs a line under it.
  state.recordResult = said.ok && !said.text ? null : said;
  if (answer.ok) { noteRecord(answer); state.takeName = ""; }
  render();
  kick();
  refreshTakes(true).then(render);
}

async function recordStop() {
  if (state.recordPending) return;
  state.recordPending = "stop";
  render();
  let answer;
  try {
    answer = await request("POST", R.STOP_CALL.path, R.STOP_CALL.json);
  } finally {
    state.recordPending = null;
  }
  state.recordResult = R.recordOutcome("stop", answer);
  if (answer.body && answer.body.name) noteRecord(null);
  render();
  kick();
  refreshTakes(true).then(render);
}

// The running take's elapsed time, redrawn in place between polls.
function tickElapsed() {
  const shown = R.clockText(R.elapsedNow(state.recordBase, Date.now()));
  for (const el of document.querySelectorAll(".record-elapsed")) el.textContent = shown || "";
}

// Start, stop and switch (#111). One at a time: while one runs, from this
// page or another, the picker offers nothing (the agent would answer 409
// busy) and says what is running. The answer's reason is shown on failure,
// and a switch says how it was made and why.
async function lifecycle(op, name, opts = {}) {
  if (state.pending) return false;
  clearTimeout(state.confirmTimer);
  state.confirming = false;
  state.pending = { op, name };
  render();
  const call = R.lifecycleCall(op, name, opts);
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
    // A restart that would drop unsaved changes is refused by the agent;
    // dropping them is a second, deliberate tap, never the default.
    const discard = op === "restart" && answer.code === "unsaved"
      ? { label: "Restart anyway, dropping them", run: () => lifecycle("restart", name, { discard: true }) } : null;
    fail(said.title, { reason: said.reason, code: answer.code }, discard);
  }
  render();
  kick();
  refreshRigs(true).then(render);
  return said.ok;
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
function rigsRegion(el, { newRig = false } = {}) {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  const running = rig.running ? rig.config_name : null;
  const view = R.rigPicker(state.health, state.rigs, state.chosen ?? running, state.pending);
  const stop = view.actions.find((a) => a.op === "stop" && !a.disabled);
  const confirming = state.confirming && !!stop;
  patch(el, JSON.stringify([view, confirming, newRig]), () => [
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
    // The New rig screen, from the rack's picker only: making a rig is not a
    // stage action, so the stage's picker does not offer it.
    newRig ? h("a", { class: "rig-act new-rig", href: "#new-rig" }, "New rig…") : null,
  ]);
}

// ---------------------------------------------------------------- the amp

// One of the amp's parameters as a fader, in its own unit (dB), sent as it
// moves through the same Sender as a knob: one request in flight, the
// newest wins, a refusal shown beside it with the agent's reason. Its updater
// goes in `state.ampUpdaters`, which the amp region empties as it rebuilds:
// the amp region is rebuilt on its own (a model picked, the list arriving),
// and an updater left in the rack's list for an input no longer on the page
// was a list that grew with every rebuild (found by review).
//
// The value beside it is a text field and the wheel steps it, as a knob's
// (2026-11-06); a drag holds whole dB as a knob's does. The amp takes real
// values, so the control here (`R.makeControl` over the parameter's range)
// is only for the decisions, and each value is sent as its real value.
function ampFader(f) {
  const key = `a\u0000${f.name}`;
  const ctrl = R.makeControl(f.name, { automatable: true, value: null, min_domain_value: f.min, max_domain_value: f.max },
    { label: f.label, unit: f.unit, scale: "linear" });
  const input = h("input", {
    type: "range", min: f.min, max: f.max, step: f.step, disabled: f.disabled,
    "aria-label": `amp ${f.label}`,
  });
  const note = h("span", { class: "note" });
  let current = null;  // the real value shown
  const real = (v) => R.realOf(ctrl, v);
  const set = (v) => {
    // To a nanounit: a value round-tripped through the normalised domain
    // carries float noise (-6.400000000000002) that Carla need not be sent.
    const x = Number(Math.min(f.max, Math.max(f.min, real(v))).toFixed(9));
    sender.change(key, x, (y) => R.ampParameterCall(f.name, y));
    draw(x);
  };
  const field = valueField(`amp ${f.label} value`, (text) => {
    if (f.disabled) return { ok: false, why: "The amp cannot be changed from here now." };
    const said = R.parseTyped(ctrl, text);
    if (!said.ok) return said;
    set(said.v);
    return { ok: true, text: R.ampText(f, real(said.v)) };
  });
  if (f.disabled) field.input.readOnly = true;
  const draw = (v) => {
    current = v;
    if (document.activeElement !== input || !sender.busy(key)) input.value = String(v ?? (f.min + f.max) / 2);
    const text = R.ampText(f, v);
    field.show(text);
    input.setAttribute("aria-valuetext", text);
    const failed = state.failed.get(key);
    const notes = [];
    if (failed) notes.push(`refused: ${failed}`);
    if (v === null) notes.push("never sent: at the plugin's own default, which is not known here");
    note.textContent = notes.join(" · ");
    note.title = note.textContent;
    el.classList.toggle("failed", !!failed);
  };
  let held = null;
  input.addEventListener("input", () => {
    const snapped = R.sticky(ctrl, R.normalisedOf(ctrl, Number(input.value)), held);
    held = snapped.held;
    set(snapped.v);
  });
  input.addEventListener("change", () => { held = null; });
  const el = h("label", { class: "fader" }, h("span", { class: "label" }, f.label), input, field.input, note);
  const norm = () => (current === null ? null : R.normalisedOf(ctrl, current));
  if (!f.disabled) bindGestures(input, ctrl, norm, set, { drag: false, field });
  state.ampUpdaters.push((ctx) => {
    const body = state.amp && state.amp.ok ? state.amp.body : null;
    draw(sender.displayed(key, R.ampValueOf(body, f.name), ctx.seq));
  });
  return el;
}

// The rack's amp card: the model and what it declares, the panel's faders,
// Quality shown and not on a fader, and the model picker with its warning.
function ampRegion(el) {
  const view = R.ampCard(state.amp, state.ampModels, { locked: state.locked, pending: state.ampPending });
  patch(el, JSON.stringify([view, state.ampChoice]), () => {
    state.ampUpdaters = [];
    if (view.state === "hidden") return [];
    if (view.state !== "up") {
      return [h("h2", {}, "AMP"), h("p", { class: view.state === "failed" ? "missing" : "amp-none" }, view.why)];
    }
    const chosen = state.ampChoice && view.picker.options.find((o) => o.model === state.ampChoice);
    const select = h("select", { "aria-label": "Amp model", disabled: !view.picker.canChange },
      h("option", { value: "" }, "Choose another model…"),
      view.picker.options.map((o) => h("option", { value: o.model, disabled: !o.usable, title: o.why || (o.declared ? `It calls itself ${o.declared}` : null) }, o.label)));
    select.value = chosen ? chosen.model : "";
    select.addEventListener("change", () => { state.ampChoice = select.value || null; render(); });
    return [
      h("div", { class: "amp-head" },
        h("span", { class: "swatch", style: "background:#E07A73" }),
        h("div", { class: "who" }, h("h2", {}, "AMP"), h("span", { class: "amp-model", title: view.model ? view.model.stored : null }, view.model ? view.model.name : "no model"),
          view.model && view.model.declared ? h("span", { class: "amp-declared" }, `The file calls itself ${view.model.declared}`) : null,
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
function stageAmpRegion(el) {
  if (state.locked) { patch(el, "locked", () => { state.ampUpdaters = []; return []; }); return; }
  const view = R.stageAmp(state.amp);
  patch(el, JSON.stringify(view), () => { state.ampUpdaters = []; return view ? [
    h("h2", {}, "AMP"), h("span", { class: "amp-model" }, view.name),
    view.output ? ampFader(view.output) : null,
  ] : []; });
}

// ---------------------------------------------------------------- volume: the master's and each channel's (2026-11-06)

// A volume fader: in dB from -∞ to +6 (R.volumeSlider), the value as a text
// field, the wheel, the keys and a drag that holds whole dB, all on one
// processor's `gain` through POST /live/parameter, so it is logged, undone
// and saved as any knob is. The master's on both headers (`processor`
// R.MASTER) and each channel's (its send) at the top of its row and on the
// stage. Not behind the lock: volume is a performance control, and takes no
// confirm. `updaters` is the list its updater goes in, for the region that
// holds it; with no rig up it is there, off, and says so. `blended(ctx)` is
// the MIDI control that drives this gain, or null: then the fader follows
// the pedal and is not settable (R.blendedBy says why).
function volumeFader({ where, processor, label, aria, control, updaters, blended = () => null }) {
  const key = `p\u0000${processor}\u0000gain`;
  const input = h("input", {
    type: "range", min: R.VOLUME_OFF_SLIDER, max: R.VOLUME_MAX_DB, step: 0.1, "aria-label": aria,
    name: `volume-${processor}`,  // so a region rebuilt around it can give the focus back (`refocus`)
  });
  let ctrl = control(null);
  let current = null;  // normalised, as Sushi has it
  let held = null;
  let live = false;
  let driven = null;
  const settable = () => live && !driven;
  const dbNow = () => R.volumeDb(ctrl, current);
  const set = (db) => {
    if (!settable()) return;
    const kept = R.volumeClamp(db);
    if (kept === null) return;
    const v = R.volumeValueOf(ctrl, kept);
    sender.change(key, v, (x) => ({ path: "/live/parameter", json: { processor, parameter: "gain", value: x } }));
    draw(v);
  };
  const field = valueField(`${aria} value`, (text) => {
    if (!live) return { ok: false, why: "No rig is up to set it on." };
    if (driven) return { ok: false, why: `Driven by ${driven}: the pedal sets it.` };
    const said = R.parseVolume(text);
    if (!said.ok) return said;
    set(said.db);
    return { ok: true, text: R.volumeText(said.db) };
  }, `volume-${processor}-value`);
  const note = h("span", { class: "driven" });
  const el = h("div", { class: `volume volume-${where}`, role: "group", "aria-label": aria, "data-control": key },
    h("span", { class: "k" }, label), input, field.input, note);
  const draw = (v) => {
    current = v;
    const db = dbNow();
    if (document.activeElement !== input || !sender.busy(key)) input.value = String(R.volumeSlider(db));
    const text = live ? R.volumeText(db) : "—";
    field.show(text);
    input.setAttribute("aria-valuetext", text);
    const failed = state.failed.get(key);
    el.classList.toggle("failed", !!failed);
    el.classList.toggle("is-driven", !!driven);
    note.textContent = driven ? `driven by ${driven}` : "";
    el.title = failed ? `refused: ${failed}`
      : driven ? `Driven by ${driven}, a MIDI blend: it follows the pedal, so it is not set from here (and a save does not write it).`
        : live ? "-∞, then -60 to +6 dB: drag, scroll or type. Saved as its level." : "No rig is up.";
  };
  input.addEventListener("input", () => {
    const snapped = R.volumeSticky(R.volumeOfSlider(Number(input.value)), held);
    held = snapped.held;
    set(snapped.db);
  });
  input.addEventListener("change", () => { held = null; });
  let wheelRest = 0;
  for (const type of ["pointerdown", "keydown"]) input.addEventListener(type, () => field.release());
  // The fader itself takes the wheel, when settable and meant (`wheelControl`).
  wheelControl(input, settable, (event) => {
    field.release();
    const { notches, rest } = R.wheelNotches(wheelRest, event.deltaY, event.deltaMode);
    wheelRest = rest;
    if (notches) set(R.volumeStepped(dbNow(), notches, event.shiftKey).db);
  });
  input.addEventListener("keydown", (event) => {
    const steps = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 10, PageDown: -10 };
    if (event.key in steps) set(R.volumeStepped(dbNow(), steps[event.key], event.shiftKey).db);
    else if (event.key === "Home") set(-Infinity);
    else if (event.key === "End") set(R.VOLUME_MAX_DB);
    else return;
    event.preventDefault();
  });
  updaters.push((ctx) => {
    live = !!ctx.live;
    driven = live ? blended(ctx) : null;
    input.disabled = !settable();
    field.input.readOnly = !settable();
    if (ctx.live) ctrl = control(ctx.live);
    draw(ctx.live ? sender.displayed(key, R.valueOf(ctx.live, processor, "gain"), ctx.seq) : null);
  });
  return el;
}

function masterFader(where) {
  return volumeFader({ where, processor: R.MASTER, label: "MASTER", aria: "Master volume",
    control: (live) => R.masterControl(live), updaters: state.masterUpdaters });
}

// The MIDI control driving a channel's send, from the running rig's file.
function blendOf(channel) {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  return rig.running ? R.blendedBy(state.rigChannels[rig.config_name] || null, channel) : null;
}

// A channel's volume, its send's gain (the channel's `level:`), on the rack
// at the top of its row and on the stage among the levels.
function channelVolume(track, where, updaters) {
  return volumeFader({ where, processor: track.send.name, label: "VOLUME", aria: `${track.name} volume`,
    control: (live) => R.gainControl(live, track.send.name), updaters, blended: () => blendOf(track.name) });
}

// ---------------------------------------------------------------- the rack (S1)

function rackFrame() {
  const regions = {
    title: h("h1", { class: "rig-name" }),
    status: h("span", { class: "status-slot" }),
    lock: h("span", { class: "lock-slot" }),
    problems: h("div", { class: "problems", "aria-live": "polite" }),
    tiles: h("span", { class: "tile-row" }),
    rigs: h("section", { class: "rigs", "aria-label": "Rigs" }),
    channels: h("div", { class: "channel-list" }),
    wiring: h("section", { class: "wiring strips", "aria-label": "Channels and their inputs" }),
    amp: h("section", { class: "amp-card", "aria-label": "Amp" }),
    rack: h("main", { class: "rack" }),
    changes: h("div", { class: "changes-list" }),
    save: h("section", { class: "save", "aria-label": "Save" }),
    takes: h("section", { class: "takes", "aria-label": "Takes" }),
  };
  // The header is built once, its parts patched on their own: the master
  // fader in it keeps its focus and a drag in progress whatever else in the
  // bar changes (the status line moves on every minute of uptime). The
  // master sits after the rig's name, before anything whose width changes.
  regions.header = h("header", { class: "bar" },
    h("svg", { width: 28, height: 28, viewBox: "0 0 28 28", "aria-hidden": "true", class: "mark" }, h("path", { d: "M5 18a9 9 0 0 1 18 0" }), h("path", { d: "M10 18a4 4 0 0 1 8 0" })),
    regions.title, masterFader("rack"), regions.status,
    h("span", { class: "spacer" }), regions.tiles, h("a", { class: "go", href: "#stage" }, "Stage"), regions.lock);
  // The amp first: it is in front of Sushi, between the guitar and the board.
  const frame = h("div", { class: "screen rack-screen" }, regions.header, regions.problems,
    // A mixing desk (2026-11-06): the channel strips on the left, the chains
    // and the amp in the middle, the files (rigs, save, revert, takes) on the
    // right, the master in the header. At phone width they stack in that
    // order, the strips first: volume is what is reached for.
    h("div", { class: "rack-body" }, h("nav", { class: "channels", "aria-label": "Channel strips" }, regions.wiring),
      h("div", { class: "rack-main" }, regions.amp, regions.rack),
      h("div", { class: "side files", "aria-label": "Rigs and files" }, regions.rigs,
        h("aside", { class: "changes", "aria-label": "Undo, save and revert" }, regions.changes, regions.save),
        regions.takes, regions.channels)));
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
      h("span", { class: "name-line" },
        h("button", { class: "name", "aria-expanded": String(open), onclick: () => { open ? state.open.delete(unit.name) : state.open.add(unit.name); render(); } }, unit.name),
        monoBadge(unit)),
      h("span", { class: "sub" }, sub)),
    h("div", { class: "knobs" }, shown.map((c) => control(unit, c, "small")),
      !open && primary.length > shown.length ? h("span", { class: "more" }, `+${primary.length - shown.length} more`) : null),
    bypassButton(unit, "small"));
  if (open) row.append(detail(unit, "big"));
  return row;
}

// The small "mono" mark on a plugin the compiler copies to stereo after
// (R.monoNote), its reason in the title: a warning where it follows a stereo
// plugin, whose width it collapses. Fixed in its line: it comes and goes
// only with the chain, never with a value.
function monoBadge(unit) {
  const note = unit.monoNote;
  if (!note) return null;
  return h("span", { class: `mono-badge${note.warn ? " warn" : ""}`, title: note.title, "aria-label": note.title }, note.badge);
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
  patch(regions.title, status.title, () => status.title);
  patch(regions.status, JSON.stringify(status), () => statusLine(status));
  patch(regions.lock, JSON.stringify([state.locked, state.armed]), () => lockButton());
  tilesRegion(regions.tiles);
  problemsRegion(regions.problems);
  const live = liveBody();
  // With no rig, the message depends on the tone (changing, or down), so
  // the tone is in the key, or it would never be redrawn (found by review).
  const structure = model ? JSON.stringify([model.tracks.map((t) => [t.name, t.chain, t.units.map((u) => [u.uri, u.profile, u.panel, u.monoNote])]), state.locked, [...state.open], !!state.names]) : JSON.stringify(["none", status.tone]);
  patch(regions.rack, structure, () => {
    state.updaters = [];
    if (!model) {
      return h("p", { class: "empty" }, status.tone === "busy" ? "The rig is changing; the rack comes back when it is up."
        : status.tone === "off" ? "No rig to show: nothing is running in Sushi. Choose one under RIGS to start it."
          : "No rig to show: nothing is running in Sushi.");
    }
    return model.tracks.map((track) => h("section", { class: "track", id: `t-${track.name}` },
      // A channel's volume is its strip's, in the left column: one control
      // per channel (the small "send to master" knob that ended each row,
      // and was the same gain, is gone, 2026-11-06).
      h("div", { class: "track-head" }, h("span", { class: "ring", "aria-hidden": "true" }), h("h2", {}, track.name),
        h("span", { class: "hint" }, track.units.length ? `${track.units.length} plugin${track.units.length === 1 ? "" : "s"} · open a unit for all its controls` : "no plugins")),
      track.units.map((u, i) => unitRow(track, u, i))));
  });
  ampRegion(regions.amp);
  rigsRegion(regions.rigs, { newRig: true });
  stripsRegion(regions.wiring, model, unsaved);
  patch(regions.channels, "agent", () => h("p", { class: "agent" }, `Agent on ${location.host}. Phone and laptop see the same rig.`));
  renderChanges(regions.changes);
  saveRegion(regions.save, unsaved);
  takesRegion(regions.takes);
}

// A time of today as the page shows one (the log's are to the second).
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// Undo, above Save: an action, not a view of history. The itemised list of
// unsaved changes that stood here, and the "was" beside each knob, were
// removed (2026-11-06): they came and went as values changed, which moved
// what was under the player's hand. What is unsaved is the count on Save;
// history is Revert.
function renderChanges(el) {
  const next = R.nextUndo(state.log);
  const undoLabel = !state.log ? "Undo: the log could not be read"
    : !next ? "Nothing to undo" : next.barrier ? "Nothing to undo past the rig switch" : `Undo · ${R.describeEntry(next.entry)}`;
  patch(el, JSON.stringify([undoLabel]), () => [
    h("h2", {}, "SAVE"),
    h("button", { class: "undo", title: undoLabel, disabled: !next || !!next.barrier, onclick: () => undo(false) }, undoLabel),
  ]);
}

// Save, Save as… and Revert… (S4), under Undo. Its own region, keyed without
// what is typed in the name and without the unsaved count, so neither a
// poll nor a turned knob rebuilds it under the fingers: the count is drawn
// into the dot in place (`drawSaveDot`), the name's check from the input's
// own handler.
function saveRegion(el, unsaved) {
  const view = R.saveView(state.health, unsaved, state.saving, state.log, clock);
  const last = R.lastSaveText(R.lastSave(state.log), clock);
  const typing = document.activeElement && document.activeElement.name === "rig-name";
  const { dot, ...keyed } = view;
  const revert = state.revert.open ? revertKey(view) : null;
  const rebuilt = patch(el, JSON.stringify([keyed, state.saveAsOpen, state.saveResult, last, revert]), () => {
    const result = state.saveResult;
    return [
      h("div", { class: "save-acts" },
        h("button", { class: "save-act primary save-main", disabled: view.save.disabled, onclick: () => save(null) },
          h("span", { class: "save-label" }, view.save.label),
          h("span", { class: "save-dot", "aria-hidden": "true" }),
          h("span", { class: "spoken" })),
        h("button", { class: "save-act", disabled: view.saveAs.disabled, "aria-expanded": String(state.saveAsOpen),
          onclick: () => { state.saveAsOpen = !state.saveAsOpen; render(); } }, view.saveAs.label),
        h("button", { class: "save-act revert-open", disabled: view.revert.disabled, "aria-expanded": String(state.revert.open),
          onclick: () => toggleRevert(view.running) }, view.revert.label)),
      state.saveAsOpen ? saveAsForm(view) : null,
      state.revert.open ? revertPanel(view) : null,
      h("p", { class: "sub save-note" }, view.note),
      result ? h("div", { class: `save-result ${result.ok ? "ok" : "refused"}`, role: result.ok ? "status" : "alert" },
        h("strong", {}, result.title),
        h("span", {}, result.ok ? result.text : result.reason),
        result.ok ? h("span", { class: "commit" }, result.commit) : null,
        result.action ? h("button", { class: "rig-act danger", onclick: result.action.run }, result.action.label) : null,
        h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: () => { state.saveResult = null; render(); } }, "×")) : null,
      last ? h("p", { class: "sub last-save" }, `Last saved ${last.time} to `, h("code", {}, last.file), `, ${last.then}.`) : null,
    ];
  });
  drawSaveDot(el, dot);
  if (rebuilt && typing) {
    const input = el.querySelector('input[name="rig-name"]');
    if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }
}

// The count on Save, in place: a box of its own width that is always there.
function drawSaveDot(el, dot) {
  const box = el.querySelector(".save-dot");
  if (!box) return;
  box.textContent = dot.text;
  box.classList.toggle("has", dot.count > 0);
  el.querySelector(".save-main .spoken").textContent = dot.spoken;
  el.querySelector(".save-main").title = dot.title;
}

// ---------------------------------------------------------------- revert to a previous save

// What the revert panel shows, for its region's key: the list as of this
// minute (its "5 min ago" moves on), the choice, and the confirm's count.
function revertKey(view) {
  const r = state.revert;
  const rows = R.revertRows(r.list, Date.now(), clockDay);
  const row = rows.rows.find((x) => x.id === r.choice) || null;
  const count = view.dot.count;
  return { rig: r.rig, rows, row, confirm: row ? R.revertConfirm(row, r.list, count, r.rig, clockDay) : null, pending: r.pending };
}

// A time with its day when it is not today's: a save of yesterday at 14:02
// is not one of today's.
function clockDay(iso) {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return String(iso);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return when.toDateString() === new Date().toDateString() ? time
    : `${when.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

async function refreshSaves() {
  const rig = state.revert.rig;
  if (!rig) return;
  const answer = await request("GET", R.savesCall(rig).path);
  if (state.revert.rig === rig) state.revert.list = answer;
  render();
}

function toggleRevert(rig) {
  const r = state.revert;
  if (r.open) { Object.assign(r, { open: false, choice: null }); render(); return; }
  Object.assign(r, { open: true, rig, list: null, choice: null });
  render();
  refreshSaves();
}

function revertPanel(view) {
  const key = revertKey(view);
  const { rows } = key;
  const choose = (id) => { state.revert.choice = id; render(); };
  return h("div", { class: "revert", role: "group", "aria-label": `Revert ${key.rig} to a previous save` },
    h("h3", {}, `Revert ${key.rig} to…`),
    rows.loading ? h("p", { class: "sub" }, "Reading the saves…") : null,
    rows.problem ? h("p", { class: "rig-problem" }, rows.problem) : null,
    rows.empty ? h("p", { class: "sub" }, "No saves yet, and git has no commits of this file: Save keeps one each time.") : null,
    rows.rows.length ? h("ul", { class: "revert-list" }, rows.rows.map((row) => h("li", {},
      h("button", { class: `revert-point${row.id === state.revert.choice ? " chosen" : ""}`, "data-point": row.id,
        "aria-pressed": String(row.id === state.revert.choice), onclick: () => choose(row.id) },
      h("span", { class: "n" }, row.title, row.current ? h("span", { class: "tag" }, "the file now") : null),
      h("span", { class: "s" }, row.sub))))) : null,
    key.confirm ? h("div", { class: "confirm revert-confirm", role: "group", "aria-label": "Confirm the revert" },
      h("p", {}, key.confirm.title),
      h("ul", { class: "lost" }, h("li", {}, key.confirm.plays), key.confirm.lost.map((line) => h("li", {}, line))),
      h("button", { class: "rig-act danger", disabled: !!key.pending,
        onclick: () => revertTo(key.rig, key.row, key.confirm.discard) }, key.pending ? "Reverting…" : key.confirm.go),
      h("button", { class: "rig-act", onclick: () => choose(null) }, "Cancel")) : null,
    rows.notes.map((note) => h("p", { class: "sub" }, note)));
}

// POST /rigs/<rig>/revert, then what it said, under the buttons. A refusal
// for unsaved work the page had not counted offers dropping it, as a
// second, deliberate tap.
async function revertTo(rig, row, discard) {
  const r = state.revert;
  if (r.pending) return;
  r.pending = true;
  render();
  const call = R.revertCall(rig, row.id, discard);
  let answer;
  try {
    answer = await request("POST", call.path, call.json);
  } finally {
    r.pending = false;
  }
  const said = R.revertOutcome(rig, answer);
  if (!said.ok && said.code === "unsaved") {
    said.action = { label: "Revert anyway, dropping them", run: () => { state.saveResult = null; revertTo(rig, row, true); } };
  }
  state.saveResult = said;
  if (answer.ok) Object.assign(r, { open: false, choice: null });
  render();
  kick();
  if (r.open) refreshSaves();
}

function saveAsForm(view) {
  const check = h("p", { class: "save-check", "aria-live": "polite" });
  const go = h("button", { class: "save-act primary", type: "submit" });
  const input = h("input", { name: "rig-name", type: "text", autocomplete: "off", spellcheck: "false",
    autocapitalize: "none", placeholder: "e.g. electric-board-2", "aria-describedby": "save-check" });
  check.id = "save-check";
  input.value = state.saveAsName;
  const draw = () => {
    const problem = R.checkRigName(input.value, view.running);
    check.textContent = input.value ? (problem || `Writes config/rigs/${input.value}.yaml, which becomes the running rig.`) : "";
    check.classList.toggle("bad", !!problem && !!input.value);
    go.disabled = !!problem || view.saveAs.disabled;
    go.textContent = problem ? "Save as" : `Save as ${input.value}`;
  };
  input.addEventListener("input", () => { state.saveAsName = input.value; draw(); });
  draw();
  return h("form", { class: "save-as", onsubmit: (event) => {
    event.preventDefault();
    if (!R.checkRigName(input.value, view.running)) save(input.value);
  } },
    h("label", {}, h("span", {}, "New rig name"), input), check,
    h("div", { class: "save-acts" }, go,
      h("button", { class: "save-act", type: "button", onclick: () => { state.saveAsOpen = false; render(); } }, "Cancel")));
}

// ---------------------------------------------------------------- recording (S7, S8)

// The stage's record bar: the take's name, Record and Stop, and while a take
// runs its name and elapsed time. Keyed without what is typed and without
// the elapsed time (redrawn by `tickElapsed`), so neither a poll nor the
// clock rebuilds the field under the fingers.
function recordBarView(typed = state.takeName) {
  return R.recordBar(state.health, state.record, typed, state.recordPending);
}

function recordRegion(el) {
  const view = recordBarView();
  const typing = document.activeElement && document.activeElement.name === "take-name";
  const result = state.recordResult;
  // What follows the typing (the check, Record's state) is drawn by the
  // field's own handler (`drawNameCheck`), not by a rebuild.
  const rebuilt = patch(el, JSON.stringify([recordBarView(""), result]), () => {
    const recording = view.state === "recording";
    el.className = `record-bar${recording ? " recording" : ""}${view.state === "unavailable" ? " off" : ""}`;
    return [
      h("div", { class: "record-head" },
        h("span", { class: "rec-dot", "aria-hidden": "true" }),
        h("h2", {}, view.title), h("span", { class: "spacer" }),
        recording ? h("span", { class: "record-elapsed", role: "timer", "aria-label": "Elapsed" }) : null),
      recording
        ? h("div", { class: "take-now" }, h("span", { class: "k" }, "Take name"), h("span", { class: "v" }, view.name))
        : view.state === "idle" ? nameField(view) : null,
      view.reason ? h("p", { class: "record-note" }, view.reason) : null,
      view.state === "idle" ? h("p", { class: "record-note", id: "take-note", "aria-live": "polite" }) : null,
      recording && view.note ? h("p", { class: "record-note" }, view.note) : null,
      view.stems ? h("p", { class: "record-stems" }, view.stems) : null,
      h("div", { class: "record-acts" },
        h("button", { class: "record-act rec", disabled: view.record.disabled, onclick: recordStart }, view.record.label),
        h("button", { class: "record-act stop", disabled: view.stop.disabled, onclick: recordStop }, view.stop.label)),
      result ? h("div", { class: `record-result ${result.ok ? "ok" : "refused"}`, role: result.ok ? "status" : "alert" },
        h("strong", {}, result.title), result.ok ? (result.text ? h("span", {}, result.text) : null) : h("span", {}, result.reason),
        h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: () => { state.recordResult = null; render(); } }, "×")) : null,
    ];
  });
  if (rebuilt && typing) {
    const input = el.querySelector('input[name="take-name"]');
    if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }
  drawNameCheck(el);
  tickElapsed();
}

function drawNameCheck(el) {
  const view = recordBarView();
  if (view.state !== "idle") return;
  const note = el.querySelector("#take-note");
  const button = el.querySelector("button.record-act.rec");
  if (note) { note.textContent = view.note || ""; note.classList.toggle("bad", !!view.bad); }
  if (button) { button.disabled = view.record.disabled; button.textContent = view.record.label; }
}

function nameField(view) {
  const input = h("input", { name: "take-name", type: "text", autocomplete: "off", spellcheck: "false",
    autocapitalize: "none", placeholder: "e.g. riff-idea", "aria-describedby": "take-note",
    enterkeyhint: "go" });
  input.value = state.takeName;
  // Redrawn through render(), whose key holds the view and not the text, so
  // the check under the field follows the typing without a rebuild.
  input.addEventListener("input", () => { state.takeName = input.value; drawNameCheck(input.closest(".record-bar")); });
  input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !recordBarView().record.disabled) recordStart(); });
  return h("label", { class: "take-name" }, h("span", {}, "Take name"), input);
}

// The rack's takes (S8): each take's name, length and `take check`'s verdict.
function takesRegion(el) {
  const body = state.record && state.record.ok ? state.record.body : null;
  const recording = body && body.state === "recording" ? body.name : null;
  const view = R.takeRows(state.takes, recording);
  patch(el, JSON.stringify(view), () => [
    h("h2", {}, "TAKES"),
    view.dir ? h("p", { class: "sub" }, h("code", {}, view.dir)) : null,
    view.problem ? h("p", { class: "sub" }, view.problem) : null,
    view.rows.length ? h("ul", { class: "take-list", "aria-label": "Takes" }, view.rows.map((r) => h("li", { class: `take ${r.tone}`, title: r.title },
      h("span", { class: "take-top" }, h("span", { class: "n" }, r.name), r.len ? h("span", { class: "len" }, r.len) : null),
      h("span", { class: "note" }, r.note)))) : (view.dir && !view.problem ? h("p", { class: "sub" }, "No takes yet.") : null),
    view.more ? h("p", { class: "sub" }, `and ${view.more} older, not listed`) : null,
    view.archived ? h("p", { class: "sub" }, `${view.archived} earlier take${view.archived === 1 ? "" : "s"} kept in _archive/`) : null,
  ]);
}

// ---------------------------------------------------------------- the stage (S5)

function stageFrame() {
  const regions = {
    row: h("div", { class: "row" }),
    detail: h("div", { class: "detail-line" }),
    problems: h("div", { class: "problems", "aria-live": "polite" }),
    record: h("section", { class: "record-bar", "aria-label": "Record" }),
    levels: h("div", { class: "levels" }),
    amp: h("section", { class: "stage-amp", "aria-label": "Amp" }),
    cards: h("main", { class: "cards" }),
    tiles: h("div", { class: "tiles" }),
    rigs: h("section", { class: "rigs stage-rigs", "aria-label": "Rigs" }),
  };
  // Built once, its parts patched on their own, as the rack's is: the master
  // fader under the rig's state keeps a drag and its focus through a poll.
  regions.header = h("header", { class: "stage-head" }, regions.row, regions.detail, masterFader("stage"), regions.tiles);
  const frame = h("div", { class: "screen stage-screen" }, regions.header, regions.problems, regions.record, regions.levels, regions.amp, regions.cards, regions.rigs,
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
      h("span", { class: "name" }, unit.name, monoBadge(unit)), vals),
    bypassButton(unit, "big"));
  if (open) {
    el.append(h("div", { class: "detail" }, h("div", { class: "knobs" }, R.primaryControls(unit.panel).map((c) => control(unit, c, "big")))));
  }
  return el;
}

function renderStage(regions, model, status) {
  patch(regions.row, JSON.stringify([status.tone, status.title, state.locked, state.armed]), () => [
    h("span", { class: `dot tone-${status.tone}`, "aria-hidden": "true" }), h("h1", { class: "rig-name" }, status.title),
    h("span", { class: "spacer" }), lockButton()]);
  regions.detail.className = `detail-line tone-${status.tone}`;
  regions.detail.title = status.tone === "up" ? SOUND_NOTE : "";
  patch(regions.detail, status.detail, () => status.detail);
  tilesRegion(regions.tiles);
  problemsRegion(regions.problems);
  recordRegion(regions.record);  // not behind the lock: recording is a stage action
  const live = liveBody();
  // Each channel's level, editable as the rack's send knob is (a text
  // field and the wheel): keyed by the channels, never by their values.
  const sends = model ? model.tracks.filter((t) => t.send) : [];
  patch(regions.levels, JSON.stringify(sends.map((t) => [t.name, t.send.name, t.send.panel])), () => {
    state.levelUpdaters = [];
    return sends.length ? [h("span", { class: "k" }, "CHANNELS"), ...sends.map((t) => h("div", { class: "level" },
      h("span", { class: "n" }, t.name), channelVolume(t, "stage", state.levelUpdaters)))] : [];
  });
  const structure = model ? JSON.stringify([model.tracks.map((t) => [t.name, t.chain, t.units.map((u) => [u.uri, u.profile, u.panel, u.monoNote])]), state.locked, [...state.open]]) : JSON.stringify(["none", status.tone, state.locked]);
  const lockNote = () => h("p", { class: `lock-note${state.locked ? " locked" : ""}` }, state.locked ? lockIcon() : null,
    state.locked
      ? "Add, remove and reorder are off while locked, and start, stop and switch are hidden. Knobs and bypass still work. This is the page's lock: the agent does not enforce one yet."
      : "Lock off: the rack can reorder the rig, and the rig can be started, stopped or switched below.");
  patch(regions.cards, structure, () => {
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
  stageAmpRegion(regions.amp);
  // Hidden, not disabled, while locked: on stage a Stop within reach of a
  // thumb is the mistake the lock is there to prevent.
  if (state.locked) patch(regions.rigs, "locked", () => []);
  else rigsRegion(regions.rigs);
}

// ---------------------------------------------------------------- rig files
// The New rig screen (#new-rig, from the rack's picker) and, on the rack, the
// running rig's inputs: "Add channel" and a channel's input change. Each
// writes a file of config/rigs through the agent, which compiles it first and
// refuses with the compiler's reason, shown in place; what the page checks
// before sending is a hint from the agent's own answers (R.newRigView).
// Nothing here is on the stage screen.

// A text field whose typing updates `state` and redraws the checks in place
// (`draw`), never through a rebuild, so a poll cannot take the field from
// under the fingers; a rebuild for another reason puts the focus back.
function textField(label, name, value, onInput, placeholder = null) {
  const input = h("input", { name, type: "text", autocomplete: "off", spellcheck: "false", autocapitalize: "none",
    placeholder, "aria-label": label });
  input.value = value;
  input.addEventListener("input", () => onInput(input.value));
  return h("label", { class: "text-field" }, h("span", {}, label), input);
}

function focusedName(el) {
  const active = document.activeElement;
  return active && el.contains(active) && active.name ? active.name : null;
}

function refocus(el, name) {
  if (!name) return;
  const input = el.querySelector(`[name="${name}"]`);
  if (!input) return;
  input.focus();
  if (input.setSelectionRange && input.type === "text") input.setSelectionRange(input.value.length, input.value.length);
}

function inputPicker(choices, value, onChange, label) {
  const select = h("select", { "aria-label": label },
    h("option", { value: "" }, choices.loading ? "Reading the machine file…" : "Choose an input…"),
    choices.inputs.map((r) => h("option", { value: r.name, title: r.usedBy.length ? `used by ${r.usedBy.join(", ")} now` : null }, r.label)));
  select.value = value || "";
  select.addEventListener("change", () => onChange(select.value));
  return h("label", { class: "selector" }, h("span", { class: "label" }, "Input"), select);
}

// Mono or stereo, stereo offered only where the role allows it: a choice the
// role cannot give is there but disabled, with the patchbay's reason under it.
function widthPicker(widths, value, onChange, label) {
  const select = h("select", { "aria-label": label },
    widths.map((w) => h("option", { value: w.width, disabled: !w.ok, title: w.why }, w.width)));
  select.value = value;
  select.addEventListener("change", () => onChange(select.value));
  const refused = widths.filter((w) => !w.ok && w.why && !w.why.startsWith("Choose"));
  // The reason on a line of its own, after the pickers it explains.
  return [h("label", { class: "selector" }, h("span", { class: "label" }, "Width"), select),
    refused.map((w) => h("p", { class: "sub width-why" }, `${w.width} is not offered: ${w.why}`))];
}

// The width's picker and its reasons, with the start-from picker between,
// so the pickers sit on one row and the reasons go under them.
function pickersThenReasons([width, reasons], source) {
  return [width, source, reasons];
}

function sourcePicker(sources, from, onChange, label) {
  const select = h("select", { "aria-label": label },
    h("option", { value: "" }, "Nothing: an empty chain"),
    sources.map((g) => h("optgroup", { label: g.problem ? `${g.rig} (could not be read)` : g.rig },
      g.options.map((o) => h("option", { value: o.value }, o.label)))));
  select.value = from ? `${from.rig}\u0000${from.channel}` : "";
  select.addEventListener("change", () => {
    const [rig, channel] = select.value ? select.value.split("\u0000") : [];
    onChange(rig ? { rig, channel } : null);
  });
  return h("label", { class: "selector" }, h("span", { class: "label" }, "Start from"), select);
}

// A width the role cannot give is replaced by one it can, when the input
// changes: the choice follows the agent's answer, never the other way.
function fitWidth(choices, draft) {
  const widths = R.widthOptions(choices, draft.input);
  if (!widths.find((w) => w.width === draft.width && w.ok)) {
    const ok = widths.find((w) => w.ok);
    if (ok) draft.width = ok.width;
  }
}

// What a write said, in place: the agent's reason (and the lines to add to
// the machine file, when it gave them), or the file, what git says of it and
// the gesture that plays it.
function rigFileResult(result, dismiss, play) {
  return h("div", { class: `save-result rigfile-result ${result.ok ? "ok" : "refused"}`, role: result.ok ? "status" : "alert" },
    h("strong", {}, result.title),
    h("span", { class: "said" }, result.ok ? result.text : result.reason),
    result.hint ? h("pre", { class: "machine-hint" }, result.hint) : null,
    result.programs ? h("div", { class: "programs-moved", role: "note" },
      h("span", { class: "warn" }, result.programs.note),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Rig"), h("th", {}, "Program before"), h("th", {}, "after"))),
        h("tbody", {}, result.programs.rows.map((r) => h("tr", {}, h("td", {}, r.rig), h("td", {}, String(r.from)), h("td", {}, String(r.to))))))) : null,
    result.ok ? h("span", { class: "commit" }, result.commit) : null,
    result.ok && result.offer ? h("button", { class: "rig-act primary offer", disabled: !!state.pending,
      onclick: () => play(result.offer) }, result.offer.label) : null,
    h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: dismiss }, "×"));
}

function newRigFrame() {
  const regions = {
    header: h("header", { class: "bar" }),
    problems: h("div", { class: "problems", "aria-live": "polite" }),
    form: h("main", { class: "new-rig" }),
  };
  const frame = h("div", { class: "screen rack-screen new-rig-screen" }, regions.header, regions.problems, regions.form);
  return { frame, regions };
}

function renderNewRig(regions, status) {
  patch(regions.header, JSON.stringify([status]), () => [
    h("svg", { width: 28, height: 28, viewBox: "0 0 28 28", "aria-hidden": "true", class: "mark" }, h("path", { d: "M5 18a9 9 0 0 1 18 0" }), h("path", { d: "M10 18a4 4 0 0 1 8 0" })),
    h("h1", { class: "rig-name" }, "New rig"), statusLine(status),
    h("span", { class: "spacer" }), h("a", { class: "go", href: "#rack" }, "Back to the rack"),
  ]);
  problemsRegion(regions.problems);
  const nr = state.newRig;
  const choices = R.roleChoices(state.roles);
  // One channel to start from, once the roles are known, unless the player
  // has been at the form (a form emptied on purpose stays empty).
  if (!nr.touched && nr.channels.length === 0 && choices.inputs.length) nr.channels.push(R.newChannel(choices, []));
  if (!filesReady()) {
    patch(regions.form, "reading", () => h("p", { class: "empty" }, "Reading the machine file's roles and the rigs' channels…"));
    return;
  }
  newRigRegion(regions.form);
}

function newRigView() {
  const nr = state.newRig;
  return R.newRigView(nr, state.roles, state.rigs, state.health, nr.pending);
}

function newRigRegion(el) {
  const nr = state.newRig;
  const view = newRigView();
  const sources = R.sourceChoices(state.rigs, state.rigChannels);
  const focus = focusedName(el);
  // Keyed without what is typed: the names' checks are drawn by `drawNewRigChecks`.
  const key = JSON.stringify([nr.channels.map((c) => [c.input, c.width, c.from]), view.choices, view.channels.map((c) => c.widths),
    sources, view.countText, nr.addRefused, nr.result, nr.pending, view.blocked, !!state.pending]);
  const rebuilt = patch(el, key, () => [
    h("form", { class: "new-rig-form", onsubmit: (event) => { event.preventDefault(); createRig(); } },
      h("p", { class: "sub" }, "A rig of channels, written to ", h("code", {}, "config/rigs/<name>.yaml"),
        `: each channel plays from an input of ${view.choices.machine || "the machine file"}, and their sends mix into a master on `,
        h("code", {}, view.choices.output || "monitor-out"), ". Chains start empty unless a channel starts from another rig's. ",
        "The agent compiles the rig before it writes it, and says why if it will not."),
      view.choices.problem ? h("p", { class: "rig-problem" }, view.choices.problem) : null,
      textField("Rig name", "new-rig-name", nr.name, (v) => { nr.name = v; drawNewRigChecks(el); }, "e.g. two-guitars"),
      h("p", { class: "save-check", "data-check": "new-rig-name" }),
      h("div", { class: "draft-head" }, h("h2", {}, "CHANNELS"), h("span", { class: "sub" }, view.countText)),
      nr.channels.map((c, i) => channelDraft(el, c, i, view, sources)),
      h("div", { class: "save-acts" },
        h("button", { type: "button", class: "save-act add-channel", onclick: addDraftChannel }, "Add channel"),
        h("button", { type: "submit", class: "save-act primary create" }, view.create.label)),
      nr.addRefused ? h("p", { class: "save-check bad add-refused", role: "alert" }, nr.addRefused) : null,
      view.blocked ? h("p", { class: "sub" }, view.blocked) : null),
    nr.result ? rigFileResult(nr.result, () => { nr.result = null; render(); }, playNewRig) : null,
  ]);
  if (rebuilt) refocus(el, focus);
  drawNewRigChecks(el);
}

function channelDraft(el, c, i, view, sources) {
  const nr = state.newRig;
  const touch = () => { nr.touched = true; nr.addRefused = null; };
  return h("fieldset", { class: "channel-draft" },
    h("legend", {}, `Channel ${i + 1}`),
    textField(`Channel ${i + 1} name`, `ch-name-${i}`, c.name, (v) => { c.name = v; touch(); drawNewRigChecks(el); }, "e.g. guitar"),
    inputPicker(view.choices, c.input, (v) => { c.input = v; fitWidth(view.choices, c); touch(); render(); }, `Channel ${i + 1} input`),
    ...pickersThenReasons(widthPicker(view.channels[i].widths, c.width, (v) => { c.width = v; touch(); render(); }, `Channel ${i + 1} width`),
      sourcePicker(sources, c.from, (from) => { c.from = from; touch(); render(); }, `Channel ${i + 1} starts from`)),
    h("p", { class: "save-check", "data-check": `ch-name-${i}` }),
    nr.channels.length > 1 ? h("button", { type: "button", class: "save-act remove",
      onclick: () => { nr.channels.splice(i, 1); touch(); render(); } }, `Remove channel ${i + 1}`) : null);
}

// The checks under the fields and Create's state, from what is typed now.
function drawNewRigChecks(el) {
  const nr = state.newRig;
  const view = newRigView();
  const set = (name, text, bad) => {
    const p = el.querySelector(`[data-check="${name}"]`);
    if (p) { p.textContent = text || ""; p.classList.toggle("bad", !!bad); }
  };
  set("new-rig-name", nr.name ? view.nameProblem || `Writes config/rigs/${nr.name}.yaml.` : "", !!(nr.name && view.nameProblem));
  view.channels.forEach((c, i) => set(`ch-name-${i}`, c.problem, !!c.problem));
  const create = el.querySelector("button.create");
  if (create) { create.disabled = view.create.disabled; create.textContent = view.create.label; }
}

function addDraftChannel() {
  const nr = state.newRig;
  const view = newRigView();
  nr.touched = true;
  // At the ceiling, the button refuses with the compiler's own reason.
  if (view.add.refused) { nr.addRefused = view.add.refused; render(); return; }
  nr.addRefused = null;
  nr.channels.push(R.newChannel(view.choices, nr.channels));
  render();
}

async function createRig() {
  const nr = state.newRig;
  if (nr.pending || newRigView().create.disabled) return;
  const name = nr.name;
  const call = R.newRigCall(name, nr.channels);
  nr.pending = true;
  render();
  let answer;
  try {
    answer = await request("POST", call.path, call.json);
  } finally {
    nr.pending = false;
  }
  nr.result = R.rigFileOutcome("new", name, answer);
  if (answer.ok) Object.assign(nr, { name: "", channels: [], touched: false, addRefused: null });
  render();
  if (answer.ok) {
    await refreshRigs(true);
    refreshRigChannels(name, true).then(render);
    render();
  }
}

// Start or switch to the rig just made, then show it on the rack.
async function playNewRig(offer) {
  const ok = await lifecycle(offer.op, offer.name);
  if (ok) {
    state.newRig.result = null;
    if (state.screen === "newrig") location.hash = "#rack";
  }
}

// The rack's left column: a channel strip per channel, as a mixing desk
// has them (2026-11-06, the layout agreed with the player). Each strip is
// the channel's name, a small, quiet input line (its role and width as the
// running rig's file has them, and "Change input" behind a tap), and its
// volume as the large part (`channelVolume`, the send's gain). Then a strip
// for the master, whose name leads to its chain (its volume is the
// header's), and Add channel at the bottom. An input edit writes the file;
// the rig keeps playing as it was until the restart offered, which takes a
// confirm.
//
// Three parts, each patched on its own key, so nothing that is not a
// strip's own rebuilds it: the head (the file, and why nothing can be
// edited when nothing can), the strips (keyed by the channels alone), and
// the foot (Add channel, an edit's answer, the restart confirm). Each
// strip's input line is patched on its own too. A volume fader is rebuilt
// only when the channels change, so a save from another page, a lifecycle
// command or an edit's answer never ends a drag or takes the focus (review,
// 2026-11-07: one key for the whole column did both).
//
// The strips are the live rig's channels, so their volumes are there even
// while the file is still being read; a channel the file has gained since
// the rig came up is a strip with no volume until the restart plays it.
// With no rig of channels up, the column still says CHANNELS AND INPUTS and
// why nothing can be edited (a v1 run drew nothing at all on the rig).
function stripsRegion(el, model, unsaved) {
  let parts = state.stripParts;
  if (!parts || parts.el !== el) {
    parts = { el, head: h("div", { class: "strips-head" }), list: h("div", { class: "strip-list" }),
      foot: h("div", { class: "strips-foot" }), slots: new Map() };
    el.replaceChildren(parts.head, parts.list, parts.foot);
    state.stripParts = parts;
  }
  const w = state.wiring;
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  const view = R.wiringView(state.health, rig.running ? state.rigChannels[rig.config_name] || null : null, unsaved);
  // While a command runs, /live is not read (R.readsLive) and there is no
  // model; the rig is still up, so its strips stay as they were, faders and
  // all, rather than be rebuilt empty and again a moment later.
  const busyNow = !!(state.health && state.health.ok && (state.health.body.agent || {}).busy);
  if (model) parts.model = model;
  else if (!(busyNow && rig.running)) parts.model = null;
  const shown = parts.model;
  const live = shown ? shown.tracks.filter((t) => t.send) : [];
  const master = shown ? shown.tracks.find((t) => t.isMaster) || null : null;
  const names = live.map((t) => t.name);
  const ready = !view.blocked && filesReady({ sources: false }) && !!state.rigChannels[view.rig];
  const choices = R.roleChoices(state.roles);
  const filed = ready ? view.channels : [];
  const extra = filed.filter((c) => !names.includes(c.name));

  patch(parts.head, JSON.stringify([view.blocked || null, view.file || null, view.problem || null, ready && choices.problem]), () => [
    h("h2", {}, "CHANNELS AND INPUTS"),
    view.blocked ? h("p", { class: "sub inputs-why" }, view.blocked) : null,
    view.file ? h("p", { class: "sub" }, h("code", {}, view.file), " — an input edit writes the file; the rig plays it after a restart") : null,
    view.problem ? h("p", { class: "rig-problem" }, view.problem) : null,
    ready && choices.problem ? h("p", { class: "rig-problem" }, choices.problem) : null,
  ]);

  patch(parts.list, JSON.stringify([live.map((t) => [t.name, t.send.name, t.send.panel]), extra.map((c) => c.name),
    master ? master.units.length : null]), () => {
    state.stripUpdaters = [];
    parts.slots = new Map();
    const strip = (name, volume) => {
      const slot = h("div", { class: "strip-input-slot" });
      parts.slots.set(name, slot);
      return h("section", { class: "strip", "data-channel": name, "aria-label": `${name} channel` },
        h("a", { class: "strip-name", href: `#t-${name}` }, name), slot, volume);
    };
    return [
      live.map((t) => strip(t.name, channelVolume(t, "strip", state.stripUpdaters))),
      extra.map((c) => strip(c.name, h("p", { class: "sub" }, "Added to the file: restart the rig to play it, and its volume comes with it."))),
      master ? h("section", { class: "strip strip-master", "data-channel": R.MASTER, "aria-label": "master" },
        h("a", { class: "strip-name", href: `#t-${R.MASTER}` }, R.MASTER),
        h("p", { class: "sub" }, `The mix: ${master.units.length} plugin${master.units.length === 1 ? "" : "s"} on its chain. `
          + "Its volume is the master's, in the header.")) : null,
    ];
  });

  // Each strip's input line, on its own key.
  for (const [name, slot] of parts.slots) {
    const c = ready ? filed.find((x) => x.name === name) || null : undefined;
    const editing = w.editing === name;
    const focus = focusedName(slot);
    const rebuilt = patch(slot, JSON.stringify([view.blocked ? "blocked" : c === undefined ? "reading" : c, editing,
      editing ? [w.change, choices] : null, !!w.pending, view.rig || null]), () => (
      view.blocked ? null
        : c === undefined ? h("p", { class: "sub strip-input" }, "Reading the rig's file…")
          : c === null ? h("p", { class: "sub strip-input" }, "Not in the running rig's file.")
            : wiringRow(view, c, choices)));
    if (rebuilt) refocus(slot, focus);
  }

  if (view.blocked) { patch(parts.foot, "blocked", () => []); return; }
  const sources = w.adding && filesReady() ? R.sourceChoices(state.rigs, state.rigChannels) : null;
  const busy = state.health && state.health.ok ? (state.health.body.agent || {}).busy || null : null;
  const focus = focusedName(parts.foot);
  const rebuilt = patch(parts.foot, JSON.stringify([ready, view.rig, view.channels.map((c) => c.name), choices,
    w.adding && [w.adding.input, w.adding.width, w.adding.from], sources, w.pending, w.result, w.restartConfirm, busy,
    !!state.pending, view.unsaved]), () => [
    !ready ? null
      : w.adding && !sources ? h("p", { class: "sub" }, "Reading the rigs' channels, to start from…")
        : w.adding ? addChannelForm(parts.foot, view, choices, sources)
          : h("button", { class: "save-act add-channel", disabled: !!w.pending,
            onclick: () => { w.adding = R.newChannel(choices, view.channels); w.result = null; render(); inBackground("rig channels", refreshAllRigChannels); } }, "Add channel"),
    h("p", { class: "warn" }, view.unsaved),
    w.result ? rigFileResult(w.result, () => { w.result = null; render(); }, () => { w.restartConfirm = true; render(); }) : null,
    w.restartConfirm ? h("div", { class: "confirm", role: "group", "aria-label": "Confirm the restart" },
      h("p", {}, `Restart ${view.rig}? The sound stops for a few seconds while it comes back from its file.`),
      h("button", { class: "rig-act danger", disabled: !!state.pending || !!busy, onclick: restartForEdit }, "Restart now"),
      h("button", { class: "rig-act", onclick: () => { w.restartConfirm = false; render(); } }, "Not now")) : null,
  ]);
  if (rebuilt) refocus(parts.foot, focus);
  if (ready) drawAddCheck(parts.foot, view);
}

function wiringRow(view, c, choices) {
  const w = state.wiring;
  const editing = w.editing === c.name;
  const row = h("div", { class: `wiring-row${editing ? " editing" : ""}`, "data-channel": c.name },
    h("span", { class: "s", title: `${c.name}'s input, as the rig's file has it` }, `${c.input} · ${c.width}`));
  if (!editing) {
    row.append(h("button", { class: "rig-act small", disabled: !!w.pending, "aria-label": `Change ${c.name}'s input`,
      onclick: () => { w.editing = c.name; w.change = { input: c.input, width: c.width }; w.result = null; render(); } }, "Change input"));
    return row;
  }
  const change = {};
  if (w.change.input !== c.input) change.input = w.change.input;
  if (w.change.width !== c.width) change.width = w.change.width;
  row.append(
    inputPicker(choices, w.change.input, (v) => { w.change.input = v; fitWidth(choices, w.change); render(); }, `${c.name} input`),
    ...widthPicker(R.widthOptions(choices, w.change.input), w.change.width, (v) => { w.change.width = v; render(); }, `${c.name} width`).flat(),
    h("div", { class: "save-acts" },
      h("button", { class: "save-act primary", disabled: !!w.pending || !Object.keys(change).length,
        onclick: () => changeInput(view.rig, c.name, change) }, `Apply to ${c.name}`),
      h("button", { class: "save-act", onclick: () => { w.editing = null; render(); } }, "Cancel")));
  return row;
}

function addChannelForm(el, view, choices, sources) {
  const w = state.wiring;
  const draft = w.adding;
  return h("form", { class: "add-channel-form", onsubmit: (event) => { event.preventDefault(); addChannel(view.rig, el, view); } },
    h("h3", {}, `Add a channel to ${view.rig}`),
    textField("New channel name", "add-channel-name", draft.name, (v) => { draft.name = v; drawAddCheck(el, view); }, "e.g. keys"),
    h("p", { class: "save-check", "data-check": "add-channel-name" }),
    inputPicker(choices, draft.input, (v) => { draft.input = v; fitWidth(choices, draft); render(); }, "New channel input"),
    ...pickersThenReasons(widthPicker(R.widthOptions(choices, draft.input), draft.width, (v) => { draft.width = v; render(); }, "New channel width"),
      sourcePicker(sources, draft.from, (from) => { draft.from = from; render(); }, "New channel starts from")),
    h("div", { class: "save-acts" },
      h("button", { type: "submit", class: "save-act primary add" }, "Add"),
      h("button", { type: "button", class: "save-act", onclick: () => { w.adding = null; render(); } }, "Cancel")));
}

function drawAddCheck(el, view) {
  const draft = state.wiring.adding;
  const p = el.querySelector('[data-check="add-channel-name"]');
  const add = el.querySelector("button.add");
  if (!draft || !p) return;
  const problem = draft.name ? R.checkChannelName(draft.name, view.channels.map((c) => c.name)) : null;
  p.textContent = problem || "";
  p.classList.toggle("bad", !!problem);
  if (add) {
    add.disabled = !!state.wiring.pending || !draft.name || !!problem || !draft.input;
    add.textContent = draft.name && !problem ? `Add ${draft.name}` : "Add";
  }
}

async function rigFileWrite(call, op, what, rig) {
  if (state.wiring.pending) return null;
  state.wiring.pending = true;
  render();
  let answer;
  try {
    answer = await request("POST", call.path, call.json);
  } finally {
    // `state.wiring`, read now: a refresh may have replaced the object this
    // write began on, and the answer belongs on the one that is drawn.
    state.wiring.pending = false;
  }
  state.wiring.result = R.rigFileOutcome(op, what, answer);
  render();
  if (answer.ok) refreshRigChannels(rig, true).then(render);
  return answer;
}

async function addChannel(rig, el, view) {
  const draft = state.wiring.adding;
  if (!draft || R.checkChannelName(draft.name, view.channels.map((c) => c.name)) || !draft.input) return;
  const answer = await rigFileWrite(R.addChannelCall(rig, draft), "add", `${draft.name} to ${rig}`, rig);
  if (answer && answer.ok) { state.wiring.adding = null; render(); }
}

async function changeInput(rig, channel, change) {
  const answer = await rigFileWrite(R.inputCall(rig, channel, change), "input", `${channel}'s input`, rig);
  if (answer && answer.ok) { state.wiring.editing = null; render(); }
}

async function restartForEdit() {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  state.wiring.restartConfirm = false;
  const ok = await lifecycle("restart", rig.config_name);
  if (ok) state.wiring.result = null;
  render();
}

// ---------------------------------------------------------------- render

function currentScreen() {
  if (location.hash === "#stage") return "stage";
  if (location.hash === "#new-rig") return "newrig";
  if (location.hash === "" && matchMedia("(max-width: 640px)").matches) return "stage";
  return "rack";
}

function render() {
  const app = document.getElementById("app");
  try {
    const screen = currentScreen();
    if (state.screen !== screen) {
      state.screen = screen;
      state.updaters = [];
      state.ampUpdaters = [];
      state.levelUpdaters = [];
      state.stripUpdaters = [];
      state.stripParts = null;
      state.masterUpdaters = [];
      const built = screen === "stage" ? stageFrame() : screen === "newrig" ? newRigFrame() : rackFrame();
      app.replaceChildren(built.frame);
      state.regions = built.regions;
      if (screen === "rack") refreshTakes(true).then((fetched) => { if (fetched) render(); });
      if (screen === "newrig") {
        inBackground("roles", refreshRoles);
        inBackground("rig channels", () => refreshAllRigChannels(true));
      }
    }
    const live = liveBody();
    const model = live ? R.rackModel(live, state.plugins, state.profiles) : null;
    const rig = state.health && state.health.ok ? state.health.body.rig : null;
    const amp = state.amp && state.amp.ok ? state.amp.body : null;
    const unsaved = live ? R.unsavedChanges(state.log, live, rig, amp, state.plugins) : { items: [], partial: false };
    const status = R.rigStatus(state.health, state.live);
    document.title = `${status.title} · sushi-rig`;
    if (screen === "stage") renderStage(state.regions, model, status);
    else if (screen === "newrig") renderNewRig(state.regions, status);
    else renderRack(state.regions, model, unsaved, status);
    const ctx = { live, seq: state.seq };
    if (live) for (const update of [...state.updaters, ...state.ampUpdaters, ...state.levelUpdaters, ...state.stripUpdaters]) update(ctx);
    // The master is drawn with no rig too: there, and off, saying so.
    for (const update of state.masterUpdaters) update(ctx);
  } catch (error) {
    // Loud: a page that drew nothing would read as a rig with nothing on it.
    app.replaceChildren(h("div", { class: "problem refused", role: "alert" },
      h("strong", {}, "The page failed to draw the rig"), h("span", {}, String(error && error.stack || error))));
    state.screen = null;
  }
}

// How many updaters the page holds, for the browser tests to see that the
// count stays flat as regions are rebuilt. Read-only, and nothing else.
window.sushiRigUpdaters = () => ({ rack: state.updaters.length, amp: state.ampUpdaters.length,
  levels: state.levelUpdaters.length, strips: state.stripUpdaters.length, master: state.masterUpdaters.length });

// A channel's link is a hash too; only a change of screen closes what was open.
window.addEventListener("hashchange", () => { if (currentScreen() !== state.screen) state.open.clear(); render(); });
setInterval(tickElapsed, TICK_MS);
poll().then(pollLoad);
