// The frame around every page: the side rail (navigation, search, the person acting, CRM sync), toasts, and the
// agents pill and Activity drawer at the bottom right.
import { html, icon, href, initials, plural, safeUrl } from "./lib.js";
import { AGENTS, agentState } from "./pages/team.js";

// The side rail (design/redesign/Rail.dc.html): [page, label, icon].
export const NAV = [
  { label: "Work", items: [
    ["overview", "Overview", "overview"],
    ["inbox", "Inbox", "inbox"],
    ["find", "Find", "search"],
    ["companies", "Companies", "building"],
    ["outreach", "Outreach", "outreach"],
    ["qualification", "Qualification", "target"],
  ] },
  { label: "Agents", items: [
    ["agents", "Agent hub", "sparkle"],
    ["library", "Library", "fileCheck"],
    ["connectors", "Connectors", "link"],
  ] },
];

function modeBadge(config) {
  if (!config) return "";
  if (config.demoData) {
    const title = config.appMode === "demo"
      ? "Demo mode: invented companies, and text rules instead of AI."
      : "Live AI on invented companies (the synthetic internet).";
    return html`<span class="mode" title="${title}">Synthetic data</span>`;
  }
  return html`<span class="mode live" title="Real public companies. Every email and CRM write still needs a person's click.">Live data</span>`;
}

// The rail closes by itself on windows narrower than 1280px until the person opens it; their choice is remembered.
const RAIL_KEY = "cgs-rail-collapsed";
export function railClosed() {
  try {
    const v = localStorage.getItem(RAIL_KEY);
    if (v === "1" || v === "0") return v === "1";
  } catch { /* storage blocked: fall back to the window width */ }
  return window.innerWidth < 1280;
}
export function setRailClosed(closed) {
  try { localStorage.setItem(RAIL_KEY, closed ? "1" : "0"); } catch { /* not remembered */ }
}

function railLink(id, label, ic, page, badge = 0) {
  const name = badge ? `${label}, ${badge} need you` : label;
  return html`<a class="rail-link" href="${href(id)}" title="${label}" aria-label="${name}" ${page === id ? html`aria-current="page"` : ""}>
    ${icon(ic, 16)}<span class="label">${label}</span>${badge ? html`<span class="badge" aria-hidden="true">${badge}</span><span class="pip" aria-hidden="true"></span>` : ""}</a>`;
}

// inboxCount is GET /api/todo's count: the number the Inbox page lists under All.
export function rail({ page, inboxCount, config, users, me, crm, signedIn, self, admin, closed }) {
  const other = me && self && me !== self;
  const who = signedIn ? `Signed in with Google as ${signedIn}` : `Signed in as ${self}`;
  const sub = other ? `${self} acting as` : admin ? "Admin" : "Member";
  return html`
  <div class="rail-head">
    <a class="brand" href="${href("overview")}" aria-label="CGS Lead Workspace, Overview">
      <span class="brand-mark" aria-hidden="true"><span class="brand-tiles"><span></span><span></span><span></span><span></span></span></span>
      <span class="brand-text"><b>CGS</b><span>Lead Workspace</span></span>
    </a>
    <button type="button" class="rail-toggle" data-shell="rail-toggle" aria-label="${closed ? "Expand navigation" : "Collapse navigation"}"
      title="${closed ? "Expand navigation" : "Collapse navigation"}">${icon(closed ? "expand" : "collapse", 16)}</button>
  </div>
  <button type="button" class="rail-search" data-shell="search" aria-label="Search companies, people and pages" title="Search (Ctrl K)">
    ${icon("search", 16)}<span class="label">Search</span><span class="keys">Ctrl K</span></button>
  ${NAV.map((g) => html`<div class="rail-group">
    <span class="rail-label" aria-hidden="true">${g.label}</span>
    ${g.items.map(([id, label, ic]) => railLink(id, label, ic, page, id === "inbox" ? inboxCount : 0))}
  </div>`)}
  <div class="rail-foot">
    ${modeBadge(config)}
    ${crmStatus(config, crm)}
    ${railLink("settings", "Settings", "sliders", page)}
    ${me ? (admin ? html`
    <div class="user ${other ? "acting" : ""}" title="${who}. As an admin you may act as someone else.">
      <span class="avatar round ${other ? "acting" : ""}" aria-hidden="true">${initials(me)}</span>
      <label class="user-text"><span class="sub">${sub}</span>
        <select data-shell="person" aria-label="Acting as">
          ${users.map((u) => html`<option value="${u}" ${u === me ? "selected" : ""}>${u === self ? u : `As ${u}`}</option>`)}
        </select></label>
    </div>`
    : html`<a class="user" href="${href("settings")}" title="${who}. People and roles" aria-label="${me}, people and roles">
      <span class="avatar round" aria-hidden="true">${initials(me)}</span>
      <span class="user-text"><b>${me}</b><span class="sub">${sub}</span></span></a>`) : ""}
  </div>`;
}

