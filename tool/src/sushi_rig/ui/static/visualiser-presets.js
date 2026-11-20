// The visualiser's butterchurn looks (#176, #196), written for the rig, in
// butterchurn's preset form: MilkDrop's per-frame and per-pixel equations as
// JavaScript over `a`, the frame's variables (a.time, a.bass, a.mid, a.treb
// and their slower `_att` forms, each near 1 at an average level and above
// it on a loud note), and for some a `warp` or `comp` shader, a GLSL body
// butterchurn 2.6.7 puts into its own shader (`shader_body { ... }`, setting
// `ret`). Not taken from any preset pack (vendor/butterchurn-2.6.7/SOURCE.txt
// says why there is none).
//
// Each look is `{name, vary, make(v)}`: `vary` the bounds of its colour and
// motion, `make` the preset for one variation within them (rig.js's
// lookVariation), so it is a little different each time it comes on.
//
// Butterchurn draws four custom shapes and four custom waves whatever a
// preset says, so each preset lists four of each, the unused off.
//
// Flashes. A look that flashes on a loud attack does it through `flash`
// below: never closer than VISUALISER.flashGap seconds nor flashFrames
// frames apart (never more than two in any second, under the 3 a second
// photosensitivity guidance), and each flash is gone within about four
// frames. The tests drive every look's equations with a loud attack on
// every frame and count.

import { VISUALISER } from "./rig.js";

const shapeOff = () => ({ baseVals: { enabled: 0 }, init_eqs_str: "", frame_eqs_str: "" });
const waveOff = () => ({ baseVals: { enabled: 0 }, init_eqs_str: "", frame_eqs_str: "", point_eqs_str: "" });
const four = (made, off) => [...made, ...Array.from({ length: 4 - made.length }, off)];

// A number as a GLSL float literal, which must have a decimal point.
const f = (x) => Number(x).toFixed(4);

// A colour of `hue` degrees, `sat` 0 (white) to 1, full value: [r, g, b].
function tint(hue, sat) {
  const h = (((hue % 360) + 360) % 360) / 60, c = sat, x = c * (1 - Math.abs((h % 2) - 1));
  const [r, g, b] = h < 1 ? [c, x, 0] : h < 2 ? [x, c, 0] : h < 3 ? [0, c, x] : h < 4 ? [0, x, c] : h < 5 ? [x, 0, c] : [c, 0, x];
  return [r + 1 - c, g + 1 - c, b + 1 - c];
}

// A hue rotation of `deg` degrees as a 3 by 3 matrix (rows), about the grey
// axis, so a colour keeps its brightness.
function hueMatrix(deg) {
  const c = Math.cos((deg * Math.PI) / 180), s = Math.sin((deg * Math.PI) / 180);
  return [
    [0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928],
    [0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140, 0.072 - c * 0.072 - s * 0.283],
    [0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072],
  ];
}
const turn = (m, [r, g, b]) => m.map((row) => Math.min(1, Math.max(0, row[0] * r + row[1] * g + row[2] * b)));

// The equations that turn the wave colour (a.wave_r, _g, _b) by a matrix.
function turnWave(m) {
  const n = (x) => x.toFixed(4);
  return ` var r_ = a.wave_r, g_ = a.wave_g, b_ = a.wave_b;`
    + ` a.wave_r = ${n(m[0][0])}*r_ + ${n(m[0][1])}*g_ + ${n(m[0][2])}*b_;`
    + ` a.wave_g = ${n(m[1][0])}*r_ + ${n(m[1][1])}*g_ + ${n(m[1][2])}*b_;`
    + ` a.wave_b = ${n(m[2][0])}*r_ + ${n(m[2][1])}*g_ + ${n(m[2][2])}*b_;`;
}

// A flash on a loud attack, as `a.flash` (1 on the frame it comes, then
// halving each frame) and q1 for the shapes and shaders. A loud attack: the
// level loud, and well over `a.slow`, its own average over about the last
// second (butterchurn's `_att` levels follow too closely: on plucked chords
// the level was never more than 1.2 times them, so nothing flashed). Its
// state is set in init, so butterchurn keeps it from frame to frame.
//
// When the last flash came is not a preset's but the page's: one gate,
// `globalThis.sushiRigFlashGate`, made afresh by visualiser.js for each
// session (resetFlashGate). During a blend butterchurn runs both presets'
// frame equations every frame and draws both, so a gate of each preset's own
// let two schedules add up: Shutter into Tunnel, strummed every 0.3 s,
// flashed at frames 63, 72, 81 and 90, four in a second (PR #201's review).
// Both runners see the same `a.time` and `a.frame` in a frame, so whichever
// flashes first shuts the other out. A clock that goes back (another
// butterchurn, or the tests running looks one after another) starts the
// gate again.
const FLASH_INIT = "a.flash = 0; a.slow = 1;";
export const FLASH_GATE = "sushiRigFlashGate";
export function resetFlashGate() {
  globalThis[FLASH_GATE] = { time: -1e9, frame: -1e9 };
}

