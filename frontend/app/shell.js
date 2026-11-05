// The frame around every page: header with navigation and counts, the person picker, toasts.
import { html, icon, href, initials, plural, safeUrl } from "./lib.js";
import { AGENTS, agentState } from "./pages/team.js";

export const NAV = [
  ["home", "Home", "home"],
  ["companies", "Companies", "companies"],
  ["outreach", "Outreach", "outreach"],
  ["qualification", "Qualification", "qualification"],
  ["agents", "Agents", "agents"],
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
  const link = href("agents", { tab: "controls" });
  // On a narrow window only the name and the dot show, unless something failed or waits; the title keeps the full text.
  const chip = (dot, detail, title, keep = false) => html`<a class="crm-status" href="${link}" title="${name}${detail}. ${title}">
    <span class="with-dot ${dot}"><span>${name}<span class="crm-detail ${keep ? "keep" : ""}">${detail}</span></span></span></a>`;
  if (!provider || provider === "off" || mode === "disabled") return chip("", ": off", "The workspace does not read or write a CRM.");
  if (!state || !state.writes) return chip("", ": read only", "The workspace checks the CRM for duplicates but writes nothing.");
  if (state.failed) return chip("fail", `: ${state.failed} not synced`, "A sync failed. Open the controls to see why and try again.", true);
  if (state.conflicts) return chip("wait", `: ${state.conflicts} to check`, "Someone changed a lead in the CRM and the workspace says something else. A person decides on each opportunity's page.", true);
  if (state.pending) return chip("wait", `: ${state.pending} waiting to sync`, "These changes go to the CRM after the request that made them, or at the daily sync.", true);
  if (state.lastSyncedAt) return chip("done", ` synced ${syncedWhen(state.lastSyncedAt)}`, "Every accepted lead is in step with the CRM.");
  return chip("", ": nothing synced yet", "Leads go to the CRM once a person accepts a company at Step 4.");
}

export function header({ page, counts, config, users, me, crm }) {
  return html`
  <div class="top-in">
    <a class="brand" href="${href("home")}">
      <span class="brand-mark" aria-hidden="true">C</span>
      <span class="brand-text"><b>Lead Workspace</b><span>CGS Advisors</span></span>
    </a>
    <nav class="nav" aria-label="Main">
      ${NAV.map(([id, label, ic]) => html`
        <a href="${href(id)}" ${page === id || (id === "companies" && page === "sources") ? html`aria-current="page"` : ""}>${icon(ic)}${label}${counts[id] ? html`<span class="count" aria-label="${counts[id]} waiting">${counts[id]}</span>` : ""}</a>`)}
    </nav>
    <div class="top-side">
      ${crmStatus(config, crm)}
      ${modeBadge(config)}
      ${me ? html`
      <span class="avatar round" aria-hidden="true">${initials(me)}</span>
      <label class="person"><span class="person-label sr-only">Acting as</span>
        <select data-shell="person" aria-label="Acting as">
          ${users.map((u) => html`<option value="${u}" ${u === me ? "selected" : ""}>${u}</option>`)}
        </select>
      </label>` : ""}
    </div>
  </div>`;
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
    <h1>Who is using the workspace?</h1>
    <p class="meta" style="margin-top:8px">There is no sign-in yet. Every change you make records the name you choose here.</p>
    ${problem ? html`<div class="notice bad" role="alert" style="margin-top:16px">${icon("alert")}<span>${problem}</span></div>` : ""}
    <div class="people">
      ${users.map((u) => html`<button type="button" class="btn" data-shell="choose" data-name="${u}" style="justify-content:flex-start;height:48px">
        <span class="avatar" aria-hidden="true">${initials(u)}</span>${u}</button>`)}
    </div>
  </div>`;
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
  <section class="dock-panel notes-panel" aria-label="Notifications">
    <div class="dock-head"><b>Notifications</b><span class="meta-3">The past week</span></div>
    ${list.length ? html`<ul>${list.slice(0, 60).map((x) => html`<li class="${x.isNew ? "unread" : ""}">
      ${x.link ? html`<a href="${x.link.href}" data-shell="note-link">` : html`<div class="note-row">`}
        <span class="row-flex" style="gap:10px;flex-wrap:nowrap"><span class="note-text">${x.text}</span><span class="meta-3" style="margin-left:auto;white-space:nowrap">${when(x.at)}</span></span>
        <span class="meta-3">${x.agent || (x.kind === "local" ? "You" : "Workspace")}${x.link ? html`. ${x.link.label}` : ""}</span>
      ${x.link ? html`</a>` : html`</div>`}</li>`)}</ul>`
      : html`<p class="meta" style="padding:12px 18px">Nothing yet. What the agents finish and what you do shows here for a week.</p>`}
  </section>`;
}

// ---------- the agent dock ----------
// open: "" | "agents" | "notes". notes: { list, unread } for the bell.
export function dock(state, open, data, me, notes = { list: [], unread: 0 }) {
  const summary = state.paused ? "Agents paused" : state.running ? `${plural(state.running, "agent job")} running` : state.queued ? `${plural(state.queued, "job")} queued` : "Agents idle";
  return html`
  ${open === "notes" ? notesPanel(notes.list) : ""}
  ${open === "agents" ? html`
  <section class="dock-panel" aria-label="Agents now">
    <div class="dock-head"><b>Agents now</b><a href="${href("agents")}">All agents</a></div>
    ${!data ? html`<p class="meta" style="padding:12px 18px">Loading…</p>`
      : data.error ? html`<p class="meta" style="padding:12px 18px">The agents could not be loaded.</p>`
      : html`<ul>${AGENTS.map((a) => {
        const s = agentState(a, data, me);
        return html`<li><a href="${href("agents", { agent: a.slug })}"><span class="row-flex" style="gap:10px;flex-wrap:nowrap"><b class="with-dot ${s.dot}">${a.name}</b><span class="meta-3" style="margin-left:auto">${s.label}</span></span>
          <span class="meta dock-now">${s.now}</span></a></li>`;
      })}</ul>`}
  </section>` : ""}
  <div class="dock-row">
    <button type="button" class="dock-bell" data-shell="notes" aria-expanded="${open === "notes" ? "true" : "false"}"
      aria-label="${notes.unread ? `Notifications, ${notes.unread} new` : "Notifications"}">${icon("bell", 18)}${notes.unread ? html`<span class="bell-count" aria-hidden="true">${notes.unread > 99 ? "99+" : notes.unread}</span>` : ""}</button>
    <button type="button" class="dock-pill" data-shell="dock" aria-expanded="${open === "agents" ? "true" : "false"}">
      <span class="with-dot ${state.running ? "run" : state.paused ? "" : "done"}"><span><b>${summary}</b>${state.waiting ? `, ${state.waiting} from agents waiting for you` : ""}</span></span>
    </button>
  </div>`;
}
