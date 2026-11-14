// Companies (design/redesign/Companies.dc.html, view "companies"): Step 1 candidates (Found), Steps 2-3 research
// (Researching), Step 4 decision (Your decision, a full-page brief review), then the accepted leads (Identified) and
// what was set aside. The stage bar is the funnel and the tab control at once; each stage names where the company
// stands in Insightly (config/crm.json). The list stages open a side panel; the selection lives in the URL (id).
import { html, icon, day, addDays, daysBetween, href, plural, initials, safeUrl, sourceLink, linkedinSearch } from "../lib.js";
import { enc, ApiError } from "../api.js";
import { SKIP_REASONS, REJECT_REASONS, ACCEPT_REASONS, SCOPES, STAGE_LABELS } from "../options.js";
import { emptyState, inlineError, dropToast, crmLinks } from "../shell.js";
import { linkFor } from "./links.js";
import { UNDO_SECONDS, offerUndo } from "../undo.js";

export const title = "Companies";

// [id, label, who has the next move, the Insightly line]. "who" colours the bar: you (orange), agent (purple), done.
const STAGES = [
  ["found", "Found", "you", "Not in Insightly"],
  ["research", "Researching", "agent", "Not in Insightly"],
  ["decision", "Your decision", "you", "Lead created on Accept"],
  ["identified", "Identified", "done", "Insightly: Identified, then Contacted"],
  ["aside", "Set aside", "none", "Disqualified, if it reached Insightly"],
];
// Tab names before the redesign, so old links (notifications, the to-do list) still open the right stage.
const TAB_ALIAS = { new: "found", review: "decision", accepted: "identified" };
const IDENTIFIED = ["identified", "contacted"];
const ASIDE = ["rejected", "closed"];

// What the person has open on this page. It survives a redraw and is cleared when they move on.
const ui = {
  checked: new Set(),    // Found: candidate ids ticked
  bulkSkip: false,       // Found: the "Set aside" form for the ticked ones is open
  skipping: null,        // Found side panel: the candidate whose set-aside form is open
  mode: null,            // Your decision: "accept" | "return" | "reject"
  modeFor: null,         // the opportunity that form belongs to
  ticks: new Map(),      // Your decision: opportunity id -> Set of ticked questions
  folds: new Map(),      // Your decision: opportunity id -> { fold id: open }
  error: null,           // { where, message }
  pollTimer: null,
};

function resetForms() {
  ui.mode = null; ui.modeFor = null; ui.error = null; ui.skipping = null; ui.bulkSkip = false;
}

// ---------- data ----------
// Checks already made for a company in this session, so picking it again shows them at once. The CRM check asks
// Insightly, so it is kept for a few minutes and not fetched ahead; the check for other CGS work reads the workspace.
const checks = { crm: new Map(), related: new Map() };
const CHECK_TTL = 5 * 60 * 1000;
const known = (map, id) => { const x = map.get(id); return x && Date.now() - x.at < CHECK_TTL ? x.value : null; };
const keep = (map, id, value) => { if (value && !value.error) map.set(id, { at: Date.now(), value }); };

function lists(cands, opps) {
  return {
    found: cands.filter((c) => c.status === "new"),
    research: opps.filter((o) => o.stage === "researching"),
    decision: opps.filter((o) => o.stage === "review"),
    identified: opps.filter((o) => IDENTIFIED.includes(o.stage)),
    aside: [...cands.filter((c) => c.status === "skipped").map((c) => ({ ...c, kind: "skipped" })),
      ...opps.filter((o) => ASIDE.includes(o.stage)).map((o) => ({ ...o, kind: o.stage }))],
  };
}

export async function load(ctx, params) {
  const [cands, opps] = await Promise.all([ctx.api.get("/candidates"), ctx.api.get("/opportunities")]);
  const l = lists(cands, opps);
  const asked = TAB_ALIAS[params.tab] || params.tab;
  const tab = STAGES.some(([t]) => t === asked) ? asked : l.decision.length ? "decision" : "found";
  const id = params.id || params.open || undefined;
  if (params.tab !== tab || params.open) ctx.replace("companies", { tab, id });  // the tab chosen now stays when the page redraws
  const data = { tab, id, cands, opps, lists: l };
  if (tab === "research") {
    data.details = Object.fromEntries((await Promise.all(l.research.map((o) => ctx.api.get(`/opportunities/${enc(o.id)}`)))).map((x) => [x.id, x]));
  }
  if (tab === "aside") {
    // Why each lead was set aside: the step 4 rejection or the step 8 close, from its decisions and the feedback.
    const [feedback, ...details] = await Promise.all([ctx.api.get("/feedback"),
      ...l.aside.filter((x) => x.kind !== "skipped").map((o) => ctx.api.get(`/opportunities/${enc(o.id)}`).catch(() => null))]);
    data.feedback = feedback;
    data.details = Object.fromEntries(details.filter(Boolean).map((x) => [x.id, x]));
  }
  if (tab === "decision" && l.decision.length) {
    const briefs = await Promise.all(l.decision.map((o) => ctx.api.get(`/opportunities/${enc(o.id)}`).catch(() => null)));
    data.briefs = Object.fromEntries(briefs.filter(Boolean).map((b) => [b.id, b]));
    const cur = l.decision.some((o) => o.id === id) ? id : l.decision[0].id;
    const [detail, crm, related] = await Promise.all([
      ctx.api.get(`/opportunities/${enc(cur)}`),
      ctx.api.get(`/opportunities/${enc(cur)}/crm`).catch((e) => ({ error: e.message })),
      ctx.api.get(`/opportunities/${enc(cur)}/related`).catch((e) => ({ error: e.message })),
    ]);
    Object.assign(data, { current: detail, crm, related });
    keep(checks.crm, cur, crm);
    keep(checks.related, cur, related);
    if (ui.modeFor !== cur) resetForms();
  }
  return data;
}

// Another item on the same stage is drawn at once from what the page holds: a brief (Your decision) or a row (the
// side panel). Checks not made yet say so, and the full load fills them in (slots).
export function instant(ctx, params, last) {
  const tab = TAB_ALIAS[params.tab] || params.tab;
  if (!last || last.tab !== tab) return null;
  const id = params.id || undefined;
  if (tab !== "decision") {
    if (ui.skipping !== id) ui.skipping = null;
    return { ...last, id };
  }
  if (!id || !last.briefs || !last.briefs[id] || !last.lists.decision.some((o) => o.id === id)) return null;
  if (ui.modeFor !== id) resetForms();
  return { ...last, id, current: last.briefs[id], crm: known(checks.crm, id) || { pending: true }, related: known(checks.related, id) || { pending: true } };
}

export function slots(d, ctx) {
  if (d.tab !== "decision" || !d.current) return {};
  const [value, why] = relationshipOf(d.crm, d.related);
  return {
    checks: html`${crmCheck(d.crm, d.current)}${relatedCheck(d.related, ctx)}`,
    relationship: html`<span class="label">Relationship</span><span class="value">${value}</span><span class="meta-3">${why}</span>`,
    duplicates: duplicateCheck(d),
  };
}

export function changed(early, d) {
  const ids = (x) => STAGES.map(([t]) => x.lists[t].map((o) => o.id).join(",")).join("|");
  return early.tab !== d.tab || ids(early) !== ids(d) || JSON.stringify(early.current) !== JSON.stringify(d.current);
}

