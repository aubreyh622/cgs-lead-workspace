// Qualification (design/redesign/Companies.dc.html, view "qualification"): Steps 6-8. From the client's first reply
// to an approved pack. The Evidence agent proposes evidence from replies and call notes; a person confirms each piece.
//
// Stages: Active (one reply handled on one screen), Awaiting approval (one pack reviewed on one screen), Qualified and
// Parked (tables with a side panel). The rules engine decides Ready; this page never shows a 0-100 score or a revenue
// amount (redesign decisions D3 and D5).
import { html, icon, day, addDays, daysBetween, href, plural, initials } from "../lib.js";
import { enc, ApiError } from "../api.js";
import { STAGE_LABELS, CLOSE_REASONS, NOTE_TYPES, NEED_FIELDS } from "../options.js";
import { emptyState, inlineError, crmLinks, crmConflicts, resolveCrm, drawer, panelActions } from "../shell.js";

export const title = "Qualification";

// The stage cards, as on Companies: who has the move, and what Insightly shows (config/crm.json: a pack waiting for
// approval stays Active; approval converts the lead to an opportunity; parked leads are Nurture).
const TABS = [
  ["active", "Active", "you", "In conversation with the client"],
  ["approval", "Awaiting approval", "you", "Insightly: Active"],
  ["qualified", "Qualified", "done", "Insightly: Opportunity"],
  ["parked", "Parked", "none", "Insightly: Nurture"],
];
const STAGE_OF = { active: "active", approval: "approval", qualified: "qualification", parked: "nurture" };
// Tab names before the redesign. Closed leads are under Companies, Set aside, now (D2).
const TAB_ALIAS = { qualification: "qualified", nurture: "parked" };
const DIMS = ["B", "A", "N", "T", "H", "E"];
// What the reply means: the Evidence agent's label (api/services.py). There is no endpoint to change it (G16).
const MEANS = {
  meet: ["Wants to meet", "good"], need: ["Talks about a need", "info"], courtesy: ["Polite, no ask", ""],
  defer: ["Not now", "warn"], decline: ["Not interested", "warn"], pending: ["Not read yet", ""],
};
const NOT_QUITE = ["Too weak", "Wrong person", "Out of date"];
const NOTE = {
  active: "The client replied. The Evidence agent reads each reply and call note and proposes evidence. Nothing counts until a person confirms it.",
  approval: "The owner sent the pack. A colleague approves it, sends it back, or asks for a new one when it is out of date.",
  qualified: "Approved packs. In Insightly each one is now an opportunity in the Qualification stage, managed there from here on.",
  parked: "The client said not now. Each lead comes back on its review date, with a reconnect draft if there is news.",
};
const EMPTY = {
  active: ["No one in conversation", "A lead arrives here when the client replies.", { href: href("outreach", { tab: "wait" }), label: "Open Outreach" }],
  approval: ["Nothing waiting for approval", "A pack appears here when its owner sends it for approval."],
  qualified: ["Nothing qualified yet", "Approved packs move here."],
  parked: ["Nothing is parked", "Leads you park wait here until their review date."],
};

let rules = null; // GET /api/rules: labels, anchors, floors and Ready thresholds in use (an admin may change them)
// What the person has open. Kept while they stay on one lead; the reply they are writing is kept per lead.
const ui = { key: null, open: null, error: null, notice: null, folds: new Map(), reply: new Map(), asks: new Map() };

function resetUi(key = null) {
  ui.key = key; ui.open = null; ui.error = null; ui.notice = null;
}

// Items waiting for this person: packs others sent for approval, and their own leads the rules call Ready.
export function waitingFor(me, opps) {
  return opps.filter((o) => (o.stage === "approval" && o.owner !== me)
    || (o.stage === "active" && o.owner === me && o.assessment && o.assessment.readiness === "Ready"));
}

