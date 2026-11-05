// Background updates and the notification history.
// The page asks the server what happened for this person (GET /api/updates) every 20 seconds while the tab is visible,
// and at once when they come back to it. New items show as notices; they, and the notices of the person's own actions,
// stay in the notification history (the bell, bottom right) for a week. What has been read is remembered per person in
// this browser.
import { href } from "./lib.js";
import { linkFor } from "./pages/links.js";

const POLL_MS = 20000;
const KEEP_MS = 7 * 24 * 3600 * 1000;
const LOCAL_MAX = 50;

const state = { me: "", cursor: null, server: [], local: [], seen: "", timer: null, busy: false, api: null, onNew: null };

const store = {
  get: (what, fallback) => { try { return JSON.parse(localStorage.getItem(`cgs.${what}.${state.me}`)) ?? fallback; } catch { return fallback; } },
  set: (what, value) => { try { localStorage.setItem(`cgs.${what}.${state.me}`, JSON.stringify(value)); } catch { /* storage full or off */ } },
};

// Where an item leads, and what the link says.
export function linkOf(x) {
  const o = x.opportunityId ? { id: x.opportunityId, stage: x.stage } : null;
  if (x.kind === "inbox") {
    if (["draft", "reply"].includes(x.itemKind) && o && ["identified", "contacted", "nurture"].includes(o.stage)) {
      return { href: href("outreach", { item: x.itemId }), label: "Open it" };
    }
    if (x.itemKind === "candidates") return { href: href("companies", { tab: "new" }), label: "Open them" };
    return o && o.stage ? { href: linkFor(o), label: "Open it" } : { href: href("agents"), label: "Open the agents" };
  }
  if (x.kind === "research") {
    return x.status === "done" && o.stage === "review" ? { href: href("companies", { tab: "review", id: o.id }), label: "Review it" }
      : { href: o.stage ? linkFor(o) : href("companies", { tab: "research" }), label: "See it" };
  }
  return o && o.stage ? { href: linkFor(o), label: "See it" } : null;
}

// Everything in the history, newest first.
export function items() {
  const all = [...state.server.map((x) => ({ ...x, link: linkOf(x) })), ...state.local];
  return all.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
}

export function unread() {
  return items().filter((x) => x.at > state.seen).length;
}

export const seenAt = () => state.seen;

export function markRead() {
  state.seen = new Date().toISOString();
  store.set("seen", state.seen);
}

const textOf = (message) => {
  const s = String(message);
  return s.includes("<") ? new DOMParser().parseFromString(s, "text/html").body.textContent.trim() : s;
};

// A notice from the person's own action joins the history (it is already on screen, so it is read).
export function record(message, link) {
  if (!state.me) return;
  const at = new Date().toISOString();
  state.local = [{ id: `local:${at}:${Math.random().toString(36).slice(2, 7)}`, at, kind: "local", text: textOf(message), link: link || null },
    ...state.local].filter((x) => Date.now() - Date.parse(x.at) < KEEP_MS).slice(0, LOCAL_MAX);
  store.set("history", state.local);
  if (state.seen < at) { state.seen = at; store.set("seen", at); }
}

// Start for the person acting (again when they switch). onNew(newItems) runs after every check.
export async function start(api, me, onNew) {
  stop();
  Object.assign(state, { me, api, onNew, server: [], cursor: null });
  state.local = store.get("history", []).filter((x) => Date.now() - Date.parse(x.at) < KEEP_MS);
  try {
    const res = await api.get("/updates?days=7");
    state.server = res.items;
    state.cursor = res.now;
    state.seen = store.get("seen", "") || res.now; // the first time, the past week counts as read
    store.set("seen", state.seen);
  } catch {
    state.cursor = new Date().toISOString();
    state.seen = store.get("seen", "") || state.cursor;
  }
  onNew([]);
  state.timer = setInterval(check, POLL_MS);
  document.addEventListener("visibilitychange", onVisible);
}

export function stop() {
  clearInterval(state.timer);
  state.timer = null;
  document.removeEventListener("visibilitychange", onVisible);
}

function onVisible() {
  if (!document.hidden) check();
}

export async function check() {
  if (state.busy || document.hidden || !state.api || !state.cursor) return;
  state.busy = true;
  try {
    const res = await state.api.get(`/updates?since=${encodeURIComponent(state.cursor)}`);
    const known = new Set(state.server.map((x) => x.id));
    const fresh = res.items.filter((x) => !known.has(x.id));
    state.cursor = res.now;
    state.server = [...fresh, ...state.server].filter((x) => Date.now() - Date.parse(x.at) < KEEP_MS);
    if (state.onNew) state.onNew(fresh.map((x) => ({ ...x, link: linkOf(x) })));
  } catch {
    /* the server is not answering: the next check tries again */
  } finally {
    state.busy = false;
  }
}
