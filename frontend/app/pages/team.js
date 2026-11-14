// The Agent hub as designed: the agents by stage with what each is doing and its switch, the manager agent, the AI
// requests and the proposals waiting in the Inbox, and a page per agent. Everything shown is computed from the backend's jobs, inbox, runs, proposals and scorecard.
import { html, icon, day, addDays, href, plural, safeUrl } from "../lib.js";
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
    gives: ["A brief in Companies, Your decision; never a decision", "Contacts only when a source names them"],
  },
  {
    slug: "outreach", name: "Outreach agent", icon: "outreach",
    job: "Writes the first email once a lead is accepted, follow-ups when a sent email has no reply, and reconnect notes that cite the company's latest news.",
    when: ["A company is accepted at Step 4", "A sent email has had no reply for the follow-up period", "A lead in nurture reaches its review date"],
    gives: ["Email drafts in Outreach, Needs you; nothing is sent until a person approves", "Citations of the research brief, checked by code"],
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
    when: ["Once a week", "A person asks on the Find page"],
    gives: ["Candidates in Companies, Found, each with the signal and its date"],
  },
  {
    slug: "direction", name: "Direction agent", icon: "compass",
    job: "Keeps what the agents look for up to date: learns from why people skip, accept or reject companies, and turns the CGS library into problems to watch for.",
    when: ["Once a week", "A person asks on the Find page"],
    gives: ["Proposed changes to the research direction; a person approves each one"],
  },
];
const BY_SLUG = Object.fromEntries(AGENTS.map((a) => [a.slug, a]));
const DETAIL_TABS = [["overview", "Overview"], ["work", "Work"], ["runs", "AI runs"], ["permissions", "Permissions"], ["lessons", "Instructions"]];
// Agents a person can pause one at a time (api/jobs.py PAUSABLE): the evidence agent has no jobs on the task board.
const PAUSABLE = ["Research agent", "Outreach agent", "Manager agent", "Discovery agent", "Direction agent"];
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
  const [b, todo] = await Promise.all([base(ctx), ctx.api.get("/todo").catch(() => null)]);
  return { mode: "overview", ...b, todo };
}

export async function loadAgent(ctx, params) {
  const agent = BY_SLUG[params.agent] || AGENTS[0];
  const tab = DETAIL_TABS.some(([t]) => t === params.dtab) ? params.dtab : "overview";
  const [b, runs, perms] = await Promise.all([base(ctx), ctx.api.get("/agent-runs?limit=200"), ctx.api.get("/toolkit/permissions")]);
  const trials = agent.slug === "research" ? await ctx.api.get("/agents/research/try").catch((e) => ({ error: e.message })) : null;
  return { mode: "agent", agent, tab, ...b, runs: runs.filter((r) => r.agent === agent.name), perms, trials };
}

// ---------- what waits for a person ----------
// How many items one agent prepared that wait for this person. The dock adds these up, so its "waiting for you" equals
// the sum of the agent rows it opens.
export function agentWaiting(a, inbox, direction, lessons) {
  let n = inbox.filter((i) => i.status === "new" && i.agent === a.name).length;
  if (a.slug === "direction") n += direction.proposals.filter((p) => p.status === "pending" && p.origin !== "manual").length;
  if (a.slug === "manager") n += lessons.filter((l) => l.status === "proposed" && l.proposedBy === "Manager agent").length;
  return n;
}

