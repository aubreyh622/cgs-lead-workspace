// Outreach: Step 5 (first email, follow-ups, nurture) and the first reply that makes a lead Active.
// Agents draft; a person approves the exact email and address before anything is sent.
import { html, icon, day, addDays, daysBetween, href, plural, initials, sourceLink, linkedinSearch } from "../lib.js";
import { enc } from "../api.js";
import { STAGE_LABELS, CLOSE_REASONS } from "../options.js";
import { emptyState, inlineError, crmLinks, crmConflicts, resolveCrm } from "../shell.js";
import { UNDO_SECONDS, offerUndo, undoEmail, waitingNotice, emailWaits, undoEmailAct, sendNowAct } from "../undo.js";

export const title = "Outreach";

const TABS = [
  ["approve", "To approve"],
  ["waiting", "Waiting for a reply"],
  ["nurture", "Nurture"],
  ["talking", "In conversation"],
];
const STAGES = ["identified", "contacted", "nurture", "active", "approval", "qualification"];
const PURPOSE = { first: "First email", followup: "Follow-up", reconnect: "Reconnect email" };
const CHANNELS = ["Email", "LinkedIn message", "Phone call", "Other"];
const REVISE_HINTS = ["Make it shorter.", "Use a warmer opening.", "Lead with the most recent news.", "Ask for a 20-minute call next week."];

// What the person has open. Cleared when they pick another item.
const ui = { mode: null, key: null, error: null, notice: null };

function resetUi(key = null) {
  ui.mode = null; ui.key = key; ui.error = null; ui.notice = null;
}

// ---------- work items ----------
// One list of things to approve, built from the agents' inbox and the accepted leads without a draft item.
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

// A client reply found in the email thread waits on the lead it answers (Waiting for a reply, or Nurture), so the person
// reads and records it where they were waiting for it. Leads with a reply come first.
function withReplies(opps, stage, kind, inbox) {
  const replies = Object.fromEntries(inbox.filter((it) => it.kind === "reply" && it.status === "new").map((it) => [it.opportunityId, it]));
  const items = opps.filter((o) => o.stage === stage).map((o) => (replies[o.id]
    ? { key: `item-${replies[o.id].id}`, kind: "reply", item: replies[o.id], opp: o }
    : { key: `opp-${o.id}`, kind, opp: o }));
  return [...items.filter((x) => x.kind === "reply"), ...items.filter((x) => x.kind !== "reply")];
}

// What waits for this person on Outreach (the navigation count): drafts and first emails to approve, and replies to record.
export function waitingFor(opps, inbox) {
  const replies = ["contacted", "nurture"].flatMap((s) => withReplies(opps, s, "", inbox)).filter((x) => x.kind === "reply");
  return approveItems(opps, inbox).length + replies.length;
}

export async function load(ctx, params) {
  const team = params.team === "1";
  const [all, inbox, toolkit] = await Promise.all([
    ctx.api.get(`/opportunities?${STAGES.map((s) => `stage=${s}`).join("&")}`),
    ctx.api.get(team ? "/inbox" : `/inbox?owner=${enc(ctx.me)}`),
    ctx.api.get("/toolkit").catch(() => ({ mail: { provider: "off", ready: false } })),
  ]);
  const opps = team ? all : all.filter((o) => o.owner === ctx.me);
  const lists = {
    approve: approveItems(opps, inbox),
    waiting: withReplies(opps, "contacted", "waiting", inbox),
    nurture: withReplies(opps, "nurture", "nurture", inbox),
    talking: opps.filter((o) => ["active", "approval", "qualification"].includes(o.stage)),
  };
  // A link from Home names an opportunity (id) or an inbox item (item); open the tab that holds it.
  let tab = TABS.some(([t]) => t === params.tab) ? params.tab : null;
  const wanted = params.item ? `item-${params.item}` : params.id ? `opp-${params.id}` : null;
  if (!tab && wanted) tab = ["approve", "waiting", "nurture"].find((t) => lists[t].some((x) => x.key === wanted)) || null;
  if (!tab && params.id) tab = lists.talking.some((o) => o.id === params.id) ? "talking" : null;
  const replied = (t) => lists[t].some((x) => x.kind === "reply");
  tab = tab || (replied("waiting") ? "waiting" : replied("nurture") ? "nurture" : lists.approve.length ? "approve" : lists.waiting.length ? "waiting" : "approve");
  if (params.tab !== tab) ctx.replace("outreach", { ...params, tab });  // the tab chosen now stays when the page redraws

  const data = { tab, team, lists, inbox, mail: toolkit.mail || { provider: "off", ready: false } };
  const list = lists[tab];
  if (tab !== "talking" && list.length) {
    const cur = list.find((x) => x.key === wanted) || list[0];
    if (ui.key !== cur.key) resetUi(cur.key);
    data.cur = cur;
    Object.assign(data, await itemData(ctx, cur));
  }
  return data;
}

