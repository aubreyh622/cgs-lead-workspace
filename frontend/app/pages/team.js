// The Agents page as designed: six agent cards with what each is doing, the controls, suggestions from the agents,
// and a page per agent. Everything shown is computed from the backend's jobs, inbox, runs, proposals and scorecard.
import { html, icon, day, addDays, href, plural } from "../lib.js";
import { enc } from "../api.js";
import { emptyState } from "../shell.js";
import { tabBar, cancelButton, openButton } from "../forms.js";
import { linkFor } from "./links.js";

// What each agent does, in the words of the backend guide (docs/BACKEND_V2.md). Only facts the code implements.
export const AGENTS = [
  {
    slug: "research", name: "Research agent", icon: "search",
    job: "Researches one company on the public web: a brief with dated sources, flags for anything doubtful, a priority estimate and a contact only when a source names one.",
    when: ["A person picks a candidate or adds a company", "A person sends a company back with questions", "Another agent needs a missing person, such as who approves the budget", "A company is checked again for news"],
    gives: ["A brief in Companies, Ready to decide; never a decision", "Contacts only when a source names them"],
  },
  {
    slug: "outreach", name: "Outreach agent", icon: "outreach",
    job: "Writes the first email once a lead is accepted, follow-ups when a sent email has no reply, and reconnect notes that cite the company's latest news.",
    when: ["A company is accepted at Step 4", "A sent email has had no reply for the follow-up period", "A lead in nurture reaches its review date"],
    gives: ["Email drafts in Outreach, To approve; nothing is sent until a person approves", "Citations of the research brief, checked by code"],
  },
  {
    slug: "evidence", name: "Evidence agent", icon: "fileCheck",
    job: "Reads client replies and call notes, proposes scores, conditions and blockers with the client's exact words, and labels replies.",
    when: ["A person saves notes and asks for proposals", "A client reply is recorded"],
    gives: ["Proposals in Qualification; a person accepts, edits or rejects each one", "Quotes that must appear word for word in the notes"],
  },
  {
    slug: "manager", name: "Manager agent", icon: "calendarCheck",
    job: "Looks over every open opportunity each day, keeps a memory of each one, plans next actions, routes requests between agents and writes one message per owner.",
    when: ["The daily patrol", "Something happens to an opportunity (a reply, notes, a returned pack)", "Once a week, the lessons review"],
    gives: ["Next actions and messages in a person's inbox", "Lessons for the agents, which a person approves"],
  },
  {
    slug: "discovery", name: "Discovery agent", icon: "globe",
    job: "Finds companies in recent news that match what CGS looks for, on a schedule or when someone asks.",
    when: ["Once a week", "A person asks on the Sources page"],
    gives: ["Candidates in Companies, New candidates, each with the signal and its date"],
  },
  {
    slug: "direction", name: "Direction agent", icon: "compass",
    job: "Keeps what the agents look for up to date: learns from why people skip, accept or reject companies, and turns the CGS library into problems to watch for.",
    when: ["Once a week", "A person asks on the Sources page"],
    gives: ["Proposed changes to the research direction; a person approves each one"],
  },
];
const BY_SLUG = Object.fromEntries(AGENTS.map((a) => [a.slug, a]));
const DETAIL_TABS = [["overview", "Overview"], ["work", "Work"], ["runs", "AI runs"], ["permissions", "Permissions"], ["lessons", "Lessons"]];
const LESSON_AGENTS = ["Evidence agent", "Outreach agent", "Manager agent"];
const JOB_TAG = { queued: "", running: "info", done: "good", failed: "bad", skipped: "", cancelled: "" };
const STATE_LABEL = { on: "On", off: "Off", forbidden: "Never (code)", "not built": "Not built", "connector off": "Connector off" };

// ---------- data ----------
async function base(ctx) {
  const opt = (p) => p.catch((e) => ({ error: e.message }));
  const [overview, inbox, jobs, direction, lessons, scorecard, opps] = await Promise.all([
    ctx.api.get("/agents"), ctx.api.get(`/inbox?owner=${enc(ctx.me)}`), ctx.api.get("/agents/jobs?limit=500"),
    ctx.api.get("/direction"), ctx.api.get("/lessons"), opt(ctx.api.get("/manager/scorecard")), ctx.api.get("/opportunities"),
  ]);
  return { overview, inbox: inbox.filter((i) => i.status === "new"), jobs, direction, lessons, scorecard, opps };
}

