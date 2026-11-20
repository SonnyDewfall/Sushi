// The visualiser's equaliser looks (#196): Bars, Curve, Aurora and Mono
// bars, drawn by the page on a 2D canvas from the spectrum of the same
// capture butterchurn hears, not by butterchurn. Loaded with visualiser.js.
//
// Each is on one axis: a log frequency scale, each of eight octaves the same
// width, 47 Hz at the left to 12 kHz at the right, with each octave's colour
// the same in all three coloured looks (rig.js EQ_COLOURS), so someone who
// knows the map can read what range is sounding from where and what colour
// it is. Nothing is labelled: there is no number or word on the screen. The
// mapping, the smoothing and the peak caps are rig.js's (eqBands and its
// neighbours), unit-tested; this is the drawing.
//
// Two AnalyserNodes on the capture: a long FFT for the bands under 750 Hz,
// a short one from there up (rig.js VISUALISER_EQ says why). An
// AnalyserNode does its FFT only when asked, so while a butterchurn look is
// up they cost nothing but the copy of the samples.

import * as R from "./rig.js";

const E = R.VISUALISER_EQ;
const COLOURS = R.EQ_COLOURS.map((c) => c.rgb);
const rgba = ([r, g, b], a = 1) => `rgba(${r | 0}, ${g | 0}, ${b | 0}, ${a})`;
const toward = ([r, g, b], [r2, g2, b2], t) => [r + (r2 - r) * t, g + (g2 - g) * t, b + (b2 - b) * t];
const WHITE = [255, 255, 255];

// The colour of `hue` degrees and `sat`, full value, as [r, g, b] 0 to 255.
function hsv(hue, sat) {
  const h = (((hue % 360) + 360) % 360) / 60, x = sat * (1 - Math.abs((h % 2) - 1));
  const [r, g, b] = h < 1 ? [sat, x, 0] : h < 2 ? [x, sat, 0] : h < 3 ? [0, sat, x] : h < 4 ? [0, x, sat] : h < 5 ? [x, 0, sat] : [sat, 0, x];
  return [r, g, b].map((c) => Math.round((c + 1 - sat) * 255));
}

// The display's frame, in canvas pixels: left and right margins, the foot
// the levels stand on, and their full height.
function frameOf(w, h) {
  const left = Math.round(w * 0.04), right = Math.round(w * 0.96);
  return { left, right, width: right - left, top: Math.round(h * 0.1), foot: Math.round(h * 0.9), height: Math.round(h * 0.8), scale: h / 800 };
}

// A gradient across the display in the octave colours: each octave its own
// colour, with a narrow blend at each boundary.
function octaveGradient(g, fr) {
  const grad = g.createLinearGradient(fr.left, 0, fr.right, 0);
  const blend = 0.012;
  COLOURS.forEach((c, k) => {
    grad.addColorStop(Math.max(0, k / E.octaves + (k ? blend : 0)), rgba(c));
    grad.addColorStop(Math.min(1, (k + 1) / E.octaves - (k < E.octaves - 1 ? blend : 0)), rgba(c));
  });
  return grad;
}

// ---------------------------------------------------------------- Bars

// A graphic equaliser's LED bars, two to a third-octave, each lit to its
// level in its octave's colour (or one colour, for Mono bars), unlit
// segments a faint ghost of it, a cap on each holding its peak and falling
// back slowly. A wider gap at each octave's edge.
function drawBars(g, w, h, bands, s, look) {
  const fr = frameOf(w, h);
  const segments = 32;
  const segH = fr.height / segments;
  const segGap = Math.max(1, Math.round(segH * 0.3));
  const barGap = Math.max(1, Math.round(fr.width * 0.0022));
  const octaveGap = Math.max(2, Math.round(fr.width * 0.006));
  g.globalCompositeOperation = "source-over";
  g.fillStyle = "#000";
  g.fillRect(0, 0, w, h);
  const one = look.mono ? hsv(s.variation.hue, s.variation.sat) : null;
  for (let i = 0; i < bands.length; i += 1) {
    const b = bands[i];
    const first = i % look.perOctave === 0, last = i % look.perOctave === look.perOctave - 1;
    const x0 = Math.round(fr.left + b.x0 * fr.width + (first ? octaveGap / 2 : barGap / 2));
    const x1 = Math.round(fr.left + b.x1 * fr.width - (last ? octaveGap / 2 : barGap / 2));
    const c = one || COLOURS[b.octave];
    g.fillStyle = rgba(c, 0.07);
    g.fillRect(x0, fr.top, x1 - x0, fr.height);
    const lit = Math.round(s.levels[i] * segments);
    if (lit > 0) {
      g.fillStyle = rgba(c);
      g.fillRect(x0, fr.foot - lit * segH, x1 - x0, lit * segH);
    }
    const cap = Math.ceil(s.peaks[i].value * segments - 0.01);
    if (cap > 0) {
      g.fillStyle = rgba(toward(c, WHITE, 0.55));
      g.fillRect(x0, fr.foot - cap * segH, x1 - x0, segH);
    }
  }
  // The segments: black lines across, over every bar at once.
  g.fillStyle = "#000";
  for (let k = 1; k <= segments; k += 1) g.fillRect(fr.left, Math.round(fr.foot - k * segH), fr.width, segGap);
}