// Where the screen's foot and head are for a custom wave's y. Butterchurn
// draws a custom wave's point at (-2y + 1) times the inverse aspect, so on a
// landscape screen only the middle of 0 to 1 is seen (1/6 to 5/6 at 3:2): a
// line at y = 0.1 was off the screen. Each preset that wants the foot sets
// q4 to a.aspecty in its frame equations (ASPECT); the point equations take
// FOOT and HEAD.
const ASPECT = "a.q4 = a.aspecty > 0 ? a.aspecty : 1;";
const FOOT = "var foot = 0.5 - 0.5/a.q4; var head = 0.5 + 0.5/a.q4;";
function flash(threshold = 1.35) {
  return [
    `var gate = globalThis.${FLASH_GATE} || (globalThis.${FLASH_GATE} = {time: -1e9, frame: -1e9});`,
    "if (a.time < gate.time || a.frame < gate.frame) { gate.time = -1e9; gate.frame = -1e9; }",
    "a.flash = a.flash*0.5;",
    "a.loud = (a.bass + a.mid + a.treb)/3;",
    `if (a.loud > ${threshold} && a.loud > 1.6*a.slow`,
    `  && a.time - gate.time >= ${VISUALISER.flashGap} && a.frame - gate.frame >= ${VISUALISER.flashFrames}) {`,
    "  a.flash = 1; gate.time = a.time; gate.frame = a.frame; }",
    "a.slow = 0.96*a.slow + 0.04*a.loud;",
    "a.q1 = a.flash;",
  ].join(" ");
}

// ---------------------------------------------------------------- the first three (#176)

// A ring of the waveform, warm, drawn over its own fading trail; a loud
// note pushes the trail outwards and treble roughens it.
const embers = {
  name: "Embers",
  vary: { hue: [-18, 18], speed: [0.85, 1.2] },
  make: (v) => ({
    baseVals: {
      decay: 0.965, gammaadj: 1.7, echo_zoom: 1, echo_alpha: 0, wave_mode: 0, additivewave: 1,
      wave_a: 0.9, wave_scale: 1.2, wave_smoothing: 0.7, modwavealphabyvolume: 1,
      modwavealphastart: 0.5, modwavealphaend: 1.3, zoom: 1, rot: 0, warp: 0.25, mv_a: 0,
      ob_size: 0, ib_size: 0, darken_center: 1,
    },
    init_eqs_str: "",
    frame_eqs_str: [
      `var t = a.time*${f(v.speed)};`,
      "a.wave_r = 0.85 + 0.15*Math.sin(t*0.37);",
      "a.wave_g = 0.45 + 0.25*Math.sin(t*0.23 + 1.0);",
      "a.wave_b = 0.15 + 0.15*Math.sin(t*0.51 + 2.0);",
      "a.zoom = 1.0 + 0.045*Math.min(a.bass_att, 3);",
      "a.rot = 0.008*Math.sin(t*0.21) + 0.012*Math.min(a.mid_att - 1, 2);",
      "a.warp = 0.15 + 0.35*Math.min(a.treb_att, 2.5);",
    ].join(" ") + turnWave(hueMatrix(v.hue)),
    pixel_eqs_str: `a.zoom = a.zoom + 0.02*a.rad*Math.sin(a.time*${f(0.4 * v.speed)} + a.ang*3);`,
    warp: "", comp: "",
    shapes: four([], shapeOff),
    waves: four([], waveOff),
  }),
};

