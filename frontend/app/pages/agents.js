// Agents: what the six agents are doing, what they prepared, every AI run with its steps, which connectors each agent
// may use, the lessons people approved for them, and how people received their work.
import { html, icon, day, href, plural } from "../lib.js";
import { enc } from "../api.js";
import { emptyState } from "../shell.js";
import { pageState, missing, tabBar, tabKeys, cancelButton, openButton } from "../forms.js";
import { linkFor } from "./links.js";
import * as team from "./team.js";

export const title = "Agents";

const TABS = [
  ["controls", "Controls and CRM"],
  ["work", "Work and inbox"],
  ["runs", "AI runs"],
  ["permissions", "Permissions"],
  ["lessons", "Lessons"],
  ["team", "Team instructions"],
  ["scorecard", "Scorecard"],
];
const LESSON_AGENTS = ["Research agent", "Outreach agent", "Evidence agent", "Manager agent", "Discovery agent", "Direction agent"];
const JOB_TAG = { queued: "", running: "info", done: "good", failed: "bad", skipped: "", cancelled: "" };
const STATE_LABEL = { on: "On", off: "Off", forbidden: "Never (code)", "not built": "Not built", "connector off": "Connector off" };

const st = pageState();

// Items in the agents' inbox that the Outreach page does not handle (drafts and replies live there).
export function otherInbox(items) {
  return items.filter((i) => i.status === "new" && !["draft", "reply"].includes(i.kind));
}

export async function load(ctx, params) {
  // No tab: the team overview as designed. An agent: its own page. A tab: the records.
  if (params.agent) return team.loadAgent(ctx, params);
  if (!TABS.some(([t]) => t === params.tab)) return team.loadOverview(ctx);
  const tab = params.tab;
  const me = enc(ctx.me);
  const [overview, inbox] = await Promise.all([ctx.api.get("/agents"), ctx.api.get(`/inbox?owner=${me}`)]);
  const data = { tab, overview, inbox: otherInbox(inbox) };
  const opt = (p) => p.catch((e) => ({ error: e.message }));
  if (tab === "controls") data.crm = await opt(ctx.api.get("/crm"));
  if (tab === "work") {
    [data.jobs, data.opps] = await Promise.all([ctx.api.get(`/agents/jobs?limit=100${params.status ? `&status=${enc(params.status)}` : ""}`), ctx.api.get("/opportunities")]);
    data.status = params.status || "";
  }
  if (tab === "runs") {
    data.runs = await ctx.api.get("/agent-runs?limit=60");
    data.runId = params.run || "";
    if (data.runId) data.run = await opt(ctx.api.get(`/agent-runs/${enc(data.runId)}`));
  }
  if (tab === "permissions") {
    [data.perms, data.usage, data.history] = await Promise.all([ctx.api.get("/toolkit/permissions"), ctx.api.get("/toolkit/usage"), opt(ctx.api.get("/toolkit/permissions/history"))]);
  }
  if (tab === "lessons") [data.lessons, data.findings] = await Promise.all([ctx.api.get("/lessons"), opt(ctx.api.get("/lessons/findings"))]);
  if (tab === "team") [data.team, data.teamHistory] = await Promise.all([ctx.api.get("/team-text"), ctx.api.get("/team-text/history")]);
  if (tab === "scorecard") [data.scorecard, data.decisions, data.rules] = await Promise.all([ctx.api.get("/manager/scorecard"), opt(ctx.api.get("/manager/decisions?limit=50")), ctx.api.get("/rules")]);
  return data;
}

// While a "Try it" research run is going, check again every few seconds (the GET collects a finished run).
let pollTimer = null;
export function after(root, ctx, d) {
  clearTimeout(pollTimer);
  if (d.mode !== "agent" || !Array.isArray(d.trials) || !d.trials.some((t) => t.status === "running")) return;
  const route = location.hash;
  pollTimer = setTimeout(() => {
    if (location.hash !== route) return;
    if (root.contains(document.activeElement) && document.activeElement.matches("input, textarea, select")) return after(root, ctx, d);
    ctx.refresh();
  }, 8000);
}

export function view(d, ctx) {
  if (d.mode === "overview") return team.overview(d, ctx, st);
  if (d.mode === "agent") return team.detail(d, ctx, st);
  const o = d.overview;
  return html`
  <a class="back" href="${href("agents")}">${icon("back", 16)}Agents</a>
  <div class="page-head" style="margin-top:12px">
    <div>
      <h1>Agent records</h1>
      <p>The task board, every AI run, permissions, lessons and the scorecard. ${o.paused ? "The agents are paused." : o.worker ? `They work in the background in ${plural(o.lanes, "lane")}.` : "No background worker runs in this server: use Run the queue now."}</p>
    </div>
    <div class="actions">
      <button type="button" class="btn" data-act="patrol">Patrol now</button>
      <button type="button" class="btn" data-act="runQueue" ${o.queued ? "" : "disabled"}>Run the queue now (${o.queued})</button>
    </div>
  </div>
  ${st.err("top")}
  ${st.notices()}
  ${tabBar("Agents", TABS, d.tab, { work: d.inbox.length || undefined })}
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${{ controls: teamView, work: workView, runs: runsView, permissions: permissionsView, lessons: lessonsView, team: teamTextView, scorecard: scorecardView }[d.tab](d, ctx)}
  </div>`;
}

