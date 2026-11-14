// The company record (design/redesign/Record.dc.html, redesign decision D4): everything about one opportunity on
// one page, read from GET /api/opportunities/{id}. Where it is in the journey with the Insightly status of each stage,
// the next step (a link to the page where it is done), and five tabs: Brief, Outreach, Conversation, Qualification,
// Activity. The record decides nothing: every action stays on its stage's page.
import { html, icon, day, href, plural, initials, sourceLink } from "../lib.js";
import { enc } from "../api.js";
import { STAGE_LABELS } from "../options.js";
import { crmLinks, emptyState } from "../shell.js";
import { linkFor, pageFor } from "./links.js";
import { readiness, moneyless } from "./qualification.js";
import { suggestion } from "./companies.js";

export const title = "Company record";

const TABS = [["brief", "Brief"], ["outreach", "Outreach"], ["conv", "Conversation"], ["qual", "Qualification"], ["activity", "Activity"]];
// The journey, with what the CRM shows at each stage (config/crm.json: approval stays Active; Qualified converts the
// lead to an opportunity in the Qualification stage).
const JOURNEY = [
  ["researching", "Researching", "Not in Insightly"],
  ["review", "Your decision", "Lead created on Accept"],
  ["identified", "Identified", "Insightly: Identified"],
  ["contacted", "Contacted", "Insightly: Contacted"],
  ["active", "Active", "Insightly: Active"],
  ["approval", "Awaiting approval", "Insightly: Active"],
  ["qualification", "Qualified", "Insightly: opportunity"],
];
const OFF_PATH = { nurture: "Parked", rejected: "Set aside", closed: "Set aside" };
const PAGE_LABEL = { companies: "Companies", outreach: "Outreach", qualification: "Qualification", overview: "Overview" };
const CHANNEL = { email: "Email", linkedin_connect: "LinkedIn request", linkedin_message: "LinkedIn message", share_content: "Share a CGS piece", invite: "Invitation", warm_intro: "Introduction", call: "Phone call" };
const ROUTE = { warm: "Warm route", value_first: "Value first", direct: "Direct" };
const MEANS = { meet: "Wants to meet", need: "Talks about a need", courtesy: "Polite, no ask", defer: "Not now", decline: "Not interested", pending: "Not read yet" };

let rules = null;

export async function load(ctx, params) {
  if (!params.id) return { missing: true };
  const [o, r] = await Promise.all([ctx.api.get(`/opportunities/${enc(params.id)}`), rules ? rules : ctx.api.get("/rules").catch(() => null)]);
  rules = r;
  const tab = TABS.some(([t]) => t === params.tab) ? params.tab : defaultTab(o);
  if (params.tab !== tab) ctx.replace("record", { id: params.id, tab });
  return { o, tab };
}

function defaultTab(o) {
  if (["researching", "review", "rejected"].includes(o.stage)) return "brief";
  if (["identified", "contacted"].includes(o.stage)) return "outreach";
  if (["active", "approval", "qualification"].includes(o.stage)) return "qual";
  return "activity";
}

// ---------- the dates each stage was reached, from the records ----------
function reached(o) {
  const step = (n, re) => [...o.decisions].reverse().find((x) => x.step === n && (!re || re.test(x.decision)));
  const s4 = step(4, /accept/i);
  const s8 = step(8, /approv/i);
  const pack = (o.packs || [])[0];
  const r = o.outreach || {};
  return {
    researching: (o.activity[0] || {}).on, review: o.brief && o.brief.frozenOn, identified: s4 && s4.on, contacted: r.sentOn,
    active: r.replyOn, approval: pack && pack.submittedOn, qualification: s8 && s8.on,
  };
}