export async function loadOverview(ctx) {
  return { mode: "overview", ...(await base(ctx)) };
}

export async function loadAgent(ctx, params) {
  const agent = BY_SLUG[params.agent] || AGENTS[0];
  const tab = DETAIL_TABS.some(([t]) => t === params.dtab) ? params.dtab : "overview";
  const [b, runs, perms] = await Promise.all([base(ctx), ctx.api.get("/agent-runs?limit=200"), ctx.api.get("/toolkit/permissions")]);
  return { mode: "agent", agent, tab, ...b, runs: runs.filter((r) => r.agent === agent.name), perms };
}

// ---------- what each agent is doing now ----------
export function agentState(a, d, me) {
  const mine = d.jobs.filter((j) => j.agent === a.name);
  const running = mine.filter((j) => j.status === "running");
  const queued = mine.filter((j) => j.status === "queued");
  let waiting = d.inbox.filter((i) => i.agent === a.name).length;
  if (a.slug === "direction") waiting += d.direction.proposals.filter((p) => p.status === "pending" && p.origin !== "manual").length;
  if (a.slug === "manager") waiting += d.lessons.filter((l) => l.status === "proposed" && l.proposedBy === "Manager agent").length;
  const lastDone = mine.find((j) => j.status === "done");
  if (d.overview.paused) return { label: "Paused", cls: "", dot: "", now: queued.length ? `${plural(queued.length, "job")} waiting until the agents resume` : "Nothing new starts while paused" };
  if (running.length) return { label: "Working", cls: "info", dot: "run", now: running[0].reason };
  if (waiting) return { label: "Waiting for you", cls: "warn", dot: "wait", now: `${plural(waiting, "item")} waiting for ${me}` };
  if (queued.length) return { label: "Queued", cls: "", dot: "", now: `Next: ${queued[0].reason}` };
  return { label: "Idle", cls: "", dot: "", now: lastDone ? `Last: ${lastDone.result || lastDone.reason}` : "No work yet" };
}

function monthStats(a, d, today) {
  const since = addDays(today, -30);
  const jobs = d.jobs.filter((j) => j.agent === a.name && j.on >= since);
  const rows = d.scorecard && d.scorecard.work ? d.scorecard.work.filter((w) => w.agent === a.name) : [];
  const judged = rows.reduce((n, w) => n + w.judged, 0), used = rows.reduce((n, w) => n + w.used, 0);
  return { jobs: jobs.length, done: jobs.filter((j) => j.status === "done").length, failed: jobs.filter((j) => j.status === "failed").length,
    used: judged ? `${Math.round((used / judged) * 100)}%` : null, judged };
}

