// The visualiser's drawing and listening (#176), loaded by screensaver.js
// the first time the visualiser starts and never before, with butterchurn
// (vendor/butterchurn-2.6.7/), so the page pays for neither until then.
// What to listen to and when to draw are rig.js's decisions.
//
// The sound is the browser's own capture of an input, through PipeWire's
// PulseAudio server (pipewire-pulse): the guitar's input, beside Sushi,
// adding nothing to the signal's path. What that does and does not keep apart from
// Sushi: the browser reads from a buffer the server keeps, so a browser that
// stalls only loses its own samples. But the server's capture stream is a
// node in the graph Sushi's driver runs, doing a small copy every cycle, so
// if pipewire-pulse is late (no realtime priority, or preempted) the cycle
// can be late too: `sushi-rig doctor` says whether it runs realtime, and
// screensaver.js stops the capture if the rig's xruns rise while it listens.
// Nothing is played: the audio goes to butterchurn's analysers and the
// equaliser looks' (visualiser-eq.js), and where the browser can (a
// Chromium) the context has no output device at all.
//
// The looks (#196): butterchurn's (visualiser-presets.js) and the page's own
// equaliser looks, in a random order unless one is held (rig.js lookPool,
// nextLook, lookSeconds, lookVariation). One session draws whichever is
// showing, each on a canvas of its own, so screensaver.js never needs to
// know which kind it is: start, stop, resize and a lost context are the same
// for both.

import * as R from "./rig.js";
import { LOOKS as MILKDROP, BLEND_SECONDS, resetFlashGate } from "./visualiser-presets.js";
import { EQ_LOOKS, createEq } from "./visualiser-eq.js";

export const BUTTERCHURN = "/ui/vendor/butterchurn-2.6.7/butterchurn.min.js";

// The capture as it is, with nothing the browser would do for a call.
const RAW = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };

let loading = null;

// butterchurn's bundle, as a classic script (it is UMD, and sets
// `window.butterchurn`), once.
function butterchurn() {
  if (!loading) {
    loading = new Promise((done, fail) => {
      const script = document.createElement("script");
      script.src = BUTTERCHURN;
      script.async = true;
      script.onload = () => {
        const B = window.butterchurn && (window.butterchurn.default || window.butterchurn);
        if (B && typeof B.createVisualizer === "function") done(B);
        else fail(new Error("butterchurn loaded but offers no createVisualizer"));
      };
      script.onerror = () => { loading = null; fail(new Error(`${BUTTERCHURN} could not be loaded`)); };
      document.head.append(script);
    });
  }
  return loading;
}

// An AudioContext that plays nowhere where the browser allows it (Chromium's
// `sinkId: {type: "none"}`): otherwise one more output stream would join the
// graph for nothing. Firefox does not know the option and opens its usual one.
function silentContext() {
  try {
    return new AudioContext({ sinkId: { type: "none" }, latencyHint: "playback" });
  } catch {
    return new AudioContext({ latencyHint: "playback" });
  }
}

async function audioInputs() {
  const all = await navigator.mediaDevices.enumerateDevices();
  return all.filter((d) => d.kind === "audioinput").map((d) => ({ deviceId: d.deviceId, label: d.label }));
}

// The stream for `pick`, or the browser's default when it names none.
function capture(pick) {
  const audio = { ...RAW, channelCount: { ideal: 2 } };
  if (pick && pick.deviceId && pick.deviceId !== "default") audio.deviceId = { exact: pick.deviceId };
  return navigator.mediaDevices.getUserMedia({ audio });
}

function stopStream(stream) {
  if (stream) for (const track of stream.getTracks()) track.stop();
}

// Choose the input and open it: `{stream, pick}`. With no permission yet,
// the default is opened first (that is when the browser asks), and then,
// once the inputs have names, the one `R.pickInput` chooses, if another.
export async function listen(settings, guitar) {
  let stream = null;
  let devices = await audioInputs();
  if (!devices.some((d) => d.label)) {
    stream = await capture(null);
    devices = await audioInputs();
  }
  const pick = R.pickInput(devices, settings, guitar);
  const opened = stream && stream.getAudioTracks()[0] ? stream.getAudioTracks()[0].getSettings().deviceId : null;
  if (!stream || (pick.deviceId && pick.deviceId !== "default" && pick.deviceId !== opened)) {
    stopStream(stream);
    stream = await capture(pick);
  }
  return { stream, pick, devices };
}

export { audioInputs };