export function view(d, ctx) {
  if (d.missing) return html`<div class="page-head"><h1>Company record</h1></div>
    <section class="card">${emptyState("No company chosen", "Open a company from Search, or from its stage's page.")}</section>`;
  const o = d.o;
  const page = pageFor(o.stage);
  const pl = o.outreach && o.outreach.plan;
  return html`
  <nav class="crumbs" aria-label="Breadcrumb"><a href="${linkFor(o)}">${PAGE_LABEL[page] || "Back"}</a>${icon("chevron", 14)}<span aria-current="page">${o.company.name}</span></nav>
  <div class="page-head record-head">
    <div class="row-flex" style="flex-wrap:nowrap;align-items:center;gap:14px">
      <span class="avatar lg ink" aria-hidden="true">${initials(o.company.name)}</span>
      <div><h1>${o.company.name}</h1><p>${[o.company.industry, o.company.hq].filter(Boolean).join(", ") || o.title}${o.company.domain ? html` · ${sourceLink(o.company.domain.startsWith("http") ? o.company.domain : `https://${o.company.domain}`)}` : ""}</p></div>
    </div>
    <div class="head-side">
      <span class="tag ${OFF_PATH[o.stage] ? "" : "info"}">${STAGE_LABELS[o.stage] || o.stage}</span>
      ${pl && pl.plan && ROUTE[pl.plan.approach] ? html`<span class="tag ${pl.plan.approach === "warm" ? "good" : ""}">${ROUTE[pl.plan.approach]}</span>` : ""}
      <span class="row-flex" style="gap:8px"><span class="avatar round sm" aria-hidden="true">${initials(o.owner || "?")}</span><span><span class="meta-3" style="display:block">Owner</span><span class="md">${o.owner || "Not set"}${o.owner === ctx.me ? " (you)" : ""}</span></span></span>
      ${crmLinks(o.crm)}
    </div>
  </div>
  ${journey(o, ctx)}
  ${nextBand(o, ctx)}
  <div class="tabs" role="tablist" aria-label="Record" data-act="tabkeys" data-on="keydown">
    ${TABS.map(([id, label]) => html`<button type="button" role="tab" id="tab-${id}" aria-selected="${d.tab === id ? "true" : "false"}" aria-controls="panel"
      tabindex="${d.tab === id ? "0" : "-1"}" data-act="tab" data-tab="${id}">${label}${count(o, id) !== null ? html`<span class="count">${count(o, id)}</span>` : ""}</button>`)}
  </div>
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${{ brief: briefTab, outreach: outreachTab, conv: convTab, qual: qualTab, activity: activityTab }[d.tab](o, ctx)}
  </div>`;
}

function count(o, tab) {
  if (tab === "conv") return o.notes.length;
  if (tab === "activity") return o.activity.length;
  return null;
}

function journey(o, ctx) {
  const at = JOURNEY.findIndex(([s]) => s === o.stage);
  const when = reached(o);
  const off = OFF_PATH[o.stage];
  return html`<section class="card journey-card" aria-label="Where this company is">
    <ol class="jr">${JOURNEY.map(([s, label, crm], i) => {
      const state = at < 0 ? (when[s] ? "done" : "") : i < at ? "done" : i === at ? "now" : "";
      return html`<li class="${state}" ${state === "now" ? html`aria-current="step"` : ""}>
        <span class="${state === "now" ? "md" : state === "done" ? "" : "meta-3"}">${label}</span>
        <span class="meta-3">${crm}</span>
        ${when[s] ? html`<span class="meta-3 num">${day(when[s], ctx.today)}</span>` : ""}</li>`;
    })}</ol>
    ${off ? html`<p class="meta" style="margin-top:10px">${off}${o.stage === "nurture" ? `: in Insightly as Nurture${o.next.due ? `, until ${day(o.next.due, ctx.today)}` : ""}` : ": Disqualified in Insightly, if it reached it"}.</p>` : ""}
  </section>`;
}