// ---------- overview ----------
export function overview(d, ctx, st) {
  const o = d.overview;
  const pct = o.aiBudget ? Math.min(100, Math.round((o.aiCallsToday / o.aiBudget) * 100)) : 0;
  return html`
  <div class="page-head">
    <div>
      <h1>Agents</h1>
      <p>Six agents prepare the work. People decide. Open an agent to see what it does, its work, its AI runs and its permissions.</p>
    </div>
    <a class="btn ghost" href="${href("agents", { tab: "work" })}">Records</a>
  </div>
  ${st.err("top")}
  ${st.notices()}
  <section class="card strip" aria-label="Controls">
    <div class="strip-cell">
      <div class="row-flex" style="justify-content:space-between;flex-wrap:nowrap">
        <div><b>${o.paused ? "All agents paused" : "Pause all agents"}</b><p class="meta">${o.paused ? "Jobs wait. People can still work." : "Jobs wait, nothing new starts."}</p></div>
        <button type="button" class="switch" role="switch" aria-checked="${o.paused ? "true" : "false"}" aria-label="Pause all agents" data-act="pause" data-paused="${o.paused ? "" : "1"}"><span></span></button>
      </div>
    </div>
    <div class="strip-cell wide">
      <div class="row-flex" style="justify-content:space-between"><b>AI requests today</b><b class="num">${o.aiCallsToday} of ${o.aiBudget}</b></div>
      <div class="bar" aria-hidden="true"><span style="width:${pct}%"></span></div>
      <p class="meta">One request is one call to the language model. The daily limit keeps costs predictable; when it runs out, agent jobs wait until tomorrow. ${st.ui.open === "settings" ? "" : html`<button type="button" class="link-btn" data-act="open" data-open="settings">Change the limit</button>`}</p>
    </div>
    <div class="strip-cell">
      <b>Daily check</b>
      <p class="meta">${String(o.patrolHour).padStart(2, "0")}:00 every day. ${o.managerMode === "on" ? "The manager agent looks at every open lead and plans the day." : o.managerMode === "shadow" ? "The manager agent decides in shadow mode; the rules queue the work." : "The rules queue the work; the manager agent is off."}
        ${o.lastPatrol ? `Last run ${day(o.lastPatrol, ctx.today)}.` : "Not run yet."}</p>
      <div class="actions" style="margin-top:8px">
        <button type="button" class="btn small" data-act="patrol">Check now</button>
        ${o.queued ? html`<button type="button" class="btn small" data-act="runQueue">Run ${plural(o.queued, "queued job")}</button>` : ""}
      </div>
    </div>
  </section>
  ${st.ui.open === "settings" ? html`
  <section class="card card-body" style="margin-top:16px">
    <form class="panel-form" data-act="settingsForm" data-on="submit">
      <div class="form-grid">
        <label class="field">AI requests per day<input type="number" name="ai_budget" min="0" max="2000" step="1" value="${o.aiBudget}"></label>
        <label class="field">Daily check hour (0-23)<input type="number" name="patrol_hour" min="0" max="23" step="1" value="${o.patrolHour}"></label>
        <label class="field">Manager agent<select name="manager_mode">${["off", "shadow", "on"].map((m) => html`<option ${m === o.managerMode ? "selected" : ""}>${m}</option>`)}</select>
          <span class="hint">Shadow: it decides and compares, but queues nothing.</span></label>
      </div>
      <div class="actions"><button type="submit" class="btn primary">Save</button>${cancelButton}</div>
      ${st.err("settings")}
    </form>
  </section>` : ""}
  <div class="agent-grid">
    ${AGENTS.map((a) => {
      const s = agentState(a, d, ctx.me);
      const m = monthStats(a, d, ctx.today);
      return html`
      <a class="agent-card" href="${href("agents", { agent: a.slug })}">
        <span class="row-flex" style="flex-wrap:nowrap">
          <span class="agent-icon" aria-hidden="true">${icon(a.icon, 20)}</span>
          <b class="agent-name">${a.name}</b>
          <span class="tag ${s.cls}"><span class="dot ${s.dot}" aria-hidden="true"></span>${s.label}</span>
        </span>
        <span class="meta">${a.job}</span>
        <span class="agent-now">${s.now}</span>
        <span class="meta"><b class="num">${m.jobs}</b> ${m.jobs === 1 ? "job" : "jobs"} in the last 30 days${m.used ? html`. <b class="num">${m.used}</b> used as is${m.judged < 5 ? " (small sample)" : ""}` : ""}</span>
      </a>`;
    })}
  </div>
  ${suggestions(d, ctx, st)}`;
}

// ---------- suggestions from agents ----------
function proposalTitle(p) {
  if (p.type === "exclude") return `Leave ${p.target} out of future searches`;
  return p.title || `Change ${p.field}`;
}