// One item's details: the opportunity, other CGS work with it, and the email preview when there is a draft.
// Kept for a minute, so picking an item already fetched shows at once; the full load then checks it again.
const items = new Map();
const ITEM_TTL = 60 * 1000;

async function itemData(ctx, cur) {
  const id = cur.opp.id;
  const [detail, related] = await Promise.all([
    ctx.api.get(`/opportunities/${enc(id)}`),
    ctx.api.get(`/opportunities/${enc(id)}/related`).catch(() => null),
  ]);
  const out = { detail, related, preview: undefined };
  const hasFirstDraft = cur.kind === "first" && detail.outreach.status === "draft";
  if (cur.kind === "draft" || hasFirstDraft) {
    const q = cur.kind === "draft" ? `?itemId=${enc(cur.item.id)}` : "";
    out.preview = await ctx.api.get(`/opportunities/${enc(id)}/outreach/email${q}`).catch((e) => ({ error: e.message }));
  }
  items.set(cur.key, { at: Date.now(), value: out });
  return out;
}

function wantedKey(params) {
  return params.item ? `item-${params.item}` : params.id ? `opp-${params.id}` : null;
}

export function instant(ctx, params, last) {
  if (!last || params.tab !== last.tab || (params.team === "1") !== last.team || !last.lists[last.tab] || last.tab === "talking") return null;
  const cur = last.lists[last.tab].find((x) => x.key === wantedKey(params));
  const hit = cur && items.get(cur.key);
  if (!hit || Date.now() - hit.at > ITEM_TTL) return null;
  if (ui.key !== cur.key) resetUi(cur.key);
  return { ...last, cur, ...hit.value };
}

export function changed(early, d) {
  const pick = (x) => JSON.stringify([x.tab, x.cur && x.cur.key, x.detail, x.preview, (x.lists[x.tab] || []).map((i) => i.key)]);
  return pick(early) !== pick(d);
}

// After drawing: fetch the other items in this list, so picking one shows it at once.
export function after(root, ctx, d) {
  if (d.tab === "talking") return;
  for (const cur of (d.lists[d.tab] || []).slice(0, 12)) {
    const hit = items.get(cur.key);
    if (!hit || Date.now() - hit.at > ITEM_TTL) itemData(ctx, cur).catch(() => null);
  }
}

// ---------- view ----------
export function view(d, ctx) {
  const count = { approve: d.lists.approve.length, waiting: d.lists.waiting.length, nurture: d.lists.nurture.length, talking: d.lists.talking.length };
  const teamLink = href("outreach", { tab: d.tab, team: d.team ? undefined : "1" });
  return html`
  <div class="page-head">
    <div>
      <h1>Outreach</h1>
      <p>Emails the outreach agent wrote for ${d.team ? "the team's" : "your"} leads, and the leads waiting for a reply. Nothing is sent until a person approves it.</p>
    </div>
    <div class="actions">
      <a class="btn ghost" href="${teamLink}">${d.team ? "Show only mine" : "Show the whole team"}</a>
      ${d.mail.ready ? html`<button type="button" class="btn" data-act="syncMail">${icon("refresh", 16)}Check email now</button>` : ""}
    </div>
  </div>
  ${mailNotice(d.mail)}
  <div class="tabs" role="tablist" aria-label="Outreach" data-act="tabkeys" data-on="keydown">
    ${TABS.map(([id, label]) => html`
      <button type="button" role="tab" id="tab-${id}" aria-selected="${d.tab === id ? "true" : "false"}" aria-controls="panel"
        tabindex="${d.tab === id ? "0" : "-1"}" data-act="tab" data-tab="${id}">${label}<span class="count">${count[id]}</span></button>`)}
  </div>
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${d.tab === "talking" ? talkingView(d, ctx) : listAndDetail(d, ctx)}
  </div>`;
}