// The next step: what the stage waits for, and the page where it is done.
function nextBand(o, ctx) {
  const link = linkFor(o);
  const N = {
    researching: ["Agent working", "The Research agent is writing the brief", "It comes to Your decision when it is ready.", "Watch the research"],
    review: ["Waiting on a person", "Accept, send back or reject the brief", "The Research agent's suggestion and the evidence are on the decision screen.", "Open the decision"],
    identified: ["Waiting on a person", o.next.action || "Approve the first email or the contact plan", "Nothing goes out until a person approves it.", "Open in Outreach"],
    contacted: ["Waiting for the client", o.next.action || "Waiting for a reply", "A plan stops the moment the client replies.", "Open in Outreach"],
    active: ["In conversation", o.next.action || "Collect the evidence for the six conditions", "The Evidence agent proposes; the owner confirms.", "Open in Qualification"],
    approval: ["Waiting on an approver", "Approve or return the qualification pack", "A colleague decides; nothing goes to Insightly before.", "Open the pack"],
    nurture: ["Parked", o.next.action || "Check back on the review date", "On that date the agents look for news and may draft a reconnect email.", "Open in Parked"],
  }[o.stage];
  if (!N) {
    const settled = o.stage === "qualification";
    return html`<section class="card next-band ${settled ? "done" : ""}" aria-label="Next step">${icon(settled ? "check" : "alert", 16)}
      <span><b>${settled ? "Qualified." : "Set aside."}</b> ${settled ? "It is an opportunity in Insightly and managed there from here on." : "It left the workflow with the reason under Activity."}</span>
      <a class="btn small" href="${link}">Open its list</a></section>`;
  }
  const due = o.next.due ? ` Due ${day(o.next.due, ctx.today)}.` : "";
  // Orange when the move is a person's in this workspace (design: "waiting on you"); grey otherwise.
  const you = ["review", "identified", "approval"].includes(o.stage);
  return html`<section class="card next-band ${you ? "you" : ""}" aria-label="Next step">
    <div style="flex:1 1 320px;min-width:0"><span class="lbl">${N[0]}</span><b style="display:block">${N[1]}</b><span class="meta">${N[2]}${due}</span></div>
    <a class="btn primary" href="${link}">${N[3]}</a>
  </section>`;
}