// ---------- view ----------
export function view(d, ctx) {
  const crmOff = ctx.config && (ctx.config.crm === "off" || ctx.config.crmMode === "disabled");
  return html`
  <div class="page-head">
    <div>
      <h1>Companies</h1>
      <p>Pick candidates, watch the research, and decide on each brief.</p>
    </div>
    <div class="head-side">
      <span class="legend"><span><span class="dot wait" aria-hidden="true"></span>Waiting on you</span>
        <span><span class="dot agent" aria-hidden="true"></span>An agent works on it</span><span><span class="dot done" aria-hidden="true"></span>Done</span></span>
      <a class="btn" href="${href("find")}">${icon("search", 16)}Find companies</a>
    </div>
  </div>
  <div class="stages" role="tablist" aria-label="Stages" data-act="tabkeys" data-on="keydown">
    ${STAGES.map(([id, label, who, crm]) => html`
      <button type="button" role="tab" class="stg" id="tab-${id}" aria-selected="${d.tab === id ? "true" : "false"}" aria-controls="panel"
        tabindex="${d.tab === id ? "0" : "-1"}" data-act="tab" data-tab="${id}">
        <span class="who ${who}" aria-hidden="true"></span>
        <span class="n">${d.lists[id].length}</span>
        <span class="${who === "you" ? "label you-text" : "label"}">${label}</span>
        <span class="crm">${crmOff && id !== "decision" ? "CRM is off" : crmOff ? "No CRM: nothing is created" : crm}</span>
      </button>`)}
  </div>
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${d.tab === "found" ? foundView(d, ctx) : ""}
    ${d.tab === "research" ? researchView(d, ctx) : ""}
    ${d.tab === "decision" ? reviewView(d, ctx) : ""}
    ${d.tab === "identified" ? identifiedView(d, ctx) : ""}
    ${d.tab === "aside" ? asideView(d, ctx) : ""}
  </div>
  ${d.tab !== "decision" ? sidePanel(d, ctx) : ""}`;
}

