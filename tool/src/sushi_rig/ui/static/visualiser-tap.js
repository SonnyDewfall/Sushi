// The visualiser's tap on the rig's output (#195): asked of the agent when
// the visualiser is about to listen, and only then. The agent starts it
// (vistap.py: a pw-loopback in Sushi's cycle, "Sushi rig output") and
// answers once it is linked and realtime; this page then renews its lease
// while it listens and gives it back when it stops (a close, a hidden tab,
// a trip, the page going). Refused, or not answered, the visualiser
// listens to the guitar's input instead. Loaded by screensaver.js; the
// decisions are the agent's.

// This page's id for its lease: two pages share one tap, each holding its own.
const PAGE = ((globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID()
  : `${Date.now()}-${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
// How long a request may take before the page gives up and falls back: the
// agent links the tap (up to 5 s) and looks at its threads (up to 1 s). A
// test sets `sushiRigTapAskMs` to shorten it.
const ASK_MS = Number(globalThis.sushiRigTapAskMs) > 0 ? Number(globalThis.sushiRigTapAskMs) : 9000;

let holding = false;
let offered = null;  // the source's name, as the agent last gave it
let asked = false;  // a request may be in flight, or have won a lease this page gave up waiting for
// Each acquire's ticket: `release` moves it on, and a newer acquire does, so
// an answer that comes after either is not this page's to act on (review of
// PR #200: a close during acquire left the late answer renewing for ever).
let ticket = 0;
let timer = null;
let why = null;

function post(path, body, extra = {}) {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), ...extra });
}

function stopRenewing() {
  if (timer) clearInterval(timer);
  timer = null;
}

// Ask for the tap: its source (`{node, label}`) once the agent has it
// linked and realtime, else null, and `reason()` says why not.
export async function acquire() {
  const mine = ++ticket;
  const ask = new AbortController();
  const late = setTimeout(() => ask.abort(), ASK_MS);
  asked = true;
  try {
    const response = await post("/visualiser/tap", { page: PAGE }, { signal: ask.signal });
    const body = await response.json().catch(() => null);
    // Released, or asked again, while this waited: the release already gave
    // back what this answer took (the agent takes it behind the start), and a
    // newer request owns the flags now. Nothing is held for it, or renewed.
    if (mine !== ticket) return null;
    if (!response.ok || !body || !body.ok || !body.source) {
      why = (body && body.reason) || `the agent answered ${response.status}`;
      holding = false;
      asked = false;  // answered: refused, so nothing is held
      stopRenewing();
      return null;
    }
    holding = true;
    why = null;
    offered = body.source.label || null;
    if (!timer) timer = setInterval(renew, 1000 * (body.renew_s || 10));
    return body.source;
  } catch (error) {
    if (mine !== ticket) return null;  // a newer request's flags are not this one's to drop
    why = ask.signal.aborted ? `the agent did not answer within ${ASK_MS / 1000} s` : `${error && error.message || error}`;
    holding = false;
    stopRenewing();
    // Not answered is not refused: the agent may yet start the tap and give
    // this page the lease. Given back now, it waits on the agent behind that
    // start, so nothing is left held (review of PR #200).
    release();
    return null;
  } finally {
    clearTimeout(late);
  }
}

// The lease, again, while this page listens. A renewal refused means the
// tap is gone: the capture's track ends, and screensaver.js chooses again.
async function renew() {
  if (!holding) { stopRenewing(); return; }
  try {
    const response = await post("/visualiser/tap", { page: PAGE });
    if (!response.ok) { holding = false; stopRenewing(); }
  } catch { /* the next one, or the lease running out, says */ }
}

// Give the lease back. `now`: the xrun safeguard tripped, so the agent stops
// the tap at once unless another page holds it.
export function release(now = false) {
  ticket += 1;
  if (!holding && !asked) return;
  holding = false;
  asked = false;
  stopRenewing();
  post("/visualiser/tap/release", { page: PAGE, now }, { keepalive: true }).catch(() => {});
}

export function isHolding() { return holding; }
export function offeredLabel() { return offered; }
export function reason() { return why; }
export { PAGE as page };

window.addEventListener("pagehide", () => release());
