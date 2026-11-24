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
// The rack's open tab, kept in this browser (#135): `R.tabToRemember`'s record.
const TAB_KEY = "sushi-rig.tab";
// amp/models is listed again this often while the page is open, so a model
// copied in shows in the picker without a reload; a failed listing is asked
// for again after RETRY_MS.
const MODELS_MS = 30000;
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

// The rack's open tab is remembered in this browser only, as the lock is: a
// phone and a laptop each keep their own. Storage can be absent or refuse;
// then nothing is remembered and the rack opens on the first channel.
function readTab() {
  try { return localStorage.getItem(TAB_KEY); } catch { return null; }
}
function writeTab(text) {
  try { localStorage.setItem(TAB_KEY, text); } catch { /* the page still works */ }
}

// What this browser kept for the page once and no longer reads
// (`R.RETIRED_KEYS`): the Undos of deletes, kept across reloads until #189.
// Removed as the page loads, so an old record neither lingers nor comes back.
for (const key of R.RETIRED_KEYS) {
  try { localStorage.removeItem(key); } catch { /* storage refused: there is nothing of it to read either */ }
}

const state = {
  health: null, live: null, log: null,
  plugins: null, pluginsFor: null, pluginsRig: null, pluginsError: null, pluginsAt: 0,
  reading: null,             // another rig's plugins being read: {rig, since}; its chain is held back meanwhile
  switches: null,            // the live switches seen in the log (R.switchesSeen), for `rigIdentity`
  ampPlace: null,            // the amp's tab, decided once a run: see placeAmp
  profiles: null, profilesError: null, profilesAt: 0, profileProblems: [],
  names: null, namesAt: 0,   // plugin names by URI, from /library: decoration, so a failure is not shown
  library: null, libraryError: null,  // GET /library's body, for the Library drawer (#130); its failure is said there
  locked: readLocked(), armed: false, armTimer: null,
  load: null, loadView: null, xruns: null, xrunsFor: null, loadTimer: null,  // GET /rig/load, every LOAD_MS
  rigs: null, rigsAt: 0,     // GET /rigs: kept once it answers, read again after a lifecycle command
  amp: null,                 // GET /amp, with /live: what the agent last sent Carla
  ampModels: null, ampModelsAt: 0,  // GET /amp/models: kept once it answers, read again after a model change
  ampChoice: null,           // the model picked in the amp card, awaiting its confirm
  ampPicker: null,           // the amp card's picker, patched on its own key: see ampRegion
  ampPending: null,          // the model change in flight
  ampUpdaters: [],           // the amp region's own, emptied whenever it is rebuilt
  masterUpdaters: [],        // the master fader's, made once with each screen's header
  levelUpdaters: [],         // the stage's channel levels', emptied whenever that region is rebuilt
  stripUpdaters: [],         // the rack's channel strips' volumes, likewise
  chosen: null,              // the rig picked in the picker; null follows the running rig
  // Deleting a rig from the rack's picker (#129): the delete in flight, a
  // refusal (until dismissed), and where the focus goes after. No confirm
  // and no Undo of its own: the one Undo puts a rig back (#209).
  bin: { pending: false, said: null, focus: null },
  rigsRunKey: null,          // the run GET /rigs was last read for (`R.runKeyOf`): its `running` follows the run
  pending: null,             // this page's lifecycle command in flight: {op, name}
  saving: null,              // this page's save in flight: {name}, null for the running rig's own file
  saveAsOpen: false, saveAsName: "",  // the Save as… form, and what is typed in it
  saveResult: null,          // what the last save or revert from this page said (R.saveOutcome, R.revertOutcome)
  saveRestartConfirm: false, // the restart a save-as offers (its file waits for one, #192), asked again before it runs
  revert: { open: false, rig: null, list: null, choice: null, pending: false, drop: null },  // Revert…: GET /rigs/<rig>/saves, the point reverted to, the "Drop changes?" asked
  confirming: false, confirmTimer: null,  // Stop's second tap
  record: null, recordBase: null,  // GET /record, with /live; and {elapsed, at} of the take running
  takesOpen: { pending: false, result: null },  // the rack's Open folder (#189): in flight, and what it said
  takeName: "",              // what the player typed in the record bar's name; "" while it shows the agent's default
  recordPending: null,       // "start" or "stop", from this page, in flight
  recordResult: null,        // what the last start or stop from this page said (R.recordOutcome)
  // The New rig screen and the rack's channel edits (the rig files):
  roles: null, rolesAt: 0, rolesFor: null, rolesRun: null,  // GET /machine/roles; read again when the run changes
  rigChannels: {},           // GET /rigs/<rig>/channels, by rig: "start from", and the running rig's inputs
  newRig: { name: "", channels: [], touched: false, pending: false, result: null, addRefused: null },
  wiring: freshWiring(),     // the rack's channel edits: see freshWiring
  // Pedals (#192): GET /midi, kept by run and read again after an assignment;
  // and the rack's pedal menus: the one open (a target's key), the move it
  // asks to confirm, the assignment in flight, what the last one said (and
  // the restart it offers), the device chosen when the machine has several,
  // and each control's light (`R.pedalLights`).
  midi: null, midiAt: 0, midiFor: null,
  pedalUi: { open: null, confirm: null, pending: null, result: null, restartConfirm: false, device: null,
    lights: {}, refused: null, row: null },
  // Channel presets (#127): GET /presets, and a strip's Preset… panel.
  presets: null, presetsAt: 0,
  presetUi: { channel: null, name: "", choice: "", pending: null, result: null, rig: null },
  open: new Set(),           // units opened, by name
  // The rack's tabs and rows (#135):
  tab: { rig: null, id: null },  // the tab the player chose (or the one remembered), and the rig it is for
  peeked: new Set(),         // bypassed units opened without switching them on, by name (R.unitRow)
  rackParts: null,           // the centre column's parts, made with the rack's frame: see chainRegion
  unitUpdaters: new Map(),   // the rack's units' updaters, by unit: each row is rebuilt on its own
  // The Library drawer and the chain edits (#130):
  drawer: null,              // the drawer open: {track, slot (R.addSlots), opener, query}; null when closed
  drawerParts: null,         // what the open drawer was built with: see drawerRegion
  editsRun: null,            // the rig shown (`shownRun`) the three below belong to: see settleEdits
  unplaced: [],              // answers of adds and removes that came while /health did not answer: see placeAnswer
  adding: new Map(),         // "track\u0000slot" -> {shown, done?, at}: an add from that slot in flight, or confirmed and not drawn yet
  addRefused: new Map(),     // "track\u0000slot" -> the agent's reason an add from there was refused, shown in place
  removing: new Map(),       // unit -> {armed, timer} | {pending} | {reason}: its Remove, in place in its row
  failures: [],              // this page's refused changes, newest first
  failed: new Map(),         // control key -> the reason its last change was refused
  notice: null,
  // The one Undo (#209): GET /undo's answer, a press in flight, what the last
  // press said (`R.undoOutcome`, with its time), and the restart it offers
  // asked again before it runs.
  undo: { answer: null, pending: false, said: null, restartConfirm: false },
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

// `run` is the rig the page shows (`rigIdentity`): a change queued for one
// rig is never sent once the page shows another, where a plugin of the same
// name would take it (review of #135). The agent's /live routes take no
// word of which rig a change is meant for, so one already in flight when
// the rig changes cannot be stopped here.
const sender = new R.Sender((call) => request("POST", call.path, call.json), {
  pollSeq: () => state.pollSeq,
  run: () => JSON.stringify(rigIdentity(state.health)),
  onFailure: (key, result) => {
    state.failed.set(key, result.reason);
    fail(`Not changed: ${key.split("\u0000").slice(1).join(" · ")}`, result);
    render();
  },
  onSent: (key) => { state.failed.delete(key); },
});

function fail(title, result, action = null) {
  state.failures.unshift({ title, reason: result.reason, code: result.code || null, action, at: new Date(), lock: !!result.lock });
  state.failures.length = Math.min(state.failures.length, 4);
}

// Which rig this is, beyond its processors' names: a switch can bring up a
// rig with the same tracks and names and a different plugin under one of
// them, by a restart (a new start time) or live (a new name, or a live
// switch the log shows, `R.switchesSeen`, which counts one only on a
// positive sign: never a log trimmed or unread).
function rigIdentity(health, switches = state.switches) {
  const rig = health && health.ok ? health.body.rig || {} : {};
  return [rig.config_name ?? null, rig.rig ?? null, rig.started_at ?? null, switches ? switches.count : 0];
}

// Which plugin each processor is (GET /live/plugins): due again when the
// chains or the rig changed, and after RETRY_MS while it fails. `pluginsDue`
// says whether it is, and for which rig; `readPlugins` asks, and returns what
// goes into `state`. Neither changes `state`: the poll decides when what was
// read is shown (review of #135, and the container's run of its tests).
//
// It used to drop the plugins held as soon as the chains changed and then
// wait for the answer, with the new /live already shown. A render landing in
// that wait (the load tiles' every two seconds, any fetch coming back) drew
// the new chains with no plugin known: every row was rebuilt as a plain
// list, and again when the answer came, under whatever knob was being
// dragged or value typed. A plugin moved from another device took the field
// from under the fingers six times in fifty here. So:
//  - the same rig with its chains changed (a plugin added, removed or moved)
//    is shown at once with the plugins already held, which still say what
//    every plugin that was there is; the answer then says what a new one is,
//    and rebuilds that row alone;
//  - another rig, or the first sight of one, is not shown until its plugins
//    have been read, so the page is never drawn with one rig's chains and
//    another's plugins, nor with a rig's chains and none.
function pluginsDue(live, identity) {
  const key = JSON.stringify([live.tracks, identity]);
  const retry = state.pluginsError && Date.now() - state.pluginsAt > RETRY_MS;
  return key === state.pluginsFor && !retry ? null : { key, rig: JSON.stringify(identity) };
}

async function readPlugins(due) {
  const answer = await request("GET", "/live/plugins");
  return {
    pluginsFor: due.key, pluginsRig: due.rig, pluginsAt: Date.now(),
    plugins: answer.ok ? answer.body.processors : null, pluginsError: answer.ok ? null : answer.reason,
  };
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
    // Drawn once the first poll has (`state.health`): a fetch from the page's
    // first moment answering before it would draw "Agent not answering".
    .then((fetched) => { if (fetched && state.health) render(); })
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

// The library (GET /library): the plugins' names under each unit, and the
// Library drawer's list (#130). Kept once it answers; a failure is asked
// again after RETRY_MS, and said in the drawer (the names are decoration).
async function refreshNames() {
  if (state.names) return false;
  if (state.namesAt && Date.now() - state.namesAt < RETRY_MS) return false;
  const answer = await request("GET", "/library");
  state.namesAt = Date.now();
  if (answer.ok) {
    state.names = Object.fromEntries(answer.body.plugins.map((p) => [p.uri, p.name]));
    state.library = answer.body;
    state.libraryError = null;
  } else {
    state.libraryError = answer.reason;
  }
  return true;
}

// config/rigs/, for the picker. Kept once it answers; asked again after
// RETRY_MS while it fails, and at once (`force`) after a start, stop or switch.
async function refreshRigs(force = false) {
  // Which row is the running rig's is GET /rigs's to say (#129), so it is
  // read again whenever the run changes, by a pedal or another page too.
  const run = R.runKeyOf(state.health);
  if (state.health && run !== state.rigsRunKey) force = true;
  if (!force && state.rigs && state.rigs.ok) return false;
  if (!force && state.rigsAt && Date.now() - state.rigsAt < RETRY_MS) return false;
  state.rigs = await request("GET", "/rigs");
  state.rigsAt = Date.now();
  if (state.health) state.rigsRunKey = run;
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
  const run = runKey();
  if (run !== state.rolesFor) force = true;
  if (!force && state.roles && state.roles.ok) return false;
  if (!force && state.rolesAt && Date.now() - state.rolesAt < RETRY_MS) return false;
  state.rolesFor = run;
  state.rolesAt = Date.now();
  const answer = await request("GET", "/machine/roles");
  // An answer for a run since replaced is dropped: the newer run's request
  // is out, and its answer is the one to keep.
  if (state.rolesFor !== run) return false;
  // The run this answer is about, set with it: `rolesFor` is set before the
  // request, so it cannot say whether `roles` is still the run before's. The
  // amp's tab (R.ampTab) is only decided from this run's.
  state.roles = answer;
  state.rolesRun = run;
  return true;
}

// The run, as the roles are kept by it: the rig up and when it started (a
// restart is a new run; a live switch, another rig's name).
function runKey() {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  return JSON.stringify([rig.running ? rig.config_name : null, rig.started_at ?? null]);
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

// GET /midi (#192): the devices, what each control may take, and the
// running rig's assignments. Read again when the run changes (a restart
// brings in what an assignment wrote), at once after an assignment, and after
// RETRY_MS while it fails; an answer for a run since replaced is dropped.
async function refreshMidi(force = false) {
  const run = runKey();
  if (run !== state.midiFor) force = true;
  if (!force && state.midi && state.midi.ok) return false;
  if (!force && state.midiAt && Date.now() - state.midiAt < RETRY_MS) return false;
  state.midiFor = run;
  state.midiAt = Date.now();
  const answer = await request("GET", "/midi");
  if (state.midiFor !== run) return false;
  state.midi = answer;
  return true;
}

// config/channels/'s presets (#127), for "start from" and a strip's
// Preset… panel: read when either opens, and again after a save.
async function refreshPresets(force = false) {
  if (!force && state.presets && state.presets.ok) return false;
  if (!force && state.presetsAt && Date.now() - state.presetsAt < RETRY_MS) return false;
  state.presets = await request("GET", "/presets");
  state.presetsAt = Date.now();
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
    const [live, log, amp, record, undoNext] = await Promise.all([
      R.readsLive(health) ? request("GET", "/live") : null,
      health.ok ? request("GET", "/live/log") : null,
      // The amp is read when /live is, for the same reason: with no rig up
      // it can only be refused (#111).
      R.readsLive(health) ? request("GET", "/amp") : null,
      // And the recorder: with no rig up there is none (R.recordBar says so).
      R.readsLive(health) ? request("GET", "/record") : null,
      // What the one Undo would step back (#209): whatever the rig.
      health.ok ? request("GET", R.UNDO_CALL.path) : null,
    ]);
    const logBody = log && log.ok ? log.body : null;
    // See `pluginsDue`. Another rig is shown at once, so that nothing of the
    // rig before stays on the page taking input (review of #135: a knob of
    // the rig switched from, still drawn, would have sent its change to the
    // rig switched to); with no plugins held for it, and its chain held
    // back meanwhile (`state.reading`, `chainRegion`). The same rig's new
    // chains are shown with the plugins already held. Either way the
    // plugins are read after, and drawn when they come.
    const switches = R.switchesSeen(state.switches, logBody,
      health.ok ? (health.body.agent || {}).started_at ?? null : null);
    const due = live && live.ok ? pluginsDue(live.body, rigIdentity(health, switches)) : null;
    const another = !!due && due.rig !== state.pluginsRig;
    state.health = health;
    // Each control's light, from /health's counts (#192): a count that moved since the last poll lights it.
    state.pedalUi.lights = R.pedalLights(state.pedalUi.lights, health, pedalBoard().controller, Date.now());
    state.live = live;
    state.amp = amp;
    noteRecord(record);
    state.log = logBody;
    state.undo.answer = undoNext;
    state.switches = switches;
    state.seq = seq;
    // The visualiser (screensaver.js, #176) runs only while a rig is up.
    document.dispatchEvent(new CustomEvent("sushi-rig:health", { detail: { running: R.visualiserRigRunning(health) } }));
    if (another) {
      Object.assign(state, { plugins: null, pluginsError: null, pluginsFor: null, pluginsRig: due.rig });
      state.reading = { rig: due.rig, since: Date.now() };
      setTimeout(render, HOLD_MS + 20);  // the hold's bound, if the answer is slower
    }
    render();
    if (due) {
      Object.assign(state, await readPlugins(due));
      if (state.reading && state.reading.rig === due.rig) state.reading = null;
      render();
    }
    inBackground("profiles", refreshProfiles);
    inBackground("names", refreshNames);
    inBackground("rigs", refreshRigs);
    if (amp && amp.ok) inBackground("amp models", refreshAmpModels);
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
      inBackground("midi", refreshMidi);
      // Every rig's channels only while Add channel is open: "start from".
      if (state.wiring.adding) inBackground("rig channels", refreshAllRigChannels);
    }
  } finally {
    state.polling = false;
    state.timer = setTimeout(poll, POLL_MS);
  }
}

// GET /record's answer, and the base the elapsed time counts on from.
function noteRecord(record) {
  state.record = record;
  const body = record && record.ok ? record.body : null;
  state.recordBase = body && body.state === "recording" ? { elapsed: body.elapsed, at: Date.now(), name: body.name } : null;
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
      // The visualiser (screensaver.js, #176) stops itself if these rise while it listens.
      if (step) document.dispatchEvent(new CustomEvent("sushi-rig:xruns", { detail: { count: step.seen.count, run } }));
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
  const refresh = () => { clearBad(); input.value = shown; dirty = false; delete input.dataset.typed; };
  input.addEventListener("focus", () => { selecting = true; dirty = false; delete input.dataset.typed; input.select(); });
  input.addEventListener("input", () => { dirty = true; input.dataset.typed = "1"; });
  // A click's mouseup would put the caret where it landed, undoing the select.
  input.addEventListener("mouseup", (event) => { if (selecting) { event.preventDefault(); selecting = false; } });
  input.addEventListener("pointerdown", (event) => event.stopPropagation());
  const done = (said) => {
    if (said.ok) { clearBad(); shown = said.text; input.value = said.text; dirty = false; delete input.dataset.typed; return true; }
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
  // The drag ends, whatever ended it: its pointer released or cancelled, or
  // its capture lost (the dial taken out of the page, the browser taking the
  // pointer). A drag left set when its pointer was gone made every later
  // press a "second finger", jumping from a stale height, and held its row
  // open for good (review of #135).
  // The page is drawn again after, outside whatever event ended the drag:
  // a row held unchanged under the drag (`unitSlot`) takes its changes then.
  const stop = () => { drag = null; delete target.dataset.dragging; setTimeout(render, 0); };
  if (draggable) {
    target.addEventListener("pointerdown", (event) => {
      if (drag && !target.hasPointerCapture(drag.id)) stop();  // a drag whose pointer was lost unseen
      if (drag) {
        // A second finger on the control while the first drags: fine.
        drag.pointers.add(event.pointerId);
        event.preventDefault();
        return;
      }
      if (event.button !== undefined && event.button > 0) return;
      target.setPointerCapture(event.pointerId);
      drag = { id: event.pointerId, y: event.clientY, p: R.positionOf(ctrl, get() ?? 0), held: null, pointers: new Set([event.pointerId]) };
      // A row in use is neither minimised nor moved under the hand (`dragging`).
      target.dataset.dragging = String(event.pointerId);
      target.focus({ preventScroll: true });
      event.preventDefault();
    });
    target.addEventListener("lostpointercapture", (event) => { if (drag && event.pointerId === drag.id) stop(); });
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
      if (event.pointerId === drag.id) stop();
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

// How a knob reaches what it shows: one parameter, or for a profile's
// control of several (`R.makeMix`), each parameter it drives. `send(v)` sets
// it, `read(ctx)` is what to show (the value a send in flight will leave, as
// `sender.displayed` says, else the poll's), `failed()` a refusal to show.
// A mix is only ever read on a poll: what it shows is where its parameters
// are (`R.mixPosition`, the nearest point of its curve), and nothing is sent
// until it is turned, so a page that loads never moves the sound.
function parameterIo(unit, ctrl) {
  const key = `p\u0000${unit.name}\u0000${ctrl.name}`;
  return {
    send: (v) => sender.change(key, v, (x) => ({ path: "/live/parameter", json: { processor: unit.name, parameter: ctrl.name, value: x } })),
    read: (ctx) => ({ v: sender.displayed(key, R.valueOf(ctx.live, unit.name, ctrl.name), ctx.seq), notes: [] }),
    failed: () => state.failed.get(key),
  };
}

function mixIo(unit, ctrl) {
  // One key for the whole control: each turn is one POST /live/parameters
  // of all its levels, so one log entry and one undo, like a step of any
  // knob, and all or none.
  const key = `m\u0000${unit.name}\u0000${ctrl.name}`;
  // Where turning starts from (`R.mixAnchor`), and where it last sent the
  // knob: while the levels are where that put them, the knob is there;
  // otherwise (a load, an undo, another page, a pedal) it starts again
  // from what the levels are, so nothing jumps on the next turn.
  let anchor = null, at = null;
  return {
    send: (v) => {
      if (!anchor) return;  // nothing read yet: nothing to turn from
      at = v;
      const json = (x) => ({
        path: "/live/parameters",
        json: { processor: unit.name, parameters: Object.fromEntries(R.mixPath(ctrl, anchor, x).map((t) => [t.parameter, t.value])) },
      });
      sender.change(key, v, json);
    },
    read: (ctx) => {
      const values = {};
      for (const d of ctrl.drives) values[d.parameter] = R.valueOf(ctx.live, unit.name, d.parameter);
      const pending = sender.busy(key) ? sender.displayed(key, null, ctx.seq) : null;
      if (pending !== null) return { v: pending, notes: [] };
      if (!(anchor && R.onMixPath(ctrl, anchor, at, values))) {
        anchor = R.mixAnchor(ctrl, values);
        at = anchor ? anchor.m0 : null;
      }
      if (!anchor) return { v: null, notes: [] };
      const off = R.mixPath(ctrl, anchor, at).some((t, i) => Math.abs(t.value - R.mixTargets(ctrl, at)[i].value) > R.MIX_OFF_CURVE);
      return { v: at, notes: off ? ["off Mix's curve: turning moves each level from where it is"] : [] };
    },
    failed: () => state.failed.get(key),
  };
}

function knob(unit, ctrl, size) {
  const key = ctrl.kind === "mix" ? `m\u0000${unit.name}\u0000${ctrl.name}` : `p\u0000${unit.name}\u0000${ctrl.name}`;
  const io = ctrl.kind === "mix" ? mixIo(unit, ctrl) : parameterIo(unit, ctrl);
  const inert = ctrl.readOnly || ctrl.missing;
  const r = size === "big" ? 26 : 13, w = size === "big" ? 6 : 4, box = 2 * r + 2 * w;
  const sweep = 1.5 * Math.PI * r, round = 2 * Math.PI * r;
  const arc = h("circle", { cx: box / 2, cy: box / 2, r, class: "arc", "stroke-width": w, transform: `rotate(135 ${box / 2} ${box / 2})` });
  const note = h("span", { class: "note" });
  const dial = h("span", {
    class: "dial", role: "slider", tabindex: inert ? -1 : 0, "data-focus": `dial ${ctrl.name}`,
    "aria-label": `${unit.name} ${ctrl.label}`, "aria-valuemin": 0, "aria-valuemax": 100,
    "aria-readonly": inert ? "true" : null,
  }, h("svg", { width: box, height: box, viewBox: `0 0 ${box} ${box}`, "aria-hidden": "true" },
    h("circle", { cx: box / 2, cy: box / 2, r, class: "track", "stroke-width": w, "stroke-dasharray": `${sweep} ${round}`, transform: `rotate(135 ${box / 2} ${box / 2})` }),
    arc));
  let current = null;
  let readNotes = [];
  const set = (v) => {
    v = Math.min(1, Math.max(0, v));
    io.send(v);
    readNotes = [];
    draw(v);
  };
  // Named, so a row rebuilt round it can give back what was being typed (`giveBack`).
  const field = valueField(`${unit.name} ${ctrl.label} value`, typedInto(ctrl, set), `knob-${unit.name}-${ctrl.name}`);
  field.input.dataset.focus = `field ${ctrl.name}`;
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
    const failed = io.failed();
    const notes = [...ctrl.notes, ...readNotes];
    if (ctrl.missing) notes.unshift("not on the running plugin");
    else if (ctrl.readOnly) notes.unshift("read-only");
    if (failed) notes.unshift(`refused: ${failed}`);
    note.textContent = notes.join(" · ");
    note.title = note.textContent;
    el.classList.toggle("failed", !!failed);
  };
  if (!inert) bindGestures(dial, ctrl, () => current, set, { field });
  state.updaters.push((ctx) => {
    const read = io.read(ctx);
    readNotes = read.notes;
    draw(read.v);
  });
  return el;
}

function toggle(unit, ctrl) {
  const key = `p\u0000${unit.name}\u0000${ctrl.name}`;
  const value = h("span", { class: "value" });
  const el = h("button", { class: "switch", "aria-label": `${unit.name} ${ctrl.label}`, disabled: ctrl.readOnly || ctrl.missing,
    "data-focus": `toggle ${ctrl.name}` },
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
  const select = h("select", { "aria-label": `${unit.name} ${ctrl.label}`, disabled: ctrl.readOnly || ctrl.missing,
    "data-focus": `select ${ctrl.name}` }, ctrl.choices.map((c) => h("option", { value: c.value }, c.label)));
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
  if (ctrl.kind === "mix") return knob(unit, ctrl, size);
  if (ctrl.control === "toggle" || ctrl.scale === "toggle") return toggle(unit, ctrl);
  if (ctrl.control === "selector" && ctrl.choices) return selector(unit, ctrl);
  return knob(unit, ctrl, size);
}

// A unit's on/off switch. `seen(bypassed)`, when given, is told what the
// switch shows at every render (true, false, or null where Sushi did not
// say): the rack's row minimises and opens by it.
function bypassButton(unit, size, seen = null) {
  const key = `b\u0000${unit.name}`;
  const led = h("span", { class: "led" });
  const el = h("button", { class: `bypass b-${size}`, "data-focus": "bypass" }, led);
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
    if (seen) seen(current);
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
        forgetLockRefusals();
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
    ...state.failures.map((f, i) => h("div", { class: "problem refused", role: "alert", title: f.reason },
      h("strong", {}, f.title), h("span", {}, R.shortSaid(f.reason)),
      f.action ? h("button", { class: "act", onclick: () => { state.failures.splice(i, 1); f.action.run(); } }, f.action.label) : null,
      h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: () => { state.failures.splice(i, 1); render(); } }, "×"))),
    ...list.map((p) => h("div", { class: `problem ${p.kind}`, title: p.reason }, h("strong", {}, p.title), h("span", {}, R.shortSaid(p.reason)),
      p.at ? h("span", { class: "at" }, p.at) : null)),
    state.notice ? h("div", { class: "notice", title: state.notice.title || null }, state.notice.text ?? state.notice,
      h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: () => { state.notice = null; render(); } }, "×")) : null,
  ]);
}

