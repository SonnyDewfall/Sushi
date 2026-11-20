// The visualiser's drawing and listening (#176), loaded by screensaver.js
// the first time the visualiser starts and never before, with butterchurn
// (vendor/butterchurn-2.6.7/), so the page pays for neither until then.
// What to listen to and when to draw are rig.js's decisions.
//
// The sound is the browser's own capture of an input, through PipeWire's
// PulseAudio server (pipewire-pulse): the rig's output ("Sushi rig output",
// the agent's tap in vistap.py, in Sushi's cycle while it listens) or the guitar's input, beside Sushi,
// adding nothing to the signal's path. What that does and does not keep apart from
// Sushi: the browser reads from a buffer the server keeps, so a browser that
// stalls only loses its own samples. But the server's capture stream is a
// node in the graph Sushi's driver runs, doing a small copy every cycle, so
// if pipewire-pulse is late (no realtime priority, or preempted) the cycle
// can be late too: `sushi-rig doctor` says whether it runs realtime, and
// screensaver.js stops the capture if the rig's xruns rise while it listens.
// Nothing is played: the audio goes to butterchurn's analysers, and where
// the browser can (a Chromium) the context has no output device at all.

import * as R from "./rig.js";
import { PRESETS, PRESET_SECONDS, BLEND_SECONDS } from "./visualiser-presets.js";

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
// once the inputs have names, the one `R.pickInput` chooses, if another:
// the rig's output (`output`) before the guitar's input.
export async function listen(settings, guitar, output = null) {
  let stream = null;
  let devices = await audioInputs();
  if (!devices.some((d) => d.label)) {
    stream = await capture(null);
    devices = await audioInputs();
  }
  const pick = R.pickInput(devices, settings, guitar, output);
  const opened = stream && stream.getAudioTracks()[0] ? stream.getAudioTracks()[0].getSettings().deviceId : null;
  if (!stream || (pick.deviceId && pick.deviceId !== "default" && pick.deviceId !== opened)) {
    stopStream(stream);
    stream = await capture(pick);
  }
  return { stream, pick, devices };
}

export { audioInputs };

// One visualiser for the page's life, drawing on `canvas`: its WebGL
// context and its AudioContext are made once and kept (made again each time,
// butterchurn's textures would pile up over an evening), and only the
// capture is opened and closed with each showing. `onLost` is called if the
// browser takes the WebGL context away; the session is then of no more use,
// and is disposed. A visualiser that cannot be made at all closes its
// AudioContext and throws an error marked `hard`: trying again will not help.
export async function create(canvas, onLost = () => {}) {
  const B = await butterchurn();
  const context = silentContext();
  const size = R.drawSize(window.innerWidth, window.innerHeight);
  canvas.width = size.width;
  canvas.height = size.height;
  let viz;
  try {
    viz = B.createVisualizer(context, canvas, {
      width: size.width, height: size.height, pixelRatio: 1, textureRatio: 1, meshWidth: 32, meshHeight: 24,
    });
  } catch (error) {
    try { await context.close(); } catch { /* closed already */ }
    const hard = new Error(`butterchurn could not draw here: ${error && error.message || error}`);
    hard.name = "VisualiserError";
    hard.hard = true;
    throw hard;
  }
  let preset = 0;
  viz.loadPreset(PRESETS[preset], 0);
  let presetAt = performance.now();
  let source = null, mixer = null, splitter = null, stream = null;
  let raf = null, last = null, frames = 0;
  // Moved on by every stop: a run whose resume finishes after a stop does
  // not start drawing (found in review: it drew on, unseen, at 30 fps).
  let runs = 0;
  let calls = 0;  // run()s asked for, the latest of which owns the context's state
  let lost = false, disposed = false;

  function frame(now) {
    raf = requestAnimationFrame(frame);
    if (!R.frameDue(last, now)) return;
    last = now;
    if (now - presetAt > PRESET_SECONDS * 1000) {
      preset = (preset + 1) % PRESETS.length;
      viz.loadPreset(PRESETS[preset], BLEND_SECONDS);
      presetAt = now;
    }
    viz.render();
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

  async function dispose() {
    await stop();
    disposed = true;
    try { await context.close(); } catch { /* closed already */ }
  }

  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    lost = true;
    dispose().finally(() => onLost());
  });

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
    },
    resize(width, height) {
      if (disposed) return;
      const s = R.drawSize(width, height);
      canvas.width = s.width;
      canvas.height = s.height;
      viz.setRendererSize(s.width, s.height);
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
      if (raf == null) { last = null; raf = requestAnimationFrame(frame); }
    },
    // Stopped and the input let go: the browser's capture leaves the graph.
    // A hidden tab is the same: nothing is heard while nothing is drawn.
    stop,
    dispose,
    stats() {
      return { frames, drawing: raf != null, context: context.state, preset: PRESETS[preset].name,
        width: canvas.width, height: canvas.height, listening: !!stream, lost, disposed };
    },
  };
}