// ---------- what each agent is doing now ----------
export function agentState(a, d, me) {
  const mine = d.jobs.filter((j) => j.agent === a.name);
  const running = mine.filter((j) => j.status === "running");
  const queued = mine.filter((j) => j.status === "queued");
  const waiting = agentWaiting(a, d.inbox, d.direction, d.lessons);
  const lastDone = mine.find((j) => j.status === "done");
  if (d.overview.paused) return { label: "Paused", cls: "", dot: "", now: queued.length ? `${plural(queued.length, "job")} waiting until the agents resume` : "Nothing new starts while paused" };
  if ((d.overview.pausedAgents || []).includes(a.name)) {
    return { label: "Paused", cls: "", dot: "", now: queued.length ? `${plural(queued.length, "job")} waiting until a person resumes it` : "Paused by a person; the other agents keep working" };
  }
  if (running.length) return { label: "Working", cls: "info", dot: "run", now: running[0].reason };
  // research runs on its own threads, not as board jobs (GET /api/agents, research)
  const research = a.name === "Research agent" && d.overview.research;
  if (research && research.running.length) {
    return { label: "Working", cls: "info", dot: "run", now: `Researching ${research.running.slice(0, 3).join(", ")}${research.running.length > 3 ? ` and ${research.running.length - 3} more` : ""}` };
  }
  if (waiting) return { label: "Waiting for you", cls: "warn", dot: "wait", now: `${plural(waiting, "item")} waiting for ${me}` };
  if (queued.length) return { label: "Queued", cls: "", dot: "", now: `Next: ${queued[0].reason}` };
  const lastResearch = research && research.last
    ? `Last: ${research.last.status === "done" ? "brief ready for" : "research stopped for"} ${research.last.company}` : "";
  return { label: "Idle", cls: "", dot: "", now: lastDone ? `Last: ${lastDone.result || lastDone.reason}` : lastResearch || "No work yet" };
}

function monthStats(a, d, today) {
  const since = addDays(today, -30);
  const jobs = d.jobs.filter((j) => j.agent === a.name && j.on >= since);
  const rows = d.scorecard && d.scorecard.work ? d.scorecard.work.filter((w) => w.agent === a.name) : [];
  const judged = rows.reduce((n, w) => n + w.judged, 0), used = rows.reduce((n, w) => n + w.used, 0);
  return { jobs: jobs.length, done: jobs.filter((j) => j.status === "done").length, failed: jobs.filter((j) => j.status === "failed").length,
    used: judged ? `${Math.round((used / judged) * 100)}%` : null, judged };
}

// ---------- overview (design/redesign/Agents.dc.html) ----------
// The agents by the stage of the work they serve, each with its state, what it is doing and its pause switch; then the
// manager agent, today's AI requests and the agents' proposals waiting in the Inbox. The design's Workflows tab has no
// backend (REDESIGN_PLAN G18).
const STAGES = [
  ["Find companies", ["discovery", "direction"]],
  ["Research", ["research"]],
  ["Reach out", ["outreach"]],
  ["Qualify", ["evidence"]],
];

// How people received a stage's work (GET /api/manager/scorecard): results used of those judged, or for the Evidence
// agent the proposals people confirmed of those decided.
export function stageMetric(slugs, scorecard) {
  if (!scorecard || scorecard.error) return null;
  if (slugs.includes("evidence")) {
    const t = scorecard.evidence && scorecard.evidence.totals;
    return t && t.decided ? { label: "Proposals people confirmed", n: t.accepted, of: t.decided } : { label: "Proposals people confirmed", n: 0, of: 0 };
  }
  const names = slugs.map((s) => BY_SLUG[s].name);
  const rows = (scorecard.work || []).filter((w) => names.includes(w.agent));
  return { label: "Results people used", n: rows.reduce((n, w) => n + w.used, 0), of: rows.reduce((n, w) => n + w.judged, 0) };
}

function agentRow(a, d, ctx) {
  const s = agentState(a, d, ctx.me);
  const o = d.overview;
  const pausable = PAUSABLE.includes(a.name);
  const paused = (o.pausedAgents || []).includes(a.name);
  return html`<li class="agent-row">
    <span class="agent-row-text">
      <span class="row-flex" style="gap:8px"><a class="md" href="${href("agents", { agent: a.slug })}">${a.name.replace(/ agent$/, "")}</a>
        <span class="tag ${s.cls}">${s.dot === "run" ? html`<span class="dot run" aria-hidden="true"></span>` : ""}${s.label}</span></span>
      <span class="${s.label === "Paused" ? "you-text" : "meta"}">${s.now}</span>
    </span>
    ${pausable ? html`<button type="button" class="switch" role="switch" aria-checked="${paused || o.paused ? "false" : "true"}" aria-label="${a.name}"
        ${o.paused ? html`aria-disabled="true" title="Turned off with All agents"` : html`title="${paused ? "Turn it back on" : "Pause it"}"`}
        data-act="pauseAgent" data-agent="${a.name}" data-paused="${paused ? "" : "1"}"><span></span></button>`
      : html`<span class="meta-3" title="It runs when notes or a reply are saved, not from the task board">Runs on notes</span>`}
  </li>`;
}