// ----- team -----
function teamView(d, ctx) {
  const o = d.overview;
  return html`
  <div class="two-col">
    <div class="main stack">
      <section class="card" aria-labelledby="team-h">
        <div class="card-head"><h2 id="team-h">The team</h2><span class="meta">Work done today, ${day(o.today, ctx.today)}</span></div>
        <ul class="rows">${o.agents.map((a) => html`<li style="align-items:flex-start">
          <span class="what"><b>${a.agent}</b><ul class="bullets meta">${a.does.map((x) => html`<li>${x}</li>`)}</ul></span>
          <span class="meta num">${plural(a.doneToday, "job")} done</span>
        </li>`)}</ul>
      </section>
    </div>
    <aside class="side">
      <section class="card card-body stack" aria-labelledby="brakes-h">
        <h2 id="brakes-h" class="section-title" style="margin:0">Controls</h2>
        <ul class="kv">
          <li><span>State</span><span>${o.paused ? "Paused" : "Working"}</span></li>
          <li><span>AI calls today</span><span>${o.aiCallsToday} of ${o.aiBudget}</span></li>
          <li><span>Daily patrol</span><span>${String(o.patrolHour).padStart(2, "0")}:00</span></li>
          <li><span>Last patrol</span><span>${o.lastPatrol ? day(o.lastPatrol, ctx.today) : "Never"}</span></li>
          <li><span>Manager agent</span><span>${o.managerMode}</span></li>
          <li><span>First contact</span><span>${o.firstContact === "plan" ? "Contact plan" : "First email"}</span></li>
          <li><span>Jobs queued</span><span>${o.queued}</span></li>
        </ul>
        <button type="button" class="btn ${o.paused ? "primary" : ""}" data-act="pause" data-paused="${o.paused ? "" : "1"}">${o.paused ? "Resume the agents" : "Pause all agents"}</button>
        ${st.ui.open === "settings" ? html`
        <form class="panel-form" data-act="settingsForm" data-on="submit">
          <label class="field">AI calls per day<input type="number" name="ai_budget" min="0" max="2000" step="1" value="${o.aiBudget}"></label>
          <label class="field">Patrol hour (0-23)<input type="number" name="patrol_hour" min="0" max="23" step="1" value="${o.patrolHour}"></label>
          <label class="field">Manager agent<select name="manager_mode">${["off", "shadow", "on"].map((m) => html`<option ${m === o.managerMode ? "selected" : ""}>${m}</option>`)}</select>
            <span class="hint">Shadow: it decides and compares, but queues nothing.</span></label>
          <label class="field">First contact after Step 4<select name="first_contact">${[["email", "The first email"], ["plan", "A contact plan"]].map(([v, l]) => html`<option value="${v}" ${v === o.firstContact ? "selected" : ""}>${l}</option>`)}</select>
          <span class="hint">A contact plan: the outreach agent proposes whom to approach and how; the owner approves it before any step.</span></label>
          <div class="actions"><button type="submit" class="btn primary">Save</button>${cancelButton}</div>
          ${st.err("settings")}
        </form>` : openButton("settings", "Change budget, patrol or manager…")}
      </section>
      ${crmCard(d.crm)}
    </aside>
  </div>`;
}

