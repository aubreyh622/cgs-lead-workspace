// Qualification: Steps 6-8. Evidence from conversations, the rules engine's readiness, next actions and tasks,
// the qualification pack, and the Step 8 decision. Agents propose; a person accepts, edits or rejects every change.
import { html, icon, day, addDays, daysBetween, href, plural, initials } from "../lib.js";
import { enc, ApiError } from "../api.js";
import { STAGE_LABELS, CLOSE_REASONS, NOTE_TYPES, NEED_FIELDS } from "../options.js";
import { emptyState, inlineError, crmLinks, crmConflicts, resolveCrm } from "../shell.js";

export const title = "Qualification";

const TABS = [
  ["active", "In conversation"],
  ["approval", "Awaiting approval"],
  ["qualification", "Qualified"],
  ["closed", "Closed"],
];
const DIMS = ["B", "A", "N", "T", "H", "E"];
const READINESS_TAG = { Ready: "good", "More evidence needed": "warn", "Not ready": "bad" };

let rules = null; // GET /api/rules, loaded once
const ui = { key: null, open: null, error: null, notice: null, folds: new Map() };  // folds: kept while moving between leads

function resetUi(key = null) {
  ui.key = key; ui.open = null; ui.error = null; ui.notice = null;
}

// Items waiting for this person: packs others sent for approval, and their own leads the rules call Ready.
export function waitingFor(me, opps) {
  return opps.filter((o) => (o.stage === "approval" && o.owner !== me)
    || (o.stage === "active" && o.owner === me && o.assessment && o.assessment.readiness === "Ready"));
}

// ---------- data ----------
export async function load(ctx, params) {
  const team = params.team === "1";
  const [all, r] = await Promise.all([
    ctx.api.get(`/opportunities?${TABS.map(([s]) => `stage=${s}`).join("&")}`),
    rules ? Promise.resolve(rules) : ctx.api.get("/rules"),
  ]);
  rules = r;
  // Approvers are usually not the owner, so Awaiting approval always shows the whole team.
  const visible = (o) => team || o.stage === "approval" || o.owner === ctx.me;
  const lists = Object.fromEntries(TABS.map(([s]) => [s, all.filter((o) => o.stage === s && visible(o))]));
  let tab = TABS.some(([t]) => t === params.tab) ? params.tab : null;
  if (!tab && params.id) tab = (all.find((o) => o.id === params.id) || {}).stage || null;
  if (!TABS.some(([t]) => t === tab)) tab = lists.active.length ? "active" : lists.approval.length ? "approval" : "active";
  const data = { tab, team, lists };
  const list = lists[tab];
  if (list.length) {
    const cur = list.find((o) => o.id === params.id) || list[0];
    if (ui.key !== cur.id) resetUi(cur.id);
    Object.assign(data, await leadData(ctx, cur.id));
  }
  return data;
}

// One lead's details and the manager agent's memory of it. Kept for a minute, so picking a lead already fetched
// shows at once; the full load then checks it again.
const leads = new Map();
const LEAD_TTL = 60 * 1000;

async function leadData(ctx, id) {
  const [detail, memory] = await Promise.all([ctx.api.get(`/opportunities/${enc(id)}`),
    ctx.api.get(`/opportunities/${enc(id)}/memory`).catch(() => null)]);
  leads.set(id, { at: Date.now(), value: { detail, memory } });
  return { detail, memory };
}

export function instant(ctx, params, last) {
  if (!last || params.tab !== last.tab || (params.team === "1") !== last.team) return null;
  if (!(last.lists[last.tab] || []).some((o) => o.id === params.id)) return null;
  const hit = leads.get(params.id);
  if (!hit || Date.now() - hit.at > LEAD_TTL) return null;
  if (ui.key !== params.id) resetUi(params.id);
  return { ...last, ...hit.value };
}

export function changed(early, d) {
  const pick = (x) => JSON.stringify([x.tab, x.detail, x.memory, (x.lists[x.tab] || []).map((o) => o.id)]);
  return pick(early) !== pick(d);
}

// After drawing: fetch the other leads in this list, so picking one shows it at once.
export function after(root, ctx, d) {
  for (const o of (d.lists[d.tab] || []).slice(0, 12)) {
    const hit = leads.get(o.id);
    if (!hit || Date.now() - hit.at > LEAD_TTL) leadData(ctx, o.id).catch(() => null);
  }
}

// ---------- words for values ----------
function scoreText(dim, v) {
  if (v === null || v === undefined) return "Pending";
  if (v === 2.5) return "2.5: first-time neutral";
  return `${v}: ${rules.anchors[dim][Math.floor(v)]}`;
}
const condText = (v) => (v === true ? "Met" : v === false ? "Not met" : "Pending");
const condTag = (v) => (v === true ? "good" : v === false ? "bad" : "");
const money = (n) => `$${Number(n).toLocaleString("en-US")}`;
const revenueText = (r) => (r ? (r.low === r.high ? money(r.low) : `${money(r.low)} to ${money(r.high)}`) : "To estimate");

function valueText(kind, key, v) {
  if (kind === "score") return scoreText(key, v);
  if (kind === "cond") return condText(v);
  if (kind === "blocker") return v ? rules.reasons[v] || v : "Cleared";
  if (kind === "revenue") return revenueText(v);
  return String(v);
}
const keyLabel = (kind, key) => (kind === "score" ? rules.labels[key] : kind === "cond" ? rules.conditions[key] : kind === "revenue" ? "CGS revenue" : "Blocker");

// Conditions from the summary's gaps (the list view carries the rules result, not the raw conditions).
function condsFromGaps(o) {
  const gaps = (o.assessment && o.assessment.gaps) || [];
  return Object.keys(rules.conditions).map((k) => {
    const g = gaps.find((x) => x.key === `condition:${k}`);
    return g ? (/not met$/.test(g.label) ? false : null) : true;
  });
}

// ---------- view ----------
export function view(d, ctx) {
  const count = Object.fromEntries(TABS.map(([s]) => [s, d.lists[s].length]));
  return html`
  <div class="page-head">
    <div>
      <h1>Qualification</h1>
      <p>Leads in conversation. Collect evidence until the rules say Ready, then send the pack for approval.</p>
    </div>
    <div class="actions"><a class="btn ghost" href="${href("qualification", { tab: d.tab, team: d.team ? undefined : "1" })}">${d.team ? "Show only mine" : "Show the whole team"}</a></div>
  </div>
  <div class="tabs" role="tablist" aria-label="Qualification stage" data-act="tabkeys" data-on="keydown">
    ${TABS.map(([id, label]) => html`
      <button type="button" role="tab" id="tab-${id}" aria-selected="${d.tab === id ? "true" : "false"}" aria-controls="panel"
        tabindex="${d.tab === id ? "0" : "-1"}" data-act="tab" data-tab="${id}">${label}<span class="count">${count[id]}</span></button>`)}
  </div>
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${d.lists[d.tab].length ? html`
    <div class="split wide-list">
      <nav class="card lead-table" aria-label="${TABS.find(([t]) => t === d.tab)[1]}">
        <div class="lead-row lead-head" aria-hidden="true"><span>Lead</span><span>Evidence</span><span>Next</span></div>
        ${d.lists[d.tab].map((o) => pickCard(o, d, ctx))}
      </nav>
      <section class="card detail" aria-labelledby="detail-h">${detail(d, ctx)}</section>
    </div>` : html`<section class="card">${emptyState(...{
      active: ["No one in conversation", "A lead arrives here when the client replies (record it on the Outreach page).", { href: href("outreach", { tab: "waiting" }), label: "Open Outreach" }],
      approval: ["Nothing waiting for approval", "A pack appears here when its owner sends it for approval."],
      qualification: ["Nothing qualified yet", "Approved packs move here."],
      closed: ["Nothing closed", "Opportunities closed at Step 8 appear here with the reason."],
    }[d.tab])}</section>`}
  </div>`;
}