// ---------- what stands between a lead and Ready, in words (D3) ----------
// Conditions not met, BANTE dimensions below their minimum or not confirmed, and, when only the strength of the
// evidence is short (confirmed Q or coverage), one sentence naming the weakest dimensions. Never a number.
export function readiness(o, R) {
  const a = o.assessment || { gaps: [], readiness: "More evidence needed" };
  const keys = Object.keys(R.conditions);
  const met = keys.filter((k) => o.conditions && o.conditions[k] === true);
  const missing = keys.filter((k) => !(o.conditions && o.conditions[k] === true));
  const dims = (a.gaps || []).filter((g) => R.floors[g.key] !== undefined)
    .map((g) => ({ key: g.key, text: `${R.labels[g.key]}: ${/below/.test(g.label) ? "not strong enough yet" : "not confirmed yet"}`, ask: g.question }));
  const ready = a.readiness === "Ready";
  const blocker = !!o.blocker;
  const weak = !ready && !blocker && !missing.length && !dims.length;
  const weakest = DIMS.filter((k) => !o.scores || o.scores[k] === null || o.scores[k] === undefined || o.scores[k] < 5)
    .sort((x, y) => score(o, x) - score(o, y) || (R.weights[y] || 0) - (R.weights[x] || 0)).slice(0, 3).map((k) => R.labels[k].toLowerCase());
  const weakText = `The evidence is not strong enough yet for approval. Confirm more about ${list(weakest, "or")}.`;
  const count = missing.length + dims.length + (weak ? 1 : 0) + (blocker ? 1 : 0);
  const onlyConds = !dims.length && !weak && !blocker;
  const head = `${met.length} of ${keys.length} conditions met.`
    + (missing.length ? ` Missing: ${list(missing.map((k) => R.conditions[k].toLowerCase()), "and")}.`
      : ready ? " Ready for approval." : " The evidence is not strong enough yet for approval.");
  return {
    ready, met: met.length, total: keys.length, missing, dims, blocker, weak, weakText: weak ? weakText : "", count, head,
    asks: [...missing.map((k) => ({ key: k, ask: ((a.gaps || []).find((g) => g.key === `condition:${k}`) || {}).question })), ...dims],
    excLabel: `Ask for approval with ${count} ${onlyConds ? (count === 1 ? "condition" : "conditions") : count === 1 ? "thing" : "things"} missing`,
  };
}
const score = (o, k) => (o.scores && o.scores[k] !== null && o.scores[k] !== undefined ? o.scores[k] : -1);
function list(items, joiner) {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} ${joiner} ${items[items.length - 1]}`;
}

// ---------- words for values ----------
// A dimension's value in the rules' own words, never the number (redesign rule 4, G26). The Ready rules still use the
// numbers; the page says what they mean.
function scoreText(dim, v) {
  if (v === null || v === undefined) return "Pending";
  if (v === 2.5) return "Not confirmed yet, a neutral start";
  return rules.anchors[dim][Math.floor(v)];
}
const condText = (v) => (v === true ? "Met" : v === false ? "Not met" : "Pending");
const condTag = (v) => (v === true ? "good" : "");

function valueText(kind, key, v) {
  if (kind === "score") return scoreText(key, v);
  if (kind === "cond") return condText(v);
  if (kind === "blocker") return v ? rules.reasons[v] || v : "Cleared";
  if (kind === "revenue") return v ? "estimate recorded" : "estimate cleared";  // the amount is never shown (D5)
  return String(v);
}
const keyLabel = (kind, key) => (kind === "score" ? rules.labels[key] : kind === "cond" ? rules.conditions[key] : kind === "revenue" ? "CGS revenue" : "Blocker");

// The revenue estimate in words: recorded, when it last changed. Never the amount (D5).
function revenueWords(o) {
  const last = [...(o.evidence || [])].reverse().find((e) => e.kind === "revenue");
  if (!o.revenue) return { head: "Not estimated yet", sub: last ? `Cleared ${day(last.on)}` : "Optional for Ready" };
  return { head: last ? `Changed ${day(last.on)}` : "Recorded", sub: "The amount is not shown here" };
}

// ---------- data ----------
export async function load(ctx, params) {
  if (params.tab === "closed") {  // closed leads moved to Companies, Set aside
    ctx.go("companies", { tab: "aside", id: params.id });
    return { moved: true };
  }
  const team = params.team === "1";
  const [all, r, inbox] = await Promise.all([
    ctx.api.get(`/opportunities?${Object.values(STAGE_OF).map((s) => `stage=${s}`).join("&")}`),
    ctx.api.get("/rules"),
    ctx.api.get("/inbox").catch(() => []),
  ]);
  rules = r;
  // Approvers are usually not the owner, so Awaiting approval always shows the whole team.
  const visible = (o) => team || o.stage === "approval" || o.owner === ctx.me;
  const lists = Object.fromEntries(TABS.map(([t]) => [t, all.filter((o) => o.stage === STAGE_OF[t] && visible(o))]));
  let tab = TAB_ALIAS[params.tab] || params.tab;
  if (!TABS.some(([t]) => t === tab)) tab = null;
  if (params.id && (!tab || !lists[tab].some((o) => o.id === params.id))) {
    const o = all.find((x) => x.id === params.id);
    if (o) tab = TABS.find(([t]) => STAGE_OF[t] === o.stage)[0];
  }
  tab = tab || (lists.active.some((o) => o.owner === ctx.me) ? "active" : lists.approval.length ? "approval" : "active");
  // A lead of someone else's opened by its link: show the whole team so it is in the list.
  const teamNow = team || (params.id && !lists[tab].some((o) => o.id === params.id) && all.some((o) => o.id === params.id && o.stage === STAGE_OF[tab]));
  if (teamNow && !team) lists[tab] = all.filter((o) => o.stage === STAGE_OF[tab]);
  const review = tab === "active" || tab === "approval";
  let id = lists[tab].some((o) => o.id === params.id) ? params.id : undefined;
  if (review && !id && lists[tab].length) id = (lists[tab].find((o) => o.owner === ctx.me) || lists[tab][0]).id;
  if (tab === "approval" && !params.id && lists.approval.length) id = (lists.approval.find((o) => o.owner !== ctx.me) || lists.approval[0]).id;
  if (params.tab !== tab || params.id !== id || (teamNow && !team)) ctx.replace("qualification", { tab, id, team: teamNow ? "1" : undefined });

  const details = Object.fromEntries((await Promise.all(lists[tab].map((o) => detailOf(ctx, o.id)))).filter(Boolean).map((x) => [x.id, x]));
  const data = { tab, team: !!teamNow, lists, details, id, inbox };
  if (id) {
    if (ui.key !== id) resetUi(id);
    data.detail = details[id] || await ctx.api.get(`/opportunities/${enc(id)}`);
    data.memory = tab === "active" ? await ctx.api.get(`/opportunities/${enc(id)}/memory`).catch(() => null) : null;
  }
  return data;
}

// Each lead in full, kept for a minute so picking one draws at once; the full load checks it again.
const cache = new Map();
const TTL = 60 * 1000;
async function detailOf(ctx, id) {
  try {
    const x = await ctx.api.get(`/opportunities/${enc(id)}`);
    cache.set(id, { at: Date.now(), value: x });
    return x;
  } catch { return null; }
}

export function instant(ctx, params, last) {
  const tab = TAB_ALIAS[params.tab] || params.tab;
  if (!last || last.moved || tab !== last.tab || (params.team === "1") !== last.team) return null;
  if (!params.id) return tab === "active" || tab === "approval" ? null : { ...last, id: undefined, detail: null };
  const hit = cache.get(params.id);
  if (!hit || Date.now() - hit.at > TTL || !last.lists[tab].some((o) => o.id === params.id)) return null;
  if (ui.key !== params.id) resetUi(params.id);
  return { ...last, id: params.id, detail: hit.value, memory: null };
}

export function changed(early, d) {
  const pick = (x) => JSON.stringify([x.tab, x.id, x.detail, x.memory, (x.lists ? x.lists[x.tab] : []).map((o) => o.id)]);
  return pick(early) !== pick(d);
}

let keysOn = false;
export function after(root) {
  if (!keysOn) { root.addEventListener("keydown", queueKey); keysOn = true; }
}

// J and K move through the queue on Active and Awaiting approval.
function queueKey(e) {
  if (!/^#\/qualification\?(.*&)?tab=(active|approval)/.test(location.hash) || document.querySelector(".drawer")) return;
  if ((e.target && e.target.matches && e.target.matches("input, textarea, select")) || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = (e.key || "").toLowerCase();
  if (k !== "j" && k !== "k") return;
  const all = [...document.querySelectorAll(".queue .pick")];
  const i = all.findIndex((a) => a.getAttribute("aria-current") === "true");
  const n = all[(i + (k === "j" ? 1 : -1) + all.length) % all.length];
  if (n && all.length > 1) { e.preventDefault(); location.hash = n.getAttribute("href"); }
}

// ---------- view ----------
export function view(d, ctx) {
  if (d.moved) return html`<p class="meta">Closed leads are under Companies, Set aside.</p>`;
  const link = (tab, extra = {}) => href("qualification", { tab, team: d.team ? "1" : undefined, ...extra });
  const list = d.lists[d.tab];
  return html`
  <div class="page-head">
    <div>
      <h1>Qualification</h1>
      <p>Client replies, evidence for the six conditions, and the pack a colleague approves. Agents propose; a person confirms.</p>
    </div>
    <div class="head-side">
      <span class="legend"><span><span class="dot wait" aria-hidden="true"></span>Waiting on you</span>
        <span><span class="dot agent" aria-hidden="true"></span>An agent works on it</span><span><span class="dot done" aria-hidden="true"></span>Done</span></span>
      <div class="segmented" role="group" aria-label="Whose leads">
        <a href="${href("qualification", { tab: d.tab })}" aria-current="${d.team ? "false" : "true"}">Mine</a>
        <a href="${href("qualification", { tab: d.tab, team: "1" })}" aria-current="${d.team ? "true" : "false"}">Whole team</a>
      </div>
    </div>
  </div>
  <div class="stages" role="tablist" aria-label="Stages" data-act="tabkeys" data-on="keydown">
    ${TABS.map(([id, label, who, line]) => html`
      <button type="button" role="tab" class="stg" id="tab-${id}" aria-selected="${d.tab === id ? "true" : "false"}" aria-controls="panel"
        tabindex="${d.tab === id ? "0" : "-1"}" data-act="tab" data-tab="${id}">
        <span class="who ${who}" aria-hidden="true"></span>
        <span class="n">${d.lists[id].length}</span>
        <span class="${youCount(id, d, ctx) ? "label you-text" : "label"}">${label}</span>
        <span class="crm">${line}</span>
      </button>`)}
  </div>
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${!list.length ? html`<section class="card">${emptyState(...EMPTY[d.tab])}</section>`
      : d.tab === "active" ? activeView(d, ctx, link)
      : d.tab === "approval" ? approvalView(d, ctx, link)
      : tableView(d, ctx, link)}
  </div>
  ${(d.tab === "qualified" || d.tab === "parked") && d.detail ? panel(d, ctx, link) : ""}`;
}

// The tab count is orange when something there waits on this person.
function youCount(tab, d, ctx) {
  if (tab === "active") return d.lists.active.some((o) => o.owner === ctx.me);
  if (tab === "approval") return d.lists.approval.some((o) => o.owner !== ctx.me);
  return false;
}

// ----- queue on the left (Active, Awaiting approval) -----
function queue(d, ctx, link, label, sub) {
  return html`
  <nav class="card queue" aria-label="${label}">
    <div class="queue-head">${label}</div>
    ${d.lists[d.tab].map((o) => {
      const [text, you] = sub(o, d.details[o.id]);
      return html`<a class="pick" href="${link(d.tab, { id: o.id })}" ${o.id === d.id ? html`aria-current="true"` : ""}>
        <span class="avatar" aria-hidden="true">${initials(o.company)}</span>
        <span><b>${o.company}</b><span class="meta-3 ${you ? "you-text md" : ""}">${text}</span></span></a>`;
    })}
    <div class="queue-foot"><span class="keys">J</span><span class="keys">K</span>to move</div>
  </nav>`;
}

function headBlock(o, ctx, line, extra = "") {
  return html`
  <div class="block">
    <div class="row-flex" style="align-items:flex-start">
      <span class="avatar lg ink" aria-hidden="true">${initials(o.company.name)}</span>
      <div style="flex:1 1 220px;min-width:0">
        <h2 id="detail-h">${o.company.name}</h2>
        <p class="meta">${o.contact.name || "Contact to confirm"}${o.contact.title ? `, ${o.contact.title}` : ""}. Owner: ${o.owner || "not set"}.</p>
        ${line ? html`<p class="md" style="margin-top:4px">${line}</p>` : ""}
      </div>
      ${extra}
    </div>
    <div class="row-flex" style="margin-top:10px">${crmLinks(o.crm)}<a class="btn small" href="${href("record", { id: o.id })}">Open full record</a></div>
    ${o.crm && o.crm.conflicts && o.crm.conflicts.length ? html`<div style="margin-top:12px">${crmConflicts(o.id, o.crm)}</div>` : ""}
  </div>`;
}

function noticeBlock() {
  if (!ui.notice) return "";
  return html`<div class="block stack">${ui.notice.map((n) => html`<div class="notice ${n.cls || ""}" role="status">${icon(n.cls === "good" ? "check" : "alert")}<span>${n.text}</span></div>`)}</div>`;
}

function err(where) {
  return inlineError(ui.error && ui.error.where === where ? ui.error.message : "");
}

const isOpen = (name) => ui.open === name;
const openBtn = (name, label, cls = "btn small", aria = "") => html`<button type="button" class="${cls}" data-act="open" data-open="${name}" ${aria ? html`aria-label="${aria}"` : ""}>${label}</button>`;
const cancelBtn = html`<button type="button" class="btn ghost" data-act="open" data-open="">Cancel</button>`;
const first = (name) => (name ? name.split(" ")[0] : "the contact");

// =====================================================================================================
// Active: one reply handled on one screen. Middle: the reply, notes and the conversation so far. Right: what the
// reply means, the six conditions with the evidence to confirm, and the reply and pack.
// =====================================================================================================
function activeView(d, ctx, link) {
  const o = d.detail;
  return html`
  <div class="review three wide-side">
    ${queue(d, ctx, link, "Conversations", (s, det) => {
      if (s.owner !== ctx.me) return [`${s.owner || "No owner"}: ${s.next.action || "in conversation"}`, false];
      const n = det ? det.proposals.filter((p) => p.status === "pending").length : 0;
      const ready = s.assessment && s.assessment.readiness === "Ready";
      return [ready ? "Ready to send for approval" : n ? `New evidence, ${n} to confirm` : s.next.action || "Your conversation", true];
    })}
    ${o ? html`<section class="card detail mid" aria-labelledby="detail-h">${activeMain(o, d, ctx)}</section>
    <aside class="card side-col" aria-label="Handle the reply">${activeSide(o, ctx)}</aside>` : ""}
  </div>`;
}

function activeMain(o, d, ctx) {
  const rd = readiness(o, rules);
  return html`
  ${headBlock(o, ctx, rd.head, html`<span class="tag ${rd.ready ? "good" : ""}">${rd.ready ? "Ready" : "More evidence needed"}</span>`)}
  ${noticeBlock()}
  ${o.blocker ? blockerBlock(o) : ""}
  ${replyBlock(o, ctx)}
  ${agentLine(o, ctx)}
  <div class="block stack">
    ${isOpen("note") ? notesForm(o, ctx) : html`<div class="row-flex"><span class="meta" style="flex:1">Emails arrive from Gmail on their own when it is connected. Calls and meetings need notes.</span>
      ${openBtn("note", "Add call notes…", "btn")}</div>`}
  </div>
  ${earlierBlock(o, ctx)}
  <div class="block"><span class="lbl">Next step</span>
    <p>${o.next.action || "None set."}${o.next.due ? html` <span class="${daysBetween(ctx.today, o.next.due) < 0 ? "you-text md" : "meta"}">${daysBetween(ctx.today, o.next.due) < 0 ? "Overdue since" : "Due"} ${day(o.next.due, ctx.today)}</span>` : ""}</p></div>
  ${fold("scores", "Evidence, conditions and revenue by hand", html`${scoresBlock(o)}${conditionsBlock(o)}${revenueBlock(o)}`, { note: revenueWords(o).head === "Not estimated yet" ? "Revenue not estimated" : "" })}
  ${fold("need", "Client need", needBlock(o), { note: NEED_FIELDS.some(([k]) => (o.need || {})[k]) ? "" : "Not written yet" })}
  ${fold("tasks", "Next actions and tasks", actionsBlock(o, ctx), { note: taskNote(o, ctx) })}
  ${fold("memory", "What the manager agent remembers", memoryBlock(o, d.memory, ctx))}
  ${fold("history", "History: evidence, notes, decisions", historyBlock(o, ctx))}`;
}

// The client's latest reply. The phrases the Evidence agent quotes are marked; pointing at a condition on the right
// highlights its phrase (the quote is checked word for word against the notes, so an exact match finds it).
function latestReply(o) {
  const r = o.outreach || {};
  if (r.replyText) return { text: r.replyText, on: r.replyOn, label: `Latest reply from ${first(o.contact.name)}` };
  // No email reply on file: the newest note of the conversation (a call, a meeting) stands in for it.
  const n = [...o.notes].reverse()[0];
  return n ? { text: n.text, on: n.on, note: n.id, label: /reply|email/i.test(n.type) ? `Latest reply from ${first(o.contact.name)}` : `Latest from the conversation: ${n.type.toLowerCase()}` } : null;
}

function replyBlock(o, ctx) {
  const rep = latestReply(o);
  if (!rep) return html`<div class="block"><p class="meta">No reply text is recorded for this lead.</p></div>`;
  const marks = o.proposals.filter((p) => p.status === "pending" && p.quote && rep.text.includes(p.quote));
  const parts = [];
  let at = 0;
  const hits = marks.map((p) => ({ p, i: rep.text.indexOf(p.quote) })).sort((x, y) => x.i - y.i);
  for (const { p, i } of hits) {
    if (i < at) continue;
    parts.push(rep.text.slice(at, i));
    parts.push(html`<mark class="q" data-q="${p.id}">${p.quote}</mark>`);
    at = i + p.quote.length;
  }
  parts.push(rep.text.slice(at));
  return html`
  <div class="block">
    <div class="sb"><span class="lbl">${rep.label}</span><span class="meta-3">${rep.on ? day(rep.on, ctx.today) : ""}</span></div>
    <div class="reply-quote">${parts}</div>
  </div>`;
}

// What the Evidence agent is doing with the notes: reading one now, one not read yet, or what it said last.
function agentLine(o, ctx) {
  const unread = [...o.notes].reverse().find((n) => !n.analysis && !o.proposals.some((p) => p.noteId === n.id));
  const read = [...o.notes].reverse().find((n) => n.analysis && n.analysis.summary);
  if (unread && unread.reading) return html`<div class="block"><p class="meta"><span class="dot run" aria-hidden="true" style="display:inline-block;margin-right:8px"></span>The Evidence agent is reading the ${unread.type.toLowerCase()} of ${day(unread.on, ctx.today)}. What it proposes appears on the right.</p></div>`;
  if (unread) {
    return html`<div class="block row-flex"><span class="meta" style="flex:1">The ${unread.type.toLowerCase()} of ${day(unread.on, ctx.today)} has not been read for evidence yet.</span>
      <button type="button" class="btn small" data-act="readNote" data-id="${o.id}" data-note="${unread.id}">Ask the Evidence agent to read it</button>${err("readNote")}</div>`;
  }
  if (!read) return "";
  return html`<div class="block"><div class="why-box"><span class="lbl">What the Evidence agent said, after the ${read.type.toLowerCase()} of ${day(read.on, ctx.today)}</span><span>${read.analysis.summary}</span></div></div>`;
}

// Everything before the latest reply: call notes, earlier emails, plan steps done, the first email.
function earlierBlock(o, ctx) {
  const rep = latestReply(o);
  const rows = [];
  for (const n of [...o.notes].reverse()) {
    if (rep && (n.id === rep.note || n.text === rep.text)) continue;
    rows.push({ on: n.on, title: n.type, line: n.text.length > 160 ? `${n.text.slice(0, 160)}…` : n.text });
  }
  const pl = o.outreach && o.outreach.plan;
  for (const s of ((pl && pl.plan && pl.plan.steps) || []).filter((x) => x.status === "done")) rows.push({ on: s.doneOn, title: s.channel.replace(/_/g, " "), line: s.purpose });
  if (o.outreach && o.outreach.sentOn) rows.push({ on: o.outreach.sentOn, title: o.outreach.status === "followup" ? "Follow-up email" : "First email", line: o.outreach.subject || "" });
  rows.sort((x, y) => String(y.on).localeCompare(String(x.on)));
  if (!rows.length) return "";
  return html`<div class="block"><span class="lbl">Earlier in this conversation</span>
    <ul class="thread">${rows.slice(0, 8).map((x) => html`<li><span class="md">${x.title}</span><span class="meta">${x.line}</span><span class="meta-3">${x.on ? day(x.on, ctx.today) : ""}</span></li>`)}</ul></div>`;
}

function blockerBlock(o) {
  return html`<div class="block stack">
    <div class="notice warn">${icon("alert")}<span><b>Blocker: ${rules.reasons[o.blocker] || o.blocker}.</b> The rules say Not ready while it stands.</span></div>
    ${isOpen("blocker") ? html`
    <form class="panel-form" data-act="reviewForm" data-on="submit input" data-kind="blocker_clear" data-id="${o.id}" data-revision="${o.revision}">
      <label class="field">Why the blocker no longer applies<textarea name="reason" rows="2" required></textarea></label>
      <div class="actions"><button type="submit" class="btn primary" disabled>Clear the blocker</button>${cancelBtn}<span class="missing" data-missing>Give a reason.</span></div>
      ${err("review")}
    </form>` : html`<div>${openBtn("blocker", "Clear the blocker…")}</div>`}
  </div>`;
}

// ----- the right column -----
function activeSide(o, ctx) {
  const mine = o.owner === ctx.me;
  const rd = readiness(o, rules);
  const [means, tone] = MEANS[o.replyLabel] || MEANS.pending;
  return html`
  <div class="side-sec">
    <div class="sb"><h3>1. What the reply means</h3><span class="tag info">Agent's reading</span></div>
    <p><span class="tag ${tone}">${means}</span></p>
    ${mine ? html`
    ${isOpen("park") ? parkForm(o, ctx) : isOpen("close") ? closeForm(o) : html`<div class="row-flex" style="gap:6px">
      ${openBtn("park", "Not now: park it…", o.replyLabel === "defer" ? "btn primary small" : "btn small")}
      ${openBtn("close", "Not interested: set aside…", o.replyLabel === "decline" ? "btn primary small" : "btn ghost small")}</div>`}` : ""}
  </div>
  <div class="side-sec">
    <div class="sb"><h3>2. The six conditions</h3><span class="${rd.ready ? "good-text" : "meta-3"} md">${rd.met} of ${rd.total} confirmed${pendingConds(o) ? html`, <span class="you-text">${pendingConds(o)} your call</span>` : ""}</span></div>
    <ul class="conds">${Object.keys(rules.conditions).map((k) => condRow(o, k, mine, ctx))}</ul>
    ${rd.dims.length || rd.weak ? html`<ul class="gap-lines">${rd.dims.map((x) => html`<li>${x.text}</li>`)}${rd.weak ? html`<li>${rd.weakText}</li>` : ""}</ul>` : ""}
    ${otherProposals(o, mine)}
    ${rd.asks.length ? html`<div><button type="button" class="btn ghost small" data-act="copyQuestions" data-text="${rd.asks.filter((x) => x.ask).map((x) => `- ${x.ask}`).join("\n")}">Copy the questions for the next call</button></div>` : ""}
  </div>
  <div class="side-sec">${mine ? replyAndPack(o, ctx, rd) : html`<h3>3. The reply</h3>
    <p class="meta">${o.owner || "Nobody"} owns this conversation.${o.next.action ? ` Next: ${o.next.action}.` : ""} You can read along; the reply and the evidence are ${o.owner ? "theirs" : "the owner's"} to handle.</p>`}</div>`;
}

const pendingConds = (o) => o.proposals.filter((p) => p.status === "pending" && p.kind === "cond").length;

function condRow(o, k, mine, ctx) {
  const v = o.conditions[k];
  const p = o.proposals.find((x) => x.status === "pending" && x.kind === "cond" && x.key === k);
  const ev = [...o.evidence].reverse().find((e) => e.kind === "cond" && e.key === k && e.value === true);
  const note = p && o.notes.find((n) => n.id === p.noteId);
  const asks = ui.asks.get(o.id) || new Set();
  const [label, tone] = p ? ["Your call", "warn"] : v === true ? ["Confirmed", "good"] : v === false ? ["Not met", ""] : ["Missing", ""];
  const conflict = p && v !== null && v !== undefined && v !== p.value;
  return html`<li class="cond-row" ${p ? html`data-act="hl" data-on="mouseover mouseout focusin focusout" data-q="${p.id}"` : ""}>
    <span class="cond-name md">${rules.conditions[k]}</span><span class="tag ${tone}">${label}</span>
    ${p ? html`<span class="cond-proof">${p.quote ? html`“${p.quote}” ` : ""}${p.why ? html`<span class="meta">${p.why}</span>` : ""}
      <span class="meta-3" style="display:block">Proposes ${condText(p.value).toLowerCase()}. From ${note ? `the ${note.type.toLowerCase()} of ${day(note.on, ctx.today)}` : "the notes"}.</span></span>` : ""}
    ${v === true && !p && ev ? html`<span class="cond-proof meta-3">${ev.how} by ${ev.by}, ${day(ev.on, ctx.today)}</span>` : ""}
    ${p && mine ? html`<span class="cond-acts">
      ${conflict ? openBtn(`prop:${p.id}:accept`, "Confirm…", "btn primary small") : html`<button type="button" class="btn primary small" data-act="proposal" data-do="accept" data-id="${p.id}">Confirm</button>`}
      ${openBtn(`notquite:${p.id}`, "Not quite…", "btn small")}</span>
      ${isOpen(`notquite:${p.id}`) ? html`<span class="cond-acts meta-3">Why not?${NOT_QUITE.map((r) => html`<button type="button" class="btn small" data-act="notQuite" data-id="${p.id}" data-reason="${r}">${r}</button>`)}${cancelBtn}</span>` : ""}
      ${isOpen(`prop:${p.id}:accept`) ? proposalForm(p, "accept") : ""}
      ${err(`prop:${p.id}`)}` : ""}
    ${v !== true && !p && mine ? html`<span class="cond-acts">
      <button type="button" class="tick small-tick" aria-pressed="${asks.has(k) ? "true" : "false"}" data-act="askToggle" data-key="${k}" data-id="${o.id}">
        <span class="bx" aria-hidden="true">${icon("check", 12)}</span><span>Ask about this in the reply</span></button>
      ${isOpen(`hand:${k}`) ? "" : openBtn(`hand:${k}`, "Confirm by hand…", "btn ghost small", `Confirm by hand: ${rules.conditions[k]}`)}</span>
      ${isOpen(`hand:${k}`) ? reviewForm(o, "cond", k, v) : ""}` : ""}
  </li>`;
}

// Proposals for the BANTHE dimensions and blockers: fewer, and each reviewed on its own.
function otherProposals(o, mine) {
  const rest = o.proposals.filter((p) => p.status === "pending" && p.kind !== "cond");
  if (!rest.length) return "";
  return html`<div class="stack"><span class="lbl">Also proposed from the notes (${rest.length})</span>${rest.map((p) => proposalCard(o, p, mine))}</div>`;
}

function proposalCard(o, p, mine) {
  const current = p.kind === "score" ? o.scores[p.key] : o.blocker;
  const conflict = current !== null && current !== undefined && current !== p.value;
  const name = `prop:${p.id}`;
  const form = isOpen(`${name}:accept`) ? "accept" : isOpen(`${name}:edit`) ? "edit" : isOpen(`${name}:reject`) ? "reject" : "";
  return html`
  <div class="prop" data-act="hl" data-on="mouseover mouseout focusin focusout" data-q="${p.id}">
    <b>${keyLabel(p.kind, p.key)}: ${valueText(p.kind, p.key, p.value)}</b>
    <span class="meta-3">${p.evidenceType === "client_statement" ? "From a client statement" : "From an internal note"}. Now: ${valueText(p.kind, p.key, current)}.</span>
    ${p.quote ? html`<span class="meta">“${p.quote}”</span>` : ""}
    ${conflict ? html`<span class="warn-text meta">${icon("alert", 14)} It replaces a value a person confirmed: accepting needs a reason.</span>` : ""}
    ${!mine ? "" : form ? proposalForm(p, form) : html`<span class="cond-acts">
      ${conflict ? openBtn(`${name}:accept`, "Accept…", "btn primary small") : html`<button type="button" class="btn primary small" data-act="proposal" data-do="accept" data-id="${p.id}">Accept</button>`}
      ${openBtn(`${name}:edit`, "Edit…", "btn small", `Edit the proposal for ${keyLabel(p.kind, p.key)}`)}
      ${openBtn(`${name}:reject`, "Reject…", "btn ghost small", `Reject the proposal for ${keyLabel(p.kind, p.key)}`)}</span>`}
    ${err(name)}
  </div>`;
}

function proposalForm(p, what) {
  return html`
  <form class="panel-form" data-act="proposalForm" data-on="submit input change" data-id="${p.id}" data-do="${what}" data-kind="${p.kind}" data-key="${p.key}">
    ${what === "edit" ? valueSelect(p.kind, p.key, p.value) : ""}
    <label class="field">${what === "reject" ? "Why reject it" : what === "edit" ? "Why change it" : "Why replace the confirmed value"}<textarea name="reason" rows="2" required></textarea></label>
    <div class="actions"><button type="submit" class="btn primary small" disabled>${what === "accept" ? "Confirm" : what === "edit" ? "Accept my version" : "Reject"}</button>${cancelBtn}
      <span class="missing" data-missing>Give a reason.</span></div>
  </form>`;
}

// 3. The reply the owner writes, and the pack. The workspace sends email only before the first reply (Step 5), so
// the reply is copied into the owner's own email (REDESIGN_PLAN G21).
function replyAndPack(o, ctx, rd) {
  const text = ui.reply.has(o.id) ? ui.reply.get(o.id) : startReply(o, ctx);
  return html`
  <div class="sb"><h3>3. Your reply</h3></div>
  <label class="sr-only" for="reply-${o.id}">Your reply</label>
  <textarea id="reply-${o.id}" class="reply-box" rows="9" data-act="replyText" data-on="input" data-id="${o.id}">${text}</textarea>
  <p class="meta-3">Tick "Ask about this in the reply" on a condition to add its question. The workspace sends email only before the first reply: copy this into your own email, then add the client's answer here as notes.</p>
  <div class="row-flex" style="gap:6px"><button type="button" class="btn" data-act="copyReply" data-id="${o.id}">Copy the reply</button>
    <button type="button" class="btn ghost small" data-act="resetReply" data-id="${o.id}">Start again</button></div>
  <div class="pack-line">
    ${o.proposals.some((p) => p.status === "pending") ? html`<p class="meta-3">Evidence proposals still wait for you. Decide on them first, or they will not count in the pack.</p>` : ""}
    ${isOpen("pack") ? packForm(o, rd) : rd.ready
      ? openBtn("pack", "All six confirmed. Send the pack for approval…", "btn primary")
      : openBtn("pack", `${rd.excLabel}…`, "btn ghost")}
    ${err("pack")}
  </div>`;
}

function startReply(o, ctx) {
  return `Hi ${first(o.contact.name)},\n\n\n\nBest regards,\n${ctx.me}`;
}

function packForm(o, rd) {
  if (rd.ready) {
    return html`<form class="panel-form" data-act="packForm" data-on="submit" data-id="${o.id}" data-revision="${o.revision}" data-ready="1">
      <p class="meta">The rules say Ready. The pack freezes today's conditions, scores, revenue estimate and need for the approver.</p>
      <div class="actions"><button type="submit" class="btn primary">Send the pack</button>${cancelBtn}</div>
    </form>`;
  }
  const missing = [...rd.missing.map((k) => rules.conditions[k]), ...rd.dims.map((x) => x.text), ...(rd.weak ? ["The evidence is not strong enough yet"] : []),
    ...(rd.blocker ? [`Blocker: ${rules.reasons[o.blocker] || o.blocker}`] : [])];
  return html`<form class="panel-form exc" data-act="packForm" data-on="submit input" data-id="${o.id}" data-revision="${o.revision}" data-ready="">
    <b>${rd.excLabel}</b>
    <p class="meta">Missing: ${missing.join("; ")}. The approver sees what is missing and your reason before anything else in the pack. Readiness stays as the rules say.</p>
    <label class="field">Reason, one line <span class="hint">Up to 140 characters.</span><input name="exception" maxlength="140" required placeholder="Why this should go ahead now"></label>
    <div class="actions"><button type="submit" class="btn primary" disabled>Send for approval</button>${cancelBtn}<span class="missing" data-missing>Give the reason.</span></div>
  </form>`;
}

function parkForm(o, ctx) {
  return html`<form class="panel-form" data-act="parkForm" data-on="submit input" data-id="${o.id}">
    <label class="field">Check back on<input type="date" name="review_on" value="${addDays(ctx.today, 60)}" min="${ctx.today}" required></label>
    <label class="field">Reason<input name="reason" required placeholder="For example: budget is set for 2027"></label>
    <div class="actions"><button type="submit" class="btn primary small" disabled>Park until then</button>${cancelBtn}<span class="missing" data-missing>Give a reason.</span></div>
    ${err("park")}
  </form>`;
}

function closeForm(o) {
  return html`<form class="panel-form" data-act="closeForm" data-on="submit input change" data-id="${o.id}">
    <p class="meta">The lead closes and moves to Set aside under Companies, with the reason.</p>
    <label class="field">Reason<select name="reason" required><option value="">Choose a reason</option>${CLOSE_REASONS.map((r) => html`<option>${r}</option>`)}</select></label>
    <label class="field">Comment <span class="hint">Needed when the reason is Other.</span><textarea name="comment" rows="2"></textarea></label>
    <div class="actions"><button type="submit" class="btn primary small" disabled>Set aside</button>${cancelBtn}<span class="missing" data-missing>Choose a reason.</span></div>
    ${err("close")}
  </form>`;
}

// ----- the folded sections (Active) -----
function fold(key, titleText, body, { open = false, note = "" } = {}) {
  if (!body) return "";
  const shown = ui.folds.has(key) ? ui.folds.get(key) : open;
  return html`<details class="fold" ${shown ? "open" : ""}>
    <summary data-act="fold" data-fold="${key}"><span class="fold-title">${titleText}</span>${note ? html`<span class="meta-3">${note}</span>` : ""}</summary>
    <div class="fold-body">${body}</div>
  </details>`;
}

function taskNote(o, ctx) {
  const open = o.tasks.filter((t) => t.status !== "done");
  const late = open.filter((t) => daysBetween(ctx.today, t.due) < 0).length;
  return [open.length ? `${open.length} open` : "", late ? `${late} overdue` : "", o.suggestions.length ? plural(o.suggestions.length, "suggestion") : ""].filter(Boolean).join(", ");
}

function notesForm(o, ctx) {
  return html`
    <form class="panel-form" data-act="noteForm" data-on="submit input" data-id="${o.id}">
      <h3 class="sub-title">Add conversation notes</h3>
      <div class="form-grid">
        <label class="field">Kind<select name="type">${NOTE_TYPES.map((t) => html`<option>${t}</option>`)}</select></label>
        <label class="field">Date<input type="date" name="on" value="${ctx.today}" max="${ctx.today}" required></label>
      </div>
      <label class="field">Notes <span class="hint">Use their own words where you can; the agent may only cite words that are here.</span>
        <textarea name="text" rows="8" required></textarea></label>
      <label class="field">Or load them from a file <span class="hint">A text file or a meeting transcript (.txt, .md, .vtt, .srt). For Word or PDF, copy the text in.</span>
        <input type="file" name="file" accept=".txt,.md,.vtt,.srt,text/plain,text/vtt" data-act="noteFile" data-on="change"></label>
      <p class="meta-3" data-file role="status"></p>
      <label class="check"><input type="checkbox" name="analyze" checked>Ask the Evidence agent for proposals</label>
      <div class="actions"><button type="submit" class="btn primary" disabled>Save notes</button>${cancelBtn}<span class="missing" data-missing>Write the notes.</span></div>
      ${err("note")}
    </form>`;
}

function scoresBlock(o) {
  return html`
  <div class="block">
    <h3 class="sub-title">What the evidence shows</h3>
    <table class="list compact">
      <thead><tr><th scope="col">About</th><th scope="col">Confirmed</th><th scope="col">Needed for approval</th><th scope="col"><span class="sr-only">Review</span></th></tr></thead>
      <tbody>${DIMS.map((k) => {
        const v = o.scores[k];
        const floor = rules.floors[k];
        const low = floor !== undefined && v !== null && v !== undefined && v < floor;
        return html`
        <tr>
          <td><b>${rules.labels[k]}</b></td>
          <td>${v === null || v === undefined ? html`<span class="tag">Pending</span>` : html`<span class="${low ? "warn-text" : ""}">${scoreText(k, v)}</span>`}</td>
          <td>${floor === undefined ? html`<span class="meta-3">No minimum</span>` : html`<span class="meta">At least: ${rules.anchors[k][Math.ceil(floor)]}</span>`}</td>
          <td>${isOpen(`score:${k}`) ? "" : openBtn(`score:${k}`, "Review…", "btn ghost small", `Review ${rules.labels[k]}`)}</td>
        </tr>
        ${isOpen(`score:${k}`) ? html`<tr><td colspan="4">${reviewForm(o, "score", k, v)}</td></tr>` : ""}`;
      })}</tbody>
    </table>
  </div>`;
}

function conditionsBlock(o) {
  return html`
  <div class="block">
    <h3 class="sub-title">Conditions</h3>
    <table class="list compact">
      <thead><tr><th scope="col">Condition</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Review</span></th></tr></thead>
      <tbody>${Object.entries(rules.conditions).map(([k, label]) => html`
        <tr><td><b>${label}</b></td><td><span class="tag ${condTag(o.conditions[k])}">${condText(o.conditions[k])}</span></td>
          <td>${isOpen(`cond:${k}`) ? "" : openBtn(`cond:${k}`, "Review…", "btn ghost small", `Review: ${label}`)}</td></tr>
        ${isOpen(`cond:${k}`) ? html`<tr><td colspan="3">${reviewForm(o, "cond", k, o.conditions[k])}</td></tr>` : ""}`)}
      </tbody>
    </table>
  </div>`;
}

// The CGS revenue estimate (D5): entered here, used by the priority rules, never shown again.
function revenueBlock(o) {
  const w = revenueWords(o);
  return html`
  <div class="block">
    <div class="row-flex"><h3 class="sub-title" style="margin:0">CGS revenue estimate</h3>
      <span>${o.revenue ? `Revenue estimate recorded, ${w.head.toLowerCase()}` : w.head}</span>
      ${isOpen("revenue") ? "" : openBtn("revenue", o.revenue ? "Enter a new estimate…" : "Estimate it…", "btn ghost small")}</div>
    <p class="meta-3">The amount is not shown on any page after it is saved.</p>
    ${isOpen("revenue") ? html`
    <form class="panel-form" style="margin-top:10px" data-act="revenueForm" data-on="submit input" data-id="${o.id}" data-revision="${o.revision}">
      <div class="form-grid">
        <label class="field">Low (USD)<input type="number" name="low" min="1" step="any" autocomplete="off"></label>
        <label class="field">High (USD)<input type="number" name="high" min="1" step="any" autocomplete="off"></label>
      </div>
      <label class="check"><input type="checkbox" name="clear">Clear the estimate (not known)</label>
      <label class="field">Reason<textarea name="reason" rows="2" required></textarea></label>
      <div class="actions"><button type="submit" class="btn primary" disabled>Save estimate</button>${cancelBtn}<span class="missing" data-missing>Give a reason.</span></div>
      ${err("review")}
    </form>` : ""}
  </div>`;
}

function reviewForm(o, kind, key, value) {
  return html`
  <form class="panel-form" data-act="reviewForm" data-on="submit input change" data-kind="${kind}" data-key="${key}" data-id="${o.id}" data-revision="${o.revision}">
    ${valueSelect(kind, key, value, kind === "score" ? rules.labels[key] : rules.conditions[key])}
    ${kind === "cond" ? html`<label class="field">If not met, is it a blocker? <span class="hint">Optional. A blocker makes the rules say Not ready.</span>
      <select name="blocker"><option value="">No blocker</option>${Object.entries(rules.reasons).map(([v, t]) => html`<option value="${v}">${t}</option>`)}</select></label>` : ""}
    <label class="field">Evidence or reason <span class="hint">What the client said or did. It is kept with the change.</span><textarea name="reason" rows="2" required></textarea></label>
    <div class="actions"><button type="submit" class="btn primary small" disabled>Save</button>${cancelBtn}<span class="missing" data-missing>Give the evidence or a reason.</span></div>
    ${err("review")}
  </form>`;
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

function needBlock(o) {
  const need = o.need || {};
  const filled = NEED_FIELDS.some(([k]) => need[k]);
  return html`
  <div class="block">
    ${!isOpen("need") ? html`<div>${openBtn("need", "Edit…", "btn ghost small", "Edit the client need")}</div>` : html`
    <form class="panel-form" style="margin-top:10px" data-act="needForm" data-on="submit" data-id="${o.id}">
      <div class="form-grid">${NEED_FIELDS.map(([k, label]) => html`<label class="field">${label}<input name="${k}" value="${need[k] || ""}"></label>`)}</div>
      <p class="meta-3">Editing the need statement does not confirm the Need dimension or CGS fit; review those separately.</p>
      <div class="actions"><button type="submit" class="btn primary">Save need</button>${cancelBtn}</div>
      ${err("need")}
    </form>`}
    ${!isOpen("need") && filled ? html`<dl class="need">${NEED_FIELDS.filter(([k]) => need[k]).map(([k, label]) => html`<dt>${label}</dt><dd>${need[k]}</dd>`)}</dl>`
      : !isOpen("need") ? html`<p class="meta">Not written yet.</p>` : ""}
  </div>`;
}

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
        <span class="meta-3" style="display:block">${t.side} side, ${t.assignee}. <span class="${late ? "you-text" : ""}">${late ? "Overdue since" : "Due"} ${day(t.due, today)}</span></span></span></label></li>`;
    })}</ul>` : html`<p class="meta">No agreed tasks yet. Turn a suggested next action into a task.</p>`}
  </div>`;
}

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
      <span class="meta-3">${c.side === "cgs" ? "CGS promised" : c.side === "client" ? "The client promised" : "Promised"}${c.due ? `, due ${day(c.due, ctx.today)}` : ""}${c.overdue ? html`. <span class="you-text">Overdue by ${plural(c.daysOverdue, "day")}</span>` : ""}</span>
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

