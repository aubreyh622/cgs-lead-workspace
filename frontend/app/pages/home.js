// Overview (design/redesign/Main.dc.html): the day at a glance. Outreach results and the pipeline on top; below, four
// columns: Needs you, Agents working, Waiting on others, Done this week. Every number equals the list it links to:
// Needs you is GET /api/todo (the Inbox), the pipeline counts the same lists the Companies, Outreach and
// Qualification pages show for the whole team, and the columns count the cards they show.
import { html, icon, day, href, plural, initials } from "../lib.js";
import { enc } from "../api.js";
import { leadItems } from "./outreach.js";

export const title = "Overview";

// Colours carry meaning: red is broken, orange waits on you, purple is what the agents prepared.
const TONE = { problem: "bad", waiting: "warn", decide: "warn", check: "info", steer: "info" };
const APPROACH = { warm: "Warm route", value_first: "Value first", direct: "Direct" };
const MEANS = { meet: ["want to meet", "good"], need: ["talk about a need", "info"], defer: ["not now", ""], decline: ["not interested", ""],
  courtesy: ["polite, no ask", ""], pending: ["not read yet", ""] };

export async function load(ctx) {
  const opt = (p, fallback) => ctx.api.get(p).catch(() => fallback);
  const [todo, opps, cands, running, agents, score, inbox] = await Promise.all([
    ctx.api.get("/todo"), ctx.api.get("/opportunities"), opt("/candidates?status=new", []),
    opt("/agents/jobs?status=running&limit=20", []), opt("/agents", { research: { running: [] } }),
    opt("/manager/scorecard", null), opt("/inbox", []),
  ]);
  // The leads whose details the columns need: active and qualified (team), and the person's own before a reply.
  const need = opps.filter((o) => ["active", "qualification"].includes(o.stage)
    || (["identified", "contacted"].includes(o.stage) && o.owner === ctx.me));
  const details = Object.fromEntries((await Promise.all(need.map((o) => opt(`/opportunities/${enc(o.id)}`, null)))).filter(Boolean).map((x) => [x.id, x]));
  return { todo, opps, cands, running, research: (agents.research && agents.research.running) || [], score, inbox, details };
}

// ---------- what each part shows ----------
export function pipeline(opps, cands) {
  const n = (...stages) => opps.filter((o) => stages.includes(o.stage)).length;
  return [
    ["Found", cands.length, "you", href("companies", { tab: "found" })],
    ["Researching", n("researching"), "agent", href("companies", { tab: "research" })],
    ["Your decision", n("review"), "you", href("companies", { tab: "decision" })],
    ["Identified, Contacted", n("identified", "contacted"), "agent", href("companies", { tab: "identified" })],
    ["Active", n("active"), "agent", href("qualification", { tab: "active", team: "1" })],
    ["Awaiting approval", n("approval"), "you", href("qualification", { tab: "approval" })],
    ["Qualified", n("qualification"), "done", href("qualification", { tab: "qualified", team: "1" })],
    ["Parked", n("nurture"), "none", href("qualification", { tab: "parked", team: "1" })],
  ];
}

// Leads of this person where someone else has the move: a second approver, or the client (outreach.js decides).
function waitingOnOthers(d, ctx) {
  const mine = d.opps.filter((o) => ["identified", "contacted"].includes(o.stage) && o.owner === ctx.me);
  return leadItems(mine, d.inbox, d.details, ctx.today).filter((x) => (x.kind === "approve2" && x.who !== ctx.me) || x.kind === "wait").map((x) => {
    const o = x.opp;
    if (x.kind === "approve2") {
      return { tag: "Second approval", tone: "", when: "", title: o.company, line: `Email to ${o.contact.name || "the contact"}`, foot: `${x.who} approves`, mono: initials(x.who),
        href: href("outreach", { tab: "mate", id: o.id }) };
    }
    const sent = x.det && x.det.outreach && x.det.outreach.sentOn;
    return { tag: "Reply due", tone: "", when: o.next.due ? day(o.next.due, ctx.today) : "", title: o.company, line: sent ? `Sent ${day(sent, ctx.today)}` : "Sent",
      foot: o.contact.name || "", mono: initials(o.contact.name || o.company), href: href("outreach", { tab: "wait", id: o.id }) };
  });
}