// A slow tide drifting up the screen, the waveform a line across it, with
// the spectrum as a second line under it: low notes swell the tide.
const tide = {
  name: "Tide",
  vary: { hue: [-18, 18], speed: [0.85, 1.2] },
  make: (v) => {
    const [r, g, b] = turn(hueMatrix(v.hue), [1, 0.8, 0.5]);
    return {
      baseVals: {
        decay: 0.955, gammaadj: 1.5, echo_zoom: 1, echo_alpha: 0, wave_mode: 6, additivewave: 1,
        wave_a: 0.8, wave_scale: 0.9, wave_smoothing: 0.6, wave_y: 0.55, zoom: 0.995, dy: -0.004,
        warp: 0.6, warpscale: 2, warpanimspeed: 0.6 * v.speed, mv_a: 0, ob_size: 0, ib_size: 0,
      },
      init_eqs_str: "",
      frame_eqs_str: [
        `var t = a.time*${f(v.speed)};`,
        "a.wave_r = 0.25 + 0.2*Math.sin(t*0.17);",
        "a.wave_g = 0.6 + 0.3*Math.sin(t*0.29 + 2.0);",
        "a.wave_b = 0.9;",
        "a.dy = -0.004 - 0.006*Math.min(a.bass_att, 3);",
        "a.warp = 0.4 + 0.4*Math.min(a.mid_att, 2);",
      ].join(" ") + turnWave(hueMatrix(v.hue)),
      pixel_eqs_str: `a.dx = 0.003*Math.sin(a.y*6 + a.time*${f(0.7 * v.speed)});`,
      warp: "", comp: "",
      shapes: four([], shapeOff),
      waves: four([{
        baseVals: { enabled: 1, samples: 256, spectrum: 1, scaling: 1.6, smoothing: 0.5, additive: 1, thick: 1, r, g, b, a: 0.7 },
        init_eqs_str: "",
        frame_eqs_str: "",
        point_eqs_str: "a.x = a.sample; a.y = 0.2 + 0.25*a.value1; a.a = 0.35 + 0.5*a.sample;",
      }], waveOff),
    };
  },
};

// A soft disc in the middle that blooms with each note, its outline turning
// with the music, over a trail that spins out slowly.
const bloom = {
  name: "Bloom",
  vary: { hue: [-18, 18], speed: [0.85, 1.2] },
  make: (v) => {
    const m = hueMatrix(v.hue);
    const [r, g, b] = turn(m, [0.95, 0.55, 0.35]), [r2, g2, b2] = turn(m, [0.3, 0.1, 0.6]);
    const [br, bg, bb] = turn(m, [1, 0.9, 0.7]);
    return {
      baseVals: {
        decay: 0.94, gammaadj: 1.4, echo_zoom: 1, echo_alpha: 0, wave_mode: 2, additivewave: 1,
        wave_a: 0.5, wave_scale: 0.8, zoom: 1.01, rot: 0.004, warp: 0.1, mv_a: 0, ob_size: 0, ib_size: 0,
      },
      init_eqs_str: "",
      frame_eqs_str: [
        `var t = a.time*${f(v.speed)};`,
        "a.wave_r = 0.6 + 0.4*Math.sin(t*0.31 + 4.0);",
        "a.wave_g = 0.4 + 0.2*Math.sin(t*0.19);",
        "a.wave_b = 0.8;",
        "a.rot = 0.004 + 0.01*Math.min(a.treb_att - 1, 2);",
        "a.zoom = 1.01 + 0.02*Math.min(a.bass, 3);",
      ].join(" ") + turnWave(m),
      pixel_eqs_str: "",
      warp: "", comp: "",
      shapes: four([{
        baseVals: { enabled: 1, sides: 48, additive: 1, thickoutline: 1, x: 0.5, y: 0.5, rad: 0.12,
          r, g, b, a: 0.35, r2, g2, b2, a2: 0, border_r: br, border_g: bg, border_b: bb, border_a: 0.5 },
        init_eqs_str: "",
        frame_eqs_str: `a.rad = 0.08 + 0.09*Math.min(a.bass_att, 3); a.ang = a.time*${f(0.2 * v.speed)}; a.border_a = 0.2 + 0.25*Math.min(a.mid, 3);`,
      }], shapeOff),
      waves: four([], waveOff),
    };
  },
};

// ---------------------------------------------------------------- monochrome and dramatic (#196)

// The colour a monochrome look is drawn in: white, or nearly, or one colour.
const mono = (v) => tint(v.hue, v.sat);
const vec3 = ([r, g, b]) => `vec3(${f(r)}, ${f(g)}, ${f(b)})`;

