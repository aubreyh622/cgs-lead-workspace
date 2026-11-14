// Starts the workspace page: who is acting, the side rail, and the page the URL names.
import { api, person, actAs, writeCount } from "./api.js";
import { readRoute, href, ROUTE_ALIASES } from "./lib.js";
import { rail, railClosed, setRailClosed, chooser, toast, loadingCard, errorCard, problems } from "./shell.js";
import { initSearch, openSearch, forgetSearch } from "./search.js";
import * as home from "./pages/home.js";
import * as inbox from "./pages/inbox.js";
import * as companies from "./pages/companies.js";
import * as outreach from "./pages/outreach.js";
import * as qualification from "./pages/qualification.js";
import * as sources from "./pages/sources.js";
import * as agents from "./pages/agents.js";
import * as team from "./pages/team.js";
import * as settings from "./pages/settings.js";
import * as library from "./pages/library.js";
import * as connectors from "./pages/connectors.js";
import * as record from "./pages/record.js";
import { dock } from "./shell.js";
import * as updates from "./updates.js";

// Routes. Old names (#/home, #/sources) are aliases (lib.js ROUTE_ALIASES) and are rewritten in the URL.
const PAGES = {
  overview: home,
  inbox,
  find: sources,
  companies,
  outreach,
  qualification,
  agents,
  library,
  connectors,
  settings,
  record,  // a company's full record (#/record?id=…); not in the rail
};

const el = {
  rail: document.getElementById("rail"),
  main: document.getElementById("main"),
  dock: document.getElementById("dock"),
};

const ctx = {
  me: "",      // the person this page acts as: the signed-in person, or whom an admin acts as
  self: "",    // the person signed in
  admin: false,
  users: [],
  config: null,
  today: "",
  counts: {},
  route: readRoute(),
  api,
  // A notice on screen; it also joins the notification history (updates.js).
  toast: (message, opts = {}) => { toast(message, opts); updates.record(message, opts.link); },
  href,
  // Reload the current page's data and draw it again. Pages keep their own open forms in module state.
  refresh: () => show({ quiet: true }),
  go: (page, params) => {
    const next = href(page, params);
    if (location.hash === next) show({ quiet: true }); // same URL: no hashchange event, so draw again here
    else location.hash = next;
  },
  // Set the URL without reloading data (for selections and tabs the page already has).
  replace: (page, params) => { history.replaceState(null, "", href(page, params)); ctx.route = readRoute(); },
  focusAfter: null,
  scrollTop: false,
};

// The side rail. Drawn again after every count refresh; a focused rail control keeps its focus.
function drawRail() {
  const focused = document.activeElement && el.rail.contains(document.activeElement) ? document.activeElement.dataset.shell : null;
  el.rail.classList.toggle("closed", railClosed());
  el.rail.innerHTML = String(rail({ page: ctx.route.page, inboxCount: ctx.counts.inbox || 0, config: ctx.config, users: ctx.users, me: ctx.me,
    crm: ctx.crm, signedIn: ctx.signedIn, self: ctx.self, admin: ctx.admin, closed: railClosed() }));
  if (focused) { const again = el.rail.querySelector(`[data-shell="${focused}"]`); if (again) again.focus(); }
}

// The Inbox badge is GET /api/todo's count: exactly the rows the Inbox page lists. The other pages carry their counts
// on their own tabs.
// Plain navigation reuses counts read in the last 15 seconds (they cost seven requests); after a write, a background
// update or a quick action they are read again at once (fresh), so a number never lags what the person just did.
let countsAt = 0;
export async function refreshCounts({ fresh = true } = {}) {
  if (!fresh && Date.now() - countsAt < 15000) { drawRail(); drawProblems(); return; }
  countsAt = Date.now();
  try {
    const me = encodeURIComponent(ctx.me);
    const [todo, inbox, running, overview, direction, lessons] = await Promise.all([
      api.get("/todo"), api.get(`/inbox?owner=${me}`), api.get("/agents/jobs?status=running&limit=20"), api.get("/agents"),
      api.get("/direction"), api.get("/lessons"),
    ]);
    ctx.counts = { inbox: todo.count };
    ctx.health = { health: todo.health || [], rows: ((todo.groups || []).find((g) => g.id === "problem") || {}).items || [],
      ai: { used: overview.aiCallsToday, budget: overview.aiBudget } };
    // The dock counts agent work waiting for this person: the sum of the agent rows its panel lists.
    ctx.dock = { running: running.length + ((overview.research && overview.research.running.length) || 0), queued: overview.queued, paused: overview.paused,
      waiting: team.AGENTS.reduce((n, a) => n + team.agentWaiting(a, inbox, direction, lessons), 0) };
  } catch {
    ctx.counts = {};
  }
  try {
    ctx.crm = await api.get("/crm/state");
  } catch {
    ctx.crm = null; // the header falls back to the setting in /api/config
  }
  drawRail();
  drawDock();
  drawProblems();
}