// ---------------------------------------------------------------- gestures

async function move(unit, direction) {
  const refusal = R.lockRefusal(state.locked, "move");
  if (refusal) { fail(`Not moved: ${unit.name}`, { reason: refusal, lock: true }); render(); return; }
  const answer = await request("POST", "/live/move", { processor: unit.name, direction });
  if (!answer.ok) fail(`Not moved: ${unit.name} ${direction}`, answer);
  render();
  kick();
}

// The one Undo (#209): POST /undo, and what it said beside the button for
// R.NOTICE_MS. Whatever it put back may be a rig file, the bin or the
// pedals, so everything they show is read again.
async function undo() {
  const u = state.undo;
  if (u.pending) return;
  u.pending = true;
  render();
  let answer;
  try {
    answer = await request("POST", R.UNDO_CALL.path, R.UNDO_CALL.json);
  } finally {
    u.pending = false;
  }
  u.said = { ...R.undoOutcome(answer), at: noticeClock() };
  u.restartConfirm = false;
  if (answer.ok && answer.body) u.answer = { ok: true, status: 200, body: { next: answer.body.next ?? null } };
  armNotices();
  render();
  kick();
  if (answer.ok) {
    refreshRigs(true).then(render);
    const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
    if (rig.config_name) refreshRigChannels(rig.config_name, true).then(render);
    refreshMidi(true).then(render);
    if (state.revert.open) refreshSaves();
  }
}

async function restartForUndo() {
  const u = state.undo;
  u.restartConfirm = false;
  const offer = u.said && u.said.offer;
  if (!offer) return;
  const ok = await lifecycle("restart", offer.name);
  if (ok) u.said = null;
  render();
}

// The button and what its last press said, in its own slot of each
// screen's header. `screen` decides what it may undo (R.undoButton).
function undoRegion(el, screen) {
  const u = state.undo;
  const view = R.undoButton(u.answer, { locked: state.locked, screen, pending: u.pending });
  const said = u.said;
  patch(el, JSON.stringify([view, said && [said.ok, said.text, said.title, !!said.offer], u.restartConfirm, !!state.pending]), () => [
    h("button", { class: `undo${view.cant ? " cant" : ""}`, disabled: view.disabled, title: view.title, "aria-label": view.title,
      onclick: () => undo() }, "Undo"),
    said ? h("span", { class: `undo-said ${said.ok ? "ok" : "refused"}`, role: said.ok ? "status" : "alert", title: said.title || null },
      said.text) : null,
    said && said.offer && !u.restartConfirm ? h("button", { class: "rig-act small undo-restart", disabled: !!state.pending,
      onclick: () => { u.restartConfirm = true; render(); } }, "Restart") : null,
    said && said.offer && u.restartConfirm ? confirmBox("Restart?", restartForUndo, () => { u.restartConfirm = false; render(); }) : null,
  ]);
}

// What an action said, in a few words (#209): its title ("Saved
// electric_board", "Not assigned: C"), with everything else it said -- the
// file written, what git says, the agent's reason -- in the box's `title`,
// for a hover. `action` and `offer` are its buttons, if any.
function resultBox(result, { cls = "save-result", dismiss = null, offer = null } = {}) {
  const more = [result.ok ? result.text : result.reason, result.ok ? result.commit : null].filter(Boolean).join(" ");
  return h("div", { class: `${cls} ${result.ok ? "ok" : "refused"}`, role: result.ok ? "status" : "alert", title: more || null },
    h("strong", {}, result.title),
    result.action ? h("button", { class: "rig-act danger", onclick: result.action.run }, result.action.label) : null,
    offer,
    dismiss ? h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: dismiss }, "×") : null);
}

// A confirm, where one is still asked (#209: what Undo cannot take back, or
// what cuts the sound): one or two words, Yes and No.
function confirmBox(question, yes, no, { pending = false, cls = "" } = {}) {
  return h("span", { class: `confirm${cls ? " " + cls : ""}`, role: "group", "aria-label": question },
    h("span", { class: "q" }, question),
    h("button", { class: "rig-act danger small confirm-yes", disabled: pending, onclick: yes }, "Yes"),
    h("button", { class: "rig-act small confirm-no", onclick: no }, "No"));
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
  state.saveRestartConfirm = false;
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
  state.recordResult = said.ok && !said.text ? null : { ...said, at: noticeClock() };
  armNotices();
  if (answer.ok) { noteRecord(answer); state.takeName = ""; }
  render();
  kick();
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
  state.recordResult = { ...R.recordOutcome("stop", answer), at: noticeClock() };
  armNotices();
  if (answer.body && answer.body.name) noteRecord(null);
  render();
  kick();
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
    state.notice = { text: said.title, title: said.text };
    // Follow the rig that is up now; after a stop, keep the one stopped, so
    // starting it again is one tap.
    state.chosen = op === "stop" ? name : null;
  } else {
    // A restart that would drop unsaved changes is refused by the agent;
    // dropping them is a second, deliberate tap, never the default.
    const discard = op === "restart" && answer.code === "unsaved"
      ? { label: "Restart anyway", run: () => lifecycle("restart", name, { discard: true }) } : null;
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
  if (said.ok) state.notice = { text: said.title, title: said.text };
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
// mockups' S1 has it. On the stage (`dropdown`, the element to draw it in,
// #182), a native select in its header instead of the list: shown locked
// but off, and choosing a rig switches to it through the same `lifecycle`
// as the list's Switch to.
function rigsRegion(el, { newRig = false, deletes = false, dropdown = null, title = null } = {}) {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  const running = rig.running ? rig.config_name : null;
  const view = R.rigPicker(state.health, state.rigs, state.chosen ?? running, state.pending);
  const stop = view.actions.find((a) => a.op === "stop" && !a.disabled);
  const confirming = state.confirming && !!stop;
  // Stop takes a second tap, because it cuts the sound; the rest are one.
  const acts = (actions) => (confirming
    ? h("div", { class: "rig-actions" }, confirmBox("Stop?", () => lifecycle("stop", stop.name),
      () => { clearTimeout(state.confirmTimer); state.confirming = false; render(); }, { cls: "stop-confirm" }))
    : h("div", { class: "rig-actions" }, actions.map((a) => h("button", {
      class: `rig-act${a.primary ? " primary" : ""}`, disabled: a.disabled,
      onclick: () => (a.op === "stop" ? armStop() : lifecycle(a.op, a.name)),
    }, a.label))));
  if (dropdown) {
    // The select in the header's first row, beside the Lock button, in the
    // rig's name's place: what it shows is the screen's title (the running
    // rig, else the state's title, `title`). The rest under the master.
    const sel = R.rigSelect(view, state.health, state.locked, title);
    const locked = state.locked;
    patch(dropdown, JSON.stringify(["dropdown", sel]), () => h("select", {
      id: "stage-rig", name: "stage-rig", class: "rig-select", "aria-label": "Rig", disabled: sel.disabled, title: sel.title,
      onchange: (event) => {
        const said = R.rigSelected(sel, event.target.value);
        if (!said) return;
        state.confirming = false;
        if (said.op === "switch") lifecycle("switch", said.name);
        else { state.chosen = said.name; render(); }
      },
    },
    sel.prompt ? h("option", { value: "", disabled: true, selected: true }, sel.prompt) : null,
    sel.options.map((o) => h("option", { value: o.value, selected: o.value === sel.value }, o.label))));
    patch(el, JSON.stringify(["dropdown", sel.actions, view.problem, view.busy, confirming && !locked, locked]), () => [
      view.problem ? h("p", { class: "rig-problem", title: view.problem }, R.shortSaid(view.problem)) : null,
      view.busy ? h("p", { class: "rig-busy", role: "status" }, view.busy) : null,
      // Start and Stop only unlocked, in one slim row with the guide's
      // link: on stage a Stop within reach of a thumb is the mistake the
      // lock is there to prevent.
      locked ? null : h("div", { class: "rig-row-stage" }, acts(sel.actions), helpAnchor("stageRigs")),
    ]);
    return;
  }
  // The rack's picker (the files panel) has a Delete on each row (#129); the
  // stage's does not: deleting a file is not a stage action.
  const dels = deletes ? view.rows.map((r) => R.deleteAction(r.name, R.runningListed(state.rigs), state.bin.pending)) : null;
  const bin = deletes ? binKey() : null;
  const rigButton = (r) => h("button", {
    class: `rig${r.up ? " up" : ""}${r.chosen ? " chosen" : ""}`, "aria-pressed": String(r.chosen),
    onclick: () => { state.chosen = r.name; state.confirming = false; render(); },
  }, h("span", { class: "n" }, r.name), r.up ? h("span", { class: "tag" }, "up now") : null);
  const rebuilt = patch(el, JSON.stringify([view, confirming, newRig, dels, bin]), () => [
    h("h2", {}, "RIGS"),
    view.problem ? h("p", { class: "rig-problem", title: view.problem }, R.shortSaid(view.problem)) : null,
    view.rows.length ? h("div", { class: "rig-list", role: "group", "aria-label": "Rigs in config/rigs" },
      view.rows.map((r, i) => (dels ? h("div", { class: "rig-row", "data-rig": r.name }, rigButton(r),
        h("button", { class: "rig-del", disabled: dels[i].disabled, "aria-label": dels[i].aria, title: dels[i].why,
          onclick: () => deleteRig(r.name) }, dels[i].label))
        : rigButton(r)))) : null,
    bin && bin.said && !bin.said.ok ? saidBox(bin.said) : null,
    view.busy ? h("p", { class: "rig-busy", role: "status" }, view.busy) : null,
    acts(view.actions),
    // The New rig screen, from the rack's picker only: making a rig is not a
    // stage action, so the stage's picker does not offer it.
    newRig ? h("a", { class: "rig-act new-rig", href: "#new-rig" }, "New rig…") : null,
    helpAnchor("rigs"),
  ]);
  if (rebuilt && state.bin.focus) {
    const target = el.querySelector(state.bin.focus);
    if (target) { state.bin.focus = null; target.focus(); }
  }
}

// ---------------------------------------------------------------- deleting a rig (#129)
// The rack's picker: Delete on a row moves the file into config/rigs/.deleted/
// at once, with no confirm: the one Undo (#209) moves it back.

function binKey() {
  const b = state.bin;
  return { said: b.said, pending: b.pending };
}

function saidBox(said) {
  return h("div", { class: `save-result rig-deleted ${said.ok ? "ok" : "refused"}`, role: said.ok ? "status" : "alert",
    title: said.ok ? null : said.reason },
    h("strong", {}, said.title),
    h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: () => { state.bin.said = null; render(); } }, "×"));
}

// The notices that go by themselves after R.NOTICE_MS (#189): the takes
// folder's and the record bar's when they worked, and the one Undo's few
// words, whatever they said (#209: the button's title keeps saying what the
// next press would do). A refusal elsewhere stays until it is dismissed.
// Timed on the page's monotonic clock (`noticeClock`), not the wall clock,
// which NTP can step back; one timer, set for the soonest due
// (`armNotices`), as the Stop confirm's is (`armStop`), and set again after
// each pass for what is left, so a timer that fires early only looks again.
const noticeClock = () => performance.now();
let noticeTimer = null;

function undoNotice() {
  const u = state.undo.said;
  return u && !state.undo.restartConfirm ? { ok: true, at: u.at } : null;
}

function armNotices() {
  clearTimeout(noticeTimer);
  noticeTimer = null;
  const wait = R.nextNoticeIn([state.takesOpen.result, state.recordResult, undoNotice()], noticeClock());
  if (wait !== null) noticeTimer = setTimeout(expireNotices, wait);
}

// Where the focus goes when what held it leaves with its notice: the record
// bar's to Record, the takes folder's to Open folder, the Undo's to Undo.
// Never left on the page's body.
function focusAfterNotice(where) {
  const tries = where === "record" ? ["button.record-act.rec", "button.record-act.stop"]
    : where === "takes" ? ["button.open-takes"] : where === "undo" ? ["button.undo"] : [];
  for (const selector of tries) {
    const el = document.querySelector(selector);
    if (el && !el.disabled) { el.focus(); if (document.activeElement === el) return; }
  }
}

function expireNotices() {
  const now = noticeClock();
  const openDone = R.noticeDone(state.takesOpen.result, now);
  const recordDone = R.noticeDone(state.recordResult, now);
  const undoDone = R.noticeDone(undoNotice(), now);
  if (openDone || recordDone || undoDone) {
    const active = document.activeElement;
    const held = active && active !== document.body ? active : null;
    const where = held && (held.closest(".record-bar") ? "record" : held.closest("section.takes") ? "takes"
      : held.closest(".undo-slot") ? "undo" : null);
    if (undoDone) state.undo.said = null;
    if (openDone) state.takesOpen.result = null;
    if (recordDone) state.recordResult = null;
    render();
    if (held && !held.isConnected) focusAfterNotice(where);
  }
  armNotices();
}