function historyBlock(o, ctx) {
  const decided = o.proposals.filter((p) => p.status !== "pending");
  return html`
  <div class="block stack">
    <details><summary class="sub-title">Confirmed evidence (${o.evidence.length})</summary>
      ${o.evidence.length ? html`<ul class="history">${[...o.evidence].reverse().map((e) => html`<li>
        <b>${keyLabel(e.kind, e.key) || e.key}: ${valueText(e.kind, e.key, e.value)}</b>
        ${e.quote ? html`<span class="quote">"${e.quote}"</span>` : ""}
        <span class="meta-3">${e.how} by ${e.by}, ${day(e.on, ctx.today)}${e.reason && e.reason !== e.quote && e.kind !== "revenue" ? `. ${e.reason}` : ""}</span></li>`)}</ul>` : html`<p class="meta">None yet.</p>`}
    </details>
    ${decided.length ? html`<details><summary class="sub-title">Decided proposals (${decided.length})</summary>
      <ul class="history">${decided.map((p) => html`<li><b>${keyLabel(p.kind, p.key)}: ${valueText(p.kind, p.key, p.value)}</b><span class="meta-3">${p.status}${p.reason ? `: ${p.reason}` : ""}</span></li>`)}</ul>
    </details>` : ""}
    <details><summary class="sub-title">Notes (${o.notes.length})</summary>
      ${o.notes.length ? html`<ul class="history">${[...o.notes].reverse().map((n) => html`<li><b>${n.type}, ${day(n.on, ctx.today)}</b><span class="email-plain">${n.text}</span></li>`)}</ul>` : html`<p class="meta">No notes yet.</p>`}
    </details>
    ${o.decisions.length ? html`<details><summary class="sub-title">Decisions (${o.decisions.length})</summary>
      <ul class="history">${o.decisions.map((x) => html`<li><b>Step ${x.step}: ${x.decision}</b><span class="meta-3">${x.by}${x.role ? ` (${x.role})` : ""}, ${day(x.on, ctx.today)}${x.note ? `. ${x.note}` : ""}</span></li>`)}</ul>
    </details>` : ""}
    <details><summary class="sub-title">Activity (${o.activity.length})</summary>
      <ul class="history">${[...o.activity].reverse().slice(0, 30).map((a) => html`<li><span>${moneyless(a.text)}</span><span class="meta-3">${day(a.on, ctx.today)}</span></li>`)}</ul>
    </details>
  </div>`;
}

