// Outreach (design/redesign/Companies.dc.html, view "outreach"): Step 5 (first email, contact plans, follow-ups) up to
// the first reply. Agents draft; a person approves the exact email and address before anything is sent.
//
// Each lead is in one place, by who has the next move: Needs you (the person acting), With a teammate (someone else:
// the owner, or the second approver), Agent drafting (a plan step the outreach agent prepares when it is due), Waiting
// for a reply (the client), Parked (the review date). Mine shows the person's own leads; Whole team shows everyone's.
// Needs you is a review queue in three columns; the other stages are tables with a side panel.
import { html, icon, day, addDays, daysBetween, href, plural, initials, sourceLink, linkedinSearch, safeUrl } from "../lib.js";
import { enc } from "../api.js";
import { STAGE_LABELS } from "../options.js";
import { emptyState, inlineError, crmLinks, crmConflicts, resolveCrm, drawer, panelActions } from "../shell.js";
import { UNDO_SECONDS, offerUndo, undoEmail, waitingNotice, emailWaits, undoEmailAct, sendNowAct, secondFormAct } from "../undo.js";

export const title = "Outreach";

// The stage cards, as on Companies (design: Companies.dc.html stageBar): who has the move, and its line under the name.
const TABS = [
  ["you", "Needs you", "you", "Review, approve or decide"],
  ["mate", "With a teammate", "none", "A colleague has the next move"],
  ["agent", "Agent drafting", "agent", "Drafted when the step is due"],
  ["wait", "Waiting for a reply", "none", "The client has the next move"],
];
// Tab names before the redesign. "talking" (In conversation) is on Qualification now; its old links open Waiting.
// Parked (nurture) is on Qualification (D2): load() sends those links there.
const TAB_ALIAS = { approve: "you", waiting: "wait", talking: "wait" };
const MOVED = ["nurture", "parked"];
const STAGES = ["identified", "contacted", "nurture"];
const PURPOSE = { first: "First email", followup: "Follow-up email", reconnect: "Reconnect email" };
const CHANNELS = ["Email", "LinkedIn message", "Phone call", "Other"];
const REVISE_HINTS = ["Make it shorter.", "Use a warmer opening.", "Lead with the most recent news.", "Ask for a 20-minute call next week."];
const KIND_LABEL = {
  reply: "Client replied", approve2: (x) => `Approve for ${x.by}`, draft: (x, me) => (x.opp.owner === me ? "Your draft to review" : `${x.opp.owner}'s draft`),
  first: "First email", plan: "Decide the contact plan", step: "Plan step to do", agentstep: "Agent drafts it", wait: "Waiting for a reply", parked: "Parked",
};

// What the person has open. Cleared when they pick another item.
const ui = { mode: null, key: null, error: null, notice: null, thread: null };

function resetUi(key = null) {
  ui.mode = null; ui.key = key; ui.error = null; ui.notice = null; ui.thread = null;
}

// ---------- work items ----------
// Drafts the agents put in the inbox, and accepted leads without a first-email draft item (tests/v2/test_frontend.py).
export function approveItems(opps, inbox) {
  const byId = Object.fromEntries(opps.map((o) => [o.id, o]));
  const items = [];
  for (const it of inbox) {
    const o = byId[it.opportunityId];
    if (!o || it.status !== "new") continue;
    if (it.kind === "draft") items.push({ key: `item-${it.id}`, kind: "draft", item: it, opp: o });
  }
  const firstDraftFor = new Set(items.filter((x) => x.kind === "draft" && x.item.data.purpose === "first").map((x) => x.opp.id));
  for (const o of opps.filter((x) => x.stage === "identified" && !firstDraftFor.has(x.id))) {
    items.push({ key: `opp-${o.id}`, kind: "first", opp: o });
  }
  return items;
}

// Drafts to approve plus client replies to record (the count before the redesign; kept for its test).
export function waitingFor(opps, inbox) {
  const replies = inbox.filter((it) => it.kind === "reply" && it.status === "new" && opps.some((o) => o.id === it.opportunityId && ["contacted", "nurture"].includes(o.stage)));
  return approveItems(opps, inbox).length + replies.length;
}

// One item per lead: what it waits for now, and whose move it is. In order of what comes first when several apply.
export function leadItems(opps, inbox, details, today) {
  const replies = Object.fromEntries(inbox.filter((it) => it.kind === "reply" && it.status === "new").map((it) => [it.opportunityId, it]));
  const drafts = approveItems(opps, inbox);
  const out = [];
  for (const o of opps) {
    const det = details[o.id] || {};
    const r = det.outreach || {};
    const w = r.waiting;
    const pl = r.plan;
    const cur = pl && pl.running ? (pl.plan.steps || []).find((s) => s.n === pl.current) : null;
    const draft = drafts.find((x) => x.kind === "draft" && x.opp.id === o.id);
    let x;
    if (replies[o.id]) x = { kind: "reply", item: replies[o.id], who: o.owner };
    else if (w && w.status === "approval") x = { kind: "approve2", out: w, by: w.by, who: w.approver };
    else if (draft) x = { kind: "draft", item: draft.item, who: o.owner };
    else if (pl && pl.status === "proposed") x = { kind: "plan", who: o.owner };
    else if (cur && (cur.status === "ready" || (cur.due && cur.due <= today))) x = { kind: "step", step: cur, who: o.owner };
    else if (o.stage === "identified") x = { kind: "first", who: o.owner };
    else if (cur) x = { kind: "agentstep", step: cur, who: "agent" };
    else if (o.stage === "nurture") x = { kind: "parked", who: o.owner };
    else x = { kind: "wait", who: "client" };
    out.push({ ...x, key: o.id, opp: o, det });
  }
  return out;
}

function bucket(x, me, team) {
  const mine = x.opp.owner === me;
  if (["reply", "approve2", "draft", "plan", "step", "first"].includes(x.kind)) {
    if (x.who === me) return "you";
    return team || mine || (x.kind === "approve2" && x.by === me) ? "mate" : null;
  }
  if (!team && !mine) return null;
  return { agentstep: "agent", wait: "wait" }[x.kind] || null;  // parked leads are on Qualification
}

// ---------- data ----------
let rubric = null; // the CGS email standard: criteria labels and word ranges (Settings, Email standard)
let people = null; // who approves whose emails a second time

export async function load(ctx, params) {
  if (MOVED.includes(params.tab)) {
    ctx.go("qualification", { tab: "parked", id: params.id, team: params.team });
    return { moved: true };
  }
  const team = params.team === "1";
  const [opps, inbox, toolkit] = await Promise.all([
    ctx.api.get(`/opportunities?${STAGES.map((s) => `stage=${s}`).join("&")}`),
    ctx.api.get("/inbox"),
    ctx.api.get("/toolkit").catch(() => ({ mail: { provider: "off", ready: false } })),
  ]);
  if (!rubric) rubric = await ctx.api.get("/email-rubric").then((x) => x.current).catch(() => null);
  if (!people) people = await ctx.api.get("/people").then((x) => x.people).catch(() => []);
  const details = Object.fromEntries((await Promise.all(opps.map((o) => detailOf(ctx, o.id)))).filter(Boolean).map((x) => [x.id, x]));
  const all = leadItems(opps, inbox, details, ctx.today);
  const lists = Object.fromEntries(TABS.map(([t]) => [t, []]));
  for (const x of all) { const b = bucket(x, ctx.me, team); if (b) lists[b].push(x); }
  lists.you.sort((a, b) => order(a) - order(b));

  // A link names a lead (id) or an inbox item (item): open the stage that holds it.
  const wantedId = params.item ? (inbox.find((it) => it.id === params.item) || {}).opportunityId : params.id;
  let tab = TAB_ALIAS[params.tab] || params.tab;
  if (!TABS.some(([t]) => t === tab)) tab = null;
  if (wantedId && (!tab || !lists[tab].some((x) => x.key === wantedId))) tab = TABS.map(([t]) => t).find((t) => lists[t].some((x) => x.key === wantedId)) || tab;
  tab = tab || (lists.you.length ? "you" : "wait");
  let id = wantedId && lists[tab].some((x) => x.key === wantedId) ? wantedId : undefined;
  if (tab === "you" && !id && lists.you.length) id = lists.you[0].key;
  if (params.tab !== tab || params.id !== id || params.item) ctx.replace("outreach", { tab, id, team: team ? "1" : undefined });

  const data = { tab, team, lists, inbox, id, mail: toolkit.mail || { provider: "off", ready: false } };
  const cur = id && lists[tab].find((x) => x.key === id);
  if (cur) {
    if (ui.key !== cur.key) resetUi(cur.key);
    data.cur = cur;
    Object.assign(data, await itemData(ctx, cur));
  }
  return data;
}
const ORDER = ["reply", "approve2", "draft", "step", "plan", "first"];
const order = (x) => ORDER.indexOf(x.kind);

// The opportunity in full, kept for a minute so the queue and the panel draw at once; the full load checks again.
const details = new Map();
const TTL = 60 * 1000;
async function detailOf(ctx, id) {
  try {
    const x = await ctx.api.get(`/opportunities/${enc(id)}`);
    details.set(id, { at: Date.now(), value: x });
    return x;
  } catch { return null; }
}

// One item's extras: other CGS work with it, and the email preview when there is a draft.
const extras = new Map();
async function itemData(ctx, cur) {
  const id = cur.opp.id;
  const detail = cur.det && cur.det.id ? cur.det : await ctx.api.get(`/opportunities/${enc(id)}`);
  const related = await ctx.api.get(`/opportunities/${enc(id)}/related`).catch(() => null);
  const out = { detail, related, preview: undefined };
  const firstDraft = cur.kind === "first" && detail.outreach.status === "draft";
  if (cur.kind === "draft" || firstDraft) {
    const q = cur.kind === "draft" ? `?itemId=${enc(cur.item.id)}` : "";
    out.preview = await ctx.api.get(`/opportunities/${enc(id)}/outreach/email${q}`).catch((e) => ({ error: e.message }));
  }
  extras.set(`${cur.kind}:${cur.key}`, { at: Date.now(), value: out });
  return out;
}

export function instant(ctx, params, last) {
  const tab = TAB_ALIAS[params.tab] || params.tab;
  if (!last || last.moved || tab !== last.tab || (params.team === "1") !== last.team) return null;
  if (!params.id) return tab === "you" ? null : { ...last, id: undefined, cur: null };
  const cur = last.lists[tab].find((x) => x.key === params.id);
  const hit = cur && extras.get(`${cur.kind}:${cur.key}`);
  if (!hit || Date.now() - hit.at > TTL) return null;
  if (ui.key !== cur.key) resetUi(cur.key);
  return { ...last, id: params.id, cur, ...hit.value };
}

export function changed(early, d) {
  const pick = (x) => JSON.stringify([x.tab, x.cur && x.cur.key, x.cur && x.cur.kind, x.detail, x.preview, TABS.map(([t]) => ((x.lists || {})[t] || []).map((i) => `${i.key}:${i.kind}`))]);
  return pick(early) !== pick(d);
}

