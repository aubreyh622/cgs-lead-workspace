// Companies: Step 1 candidates, Steps 2-3 research, Step 4 decision, then accepted and set-aside lists.
import { html, icon, day, addDays, daysBetween, href, plural, initials, safeUrl, sourceLink, linkedinSearch } from "../lib.js";
import { enc, ApiError } from "../api.js";
import { SKIP_REASONS, REJECT_REASONS, ACCEPT_REASONS, SCOPES, STAGE_LABELS, RECOMMENDATION_LABELS } from "../options.js";
import { emptyState, inlineError, dropToast } from "../shell.js";
import { linkFor } from "./links.js";
import { UNDO_SECONDS, offerUndo } from "../undo.js";

export const title = "Companies";

const TABS = [
  ["new", "New candidates"],
  ["research", "Being researched"],
  ["review", "Ready to decide"],
  ["accepted", "Accepted"],
  ["aside", "Set aside"],
];
const ACCEPTED = ["identified", "contacted", "active", "approval", "qualification", "nurture", "closed"];

// What the person has open on this page. It survives a redraw and is cleared when they move on.
const ui = {
  selected: new Set(),   // candidate ids ticked for research
  skipping: null,        // candidate id with the skip form open
  mode: null,            // review: "accept" | "return" | "reject"
  modeFor: null,         // the opportunity that form belongs to
  error: null,           // { where, message }
  pollTimer: null,
};

function resetForms() {
  ui.mode = null; ui.modeFor = null; ui.error = null; ui.skipping = null;
}

// ---------- data ----------
// Checks already made for a company in this session, so picking it again shows them at once. The CRM check asks
// Insightly, so it is kept for a few minutes and not fetched ahead; the check for other CGS work reads the workspace.
const checks = { crm: new Map(), related: new Map() };
const CHECK_TTL = 5 * 60 * 1000;
const known = (map, id) => { const x = map.get(id); return x && Date.now() - x.at < CHECK_TTL ? x.value : null; };
const keep = (map, id, value) => { if (value && !value.error) map.set(id, { at: Date.now(), value }); };

export async function load(ctx, params) {
  const [cands, opps] = await Promise.all([ctx.api.get("/candidates"), ctx.api.get("/opportunities")]);
  const review = opps.filter((o) => o.stage === "review");
  const tab = TABS.some(([t]) => t === params.tab) ? params.tab : review.length ? "review" : "new";
  if (params.tab !== tab) ctx.replace("companies", { ...params, tab });  // the tab chosen now stays when the page redraws
  const data = { tab, cands, opps, review };
  if (tab === "research") {
    const ids = opps.filter((o) => o.stage === "researching").map((o) => o.id);
    data.researching = await Promise.all(ids.map((id) => ctx.api.get(`/opportunities/${enc(id)}`)));
  }
  if (tab === "review" && review.length) {
    // the research agent's suggestion for each company in the list (DESIGN: "Suggested: accept" on each item)
    const briefs = await Promise.all(review.map((o) => ctx.api.get(`/opportunities/${enc(o.id)}`).catch(() => null)));
    data.briefs = Object.fromEntries(briefs.filter(Boolean).map((b) => [b.id, b]));
    data.suggested = Object.fromEntries(briefs.filter(Boolean).map((b) => [b.id, b.brief && b.brief.recommendation ? b.brief.recommendation : null]));
    const id = review.some((o) => o.id === params.id) ? params.id : review[0].id;
    const [detail, crm, related] = await Promise.all([
      ctx.api.get(`/opportunities/${enc(id)}`),
      ctx.api.get(`/opportunities/${enc(id)}/crm`).catch((e) => ({ error: e.message })),
      ctx.api.get(`/opportunities/${enc(id)}/related`).catch((e) => ({ error: e.message })),
    ]);
    data.current = detail;
    data.crm = crm;
    data.related = related;
    keep(checks.crm, id, crm);
    keep(checks.related, id, related);
    if (ui.modeFor !== id) resetForms();
  }
  if (tab === "aside") data.feedback = await ctx.api.get("/feedback");
  return data;
}

// Another company in Ready to decide: its brief is already here, so it is drawn at once. Checks not made yet say so,
// and the full load fills them in (slots).
export function instant(ctx, params, last) {
  if (!last || last.tab !== "review" || params.tab !== "review" || !last.briefs) return null;
  const id = params.id;
  if (!id || !last.briefs[id] || !last.review.some((o) => o.id === id)) return null;
  if (ui.modeFor !== id) resetForms();
  return { ...last, current: last.briefs[id], crm: known(checks.crm, id) || { pending: true }, related: known(checks.related, id) || { pending: true } };
}

export function slots(d, ctx) {
  if (d.tab !== "review" || !d.current) return {};
  const [value, why] = relationshipOf(d.crm, d.related);
  return {
    checks: html`${crmCheck(d.crm, d.current)}${relatedCheck(d.related, ctx)}`,
    relationship: html`<div class="label">Relationship</div><div class="value">${value}</div><div class="meta-3">${why}</div>`,
    duplicates: duplicateCheck(d),
  };
}

export function changed(early, d) {
  const ids = (x) => x.review.map((o) => o.id).join(",");
  return early.tab !== d.tab || ids(early) !== ids(d) || JSON.stringify(early.current) !== JSON.stringify(d.current);
}

function counts(d) {
  return {
    new: d.cands.filter((c) => c.status === "new").length,
    research: d.opps.filter((o) => o.stage === "researching").length,
    review: d.review.length,
    accepted: d.opps.filter((o) => ACCEPTED.includes(o.stage)).length,
    aside: d.opps.filter((o) => o.stage === "rejected").length + d.cands.filter((c) => c.status === "skipped").length,
  };
}