function pickCard(o, d, ctx) {
  const conds = condsFromGaps(o);
  const met = conds.filter((x) => x === true).length;
  const late = o.next.due && daysBetween(ctx.today, o.next.due) < 0;
  const ready = o.assessment && o.assessment.readiness === "Ready";
  const flag = o.stage === "approval" ? "Waiting for approval" : ready ? "Ready to send for approval"
    : o.next.action || (o.assessment ? o.assessment.readiness : STAGE_LABELS[o.stage]);
  return html`
  <a class="lead-row" href="${href("qualification", { tab: d.tab, id: o.id, team: d.team ? "1" : undefined })}" ${d.detail && d.detail.id === o.id ? html`aria-current="true"` : ""}>
    <span><b>${o.contact.name || o.company}</b><span class="meta">${o.company}, ${o.owner || "no owner"}</span></span>
    <span>${o.assessment ? html`<span class="cond-bar" aria-hidden="true">${conds.map((v) => html`<span class="seg ${v === true ? "met" : v === false ? "not" : ""}"></span>`)}</span>
      <span class="meta-3">${met} of 6 conditions</span>` : html`<span class="meta-3">${STAGE_LABELS[o.stage]}</span>`}</span>
    <span class="lead-next ${late && o.stage === "active" ? "bad-text" : ready ? "good-text" : ""}">${flag}${o.next.due && o.stage === "active" ? html`<span class="meta-3">${late ? "Overdue since" : "Due"} ${day(o.next.due, ctx.today)}</span>` : ""}</span>
  </a>`;
}

// The first screen holds what a person acts on: the conditions, evidence waiting for review, and the next step.
// Everything else is in folded sections below; a section stays open or closed while they move between leads.
function detail(d, ctx) {
  const o = d.detail;
  const live = ["active", "approval"].includes(o.stage);
  const ready = o.assessment && o.assessment.readiness === "Ready";
  const tasks = taskCounts(o, ctx);
  const promises = d.memory ? d.memory.commitments.filter((c) => c.status === "open").length : 0;
  const a = o.assessment;
  return html`
  ${headerBlock(o, ctx)}
  ${noticeBlock()}
  ${o.stage === "approval" ? approvalBlock(o, ctx, "card") : ""}
  ${conditionsSummary(o, live && o.stage === "active")}
  ${live ? proposalsBlock(o, ctx) : ""}
  ${o.stage === "active" ? nextBlock(o, ctx, tasks) : ""}
  ${o.stage === "active" && ready ? packBlock(o, ctx) : ""}
  ${o.stage === "approval" ? approvalBlock(o, ctx, "pack") : ""}
  ${fold("scores", "Scores, conditions and revenue", html`${scoresBlock(o, ctx, live)}${conditionsBlock(o, ctx, live)}${revenueBlock(o, ctx, live)}`,
    { note: a ? `Qualification ${a.ql === a.qu ? a.ql : `${a.ql} to ${a.qu}`}, priority ${a.priority}` : "" })}
  ${fold("need", "Client need", needBlock(o, ctx, live), { note: NEED_FIELDS.some(([k]) => (o.need || {})[k]) ? "" : "Not written yet" })}
  ${live ? fold("tasks", "Next actions and tasks", actionsBlock(o, ctx), { note: tasks.text }) : ""}
  ${fold("memory", "What the manager agent remembers", memoryBlock(o, d.memory, ctx), { note: promises ? plural(promises, "open promise") : "" })}
  ${o.stage === "active" && !ready ? fold("exception", "Send for approval with an exception", packBlock(o, ctx), { note: a ? `The rules say ${a.readiness}` : "" }) : ""}
  ${historyBlock(o, ctx)}`;
}

// A folded section. Its summary records the person's choice, so a redraw keeps it.
function fold(key, title, body, { open = false, note = "" } = {}) {
  if (!body) return "";
  const shown = ui.folds.has(key) ? ui.folds.get(key) : open;
  return html`<details class="fold" ${shown ? "open" : ""}>
    <summary data-act="fold" data-fold="${key}"><span class="fold-title">${title}</span>${note ? html`<span class="meta-3">${note}</span>` : ""}</summary>
    <div class="fold-body">${body}</div>
  </details>`;
}

function taskCounts(o, ctx) {
  const open = o.tasks.filter((t) => t.status !== "done");
  const late = open.filter((t) => daysBetween(ctx.today, t.due) < 0).length;
  const parts = [open.length ? `${open.length} open` : "", late ? `${late} overdue` : "", o.suggestions.length ? plural(o.suggestions.length, "suggestion") : ""].filter(Boolean);
  return { open: open.length, late, text: parts.join(", ") };
}

// The six conditions at a glance, what the rules say, and what is still missing.
function conditionsSummary(o, live) {
  const a = o.assessment;
  if (!a) return "";
  const entries = Object.entries(rules.conditions);
  const met = entries.filter(([k]) => o.conditions[k] === true).length;
  const q = a.ql === a.qu ? `${a.ql}` : `${a.ql} to ${a.qu}`;
  return html`
  <div class="block stack">
    <div class="row-flex"><h3 class="sub-title" style="margin:0">Conditions</h3><span class="meta">${met} of ${entries.length} met</span>
      <span class="tag ${READINESS_TAG[a.readiness] || ""}" style="margin-left:auto">${a.readiness}</span></div>
    <ul class="cond-list">${entries.map(([k, label]) => {
      const v = o.conditions[k];
      return html`<li class="${v === true ? "met" : v === false ? "not" : ""}"><span class="cond-dot" aria-hidden="true">${v === true ? icon("check", 13) : ""}</span>
        <span>${label}</span>${v === true ? html`<span class="sr-only">Met</span>` : html`<span class="meta-3">${v === false ? "Not met" : "Needs evidence"}</span>`}</li>`;
    })}</ul>
    ${o.blocker ? html`
    <div class="notice bad">${icon("alert")}<span><b>Blocker: ${rules.reasons[o.blocker] || o.blocker}.</b> The rules say Not ready while it stands.</span></div>
    ${!live ? "" : isOpen("blocker") ? html`
    <form class="panel-form" data-act="reviewForm" data-on="submit input" data-kind="blocker_clear" data-id="${o.id}" data-revision="${o.revision}">
      <label class="field">Why the blocker no longer applies<textarea name="reason" rows="2" required></textarea></label>
      <div class="actions"><button type="submit" class="btn primary" disabled>Clear the blocker</button>${cancelBtn}<span class="missing" data-missing>Give a reason.</span></div>
      ${err("review")}
    </form>` : html`<div>${openBtn("blocker", "Clear the blocker…")}</div>`}` : ""}
    ${live && a.gaps.length ? html`
    <div>
      <p class="sub-title">Still missing</p>
      <ul class="gaps compact">${a.gaps.map((g) => html`<li><b>${g.label}</b><span class="meta">Ask: ${g.question}</span></li>`)}</ul>
    </div>` : ""}
    <p class="meta-3">${a.coverage ? `Qualification score ${q} of 100, with ${a.coverage}% of the evidence weight confirmed.` : "No score is confirmed yet."} Priority ${a.priority}. Rules ${a.ruleVersion}; no AI calculates these numbers.</p>
  </div>`;
}