function suggestions(d, ctx, st) {
  const byId = Object.fromEntries(d.opps.map((o) => [o.id, o]));
  const props = d.direction.proposals.filter((p) => p.status === "pending");
  const lessons = d.lessons.filter((l) => l.status === "proposed");
  const items = d.inbox.filter((i) => !["draft", "reply"].includes(i.kind));
  const drafts = d.inbox.filter((i) => ["draft", "reply"].includes(i.kind)).length;
  const total = props.length + lessons.length + items.length;
  return html`
  <section aria-labelledby="sug-h" style="margin-top:32px">
    <div class="row-flex" style="justify-content:space-between;margin-bottom:12px">
      <h2 id="sug-h" class="section-title" style="margin:0">Suggestions from agents</h2>
      ${drafts ? html`<a href="${href("outreach")}">${plural(drafts, "email draft or reply", "email drafts or replies")} on the Outreach page</a>` : ""}
    </div>
    ${total ? html`<div class="sug-grid">
      ${props.map((p) => html`
      <div class="suggestion">${icon("bulb", 20)}<div style="flex:1;min-width:0">
        <b>${proposalTitle(p)}</b>
        <p class="meta-3">${p.origin === "manual" ? `Proposed by ${p.proposedBy}` : p.origin === "ai" ? "From the direction agent's review" : "From feedback"}, ${day(p.createdOn, ctx.today)}</p>
        <p class="meta" style="margin-top:6px">${p.rationale}</p>
        <div class="actions" style="margin-top:12px">
          <button type="button" class="btn primary small" data-act="decideProposal" data-id="${p.id}" data-verdict="approve">Apply</button>
          <a class="btn small" href="${href("sources", { tab: "direction" })}">See the change</a>
          <button type="button" class="btn ghost small" data-act="decideProposal" data-id="${p.id}" data-verdict="reject">Dismiss</button>
        </div>
        ${st.err(`prop:${p.id}`)}
      </div></div>`)}
      ${lessons.map((l) => html`
      <div class="suggestion">${icon("bulb", 20)}<div style="flex:1;min-width:0">
        <b>${l.text}</b>
        <p class="meta-3">A lesson for the ${l.agent.toLowerCase()}, proposed by ${l.proposedBy}</p>
        <p class="meta" style="margin-top:6px">${l.why}</p>
        ${st.ui.open === `lesson:${l.id}` ? html`
        <form class="panel-form" style="margin-top:10px" data-act="lessonDecide" data-on="submit" data-id="${l.id}" data-status="proposed">
          <label class="field">Why reject it <span class="hint">The manager agent reads this before it proposes again.</span><textarea name="reason" rows="2"></textarea></label>
          <div class="actions"><button type="submit" class="btn primary small" name="decision" value="reject">Reject lesson</button>${cancelButton}</div>
          ${st.err(`lesson:${l.id}`)}
        </form>` : html`
        <div class="actions" style="margin-top:12px">
          <button type="button" class="btn primary small" data-act="approveLesson" data-id="${l.id}">Approve lesson</button>
          ${openButton(`lesson:${l.id}`, "Reject…", "btn ghost small", `Reject lesson ${l.label}`)}
        </div>
        ${st.err(`lesson:${l.id}`)}`}
      </div></div>`)}
      ${items.map((i) => {
        const o = byId[i.opportunityId];
        return html`
      <div class="suggestion">${icon("bulb", 20)}<div style="flex:1;min-width:0">
        <b>${i.title}</b>
        <p class="meta-3">${i.agent}, ${day(i.on, ctx.today)}${o ? html`. <a href="${linkFor(o)}">${o.company}</a>` : ""}</p>
        ${i.body ? html`<p class="meta" style="margin-top:6px;white-space:pre-wrap">${i.body}</p>` : ""}
        <div class="actions" style="margin-top:12px">
          ${["facts", "contact", "nurture"].includes(i.kind) ? html`<button type="button" class="btn primary small" data-act="item" data-do="apply" data-id="${i.id}" data-hash="${i.payloadHash || ""}">Apply</button>`
            : html`<button type="button" class="btn primary small" data-act="item" data-do="done" data-id="${i.id}">Done</button>`}
          <button type="button" class="btn ghost small" data-act="item" data-do="dismiss" data-id="${i.id}">Dismiss</button>
        </div>
        ${st.err(`item:${i.id}`)}
      </div></div>`;
      })}
    </div>` : html`<section class="card">${emptyState("No suggestions waiting", "Direction changes, lessons for the agents and other prepared items appear here when an agent has one.")}</section>`}
  </section>`;
}