// ---------- view ----------
export function view(d, ctx) {
  const n = counts(d);
  return html`
  <div class="page-head">
    <div>
      <h1>Companies</h1>
      <p>Pick which candidates get a research brief, then decide on each researched company.</p>
    </div>
    <a class="btn" href="${href("sources")}">Find more companies</a>
  </div>
  <div class="tabs" role="tablist" aria-label="Company pipeline" data-act="tabkeys" data-on="keydown">
    ${TABS.map(([id, label], i) => html`
      ${i === 3 ? html`<span class="gap" aria-hidden="true"></span>` : ""}
      <button type="button" role="tab" id="tab-${id}" aria-selected="${d.tab === id ? "true" : "false"}" aria-controls="panel"
        tabindex="${d.tab === id ? "0" : "-1"}" data-act="tab" data-tab="${id}">${label}<span class="count">${n[id]}</span></button>
      ${i < 2 ? html`<span class="sep" aria-hidden="true">${icon("chevron", 16)}</span>` : ""}`)}
  </div>
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${d.tab === "new" ? candidatesView(d, ctx) : ""}
    ${d.tab === "research" ? researchView(d, ctx) : ""}
    ${d.tab === "review" ? reviewView(d, ctx) : ""}
    ${d.tab === "accepted" ? acceptedView(d, ctx) : ""}
    ${d.tab === "aside" ? asideView(d, ctx) : ""}
  </div>
  ${d.tab === "new" && ui.selected.size ? html`
    <div class="selection-bar" role="region" aria-label="Selection">
      <span>${ui.selected.size} selected</span>
      <button type="button" class="btn primary small" data-act="researchSelected">Research these</button>
      <button type="button" class="btn ghost small" data-act="clearSelection">Clear</button>
    </div>` : ""}`;
}

// ----- Step 1 -----
function candidatesView(d, ctx) {
  const fresh = d.cands.filter((c) => c.status === "new");
  for (const id of [...ui.selected]) if (!fresh.some((c) => c.id === id)) ui.selected.delete(id);
  const rounds = [...new Set(fresh.map((c) => c.round))];
  const all = fresh.length > 0 && fresh.every((c) => ui.selected.has(c.id));
  return html`
  <section class="card" aria-labelledby="cands-h">
    <div class="card-head">
      <div>
        <h2 id="cands-h">New candidates</h2>
        <p class="meta">Short list from the discovery agent${rounds.length === 1 ? `, search round ${rounds[0]}` : ""}. Pick the ones worth a research brief.</p>
      </div>
      ${fresh.length ? html`<label class="check"><input type="checkbox" data-act="selectAll" data-on="change" ${all ? "checked" : ""}>Select all</label>` : ""}
    </div>
    ${inlineError(ui.error && ui.error.where === "cands" ? ui.error.message : "")}
    ${fresh.length ? fresh.map((c) => candidateRow(c)) : !d.cands.length ? emptyState(
      "No candidates yet",
      "The discovery agent searches on its own once a week, and right after the workspace starts. You can also ask it now.",
      { href: href("sources"), label: "Find companies" }) : emptyState(
      "Every candidate has a decision",
      "The discovery agent looks for new companies once a week. Researched companies appear in Ready to decide.",
      { href: href("companies", { tab: "review" }), label: "Open Ready to decide" })}
  </section>`;
}

function candidateRow(c) {
  const sel = ui.selected.has(c.id);
  const skipping = ui.skipping === c.id;
  const url = safeUrl(c.sourceUrl);
  return html`
  <div class="cand${sel ? " selected" : ""}">
    <input type="checkbox" data-act="select" data-on="change" data-id="${c.id}" ${sel ? "checked" : ""} aria-label="Select ${c.company}">
    <div>
      <b>${c.company}</b>
      <div class="meta">${c.domain || "Website not known yet"}</div>
      <div class="meta">${c.person ? `${c.person}, ${c.title}` : `Contact to find: ${c.title || "not named"}`}</div>
      ${c.whyPerson ? html`<div class="meta-3">${c.whyPerson}</div>` : ""}
    </div>
    <div>
      <div>${c.why}</div>
      <div class="row-flex" style="margin-top:6px">
        <span class="tag">${c.signal}</span>
        <span class="meta-3">${c.signalDate}</span>
        ${c.problem ? html`<span class="tag info">${c.problem}</span>` : ""}
        ${(c.flags || []).map((f) => html`<span class="tag warn">${typeof f === "string" ? f : f.text || f.type}</span>`)}
      </div>
      ${c.quote ? html`<p class="quote">"${c.quote}"</p>` : ""}
      ${url ? html`<a class="meta" href="${url}" target="_blank" rel="noopener noreferrer">Source ${icon("link", 14)}</a>` : ""}
    </div>
    <div class="actions">
      <button type="button" class="btn small" data-act="researchOne" data-id="${c.id}">Research</button>
      <button type="button" class="btn ghost small" data-act="skipOpen" data-id="${c.id}" aria-expanded="${skipping ? "true" : "false"}">Skip</button>
    </div>
    ${skipping ? html`
    <form class="skip-form" data-act="skipForm" data-on="submit input change" data-id="${c.id}" data-name="${c.company}">
      <label class="field">Why skip? The direction agent learns from this.
        <select name="reason" required>
          <option value="">Choose a reason</option>
          ${SKIP_REASONS.map((r) => html`<option>${r}</option>`)}
        </select>
      </label>
      <label class="field">Who it should teach
        <select name="scope">${SCOPES.map(([v, l]) => html`<option value="${v}">${l}</option>`)}</select>
      </label>
      <div class="actions">
        <button type="submit" class="btn primary small" disabled>Skip company</button>
        <button type="button" class="btn ghost small" data-act="skipCancel">Cancel</button>
        <span class="missing" data-missing>Choose a reason first.</span>
      </div>
    </form>` : ""}
  </div>`;
}

// ----- Steps 2-3 -----
function researchView(d, ctx) {
  const list = d.researching || [];
  if (!list.length) {
    return html`<section class="card">${emptyState("Nothing is being researched",
      "Pick a candidate, or send a company back with questions, and its research shows here.",
      { href: href("companies", { tab: "new" }), label: "Open new candidates" })}</section>`;
  }
  return html`
  <section class="card" aria-labelledby="res-h">
    <div class="card-head"><div><h2 id="res-h">Being researched</h2>
      <p class="meta">Runs without you. Each company moves to Ready to decide when its brief is done.</p></div></div>
    <ul class="rows">${list.map((o) => researchRow(o, ctx))}</ul>
  </section>`;
}