// ---------------------------------------------------------------- Curve

// An analyser's curve: a smooth line through the spectrum, glowing, in the
// octave colours, with a fill under it fading to black, a thin line of the
// held peaks over it, and faint lines at the octaves' edges.
function drawCurve(g, w, h, bands, s, look) {
  const fr = frameOf(w, h);
  if (!s.gradient || s.gradientFor !== `${w}x${h}`) { s.gradient = octaveGradient(g, fr); s.gradientFor = `${w}x${h}`; }
  g.globalCompositeOperation = "source-over";
  g.fillStyle = "#000";
  g.fillRect(0, 0, w, h);
  g.fillStyle = "rgba(255, 255, 255, 0.045)";
  for (let k = 1; k < E.octaves; k += 1) g.fillRect(Math.round(fr.left + (k / E.octaves) * fr.width), fr.top, Math.max(1, Math.round(fr.scale)), fr.height);
  g.fillRect(fr.left, fr.foot, fr.width, Math.max(1, Math.round(fr.scale)));

  const point = (i, values) => [fr.left + ((bands[i].x0 + bands[i].x1) / 2) * fr.width, fr.foot - values(i) * fr.height];
  const trace = (values) => {
    g.beginPath();
    let [px, py] = point(0, values);
    g.moveTo(fr.left, py);
    g.lineTo(px, py);
    for (let i = 1; i < bands.length; i += 1) {
      const [x, y] = point(i, values);
      g.quadraticCurveTo(px, py, (px + x) / 2, (py + y) / 2);
      px = x; py = y;
    }
    g.lineTo(fr.right, py);
  };
  const level = (i) => s.levels[i];

  // The fill: the colours faint under the line, darkening to the foot.
  trace(level);
  g.lineTo(fr.right, fr.foot);
  g.lineTo(fr.left, fr.foot);
  g.closePath();
  g.globalAlpha = 0.42;
  g.fillStyle = s.gradient;
  g.fill();
  g.globalAlpha = 1;
  if (!s.shade || s.shadeFor !== `${w}x${h}`) {
    s.shade = g.createLinearGradient(0, fr.top, 0, fr.foot);
    s.shade.addColorStop(0, "rgba(0, 0, 0, 0)");
    s.shade.addColorStop(1, "rgba(0, 0, 0, 0.8)");
    s.shadeFor = `${w}x${h}`;
  }
  g.fillStyle = s.shade;
  g.fill();

  // The line, glowing: wide and faint, then narrower, then sharp. Three
  // passes and twelve points an octave: four passes over 24 took 27 ms a
  // frame at 1280 by 800 in software (Chromium with no GPU), these 15.
  g.globalCompositeOperation = "lighter";
  g.strokeStyle = s.gradient;
  g.lineJoin = "round";
  g.lineCap = "round";
  const glow = s.variation.glow || 1;
  for (const [width, alpha] of [[8, 0.14], [3.2, 0.5], [1.6, 1]]) {
    g.lineWidth = Math.max(1, width * fr.scale * (width > 2 ? glow : 1));
    g.globalAlpha = alpha;
    trace(level);
    g.stroke();
  }
  // The held peaks, thin and dim.
  g.globalAlpha = 0.4;
  g.lineWidth = Math.max(1, 1.2 * fr.scale);
  trace((i) => s.peaks[i].value);
  g.stroke();
  g.globalAlpha = 1;
  g.globalCompositeOperation = "source-over";
}

// ---------------------------------------------------------------- Aurora

// Curtains of light, a curtain to each twelfth of an octave in its
// octave's colour, rising from the foot as high and as bright as its level
// and swaying a little; behind them a slower glow that builds only where a
// level stays (rig.js eqGlow), so a sustained build-up, such as mud, lingers
// as a haze in its colour. A short trail on the canvas smears the motion.
// Faint marks along the foot at the octaves' edges.
function curtainSprite(c) {
  const sprite = document.createElement("canvas");
  sprite.width = 4;
  sprite.height = 256;
  const g = sprite.getContext("2d");
  const grad = g.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, rgba(c, 0));
  grad.addColorStop(0.55, rgba(c, 0.35));
  grad.addColorStop(0.88, rgba(toward(c, WHITE, 0.25), 0.95));
  grad.addColorStop(1, rgba(c, 0.5));
  g.fillStyle = grad;
  g.fillRect(0, 0, 4, 256);
  return sprite;
}