// Settled in the last seven days: leads qualified, and clients who replied. (No list of everything settled exists:
// REDESIGN_PLAN G5.)
function doneThisWeek(d, ctx) {
  const since = new Date(`${ctx.today}T00:00:00`);
  since.setDate(since.getDate() - 6);
  const recent = (iso) => iso && new Date(`${iso}T00:00:00`) >= since;
  const out = [];
  for (const o of Object.values(d.details)) {
    if (o.stage === "qualification") {
      const dec = [...o.decisions].reverse().find((x) => x.step === 8 && /approv/i.test(x.decision));
      if (dec && recent(dec.on)) out.push({ on: dec.on, tag: "Qualified", title: o.company.name, line: o.crm && o.crm.leadId ? "Opportunity in Insightly" : "Pack approved",
        foot: o.owner || "", mono: initials(o.owner || o.company.name), href: href("qualification", { tab: "qualified", id: o.id, team: "1" }) });
    }
    if (o.stage === "active" && o.outreach && recent(o.outreach.replyOn)) {
      out.push({ on: o.outreach.replyOn, tag: "Client replied", title: o.company.name, line: o.contact.name ? `${o.contact.name} wrote back` : "The client wrote back",
        foot: o.owner || "", mono: initials(o.owner || o.company.name), href: href("qualification", { tab: "active", id: o.id, team: "1" }) });
    }
  }
  return out.sort((a, b) => b.on.localeCompare(a.on)).map((x) => ({ ...x, when: day(x.on, ctx.today) }));
}

function agentsWorking(d) {
  const company = Object.fromEntries(d.opps.map((o) => [o.id, o]));
  return [
    ...d.research.map((name) => ({ tag: "Research", title: name, line: "Writing the research brief", href: href("companies", { tab: "research" }) })),
    ...d.running.map((j) => ({ tag: j.agent.replace(/ agent$/, ""), title: company[j.opportunityId] ? company[j.opportunityId].company : j.kind.replace(/_/g, " "),
      line: j.reason || "Working on it", href: company[j.opportunityId] ? href("companies", { tab: "research" }) : href("agents") })),
  ];
}