function researchRow(o, ctx) {
  const r = o.research || { status: "queued", log: [] };
  const status = {
    running: ["run", "Researching"], paused: ["wait", r.pauseReason === "budget" ? "Paused: budget reached" : "Paused"],
    failed: ["fail", "Stopped by a problem"], done: ["done", "Brief done"], queued: ["", "Queued"],
  }[r.status] || ["", r.status];
  const needsSite = r.status === "failed" && (!o.company.domain || /website|domain/i.test(r.error || ""));
  const err = ui.error && ui.error.where === o.id ? ui.error.message : "";
  return html`
  <li style="align-items:flex-start">
    <span class="what">
      <b>${o.company.name}</b>
      <span class="meta">${o.title}. ${o.source.note}</span>
      <span class="meta with-dot ${status[0]}" style="margin-top:6px">${status[1]}</span>
      ${r.log && r.log.length ? html`<ul class="steps">${r.log.slice(-6).map((l) => html`<li>${icon("check", 14)}<span>${l.m || l.message || JSON.stringify(l)}</span></li>`)}
        ${r.status === "running" ? html`<li class="now"><span class="with-dot run">Working on the next step</span></li>` : ""}</ul>` : ""}
      ${r.error ? html`<div class="notice bad" style="margin-top:8px">${icon("alert")}<span>${r.error}</span></div>` : ""}
      ${err ? html`<div style="margin-top:8px">${inlineError(err)}</div>` : ""}
    </span>
    <span class="actions">
      ${needsSite ? html`
        <form class="row-flex" data-act="siteForm" data-on="submit" data-id="${o.id}">
          <label class="sr-only" for="site-${o.id}">Company website for ${o.company.name}</label>
          <input class="input" id="site-${o.id}" name="domain" placeholder="company.com" value="${o.company.domain || ""}" required style="width:200px">
          <button type="submit" class="btn small">Save and retry</button>
        </form>` : ""}
      ${r.status === "running" ? html`<button type="button" class="btn small" data-act="research" data-do="stop" data-id="${o.id}">Stop</button>` : ""}
      ${r.status === "paused" ? html`<button type="button" class="btn small" data-act="research" data-do="resume" data-id="${o.id}">Resume</button>` : ""}
      ${r.status === "failed" && !needsSite ? html`<button type="button" class="btn small" data-act="research" data-do="retry" data-id="${o.id}">Retry</button>` : ""}
    </span>
  </li>`;
}

// ----- Step 4 -----
// A live brief's recommendation comes from code checks, not the agent's judgement: it says so (basis "checks"). Briefs
// saved before 5 Nov 2026 carry the old fixed reason instead of the basis.
const OLD_CHECKS_REASON = "Research brief is ready for review with dated, quoted sources.";
const byChecks = (rec) => !!rec && (rec.basis === "checks" || rec.reason === OLD_CHECKS_REASON);

function suggestTag(rec) {
  if (!rec) return "";
  const decision = rec.decision;
  const [cls, text] = byChecks(rec) || rec.overruled
    ? (decision === "accept" ? ["good", "Checks passed"] : ["warn", "Needs a look"])
    : { accept: ["good", "Suggested: accept"], return: ["warn", "Suggested: look again"], reject: ["", "Suggested: not now"] }[decision] || ["", decision];
  return html`<span class="tag ${cls}" style="margin-top:6px">${text}</span>`;
}

function reviewView(d, ctx) {
  if (!d.review.length) {
    return html`<section class="card">${emptyState("No company is waiting for a decision",
      "A company appears here when its research brief is done.",
      { href: href("companies", { tab: "new" }), label: "Pick candidates" })}</section>`;
  }
  const o = d.current;
  return html`
  <div class="split">
    <nav class="pick-list" aria-label="Companies to decide on">
      ${d.review.map((x) => html`
        <a class="pick" href="${href("companies", { tab: "review", id: x.id })}" ${x.id === o.id ? html`aria-current="true"` : ""}>
          <span class="avatar" aria-hidden="true">${initials(x.company)}</span>
          <span><b>${x.company}</b><span class="meta">${x.title}</span>
            ${suggestTag(d.suggested && d.suggested[x.id])}</span>
        </a>`)}
    </nav>
    <section class="card detail" aria-labelledby="brief-h">${brief(o, d, ctx)}${decision(o, d, ctx)}</section>
  </div>`;
}