// A list stage as a table: the company name is the link that opens the side panel.
function table(d, cols, rows, { check } = {}) {
  return html`<div class="table-wrap"><table class="list rows-table">
    <thead><tr>${check ? html`<th scope="col" class="ck-col">${check}</th>` : ""}${cols.map((c) => html`<th scope="col">${c}</th>`)}</tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function companyCell(d, id, name, sub) {
  return html`<td><span class="co"><span class="avatar" aria-hidden="true">${initials(name)}</span><span class="co-text">
    <a class="nm" href="${href("companies", { tab: d.tab, id })}" data-act="openRow" data-row="${id}">${name}</a>
    <span class="meta-3">${sub}</span></span></span></td>`;
}

function tableHead(titleText, count, note, extra = "") {
  return html`<div class="card-head">
    <div><h2>${titleText} <span class="cnt">${count}</span></h2><p class="meta">${note}</p></div>${extra}</div>`;
}

// ----- Found (Step 1) -----
function foundView(d, ctx) {
  const list = d.lists.found;
  for (const id of [...ui.checked]) if (!list.some((c) => c.id === id)) ui.checked.delete(id);
  const latest = Math.max(0, ...list.map((c) => c.round || 0));
  const all = list.length > 0 && list.every((c) => ui.checked.has(c.id));
  const n = ui.checked.size;
  const bulk = n ? html`<div class="bulk" role="group" aria-label="Selected candidates">
      <span class="md">${n} selected</span>
      <button type="button" class="btn ghost" data-act="clearChecks">Clear</button>
      <button type="button" class="btn" data-act="bulkSkipOpen" aria-expanded="${ui.bulkSkip ? "true" : "false"}">Set aside</button>
      <button type="button" class="btn primary" data-act="researchSelected">Request briefs</button>
    </div>` : "";
  return html`
  <section class="card" aria-labelledby="found-h">
    <div class="card-head">
      <div><h2 id="found-h">Found <span class="cnt">${list.length}</span></h2>
        <p class="meta">The Discovery agent found these. Tick the ones worth a research brief.</p></div>
      ${bulk}
    </div>
    ${ui.bulkSkip && n ? skipForm({ act: "bulkSkipForm", name: plural(n, "company", "companies"), label: `Set aside ${plural(n, "company", "companies")}` }) : ""}
    ${inlineError(ui.error && ui.error.where === "cands" ? ui.error.message : "")}
    ${list.length ? table(d, ["Company", "Why it matched", "Contact", "From search"], list.map((c) => html`
      <tr class="${c.id === d.id ? "on" : ""}${ui.checked.has(c.id) ? " picked" : ""}">
        <td class="ck-col"><input type="checkbox" data-act="check" data-on="change" data-id="${c.id}" ${ui.checked.has(c.id) ? "checked" : ""} aria-label="Select ${c.company}"></td>
        ${companyCell(d, c.id, c.company, c.domain || "Website not known yet")}
        <td><span class="cell-main">${c.signal || c.why}</span><span class="meta-3">${c.signalDate || ""}${c.problem ? ` · ${c.problem}` : ""}</span></td>
        <td>${c.person ? html`<span class="cell-main">${c.person}</span><span class="meta-3">${c.title}</span>` : html`<span class="meta">${c.title ? `To find: ${c.title}` : "Not named"}</span>`}</td>
        <td>${c.round === latest ? html`<span class="tag info">New, round ${c.round}</span>` : html`<span class="meta">Round ${c.round}</span>`}</td>
      </tr>`), { check: html`<input type="checkbox" data-act="checkAll" data-on="change" ${all ? "checked" : ""} aria-label="Select all candidates">` })
    : emptyState(d.cands.length ? "Every candidate has a decision" : "No candidates yet",
      d.cands.length ? "The Discovery agent looks for new companies on its schedule. Ask it now on Find." : "The Discovery agent searches on its own once a week, and right after the workspace starts. You can also ask it now.",
      { href: href("find"), label: "Find companies" })}
  </section>`;
}

function skipForm({ act, id = "", name, label }) {
  return html`
  <form class="skip-form" data-act="${act}" data-on="submit input change" data-id="${id}" data-name="${name}">
    <label class="field">Why set it aside? The Direction agent learns from this.
      <select name="reason" required>
        <option value="">Choose a reason</option>
        ${SKIP_REASONS.map((r) => html`<option>${r}</option>`)}
      </select>
    </label>
    <label class="field">Who it should teach
      <select name="scope">${SCOPES.map(([v, l]) => html`<option value="${v}">${l}</option>`)}</select>
    </label>
    <div class="actions">
      <button type="submit" class="btn primary" disabled>${label}</button>
      <button type="button" class="btn ghost" data-act="skipCancel">Cancel</button>
      <span class="missing" data-missing>Choose a reason first.</span>
    </div>
  </form>`;
}

// ----- Researching (Steps 2-3) -----
// The research run reports what it did as log lines; it does not report a fixed step number (REDESIGN_PLAN G3).
const RESEARCH_STATE = {
  running: ["info", "Researching"], queued: ["", "Queued"], paused: ["warn", "Paused"],
  failed: ["bad", "Stopped: needs you"], done: ["good", "Brief done"],
};
function researchState(o) {
  const r = o.research || { status: "queued", log: [] };
  const [cls, text] = RESEARCH_STATE[r.status] || ["", r.status];
  const last = (r.log || []).slice(-1)[0];
  const now = r.status === "failed" ? r.error || "The research stopped." : r.status === "paused"
    ? (r.pauseReason === "budget" ? "Paused: today's AI requests are used up." : "Paused.") : last ? last.m || last.message || "" : "Waiting to start.";
  return { r, cls, text, now };
}

function researchView(d, ctx) {
  const list = d.lists.research;
  if (!list.length) {
    return html`<section class="card">${emptyState("Nothing is being researched",
      "Request a brief for a candidate, or send a brief back with questions, and its research shows here.",
      { href: href("companies", { tab: "found" }), label: "Open Found" })}</section>`;
  }
  return html`
  <section class="card" aria-labelledby="res-h">
    ${tableHead("Researching", list.length, "The Research agent is writing briefs. Each one moves to Your decision when it is ready.")}
    ${table(d, ["Company", "Now", "State"], list.map((o) => {
      const s = researchState((d.details || {})[o.id] || o);
      return html`<tr class="${o.id === d.id ? "on" : ""}">
        ${companyCell(d, o.id, o.company, o.title)}
        <td><span class="cell-main ${s.r.status === "failed" ? "bad-text" : ""}">${s.now}</span></td>
        <td><span class="tag ${s.cls} with-dot">${s.text}</span></td></tr>`;
    }))}
  </section>`;
}

// ----- Identified -----
function identifiedView(d, ctx) {
  const list = d.lists.identified;
  if (!list.length) return html`<section class="card">${emptyState("No accepted companies yet", "Companies you accept at Your decision appear here until the client replies.")}</section>`;
  return html`
  <section class="card" aria-labelledby="idn-h">
    ${tableHead("Identified", list.length, "Accepted leads before the client replies. Identified until the first email goes out, then Contacted.")}
    ${table(d, ["Company", "Contact", "Owner", "Now", "Next step"], list.map((o) => html`<tr class="${o.id === d.id ? "on" : ""}">
      ${companyCell(d, o.id, o.company, o.title)}
      <td><span class="cell-main">${o.contact.name || "Not known yet"}</span><span class="meta-3">${o.contact.title}</span></td>
      <td>${o.owner || "Not assigned"}</td>
      <td><span class="tag info">${STAGE_LABELS[o.stage]}</span></td>
      <td><span class="cell-main">${o.next.action || "None set"}</span>${o.next.due ? html`<span class="meta-3">Due ${day(o.next.due, ctx.today)}</span>` : ""}</td>
    </tr>`))}
  </section>`;
}

// ----- Set aside -----
function asideReason(x, d, ctx) {
  if (x.kind === "skipped") {
    const scope = Object.fromEntries(SCOPES)[x.skipScope];
    return { reason: x.skipReason || "Reason not recorded", by: "", when: "", note: scope ? `Teaches: ${scope.toLowerCase()}` : "" };
  }
  const det = (d.details || {})[x.id];
  const step = x.kind === "closed" ? 8 : 4;
  const dec = det ? [...det.decisions].reverse().find((y) => y.step === step && /Rejected|Closed/.test(y.decision)) : null;
  const fb = x.kind === "rejected" ? (d.feedback || []).find((f) => f.step === 4 && f.outcome === "Rejected" && f.subject === x.company) : null;
  return { reason: (fb && fb.reason) || (dec && dec.note) || "Reason not recorded", by: (dec && dec.by) || (fb && fb.by) || "",
    when: (dec && dec.on) || (fb && fb.on) || "", note: "" };
}
const ASIDE_KIND = { skipped: "Skipped candidate", rejected: "Rejected at Your decision", closed: "Closed lead" };

function asideView(d, ctx) {
  const list = d.lists.aside;
  if (!list.length) return html`<section class="card">${emptyState("Nothing set aside", "Candidates you skip and companies you reject or close appear here with the reason.")}</section>`;
  return html`
  <section class="card" aria-labelledby="aside-h">
    ${tableHead("Set aside", list.length, "Companies set aside by you or an agent, with the reason. A skipped candidate can be brought back.")}
    ${table(d, ["Company", "Reason", "Set aside by", "When"], list.map((x) => {
      const r = asideReason(x, d, ctx);
      return html`<tr class="${x.id === d.id ? "on" : ""}">
        ${companyCell(d, x.id, x.company, ASIDE_KIND[x.kind])}
        <td><span class="cell-main">${r.reason}</span>${r.note ? html`<span class="meta-3">${r.note}</span>` : ""}</td>
        <td>${r.by || html`<span class="meta-3">Not recorded</span>`}</td>
        <td>${r.when ? day(r.when, ctx.today) : html`<span class="meta-3">Not recorded</span>`}</td></tr>`;
    }))}
  </section>`;
}

// ----- the side panel for the list stages -----
function sidePanel(d, ctx) {
  const list = d.lists[d.tab];
  const i = list.findIndex((x) => x.id === d.id);
  if (i < 0) return "";
  const x = list[i];
  const near = (k) => list[i + k] ? href("companies", { tab: d.tab, id: list[i + k].id }) : "";
  const p = { found: foundPanel, research: researchPanel, identified: identifiedPanel, aside: asidePanel }[d.tab](x, d, ctx);
  const prev = near(-1), next = near(1);
  return html`
  <button type="button" class="drawer-scrim" tabindex="-1" aria-label="Close panel" data-act="closePanel"></button>
  <aside class="drawer" aria-labelledby="panel-h" data-act="panelKeys" data-on="keydown">
    <header class="ph">
      <div class="row-flex" style="flex-wrap:nowrap">
        <button type="button" class="btn icon-only" data-act="closePanel" aria-label="Close panel" title="Close (Esc)">${icon("close", 16)}</button>
        <span class="avatar" aria-hidden="true">${initials(x.company)}</span>
        <div style="flex:1;min-width:0"><h2 id="panel-h" tabindex="-1">${x.company}</h2><p class="meta">${p.sub}</p></div>
        ${prev ? html`<a class="btn ghost icon-only" href="${prev}" data-act="openRow" data-row="${list[i - 1].id}" aria-label="Previous company" title="Previous (K)">${icon("chevron", 16)}</a>` : ""}
        ${next ? html`<a class="btn ghost icon-only next" href="${next}" data-act="openRow" data-row="${list[i + 1].id}" aria-label="Next company" title="Next (J)">${icon("chevron", 16)}</a>` : ""}
      </div>
      <div class="row-flex"><span class="tag ${p.tone}">${p.stage}</span><span class="meta">${p.owner}</span><span class="meta-3" style="margin-left:auto">${i + 1} of ${list.length}</span></div>
    </header>
    <div class="pb">${p.blocks}</div>
    ${p.foot ? html`<footer class="pf">${p.foot}</footer>` : ""}
  </aside>`;
}

function block(titleText, body, { aside = "", cls = "" } = {}) {
  return html`<section class="card pblock ${cls}"><div class="pblock-head"><h3>${titleText}</h3>${aside}</div>${body}</section>`;
}

function foundPanel(c, d) {
  const url = safeUrl(c.sourceUrl);
  const skipping = ui.skipping === c.id;
  return {
    sub: c.domain || "Website not known yet", stage: "Found", tone: "warn", owner: "Not assigned yet",
    blocks: html`
      ${block("Why it matched", html`<p>${c.why}</p>
        <div class="row-flex" style="gap:6px"><span class="tag">${c.signal}</span>${c.signalDate ? html`<span class="meta-3">${c.signalDate}</span>` : ""}
          ${c.problem ? html`<span class="tag info">${c.problem}</span>` : ""}${(c.flags || []).map((f) => html`<span class="tag warn">${typeof f === "string" ? f : f.text || f.type}</span>`)}</div>
        ${c.quote ? html`<p class="quote">“${c.quote}”</p>` : ""}
        ${url ? html`<a class="meta" href="${url}" target="_blank" rel="noopener noreferrer">${sourceLink(c.sourceUrl)} ${icon("link", 14)}</a>` : ""}`,
        { aside: html`<span class="meta-3">Search round ${c.round}</span>` })}
      ${block("Who to contact", c.person ? html`<p><b>${c.person}</b>, ${c.title}</p>${c.whyPerson ? html`<p class="meta">${c.whyPerson}</p>` : ""}`
        : html`<p class="meta">No person named yet. Role to find: ${c.title || "not set"}.</p>`)}
      ${block("What a brief adds", html`<p class="meta">This is one news item, not research. A brief adds dated, quoted facts, a named contact if a source gives one, and the Insightly duplicate check.</p>`)}
      ${skipping ? skipForm({ act: "skipForm", id: c.id, name: c.company, label: "Set aside" }) : ""}
      ${inlineError(ui.error && ui.error.where === "cands" ? ui.error.message : "")}`,
    foot: skipping ? "" : html`<div class="actions">
      <button type="button" class="btn primary" data-act="researchOne" data-id="${c.id}">Request a brief</button>
      <button type="button" class="btn" data-act="skipOpen" data-id="${c.id}">Set aside…</button>
      <span class="meta-3" style="margin-left:auto">The Research agent writes it; you get a notice.</span></div>`,
  };
}

function researchPanel(o, d) {
  const det = (d.details || {})[o.id] || o;
  const s = researchState(det);
  const r = s.r;
  const needsSite = r.status === "failed" && (!det.company || !det.company.domain || /website|domain/i.test(r.error || ""));
  const err = ui.error && ui.error.where === o.id ? ui.error.message : "";
  const log = (r.log || []).map((l) => l.m || l.message || JSON.stringify(l));
  return {
    sub: o.title, stage: s.text, tone: s.cls, owner: o.owner ? `Owner: ${o.owner}` : "Not assigned yet",
    blocks: html`
      ${r.status === "failed" ? block("What went wrong", html`<p>${r.error || "The research stopped."}</p>
        <p class="meta">It stopped rather than write a brief from too little. Try again if the site was only down for a while; if the website is wrong, give the right one.</p>`,
        { cls: "err", aside: html`<span class="tag bad">Needs you</span>` }) : ""}
      ${r.status === "paused" ? block("Paused", html`<p>${r.pauseReason === "budget" ? "Today's AI requests are used up. Resume it tomorrow, or raise the limit on the Agent hub. Nothing done so far is lost." : "Someone paused this research. Nothing done so far is lost."}</p>`,
        { cls: "warn", aside: html`<span class="tag warn">Paused</span>` }) : ""}
      ${block("Progress", log.length ? html`<ol class="rl">${log.map((l, i) => html`<li class="${i < log.length - 1 || r.status === "done" ? "done" : ""}">
          <span class="d ${i < log.length - 1 || r.status === "done" ? "done" : r.status === "running" ? "live" : r.status === "failed" ? "err" : ""}" aria-hidden="true"></span><span>${l}</span></li>`)}
          ${r.status === "running" ? html`<li><span class="d live" aria-hidden="true"></span><span class="meta">Working on the next step</span></li>` : ""}</ol>`
        : r.status === "running" ? html`<ol class="rl"><li><span class="d live" aria-hidden="true"></span><span class="meta">Working on it now. Steps show here as the agent reports them.</span></li></ol>`
        : html`<p class="meta">${r.status === "queued" ? "Waiting for the Research agent to start." : "No steps recorded."}</p>`,
        { aside: html`<span class="tag info">Research agent</span>` })}
      ${block("What happens next", html`<p class="meta">When the brief is ready, the company moves to Your decision and appears in your Inbox.</p>`)}
      ${needsSite ? html`<form class="skip-form" data-act="siteForm" data-on="submit" data-id="${o.id}">
        <label class="field">Company website for ${o.company}<input class="input" name="domain" placeholder="company.com" value="${(det.company && det.company.domain) || ""}" required></label>
        <div class="actions"><button type="submit" class="btn primary">Save and try again</button></div></form>` : ""}
      ${inlineError(err)}`,
    foot: html`<div class="actions">
      ${r.status === "failed" && !needsSite ? html`<button type="button" class="btn primary" data-act="research" data-do="retry" data-id="${o.id}">Try again</button>` : ""}
      ${r.status === "paused" ? html`<button type="button" class="btn primary" data-act="research" data-do="resume" data-id="${o.id}">Resume</button>` : ""}
      ${["running", "queued"].includes(r.status) ? html`<button type="button" class="btn" data-act="research" data-do="stop" data-id="${o.id}">Stop research</button>` : ""}
      <span class="meta-3" style="margin-left:auto">${r.status === "running" ? "Working on it now" : s.text}</span></div>`,
  };
}

function identifiedPanel(o, d, ctx) {
  return {
    sub: o.title, stage: STAGE_LABELS[o.stage], tone: "info", owner: o.owner ? `Owner: ${o.owner}` : "Not assigned yet",
    blocks: html`
      ${block("Next step", html`<dl class="dl">
        <dt>Step</dt><dd>${o.next.action || "None set"}</dd>
        <dt>When</dt><dd>${o.next.due ? day(o.next.due, ctx.today) : "No date"}</dd>
        <dt>Contact</dt><dd>${o.contact.name ? `${o.contact.name}, ${o.contact.title}` : "Not known yet"}</dd></dl>`,
        { aside: html`<span class="tag info">${STAGE_LABELS[o.stage]}</span>` })}
      ${block("In Insightly", o.crm ? html`<div class="row-flex" style="gap:6px">${crmLinks(o.crm)}</div><p class="meta-3">${o.stage === "identified" ? "Identified until the first email goes out, then Contacted." : "Contacted: the first email has gone out."}</p>`
        : html`<p class="meta">${o.crmSync === "pending" ? "Waiting to sync." : "Not synced yet."}</p>`)}`,
    foot: html`<div class="actions"><a class="btn primary" href="${linkFor(o)}">Open in Outreach</a>
      <span class="meta-3" style="margin-left:auto">Emails and plans are handled on Outreach.</span></div>`,
  };
}

function asidePanel(x, d, ctx) {
  const r = asideReason(x, d, ctx);
  return {
    sub: x.kind === "skipped" ? x.domain || "" : x.title, stage: "Set aside", tone: "", owner: ASIDE_KIND[x.kind],
    blocks: html`${block("Why it was set aside", html`<dl class="dl">
        <dt>Reason</dt><dd>${r.reason}</dd>
        ${r.note ? html`<dt>Teaches</dt><dd>${r.note.replace(/^Teaches: /, "")}</dd>` : ""}
        <dt>By</dt><dd>${r.by || "Not recorded"}</dd>
        <dt>When</dt><dd>${r.when ? day(r.when, ctx.today) : "Not recorded"}</dd></dl>`)}
      ${x.kind !== "skipped" ? block("Bringing it back", html`<p class="meta">A rejected or closed lead cannot be brought back from here. Add the company again from Find if things change.</p>`) : ""}`,
    foot: x.kind === "skipped" ? html`<div class="actions">
      <button type="button" class="btn primary" data-act="unskip" data-id="${x.id}">Bring back to Found</button>
      <span class="meta-3" style="margin-left:auto">The skip then no longer teaches the Direction agent.</span></div>` : "",
  };
}

// ----- Your decision (Step 4): the full-page brief review -----
// A live brief's recommendation comes from code checks, not the agent's judgement: it says so (basis "checks"). Briefs
// saved before 5 Nov 2026 carry the old fixed reason instead of the basis.
const OLD_CHECKS_REASON = "Research brief is ready for review with dated, quoted sources.";
const byChecks = (rec) => !!rec && (rec.basis === "checks" || rec.reason === OLD_CHECKS_REASON);

// Which of the three buttons the suggestion points to.
function suggested(rec) {
  if (!rec) return null;
  if (byChecks(rec) || rec.overruled) return rec.decision === "accept" && !rec.overruled ? "accept" : "return";
  return rec.decision;
}
const SUGGEST_SHORT = { accept: "Agent: accept", return: "Agent: send back", reject: "Agent: set aside" };

export function suggestion(rec, o) {
  if (!rec) return { label: "No suggestion", title: "The brief has no suggestion", body: "Read the evidence below and decide." };
  if (byChecks(rec)) return {
    label: "The research checks say",
    title: rec.decision === "accept" ? "The brief passed the research checks" : "The brief needs another look",
    body: rec.reason === OLD_CHECKS_REASON ? "The company is named in its dated, quoted sources and a contact was found. Whether it is worth pursuing is your call." : rec.reason,
    note: "Checked by code, not judged by the agent.",
  };
  return {
    label: "Research agent suggests",
    title: rec.overruled ? "The agent suggested accept, but the checks say: look again"
      : { accept: "Accept and plan contact", return: "Send this back for another look", reject: "Set this aside for now" }[rec.decision] || rec.decision,
    body: rec.reason + (rec.overruled && rec.checks ? ` Checks: ${rec.checks}` : ""),
    note: rec.basis === "agent" ? "The agent's own judgement; code checks can only overrule an accept." : "",
  };
}

const FOLDS = [["unknowns", "What we don't know"], ["case", "The case"], ["sources", "Sources and quotes"], ["people", "Who to contact, and checks"], ["priority", "Priority conditions"]];

function questionsOf(o) {
  const b = o.brief || {};
  const stop = (t) => (/[.?!]$/.test(String(t).trim()) ? String(t).trim() : `${String(t).trim()}.`);
  return [...(b.unknowns || []).map(stop), ...(b.flags || []).map((f) => `${f.type}: ${stop(f.text)}`)];
}

function foldsFor(o) {
  if (!ui.folds.has(o.id)) ui.folds.set(o.id, { unknowns: suggested(o.brief && o.brief.recommendation) === "return" });
  return ui.folds.get(o.id);
}
function ticksFor(id) {
  if (!ui.ticks.has(id)) ui.ticks.set(id, new Set());
  return ui.ticks.get(id);
}

function reviewView(d, ctx) {
  if (!d.lists.decision.length) {
    return html`<section class="card">${emptyState("No company is waiting for a decision",
      "A company appears here when its research brief is done.",
      { href: href("companies", { tab: "found" }), label: "Pick candidates" })}</section>`;
  }
  const o = d.current;
  return html`
  <div class="review">
    <nav class="card queue" aria-label="Briefs to decide">
      <div class="queue-head">Your decision</div>
      ${d.lists.decision.map((x) => {
        const rec = d.briefs && d.briefs[x.id] && d.briefs[x.id].brief ? d.briefs[x.id].brief.recommendation : null;
        return html`<a class="pick" href="${href("companies", { tab: "decision", id: x.id })}" ${x.id === o.id ? html`aria-current="true"` : ""}>
          <span class="avatar" aria-hidden="true">${initials(x.company)}</span>
          <span><b>${x.company}</b><span class="meta-3">${SUGGEST_SHORT[suggested(rec)] || x.title}</span></span></a>`;
      })}
      <div class="queue-foot"><span class="keys">J</span><span class="keys">K</span>next brief</div>
    </nav>
    <div class="review-main">${decisionCard(o, d, ctx)}${evidence(o, d, ctx)}</div>
  </div>`;
}

function decisionCard(o, d, ctx) {
  const b = o.brief || {};
  const rec = b.recommendation;
  const s = suggestion(rec, o);
  const sug = suggested(rec);
  const p = b.priority || {};
  const site = o.company.domain ? safeUrl(`https://${o.company.domain}`) : "";
  const place = [o.company.industry, o.company.hq].filter(Boolean).join(", ");
  const flags = (b.flags || []).length, unknowns = (b.unknowns || []).length;
  const issues = [flags && plural(flags, "flag"), unknowns && plural(unknowns, "unknown")].filter(Boolean).join(", ");
  const cls = (k) => `btn ${sug === k ? "primary" : ""}`;
  const nT = ticksFor(o.id).size;
  const err = ui.error && ui.error.where === "step4" ? ui.error.message : "";
  return html`
  <section class="card decision" aria-labelledby="brief-h">
    <div class="row-flex" style="flex-wrap:nowrap;align-items:flex-start">
      <span class="avatar lg ink" aria-hidden="true">${initials(o.company.name)}</span>
      <div style="flex:1;min-width:0">
        <h2 id="brief-h">${o.company.name}</h2>
        <p class="meta">${place ? `${place}. ` : ""}${o.title}${o.team ? `, ${o.team} team` : ""}. ${o.source.note}</p>
      </div>
      ${site ? html`<a class="btn ghost small" href="${site}" target="_blank" rel="noopener noreferrer">${o.company.domain}</a>` : html`<span class="meta-3">Website not known</span>`}
    </div>
    ${site && o.company.domainBy === "search" ? html`<p class="meta-3">The website was found by a web search: check it is this company's.</p>` : ""}
    <div class="band">
      <div class="band-text">
        <span class="band-label">${s.label}</span>
        <span class="band-title">${s.title}</span>
        <p class="meta">${s.body}</p>
        <div class="row-flex" style="gap:6px">
          ${p.confidence ? html`<span class="tag">Confidence: ${p.confidence}</span>` : ""}
          ${priorityChips(o, d)}
          ${issues ? html`<span class="tag warn">${issues}</span>` : ""}
        </div>
        ${s.note ? html`<span class="meta-3">${s.note} Direction ${o.directionVersion || "unknown"}.</span>` : ""}
      </div>
      ${!ui.mode ? html`<div class="band-buttons">
        <button type="button" class="${cls("accept")}" data-act="mode" data-mode="accept" data-id="${o.id}">Accept and plan contact<span class="keys">A</span></button>
        <button type="button" class="${cls("return")}" data-act="mode" data-mode="return" data-id="${o.id}"><span data-backlabel>${nT ? `Send back with ${plural(nT, "question")}` : "Send back with questions"}</span><span class="keys">S</span></button>
        <button type="button" class="${cls("reject")}" data-act="mode" data-mode="reject" data-id="${o.id}">Reject<span class="keys">R</span></button>
      </div>` : ""}
      ${ui.mode === "accept" ? html`<div class="band-form">${acceptForm(o, d, ctx)}${inlineError(err)}</div>` : ""}
      ${ui.mode === "return" ? html`<div class="band-form">${returnForm(o)}${inlineError(err)}</div>` : ""}
      ${ui.mode === "reject" ? html`<div class="band-form">${rejectForm(o)}${inlineError(err)}</div>` : ""}
      ${!ui.mode && err ? html`<div class="band-form">${inlineError(err)}</div>` : ""}
    </div>
  </section>`;
}

function evidence(o, d, ctx) {
  const b = o.brief || {};
  const open = foldsFor(o);
  const all = FOLDS.every(([k]) => open[k]);
  const ticks = ticksFor(o.id);
  const qs = questionsOf(o);
  const facts = b.facts || [];
  const sources = new Set(facts.map((f) => f.src)).size;
  const people = Math.min(3, (b.contacts || []).length || (o.contact.name ? 1 : 0));
  const summary = {
    unknowns: html`<span class="${ticks.size ? "you-text md" : "meta-3"}" data-ticksum>${plural(qs.length, "open question")}${ticks.size ? `, ${ticks.size} ticked` : ""}</span>`,
    case: html`<span class="meta-3">Problem, why CGS, why it may be nothing</span>`,
    sources: html`<span class="meta-3">${facts.length ? `${plural(facts.length, "fact")} from ${plural(sources, "source")}` : "No sourced facts"}</span>`,
    people: html`<span class="meta-3">${plural(people, "contact")}, Insightly and other CGS work checked</span>`,
    priority: html`<span class="meta-3">From the brief and the checks</span>`,
  };
  const body = {
    unknowns: html`${(b.flags || []).map((f) => html`<div class="notice warn">${icon("alert")}<span><b>${f.type}.</b> ${f.text}</span></div>`)}
      ${qs.length ? html`<p class="meta-3">Tick the ones you want answered. They go with Send back.</p>
        <div class="stack" style="gap:8px">${qs.map((q, i) => html`<button type="button" class="tick" aria-pressed="${ticks.has(q) ? "true" : "false"}" data-act="tick" data-id="${o.id}" data-q="${q}">
          <span class="bx" aria-hidden="true">${icon("check", 11)}</span><span>${q}</span></button>`)}</div>`
        : html`<p class="meta">The brief lists nothing it could not find out.</p>`}`,
    case: html`<div class="stack" style="gap:14px">
      <div><span class="lbl">The problem they may have</span>${prose(b.hypothesis || "No hypothesis written.")}</div>
      ${b.relevance ? html`<div><span class="lbl">Why CGS</span>${prose(b.relevance)}</div>` : ""}
      ${b.alternative ? html`<div class="maybe-nothing"><span class="lbl">Why this may be nothing</span>${prose(b.alternative)}</div>` : ""}</div>`,
    sources: facts.length ? factList(facts, ctx) : html`<p class="meta">The brief has no sourced facts.</p>`,
    people: html`<div class="two-up">
      <div><span class="lbl">Who to contact</span>${contactList(o)}</div>
      <div><span class="lbl">Checks</span><div class="stack" style="gap:8px" data-slot="checks">${crmCheck(d.crm, o)}${relatedCheck(d.related, ctx)}</div>
        ${b.usage ? html`<p class="meta-3" style="margin-top:8px">Research used ${plural(b.usage.searches || 0, "search", "searches")}, ${plural(b.usage.pages || 0, "page")} and ${plural(b.usage.calls || 0, "AI call")}.</p>` : ""}</div></div>`,
    priority: html`<div class="grid-4">${priorityCells(o, d)}</div>
      <p class="meta-3" style="margin-top:8px">There is no single score: each condition says what the brief and the checks found. What the work would be worth is estimated at Qualification and kept in Insightly.</p>`,
  };
  return html`
  <div class="evidence-head"><span class="lbl">The evidence behind it</span>
    <button type="button" class="btn ghost small" data-act="foldAll" data-id="${o.id}" data-open="${all ? "" : "1"}">${all ? "Fold all" : "Open all"}</button></div>
  ${FOLDS.map(([k, label]) => html`<section class="card fold-card">
    <h3><button type="button" class="fold-btn" aria-expanded="${open[k] ? "true" : "false"}" aria-controls="fold-${k}" data-act="fold" data-id="${o.id}" data-fold="${k}">
      ${icon("chevron", 14)}<span class="fold-title">${label}</span><span class="fold-sum">${summary[k]}</span></button></h3>
    <div class="fold-content" id="fold-${k}" ${open[k] ? "" : "hidden"}>${body[k]}</div>
  </section>`)}`;
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
    ${people.length ? html`<ul class="people">${people.map(row)}</ul>` : html`<p class="meta">No person is named in the sources yet. The agent never guesses a contact.</p>`}
    ${main ? html`<p class="meta">${o.contact.email ? `Email: ${o.contact.email}` : "No email address found. Agents never guess one."}</p>` : ""}
    <p class="meta-3">Role to find: ${o.contact.title || "not set"}. <a href="${linkedinSearch([o.contact.title === "Contact to confirm" ? "" : o.contact.title, company])}" target="_blank" rel="noopener noreferrer">Search LinkedIn for this role</a> (opens in your browser; agents do not read LinkedIn).</p>`;
}

