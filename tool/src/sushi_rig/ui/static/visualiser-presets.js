// The visualiser's presets (#176), written for the rig, in butterchurn's
// preset form: MilkDrop's per-frame and per-pixel equations as JavaScript
// over `a`, the frame's variables (a.time, a.bass, a.mid, a.treb and their
// slower `_att` forms, each near 1 at an average level and above it on a
// loud note). No GLSL: butterchurn's own warp and composite shaders draw
// them. Not taken from any preset pack (vendor/butterchurn-2.6.7/SOURCE.txt
// says why there is none).
//
// Butterchurn draws four custom shapes and four custom waves whatever a
// preset says, so each preset lists four of each, the unused off.

const shapeOff = () => ({ baseVals: { enabled: 0 }, init_eqs_str: "", frame_eqs_str: "" });
const waveOff = () => ({ baseVals: { enabled: 0 }, init_eqs_str: "", frame_eqs_str: "", point_eqs_str: "" });
const four = (made, off) => [...made, ...Array.from({ length: 4 - made.length }, off)];

// A ring of the waveform, warm, drawn over its own fading trail; a loud
// note pushes the trail outwards and treble roughens it.
const embers = {
  name: "Embers",
  baseVals: {
    decay: 0.965, gammaadj: 1.7, echo_zoom: 1, echo_alpha: 0, wave_mode: 0, additivewave: 1,
    wave_a: 0.9, wave_scale: 1.2, wave_smoothing: 0.7, modwavealphabyvolume: 1,
    modwavealphastart: 0.5, modwavealphaend: 1.3, zoom: 1, rot: 0, warp: 0.25, mv_a: 0,
    ob_size: 0, ib_size: 0, darken_center: 1,
  },
  init_eqs_str: "",
  frame_eqs_str: [
    "a.wave_r = 0.85 + 0.15*Math.sin(a.time*0.37);",
    "a.wave_g = 0.45 + 0.25*Math.sin(a.time*0.23 + 1.0);",
    "a.wave_b = 0.15 + 0.15*Math.sin(a.time*0.51 + 2.0);",
    "a.zoom = 1.0 + 0.045*Math.min(a.bass_att, 3);",
    "a.rot = 0.008*Math.sin(a.time*0.21) + 0.012*Math.min(a.mid_att - 1, 2);",
    "a.warp = 0.15 + 0.35*Math.min(a.treb_att, 2.5);",
  ].join(" "),
  pixel_eqs_str: "a.zoom = a.zoom + 0.02*a.rad*Math.sin(a.time*0.4 + a.ang*3);",
  warp: "", comp: "",
  shapes: four([], shapeOff),
  waves: four([], waveOff),
};

// A slow tide drifting up the screen, the waveform a line across it, with
// the spectrum as a second line under it: low notes swell the tide.
const tide = {
  name: "Tide",
  baseVals: {
    decay: 0.955, gammaadj: 1.5, echo_zoom: 1, echo_alpha: 0, wave_mode: 6, additivewave: 1,
    wave_a: 0.8, wave_scale: 0.9, wave_smoothing: 0.6, wave_y: 0.55, zoom: 0.995, dy: -0.004,
    warp: 0.6, warpscale: 2, warpanimspeed: 0.6, mv_a: 0, ob_size: 0, ib_size: 0,
  },
  init_eqs_str: "",
  frame_eqs_str: [
    "a.wave_r = 0.25 + 0.2*Math.sin(a.time*0.17);",
    "a.wave_g = 0.6 + 0.3*Math.sin(a.time*0.29 + 2.0);",
    "a.wave_b = 0.9;",
    "a.dy = -0.004 - 0.006*Math.min(a.bass_att, 3);",
    "a.warp = 0.4 + 0.4*Math.min(a.mid_att, 2);",
  ].join(" "),
  pixel_eqs_str: "a.dx = 0.003*Math.sin(a.y*6 + a.time*0.7);",
  warp: "", comp: "",
  shapes: four([], shapeOff),
  waves: four([{
    baseVals: { enabled: 1, samples: 256, spectrum: 1, scaling: 1.6, smoothing: 0.5, additive: 1, thick: 1, r: 1, g: 0.8, b: 0.5, a: 0.7 },
    init_eqs_str: "",
    frame_eqs_str: "",
    point_eqs_str: "a.x = a.sample; a.y = 0.2 + 0.25*a.value1; a.a = 0.35 + 0.5*a.sample;",
  }], waveOff),
};

// A soft disc in the middle that blooms with each note, its outline turning
// with the music, over a trail that spins out slowly.
const bloom = {
  name: "Bloom",
  baseVals: {
    decay: 0.94, gammaadj: 1.4, echo_zoom: 1, echo_alpha: 0, wave_mode: 2, additivewave: 1,
    wave_a: 0.5, wave_scale: 0.8, zoom: 1.01, rot: 0.004, warp: 0.1, mv_a: 0, ob_size: 0, ib_size: 0,
  },
  init_eqs_str: "",
  frame_eqs_str: [
    "a.wave_r = 0.6 + 0.4*Math.sin(a.time*0.31 + 4.0);",
    "a.wave_g = 0.4 + 0.2*Math.sin(a.time*0.19);",
    "a.wave_b = 0.8;",
    "a.rot = 0.004 + 0.01*Math.min(a.treb_att - 1, 2);",
    "a.zoom = 1.01 + 0.02*Math.min(a.bass, 3);",
  ].join(" "),
  pixel_eqs_str: "",
  warp: "", comp: "",
  shapes: four([{
    baseVals: { enabled: 1, sides: 48, additive: 1, thickoutline: 1, x: 0.5, y: 0.5, rad: 0.12,
      r: 0.95, g: 0.55, b: 0.35, a: 0.35, r2: 0.3, g2: 0.1, b2: 0.6, a2: 0, border_r: 1, border_g: 0.9, border_b: 0.7, border_a: 0.5 },
    init_eqs_str: "",
    frame_eqs_str: "a.rad = 0.08 + 0.09*Math.min(a.bass_att, 3); a.ang = a.time*0.2; a.border_a = 0.2 + 0.25*Math.min(a.mid, 3);",
  }], shapeOff),
  waves: four([], waveOff),
};

export const PRESETS = [embers, tide, bloom];
// How long each is shown, and how long one takes to blend into the next.
export const PRESET_SECONDS = 40;
export const BLEND_SECONDS = 4;