async function deleteRig(rig) {
  const b = state.bin;
  if (b.pending) return;
  b.pending = true;
  render();
  const call = R.deleteCall(rig);
  let answer;
  try {
    answer = await request("POST", call.path, call.json);
  } finally {
    b.pending = false;
  }
  const said = { ...R.deletedOutcome(rig, answer), at: noticeClock() };
  // No confirm and no Undo of its own (#209): the one Undo puts it back,
  // and says so; only a refusal is said here, in a few words.
  b.said = said.ok ? null : said;
  if (said.ok) {
    if (state.chosen === rig) state.chosen = null;
    b.focus = ".rig-list button.rig";
    await refreshRigs(true);
  }
  render();
  kick();
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
// The picker is patched on its own key, inside the card: the model list's
// first answer comes a poll after the card is drawn, and a model chosen or
// cancelled is the picker's alone, so neither rebuilds the faders under a
// hand. Keyed whole, the list arriving replaced a fader just focused or
// pressed, and the key or the drag went nowhere (CI on master 58dd382).
function ampRegion(el) {
  const view = R.ampCard(state.amp, state.ampModels, { locked: state.locked, pending: state.ampPending });
  const { picker, listProblem, ...card } = view;
  patch(el, JSON.stringify(card), () => {
    state.ampUpdaters = [];
    state.ampPicker = null;
    if (view.state === "hidden") return [];
    if (view.state !== "up") {
      return [h("h2", {}, "AMP"), h("p", { class: view.state === "failed" ? "missing" : "amp-none", title: view.why }, R.shortSaid(view.why))];
    }
    state.ampPicker = h("div", { class: "amp-picker" });
    return [
      h("div", { class: "amp-head" },
        h("span", { class: "swatch", style: "background:#E07A73" }),
        h("div", { class: "who" }, h("h2", {}, "AMP"), h("span", { class: "amp-model",
          title: view.model ? [view.model.stored, view.model.declared ? `It calls itself ${view.model.declared}` : null].filter(Boolean).join(" · ") : null },
          view.model ? view.model.name : "no model"),
          view.known === "panel" ? h("span", { class: "sub", title: view.note }, "From the panel") : null)),
      view.model && view.model.problem ? h("p", { class: "missing", title: view.model.problem }, R.shortSaid(view.model.problem)) : null,
      view.model && view.model.warning ? h("p", { class: "warn", title: view.model.warning }, R.shortSaid(view.model.warning)) : null,
      view.model && view.model.description ? h("details", { class: "amp-about" }, h("summary", {}, "Model notes"),
        h("pre", {}, view.model.description)) : null,
      view.down ? h("p", { class: "missing", title: "Carla is not running: restart the rig to bring the amp back." }, "Carla down") : null,
      h("div", { class: "faders" }, view.faders.map(ampFader),
        view.shown.map((p) => h("div", { class: "shown", title: p.note }, h("span", { class: "label" }, p.name), h("span", { class: "value" }, p.text)))),
      state.ampPicker,
      helpAnchor("amp"),
    ];
  });
  if (state.ampPicker) ampPickerRegion(state.ampPicker, picker);
}

function ampPickerRegion(el, picker) {
  patch(el, JSON.stringify([picker, state.ampChoice]), () => {
    const chosen = state.ampChoice && picker.options.find((o) => o.model === state.ampChoice);
    const select = h("select", { "aria-label": "Amp model", disabled: !picker.canChange },
      h("option", { value: "" }, "Choose another model…"),
      picker.options.map((o) => h("option", { value: o.model, disabled: !o.usable, title: o.why || (o.declared ? `It calls itself ${o.declared}` : null) }, o.label)));
    select.value = chosen ? chosen.model : "";
    select.addEventListener("change", () => { state.ampChoice = select.value || null; render(); });
    // The select says why it is off, on hover; a model chosen asks once,
    // since loading it restarts the amp and cuts the sound.
    if (picker.why) select.title = picker.why;
    return [
      h("label", { class: "selector" }, h("span", { class: "label" }, "Model"), select),
      chosen && picker.canChange ? confirmBox("Restart amp?", () => changeAmpModel(chosen.model),
        () => { state.ampChoice = null; render(); }, { cls: "amp-confirm" }) : null,
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

// A volume fader: in dB from -∞ to +6, or to 0 for the master (#137:
// `maxDb`, R.MASTER_VOLUME_MAX_DB), (R.volumeSlider), the value as a text
// field, the wheel, the keys and a drag that holds whole dB, all on one
// processor's `gain` through POST /live/parameter, so it is logged, undone
// and saved as any knob is. The master's on both headers (`processor`
// R.MASTER) and each channel's (its send) at the top of its row and on the
// stage. Not behind the lock: volume is a performance control, and takes no
// confirm. `updaters` is the list its updater goes in, for the region that
// holds it; with no rig up it is there, off, and says so. `blended(ctx)` is
// the MIDI control that drives this gain, or null: then the fader follows
// the pedal and is not settable (R.blendedBy says why).
// On the stage (#182) it is behind the lock (`R.lockRefusal`
// "stage_volume"): locked, no drag, tap, wheel, key or typed value moves
// it, and it shows the lock's icon. And it has −0.5 dB and +0.5 dB buttons
// either side of the fader (`R.volumeNudged`), the value above them; `name`
// is the channel's, put before its label. The rack's is as it was.
function volumeFader({ where, processor, label, aria, control, updaters, blended = () => null, maxDb = R.VOLUME_MAX_DB, name = null }) {
  const key = `p\u0000${processor}\u0000gain`;
  const input = h("input", {
    type: "range", min: R.VOLUME_OFF_SLIDER, max: maxDb, step: 0.1, "aria-label": aria,
    name: `volume-${processor}`,  // so a region rebuilt around it can give the focus back (`refocus`)
  });
  let ctrl = control(null);
  let current = null;  // normalised, as Sushi has it
  let held = null;
  let live = false;
  let driven = null;
  const locked = () => (where === "stage" ? R.lockRefusal(state.locked, "stage_volume") : null);
  const settable = () => live && !driven && !locked();
  const dbNow = () => R.volumeDb(ctrl, current);
  const set = (db) => {
    if (!settable()) return;
    const kept = R.volumeClamp(db, maxDb);
    if (kept === null) return;
    const v = R.volumeValueOf(ctrl, kept);
    sender.change(key, v, (x) => ({ path: "/live/parameter", json: { processor, parameter: "gain", value: x } }));
    draw(v);
  };
  // A wheel notch or an arrow: nothing sent where it would not move (up from
  // over the top, #137), and the master's hard top on the way down.
  const step = (steps, fine) => {
    const next = R.volumeStepped(dbNow(), steps, fine, null, maxDb, maxDb < R.VOLUME_MAX_DB);
    if (!next.unchanged) set(next.db);
  };
  const field = valueField(`${aria} value`, (text) => {
    if (!live) return { ok: false, why: "No rig is up to set it on." };
    if (locked()) return { ok: false, why: locked() };
    if (driven) return { ok: false, why: `Driven by ${driven}: the pedal sets it.` };
    const said = R.parseVolume(text, maxDb);
    if (!said.ok) return said;
    set(said.db);
    return { ok: true, text: R.volumeText(said.db) };
  }, `volume-${processor}-value`);
  const note = h("span", { class: "driven" });
  const steps = where === "stage" ? [stepButton(-1), stepButton(1)] : null;
  // On the stage, one row a thumb high (review of PR #184: two rows pushed
  // the channels off a small phone): the name over the value, the value
  // field the whole cell so it is 44 px to tap, then −0.5, the fader, +0.5.
  const el = steps
    ? h("div", { class: `volume volume-${where}`, role: "group", "aria-label": aria, "data-control": key },
      h("span", { class: "vol-cell" },
        h("span", { class: "vol-label", "aria-hidden": "true" }, name ? h("span", { class: "n" }, name) : h("span", { class: "k" }, label),
          h("span", { class: "vol-lock" }, lockIcon())),
        field.input),
      steps[0], input, steps[1], note)
    : h("div", { class: `volume volume-${where}`, role: "group", "aria-label": aria, "data-control": key },
      h("span", { class: "k" }, label), input, field.input, note);
  // A step button: a tap is one step; held, the step repeats at a steady
  // rate (R.VOLUME_REPEAT_MS) once held R.VOLUME_HOLD_MS, at most
  // R.VOLUME_REPEAT_MAX steps a hold. It stops when the press ends however
  // it ends: let go anywhere, slid off the button, taken for a scroll
  // (pointercancel), the window losing the focus or the page hidden, and
  // when a step would not be sent (the lock on, the button drawn again and
  // gone, the end reached). A scroll that starts on it moves nothing.
  function stepButton(direction) {
    const btn = h("button", { type: "button", class: `vol-step ${direction > 0 ? "up" : "down"}`,
      "aria-label": `${aria} ${direction > 0 ? "up" : "down"} 0.5 dB` }, direction > 0 ? "+0.5" : "\u22120.5");
    let hold = null, repeat = null, repeated = false, count = 0;
    const nudge = () => {
      if (!settable() || !btn.isConnected) return false;
      const next = R.volumeNudged(dbNow(), direction, maxDb, maxDb < R.VOLUME_MAX_DB);
      if (next.unchanged) return false;
      set(next.db);
      return true;
    };
    // The window's own blur only (not a control's, which does not bubble
    // but a capturing listener would see), and the page hidden.
    const ends = [[window, "pointerup", true], [window, "pointercancel", true], [window, "blur", false], [document, "visibilitychange", false]];
    const ended = (event) => {
      if (event.type === "blur" && event.target !== window) return;
      if (event.type === "visibilitychange" && document.visibilityState !== "hidden") return;
      stop();
    };
    function stop() {
      clearTimeout(hold);
      clearInterval(repeat);
      hold = repeat = null;
      for (const [target, type, capture] of ends) target.removeEventListener(type, ended, capture);
    }
    const tick = () => { count += 1; if (count > R.VOLUME_REPEAT_MAX || !nudge()) { stop(); return false; } return true; };
    btn.addEventListener("pointerdown", (event) => {
      if (event.button !== undefined && event.button > 0) return;
      stop();
      repeated = false;
      count = 0;
      field.release();
      // A touch is captured by the button it lands on; let go of it, so
      // sliding off the button is seen (pointerleave) and ends the hold.
      try { if (btn.hasPointerCapture(event.pointerId)) btn.releasePointerCapture(event.pointerId); } catch { /* gone */ }
      for (const [target, type, capture] of ends) target.addEventListener(type, ended, capture);
      hold = setTimeout(() => {
        hold = null;
        repeated = true;
        if (tick()) repeat = setInterval(tick, R.VOLUME_REPEAT_MS);
      }, R.VOLUME_HOLD_MS);
    });
    for (const type of ["pointerup", "pointercancel", "pointerleave"]) btn.addEventListener(type, stop);
    // A tap, Enter or Space: one step. After a hold its own steps were the
    // press, and the click its release makes is not one more.
    btn.addEventListener("click", (event) => {
      if (repeated && event.detail !== 0) { repeated = false; return; }
      nudge();
    });
    btn.addEventListener("contextmenu", (event) => event.preventDefault());  // a long press is a hold, not a menu
    return btn;
  }
  const draw = (v) => {
    current = v;
    const db = dbNow();
    if (document.activeElement !== input || !sender.busy(key)) input.value = String(R.volumeSlider(db, maxDb));
    const text = live ? R.volumeText(db) : "—";
    field.show(text);
    input.setAttribute("aria-valuetext", text);
    const failed = state.failed.get(key);
    el.classList.toggle("failed", !!failed);
    el.classList.toggle("is-driven", !!driven);
    el.classList.toggle("is-locked", !!locked());
    const over = live && !driven ? R.overTopNote(db, maxDb) : null;
    note.textContent = driven ? `driven by ${driven}` : over ? "over the top" : "";
    el.title = failed ? `refused: ${failed}`
      : locked() ? locked()
      : over ? `${over[0].toUpperCase()}${over.slice(1)}.`
      : driven ? `Driven by ${driven}, a MIDI blend: it follows the pedal, so it is not set from here (and a save does not write it).`
        : live ? `-∞, then -60 to ${maxDb > 0 ? "+" + maxDb : maxDb} dB: drag, scroll or type. Saved as its level.${maxDb < R.VOLUME_MAX_DB ? " The master stops at 0 dB: its gain comes after the limiter." : ""}` : "No rig is up.";
  };
  input.addEventListener("input", () => {
    const snapped = R.volumeSticky(R.volumeOfSlider(Number(input.value), maxDb), held);
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
    if (notches) step(notches, event.shiftKey);
  });
  input.addEventListener("keydown", (event) => {
    const steps = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 10, PageDown: -10 };
    if (event.key in steps) step(steps[event.key], event.shiftKey);
    else if (event.key === "Home") set(-Infinity);
    else if (event.key === "End") { if (!(dbNow() > maxDb)) set(maxDb); }  // the top, never down to it
    else return;
    event.preventDefault();
  });
  updaters.push((ctx) => {
    live = !!ctx.live;
    driven = live ? blended(ctx) : null;
    input.disabled = !settable();
    field.input.readOnly = !settable();
    if (steps) for (const b of steps) b.disabled = !settable();
    if (ctx.live) ctrl = control(ctx.live);
    draw(ctx.live ? sender.displayed(key, R.valueOf(ctx.live, processor, "gain"), ctx.seq) : null);
  });
  return el;
}

function masterFader(where) {
  return volumeFader({ where, processor: R.MASTER, label: "MASTER", aria: "Master volume",
    control: (live) => R.masterControl(live), updaters: state.masterUpdaters, maxDb: R.MASTER_VOLUME_MAX_DB });
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
    control: (live) => R.gainControl(live, track.send.name), updaters, blended: () => blendOf(track.name),
    name: where === "stage" ? track.name : null });
}

// ---------------------------------------------------------------- the rack (S1)

// A link into the user guide (#167): feature `which` of R.HELP, from this
// screen, so the guide's way back returns here. Same tab: the guide's bar
// says "Back to the rig", and the browser's own Back works as well.
function helpAnchor(which, cls = "help-link") {
  const link = R.helpLink(which, state.screen);
  return link ? h("a", { class: cls, href: link.href, "aria-label": link.aria, "data-help": which }, link.label) : null;
}

function rackFrame() {
  const regions = {
    title: h("h1", { class: "rig-name" }),
    status: h("span", { class: "status-slot" }),
    lock: h("span", { class: "lock-slot" }),
    undo: h("span", { class: "undo-slot" }),
    problems: h("div", { class: "problems", "aria-live": "polite" }),
    tiles: h("span", { class: "tile-row" }),
    rigs: h("section", { class: "rigs", "aria-label": "Rigs" }),
    channels: h("div", { class: "channel-list" }),
    wiring: h("section", { class: "wiring strips", "aria-label": "Channels and their inputs" }),
    pedals: h("section", { class: "pedals", "aria-label": "Pedals", hidden: true }),
    tabs: h("div", { class: "tabs", role: "tablist", "aria-label": "Chains", hidden: true }),
    panel: h("div", { class: "tab-panel", id: "rack-panel" }),
    amp: h("section", { class: "amp-card", "aria-label": "Amp" }),
    rack: h("main", { class: "rack" }),
    changes: h("div", { class: "changes-list" }),
    save: h("section", { class: "save", "aria-label": "Save" }),
    takes: h("section", { class: "takes", "aria-label": "Takes" }),
    drawer: h("div", { class: "drawer", hidden: true }),
  };
  // The header is built once, its parts patched on their own: the master
  // fader in it keeps its focus and a drag in progress whatever else in the
  // bar changes (the status line moves on every minute of uptime). The
  // master sits after the rig's name, before anything whose width changes.
  regions.header = h("header", { class: "bar" },
    h("svg", { width: 28, height: 28, viewBox: "0 0 28 28", "aria-hidden": "true", class: "mark" }, h("path", { d: "M5 18a9 9 0 0 1 18 0" }), h("path", { d: "M10 18a4 4 0 0 1 8 0" })),
    regions.title, masterFader("rack"), regions.status,
    h("span", { class: "spacer" }), regions.tiles, h("a", { class: "go", href: "#stage" }, "Stage"), helpAnchor("guide", "go help"), regions.undo, regions.lock);
  // The open tab's panel (#135): the amp first, in the tab of the channel it
  // is in front of (between the guitar and the board), then that chain.
  regions.panel.append(regions.amp, regions.rack);
  const frame = h("div", { class: "screen rack-screen" }, regions.header, regions.problems,
    // A mixing desk (2026-11-06): the channel strips on the left, the chains
    // in the middle, one at a time behind a tab per channel (#135), the
    // files (rigs, save, revert, takes) on the right, the master in the
    // header. At phone width they stack in that order, the strips first:
    // volume is what is reached for. The problem boxes are above all three,
    // outside the tabs: nothing they say is behind a closed one.
    h("div", { class: "rack-body" }, h("nav", { class: "channels", "aria-label": "Channel strips" }, regions.wiring, regions.pedals),
      h("div", { class: "rack-main" }, regions.tabs, regions.panel),
      h("div", { class: "side files", "aria-label": "Rigs and files" }, regions.rigs,
        h("aside", { class: "changes", "aria-label": "Undo, save and revert" }, regions.changes, regions.save),
        regions.takes, regions.channels)),
    // The Library drawer (#130): over the page, not in a column, so opening
    // it moves nothing on the rack, and a chain changing under it moves
    // nothing in it.
    regions.drawer);
  return { frame, regions };
}

// ---------------------------------------------------------------- the rack's tabs and rows (#135)

// What the player opened, kept as two sets of unit names (R.unitRow says
// what they mean): `state.open`, all of a unit's controls; `state.peeked`, a
// bypassed unit opened without switching it on.
function rowState(name) {
  return { expanded: state.open.has(name), peeked: state.peeked.has(name) };
}

function setRow(name, { expanded, peeked }) {
  if (expanded) state.open.add(name); else state.open.delete(name);
  if (peeked) state.peeked.add(name); else state.peeked.delete(name);
}

// Whether the player is in the middle of a row: a knob of it being dragged
// (`bindGestures` marks it), or a value half typed into one of its fields. A
// focus left on a knob turned a while ago is not that: a pedal's bypass must
// still minimise the row.
function rowInUse(el) {
  const active = document.activeElement;
  return dragging(el) || !!(active && el.contains(active) && active.dataset.typed === "1");
}

// Whether a control in `el` is being dragged: marked by `bindGestures`, and
// still holding its pointer (a mark whose capture is gone is no drag).
function dragging(el) {
  return [...el.querySelectorAll("[data-dragging]")].some((c) => {
    try { return c.hasPointerCapture(Number(c.dataset.dragging)); } catch { return false; }
  });
}

// The name's button, or a tap on a slim row: open or close it (R.unitTapped).
// Allowed locked: it changes what is shown, not the rig.
function tapUnit(name, slot) {
  setRow(name, R.unitTapped(slot.bypass ?? null, rowState(name)));
  render();
}

// The switch's state, at every render: the row minimises when its plugin is
// bypassed and opens when it is switched on, whoever did it (this page,
// another device, a pedal). Only classes and the button's state change here,
// never the row's controls, so a bypass arriving rebuilds nothing: a knob
// being dragged on the row above keeps its pointer, and a row in use stays
// open (R.bypassSeen).
function rowSeen(name, slot, bypassed) {
  if (slot.bypass !== undefined && slot.bypass !== bypassed) setRow(name, R.bypassSeen(bypassed, rowState(name), rowInUse(slot.el)));
  slot.bypass = bypassed;
  const view = R.unitRow(bypassed, rowState(name));
  // The focus on a part of the row a minimise hides (a knob, a value not
  // typed into) would drop to the page's body, and a keyboard or a screen
  // reader would lose its place (review of #135): it goes to the row's
  // name, which is still there and opens the row again.
  const active = document.activeElement;
  const hiding = view.minimised && !slot.el.classList.contains("minimised") && active && slot.el.contains(active)
    && active !== slot.name && !active.closest(".bypass");
  slot.el.classList.toggle("minimised", view.minimised);
  if (hiding) slot.name.focus({ preventScroll: true });
  slot.el.classList.toggle("open", view.all && !view.minimised);
  slot.name.setAttribute("aria-expanded", String(view.opened));
  slot.name.title = view.minimised ? "Bypassed: open it to set it up without switching it on"
    : bypassed === true ? "Bypassed, and open: tap to close it" : "";
}

// A unit's row, built into its slot (an <article> that stays while the unit
// is on the open chain). Everything the row can show is built: bypassed, it
// is only hidden (`rowSeen`).
function unitParts(slot, unit, expanded) {
  const panel = unit.panel;
  const primary = R.primaryControls(panel);
  // Closed, a row shows what fits beside its name; open, everything.
  const shown = expanded ? [] : primary.slice(0, 6);
  // The plugin's name under the unit's is written in place (`unitSlot`): it
  // comes from the library, which may answer after the row is drawn, and
  // a row rebuilt for its text alone took the control under a hand with it.
  slot.sub = h("span", { class: "sub" });
  slot.remove = null;  // built with the detail, when the row is open (`removeParts`)
  // Whether a step is possible is drawn in place (`unitSlot`): a neighbour
  // added or moved from elsewhere changes it, and must not rebuild the row.
  const moveButton = (direction) => h("button", {
    class: `step${state.locked ? " locked" : ""}`, "data-focus": direction,
    "aria-label": `Move ${unit.name} ${direction}${state.locked ? " (locked)" : ""}`,
    title: state.locked ? "Locked: reordering changes the rig's structure" : null,
    onclick: () => move(unit, direction),
  }, state.locked ? lockIcon() : (direction === "up" ? "▲" : "▼"));
  slot.steps = { up: moveButton("up"), down: moveButton("down") };
  slot.name = h("button", { class: "name", "aria-expanded": "false", "data-focus": "name", onclick: () => tapUnit(unit.name, slot) }, unit.name);
  // The mono badge has a place of its own, drawn in place (`unitSlot`): it
  // changes when a stereo plugin is moved past this one, from anywhere.
  slot.badge = h("span", { class: "badge-slot" });
  slot.profiled = !!unit.profile;
  return [
    h("div", { class: "steps" }, slot.steps.up, slot.steps.down),
    h("span", { class: "swatch", style: `background:${swatch(unit)}` }),
    h("div", { class: "who" },
      h("span", { class: "name-line" }, slot.name, slot.badge),
      slot.sub),
    h("div", { class: "knobs" }, shown.map((c) => control(unit, c, "small")),
      !expanded && primary.length > shown.length ? h("span", { class: "more" }, `+${primary.length - shown.length} more`) : null),
    slot.pedal = h("span", { class: "pedal-slot" }),
    bypassButton(unit, "small", (bypassed) => rowSeen(unit.name, slot, bypassed)),
    expanded ? detail(unit, "big", removeParts(slot, unit)) : null,
    slot.pedalPanel = h("div", { class: "pedal-panel-slot" }),
  ];
}

// A unit's Remove (#130), at the foot of its open row: one tap, with no
// confirm, since the one Undo puts the plugin back with its settings (#209).
// Built with the row; what it shows is drawn in place (`drawRemove`), so a
// refusal rebuilds nothing. Never offered on the compiler's send, return or
// copy to stereo, which are not units here.
function removeParts(slot, unit) {
  if (!R.removable(unit)) return null;
  const name = unit.name;
  const parts = {
    ask: h("button", { class: "row-act remove", "data-focus": "remove", onclick: () => removeUnit(name) }),
    said: h("p", { class: "row-said", hidden: true }),
  };
  slot.remove = parts;
  return h("div", { class: "row-acts" }, parts.ask, parts.said);
}

// The Remove's state, in place: offered, in flight, or refused in a few
// words (the agent's reason on hover). Locked, it says so and refuses in
// place. In flight its button keeps the focus (`aria-disabled`, not
// `disabled`, which would drop it to the page), until its row goes.
function drawRemove(slot, name) {
  const parts = slot.remove;
  if (!parts || !parts.ask.isConnected) return;
  let now = state.removing.get(name) || {};
  if (now.done && Date.now() - now.at > 5000) { state.removing.delete(name); now = {}; }
  const pending = !!now.pending;
  const label = pending ? "Removing…" : state.locked ? "Remove (locked)" : "Remove";
  if (parts.ask.textContent !== label) parts.ask.textContent = label;
  parts.ask.setAttribute("aria-label", `Remove ${name}`);
  parts.ask.title = state.locked ? R.lockRefusal(true, "remove") : "";
  parts.ask.setAttribute("aria-disabled", String(pending));
  const said = now.reason ? (now.lock ? "Locked" : "Not removed") : "";
  if (parts.said.textContent !== said) parts.said.textContent = said;
  parts.said.title = now.reason || "";
  parts.said.hidden = !said;
  parts.said.classList.toggle("refused", !!now.reason);
  parts.said.setAttribute("role", now.reason ? "alert" : "status");
}

function removeFocus(name, which) {
  const slot = state.rackParts && state.rackParts.slots.get(name);
  if (slot && slot.remove) slot.remove[which].focus({ preventScroll: true });
}

// Remove, tapped: removed through the agent. Its answer is about the rig it
// was sent to: if the page has come to show another since (a switch, a
// restart, from anywhere), it is said as that rig's, in the boxes at the
// top, and nothing on this rig's rows is touched (review of #130).
async function removeUnit(name) {
  const was = state.removing.get(name);
  if (was && was.pending) return;
  const refusal = R.lockRefusal(state.locked, "remove");
  if (refusal) { state.removing.set(name, { reason: refusal, lock: true }); render(); return; }
  const run = state.editsRun, rig = shownRigName();
  state.removing.set(name, { pending: true });
  render();
  removeFocus(name, "ask");
  const call = R.removeCall(name);
  const answer = await request("POST", call.path, call.json);
  placeAnswer(run, () => {
    if (answer.ok) {
      // Gone: its row goes when the next /live shows the chain without it,
      // and says "Removing…" until then (forgotten after a few seconds).
      state.removing.set(name, { pending: true, done: true, at: Date.now() });
    } else if (state.rackParts && state.rackParts.slots.has(name)) {
      state.removing.set(name, { reason: answer.reason });
    } else {
      // Its row is not on the page now (another tab, the chain redrawn):
      // said where it is seen, and the row is free to try again.
      state.removing.delete(name);
      fail(`Not removed: ${name}`, answer);
    }
  }, () => {
    state.removing.delete(name);
    if (answer.ok) state.notice = `Removed ${name} from ${rig}`;
    else fail(`Not removed from ${rig}: ${name}`, answer);
  });
  render();
  kick();
}

// An answer to an add or a remove sent while the page showed rig `run`,
// placed by which rig it shows now: `same` for that rig, `other` for
// another (said as the rig it was sent to). While /health does not answer,
// which rig is shown is not known, and that is no sign of another (review
// of #130: a poll that failed read as a switch, and the add's place said
// "Adding…" for good): the answer is held, and placed once it answers again
// (`settleEdits`). Either way every add and remove in flight ends.
function placeAnswer(run, same, other) {
  const now = shownRun();
  if (now === null) state.unplaced.push({ run, same, other });
  else (now === run ? same : other)();
}

// The rig the page shows (`rigIdentity`), as a key, and its name: what a
// confirm armed, a place's "Adding…" or refusal, and an open drawer belong
// to. Null while /health has not answered: no answer is no sign of another.
function shownRun() {
  return state.health && state.health.ok ? JSON.stringify(rigIdentity(state.health)) : null;
}
function shownRigName() {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  return rig.config_name || "the rig";
}

// At each render, before anything is drawn. Once the page knows which rig
// it shows (/health answered): if that is another rig, or the same rig
// started again, everything of the chain edits kept by a unit's or a
// place's name was the rig before's, and a confirm left armed would remove
// the new rig's plugin of that name with one tap (review of #130), so all
// of it goes; then the answers held while it was not known are placed. A
// /health that did not answer is not known, never "another rig". And an add
// confirmed whose row was never drawn here (its chain left the page) is let
// go after a few seconds, so its name is not counted as in flight for good.
function settleEdits() {
  const run = shownRun();
  if (run === null) return;
  if (run !== state.editsRun) {
    const first = state.editsRun === null;
    state.editsRun = run;
    if (!first) {
      for (const held of state.removing.values()) if (held.timer) clearTimeout(held.timer);
      state.removing.clear();
      state.adding.clear();
      state.addRefused.clear();
    }
  }
  const held = state.unplaced;
  state.unplaced = [];
  for (const answer of held) (answer.run === run ? answer.same : answer.other)();
  for (const [key, adding] of [...state.adding]) if (adding.done && Date.now() - adding.at > 5000) state.adding.delete(key);
}

// The page unlocked: every "Locked:" line said for a refused structural edit
// goes, since the lock it was about is off (review of #130).
function forgetLockRefusals() {
  for (const [name, held] of [...state.removing]) if (held.lock) state.removing.delete(name);
  if (state.pedalUi.refused && state.pedalUi.refused.lock) state.pedalUi.refused = null;
  state.failures = state.failures.filter((f) => !f.lock);
}

// One unit's slot, patched on its own key: what the plugin is and whether
// all its controls show. Not its bypass, its place, its neighbours or its
// mono badge (which is its neighbours' doing), so nothing that happens to
// another row, or arrives from elsewhere, rebuilds this one under a drag or
// a half-typed value. Its updaters are its own list
// (`state.unitUpdaters`), replaced when it is rebuilt.
function unitSlot(slot, track, unit, index) {
  const expanded = state.open.has(unit.name);
  const typing = typedIn(slot.el);
  // The control of the row with the focus, by what it is in the row
  // (`data-focus`, set as it is built: its kind and its parameter, never its
  // state or a label an updater writes later): a row rebuilt (its profile
  // arriving after the first draw) gave the focus to the page's body, and
  // the next key turned nothing (CI on #135, a knob focused just before the
  // profiles came).
  const active = document.activeElement;
  const focused = active && slot.el.contains(active) ? active.dataset.focus || null : null;
  const key = JSON.stringify([unit.uri, unit.profile, unit.panel, expanded, state.locked]);
  // A row with a knob being dragged is not rebuilt under the hand, as a
  // bypass does not minimise it (review of #135: profiles arriving past the
  // first draw's wait took the drag with the old row). What changed is drawn
  // when the drag ends: the drag's end redraws the page (`bindGestures`).
  const held = state.keys.has(slot.el) && state.keys.get(slot.el) !== key && dragging(slot.el);
  const rebuilt = !held && patch(slot.el, key, () => {
    const outer = state.updaters;
    state.updaters = [];
    try {
      return unitParts(slot, unit, expanded);
    } finally {
      state.unitUpdaters.set(unit.name, state.updaters);
      state.updaters = outer;
    }
  });
  if (rebuilt) {
    giveBack(slot.el, typing);  // a field typed in: its text too
    const again = !typing && focused ? slot.el.querySelector(`[data-focus="${CSS.escape(focused)}"]`) : null;
    if (again) again.focus({ preventScroll: true });
  }
  patch(slot.badge, JSON.stringify(unit.monoNote), () => monoBadge(unit));
  // What the row as drawn shows: a row held under a drag keeps its old controls.
  const sub = [pluginName(unit), slot.profiled ? "profile" : "no profile"].join(" · ");
  if (slot.sub.textContent !== sub) slot.sub.textContent = sub;
  slot.steps.up.disabled = !R.canMove(track, index, "up");
  slot.steps.down.disabled = !R.canMove(track, index, "down");
  pedalSlots(slot.pedal, slot.pedalPanel, unitPedalTarget(track, unit));
}

// Put `els` in `container`, in that order, never moving a row in use: one
// that holds the focus (a field being typed in has it) or a drag (a knob
// keeps its drag when the focus goes elsewhere, a second finger on a strip,
// say). Taking a row out of the page to put it back, even where it was,
// ends its drag and blurs its field, so the others are placed round the
// rows in use. Two rows in use stay as they are towards each other, even
// where the order has swapped them: the order is put right once one is let
// go (the next render). A row already followed by the right row stays.
function inOrder(container, els) {
  const now = [...container.children];
  if (now.length === els.length && els.every((el, i) => now[i] === el)) return;
  for (const el of now) if (!els.includes(el)) el.remove();
  const active = document.activeElement;
  const held = new Set(els.filter((el) => el.parentNode === container && (el.contains(active) || dragging(el))));
  // The rows in use, in the order they stand, take the places the order
  // gives rows in use; every other row is placed round them.
  const standing = [...container.children].filter((el) => held.has(el));
  const want = els.map((el) => (held.has(el) ? standing.shift() : el));
  let ref = null;
  for (let i = want.length - 1; i >= 0; i--) {
    const el = want[i];
    if (!held.has(el) && (el.parentNode !== container || el.nextSibling !== ref)) container.insertBefore(el, ref);
    ref = el;
  }
}

// A tab chosen: by its tab, by the keys on the tablist (`focus`), or by its
// channel's strip (`reveal`: on a phone the chain is below the strips, so
// the page goes to it, as the strip's link did). Remembered in this browser
// for this rig. Allowed locked: it changes what is shown, not the rig.
function chooseTab(id, { focus = false, reveal = false } = {}) {
  state.tab = { rig: state.tab.rig, id };
  writeTab(R.tabToRemember(state.tab.rig, id));
  render();
  const parts = state.rackParts;
  const tab = parts && parts.buttons.get(id);
  if (focus && tab) tab.button.focus();
  if (reveal && parts) {
    const box = parts.el.getBoundingClientRect();
    if (box.top < 0 || box.top > innerHeight - 2 * box.height) parts.el.scrollIntoView({ block: "start" });
  }
}

// The tabs (#135): one per channel in the rig's order, then Master, and
// which is open. A real tablist: the open tab is the one in the tab order,
// the arrows, Home and End move between them (R.tabKey), and the panel is
// labelled by its tab. Keyed by the tabs alone and marked in place, so
// choosing one keeps the focus on it. While the rig is up and /live is not
// read (a command running) or fails, the rig's tabs stay as they were, so
// nothing above the chain moves under a hand: the amp's card in particular,
// whose faders are Carla's and work without Sushi (review of #135: one
// failed poll hid the card under a fader drag). Returns the open tab, or
// null.
function tabsRegion(regions, model, ampView) {
  let parts = state.rackParts;
  if (!parts || parts.el !== regions.tabs) {
    parts = { el: regions.tabs, model: null, modelRun: null, buttons: new Map(), open: null, ampOn: null, ampWaiting: false,
      slots: new Map(), chain: null, hint: null };
    state.rackParts = parts;
  }
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  // A model held is held for its run only: tabs drawn from the run before
  // must never stand for a run that has started since (a restart with a
  // channel more, /live not answering yet), nor place its amp (review of
  // #135).
  const run = runKey();
  if (model) { parts.model = model; parts.modelRun = run; }
  else if (!rig.running || parts.modelRun !== run) { parts.model = null; parts.modelRun = null; }
  const tabs = R.rackTabs(parts.model);
  if (model) {
    // Another rig: its own remembered tab, or its first channel.
    const name = rig.config_name ?? null;
    if (state.tab.rig !== name) state.tab = { rig: name, id: R.rememberedTab(readTab(), name) };
  }
  const open = R.openTab(tabs, state.tab.id);
  // A tab that has gone (its channel left the rig) is not come back to.
  if (open !== null && state.tab.id !== null && state.tab.id !== open) state.tab.id = open;
  if (open !== parts.open) {
    // The tab changed, however it did: what was peeked into closes.
    const left = R.tabLeft([...state.open], [...state.peeked]);
    state.open = new Set(left.expanded);
    state.peeked = new Set(left.peeked);
    parts.open = open;
  }
  const focused = regions.tabs.contains(document.activeElement) ? document.activeElement.dataset.tab : null;
  const rebuilt = patch(regions.tabs, JSON.stringify(tabs), () => {
    parts.buttons = new Map();
    return tabs.map((t, i) => {
      const button = h("button", {
        class: `tab${t.master ? " tab-master" : ""}`, role: "tab", type: "button", id: `rack-tab-${i}`,
        "data-tab": t.id, "aria-controls": "rack-panel",
        onclick: () => chooseTab(t.id),
        onkeydown: (event) => {
          const to = R.tabKey(tabs, t.id, event.key);
          if (to === null) return;
          event.preventDefault();
          chooseTab(to, { focus: true });
        },
      }, h("span", { class: "tab-label" }, t.label), h("span", { class: "tab-mark", "aria-hidden": "true" }));
      parts.buttons.set(t.id, { button });
      return button;
    });
  });
  regions.tabs.hidden = !tabs.length;
  if (rebuilt && focused && parts.buttons.has(focused)) parts.buttons.get(focused).button.focus();
  placeAmp(parts, tabs, rig, ampView);
  const marks = R.tabMarks(tabs, parts.model, { failed: state.failed.keys(), amp: ampView,
    ampOn: parts.ampWaiting ? null : parts.ampOn });
  for (const [id, { button }] of parts.buttons) {
    button.setAttribute("aria-selected", String(id === open));
    button.tabIndex = id === open ? 0 : -1;
    // A mark of its own size that is always there, so one coming or going
    // moves nothing; what it is about is the tab's title.
    button.classList.toggle("marked", !!marks[id]);
    button.title = marks[id] || "";
  }
  if (open !== null) {
    regions.panel.setAttribute("role", "tabpanel");
    regions.panel.setAttribute("aria-labelledby", parts.buttons.get(open).button.id);
  } else {
    regions.panel.removeAttribute("role");
    regions.panel.removeAttribute("aria-labelledby");
  }
  // With no tabs (no chains read yet, or /live failing from the first) the
  // card is shown on its own, as before #135; with tabs, in its tab alone.
  regions.amp.hidden = tabs.length > 0 && (parts.ampWaiting || parts.ampOn !== open);
  return open;
}

// How long the centre holds the open chain back while what is drawn there
// is still to come: at a run's first sight with an amp, what says which
// channel the amp is in front of (R.ampTab: the running rig's roles, else
// its file), and for another rig, its plugins. Each usually answers in a
// few milliseconds; a slow agent is not waited for longer than this, after
// which the amp goes with the first channel, and a chain whose plugins have
// not come is drawn without them.
const HOLD_MS = 2000;

// The amp's tab, decided once a run (`parts.ampOn`), so that the card never
// moves while the rig plays: not when an answer arrives late, nor when the
// file is edited (the restart that plays the edit is a new run). Until it is
// decided the open tab's chain is held back with the card (`chainRegion`):
// a chain drawn first and pushed down by the card arriving moved the knob a
// tap was aimed at (review of #135: the first row went from 226 px to 609 px
// on every load). With no amp to place (none on the rig, none read yet) the
// card, which then says only why there is none, goes with the first channel
// and nothing waits. The decision is the page's, kept by run
// (`state.ampPlace`), not the rack frame's: leaving the rack for the stage
// and coming back must not decide it again, from a file edited since. It is
// only made against tabs drawn from this run's own /live (`parts.modelRun`).
function placeAmp(parts, tabs, rig, ampView) {
  const run = runKey();
  const channels = tabs.filter((t) => !t.master);
  const first = tabs.length ? (channels[0] || tabs[0]).id : null;
  if (!state.ampPlace || state.ampPlace.run !== run) {
    if (state.ampPlace) clearTimeout(state.ampPlace.timer);
    state.ampPlace = { run, on: null, since: null, timer: null };
  }
  const amp = state.ampPlace;
  // A channel the amp was placed with that has left the rig: placed again.
  if (amp.on !== null && !tabs.some((t) => t.id === amp.on)) amp.on = null;
  const placing = ampView.state === "up" || ampView.state === "failed";
  if (!placing || !tabs.length || parts.modelRun !== run) {
    parts.ampOn = first;
    parts.ampWaiting = false;
    return;
  }
  if (amp.on === null) {
    const filed = !!(rig.running && rig.rig);
    const roles = state.rolesRun === run ? state.roles : null;
    const decided = R.ampTab(tabs, roles, filed ? state.rigChannels[rig.config_name] || null : null, filed);
    if (amp.since === null) amp.since = Date.now();
    const left = HOLD_MS - (Date.now() - amp.since);
    if (decided !== null) amp.on = decided;
    else if (left <= 0) amp.on = first;
    else if (!amp.timer) amp.timer = setTimeout(() => { amp.timer = null; render(); }, left + 20);
    // What places it is asked for now, not on the next poll.
    if (decided === null && filed && state.screen === "rack") {
      inBackground("roles", refreshRoles);
      inBackground("running channels", refreshRunningChannels);
    }
  }
  parts.ampOn = amp.on;
  parts.ampWaiting = amp.on === null;
}

// Whether the chain is still to wait for the profiles' first answer. A row
// drawn before it is drawn again when it comes, with its profile's controls
// in place of the plain list: every control replaced, after the page had
// looked settled (CI on #135: a knob focused, or measured, a moment before
// was gone). So the first chain is drawn once the profiles have answered,
// or failed, or HOLD_MS after it was first due, whichever is first; the
// profiles are asked for as the page opens.
function profilesDue() {
  if (state.profilesAt) return false;
  if (state.profilesWaitSince === undefined) {
    state.profilesWaitSince = Date.now();
    setTimeout(render, HOLD_MS + 20);
  }
  return Date.now() - state.profilesWaitSince < HOLD_MS;
}

// The open tab's chain: a slot per plugin, in the chain's order. The chain
// is rebuilt whole only when the tab changes; a plugin added, removed or
// moved, here or from elsewhere, adds, drops or moves its slot and leaves
// the others as they are (`inOrder`, `unitSlot`).
function chainRegion(regions, model, status, open) {
  const parts = state.rackParts;
  // Held back, with the amp's card, until the card's tab is known
  // (`placeAmp`); and while another rig's plugins are read (`poll`), so
  // that its rows are drawn once, with them, not plain and then again.
  const reading = !!state.reading && Date.now() - state.reading.since < HOLD_MS;
  const waiting = (parts.ampWaiting || reading || profilesDue()) && !!model;
  const track = model && open !== null && !waiting ? model.tracks.find((t) => t.name === open) || null : null;
  // With no rig, the message depends on the tone (changing, or down), so
  // the tone is in the key, or it would never be redrawn (found by review).
  patch(regions.rack, JSON.stringify(track ? ["chain", track.name] : waiting ? ["waiting"] : ["none", !!model, status.tone]), () => {
    state.unitUpdaters = new Map();
    parts.slots = new Map();
    parts.chain = parts.hint = parts.limiter = null;
    parts.start = null;
    parts.order = [];
    // The rows they were drawn on are gone: a confirm left armed or a line
    // said on them goes too, and only what is in flight is kept (review of
    // #130: they outlived the chain they were said of).
    for (const [name, held] of [...state.removing]) {
      if (held.pending) continue;
      if (held.timer) clearTimeout(held.timer);
      state.removing.delete(name);
    }
    state.addRefused.clear();
    if (waiting) return h("p", { class: "empty" }, "Reading…");
    if (!track) {
      // With no chain to add to, the library can still be looked through
      // (#130: the drawer is there, its adds off with the reason).
      const empty = model ? ["No tracks", "This rig has no tracks to show."]
        : status.tone === "busy" ? ["Changing…", "The rig is changing; the rack comes back when it is up."]
          : status.tone === "off" ? ["No rig", "Nothing is running in Sushi. Choose one under RIGS to start it."]
            : ["No rig", "Nothing is running in Sushi."];
      return [h("p", { class: "empty", title: empty[1] }, empty[0]),
      h("p", { class: "browse" }, h("button", { class: "add browse-library", "data-focus": "browse",
        onclick: () => openDrawer(null, null) }, "Browse the library…"))];
    }
    parts.hint = h("span", { class: "hint" });
    parts.chain = h("div", { class: "chain" });
    // The place at the start of the chain (#130): in front of the first
    // plugin, which no "after" can say.
    parts.start = addSlotParts(track.name, R.addSlots(track)[0]);
    // The Master tab ends with the limiter's line (#137): read-only, after
    // the last place to add, since nothing goes after it.
    parts.limiter = track.isMaster ? h("p", { class: "limiter", role: "note" }) : null;
    // A channel's volume is its strip's, in the left column, and its name is
    // its tab's: the chain's own head is only what it holds.
    return h("section", { class: "track", id: `t-${track.name}` }, h("div", { class: "track-head" }, parts.hint),
      parts.start.el, parts.chain, parts.limiter);
  });
  if (!track) return;
  if (parts.limiter) {
    const line = R.limiterLine(track, liveBody());
    const text = line ? line.text : "";
    if (parts.limiter.textContent !== text) parts.limiter.textContent = text;
    parts.limiter.classList.toggle("warn", !!line && (!line.present || line.bypassed));
  }
  const slots = R.addSlots(track);
  drawAddSlot(parts.start, track, slots[0]);
  const count = track.units.length;
  const hint = count ? `${count} plugin${count === 1 ? "" : "s"}` : "no plugins";
  if (parts.hint.textContent !== hint) parts.hint.textContent = hint;
  const names = new Set(track.units.map((u) => u.name));
  // A row taken away with the focus in it (its "Removing…", most often): the
  // focus goes to the row that takes its place, or the place to add there,
  // not to the page (review of #130).
  let focusAt = null;
  for (const [name, slot] of [...parts.slots]) {
    if (names.has(name)) continue;
    if (slot.place.contains(document.activeElement)) focusAt = Math.max(0, (parts.order || []).indexOf(name));
    parts.slots.delete(name);
    state.unitUpdaters.delete(name);
    state.peeked.delete(name);
    state.removing.delete(name);
    slot.place.remove();
  }
  for (const unit of track.units) {
    if (parts.slots.has(unit.name)) continue;
    const slot = { el: h("article", { class: "unit", "data-unit": unit.name }), bypass: undefined, name: null, steps: null };
    // The unit's row and, under it, the place to add a plugin after it
    // (#130), which goes where the unit goes: "after" names the unit, so
    // the place is the unit's, whatever moves round it.
    slot.add = addSlotParts(track.name, slots.find((x) => x.after === unit.name));
    slot.place = h("div", { class: "unit-place", "data-place": unit.name }, slot.el, slot.add.el);
    // A slim row is opened by a tap anywhere on it that is not its switch;
    // the name's button is the same gesture for the keys and a screen reader.
    slot.el.addEventListener("click", (event) => {
      if (!slot.el.classList.contains("minimised") || event.target.closest("button, a, input, select, label")) return;
      tapUnit(unit.name, slot);
    });
    parts.slots.set(unit.name, slot);
  }
  inOrder(parts.chain, track.units.map((u) => parts.slots.get(u.name).place));
  track.units.forEach((unit, index) => {
    const slot = parts.slots.get(unit.name);
    unitSlot(slot, track, unit, index);
    drawRemove(slot, unit.name);
    drawAddSlot(slot.add, track, slots[index + 1]);
  });
  parts.order = track.units.map((u) => u.name);
  if (focusAt !== null) {
    const units = track.units;
    const to = units[focusAt] ? parts.slots.get(units[focusAt].name).name
      : focusAt === 0 || !units[focusAt - 1] ? parts.start.button : parts.slots.get(units[focusAt - 1].name).add.button;
    if (to) to.focus({ preventScroll: true });
  }
}

// ---------------------------------------------------------------- the Library drawer (#130)

// A place on the chain to add a plugin: its button, which opens the drawer
// for it, and a line under it for what an add from there is doing or why it
// was refused. Built once with its place, drawn in place (`drawAddSlot`).
function addSlotParts(track, slot) {
  const key = `${track}\u0000${slot.key}`;
  const parts = { key, slot };
  parts.button = h("button", { class: "add", "data-focus": "add", "aria-haspopup": "dialog",
    onclick: (event) => {
      if (event.currentTarget.getAttribute("aria-disabled") === "true") return;
      openDrawer(track, parts.slot);
    } });
  parts.said = h("span", { class: "add-said", hidden: true });
  parts.dismiss = h("button", { class: "dismiss small", "aria-label": "Dismiss", hidden: true,
    onclick: () => { state.addRefused.delete(key); render(); parts.button.focus({ preventScroll: true }); } }, "×");
  parts.el = h("div", { class: "add-slot", "data-slot": slot.key }, parts.button, parts.said, parts.dismiss);
  return parts;
}

// What a place shows: its label ("Add after overdrive…"), or that an add
// from it is in flight ("Adding mda_delay…"), until its row is drawn; or the
// agent's reason for refusing it, in place. `slot` is the place as the
// chain stands now (its index moves as plugins come and go round it).
function drawAddSlot(parts, track, slot) {
  if (!parts || !slot) return;
  parts.slot = slot;
  const adding = state.adding.get(parts.key);
  // Confirmed: kept "Adding…" until the poll draws its row, or a few
  // seconds have passed whatever happened.
  if (adding && adding.done && (track.units.some((u) => u.name === adding.done) || Date.now() - adding.at > 5000)) {
    state.adding.delete(parts.key);
  }
  const now = state.adding.get(parts.key);
  const label = now ? `Adding ${now.shown}…` : slot.label;
  if (parts.button.textContent !== label) parts.button.textContent = label;
  // Off while its add is in flight, and with no rig to add to; not
  // `disabled`, which would drop the focus the drawer gave back to it.
  const off = R.addBlocked({ health: state.health, track, slot: null, locked: false });
  parts.button.setAttribute("aria-disabled", String(!!now || !!off));
  parts.button.classList.toggle("working", !!now);
  parts.button.setAttribute("aria-busy", String(!!now));
  parts.button.title = off || (state.locked ? `${R.lockRefusal(true, "add")} The library can still be looked through.` : "");
  const refused = now ? null : state.addRefused.get(parts.key) || null;
  const short = R.shortSaid(refused || "");
  if (parts.said.textContent !== short) parts.said.textContent = short;
  parts.said.title = refused || "";
  parts.said.hidden = parts.dismiss.hidden = !refused;
  parts.said.setAttribute("role", refused ? "alert" : "status");
}

// The drawer opened for a place (`slot` on `track`), or with neither, to
// look through the library with nothing to add to. The rest of the page is
// inert while it is open, so a tap or a key cannot reach the rack under it.
// It is for the rig shown when it was opened (`run`): which rig is shown
// needs a sign, and a drawer left open across a switch must not add to the
// rig switched to (review of #130). Where it was opened from is kept by
// what it is (`back`), not by the element, which may be gone when it closes.
// The focus goes into it (`drawerRegion`, `drawerHome`).
function openDrawer(track, slot) {
  state.drawer = { track, slot, query: "", run: shownRun(),
    back: slot ? { track, key: slot.key, index: slot.index } : { browse: true } };
  inBackground("names", refreshNames);
  render();
}

function closeDrawer() {
  if (!state.drawer) return;
  const { back } = state.drawer;
  state.drawer = null;
  render();
  focusBack(back);
}

// The focus back where the drawer was opened from: that place on the chain,
// found by its key; if it has gone (the plugin it followed was removed, from
// anywhere), the place before where it was, then the start, then the open
// tab; from "Browse the library…", that button, or the chain's first place
// if a rig has come up meanwhile.
function focusBack(back) {
  const rack = state.regions && state.regions.rack;
  if (!rack || !back) return;
  const places = [...rack.querySelectorAll(".add-slot")];
  const button = (place) => (place ? place.querySelector("button.add") : null);
  let to = null;
  if (back.browse) to = rack.querySelector("button.browse-library") || button(places[0]);
  else if (rack.querySelector(`#t-${CSS.escape(back.track)}`) && places.length) {
    to = button(places.find((p) => p.dataset.slot === back.key))
      || button(places[Math.max(0, Math.min(back.index - 1, places.length - 1))]);
  }
  if (!to && state.regions.tabs) to = state.regions.tabs.querySelector('[role="tab"][aria-selected="true"]');
  if (to) to.focus({ preventScroll: true });
}

// The control the focus goes to in the drawer: its search where there is a
// keyboard to type with and a pointer to aim; on a touch screen its Close,
// since focusing a field there brings up a keyboard over half the list (not
// tried on a real phone: the tests' Chromium has a mouse).
function drawerHome(parts) {
  return matchMedia("(hover: hover) and (pointer: fine)").matches ? parts.search : parts.close;
}

// The drawer is modal (review of #130): Escape closes it wherever the focus
// is, Tab and Shift+Tab go round its own controls, and the rest of the page
// is inert meanwhile.
document.addEventListener("keydown", (event) => {
  if (!state.drawer || !state.drawerParts) return;
  if (event.key === "Escape") { event.preventDefault(); closeDrawer(); return; }
  if (event.key !== "Tab") return;
  const sheet = state.drawerParts.sheet;
  const stops = [...sheet.querySelectorAll("button, input")].filter((e) => !e.disabled && e.offsetParent !== null);
  if (!stops.length) return;
  const first = stops[0], last = stops[stops.length - 1], at = document.activeElement;
  const inside = sheet.contains(at);
  if (event.shiftKey && (at === first || !inside)) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && (at === last || !inside)) { event.preventDefault(); first.focus(); }
});

// The names adds from this page have taken that /live may not show yet: in
// flight, or confirmed and not drawn (review of #130: a second add of the
// same plugin went with no name, and the agent refused the name it chose).
function namesInFlight(except = null) {
  return [...state.adding].filter(([key]) => key !== except).map(([, a]) => a.done || a.shown);
}

// An entry tapped: added at the drawer's place, through the agent. The
// drawer closes, the place says "Adding…" until the agent answers and the
// row is drawn there, and a refusal comes back to the place with the
// agent's reason. The name is one the agent will take (`R.addName`), with
// this page's adds in flight counted; a name another device took since the
// last read (409 `name_taken`) is chosen again, once, from /live read
// again. The answer is about the rig it was sent to: if the page has come
// to show another since, it is said as that rig's, in the boxes at the top.
async function addFrom(entry) {
  const drawer = state.drawer;
  if (!drawer) return;
  // Never quietly: whatever stops an add is said in the drawer (review of #130).
  const refuse = (why) => { drawer.said = why; render(); };
  const run = shownRun(), rig = shownRigName();
  if (drawer.run === null && run !== null) drawer.run = run;
  if (!drawer.slot) return refuse("To add a plugin, use an “Add after…” in a chain.");
  if (run === null) return refuse("The agent is not answering, so nothing can be added.");
  if (drawer.run !== run) return refuse(`The rig changed since the library was opened (it is now ${rig}): close this, and open it again from the chain.`);
  const live = liveBody();
  if (!live) return refuse("The rig's chains could not be read just now, so nothing is added: try again in a moment.");
  const model = R.rackModel(live, state.plugins, state.profiles);
  const track = model.tracks.find((t) => t.name === drawer.track) || null;
  const slot = track ? R.addSlots(track).find((x) => x.key === drawer.slot.key) || drawer.slot : drawer.slot;
  const blocked = R.addBlocked({ health: state.health, track, slot, locked: state.locked });
  if (blocked) return refuse(blocked);
  if (!R.libraryEntry(entry).addable) return refuse(`${entry.name} cannot be added: ${R.libraryEntry(entry).why}`);
  const key = `${drawer.track}\u0000${slot.key}`;
  let { name, shown } = R.addName(entry, live, namesInFlight(key));
  const at = Date.now();
  state.adding.set(key, { shown, at });
  state.addRefused.delete(key);
  closeDrawer();
  const send = (n) => { const call = R.addCall(drawer.track, slot, entry, n); return request("POST", call.path, call.json); };
  let answer = await send(name);
  if (!answer.ok && answer.code === "name_taken" && shownRun() === run) {
    const fresh = await request("GET", "/live");
    ({ name, shown } = R.addName(entry, fresh.ok ? fresh.body : liveBody(), [...namesInFlight(key), shown]));
    if (state.adding.has(key)) { state.adding.set(key, { shown, at }); render(); }
    answer = await send(name || shown);
  }
  const outcome = R.addOutcome(shown, answer);
  placeAnswer(run, () => {
    if (outcome.ok) {
      state.adding.set(key, { shown, done: outcome.name, at: Date.now() });
      knowAdded(drawer.track, entry, answer.body.args || {});
      return;
    }
    state.adding.delete(key);
    // Said at its place; where that is not on the page now, in the boxes.
    if (placeOnPage(key)) state.addRefused.set(key, outcome.reason);
    else fail(`Not added: ${shown}`, { reason: outcome.reason.replace(/^Not added: /, "") });
  }, () => {
    state.adding.delete(key);
    if (outcome.ok) state.notice = `Added ${outcome.name} to ${rig}`;
    else fail(`Not added to ${rig}: ${shown}`, { reason: outcome.reason.replace(/^Not added: /, "") });
  });
  render();
  kick();
}

// Whether the place with this key is drawn on the page now.
function placeOnPage(key) {
  const parts = state.rackParts;
  if (!parts) return false;
  if (parts.start && parts.start.key === key) return true;
  return [...parts.slots.values()].some((slot) => slot.add && slot.add.key === key);
}

// What the page already knows of a plugin it has just added, so its row is
// drawn once, as what it is (its profile's controls), when the chain shows
// it, and not plain and then again when /live/plugins answers (the rule of
// #135: a row is not rebuilt a moment after it is drawn). Only beside
// plugins already read: with none, every row is plain until they are.
function knowAdded(track, entry, args) {
  if (!state.plugins || typeof args.name !== "string") return;
  const view = R.libraryEntry(entry);
  state.plugins = { ...state.plugins, [args.name]: { track, uri: entry.uri, uid: null, type: "lv2", role: null, width: view.width } };
  if (typeof args.copy === "string") {
    state.plugins[args.copy] = { track, uri: null, uid: R.COPY_UID, type: "internal", role: "stereo", width: null };
  }
}

// The drawer: the library by kind, searchable, over the page (a sheet at a
// phone's width). Built once each time it is opened, and its list once the
// library is read; everything that changes while it is open (whether adding
// is possible and why not, what an entry would do to the chain's width,
// what the search hides) is drawn in place, so a chain changed from another
// device, or a poll, rebuilds nothing under the hand or the search.
function drawerRegion(el, model) {
  const frame = el.parentNode;
  const open = !!state.drawer;
  for (const child of frame ? frame.children : []) if (child !== el) child.inert = open;
  el.hidden = !open;
  if (!open) { patch(el, "closed", () => []); return; }
  const d = state.drawer;
  // Opened while /health did not answer: it is for the first rig it sees
  // once it answers (review of #130: one opened then stayed on no rig, and
  // an add from it did nothing). Until then it adds nothing, and says why.
  if (d.run === null && shownRun() !== null) d.run = shownRun();
  // Opened for another rig than the one shown now: nothing is added to this
  // one from it, and it says so until it is opened again.
  const moved = d.run !== null && shownRun() !== null && shownRun() !== d.run;
  const track = model && d.track && !moved ? model.tracks.find((t) => t.name === d.track) || null : null;
  const slot = d.slot ? (track ? R.addSlots(track).find((x) => x.key === d.slot.key) : null) || d.slot : null;
  if (slot) d.slot = slot;
  const where = !d.slot ? "Look through what can be added." : d.slot.first ? `Add at the start of ${d.track}` : `Add after ${d.slot.after} on ${d.track}`;
  patch(el, JSON.stringify(["open", d.track, d.slot && d.slot.key]), () => {
    const parts = { list: h("div", { class: "drawer-list" }), why: h("p", { class: "drawer-why", role: "status" }) };
    parts.search = h("input", { type: "search", name: "library-search", placeholder: "Search by name or kind",
      "aria-label": "Search the library", autocomplete: "off", spellcheck: "false",
      oninput: (event) => { d.query = event.target.value; filterDrawer(parts); } });
    parts.search.value = d.query;
    parts.close = h("button", { class: "drawer-close", "aria-label": "Close the library", onclick: closeDrawer }, "Close");
    const sheet = h("div", { class: "drawer-sheet", role: "dialog", "aria-modal": "true", "aria-labelledby": "drawer-title", tabindex: "-1" },
    h("div", { class: "drawer-head" },
      h("div", { class: "drawer-title" },
        h("h2", { id: "drawer-title" }, "LIBRARY"),
        parts.close),
      h("p", { class: "drawer-where" }, where),
      parts.search, parts.why),
    parts.list);
    parts.sheet = sheet;
    state.drawerParts = parts;
    return [h("div", { class: "drawer-back", "aria-hidden": "true", onclick: closeDrawer }), sheet];
  });
  const parts = state.drawerParts;
  patch(parts.list, JSON.stringify([state.library ? state.library.plugins.map((p) => p.uri) : null, state.libraryError]), () => {
    parts.entries = [];
    parts.groups = [];
    if (!state.library) {
      return state.libraryError
        ? h("p", { class: "drawer-none", title: `The library could not be read: ${state.libraryError}. Asking again every ${RETRY_MS / 1000} s.` }, "Library unreadable")
        : h("p", { class: "drawer-none" }, "Reading…");
    }
    const groups = R.libraryGroups(state.library);
    return [...groups.map((g) => {
      const items = g.entries.map((entry) => {
        const view = R.libraryEntry(entry);
        const item = { entry, view, group: g.label,
          note: h("span", { class: "lib-note" }),
          button: h("button", { class: "lib-entry", "data-uri": entry.uri, onclick: () => addFrom(entry) }) };
        item.button.append(
          h("span", { class: "lib-line" }, h("span", { class: "lib-name" }, entry.name),
            view.mono ? h("span", { class: "mono-badge", title: R.MONO_TITLE, "aria-label": R.MONO_TITLE }, "mono") : null),
          h("span", { class: "lib-bundle" }, view.bundle), item.note);
        parts.entries.push(item);
        return item.button;
      });
      const section = h("section", { class: "lib-group", "data-kind": g.id || "other" }, h("h3", {}, g.label), items);
      parts.groups.push({ section, items: parts.entries.slice(-items.length) });
      return section;
    }), h("p", { class: "drawer-none lib-nothing", hidden: true }, "Nothing in the library matches that.")];
  });
  const blocked = moved
    ? `The rig changed since the library was opened (it is now ${shownRigName()}): close this, and open it again from the chain.`
    : R.addBlocked({ health: state.health, track, slot: d.slot, locked: state.locked });
  // Opened to look through, with a rig up: where adding is done from.
  const why = (!d.slot && !moved && R.readsLive(state.health) ? "To add a plugin, use an “Add after…” in a chain." : blocked)
    || d.said || null;
  const whyShort = R.shortSaid(why || "");
  if (parts.why.textContent !== whyShort) parts.why.textContent = whyShort;
  parts.why.title = why || "";
  parts.why.hidden = !why;
  const home = drawerHome(parts);
  const flying = namesInFlight();
  for (const item of parts.entries || []) {
    const warning = !blocked && item.view.addable && track && d.slot
      ? R.addWarning(track, d.slot, item.entry, R.addName(item.entry, liveBody(), flying).shown) : null;
    const note = item.view.why ? "Can't add" : warning ? warning.text : "";
    if (item.note.textContent !== note) item.note.textContent = note;
    item.note.title = item.view.why ? `Cannot be added: ${item.view.why}` : warning ? warning.text : "";
    item.note.classList.toggle("warn", !!warning);
    const off = !!blocked || !item.view.addable || !d.slot || d.run === null;
    // An entry turned off with the focus on it would drop the focus to the
    // page, where no key reaches the drawer: it goes home first.
    if (off && !item.button.disabled && document.activeElement === item.button) home.focus({ preventScroll: true });
    item.button.disabled = off;
  }
  filterDrawer(parts);
  // However the focus left the drawer (opened just now, a control under it
  // hidden or gone), it comes back: the rest of the page is inert.
  if (!parts.sheet.contains(document.activeElement)) home.focus({ preventScroll: true });
}

// The search, applied in place: an entry that does not match is hidden, a
// group with none left too, and nothing is rebuilt.
function filterDrawer(parts) {
  if (!parts.groups) return;
  const query = state.drawer ? state.drawer.query : "";
  let any = false;
  for (const g of parts.groups) {
    let shown = 0;
    for (const item of g.items) {
      const match = R.libraryMatch(item.entry, item.group, query);
      item.button.hidden = !match;
      if (match) shown++;
    }
    g.section.hidden = !shown;
    any = any || shown > 0;
  }
  const nothing = parts.list.querySelector(".lib-nothing");
  if (nothing) nothing.hidden = any;
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

function detail(unit, size, acts = null) {
  const panel = unit.panel;
  return h("div", { class: "detail" },
    panel.groups.map((g) => h("section", { class: "group" }, g.label ? h("h3", {}, g.label) : null,
      h("div", { class: "knobs" }, g.controls.map((c) => control(unit, c, size))))),
    panel.advanced.length ? h("section", { class: "group advanced" }, h("h3", {}, "More"),
      h("div", { class: "knobs" }, panel.advanced.map((c) => control(unit, c, "small")))) : null,
    panel.missing.length ? h("p", { class: "missing", title: "In the profile but not on the running plugin" },
      `Missing: ${panel.missing.map((c) => c.label).join(", ")}`) : null,
    acts);
}

function renderRack(regions, model, unsaved, status) {
  patch(regions.title, status.title, () => status.title);
  patch(regions.status, JSON.stringify(status), () => statusLine(status));
  patch(regions.lock, JSON.stringify([state.locked, state.armed]), () => lockButton());
  undoRegion(regions.undo, "rack");
  tilesRegion(regions.tiles);
  problemsRegion(regions.problems);
  const ampView = R.ampCard(state.amp, state.ampModels, { locked: state.locked, pending: state.ampPending });
  const open = tabsRegion(regions, model, ampView);
  chainRegion(regions, model, status, open);
  drawerRegion(regions.drawer, model);
  ampRegion(regions.amp);
  rigsRegion(regions.rigs, { newRig: true, deletes: true });
  stripsRegion(regions.wiring, model, unsaved);
  pedalsRegion(regions.pedals);
  agentRegion(regions.channels);
  renderChanges(regions.changes);
  saveRegion(regions.save, unsaved);
  takesRegion(regions.takes);
}

// "Agent on …" (#181): the phone's URL when the agent listens on the
// network, and that it is this machine's alone when not (R.agentLine).
function agentRegion(el) {
  const agent = state.health && state.health.ok ? state.health.body.agent || {} : {};
  const line = R.agentLine(agent.listen ?? null, location.host);
  patch(el, JSON.stringify(line), () => h("p", { class: "agent", title: line.title || null }, line.text,
    line.urls.flatMap((u, i) => [i ? ", " : " ", h("a", { href: u.url, class: "phone-url", title: u.label || null }, u.url)]),
    line.after));
}

// A time of today as the page shows one (the log's are to the second).
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// The save column's heading. Its Undo, which undid live edits only, is the
// one Undo in each screen's header now (#209). The itemised list of
// unsaved changes that stood here was removed (2026-11-06); what is unsaved
// is the count on Save, and history is Revert.
function renderChanges(el) {
  patch(el, "SAVE", () => [h("h2", {}, "SAVE")]);
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
  const busyNow = state.health && state.health.ok ? (state.health.body.agent || {}).busy || null : null;
  const rebuilt = patch(el, JSON.stringify([keyed, state.saveAsOpen, state.saveResult, last, revert, state.saveRestartConfirm,
    busyNow, !!state.pending]), () => {
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
      // Why Save is off, or what is under way, in a few words; the whole
      // sentence on hover.
      view.blocked || state.saving ? h("p", { class: "sub save-note", title: view.note }, R.shortSaid(view.note)) : null,
      result ? resultBox(result, {
        cls: "save-result",
        dismiss: () => { state.saveResult = null; state.saveRestartConfirm = false; render(); },
        offer: result.ok && result.offer && !state.saveRestartConfirm
          ? h("button", { class: "rig-act primary offer", disabled: !!state.pending,
            onclick: () => { state.saveRestartConfirm = true; render(); } }, "Restart") : null,
      }) : null,
      result && result.ok && result.offer && state.saveRestartConfirm
        ? confirmBox("Restart?", restartForSave, () => { state.saveRestartConfirm = false; render(); }, { pending: !!state.pending || !!busyNow })
        : null,
      last ? h("p", { class: "sub last-save", title: `${last.file}, ${last.then}` }, `Saved ${last.time}`) : null,
      helpAnchor("save"),
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
// minute (its "5 min ago" moves on), the point being reverted to, and the
// question asked when unsaved changes stand in the way.
function revertKey(view) {
  const r = state.revert;
  const rows = R.revertRows(r.list, Date.now(), clockDay);
  return { rig: r.rig, rows, choice: r.choice, drop: r.drop, pending: r.pending };
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
  if (r.open) { Object.assign(r, { open: false, choice: null, drop: null }); render(); return; }
  Object.assign(r, { open: true, rig, list: null, choice: null, drop: null });
  render();
  refreshSaves();
}

// A point, tapped, is reverted to at once: no confirm, since the one Undo
// puts the file back (#209). Only unsaved changes the revert would drop,
// which no Undo brings back, are asked about: "Drop changes?".
function revertPanel(view) {
  const key = revertKey(view);
  const { rows } = key;
  return h("div", { class: "revert", role: "group", "aria-label": `Revert ${key.rig} to a previous save` },
    h("h3", {}, `Revert ${key.rig}`),
    rows.loading ? h("p", { class: "sub" }, "Reading…") : null,
    rows.problem ? h("p", { class: "rig-problem", title: rows.problem }, R.shortSaid(rows.problem)) : null,
    rows.empty ? h("p", { class: "sub" }, "No saves") : null,
    rows.rows.length ? h("ul", { class: "revert-list", title: rows.notes.join(" ") || null }, rows.rows.map((row) => h("li", {},
      h("button", { class: `revert-point${row.id === key.choice ? " chosen" : ""}`, "data-point": row.id,
        "aria-pressed": String(row.id === key.choice), disabled: !!key.pending, onclick: () => revertTo(key.rig, row, false) },
      h("span", { class: "n" }, row.title, row.current ? h("span", { class: "tag" }, "now") : null),
      h("span", { class: "s" }, row.sub))))) : null,
    key.drop ? confirmBox("Drop changes?", () => revertTo(key.rig, key.drop, true),
      () => { state.revert.drop = null; render(); }, { pending: !!key.pending, cls: "revert-drop" }) : null);
}

// POST /rigs/<rig>/revert, then what it said, under the buttons. A refusal
// for unsaved work asks, once, whether to drop it.
async function revertTo(rig, row, discard) {
  const r = state.revert;
  if (r.pending) return;
  r.pending = true;
  r.choice = row.id;
  r.drop = null;
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
    r.drop = row;
    state.saveResult = null;
  } else {
    state.saveResult = said;
  }
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
    check.textContent = input.value && problem ? R.shortSaid(problem) : "";
    check.title = problem || "";
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
    h("label", {}, h("span", {}, "Name"), input), check,
    h("div", { class: "save-acts" }, go,
      h("button", { class: "save-act", type: "button", onclick: () => { state.saveAsOpen = false; render(); } }, "Cancel")));
}

// ---------------------------------------------------------------- recording (S7, S8)

// The stage's record bar: the take's name, Record and Stop, and while a take
// runs its name and elapsed time. Keyed without what is typed and without
// the elapsed time (redrawn by `tickElapsed`), so neither a poll nor the
// clock rebuilds the field under the fingers.
//
// The name (#134). The field shows the agent's default for the next take
// (`view.default`) until the player types; `state.takeName` holds only what
// they typed (`R.takeTyped`), so Record on an untouched field sends no name
// and the agent names the take, by its count at that moment. The key holds
// the default, so a new one (a take finished, here or on another page; a rig
// switch; midnight) rebuilds the field with it, and what the player typed
// goes back in as it was: only clearing the field, or recording with it,
// ends it.
function recordBarView(typed = state.takeName) {
  return R.recordBar(state.health, state.record, typed, state.recordPending);
}

// True while a rebuild hands the focus back to the name field: that focus is
// not the player entering the field, so it selects nothing (`nameField`).
let restoringTakeFocus = false;

function recordRegion(el) {
  const view = recordBarView();
  const held = el.querySelector('input[name="take-name"]');
  const typing = !!held && document.activeElement === held;
  // Held open and emptied: the player is about to type, and a rebuild must
  // not put a name back under the caret (the placeholder shows the default).
  const cleared = typing && held.value === "";
  // Where the caret or the selection was, for a rebuild to put back.
  const caret = typing
    ? { start: held.selectionStart, end: held.selectionEnd, way: held.selectionDirection, length: held.value.length }
    : null;
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
        recording ? h("span", { class: "record-elapsed", role: "timer", "aria-label": "Elapsed" }) : helpAnchor("record")),
      recording
        ? h("div", { class: "take-now" }, h("span", { class: "k" }, "Take name"), h("span", { class: "v" }, view.name))
        : view.state === "idle" ? nameField(view) : null,
      view.state === "idle" ? h("p", { class: "take-whole", "aria-hidden": "true" }) : null,
      // The ruler a name is measured on (`nameCut`): never seen, and no part of the layout.
      view.state === "idle" ? h("span", { class: "take-rule", "aria-hidden": "true" }, h("span", {})) : null,
      view.reason ? h("p", { class: "record-note", title: view.reason }, R.shortSaid(view.reason)) : null,
      view.state === "idle" ? h("p", { class: "record-note", id: "take-note", "aria-live": "polite" }) : null,

      view.stems ? h("p", { class: "record-stems" }, view.stems) : null,
      h("div", { class: "record-acts" },
        h("button", { class: "record-act rec", disabled: view.record.disabled, onclick: recordStart }, view.record.label),
        h("button", { class: "record-act stop", disabled: view.stop.disabled, onclick: recordStop }, view.stop.label)),
      // A stop names the take, its folder and `take check`'s verdict (#189),
      // a take with problems in the refusal colour and as an alert, with
      // the stop's own reason too when it failed.
      result ? h("div", { class: `record-result ${result.ok ? "ok" : "refused"}`, role: result.ok ? "status" : "alert",
        title: [result.text, !result.ok ? result.reason : null, result.folder].filter(Boolean).join(" ") || null },
        h("strong", {}, result.title),
        h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: () => { state.recordResult = null; render(); } }, "×")) : null,
    ];
  });
  if (rebuilt && typing) {
    const input = el.querySelector('input[name="take-name"]');
    if (input) {
      if (cleared) input.value = "";
      restoringTakeFocus = true;
      try { input.focus(); } finally { restoringTakeFocus = false; }
      // The field as the player left it: a caret they placed stays where it
      // was (a new default once selected itself here, and the next key
      // replaced the name they meant to add to), kept within the new text;
      // a name selected whole stays selected whole, however long the new one.
      const length = input.value.length;
      const whole = caret.length > 0 && caret.start === 0 && caret.end === caret.length;
      input.setSelectionRange(whole ? 0 : Math.min(caret.start, length), whole ? length : Math.min(caret.end, length),
        caret.way || "none");
    }
  }
  drawNameCheck(el);
  tickElapsed();
}