// The problem banners at the top of the page (shell.js problems), from the last GET /api/todo. Drawn again only when
// their text changes, so a screen reader does not hear them again on every refresh.
const LATER = "cgs-problem-later";
function laterSet() {
  try { return JSON.parse(localStorage.getItem(LATER) || "{}"); } catch { return {}; }
}
let problemsShown = "";
function problemsHtml() {
  const h = ctx.health || {};
  problemsShown = String(problems({ page: ctx.route.page, health: h.health, rows: h.rows, ai: h.ai, dismissed: laterSet(), today: ctx.today }));
  return problemsShown;
}
// Under the page's title, as in the design (or at the top when a page has no title row).
function placeProblems() {
  const box = document.createElement("div");
  box.id = "problems";
  box.className = "problems";
  box.innerHTML = problemsHtml();
  const head = el.main.querySelector(":scope > .page-head");
  if (head) head.after(box); else el.main.prepend(box);
}
function drawProblems() {
  const box = el.main.querySelector(":scope > #problems");
  if (!box) return;
  const before = problemsShown;
  const next = problemsHtml();
  if (next !== before) box.innerHTML = next;
}
ctx.refreshCounts = refreshCounts;

// The agent dock (DESIGN.md: bottom right, under the toasts): how many agents are working and how much waits for
// this person; opened, what each agent is doing now.
let dockOpen = "";      // "" | "agents" | "notes"
let dockData = null;
let notesSeenBefore = ""; // items newer than this were unread when the history opened
function drawDock() {
  const list = updates.items().map((x) => ({ ...x, isNew: dockOpen === "notes" && x.at > notesSeenBefore }));
  el.dock.innerHTML = ctx.me && ctx.dock ? String(dock(ctx.dock, dockOpen, dockData, ctx.me, { list, unread: updates.unread() })) : "";
}
document.addEventListener("click", async (event) => {
  if (event.target.closest("[data-shell=note-link]")) { // the link navigates (or not, when it is this page); the panel closes
    dockOpen = "";
    setTimeout(drawDock, 0);
    return;
  }
  const t = event.target.closest("[data-shell=dock], [data-shell=notes]");
  if (!t) return;
  const which = t.dataset.shell === "notes" ? "notes" : "agents";
  dockOpen = dockOpen === which ? "" : which;
  dockData = null;
  if (dockOpen === "notes") {
    // notices are in the history and would cover it; one with Undo stays while its time lasts
    document.querySelectorAll("#toasts .toast").forEach((x) => { if (!x.querySelector(".toast-action")) x.remove(); });
    notesSeenBefore = updates.seenAt();
    updates.markRead();
  }
  drawDock();
  if (dockOpen === "agents") {
    try { dockData = await team.loadOverview(ctx); } catch { dockData = { error: true }; }
    drawDock();
  }
});

// What the agents and the workspace finished in the background (updates.js calls this after every check): show it,
// bring the counts up to date, and draw the page again when it is safe (nothing typed that would be lost).
async function onUpdates(fresh) {
  const shownNow = [...fresh].reverse().slice(-3);
  for (const x of shownNow) toast(x.text, x.link ? { link: x.link, timeout: 8000 } : { timeout: 8000 });
  if (fresh.length > shownNow.length) toast(`${fresh.length - shownNow.length} more under Activity, bottom right.`);
  await refreshCounts();
  if (dockOpen === "agents") {
    try { dockData = await team.loadOverview(ctx); } catch { /* keep what is shown */ }
    drawDock();
  }
  if (fresh.length && shown && !typing() && !unsaved()) ctx.refresh();
}

// Anything typed into the page that a redraw would lose.
function unsaved() {
  return [...el.main.querySelectorAll("input, textarea, select")].some((f) =>
    f.type === "checkbox" || f.type === "radio" ? f.checked !== f.defaultChecked
      : f.tagName === "SELECT" ? [...f.options].some((o) => o.selected !== o.defaultSelected)
      : f.value !== f.defaultValue);
}

let showSeq = 0;
let countsWrites = 0; // writeCount() when the counts were last read on a page change
let shown = null; // the page and tab on screen, and the data drawn there