// Squares rushing out of the middle: a deep zoom, so each frame's square
// becomes a wall of the tunnel. White on black at hard contrast; the
// tunnel twists with the mids and a loud attack turns it inside out for a
// moment.
const tunnel = {
  name: "Tunnel",
  vary: { hue: [0, 360], sat: [0, 0.12], speed: [0.85, 1.2], twist: [-1, 1] },
  make: (v) => ({
    baseVals: {
      decay: 0.93, gammaadj: 1, echo_zoom: 1, echo_alpha: 0, wave_a: 0, zoom: 1.06, rot: 0, warp: 0, mv_a: 0,
      ob_size: 0, ib_size: 0, wrap: 0,
    },
    init_eqs_str: FLASH_INIT,
    frame_eqs_str: [
      flash(),
      `a.zoom = 1.05 + 0.05*Math.min(a.bass_att, 2.5);`,
      `a.rot = ${f(0.012 * v.twist)}*Math.sin(a.time*${f(0.3 * v.speed)}) + ${f(0.01 * Math.sign(v.twist || 1))}*Math.min(Math.max(a.mid_att - 1, 0), 2);`,
    ].join(" "),
    pixel_eqs_str: "a.zoom = a.zoom + 0.06*a.rad;",
    warp: "",
    comp: `shader_body {
      vec3 c = texture(sampler_main, uv).rgb;
      float l = smoothstep(0.10, 0.38, dot(c, vec3(0.333)));
      l = mix(l, 1.0 - l, q1*0.85);
      ret = vec3(l) * ${vec3(mono(v))};
    }`,
    shapes: four([{
      baseVals: { enabled: 1, sides: 4, additive: 0, thickoutline: 1, x: 0.5, y: 0.5, rad: 0.05,
        r: 0, g: 0, b: 0, a: 0, r2: 0, g2: 0, b2: 0, a2: 0, border_r: 1, border_g: 1, border_b: 1, border_a: 1 },
      init_eqs_str: "",
      frame_eqs_str: `a.rad = 0.035 + 0.05*Math.min(a.bass, 3); a.ang = a.time*${f(0.35 * v.speed)} + 0.785*a.q1;`,
    }, {
      baseVals: { enabled: 1, sides: 4, additive: 0, thickoutline: 1, x: 0.5, y: 0.5, rad: 0.02,
        r: 0, g: 0, b: 0, a: 0, r2: 0, g2: 0, b2: 0, a2: 0, border_r: 1, border_g: 1, border_b: 1, border_a: 0.8 },
      init_eqs_str: "",
      frame_eqs_str: `a.rad = 0.02 + 0.03*Math.min(a.treb, 3); a.ang = 0.785 - a.time*${f(0.5 * v.speed)}; a.border_a = 0.3 + 0.35*Math.min(a.treb_att, 2);`,
    }], shapeOff),
    waves: four([], waveOff),
  }),
};