function lastRead(crm) {
  const times = (crm.opportunities || []).map((x) => x.crm && x.crm.pulledAt).filter(Boolean).sort();
  if (!times.length) return "Not yet";
  const t = new Date(times[times.length - 1]);
  return t.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function crmCard(crm) {
  if (!crm) return "";
  if (crm.error) return html`<section class="card card-body"><h2 class="section-title">CRM</h2><div class="notice bad">${icon("alert")}<span>${crm.error}</span></div></section>`;
  const waiting = (crm.opportunities || []).filter((x) => x.crmSync !== "synced");
  const disagree = (crm.opportunities || []).filter((x) => x.crm && x.crm.conflicts && x.crm.conflicts.length);
  return html`
  <section class="card card-body stack" aria-labelledby="crm-h">
    <h2 id="crm-h" class="section-title" style="margin:0">CRM</h2>
    <ul class="kv">
      <li><span>Provider</span><span>${crm.provider}</span></li>
      <li><span>Mode</span><span>${crm.mode}</span></li>
      <li><span>Writes</span><span>${crm.writes ? "On, after a person's action" : "Off"}</span></li>
      <li><span>Last read-back</span><span>${lastRead(crm)}</span></li>
    </ul>
    ${crm.error ? html`<div class="notice bad">${icon("alert")}<span>${crm.error}</span></div>` : ""}
    ${waiting.length ? html`<p class="meta">${plural(waiting.length, "opportunity", "opportunities")} waiting to sync or failed: ${waiting.map((x) => x.company).join(", ")}.</p>` : html`<p class="meta">Nothing waiting to sync.</p>`}
    ${disagree.length ? html`<p class="meta">Changed in the CRM, waiting for a person: ${disagree.map((x) => html`<a href="${linkFor(x)}">${x.company}</a>`).reduce((a, b) => html`${a}, ${b}`)}.</p>` : ""}
    <div class="actions">
      ${crm.writes && waiting.length ? html`<button type="button" class="btn small" data-act="crmSync">Sync now</button>` : ""}
      ${crm.reads ? html`<button type="button" class="btn small" data-act="crmPull">Read back from the CRM now</button>` : ""}
    </div>
    <p class="meta-3">The read-back runs with the daily check. It notes edits made in the CRM, the CRM opportunity's stage and tasks done there; it never changes a stage here.</p>
    ${st.err("crm")}
  </section>`;
}

// ----- work: task board and the inbox items no other page handles -----
function workView(d, ctx) {
  const byId = Object.fromEntries(d.opps.map((o) => [o.id, o]));
  const statuses = ["", "queued", "running", "done", "failed", "skipped"];
  return html`
  <div class="stack">
    <section class="card" aria-labelledby="inbox-h">
      <div class="card-head"><div><h2 id="inbox-h">Prepared for you</h2>
        <p class="meta">Facts, contacts, reminders and messages the agents prepared. Email drafts and client replies are on the <a href="${href("outreach")}">Outreach</a> page.</p></div></div>
      ${d.inbox.length ? html`<ul class="rows">${d.inbox.map((i) => inboxRow(i, byId[i.opportunityId], ctx))}</ul>`
        : emptyState("Nothing waiting", "Items appear here when an agent prepares something that needs a person.")}
    </section>
    <section class="card" aria-labelledby="board-h">
      <div class="card-head"><h2 id="board-h">Task board</h2>
        <label class="person">Show<select data-act="jobFilter" data-on="change" aria-label="Show jobs">${statuses.map((s) => html`<option value="${s}" ${s === d.status ? "selected" : ""}>${s || "all"}</option>`)}</select></label></div>
      ${d.jobs.length ? html`<div class="table-wrap"><table class="list">
        <thead><tr><th scope="col">Agent and work</th><th scope="col">Why</th><th scope="col">Status</th><th scope="col">Result</th></tr></thead>
        <tbody>${d.jobs.map((j) => {
          const o = byId[j.opportunityId];
          return html`<tr>
            <td><b>${j.agent}</b><span class="meta">${j.kind.replace(/_/g, " ")}${o ? html`, <a href="${linkFor(o)}">${o.company}</a>` : ""}</span>
              <div class="meta-3">${day(j.on, ctx.today)}, ${j.trigger}${j.requestedBy ? `, asked by ${j.requestedBy}` : ""}</div></td>
            <td>${j.reason}</td>
            <td><span class="tag ${JOB_TAG[j.status] || ""}">${j.status}</span>${j.attempts > 1 ? html`<div class="meta-3">${j.attempts} attempts</div>` : ""}</td>
            <td>${j.error ? html`<span class="bad-text">${j.error}</span>` : j.result}</td>
          </tr>`;
        })}</tbody>
      </table></div>` : emptyState("No jobs", d.status ? "None with this status." : "Jobs appear when the patrol or an event gives an agent work.")}
    </section>
  </div>`;
}

function inboxRow(i, o, ctx) {
  return html`<li style="align-items:flex-start">
    <span class="what"><b>${i.title}</b>
      <span class="meta">${i.agent}, ${day(i.on, ctx.today)}${o ? html`. <a href="${linkFor(o)}">${o.company}</a>` : ""}</span>
      ${i.body ? html`<p class="email-plain" style="margin-top:8px">${i.body}</p>` : ""}
      ${st.err(`item:${i.id}`)}</span>
    <span class="actions">
      ${["facts", "contact", "nurture"].includes(i.kind) ? html`<button type="button" class="btn primary small" data-act="item" data-do="apply" data-id="${i.id}" data-hash="${i.payloadHash || ""}">Apply</button>`
        : html`<button type="button" class="btn small" data-act="item" data-do="done" data-id="${i.id}">Done</button>`}
      <button type="button" class="btn ghost small" data-act="item" data-do="dismiss" data-id="${i.id}">Dismiss</button>
    </span>
  </li>`;
}

// ----- AI runs -----
function runsView(d, ctx) {
  return html`
  <div class="split">
    <section class="card" aria-labelledby="runs-h">
      <div class="card-head"><h2 id="runs-h">Recent AI runs</h2></div>
      ${d.runs.length ? html`<ul class="rows">${d.runs.map((r) => html`<li>
        <a class="what" href="${href("agents", { tab: "runs", run: r.id })}" ${d.runId === r.id ? html`aria-current="true"` : ""}>
          <b>${r.agent}</b><span class="meta">${r.subject || "Workspace"}, ${day(r.on, ctx.today)}</span></a>
        <span class="tag ${r.status === "completed" ? "good" : r.status === "failed" ? "bad" : ""}">${r.status}</span>
      </li>`)}</ul>` : emptyState("No AI runs yet", "Each agent call is recorded here with its model, prompt version and steps.")}
    </section>
    <section class="card detail" aria-labelledby="run-h">
      ${!d.runId ? html`<div class="block">${emptyState("Choose a run", "See the model, the prompt version, the calls made and every step a tool-using agent took.")}</div>`
        : d.run.error ? html`<div class="block"><div class="notice bad">${icon("alert")}<span>${d.run.error}</span></div></div>` : runDetail(d.run, ctx)}
    </section>
  </div>`;
}

function runDetail(r, ctx) {
  return html`
  <div class="block">
    <h2 id="run-h">${r.agent}: ${r.subject || "Workspace"}</h2>
    <ul class="kv" style="margin-top:10px">
      <li><span>When</span><span>${day(r.on, ctx.today)}</span></li>
      <li><span>Model</span><span>${r.model}</span></li>
      <li><span>Prompt version</span><span>${r.prompt}</span></li>
      <li><span>AI calls</span><span>${r.calls}</span></li>
      ${r.tokensIn !== null && r.tokensIn !== undefined ? html`<li><span>Tokens in and out</span><span>${r.tokensIn} / ${r.tokensOut}</span></li>` : ""}
      <li><span>Status</span><span>${r.status}</span></li>
    </ul>
    ${r.error ? html`<div class="notice bad" style="margin-top:10px">${icon("alert")}<span>${r.error}</span></div>` : ""}
  </div>
  <div class="block">
    <h3 class="sub-title">Steps</h3>
    ${r.trace.length ? html`<ol class="history">${r.trace.map((s, i) => html`<li>
      <b>${i + 1}. ${s.type === "tool" ? `Tool: ${s.tool}` : s.type === "model" ? "Model turn" : s.type === "finish" ? (s.accepted ? "Finished, answer accepted" : "Finish refused by the code checks") : s.type || "Step"}</b>
      ${s.text ? html`<span class="meta">${s.text}</span>` : ""}
      ${s.calls && s.calls.length ? html`<span class="meta-3">Asked for: ${s.calls.map((c) => c.tool).join(", ")}</span>` : ""}
      ${s.args ? html`<span class="email-plain">${JSON.stringify(s.args, null, 2)}</span>` : ""}
      ${s.result ? html`<span class="meta-3">Result: ${typeof s.result === "string" ? s.result : JSON.stringify(s.result)}</span>` : ""}
      ${s.error ? html`<span class="bad-text">${s.error}</span>` : ""}
      ${s.reason ? html`<span class="meta-3">${s.reason}</span>` : ""}
    </li>`)}</ol>` : html`<p class="meta">No step record: this was a single call${r.model === "demo-text-rules" ? " (demo text rules, no AI)" : ""}.</p>`}
  </div>`;
}

// ----- permissions -----
function permissionsView(d, ctx) {
  const p = d.perms;
  const limits = Object.fromEntries(d.usage.limits.map((l) => [l.connector, l]));
  return html`
  <div class="stack">
    <section class="card" aria-labelledby="conn-h">
      <div class="card-head"><div><h2 id="conn-h">Connectors</h2><p class="meta">Every outside system goes through one gate with a usage log and daily limits.</p></div></div>
      <div class="table-wrap"><table class="list">
        <thead><tr><th scope="col">Connector</th><th scope="col">Data</th><th scope="col">State</th><th scope="col">Today, of daily limit</th><th scope="col"><span class="sr-only">Change</span></th></tr></thead>
        <tbody>${p.connectors.map((c) => {
          const l = limits[c.name];
          return html`<tr>
            <td><b>${c.name}</b><span class="meta">${c.about}</span></td>
            <td>${c.data}</td>
            <td>${!c.built ? "Not built" : c.on ? "On" : "Off"}<div class="meta-3">${c.source}</div></td>
            <td class="num">${l ? `${l.usedToday} of ${l.perDay ? l.perDay : "no limit"}` : "Not metered"}</td>
            <td>${c.built ? openButton(`conn:${c.name}`, "Change…", "btn ghost small", `Change the ${c.name} connector`) : ""}</td>
          </tr>
          ${st.ui.open === `conn:${c.name}` ? html`<tr><td colspan="5">${connectorForm(c, l)}</td></tr>` : ""}`;
        })}</tbody>
      </table></div>
    </section>
    <section class="card" aria-labelledby="perm-h">
      <div class="card-head"><div><h2 id="perm-h">Which agent may use which connector</h2>
        <p class="meta">"Never" is set in the code and cannot be changed here. A change is recorded with your reason and can be undone.</p></div></div>
      <div class="table-wrap"><table class="list compact-grid">
        <thead><tr><th scope="col">Agent</th>${p.connectors.map((c) => html`<th scope="col">${c.name}</th>`)}</tr></thead>
        <tbody>${p.agents.map((a) => html`<tr>
          <td><b>${a.agent}</b></td>
          ${p.connectors.map((c) => {
            const cell = a.connectors[c.name];
            const name = `perm:${a.agent}:${c.name}`;
            return html`<td>${cell.editable ? html`<button type="button" class="btn ghost small" data-act="open" data-open="${name}" aria-label="${a.agent}, ${c.name}: ${STATE_LABEL[cell.state] || cell.state}. Change">${STATE_LABEL[cell.state] || cell.state}</button>`
              : html`<span class="meta">${STATE_LABEL[cell.state] || cell.state}</span>`}</td>`;
          })}
        </tr>
        ${p.connectors.some((c) => st.ui.open === `perm:${a.agent}:${c.name}`) ? html`<tr><td colspan="${p.connectors.length + 1}">${permForm(a, p.connectors.find((c) => st.ui.open === `perm:${a.agent}:${c.name}`))}</td></tr>` : ""}`)}</tbody>
      </table></div>
      ${st.err("perm")}
    </section>
    <section class="card" aria-labelledby="hist-h">
      <div class="card-head"><h2 id="hist-h">Changes</h2></div>
      ${d.history.error ? html`<div class="card-body"><div class="notice bad">${icon("alert")}<span>${d.history.error}</span></div></div>`
        : d.history.length ? html`<ul class="rows">${d.history.map((h) => html`<li>
          <span class="what"><b>${h.kind === "limit" ? `${h.connector} limit set to ${h.set}` : `${h.agent || "All agents"}, ${h.connector}: ${h.set}`}</b>
            <span class="meta">${h.by}, ${day(h.on, ctx.today)}. ${h.reason}</span></span>
          ${h.undoable ? (st.ui.open === `undo:${h.id}` ? html`<form class="row-flex" data-act="undoForm" data-on="submit" data-id="${h.id}">
            <label class="sr-only" for="undo-${h.id}">Why undo</label><input class="input" id="undo-${h.id}" name="reason" placeholder="Why undo" required style="width:220px">
            <button type="submit" class="btn small">Undo</button>${cancelButton}</form>` : openButton(`undo:${h.id}`, "Undo…", "btn ghost small")) : ""}
        </li>`)}</ul>` : emptyState("No changes yet", "Changes to the table and the limits appear here.")}
      ${st.err("undo")}
    </section>
  </div>`;
}

function stateSelect() {
  return html`<label class="field">Set to<select name="enabled"><option value="true">On</option><option value="false">Off</option><option value="">Back to the default</option></select></label>`;
}

function permForm(a, c) {
  return html`
  <form class="panel-form" data-act="permForm" data-on="submit input" data-agent="${a.agent}" data-connector="${c.name}">
    <h3 class="sub-title">${a.agent} and ${c.name}</h3>
    <div class="form-grid">${stateSelect()}<label class="field">Reason<input name="reason" required></label></div>
    <div class="actions"><button type="submit" class="btn primary" disabled>Save</button>${cancelButton}<span class="missing" data-missing>Give a reason.</span></div>
  </form>`;
}

function connectorForm(c, l) {
  return html`
  <div class="stack">
    <form class="panel-form" data-act="connForm" data-on="submit input" data-connector="${c.name}">
      <h3 class="sub-title">Switch ${c.name} for every agent</h3>
      <div class="form-grid">${stateSelect()}<label class="field">Reason<input name="reason" required></label></div>
      <div class="actions"><button type="submit" class="btn primary" disabled>Save</button><span class="missing" data-missing>Give a reason.</span></div>
    </form>
    ${l ? html`<form class="panel-form" data-act="limitForm" data-on="submit input" data-connector="${c.name}">
      <h3 class="sub-title">Daily limit for ${c.name}</h3>
      <div class="form-grid">
        <label class="field">Calls per day <span class="hint">0 means no limit; empty means the config file's default.</span><input type="number" name="perDay" min="0" step="1" value="${l.perDay === null ? "" : l.perDay}"></label>
        <label class="field">Reason<input name="reason" required></label>
      </div>
      <div class="actions"><button type="submit" class="btn primary" disabled>Save limit</button>${cancelButton}<span class="missing" data-missing>Give a reason.</span></div>
    </form>` : ""}
    ${st.err("conn")}
  </div>`;
}

// ----- lessons -----
function lessonsView(d, ctx) {
  const groups = ["proposed", "approved", "rejected", "retired"].map((s) => [s, d.lessons.filter((l) => l.status === s)]);
  return html`
  <div class="two-col">
    <div class="main stack">
      ${groups.map(([s, list]) => list.length || s === "proposed" ? html`
      <section class="card" aria-labelledby="les-${s}">
        <div class="card-head"><h2 id="les-${s}">${{ proposed: "Waiting for a decision", approved: "In the agents' prompts", rejected: "Rejected", retired: "Retired" }[s]}</h2><span class="meta num">${list.length}</span></div>
        ${list.length ? html`<ul class="rows">${list.map((l) => lessonRow(l, ctx))}</ul>`
          : emptyState("No lessons waiting", "The manager agent proposes lessons from the scorecard once a week; you can propose one too.")}
      </section>` : "")}
    </div>
    <aside class="side">
      <section class="card card-body stack">
        <h2 class="section-title" style="margin:0">Propose a lesson</h2>
        <p class="meta">One or two sentences to an agent: in which situation, what to do. It joins the agent's prompt only after approval (at most five per agent).</p>
        <form class="panel-form" data-act="lessonForm" data-on="submit input">
          <label class="field">For<select name="agent">${LESSON_AGENTS.map((a) => html`<option>${a}</option>`)}</select></label>
          <label class="field">The lesson<textarea name="text" rows="3" required></textarea></label>
          <label class="field">What you saw that it answers<textarea name="why" rows="2" required></textarea></label>
          <div class="actions"><button type="submit" class="btn primary" disabled>Propose</button><span class="missing" data-missing>Write the text and why.</span></div>
          ${st.err("lesson")}
        </form>
      </section>
      <section class="card card-body stack">
        <h2 class="section-title" style="margin:0">What stands out</h2>
        ${d.findings.error ? html`<div class="notice bad">${icon("alert")}<span>${d.findings.error}</span></div>`
          : d.findings.length ? html`<ul class="history">${d.findings.map((f) => html`<li><b>${f.agent}</b><span class="meta">${f.summary}</span></li>`)}</ul>`
          : html`<p class="meta">Nothing yet: the scorecard needs at least five decisions on a kind of work.</p>`}
        <button type="button" class="btn small" data-act="lessonReview">Run the lessons review now</button>
        ${st.err("review")}
      </section>
    </aside>
  </div>`;
}

// ----- team instructions (api/team_text.py) -----
// What the person typed, kept until it is saved: a refused save (someone saved in between) redraws the page.
let teamDraft = null;

function teamTextView(d, ctx) {
  const t = d.team, cur = t.current;
  const base = cur ? cur.revision : null;
  const text = (k) => (teamDraft ? teamDraft.sections[k] : cur && cur.sections[k]) || "";
  const saved = (r) => `${r.savedBy}, ${day(r.savedAt.slice(0, 10), ctx.today)}`;
  return html`
  <div class="two-col">
    <div class="main stack">
      <section class="card card-body stack" aria-labelledby="tt-h">
        <div class="row-flex"><h2 id="tt-h" class="section-title" style="margin:0">What CGS tells the agents</h2>
          <span class="meta" style="margin-left:auto">${cur ? `Revision ${cur.revision}, ${saved(cur)}` : "Nothing saved yet"}</span></div>
        <p class="meta">How CGS works, in your own words. Each section joins the end of the prompts of the agents that read it, from their next run,
          and changes how they write and what they look for. It cannot change what an agent may use, the checks on its work (quotes, facts and
          citations from their sources), or that a person approves before anything is sent or scored.</p>
        <div class="notice warn">${icon("alert")}<span>This text goes to the AI provider (${t.aiProvider}) with every request of those agents.
          Put CGS-confidential material here only once CGS has approved that provider (SoW section 9).</span></div>
        ${t.canChange ? html`<form class="panel-form" data-act="teamSave" data-on="submit input" data-base="${base === null ? "" : base}">
          ${t.sectionsMeta.map((s) => html`<label class="field">${s.title}
            <span class="meta">Read by ${s.readBy.join(", ")}. ${s.hint} At most ${s.max} characters.</span>
            <textarea name="${s.key}" rows="6" maxlength="${s.max}">${text(s.key)}</textarea></label>`)}
          <label class="field">Why (optional)<input name="reason" value="${teamDraft ? teamDraft.reason : ""}"></label>
          <div class="actions"><button type="submit" class="btn primary">Save as revision ${base === null ? 1 : base + 1}</button>
            ${teamDraft ? html`<button type="button" class="btn ghost" data-act="teamDiscard">Discard changes</button><span class="meta">Not saved yet.</span>` : ""}</div>
          ${st.err("team")}
        </form>`
        : html`${t.sectionsMeta.map((s) => html`<div><p class="sub-title" style="margin:0">${s.title}</p><p class="meta">Read by ${s.readBy.join(", ")}.</p>
            <p style="white-space:pre-wrap">${(cur && cur.sections[s.key]) || "Nothing written."}</p></div>`)}
          <p class="meta">Only ${t.admins.join(", ")} can change it (CGS_ADMINS).</p>`}
      </section>
    </div>
    <aside class="side">
      <section class="card card-body stack" aria-labelledby="tt-hist">
        <h2 id="tt-hist" class="section-title" style="margin:0">Revisions</h2>
        ${st.err("teamHist")}
        ${d.teamHistory.length ? html`<ul class="history">${d.teamHistory.map((r) => html`<li>
          <b>Revision ${r.revision}${r.revision === base ? " (in use)" : ""}</b>
          <span class="meta">${saved(r)}. ${r.reason || (r.origin === "restore" ? "Restored." : "No reason given.")}</span>
          <details><summary class="meta">The text</summary>${t.sectionsMeta.map((s) => r.sections[s.key] ? html`<p class="meta" style="white-space:pre-wrap"><b>${s.title}</b>
            ${r.sections[s.key]}</p>` : "")}</details>
          ${t.canChange && r.revision !== base ? html`<button type="button" class="btn small" data-act="teamRestore" data-rev="${r.revision}" data-base="${base}"
            ${teamDraft ? html`disabled title="Save or discard your changes first"` : ""}>Use this text again</button>` : ""}
        </li>`)}</ul>` : html`<p class="meta">Every save is kept here. Nothing yet.</p>`}
      </section>
    </aside>
  </div>`;
}

function lessonRow(l, ctx) {
  const since = l.sinceApproved;
  return html`<li style="align-items:flex-start">
    <span class="what"><b>${l.label}: ${l.text}</b>
      <span class="meta">${l.agent}. Proposed by ${l.proposedBy}, ${day(l.proposedOn, ctx.today)}. ${l.why}</span>
      ${l.decisionReason ? html`<span class="meta-3">${l.decidedBy}: ${l.decisionReason}</span>` : ""}
      ${since ? html`<span class="meta-3">In use for ${plural(since.days, "day")}, used ${plural(l.uses, "time")}.</span>` : ""}
      ${st.ui.open === `lesson:${l.id}` ? html`<form class="panel-form" style="margin-top:8px" data-act="lessonDecide" data-on="submit input" data-id="${l.id}" data-status="${l.status}">
        <label class="field">Reason <span class="hint">${l.status === "approved" ? "Why retire it." : "Needed to reject; the manager reads it."}</span><textarea name="reason" rows="2"></textarea></label>
        <div class="actions">
          ${l.status === "proposed" ? html`<button type="submit" class="btn primary" name="decision" value="approve">Approve</button><button type="submit" class="btn" name="decision" value="reject">Reject</button>`
            : html`<button type="submit" class="btn primary" name="decision" value="retire">Retire</button>`}
          ${cancelButton}
        </div>
        ${st.err(`lesson:${l.id}`)}
      </form>` : ""}
    </span>
    ${["proposed", "approved"].includes(l.status) && st.ui.open !== `lesson:${l.id}` ? openButton(`lesson:${l.id}`, l.status === "proposed" ? "Decide…" : "Retire…", "btn small", `${l.status === "proposed" ? "Decide on" : "Retire"} ${l.label}`) : ""}
  </li>`;
}

// ----- scorecard -----
function pct(x) {
  return x === null || x === undefined ? "n/a" : `${Math.round(x * 100)}%`;
}

function scorecardView(d, ctx) {
  const s = d.scorecard;
  const ev = s.evidence || { keys: [], totals: {} };
  return html`
  <div class="stack">
    <p class="meta">The last ${s.days} days, since ${day(s.since, ctx.today)}. Rates need at least ${s.minSample} decisions before they count; smaller samples are marked.</p>
    <section class="card" aria-labelledby="work-h">
      <div class="card-head"><h2 id="work-h">How people received the agents' work</h2></div>
      ${s.work.length ? html`<div class="table-wrap"><table class="list">
        <thead><tr><th scope="col">Work</th><th scope="col">Prepared</th><th scope="col">Used</th><th scope="col">Dismissed</th><th scope="col">Left to expire</th><th scope="col">Useful</th></tr></thead>
        <tbody>${s.work.map((w) => html`<tr><td><b>${w.work.replace(/_/g, " ")}</b><span class="meta">${w.agent}</span></td>
          <td class="num">${w.prepared}</td><td class="num">${w.used}</td><td class="num">${w.dismissed}</td><td class="num">${w.ignored}</td>
          <td class="num">${pct(w.usefulRate)}${w.enough ? "" : html`<div class="meta-3">small sample</div>`}</td></tr>`)}</tbody>
      </table></div>` : emptyState("Nothing to count yet", "Rows appear once people use or dismiss what the agents prepared.")}
    </section>
    <section class="card" aria-labelledby="ev-h">
      <div class="card-head"><div><h2 id="ev-h">Evidence agent calibration</h2><p class="meta">What people did with its proposals, per score or condition.</p></div></div>
      ${ev.keys.length ? html`<div class="table-wrap"><table class="list">
        <thead><tr><th scope="col">Item</th><th scope="col">Decided</th><th scope="col">Accepted</th><th scope="col">Raised</th><th scope="col">Lowered</th><th scope="col">Rejected</th><th scope="col">Set by hand, never proposed</th></tr></thead>
        <tbody>${ev.keys.map((k) => html`<tr><td><b>${d.rules.labels[k.key] || d.rules.conditions[k.key] || k.key}</b>${k.findings.length ? html`<div class="meta-3">${k.findings.join("; ")}</div>` : ""}</td>
          <td class="num">${k.decided}</td><td class="num">${k.accepted}</td><td class="num">${k.raised}</td><td class="num">${k.lowered}</td><td class="num">${k.rejected}</td><td class="num">${k.byHandNeverProposed}</td></tr>`)}</tbody>
      </table></div>` : emptyState("No evidence decisions yet", "Rows appear once people decide on evidence proposals.")}
    </section>
    ${plansCard(s.plans)}
    <section class="card" aria-labelledby="dec-h">
      <div class="card-head"><h2 id="dec-h">The manager agent's decisions</h2></div>
      ${d.decisions.error ? html`<div class="card-body"><div class="notice bad">${icon("alert")}<span>${d.decisions.error}</span></div></div>`
        : d.decisions.length ? html`<ul class="rows">${d.decisions.map((x) => html`<li><span class="what"><b>${x.kind}${x.opportunityId ? ` (${x.opportunityId})` : ""}</b>
          <span class="meta">${x.reason}</span><span class="meta-3">${day(x.on, ctx.today)}, ${x.mode}, ${x.scope}. ${x.outcome}</span></span></li>`)}</ul>`
        : emptyState("No decisions yet", "The manager agent decides during its daily review and when something happens to an opportunity.")}
    </section>
  </div>`;
}

const APPROACH_LABEL = { warm: "Warm (an introduction)", value_first: "Value first", direct: "Direct" };

// Contact plans (api/scorecard.py plans): how people decided them and what came of the approved ones.
function plansCard(p) {
  if (!p) return "";
  return html`<section class="card" aria-labelledby="plans-h">
    <div class="card-head"><div><h2 id="plans-h">Contact plans</h2>
      <p class="meta">${p.decided ? `${p.approved} approved, ${p.rejected} rejected.` : "No plan decided yet."} A reply counts when it came after the plan started.</p></div></div>
    ${p.decided || p.byChannel.length ? html`<div class="table-wrap"><table class="list">
      <thead><tr><th scope="col">Approach</th><th scope="col">Approved</th><th scope="col">Rejected</th><th scope="col">Running</th><th scope="col">Replied</th><th scope="col">Reply rate</th></tr></thead>
      <tbody>${p.byApproach.filter((a) => a.decided || a.plans).map((a) => html`<tr><td><b>${APPROACH_LABEL[a.approach] || a.approach}</b></td>
        <td class="num">${a.approved}</td><td class="num">${a.rejected}</td><td class="num">${a.running}</td><td class="num">${a.replied}</td>
        <td class="num">${pct(a.replyRate)}${a.enough ? "" : html`<div class="meta-3">small sample</div>`}</td></tr>`)}</tbody>
    </table></div>
    ${p.byChannel.length ? html`<div class="table-wrap"><table class="list">
      <thead><tr><th scope="col">Step</th><th scope="col">Done</th><th scope="col">Skipped</th><th scope="col">Last step before a reply</th></tr></thead>
      <tbody>${p.byChannel.map((c) => html`<tr><td><b>${c.channel.replace(/_/g, " ")}</b>${c.skipNotes.length ? html`<div class="meta-3">Skipped because: ${c.skipNotes.join("; ")}</div>` : ""}</td>
        <td class="num">${c.done}</td><td class="num">${c.skipped}</td><td class="num">${c.beforeReply}</td></tr>`)}</tbody>
    </table></div>` : ""}
    ${p.rejectionNotes.length ? html`<div class="card-body"><p class="meta"><b>Why plans were rejected:</b> ${p.rejectionNotes.join("; ")}</p></div>` : ""}`
    : html`<div class="card-body">${emptyState("Nothing to count yet", "Rows appear once people approve or reject contact plans.")}</div>`}
  </section>`;
}

// ---------- actions ----------
const bool = (v) => (v === "true" ? true : v === "false" ? false : null);

export const actions = {
  ...team.teamActions,
  decideProposal(t, e, ctx) {
    const approve = t.dataset.verdict === "approve";
    st.write(`prop:${t.dataset.id}`, t, ctx, () => ctx.api.post(`/direction/proposals/${enc(t.dataset.id)}/${t.dataset.verdict}`), (res) => {
      st.ui.notice = [{ text: approve ? `Applied. ${res.version ? `Direction ${res.version} is now in use.` : ""}` : "Dismissed. The direction is unchanged.", cls: approve ? "good" : "" }];
    });
  },
  approveLesson(t, e, ctx) {
    st.write(`lesson:${t.dataset.id}`, t, ctx, () => ctx.api.post(`/lessons/${enc(t.dataset.id)}/decision`, { decision: "approve", reason: "" }),
      () => { st.ui.notice = [{ text: "Lesson approved. It joins the agent's instructions from its next run.", cls: "good" }]; });
  },
  tab(t, e, ctx) { st.reset(); ctx.go("agents", { tab: t.dataset.tab }); },
  tabkeys(t, e, ctx) { tabKeys(t, e, ctx, (tab) => { st.reset(); ctx.go("agents", { tab }); }); },
  open(t, e, ctx) { st.ui.open = t.dataset.open || null; st.ui.error = null; ctx.refresh(); },
  jobFilter(t, e, ctx) { ctx.go("agents", { tab: "work", status: t.value }); },

  patrol(t, e, ctx) {
    st.write("top", t, ctx, () => ctx.api.post("/agents/patrol"), (res) => {
      st.ui.notice = [{ text: `Patrol done: ${plural(res.queued.length, "job")} queued.`, cls: "good" }];
    });
  },
  runQueue(t, e, ctx) {
    st.write("top", t, ctx, () => ctx.api.post("/agents/run?limit=20"), (res) => {
      const ran = res.ran || [];
      const failed = ran.filter((j) => j.status === "failed").length;
      st.ui.notice = [{ text: `Ran ${plural(ran.length, "job")}${failed ? `; ${failed} failed (see the task board)` : ""}.`, cls: failed ? "warn" : "good" }];
    });
  },
  tryForm(form, e, ctx) {
    const f = form.elements;
    const why = f.company.value.trim() && f.domain.value.trim() ? "" : "Give the company and its website.";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    st.write("try", form.querySelector("[type=submit]"), ctx, () => ctx.api.post("/agents/research/try", { company: f.company.value.trim(), domain: f.domain.value.trim() }),
      (res) => { st.ui.notice = [{ text: res.status === "completed" ? `Brief ready for ${res.company}. Nothing was saved to the workflow.` : `The research agent is researching ${res.company}. The brief appears below when it is done.`, cls: "good" }]; });
  },
  pauseAgent(t, e, ctx) {
    const paused = t.dataset.paused === "1";
    st.write("top", t, ctx, () => ctx.api.post("/agents/pause", { agent: t.dataset.agent, paused }),
      () => { st.ui.notice = [{ text: `${t.dataset.agent} ${paused ? "paused. Its jobs wait on the task board." : "resumed."}`, cls: "good" }]; });
  },
  pause(t, e, ctx) {
    st.write("top", t, ctx, () => ctx.api.patch("/agents", { paused: t.dataset.paused === "1" }));
  },
  settingsForm(form, e, ctx) {
    const f = form.elements;
    st.write("settings", form.querySelector("[type=submit]"), ctx, () => ctx.api.patch("/agents",
      { ai_budget: Number(f.ai_budget.value), patrol_hour: Number(f.patrol_hour.value), manager_mode: f.manager_mode.value,
        ...(f.first_contact ? { first_contact: f.first_contact.value } : {}) }));
  },
  crmPull(t, e, ctx) {
    st.write("crm", t, ctx, () => ctx.api.post("/crm/pull"), (res) => {
      st.ui.notice = [{ text: `Read back ${plural(res.read, "opportunity", "opportunities")} from the CRM: ${plural(res.changes, "new change")} there.`
        + (res.failed ? ` ${res.failed} could not be read: ${res.errors.join("; ")}` : ""), cls: res.failed ? "warn" : "good" }];
    });
  },
  crmSync(t, e, ctx) {
    st.write("crm", t, ctx, () => ctx.api.post("/crm/sync"), () => { st.ui.notice = [{ text: "CRM sync ran. The list shows what is still waiting.", cls: "good" }]; });
  },

  item(t, e, ctx) {
    const body = { action: t.dataset.do };
    if (t.dataset.hash) body.payload_hash = t.dataset.hash;
    st.write(`item:${t.dataset.id}`, t, ctx, () => ctx.api.post(`/inbox/${enc(t.dataset.id)}`, body), (res) => { ctx.toast(res.note || "Done."); });
  },

  permForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give a reason.");
    if (!reason) return;
    st.write("perm", form.querySelector("[type=submit]"), ctx, () => ctx.api.put(`/toolkit/permissions/${enc(form.dataset.agent)}/${enc(form.dataset.connector)}`,
      { enabled: bool(form.elements.enabled.value), reason }));
  },
  connForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give a reason.");
    if (!reason) return;
    st.write("conn", form.querySelector("[type=submit]"), ctx, () => ctx.api.put(`/toolkit/connectors/${enc(form.dataset.connector)}`,
      { enabled: bool(form.elements.enabled.value), reason }));
  },
  limitForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give a reason.");
    if (!reason) return;
    const v = form.elements.perDay.value;
    st.write("conn", form.querySelector("[type=submit]"), ctx, () => ctx.api.put(`/toolkit/limits/${enc(form.dataset.connector)}`,
      { perDay: v === "" ? null : Number(v), reason }));
  },
  undoForm(form, e, ctx) {
    st.write("undo", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/toolkit/permissions/history/${enc(form.dataset.id)}/undo`,
      { reason: form.elements.reason.value.trim() }));
  },

  teamSave(form, e, ctx) {
    const sections = Object.fromEntries([...form.querySelectorAll("textarea")].map((x) => [x.name, x.value]));
    const base = form.dataset.base === "" ? null : Number(form.dataset.base);
    // the draft remembers the revision the person started from, so a newer save by someone else shows as a warning
    teamDraft = { base: teamDraft ? teamDraft.base : base, sections, reason: form.elements.reason.value };
    if (e.type !== "submit") return;
    teamDraft.base = base; // saving now means: over the revision on the page
    st.write("team", form.querySelector("[type=submit]"), ctx, () => ctx.api.put("/team-text", { sections, baseRevision: base, reason: form.elements.reason.value.trim() }),
      (res) => { teamDraft = null; st.ui.notice = [{ text: `Saved as revision ${res.revision}. The agents that read it use it from their next run.`, cls: "good" }]; });
  },
  teamDiscard(t, e, ctx) {
    teamDraft = null;
    ctx.refresh();
  },
  teamRestore(t, e, ctx) {
    if (teamDraft) { // typing does not redraw the page, so the button may still look available
      st.ui.error = { where: "teamHist", message: "You have changes that are not saved. Save or discard them first." };
      ctx.refresh();
      return;
    }
    st.write("teamHist", t, ctx, () => ctx.api.post(`/team-text/${enc(t.dataset.rev)}/restore`, { baseRevision: t.dataset.base === "null" ? null : Number(t.dataset.base) }),
      (res) => { teamDraft = null; st.ui.notice = [{ text: `The text of revision ${t.dataset.rev} is in use again, as revision ${res.revision}.`, cls: "good" }]; });
  },
  lessonForm(form, e, ctx) {
    const f = form.elements;
    const why = f.text.value.trim() && f.why.value.trim() ? "" : "Write the text and why.";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    st.write("lesson", form.querySelector("[type=submit]"), ctx, () => ctx.api.post("/lessons", { agent: f.agent.value, text: f.text.value.trim(), why: f.why.value.trim() }),
      (res) => { st.ui.notice = [{ text: `${res.label} proposed for the ${res.agent.toLowerCase()}. The agent uses it only after an admin approves it.`, cls: "good" }]; });
  },
  lessonDecide(form, e, ctx) {
    if (e.type !== "submit") return;
    const decision = (e.submitter && e.submitter.value) || "approve";
    const reason = form.elements.reason.value.trim();
    if ((decision === "reject" || decision === "retire") && !reason) {
      st.ui.error = { where: `lesson:${form.dataset.id}`, message: decision === "reject" ? "Give a reason to reject." : "Give a reason to retire." };
      return ctx.refresh();
    }
    const id = enc(form.dataset.id);
    st.write(`lesson:${form.dataset.id}`, e.submitter, ctx, () => (decision === "retire"
      ? ctx.api.post(`/lessons/${id}/retire`, { reason })
      : ctx.api.post(`/lessons/${id}/decision`, { decision, reason })));
  },
  lessonReview(t, e, ctx) {
    st.write("review", t, ctx, () => ctx.api.post("/lessons/review"), (res) => {
      st.ui.notice = [{ text: res && res.proposed !== undefined ? `The lessons review proposed ${plural(res.proposed, "lesson")}.` : "The lessons review ran.", cls: "good" }];
    });
  },
};