function drawAurora(g, w, h, bands, s, look, now) {
  const fr = frameOf(w, h);
  if (!s.sprites) s.sprites = COLOURS.map(curtainSprite);
  g.globalCompositeOperation = "source-over";
  g.globalAlpha = 1;
  g.fillStyle = "rgba(0, 0, 0, 0.32)";
  g.fillRect(0, 0, w, h);
  g.globalCompositeOperation = "lighter";
  const t = now / 1000 * (s.variation.sway || 1);
  const bw = fr.width / bands.length;
  for (let i = 0; i < bands.length; i += 1) {
    const b = bands[i];
    const x = fr.left + b.x0 * fr.width;
    const sprite = s.sprites[b.octave];
    const glow = s.glow[i];
    if (glow > 0.02) {
      const gh = glow * fr.height * 0.95;
      g.globalAlpha = Math.min(1, glow * 0.55);
      g.drawImage(sprite, x - bw, fr.foot - gh, bw * 3, gh * 1.04);
    }
    const level = s.levels[i];
    if (level > 0.01) {
      const ripple = 1 + 0.06 * Math.sin(t * 1.3 + i * 0.7);
      const lh = level * fr.height * ripple;
      const sway = bw * 0.45 * Math.sin(t * 0.8 + i * 0.37);
      g.globalAlpha = Math.min(1, 0.2 + level * 0.85);
      g.drawImage(sprite, x + sway - bw * 0.1, fr.foot - lh, bw * 1.2, lh * 1.03);
    }
  }
  g.globalCompositeOperation = "source-over";
  g.globalAlpha = 1;
  g.fillStyle = "rgba(255, 255, 255, 0.2)";
  const mark = Math.max(1, Math.round(1.5 * fr.scale));
  for (let k = 0; k <= E.octaves; k += 1) {
    g.fillRect(Math.round(fr.left + (k / E.octaves) * fr.width - mark / 2), Math.round(h * 0.95), mark, Math.round(10 * fr.scale));
  }
}

// ---------------------------------------------------------------- the looks

// Each look: its bands to an octave, its drawing, and the bounds of what
// varies each time it comes on. Not the colours of the coloured three:
// those are the map, and stay put.
export const EQ_LOOKS = {
  Bars: { perOctave: 6, draw: drawBars, mono: false, vary: {} },
  Curve: { perOctave: 12, draw: drawCurve, mono: false, vary: { glow: [0.8, 1.25] } },
  Aurora: { perOctave: 12, draw: drawAurora, mono: false, vary: { sway: [0.6, 1.4] } },
  "Mono bars": { perOctave: 6, draw: drawBars, mono: true, vary: { hue: [95, 200], sat: [0.45, 0.85] } },
};

// The drawing for the equaliser looks on `canvas`, hearing through
// `context`: `{listen(node), show(name, variation), render(now), resize(w, h),
// stats()}`. Throws if the canvas gives no 2D context.
export function createEq(canvas, context) {
  const g = canvas.getContext("2d", { alpha: false });
  if (!g) throw new Error("the canvas gave no 2D context");
  const analysers = { long: context.createAnalyser(), short: context.createAnalyser() };
  analysers.long.fftSize = E.longFft;
  analysers.short.fftSize = E.shortFft;
  const data = {};
  for (const [key, node] of Object.entries(analysers)) {
    node.smoothingTimeConstant = 0;
    data[key] = new Float32Array(node.frequencyBinCount);
  }
  const bandSets = {};
  const bandsFor = (n) => (bandSets[n] ||= R.eqBands(context.sampleRate, n));
  let look = null, name = null, s = null, at = null, drawn = 0, top = E.floor;

  return {
    // Hear `node` (the capture, mixed to one): it is disconnected, these
    // with it, when the capture is let go.
    listen(node) {
      node.connect(analysers.long);
      node.connect(analysers.short);
    },
    show(which, variation = {}) {
      look = EQ_LOOKS[which];
      name = which;
      const n = bandsFor(look.perOctave).length;
      s = { variation, levels: new Float32Array(n), glow: new Float32Array(n), peaks: Array.from({ length: n }, () => ({ value: 0, held: 0 })) };
      at = null;
      g.globalCompositeOperation = "source-over";
      g.globalAlpha = 1;
      g.fillStyle = "#000";
      g.fillRect(0, 0, canvas.width, canvas.height);
    },
    render(now) {
      if (!look) return;
      const dt = at == null ? 1 / 30 : Math.min(0.1, Math.max(0, (now - at) / 1000));
      at = now;
      analysers.long.getFloatFrequencyData(data.long);
      analysers.short.getFloatFrequencyData(data.short);
      const bands = bandsFor(look.perOctave);
      const db = bands.map((b) => R.eqBandDb(data[b.fft], b));
      top = R.eqTop(top, Math.max(...db), dt);
      for (let i = 0; i < bands.length; i += 1) {
        s.levels[i] = R.eqSmooth(s.levels[i], R.eqLevel(db[i], top), dt);
        s.peaks[i] = R.eqPeak(s.peaks[i], s.levels[i], dt);
        s.glow[i] = R.eqGlow(s.glow[i], s.levels[i], dt);
      }
      look.draw(g, canvas.width, canvas.height, bands, s, look, now);
      drawn += 1;
    },
    resize() {
      if (s) { s.gradient = null; s.shade = null; }
    },
    stats() {
      return { look: name, drawn, top, loudest: s ? Math.max(0, ...s.levels) : 0 };
    },
  };
}