function mailNotice(mail) {
  if (mail.ready) return "";
  const why = mail.provider === "off" ? "Email is off in this workspace" : `The ${mail.provider} mailbox is not ready`;
  return html`<div class="notice" style="margin-bottom:20px">${icon("alert")}<span>${why}, so the workspace cannot send. Send from your own email or phone, then record what you sent here.</span></div>`;
}

function listAndDetail(d, ctx) {
  const list = d.lists[d.tab];
  if (!list.length) {
    const empty = {
      approve: ["Nothing to approve", "When you accept a company, the outreach agent drafts the first email and it appears here. Follow-ups appear when a lead has not replied.", { href: href("companies", { tab: "review" }), label: "Open companies to decide on" }],
      waiting: ["No one to wait for", "Leads move here when you send or record the first email."],
      nurture: ["Nothing in nurture", "Leads you move to nurture wait here until their review date."],
    }[d.tab];
    return html`<section class="card">${emptyState(...empty)}</section>`;
  }
  return html`
  <div class="split">
    <nav class="pick-list" aria-label="${TABS.find(([t]) => t === d.tab)[1]}">
      ${list.map((x) => pickCard(x, d, ctx))}
    </nav>
    <section class="card detail" aria-labelledby="detail-h">${detailView(d, ctx)}</section>
  </div>`;
}

function pickCard(x, d, ctx) {
  const o = x.opp;
  const link = x.item ? href("outreach", { tab: d.tab, item: x.item.id, team: d.team ? "1" : undefined })
    : href("outreach", { tab: d.tab, id: o.id, team: d.team ? "1" : undefined });
  const what = x.kind === "draft" ? `${PURPOSE[x.item.data.purpose] || "Email"} written by the ${x.item.agent.toLowerCase()}`
    : x.kind === "reply" ? html`<span class="good-text">Replied ${day(x.item.data.on || x.item.on, ctx.today)}: record the reply</span>`
    : x.kind === "first" ? "First email"
    : x.kind === "waiting" ? (o.next.due ? `Follow up ${day(o.next.due, ctx.today)}` : "Waiting")
    : `Review ${o.next.due ? day(o.next.due, ctx.today) : "date not set"}`;
  return html`
  <a class="pick" href="${link}" ${d.cur && d.cur.key === x.key ? html`aria-current="true"` : ""}>
    <span class="avatar" aria-hidden="true">${initials(o.contact.name || o.company)}</span>
    <span><b>${o.contact.name || "Contact to confirm"}</b><span class="meta">${o.company}</span><span class="meta-3" style="display:block">${what}</span></span>
  </a>`;
}

function detailView(d, ctx) {
  const k = d.cur.kind;
  if (k === "first") return firstEmail(d, ctx);
  if (k === "draft") return agentDraft(d, ctx);
  if (k === "reply") return replyItem(d, ctx);
  if (k === "waiting") return waiting(d, ctx);
  return nurtureView(d, ctx);
}

// ----- shared pieces -----
// "Skip for now": open the next item in the list without deciding this one.
function skipLink(d) {
  const list = d.lists[d.tab];
  const i = list.findIndex((x) => x.key === d.cur.key);
  const next = list[i + 1];
  if (!next) return "";
  const params = next.item ? { tab: d.tab, item: next.item.id } : { tab: d.tab, id: next.opp.id };
  return html`<a class="btn ghost skip-next" href="${href("outreach", { ...params, team: d.team ? "1" : undefined })}">Skip for now</a>`;
}
function header(heading, sub, extra) {
  return html`<div class="block"><h2 id="detail-h">${heading}</h2><p class="meta">${sub}</p>${extra || ""}</div>`;
}

function contactLine(o) {
  const c = o.contact;
  return `${c.title || "Role not known"}, ${o.company.name}`;
}