// After drawing: the extras of the other items in the queue, so picking one shows it at once.
export function after(root, ctx, d) {
  if (!keysOn) { root.addEventListener("keydown", queueKey); keysOn = true; }
  if (d.moved) return;
  for (const cur of (d.lists[d.tab] || []).slice(0, 12)) {
    const hit = extras.get(`${cur.kind}:${cur.key}`);
    if (!hit || Date.now() - hit.at > TTL) itemData(ctx, cur).catch(() => null);
  }
}

// ---------- view ----------
export function view(d, ctx) {
  if (d.moved) return html`<p class="meta">Parked leads are under Qualification, Parked.</p>`;
  const link = (tab, extra = {}) => href("outreach", { tab, team: d.team ? "1" : undefined, ...extra });
  return html`
  <div class="page-head">
    <div>
      <h1>Outreach</h1>
      <p>Contact plans and emails before the first reply. Nothing is sent until a person approves it. In Insightly a lead is Identified until its first email goes out, then Contacted.</p>
    </div>
    <div class="head-side">
      <span class="legend"><span><span class="dot wait" aria-hidden="true"></span>Waiting on you</span>
        <span><span class="dot agent" aria-hidden="true"></span>An agent works on it</span></span>
      <div class="segmented" role="group" aria-label="Whose leads">
        <a href="${href("outreach", { tab: d.tab })}" aria-current="${d.team ? "false" : "true"}">Mine</a>
        <a href="${href("outreach", { tab: d.tab, team: "1" })}" aria-current="${d.team ? "true" : "false"}">Whole team</a>
      </div>
      ${d.mail.ready ? html`<button type="button" class="btn" data-act="syncMail">${icon("refresh", 16)}Check email now</button>` : ""}
    </div>
  </div>
  ${mailNotice(d.mail)}
  <div class="stages" role="tablist" aria-label="Stages" data-act="tabkeys" data-on="keydown">
    ${TABS.map(([id, label, who, line]) => html`
      <button type="button" role="tab" class="stg" id="tab-${id}" aria-selected="${d.tab === id ? "true" : "false"}" aria-controls="panel"
        tabindex="${d.tab === id ? "0" : "-1"}" data-act="tab" data-tab="${id}">
        <span class="who ${who}" aria-hidden="true"></span>
        <span class="n">${d.lists[id].length}</span>
        <span class="${who === "you" ? "label you-text" : "label"}">${label}</span>
        <span class="crm">${line}</span>
      </button>`)}
  </div>
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${d.tab === "you" ? queueView(d, ctx, link) : tableView(d, ctx, link)}
  </div>
  ${d.tab !== "you" && d.cur ? panel(d, ctx, link) : ""}`;
}

// Gmail signed out is a workspace problem: the red banner at the top says so (shell.js problems), not this notice.
const gmailOff = (mail) => mail.provider === "gmail" && !mail.ready;
function mailNotice(mail) {
  if (mail.ready || gmailOff(mail)) return "";
  const why = mail.provider === "off" ? "Email is off in this workspace" : `The ${mail.provider} mailbox is not ready`;
  return html`<div class="notice" style="margin-bottom:20px">${icon("alert")}<span>${why}, so the workspace cannot send. Send from your own email or phone, then record what you sent here.</span></div>`;
}

const STAGE_NOTE = {
  mate: "Waiting on a colleague: the owner still has to review the draft, or the second approver has it.",
  agent: "Nothing to do yet. The Outreach agent drafts each plan step when it is due, then it comes to Needs you.",
  wait: "Sent and waiting. A plan stops the moment the client replies, and the lead moves to Qualification.",
};
const EMPTY = {
  you: ["Nothing needs you right now", "When you accept a company, the Outreach agent drafts the first email or a contact plan and it appears here."],
  mate: ["Nothing waits on a colleague", "Leads show here while a colleague has the next move, for example a second approval."],
  agent: ["No plan step is being drafted", "Approved contact plans show their next step here until it is due."],
  wait: ["No one to wait for", "Leads move here when the first message goes out."],
};

// ----- the plan: where a lead is, as dots and words -----
const CHANNEL = {
  email: "Email", linkedin_connect: "LinkedIn request", linkedin_message: "LinkedIn message", share_content: "Share a CGS piece",
  invite: "Invitation", warm_intro: "Introduction", call: "Phone call",
};
const ROUTE = { warm: ["Warm route", "good"], value_first: ["Value first", ""], direct: ["Direct", ""] };
function planOf(det) {
  const pl = det && det.outreach && det.outreach.plan;
  if (!pl || pl.status !== "approved" || !pl.plan) return null;
  const steps = pl.plan.steps || [];
  const cur = steps.find((s) => s.n === pl.current);
  const last = steps[steps.length - 1];
  return { pl, steps, cur, length: last ? last.day : 0, at: cur ? cur.day : last ? last.day : 0 };
}
function planDots(det) {
  const p = planOf(det);
  if (!p) return html`<span class="meta-3">No plan</span>`;
  return html`<span class="plan-dots" title="${p.cur ? `Day ${p.at} of ${p.length}` : p.pl.running ? "" : "Plan stopped"}"><span class="pd-row" aria-hidden="true">${p.steps.map((s) =>
    html`<span class="pd ${s.status === "done" || s.status === "skipped" ? "done" : p.cur && s.n === p.cur.n ? "now" : ""}"></span>`)}</span>
    <span class="meta-3">${p.cur ? `Day ${p.at} of ${p.length}` : p.pl.running ? "Done" : "Stopped"}</span></span>`;
}
function routeTag(det) {
  const pl = det && det.outreach && det.outreach.plan;
  const r = pl && pl.plan && ROUTE[pl.plan.approach];
  return r ? html`<span class="tag ${r[1]}">${r[0]}</span>` : html`<span class="meta-3">Single email</span>`;
}

// ----- the tables (With a teammate, Agent drafting, Waiting for a reply, Parked) -----
function tableView(d, ctx, link) {
  const list = d.lists[d.tab];
  if (!list.length) return html`<section class="card">${emptyState(...EMPTY[d.tab])}</section>`;
  return html`
  <section class="card" aria-labelledby="out-h">
    <div class="card-head"><div><h2 id="out-h">${TABS.find(([t]) => t === d.tab)[1]} <span class="cnt">${list.length}</span></h2><p class="meta">${STAGE_NOTE[d.tab]}</p></div></div>
    <div class="table-wrap"><table class="list rows-table">
      <thead><tr><th scope="col">Company</th><th scope="col">Contact</th><th scope="col">Next step</th><th scope="col">When</th><th scope="col">Route</th><th scope="col">Plan</th><th scope="col">Status</th></tr></thead>
      <tbody>${list.map((x) => {
        const o = x.opp;
        const next = nextStep(x, ctx);
        return html`<tr class="${x.key === d.id ? "on" : ""}">
          <td><span class="co"><span class="avatar" aria-hidden="true">${initials(o.company)}</span><span class="co-text">
            <a class="nm" href="${link(d.tab, { id: x.key })}" data-act="openRow" data-row="${x.key}">${o.company}</a><span class="meta-3">${o.owner ? `Owner: ${o.owner}` : "No owner"}</span></span></span></td>
          <td><span class="cell-main">${o.contact.name || "To confirm"}</span><span class="meta-3">${o.contact.title}</span></td>
          <td>${next.what}</td>
          <td><span class="${next.late ? "you-text md" : "meta"}">${next.when}</span></td>
          <td>${routeTag(x.det)}</td>
          <td>${planDots(x.det)}</td>
          <td>${statusTag(x, ctx, d.mail)}</td></tr>`;
      })}</tbody>
    </table></div>
  </section>`;
}

function nextStep(x, ctx) {
  const o = x.opp;
  const p = planOf(x.det);
  const due = p && p.cur && p.cur.due ? p.cur.due : o.next.due;
  const what = p && p.cur ? `${CHANNEL[p.cur.channel] || p.cur.channel} to ${p.cur.to}` : x.kind === "parked" ? "Review" : o.next.action || "None set";
  const left = due ? daysBetween(ctx.today, due) : null;
  return { what, when: due ? (left === 0 ? "Today" : left < 0 ? `${plural(-left, "day")} late` : day(due, ctx.today)) : "No date", late: left !== null && left <= 0 };
}

function statusTag(x, ctx, mail) {
  // With Gmail signed out, a row whose next move is an email says it cannot go out, and a sent one that its reply is not read.
  if (mail && gmailOff(mail)) {
    const email = ["draft", "first", "approve2"].includes(x.kind) || (["step", "agentstep"].includes(x.kind) && x.step && x.step.channel === "email");
    if (email) return html`<span class="tag bad">Can't send, Gmail off</span>`;
    if (x.kind === "wait") return html`<span class="tag bad">Replies not read</span>`;
  }
  if (x.kind === "approve2") return html`<span class="tag warn">With ${x.who} for approval</span>`;
  if (["draft", "first", "plan", "step", "reply"].includes(x.kind)) return html`<span class="tag warn">${x.who === ctx.me ? "Needs you" : `With ${x.who || "no owner"}`}</span>`;
  if (x.kind === "agentstep") return html`<span class="tag info">Agent drafts it${x.step && x.step.due ? ` ${day(x.step.due, ctx.today)}` : ""}</span>`;
  if (x.kind === "parked") return html`<span class="tag">Parked until ${x.opp.next.due ? day(x.opp.next.due, ctx.today) : "no date"}</span>`;
  return html`<span class="tag">Stops when they reply</span>`;
}

// ----- Needs you: the review queue in three columns -----
function queueView(d, ctx, link) {
  const list = d.lists.you;
  if (!list.length) return html`<section class="card">${emptyState(...EMPTY.you, { href: href("companies", { tab: "decision" }), label: "Open Your decision" })}</section>`;
  const c = content(d, ctx);
  return html`
  <div class="review three">
    <nav class="card queue" aria-label="Needs you">
      <div class="queue-head">Needs you</div>
      ${list.map((x) => {
        const sub = typeof KIND_LABEL[x.kind] === "function" ? KIND_LABEL[x.kind](x, ctx.me) : KIND_LABEL[x.kind];
        return html`<a class="pick" href="${link("you", { id: x.key })}" ${d.cur && x.key === d.cur.key ? html`aria-current="true"` : ""}>
          <span class="avatar" aria-hidden="true">${initials(x.opp.company)}</span>
          <span><b>${x.opp.company}</b><span class="meta-3 you-text md">${sub}</span></span></a>`;
      })}
      <div class="queue-foot"><span class="keys">J</span><span class="keys">K</span>to move</div>
    </nav>
    <section class="card detail mid" aria-labelledby="detail-h">${c.main}</section>
    <aside class="card side-col" aria-label="Check and decide">${c.side}</aside>
  </div>`;
}

// The selected lead: { main, side } for the queue, { blocks, foot } for the panel.
function content(d, ctx) {
  const k = d.cur.kind;
  if (k === "first") return firstEmail(d, ctx);
  if (k === "draft") return agentDraft(d, ctx);
  if (k === "reply") return replyItem(d, ctx);
  if (k === "approve2") return secondApproval(d, ctx);
  if (k === "plan" || k === "step" || k === "agentstep") return planItem(d, ctx);
  return waiting(d, ctx);
}