// A ring of triangles orbiting a hexagon, all hard outlines, the trail
// pulled inwards so each frame folds into the middle. Pure black and white,
// thresholded; a loud attack inverts the screen for a moment (a strobe at
// most about twice a second).
const shutter = {
  name: "Shutter",
  vary: { hue: [0, 360], sat: [0, 0.08], speed: [0.85, 1.25], twist: [-1, 1] },
  make: (v) => {
    const dir = v.twist < 0 ? -1 : 1;
    return {
      baseVals: {
        decay: 0.88, gammaadj: 1, echo_zoom: 1, echo_alpha: 0, wave_a: 0, zoom: 0.97, rot: 0, warp: 0, mv_a: 0,
        ob_size: 0, ib_size: 0,
      },
      init_eqs_str: FLASH_INIT,
      frame_eqs_str: [
        flash(1.3),
        "a.zoom = 0.975 - 0.02*Math.min(a.bass_att - 1, 1.5);",
        `a.rot = ${f(0.006 * dir)} + ${f(0.004 * v.twist)}*Math.min(a.treb_att, 2);`,
      ].join(" "),
      pixel_eqs_str: "",
      warp: "",
      comp: `shader_body {
        vec3 c = texture(sampler_main, uv).rgb;
        float l = smoothstep(0.22, 0.30, dot(c, vec3(0.333)));
        l = mix(l, 1.0 - l, q1);
        ret = vec3(l) * ${vec3(mono(v))};
      }`,
      shapes: four([{
        baseVals: { enabled: 1, sides: 3, num_inst: 6, additive: 0, thickoutline: 1, x: 0.5, y: 0.5, rad: 0.05,
          r: 0, g: 0, b: 0, a: 0, r2: 0, g2: 0, b2: 0, a2: 0, border_r: 1, border_g: 1, border_b: 1, border_a: 1 },
        init_eqs_str: "",
        frame_eqs_str: [
          `var p = a.instance/a.num_inst*6.2832 + a.time*${f(0.4 * v.speed * dir)};`,
          "var r = 0.22 + 0.06*Math.min(a.bass_att, 2.5);",
          "a.x = 0.5 + r*Math.cos(p); a.y = 0.5 + r*Math.sin(p);",
          `a.rad = 0.035 + 0.03*Math.min(a.mid, 3); a.ang = -p*2 + a.time*${f(0.7 * v.speed)};`,
        ].join(" "),
      }, {
        baseVals: { enabled: 1, sides: 6, additive: 0, thickoutline: 1, x: 0.5, y: 0.5, rad: 0.1,
          r: 0, g: 0, b: 0, a: 0, r2: 0, g2: 0, b2: 0, a2: 0, border_r: 1, border_g: 1, border_b: 1, border_a: 1 },
        init_eqs_str: "",
        frame_eqs_str: `a.rad = 0.07 + 0.08*Math.min(a.bass, 3) + 0.05*a.q1; a.ang = -a.time*${f(0.25 * v.speed * dir)};`,
      }], shapeOff),
      waves: four([], waveOff),
    };
  },
};

// Grey smoke rising off the waveform, which lies along the bottom: the
// warp stirs it with noise, treble roughens it, and a loud attack sends up
// a brighter puff.
const smoke = {
  name: "Smoke",
  vary: { hue: [0, 360], sat: [0, 0.1], speed: [0.8, 1.25], rise: [0.8, 1.3] },
  make: (v) => ({
    baseVals: {
      decay: 0.985, gammaadj: 1, echo_zoom: 1, echo_alpha: 0, wave_a: 0,
      zoom: 1, dy: -0.004, rot: 0, warp: 0, mv_a: 0, ob_size: 0, ib_size: 0,
    },
    init_eqs_str: FLASH_INIT,
    frame_eqs_str: [
      flash(1.4),
      `a.dy = ${f(-0.0035 * v.rise)} - 0.002*Math.min(a.bass_att, 2);`,
      ASPECT,
      "a.q5 = 0.35 + 0.3*Math.min(a.bass_att, 2) + 0.4*a.flash;",
      "a.q2 = 0.004 + 0.004*Math.min(a.treb_att, 2);",
    ].join(" "),
    pixel_eqs_str: `a.dx = 0.002*Math.sin(a.y*9 + a.time*${f(0.6 * v.speed)});`,
    warp: `shader_body {
      vec2 n = texture(sampler_noise_lq, uv*0.6 + vec2(time*${f(0.011 * v.speed)}, -time*${f(0.02 * v.speed)})).xy - 0.5;
      ret = texture(sampler_main, uv + n*q2).rgb * 0.985 - 0.0015;
    }`,
    comp: `shader_body {
      vec3 c = texture(sampler_main, uv).rgb;
      float l = dot(c, vec3(0.333));
      l = smoothstep(0.015, 0.75, l);
      ret = vec3(pow(l, 0.85)) * ${vec3(mono(v))};
    }`,
    shapes: four([], shapeOff),
    waves: four([{
      baseVals: { enabled: 1, samples: 96, spectrum: 1, scaling: 1.2, smoothing: 0.6, usedots: 1, thick: 1, additive: 1, r: 1, g: 1, b: 1, a: 0.6 },
      init_eqs_str: "",
      frame_eqs_str: "",
      point_eqs_str: `${FOOT} a.x = a.sample; a.y = foot + 0.02 + 0.1*Math.min(a.value1, 2); a.a = 0.25 + 0.5*Math.min(a.value1, 1);`,
    }, {
      baseVals: { enabled: 1, samples: 256, spectrum: 0, scaling: 1, smoothing: 0.7, thick: 1, additive: 1, r: 1, g: 1, b: 1, a: 0.6 },
      init_eqs_str: "",
      frame_eqs_str: "",
      point_eqs_str: `${FOOT} a.x = a.sample; a.y = foot + 0.05 + 0.25*a.value1; a.a = a.q5;`,
    }], waveOff),
  }),
};