// ---------- view ----------
export function view(d, ctx) {
  const working = agentsWorking(d);
  const waiting = waitingOnOthers(d, ctx);
  const done = doneThisWeek(d, ctx);
  const date = new Date(`${ctx.today}T00:00:00`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  const n = d.todo.count;
  const team = ctx.users || [];
  return html`
  <div class="page-head">
    <div>
      <h1>Overview</h1>
      <p>${date}. ${n ? `${plural(n, "thing needs", "things need")} you` : "Nothing needs you"}, and ${working.length ? `${plural(working.length, "agent is", "agents are")} working` : "no agent is working"}.${ctx.config && ctx.config.timeTravel ? " The clock is simulated." : ""}</p>
    </div>
    <div class="head-side">
      <span class="avatars" role="img" aria-label="Team: ${team.join(", ")}">${team.slice(0, 3).map((u) => html`<span class="avatar round ${u === ctx.me ? "ink" : ""}">${initials(u)}</span>`)}
        ${team.length > 3 ? html`<span class="avatar round more">+${team.length - 3}</span>` : ""}</span>
      <a class="btn primary" href="${href("find")}">${icon("search", 16)}Find companies</a>
    </div>
  </div>
  ${d.todo.health.length ? html`<div class="notice warn" role="status" style="margin-bottom:20px">${icon("alert")}<span>
    ${d.todo.health.map((h) => html`<span style="display:block">${h.text} <a href="${href(h.page)}">Open it</a></span>`)}</span></div>` : ""}
  <div class="ov-top">
    ${resultsCard(d)}
    ${pipelineCard(d)}
  </div>
  <div class="kcols">
    ${column("Needs you", "clock", "you-text", n, d.todo.groups.flatMap((g) => g.items).slice(0, 2).map((r) => ({
      tag: r.label, tone: TONE[r.group], title: r.opportunity ? r.opportunity.company : r.title, line: r.opportunity ? stripName(r.title, r.opportunity.company) : r.sub,
      href: href("inbox", { sel: r.key }) })), n > 2 ? { href: href("inbox"), label: `${n - 2} more in Inbox` } : n ? { href: href("inbox"), label: "Open Inbox" } : null,
      "Nothing waits for you.")}
    ${column("Agents working", "sparkle", "agent-text", working.length, working.map((x) => ({ ...x, tone: "info", live: true })), null, "No agent is working right now.")}
    ${column("Waiting on others", "hourglass", "meta", waiting.length, waiting, null, "Nothing of yours waits on a colleague or a client.")}
    ${column("Done this week", "check", "good-text", done.length, done.map((x) => ({ ...x, tone: "good" })), null, "Nothing qualified or replied in the last seven days.")}
  </div>`;
}

// "Meridian Industries: Confirm the group" under the heading "Meridian Industries" reads "Confirm the group".
function stripName(text, name) {
  const t = String(text);
  return t.startsWith(`${name}: `) ? t.slice(name.length + 2) : t;
}

function column(name, ic, cls, count, cards, more, empty) {
  return html`<section class="kcol" aria-label="${name}">
    <h2 class="khead"><span class="${cls}">${icon(ic, 16)}</span>${name}<span class="cnt">${count}</span></h2>
    ${cards.length ? cards.map((c) => html`<a class="kc" href="${c.href}">
      <span class="row-flex"><span class="tag ${c.tone || ""}">${c.live ? html`<span class="dot run" aria-hidden="true"></span>` : ""}${c.tag}</span>${c.when ? html`<span class="meta-3" style="margin-left:auto">${c.when}</span>` : ""}</span>
      <b>${c.title}</b><span class="meta">${c.line}</span>
      ${c.foot ? html`<span class="kc-foot"><span class="avatar round sm" aria-hidden="true">${c.mono}</span><span class="meta-3">${c.foot}</span></span>` : ""}
    </a>`) : html`<p class="meta-3 kempty">${empty}</p>`}
    ${more ? html`<a class="btn small" href="${more.href}">${more.label}</a>` : ""}
  </section>`;
}

// Outreach results: what the API counts. Reply rates exist per contact-plan approach (GET /api/manager/scorecard),
// not per single email or per four-week period (REDESIGN_PLAN G4). What replies said: the agent's reply label on the
// leads in Active, the list "Read replies" opens.
function resultsCard(d) {
  const p = d.score && d.score.plans;
  const rows = p ? p.byApproach.filter((x) => x.plans) : [];
  const active = Object.values(d.details).filter((o) => o.stage === "active");
  const said = Object.entries(MEANS).map(([k, [text, tone]]) => [active.filter((o) => (o.replyLabel || "pending") === k).length, text, tone]).filter(([c]) => c);
  return html`<section class="card ov-card" aria-labelledby="res-h">
    <div class="sb"><h2 id="res-h" class="ttl">${icon("outreach", 16)}Outreach results</h2><span class="meta-3">${d.score ? `Last ${d.score.days} days` : ""}</span></div>
    <div class="stack">
      <span class="lbl">Contact plans by approach</span>
      ${rows.length ? html`<div class="rate-grid" role="img" aria-label="${rows.map((x) => `${APPROACH[x.approach] || x.approach}: ${x.replied} replied of ${x.plans - x.running} ended`).join(". ")}">
        ${rows.map((x) => {
          const ended = x.plans - x.running;
          return html`<span class="md">${APPROACH[x.approach] || x.approach}</span>
            <span class="bar"><span style="width:${ended ? Math.round((x.replied / ended) * 100) : 0}%"></span></span>
            <span class="meta-3 num">${ended ? `${x.replied} of ${ended}` : `${x.running} running`}</span>`;
        })}</div>
        <p class="meta-3">Replies to contact plans that have ended${rows.some((x) => !x.enough) ? "; too few plans yet to compare approaches" : ""}. Single emails are not counted here.</p>`
        : html`<p class="meta">No contact plan has run in this period, so there is no reply rate to show.</p>`}
    </div>
    <div class="row-flex said">
      <span class="lbl" style="margin:0">What replies said</span>
      ${said.length ? said.map(([c, text, tone]) => html`<span class="tag ${tone}">${c} ${text}</span>`) : html`<span class="meta-3">No one is in conversation.</span>`}
      ${active.length ? html`<a class="meta" style="margin-left:auto" href="${href("qualification", { tab: "active", team: "1" })}">Read replies</a>` : ""}
    </div>
  </section>`;
}

function pipelineCard(d) {
  const rows = pipeline(d.opps, d.cands);
  const max = Math.max(1, ...rows.map((r) => r[1]));
  return html`<section class="card ov-card wide" aria-labelledby="pipe-h">
    <div class="sb"><h2 id="pipe-h" class="ttl">${icon("companies", 16)}Pipeline <span class="meta" style="font-weight:400">companies by stage, whole team</span></h2>
      <span class="legend"><span><span class="dot agent" aria-hidden="true"></span>Agent works on it</span><span><span class="dot wait" aria-hidden="true"></span>Waiting on you</span></span></div>
    <nav aria-label="Pipeline stages" class="pipe">
      ${rows.map(([label, count, who, link]) => html`<a class="prl" href="${link}">
        <span class="${who === "you" && count ? "you-text md" : who === "done" ? "good-text md" : "md"}">${label}</span>
        <span class="bar ${who}"><span style="width:${Math.round((count / max) * 100)}%"></span></span>
        <span class="num md">${count}</span>${icon("chevron", 14)}</a>`)}
    </nav>
  </section>`;
}

export const actions = {};