// Give back `canvas`'s WebGL context now (WEBGL_lose_context): asked of a
// canvas that has one, getContext returns it and makes none.
function loseWebGL(canvas) {
  try {
    const gl = canvas.getContext("webgl2");
    const lose = gl && !gl.isContextLost() && gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();
  } catch { /* nothing to give back */ }
}

// One visualiser for the page's life, drawing on `canvas` (butterchurn's,
// WebGL 2) and on a 2D canvas of its own put beside it (the equaliser
// looks'): its contexts and its AudioContext are made once and kept (made
// again each time, butterchurn's textures would pile up over an evening),
// and only the capture is opened and closed with each showing. `onLost(kind)`
// is called if the browser takes either canvas's context away ("WebGL" or
// "canvas"); the session is then of no more use, and is disposed. A
// visualiser that cannot be made at all closes its AudioContext and throws
// an error marked `hard`: trying again will not help. `options.seed` seeds
// the order and the variations (rig.js seededRandom); otherwise a random one.
export async function create(canvas, onLost = () => {}, options = {}) {
  const B = await butterchurn();
  // One flash limit for the whole visualiser, whichever looks are blending
  // (visualiser-presets.js says why), afresh with each session's clock.
  resetFlashGate();
  const context = silentContext();
  const size = R.drawSize(window.innerWidth, window.innerHeight);
  canvas.width = size.width;
  canvas.height = size.height;
  const flat = document.createElement("canvas");
  flat.className = "visualiser-flat off";
  const half = R.eqDrawSize(size.width, size.height);
  flat.width = half.width;
  flat.height = half.height;
  canvas.after(flat);
  let viz, eq;
  try {
    viz = B.createVisualizer(context, canvas, {
      width: size.width, height: size.height, pixelRatio: 1, textureRatio: 1, meshWidth: 32, meshHeight: 24,
    });
    eq = createEq(flat, context);
  } catch (error) {
    flat.remove();
    if (viz) loseWebGL(canvas);
    try { await context.close(); } catch { /* closed already */ }
    const hard = new Error(`${viz ? "the equaliser looks" : "butterchurn"} could not draw here: ${error && error.message || error}`);
    hard.name = "VisualiserError";
    hard.hard = true;
    throw hard;
  }
  const kinds = new Map(R.VISUALISER_LOOKS.map((l) => [l.name, l.kind]));
  const presets = new Map(MILKDROP.map((p) => [p.name, p]));
  const seed = Number.isFinite(options.seed) ? options.seed : crypto.getRandomValues(new Uint32Array(1))[0];
  const rng = R.seededRandom(seed);
  let choice = "random";
  // The look showing: {name, kind, seconds (null while held), at (when it
  // came on, the frame clock's ms), variation}; and those shown, for stats.
  let look = null;
  const history = [];
  let source = null, mixer = null, splitter = null, stream = null;
  let raf = null, last = null, frames = 0;
  // How long the equaliser looks' last frames took to draw (ms), for R.eqFps;
  // begun again with each look.
  const eqTimes = [];
  // Moved on by every stop: a run whose resume finishes after a stop does
  // not start drawing (found in review: it drew on, unseen, at 30 fps).
  let runs = 0;
  let calls = 0;  // run()s asked for, the latest of which owns the context's state
  let lost = false, disposed = false;

  // Put `name` on, varied, from the frame at `now` (null: the next frame's).
  function show(name, now) {
    const kind = kinds.get(name);
    const bounds = kind === "eq" ? EQ_LOOKS[name].vary : presets.get(name).vary;
    const variation = R.lookVariation(bounds, rng);
    const pool = R.lookPool(choice);
    const seconds = pool.length > 1 ? R.lookSeconds(rng) : null;
    if (kind === "milkdrop") viz.loadPreset(presets.get(name).make(variation), look && look.kind === "milkdrop" ? BLEND_SECONDS : 0);
    else eq.show(name, variation);
    eqTimes.length = 0;
    canvas.classList.toggle("off", kind !== "milkdrop");
    flat.classList.toggle("off", kind !== "eq");
    look = { name, kind, seconds, at: now, variation };
    history.push({ name, seconds, variation });
    if (history.length > 50) history.shift();
  }

  function next(now) {
    show(R.nextLook(R.lookPool(choice), look && look.name, rng), now);
  }

  function frame(now) {
    raf = requestAnimationFrame(frame);
    if (!R.frameDue(last, now, look && look.kind === "eq" ? R.eqFps(eqTimes) : R.VISUALISER.fps)) return;
    last = now;
    if (!look) next(now);
    else if (look.at == null) look.at = now;
    else if (look.seconds != null && now - look.at >= look.seconds * 1000) next(now);
    if (look.kind === "milkdrop") viz.render();
    else {
      const began = performance.now();
      eq.render(now);
      eqTimes.push(performance.now() - began);
      if (eqTimes.length > 31) eqTimes.shift();
    }
    frames += 1;
  }

  function detach() {
    for (const node of [source, splitter, mixer]) { try { if (node) node.disconnect(); } catch { /* already gone */ } }
    source = splitter = mixer = null;
    stopStream(stream);
    stream = null;
  }

  async function stop() {
    runs += 1;
    if (raf != null) cancelAnimationFrame(raf);
    raf = null;
    detach();
    if (context.state === "running") await context.suspend();
  }

  // Done with: the input let go, the AudioContext closed, the 2D canvas
  // gone, and butterchurn's WebGL context given back at once unless it is
  // the one that was lost (else it waits for the garbage collector, and a
  // browser holds only so many: PR #201's review).
  async function dispose() {
    await stop();
    disposed = true;
    flat.remove();
    if (lost !== "WebGL") loseWebGL(canvas);
    try { await context.close(); } catch { /* closed already */ }
  }

  function loseTo(kind) {
    return (event) => {
      event.preventDefault();
      if (lost || disposed) return;
      lost = kind;
      dispose().finally(() => onLost(kind));
    };
  }
  canvas.addEventListener("webglcontextlost", loseTo("WebGL"));
  flat.addEventListener("contextlost", loseTo("canvas"));

  return {
    // Hear `opened` (listen's answer): its channel, or both mixed to one.
    attach(opened) {
      detach();
      if (disposed) { stopStream(opened.stream); return; }
      stream = opened.stream;
      source = context.createMediaStreamSource(stream);
      mixer = context.createGain();
      mixer.channelCount = 1;
      mixer.channelCountMode = "explicit";
      mixer.channelInterpretation = "speakers";
      if (opened.pick.channel === 0 || opened.pick.channel === 1) {
        splitter = context.createChannelSplitter(2);
        source.connect(splitter);
        splitter.connect(mixer, opened.pick.channel);
      } else {
        source.connect(mixer);
      }
      viz.connectAudio(mixer);
      eq.listen(mixer);
    },
    // What to show: "random", "random-mono", "random-eq" or a look's name,
    // which holds it (rig.js lookChoices). A look that is not in the choice
    // goes at the next frame; one that is stays, timed afresh if it was held.
    choose(value) {
      if (disposed) return;
      choice = R.lookChoice(value);
      const pool = R.lookPool(choice);
      if (!look) return;
      if (!pool.includes(look.name)) look = null;
      else if (pool.length > 1 && look.seconds == null) { look.seconds = R.lookSeconds(rng); look.at = null; }
      else if (pool.length === 1) look.seconds = null;
    },
    resize(width, height) {
      if (disposed) return;
      const s = R.drawSize(width, height);
      canvas.width = s.width;
      canvas.height = s.height;
      const half = R.eqDrawSize(s.width, s.height);
      flat.width = half.width;
      flat.height = half.height;
      viz.setRendererSize(s.width, s.height);
      eq.resize(s.width, s.height);
    },
    async run() {
      if (disposed) return;
      const mine = runs;
      const call = ++calls;
      if (context.state !== "running") await context.resume();
      if (mine !== runs || disposed) {
        // Stopped while it resumed: the stop's suspend came first, so again,
        // unless a later run is under way and wants it running.
        if (!disposed && call === calls && raf == null && context.state === "running") await context.suspend();
        return;
      }
      if (raf == null) {
        last = null;
        // Shown again: the look on comes back for its time afresh, from the
        // next frame (its time ran on while it was put away, so it used to
        // go on the first frame: PR #201's review).
        if (look && look.seconds != null) look.at = null;
        raf = requestAnimationFrame(frame);
      }
    },
    // Stopped and the input let go: the browser's capture leaves the graph.
    // A hidden tab is the same: nothing is heard while nothing is drawn.
    stop,
    dispose,
    stats() {
      return { frames, drawing: raf != null, context: context.state, look: look ? look.name : null,
        kind: look ? look.kind : null, seconds: look ? look.seconds : null, at: look ? look.at : null,
        variation: look ? look.variation : null, choice, seed, history: history.map((h) => ({ ...h })),
        eq: { ...eq.stats(), width: flat.width, height: flat.height, fps: R.eqFps(eqTimes) },
        width: canvas.width, height: canvas.height, listening: !!stream, lost, disposed };
    },
  };
}