function contactBlock(o) {
  const c = o.contact;
  const confirmed = c.confirmedBy ? html`<span class="tag good">${icon("check", 14)}Confirmed by ${c.confirmedBy}</span>` : html`<span class="tag warn">Not confirmed by a person</span>`;
  const panel = ui.mode === "contact";
  return html`
  <div class="block stack">
    <div class="row-flex"><h3 class="sub-title" style="margin:0">Contact</h3>${confirmed}${crmLinks(o.crm)}</div>
    ${crmConflicts(o.id, o.crm)}
    <p>${c.name || "No person named yet"}${c.name ? html`, <span class="meta">${c.title}</span>` : ""}
      <a class="meta-3" style="margin-left:8px" href="${linkedinSearch([c.name || (c.title === "Contact to confirm" ? "" : c.title), o.company.name])}" target="_blank" rel="noopener noreferrer">Search on LinkedIn</a></p>
    <p class="meta">${c.email ? `Email: ${c.email}` : "No email address on file. Agents never guess one; add it when you send."}</p>
    ${panel ? html`
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

// Draft text with citation markers [[key|phrase]]: the phrase is highlighted and numbered; sources listed below.
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
      parts.push(html`<mark class="cite">${m[2]}</mark><sup class="cite-n">${n}</sup>`);
      last = m.index + m[0].length;
    }
    parts.push(lines(para.slice(last)));
    return html`<p>${parts}</p>`;
  });
  return html`
  <div class="block">
    <dl class="email-head">
      ${extra.to !== undefined ? html`<dt>To</dt><dd>${extra.to || html`<span class="meta">Address needed: add it when you approve</span>`}${extra.confirmed ? html` <span class="tag good">Confirmed</span>` : ""}</dd>` : ""}
      <dt>Subject</dt><dd><b>${subject || "(no subject)"}</b></dd>
    </dl>
    <div class="email-body">${paragraphs}</div>
    ${notes.length ? html`<ol class="sources">${notes.map((x) => html`<li>${x.fact ? html`${x.fact.text} <span class="meta-3">${sourceLink(x.fact.src, x.fact.date)}</span>` : `CGS library: ${x.key}`}</li>`)}</ol>` : ""}
    ${extra.checks ? checksRow(notes, extra) : ""}
  </div>`;
}

// What the page can verify about a draft, shown as the design's checks row. Nothing here is claimed without a check.
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
      <h3 class="sub-title">Move to nurture</h3>
      <p class="meta">The lead leaves the active list until the review date. Agents may draft a reconnect email then.</p>
      <div class="form-grid">
        <label class="field">Reason<input name="reason" required placeholder="For example: budget is set for 2027"></label>
        <label class="field">Review on<input type="date" name="review_on" value="${addDays(ctx.today, 60)}" min="${ctx.today}" required></label>
      </div>
      <div class="actions">
        <button type="submit" class="btn primary" disabled>Move to nurture</button>
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
      <p class="meta">Any reply makes the lead Active (In conversation) at once. The evidence agent labels it and reads it for qualification evidence, for you to review.</p>
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

// ----- first email from the Step 5 draft -----
function firstEmail(d, ctx) {
  const o = d.detail;
  const r = o.outreach;
  const hasDraft = r.status === "draft" && r.body;
  const facts = (o.brief && o.brief.facts) || [];
  const m = ui.mode;
  return html`
  ${header(`First email to ${o.contact.name || "the contact"}`, contactLine(o))}
  ${contactBlock(o)}
  ${relatedBlock(d.related)}
  ${noticeBlock()}
  ${waitingNotice(r.waiting)}
  ${hasDraft && m !== "edit" ? draftBody(r.subject, r.body, facts, { to: (d.preview && d.preview.to) || "", confirmed: !!o.contact.confirmedBy, checks: true, blocked: d.preview && d.preview.blocked }) : ""}
  ${!hasDraft && m !== "edit" ? html`<div class="block">${emptyState("No draft yet",
    "The outreach agent writes one when the lead is accepted. If none arrived, ask for one now, or write it yourself.")}</div>` : ""}
  ${m === "send" ? sendPanel(d) : ""}
  ${m === "revise" ? revisePanel(o, hasDraft) : ""}
  ${m === "edit" ? editPanel(o) : ""}
  ${m === "record" ? recordSentPanel(o, ctx) : ""}
  ${m === "nurture" ? nurturePanel(o, ctx) : ""}
  ${(!m || m === "contact") && !emailWaits(r.waiting) ? html`
  <div class="decision-bar">
    <div class="actions">
      ${hasDraft ? html`<button type="button" class="btn primary" data-act="mode" data-mode="send">Approve and send…</button>` : ""}
      <button type="button" class="btn ${hasDraft ? "" : "primary"}" data-act="mode" data-mode="revise">${hasDraft ? "Comment and revise…" : "Ask the outreach agent for a draft…"}</button>
      <button type="button" class="btn" data-act="mode" data-mode="edit">${hasDraft ? "Edit myself" : "Write it myself"}</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="record">Record as sent…</button>
      <button type="button" class="btn ghost" data-act="mode" data-mode="nurture">Move to nurture…</button>
      ${skipLink(d)}
    </div>
  </div>` : ""}`;
}