// ---------- tabs ----------
function briefTab(o, ctx) {
  const b = o.brief;
  if (!b) return html`<section class="card">${emptyState("No brief yet", "The Research agent writes one when the company is picked or added.")}</section>`;
  const s4 = [...o.decisions].reverse().find((x) => x.step === 4);
  const p = b.priority || {};
  const sug = suggestion(b.recommendation, o);
  const facts = b.facts || [];
  const people = (b.contacts && b.contacts.length ? b.contacts : o.contact.name ? [o.contact] : []).slice(0, 3);
  return html`<div class="record-cols">
    <div class="stack">
      <p class="meta-3">${icon("clock", 14)} ${b.frozenOn ? `The brief as it was ${s4 ? `when ${s4.by} decided on ${day(s4.on, ctx.today)}` : `on ${day(b.frozenOn, ctx.today)}`}.` : "The brief as the Research agent wrote it."} Later facts are under Conversation and Qualification.</p>
      <section class="card card-body stack">
        <div class="sb"><h2 class="ttl">The case</h2><span class="tag info">Written by the Research agent</span></div>
        ${b.hypothesis ? html`<div><span class="lbl">The problem they may have</span><p>${b.hypothesis}</p></div>` : ""}
        ${b.relevance ? html`<div><span class="lbl">Why CGS</span><p>${b.relevance}</p></div>` : ""}
        ${b.alternative ? html`<div class="maybe-nothing"><span class="lbl">Why this may be nothing</span><p>${b.alternative}</p></div>` : ""}
      </section>
      <section class="card card-body stack">
        <div class="sb"><h2 class="ttl">Evidence</h2><span class="meta-3">${plural(facts.length, "fact")} from ${plural(new Set(facts.map((f) => f.src)).size, "source")}</span></div>
        ${facts.length ? html`<div class="evs">${facts.map((f) => html`<div class="ev"><span class="md">${f.text}</span>${f.quote ? html`<p class="quote">“${f.quote}”</p>` : ""}
          <span class="meta-3">${sourceLink(f.src, f.date === "undated" ? "undated" : day(f.date, ctx.today))}</span></div>`)}</div>` : html`<p class="meta">No facts recorded.</p>`}
      </section>
    </div>
    <div class="stack">
      <section class="card card-body"><h2 class="ttl">The decision</h2>
        <dl class="dl">
          <dt>${sug.label}</dt><dd>${sug.title}${sug.note ? html`<span class="meta-3" style="display:block">${sug.note}</span>` : ""}</dd>
          <dt>Decided</dt><dd>${s4 ? `${s4.decision} by ${s4.by}, ${day(s4.on, ctx.today)}`
            : ["researching", "review"].includes(o.stage) ? "Not yet" : o.stage === "rejected" ? "Rejected; no decision record is on file" : "Accepted; no decision record is on file"}</dd>
          ${o.rating ? html`<dt>Rating</dt><dd>${o.rating}</dd>` : ""}
          <dt>Next step</dt><dd>${o.next.action || "None set"}</dd>
        </dl></section>
      <section class="card card-body"><h2 class="ttl">Contact</h2>
        ${people.length ? html`<ul class="people">${people.map((x) => html`<li><span class="avatar round sm" aria-hidden="true">${initials(x.name || "?")}</span>
          <span><b>${x.name || "To confirm"}</b><span class="meta" style="display:block">${x.title || ""}</span>${x.source ? html`<span class="meta-3">${x.source}</span>` : ""}</span></li>`)}</ul>`
          : html`<p class="meta">No person named yet.</p>`}
        ${o.contact.confirmedBy ? html`<p class="meta-3">Confirmed by ${o.contact.confirmedBy}${o.contact.confirmedOn ? `, ${day(o.contact.confirmedOn, ctx.today)}` : ""}.</p>` : ""}</section>
      <section class="card card-body"><h2 class="ttl">Priority</h2>
        <dl class="dl">
          ${p.fit ? html`<dt>Fit with CGS</dt><dd>${p.fit}</dd>` : ""}
          ${p.authority ? html`<dt>Contact authority</dt><dd>${p.authority}</dd>` : ""}
          ${p.relationship ? html`<dt>Relationship</dt><dd>${p.relationship}</dd>` : ""}
          ${p.confidence ? html`<dt>Confidence</dt><dd>${p.confidence}</dd>` : ""}
        </dl>
        <p class="meta-3">In words, as the brief has them. What the work would be worth is not shown.</p></section>
    </div>
  </div>`;
}