// Picking another item on the page you are on draws it at once when the page already holds its data
// (page.instant(ctx, params, data on screen)); the full load then fills in what was still being checked
// (page.slots: parts that are replaced in place) and draws again only if something else changed meanwhile.
// Without an instant view the page stays as it is, not clickable, until the next item is ready. No skeleton either way.
async function show({ quiet = false } = {}) {
  const seq = ++showSeq;
  ctx.route = readRoute();
  const asked = location.hash.replace(/^#\/?/, "").split("?")[0];
  if (ROUTE_ALIASES[asked] || !PAGES[ctx.route.page]) { // an old or unknown name: show the page under its own name
    if (!PAGES[ctx.route.page]) ctx.route.page = "overview";
    history.replaceState(null, "", href(ctx.route.page, ctx.route.params));
  }
  const page = PAGES[ctx.route.page];
  document.title = `${page.title || "Overview"} | CGS Lead Workspace`;
  drawRail();
  const samePage = !quiet && shown && shown.page === ctx.route.page;
  const sameTab = samePage && shown.tab === (ctx.route.params.tab || "");
  const used = samePage ? usedLink() : null;
  let early = null;
  if (samePage && page.instant && shown.writes === writeCount()) early = page.instant(ctx, ctx.route.params, shown.data);
  if (early) {
    draw(page, early, { scroll: sameTab ? window.scrollY : 0, used, quiet });
  } else if (samePage) {
    // Until the next view is drawn nothing old can be acted on: the old item's details, and on another tab the old list too.
    if (sameTab) el.main.querySelectorAll(".detail").forEach((d) => { d.inert = true; });
    else el.main.inert = true;
  } else if (!quiet) {
    el.main.innerHTML = String(loadingCard(4));
  }
  let data;
  try {
    data = await page.load(ctx, ctx.route.params);
  } catch (err) {
    if (seq !== showSeq) return;
    shown = null;
    el.main.inert = false;
    el.main.innerHTML = String(errorCard(err));
    return;
  }
  if (seq !== showSeq) return; // a newer navigation started while this one loaded
  if (early) {
    if (page.slots) patchSlots(page.slots(data, ctx));
    if (page.changed && page.changed(early, data) && !typing()) draw(page, data, { scroll: window.scrollY, used: usedLink(), quiet: true });
    else shown.data = data;
  } else {
    const scroll = (quiet || sameTab) && !ctx.scrollTop ? window.scrollY : 0;
    draw(page, data, { scroll, used, quiet });
  }
  if (page.after) page.after(el.main, ctx, data);
  if (quiet) forgetSearch(); // something may have changed: the next search reads the records again
  refreshCounts({ fresh: quiet || writeCount() !== countsWrites });
  countsWrites = writeCount();
}

function draw(page, data, { scroll, used, quiet }) {
  el.main.inert = false;
  if (ctx.scrollTop) scroll = 0;
  ctx.scrollTop = false;
  const hadPanel = !!el.main.querySelector(":scope > .drawer");
  el.main.innerHTML = String(page.view(data, ctx, ctx.route.params));
  placeProblems();
  // The side panel (shell.js drawer) covers the page: what is behind it cannot be reached by keyboard either, and a
  // panel that was already open does not slide in again on a redraw.
  const panel = el.main.querySelector(":scope > .drawer");
  if (panel) {
    for (const x of el.main.children) {
      if (x === panel) continue;
      if (x.classList.contains("drawer-scrim")) { if (hadPanel) x.classList.add("still"); } else x.inert = true;
    }
    if (hadPanel) panel.classList.add("still");
  }
  shown = { page: ctx.route.page, tab: ctx.route.params.tab || "", data, writes: writeCount() };
  window.scrollTo(0, scroll);
  // Picking another item on the same page: focus stays on the link that was used, not the top of the page.
  const back = used ? [...el.main.querySelectorAll("a[href]")].find((a) => a.getAttribute("href") === used) : null;
  if (back) back.focus({ preventScroll: true });
  else if (!quiet) el.main.focus({ preventScroll: true });
  if (ctx.focusAfter) {
    const target = el.main.querySelector(ctx.focusAfter);
    ctx.focusAfter = null;
    if (target) target.focus();
  }
  // Opened from a link or a bookmark: focus goes into the panel, so Escape and J/K work at once.
  if (panel && !quiet && !panel.contains(document.activeElement)) {
    const h = panel.querySelector("#panel-h");
    if (h) h.focus({ preventScroll: true });
  }
}

function usedLink() {
  const a = document.activeElement;
  return a && el.main.contains(a) ? a.getAttribute("href") : null;
}

function typing() {
  const a = document.activeElement;
  return !!a && el.main.contains(a) && a.matches("input, textarea, select");
}

function patchSlots(parts) {
  for (const [name, part] of Object.entries(parts || {})) {
    const slot = el.main.querySelector(`[data-slot="${name}"]`);
    if (slot) slot.innerHTML = String(part);
  }
}

// One listener per event type; pages name their handlers with data-act="name".
function delegate(type) {
  el.main.addEventListener(type, (event) => {
    const page = PAGES[ctx.route.page] || PAGES.overview;
    // The nearest element with data-act that listens for this event type (data-on, default "click").
    let target = event.target.closest("[data-act]");
    while (target && !(target.dataset.on || "click").split(" ").includes(type)) {
      target = target.parentElement && target.parentElement.closest("[data-act]");
    }
    if (!target || !el.main.contains(target)) return;
    const fn = page.actions && page.actions[target.dataset.act];
    if (!fn) return;
    if (type === "submit") event.preventDefault();
    fn(target, event, ctx);
  });
}
// mouseover/focusin and mouseout/focusout: a row that highlights what it points to (Qualification, the reply's phrases).
["click", "change", "input", "submit", "keydown", "mouseover", "mouseout", "focusin", "focusout"].forEach(delegate);

document.addEventListener("click", (event) => {
  const t = event.target.closest("[data-shell]");
  if (!t) return;
  const what = t.dataset.shell;
  if (what === "retry") show();
  if (what === "skip") el.main.focus();
  if (what === "choose") { person.set(t.dataset.name); actAs.clear(); start(); }
  if (what === "rail-toggle") { setRailClosed(!railClosed()); drawRail(); }
  if (what === "search") openSearch(api);
  if (what === "problem-later") { // "Not now" on the AI-limit banner: hidden for the rest of today in this browser
    const later = laterSet();
    later[t.dataset.key] = ctx.today;
    try { localStorage.setItem(LATER, JSON.stringify(later)); } catch { /* private window: it shows again next time */ }
    drawProblems();
    el.main.focus({ preventScroll: true });
  }
  if (what === "act-as" && ctx.admin) switchTo(t.dataset.name);  // Settings, People: an admin acts as this person
});
// Only an admin sees the "Acting as" list; the server refuses anyone else's X-Act-As as well.
function switchTo(name) {
  if (name === ctx.self) actAs.clear(); else actAs.set(name);
  ctx.me = name;
  updates.start(api, ctx.me, onUpdates); // each person has their own notifications
  ctx.toast(name === ctx.self ? `You are acting as yourself again (${name}).`
    : `You are now acting as ${name}. Changes record "${ctx.self} as ${name}", and email goes out as ${name}.`);
  show();
}
document.addEventListener("change", (event) => {
  const t = event.target.closest("[data-shell=person]");
  if (!t) return;
  switchTo(t.value);
});

window.addEventListener("hashchange", () => { dockOpen = ""; show(); });
initSearch(api);

async function start() {
  let users = [];
  let login = { login: "off" };
  try {
    [ctx.config, users, login] = await Promise.all([api.get("/config"), api.get("/users"), api.get("/me")]);
    const agents = await api.get("/agents");
    ctx.today = agents.today;
  } catch (err) {
    el.main.innerHTML = String(errorCard(err));
    return;
  }
  ctx.users = users;
  // Google sign-in (a hosted workspace): the server knows who this is, so nobody picks a name.
  ctx.signedIn = login.login === "google" ? login.email : "";
  if (ctx.signedIn && login.name) person.set(login.name);
  const saved = person.get();
  if (!saved || !users.includes(saved)) {
    if (saved) person.clear();
    actAs.clear();
    ctx.me = ctx.self = "";
    drawRail();
    el.main.innerHTML = String(chooser(users, saved ? `${saved} is not a workspace user any more. Choose again.` : ""));
    return;
  }
  if (login.name !== saved) { // /me answered before the name was stored: ask again as the signed-in person
    try { login = await api.get("/me"); } catch { /* keep the first answer */ }
  }
  ctx.self = saved;
  ctx.admin = !!login.admin;
  const as = actAs.get();
  if (as && (!ctx.admin || !users.includes(as) || as === saved)) actAs.clear();
  ctx.me = actAs.get() || saved;
  show();
  updates.start(api, ctx.me, onUpdates);
}

start();