function panel(d, ctx, link) {
  const list = d.lists[d.tab];
  const i = list.findIndex((x) => x.key === d.cur.key);
  const near = (n) => (list[i + n] ? { href: link(d.tab, { id: list[i + n].key }), id: list[i + n].key } : null);
  const c = content(d, ctx);
  const o = d.cur.opp;
  return drawer({
    name: o.company, sub: `${o.contact.name || "Contact to confirm"}${o.contact.title ? `, ${o.contact.title}` : ""}`,
    stage: STAGE_LABELS[o.stage], tone: o.stage === "nurture" ? "" : "info", owner: o.owner ? `Owner: ${o.owner}` : "No owner",
    pos: `${i + 1} of ${list.length}`, prev: near(-1), next: near(1), record: o.id,
    blocks: html`<section class="card detail">${c.main}</section>${c.side ? html`<section class="card side-col in-panel">${c.side}</section>` : ""}`,
    foot: "",
  });
}

// ----- shared pieces -----
function header(heading, sub, extra) {
  return html`<div class="block"><h2 id="detail-h">${heading}</h2><p class="meta">${sub}</p>${extra || ""}</div>`;
}

function contactLine(o) {
  const c = o.contact;
  return `${c.title || "Role not known"}, ${o.company.name}${o.owner ? `. Owner: ${o.owner}` : ""}`;
}

// The company, its route and why it is worth writing to, from the brief.
function leadHead(o, heading, extra) {
  const b = o.brief || {};
  const why = String(b.hypothesis || "").split(/(?<=[.!?])\s+/).slice(0, 2).join(" ");
  return html`
  <div class="block">
    <div class="row-flex" style="flex-wrap:nowrap;align-items:flex-start">
      <span class="avatar lg ink" aria-hidden="true">${initials(o.company.name)}</span>
      <div style="flex:1;min-width:0"><h2 id="detail-h">${heading}</h2><p class="meta">${contactLine(o)}</p></div>
      ${routeTag(o)}
    </div>
    ${why ? html`<div class="why-box"><span class="lbl">Why this company, from the brief</span><span>${why}</span></div>` : ""}
    ${extra || ""}
  </div>`;
}

// What was sent so far: the plan's done steps and the first email, folded.
function threadBlock(o, ctx) {
  const p = planOf(o);
  const done = p ? p.steps.filter((s) => s.status === "done" || s.status === "skipped") : [];
  const r = o.outreach || {};
  const rows = [...done.map((s) => ({ when: s.doneOn, title: CHANNEL[s.channel] || s.channel, line: `${s.status === "skipped" ? "Skipped" : "Done"}${s.note ? `: ${s.note}` : ""}` })),
    ...(r.sentOn && !done.some((s) => s.channel === "email") ? [{ when: r.sentOn, title: r.status === "followup" ? "Follow-up email" : "First email", line: r.subject || "" }] : [])];
  if (!rows.length) return "";
  const open = ui.thread === true;
  const first = o.contact.name ? o.contact.name.split(" ")[0] : "the contact";
  return html`<div class="block">
    <button type="button" class="btn ghost small" data-act="thread" aria-expanded="${open ? "true" : "false"}">${icon("chevron", 14)}${open ? "Hide" : "Show"} the ${plural(rows.length, "earlier message")} to ${first}</button>
    ${open ? html`<ul class="thread">${rows.map((x) => html`<li><span class="md">${x.title}</span><span class="meta">${x.line}</span><span class="meta-3">${day(x.when, ctx.today)}</span></li>`)}</ul>` : ""}
  </div>`;
}

function contactBlock(o) {
  const c = o.contact;
  const confirmed = c.confirmedBy ? html`<span class="tag good">${icon("check", 14)}Confirmed by ${c.confirmedBy}</span>` : html`<span class="tag warn">Not confirmed by a person</span>`;
  const panelOpen = ui.mode === "contact";
  return html`
  <div class="block stack">
    <div class="row-flex"><h3 class="sub-title" style="margin:0">Contact</h3>${confirmed}${crmLinks(o.crm)}</div>
    ${crmConflicts(o.id, o.crm)}
    <p>${c.name || "No person named yet"}${c.name ? html`, <span class="meta">${c.title}</span>` : ""}
      <a class="meta-3" style="margin-left:8px" href="${linkedinSearch([c.name || (c.title === "Contact to confirm" ? "" : c.title), o.company.name])}" target="_blank" rel="noopener noreferrer">Search on LinkedIn</a></p>
    <p class="meta">${c.email ? `Email: ${c.email}` : "No email address on file. Agents never guess one; add it when you send."}</p>
    ${panelOpen ? html`
    <form class="panel-form" data-act="contactForm" data-on="submit input" data-id="${o.id}">
      <div class="form-grid">
        <label class="field">Name<input name="name" value="${c.name || ""}" required></label>
        <label class="field">Role<input name="title" value="${c.title || ""}"></label>
      </div>
      <label class="field">Where you checked the name and role <span class="hint">For example: company website, checked today; or a phone call with reception.</span>
        <input name="note" required></label>
      <div class="actions">
        <button type="submit" class="btn primary" disabled>Confirm contact</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing>${c.name ? "Say where you checked it." : "Give the name."}</span>
      </div>
      ${inlineError(ui.error && ui.error.where === "contact" ? ui.error.message : "")}
    </form>` : html`<div><button type="button" class="btn small" data-act="mode" data-mode="contact">${c.confirmedBy ? "Correct the contact…" : "Confirm the contact…"}</button></div>`}
  </div>`;
}

function relatedBlock(rel) {
  if (!rel || !(rel.other_opportunities || []).length) return "";
  const recent = rel.same_person_emailed_recently || [];
  return html`<div class="block"><div class="notice ${recent.length ? "warn" : ""}">${icon("alert")}<span>
    ${recent.length ? "This person was emailed recently in another CGS opportunity." : "Other CGS work with this company or person:"}
    <ul class="bullets">${rel.other_opportunities.map((x) => html`<li>${x.company}: ${x.title} (${STAGE_LABELS[x.stage] || x.stage}, ${x.owner || "no owner"})${x.last_email ? `. Emailed ${x.last_email.days_ago} days ago: "${x.last_email.subject}"` : ""}</li>`)}</ul>
  </span></div></div>`;
}

// Words in a draft, without the citation marks the client never sees.
const plain = (body) => String(body || "").replace(/\[\[[^|\]]+\|([^\]]+)\]\]/g, "$1");
const words = (body) => (plain(body).trim() ? plain(body).trim().split(/\s+/).length : 0);

// Draft text with citation markers [[key|phrase]]: the phrase is highlighted and numbered; sources listed below.
// canEdit: the text itself opens the editor (the person's own draft).
function draftBody(subject, body, facts, extra = {}) {
  const notes = [];
  const byId = Object.fromEntries((facts || []).map((f) => [f.id, f]));
  const paragraphs = String(body || "").split(/\n{2,}/).map((para) => {
    const parts = [];
    let last = 0;
    for (const m of para.matchAll(/\[\[([^|\]]+)\|([^\]]+)\]\]/g)) {
      parts.push(lines(para.slice(last, m.index)));
      let n = notes.findIndex((x) => x.key === m[1]) + 1;
      if (!n) { notes.push({ key: m[1], fact: byId[m[1]] }); n = notes.length; }
      const f = byId[m[1]];
      parts.push(html`<mark class="cite" title="${f ? `${f.text} (${f.src}${f.date ? `, ${f.date}` : ""})` : `CGS library: ${m[1]}`}">${m[2]}</mark><sup class="cite-n">${n}</sup>`);
      last = m.index + m[0].length;
    }
    parts.push(lines(para.slice(last)));
    return html`<p>${parts}</p>`;
  });
  return html`
  <div class="block">
    <dl class="email-head">
      ${extra.from ? html`<dt>From</dt><dd>${extra.from}</dd>` : ""}
      ${extra.to !== undefined ? html`<dt>To</dt><dd>${extra.to || html`<span class="meta">Address needed: add it when you approve</span>`}${extra.confirmed ? html` <span class="tag good">Confirmed</span>` : ""}</dd>` : ""}
      <dt>Subject</dt><dd><b>${subject || "(no subject)"}</b></dd>
    </dl>
    ${extra.canEdit ? html`<div class="email-body can-edit" data-act="mode" data-mode="${extra.editMode}" title="Click to edit">${paragraphs}</div>
      <p class="meta-3"><button type="button" class="btn small" data-act="mode" data-mode="${extra.editMode}">Edit</button> or click the text. Highlighted words come from a source; hover to see it.</p>`
      : html`<div class="email-body">${paragraphs}</div>`}
    ${notes.length ? html`<ol class="sources">${notes.map((x) => html`<li>${x.fact ? html`${x.fact.text} <span class="meta-3">${sourceLink(x.fact.src, x.fact.date)}</span>` : `CGS library: ${x.key}`}</li>`)}</ol>` : ""}
    ${extra.checks ? checksRow(notes, extra) : ""}
  </div>`;
}

// What the page can verify about a draft. Nothing here is claimed without a check.
function checksRow(notes, extra) {
  const unsourced = notes.filter((x) => !x.fact && !/^[a-z]/i.test(x.key)).length;
  const fromBrief = notes.filter((x) => x.fact).length;
  const items = [
    notes.length ? [unsourced === 0, fromBrief ? `${fromBrief} cited ${fromBrief === 1 ? "fact" : "facts"} from the research brief` : "Cites the CGS library"] : [false, "No citations: check every fact yourself"],
    [!!extra.confirmed, extra.confirmed ? "Contact confirmed by a person" : "Contact not confirmed by a person yet"],
  ];
  if (extra.blocked) items.push([false, extra.blocked]);
  return html`<ul class="checks">${items.map(([ok, text]) => html`<li class="${ok ? "good-text" : "warn-text"}">${icon(ok ? "check" : "alert", 16)}${text}</li>`)}</ul>`;
}