function revisePanel(o, hasDraft) {
  return html`
  <div class="block">
    <form class="panel-form" data-act="reviseForm" data-on="submit" data-id="${o.id}">
      <h3 class="sub-title">${hasDraft ? "Tell the outreach agent what to change" : "Ask the outreach agent for a draft"}</h3>
      <p class="meta">${hasDraft ? "It writes the draft again from the research brief and your comment. The new draft replaces this one." : "It writes from the research brief. Nothing is sent."}</p>
      <div class="chips">${REVISE_HINTS.map((c) => html`<button type="button" class="chip" data-act="chip" data-text="${c}">${c}</button>`)}</div>
      <label class="field">Your comment <span class="hint">Optional.</span><textarea name="angle" rows="3"></textarea></label>
      <label class="field" style="max-width:220px">Length<select name="tone"><option value="standard">Standard</option><option value="brief">Brief</option></select></label>
      <div class="actions">
        <button type="submit" class="btn primary">${hasDraft ? "Send to the agent" : "Write the draft"}</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
      </div>
      ${inlineError(ui.error && ui.error.where === "revise" ? ui.error.message : "")}
    </form>
  </div>`;
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
        <button type="submit" class="btn primary" ${r.subject && r.body ? "" : "disabled"}>Save draft</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing>${r.subject && r.body ? "" : "Write a subject and a message."}</span>
      </div>
      ${inlineError(ui.error && ui.error.where === "edit" ? ui.error.message : "")}
    </form>
  </div>`;
}

// ----- a draft the agent put in the inbox (first, follow-up or reconnect) -----
function agentDraft(d, ctx) {
  const o = d.detail;
  const it = d.cur.item;
  const purpose = it.data.purpose || "followup";
  const facts = [...((o.brief && o.brief.facts) || []), ...(it.data.facts || [])];
  const m = ui.mode;
  return html`
  ${header(`${PURPOSE[purpose] || "Email"} to ${o.contact.name || "the contact"}`, contactLine(o),
    html`<p class="meta-3">${it.agent}, ${day(it.on, ctx.today)}${it.expiresOn ? `. Expires ${day(it.expiresOn, ctx.today)} if nobody decides.` : ""}</p>`)}
  ${contactBlock(o)}
  ${relatedBlock(d.related)}
  ${noticeBlock()}
  ${waitingNotice(o.outreach.waiting)}
  ${it.data.editedBy ? html`<div class="block"><span class="tag info">Edited by ${it.data.editedBy}${it.data.editedOn ? `, ${day(it.data.editedOn, ctx.today)}` : ""}</span></div>` : ""}
  ${m === "editItem" ? itemEditPanel(it) : draftBody(it.data.subject, it.data.body, facts, { to: (d.preview && d.preview.to) || "", confirmed: !!o.contact.confirmedBy, checks: true, blocked: d.preview && d.preview.blocked })}
  ${m === "send" ? sendPanel(d, { itemId: it.id, hash: it.payloadHash }) : ""}
  ${(!m || m === "contact") && !emailWaits(o.outreach.waiting) ? html`
  <div class="decision-bar">
    <div class="actions">
      ${d.mail.ready ? html`<button type="button" class="btn primary" data-act="mode" data-mode="send">Approve and send…</button>` : ""}
      <button type="button" class="btn" data-act="mode" data-mode="editItem">Edit</button>
      <button type="button" class="btn ${d.mail.ready ? "" : "primary"}" data-act="inboxAct" data-do="apply" data-item="${it.id}" data-hash="${it.payloadHash}">
        ${purpose === "first" ? "Use it as my draft" : "Accept, I will send it myself"}</button>
      <button type="button" class="btn ghost" data-act="inboxAct" data-do="dismiss" data-item="${it.id}">Dismiss</button>
      ${skipLink(d)}
    </div>
    <p class="meta-3" style="margin-top:8px">${purpose === "first" ? "Use it as my draft: you can then edit it, ask for a revision, or send it." : "After you send it yourself, record the follow-up under Waiting for a reply."}</p>
  </div>` : ""}`;
}

function itemEditPanel(it) {
  return html`
  <div class="block">
    <form class="panel-form" data-act="itemEditForm" data-on="submit input" data-item="${it.id}" data-hash="${it.payloadHash}">
      <h3 class="sub-title">Edit the draft</h3>
      <p class="meta">Your version is the one you approve. The agent's text is kept, so it can learn from what you change. Text in [[f1|...]] marks a citation of the research brief: keep the mark, or delete it with its phrase. The client never sees the marks.</p>
      <label class="field">Subject<input name="subject" value="${it.data.subject || ""}" required></label>
      <label class="field">Message<textarea name="body" rows="12" required>${it.data.body || ""}</textarea></label>
      <div class="actions">
        <button type="submit" class="btn primary">Save my version</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing></span>
      </div>
      ${inlineError(ui.error && ui.error.where === "itemEdit" ? ui.error.message : "")}
    </form>
  </div>`;
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
  return html`
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
  <div class="block"><div class="email-plain">${it.data.text || it.body}</div></div>
  <div class="decision-bar">
    <div class="actions">
      <button type="button" class="btn primary" data-act="inboxAct" data-do="apply" data-item="${it.id}" data-hash="${it.payloadHash}">Record the reply</button>
      <button type="button" class="btn ghost" data-act="inboxAct" data-do="dismiss" data-item="${it.id}">Not a reply</button>
    </div>
    <p class="meta-3" style="margin-top:8px">Recording makes the lead Active. Choose Not a reply for an out-of-office or automatic message.</p>
  </div>`;
}

// ----- sent, waiting for a reply -----
function waiting(d, ctx) {
  const o = d.detail;
  const r = o.outreach;
  const pending = d.inbox.find((i) => i.opportunityId === o.id && i.kind === "draft" && i.status === "new");
  const m = ui.mode;
  const sent = r.sentOn ? `${r.status === "followup" ? "Follow-up" : "First message"} sent ${day(r.sentOn, ctx.today)}${r.via ? ` by ${r.via}` : ""}${r.toEmail ? ` to ${r.toEmail}` : ""}.` : "Sent.";
  return html`
  ${header(`${o.contact.name || "Contact"} at ${o.company.name}`, contactLine(o))}
  <div class="block stack">
    <p>${sent}</p>
    ${r.subject ? html`<p class="meta">Subject: ${r.subject}</p>` : ""}
    <p class="meta">${o.next.action ? `${o.next.action}, due ${day(o.next.due, ctx.today)}.` : "No next step set."}</p>
    ${pending ? html`<div class="notice info">${icon("inbox")}<span>The ${pending.agent.toLowerCase()} wrote a ${PURPOSE[pending.data.purpose]?.toLowerCase() || "draft"}. <a href="${href("outreach", { tab: "approve", item: pending.id })}">Review it</a></span></div>` : ""}
  </div>
  ${noticeBlock()}
  ${m === "reply" ? replyPanel(o, ctx) : ""}
  ${m === "nurture" ? nurturePanel(o, ctx) : ""}
  ${!m ? html`
  <div class="decision-bar"><div class="actions">
    <button type="button" class="btn primary" data-act="mode" data-mode="reply">Record a reply…</button>
    <button type="button" class="btn" data-act="followup" data-id="${o.id}">I sent a follow-up</button>
    <button type="button" class="btn ghost" data-act="mode" data-mode="nurture">Move to nurture…</button>
  </div></div>` : ""}`;
}

// Nurture: the client does not need this now but may later. The lead waits here until its review date.
function nurtureView(d, ctx) {
  const o = d.detail;
  const moved = [...o.activity].reverse().find((a) => /^Moved to nurture/.test(a.text));
  const reason = moved ? moved.text.replace(/^Moved to nurture( at Step 8)? by [^:.]+[:.]?\s*/, "").replace(/\s*\d+ unsent email draft\(s\) withdrawn\.?$/, "") : "";
  const by = moved && (moved.text.match(/ by ([^:.]+)/) || [])[1];
  const due = o.next.due;
  const left = due ? daysBetween(ctx.today, due) : null;
  const asked = [...o.activity].reverse().find((a) => /asked to reconnect now/.test(a.text) && a.on === ctx.today);
  const m = ui.mode;
  return html`
  ${header(`${o.contact.name || "Contact"} at ${o.company.name}`, contactLine(o))}
  ${noticeBlock()}
  <div class="block stack">
    <dl class="need">
      <dt>Why</dt><dd>${reason || "No reason was recorded."}</dd>
      <dt>Moved</dt><dd>${moved ? `${by ? `By ${by}, ` : ""}${day(moved.on, ctx.today)}` : "Not recorded"}</dd>
      <dt>Review</dt><dd>${due ? html`${day(due, ctx.today)} <span class="meta">${left > 0 ? `in ${plural(left, "day")}` : left === 0 ? "today" : `${plural(-left, "day")} ago`}</span>` : "No date set"}</dd>
    </dl>
    <div class="notice">${icon("clock")}<span>On the review date the research agent checks ${o.company.name} for news since the brief. If it finds some, the outreach agent drafts a reconnect email that cites it, for ${o.owner || "the owner"} to approve; if not, ${o.owner || "the owner"} is told and decides. Nothing is sent on its own.${o.crm && o.crm.leadId ? " Insightly shows the lead as Nurture until then." : ""}</span></div>
    ${asked && !ui.notice ? html`<div class="notice info">${icon("clock")}<span>Reconnect was asked for today. The research agent is checking for news; a notification follows when it is done.</span></div>` : ""}
  </div>
  ${m === "reply" ? replyPanel(o, ctx) : ""}
  ${m === "review" ? reviewDatePanel(o, ctx) : ""}
  ${m === "close" ? closePanel(o) : ""}
  ${!m ? html`
  <div class="decision-bar"><div class="actions">
    <button type="button" class="btn primary" data-act="reconnect" data-id="${o.id}" ${asked ? "disabled" : ""}>Reconnect now</button>
    <button type="button" class="btn" data-act="mode" data-mode="review">Change the review date…</button>
    <button type="button" class="btn" data-act="mode" data-mode="reply">Record a reply…</button>
    <button type="button" class="btn ghost" data-act="mode" data-mode="close">Close the lead…</button>
  </div>
  <p class="meta-3" style="margin-top:8px">Reconnect now does the review today instead of waiting for the date.</p></div>` : ""}`;
}

function reviewDatePanel(o, ctx) {
  return html`
  <div class="block">
    <form class="panel-form" data-act="reviewDateForm" data-on="submit input" data-id="${o.id}">
      <h3 class="sub-title">Change the review date</h3>
      <div class="form-grid">
        <label class="field">Review on<input type="date" name="review_on" value="${o.next.due || addDays(ctx.today, 30)}" min="${ctx.today}" required></label>
        <label class="field">Reason<input name="reason" required placeholder="For example: their budget round is in March"></label>
      </div>
      <div class="actions">
        <button type="submit" class="btn primary" disabled>Save the date</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing>Give a reason.</span>
      </div>
      ${inlineError(ui.error && ui.error.where === "review" ? ui.error.message : "")}
    </form>
  </div>`;
}

function closePanel(o) {
  return html`
  <div class="block">
    <form class="panel-form" data-act="closeForm" data-on="submit input change" data-id="${o.id}">
      <h3 class="sub-title">Close the lead</h3>
      <p class="meta">The lead leaves nurture for good and no reconnect email is drafted. The reason is kept with the decision.</p>
      <label class="field">Reason<select name="reason" required><option value="">Choose a reason</option>${CLOSE_REASONS.map((r) => html`<option>${r}</option>`)}</select></label>
      <label class="field">Comment <span class="hint">Needed when the reason is Other.</span><textarea name="comment" rows="2"></textarea></label>
      <div class="actions">
        <button type="submit" class="btn primary" disabled>Close the lead</button>
        <button type="button" class="btn ghost" data-act="mode" data-mode="">Cancel</button>
        <span class="missing" data-missing>Choose a reason.</span>
      </div>
      ${inlineError(ui.error && ui.error.where === "close" ? ui.error.message : "")}
    </form>
  </div>`;
}

// ----- after the reply -----
function talkingView(d, ctx) {
  const list = d.lists.talking;
  if (!list.length) return html`<section class="card">${emptyState("No one in conversation yet", "A lead moves here when the client replies.")}</section>`;
  return html`
  <section class="card">
    <div class="card-head"><h2>In conversation</h2><span class="meta">Qualification continues on the Qualification page</span></div>
    <div class="table-wrap"><table class="list">
      <thead><tr><th scope="col">Contact</th><th scope="col">Stage</th><th scope="col">Next step</th><th scope="col"><span class="sr-only">Open</span></th></tr></thead>
      <tbody>${list.map((o) => html`<tr>
        <td><b>${o.contact.name || "Contact"}</b><span class="meta">${o.company}</span></td>
        <td>${STAGE_LABELS[o.stage] || o.stage}</td>
        <td>${o.next.action || "None set"}${o.next.due ? html`<div class="meta-3">Due ${day(o.next.due, ctx.today)}</div>` : ""}</td>
        <td><a class="btn small" href="${href("qualification", { id: o.id })}">Open</a></td>
      </tr>`)}</tbody>
    </table></div>
  </section>`;
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

function moveOn(ctx, d, message, link) {
  resetUi();
  ctx.toast(message, link ? { link } : {});
  ctx.scrollTop = true;
  ctx.go("outreach", { tab: ctx.route.params.tab, team: ctx.route.params.team });
}

export const actions = {
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
    if (how === "draft") return moveOn(ctx, null, `The email is in your Gmail drafts for ${to}. Nothing was sent; the workspace records it once you send it from Gmail.`);
    const out = itemId ? res.sent : res;
    if (out && out.mode === "queued") {
      resetUi();
      offerUndo(ctx, `Approved. The email goes to ${to} in ${out.seconds} seconds.`, () => undoEmail(ctx, out.id), { seconds: out.seconds, key: `email:${out.id}` });
      ctx.scrollTop = true;
      return ctx.go("outreach", { tab: ctx.route.params.tab, team: ctx.route.params.team });
    }
    moveOn(ctx, null, `Sent to ${to}. The lead is now waiting for a reply.`, { href: href("outreach", { tab: "waiting", id }), label: "See it" });
  },
  async recordSentForm(form, e, ctx) {
    const f = form.elements;
    const id = form.dataset.id;
    const ok = await attempt("sent", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(id)}/outreach/sent`, { via: f.via.value, on: f.on.value }));
    if (ok) moveOn(ctx, null, `Recorded: ${f.via.value} sent. The lead is now waiting for a reply.`, { href: href("outreach", { tab: "waiting", id }), label: "See it" });
  },

  undoEmail: undoEmailAct,
  sendNow: sendNowAct,

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

  // waiting and nurture
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
    const label = res.replyLabel === "pending" ? "The reply label is pending: label it by hand on the Qualification page." : `The evidence agent labelled the reply: ${res.replyLabel}.`;
    moveOn(ctx, null, `Reply recorded; the lead is now In conversation. ${label}`, { href: href("qualification", { id }), label: "Open it" });
  },
  async itemEditForm(form, e, ctx) {
    const f = form.elements;
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
  async reconnect(t, e, ctx) {
    const res = await attempt("reconnect", t, ctx, () => ctx.api.post(`/opportunities/${enc(t.dataset.id)}/nurture/reconnect`));
    if (!res) return ctx.toast(ui.error.message);
    ui.notice = [{ text: res.queued ? "The research agent checks for news now. If it finds some, a reconnect draft follows for you to approve."
      : "The research agent is already checking this company.", cls: "good" }];
    ctx.refresh();
  },
  async reviewDateForm(form, e, ctx) {
    const f = form.elements;
    const why = !f.review_on.value ? "Choose a date." : !f.reason.value.trim() ? "Give a reason." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    const ok = await attempt("review", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(form.dataset.id)}/nurture/review`, { review_on: f.review_on.value, reason: f.reason.value.trim() }));
    if (!ok) return;
    ui.mode = null;
    ui.notice = [{ text: `The review is now on ${day(f.review_on.value, ctx.today)}.`, cls: "good" }];
    ctx.refresh();
  },
  async closeForm(form, e, ctx) {
    const f = form.elements;
    const why = !f.reason.value ? "Choose a reason." : f.reason.value === "Other" && !f.comment.value.trim() ? "Say why: the reason is Other." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    const ok = await attempt("close", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(form.dataset.id)}/close`, { reason: f.reason.value, comment: f.comment.value.trim() }));
    if (ok) moveOn(ctx, null, `Closed: ${f.reason.value}.`);
  },
  async nurtureForm(form, e, ctx) {
    const f = form.elements;
    if (e.type !== "submit") return missing(form, f.reason.value.trim() ? "" : "Give a reason.");
    if (!f.reason.value.trim()) return;
    const id = form.dataset.id;
    const ok = await attempt("nurture", form.querySelector("[type=submit]"), ctx, () =>
      ctx.api.post(`/opportunities/${enc(id)}/nurture`, { reason: f.reason.value.trim(), review_on: f.review_on.value }));
    if (ok) moveOn(ctx, null, `Moved to nurture until ${day(f.review_on.value, ctx.today)}.`, { href: href("outreach", { tab: "nurture", id }), label: "See it" });
  },
};