// ---------- one agent ----------
export function detail(d, ctx, st) {
  const a = d.agent;
  const s = agentState(a, d, ctx.me);
  const last = d.runs[0];
  const tabs = DETAIL_TABS.filter(([t]) => t !== "lessons" || LESSON_AGENTS.includes(a.name));
  return html`
  <a class="back" href="${href("agents")}">${icon("back", 16)}Agents</a>
  <div class="page-head" style="margin-top:12px">
    <div class="row-flex" style="align-items:flex-start;flex-wrap:nowrap;gap:16px">
      <span class="agent-icon big" aria-hidden="true">${icon(a.icon, 28)}</span>
      <div>
        <h1>${a.name}</h1>
        <p class="row-flex" style="gap:8px;margin-top:4px"><span class="dot ${s.dot}" aria-hidden="true"></span><b style="font-weight:500">${s.label}</b><span class="meta">${s.now}</span></p>
        <p class="meta-3">${last ? `Last AI run ${day(last.on, ctx.today)}: ${last.model}, prompt ${last.prompt}.` : "No AI run recorded yet."}</p>
      </div>
    </div>
  </div>
  ${st.err("top")}
  ${st.notices()}
  ${tabBar(a.name, tabs, d.tab, {}, { act: "dtab", keys: "dtabkeys" })}
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${{ overview: agentOverview, work: agentWork, runs: agentRuns, permissions: agentPerms, lessons: agentLessons }[d.tab](d, ctx, st)}
  </div>`;
}

function agentOverview(d, ctx) {
  const a = d.agent;
  const m = monthStats(a, d, ctx.today);
  const calls = d.runs.filter((r) => r.on >= addDays(ctx.today, -30)).reduce((n, r) => n + (r.calls || 0), 0);
  const does = (d.overview.agents.find((x) => x.agent === a.name) || {}).does || [];
  return html`
  <div class="two-col">
    <section class="card card-body main prose" aria-label="About this agent">
      <h2 class="section-title">What it does</h2>
      <p>${a.job}</p>
      ${does.length ? html`<h2 class="section-title">Work it is given</h2><ul class="bullets">${does.map((x) => html`<li>${x}</li>`)}</ul>` : ""}
      <h2 class="section-title">When it runs</h2>
      <ul class="bullets">${a.when.map((x) => html`<li>${x}</li>`)}</ul>
      <h2 class="section-title">What it hands back</h2>
      <ul class="bullets">${a.gives.map((x) => html`<li>${x}</li>`)}</ul>
    </section>
    <aside class="side">
      <section class="card card-body">
        <h2 class="section-title">The last 30 days</h2>
        <ul class="kv">
          <li><span>Jobs</span><span>${m.jobs}</span></li>
          <li><span>Done</span><span>${m.done}</span></li>
          <li><span>Failed</span><span>${m.failed}</span></li>
          <li><span>AI requests</span><span>${calls}</span></li>
          <li><span>Used as is</span><span>${m.used ? `${m.used}${m.judged < 5 ? " (small sample)" : ""}` : "Not measured yet"}</span></li>
        </ul>
      </section>
    </aside>
  </div>`;
}

function agentWork(d, ctx) {
  const byId = Object.fromEntries(d.opps.map((o) => [o.id, o]));
  const jobs = d.jobs.filter((j) => j.agent === d.agent.name);
  return html`<section class="card">${jobs.length ? html`<div class="table-wrap"><table class="list">
    <thead><tr><th scope="col">Work</th><th scope="col">Why</th><th scope="col">Status</th><th scope="col">Result</th></tr></thead>
    <tbody>${jobs.map((j) => {
      const o = byId[j.opportunityId];
      return html`<tr><td><b>${j.kind.replace(/_/g, " ")}</b>${o ? html`<a class="meta" href="${linkFor(o)}">${o.company}</a>` : ""}<div class="meta-3">${day(j.on, ctx.today)}, ${j.trigger}${j.requestedBy ? `, asked by ${j.requestedBy}` : ""}</div></td>
        <td>${j.reason}</td><td><span class="tag ${JOB_TAG[j.status] || ""}">${j.status}</span></td>
        <td>${j.error ? html`<span class="bad-text">${j.error}</span>` : j.result}</td></tr>`;
    })}</tbody></table></div>` : emptyState("No work yet", "Jobs appear when the daily check or an event gives this agent work.")}</section>`;
}