// The CGS email standard (Settings, Email standard): the reviewer's score per line and its weighted mean, and the
// length, counted here. Without a review, it says so.
function standardBlock(body, review, tone = "standard") {
  const range = rubric && rubric.length && rubric.length[tone];
  const n = words(body);
  const longOk = !range || (n >= range[0] && n <= range[1]);
  const label = Object.fromEntries(((rubric && rubric.criteria) || []).map((c) => [c.key, c.label]));
  const bar = rubric && rubric.bar ? rubric.bar.each : 3;
  return html`<div class="side-sec">
    <div class="sb"><h3>CGS email standard</h3>${review && review.scores ? html`<span><span class="n3">${review.weighted}</span> <span class="meta-3">of 5</span></span>` : ""}</div>
    ${review && review.scores ? html`<ul class="std">${Object.entries(review.scores).map(([k, v]) => html`<li><span>${label[k] || k}</span><span class="${v >= bar ? "good-text" : "warn-text"} md">${v} of 5</span></li>`)}
      <li><span>Length</span><span class="${longOk ? "good-text" : "warn-text"} md">${n} words${range ? ` (${range[0]}-${range[1]})` : ""}</span></li>
      ${review.unsupported_claim ? html`<li><span>Claims</span><span class="warn-text md">One is not in the facts</span></li>` : ""}</ul>
      <p class="meta-3">${review.meets ? `Meets standard ${review.standard}` : `Below standard ${review.standard}`}${review.rounds ? `, after ${plural(review.rounds, "revision")}` : ""}.${!review.meets && review.fixes && review.fixes.length ? ` Reviewer: ${review.fixes[0]}` : ""}</p>`
    : html`<ul class="std"><li><span>Length</span><span class="${longOk ? "good-text" : "warn-text"} md">${n} words${range ? ` (${range[0]}-${range[1]})` : ""}</span></li></ul>
      <p class="meta-3">${review && review.error ? `Not reviewed: ${review.error}` : "Not reviewed against the standard. Check it yourself."}</p>`}
  </div>`;
}

// Where this message sits in the contact plan, and what happens with no reply.
function planSide(o, ctx, n) {
  const p = planOf(o);
  if (!p) return html`<div class="side-sec"><h3>Where this sits in the plan</h3><p class="meta">No contact plan: this is a single ${o.stage === "identified" ? "first email" : "message"}.${o.next.action ? ` Next: ${o.next.action}${o.next.due ? `, ${day(o.next.due, ctx.today)}` : ""}.` : ""}</p></div>`;
  const here = n || (p.cur && p.cur.n);
  const i = p.steps.findIndex((s) => s.n === here);
  const after = i >= 0 ? p.steps[i + 1] : null;
  const first = o.contact.name ? o.contact.name.split(" ")[0] : "the contact";
  return html`<div class="side-sec">
    <div class="sb"><h3>Where this sits in the plan</h3><span class="meta-3">${p.cur ? `Day ${p.at} of ${p.length}` : "Plan done"}</span></div>
    <ol class="rl">${p.steps.map((s) => {
      const done = s.status === "done" || s.status === "skipped";
      const now = s.n === here;
      return html`<li class="${done ? "done" : ""}"><span class="d ${done ? "done" : now ? "you" : ""}" aria-hidden="true"></span>
        <span><span class="${now ? "you-text md" : "meta-3"}" style="display:block">Day ${s.day}${done ? `, ${s.status}` : now ? ", this message" : ""}</span>
        <span class="${now ? "md" : ""}">${CHANNEL[s.channel] || s.channel} to ${s.to}</span></span></li>`;
    })}</ol>
    <p class="meta">${after ? `If ${first} does not reply: ${(CHANNEL[after.channel] || after.channel).toLowerCase()} on day ${after.day}.` : "This is the last step. With no reply, the plan ends."}</p>
  </div>`;
}

// Who approves after this person, from Settings > People.
function approverLine(writer, ctx) {
  const p = (people || []).find((x) => x.name === writer);
  if (!p || !p.secondApprover) return "";
  return html`<p class="meta">${writer === ctx.me ? "You approve first" : `${writer} approves first`}; then ${p.secondApprover} approves it. Nothing is sent before then.</p>`;
}

function lines(text) {
  return text.split("\n").map((l, i) => (i ? html`<br>${l}` : l));
}

// The exact email and address a person approves. "shown" is what the preview returned when the page was drawn.
function sendPanel(d, { itemId, hash } = {}) {
  const p = d.preview || {};
  if (p.error) return html`<div class="block">${inlineError(p.error)}</div>`;
  const err = ui.error && ui.error.where === "send" ? ui.error.message : "";
  return html`
  <div class="block">
    <form class="panel-form" data-act="sendForm" data-on="submit input" data-id="${d.detail.id}" data-item="${itemId || ""}" data-hash="${hash || ""}">
      <h3 class="sub-title">Approve and send</h3>
      <p class="meta">This is exactly what the client receives: citation marks are removed. ${d.mail.ready ? `It goes out from the ${d.mail.provider} mailbox ${UNDO_SECONDS} seconds after you approve it, so you can still undo it.` : ""}</p>
      ${p.blocked ? html`<div class="notice bad">${icon("alert")}<span>${p.blocked}</span></div>` : ""}
      ${p.from ? html`<p class="meta from-line">${p.from.note}</p>` : ""}
      <label class="field">To <span class="hint">The client's address. Check it: agents never guess one.</span>
        <input type="email" name="to" value="${p.to || ""}" required autocomplete="off"></label>
      <div class="email-plain"><b>${p.subject}</b>\n\n${p.body}</div>
      <div class="actions">
        ${d.mail.ready ? html`
          <button type="submit" class="btn primary" name="how" value="send" ${p.blocked || !p.to ? "disabled" : ""}>Approve and send</button>
          ${itemId ? "" : html`<button type="submit" class="btn" name="how" value="draft" ${p.blocked || !p.to ? "disabled" : ""}>Put it in my Gmail drafts</button>`}`
        : html`<span class="meta">Email is off here. Send it yourself, then use "Record as sent".</span>`}
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing>${p.blocked ? "Fix the draft first." : p.to ? "" : "Add the client's address."}</span>
      </div>
      ${inlineError(err)}
    </form>
  </div>`;
}

function nurturePanel(o, ctx) {
  return html`
  <div class="block">
    <form class="panel-form" data-act="nurtureForm" data-on="submit input" data-id="${o.id}">
      <h3 class="sub-title">Park the lead</h3>
      <p class="meta">The lead leaves the active lists until the review date. The agents may draft a reconnect email then. In Insightly it shows as Nurture.</p>
      <div class="form-grid">
        <label class="field">Reason<input name="reason" required placeholder="For example: budget is set for 2027"></label>
        <label class="field">Review on<input type="date" name="review_on" value="${addDays(ctx.today, 60)}" min="${ctx.today}" required></label>
      </div>
      <div class="actions">
        <button type="submit" class="btn primary" disabled>Park until then</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing>Give a reason.</span>
      </div>
      ${inlineError(ui.error && ui.error.where === "nurture" ? ui.error.message : "")}
    </form>
  </div>`;
}

function recordSentPanel(o, ctx) {
  return html`
  <div class="block">
    <form class="panel-form" data-act="recordSentForm" data-on="submit" data-id="${o.id}">
      <h3 class="sub-title">Record a first message you sent yourself</h3>
      <p class="meta">The workspace did not send it; this records that you did. The lead moves to Waiting for a reply.</p>
      <div class="form-grid">
        <label class="field">How<select name="via">${CHANNELS.map((c) => html`<option>${c}</option>`)}</select></label>
        <label class="field">When<input type="date" name="on" value="${ctx.today}" max="${ctx.today}" required></label>
      </div>
      <div class="actions">
        <button type="submit" class="btn primary">Record as sent</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      </div>
      ${inlineError(ui.error && ui.error.where === "sent" ? ui.error.message : "")}
    </form>
  </div>`;
}

function replyPanel(o, ctx) {
  return html`
  <div class="block">
    <form class="panel-form" data-act="replyForm" data-on="submit input" data-id="${o.id}">
      <h3 class="sub-title">Record the client's reply</h3>
      <p class="meta">Any reply makes the lead Active at once. The Evidence agent labels it and reads it for qualification evidence, for you to review.</p>
      <label class="field">What they wrote<textarea name="text" rows="5" required></textarea></label>
      <label class="field">Received on<input type="date" name="on" value="${ctx.today}" max="${ctx.today}" required style="max-width:220px"></label>
      <div class="actions">
        <button type="submit" class="btn primary" disabled>Record reply</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing>Paste the reply.</span>
      </div>
      ${inlineError(ui.error && ui.error.where === "reply" ? ui.error.message : "")}
    </form>
  </div>`;
}

function noticeBlock() {
  if (!ui.notice) return "";
  return html`<div class="block">${ui.notice.map((n) => html`<div class="notice ${n.cls || ""}">${icon(n.cls === "good" ? "check" : "alert")}<span>${n.text}</span></div>`)}</div>`;
}

// The buttons in the right-hand column. Forms they open appear in the middle, next to the text they act on.
function sideActions(lead, buttons, minor = "") {
  return html`<div class="side-sec side-actions">${lead || ""}${buttons}${minor ? html`<div class="minor">${minor}</div>` : ""}</div>`;
}

// Skip this step / Stop the plan, when a plan is running.
function planMinor(o) {
  const p = planOf(o);
  if (!p || !p.pl.running || !p.cur) return "";
  return html`<button type="button" class="btn ghost small" data-act="mode" data-mode="planSkip:${p.cur.n}">Skip this step</button>
    <button type="button" class="btn ghost small" data-act="mode" data-mode="planStop">Stop the plan</button>`;
}

function planForms(o) {
  const m = ui.mode || "";
  const p = planOf(o);
  if (m === "planStop") return html`<div class="block">${planStopForm(o)}</div>`;
  if (p && p.cur && (m === `planSkip:${p.cur.n}` || m === `planDo:${p.cur.n}`)) return html`<div class="block">${stepForm(p.cur, o, m.startsWith("planDo"))}</div>`;
  return "";
}

// ----- first email from the Step 5 draft -----
function firstEmail(d, ctx) {
  const o = d.detail;
  const r = o.outreach;
  const hasDraft = r.status === "draft" && r.body;
  const facts = (o.brief && o.brief.facts) || [];
  const m = ui.mode;
  const mine = o.owner === ctx.me;
  const waits = emailWaits(r.waiting);
  return {
    main: html`
      ${leadHead(o, `First email to ${o.contact.name || "the contact"}`)}
      ${noticeBlock()}
      ${waitingNotice(r.waiting, ctx.me)}
      ${relatedBlock(d.related)}
      ${hasDraft && m !== "edit" ? draftBody(r.subject, r.body, facts, { to: (d.preview && d.preview.to) || "", confirmed: !!o.contact.confirmedBy, checks: true,
        blocked: d.preview && d.preview.blocked, from: d.preview && d.preview.from ? d.preview.from.address || "" : "", canEdit: mine && !waits, editMode: "edit" }) : ""}
      ${!hasDraft && m !== "edit" ? html`<div class="block">${emptyState("No draft yet",
        "The Outreach agent writes one when the lead is accepted, or a contact plan first. If none arrived, ask for one, or write it yourself.")}</div>` : ""}
      ${m === "send" ? sendPanel(d) : ""}
      ${m === "revise" ? revisePanel(o, hasDraft) : ""}
      ${m === "edit" ? editPanel(o) : ""}
      ${m === "record" ? recordSentPanel(o, ctx) : ""}
      ${m === "nurture" ? nurturePanel(o, ctx) : ""}
      ${planBlock(o, ctx)}
      ${contactBlock(o)}
      ${linkedinBlock(o, ctx)}`,
    side: html`
      ${hasDraft ? standardBlock(r.body, null) : ""}
      ${planSide(o, ctx)}
      ${!m && !waits ? sideActions(approverLine(o.owner, ctx), html`
        ${hasDraft ? html`<button type="button" class="btn primary" data-act="mode" data-mode="send">Approve and send…</button>` : ""}
        <button type="button" class="btn ${hasDraft ? "" : "primary"}" data-act="mode" data-mode="revise">${hasDraft ? "Ask the agent to revise…" : "Ask the agent for a draft…"}</button>
        ${hasDraft ? "" : html`<button type="button" class="btn" data-act="mode" data-mode="edit">Write it myself</button>`}
        <button type="button" class="btn ghost" data-act="mode" data-mode="record">Record as sent…</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="nurture">Park the lead…</button>`) : ""}`,
  };
}

