// Starts the workspace page: who is acting, the header, and the page the URL names.
import { api, person, actAs, writeCount } from "./api.js";
import { readRoute, href } from "./lib.js";
import { header, chooser, toast, loadingCard, errorCard } from "./shell.js";
import * as home from "./pages/home.js";
import * as companies from "./pages/companies.js";
import * as outreach from "./pages/outreach.js";
import * as qualification from "./pages/qualification.js";
import * as sources from "./pages/sources.js";
import * as agents from "./pages/agents.js";
import * as team from "./pages/team.js";
import * as settings from "./pages/settings.js";
import { dock } from "./shell.js";
import * as updates from "./updates.js";

const PAGES = {
  home,
  companies,
  outreach,
  qualification,
  sources,
  agents,
  settings,
};

const el = {
  header: document.getElementById("top"),
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

function drawHeader() {
  el.header.innerHTML = String(header({ page: ctx.route.page, counts: ctx.counts, config: ctx.config, users: ctx.users, me: ctx.me, crm: ctx.crm,
    signedIn: ctx.signedIn, self: ctx.self, admin: ctx.admin }));
}

// Counts in the navigation: items waiting for a person. Only pages that exist get a count.
export async function refreshCounts() {
  try {
    const me = encodeURIComponent(ctx.me);
    const [cands, review, inbox, identified, qual, running, overview, direction, lessons] = await Promise.all([
      api.get("/candidates?status=new"), api.get("/opportunities?stage=review"),
      api.get(`/inbox?owner=${me}`), api.get(`/opportunities?stage=identified&stage=contacted&stage=nurture&owner=${me}`),
      api.get("/opportunities?stage=active&stage=approval"), api.get("/agents/jobs?status=running&limit=20"), api.get("/agents"),
      api.get("/direction"), api.get("/lessons"),
    ]);
    // Each badge equals what its page shows when opened.
    ctx.counts = {
      companies: cands.length + review.length,
      outreach: outreach.waitingFor(identified, inbox),
      qualification: qualification.waitingFor(ctx.me, qual).length,
      agents: team.suggestionCount(inbox, direction, lessons),
    };
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
  drawHeader();
  drawDock();
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
  if (fresh.length > shownNow.length) toast(`${fresh.length - shownNow.length} more in your notifications (the bell, bottom right).`);
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
let shown = null; // the page and tab on screen, and the data drawn there

// Picking another item on the page you are on draws it at once when the page already holds its data
// (page.instant(ctx, params, data on screen)); the full load then fills in what was still being checked
// (page.slots: parts that are replaced in place) and draws again only if something else changed meanwhile.
// Without an instant view the page stays as it is, not clickable, until the next item is ready. No skeleton either way.
async function show({ quiet = false } = {}) {
  const seq = ++showSeq;
  ctx.route = readRoute();
  const page = PAGES[ctx.route.page] || PAGES.home;
  document.title = `${page.title || "Home"} | CGS Lead Workspace`;
  drawHeader();
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
  refreshCounts();
}

function draw(page, data, { scroll, used, quiet }) {
  el.main.inert = false;
  if (ctx.scrollTop) scroll = 0;
  ctx.scrollTop = false;
  el.main.innerHTML = String(page.view(data, ctx, ctx.route.params));
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
    const page = PAGES[ctx.route.page] || PAGES.home;
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
["click", "change", "input", "submit", "keydown"].forEach(delegate);

document.addEventListener("click", (event) => {
  const t = event.target.closest("[data-shell]");
  if (!t) return;
  const what = t.dataset.shell;
  if (what === "retry") show();
  if (what === "skip") el.main.focus();
  if (what === "choose") { person.set(t.dataset.name); actAs.clear(); start(); }
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
    drawHeader();
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