function metric(label, value, why, slot) {
  return html`<div class="metric" ${slot ? html`data-slot="${slot}"` : ""}><span class="label">${label}</span><span class="value">${value || "Unknown"}</span>${why ? html`<span class="meta-3">${why}</span>` : ""}</div>`;
}

// Facts that cite the same page sit together and name it once. News over a year old is marked: it may not be "now".
function factList(facts, ctx) {
  const groups = [];
  for (const f of facts) {
    const g = groups.find((x) => x.src === f.src);
    if (g) g.facts.push(f); else groups.push({ src: f.src, date: f.date, facts: [f] });
  }
  return html`<div class="evs">${groups.map((g) => {
    const age = daysBetween(g.date, ctx.today);
    return html`<div class="ev">${g.facts.map((f) => html`<span class="md">${f.text}</span>${f.quote ? html`<p class="quote">“${f.quote}”</p>` : ""}`)}
      <span class="meta-3">${sourceLink(g.src, g.date === "undated" ? "undated" : day(g.date, ctx.today))}${age !== null && age > 365 ? html` <span class="tag warn">Over a year old</span>` : ""}</span></div>`;
  })}</div>`;
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
  if (crm.error || related.error) return ["Not checked", "A check could not run: see Checks."];
  const inCrm = !crm.off && ["leads", "contacts", "organizations", "opportunities"].some((k) => (crm[k] || []).length);
  const other = (related.other_opportunities || []).length;
  if (inCrm || other) return ["Known to CGS", [inCrm && "Records in Insightly", other && "other CGS work"].filter(Boolean).join(" and ") + ": see Checks."];
  if (crm.off) return ["No other CGS work", "The CRM is off, so Insightly was not checked."];
  return ["None found", "Nothing in Insightly or in other CGS work."];
}