// ----- contact plan (api/outreach_plan.py) -----
// The outreach agent proposes whom to approach first and how, over a few weeks; a person approves or rejects it.
const APPROACH = { warm: "Warm: through someone who knows them", value_first: "Value first: something useful before asking for time", direct: "Direct" };
const ROLE = { budget: "Likely signs the budget", sponsor: "Sponsor", user: "Works on it", introducer: "Could introduce CGS" };

function planBlock(o, ctx) {
  const pl = o.outreach && o.outreach.plan;
  const err = ui.error && ui.error.where === "plan" ? ui.error.message : "";
  const asking = ui.mode === "planAsk";
  if (!pl) {
    return html`<div class="block stack plan">
      <h3 class="sub-title" style="margin:0">Contact plan</h3>
      <p class="meta">Before the first email: whom to approach first, who else matters, and a few steps over the next weeks.
        A cold email is one option, not the default. The Outreach agent proposes; you decide. Nothing is sent.</p>
      ${asking ? planAskForm(o, false) : html`<div><button type="button" class="btn small" data-act="mode" data-mode="planAsk">Ask the Outreach agent for a plan…</button></div>`}
      ${inlineError(err)}
    </div>`;
  }
  const p = pl.plan;
  const status = pl.status === "approved" && pl.running === false ? html`<span class="tag">Stopped</span>`
    : pl.status === "approved" ? html`<span class="tag good">${icon("check", 14)}Running since ${day(pl.decidedOn, ctx.today)}</span>`
    : pl.status === "rejected" ? html`<span class="tag bad">Rejected by ${pl.decidedBy}</span>`
    : html`<span class="tag warn">Proposed, waiting for you</span>`;
  const other = p.entry.name !== o.contact.name;
  return html`
  <div class="block stack plan">
    <div class="row-flex"><h3 class="sub-title" style="margin:0">Contact plan</h3>${status}</div>
    <p class="meta-3">${pl.by}, ${day(pl.on, ctx.today)}. Playbook ${pl.playbook}${pl.comment ? `. Your comment: “${pl.comment}”` : ""}${pl.decidedBy ? `. ${pl.status === "rejected" ? "Rejected" : "Approved"} by ${pl.decidedBy}` : ""}${pl.note ? `: ${pl.note}` : ""}</p>
    ${pl.running === false ? html`<div class="notice" role="status">${icon("alert")}<span>The plan has stopped: ${pl.stoppedWhy} Ask for a new plan if more outreach is needed.</span></div>` : ""}
    <div class="plan-entry">
      <p><b>Start with ${p.entry.name}</b>${p.entry.title ? `, ${p.entry.title}` : ""} <span class="meta-3">(${p.entry.source})</span></p>
      <p class="meta">${p.entry.why}</p>
      ${other ? html`<p class="warn-text meta">${icon("alert", 14)} This is not the opportunity's contact (${o.contact.name || "none yet"}). To follow the plan,
        confirm ${p.entry.name} as the contact first; email steps go to the contact.</p>` : ""}
    </div>
    ${p.others.length ? html`<ul class="bullets">${p.others.map((x) => html`<li><b>${x.name}</b>${x.title ? `, ${x.title}` : ""}: ${ROLE[x.role] || x.role}. <span class="meta">${x.why}</span></li>`)}</ul>` : ""}
    <p><b>${APPROACH[p.approach] || p.approach}.</b> <span class="meta">${p.approach_why}</span></p>
    <ol class="plan-steps">${p.steps.map((s) => planStep(s, o, pl, ctx))}</ol>
    ${p.stop_when ? html`<p class="meta"><b>Stop or change course:</b> ${p.stop_when}</p>` : ""}
    ${p.unknowns.length ? html`<p class="meta"><b>Still unknown:</b> ${p.unknowns.join("; ")}</p>` : ""}
    ${ui.mode === "planReject" ? html`
    <form class="panel-form" data-act="planRejectForm" data-on="submit input" data-id="${o.id}">
      <label class="field">Why reject it <span class="hint">The next plan the agent proposes reads this.</span><textarea name="note" rows="2"></textarea></label>
      <div class="actions"><button type="submit" class="btn primary small" disabled>Reject the plan</button>
        <button type="button" class="btn ghost small" data-act="mode" data-mode="">Cancel</button><span class="missing" data-missing>Say why.</span></div>
    </form>` : asking ? planAskForm(o, true) : html`
    <div class="actions">
      ${pl.status === "proposed" ? html`<button type="button" class="btn primary small" data-act="planDecide" data-id="${o.id}" data-decision="approve">Approve the plan</button>
        <button type="button" class="btn small" data-act="mode" data-mode="planReject">Reject…</button>` : ""}
      ${pl.running ? html`<button type="button" class="btn ghost small" data-act="mode" data-mode="planStop">Stop the plan…</button>` : ""}
      <button type="button" class="btn ghost small" data-act="mode" data-mode="planAsk">Ask for another plan…</button>
    </div>`}
    ${ui.mode === "planStop" ? planStopForm(o) : ""}
    ${inlineError(err)}
  </div>`;
}

function planStopForm(o) {
  return html`<form class="panel-form" data-act="planStopForm" data-on="submit input" data-id="${o.id}">
    <label class="field">Why stop it <span class="hint">For example: they replied on LinkedIn, or the contact left. No steps run after this.</span><textarea name="note" rows="2"></textarea></label>
    <div class="actions"><button type="submit" class="btn primary small" disabled>Stop the plan</button>
      <button type="button" class="btn ghost small" data-act="mode" data-mode="">Cancel</button><span class="missing" data-missing>Say why.</span></div>
  </form>`;
}

const STEP_STATUS = { done: ["good", "Done"], skipped: ["", "Skipped"], ready: ["warn", "Prepared, waiting for you"] };

function planStep(s, o, pl, ctx) {
  const mine = s.to === o.contact.name;
  const isCurrent = pl.running && pl.current === s.n;
  const [cls, label] = STEP_STATUS[s.status] || ["", ""];
  const when = s.status === "done" || s.status === "skipped"
    ? `${label} ${day(s.doneOn, ctx.today)}${s.how ? ` (${s.how})` : ""}${s.note ? `: ${s.note}` : ""}`
    : s.due ? `Due ${day(s.due, ctx.today)}` : pl.status === "approved" ? "After the step before" : "";
  const recording = ui.mode === `planDo:${s.n}` || ui.mode === `planSkip:${s.n}`;
  return html`<li class="${isCurrent ? "plan-current" : ""}">
    <span class="meta-3">Day ${s.day}</span> <b>${CHANNEL[s.channel] || s.channel}</b> to ${s.to}${s.via ? html` <span class="meta">(through ${s.via})</span>` : ""}
    ${s.status && s.status !== "pending" ? html` <span class="tag ${cls}">${label}</span>` : ""}
    <div class="meta">${s.purpose}</div>
    ${s.content ? html`<div class="meta-3">${s.content.type}: ${s.content.title}${s.content.synthetic ? " (SYNTHETIC demo document)" : ""}</div>` : ""}
    ${when && pl.status === "approved" ? html`<div class="meta-3">${when}</div>` : ""}
    ${isCurrent && s.prepared ? prepared(s, o) : ""}
    ${isCurrent && !recording ? stepActions(s, o, mine) : ""}
    ${isCurrent && recording ? stepForm(s, o, ui.mode.startsWith("planDo")) : ""}
  </li>`;
}

// What the outreach agent prepared for the current step.
function prepared(s, o) {
  const p = s.prepared;
  const copy = (text, label) => html`<button type="button" class="btn ghost small" data-act="liCopy" data-text="${text}">${label}</button>`;
  if (p.kind === "email") {
    return html`<div class="meta">The email draft “${p.subject}” waits in <a href="${href("outreach", { tab: "you", item: p.item, team: "1" })}">Needs you</a>.
      Sending it there completes this step.</div>`;
  }
  if (p.kind === "linkedin") return html`<div class="meta">The LinkedIn note is ready in the LinkedIn block. Recording it as sent there completes this step.</div>`;
  if (p.kind === "call") {
    return html`<div class="plan-prepared"><p><b>Opening:</b> ${p.opening}</p>
      <ul class="bullets">${p.questions.map((q) => html`<li>${q}</li>`)}</ul>
      <p class="meta"><b>Voicemail:</b> ${p.voicemail}</p></div>`;
  }
  return html`<div class="plan-prepared"><p class="meta">To ${p.to || "your colleague"}:</p><div class="email-plain">${p.message}</div>
    <div class="actions">${copy(p.message, "Copy the message")}</div></div>`;
}

function stepActions(s, o, mine) {
  const contactOnly = ["email", "share_content", "invite", "linkedin_connect", "linkedin_message", "call"].includes(s.channel);
  return html`<div class="actions">
    ${s.status === "pending" ? html`<button type="button" class="btn small" data-act="planPrepare" data-id="${o.id}" data-n="${s.n}" ${contactOnly && !mine ? "disabled" : ""}>Draft it now</button>` : ""}
    <button type="button" class="btn small" data-act="mode" data-mode="planDo:${s.n}">I did it…</button>
    <button type="button" class="btn ghost small" data-act="mode" data-mode="planSkip:${s.n}">Skip…</button>
    ${contactOnly && !mine ? html`<span class="meta-3">Goes to ${s.to}: confirm them as the contact first, or skip it.</span>` : ""}
  </div>`;
}

function stepForm(s, o, done) {
  const need = !done || s.channel === "call";
  const hint = !done ? "Why you skip it. Nothing is sent for this step; the plan moves on." : s.channel === "call" ? "What happened on the call." : "Optional, for example where you sent it.";
  return html`<form class="panel-form" data-act="planStepForm" data-on="submit input" data-id="${o.id}" data-n="${s.n}" data-action="${done ? "done" : "skip"}" data-need="${need ? "1" : ""}">
    <label class="field">${done ? "Note" : "Reason"} <span class="hint">${hint}</span><textarea name="note" rows="2"></textarea></label>
    <div class="actions"><button type="submit" class="btn primary small" ${need ? "disabled" : ""}>${done ? "Record it as done" : "Skip this step"}</button>
      <button type="button" class="btn ghost small" data-act="mode" data-mode="">Cancel</button><span class="missing" data-missing>${need ? "Write a few words." : ""}</span></div>
  </form>`;
}