export function overview(d, ctx, st) {
  const o = d.overview;
  const working = AGENTS.filter((a) => agentState(a, d, ctx.me).dot === "run").length;
  const pct = o.aiBudget ? Math.min(100, Math.round((o.aiCallsToday / o.aiBudget) * 100)) : 0;
  const proposals = d.todo ? d.todo.groups.flatMap((g) => g.items).filter((r) => r.group === "steer" || r.group === "check").length : 0;
  const manager = BY_SLUG.manager;
  return html`
  <div class="page-head">
    <div>
      <h1>Agent hub <span class="tag ${o.paused ? "warn" : working ? "info" : ""}" style="vertical-align:middle;margin-left:8px">${working && !o.paused ? html`<span class="dot run" aria-hidden="true"></span>` : ""}${o.paused ? "All paused" : `${working} of ${AGENTS.length} working`}</span></h1>
      <p>Six agents prepare the work. People decide. Open an agent to see what it does, its work, its AI runs and its permissions.</p>
    </div>
    <div class="head-side">
      <a class="btn ghost" href="${href("agents", { tab: "work" })}">Records</a>
      <span class="row-flex" style="gap:10px"><span class="meta">All agents</span>
        <button type="button" class="switch" role="switch" aria-checked="${o.paused ? "false" : "true"}" aria-label="All agents" data-act="pause" data-paused="${o.paused ? "" : "1"}"><span></span></button></span>
    </div>
  </div>
  ${st.err("top")}
  ${st.notices()}
  ${o.paused ? html`<div class="notice warn" role="status" style="margin-bottom:20px">${icon("alert")}<span><b>All agents are paused.</b> Nothing new is found, researched or drafted. Work already in your Inbox stays there.
    <button type="button" class="btn primary small" style="margin-left:10px" data-act="pause" data-paused="">Turn agents back on</button></span></div>` : ""}
  <div class="stage-cards">
    ${STAGES.map(([name, slugs]) => {
      const m = stageMetric(slugs, d.scorecard);
      return html`<section class="card stage-card" aria-label="${name}">
        <h2 class="ttl">${name}</h2>
        ${m ? html`<div class="stack" style="gap:6px"><span class="meta-3">${m.label}, last ${(d.scorecard && d.scorecard.days) || 60} days</span>
          <div class="row-flex" style="flex-wrap:nowrap;gap:10px"><span class="bar" style="flex:1;margin:0"><span style="width:${m.of ? Math.round((m.n / m.of) * 100) : 0}%"></span></span>
            <span class="md num">${m.of ? `${m.n} of ${m.of}` : "None yet"}</span></div></div>` : ""}
        <ul class="agent-rows">${slugs.map((s) => agentRow(BY_SLUG[s], d, ctx))}</ul>
      </section>`;
    })}
  </div>
  <div class="hub-row">
    <section class="card hub-card wide" aria-labelledby="mgr-h">
      <div class="sb"><h2 id="mgr-h" class="ttl">Manager agent <span class="tag">${o.managerMode === "on" ? "On" : o.managerMode === "shadow" ? "Shadow" : "Off"}</span></h2>
        ${PAUSABLE.includes(manager.name) ? html`<button type="button" class="switch" role="switch" aria-checked="${(o.pausedAgents || []).includes(manager.name) || o.paused ? "false" : "true"}" aria-label="Manager agent"
          ${o.paused ? html`aria-disabled="true"` : ""} data-act="pauseAgent" data-agent="${manager.name}" data-paused="${(o.pausedAgents || []).includes(manager.name) ? "" : "1"}"><span></span></button>` : ""}</div>
      <p class="meta">${o.managerMode === "on" ? "Looks at every open lead at the daily check and plans the next action for each." : o.managerMode === "shadow" ? "Decides in shadow mode and compares; the rules queue the work." : "Off: the rules queue the work."} ${agentState(manager, d, ctx.me).now}.</p>
      <dl class="dl"><dt>Daily check</dt><dd>${String(o.patrolHour).padStart(2, "0")}:00. ${o.lastPatrol ? `Last run ${day(o.lastPatrol, ctx.today)}.` : "Not run yet."}</dd></dl>
      <div class="actions"><button type="button" class="btn small" data-act="patrol">Check now</button>
        ${o.queued ? html`<button type="button" class="btn small" data-act="runQueue">Run ${plural(o.queued, "queued job")}</button>` : ""}</div>
    </section>
    <section class="card hub-card" aria-labelledby="ai-h">
      <h2 id="ai-h" class="ttl">AI requests today</h2>
      <div class="row-flex" style="align-items:baseline;gap:8px"><span class="n2">${o.aiCallsToday}</span><span class="meta">of ${o.aiBudget}</span></div>
      <div class="bar" role="img" aria-label="${o.aiCallsToday} of ${o.aiBudget} used"><span style="width:${pct}%"></span></div>
      ${st.ui.open === "settings" ? "" : html`<button type="button" class="link-btn" data-act="open" data-open="settings">Change the limit</button>`}
    </section>
    <section class="card hub-card" aria-labelledby="prop-h">
      <h2 id="prop-h" class="ttl">Agent proposals</h2>
      <div class="row-flex" style="align-items:baseline;gap:8px"><span class="n2">${proposals}</span><span class="meta">waiting in your Inbox</span></div>
      <a class="btn small" href="${href("inbox", { tab: "proposal" })}" style="align-self:flex-start">Review in Inbox</a>
    </section>
  </div>
  ${st.ui.open === "settings" ? html`
  <section class="card card-body" style="margin-top:16px">
    <form class="panel-form" data-act="settingsForm" data-on="submit">
      <div class="form-grid">
        <label class="field">AI requests per day<input type="number" name="ai_budget" min="0" max="2000" step="1" value="${o.aiBudget}"></label>
        <label class="field">Daily check hour (0-23)<input type="number" name="patrol_hour" min="0" max="23" step="1" value="${o.patrolHour}"></label>
        <label class="field">Manager agent<select name="manager_mode">${["off", "shadow", "on"].map((m) => html`<option ${m === o.managerMode ? "selected" : ""}>${m}</option>`)}</select>
          <span class="hint">Shadow: it decides and compares, but queues nothing.</span></label>
        <label class="field">First contact after Step 4<select name="first_contact">${[["email", "The first email"], ["plan", "A contact plan"]].map(([v, l]) => html`<option value="${v}" ${v === o.firstContact ? "selected" : ""}>${l}</option>`)}</select>
          <span class="hint">A contact plan: the Outreach agent proposes whom to approach and how; the owner approves it before any step.</span></label>
      </div>
      <div class="actions"><button type="submit" class="btn primary">Save</button>${cancelButton}</div>
      ${st.err("settings")}
    </form>
  </section>` : ""}`;
}

