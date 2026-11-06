// Static demo: answers the workspace page's API requests from a recording of the real backend
// (tools/static_demo/record.py). The recording is a chain of states ("epochs"); a click that the story recorded moves
// to the next state, and changes the backend made on its own (an undo window closing, an agent finishing) follow by
// themselves after a few seconds. Everything happens in this browser tab; nothing is sent anywhere.

const REC = await fetch(new URL("../../data/recording.json", import.meta.url)).then((r) => r.json());
const KEY = "demo.state";
const RESEARCH_MS = 9000; // how long the research agent appears to work on a picked company

const fresh = () => ({ epoch: 0, history: [], entered: { 0: Date.now() }, subs: [], overlay: null });
let S = load();

function load() {
  try {
    const s = JSON.parse(sessionStorage.getItem(KEY));
    if (s && REC.epochs[s.epoch]) return s;
  } catch { /* start again */ }
  return fresh();
}
const save = () => sessionStorage.setItem(KEY, JSON.stringify(S));

// ---------- keys and time ----------
export function normKey(path) {
  const [p, q = ""] = path.split("?");
  const dec = (x) => { try { return decodeURIComponent(x.replace(/\+/g, " ")); } catch { return x; } };
  let parts = q ? q.split("&").filter(Boolean).map(dec) : [];
  const pp = dec(p);
  if (pp === "/updates") parts = parts.map((x) => (x.startsWith("since=") ? "since=*" : x));
  return pp + (parts.length ? `?${parts.join("&")}` : "");
}

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;
const pad = (n, w = 2) => String(n).padStart(w, "0");
function shiftIso(s, ms) {
  const t = Date.parse(s);
  if (Number.isNaN(t)) return s;
  const d = new window.DemoRealDate(t + ms);
  if (/(Z|[+-]\d{2}:?\d{2})$/.test(s)) return d.toISOString();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    + (s.includes(".") ? `.${pad(d.getMilliseconds(), 3)}000` : "");
}

// Recorded times move with the visitor: what happened when an epoch began in the recording happens when the visitor
// reached it. Typed values replace the recorded ones (an email address, a reply).
function adapt(body, epoch) {
  const at = S.entered[epoch] ?? S.entered[S.epoch];
  const ms = at - REC.epochs[epoch].t;
  const walk = (v) => {
    if (typeof v === "string") {
      let s = ISO.test(v) && ms ? shiftIso(v, ms) : v;
      for (const [a, b] of S.subs) if (s.includes(a)) s = s.split(a).join(b);
      return s;
    }
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, walk(x)]));
    return v;
  };
  return walk(body);
}

// The epochs this visit passed through, newest first: an answer missing in this epoch comes from the last one that had it.
const chain = () => [S.epoch, ...S.history.map((h) => h.from).reverse(), 0].filter((e, i, a) => a.indexOf(e) === i);

function recorded(key, user) {
  for (const e of chain()) {
    const users = REC.epochs[e].users;
    const hit = (users[user] && users[user][key]) || Object.values(users).map((u) => u[key]).find(Boolean);
    if (hit) return { status: hit[0], body: adapt(REC.blobs[hit[1]], e), epoch: e };
  }
  return null;
}

const notInDemo = (what) => ({ status: 409, body: { error: what, code: "demo" } });

// ---------- the research agent at work (the recording finished at once) ----------
function overlay() {
  const o = S.overlay;
  if (!o) return null;
  const f = (Date.now() - o.start) / RESEARCH_MS;
  if (f >= 1) {
    S.overlay = null;
    save();
    return null;
  }
  return { ids: new Set(o.ids), f };
}

function dressResearch(key, body, ov) {
  if (!ov || body == null) return body;
  const id = key.split("?")[0].split("/")[2];
  if (key === "/opportunities") return body.map((o) => (ov.ids.has(o.id) ? { ...o, stage: "researching" } : o));
  if (key.startsWith("/opportunities?")) return key.includes("stage=review") ? body.filter((o) => !ov.ids.has(o.id)) : body;
  if (/^\/opportunities\/[^/]+$/.test(key.split("?")[0]) && ov.ids.has(id)) {
    const r = body.research || {};
    const log = r.log || [];
    const n = Math.max(1, Math.ceil(log.length * Math.min(1, ov.f * 1.15)));
    return { ...body, stage: "researching", research: { ...r, status: "running", log: log.slice(0, n), error: null } };
  }
  if (key === "/updates?days=7" || key.startsWith("/updates?")) {
    return { ...body, items: (body.items || []).filter((x) => !(ov.ids.has(x.opportunityId) && x.kind !== "local")) };
  }
  return body;
}

// ---------- moving between epochs ----------
const listeners = new Set();
let working = null; // the agent shown at work now: a listener that joins later still sees it
export function onChange(fn) {
  listeners.add(fn);
  if (working && working.until > Date.now()) fn({ ...working, ms: working.until - Date.now() });
}
function emit(what) {
  if (what.type === "working") working = { ...what, until: Date.now() + what.ms };
  if (what.type === "done") working = null;
  for (const fn of listeners) fn(what);
}