function planAskForm(o, again) {
  return html`<form class="panel-form" data-act="planAskForm" data-on="submit" data-id="${o.id}">
    ${again ? html`<label class="field">What should be different <span class="hint">Optional. The agent also sees the plan before.</span><textarea name="comment" rows="2"></textarea></label>`
      : html`<label class="field">Anything the agent should know <span class="hint">Optional, for example "Sarah knows their COO".</span><textarea name="comment" rows="2"></textarea></label>`}
    <div class="actions"><button type="submit" class="btn primary small">${again ? "Ask for a new plan" : "Make the plan"}</button>
      <button type="button" class="btn ghost small" data-act="mode" data-mode="">Cancel</button></div>
  </form>`;
}

function revisePanel(o, hasDraft) {
  return html`
  <div class="block">
    <form class="panel-form" data-act="reviseForm" data-on="submit" data-id="${o.id}">
      <h3 class="sub-title">${hasDraft ? "Ask the agent to revise" : "Ask the Outreach agent for a draft"}</h3>
      <p class="meta">${hasDraft ? "It writes the draft again from the research brief and your comment. The new draft replaces this one." : "It writes from the research brief. Nothing is sent."}</p>
      <div class="chips">${REVISE_HINTS.map((c) => html`<button type="button" class="chip" data-act="chip" data-text="${c}">${c}</button>`)}</div>
      <label class="field">Or say it in your own words <span class="hint">Optional.</span><textarea name="angle" rows="3"></textarea></label>
      <label class="field" style="max-width:220px">Length<select name="tone"><option value="standard">Standard</option><option value="brief">Brief</option></select></label>
      <div class="actions">
        <button type="submit" class="btn primary">${hasDraft ? "Send to the agent" : "Write the draft"}</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      </div>
      ${inlineError(ui.error && ui.error.where === "revise" ? ui.error.message : "")}
    </form>
  </div>`;
}

function wordLine(body, tone = "standard") {
  const range = rubric && rubric.length && rubric.length[tone];
  const n = words(body);
  return html`<span class="${range && (n < range[0] || n > range[1]) ? "warn-text md" : "meta-3"}" data-words data-min="${range ? range[0] : ""}" data-max="${range ? range[1] : ""}">${n} words${range ? ` (${range[0]}-${range[1]} for this standard)` : ""}</span>`;
}

function editPanel(o) {
  const r = o.outreach;
  return html`
  <div class="block">
    <form class="panel-form" data-act="editForm" data-on="submit input" data-id="${o.id}">
      <h3 class="sub-title">Edit the draft</h3>
      <p class="meta">Text in [[f1|...]] marks a citation of the research brief. Keep the mark, or delete it with its phrase. The client never sees the marks.</p>
      <label class="field">Subject<input name="subject" value="${r.subject || ""}" required></label>
      <label class="field">Message<textarea name="body" rows="12" required>${r.body || ""}</textarea></label>
      <div class="actions">
        <button type="submit" class="btn primary" ${r.subject && r.body ? "" : "disabled"}>Done editing</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing>${r.subject && r.body ? "" : "Write a subject and a message."}</span>
        <span style="margin-left:auto">${wordLine(r.body)}</span>
      </div>
      ${inlineError(ui.error && ui.error.where === "edit" ? ui.error.message : "")}
    </form>
  </div>`;
}