const vague = (v) => !v || /^(unknown|none known|n\/a)$/i.test(String(v).trim());

// The brief's priority cells, in words. The revenue tier is not shown: no amounts or value bands on a lead screen.
function priorityParts(o, d) {
  const p = (o.brief && o.brief.priority) || {};
  const authority = !vague(p.authority) ? [p.authority, p.authorityWhy] : o.contact && o.contact.title ? authorityOf(o.contact.title) : null;
  const rel = !vague(p.relationship) ? [p.relationship, p.relationshipWhy] : relationshipOf(d.crm, d.related);
  return { authority, rel, fit: !vague(p.fit) ? [p.fit, p.fitWhy] : null, relFromChecks: vague(p.relationship) };
}

function priorityChips(o, d) {
  const x = priorityParts(o, d);
  return html`${x.fit ? html`<span class="tag">Fit: ${x.fit[0]}</span>` : ""}${x.authority ? html`<span class="tag">Authority: ${x.authority[0]}</span>` : ""}`;
}

function priorityCells(o, d) {
  const x = priorityParts(o, d);
  return html`
    ${x.fit ? metric("Fit with the direction", x.fit[0], x.fit[1]) : ""}
    ${x.authority ? metric("Contact authority", x.authority[0], x.authority[1]) : ""}
    ${metric("Relationship", x.rel[0], x.rel[1], x.relFromChecks ? "relationship" : "")}`;
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
    ? "I know of no CGS relationship with this company that the checks could miss (for example through a colleague, a parent company or an earlier name)."
    : crm.pending || rel.pending ? "I checked for an existing relationship and for duplicates (in Insightly and with colleagues). The automatic checks are still running."
    : ran ? "I looked at what the checks found and there is no duplicate (in Insightly or with colleagues)."
    : "I checked Insightly and with colleagues for an existing relationship or a duplicate: the automatic check did not run.";
  return html`<label class="tick-card"><input type="checkbox" name="duplicates_checked"><span>${text}</span></label>`;
}