// ---------- one agent ----------
export function detail(d, ctx, st) {
  const a = d.agent;
  const last = d.runs[0];
  const s = agentState(a, d, ctx.me);
  // Some work (research picked by a person) runs outside the task board, so "no jobs" can still mean AI runs happened.
  if (s.now === "No work yet" && last) s.now = `Last worked ${day(last.on, ctx.today)}`;
  const tabs = DETAIL_TABS;
  const paused = (d.overview.pausedAgents || []).includes(a.name);
  return html`
  <a class="back" href="${href("agents")}">${icon("back", 16)}Agents</a>
  <div class="page-head" style="margin-top:12px">
    <div class="row-flex" style="align-items:flex-start;flex-wrap:nowrap;gap:16px">
      <span class="agent-icon big" aria-hidden="true">${icon(a.icon, 28)}</span>
      <div>
        <h1>${a.name}</h1>
        <p class="row-flex" style="gap:8px;margin-top:4px"><b class="with-dot ${s.dot}" style="font-weight:500">${s.label}</b><span class="meta">${s.now}</span></p>
        <p class="meta-3">${last ? `Last AI run ${day(last.on, ctx.today)}: ${runModel(last.model)}, instructions version ${runVersion(last.prompt)}.` : "No AI run recorded yet."}</p>
      </div>
    </div>
    ${PAUSABLE.includes(a.name) ? html`
    <div class="row-flex" style="flex-wrap:nowrap;gap:12px">
      ${d.trials && st.ui.open !== "try" ? openButton("try", "Try it on a company", "btn") : ""}
      <span class="meta" style="text-align:right">${paused ? "Paused. Its jobs wait on the task board." : "Pause this agent only. Its jobs wait; the others keep working."}</span>
      <button type="button" class="switch" role="switch" aria-checked="${paused ? "true" : "false"}" aria-label="Pause the ${a.name.toLowerCase()}" data-act="pauseAgent" data-agent="${a.name}" data-paused="${paused ? "" : "1"}"><span></span></button>
    </div>` : html`<p class="meta" style="max-width:280px">Not on the task board: it runs when a person saves notes or a reply is recorded, so it has no pause switch.</p>`}
  </div>
  ${st.err("top")}
  ${st.notices()}
  ${d.trials ? trialsBlock(d, ctx, st) : ""}
  ${tabBar(a.name, tabs, d.tab, {}, { act: "dtab", keys: "dtabkeys" })}
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${{ overview: agentOverview, work: agentWork, runs: agentRuns, permissions: agentPerms, lessons: agentLessons }[d.tab](d, ctx, st)}
  </div>`;
}