// A kaleidoscope in one colour: the waveform spinning in the middle,
// mirrored four ways by the composite shader (the brightest of the four
// reflections, so nothing drawn falls outside it: a fold into one octant
// went black within seconds once the trail drifted out of it), its hot
// centre near white. Red, or a shade near it.
const mirror = {
  name: "Mirror",
  vary: { hue: [-28, 10], speed: [0.85, 1.25], twist: [-1, 1] },
  make: (v) => {
    const colour = tint(v.hue, 1);
    return {
      baseVals: {
        decay: 0.93, gammaadj: 1, echo_zoom: 1, echo_alpha: 0, wave_mode: 3, additivewave: 1, wave_thick: 1,
        wave_a: 0.9, wave_scale: 1.1, wave_smoothing: 0.5, wave_r: 1, wave_g: 1, wave_b: 1,
        zoom: 1.03, rot: 0.01, warp: 0.2, mv_a: 0, ob_size: 0, ib_size: 0,
      },
      init_eqs_str: FLASH_INIT,
      frame_eqs_str: [
        flash(1.35),
        `a.rot = ${f(0.012 * (v.twist < 0 ? -1 : 1))}*(1 + Math.min(a.mid_att, 2)*0.6);`,
        "a.zoom = 1.02 + 0.04*Math.min(a.bass, 3);",
        `a.wave_x = 0.5 + 0.12*Math.sin(a.time*${f(0.37 * v.speed)}); a.wave_y = 0.5 + 0.12*Math.cos(a.time*${f(0.29 * v.speed)});`,
      ].join(" "),
      pixel_eqs_str: "",
      warp: "",
      comp: `shader_body {
        vec2 m = 1.0 - uv;
        vec3 c = max(max(texture(sampler_main, uv).rgb, texture(sampler_main, vec2(m.x, uv.y)).rgb),
                     max(texture(sampler_main, vec2(uv.x, m.y)).rgb, texture(sampler_main, m).rgb));
        float l = smoothstep(0.08, 0.6, dot(c, vec3(0.333)));
        ret = ${vec3(colour)} * l + vec3(pow(l, 5.0)) * 0.7;
        ret = mix(ret, ${vec3(colour)}, q1*0.5);
      }`,
      shapes: four([{
        baseVals: { enabled: 1, sides: 4, additive: 1, thickoutline: 1, x: 0.5, y: 0.5, rad: 0.1,
          r: 0, g: 0, b: 0, a: 0, r2: 0, g2: 0, b2: 0, a2: 0, border_r: 1, border_g: 1, border_b: 1, border_a: 0.6 },
        init_eqs_str: "",
        frame_eqs_str: `a.rad = 0.06 + 0.1*Math.min(a.bass_att, 2.5); a.ang = a.time*${f(0.3 * v.speed)}; a.x = 0.5 + 0.15*Math.cos(a.time*${f(0.21 * v.speed)}); a.y = 0.5 + 0.15*Math.sin(a.time*${f(0.17 * v.speed)});`,
      }], shapeOff),
      waves: four([], waveOff),
    };
  },
};