// ----- a draft the agent put in the inbox (first, follow-up, reconnect, or a plan step's email) -----
function agentDraft(d, ctx) {
  const o = d.detail;
  const it = d.cur.item;
  const purpose = it.data.purpose || "followup";
  const facts = [...((o.brief && o.brief.facts) || []), ...(it.data.facts || [])];
  const m = ui.mode;
  const mine = o.owner === ctx.me;
  const edited = !!it.data.editedBy;
  const step = it.data.planStep;
  const writer = edited ? `Agent draft, edited by ${it.data.editedBy}` : `Written by the ${it.agent}`;
  return {
    main: html`
      ${leadHead(o, `${PURPOSE[purpose] || "Email"} to ${o.contact.name || "the contact"}`)}
      ${threadBlock(o, ctx)}
      ${noticeBlock()}
      ${waitingNotice(o.outreach.waiting, ctx.me)}
      ${relatedBlock(d.related)}
      <div class="block draft-head">
        <h3 class="sub-title" style="margin:0">${mine ? "Your draft to review" : `${o.owner}'s draft`}</h3>
        <span class="row-flex" style="gap:6px">${edited ? html`<span class="tag">Edited by ${it.data.editedBy === ctx.me ? "you" : it.data.editedBy}</span>
          ${it.data.original && mine ? html`<button type="button" class="btn ghost small" data-act="restoreDraft" data-item="${it.id}" data-hash="${it.payloadHash}">Restore the agent's draft</button>` : ""}` : ""}
          <span class="tag info">${writer}</span></span>
        <p class="meta-3" style="flex-basis:100%">${it.agent}, ${day(it.on, ctx.today)}${it.expiresOn ? `. Expires ${day(it.expiresOn, ctx.today)} if nobody decides.` : ""}</p>
      </div>
      ${m === "editItem" ? itemEditPanel(it) : draftBody(it.data.subject, it.data.body, facts, {
        to: (d.preview && d.preview.to) || "", confirmed: !!o.contact.confirmedBy, checks: true, blocked: d.preview && d.preview.blocked,
        from: d.preview && d.preview.from ? d.preview.from.address || "" : "", canEdit: mine, editMode: "editItem" })}
      ${m === "send" ? sendPanel(d, { itemId: it.id, hash: it.payloadHash }) : ""}
      ${planForms(o)}
      ${contactBlock(o)}`,
    side: html`
      ${standardBlock(it.data.body, it.data.review)}
      ${planSide(o, ctx, step)}
      ${(!m || m === "contact") && !emailWaits(o.outreach.waiting) ? sideActions(approverLine(o.owner, ctx), html`
        ${d.mail.ready ? html`<button type="button" class="btn primary" data-act="mode" data-mode="send">Approve and send…</button>` : ""}
        <button type="button" class="btn ${d.mail.ready ? "" : "primary"}" data-act="inboxAct" data-do="apply" data-item="${it.id}" data-hash="${it.payloadHash}">
          ${purpose === "first" ? "Use it as my draft" : "Accept, I will send it myself"}</button>
        <button type="button" class="btn ghost" data-act="inboxAct" data-do="dismiss" data-item="${it.id}">Dismiss the draft</button>
        <p class="meta-3">${purpose === "first" ? "Use it as my draft: you can then ask for a revision or write it yourself." : "After you send it yourself, record it under Waiting for a reply."}</p>`,
        planMinor(o)) : ""}`,
  };
}

function itemEditPanel(it) {
  return html`
  <div class="block">
    <form class="panel-form" data-act="itemEditForm" data-on="submit input" data-item="${it.id}" data-hash="${it.payloadHash}">
      <h3 class="sub-title">Edit the draft</h3>
      <p class="meta">Your version is the one you approve. The agent's text is kept, so it can learn from what you change, and you can restore it. Text in [[f1|...]] marks a citation of the research brief: keep the mark, or delete it with its phrase. The client never sees the marks.</p>
      <label class="field">Subject<input name="subject" value="${it.data.subject || ""}" required></label>
      <label class="field">Message<textarea name="body" rows="12" required>${it.data.body || ""}</textarea></label>
      <div class="actions">
        <button type="submit" class="btn primary">Done editing</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing></span>
        <span style="margin-left:auto">${wordLine(it.data.body)}</span>
      </div>
      ${inlineError(ui.error && ui.error.where === "itemEdit" ? ui.error.message : "")}
    </form>
  </div>`;
}

// ----- an email a colleague approved that waits for this person's second approval (api/undo.py) -----
function secondApproval(d, ctx) {
  const o = d.detail;
  const w = o.outreach.waiting;
  return {
    main: html`
      ${leadHead(o, `Approve ${w.by}'s email to ${o.contact.name || w.to}`)}
      ${threadBlock(o, ctx)}
      ${noticeBlock()}
      ${waitingNotice(w, ctx.me)}`,
    side: html`
      ${standardBlock(w.body, null)}
      ${planSide(o, ctx)}
      ${sideActions(html`<p class="meta">${w.by} approved this. ${w.approver === ctx.me ? "You are the second approver: it goes out from their mailbox once you approve it, after the 10-second undo window." : `It waits for ${w.approver}.`}</p>`, "")}`,
  };
}

// ----- a contact plan to decide, a step to do, or a step the agent drafts when it is due -----
function planItem(d, ctx) {
  const o = d.detail;
  const k = d.cur.kind;
  const headText = k === "plan" ? `Contact plan for ${o.company.name}` : k === "agentstep" ? `Next step for ${o.company.name}` : `Plan step for ${o.contact.name || o.company.name}`;
  return {
    main: html`
      ${leadHead(o, headText)}
      ${threadBlock(o, ctx)}
      ${noticeBlock()}
      ${waitingNotice(o.outreach.waiting, ctx.me)}
      ${k === "agentstep" ? html`<div class="block"><div class="notice info">${icon("clock")}<span>The Outreach agent drafts this step when it is due${d.cur.step && d.cur.step.due ? `, on ${day(d.cur.step.due, ctx.today)}` : ""}. Nothing to do yet; Draft it now asks for it today.</span></div></div>` : ""}
      ${planBlock(o, ctx)}
      ${contactBlock(o)}
      ${linkedinBlock(o, ctx)}`,
    side: html`${planSide(o, ctx)}
      ${k === "plan" ? sideActions(html`<p class="meta">The plan is a proposal. Approving it starts the steps; each email still needs your approval.</p>`, "") : ""}`,
  };
}

// ----- a client reply found by the mail rules -----
function replyItem(d, ctx) {
  const o = d.detail;
  const it = d.cur.item;
  const r = o.outreach;
  const answers = r.subject ? html`<p class="meta">Answers your email${r.sentOn ? ` of ${day(r.sentOn, ctx.today)}` : ""}: ${r.subject}</p>` : "";
  // Who wrote it (api/senders.py): the contact on file, or someone else, who must not be taken for the contact.
  const s = it.data.sender;
  const writer = s ? s.name || s.email : o.contact.name || "the client";
  const other = s && !s.isContact;
  return {
    main: html`
      ${header(`Reply from ${writer}`, other ? `Your email went to ${o.contact.name || "the contact"} (${contactLine(o)})` : contactLine(o), html`<p class="meta-3">Found in the email thread, ${day(it.data.on || it.on, ctx.today)}${s && s.email ? ` · ${s.email}` : ""}</p>${answers}`)}
      ${other && s.sameAddress ? html`<div class="block"><div class="notice warn" role="status">${icon("alert")}<span>
        <b>Signed ${writer}, from ${s.email}: the address on file for ${s.contact}.</b>
        Check that address: it may be wrong, shared, or someone may answer for ${s.contact}. Recording it notes who signed it.
      </span></div></div>`
      : other ? html`<div class="block"><div class="notice warn" role="status">${icon("alert")}<span>
        <b>${writer} wrote this reply, not ${s.contact || "the contact on file"}.</b>
        ${s.knownAs ? ` ${s.knownAs} is on file as a contact at ${o.company.name}.` : " They may now be involved: an assistant, or a colleague the contact passed your email to."}
        Recording it keeps ${s.contact || "the contact"} as the lead's contact and notes who wrote.
        ${!s.knownAs && s.email ? html`<div class="actions" style="margin-top:8px"><button type="button" class="btn small" data-act="addSender" data-item="${it.id}">Add ${writer} as a contact at ${o.company.name}</button></div>` : ""}
      </span></div></div>` : ""}
      ${noticeBlock()}
      <div class="block"><div class="quote big">${it.data.text || it.body}</div></div>
      ${threadBlock(o, ctx)}`,
    side: html`${planSide(o, ctx)}
      ${sideActions(html`<p class="meta">Recording it makes the lead Active and stops the plan. The Evidence agent then reads it for evidence, for a person to confirm.</p>`, html`
        <button type="button" class="btn primary" data-act="inboxAct" data-do="apply" data-item="${it.id}" data-hash="${it.payloadHash}">Record the reply</button>
        <button type="button" class="btn ghost" data-act="inboxAct" data-do="dismiss" data-item="${it.id}">Not a reply</button>
        <p class="meta-3">Choose Not a reply for an out-of-office or automatic message.</p>`)}`,
  };
}

// ----- LinkedIn: a note and a message the person sends themselves (api/linkedin.py) -----
function linkedinBlock(o, ctx) {
  const li = (o.outreach && o.outreach.linkedin) || {};
  const who = o.contact.name || "the contact";
  const editing = ui.mode === "liEdit";
  const sent = li.sent || {};
  const err = ui.error && ui.error.where === "linkedin" ? ui.error.message : "";
  const copy = (text, label) => html`<button type="button" class="btn ghost small" data-act="liCopy" data-text="${text}">${label}</button>`;
  return html`
  <div class="block stack linkedin">
    <div class="row-flex"><h3 class="sub-title" style="margin:0">LinkedIn</h3>
      ${sent.note ? html`<span class="tag good">${icon("check", 14)}Request sent ${day(sent.note, ctx.today)}</span>` : ""}
      ${sent.message ? html`<span class="tag good">${icon("check", 14)}Message sent ${day(sent.message, ctx.today)}</span>` : ""}</div>
    <p class="meta">Sometimes a LinkedIn note works better than an email. The Outreach agent writes it from the research brief;
      you send it yourself. Agents never open LinkedIn. <a href="${safeUrl(li.search)}" target="_blank" rel="noopener noreferrer">Find ${who} on LinkedIn</a></p>
    ${editing ? html`
    <form class="panel-form" data-act="liForm" data-on="submit input" data-id="${o.id}">
      <label class="field">Connection note <span class="hint" data-count="note">${(li.note || "").length} of ${li.noteMax} characters</span>
        <textarea name="note" rows="3" maxlength="${li.noteMax}">${li.note || ""}</textarea></label>
      <label class="field">Message after they accept <span class="hint" data-count="message">${(li.message || "").length} of ${li.messageMax} characters</span>
        <textarea name="message" rows="6" maxlength="${li.messageMax}">${li.message || ""}</textarea></label>
      <div class="actions"><button type="submit" class="btn primary small">Save</button>
        <button type="button" class="btn ghost small" data-act="mode" data-mode="">Cancel</button><span class="missing" data-missing></span></div>
    </form>`
    : li.note || li.message ? html`
    <div class="li-text">
      <p class="meta-3">Connection note, ${li.note.length} of ${li.noteMax} characters${li.by ? `. ${li.by}, ${day(li.on, ctx.today)}` : ""}</p>
      <div class="email-plain">${li.note}</div>
      <div class="actions">${copy(li.note, "Copy the note")}
        ${sent.note ? "" : html`<button type="button" class="btn small" data-act="liSent" data-id="${o.id}" data-what="note">I sent the request</button>`}</div>
      ${li.message ? html`<p class="meta-3" style="margin-top:12px">Message after they accept</p>
      <div class="email-plain">${li.message}</div>
      <div class="actions">${copy(li.message, "Copy the message")}
        ${sent.message ? "" : html`<button type="button" class="btn small" data-act="liSent" data-id="${o.id}" data-what="message">I sent the message</button>`}</div>` : ""}
      <div class="actions" style="margin-top:8px">
        <button type="button" class="btn ghost small" data-act="mode" data-mode="liEdit">Edit</button>
        <button type="button" class="btn ghost small" data-act="liDraft" data-id="${o.id}">Write it again</button></div>
    </div>`
    : html`<div><button type="button" class="btn small" data-act="liDraft" data-id="${o.id}">Write a LinkedIn note</button></div>`}
    ${o.stage === "identified" && !sent.note && !sent.message ? html`<p class="meta-3">Sending it on LinkedIn counts as the first contact: the lead moves to Waiting for a reply.</p>` : ""}
    ${inlineError(err)}
  </div>`;
}

// ----- sent, waiting for a reply -----
function waiting(d, ctx) {
  const o = d.detail;
  const r = o.outreach;
  const m = ui.mode;
  const sent = r.sentOn ? `${r.status === "followup" ? "Follow-up" : "First message"} sent ${day(r.sentOn, ctx.today)}${r.via ? ` by ${r.via}` : ""}${r.toEmail ? ` to ${r.toEmail}` : ""}.` : "Sent.";
  return {
    main: html`
      ${header(`${o.contact.name || "Contact"} at ${o.company.name}`, contactLine(o))}
      <div class="block stack">
        <p>${sent}</p>
        ${r.subject ? html`<p class="meta">Subject: ${r.subject}</p>` : ""}
        <p class="meta">${o.next.action ? `${o.next.action}, due ${day(o.next.due, ctx.today)}.` : "No next step set."}</p>
      </div>
      ${noticeBlock()}
      ${waitingNotice(r.waiting, ctx.me)}
      ${m === "reply" ? replyPanel(o, ctx) : ""}
      ${m === "nurture" ? nurturePanel(o, ctx) : ""}
      ${planForms(o)}
      ${o.outreach.plan ? planBlock(o, ctx) : ""}
      ${linkedinBlock(o, ctx)}`,
    side: html`${planSide(o, ctx)}
      ${!m ? sideActions("", html`
        <button type="button" class="btn primary" data-act="mode" data-mode="reply">Record a reply…</button>
        <button type="button" class="btn" data-act="followup" data-id="${o.id}">I sent a follow-up</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="nurture">Park the lead…</button>`, planMinor(o)) : ""}`,
  };
}

// ---------- keys on Needs you: J and K move through the queue ----------
let keysOn = false;
function queueKey(e) {
  if (!/^#\/outreach\?(.*&)?tab=you/.test(location.hash) || document.querySelector(".drawer")) return;
  if ((e.target && e.target.matches && e.target.matches("input, textarea, select")) || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = (e.key || "").toLowerCase();
  if (k !== "j" && k !== "k") return;
  const list = [...document.querySelectorAll(".queue .pick")];
  const i = list.findIndex((a) => a.getAttribute("aria-current") === "true");
  const n = list[(i + (k === "j" ? 1 : -1) + list.length) % list.length];
  if (n && list.length > 1) { e.preventDefault(); location.hash = n.getAttribute("href"); }
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

async function attempt(where, button, ctx, fn) {
  try {
    const out = await busy(button, fn);
    ui.error = null;
    return out;
  } catch (err) {
    ui.error = { where, message: err.message };
    ctx.refresh();
    return undefined;
  }
}

// After a decision on an item: the queue moves to the next one (the full load picks it).
function moveOn(ctx, d, message, link) {
  resetUi();
  ctx.toast(message, link ? { link } : {});
  ctx.scrollTop = true;
  ctx.go("outreach", { tab: ctx.route.params.tab, team: ctx.route.params.team });
}

// Keep the word count beside the editor in step with the text.
function countWords(form) {
  const line = form.querySelector("[data-words]");
  if (!line) return;
  const n = words(form.elements.body.value);
  const min = Number(line.dataset.min), max = Number(line.dataset.max);
  line.textContent = `${n} words${max ? ` (${min}-${max} for this standard)` : ""}`;
  line.className = max && (n < min || n > max) ? "warn-text md" : "meta-3";
}

export const actions = {
  ...panelActions("outreach", (ctx) => ({ team: ctx.route.params.team })),
  crmResolve: resolveCrm,
  tab(t, e, ctx) { resetUi(); ctx.go("outreach", { tab: t.dataset.tab, team: ctx.route.params.team }); },
  tabkeys(t, e, ctx) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    const tabs = [...t.querySelectorAll("[role=tab]")];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const n = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : e.key === "Home" ? 0 : tabs.length - 1;
    ctx.focusAfter = `#${tabs[n].id}`;
    resetUi();
    ctx.go("outreach", { tab: tabs[n].dataset.tab, team: ctx.route.params.team });
  },
  mode(t, e, ctx) {
    ui.mode = t.dataset.mode || null;
    ui.error = null;
    ctx.focusAfter = ui.mode ? ".panel-form input:not([type=hidden]), .panel-form textarea, .panel-form select" : null;
    ctx.refresh();
  },
  thread(t, e, ctx) {
    ui.thread = !(ui.thread === true);
    ctx.focusAfter = "[data-act=thread]";
    ctx.refresh();
  },
  chip(t) {
    const area = t.closest("form").elements.angle;
    area.value = area.value ? `${area.value} ${t.dataset.text}` : t.dataset.text;
    area.focus();
  },
  async syncMail(t, e, ctx) {
    const res = await attempt("sync", t, ctx, () => ctx.api.post("/mail/sync"));
    if (!res) return ctx.toast(ui.error ? ui.error.message : "The email check did not run.");
    const parts = [`Checked ${plural(res.checked, "thread")}`];
    if (res.replies) parts.push(plural(res.replies, "new reply", "new replies"));
    if (res.sent) parts.push(`${plural(res.sent, "email")} sent from Gmail recorded`);
    ctx.toast(`${parts.join(", ")}.${(res.errors || []).length ? ` ${res.errors.length} could not be read.` : ""}`);
    ctx.refresh();
  },

  // contact
  contactForm(form, e, ctx) {
    const f = form.elements;
    const why = !f.name.value.trim() ? "Give the name." : !f.note.value.trim() ? "Say where you checked it." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    attempt("contact", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/opportunities/${enc(form.dataset.id)}/contact`,
      { name: f.name.value.trim(), title: f.title.value.trim(), note: f.note.value.trim() }))
      .then((ok) => { if (ok) { ui.mode = null; ctx.refresh(); } });
  },

  // first email: draft, revise, send
  async reviseForm(form, e, ctx) {
    const f = form.elements;
    const res = await attempt("revise", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(form.dataset.id)}/outreach/generate`, { angle: f.angle.value.trim() || null, tone: f.tone.value }));
    if (!res) return;
    ui.mode = null;
    ui.notice = (res.warnings || []).map((w) => ({ text: w, cls: "warn" }));
    if ((res.newFacts || []).length) ui.notice.push({ text: `The agent added ${plural(res.newFacts.length, "newer fact")} to the research brief.`, cls: "" });
    if (!ui.notice.length) ui.notice = null;
    ctx.refresh();
  },
  editForm(form, e, ctx) {
    const f = form.elements;
    countWords(form);
    const why = !f.subject.value.trim() || !f.body.value.trim() ? "Write a subject and a message." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    attempt("edit", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.put(`/opportunities/${enc(form.dataset.id)}/outreach/draft`, { subject: f.subject.value.trim(), body: f.body.value }))
      .then((ok) => { if (ok) { ui.mode = null; ctx.refresh(); } });
  },
  async sendForm(form, e, ctx) {
    const to = form.elements.to.value.trim();
    if (e.type !== "submit") return missing(form, to ? "" : "Add the client's address.");
    if (!to) return;
    const how = (e.submitter && e.submitter.value) || "send";
    const id = form.dataset.id;
    const itemId = form.dataset.item;
    const shown = form.querySelector(".email-plain").textContent;
    const q = `?to=${enc(to)}${itemId ? `&itemId=${enc(itemId)}` : ""}`;
    const res = await attempt("send", e.submitter || form.querySelector("[type=submit]"), ctx, async () => {
      const p = await ctx.api.get(`/opportunities/${enc(id)}/outreach/email${q}`);
      // The person approved the text on screen. If the draft changed since, stop and show the new text.
      if (`${p.subject}\n\n${p.body}` !== shown) throw new Error("The draft changed since you opened it. Look at the new text, then approve again.");
      const delay = how === "send" ? UNDO_SECONDS : 0;
      if (itemId) return ctx.api.post(`/inbox/${enc(itemId)}`, { action: "send", payload_hash: form.dataset.hash, to, delay });
      return ctx.api.post(`/opportunities/${enc(id)}/outreach/email`, { to, mode: how, seenHash: p.seenHash, delay });
    });
    if (!res) return;
    const pending = itemId ? res.sent : res;
    if (pending && pending.mode === "approval") {
      return moveOn(ctx, null, `Approved. It waits for ${pending.approver}'s approval before it goes to ${to}. It is under With a teammate until then.`);
    }
    if (how === "draft") return moveOn(ctx, null, `The email is in your Gmail drafts for ${to}. Nothing was sent; the workspace records it once you send it from Gmail.`);
    const out = itemId ? res.sent : res;
    if (out && out.mode === "queued") {
      resetUi(ui.key);
      offerUndo(ctx, `Approved. The email goes to ${to} in ${out.seconds} seconds.`, () => undoEmail(ctx, out.id), { seconds: out.seconds, key: `email:${out.id}` });
      return ctx.refresh();
    }
    moveOn(ctx, null, `Sent to ${to}. The lead is now waiting for a reply.`, { href: href("outreach", { tab: "wait", id }), label: "See it" });
  },
  async recordSentForm(form, e, ctx) {
    const f = form.elements;
    const id = form.dataset.id;
    const ok = await attempt("sent", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(id)}/outreach/sent`, { via: f.via.value, on: f.on.value }));
    if (ok) moveOn(ctx, null, `Recorded: ${f.via.value} sent. The lead is now waiting for a reply.`, { href: href("outreach", { tab: "wait", id }), label: "See it" });
  },

  undoEmail: undoEmailAct,
  sendNow: sendNowAct,
  secondForm: secondFormAct,
  async planAskForm(form, e, ctx) {
    const res = await attempt("plan", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(form.dataset.id)}/outreach/plan`, { comment: form.elements.comment.value.trim() || null }));
    if (!res) return;
    ui.mode = null;
    ctx.toast(`The plan is ready: start with ${res.plan.entry.name}. Nothing was sent.`);
    ctx.refresh();
  },
  async planDecide(t, e, ctx) {
    const ok = await attempt("plan", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/outreach/plan/decision`, { decision: t.dataset.decision }));
    if (ok) { ui.mode = null; ctx.toast("Plan approved. Each step comes to Needs you when it is due; nothing is sent without you."); }
    ctx.refresh();
  },
  async planRejectForm(form, e, ctx) {
    const note = form.elements.note.value.trim();
    if (e.type !== "submit") return missing(form, note.length < 5 ? "Say why." : "");
    const ok = await attempt("plan", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(form.dataset.id)}/outreach/plan/decision`, { decision: "reject", note }));
    if (ok) ui.mode = null;
    ctx.refresh();
  },
  async planPrepare(t, e, ctx) {
    const res = await attempt("plan", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/outreach/plan/steps/${t.dataset.n}/prepare`));
    if (res) ctx.toast(`Step ${t.dataset.n}: ${res.prepared}. Nothing was sent.`);
    ctx.refresh();
  },
  async planStepForm(form, e, ctx) {
    const note = form.elements.note.value.trim();
    const need = form.dataset.need === "1";
    if (e.type !== "submit") return missing(form, need && note.length < 3 ? "Write a few words." : "");
    const ok = await attempt("plan", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(form.dataset.id)}/outreach/plan/steps/${form.dataset.n}`, { action: form.dataset.action, note }));
    if (ok) { ui.mode = null; ctx.toast(form.dataset.action === "done" ? `Step ${form.dataset.n} recorded.` : `Step ${form.dataset.n} skipped. The plan moves on to the next one.`); }
    ctx.refresh();
  },
  async planStopForm(form, e, ctx) {
    const note = form.elements.note.value.trim();
    if (e.type !== "submit") return missing(form, note.length < 3 ? "Say why." : "");
    const ok = await attempt("plan", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(form.dataset.id)}/outreach/plan/stop`, { note }));
    if (ok) { ui.mode = null; ctx.toast("Plan stopped. No steps run after this."); }
    ctx.refresh();
  },
  async liDraft(t, e, ctx) {
    const ok = await attempt("linkedin", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/linkedin/draft`));
    if (ok) ctx.toast("The LinkedIn note is ready. Nothing was sent: copy it into LinkedIn yourself.");
    ctx.refresh();
  },
  async liSent(t, e, ctx) {
    const ok = await attempt("linkedin", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/linkedin/sent`, { what: t.dataset.what }));
    if (ok) ctx.toast(t.dataset.what === "note" ? "Recorded: LinkedIn connection request sent." : "Recorded: LinkedIn message sent.");
    ctx.refresh();
  },
  async liCopy(t, e, ctx) {
    try {
      await navigator.clipboard.writeText(t.dataset.text);
      ctx.toast("Copied. Paste it into LinkedIn.");
    } catch {
      ctx.toast("The browser did not allow copying. Select the text and copy it yourself.");
    }
  },
  async liForm(form, e, ctx) {
    const f = form.elements;
    for (const name of ["note", "message"]) {
      const line = form.querySelector(`[data-count=${name}]`);
      if (line) line.textContent = line.textContent.replace(/^\d+/, String(f[name].value.length));
    }
    const why = !f.note.value.trim() && !f.message.value.trim() ? "Write the note or the message." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    const ok = await attempt("linkedin", form.querySelector("[type=submit]"), ctx,
      () => ctx.api.put(`/opportunities/${enc(form.dataset.id)}/linkedin`, { note: f.note.value, message: f.message.value }));
    if (ok) { ui.mode = null; ctx.toast("Saved."); }
    ctx.refresh();
  },

  // inbox items: apply or dismiss
  async addSender(t, e, ctx) {
    const res = await attempt("addSender", t, ctx, () => ctx.api.post(`/inbox/${enc(t.dataset.item)}/add-sender`));
    if (!res) return ctx.toast(ui.error.message);
    ctx.toast(res.created ? `${res.name || res.email} added as a contact (role to confirm).` : `${res.name || res.email} is already a contact.`);
    ctx.refresh();
  },
  async inboxAct(t, e, ctx) {
    const body = { action: t.dataset.do };
    if (t.dataset.hash) body.payload_hash = t.dataset.hash;
    const res = await attempt("inbox", t, ctx, () => ctx.api.post(`/inbox/${enc(t.dataset.item)}`, body));
    if (!res) return ctx.toast(ui.error.message);
    moveOn(ctx, null, res.note || "Done.");
  },
  // Put the agent's own text back (it was kept when the person edited it).
  async restoreDraft(t, e, ctx) {
    const ok = await attempt("itemEdit", t, ctx, async () => {
      const cur = (await ctx.api.get("/inbox")).find((x) => x.id === t.dataset.item);
      if (!cur || !cur.data.original) throw new Error("The agent's draft could not be found.");
      return ctx.api.put(`/inbox/${enc(cur.id)}/draft`, { subject: cur.data.original.subject, body: cur.data.original.body, payload_hash: cur.payloadHash });
    });
    if (!ok) return ctx.toast(ui.error.message);
    ui.mode = null;
    ui.notice = [{ text: "The agent's draft is back. Approve it, or edit it again.", cls: "good" }];
    ctx.refresh();
  },

  // waiting and parked
  async followup(t, e, ctx) {
    const ok = await attempt("followup", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/outreach/followup`));
    if (!ok) return ctx.toast(ui.error.message);
    ui.notice = [{ text: "Follow-up recorded. The next follow-up date moved on.", cls: "good" }];
    ctx.refresh();
  },
  async replyForm(form, e, ctx) {
    const f = form.elements;
    if (e.type !== "submit") return missing(form, f.text.value.trim() ? "" : "Paste the reply.");
    if (!f.text.value.trim()) return;
    const id = form.dataset.id;
    const res = await attempt("reply", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(id)}/outreach/reply`, { text: f.text.value.trim(), on: f.on.value }));
    if (!res) return;
    const label = res.replyLabel === "pending" ? "The reply label is pending: label it by hand on the Qualification page." : `The Evidence agent labelled the reply: ${res.replyLabel}.`;
    moveOn(ctx, null, `Reply recorded; the lead is now Active. ${label}`, { href: href("qualification", { id }), label: "Open it" });
  },
  async itemEditForm(form, e, ctx) {
    const f = form.elements;
    countWords(form);
    const why = f.subject.value.trim() && f.body.value.trim() ? "" : "Write a subject and a message.";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    const ok = await attempt("itemEdit", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.put(`/inbox/${enc(form.dataset.item)}/draft`, { subject: f.subject.value.trim(), body: f.body.value.trim(), payload_hash: form.dataset.hash }));
    if (!ok) return;
    ui.mode = null;
    ui.notice = [{ text: "Saved your version. Approve and send it when it is ready.", cls: "good" }];
    ctx.refresh();
  },
  async nurtureForm(form, e, ctx) {
    const f = form.elements;
    if (e.type !== "submit") return missing(form, f.reason.value.trim() ? "" : "Give a reason.");
    if (!f.reason.value.trim()) return;
    const id = form.dataset.id;
    const ok = await attempt("nurture", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(id)}/nurture`, { reason: f.reason.value.trim(), review_on: f.review_on.value }));
    if (ok) moveOn(ctx, null, `Parked until ${day(f.review_on.value, ctx.today)}.`, { href: href("qualification", { tab: "parked", id }), label: "See it" });
  },
};
