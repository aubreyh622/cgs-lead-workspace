// Starts the workspace page: who is acting, the header, and the page the URL names.
import { api, person } from "./api.js";
import { readRoute, href } from "./lib.js";
import { header, chooser, toast, loadingCard, errorCard } from "./shell.js";
import * as home from "./pages/home.js";
import * as companies from "./pages/companies.js";
import * as outreach from "./pages/outreach.js";
import * as qualification from "./pages/qualification.js";
import * as sources from "./pages/sources.js";
import * as agents from "./pages/agents.js";
import * as team from "./pages/team.js";
import { dock } from "./shell.js";

const PAGES = {
  home,
  companies,
  outreach,
  qualification,
  sources,
  agents,
};

const el = {
  header: document.getElementById("top"),
  main: document.getElementById("main"),
  dock: document.getElementById("dock"),
};

const ctx = {
  me: "",
  users: [],
  config: null,
  today: "",
  counts: {},
  route: readRoute(),
  api,
  toast,
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
  el.header.innerHTML = String(header({ page: ctx.route.page, counts: ctx.counts, config: ctx.config, users: ctx.users, me: ctx.me }));
}

// Counts in the navigation: items waiting for a person. Only pages that exist get a count.
export async function refreshCounts() {
  try {
    const me = encodeURIComponent(ctx.me);
    const [cands, review, inbox, identified, qual, running, overview] = await Promise.all([
      api.get("/candidates?status=new"), api.get("/opportunities?stage=review"),
      api.get(`/inbox?owner=${me}`), api.get(`/opportunities?stage=identified&stage=contacted&stage=nurture&owner=${me}`),
      api.get("/opportunities?stage=active&stage=approval"), api.get("/agents/jobs?status=running&limit=20"), api.get("/agents"),
    ]);
    ctx.counts = {
      companies: cands.length + review.length,
      outreach: outreach.approveItems(identified, inbox).length,
      qualification: qualification.waitingFor(ctx.me, qual).length,
      agents: agents.otherInbox(inbox).length,
    };
    ctx.dock = { running: running.length, queued: overview.queued, paused: overview.paused,
      waiting: Object.values(ctx.counts).reduce((n, x) => n + x, 0) };
  } catch {
    ctx.counts = {};
  }
  drawHeader();
  drawDock();
}
ctx.refreshCounts = refreshCounts;

// The agent dock (DESIGN.md: bottom right, under the toasts): how many agents are working and how much waits for
// this person; opened, what each agent is doing now.
let dockOpen = false;
let dockData = null;
function drawDock() {
  el.dock.innerHTML = ctx.me && ctx.dock ? String(dock(ctx.dock, dockOpen, dockData, ctx.me)) : "";
}
document.addEventListener("click", async (event) => {
  if (!event.target.closest("[data-shell=dock]")) return;
  dockOpen = !dockOpen;
  dockData = null;
  drawDock();
  if (dockOpen) {
    try { dockData = await team.loadOverview(ctx); } catch { dockData = { error: true }; }
    drawDock();
  }
});

let showSeq = 0;
async function show({ quiet = false } = {}) {
  const seq = ++showSeq;
  ctx.route = readRoute();
  const page = PAGES[ctx.route.page] || PAGES.home;
  document.title = `${page.title || "Home"} | CGS Lead Workspace`;
  drawHeader();
  if (!quiet) el.main.innerHTML = String(loadingCard(4));
  let data;
  try {
    data = await page.load(ctx, ctx.route.params);
  } catch (err) {
    if (seq !== showSeq) return;
    el.main.innerHTML = String(errorCard(err));
    return;
  }
  if (seq !== showSeq) return; // a newer navigation started while this one loaded
  const scroll = quiet && !ctx.scrollTop ? window.scrollY : 0;
  ctx.scrollTop = false;
  el.main.innerHTML = String(page.view(data, ctx, ctx.route.params));
  window.scrollTo(0, scroll);
  if (!quiet) el.main.focus({ preventScroll: true });
  if (ctx.focusAfter) {
    const target = el.main.querySelector(ctx.focusAfter);
    ctx.focusAfter = null;
    if (target) target.focus();
  }
  if (page.after) page.after(el.main, ctx, data);
  refreshCounts();
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
  if (what === "choose") { person.set(t.dataset.name); start(); }
});
document.addEventListener("change", (event) => {
  const t = event.target.closest("[data-shell=person]");
  if (!t) return;
  person.set(t.value);
  ctx.me = t.value;
  toast(`You are now acting as ${t.value}. Changes record this name.`);
  show();
});

window.addEventListener("hashchange", () => { dockOpen = false; show(); });

async function start() {
  let users = [];
  try {
    [ctx.config, users] = await Promise.all([api.get("/config"), api.get("/users")]);
    const agents = await api.get("/agents");
    ctx.today = agents.today;
  } catch (err) {
    el.main.innerHTML = String(errorCard(err));
    return;
  }
  ctx.users = users;
  const saved = person.get();
  if (!saved || !users.includes(saved)) {
    if (saved) person.clear();
    ctx.me = "";
    drawHeader();
    el.main.innerHTML = String(chooser(users, saved ? `${saved} is not a workspace user any more. Choose again.` : ""));
    return;
  }
  ctx.me = saved;
  show();
}

start();