// The next step, and the way new evidence comes in: notes from a call or email.
function nextBlock(o, ctx, tasks) {
  const late = o.next.due && daysBetween(ctx.today, o.next.due) < 0;
  return html`
  <div class="block stack">
    <div><h3 class="sub-title" style="margin:0">Next step</h3>
      <p>${o.next.action || "None set."}${o.next.due ? html` <span class="${late ? "bad-text" : "meta"}">${late ? "Overdue since" : "Due"} ${day(o.next.due, ctx.today)}</span>` : ""}</p>
      ${tasks.late ? html`<p class="meta bad-text">${plural(tasks.late, "agreed task")} overdue: see Next actions and tasks below.</p>` : ""}</div>
    ${o.stage === "active" ? notesForm(o, ctx) : ""}
  </div>`;
}

function headerBlock(o, ctx) {
  return html`
  <div class="block">
    <h2 id="detail-h">${o.contact.name || o.company.name}</h2>
    <p class="meta">${o.contact.title ? `${o.contact.title}, ` : ""}${o.company.name}. ${o.title}.</p>
    <div class="row-flex" style="margin-top:10px">
      <span class="tag">${STAGE_LABELS[o.stage] || o.stage}</span>
      <span class="tag">Owner ${o.owner || "not set"}</span>
      ${o.replyLabel ? html`<span class="tag info">Reply label: ${o.replyLabel}</span>` : ""}
      <span class="tag">Revenue ${revenueText(o.revenue)}</span>
      ${crmLinks(o.crm)}
    </div>
    ${o.next.action && !["active", "approval"].includes(o.stage) ? html`<p class="meta" style="margin-top:10px">Next step: ${o.next.action}${o.next.due ? `, due ${day(o.next.due, ctx.today)}` : ""}.</p>` : ""}
    ${o.crm && o.crm.conflicts && o.crm.conflicts.length ? html`<div style="margin-top:12px">${crmConflicts(o.id, o.crm)}</div>` : ""}
  </div>`;
}

function noticeBlock() {
  if (!ui.notice) return "";
  return html`<div class="block stack">${ui.notice.map((n) => html`<div class="notice ${n.cls || ""}">${icon(n.cls === "good" ? "check" : "alert")}<span>${n.text}</span></div>`)}</div>`;
}

function err(where) {
  return inlineError(ui.error && ui.error.where === where ? ui.error.message : "");
}

const isOpen = (name) => ui.open === name;
const openBtn = (name, label, cls = "btn small", aria = "") => html`<button type="button" class="${cls}" data-act="open" data-open="${name}" ${aria ? html`aria-label="${aria}"` : ""}>${label}</button>`;
const cancelBtn = html`<button type="button" class="btn ghost" data-act="open" data-open="">Cancel</button>`;

// ----- the manager agent's memory of this opportunity -----
function memoryBlock(o, mem, ctx) {
  if (!mem || (!mem.summary && !mem.commitments.length)) return "";
  const open = mem.commitments.filter((c) => c.status === "open");
  const closed = mem.commitments.filter((c) => c.status !== "open");
  return html`
  <div class="block stack">
    ${mem.updatedOn ? html`<p class="meta-3">Updated ${day(mem.updatedOn, ctx.today)}</p>` : ""}
    ${mem.summary ? html`<p>${mem.summary}</p>` : ""}
    ${open.length ? html`<ul class="history">${open.map((c) => html`<li>
      <b>${c.who}: ${c.what}</b>
      <span class="meta-3">${c.side === "cgs" ? "CGS promised" : c.side === "client" ? "The client promised" : "Promised"}${c.due ? `, due ${day(c.due, ctx.today)}` : ""}${c.overdue ? html`. <span class="bad-text">Overdue by ${plural(c.daysOverdue, "day")}</span>` : ""}</span>
      ${c.quote ? html`<span class="quote">"${c.quote}"</span>` : ""}
      ${isOpen(`promise:${c.id}`) ? html`
      <form class="panel-form" data-act="promiseForm" data-on="submit input" data-id="${o.id}" data-cid="${c.id}">
        <label class="field">What shows it happened, or why it no longer applies<input name="why" required></label>
        <div class="actions"><button type="submit" class="btn primary small" name="status" value="done" disabled>Done</button>
          <button type="submit" class="btn small" name="status" value="dropped" disabled>No longer applies</button>${cancelBtn}<span class="missing" data-missing>Say why.</span></div>
        ${err(`promise:${c.id}`)}
      </form>` : html`<span>${openBtn(`promise:${c.id}`, "Close…", "btn ghost small", `Close the promise: ${c.what}`)}</span>`}
    </li>`)}</ul>` : ""}
    ${closed.length ? html`<p class="meta-3">${plural(closed.length, "earlier promise")} closed.</p>` : ""}
  </div>`;
}

// ----- evidence proposals -----
function proposalsBlock(o) {
  const pending = o.proposals.filter((p) => p.status === "pending");
  if (!pending.length) return "";
  return html`
  <div class="block stack">
    <div><h3 class="sub-title" style="margin:0">Evidence to review</h3>
      <p class="meta">The evidence agent proposed these from the notes. Client quotes are checked word for word against the notes. Nothing changes until you accept.</p></div>
    ${safeToAcceptAll(o, pending).length > 1 ? html`<div><button type="button" class="btn small" data-act="acceptAll" data-ids="${safeToAcceptAll(o, pending).map((p) => p.id).join(",")}">Accept all ${safeToAcceptAll(o, pending).length} that change nothing confirmed</button>${err("acceptAll")}</div>` : ""}
    ${pending.map((p) => proposalCard(o, p))}
  </div>`;
}

// "Accept all" covers only proposals that do not replace a value a person confirmed; those need a reason each.
function safeToAcceptAll(o, pending) {
  return pending.filter((p) => {
    const current = p.kind === "score" ? o.scores[p.key] : p.kind === "cond" ? o.conditions[p.key] : o.blocker;
    return current === null || current === undefined || current === p.value;
  });
}