function drawNameCheck(el) {
  const view = recordBarView();
  if (view.state !== "idle") return;
  const note = el.querySelector("#take-note");
  const button = el.querySelector("button.record-act.rec");
  if (note) { note.textContent = view.note || ""; note.title = view.noteTitle || ""; note.classList.toggle("bad", !!view.bad); }
  if (button) { button.disabled = view.record.disabled; button.textContent = view.record.label; }
  drawNameWhole(el, view.default);
}

// The name in full on a line under the field, when the field is too narrow
// to show all of it. A default name is the rig, the date and the take's
// number, in that order, and a field cuts its text at the end: on a phone
// the stage's field holds 31 characters, so every default but the shortest
// rig's lost its number, the part the player most needs to read. The line
// wraps, so a name of any length (64 characters at most) is all there and
// the page never scrolls sideways for it.
//
// The field itself is left as it is, its type size too (under 16 px, a
// phone's browser zooms the page when the field is entered), and nothing
// here touches its text, caret or scroll. Drawn with the bar (every poll, so
// a phone turned on its side is caught up with within one) and on each key.
//
// The line is of the name Record would use, not of the field's text: with
// the field empty, or holding only spaces, that is the default (`fallback`),
// which the placeholder shows cut and the field takes back when it is left.
// So leaving the field changes nothing here, and Record does not move
// between a press on it and the release. It did: an emptied field had no
// line, the press on Record blurred the field, the default and its line came
// back, and Record was 26 px lower when the finger came up, so the top of
// the button started no take. The width is measured on a ruler in the page
// (`nameCut`), not read from the field's `scrollWidth` alone, since a name
// that is not in the field has none: one measure for the default whether it
// is the field's text or its placeholder, so the two cannot disagree.
function drawNameWhole(el, fallback) {
  const input = el.querySelector('input[name="take-name"]');
  const whole = el.querySelector(".take-whole");
  if (!input || !whole) return;
  const name = input.value.trim() ? input.value : fallback || "";
  const text = name && nameCut(el, input, name) ? name : "";
  if (whole.textContent !== text) whole.textContent = text;
}