function acceptForm(o, d, ctx) {
  const s = (o.brief && o.brief.suggested) || {};
  const owner = ctx.users.includes(s.owner) ? s.owner : ctx.me;
  const due = s.due && daysBetween(ctx.today, s.due) >= 0 ? s.due : addDays(ctx.today, 3);
  const rating = o.rating || 3;
  return html`
  <form class="panel-form" data-act="acceptForm" data-on="submit input change" ${formData(o)} aria-label="Accept ${o.company.name}">
    <div class="form-grid">
      <label class="field">Owner
        <select name="owner">${ctx.users.map((u) => html`<option value="${u}" ${u === owner ? "selected" : ""}>${u === ctx.me ? `${u} (you)` : u}</option>`)}</select>
      </label>
      <label class="field">Main reason
        <select name="accept_reason">${ACCEPT_REASONS.map((r) => html`<option>${r}</option>`)}</select>
      </label>
      <label class="field">Next step
        <input name="next_action" value="${s.next || ""}" required>
      </label>
      <label class="field">Due
        <input type="date" name="due" value="${due}" min="${ctx.today}" required>
      </label>
    </div>
    <fieldset class="rating">
      <legend>Rating: how promising is it? (1 low, 5 high)</legend>
      ${[1, 2, 3, 4, 5].map((n) => html`<label><input type="radio" name="rating" value="${n}" ${n === rating ? "checked" : ""}><span>${n}</span></label>`)}
    </fieldset>
    <label class="field">Comment for the Direction agent <span class="hint">Optional. What made this company worth it.</span>
      <textarea name="comment" rows="2"></textarea>
    </label>
    <div data-slot="duplicates">${duplicateCheck(d)}</div>
    <div class="actions">
      <button type="submit" class="btn primary" disabled>Accept</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      <span class="missing" data-missing>${s.next ? "Confirm the duplicate check." : "Name the next step."}</span>
    </div>
    <p class="meta-3">Creates the lead in Insightly as Identified. The Outreach agent drafts the first email for the owner to approve.</p>
  </form>`;
}

function returnForm(o) {
  const n = ticksFor(o.id).size;
  return html`
  <form class="panel-form" data-act="returnForm" data-on="submit input" ${formData(o)} aria-label="Send ${o.company.name} back to the Research agent">
    <p class="md" data-backsum>${n ? `${plural(n, "ticked question")} ${n === 1 ? "goes" : "go"} to the Research agent.` : "No questions ticked yet. Tick them under What we don't know, or write one below."}</p>
    <label class="field">Anything else to look into
      <textarea name="questions" rows="2" placeholder="For example: confirm who now leads the automation program."></textarea>
    </label>
    <div class="actions">
      <button type="submit" class="btn primary" ${n ? "" : "disabled"}>Send back</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      <span class="missing" data-missing>${n ? "" : "Tick a question or write one."}</span>
    </div>
    <p class="meta-3">The Research agent updates the brief, and it returns here.</p>
  </form>`;
}