function proposalCard(o, p) {
  const current = p.kind === "score" ? o.scores[p.key] : p.kind === "cond" ? o.conditions[p.key] : o.blocker;
  const conflict = current !== null && current !== undefined && current !== p.value;
  const name = `prop:${p.id}`;
  const editing = isOpen(`${name}:edit`);
  const rejecting = isOpen(`${name}:reject`);
  const accepting = isOpen(`${name}:accept`);
  return html`
  <div class="suggestion">${icon("bulb", 20)}<div style="flex:1;min-width:0">
    <b>${keyLabel(p.kind, p.key)}: ${valueText(p.kind, p.key, p.value)}</b>
    <p class="meta-3">Evidence agent, ${p.evidenceType === "client_statement" ? "from a client statement" : "from an internal note"}. Now: ${valueText(p.kind, p.key, current)}.</p>
    ${p.quote ? html`<p class="quote">"${p.quote}"</p>` : ""}
    ${p.why ? html`<p class="meta" style="margin-top:6px">${p.why}</p>` : ""}
    ${conflict ? html`<div class="notice warn" style="margin-top:8px">${icon("alert")}<span>This replaces a value a person already confirmed. Accepting needs a reason.</span></div>` : ""}
    ${accepting || editing || rejecting ? html`
    <form class="panel-form" style="margin-top:10px" data-act="proposalForm" data-on="submit input change" data-id="${p.id}"
      data-do="${accepting ? "accept" : editing ? "edit" : "reject"}" data-kind="${p.kind}" data-key="${p.key}">
      ${editing ? valueSelect(p.kind, p.key, p.value) : ""}
      <label class="field">${rejecting ? "Why reject it" : editing ? "Why change it" : "Why replace the confirmed value"}<textarea name="reason" rows="2" required></textarea></label>
      <div class="actions">
        <button type="submit" class="btn primary" disabled>${accepting ? "Accept" : editing ? "Accept my version" : "Reject"}</button>${cancelBtn}
        <span class="missing" data-missing>Give a reason.</span>
      </div>
      ${err(name)}
    </form>` : html`
    <div class="actions" style="margin-top:10px">
      ${conflict ? openBtn(`${name}:accept`, "Accept…", "btn primary small") : html`<button type="button" class="btn primary small" data-act="proposal" data-do="accept" data-id="${p.id}">Accept</button>`}
      ${openBtn(`${name}:edit`, "Edit…", "btn small", `Edit the proposal for ${keyLabel(p.kind, p.key)}`)}
      ${openBtn(`${name}:reject`, "Reject…", "btn ghost small", `Reject the proposal for ${keyLabel(p.kind, p.key)}`)}
    </div>
    ${err(name)}`}
  </div></div>`;
}

function valueSelect(kind, key, value, label = "Value") {
  let options;
  if (kind === "score") {
    const scores = key === "H" ? [0, 1, 2, 2.5, 3, 4, 5] : [0, 1, 2, 3, 4, 5];
    options = [["", "Pending (not confirmed)"], ...scores.map((s) => [String(s), scoreText(key, s)])];
  } else if (kind === "cond") {
    options = [["true", "Met"], ["false", "Not met"], ["", "Pending"]];
  } else {
    options = Object.entries(rules.reasons);
  }
  const cur = value === null || value === undefined ? "" : String(value);
  return html`<label class="field">${label}<select name="value">${options.map(([v, t]) => html`<option value="${v}" ${v === cur ? "selected" : ""}>${t}</option>`)}</select></label>`;
}

function parseValue(kind, raw) {
  if (kind === "score") return raw === "" ? null : Number(raw);
  if (kind === "cond") return raw === "true" ? true : raw === "false" ? false : null;
  return raw;
}

// ----- notes -----
function notesForm(o, ctx) {
  if (!isOpen("note")) {
    return html`<div class="actions">${openBtn("note", "Add notes from a call or email…", "btn")}
      <span class="meta">The evidence agent reads them and proposes scores and conditions for you to review.</span></div>`;
  }
  return html`
    <form class="panel-form" data-act="noteForm" data-on="submit input" data-id="${o.id}">
      <h3 class="sub-title">Add conversation notes</h3>
      <div class="form-grid">
        <label class="field">Kind<select name="type">${NOTE_TYPES.map((t) => html`<option>${t}</option>`)}</select></label>
        <label class="field">Date<input type="date" name="on" value="${ctx.today}" max="${ctx.today}" required></label>
      </div>
      <label class="field">Notes <span class="hint">Write what was said. Quote the client where you can; the agent may only cite words that are here.</span>
        <textarea name="text" rows="8" required></textarea></label>
      <label class="check"><input type="checkbox" name="analyze" checked>Ask the evidence agent for proposals</label>
      <div class="actions"><button type="submit" class="btn primary" disabled>Save notes</button>${cancelBtn}<span class="missing" data-missing>Write the notes.</span></div>
      ${err("note")}
    </form>`;
}

// ----- scores, conditions, revenue, need -----
function scoresBlock(o, ctx, live) {
  return html`
  <div class="block">
    <h3 class="sub-title">Scores (BANTHE)</h3>
    <table class="list compact">
      <thead><tr><th scope="col">Dimension</th><th scope="col">Weight</th><th scope="col">Confirmed score</th><th scope="col">Minimum</th>${live ? html`<th scope="col"><span class="sr-only">Review</span></th>` : ""}</tr></thead>
      <tbody>${DIMS.map((k) => {
        const v = o.scores[k];
        const floor = rules.floors[k];
        const low = floor !== undefined && v !== null && v !== undefined && v < floor;
        return html`
        <tr>
          <td><b>${rules.labels[k]}</b></td>
          <td class="num">${rules.weights[k]}</td>
          <td>${v === null || v === undefined ? html`<span class="tag">Pending</span>` : html`<span class="${low ? "bad-text" : ""}">${scoreText(k, v)}</span>`}</td>
          <td class="num">${floor === undefined ? "None" : floor}</td>
          ${live ? html`<td>${isOpen(`score:${k}`) ? "" : openBtn(`score:${k}`, "Review…", "btn ghost small", `Review the ${rules.labels[k]} score`)}</td>` : ""}
        </tr>
        ${isOpen(`score:${k}`) ? html`<tr><td colspan="5">${reviewForm(o, "score", k, v)}</td></tr>` : ""}`;
      })}</tbody>
    </table>
  </div>`;
}

function reviewForm(o, kind, key, value) {
  return html`
  <form class="panel-form" data-act="reviewForm" data-on="submit input change" data-kind="${kind}" data-key="${key}" data-id="${o.id}" data-revision="${o.revision}">
    ${valueSelect(kind, key, value, kind === "score" ? `${rules.labels[key]} score` : rules.conditions[key])}
    ${kind === "cond" ? html`<label class="field">If not met, is it a blocker? <span class="hint">Optional. A blocker makes the rules say Not ready.</span>
      <select name="blocker"><option value="">No blocker</option>${Object.entries(rules.reasons).map(([v, t]) => html`<option value="${v}">${t}</option>`)}</select></label>` : ""}
    <label class="field">Evidence or reason <span class="hint">What the client said or did. It is kept with the change.</span><textarea name="reason" rows="2" required></textarea></label>
    <div class="actions"><button type="submit" class="btn primary" disabled>Save review</button>${cancelBtn}<span class="missing" data-missing>Give the evidence or a reason.</span></div>
    ${err("review")}
  </form>`;
}