// Whether `input` is too narrow to show `text` whole.
//
// Measured in the page's own layout: `.take-rule` is a hidden element in the
// bar that carries the field's type (one rule in style.css gives both their
// font), and its width with `text` in it is compared with the room inside
// the field. So whatever the browser does to the field's text it does to the
// ruler's. A canvas did this for a day and measured at the computed font
// size, which is not always the size drawn: text-only zoom (Firefox's Zoom
// Text Only, a phone's font scale) enlarges the text and leaves the computed
// 16 px and a canvas's text as they were, so on a phone with large text the
// name was cut, the canvas said it fitted, and the line was gone again.
// Where the field holds `text` itself its own `scrollWidth` is asked as
// well, and a cut that either sees shows the line.
//
// A measure that cannot be made says "cut", and never throws: the line shown
// for a name that fits is the name said twice, and the line missing is the
// take's number off the page. The canvas threw where there was no 2D
// context, from inside the draw, and the whole stage went with it.
function nameCut(el, input, text) {
  try {
    const rule = el.querySelector(".take-rule span");
    const style = getComputedStyle(input);
    const room = input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    if (!rule || !(room > 0)) return true;
    if (rule.textContent !== text) rule.textContent = text;
    const width = rule.getBoundingClientRect().width;
    if (!(width > 0) || width > room) return true;
    return input.value === text && input.scrollWidth > input.clientWidth;
  } catch {
    return true;
  }
}