function outreachTab(o, ctx) {
  const r = o.outreach || {};
  const pl = r.plan;
  const steps = (pl && pl.plan && pl.plan.steps) || [];
  const body = String(r.body || "").replace(/\[\[[^|\]]+\|([^\]]+)\]\]/g, "$1");
  return html`<div class="record-cols">
    <section class="card card-body stack">
      <div class="sb"><h2 class="ttl">Contact plan</h2><span class="meta-3">${pl ? (pl.running ? "Running" : pl.status === "approved" ? "Stopped" : pl.status === "proposed" ? "Proposed" : pl.status) : "None"}</span></div>
      ${steps.length ? html`<ol class="rl">${steps.map((s) => {
        const done = s.status === "done" || s.status === "skipped";
        const now = pl.running && pl.current === s.n;
        return html`<li class="${done ? "done" : ""}"><span class="d ${done ? "done" : now ? "you" : ""}" aria-hidden="true"></span>
          <span><span class="${now ? "you-text md" : "meta-3"}" style="display:block">Day ${s.day}${done ? `, ${s.status} ${day(s.doneOn, ctx.today)}` : s.due ? `, due ${day(s.due, ctx.today)}` : ""}</span>
          <span class="${now ? "md" : ""}">${CHANNEL[s.channel] || s.channel} to ${s.to}</span></span></li>`;
      })}</ol>${pl.plan.stop_when ? html`<p class="meta">${pl.plan.stop_when}</p>` : ""}` : html`<p class="meta">No contact plan: ${r.sentOn ? "a single email was sent." : "the first contact is a single email."}</p>`}
    </section>
    <section class="card card-body stack">
      <div class="sb"><h2 class="ttl">${r.sentOn ? "What was sent" : r.status === "draft" ? "Email draft" : "Email"}</h2>
        ${r.status === "draft" ? html`<span class="tag info">Written by the Outreach agent</span>` : ""}</div>
      ${r.subject ? html`<dl class="email-head"><dt>To</dt><dd>${r.toEmail || o.contact.name || "Not set"}</dd><dt>Subject</dt><dd><b>${r.subject}</b></dd></dl>
        ${body ? html`<div class="email-plain">${body}</div>` : ""}` : html`<p class="meta">No email yet.</p>`}
      <dl class="dl">
        ${r.sentOn ? html`<dt>Sent</dt><dd>${day(r.sentOn, ctx.today)}${r.via ? `, ${r.via.toLowerCase()}` : ""}</dd>` : ""}
        ${r.replyOn ? html`<dt>Reply</dt><dd>${day(r.replyOn, ctx.today)}</dd>` : ""}
        ${r.waiting ? html`<dt>Waiting</dt><dd>${r.waiting.status === "approval" ? `With ${r.waiting.approver} for the second approval` : r.waiting.status === "queued" ? `Goes to ${r.waiting.to} shortly` : r.waiting.status}</dd>` : ""}
        ${r.linkedin && r.linkedin.sent && (r.linkedin.sent.note || r.linkedin.sent.message) ? html`<dt>LinkedIn</dt><dd>${r.linkedin.sent.note ? `Request sent ${day(r.linkedin.sent.note, ctx.today)}` : ""}${r.linkedin.sent.message ? ` Message sent ${day(r.linkedin.sent.message, ctx.today)}` : ""}</dd>` : ""}
      </dl>
      ${["identified", "contacted"].includes(o.stage) ? html`<div><a class="btn small" href="${linkFor(o)}">Open in Outreach</a></div>` : ""}
    </section>
  </div>`;
}

function convTab(o, ctx) {
  const r = o.outreach || {};
  const rows = [...o.notes.map((n) => ({ on: n.on, title: n.type, line: n.text, read: n.analysis && n.analysis.summary }))];
  if (r.replyText && !o.notes.some((n) => n.text === r.replyText)) rows.push({ on: r.replyOn, title: "Client reply", line: r.replyText });
  if (r.sentOn) rows.push({ on: r.sentOn, title: r.status === "followup" ? "Follow-up email" : "First email", line: r.subject || "" });
  rows.sort((a, b) => String(b.on).localeCompare(String(a.on)));
  return html`<div class="stack" style="max-width:920px">
    ${o.replyLabel ? html`<p class="meta">What the latest reply means, by the Evidence agent: <span class="tag">${MEANS[o.replyLabel] || o.replyLabel}</span></p>` : ""}
    ${rows.length ? html`<section class="card"><ul class="rows">${rows.map((x) => html`<li style="align-items:flex-start">
      <span class="what"><b>${x.title}</b><span class="meta" style="white-space:pre-wrap">${x.line}</span>
        ${x.read ? html`<span class="agent-text meta">${icon("sparkle", 14)} ${x.read}</span>` : ""}</span>
      <span class="meta-3">${x.on ? day(x.on, ctx.today) : ""}</span></li>`)}</ul></section>`
      : html`<section class="card">${emptyState("No conversation yet", "Replies and call notes appear here once the client answers.")}</section>`}
    <p class="meta-3">Emails come from the owner's mailbox; call notes are added on the Qualification page. The Evidence agent reads each one and proposes evidence; a person confirms it.</p>
  </div>`;
}