function conditionsBlock(o, ctx, live) {
  return html`
  <div class="block">
    <h3 class="sub-title">Conditions</h3>
    <table class="list compact">
      <thead><tr><th scope="col">Condition</th><th scope="col">Status</th>${live ? html`<th scope="col"><span class="sr-only">Review</span></th>` : ""}</tr></thead>
      <tbody>${Object.entries(rules.conditions).map(([k, label]) => html`
        <tr><td><b>${label}</b></td><td><span class="tag ${condTag(o.conditions[k])}">${condText(o.conditions[k])}</span></td>
          ${live ? html`<td>${isOpen(`cond:${k}`) ? "" : openBtn(`cond:${k}`, "Review…", "btn ghost small", `Review: ${label}`)}</td>` : ""}</tr>
        ${isOpen(`cond:${k}`) ? html`<tr><td colspan="3">${reviewForm(o, "cond", k, o.conditions[k])}</td></tr>` : ""}`)}
      </tbody>
    </table>
  </div>`;
}

function revenueBlock(o, ctx, live) {
  return html`
  <div class="block">
    <div class="row-flex"><h3 class="sub-title" style="margin:0">CGS revenue estimate</h3><span>${revenueText(o.revenue)}</span>
      ${live && !isOpen("revenue") ? openBtn("revenue", "Change…", "btn ghost small", "Change the revenue estimate") : ""}</div>
    ${isOpen("revenue") ? html`
    <form class="panel-form" style="margin-top:10px" data-act="revenueForm" data-on="submit input" data-id="${o.id}" data-revision="${o.revision}">
      <div class="form-grid">
        <label class="field">Low (USD)<input type="number" name="low" min="1" step="any" value="${o.revenue ? o.revenue.low : ""}"></label>
        <label class="field">High (USD)<input type="number" name="high" min="1" step="any" value="${o.revenue ? o.revenue.high : ""}"></label>
      </div>
      <label class="check"><input type="checkbox" name="clear">Clear the estimate (not known)</label>
      <label class="field">Reason<textarea name="reason" rows="2" required></textarea></label>
      <div class="actions"><button type="submit" class="btn primary" disabled>Save estimate</button>${cancelBtn}<span class="missing" data-missing>Give a reason.</span></div>
      ${err("review")}
    </form>` : ""}
  </div>`;
}

function needBlock(o, ctx, live) {
  const need = o.need || {};
  const filled = NEED_FIELDS.some(([k]) => need[k]);
  return html`
  <div class="block">
    ${live && !isOpen("need") ? html`<div>${openBtn("need", "Edit…", "btn ghost small", "Edit the client need")}</div>` : ""}
    ${isOpen("need") ? html`
    <form class="panel-form" style="margin-top:10px" data-act="needForm" data-on="submit" data-id="${o.id}">
      <div class="form-grid">${NEED_FIELDS.map(([k, label]) => html`<label class="field">${label}<input name="${k}" value="${need[k] || ""}"></label>`)}</div>
      <p class="meta-3">Editing the need statement does not confirm the Need score or CGS fit; review those separately.</p>
      <div class="actions"><button type="submit" class="btn primary">Save need</button>${cancelBtn}</div>
      ${err("need")}
    </form>` : filled ? html`
    <dl class="need">${NEED_FIELDS.filter(([k]) => need[k]).map(([k, label]) => html`<dt>${label}</dt><dd>${need[k]}</dd>`)}</dl>`
    : html`<p class="meta">Not written yet.</p>`}
  </div>`;
}

// ----- next actions and tasks -----
function actionsBlock(o, ctx) {
  const today = ctx.today;
  return html`
  <div class="block stack">
    <div><button type="button" class="btn small" data-act="suggest" data-id="${o.id}">Suggest next actions</button></div>
    ${err("suggest")}
    ${o.suggestions.length ? html`<div class="stack">${o.suggestions.map((s) => html`
      <div class="suggestion">${icon("bulb", 20)}<div style="flex:1;min-width:0">
        <b>${s.title}</b>
        <p class="meta-3">Manager agent, from the open gaps</p>
        ${s.reason ? html`<p class="meta">${s.reason}</p>` : ""}
        ${s.question ? html`<p class="meta">Question to ask: ${s.question}</p>` : ""}
        ${isOpen(`task:${s.id}`) ? html`
        <form class="panel-form" style="margin-top:10px" data-act="taskForm" data-on="submit input" data-id="${s.id}">
          <label class="field">Task<input name="title" value="${s.title}" required></label>
          <div class="form-grid">
            <label class="field">Whose side<select name="side"><option>CGS</option><option>Client</option></select></label>
            <label class="field">Who does it<input name="assignee" value="${ctx.me}" required></label>
            <label class="field">Due<input type="date" name="due" value="${addDays(today, 7)}" min="${today}" required></label>
          </div>
          <div class="actions"><button type="submit" class="btn primary">Agree the task</button>${cancelBtn}<span class="missing" data-missing></span></div>
          ${err(`task:${s.id}`)}
        </form>` : html`<div class="actions" style="margin-top:10px">${openBtn(`task:${s.id}`, "Make it a task…", "btn small", `Make it a task: ${s.title}`)}</div>`}
      </div></div>`)}</div>` : ""}
    ${o.tasks.length ? html`<ul class="tasks">${o.tasks.map((t) => {
      const late = t.status !== "done" && daysBetween(today, t.due) < 0;
      return html`<li><label class="check"><input type="checkbox" data-act="toggleTask" data-on="change" data-id="${t.id}" ${t.status === "done" ? "checked" : ""}>
        <span><span class="${t.status === "done" ? "done-text" : ""}">${t.title}</span>
        <span class="meta-3" style="display:block">${t.side} side, ${t.assignee}. <span class="${late ? "bad-text" : ""}">${late ? "Overdue since" : "Due"} ${day(t.due, today)}</span></span></span></label></li>`;
    })}</ul>` : html`<p class="meta">No agreed tasks yet. Turn a suggested next action into a task.</p>`}
  </div>`;
}