function nameField(view) {
  const input = h("input", { name: "take-name", type: "text", autocomplete: "off", spellcheck: "false",
    autocapitalize: "none", placeholder: view.default || "e.g. riff-idea", "aria-describedby": "take-note",
    enterkeyhint: "go" });
  input.value = state.takeName || view.default || "";
  // Redrawn through render(), whose key holds the view and not the text, so
  // the check under the field follows the typing without a rebuild.
  input.addEventListener("input", () => {
    state.takeName = R.takeTyped(input.value, view.default);
    drawNameCheck(input.closest(".record-bar"));
  });
  // A default is selected whole when the player enters the field, so that
  // typing replaces it; what they typed is theirs to edit, and is left alone.
  // Only on entering: a rebuild that hands the focus back selects nothing
  // (`restoringTakeFocus`), and a second press places a caret as it would
  // anywhere.
  //
  // Selected once the press or tap that gave the focus is over, not inside
  // the focus event: WebKit places the caret after that event and undoes a
  // selection made in it, and then the first key lands inside the default.
  // So the focus defers it to the next task; the release of a press that
  // gave the focus is kept from moving the caret, and selects then and once
  // more in the task after. A deferred selection still waiting is dropped
  // the moment the player does anything else in the field (a key, a second
  // press, leaving it): a timer of 0 ms was seen to fire 11 ms late here,
  // after an End pressed meanwhile, and took back the caret it had placed.
  // UNTESTED on WebKit: only Chromium is here, where the test is a press, a
  // release, and the whole default still selected (test_ui.py).
  const selectDefault = () => {
    if (document.activeElement === input && !state.takeName && input.value) {
      input.setSelectionRange(0, input.value.length);
    }
  };
  let later = null;
  const selectLater = () => { clearTimeout(later); later = setTimeout(selectDefault, 0); };
  const dropLater = () => { clearTimeout(later); later = null; };
  let pressGaveFocus = false;
  input.addEventListener("pointerdown", () => { pressGaveFocus = document.activeElement !== input; dropLater(); });
  input.addEventListener("pointercancel", () => { pressGaveFocus = false; });
  input.addEventListener("focus", () => { if (!restoringTakeFocus) selectLater(); });
  input.addEventListener("mouseup", (event) => {
    if (!pressGaveFocus) return;
    pressGaveFocus = false;
    if (state.takeName || !input.value) return;
    event.preventDefault();
    selectDefault();
    selectLater();
  });
  input.addEventListener("keydown", dropLater);
  input.addEventListener("blur", dropLater);
  // Left empty, the field shows the default again: an empty name is the
  // default. Nothing else is drawn for it: the line under the field already
  // held the default (`drawNameWhole`), so this moves nothing on the page.
  input.addEventListener("blur", () => { if (!input.value.trim()) input.value = view.default || ""; });
  input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !recordBarView().record.disabled) recordStart(); });
  return h("label", { class: "take-name" }, h("span", {}, "Take name"), input);
}