// ----- "Try it on a company" (research agent): the brief is shown here and saved nowhere else -----
function trialsBlock(d, ctx, st) {
  const list = d.trials.error ? [] : d.trials;
  const live = ctx.config && ctx.config.appMode === "live";
  if (st.ui.open !== "try" && !list.length && !d.trials.error) return "";
  return html`
  <section class="card" aria-labelledby="try-h" style="margin-bottom:24px">
    <div class="card-head"><div><h2 id="try-h">Try it on a company</h2>
      <p class="meta">The research agent researches a company you name, with its usual tools, limits, checks and approved instructions. You see the brief here. Nothing else is saved: no company, lead, CRM record or email. The AI run is logged under AI runs.</p></div></div>
    ${st.ui.open === "try" ? html`<div class="card-body">${live ? html`
      <form class="panel-form" data-act="tryForm" data-on="submit input">
        <div class="form-grid">
          <label class="field">Company<input name="company" required></label>
          <label class="field">Website <span class="hint">For example arbor-fiberworks.com</span><input name="domain" required></label>
        </div>
        <div class="actions"><button type="submit" class="btn primary" disabled>Research it</button>${cancelButton}<span class="missing" data-missing>Give the company and its website.</span></div>
        ${st.err("try")}
      </form>` : html`
      <div class="notice">${icon("alert")}<span>Try it runs the real research agent, so it needs live mode. In this demo, briefs come from prepared examples.</span></div>
      <div class="actions" style="margin-top:12px">${cancelButton}</div>`}</div>` : ""}
    ${d.trials.error ? html`<div class="card-body"><div class="notice bad">${icon("alert")}<span>${d.trials.error}</span></div></div>` : ""}
    ${list.length ? html`<ul class="rows">${list.map((t) => trialRow(t, ctx))}</ul>` : ""}
  </section>`;
}

export function trialRow(t, ctx) {
  const b = t.brief;
  return html`<li style="align-items:flex-start">
    <span class="what"><b>${t.company}</b><span class="meta">${t.domain}, ${day(t.on, ctx.today)}</span>
      ${t.status === "running" ? html`<span class="meta">Researching. This takes up to about 8 minutes; the page checks again on its own.</span>` : ""}
      ${t.status === "failed" ? html`<span class="bad-text">${t.error || "No brief was produced."}</span>` : ""}
      ${b ? html`<div class="trial-brief">
        ${b.hypothesis ? html`<p><b>Possible problem:</b> ${b.hypothesis}</p>` : ""}
        ${b.facts && b.facts.length ? html`<ol class="sources">${b.facts.map((f) => html`<li>${f.text} <span class="meta-3">${safeUrl(f.src)
          ? html`<a href="${safeUrl(f.src)}" target="_blank" rel="noopener noreferrer">${f.src}</a>` : f.src}${f.date ? `, ${f.date}` : ""}</span></li>`)}</ol>` : html`<p class="meta">No facts were kept.</p>`}
        ${b.flags && b.flags.length ? html`<ul class="bullets">${b.flags.map((x) => html`<li><b>${x.type}:</b> ${x.text}</li>`)}</ul>` : ""}
        ${t.person ? html`<p class="meta">Contact named by a source: ${t.person.name}${t.person.title ? `, ${t.person.title}` : ""}</p>` : ""}
        ${b.recommendation ? html`<p class="meta">The agent would suggest: ${b.recommendation.decision}. ${b.recommendation.reason}</p>` : ""}
      </div>` : ""}</span>
    <span class="tag ${t.status === "completed" ? "good" : t.status === "failed" ? "bad" : "info"}">${t.status === "completed" ? "Brief ready" : t.status === "failed" ? "Failed" : "Running"}</span>
  </li>`;
}