function rejectForm(o) {
  return html`
  <form class="panel-form" data-act="rejectForm" data-on="submit input change" ${formData(o)} aria-label="Reject ${o.company.name}">
    <label class="field" style="max-width:420px">Reason
      <select name="reason" required><option value="">Choose a reason</option>${REJECT_REASONS.map((r) => html`<option>${r}</option>`)}</select>
    </label>
    <fieldset class="tick-cards scope-cards"><legend class="field">This applies to</legend>
      <label class="tick-card"><input type="radio" name="scope" value="opportunity" checked><span><b>Just this company</b><span class="meta-3">It moves to Set aside.</span></span></label>
      <label class="tick-card"><input type="radio" name="scope" value="theme"><span><b>Companies like this</b><span class="meta-3">The Direction agent proposes a change to the search for a person to approve.</span></span></label>
    </fieldset>
    <label class="field">Comment <span class="hint">Needed when the reason is Other.</span>
      <textarea name="comment" rows="2"></textarea>
    </label>
    <label class="check"><input type="checkbox" name="suggest_exclusion">Suggest leaving this company out of future searches</label>
    <div class="actions">
      <button type="submit" class="btn primary" disabled>Reject</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      <span class="missing" data-missing>Choose a reason first.</span>
    </div>
  </form>`;
}

// ---------- after drawing: keep research status fresh ----------
// Keys on Your decision (J/K next brief, A/S/R open a decision) work wherever focus is on the page, except in a field.
// One listener on the main region, installed once; it acts only while this stage is on screen.
let keysOn = false;
function reviewKey(e) {
  if (!/^#\/companies\?(.*&)?tab=decision/.test(location.hash) || typingIn(e) || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = (e.key || "").toLowerCase();
  if (k === "j" || k === "k") {
    const list = [...document.querySelectorAll(".queue .pick")];
    const i = list.findIndex((a) => a.getAttribute("aria-current") === "true");
    const n = list[(i + (k === "j" ? 1 : -1) + list.length) % list.length];
    if (n && list.length > 1) { e.preventDefault(); location.hash = n.getAttribute("href"); }
    return;
  }
  const mode = { a: "accept", s: "return", r: "reject" }[k];
  const btn = mode && document.querySelector(`.band [data-act=mode][data-mode=${mode}]`);
  if (btn) { e.preventDefault(); btn.click(); }
}

export function after(root, ctx, d) {
  clearTimeout(ui.pollTimer);
  if (!keysOn) { root.addEventListener("keydown", reviewKey); keysOn = true; }
  if (d.tab === "decision") {
    for (const o of d.lists.decision) {
      if (!known(checks.related, o.id)) ctx.api.get(`/opportunities/${enc(o.id)}/related`).then((r) => keep(checks.related, o.id, r)).catch(() => null);
    }
  }
  const running = d.tab === "research" && Object.values(d.details || {}).some((o) => o.research && ["running", "queued"].includes(o.research.status));
  if (!running) return;
  const route = location.hash;
  ui.pollTimer = setTimeout(async () => {
    if (location.hash !== route || root.contains(document.activeElement) && document.activeElement.matches("input, textarea, select")) {
      if (location.hash === route) after(root, ctx, d);
      return;
    }
    if (ctx.config && ctx.config.appMode === "live") await ctx.api.post("/research/sync").catch(() => null);
    const before = new Set(d.lists.research.map((o) => o.id));
    await ctx.refresh();
    const now = await ctx.api.get("/opportunities?stage=review").catch(() => []);
    for (const o of now.filter((x) => before.has(x.id))) {
      ctx.toast(`Brief ready: ${o.company}.`, { link: { href: href("companies", { tab: "decision", id: o.id }), label: "Review it" } });
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
        ui.checked.delete(id);
      } catch (err) {
        ui.error = { where: "cands", message: err.message };
        break;
      }
    }
  });
  if (created.length) ctx.replace("companies", { tab: "found" });  // the panel's company has moved on
  await ctx.refresh();
  if (!created.length) return;
  const first = await ctx.api.get(`/opportunities/${enc(created[0])}`).catch(() => null);
  const name = first ? first.company.name : "the company";
  if (first && first.stage === "review") {
    ctx.toast(created.length === 1 ? `Brief ready: ${name}.` : `${plural(created.length, "brief")} ready, starting with ${name}.`,
      { link: { href: href("companies", { tab: "decision", id: first.id }), label: "Review" } });
  } else {
    ctx.toast(created.length === 1 ? `${name} is being researched. It moves to Your decision when the brief is done.`
      : `${plural(created.length, "company", "companies")} are being researched.`, { link: { href: href("companies", { tab: "research" }), label: "Watch" } });
  }
}

function formMissing(form, text) {
  const submit = form.querySelector("[type=submit]");
  const line = form.querySelector("[data-missing]");
  submit.disabled = !!text;
  if (line) line.textContent = text || "";
}

function typingIn(e) {
  return e.target && e.target.matches && e.target.matches("input, textarea, select");
}

// Ticks and folds change only what they show: no reload, so the page does not jump.
function showTicks(id) {
  const n = ticksFor(id).size;
  const qs = document.querySelectorAll("[data-act=tick]").length;
  const sum = document.querySelector("[data-ticksum]");
  if (sum) { sum.textContent = `${plural(qs, "open question")}${n ? `, ${n} ticked` : ""}`; sum.className = n ? "you-text md" : "meta-3"; }
  const back = document.querySelector("[data-backlabel]");
  if (back) back.textContent = n ? `Send back with ${plural(n, "question")}` : "Send back with questions";
  const form = document.querySelector("form[data-act=returnForm]");
  if (form) {
    const line = form.querySelector("[data-backsum]");
    if (line) line.textContent = n ? `${plural(n, "ticked question")} ${n === 1 ? "goes" : "go"} to the Research agent.` : "No questions ticked yet. Tick them under What we don't know, or write one below.";
    formMissing(form, n || form.elements.questions.value.trim() ? "" : "Tick a question or write one.");
  }
}

