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

// The CRM the backend is set up with, as /api/config reports it. "Connected" is not claimed: it says what is switched on.
function crmStatus(config) {
  if (!config) return "";
  const off = !config.crm || config.crm === "off" || config.crmMode === "disabled";
  const name = config.crm === "insightly" ? "Insightly" : config.crm === "mock" ? "Test CRM" : "CRM";
  const mode = off ? "off" : config.crmMode === "write_approved" ? "writes after approval" : "read only";
  return html`<span class="crm-status" title="CRM setting from the server">${name}: ${mode}</span>`;
}

export function header({ page, counts, config, users, me }) {
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
      ${crmStatus(config)}
      ${modeBadge(config)}
      ${me ? html`
      <span class="avatar round" aria-hidden="true">${initials(me)}</span>
      <label class="person"><span class="person-label">Acting as</span>
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
  return html`${links.map(([u, text]) => html`<a class="tag link-tag" href="${u}" target="_blank" rel="noopener noreferrer">${icon("link", 14)}${text}</a>`)}
    ${crm.lastError ? html`<span class="tag bad" title="${crm.lastError}">CRM sync failed</span>` : ""}`;
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

export function toast(message, { link, timeout = 5000 } = {}) {
  const region = document.getElementById("toasts");
  if (!region) return;
  const id = `toast-${++toastSeq}`;
  const el = document.createElement("div");
  el.className = "toast";
  el.id = id;
  el.innerHTML = String(html`
    <div>${message}${link ? html` <a href="${link.href}">${link.label}</a>` : ""}</div>
    <button type="button" class="btn ghost" aria-label="Dismiss">${icon("close", 16)}</button>`);
  el.querySelector("button").addEventListener("click", () => el.remove());
  region.appendChild(el);
  setTimeout(() => el.remove(), timeout);
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

// ---------- the agent dock ----------
export function dock(state, open, data, me) {
  const summary = state.paused ? "Agents paused" : state.running ? `${plural(state.running, "agent job")} running` : state.queued ? `${plural(state.queued, "job")} queued` : "Agents idle";
  return html`
  ${open ? html`
  <section class="dock-panel" aria-label="Agents now">
    <div class="dock-head"><b>Agents now</b><a href="${href("agents")}">All agents</a></div>
    ${!data ? html`<p class="meta" style="padding:12px 18px">Loading…</p>`
      : data.error ? html`<p class="meta" style="padding:12px 18px">The agents could not be loaded.</p>`
      : html`<ul>${AGENTS.map((a) => {
        const s = agentState(a, data, me);
        return html`<li><a href="${href("agents", { agent: a.slug })}"><span class="row-flex" style="gap:10px;flex-wrap:nowrap"><span class="dot ${s.dot}" aria-hidden="true"></span><b>${a.name}</b><span class="meta-3" style="margin-left:auto">${s.label}</span></span>
          <span class="meta dock-now">${s.now}</span></a></li>`;
      })}</ul>`}
  </section>` : ""}
  <button type="button" class="dock-pill" data-shell="dock" aria-expanded="${open ? "true" : "false"}">
    <span class="dot ${state.running ? "run" : state.paused ? "" : "done"}" aria-hidden="true"></span>
    <span><b>${summary}</b>${state.waiting ? ` · ${state.waiting} waiting for you` : ""}</span>
  </button>`;
}