// What an AI run's model and prompt fields mean, in plain words.
function runModel(model) {
  if (!model) return "model not recorded";
  if (model === "demo-fixture") return "a prepared demo example";
  if (model.startsWith("demo") || model.startsWith("rules")) return "demo text rules, no AI";
  return model;
}

function runVersion(prompt) {
  const v = String(prompt || "").match(/(\d+(?:\.\d+)*)\s*$/);
  return v ? v[1] : "not recorded";
}

function agentOverview(d, ctx) {
  const a = d.agent;
  const m = monthStats(a, d, ctx.today);
  const recent = d.runs.filter((r) => r.on >= addDays(ctx.today, -30));
  const calls = recent.reduce((n, r) => n + (r.calls || 0), 0);
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
          <li><span>AI runs</span><span>${recent.length}</span></li>
          <li><span>AI requests</span><span>${calls}</span></li>
          <li><span>Task-board jobs</span><span>${m.jobs}</span></li>
          <li><span>Jobs done</span><span>${m.done}</span></li>
          <li><span>Jobs failed</span><span>${m.failed}</span></li>
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

// Instructions are lessons: a person writes one, an admin approves it, and only then it joins the agent's prompt (at most five).
function agentLessons(d, ctx, st) {
  const a = d.agent;
  const list = d.lessons.filter((l) => l.agent === a.name);
  const active = list.filter((l) => l.status === "approved");
  return html`
  <div class="two-col">
    <section class="card main" aria-labelledby="ins-h">
      <div class="card-head"><div><h2 id="ins-h">Instructions in its prompt</h2>
        <p class="meta">${plural(active.length, "approved instruction")} of at most five. They come after the rules and never loosen them. Approve, reject or retire them under <a href="${href("agents", { tab: "lessons" })}">Records, Lessons</a>.</p></div></div>
      ${list.length ? html`<ul class="rows">${list.map((l) => html`<li style="align-items:flex-start">
        <span class="what"><b>${l.label}: ${l.text}</b><span class="meta">${l.status}. Proposed by ${l.proposedBy}, ${day(l.proposedOn, ctx.today)}. ${l.why}</span>
        ${l.sinceApproved ? html`<span class="meta-3">In use for ${plural(l.sinceApproved.days, "day")}, used ${plural(l.uses, "time")}.</span>` : ""}</span>
        <span class="tag ${l.status === "approved" ? "good" : l.status === "proposed" ? "warn" : ""}">${l.status}</span></li>`)}</ul>`
        : emptyState("No instructions yet", "Write one here. The manager agent's weekly review may also propose some.")}
    </section>
    <aside class="side">
      <section class="card card-body stack">
        <h2 class="section-title" style="margin:0">Write an instruction</h2>
        <p class="meta">One or two sentences to the ${a.name.toLowerCase()}: in which situation, what to do. Code refuses text that tells it to ignore or skip a rule, or that mentions fees.</p>
        <form class="panel-form" data-act="lessonForm" data-on="submit input">
          <input type="hidden" name="agent" value="${a.name}">
          <label class="field">The instruction<textarea name="text" rows="3" required></textarea></label>
          <label class="field">What you saw that it answers<textarea name="why" rows="2" required></textarea></label>
          <div class="actions"><button type="submit" class="btn primary" disabled>Propose</button><span class="missing" data-missing>Write the text and why.</span></div>
          ${st.err("lesson")}
        </form>
      </section>
    </aside>
  </div>`;
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