// ----- Step 7: the qualification pack -----
function packBlock(o, ctx) {
  const a = o.assessment;
  const ready = a && a.readiness === "Ready";
  const pendingProps = o.proposals.some((p) => p.status === "pending");
  return html`
  <div class="block stack">
    <h3 class="sub-title" style="margin:0">Send for approval</h3>
    ${ready ? html`<p class="meta">The rules say Ready. The pack freezes today's scores, conditions, revenue and need for the approver.</p>`
      : html`<p class="meta">The rules say ${a ? a.readiness : "not ready"}. You can still ask for an exception: the approver sees your reason, and readiness stays as the rules say.</p>`}
    ${pendingProps ? html`<div class="notice warn">${icon("alert")}<span>Evidence proposals are still waiting for review. Decide on them first, or they will not count.</span></div>` : ""}
    ${isOpen("pack") ? html`
    <form class="panel-form" data-act="packForm" data-on="submit input" data-id="${o.id}" data-revision="${o.revision}" data-ready="${ready ? "1" : ""}">
      ${ready ? "" : html`<label class="field">Why approve without Ready<textarea name="exception" rows="3" required></textarea></label>`}
      <div class="actions"><button type="submit" class="btn primary" ${ready ? "" : "disabled"}>${ready ? "Send the pack" : "Send with an exception"}</button>${cancelBtn}
        <span class="missing" data-missing>${ready ? "" : "Give the reason for the exception."}</span></div>
      ${err("pack")}
    </form>` : html`<div>${openBtn("pack", ready ? "Send for approval…" : "Request an exception…", ready ? "btn primary" : "btn")}</div>`}
  </div>`;
}

// ----- Step 8 -----
// part "card": the quick decision for an approver (first screen); part "pack": the pack and every other decision.
function approvalBlock(o, ctx, part) {
  const pk = o.packs.filter((p) => p.status === "open").slice(-1)[0];
  if (!pk) return part === "card" ? html`<div class="block"><p class="meta">No open pack. The owner can send one again from In conversation.</p></div>` : "";
  const s = pk.snapshot || {};
  const owner = o.owner === ctx.me;
  const roles = owner ? ["Lead owner"] : ["Another consultant", "Director of Operations"];
  const roleSelect = html`<label class="field">You act as<select name="role">${roles.map((r) => html`<option>${r}</option>`)}</select></label>`;
  const decide = (kind) => isOpen(`decide:${kind}`);
  const a = o.assessment || {};
  const quick = !owner && !pk.staleReason && !pk.handoverTo && !String(ui.open || "").startsWith("decide:");
  if (part === "card") return quick ? html`
  <div class="block">
    <div class="approval-card">
      <b>${o.owner} asks for your approval</b>
      <p class="meta">Sent ${day(pk.submittedOn, ctx.today)}. Qualification ${a.ql === a.qu ? a.ql : `${a.ql} to ${a.qu}`}, priority ${a.priority}${pk.exception ? ", with an exception" : ""}. Approving makes it a Qualified opportunity.</p>
      ${s.need && s.need.problem ? html`<p class="problem-box"><b>Problem:</b> ${s.need.problem}</p>` : ""}
      <form class="panel-form" data-act="quickDecide" data-on="submit input" data-id="${pk.id}">
        <div class="form-grid">${roleSelect}</div>
        <label class="field">Your comment <span class="hint">Needed to send it back: what evidence is missing.</span><textarea name="comment" rows="3"></textarea></label>
        <div class="actions">
          <button type="submit" class="btn primary" name="decision" value="approve">Approve</button>
          <button type="submit" class="btn" name="decision" value="return" data-needs-comment disabled>Send back with comment</button>
          <span class="meta-3">Other decisions are in the pack below.</span>
        </div>
        ${err("decide")}
      </form>
    </div>
  </div>` : "";
  return html`
  ${fold("pack", "The qualification pack and other decisions", html`
  <div class="block stack">
    <p class="meta-3">Rules ${pk.ruleVersion}.</p>
    ${pk.exception ? html`<div class="notice warn">${icon("alert")}<span><b>Exception requested:</b> ${pk.exception}</span></div>` : ""}
    ${pk.staleReason ? html`<div class="notice bad">${icon("alert")}<span><b>Out of date:</b> ${pk.staleReason}. It cannot be approved; ${owner ? "resubmit it" : "send it back so the owner can resubmit"}.</span></div>` : ""}
    ${pk.handoverTo ? html`<div class="notice info">${icon("clock")}<span>Approved by ${pk.handoverBy} with a handover to ${pk.handoverTo}. ${o.owner} stays responsible until ${pk.handoverTo} accepts.</span></div>` : ""}
    <div class="pack-grid">
      <div><p class="meta-3">Scores in the pack</p><ul class="kv">${DIMS.map((k) => html`<li><span>${rules.labels[k]}</span><span>${s.scores && s.scores[k] !== null && s.scores[k] !== undefined ? s.scores[k] : "Pending"}</span></li>`)}</ul></div>
      <div><p class="meta-3">Conditions in the pack</p><ul class="kv">${Object.entries(rules.conditions).map(([k, l]) => html`<li><span>${l}</span><span>${condText(s.conditions && s.conditions[k])}</span></li>`)}</ul></div>
    </div>
    <p class="meta">Revenue in the pack: ${revenueText(s.revenue)}.${s.need && s.need.problem ? ` Problem: ${s.need.problem}` : ""}</p>
    ${pk.handoverTo === ctx.me ? html`
      <div class="actions">
        <button type="button" class="btn primary" data-act="handover" data-accept="1" data-id="${pk.id}">Accept the handover</button>
        ${openBtn("decide:decline", "Decline…", "btn ghost")}
      </div>
      ${decide("decline") ? html`<form class="panel-form" data-act="declineForm" data-on="submit input" data-id="${pk.id}">
        <label class="field">Why you decline<textarea name="reason" rows="2" required></textarea></label>
        <div class="actions"><button type="submit" class="btn primary" disabled>Decline the handover</button>${cancelBtn}<span class="missing" data-missing>Give a reason.</span></div>
      </form>` : ""}
      ${err("handover")}` : ""}
    ${owner && pk.staleReason ? html`<div><button type="button" class="btn" data-act="resubmit" data-id="${o.id}" data-revision="${o.revision}">Resubmit the updated pack</button>${err("pack")}</div>` : ""}
    ${decide("approve") ? html`
    <form class="panel-form" data-act="decideForm" data-on="submit" data-id="${pk.id}" data-decision="approve">
      <h3 class="sub-title">Approve as Qualification</h3>
      <div class="form-grid">${roleSelect}
        <label class="field">Owner from now on<select name="new_owner">${ctx.users.map((u) => html`<option ${u === o.owner ? "selected" : ""}>${u}</option>`)}</select></label></div>
      <p class="meta-3">If you choose someone else, the approval waits until they accept the handover.</p>
      <div class="actions"><button type="submit" class="btn primary">Approve</button>${cancelBtn}</div>
      ${err("decide")}
    </form>` : ""}
    ${decide("return") ? html`
    <form class="panel-form" data-act="decideForm" data-on="submit input" data-id="${pk.id}" data-decision="return" data-stale="${pk.staleReason ? "1" : ""}">
      <h3 class="sub-title">Send back to the owner</h3>
      <div class="form-grid">${roleSelect}<label class="field">New due date<input type="date" name="due" value="${addDays(ctx.today, 7)}" min="${ctx.today}"></label></div>
      <label class="field">What evidence is missing<textarea name="questions" rows="3" ${pk.staleReason ? "" : "required"}></textarea></label>
      <div class="actions"><button type="submit" class="btn primary" ${pk.staleReason ? "" : "disabled"}>Send back</button>${cancelBtn}<span class="missing" data-missing>${pk.staleReason ? "" : "Say what is missing."}</span></div>
      ${err("decide")}
    </form>` : ""}
    ${decide("nurture") ? html`
    <form class="panel-form" data-act="decideForm" data-on="submit" data-id="${pk.id}" data-decision="nurture">
      <h3 class="sub-title">Move to nurture</h3>
      <div class="form-grid">${roleSelect}<label class="field">Review on<input type="date" name="review_on" value="${addDays(ctx.today, 60)}" min="${ctx.today}"></label></div>
      <div class="actions"><button type="submit" class="btn primary">Move to nurture</button>${cancelBtn}</div>
      ${err("decide")}
    </form>` : ""}
    ${decide("close") ? html`
    <form class="panel-form" data-act="decideForm" data-on="submit input change" data-id="${pk.id}" data-decision="close">
      <h3 class="sub-title">Close the opportunity</h3>
      <div class="form-grid">${roleSelect}<label class="field">Reason<select name="reason" required><option value="">Choose a reason</option>${CLOSE_REASONS.map((r) => html`<option>${r}</option>`)}</select></label></div>
      <div class="actions"><button type="submit" class="btn primary" disabled>Close</button>${cancelBtn}<span class="missing" data-missing>Choose a reason.</span></div>
      ${err("decide")}
    </form>` : ""}
    ${!String(ui.open || "").startsWith("decide:") && pk.handoverTo !== ctx.me ? html`
    <div class="actions">
      <button type="button" class="btn primary" data-act="open" data-open="decide:approve" ${pk.staleReason || pk.handoverTo ? "disabled" : ""}>Approve…</button>
      ${openBtn("decide:return", "Send back…", "btn")}
      ${openBtn("decide:nurture", "Move to nurture…", "btn ghost")}
      ${openBtn("decide:close", "Close…", "btn ghost")}
      ${pk.staleReason ? html`<span class="missing">An out-of-date pack cannot be approved.</span>`
        : pk.handoverTo ? html`<span class="missing">Already approved; waiting for ${pk.handoverTo} to accept the handover.</span>` : ""}
    </div>` : ""}
  </div>`, { open: !quick, note: `Sent by ${pk.submittedBy}, ${day(pk.submittedOn, ctx.today)}` })}`;
}

// ----- history -----
function historyBlock(o, ctx) {
  const decided = o.proposals.filter((p) => p.status !== "pending");
  return html`
  <div class="block stack">
    <details><summary class="sub-title">Confirmed evidence (${o.evidence.length})</summary>
      ${o.evidence.length ? html`<ul class="history">${[...o.evidence].reverse().map((e) => html`<li>
        <b>${keyLabel(e.kind, e.key) || e.key}: ${valueText(e.kind, e.key, e.value)}</b>
        ${e.quote ? html`<span class="quote">"${e.quote}"</span>` : ""}
        <span class="meta-3">${e.how} by ${e.by}, ${day(e.on, ctx.today)}${e.reason && e.reason !== e.quote ? `. ${e.reason}` : ""}</span></li>`)}</ul>` : html`<p class="meta">None yet.</p>`}
    </details>
    ${decided.length ? html`<details><summary class="sub-title">Decided proposals (${decided.length})</summary>
      <ul class="history">${decided.map((p) => html`<li><b>${keyLabel(p.kind, p.key)}: ${valueText(p.kind, p.key, p.value)}</b><span class="meta-3">${p.status}${p.reason ? `: ${p.reason}` : ""}</span></li>`)}</ul>
    </details>` : ""}
    <details><summary class="sub-title">Notes (${o.notes.length})</summary>
      ${o.notes.length ? html`<ul class="history">${[...o.notes].reverse().map((n) => html`<li><b>${n.type}, ${day(n.on, ctx.today)}</b><span class="email-plain">${n.text}</span></li>`)}</ul>` : html`<p class="meta">No notes yet.</p>`}
    </details>
    ${o.decisions.length ? html`<details ${o.stage === "qualification" || o.stage === "closed" ? "open" : ""}><summary class="sub-title">Decisions (${o.decisions.length})</summary>
      <ul class="history">${o.decisions.map((x) => html`<li><b>Step ${x.step}: ${x.decision}</b><span class="meta-3">${x.by}${x.role ? ` (${x.role})` : ""}, ${day(x.on, ctx.today)}${x.note ? `. ${x.note}` : ""}</span></li>`)}</ul>
    </details>` : ""}
    <details><summary class="sub-title">Activity (${o.activity.length})</summary>
      <ul class="history">${[...o.activity].reverse().slice(0, 30).map((a) => html`<li><span>${a.text}</span><span class="meta-3">${day(a.on, ctx.today)}</span></li>`)}</ul>
    </details>
  </div>`;
}

// ---------- actions ----------
async function busy(button, fn) {
  if (button) { button.classList.add("busy"); button.disabled = true; }
  try { return await fn(); } finally { if (button && button.isConnected) { button.classList.remove("busy"); button.disabled = false; } }
}

function missing(form, text) {
  form.querySelectorAll("[type=submit]").forEach((b) => { b.disabled = !!text; });
  const line = form.querySelector("[data-missing]");
  if (line) line.textContent = text || "";
}

function message(err) {
  return err instanceof ApiError && err.code === "revision_conflict"
    ? "Someone changed this opportunity while you were looking. The page now shows the latest version; check it and try again."
    : err.message;
}

// Run a write; on success close the open form and draw again, on failure show the error where it belongs.
async function write(where, button, ctx, fn, after) {
  try {
    const res = await busy(button, fn);
    ui.error = null;
    ui.open = null;
    if (after) after(res);
  } catch (e) {
    ui.error = { where, message: message(e) };
  }
  ctx.refresh();
}

export const actions = {
  crmResolve: resolveCrm,
  tab(t, e, ctx) { resetUi(); ctx.go("qualification", { tab: t.dataset.tab, team: ctx.route.params.team }); },
  tabkeys(t, e, ctx) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    const tabs = [...t.querySelectorAll("[role=tab]")];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const n = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : e.key === "Home" ? 0 : tabs.length - 1;
    ctx.focusAfter = `#${tabs[n].id}`;
    resetUi();
    ctx.go("qualification", { tab: tabs[n].dataset.tab, team: ctx.route.params.team });
  },
  fold(t) {  // the click comes before the browser opens or closes the section
    ui.folds.set(t.dataset.fold, !t.parentElement.open);
  },
  open(t, e, ctx) {
    ui.open = t.dataset.open || null;
    ui.error = null;
    ctx.focusAfter = ui.open ? ".panel-form select, .panel-form textarea, .panel-form input" : null;
    ctx.refresh();
  },

  // proposals
  proposal(t, e, ctx) {
    write(`prop:${t.dataset.id}`, t, ctx, () => ctx.api.post(`/proposals/${enc(t.dataset.id)}`, { action: "accept" }),
      (res) => { ui.notice = [{ text: `Accepted. ${res.change}.`, cls: "good" }]; });
  },
  proposalForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give a reason.");
    if (!reason) return;
    const body = { action: form.dataset.do, reason };
    if (form.dataset.do === "edit") body.value = parseValue(form.dataset.kind, form.elements.value.value);
    write(`prop:${form.dataset.id}`, form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/proposals/${enc(form.dataset.id)}`, body),
      (res) => { ui.notice = [{ text: res.status === "rejected" ? "Rejected. Nothing changed." : `${res.status === "edited" ? "Your version was accepted" : "Accepted"}. ${res.change}.`, cls: "good" }]; });
  },

  // notes
  noteForm(form, e, ctx) {
    const f = form.elements;
    if (e.type !== "submit") return missing(form, f.text.value.trim() ? "" : "Write the notes.");
    if (!f.text.value.trim()) return;
    write("note", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(form.dataset.id)}/notes`,
      { type: f.type.value, on: f.on.value, text: f.text.value.trim(), analyze: f.analyze.checked }), (res) => {
      const n = [];
      if (f.analyze.checked) n.push({ text: `Notes saved. The evidence agent proposed ${plural(res.proposals, "item")} for you to review.`, cls: "good" });
      else n.push({ text: "Notes saved.", cls: "good" });
      if (res.droppedForInvalidQuote) n.push({ text: `${plural(res.droppedForInvalidQuote, "proposal")} dropped because the quote is not in the notes.`, cls: "warn" });
      if ((res.handoffs || []).length) n.push({ text: `The research agent was asked to find: ${res.handoffs.join(", ")}.`, cls: "" });
      ui.notice = n;
    });
  },

  // manual review: score, condition, blocker
  reviewForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give the evidence or a reason.");
    if (!reason) return;
    const kind = form.dataset.kind;
    const body = { kind, key: form.dataset.key || "", reason, revision: Number(form.dataset.revision) };
    if (kind !== "blocker_clear") body.value = parseValue(kind, form.elements.value.value);
    if (kind === "cond" && form.elements.blocker && form.elements.blocker.value) body.blocker = form.elements.blocker.value;
    write("review", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(form.dataset.id)}/review`, body),
      (res) => { ui.notice = [{ text: `Saved. ${res.change}.`, cls: "good" }]; });
  },
  revenueForm(form, e, ctx) {
    const f = form.elements;
    const clear = f.clear.checked;
    const lo = Number(f.low.value), hi = Number(f.high.value);
    const why = !f.reason.value.trim() ? "Give a reason." : clear ? "" : !(lo > 0) || !(hi >= lo) ? "Give a low value above 0 and a high value at least as large." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    write("review", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(form.dataset.id)}/review`,
      { kind: "revenue", value: clear ? null : { low: lo, high: hi }, reason: f.reason.value.trim(), revision: Number(form.dataset.revision) }),
      () => { ui.notice = [{ text: `Saved. CGS revenue estimate: ${revenueText(clear ? null : { low: lo, high: hi })}.`, cls: "good" }]; });
  },
  needForm(form, e, ctx) {
    const body = Object.fromEntries(NEED_FIELDS.map(([k]) => [k, form.elements[k].value]));
    write("need", form.querySelector("[type=submit]"), ctx, () => ctx.api.patch(`/opportunities/${enc(form.dataset.id)}/need`, body));
  },

  // next actions and tasks
  suggest(t, e, ctx) {
    write("suggest", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/suggestions`),
      (res) => { ui.notice = [{ text: res.suggestions ? `The manager agent suggested ${plural(res.suggestions, "next action")}.` : "No suggestions: nothing is missing that an action could close.", cls: res.suggestions ? "good" : "" }]; });
  },
  taskForm(form, e, ctx) {
    const f = form.elements;
    if (e.type !== "submit") return missing(form, f.title.value.trim() && f.assignee.value.trim() ? "" : "Name the task and who does it.");
    write(`task:${form.dataset.id}`, form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/suggestions/${enc(form.dataset.id)}/task`,
      { title: f.title.value.trim(), side: f.side.value, assignee: f.assignee.value.trim(), due: f.due.value }));
  },
  toggleTask(t, e, ctx) {
    write("task", null, ctx, () => ctx.api.post(`/tasks/${enc(t.dataset.id)}/toggle`));
  },

  // Step 7
  packForm(form, e, ctx) {
    const ready = form.dataset.ready === "1";
    const exc = ready ? "" : form.elements.exception.value.trim();
    if (e.type !== "submit") return missing(form, ready || exc ? "" : "Give the reason for the exception.");
    if (!ready && !exc) return;
    const id = form.dataset.id;
    write("pack", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(id)}/packs`,
      { exception: exc || null, revision: Number(form.dataset.revision) }), () => {
      ctx.toast("The pack was sent for approval.", { link: { href: href("qualification", { tab: "approval", id }), label: "See it" } });
    });
  },
  resubmit(t, e, ctx) {
    write("pack", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/packs`, { revision: Number(t.dataset.revision) }),
      () => { ui.notice = [{ text: "The updated pack was resubmitted.", cls: "good" }]; });
  },

  // Step 8
  decideForm(form, e, ctx) {
    const f = form.elements;
    const decision = form.dataset.decision;
    const why = decision === "return" && form.dataset.stale !== "1" && !f.questions.value.trim() ? "Say what is missing."
      : decision === "close" && !f.reason.value ? "Choose a reason." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    const body = { decision, role: f.role.value };
    if (decision === "approve") body.new_owner = f.new_owner.value;
    if (decision === "return") { body.questions = f.questions.value.trim() || null; body.due = f.due.value || null; }
    if (decision === "nurture") body.review_on = f.review_on.value || null;
    if (decision === "close") body.reason = f.reason.value;
    write("decide", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/packs/${enc(form.dataset.id)}/decision`, body), (res) => {
      const text = {
        approved: "Approved. The opportunity is now Qualified.",
        handover_pending: `Approved. It waits for ${res.handoverTo} to accept the handover.`,
        return: "Sent back to the owner with your questions.",
        nurture: "Moved to nurture.",
        close: "Closed.",
      }[res.status] || "Saved.";
      ctx.toast(text);
      if (res.status !== "handover_pending") resetUi();
      else ui.notice = [{ text, cls: "good" }];
    });
  },
  acceptAll(t, e, ctx) {
    const ids = t.dataset.ids.split(",");
    write("acceptAll", t, ctx, async () => {
      for (const id of ids) await ctx.api.post(`/proposals/${enc(id)}`, { action: "accept" });
    }, () => { ui.notice = [{ text: `Accepted ${plural(ids.length, "proposal")}.`, cls: "good" }]; });
  },
  promiseForm(form, e, ctx) {
    const why = form.elements.why.value.trim();
    if (e.type !== "submit") {
      form.querySelectorAll("[type=submit]").forEach((b) => { b.disabled = !why; });
      form.querySelector("[data-missing]").textContent = why ? "" : "Say why.";
      return;
    }
    if (!why) return;
    const status = (e.submitter && e.submitter.value) || "done";
    write(`promise:${form.dataset.cid}`, e.submitter, ctx, () => ctx.api.post(`/opportunities/${enc(form.dataset.id)}/commitments/${enc(form.dataset.cid)}`, { status, why }),
      () => { ui.notice = [{ text: status === "done" ? "Promise marked done." : "Promise closed: it no longer applies.", cls: "good" }]; });
  },
  quickDecide(form, e, ctx) {
    const comment = form.elements.comment.value.trim();
    if (e.type !== "submit") {
      form.querySelector("[data-needs-comment]").disabled = !comment;
      return;
    }
    const decision = (e.submitter && e.submitter.value) || "approve";
    if (decision === "return" && !comment) return;
    const body = { decision, role: form.elements.role.value };
    if (decision === "return") body.questions = comment;
    write("decide", e.submitter, ctx, () => ctx.api.post(`/packs/${enc(form.dataset.id)}/decision`, body), (res) => {
      ctx.toast(res.status === "approved" ? "Approved. The opportunity is now Qualified." : "Sent back to the owner with your comment.");
      resetUi();
    });
  },
  handover(t, e, ctx) {
    write("handover", t, ctx, () => ctx.api.post(`/packs/${enc(t.dataset.id)}/handover`, { accept: true }),
      () => { ctx.toast("You accepted the handover. The opportunity is now Qualified and yours."); resetUi(); });
  },
  declineForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give a reason.");
    if (!reason) return;
    write("handover", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/packs/${enc(form.dataset.id)}/handover`, { accept: false, reason }),
      () => { ui.notice = [{ text: "You declined the handover. The approver decides again.", cls: "" }]; });
  },
};