function agentRuns(d, ctx) {
  return html`<section class="card">${d.runs.length ? html`<ul class="rows">${d.runs.map((r) => html`<li>
    <a class="what" href="${href("agents", { tab: "runs", run: r.id })}"><b>${r.subject || "Workspace"}</b><span class="meta">${day(r.on, ctx.today)}, ${r.model}, prompt ${r.prompt}, ${plural(r.calls || 0, "call")}</span></a>
    <span class="tag ${r.status === "completed" ? "good" : r.status === "failed" ? "bad" : ""}">${r.status}</span></li>`)}</ul>`
    : emptyState("No AI runs yet", "Each call this agent makes is recorded with its model, prompt version and steps.")}</section>`;
}

function agentPerms(d, ctx, st) {
  const row = d.perms.agents.find((x) => x.agent === d.agent.name);
  if (!row) return html`<section class="card">${emptyState("No permissions recorded", "This agent uses no connector.")}</section>`;
  return html`
  <section class="card">
    <div class="card-head"><div><h2>Connectors this agent may use</h2><p class="meta">"Never" is set in the code. A change needs a reason and can be undone under Records, Permissions.</p></div></div>
    <ul class="rows">${d.perms.connectors.map((c) => {
      const cell = row.connectors[c.name];
      const name = `perm:${row.agent}:${c.name}`;
      return html`<li style="align-items:flex-start"><span class="what"><b>${c.name}</b><span class="meta">${c.about}</span>
        ${st.ui.open === name ? html`<form class="panel-form" style="margin-top:10px" data-act="permForm" data-on="submit input" data-agent="${row.agent}" data-connector="${c.name}">
          <div class="form-grid"><label class="field">Set to<select name="enabled"><option value="true">On</option><option value="false">Off</option><option value="">Back to the default</option></select></label>
          <label class="field">Reason<input name="reason" required></label></div>
          <div class="actions"><button type="submit" class="btn primary" disabled>Save</button>${cancelButton}<span class="missing" data-missing>Give a reason.</span></div>
          ${st.err("perm")}</form>` : ""}</span>
        <span>${cell.editable && st.ui.open !== name ? html`<button type="button" class="btn ghost small" data-act="open" data-open="${name}" aria-label="${c.name}: ${STATE_LABEL[cell.state] || cell.state}. Change">${STATE_LABEL[cell.state] || cell.state}</button>`
          : html`<span class="meta">${STATE_LABEL[cell.state] || cell.state}</span>`}</span></li>`;
    })}</ul>
  </section>`;
}

function agentLessons(d, ctx) {
  const list = d.lessons.filter((l) => l.agent === d.agent.name);
  return html`<section class="card">${list.length ? html`<ul class="rows">${list.map((l) => html`<li style="align-items:flex-start">
    <span class="what"><b>${l.label}: ${l.text}</b><span class="meta">${l.status}. Proposed by ${l.proposedBy}, ${day(l.proposedOn, ctx.today)}. ${l.why}</span>
    ${l.sinceApproved ? html`<span class="meta-3">In use for ${plural(l.sinceApproved.days, "day")}, used ${plural(l.uses, "time")}.</span>` : ""}</span></li>`)}</ul>`
    : emptyState("No lessons for this agent", "Lessons are proposed on the Records, Lessons tab or by the manager agent's weekly review.")}</section>`;
}

// ---------- actions specific to these views ----------
export const teamActions = {
  dtab(t, e, ctx) { ctx.go("agents", { agent: ctx.route.params.agent, dtab: t.dataset.tab }); },
  dtabkeys(t, e, ctx) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    const tabs = [...t.querySelectorAll("[role=tab]")];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const n = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : e.key === "Home" ? 0 : tabs.length - 1;
    ctx.focusAfter = `#${tabs[n].id}`;
    ctx.go("agents", { agent: ctx.route.params.agent, dtab: tabs[n].dataset.tab });
  },
};

