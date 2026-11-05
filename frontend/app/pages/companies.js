// Companies: Step 1 candidates, Steps 2-3 research, Step 4 decision, then accepted and set-aside lists.
import { html, icon, day, addDays, daysBetween, href, plural, initials, safeUrl } from "../lib.js";
import { enc, ApiError } from "../api.js";
import { SKIP_REASONS, REJECT_REASONS, ACCEPT_REASONS, SCOPES, STAGE_LABELS, RECOMMENDATION_LABELS } from "../options.js";
import { emptyState, inlineError } from "../shell.js";
import { linkFor } from "./links.js";

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
export async function load(ctx, params) {
  const [cands, opps] = await Promise.all([ctx.api.get("/candidates"), ctx.api.get("/opportunities")]);
  const review = opps.filter((o) => o.stage === "review");
  const tab = TABS.some(([t]) => t === params.tab) ? params.tab : review.length ? "review" : "new";
  const data = { tab, cands, opps, review };
  if (tab === "research") {
    const ids = opps.filter((o) => o.stage === "researching").map((o) => o.id);
    data.researching = await Promise.all(ids.map((id) => ctx.api.get(`/opportunities/${enc(id)}`)));
  }
  if (tab === "review" && review.length) {
    // the research agent's suggestion for each company in the list (DESIGN: "Suggested: accept" on each item)
    const briefs = await Promise.all(review.map((o) => ctx.api.get(`/opportunities/${enc(o.id)}`).catch(() => null)));
    data.suggested = Object.fromEntries(briefs.filter(Boolean).map((b) => [b.id, b.brief && b.brief.recommendation ? b.brief.recommendation.decision : null]));
    const id = review.some((o) => o.id === params.id) ? params.id : review[0].id;
    const [detail, crm, related] = await Promise.all([
      ctx.api.get(`/opportunities/${enc(id)}`),
      ctx.api.get(`/opportunities/${enc(id)}/crm`).catch((e) => ({ error: e.message })),
      ctx.api.get(`/opportunities/${enc(id)}/related`).catch((e) => ({ error: e.message })),
    ]);
    data.current = detail;
    data.crm = crm;
    data.related = related;
    if (ui.modeFor !== id) resetForms();
  }
  if (tab === "aside") data.feedback = await ctx.api.get("/feedback");
  return data;
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
    ${fresh.length ? fresh.map((c) => candidateRow(c)) : emptyState(
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
    <form class="skip-form" data-act="skipForm" data-on="submit input change" data-id="${c.id}">
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
      <span class="row-flex meta" style="margin-top:6px"><span class="dot ${status[0]}" aria-hidden="true"></span>${status[1]}</span>
      ${r.log && r.log.length ? html`<ul class="steps">${r.log.slice(-6).map((l) => html`<li>${icon("check", 14)}<span>${l.m || l.message || JSON.stringify(l)}</span></li>`)}
        ${r.status === "running" ? html`<li class="now"><span class="dot run" aria-hidden="true"></span><span>Working on the next step</span></li>` : ""}</ul>` : ""}
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
function suggestTag(decision) {
  if (!decision) return "";
  const [cls, text] = { accept: ["good", "Suggested: accept"], return: ["warn", "Suggested: look again"], reject: ["", "Suggested: not now"] }[decision] || ["", decision];
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
    <section class="card detail" aria-labelledby="brief-h">${brief(o, d, ctx)}${decision(o, ctx)}</section>
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
    <p class="meta">${place ? `${place}. ` : ""}${site ? html`<a href="${site}" target="_blank" rel="noopener noreferrer">${o.company.domain}</a>` : "Website not known."}</p>
    <p class="meta-3">${o.title}${o.team ? `, ${o.team} team` : ""}. ${o.source.note}</p>
  </div>
  ${rec ? html`
  <div class="block">
    <div class="suggestion">${icon("bulb", 20)}<div>
      <b>The research agent suggests: ${RECOMMENDATION_LABELS[rec.decision] || rec.decision}</b>
      <p class="meta">${rec.reason}</p>
      <p class="meta-3">Research agent, direction ${o.directionVersion || "unknown"}</p>
    </div></div>
  </div>` : ""}
  ${(b.flags || []).length ? html`<div class="block stack">${b.flags.map((f) => html`
    <div class="notice warn">${icon("alert")}<span><b>${f.type}.</b> ${f.text}</span></div>`)}</div>` : ""}
  <div class="block">
    <h3 class="sub-title">Why now: what the sources say</h3>
    ${(b.facts || []).length ? html`<ol class="facts">${b.facts.map((f, i) => html`
      <li><span class="n">${i + 1}</span><span>${f.text}<span class="meta-3" style="display:block">${f.src}${f.date ? `, ${f.date}` : ""}</span></span></li>`)}</ol>`
    : html`<p class="meta">The brief has no sourced facts.</p>`}
  </div>
  <div class="block stack">
    <div><h3 class="sub-title">What it might mean (not confirmed)</h3><p>${b.hypothesis || "No hypothesis written."}</p></div>
    ${b.relevance ? html`<div><h3 class="sub-title">Why CGS</h3><p>${b.relevance}</p></div>` : ""}
    ${b.alternative ? html`<div><h3 class="sub-title">Another explanation</h3><p>${b.alternative}</p></div>` : ""}
    ${(b.unknowns || []).length ? html`<div><h3 class="sub-title">Still unknown</h3><ul class="bullets">${b.unknowns.map((u) => html`<li>${u}</li>`)}</ul></div>` : ""}
  </div>
  ${Object.keys(p).length ? html`
  <div class="block">
    <h3 class="sub-title">Priority</h3>
    <div class="grid-4">
      ${metric("Revenue tier", p.tier ? `Tier ${p.tier}` : "Unknown", p.tierWhy)}
      ${metric("Contact authority", p.authority, p.authorityWhy)}
      ${metric("Relationship", p.relationship, p.relationshipWhy)}
      ${metric("Fit with the direction", p.fit, p.fitWhy)}
    </div>
    ${p.confidence ? html`<p class="meta-3" style="margin-top:8px">Research agent's confidence in this brief: ${p.confidence}.</p>` : ""}
  </div>` : ""}
  <div class="block">
    <h3 class="sub-title">Who to contact</h3>
    ${o.contact.name ? html`<p><b style="font-weight:500">${o.contact.name}</b>, ${o.contact.title}</p>
      <p class="meta">${o.contact.email ? `Email: ${o.contact.email}` : "No email address found. Agents never guess one."}</p>`
    : html`<p class="meta">No person is named in the sources yet. Role to find: ${o.contact.title}.</p>`}
  </div>
  <div class="block stack">
    <h3 class="sub-title">Checks</h3>
    ${crmCheck(d.crm)}
    ${relatedCheck(d.related, ctx)}
    ${b.usage ? html`<p class="meta-3">Research used ${plural(b.usage.searches || 0, "search", "searches")}, ${plural(b.usage.pages || 0, "page")} and ${plural(b.usage.calls || 0, "AI call")}.</p>` : ""}
  </div>`;
}

function metric(label, value, why) {
  return html`<div class="metric"><div class="label">${label}</div><div class="value">${value || "Unknown"}</div>${why ? html`<div class="meta-3">${why}</div>` : ""}</div>`;
}

function crmCheck(crm) {
  if (!crm) return "";
  if (crm.error) return html`<div class="notice bad">${icon("alert")}<span>The CRM could not be checked: ${crm.error}</span></div>`;
  if (crm.off) return html`<div class="notice">${icon("alert")}<span>The CRM is off in this workspace (${crm.mode}), so no duplicate check ran. Check Insightly yourself before accepting.</span></div>`;
  const found = ["leads", "contacts", "organizations", "opportunities"].some((k) => (crm[k] || []).length);
  return html`<div class="notice ${found ? "warn" : "good"}">${icon(found ? "alert" : "check")}<span>${crm.summary}</span></div>`;
}

function relatedCheck(rel, ctx) {
  if (!rel) return "";
  if (rel.error) return html`<div class="notice bad">${icon("alert")}<span>Other CGS work could not be checked: ${rel.error}</span></div>`;
  const items = rel.other_opportunities || [];
  if (!items.length) return html`<div class="notice good">${icon("check")}<span>No other CGS opportunity with this company or person.</span></div>`;
  return html`<div class="notice warn">${icon("alert")}<span>Other CGS work with this company or person:
    <ul class="bullets">${items.map((x) => html`<li>${x.company}: ${x.title} (${STAGE_LABELS[x.stage] || x.stage}, ${x.owner || "no owner"}; ${x.why.join(", ")})${x.last_email ? `. Emailed ${x.last_email.days_ago} days ago.` : ""}</li>`)}</ul>
  </span></div>`;
}

function decision(o, ctx) {
  const err = ui.error && ui.error.where === "step4" ? ui.error.message : "";
  if (ui.mode === "accept") return html`<div class="block">${acceptForm(o, ctx)}${inlineError(err)}</div>`;
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

function acceptForm(o, ctx) {
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
    <label class="check"><input type="checkbox" name="duplicates_checked">I checked for an existing relationship and for duplicates (in the CRM and with colleagues).</label>
    <div class="actions">
      <button type="submit" class="btn primary" disabled>Accept company</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      <span class="missing" data-missing>${s.next ? "Confirm the duplicate check." : "Name the next step."}</span>
    </div>
  </form>`;
}

function returnForm(o) {
  const b = o.brief || {};
  const chips = [...(b.unknowns || []).map((u) => `Find out: ${u}.`), ...(b.flags || []).map((f) => `Resolve: ${f.text}`)].slice(0, 4);
  return html`
  <form class="panel-form" data-act="returnForm" data-on="submit input" ${formData(o)} aria-labelledby="ret-h">
    <h3 id="ret-h" class="sub-title">Send back to the research agent</h3>
    <p class="meta">It researches again with your questions and brings the company back to Ready to decide.</p>
    ${chips.length ? html`<div class="chips" aria-label="Start from the brief">${chips.map((c) => html`<button type="button" class="chip" data-act="chip" data-text="${c}">${c}</button>`)}</div>` : ""}
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
        <li><span class="what"><b>${c.company}</b><span class="meta">${c.skipReason}${c.skipScope ? `. Teaches: ${(scopeLabel[c.skipScope] || c.skipScope).toLowerCase()}` : ""}</span></span></li>`)}</ul>`
      : emptyState("Nothing skipped", "Candidates you skip appear here with the reason the direction agent learns from.")}
    </section>
  </div>`;
}

// ---------- after drawing: keep research status fresh ----------
export function after(root, ctx, d) {
  clearTimeout(ui.pollTimer);
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
      await busy(button, () => ctx.api.post(`/candidates/${enc(form.dataset.id)}/skip`, { reason, scope: form.elements.scope.value }));
      ui.skipping = null;
      ui.selected.delete(form.dataset.id);
      ui.error = null;
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
  chip(t) {
    const area = t.closest("form").elements.questions;
    area.value = area.value ? `${area.value}\n${t.dataset.text}` : t.dataset.text;
    area.dispatchEvent(new Event("input", { bubbles: true }));
    area.focus();
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
  try {
    await busy(button, () => ctx.api.post(`/opportunities/${enc(id)}/step4`, { ...body, revision: Number(revision) }));
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
  ctx.toast(text, { link });
  ctx.scrollTop = true;
  ctx.go("companies", { tab: "review" });
}