function setFold(btn, open) {
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  const box = document.getElementById(btn.getAttribute("aria-controls"));
  if (box) box.hidden = !open;
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

  // the side panel
  openRow(t, e, ctx) { ctx.focusAfter = "#panel-h"; },  // the link navigates; the panel's heading then takes focus
  closePanel(t, e, ctx) {
    const { tab, id } = ctx.route.params;
    ui.skipping = null;
    ctx.focusAfter = id ? `[data-row="${id}"].nm` : null;
    ctx.go("companies", { tab });
  },
  panelKeys(t, e, ctx) {
    if (typingIn(e)) return;
    if (e.key === "Escape") { e.preventDefault(); actions.closePanel(t, e, ctx); return; }
    const k = e.key.toLowerCase();
    if (k !== "j" && k !== "k") return;
    const link = t.querySelector(k === "j" ? "a.next" : "a[aria-label='Previous company']");
    if (link) { e.preventDefault(); ctx.focusAfter = "#panel-h"; location.hash = link.getAttribute("href"); }
  },

  // Found
  check(t, e, ctx) {
    if (t.checked) ui.checked.add(t.dataset.id); else ui.checked.delete(t.dataset.id);
    if (!ui.checked.size) ui.bulkSkip = false;
    ctx.focusAfter = `[data-act=check][data-id="${t.dataset.id}"]`;
    ctx.refresh();
  },
  checkAll(t, e, ctx) {
    const boxes = [...document.querySelectorAll("[data-act=check]")];
    ui.checked = new Set(t.checked ? boxes.map((b) => b.dataset.id) : []);
    if (!ui.checked.size) ui.bulkSkip = false;
    ctx.focusAfter = "[data-act=checkAll]";
    ctx.refresh();
  },
  clearChecks(t, e, ctx) { ui.checked.clear(); ui.bulkSkip = false; ctx.focusAfter = "[data-act=checkAll]"; ctx.refresh(); },
  bulkSkipOpen(t, e, ctx) { ui.bulkSkip = !ui.bulkSkip; ctx.focusAfter = ui.bulkSkip ? ".skip-form select" : null; ctx.refresh(); },
  async unskip(t, e, ctx) {
    t.disabled = true;
    dropToast(`skip:${t.dataset.id}`);
    try {
      ctx.toast(await unskip(ctx, t.dataset.id));
      ctx.replace("companies", { tab: "aside" });
    } catch (err) {
      ctx.toast(err.message);
    }
    ctx.refresh();
  },
  researchOne(t, e, ctx) { pick([t.dataset.id], ctx, t); },
  researchSelected(t, e, ctx) { pick([...ui.checked], ctx, t); },
  skipOpen(t, e, ctx) {
    ui.skipping = t.dataset.id;
    ctx.focusAfter = ".skip-form select";
    ctx.refresh();
  },
  skipCancel(t, e, ctx) { ui.skipping = null; ui.bulkSkip = false; ctx.refresh(); },
  async skipForm(form, e, ctx) {
    const reason = form.elements.reason.value;
    if (e.type !== "submit") return formMissing(form, reason ? "" : "Choose a reason first.");
    if (!reason) return;
    const button = form.querySelector("[type=submit]");
    try {
      const id = form.dataset.id;
      await busy(button, () => ctx.api.post(`/candidates/${enc(id)}/skip`, { reason, scope: form.elements.scope.value }));
      ui.skipping = null;
      ui.checked.delete(id);
      ui.error = null;
      offerUndo(ctx, `Set aside ${form.dataset.name || "the candidate"}. The reason goes to the Direction agent.`, () => unskip(ctx, id), { key: `skip:${id}` });
      ctx.replace("companies", { tab: "found" });
      ctx.refresh();
    } catch (err) {
      fail("cands", err, ctx);
    }
  },
  // Set aside every ticked candidate with one reason; one Undo puts them all back.
  async bulkSkipForm(form, e, ctx) {
    const reason = form.elements.reason.value;
    if (e.type !== "submit") return formMissing(form, reason ? "" : "Choose a reason first.");
    if (!reason) return;
    const ids = [...ui.checked];
    const done = [];
    await busy(form.querySelector("[type=submit]"), async () => {
      for (const id of ids) {
        try {
          await ctx.api.post(`/candidates/${enc(id)}/skip`, { reason, scope: form.elements.scope.value });
          done.push(id);
          ui.checked.delete(id);
        } catch (err) {
          ui.error = { where: "cands", message: err.message };
          break;
        }
      }
    });
    ui.bulkSkip = false;
    if (done.length) {
      offerUndo(ctx, `Set aside ${plural(done.length, "company", "companies")}. The reason goes to the Direction agent.`,
        () => Promise.all(done.map((id) => ctx.api.post(`/candidates/${enc(id)}/unskip`))).then(() => `Put ${plural(done.length, "company", "companies")} back in Found.`));
    }
    ctx.refresh();
  },

  // Researching
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

  // Your decision
  mode(t, e, ctx) {
    ui.mode = t.dataset.mode || null;
    ui.modeFor = t.dataset.id || ui.modeFor;
    ui.error = null;
    if (ui.mode === "return" && ui.modeFor) foldsFor({ id: ui.modeFor, brief: null }).unknowns = true;
    ctx.focusAfter = ui.mode ? ".band-form select, .band-form textarea" : "[data-act=mode][data-mode=accept]";
    ctx.refresh();
  },
  tick(t) {
    const set = ticksFor(t.dataset.id);
    const q = t.dataset.q;
    if (set.has(q)) set.delete(q); else set.add(q);
    t.setAttribute("aria-pressed", set.has(q) ? "true" : "false");
    showTicks(t.dataset.id);
  },
  fold(t) {
    const open = t.getAttribute("aria-expanded") !== "true";
    foldsFor({ id: t.dataset.id })[t.dataset.fold] = open;
    setFold(t, open);
  },
  foldAll(t) {
    const open = t.dataset.open === "1";
    const state = foldsFor({ id: t.dataset.id });
    document.querySelectorAll("[data-act=fold]").forEach((b) => { state[b.dataset.fold] = open; setFold(b, open); });
    t.dataset.open = open ? "" : "1";
    t.textContent = open ? "Fold all" : "Open all";
  },
  acceptForm(form, e, ctx) {
    const f = form.elements;
    const missing = !f.next_action.value.trim() ? "Name the next step." : !f.due.value ? "Choose a due date." : !f.duplicates_checked.checked ? "Confirm the duplicate check." : "";
    if (e.type !== "submit") return formMissing(form, missing);
    if (missing) return;
    submitStep4(form, ctx, {
      decision: "accept", owner: f.owner.value, rating: Number(f.rating.value), accept_reason: f.accept_reason.value,
      next_action: f.next_action.value.trim(), due: f.due.value, comment: f.comment.value.trim(), duplicates_checked: true,
    }, (name) => `Accepted ${name}. The lead is created in Insightly as Identified, and the Outreach agent drafts the first email for ${f.owner.value} to approve.`);
  },
  returnForm(form, e, ctx) {
    const ticked = [...ticksFor(form.dataset.id)];
    const own = form.elements.questions.value.trim();
    const q = [...ticked, ...(own ? [own] : [])].join("\n");
    if (e.type !== "submit") return formMissing(form, q ? "" : "Tick a question or write one.");
    if (!q) return;
    submitStep4(form, ctx, { decision: "return", questions: q }, (name) => `Sent ${name} back to the Research agent${ticked.length ? ` with ${plural(ticked.length + (own ? 1 : 0), "question")}` : " with your question"}.`);
  },
  rejectForm(form, e, ctx) {
    const f = form.elements;
    const missing = !f.reason.value ? "Choose a reason first." : f.reason.value === "Other" && !f.comment.value.trim() ? "Write a comment: the reason is Other." : "";
    if (e.type !== "submit") return formMissing(form, missing);
    if (missing) return;
    submitStep4(form, ctx, {
      decision: "reject", reason: f.reason.value, scope: f.scope.value, comment: f.comment.value.trim(), suggest_exclusion: f.suggest_exclusion.checked,
    }, (name) => f.scope.value === "theme" ? `Rejected ${name}. The Direction agent will propose a change to the search for a person to approve.`
      : `Rejected ${name}. It moves to Set aside with your reason.`);
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
  ui.ticks.delete(id);
  ui.folds.delete(id);
  let text = message(name);
  let link = body.decision === "accept" ? { href: href("companies", { tab: "identified" }), label: "See Identified" } : null;
  if (body.decision === "return") {
    // Research may already be done (demo mode finishes at once); say what actually happened.
    const after = await ctx.api.get(`/opportunities/${enc(id)}`).catch(() => null);
    if (after && after.stage === "review") {
      text = `The Research agent answered your questions on ${name}. Its brief is updated.`;
      link = { href: href("companies", { tab: "decision", id }), label: "Review it" };
    } else {
      link = { href: href("companies", { tab: "research" }), label: "Watch the research" };
    }
  }
  if (res && res.undo) {
    offerUndo(ctx, `${text} You can undo this for ${res.undo.seconds} seconds.`, () =>
      ctx.api.post(`/opportunities/${enc(id)}/step4/undo`).then(() => `Undone. ${name} is back in Your decision; nothing reached the CRM or the agents.`),
    { seconds: res.undo.seconds, link });
  } else {
    ctx.toast(text, { link });
  }
  ctx.scrollTop = true;
  ctx.go("companies", { tab: "decision" });
}

const unskip = (ctx, id) => ctx.api.post(`/candidates/${enc(id)}/unskip`).then(() => "Put back in Found. The skip no longer teaches the Direction agent.");