function qualTab(o, ctx) {
  if (!rules || !o.assessment) return html`<section class="card">${emptyState("Not in qualification yet", "Evidence collects once the client replies.")}</section>`;
  const rd = readiness(o, rules);
  const pk = (o.packs || []).filter((p) => p.status === "open").slice(-1)[0];
  return html`<div class="stack">
    ${pk && pk.staleReason ? html`<div class="notice warn">${icon("alert")}<span><b>The pack is out of date.</b> ${pk.staleReason}.</span></div>` : ""}
    <section class="card card-body stack">
      <div class="sb"><h2 class="ttl">The six conditions</h2><span class="${rd.ready ? "good-text" : "meta-3"} md">${rd.head}</span></div>
      <ul class="proofs">${Object.keys(rules.conditions).map((k) => {
        const v = o.conditions[k];
        // The same lookup and words as the Qualification pack (pages/qualification.js).
        const ev = [...o.evidence].reverse().find((e) => e.kind === "cond" && e.key === k && e.value === v);
        return html`<li><span class="md">${rules.conditions[k]}</span>
          <span>${ev ? html`<span class="meta">${ev.quote ? `“${ev.quote}”` : ev.reason || "Confirmed"}</span><span class="meta-3" style="display:block">${ev.how} by ${ev.by}, ${day(ev.on, ctx.today)}</span>`
            : html`<span class="meta-3">${v === true ? "No evidence line is on file" : "No evidence yet"}</span>`}</span>
          <span class="tag ${v === true ? "good" : ""}">${v === true ? "Confirmed" : v === false ? "Not met" : "Missing"}</span></li>`;
      })}</ul>
      ${rd.dims.length || rd.weak ? html`<ul class="gap-lines">${rd.dims.map((x) => html`<li>${x.text}</li>`)}${rd.weak ? html`<li>${rd.weakText}</li>` : ""}</ul>` : ""}
    </section>
    ${pk ? html`<section class="card card-body"><h2 class="ttl">The pack</h2><dl class="dl">
      <dt>Sent</dt><dd>By ${pk.submittedBy}, ${day(pk.submittedOn, ctx.today)}</dd>
      ${pk.exception ? html`<dt>Exception</dt><dd>“${pk.exception}”</dd>` : ""}
      <dt>Service</dt><dd>${(o.need || {}).service || "Not written"}</dd>
      <dt>Revenue estimate</dt><dd>${o.revenue ? "Recorded; the amount is not shown here" : "Not estimated"}</dd>
    </dl></section>` : ""}
    ${["active", "approval"].includes(o.stage) ? html`<div><a class="btn small" href="${linkFor(o)}">Open in Qualification</a></div>` : ""}
  </div>`;
}

function activityTab(o, ctx) {
  if (!o.activity.length) return html`<section class="card">${emptyState("No activity", "Every change to this company is recorded here.")}</section>`;
  return html`<section class="card card-body" style="max-width:760px"><ol class="rl">${[...o.activity].reverse().map((a) => html`<li class="done">
    <span class="d ${a.actor === "a" ? "done" : ""}" aria-hidden="true"></span><span><span class="meta-3" style="display:block">${day(a.on, ctx.today)}${a.actor === "a" ? ", an agent" : ""}</span><span>${moneyless(a.text)}</span></span></li>`)}</ol></section>`;
}

export const actions = {
  tab(t, e, ctx) { ctx.go("record", { id: ctx.route.params.id, tab: t.dataset.tab }); },
  tabkeys(t, e, ctx) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    const tabs = [...t.querySelectorAll("[role=tab]")];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const n = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : e.key === "Home" ? 0 : tabs.length - 1;
    ctx.focusAfter = `#${tabs[n].id}`;
    ctx.go("record", { id: ctx.route.params.id, tab: tabs[n].dataset.tab });
  },
};