// The rack's takes (S8, #189): one line naming the takes folder, which is
// where the takes are looked at, and on the laptop itself Open folder, which
// asks the agent to open it in the desktop's file manager. No list, and
// nothing polled: a take's verdict is said when it stops (`R.recordOutcome`).
function takesRegion(el) {
  const view = R.takesFolder(state.health, state.takesOpen);
  const result = view.result;
  patch(el, JSON.stringify(view), () => [
    h("h2", {}, "TAKES"),
    view.dir ? h("p", { class: "takes-dir", title: "Takes go in this folder" }, h("code", {}, view.dir)) : null,
    view.button ? h("button", { class: "rig-act open-takes", disabled: view.button.disabled, onclick: openTakesFolder }, view.button.label) : null,
    result ? resultBox(result, { cls: "save-result open-result", dismiss: () => { state.takesOpen.result = null; render(); } }) : null,
    helpAnchor("takes"),
  ]);
}

async function openTakesFolder() {
  const open = state.takesOpen;
  if (open.pending) return;
  open.pending = true;
  render();
  let answer;
  try {
    answer = await request("POST", R.OPEN_FOLDER_CALL.path, R.OPEN_FOLDER_CALL.json);
  } finally {
    open.pending = false;
  }
  open.result = { ...R.openFolderOutcome(answer), at: noticeClock() };
  armNotices();
  render();
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
    title: h("span", { class: "status-slot" }),
    pick: h("span", { class: "pick-slot" }),
    undo: h("span", { class: "undo-slot" }),
    lock: h("span", { class: "lock-slot" }),
  };
  // One row: the state's dot, the rig dropdown in the rig's name's place
  // (the name itself kept for the screen readers' heading), and the Lock.
  regions.row.append(regions.title, regions.pick, regions.undo, regions.lock);
  // Built once, its parts patched on their own, as the rack's is: the master
  // fader under the rig's state keeps a drag and its focus through a poll.
  // Start, Stop and the busy line (#182) under the master and the tiles,
  // so the header is no taller locked than it was before the dropdown.
  regions.header = h("header", { class: "stage-head" }, regions.row, regions.detail, masterFader("stage"), regions.tiles, regions.rigs);
  const frame = h("div", { class: "screen stage-screen" }, regions.header, regions.problems, regions.record, regions.levels, regions.amp, regions.cards,
    h("nav", { class: "stage-nav" }, h("a", { href: "#stage", class: "here", "aria-current": "page" }, "Stage"), h("a", { href: "#rack" }, "Rack"),
      helpAnchor("stage", "help")));
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
  patch(regions.title, JSON.stringify([status.tone, status.title]), () => [
    h("span", { class: `dot tone-${status.tone}`, "aria-hidden": "true" }), h("h1", { class: "rig-name sr-only" }, status.title)]);
  patch(regions.lock, JSON.stringify([state.locked, state.armed]), () => lockButton());
  undoRegion(regions.undo, "stage");
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
      channelVolume(t, "stage", state.levelUpdaters)))] : [];
  });
  const structure = model ? JSON.stringify([model.tracks.map((t) => [t.name, t.chain, t.units.map((u) => [u.uri, u.profile, u.panel, u.monoNote])]), state.locked, [...state.open]]) : JSON.stringify(["none", status.tone, state.locked]);
  // What the lock holds is the Lock button's to say (its label and title)
  // and the guide's: no paragraph about it here (#209).
  patch(regions.cards, structure, () => {
    state.updaters = [];
    if (!model) {
      return [status.tone === "busy" ? h("p", { class: "empty", title: "The rig is changing; the cards come back when it is up." }, "Changing…")
        : h("p", { class: "empty", title: "Nothing is running in Sushi." }, "No rig")];
    }
    return model.tracks.filter((t) => t.units.length).map((t) => h("section", { class: "track" }, h("h2", {}, t.name), t.units.map(card)));
  });
  stageAmpRegion(regions.amp);
  // The dropdown is shown off while locked; Start and Stop are not there.
  rigsRegion(regions.rigs, { dropdown: regions.pick, title: status.title });
}

// ---------------------------------------------------------------- pedals (#192)
// A device control assigned to a target on the rack: beside an effect's on/off
// a pedal badge, beside a channel's volume another, each opening a small
// panel of selects in its row (as a strip's Preset… panel opens in its
// strip); and the Pedals section under the strips, saying what each control
// does on this rig with its "heard" light. The choices are R.pedalOptions',
// from GET /midi's `takes`; what is written is the agent's (POST
// /rig/midi/assign), which compiles first and refuses with the compiler's
// words. Behind the lock, as a structural edit is. Only shown where the
// machine file describes a device: a rig without one sees nothing new.

function pedalBoard() {
  return R.pedalBoard(state.midi, state.pedalUi.device);
}

// Whether the rack offers pedals at all: a device whose controls can take
// something, or a map the rig file has.
function pedalsShown(board) {
  return board.ok && (board.controls.some((c) => !c.job && c.takes.length) || board.assignments.length > 0);
}

// What a unit's badge assigns: its on/off, and each expression control's
// sweep of one of its parameters (R.pedalParameters: its panel's, settable).
function unitPedalTarget(track, unit) {
  const params = R.pedalParameters(unit.panel);
  return { key: `u\u0000${track.name}\u0000${unit.name}`, what: unit.name, path: `${track.name}/${unit.name}`,
    rows: [{ binding: "bypass", target: `${track.name}/${unit.name}`, label: "On/off" }], params };
}

function stripPedalTarget(channel) {
  return { key: `c\u0000${channel}`, what: channel, path: channel, params: null,
    rows: [{ binding: "mute", target: channel, label: "On/off" }, { binding: "volume", target: channel, label: "Volume" }] };
}

// The controls on a target's rows, for its badge: "C", "C · pedal".
function pedalOnTarget(board, t) {
  const on = t.rows.map((r) => R.pedalOn(board, r.binding, r.target)).filter(Boolean);
  if (t.params) {
    for (const a of board.assignments) if (a.binding === "parameter" && String(a.target).startsWith(`${t.path}/`)) on.push(a.control);
  }
  return [...new Set(on)];
}

// A target's badge and its panel, each on its own key, drawn in place: the
// row they are in is never rebuilt for them, and the light is a class.
function pedalSlots(badgeEl, panelEl, t) {
  if (!badgeEl || !panelEl) return;
  const board = pedalBoard();
  const shown = pedalsShown(board);
  const pu = state.pedalUi;
  const on = shown ? pedalOnTarget(board, t) : [];
  const open = shown && pu.open === t.key;
  const panelId = pedalPanelId(t.key);
  if (panelEl.id !== panelId) panelEl.id = panelId;
  patch(badgeEl, JSON.stringify([shown, on, open, state.locked]), () => (!shown ? null : h("button", {
    type: "button", class: `pedal-badge${on.length ? " assigned" : ""}`, "aria-expanded": String(open),
    "aria-controls": panelId, "data-pedal": t.key,
    "aria-label": `Pedal for ${t.what}: ${on.length ? on.map(R.pedalName).join(", ") : "none"}`,
    title: on.length ? `${on.map(R.pedalName).join(", ")} on ${t.what}` : `Assign a pedal control to ${t.what}`,
    onclick: () => { pu.open = open ? null : t.key; pu.confirm = null; pu.refused = null; render(); },
  }, pedalIcon(), on.length ? h("span", { class: "on" }, on.map(R.pedalName).join(" ")) : null)));
  const button = badgeEl.querySelector(".pedal-badge");
  if (button) button.classList.toggle("lit", on.some((c) => (pu.lights[c] || {}).until > Date.now()));
  const focus = focusedName(panelEl);
  const rebuilt = patch(panelEl, JSON.stringify(open ? [board, t.rows, t.params, pu.confirm && pu.confirm.key === t.key ? [pu.confirm.what, pu.confirm.row, pu.confirm.value] : null,
    pu.pending, pu.refused && pu.refused.key === t.key ? pu.refused.reason : null, state.locked] : null),
  () => (open ? pedalPanel(board, t) : null));
  if (rebuilt) refocus(panelEl, focus);
}

function pedalIcon() {
  // A treadle on its hinge: the expression pedal, drawn small.
  return h("svg", { width: 16, height: 16, viewBox: "0 0 16 16", "aria-hidden": "true", class: "icon" },
    h("path", { d: "M2 13h12" }), h("path", { d: "M4 13 12 6" }), h("circle", { cx: 4, cy: 13, r: 1.2 }));
}

// The panel: a select a row (none, or each control that can take it), and
// for an effect, each expression control's sweep of one of its parameters.
// Locked, the selects are off and the lock's reason is said.
function pedalPanel(board, t) {
  const pu = state.pedalUi;
  const locked = pedalLockRefusal();
  const busy = !!pu.pending;
  const rows = [];
  const confirm = pu.confirm && pu.confirm.key === t.key ? pu.confirm : null;
  const pick = (name, label, options, value, onChange) => {
    const select = h("select", { name, "aria-label": label, disabled: busy || !!locked, title: locked || null },
      options.map((o) => h("option", { value: o.value }, o.label)));
    // While a move is asked about, its select shows the control picked.
    select.value = confirm && confirm.row === name ? confirm.value : value;
    select.addEventListener("change", () => { pu.row = { name, value: select.value }; onChange(select.value); });
    return h("label", { class: "selector pedal-pick" }, h("span", { class: "label" }, label.replace(`${t.what} `, "")), select);
  };
  for (const r of t.rows) {
    if (!R.pedalTakes(board, r.binding)) continue;
    const current = R.pedalOn(board, r.binding, r.target) || "";
    rows.push(pick(`pedal-${r.binding}-${t.path}`, `${t.what} ${r.label.toLowerCase()}`, R.pedalOptions(board, r.binding, r.target), current,
      (value) => choosePedal(t, R.pedalChoice(board, r.binding, r.target, value, board.ask ? board.controller : null))));
  }
  if (t.params && t.params.length) {
    for (const c of board.controls.filter((x) => !x.job && x.takes.includes("parameter"))) {
      const use = R.pedalUse(board, c.name);
      const here = use && use.binding === "parameter" && String(use.target).startsWith(`${t.path}/`) ? String(use.target).slice(t.path.length + 1) : "";
      const options = [{ value: "", label: "none" }, ...t.params];
      rows.push(pick(`pedal-sweep-${c.name}-${t.path}`, `${t.what} ${R.pedalName(c.name)} sweeps`, options, here, (value) => {
        if (value) choosePedal(t, R.pedalChoice(board, "parameter", `${t.path}/${value}`, c.name, board.ask ? board.controller : null));
        else choosePedal(t, { call: R.pedalCall(c.name, null, null), confirm: null, what: `${R.pedalName(c.name)} off ${t.what}` });
      }));
      if (use && !here) rows.push(h("p", { class: "sub", title: `${R.pedalName(c.name)} is on ${R.pedalSaid(use.binding, use.target)} now.` }, `on ${R.pedalSaid(use.binding, use.target)}`));
    }
  }
  const refused = pu.refused && pu.refused.key === t.key ? pu.refused.reason : null;
  return h("div", { class: "pedal-panel", role: "group", "aria-label": `Pedal for ${t.what}`, title: board.controller || null },
    board.controller ? null : h("p", { class: "sub" }, "No device"),
    rows.length ? rows : h("p", { class: "sub", title: "None of the device's controls can take this." }, "Nothing fits"),
    busy ? h("p", { class: "sub", role: "status" }, "Assigning…") : null,
    locked ? h("p", { class: "sub lock-says", title: locked }, lockIcon(), "Locked") : null,
    refused ? h("p", { class: "row-said refused", role: "alert", title: refused }, R.shortSaid(refused)) : null);
}

function pedalLockRefusal() {
  return R.lockRefusal(state.locked, "pedal");
}

// The id of a target's panel, for its badge's aria-controls: the key, with
// every character an id should not hold written as its code.
function pedalPanelId(key) {
  return "pedal-panel-" + [...key].map((ch) => (/[A-Za-z0-9-]/.test(ch) ? ch : `_${ch.codePointAt(0)}_`)).join("");
}

// A choice made in a select: refused locked, asked to confirm when it moves
// a control in use, else sent.
function choosePedal(t, choice) {
  const pu = state.pedalUi;
  if (!choice) return;
  const locked = pedalLockRefusal();
  if (locked) { pu.refused = { key: t.key, reason: locked, lock: true }; render(); return; }
  pu.refused = null;
  // A control moved off what it did is not asked about (#209): the one Undo
  // puts the assignment back.
  sendPedal(t, choice);
}

async function sendPedal(t, choice) {
  const pu = state.pedalUi;
  if (pu.pending) return;
  const fromPanel = !!(document.activeElement && document.activeElement.closest(".pedal-panel-slot"));
  const locked = pedalLockRefusal();
  if (locked) { pu.refused = { key: t.key, reason: locked, lock: true }; pu.confirm = null; render(); return; }
  pu.pending = choice.what;
  pu.confirm = null;
  render();
  let answer;
  try {
    answer = await request("POST", choice.call.path, choice.call.json);
  } finally {
    pu.pending = null;
  }
  pu.result = R.pedalOutcome(choice.what, answer);
  pu.restartConfirm = false;
  if (answer.ok) {
    pu.open = null;
    await refreshMidi(true);
    const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
    if (rig.config_name) refreshRigChannels(rig.config_name, true).then(render);
  } else {
    pu.refused = { key: t.key, reason: `Not assigned: ${answer.reason}` };
  }
  render();
  if (answer.ok) {
    // The panel has closed: the focus it held goes back to the badge that
    // opened it, not to the page's body.
    const active = document.activeElement;
    // Found by its key, not by a selector: CSS.escape writes the key's NULs as U+FFFD.
    const badgeButton = [...document.querySelectorAll(".pedal-badge")].find((b) => b.dataset.pedal === t.key);
    if (badgeButton && (fromPanel || !active || active === document.body)) badgeButton.focus({ preventScroll: true });
  }
  kick();
}

async function restartForSave() {
  const result = state.saveResult;
  state.saveRestartConfirm = false;
  if (!result || !result.offer) return;
  const ok = await lifecycle("restart", result.offer.name);
  if (ok) state.saveResult = null;
  render();
}

async function restartForPedals() {
  const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
  state.pedalUi.restartConfirm = false;
  const ok = await lifecycle("restart", rig.config_name);
  if (ok) state.pedalUi.result = null;
  render();
}