// When the workspace last synced, from its own records: a time today, otherwise a date.
function syncedWhen(iso) {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  const sameDay = t.toDateString() === new Date().toDateString();
  return sameDay ? t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : t.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

// The CRM sync state (GET /api/crm/state, read from the workspace database; the CRM itself is not called). "Connected"
// is not claimed: it says what is switched on, when the last sync happened, and what is waiting or failed.
export function crmStatus(config, state) {
  if (!config) return "";
  const provider = state ? state.provider : config.crm;
  const mode = state ? state.mode : config.crmMode;
  const name = provider === "insightly" ? "Insightly" : provider === "mock" ? "Test CRM" : "CRM";
  const link = href("connectors");
  // One line in the rail: a dot and the state; the collapsed rail keeps the dot, and the title and name keep the full text.
  const chip = (dot, detail, title) => html`<a class="crm-status" href="${link}" title="${name}${detail}. ${title}" aria-label="${name}${detail}">
    <span class="dot ${dot}" aria-hidden="true"></span><span class="crm-text">${name}${detail}</span></a>`;
  if (!provider || provider === "off" || mode === "disabled") return chip("", ": off", "The workspace does not read or write a CRM.");
  if (!state || !state.writes) return chip("", ": read only", "The workspace checks the CRM for duplicates but writes nothing.");
  if (state.failed) return chip("fail", `: ${state.failed} not synced`, "A sync failed. Open the controls to see why and try again.");
  if (state.conflicts) return chip("wait", `: ${state.conflicts} to check`, "Someone changed a lead in the CRM and the workspace says something else. A person decides on each opportunity's page.");
  if (state.pending) return chip("wait", `: ${state.pending} waiting to sync`, "These changes go to the CRM after the request that made them, or at the daily sync.");
  if (state.lastSyncedAt) return chip("done", ` synced ${syncedWhen(state.lastSyncedAt)}`, "Every accepted lead is in step with the CRM.");
  return chip("", ": nothing synced yet", "Leads go to the CRM once a person accepts a company at Step 4.");
}

// Links to the records the CRM sync made for an opportunity (o.crm from the API). Only http(s) links are shown, so the
// test CRM's mock:// addresses never become links.
export function crmLinks(crm) {
  if (!crm) return "";
  const where = crm.provider === "insightly" ? "Insightly" : "the CRM";
  const links = [[crm.leadUrl, `Lead in ${where}`], [crm.opportunityUrl, `Opportunity in ${where}`]]
    .map(([u, text]) => [safeUrl(u), text]).filter(([u]) => u);
  const deal = crm.dealStage || crm.dealState
    ? `${where === "the CRM" ? "CRM" : where}: ${crm.dealState && crm.dealState !== "OPEN" ? crm.dealState.toLowerCase() : crm.dealStage}` : "";
  return html`${links.map(([u, text]) => html`<a class="tag link-tag" href="${u}" target="_blank" rel="noopener noreferrer">${icon("link", 14)}${text}</a>`)}
    ${deal ? html`<span class="tag ${crm.dealState === "WON" ? "good" : crm.dealState && crm.dealState !== "OPEN" ? "bad" : ""}" title="The CRM opportunity's stage at the last read-back">${deal}</span>` : ""}
    ${crm.lastError ? html`<span class="tag bad" title="${crm.lastError}">CRM sync failed</span>` : ""}`;
}

// Fields someone changed in the CRM that differ from the workspace: the CRM keeps its value until a person decides.
// Pages add actions.crmResolve = resolveCrm.
export function crmConflicts(oppId, crm) {
  const list = (crm && crm.conflicts) || [];
  if (!list.length) return "";
  const where = crm.provider === "insightly" ? "Insightly" : "the CRM";
  const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
  return html`<div class="notice warn crm-conflicts" role="status">${icon("alert")}<div style="flex:1;min-width:0">
    <b>Changed in ${where}: ${list.length === 1 ? "one field differs" : `${list.length} fields differ`} from the workspace</b>
    <p class="meta">${cap(where)} keeps its value until you decide. Nothing is overwritten.</p>
    <ul class="conflict-list">${list.map((x) => html`<li>
      <span class="what"><b>${cap(x.label)}</b>
        <span class="meta">${cap(where)}: ${x.crm || "(empty)"}</span>
        <span class="meta">Workspace: ${x.workspace}</span></span>
      <span class="actions">
        <button type="button" class="btn small" data-act="crmResolve" data-id="${oppId}" data-field="${x.field}" data-use="crm">Keep ${where}'s</button>
        <button type="button" class="btn small" data-act="crmResolve" data-id="${oppId}" data-field="${x.field}" data-use="workspace">Use the workspace's</button>
      </span></li>`)}</ul>
  </div></div>`;
}

export async function resolveCrm(t, e, ctx) {
  t.disabled = true;
  try {
    await ctx.api.post(`/opportunities/${encodeURIComponent(t.dataset.id)}/crm/resolve`, { field: t.dataset.field, use: t.dataset.use });
    ctx.refresh();
  } catch (err) {
    t.disabled = false;
    toast(err.message || "That did not work.");
  }
}

export function chooser(users, problem) {
  return html`
  <div class="chooser card">
    <h1>Sign in</h1>
    <p class="meta" style="margin-top:8px">Choose your name. Every change you make records it. This copy runs on one computer and has no
      passwords; the hosted workspace signs people in with their Google account. Admins can then act as someone else.</p>
    ${problem ? html`<div class="notice bad" role="alert" style="margin-top:16px">${icon("alert")}<span>${problem}</span></div>` : ""}
    <div class="people">
      ${users.map((u) => html`<button type="button" class="btn" data-shell="choose" data-name="${u}" style="justify-content:flex-start;height:48px">
        <span class="avatar" aria-hidden="true">${initials(u)}</span>${u}</button>`)}
    </div>
  </div>`;
}

// ---------- the side panel for list stages (Outreach, Qualification) ----------
// The page implements actions.openRow (focus the panel heading after navigating), closePanel and panelKeys
// (Escape closes, J and K move). prev / next: { href, id } of the neighbouring rows.
// record: the opportunity's id, for "Open full record" (pages/record.js); a candidate has none.
export function drawer({ name, sub = "", stage = "", tone = "", owner = "", pos = "", prev, next, blocks, foot, record }) {
  return html`
  <button type="button" class="drawer-scrim" tabindex="-1" aria-label="Close panel" data-act="closePanel"></button>
  <aside class="drawer" aria-labelledby="panel-h" data-act="panelKeys" data-on="keydown">
    <header class="ph">
      <div class="row-flex" style="flex-wrap:nowrap">
        <button type="button" class="btn icon-only" data-act="closePanel" aria-label="Close panel" title="Close (Esc)">${icon("close", 16)}</button>
        <span class="avatar" aria-hidden="true">${initials(name)}</span>
        <div style="flex:1;min-width:0"><h2 id="panel-h" tabindex="-1">${name}</h2><p class="meta">${sub}</p></div>
        ${prev ? html`<a class="btn ghost icon-only" href="${prev.href}" data-act="openRow" data-row="${prev.id}" aria-label="Previous company" title="Previous (K)">${icon("chevron", 16)}</a>` : ""}
        ${next ? html`<a class="btn ghost icon-only next" href="${next.href}" data-act="openRow" data-row="${next.id}" aria-label="Next company" title="Next (J)">${icon("chevron", 16)}</a>` : ""}
      </div>
      <div class="row-flex">${stage ? html`<span class="tag ${tone}">${stage}</span>` : ""}<span class="meta">${owner}</span>
        ${record ? html`<a class="meta" href="${href("record", { id: record })}">Open full record</a>` : ""}<span class="meta-3" style="margin-left:auto">${pos}</span></div>
    </header>
    <div class="pb">${blocks}</div>
    ${foot ? html`<footer class="pf">${foot}</footer>` : ""}
  </aside>`;
}

// Shared handlers for the side panel. params: the page's own URL values to keep when it closes (for example team).
export function panelActions(page, keep = () => ({})) {
  const close = (t, e, ctx) => {
    const { tab, id } = ctx.route.params;
    ctx.focusAfter = id ? `[data-row="${id}"].nm` : null;
    ctx.go(page, { tab, ...keep(ctx) });
  };
  return {
    openRow(t, e, ctx) { ctx.focusAfter = "#panel-h"; },
    closePanel: close,
    panelKeys(t, e, ctx) {
      if (e.target && e.target.matches && e.target.matches("input, textarea, select")) return;
      if (e.key === "Escape") { e.preventDefault(); close(t, e, ctx); return; }
      const k = (e.key || "").toLowerCase();
      if (k !== "j" && k !== "k") return;
      const link = t.querySelector(k === "j" ? "a.next" : "a[aria-label='Previous company']");
      if (link) { e.preventDefault(); ctx.focusAfter = "#panel-h"; location.hash = link.getAttribute("href"); }
    },
  };
}

// ---------- problem banners (redesign R8) ----------
// What is wrong with the workspace itself, on the pages whose work it stops: the AI limit (health "budget"), Gmail
// signed out (health "gmail") and research that stopped (the Inbox's "Research stopped" rows). All from GET /api/todo,
// so a banner and the Inbox always agree. A healthy workspace shows nothing. Only the AI-limit warning can be put off,
// for the rest of the day; errors stay until they are fixed.
export const PROBLEM_PAGES = {
  budget: ["companies", "find", "outreach", "qualification"],
  gmail: ["outreach", "qualification", "record"],
  research: ["companies"],
};

export function problems({ page, health = [], rows = [], ai = null, dismissed = {}, today = "" }) {
  const has = (k) => health.some((h) => h.key === k);
  const out = [];
  if (has("budget") && PROBLEM_PAGES.budget.includes(page) && dismissed.budget !== today) {
    out.push({ key: "budget", tone: "warn",
      head: `Today's AI requests are used up${ai && ai.budget ? `: ${ai.used} of ${ai.budget}` : ""}`,
      body: "Research, drafting and reading replies wait until tomorrow. Nothing is lost: each piece of work picks up where it stopped. Decisions, approvals and sending still work.",
      link: [href("agents"), "Raise today's limit"], later: true });
  }
  if (has("gmail") && PROBLEM_PAGES.gmail.includes(page)) {
    out.push({ key: "gmail", tone: "bad", head: "Gmail is disconnected",
      body: "Emails cannot go out from the workspace and client replies are not being read. Plan steps that send an email wait until it is signed in again; an admin does that on the server with python -m api.toolkit.gmail_auth.",
      link: [href("connectors"), "Open Connectors"] });
  }
  // A research paused by a person or by the AI limit is not a failure (the AI-limit banner covers the second).
  const stopped = PROBLEM_PAGES.research.includes(page) ? rows.filter((r) => r.label === "Research stopped" && r.opportunity && !/^Paused/.test(r.sub)) : [];
  if (stopped.length) {
    const one = stopped.length === 1 ? stopped[0] : null;
    out.push({ key: "research", tone: "bad", head: `Research stopped on ${plural(stopped.length, "company", "companies")}`,
      body: one ? `${one.opportunity.company}: ${one.sub} The Research agent needs a person to decide what to do.`
        : `${stopped.map((r) => r.opportunity.company).join(", ")}. The Research agent needs a person to decide what to do with each.`,
      link: one ? [href("companies", { tab: "research", id: one.opportunity.id }), `Open ${one.opportunity.company}`] : [href("companies", { tab: "research" }), "Open Researching"] });
  }
  return html`${out.map((p) => html`<section class="card problem ${p.tone}" aria-labelledby="problem-${p.key}">
    ${icon(p.tone === "bad" ? "alert" : "clock", 18)}
    <div class="problem-text"><h2 id="problem-${p.key}" class="md">${p.head}</h2><p class="meta">${p.body}</p></div>
    <div class="problem-actions"><a class="btn primary small" href="${p.link[0]}">${p.link[1]}</a>
      ${p.later ? html`<button type="button" class="btn small" data-shell="problem-later" data-key="${p.key}">Not now</button>` : ""}</div>
  </section>`)}`;
}

// ---------- toasts: for background events and things not visible on screen ----------
let toastSeq = 0;

// action: { label, run } adds a button, for example Undo; it runs once, then the toast closes.
// key names what the toast is about, so dropToast(key) can close it when that is settled elsewhere.
export function toast(message, { link, action, key, timeout = 5000 } = {}) {
  const region = document.getElementById("toasts");
  if (!region) return;
  const id = `toast-${++toastSeq}`;
  const el = document.createElement("div");
  el.className = "toast";
  el.id = id;
  if (key) el.dataset.key = key;
  el.innerHTML = String(html`
    <div>${message}${link ? html` <a href="${link.href}">${link.label}</a>` : ""}</div>
    ${action ? html`<button type="button" class="toast-action" data-toast="action">${action.label}</button>` : ""}
    <button type="button" class="btn ghost" data-toast="close" aria-label="Dismiss">${icon("close", 16)}</button>`);
  el.querySelector("[data-toast=close]").addEventListener("click", () => el.remove());
  if (action) {
    el.querySelector("[data-toast=action]").addEventListener("click", (e) => {
      e.currentTarget.disabled = true;
      el.remove();
      action.run();
    });
  }
  region.appendChild(el);
  setTimeout(() => el.remove(), timeout);
}

export function dropToast(key) {
  document.querySelectorAll("#toasts .toast[data-key]").forEach((el) => { if (el.dataset.key === key) el.remove(); });
}

// ---------- shared page pieces ----------
export function loadingCard(lines = 3) {
  const widths = ["wide", "mid", "short", "wide", "mid"];
  return html`<div class="card loading-card" aria-busy="true" aria-label="Loading">
    ${Array.from({ length: lines }, (_, i) => html`<span class="skeleton ${widths[i % widths.length]}"></span>`)}
  </div>`;
}

export function errorCard(err, retryLabel = "Try again") {
  return html`<div class="card"><div class="empty" role="alert">
    <b>This could not be loaded</b>
    <span class="meta">${err && err.message ? err.message : String(err)}</span>
    <button type="button" class="btn" data-shell="retry">${icon("refresh", 16)}${retryLabel}</button>
  </div></div>`;
}

export function emptyState(title, text, action) {
  return html`<div class="empty"><b>${title}</b><span class="meta">${text}</span>
    ${action ? html`<a class="btn" href="${action.href}">${action.label}</a>` : ""}</div>`;
}

export function inlineError(message) {
  return message ? html`<div class="notice bad" role="alert">${icon("alert")}<span>${message}</span></div>` : "";
}

// ---------- notification history (frontend/app/updates.js) ----------
const when = (iso) => {
  const t = new Date(iso);
  return t.toDateString() === new Date().toDateString()
    ? t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : t.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
};

function notesPanel(list) {
  return html`
  <section class="dock-panel notes-panel" aria-label="Activity">
    <div class="dock-head"><b>Activity</b><span class="meta-3">The past week</span></div>
    ${list.length ? html`<ul>${list.slice(0, 60).map((x) => html`<li class="${x.isNew ? "unread" : ""}">
      ${x.link ? html`<a href="${x.link.href}" data-shell="note-link">` : html`<div class="note-row">`}
        <span class="row-flex" style="gap:10px;flex-wrap:nowrap"><span class="note-text">${x.text}</span><span class="meta-3" style="margin-left:auto;white-space:nowrap">${when(x.at)}</span></span>
        <span class="meta-3">${x.agent || (x.kind === "local" ? "You" : "Workspace")}${x.link ? html`. ${x.link.label}` : ""}</span>
      ${x.link ? html`</a>` : html`</div>`}</li>`)}</ul>`
      : html`<p class="meta" style="padding:12px 18px">Nothing yet. What the agents finish and what you do shows here for a week.</p>`}
  </section>`;
}

// ---------- bottom right: the agents pill and the Activity button ----------
// open: "" | "agents" | "notes". notes: { list, unread } for the Activity drawer (the notification history).
export function dock(state, open, data, me, notes = { list: [], unread: 0 }) {
  const summary = state.paused ? "Agents paused" : state.running ? `${plural(state.running, "agent job")} running` : state.queued ? `${plural(state.queued, "job")} queued` : "Agents idle";
  // Short on screen (it must not cover the page's own buttons); the full sentence is the button's name.
  const short = state.paused ? "paused" : state.running ? `${state.running} running` : state.queued ? `${state.queued} queued` : "idle";
  const name = `${summary}${state.waiting ? `, ${state.waiting} from agents waiting for you` : ""}`;
  return html`
  ${open === "notes" ? notesPanel(notes.list) : ""}
  ${open === "agents" ? html`
  <section class="dock-panel" aria-label="Agents now">
    <div class="dock-head"><b>Agents now</b><a href="${href("agents")}">Agent hub</a></div>
    ${!data ? html`<p class="meta" style="padding:12px 18px">Loading…</p>`
      : data.error ? html`<p class="meta" style="padding:12px 18px">The agents could not be loaded.</p>`
      : html`<ul>${AGENTS.map((a) => {
        const s = agentState(a, data, me);
        return html`<li><a href="${href("agents", { agent: a.slug })}"><span class="row-flex" style="gap:10px;flex-wrap:nowrap"><b class="with-dot ${s.dot}">${a.name}</b><span class="meta-3" style="margin-left:auto">${s.label}</span></span>
          <span class="meta dock-now">${s.now}</span></a></li>`;
      })}</ul>`}
  </section>` : ""}
  <div class="dock-row">
    <button type="button" class="dock-pill" data-shell="dock" aria-expanded="${open === "agents" ? "true" : "false"}" aria-label="${name}" title="${name}">
      <span class="dot ${state.running ? "run" : state.paused ? "wait" : "done"}" aria-hidden="true"></span>
      <b>Agents</b><span class="dim">${short}</span>${state.waiting ? html`<span class="badge agent-badge" aria-hidden="true">${state.waiting}</span>` : ""}
    </button>
    <button type="button" class="activity-btn" data-shell="notes" aria-expanded="${open === "notes" ? "true" : "false"}"
      aria-label="${notes.unread ? `Activity, ${notes.unread} new` : "Activity"}">${icon("bell", 16)}Activity${notes.unread ? html`<span class="new" aria-hidden="true">${notes.unread > 99 ? "99+" : notes.unread} new</span>` : ""}</button>
  </div>`;
}