// Activity lines may name a revenue amount; the page shows them without it (D5). Two forms reach it: "$500K–$700K"
// (seeded and older lines) and the raw estimate a revenue change records today ("{'low': 500000, 'high': 700000}").
export const moneyless = (text) => String(text)
  .replace(/\{[^{}]*['"](low|high)['"][^{}]*\}/g, "[amount]")
  .replace(/\$\s?[\d,.]+(\s?(to|-|–)\s?\$?\s?[\d,.]+)?\s?[kKmM]?/g, "[amount]");

// =====================================================================================================
// Awaiting approval: one pack reviewed on one screen.
// =====================================================================================================
function approvalView(d, ctx, link) {
  const o = d.detail;
  return html`
  <div class="review three">
    ${queue(d, ctx, link, "Packs to approve", (s, det) => {
      const pk = det && openPack(det);
      if (pk && pk.staleReason) return ["Changed since it was sent", s.owner !== ctx.me];
      if (pk && pk.handoverTo) return [`Waiting for ${pk.handoverTo} to take it over`, pk.handoverTo === ctx.me];
      return s.owner === ctx.me ? ["You sent it; a colleague approves", false] : [`From ${s.owner || "no owner"}, awaiting approval`, true];
    })}
    ${o ? html`<section class="card detail mid" aria-labelledby="detail-h">${packMain(o, ctx)}</section>
    <aside class="card side-col" aria-label="Your decision">${packSide(o, ctx)}</aside>` : ""}
  </div>`;
}

const openPack = (o) => (o.packs || []).filter((p) => p.status === "open").slice(-1)[0];

function packMain(o, ctx) {
  const pk = openPack(o);
  const s = (pk && pk.snapshot) || {};
  const conds = s.conditions || o.conditions;
  const keys = Object.keys(rules.conditions);
  const met = keys.filter((k) => conds[k] === true).length;
  const missing = keys.filter((k) => conds[k] !== true).map((k) => rules.conditions[k]);
  const need = s.need || o.need || {};
  const revChanged = pk && JSON.stringify(s.revenue || null) !== JSON.stringify(o.revenue || null);
  const rw = revenueWords(o);
  return html`
  ${headBlock(o, ctx, pk ? `Sent for approval by ${pk.submittedBy} on ${day(pk.submittedOn, ctx.today)}.` : "No open pack.")}
  ${noticeBlock()}
  <div class="block"><div class="vs">
    <div><span class="lbl">Conditions</span><span class="md">${met} of ${keys.length} met</span><span class="meta-3">${missing.length ? `Missing: ${missing.join(", ").toLowerCase()}` : "Nothing missing"}</span></div>
    <div><span class="lbl">Service</span><span class="md">${need.service || "Not written"}</span><span class="meta-3">${need.problem ? "From the client need" : ""}</span></div>
    <div><span class="lbl">Revenue estimate</span><span class="md ${revChanged ? "you-text" : ""}">${revChanged ? "Changed since it was sent" : s.revenue ? "Recorded" : "Not estimated"}</span><span class="meta-3">${o.revenue ? rw.head : ""}${o.revenue ? ". " : ""}The amount is not shown here</span></div>
    <div><span class="lbl">Next step</span><span class="md">${o.next.action || "None set"}</span><span class="meta-3">${o.next.due ? day(o.next.due, ctx.today) : ""}</span></div>
  </div></div>
  ${pk && pk.exception ? html`<div class="block"><div class="notice warn">${icon("alert")}<span><b>Sent as an exception: the rules do not say Ready.</b> ${pk.submittedBy}'s reason: “${pk.exception}”</span></div></div>` : ""}
  ${pk && pk.staleReason ? html`<div class="block"><div class="notice warn">${icon("alert")}<span><b>Changed since ${pk.submittedBy} sent it.</b> ${pk.staleReason}. An out-of-date pack cannot be approved: ask for it again, or return it.</span></div></div>` : ""}
  ${pk && pk.handoverTo ? html`<div class="block"><div class="notice info">${icon("clock")}<span>Approved by ${pk.handoverBy} with a handover to ${pk.handoverTo}. ${o.owner} stays responsible until ${pk.handoverTo} accepts.</span></div></div>` : ""}
  <div class="block">
    <div class="sb"><h3 class="sub-title" style="margin:0">The six conditions</h3><span class="${met === keys.length ? "good-text" : "meta-3"} md">${met} of ${keys.length} confirmed</span></div>
    <ul class="proofs">${keys.map((k) => {
      const ev = [...o.evidence].reverse().find((e) => e.kind === "cond" && e.key === k && e.value === conds[k]);
      return html`<li><span class="md">${rules.conditions[k]}</span>
        <span>${ev ? html`<span class="meta">${ev.quote ? `“${ev.quote}”` : ev.reason || "Confirmed"}</span><span class="meta-3" style="display:block">${ev.how}, ${day(ev.on, ctx.today)}</span>` : html`<span class="meta-3">${conds[k] === true ? "No evidence line is on file" : "No evidence yet"}</span>`}</span>
        <span class="tag ${conds[k] === true ? "good" : ""}">${ev ? ev.by : condText(conds[k])}</span></li>`;
    })}</ul>
    <p class="meta-3" style="margin-top:10px">Every piece of evidence comes from an email, a call note or a person's review. Agents propose; a person confirms.</p>
  </div>
  ${fold("history", "History: evidence, notes, decisions", historyBlock(o, ctx))}`;
}

function packSide(o, ctx) {
  const pk = openPack(o);
  if (!pk) return html`<div class="side-sec"><h3>Your decision</h3><p class="meta">No open pack. The owner can send one again from Active.</p></div>`;
  const owner = o.owner === ctx.me;
  const roles = owner ? ["Lead owner"] : ["Another consultant", "Director of Operations"];
  const roleSelect = html`<label class="field">You act as<select name="role">${roles.map((r) => html`<option>${r}</option>`)}</select></label>`;
  const crm = o.crm && o.crm.leadId;
  if (pk.handoverTo === ctx.me) {
    return html`<div class="side-sec"><h3>Your decision</h3>
      <p class="meta">${pk.handoverBy} approved this and asks you to take it over. It becomes Qualified and yours when you accept.</p>
      <button type="button" class="btn primary" data-act="handover" data-id="${pk.id}">Accept the handover</button>
      ${isOpen("decline") ? html`<form class="panel-form" data-act="declineForm" data-on="submit input" data-id="${pk.id}">
        <label class="field">Why you decline<textarea name="reason" rows="2" required></textarea></label>
        <div class="actions"><button type="submit" class="btn primary small" disabled>Decline</button>${cancelBtn}<span class="missing" data-missing>Give a reason.</span></div>
      </form>` : openBtn("decline", "Decline…", "btn ghost")}
      ${err("handover")}</div>`;
  }
  if (pk.handoverTo) return html`<div class="side-sec"><h3>Your decision</h3><p class="meta">Already approved; it waits for ${pk.handoverTo} to accept the handover.</p></div>`;
  const stale = !!pk.staleReason;
  const sub = String(ui.open || "");
  return html`
  <div class="side-sec">
    <h3>Your decision</h3>
    <p class="meta">${owner ? "You sent this pack. A colleague usually approves it." : `Approving makes it Qualified${crm ? ": in Insightly the lead becomes an opportunity in the Qualification stage, with the figures as they stand there now" : ""}.`}</p>
    ${owner && stale ? html`<button type="button" class="btn primary" data-act="resubmit" data-id="${o.id}" data-revision="${o.revision}">Send the updated pack again</button>${err("pack")}` : ""}
    ${owner && stale ? "" : sub.startsWith("decide:") ? decideForms(o, pk, ctx, roleSelect) : html`
    <form class="panel-form side-form" data-act="quickDecide" data-on="submit input" data-id="${pk.id}" data-stale="${stale ? "1" : ""}">
      ${roleSelect}
      <label class="field">Comment for ${o.owner || "the owner"} <span class="hint">Needed to return it: what has to change.</span><textarea name="comment" rows="3"></textarea></label>
      ${stale ? html`<button type="submit" class="btn primary" name="decision" value="resubmit">Ask ${o.owner || "the owner"} to send it again</button>`
        : html`<button type="submit" class="btn primary" name="decision" value="approve">Approve</button>`}
      <button type="submit" class="btn" name="decision" value="return" data-needs-comment disabled>Return with a comment</button>
      ${err("decide")}
    </form>
    <div class="minor">${stale ? "" : openBtn("decide:approve", "Approve and hand over…", "btn ghost small")}
      ${openBtn("decide:nurture", "Park it…", "btn ghost small")}${openBtn("decide:close", "Close…", "btn ghost small")}</div>`}
  </div>`;
}

function decideForms(o, pk, ctx, roleSelect) {
  const m = ui.open;
  if (m === "decide:approve") {
    return html`<form class="panel-form" data-act="decideForm" data-on="submit" data-id="${pk.id}" data-decision="approve">
      ${roleSelect}
      <label class="field">Owner from now on<select name="new_owner">${ctx.users.map((u) => html`<option ${u === o.owner ? "selected" : ""}>${u}</option>`)}</select></label>
      <p class="meta-3">If you choose someone else, the approval waits until they accept the handover.</p>
      <div class="actions"><button type="submit" class="btn primary">Approve</button>${cancelBtn}</div>${err("decide")}
    </form>`;
  }
  if (m === "decide:nurture") {
    return html`<form class="panel-form" data-act="decideForm" data-on="submit" data-id="${pk.id}" data-decision="nurture">
      ${roleSelect}
      <label class="field">Check back on<input type="date" name="review_on" value="${addDays(ctx.today, 60)}" min="${ctx.today}"></label>
      <div class="actions"><button type="submit" class="btn primary">Park it</button>${cancelBtn}</div>${err("decide")}
    </form>`;
  }
  return html`<form class="panel-form" data-act="decideForm" data-on="submit input change" data-id="${pk.id}" data-decision="close">
    ${roleSelect}
    <label class="field">Reason<select name="reason" required><option value="">Choose a reason</option>${CLOSE_REASONS.map((r) => html`<option>${r}</option>`)}</select></label>
    <div class="actions"><button type="submit" class="btn primary" disabled>Close</button>${cancelBtn}<span class="missing" data-missing>Choose a reason.</span></div>${err("decide")}
  </form>`;
}

// =====================================================================================================
// Qualified and Parked: tables with the side panel.
// =====================================================================================================
function tableView(d, ctx, link) {
  const list = d.lists[d.tab];
  const qualified = d.tab === "qualified";
  return html`
  <section class="card" aria-labelledby="q-h">
    <div class="card-head"><div><h2 id="q-h">${TABS.find(([t]) => t === d.tab)[1]} <span class="cnt">${list.length}</span></h2><p class="meta">${NOTE[d.tab]}</p></div></div>
    <div class="table-wrap"><table class="list rows-table">
      <thead><tr>${(qualified ? ["Company", "Contact", "Service", "Owner", "Conditions", "Qualified"] : ["Company", "Contact", "What they said", "Check back", "Owner"]).map((h) => html`<th scope="col">${h}</th>`)}</tr></thead>
      <tbody>${list.map((s) => {
        const o = d.details[s.id] || {};
        const company = html`<td><span class="co"><span class="avatar" aria-hidden="true">${initials(s.company)}</span><span class="co-text">
          <a class="nm" href="${link(d.tab, { id: s.id })}" data-act="openRow" data-row="${s.id}">${s.company}</a></span></span></td>`;
        const contact = html`<td><span class="cell-main">${s.contact.name || "To confirm"}</span><span class="meta-3">${s.contact.title}</span></td>`;
        if (qualified) {
          const dec = approvedBy(o);
          return html`<tr class="${s.id === d.id ? "on" : ""}">${company}${contact}<td>${(o.need || {}).service || html`<span class="meta-3">Not written</span>`}</td>
            <td class="meta">${s.owner || "No owner"}</td><td class="good-text md">${conditionsMet(o)}</td><td class="meta">${dec ? day(dec.on, ctx.today) : ""}</td></tr>`;
        }
        const rep = o.outreach && o.outreach.replyText;
        const left = s.next.due ? daysBetween(ctx.today, s.next.due) : null;
        return html`<tr class="${s.id === d.id ? "on" : ""}">${company}${contact}
          <td>${rep ? html`<span class="cell-main">“${rep.length > 90 ? `${rep.slice(0, 90)}…` : rep}”</span><span class="meta-3">${day(o.outreach.replyOn, ctx.today)}</span>` : html`<span class="meta-3">No reply recorded</span>`}</td>
          <td><span class="${left !== null && left <= 0 ? "you-text md" : "md"}">${s.next.due ? day(s.next.due, ctx.today) : "No date"}</span></td>
          <td class="meta">${s.owner || "No owner"}</td></tr>`;
      })}</tbody>
    </table></div>
  </section>`;
}

const approvedBy = (o) => [...(o.decisions || [])].reverse().find((x) => x.step === 8 && /approv/i.test(x.decision));
function conditionsMet(o) {
  if (!o.conditions) return "";
  const keys = Object.keys(rules.conditions);
  return `${keys.filter((k) => o.conditions[k] === true).length} of ${keys.length} met`;
}

function panel(d, ctx, link) {
  const list = d.lists[d.tab];
  const i = list.findIndex((x) => x.id === d.id);
  const near = (n) => (list[i + n] ? { href: link(d.tab, { id: list[i + n].id }), id: list[i + n].id } : null);
  const o = d.detail;
  const parked = d.tab === "parked";
  const c = parked ? parkedContent(o, d, ctx) : qualifiedContent(o, ctx);
  return drawer({
    name: o.company.name, sub: `${o.contact.name || "Contact to confirm"}${o.contact.title ? `, ${o.contact.title}` : ""}`,
    stage: STAGE_LABELS[o.stage], tone: parked ? "" : "good", owner: o.owner ? `Owner: ${o.owner}` : "No owner",
    pos: `${i + 1} of ${list.length}`, prev: near(-1), next: near(1), blocks: c.blocks, foot: c.foot, record: o.id,
  });
}

function qualifiedContent(o, ctx) {
  const dec = approvedBy(o);
  const need = o.need || {};
  const rw = revenueWords(o);
  return {
    blocks: html`
    <section class="pblock"><dl class="dl">
      <dt>Service</dt><dd>${need.service || "Not written"}</dd>
      ${need.problem ? html`<dt>Problem</dt><dd>${need.problem}</dd>` : ""}
      <dt>Approved</dt><dd>${dec ? `${dec.by}${dec.role ? ` (${dec.role})` : ""}, ${day(dec.on, ctx.today)}` : "Not recorded"}</dd>
      <dt>Conditions</dt><dd>${conditionsMet(o)}</dd>
      <dt>Revenue estimate</dt><dd>${o.revenue ? `${rw.head}. The amount is kept out of this page${o.crm && o.crm.leadId ? "; Insightly has it" : ""}.` : "Not estimated"}</dd>
    </dl>${crmLinks(o.crm) ? html`<div class="row-flex" style="margin-top:12px">${crmLinks(o.crm)}</div>` : ""}</section>
    <section class="pblock">${historyBlock(o, ctx)}</section>`,
    foot: "",
  };
}

// Parked (backend stage nurture): why and when, the review date and what happens then.
function parkedContent(o, d, ctx) {
  const moved = [...o.activity].reverse().find((a) => /^Moved to nurture/.test(a.text));
  const reason = moved ? moved.text.replace(/^Moved to nurture( at Step 8)? by [^:.]+[:.]?\s*/, "").replace(/\s*\d+ unsent email draft\(s\) withdrawn\.?$/, "") : "";
  const by = moved && (moved.text.match(/ by ([^:.]+)/) || [])[1];
  const due = o.next.due;
  const left = due ? daysBetween(ctx.today, due) : null;
  const asked = [...o.activity].reverse().find((a) => /asked to reconnect now/.test(a.text) && a.on === ctx.today);
  const reply = (d.inbox || []).find((it) => it.kind === "reply" && it.status === "new" && it.opportunityId === o.id);
  const rep = o.outreach && o.outreach.replyText;
  return {
    blocks: html`
    ${ui.notice ? html`<section class="pblock">${ui.notice.map((n) => html`<div class="notice ${n.cls || ""}" role="status">${icon(n.cls === "good" ? "check" : "alert")}<span>${n.text}</span></div>`)}</section>` : ""}
    ${reply ? html`<section class="pblock"><div class="notice warn">${icon("alert")}<span>A new reply arrived. <a href="${href("outreach", { item: reply.id })}">Record it in Outreach</a>: it makes the lead Active again.</span></div></section>` : ""}
    <section class="pblock"><dl class="dl">
      ${rep ? html`<dt>What they said</dt><dd>“${rep}”</dd>` : ""}
      <dt>Why</dt><dd>${reason || "No reason was recorded."}</dd>
      <dt>Parked</dt><dd>${moved ? `${by ? `By ${by}, ` : ""}${day(moved.on, ctx.today)}` : "Not recorded"}</dd>
      <dt>Check back</dt><dd>${due ? html`${day(due, ctx.today)} <span class="meta">${left > 0 ? `in ${plural(left, "day")}` : left === 0 ? "today" : `${plural(-left, "day")} ago`}</span>` : "No date set"}</dd>
    </dl></section>
    <section class="pblock"><p class="meta">On the review date the Research agent checks ${o.company.name} for news since the brief. If it finds some, the Outreach agent drafts a reconnect email that cites it, for ${o.owner || "the owner"} to approve; if not, ${o.owner || "the owner"} is told and decides. Nothing is sent on its own.${o.crm && o.crm.leadId ? " Insightly shows the lead as Nurture until then." : ""}</p>
      ${asked && !ui.notice ? html`<div class="notice info" style="margin-top:10px">${icon("clock")}<span>Resume was asked for today. The Research agent is checking for news; a notification follows when it is done.</span></div>` : ""}</section>
    ${isOpen("review") ? html`<section class="pblock">${reviewDateForm(o, ctx)}</section>` : ""}
    ${isOpen("reply") ? html`<section class="pblock">${replyForm(o, ctx)}</section>` : ""}
    ${isOpen("close") ? html`<section class="pblock">${closeForm(o)}</section>` : ""}`,
    foot: ui.open ? "" : html`
      <button type="button" class="btn primary" data-act="reconnect" data-id="${o.id}" ${asked ? "disabled" : ""}>Resume now</button>
      ${openBtn("review", "Change the date…", "btn")}
      ${openBtn("reply", "Record a reply…", "btn")}
      ${openBtn("close", "Not a fit…", "btn ghost")}`,
  };
}

function reviewDateForm(o, ctx) {
  return html`<form class="panel-form" data-act="reviewDateForm" data-on="submit input" data-id="${o.id}">
    <h3 class="sub-title">Change the check-back date</h3>
    <div class="form-grid">
      <label class="field">Check back on<input type="date" name="review_on" value="${o.next.due || addDays(ctx.today, 30)}" min="${ctx.today}" required></label>
      <label class="field">Reason<input name="reason" required placeholder="For example: their budget round is in March"></label>
    </div>
    <div class="actions"><button type="submit" class="btn primary" disabled>Save the date</button>${cancelBtn}<span class="missing" data-missing>Give a reason.</span></div>
    ${err("reviewDate")}
  </form>`;
}

function replyForm(o, ctx) {
  return html`<form class="panel-form" data-act="replyForm" data-on="submit input" data-id="${o.id}">
    <h3 class="sub-title">Record the client's reply</h3>
    <p class="meta">Any reply makes the lead Active again. The Evidence agent labels it and reads it for evidence, for you to confirm.</p>
    <label class="field">What they wrote<textarea name="text" rows="5" required></textarea></label>
    <label class="field">Received on<input type="date" name="on" value="${ctx.today}" max="${ctx.today}" required style="max-width:220px"></label>
    <div class="actions"><button type="submit" class="btn primary" disabled>Record reply</button>${cancelBtn}<span class="missing" data-missing>Paste the reply.</span></div>
    ${err("reply")}
  </form>`;
}

// A transcript file (WebVTT or SubRip) as plain lines: "Speaker: words", without cue numbers and times.
function transcriptText(raw) {
  const out = [];
  for (const block of raw.replace(/\r/g, "").split(/\n{2,}/)) {
    if (/^(WEBVTT|NOTE|STYLE|REGION)\b/.test(block.trim())) continue;
    const lines = block.split("\n").filter((l) => l.trim() && !/^\d+$/.test(l.trim()) && !/-->/.test(l));
    for (const l of lines) {
      const voice = l.match(/^<v(?:\.[^ >]*)?\s+([^>]+)>(.*?)(?:<\/v>)?$/);
      const text = (voice ? `${voice[1].trim()}: ${voice[2]}` : l).replace(/<[^>]+>/g, "").trim();
      if (text && text !== out[out.length - 1]) out.push(text);
    }
  }
  return out.join("\n");
}

// ---------- actions ----------
async function busy(button, fn) {
  if (button) { button.classList.add("busy"); button.disabled = true; }
  try { return await fn(); } finally { if (button && button.isConnected) { button.classList.remove("busy"); button.disabled = false; } }
}

function missing(form, text) {
  form.querySelectorAll("[type=submit]").forEach((b) => { if (!b.hasAttribute("data-needs-comment")) b.disabled = !!text; });
  const line = form.querySelector("[data-missing]");
  if (line) line.textContent = text || "";
}

function message(e) {
  return e instanceof ApiError && e.code === "revision_conflict"
    ? "Someone changed this opportunity while you were looking. The page now shows the latest version; check it and try again."
    : e.message;
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

// The reply being written: the question for each ticked condition sits before the signature.
function setAsk(area, q, on) {
  const text = area.value;
  const sign = text.search(/\n\n(Best|Kind|Thanks|Regards|Many thanks)[^\n]*\n[^\n]*$/i);
  if (on) area.value = sign >= 0 ? `${text.slice(0, sign).replace(/\s+$/, "")}\n\n${q}${text.slice(sign)}` : `${text.replace(/\s+$/, "")}\n\n${q}`;
  else area.value = text.replace(`\n\n${q}`, "").replace(q, "");
}

export const actions = {
  ...panelActions("qualification", (ctx) => ({ team: ctx.route.params.team })),
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
    ctx.focusAfter = ui.open ? ".panel-form select, .panel-form textarea, .panel-form input, .cond-acts button" : null;
    ctx.refresh();
  },
  // Pointing at a proposal marks its phrase in the reply. Only a class changes; the text stays as drawn.
  hl(t, e) {
    const on = e.type === "mouseover" || e.type === "focusin";
    document.querySelectorAll(`mark.q[data-q="${CSS.escape(t.dataset.q)}"]`).forEach((m) => m.classList.toggle("hl", on));
  },

  // the reply being written
  replyText(t) { ui.reply.set(t.dataset.id, t.value); },
  askToggle(t) {
    const id = t.dataset.id;
    const k = t.dataset.key;
    const asks = ui.asks.get(id) || new Set();
    const on = !asks.has(k);
    if (on) asks.add(k); else asks.delete(k);
    ui.asks.set(id, asks);
    t.setAttribute("aria-pressed", on ? "true" : "false");
    const area = document.getElementById(`reply-${id}`);
    if (area && ASKS[k]) { setAsk(area, ASKS[k], on); ui.reply.set(id, area.value); }
  },
  async copyReply(t, e, ctx) {
    const area = document.getElementById(`reply-${t.dataset.id}`);
    try {
      await navigator.clipboard.writeText(area ? area.value : "");
      ctx.toast("Copied. Paste it into your own email.");
    } catch {
      ctx.toast("The browser did not allow copying. Select the text and copy it yourself.");
    }
  },
  resetReply(t, e, ctx) {
    ui.reply.delete(t.dataset.id);
    ui.asks.delete(t.dataset.id);
    ctx.refresh();
  },

  // proposals
  proposal(t, e, ctx) {
    write(`prop:${t.dataset.id}`, t, ctx, () => ctx.api.post(`/proposals/${enc(t.dataset.id)}`, { action: "accept" }),
      (res) => { ui.notice = [{ text: `Confirmed. ${res.change}.`, cls: "good" }]; });
  },
  notQuite(t, e, ctx) {
    write(`prop:${t.dataset.id}`, t, ctx, () => ctx.api.post(`/proposals/${enc(t.dataset.id)}`, { action: "reject", reason: t.dataset.reason }),
      () => { ui.notice = [{ text: `Not counted: ${t.dataset.reason.toLowerCase()}. The Evidence agent learns from your reason.`, cls: "" }]; });
  },
  proposalForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give a reason.");
    if (!reason) return;
    const body = { action: form.dataset.do, reason };
    if (form.dataset.do === "edit") body.value = parseValue(form.dataset.kind, form.elements.value.value);
    write(`prop:${form.dataset.id}`, form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/proposals/${enc(form.dataset.id)}`, body),
      (res) => { ui.notice = [{ text: res.status === "rejected" ? "Rejected. Nothing changed." : `${res.status === "edited" ? "Your version was accepted" : "Confirmed"}. ${res.change}.`, cls: "good" }]; });
  },

  // notes
  noteForm(form, e, ctx) {
    const f = form.elements;
    if (e.type !== "submit") return missing(form, f.text.value.trim() ? "" : "Write the notes.");
    if (!f.text.value.trim()) return;
    write("note", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(form.dataset.id)}/notes`,
      { type: f.type.value, on: f.on.value, text: f.text.value.trim(), analyze: f.analyze.checked }), (res) => {
      const n = [];
      if (f.analyze.checked) n.push({ text: `Notes saved. The Evidence agent proposed ${plural(res.proposals, "item")} for you to confirm.`, cls: "good" });
      else n.push({ text: "Notes saved.", cls: "good" });
      if (res.droppedForInvalidQuote) n.push({ text: `${plural(res.droppedForInvalidQuote, "proposal")} dropped because the quote is not in the notes.`, cls: "warn" });
      if ((res.handoffs || []).length) n.push({ text: `The Research agent was asked to find: ${res.handoffs.join(", ")}.`, cls: "" });
      ui.notice = n;
    });
  },
  async noteFile(t) {
    const file = t.files && t.files[0];
    const form = t.closest("form");
    const line = form.querySelector("[data-file]");
    if (!file) return;
    if (!/\.(txt|md|vtt|srt|text)$/i.test(file.name) && !(file.type || "").startsWith("text/")) {
      line.textContent = `${file.name} is not a text file. For Word or PDF, copy the text in.`;
      t.value = "";
      return;
    }
    if (file.size > 300 * 1024) {
      line.textContent = `${file.name} is larger than 300 KB. Paste the part of the conversation that matters.`;
      t.value = "";
      return;
    }
    let text = await file.text();
    if (/\.(vtt|srt)$/i.test(file.name) || /^WEBVTT/.test(text)) text = transcriptText(text);
    const area = form.elements.text;
    area.value = area.value.trim() ? `${area.value.trim()}\n\n${text}` : text;
    if (/\.(vtt|srt)$/i.test(file.name)) form.elements.type.value = "Meeting notes";
    area.dispatchEvent(new Event("input", { bubbles: true }));
    line.textContent = `Loaded ${file.name}. Check the text before you save it.`;
  },
  readNote(t, e, ctx) {
    write("readNote", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/notes/${enc(t.dataset.note)}/analyze`), (res) => {
      ui.notice = [{ text: `The Evidence agent proposed ${plural(res.proposals, "item")} for you to confirm.`, cls: "good" }];
    });
  },
  async copyQuestions(t, e, ctx) {
    try {
      await navigator.clipboard.writeText(t.dataset.text);
      ctx.toast("Copied the questions for the next call.");
    } catch {
      ctx.toast("The browser did not allow copying. Select the questions instead.");
    }
  },

  // manual review: dimension, condition, blocker, revenue, need
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
      () => { ui.notice = [{ text: clear ? "Saved. The revenue estimate is cleared." : "Saved. The revenue estimate is recorded; the amount is not shown again.", cls: "good" }]; });
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
  promiseForm(form, e, ctx) {
    const why = form.elements.why.value.trim();
    if (e.type !== "submit") return missing(form, why ? "" : "Say why.");
    if (!why) return;
    const status = (e.submitter && e.submitter.value) || "done";
    write(`promise:${form.dataset.cid}`, e.submitter, ctx, () => ctx.api.post(`/opportunities/${enc(form.dataset.id)}/commitments/${enc(form.dataset.cid)}`, { status, why }),
      () => { ui.notice = [{ text: status === "done" ? "Promise marked done." : "Promise closed: it no longer applies.", cls: "good" }]; });
  },

  // the reply means not now, or not interested
  parkForm(form, e, ctx) {
    const f = form.elements;
    const why = !f.review_on.value ? "Choose a date." : !f.reason.value.trim() ? "Give a reason." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    const id = form.dataset.id;
    write("park", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(id)}/nurture`, { reason: f.reason.value.trim(), review_on: f.review_on.value }), () => {
      resetUi();
      ctx.toast(`Parked until ${day(f.review_on.value, ctx.today)}. The evidence so far is kept.`, { link: { href: href("qualification", { tab: "parked", id }), label: "See it" } });
      ctx.go("qualification", { tab: "active", team: ctx.route.params.team });
    });
  },
  closeForm(form, e, ctx) {
    const f = form.elements;
    const why = !f.reason.value ? "Choose a reason." : f.reason.value === "Other" && !f.comment.value.trim() ? "Say why: the reason is Other." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    const tab = ctx.route.params.tab;
    write("close", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(form.dataset.id)}/close`, { reason: f.reason.value, comment: f.comment.value.trim() }), () => {
      resetUi();
      ctx.toast(`Set aside: ${f.reason.value}. It is under Set aside on Companies.`);
      ctx.go("qualification", { tab, team: ctx.route.params.team });
    });
  },

  // Step 7: the pack
  packForm(form, e, ctx) {
    const ready = form.dataset.ready === "1";
    const exc = ready ? "" : form.elements.exception.value.trim();
    if (e.type !== "submit") return missing(form, ready || exc ? "" : "Give the reason.");
    if (!ready && !exc) return;
    const id = form.dataset.id;
    write("pack", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(id)}/packs`,
      { exception: exc || null, revision: Number(form.dataset.revision) }), () => {
      ctx.toast(ready ? "The pack was sent for approval." : "The pack was sent for approval as an exception.", { link: { href: href("qualification", { tab: "approval", id }), label: "See it" } });
    });
  },
  resubmit(t, e, ctx) {
    write("pack", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/packs`, { revision: Number(t.dataset.revision) }),
      () => { ui.notice = [{ text: "The updated pack was sent again.", cls: "good" }]; });
  },

  // Step 8: the approver
  quickDecide(form, e, ctx) {
    const comment = form.elements.comment.value.trim();
    if (e.type !== "submit") {
      form.querySelector("[data-needs-comment]").disabled = !comment;
      return;
    }
    const decision = (e.submitter && e.submitter.value) || "approve";
    if (decision === "return" && !comment) return;
    const body = { decision: decision === "resubmit" ? "return" : decision, role: form.elements.role.value };
    if (decision === "return") body.questions = comment;
    if (decision === "resubmit") body.questions = comment || null;
    write("decide", e.submitter, ctx, () => ctx.api.post(`/packs/${enc(form.dataset.id)}/decision`, body), (res) => {
      ctx.toast(res.status === "approved" ? "Approved. The opportunity is now Qualified."
        : res.status === "handover_pending" ? `Approved. It waits for ${res.handoverTo} to accept the handover.`
        : decision === "resubmit" ? "Asked the owner to send the pack again. Nothing went to Insightly." : "Returned with your comment. Nothing went to Insightly.");
      resetUi();
    });
  },
  decideForm(form, e, ctx) {
    const f = form.elements;
    const decision = form.dataset.decision;
    const why = decision === "close" && !f.reason.value ? "Choose a reason." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    const body = { decision, role: f.role.value };
    if (decision === "approve") body.new_owner = f.new_owner.value;
    if (decision === "nurture") body.review_on = f.review_on.value || null;
    if (decision === "close") body.reason = f.reason.value;
    write("decide", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/packs/${enc(form.dataset.id)}/decision`, body), (res) => {
      const text = {
        approved: "Approved. The opportunity is now Qualified.",
        handover_pending: `Approved. It waits for ${res.handoverTo} to accept the handover.`,
        nurture: "Parked.",
        close: "Closed. It is under Set aside on Companies.",
      }[res.status] || "Saved.";
      ctx.toast(text);
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

  // Parked
  reconnect(t, e, ctx) {
    write("reconnect", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/nurture/reconnect`), (res) => {
      ui.notice = [{ text: res.queued ? "The Research agent checks for news now. If it finds some, a reconnect draft follows for the owner to approve."
        : "The Research agent is already checking this company.", cls: "good" }];
    });
  },
  reviewDateForm(form, e, ctx) {
    const f = form.elements;
    const why = !f.review_on.value ? "Choose a date." : !f.reason.value.trim() ? "Give a reason." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    write("reviewDate", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(form.dataset.id)}/nurture/review`,
      { review_on: f.review_on.value, reason: f.reason.value.trim() }), () => {
      ui.notice = [{ text: `The check-back date is now ${day(f.review_on.value, ctx.today)}.`, cls: "good" }];
    });
  },
  replyForm(form, e, ctx) {
    const f = form.elements;
    if (e.type !== "submit") return missing(form, f.text.value.trim() ? "" : "Paste the reply.");
    if (!f.text.value.trim()) return;
    const id = form.dataset.id;
    write("reply", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(id)}/outreach/reply`, { text: f.text.value.trim(), on: f.on.value }), () => {
      resetUi();
      ctx.toast("Reply recorded; the lead is Active again.", { link: { href: href("qualification", { tab: "active", id }), label: "Open it" } });
      ctx.go("qualification", { tab: "parked", team: ctx.route.params.team });
    });
  },
};

// The question each condition's tick adds to the reply, in the client's terms (the rules' questions are written for
// the consultant). Shown as written; the person edits the reply before copying it.
const ASKS = {
  problem: "Could you tell me where this hurts most day to day?",
  fit: "Which part of this would you want help with first?",
  external: "Would it help to have someone from outside look at it with you?",
  next: "Would a 30-minute call next week work, so we can go through it together?",
  commercial: "Is there budget set aside for this, and who else would weigh in on the decision?",
  conflict: "Is anyone else already helping you with this?",
};