function brief(o, d, ctx) {
  const b = o.brief || {};
  const rec = b.recommendation;
  const p = b.priority || {};
  const site = o.company.domain ? safeUrl(`https://${o.company.domain}`) : "";
  const place = [o.company.industry, o.company.hq && `in ${o.company.hq}`].filter(Boolean).join(" ");
  return html`
  <div class="block">
    <h2 id="brief-h">${o.company.name}</h2>
    <p class="meta">${place ? `${place}. ` : ""}${site ? html`<a href="${site}" target="_blank" rel="noopener noreferrer">${o.company.domain}</a>` : "Website not known."}${site && o.company.domainBy === "search" ? html` <span class="meta-3">Found by a web search: check it is this company's website.</span>` : ""}</p>
    <p class="meta-3">${o.title}${o.team ? `, ${o.team} team` : ""}. ${o.source.note}</p>
  </div>
  ${rec ? html`
  <div class="block">
    <div class="suggestion">${icon("bulb", 20)}<div>
      ${byChecks(rec) ? html`
      <b>${rec.decision === "accept" ? "The brief passed the research checks" : "The brief needs another look"}</b>
      <p class="meta">${rec.reason === OLD_CHECKS_REASON ? "The company is named in its dated, quoted sources and a contact was found. Whether it is worth pursuing (fit, timing, value) is your call." : rec.reason}</p>
      <p class="meta-3">Checked by code, not judged by the research agent. Direction ${o.directionVersion || "unknown"}</p>` : html`
      <b>${rec.overruled ? "The research agent suggested accept, but the checks say: look again" : `The research agent suggests: ${RECOMMENDATION_LABELS[rec.decision] || rec.decision}`}</b>
      <p class="meta">${rec.reason}</p>
      ${rec.overruled ? html`<p class="meta">Checks: ${rec.checks}</p>` : ""}
      <p class="meta-3">${rec.basis === "agent" ? "The research agent's own judgement; code checks can only overrule an accept." : "Research agent"}, direction ${o.directionVersion || "unknown"}</p>`}
    </div></div>
  </div>` : ""}
  ${(b.flags || []).length ? html`<div class="block stack">${b.flags.map((f) => html`
    <div class="notice warn">${icon("alert")}<span><b>${f.type}.</b> ${f.text}</span></div>`)}</div>` : ""}
  <div class="block">
    <h3 class="sub-title">Why now: what the sources say</h3>
    ${(b.facts || []).length ? factList(b.facts, ctx) : html`<p class="meta">The brief has no sourced facts.</p>`}
  </div>
  <div class="block stack">
    <div><h3 class="sub-title">What it might mean (not confirmed)</h3>${prose(b.hypothesis || "No hypothesis written.")}</div>
    ${b.relevance ? html`<div><h3 class="sub-title">Why CGS</h3>${prose(b.relevance)}</div>` : ""}
    ${b.alternative ? html`<div><h3 class="sub-title">Another explanation</h3>${prose(b.alternative)}</div>` : ""}
    ${(b.unknowns || []).length ? html`<div><h3 class="sub-title">Still unknown</h3><ul class="bullets">${b.unknowns.map((u) => html`<li>${u}</li>`)}</ul></div>` : ""}
  </div>
  ${Object.keys(p).length ? html`
  <div class="block">
    <h3 class="sub-title">Priority</h3>
    <div class="grid-4">${priorityCells(o, d, ctx)}</div>
    ${p.confidence ? html`<p class="meta-3" style="margin-top:8px">Research agent's confidence in this brief: ${p.confidence}.</p>` : ""}
  </div>` : ""}
  <div class="block">
    <h3 class="sub-title">Who to contact</h3>
    ${contactList(o)}
  </div>
  <div class="block stack">
    <h3 class="sub-title">Checks</h3>
    <div class="stack" data-slot="checks">${crmCheck(d.crm, o)}${relatedCheck(d.related, ctx)}</div>
    ${b.usage ? html`<p class="meta-3">Research used ${plural(b.usage.searches || 0, "search", "searches")}, ${plural(b.usage.pages || 0, "page")} and ${plural(b.usage.calls || 0, "AI call")}.</p>` : ""}
  </div>`;
}

// The contact on record first, then the other people the sources name (up to 3 in all). Each has a LinkedIn search
// the person opens themselves.
function contactList(o) {
  const all = (o.brief && o.brief.contacts) || [];
  const main = o.contact.name ? all.find((p) => p.name === o.contact.name) || { name: o.contact.name, title: o.contact.title } : null;
  const people = [...(main ? [main] : []), ...all.filter((p) => !main || p.name !== main.name)].slice(0, 3);
  const company = o.company.name;
  const row = (p, i) => html`<li><span><b>${p.name}</b>, ${p.title}${i === 0 && main ? html` <span class="tag">On record</span>` : ""}
    ${p.why ? html`<span class="meta">${p.why}</span>` : ""}
    <span class="meta-3">${p.url ? html`Named in ${sourceLink(p.url)}. ` : ""}<a href="${linkedinSearch([p.name, company])}" target="_blank" rel="noopener noreferrer">Search on LinkedIn</a></span></span></li>`;
  return html`
    ${people.length ? html`<ul class="people">${people.map(row)}</ul>` : html`<p class="meta">No person is named in the sources yet.</p>`}
    ${main ? html`<p class="meta">${o.contact.email ? `Email: ${o.contact.email}` : "No email address found. Agents never guess one."}</p>` : ""}
    <p class="meta-3">Role to find: ${o.contact.title || "not set"}. <a href="${linkedinSearch([o.contact.title === "Contact to confirm" ? "" : o.contact.title, company])}" target="_blank" rel="noopener noreferrer">Search LinkedIn for this role</a> (opens in your browser; agents do not read LinkedIn).</p>`;
}

function metric(label, value, why, slot) {
  return html`<div class="metric" ${slot ? html`data-slot="${slot}"` : ""}><div class="label">${label}</div><div class="value">${value || "Unknown"}</div>${why ? html`<div class="meta-3">${why}</div>` : ""}</div>`;
}

// Facts that cite the same page sit together and name it once. News over a year old is marked: it may not be "now".
function factList(facts, ctx) {
  const groups = [];
  for (const f of facts) {
    const g = groups.find((x) => x.src === f.src);
    if (g) g.facts.push(f); else groups.push({ src: f.src, date: f.date, facts: [f] });
  }
  let n = 0;
  return html`<ol class="facts">${groups.map((g) => {
    const age = daysBetween(g.date, ctx.today);
    return g.facts.map((f, i) => html`
      <li><span class="n">${++n}</span><span>${f.text}${i === g.facts.length - 1 ? html`<span class="meta-3" style="display:block">${sourceLink(g.src, g.date === "undated" ? "undated" : day(g.date, ctx.today))}${
        age !== null && age > 365 ? html` <span class="tag warn">Over a year old</span>` : ""}</span>` : ""}</span></li>`);
  })}</ol>`;
}

// A long paragraph reads better as points: one per sentence.
function prose(text) {
  const parts = String(text).length > 240 ? String(text).split(/(?<=[.!?])\s+(?=[A-Z0-9"“(])/).map((x) => x.trim()).filter(Boolean) : [];
  return parts.length > 2 ? html`<ul class="bullets">${parts.map((x) => html`<li>${x}</li>`)}</ul>` : html`<p>${text}</p>`;
}

// Who a job title usually speaks for, when the brief does not say.
function authorityOf(title) {
  const t = ` ${String(title || "").toLowerCase()} `;
  if (/\b(chief|ceo|cfo|coo|cio|cto|cdo|president|owner|founder)\b/.test(t) && !/\bvice president\b/.test(t)) return ["Executive", "From the job title: usually approves budget."];
  if (/\b(vp|svp|evp|vice president|general manager)\b/.test(t)) return ["Senior leader", "From the job title: usually holds the budget for their area."];
  if (/\b(director|head of)\b/.test(t)) return ["Director", "From the job title: usually shapes the budget, may not approve it."];
  if (/\bmanager\b/.test(t)) return ["Manager", "From the job title: usually recommends, rarely approves."];
  return null;
}

// What the CRM check and the check for other CGS work found, as the relationship with the company.
function relationshipOf(crm, related) {
  if (!crm || !related || crm.pending || related.pending) return ["Checking…", "Insightly and other CGS work are being checked."];
  if (crm.error || related.error) return ["Not checked", "A check could not run: see Checks below."];
  const inCrm = !crm.off && ["leads", "contacts", "organizations", "opportunities"].some((k) => (crm[k] || []).length);
  const other = (related.other_opportunities || []).length;
  if (inCrm || other) return ["Known to CGS", [inCrm && "Records in Insightly", other && "other CGS work"].filter(Boolean).join(" and ") + ": see Checks below."];
  if (crm.off) return ["No other CGS work", "The CRM is off, so Insightly was not checked."];
  return ["None found", "Nothing in Insightly or in other CGS work."];
}

const vague = (v) => !v || /^(unknown|none known|n\/a)$/i.test(String(v).trim());

function priorityCells(o, d, ctx) {
  const p = (o.brief && o.brief.priority) || {};
  // Briefs from before 5 Nov 2026 say "Tier 1" with "not estimated": that is no tier.
  const estimated = p.tier && !/not estimated/i.test(p.tierWhy || "");
  const authority = !vague(p.authority) ? [p.authority, p.authorityWhy] : o.contact && o.contact.title ? authorityOf(o.contact.title) : null;
  const rel = !vague(p.relationship) ? [p.relationship, p.relationshipWhy] : relationshipOf(d.crm, d.related);
  return html`
    ${metric("Revenue tier", estimated ? `Tier ${p.tier}` : "Not estimated", estimated ? p.tierWhy : "Public research does not show what the work would be worth to CGS. It is estimated at Qualification.")}
    ${authority ? metric("Contact authority", authority[0], authority[1]) : ""}
    ${metric("Relationship", rel[0], rel[1], vague(p.relationship) ? "relationship" : "")}
    ${!vague(p.fit) ? metric("Fit with the direction", p.fit, p.fitWhy) : ""}`;
}

function crmCheck(crm, o) {
  if (!crm) return "";
  if (crm.pending) return html`<div class="notice" role="status">${icon("clock")}<span>Checking the CRM for this company and person…</span></div>`;
  if (crm.error) return html`<div class="notice bad">${icon("alert")}<span>The CRM could not be checked: ${crm.error}</span></div>`;
  if (crm.off) return html`<div class="notice">${icon("alert")}<span>The CRM is off in this workspace (${crm.mode}), so no duplicate check ran. Check Insightly yourself before accepting.</span></div>`;
  const found = ["leads", "contacts", "organizations", "opportunities"].some((k) => (crm[k] || []).length);
  const records = [["leads", "Lead"], ["contacts", "Contact"], ["organizations", "Organization"], ["opportunities", "Opportunity"]]
    .flatMap(([k, kind]) => (crm[k] || []).map((x) => ({ kind, name: x.name || x.company || x.id, url: safeUrl(x.url), status: x.status })));
  const byName = crm.searchedBy && !crm.searchedBy.includes("email") && o && o.contact && o.contact.name;
  return html`<div class="notice ${found ? "warn" : "good"}">${icon(found ? "alert" : "check")}<span>${crm.summary}${byName
    ? html` <span class="meta-3">Insightly was searched by company name only: ${o.contact.name} has no email address to search by.</span>` : ""}
    ${records.length ? html`<ul class="crm-found">${records.slice(0, 8).map((x) => html`<li>${x.kind}: ${x.url
      ? html`<a href="${x.url}" target="_blank" rel="noopener noreferrer">${x.name}</a>` : x.name}${x.status ? html` <span class="meta-3">(${x.status})</span>` : ""}</li>`)}</ul>` : ""}</span></div>`;
}

function relatedCheck(rel, ctx) {
  if (!rel) return "";
  if (rel.pending) return html`<div class="notice" role="status">${icon("clock")}<span>Checking other CGS work with this company or person…</span></div>`;
  if (rel.error) return html`<div class="notice bad">${icon("alert")}<span>Other CGS work could not be checked: ${rel.error}</span></div>`;
  const items = rel.other_opportunities || [];
  if (!items.length) return html`<div class="notice good">${icon("check")}<span>No other CGS opportunity with this company or person.</span></div>`;
  return html`<div class="notice warn">${icon("alert")}<span>Other CGS work with this company or person:
    <ul class="bullets">${items.map((x) => html`<li>${x.company}: ${x.title} (${STAGE_LABELS[x.stage] || x.stage}, ${x.owner || "no owner"}; ${x.why.join(", ")})${x.last_email ? `. Emailed ${x.last_email.days_ago} days ago.` : ""}</li>`)}</ul>
  </span></div>`;
}

function decision(o, d, ctx) {
  const err = ui.error && ui.error.where === "step4" ? ui.error.message : "";
  if (ui.mode === "accept") return html`<div class="block">${acceptForm(o, d, ctx)}${inlineError(err)}</div>`;
  if (ui.mode === "return") return html`<div class="block">${returnForm(o)}${inlineError(err)}</div>`;
  if (ui.mode === "reject") return html`<div class="block">${rejectForm(o)}${inlineError(err)}</div>`;
  return html`
  <div class="decision-bar">
    ${inlineError(err)}
    <div class="actions">
      <button type="button" class="btn primary" data-act="mode" data-mode="accept" data-id="${o.id}">Accept…</button>
      <button type="button" class="btn" data-act="mode" data-mode="return" data-id="${o.id}">Send back with questions…</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="reject" data-id="${o.id}">Reject…</button>
    </div>
  </div>`;
}

function formData(o) {
  return html`data-id="${o.id}" data-revision="${o.revision}" data-name="${o.company.name}"`;
}

// The automatic checks cover Insightly (by company name, and by email when there is one) and other CGS work in the
// workspace. When they ran and found nothing, the person confirms only what they cannot see: relationships known to
// colleagues, a parent company, a new name. Otherwise they check by hand.
function duplicateCheck(d) {
  const crm = d.crm || {}, rel = d.related || {};
  const ran = !crm.pending && !rel.pending && !crm.error && !rel.error && !crm.off;
  const clean = ran && !["leads", "contacts", "organizations", "opportunities"].some((k) => (crm[k] || []).length) && !(rel.other_opportunities || []).length;
  const text = clean
    ? "The automatic checks found nothing in Insightly or in other CGS work. I know of no other relationship with this company (for example through a colleague, a parent company or an earlier name)."
    : crm.pending || rel.pending ? "I checked for an existing relationship and for duplicates (in Insightly and with colleagues). The automatic checks are still running."
    : ran ? "I looked at what the checks found above and there is no duplicate (in Insightly or with colleagues)."
    : "I checked Insightly and with colleagues for an existing relationship or a duplicate: the automatic check did not run.";
  return html`<label class="check"><input type="checkbox" name="duplicates_checked">${text}</label>`;
}

function acceptForm(o, d, ctx) {
  const s = (o.brief && o.brief.suggested) || {};
  const owner = ctx.users.includes(s.owner) ? s.owner : ctx.me;
  const due = s.due && daysBetween(ctx.today, s.due) >= 0 ? s.due : addDays(ctx.today, 3);
  const rating = o.rating || 3;
  return html`
  <form class="panel-form" data-act="acceptForm" data-on="submit input change" ${formData(o)} aria-labelledby="acc-h">
    <h3 id="acc-h" class="sub-title">Accept ${o.company.name}</h3>
    <p class="meta">The company becomes a lead with an owner. The outreach agent then drafts the first email for the owner to approve; nothing is sent on its own.</p>
    <div class="form-grid">
      <label class="field">Owner
        <select name="owner">${ctx.users.map((u) => html`<option ${u === owner ? "selected" : ""}>${u}</option>`)}</select>
      </label>
      <label class="field">Main reason
        <select name="accept_reason">${ACCEPT_REASONS.map((r) => html`<option>${r}</option>`)}</select>
      </label>
    </div>
    <fieldset class="rating">
      <legend>Rating: how promising is it? (1 low, 5 high)</legend>
      ${[1, 2, 3, 4, 5].map((n) => html`<label><input type="radio" name="rating" value="${n}" ${n === rating ? "checked" : ""}><span>${n}</span></label>`)}
    </fieldset>
    <div class="form-grid">
      <label class="field">Next step
        <input name="next_action" value="${s.next || ""}" required>
      </label>
      <label class="field">Due
        <input type="date" name="due" value="${due}" min="${ctx.today}" required>
      </label>
    </div>
    <label class="field">Comment for the direction agent <span class="hint">Optional. What made this company worth it.</span>
      <textarea name="comment" rows="2"></textarea>
    </label>
    <div data-slot="duplicates">${duplicateCheck(d)}</div>
    <div class="actions">
      <button type="submit" class="btn primary" disabled>Accept company</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      <span class="missing" data-missing>${s.next ? "Confirm the duplicate check." : "Name the next step."}</span>
    </div>
  </form>`;
}

function returnForm(o) {
  const b = o.brief || {};
  const stop = (t) => (/[.?!]$/.test(t.trim()) ? t.trim() : `${t.trim()}.`);
  const ideas = [...(b.unknowns || []).map(stop), ...(b.flags || []).map((f) => `${f.type}: ${stop(f.text)}`)].slice(0, 6);
  return html`
  <form class="panel-form" data-act="returnForm" data-on="submit input" ${formData(o)} aria-labelledby="ret-h">
    <h3 id="ret-h" class="sub-title">Send back to the research agent</h3>
    <p class="meta">It researches again with your questions and brings the company back to Ready to decide.</p>
    ${ideas.length ? html`<fieldset class="tick-cards"><legend class="meta">From the brief: tick what the agent should find out</legend>
      ${ideas.map((c) => html`<label class="tick-card"><input type="checkbox" data-act="idea" data-on="change" data-text="${c}"><span>${c}</span></label>`)}</fieldset>` : ""}
    <label class="field">What should the agent find out?
      <textarea name="questions" rows="3" placeholder="For example: confirm who now leads the automation program." required></textarea>
    </label>
    <div class="actions">
      <button type="submit" class="btn primary" disabled>Send to the research agent</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      <span class="missing" data-missing>Write at least one question.</span>
    </div>
  </form>`;
}

function rejectForm(o) {
  return html`
  <form class="panel-form" data-act="rejectForm" data-on="submit input change" ${formData(o)} aria-labelledby="rej-h">
    <h3 id="rej-h" class="sub-title">Reject ${o.company.name}</h3>
    <p class="meta">The reason goes to the direction agent, which may propose a change to what the agents look for. A person approves any change.</p>
    <div class="form-grid">
      <label class="field">Reason
        <select name="reason" required><option value="">Choose a reason</option>${REJECT_REASONS.map((r) => html`<option>${r}</option>`)}</select>
      </label>
      <label class="field">Who it should teach
        <select name="scope">${SCOPES.map(([v, l]) => html`<option value="${v}">${l}</option>`)}</select>
      </label>
    </div>
    <label class="field">Comment <span class="hint">Needed when the reason is Other.</span>
      <textarea name="comment" rows="2"></textarea>
    </label>
    <label class="check"><input type="checkbox" name="suggest_exclusion">Suggest leaving this company out of future searches</label>
    <div class="actions">
      <button type="submit" class="btn primary" disabled>Reject company</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      <span class="missing" data-missing>Choose a reason first.</span>
    </div>
  </form>`;
}

// ----- after Step 4 -----
function acceptedView(d, ctx) {
  const list = d.opps.filter((o) => ACCEPTED.includes(o.stage));
  if (!list.length) return html`<section class="card">${emptyState("No accepted companies yet", "Companies you accept at Ready to decide appear here with their owner and next step.")}</section>`;
  return html`
  <section class="card" aria-labelledby="acc-list-h">
    <div class="card-head"><h2 id="acc-list-h">Accepted</h2><span class="meta">Leads with an owner, at any later stage</span></div>
    <div class="table-wrap"><table class="list">
      <thead><tr><th scope="col">Company</th><th scope="col">Contact</th><th scope="col">Owner</th><th scope="col">Stage</th><th scope="col">Next step</th><th scope="col"><span class="sr-only">Open</span></th></tr></thead>
      <tbody>${list.map((o) => html`<tr>
        <td><b>${o.company}</b><span class="meta">${o.title}</span></td>
        <td>${o.contact.name || "Not known yet"}<div class="meta-3">${o.contact.title}</div></td>
        <td>${o.owner || "Unassigned"}</td>
        <td>${STAGE_LABELS[o.stage] || o.stage}</td>
        <td>${o.next.action || "None set"}${o.next.due ? html`<div class="meta-3">Due ${day(o.next.due, ctx.today)}</div>` : ""}</td>
        <td><a class="btn small" href="${linkFor(o)}">Open</a></td>
      </tr>`)}</tbody>
    </table></div>
  </section>`;
}

function asideView(d, ctx) {
  const rejected = d.opps.filter((o) => o.stage === "rejected");
  const skipped = d.cands.filter((c) => c.status === "skipped");
  const reasonFor = (company) => (d.feedback || []).find((f) => f.step === 4 && f.outcome === "Rejected" && f.subject === company);
  const scopeLabel = Object.fromEntries(SCOPES);
  return html`
  <div class="stack">
    <section class="card" aria-labelledby="rej-list-h">
      <div class="card-head"><h2 id="rej-list-h">Rejected at Step 4</h2><span class="meta num">${rejected.length}</span></div>
      ${rejected.length ? html`<ul class="rows">${rejected.map((o) => {
        const f = reasonFor(o.company);
        return html`<li><span class="what"><b>${o.company}</b><span class="meta">${f ? `${f.reason}. ${f.by}, ${day(f.on, ctx.today)}` : "Reason not recorded."}</span></span></li>`;
      })}</ul>` : emptyState("Nothing rejected", "Companies rejected at Ready to decide appear here with the reason.")}
    </section>
    <section class="card" aria-labelledby="skip-list-h">
      <div class="card-head"><h2 id="skip-list-h">Skipped candidates</h2><span class="meta num">${skipped.length}</span></div>
      ${skipped.length ? html`<ul class="rows">${skipped.map((c) => html`
        <li><span class="what"><b>${c.company}</b><span class="meta">${c.skipReason}${c.skipScope ? `. Teaches: ${(scopeLabel[c.skipScope] || c.skipScope).toLowerCase()}` : ""}</span></span>
          <button type="button" class="btn small ghost" data-act="unskip" data-id="${c.id}" aria-label="Undo skipping ${c.company}">Undo</button></li>`)}</ul>`
      : emptyState("Nothing skipped", "Candidates you skip appear here with the reason the direction agent learns from.")}
    </section>
  </div>`;
}

// ---------- after drawing: keep research status fresh ----------
export function after(root, ctx, d) {
  clearTimeout(ui.pollTimer);
  if (d.tab === "review") {
    for (const o of d.review) {
      if (!known(checks.related, o.id)) ctx.api.get(`/opportunities/${enc(o.id)}/related`).then((r) => keep(checks.related, o.id, r)).catch(() => null);
    }
  }
  const running = d.tab === "research" && (d.researching || []).some((o) => o.research && ["running", "queued"].includes(o.research.status));
  if (!running) return;
  const route = location.hash;
  ui.pollTimer = setTimeout(async () => {
    if (location.hash !== route || root.contains(document.activeElement) && document.activeElement.matches("input, textarea, select")) {
      if (location.hash === route) after(root, ctx, d);
      return;
    }
    if (ctx.config && ctx.config.appMode === "live") await ctx.api.post("/research/sync").catch(() => null);
    const before = new Set(d.researching.map((o) => o.id));
    await ctx.refresh();
    const now = await ctx.api.get("/opportunities?stage=review").catch(() => []);
    for (const o of now.filter((x) => before.has(x.id))) {
      ctx.toast(`Brief ready: ${o.company}.`, { link: { href: href("companies", { tab: "review", id: o.id }), label: "Review it" } });
    }
  }, 5000);
}

// ---------- actions ----------
async function busy(button, fn) {
  if (button) { button.classList.add("busy"); button.disabled = true; button.setAttribute("aria-busy", "true"); }
  try {
    return await fn();
  } finally {
    if (button && button.isConnected) { button.classList.remove("busy"); button.disabled = false; button.removeAttribute("aria-busy"); }
  }
}

function fail(where, err, ctx) {
  ui.error = { where, message: err instanceof ApiError && err.code === "revision_conflict"
    ? "Someone else changed this company while you were looking. The page now shows the latest version; check it and decide again."
    : err.message };
  ctx.refresh();
}

async function pick(ids, ctx, button) {
  ui.error = null;
  const created = [];
  await busy(button, async () => {
    for (const id of ids) {
      try {
        const res = await ctx.api.post(`/candidates/${enc(id)}/pick`);
        created.push(...(res.opportunities || []));
        ui.selected.delete(id);
      } catch (err) {
        ui.error = { where: "cands", message: err.message };
        break;
      }
    }
  });
  await ctx.refresh();
  if (!created.length) return;
  const first = await ctx.api.get(`/opportunities/${enc(created[0])}`).catch(() => null);
  const name = first ? first.company.name : "the company";
  if (first && first.stage === "review") {
    ctx.toast(created.length === 1 ? `Brief ready: ${name}.` : `${plural(created.length, "brief")} ready, starting with ${name}.`,
      { link: { href: href("companies", { tab: "review", id: first.id }), label: "Review" } });
  } else {
    ctx.toast(created.length === 1 ? `${name} is being researched. It moves to Ready to decide when the brief is done.`
      : `${plural(created.length, "company", "companies")} are being researched.`, { link: { href: href("companies", { tab: "research" }), label: "Watch" } });
  }
}

function formMissing(form, text) {
  const submit = form.querySelector("[type=submit]");
  const line = form.querySelector("[data-missing]");
  submit.disabled = !!text;
  if (line) line.textContent = text || "";
}

export const actions = {
  tab(t, e, ctx) {
    resetForms();
    ctx.go("companies", { tab: t.dataset.tab });
  },
  tabkeys(t, e, ctx) {
    const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
    if (!keys.includes(e.key)) return;
    const tabs = [...t.querySelectorAll("[role=tab]")];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const n = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : e.key === "Home" ? 0 : tabs.length - 1;
    ctx.focusAfter = `#${tabs[n].id}`;
    resetForms();
    ctx.go("companies", { tab: tabs[n].dataset.tab });
  },

  // Step 1
  select(t, e, ctx) {
    if (t.checked) ui.selected.add(t.dataset.id); else ui.selected.delete(t.dataset.id);
    ctx.focusAfter = `[data-act=select][data-id="${t.dataset.id}"]`;
    ctx.refresh();
  },
  selectAll(t, e, ctx) {
    const boxes = [...document.querySelectorAll("[data-act=select]")];
    ui.selected = new Set(t.checked ? boxes.map((b) => b.dataset.id) : []);
    ctx.focusAfter = "[data-act=selectAll]";
    ctx.refresh();
  },
  clearSelection(t, e, ctx) { ui.selected.clear(); ctx.refresh(); },
  async unskip(t, e, ctx) {
    t.disabled = true;
    dropToast(`skip:${t.dataset.id}`);
    try {
      ctx.toast(await unskip(ctx, t.dataset.id));
    } catch (err) {
      ctx.toast(err.message);
    }
    ctx.refresh();
  },
  researchOne(t, e, ctx) { pick([t.dataset.id], ctx, t); },
  researchSelected(t, e, ctx) { pick([...ui.selected], ctx, t); },
  skipOpen(t, e, ctx) {
    ui.skipping = ui.skipping === t.dataset.id ? null : t.dataset.id;
    ctx.focusAfter = ".skip-form select";
    ctx.refresh();
  },
  skipCancel(t, e, ctx) { ui.skipping = null; ctx.refresh(); },
  async skipForm(form, e, ctx) {
    const reason = form.elements.reason.value;
    if (e.type !== "submit") return formMissing(form, reason ? "" : "Choose a reason first.");
    if (!reason) return;
    const button = form.querySelector("[type=submit]");
    try {
      const id = form.dataset.id;
      await busy(button, () => ctx.api.post(`/candidates/${enc(id)}/skip`, { reason, scope: form.elements.scope.value }));
      ui.skipping = null;
      ui.selected.delete(id);
      ui.error = null;
      offerUndo(ctx, `Skipped ${form.dataset.name || "the candidate"}. The reason goes to the direction agent.`, () => unskip(ctx, id), { key: `skip:${id}` });
      ctx.refresh();
    } catch (err) {
      fail("cands", err, ctx);
    }
  },

  // Steps 2-3
  async research(t, e, ctx) {
    try {
      await busy(t, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/research/${t.dataset.do}`));
      ui.error = null;
    } catch (err) {
      ui.error = { where: t.dataset.id, message: err.message };
    }
    ctx.refresh();
  },
  async siteForm(form, e, ctx) {
    const id = form.dataset.id;
    const button = form.querySelector("[type=submit]");
    try {
      await busy(button, async () => {
        await ctx.api.patch(`/opportunities/${enc(id)}/company`, { domain: form.elements.domain.value });
        await ctx.api.post(`/opportunities/${enc(id)}/research/retry`);
      });
      ui.error = null;
    } catch (err) {
      ui.error = { where: id, message: err.message };
    }
    ctx.refresh();
  },

  // Step 4
  mode(t, e, ctx) {
    ui.mode = t.dataset.mode || null;
    ui.modeFor = t.dataset.id || null;
    ui.error = null;
    ctx.focusAfter = ui.mode ? ".panel-form select, .panel-form textarea" : "[data-act=mode][data-mode=accept]";
    ctx.refresh();
  },
  // A ticked idea joins the questions; unticked, it leaves them.
  idea(t) {
    const area = t.closest("form").elements.questions;
    const lines = area.value.split("\n").filter((l) => l.trim() && l.trim() !== t.dataset.text);
    if (t.checked) lines.push(t.dataset.text);
    area.value = lines.join("\n");
    area.dispatchEvent(new Event("input", { bubbles: true }));
  },
  acceptForm(form, e, ctx) {
    const f = form.elements;
    const missing = !f.next_action.value.trim() ? "Name the next step." : !f.due.value ? "Choose a due date." : !f.duplicates_checked.checked ? "Confirm the duplicate check." : "";
    if (e.type !== "submit") return formMissing(form, missing);
    if (missing) return;
    submitStep4(form, ctx, {
      decision: "accept", owner: f.owner.value, rating: Number(f.rating.value), accept_reason: f.accept_reason.value,
      next_action: f.next_action.value.trim(), due: f.due.value, comment: f.comment.value.trim(), duplicates_checked: true,
    }, (name) => `Accepted ${name}. The outreach agent drafts the first email for ${f.owner.value} to approve.`);
  },
  returnForm(form, e, ctx) {
    const q = form.elements.questions.value.trim();
    if (e.type !== "submit") return formMissing(form, q ? "" : "Write at least one question.");
    if (!q) return;
    submitStep4(form, ctx, { decision: "return", questions: q }, (name) => `Sent ${name} back to the research agent with your questions.`);
  },
  rejectForm(form, e, ctx) {
    const f = form.elements;
    const missing = !f.reason.value ? "Choose a reason first." : f.reason.value === "Other" && !f.comment.value.trim() ? "Write a comment: the reason is Other." : "";
    if (e.type !== "submit") return formMissing(form, missing);
    if (missing) return;
    submitStep4(form, ctx, {
      decision: "reject", reason: f.reason.value, scope: f.scope.value, comment: f.comment.value.trim(), suggest_exclusion: f.suggest_exclusion.checked,
    }, (name) => `Rejected ${name}. The reason goes to the direction agent.`);
  },
};

async function submitStep4(form, ctx, body, message) {
  // The revision is the one the person read; a change by someone else since then is refused (409), never overwritten.
  const { id, revision, name } = form.dataset;
  const button = form.querySelector("[type=submit]");
  let res;
  try {
    const undo_seconds = body.decision === "return" ? 0 : UNDO_SECONDS;  // returned research starts at once
    res = await busy(button, () => ctx.api.post(`/opportunities/${enc(id)}/step4`, { ...body, revision: Number(revision), undo_seconds }));
  } catch (err) {
    return fail("step4", err, ctx);
  }
  resetForms();
  let text = message(name);
  let link = body.decision === "accept" ? { href: href("companies", { tab: "accepted" }), label: "See accepted" } : null;
  if (body.decision === "return") {
    // Research may already be done (demo mode finishes at once); say what actually happened.
    const after = await ctx.api.get(`/opportunities/${enc(id)}`).catch(() => null);
    if (after && after.stage === "review") {
      text = `The research agent answered your questions on ${name}. Its brief is updated.`;
      link = { href: href("companies", { tab: "review", id }), label: "Review it" };
    } else {
      link = { href: href("companies", { tab: "research" }), label: "Watch the research" };
    }
  }
  if (res && res.undo) {
    offerUndo(ctx, `${text} You can undo this for ${res.undo.seconds} seconds.`, () =>
      ctx.api.post(`/opportunities/${enc(id)}/step4/undo`).then(() => `Undone. ${name} is back in Ready to decide; nothing reached the CRM or the agents.`),
    { seconds: res.undo.seconds, link });
  } else {
    ctx.toast(text, { link });
  }
  ctx.scrollTop = true;
  ctx.go("companies", { tab: "review" });
}

const unskip = (ctx, id) => ctx.api.post(`/candidates/${enc(id)}/unskip`).then(() => "Put back in New candidates. The skip no longer teaches the direction agent.");