// The Pedals section: the device and its state, then each control and what
// it does on this rig, with its light (and the pedal's value) drawn in place.
function pedalsRegion(el) {
  const board = pedalBoard();
  const pu = state.pedalUi;
  const shown = pedalsShown(board) || board.choose.length > 0;
  el.hidden = !shown;
  const device = board.controller ? R.pedalDeviceState(state.health, board.controller) : null;
  const rows = shown ? R.pedalSummary(board, pu.lights, Date.now()) : [];
  const busy = state.health && state.health.ok ? (state.health.body.agent || {}).busy || null : null;
  patch(el, JSON.stringify([shown, board.controller, board.choose, board.problem, device, rows.map((r) => [r.control, r.name, r.does, r.assigned, r.job]),
    pu.result, pu.restartConfirm, busy, !!state.pending]), () => (!shown ? [] : [
    h("h2", {}, "PEDALS"),
    board.choose.length ? h("label", { class: "selector pedal-pick" }, h("span", { class: "label" }, "Device for this rig's map"),
      (() => {
        const select = h("select", { name: "pedal-device", "aria-label": "Device for this rig's map" }, board.choose.map((n) => h("option", { value: n }, n)));
        select.value = board.controller || board.choose[0];
        select.addEventListener("change", () => { pu.device = select.value; render(); });
        return select;
      })()) : null,
    device ? h("p", { class: `pedal-device state-${device.state}` }, h("span", { class: "dot", "aria-hidden": "true" }),
      h("strong", {}, board.controller), ` · ${device.text}`) : null,
    board.problem ? h("p", { class: "rig-problem", title: board.problem }, R.shortSaid(board.problem)) : null,
    h("ul", { class: "pedal-list" }, rows.map((r) => h("li", { class: `pedal-row${r.assigned ? " assigned" : ""}${r.job ? " job" : ""}`, "data-control": r.control },
      h("span", { class: "heard", "aria-hidden": "true" }),
      h("strong", { class: "pedal-name" }, r.name), h("span", { class: "does" }, r.does), h("span", { class: "value" }),
      h("span", { class: "sr-only heard-said" })))),
    pu.result ? rigFileResult(pu.result, () => { pu.result = null; render(); }, () => { pu.restartConfirm = true; render(); }) : null,
    pu.restartConfirm ? confirmBox("Restart?", restartForPedals, () => { pu.restartConfirm = false; render(); },
      { pending: !!state.pending || !!busy }) : null,
    helpAnchor("pedals"),
  ]));
  // The lights and the pedal's value, in place.
  for (const r of rows) {
    const li = el.querySelector(`.pedal-row[data-control="${CSS.escape(r.control)}"]`);
    if (!li) continue;
    li.classList.toggle("lit", r.lit);
    li.classList.toggle("heard-once", r.heard);
    li.title = r.lit ? `${r.name}: heard just now` : r.heard ? `${r.name}: heard since the agent started` : `${r.name}: nothing heard from it yet`;
    // What the light says, for a screen reader: in the row's own text.
    const heardSaid = li.querySelector(".heard-said");
    const words = r.lit ? "heard just now" : r.heard ? "heard before" : "not heard yet";
    if (heardSaid.textContent !== words) heardSaid.textContent = words;
    const value = li.querySelector(".value");
    const text = r.value === null || r.value === undefined ? "" : String(r.value);
    if (value.textContent !== text) value.textContent = text;
  }
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

// What is being typed in a named field of `el`, to give back after `el` is
// rebuilt (`giveBack`): its name, its text, and whether it was typed (a
// field the player left untouched gets the value as it is now instead).
function typedIn(el) {
  const active = document.activeElement;
  if (!active || !el.contains(active) || !active.name) return null;
  return { name: active.name, value: active.value, typed: active.dataset.typed === "1" };
}

function giveBack(el, typing) {
  if (!typing) return;
  const input = el.querySelector(`[name="${CSS.escape(typing.name)}"]`);
  if (!input) return;
  input.focus();
  if (typing.typed && input.value !== typing.value) {
    input.value = typing.value;
    input.dispatchEvent(new Event("input"));  // typed: its field commits it when it is left
  }
  if (input.setSelectionRange && input.type === "text") input.setSelectionRange(input.value.length, input.value.length);
}

function refocus(el, name) {
  if (!name) return;
  const input = el.querySelector(`[name="${CSS.escape(name)}"]`);
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
    sources.map((g) => h("optgroup", { label: g.label || g.rig },
      g.options.map((o) => h("option", { value: o.value, disabled: !!o.disabled, title: o.title || null }, o.label)))));
  select.value = R.sourceValue(from);
  select.addEventListener("change", () => onChange(R.sourceFrom(select.value)));
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
// What a rig file write said (#209: a few words, the rest on hover): its
// title, the machine file's missing lines when a role is missing (a
// control: they are to copy), and the offer to restart, start or switch.
function rigFileResult(result, dismiss, play) {
  const more = [result.ok ? result.text : result.reason, result.programs ? result.programs.note : null,
    ...(result.programs ? result.programs.rows.map((r) => `${r.rig}: program ${r.from} → ${r.to}`) : []),
    result.ok ? result.commit : null].filter(Boolean).join(" ");
  return h("div", { class: `save-result rigfile-result ${result.ok ? "ok" : "refused"}`, role: result.ok ? "status" : "alert",
    title: more || null },
    h("strong", {}, result.title),
    result.hint ? h("details", { class: "machine-hint-box" }, h("summary", {}, "Machine lines"), h("pre", { class: "machine-hint" }, result.hint)) : null,
    result.ok && result.offer ? h("button", { class: "rig-act primary offer", disabled: !!state.pending,
      onclick: () => play(result.offer) }, result.offer.op === "restart" ? "Restart" : result.offer.label) : null,
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
    h("span", { class: "spacer" }), helpAnchor("newRig", "go help"), h("a", { class: "go", href: "#rack" }, "Back to the rack"),
  ]);
  problemsRegion(regions.problems);
  const nr = state.newRig;
  const choices = R.roleChoices(state.roles);
  // One channel to start from, once the roles are known, unless the player
  // has been at the form (a form emptied on purpose stays empty).
  if (!nr.touched && nr.channels.length === 0 && choices.inputs.length) nr.channels.push(R.newChannel(choices, []));
  if (!filesReady()) {
    patch(regions.form, "reading", () => h("p", { class: "empty" }, "Reading…"));
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
  const sources = R.sourceChoices(state.rigs, state.rigChannels, state.presets);
  const focus = focusedName(el);
  // Keyed without what is typed: the names' checks are drawn by `drawNewRigChecks`.
  const key = JSON.stringify([nr.channels.map((c) => [c.input, c.width, c.from]), view.choices, view.channels.map((c) => c.widths),
    sources, view.countText, nr.addRefused, nr.result, nr.pending, view.blocked, !!state.pending]);
  const rebuilt = patch(el, key, () => [
    h("form", { class: "new-rig-form", onsubmit: (event) => { event.preventDefault(); createRig(); } },
      view.choices.problem ? h("p", { class: "rig-problem", title: view.choices.problem }, R.shortSaid(view.choices.problem)) : null,
      textField("Rig name", "new-rig-name", nr.name, (v) => { nr.name = v; drawNewRigChecks(el); }, "e.g. two-guitars"),
      h("p", { class: "save-check", "data-check": "new-rig-name" }),
      h("div", { class: "draft-head" }, h("h2", {}, "CHANNELS"), h("span", { class: "sub" }, view.countText)),
      nr.channels.map((c, i) => channelDraft(el, c, i, view, sources)),
      h("div", { class: "save-acts" },
        h("button", { type: "button", class: "save-act add-channel", onclick: addDraftChannel }, "Add channel"),
        h("button", { type: "submit", class: "save-act primary create" }, view.create.label)),
      nr.addRefused ? h("p", { class: "save-check bad add-refused", role: "alert", title: nr.addRefused }, R.shortSaid(nr.addRefused)) : null,
      view.blocked ? h("p", { class: "sub", title: view.blocked }, R.shortSaid(view.blocked)) : null),
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
      sourcePicker(sources, c.from, (from) => {
        c.from = from; c.width = R.presetWidth(state.presets, from, c.width); touch(); render();
      }, `Channel ${i + 1} starts from`)),
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
    if (p) { p.textContent = R.shortSaid(text || ""); p.title = text || ""; p.classList.toggle("bad", !!bad); }
  };
  set("new-rig-name", nr.name ? view.nameProblem : "", !!(nr.name && view.nameProblem));
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
// for the master (its volume is the header's), and Add channel at the
// bottom. A strip opens its channel's tab in the middle column, the
// master's opens Master, and the open tab's strip is marked (#135); the
// strips stay together whatever tab is open, because they are the mix. An
// input edit writes the file;
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
    h("h2", { title: view.file || null }, "CHANNELS"),
    view.blocked ? h("p", { class: "sub inputs-why", title: view.blocked }, R.shortSaid(view.blocked)) : null,
    view.problem ? h("p", { class: "rig-problem", title: view.problem }, R.shortSaid(view.problem)) : null,
    ready && choices.problem ? h("p", { class: "rig-problem", title: choices.problem }, R.shortSaid(choices.problem)) : null,
  ]);

  // Keyed by the channels alone: the master's plugin count is drawn in place
  // below, so a plugin added to the master chain from another page does not
  // rebuild every strip (review, 2026-11-07). A rebuild that must happen (a
  // channel come or gone) gives the focus, and any text typed, back.
  const typing = typedIn(parts.list);
  const listRebuilt = patch(parts.list, JSON.stringify([live.map((t) => [t.name, t.send.name, t.send.panel]), extra.map((c) => c.name),
    !!master]), () => {
    state.stripUpdaters = [];
    parts.slots = new Map();
    parts.pedals = new Map();
    // A strip that plays opens its tab (#135): its name is the button, and
    // so is a tap on the strip that lands on nothing else (not its fader,
    // its value or its input, which are their own gestures). One the file
    // has gained since the rig came up has no chain to open yet.
    const opens = (el, name) => {
      el.addEventListener("click", (event) => {
        if (!event.target.closest("button, a, input, select, label, form, .volume, .pedal-panel")) chooseTab(name, { reveal: true });
      });
      return el;
    };
    const opener = (name) => h("button", { class: "strip-name", type: "button", "aria-controls": "rack-panel",
      title: `Show ${name}'s chain`, onclick: () => chooseTab(name, { reveal: true }) }, name);
    const strip = (name, volume, plays = true) => {
      const slot = h("div", { class: "strip-input-slot" });
      parts.slots.set(name, slot);
      // A channel's pedal (#192): its on/off and its volume, beside the volume.
      const pedal = plays ? h("span", { class: "pedal-slot" }) : null;
      const panel = plays ? h("div", { class: "pedal-panel-slot" }) : null;
      if (plays) parts.pedals.set(name, [pedal, panel]);
      const el = h("section", { class: "strip", "data-channel": name, "aria-label": `${name} channel` },
        plays ? h("div", { class: "strip-top" }, opener(name), pedal) : h("span", { class: "strip-name" }, name),
        slot, volume, panel);
      return plays ? opens(el, name) : el;
    };
    return [
      live.map((t) => strip(t.name, channelVolume(t, "strip", state.stripUpdaters))),
      extra.map((c) => strip(c.name, h("p", { class: "sub", title: "Added to the file: restart the rig to play it, and its volume comes with it." }, "Needs restart"), false)),
      master ? opens(h("section", { class: "strip strip-master", "data-channel": R.MASTER, "aria-label": "master" },
        opener(R.MASTER),
        h("p", { class: "sub master-says" })), R.MASTER) : null,
    ];
  });
  if (listRebuilt) giveBack(parts.list, typing);
  for (const [name, [pedal, panel]] of parts.pedals || []) pedalSlots(pedal, panel, stripPedalTarget(name));
  // The open tab's strip is marked, in place: choosing a tab rebuilds no strip.
  const openTab = state.rackParts ? state.rackParts.open : null;
  for (const el of parts.list.querySelectorAll(".strip")) {
    const here = el.dataset.channel === openTab;
    el.classList.toggle("open", here);
    const button = el.querySelector("button.strip-name");
    if (button && here) button.setAttribute("aria-current", "true");
    else if (button) button.removeAttribute("aria-current");
  }
  const says = parts.list.querySelector(".master-says");
  if (says && master) {
    says.textContent = `${master.units.length} plugin${master.units.length === 1 ? "" : "s"}`;
    says.title = "The mix. Its volume is the master's, in the header.";
  }

  // Each strip's input line, on its own key.
  for (const [name, slot] of parts.slots) {
    const c = ready ? filed.find((x) => x.name === name) || null : undefined;
    const editing = w.editing === name;
    const focus = focusedName(slot);
    const pu = state.presetUi;
    const presetsOpen = pu.channel === name && pu.rig === view.rig;
    const rebuilt = patch(slot, JSON.stringify([view.blocked ? "blocked" : c === undefined ? "reading" : c, editing,
      editing ? [w.change, choices] : null, !!w.pending, view.rig || null,
      presetsOpen ? [pu.choice, pu.pending, pu.result, state.presets, state.locked, !!state.pending] : null]), () => (
      view.blocked ? null
        : c === undefined ? h("p", { class: "sub strip-input" }, "Reading…")
          : c === null ? h("p", { class: "sub strip-input", title: "Not in the running rig's file." }, "Not in file")
            : [wiringRow(view, c, choices), presetsOpen ? presetPanel(view, c) : null]));
    if (presetsOpen) drawPresetCheck(slot, name);
    if (rebuilt) refocus(slot, focus);
  }

  if (view.blocked) { patch(parts.foot, "blocked", () => []); return; }
  const sources = w.adding && filesReady() ? R.sourceChoices(state.rigs, state.rigChannels, state.presets) : null;
  const busy = state.health && state.health.ok ? (state.health.body.agent || {}).busy || null : null;
  const focus = focusedName(parts.foot);
  const rebuilt = patch(parts.foot, JSON.stringify([ready, view.rig, view.channels.map((c) => c.name), choices,
    w.adding && [w.adding.input, w.adding.width, w.adding.from], sources, w.pending, w.result, w.restartConfirm, busy,
    !!state.pending, view.unsaved]), () => [
    !ready ? null
      : w.adding && !sources ? h("p", { class: "sub" }, "Reading…")
        : w.adding ? addChannelForm(parts.foot, view, choices, sources)
          : h("button", { class: "save-act add-channel", disabled: !!w.pending,
            onclick: () => {
              w.adding = R.newChannel(choices, view.channels); w.result = null; render();
              inBackground("rig channels", refreshAllRigChannels);
              inBackground("presets", () => refreshPresets(true));
            } }, "Add channel"),
    w.result ? rigFileResult(w.result, () => { w.result = null; render(); }, () => { w.restartConfirm = true; render(); }) : null,
    w.restartConfirm ? confirmBox("Restart?", restartForEdit, () => { w.restartConfirm = false; render(); },
      { pending: !!state.pending || !!busy }) : null,
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
    const pu = state.presetUi;
    const open = pu.channel === c.name && pu.rig === view.rig;
    row.append(h("button", { class: "rig-act small presets-toggle", "aria-expanded": open ? "true" : "false",
      "aria-label": `Presets for ${c.name}`,
      onclick: () => {
        Object.assign(state.presetUi, open ? { channel: null } : { channel: c.name, rig: view.rig, name: "", choice: "", result: null });
        render();
        if (!open) inBackground("presets", () => refreshPresets(true));
      } }, "Preset…"));
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
        onclick: () => changeInput(view.rig, c.name, change) }, "Apply"),
      h("button", { class: "save-act", onclick: () => { w.editing = null; render(); } }, "Cancel")));
  return row;
}

// A strip's Preset… panel (#127): Save the channel as a preset, by name,
// and Load a preset onto it. A save writes config/channels/<name>.yaml and
// changes nothing of the rig, so it is allowed while locked, as Save is; a
// load writes the rig's file and plays it (live where it can be), so it is
// behind the lock, as a structural edit is. What each said is shown in the
// panel, with the second tap a refusal offers: replace a preset of that
// name, drop unsaved changes, drop MIDI mappings the load would orphan.
function presetPanel(view, c) {
  const pu = state.presetUi;
  const choices = R.presetChoices(state.presets);
  const locked = R.lockRefusal(state.locked, "load_preset");
  const loading = !state.presets;
  const chosen = choices.find((p) => p.name === pu.choice) || null;
  const select = h("select", { "aria-label": `Preset to load onto ${c.name}`, disabled: loading || !choices.length },
    h("option", { value: "" }, loading ? "Reading the presets…" : choices.length ? "Choose a preset…" : "No presets yet"),
    choices.map((p) => h("option", { value: p.name, disabled: !p.ok, title: p.problem }, p.label)));
  select.value = pu.choice;
  select.addEventListener("change", () => { pu.choice = select.value; pu.result = null; render(); });
  const result = pu.result;
  return h("div", { class: "preset-panel", role: "group", "aria-label": `Presets for ${c.name}` },
    h("form", { class: "preset-save", onsubmit: (event) => { event.preventDefault(); savePreset(c.name, false); } },
      textField(`Save ${c.name} as`, `preset-name-${c.name}`, pu.name, (v) => { pu.name = v; drawPresetCheck(document, c.name); }, "e.g. crunch"),
      h("p", { class: "save-check", "data-check": `preset-name-${c.name}` }),
      h("button", { type: "submit", class: "save-act preset-save-go" }, "Save")),
    h("div", { class: "preset-load" },
      h("label", { class: "selector" }, h("span", { class: "label" }, `Load onto ${c.name}`), select),
      h("button", { type: "button", class: "save-act primary preset-load-go", title: locked,
        disabled: !!pu.pending || !!state.pending || !!locked || !chosen || !chosen.ok,
        onclick: () => loadPreset(c.name, pu.choice, {}) },
      pu.pending === "load" ? "Loading…" : chosen ? `Load ${chosen.name} onto ${c.name}` : "Load"),
      locked ? h("p", { class: "sub" }, locked) : null),
    result ? h("div", { class: `save-result preset-result ${result.ok ? "ok" : "refused"}`, role: result.ok ? "status" : "alert" },
      h("strong", {}, result.title),
      h("span", { class: "said" }, result.ok ? result.text : result.reason),
      result.notes.length ? h("ul", { class: "preset-notes" }, result.notes.map((n) => h("li", {}, n))) : null,
      result.offer ? h("button", { class: "rig-act offer", disabled: !!pu.pending || (!!locked && !result.offer.replace),
        onclick: () => (result.offer.replace ? savePreset(c.name, true, result.name)
          : loadPreset(c.name, result.name, result.offer)) }, result.offer.label) : null,
      h("button", { class: "dismiss", "aria-label": "Dismiss", onclick: () => { pu.result = null; render(); } }, "×")) : null,
    h("button", { type: "button", class: "save-act preset-close", onclick: () => { pu.channel = null; render(); } }, "Close"),
    helpAnchor("presets"));
}

// The name's hint and Save's state, drawn in place from what is typed.
function drawPresetCheck(el, channel) {
  const pu = state.presetUi;
  const p = el.querySelector(`[data-check="preset-name-${CSS.escape(channel)}"]`);
  const button = el.querySelector(".preset-save-go");
  if (!p || !button) return;
  const check = pu.name ? R.checkPresetName(pu.name, state.presets) : { problem: null, taken: false };
  p.textContent = check.problem || (check.taken ? `${pu.name} is a preset already: Save offers to replace it.` : pu.name ? `Writes config/channels/${pu.name}.yaml.` : "");
  p.classList.toggle("bad", !!check.problem);
  button.disabled = !!pu.pending || !pu.name || !!check.problem;
  button.textContent = pu.pending === "save" ? "Saving…" : pu.name && !check.problem ? `Save ${channel} as ${pu.name}` : "Save";
}

async function savePreset(channel, replace, name = null) {
  const pu = state.presetUi;
  const preset = name || pu.name;
  if (pu.pending || !preset || R.checkPresetName(preset, state.presets).problem) return;
  pu.pending = "save";
  render();
  let answer;
  try {
    answer = await request("POST", R.presetSaveCall(channel, preset, replace).path, R.presetSaveCall(channel, preset, replace).json);
  } finally {
    pu.pending = null;
  }
  pu.result = { ...R.presetOutcome("save", channel, preset, answer), name: preset };
  if (answer.ok) pu.name = "";
  render();
  if (answer.ok) refreshPresets(true).then(render);
}

async function loadPreset(channel, preset, asked) {
  const pu = state.presetUi;
  if (pu.pending || !preset || R.lockRefusal(state.locked, "load_preset")) return;
  pu.pending = "load";
  render();
  const call = R.presetLoadCall(channel, preset, asked);
  let answer;
  try {
    answer = await request("POST", call.path, call.json);
  } finally {
    pu.pending = null;
  }
  pu.result = { ...R.presetOutcome("load", channel, preset, answer, asked), name: preset };
  render();
  if (answer.ok || (answer.body || {}).written) {
    const rig = state.health && state.health.ok ? state.health.body.rig || {} : {};
    if (rig.config_name) refreshRigChannels(rig.config_name, true).then(render);
    kick();
  }
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
      sourcePicker(sources, draft.from, (from) => {
        draft.from = from; draft.width = R.presetWidth(state.presets, from, draft.width); render();
      }, "New channel starts from")),
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
      state.rackParts = null;
      state.unitUpdaters = new Map();
      state.masterUpdaters = [];
      const built = screen === "stage" ? stageFrame() : screen === "newrig" ? newRigFrame() : rackFrame();
      app.replaceChildren(built.frame);
      state.regions = built.regions;
      if (screen === "newrig") {
        inBackground("roles", refreshRoles);
        inBackground("rig channels", () => refreshAllRigChannels(true));
        inBackground("presets", () => refreshPresets(true));
      }
    }
    settleEdits();
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
    if (live) {
      const units = [...state.unitUpdaters.values()].flat();
      for (const update of [...state.updaters, ...units, ...state.ampUpdaters, ...state.levelUpdaters, ...state.stripUpdaters]) update(ctx);
    }
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
window.sushiRigUpdaters = () => ({ rack: state.updaters.length + [...state.unitUpdaters.values()].flat().length, amp: state.ampUpdaters.length,
  levels: state.levelUpdaters.length, strips: state.stripUpdaters.length, master: state.masterUpdaters.length });

// Only a change of screen closes what was open.
window.addEventListener("hashchange", () => {
  if (currentScreen() !== state.screen) { state.open.clear(); state.peeked.clear(); state.drawer = null; }
  render();
});
setInterval(tickElapsed, TICK_MS);
// The profiles and the plugins' names from the start, beside the first poll:
// the first chain waits for the profiles (`profilesDue`).
inBackground("profiles", refreshProfiles);
inBackground("names", refreshNames);
poll().then(pollLoad);