// A dark sky: the waveform a dim horizon along the bottom, and on a loud
// attack a bolt of lightning down the screen, the clouds lit for a moment
// behind it. Its afterimage fades over half a second.
const storm = {
  name: "Storm",
  vary: { hue: [190, 250], sat: [0, 0.18], speed: [0.85, 1.2] },
  make: (v) => ({
    baseVals: {
      decay: 0.86, gammaadj: 1, echo_zoom: 1, echo_alpha: 0, wave_a: 0,
      zoom: 1, rot: 0, warp: 0, mv_a: 0, ob_size: 0, ib_size: 0,
    },
    init_eqs_str: `${FLASH_INIT} a.bolt_x = 0.5; a.bolt_seed = 1;`,
    frame_eqs_str: [
      flash(1.3),
      "if (a.flash == 1) { a.bolt_x = 0.15 + 0.7*Math.random(); a.bolt_seed = 100*Math.random(); }",
      "a.q2 = a.bolt_x; a.q3 = a.bolt_seed;",
      ASPECT,
      "a.q5 = 0.2 + 0.25*Math.min(a.bass_att, 2);",
    ].join(" "),
    pixel_eqs_str: "",
    warp: "",
    comp: `shader_body {
      vec3 c = texture(sampler_main, uv).rgb;
      float cloud = texture(sampler_noise_lq, uv*vec2(1.3, 0.7) + vec2(time*${f(0.006 * v.speed)}, 0.0)).x;
      float l = dot(c, vec3(0.333));
      ret = (vec3(l) + vec3(cloud*cloud*(1.0 - uv.y*0.6))*q1*0.45) * ${vec3(mono(v))};
    }`,
    shapes: four([], shapeOff),
    waves: four([...[0, 1].map((branch) => ({
      baseVals: { enabled: 1, samples: 64, spectrum: 0, scaling: 1, smoothing: 0, thick: 1, additive: 1, r: 1, g: 1, b: 1, a: 1 },
      init_eqs_str: "",
      frame_eqs_str: "",
      point_eqs_str: FOOT + (branch === 0
        ? " var d = a.sample; a.y = head - d*(head - foot - 0.06); a.x = a.q2 + 0.05*Math.sin(d*37 + a.q3) + 0.025*Math.sin(d*113 + a.q3*3) + 0.02*a.value1; a.a = Math.min(1, a.q1*2);"
        : " var d = 0.3 + a.sample*0.45; a.y = head - d*(head - foot - 0.06); a.x = a.q2 + 0.05*Math.sin(d*37 + a.q3) + a.sample*0.3*Math.sin(a.q3) + 0.02*Math.sin(a.sample*91 + a.q3); a.a = Math.min(1, a.q1*1.4)*(1 - a.sample);"),
    })), {
      baseVals: { enabled: 1, samples: 256, spectrum: 0, scaling: 0.8, smoothing: 0.7, thick: 1, additive: 1, r: 1, g: 1, b: 1, a: 1 },
      init_eqs_str: "",
      frame_eqs_str: "",
      point_eqs_str: `${FOOT} a.x = a.sample; a.y = foot + 0.05 + 0.2*a.value1; a.a = a.q5;`,
    }], waveOff),
  }),
};

// An eclipse: a black disc in the middle, the waveform its corona, the
// trail drawn out into rays. White, or warm towards amber; a loud attack
// flares it.
const eclipse = {
  name: "Eclipse",
  vary: { hue: [20, 55], sat: [0, 0.35], speed: [0.85, 1.2], rays: [8, 14] },
  make: (v) => ({
    baseVals: {
      decay: 0.96, gammaadj: 1, echo_zoom: 1, echo_alpha: 0, wave_mode: 0, additivewave: 1, wave_thick: 1,
      wave_a: 1, wave_scale: 1.3, wave_smoothing: 0.75, wave_r: 1, wave_g: 1, wave_b: 1,
      zoom: 1.015, rot: 0.003, warp: 0, mv_a: 0, ob_size: 0, ib_size: 0,
    },
    init_eqs_str: FLASH_INIT,
    frame_eqs_str: [
      flash(1.35),
      "a.zoom = 1.012 + 0.02*Math.min(a.bass_att, 2.5);",
      `a.rot = 0.003*Math.sin(a.time*${f(0.13 * v.speed)});`,
    ].join(" "),
    pixel_eqs_str: `a.zoom = a.zoom + 0.025*a.rad*Math.sin(a.ang*${Math.round(v.rays)} + a.time*${f(0.4 * v.speed)});`,
    warp: "",
    comp: `shader_body {
      vec3 c = texture(sampler_main, uv).rgb;
      float l = smoothstep(0.03, 0.65, dot(c, vec3(0.333)));
      ret = vec3(pow(l, 1.3)) * ${vec3(mono(v))} * (1.0 + q1*0.6);
    }`,
    shapes: four([{
      baseVals: { enabled: 1, sides: 64, additive: 0, thickoutline: 0, x: 0.5, y: 0.5, rad: 0.15,
        r: 0, g: 0, b: 0, a: 1, r2: 0, g2: 0, b2: 0, a2: 1, border_r: 1, border_g: 1, border_b: 1, border_a: 0 },
      init_eqs_str: "",
      frame_eqs_str: "a.rad = 0.13 + 0.025*Math.min(a.bass, 3);",
    }], shapeOff),
    waves: four([], waveOff),
  }),
};

export const LOOKS = [embers, tide, bloom, tunnel, shutter, smoke, mirror, storm, eclipse];
// How long one takes to blend into the next.
export const BLEND_SECONDS = 4;