let autoTimer = null;
function nextAuto() {
  return REC.transitions.find((t) => t.from === S.epoch && t.auto != null && !t.retired);
}
function schedule() {
  clearTimeout(autoTimer);
  const t = nextAuto();
  if (!t) return;
  const left = Math.max(300, S.entered[S.epoch] + t.auto - Date.now());
  if (t.kind === "auto" && t.agent) emit({ type: "working", agent: t.agent, label: t.working || t.label, ms: left });
  autoTimer = setTimeout(async () => {
    if (nextAuto() !== t) return;
    enter(t);
    emit({ type: "done", agent: t.agent || "" });
    await refreshPage();
  }, left);
}

function enter(t, body) {
  S.history.push({ from: S.epoch, t: REC.transitions.indexOf(t) });
  if (body && t.body && typeof body === "object") {
    for (const k of ["to", "text", "reason", "comment"]) {
      const a = t.body[k], b = body[k];
      if (typeof a === "string" && typeof b === "string" && a.trim().length >= 4 && b.trim() && a !== b) S.subs.push([a, b.trim()]);
    }
  }
  S.epoch = t.to;
  S.entered[S.epoch] = Date.now();
  if (t.animate === "research" && t.created) {
    S.overlay = { ids: t.created, start: Date.now() };
    emit({ type: "working", agent: "Research agent", label: "Researching Corvane Industrial: reading public pages", ms: RESEARCH_MS });
    setTimeout(async () => { emit({ type: "done", agent: "Research agent" }); await refreshPage(); }, RESEARCH_MS + 50);
  }
  save();
  schedule();
  emit({ type: "epoch", epoch: S.epoch, label: t.label });
}

// Draw the page again with the new state (main.js exposes this in the demo build).
async function refreshPage() {
  if (window.demoRefresh) await window.demoRefresh();
}

function undoTarget() {
  const h = S.history[S.history.length - 1];
  return h && REC.transitions[h.t].undoable ? h : null;
}

// ---------- the API ----------
export async function answer(method, path, body, user) {
  await new Promise((r) => setTimeout(r, method === "GET" ? 40 + Math.random() * 80 : 250 + Math.random() * 250));
  const key = normKey(path);
  const ov = overlay();
  if (method === "GET") {
    if (key.startsWith("/updates?since=")) {
      const week = recorded("/updates?days=7", user);
      const since = Date.parse(decodeURIComponent((path.split("since=")[1] || "").split("&")[0]));
      const items = week ? week.body.items.filter((x) => !(Date.parse(x.at) <= since)) : [];
      return { status: 200, body: dressResearch(key, { items, now: new Date().toISOString() }, ov) };
    }
    const hit = recorded(key, user);
    if (!hit) return notInDemo("This view is not part of the recorded demo. Follow the guide (bottom left) to see the main flow.");
    return { status: hit.status, body: dressResearch(key, hit.body, ov) };
  }
  // a click the story recorded: move on
  const t = REC.transitions.find((x) => x.from === S.epoch && x.method === method && x.key === key && !x.retired && !x.undo);
  if (t) {
    if (t.user && user && t.user !== user) {
      return notInDemo(`In this demo ${t.user} takes this step. Choose ${t.user} under "Acting as" at the top right.`);
    }
    enter(t, body);
    return { status: t.status, body: adapt(REC.blobs[t.res], S.epoch) };
  }
  // Undo: back to the state before the click it takes back
  if (/\/undo$/.test(key) && undoTarget()) {
    const h = S.history.pop();
    S.epoch = h.from;
    S.entered[S.epoch] = Date.now();
    S.overlay = null;
    save();
    schedule();
    emit({ type: "epoch", epoch: S.epoch, label: "undo" });
    const u = REC.transitions.find((x) => x.undo && x.key.split("/")[1] === key.split("/")[1]);
    return { status: 200, body: u ? adapt(REC.blobs[u.res], S.epoch) : { ok: true } };
  }
  return notInDemo("This is a demo with recorded data, so this change is not saved. The guide (bottom left) shows the next step of the story.");
}

// ---------- for the guide ----------
export const state = () => ({ epoch: S.epoch, researching: !!overlay(), history: S.history.map((h) => REC.transitions[h.t].label) });
export const transitions = () => REC.transitions;
export function nextStep() {
  const ui = REC.transitions.find((t) => t.from === S.epoch && t.kind === "ui" && !t.retired && !t.undo);
  const auto = nextAuto();
  return { ui, auto };
}
export function restart() {
  sessionStorage.removeItem(KEY);
  sessionStorage.removeItem("demo.clock");
  for (const k of Object.keys(localStorage)) if (k.startsWith("cgs.") || k.startsWith("demo.")) localStorage.removeItem(k);
  location.href = location.pathname;
}

schedule();
if (S.overlay) {
  const left = RESEARCH_MS - (Date.now() - S.overlay.start);
  if (left > 0) setTimeout(async () => { emit({ type: "done", agent: "Research agent" }); await refreshPage(); }, left + 50);
}
