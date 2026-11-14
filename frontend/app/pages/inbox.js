// Inbox (design/redesign/Inbox.dc.html): every decision that needs a person, from agents and teammates. The rows are
// GET /api/todo (api/todo.py); the rail's Inbox badge is todo.count, the number of rows under All.
//
// Tabs: All lists every row. Approvals, Company decisions and Agent proposals filter it; client replies, problems and
// due steps are only under All (the design has no tab for them). Done lists the agents' items people settled, and
// what was settled in this browser (REDESIGN_PLAN G6).
//
// Quick actions only where the API allows them without a form the row cannot hold. A row's key carries the record id
// (api/todo.py: "approve:<email id>", "proposal:<id>", "lesson:<id>"; tests/v2/test_frontend.py checks this).
import { html, icon, day, href, initials, plural } from "../lib.js";
import { enc } from "../api.js";
import { STAGE_LABELS } from "../options.js";
import { emptyState, inlineError } from "../shell.js";
import { UNDO_SECONDS, offerUndo, undoEmail } from "../undo.js";
import { linkFor } from "./links.js";
import { suggestion } from "./companies.js";

export const title = "Inbox";

const TABS = [["all", "All"], ["approval", "Approvals"], ["decision", "Company decisions"], ["proposal", "Agent proposals"], ["done", "Done"]];
const APPROVALS = ["Approve email", "Approve or return", "Answer the approver"];
const DECISIONS = ["Accept or reject", "Decide the contact plan", "Decide: nurture?"];
// Colours carry meaning: red is broken, orange waits on you, purple is what the agents prepared.
const TONE = { problem: "bad", waiting: "warn", decide: "warn", check: "info", steer: "info" };
const KIND = { approval: "Approval", decision: "Company decision", proposal: "Agent proposal", problem: "Problem", other: "Needs you" };
const BUTTONS = { done: "Done", dismiss: "Not useful", checked: "Checked" };
// The backend's labels where the page names the thing differently (the decision has three choices, not two).
const LABEL_SHOWN = { "Accept or reject": "Your decision" };
const labelOf = (r) => LABEL_SHOWN[r.label] || r.label;

export function typeOf(row) {
  if (APPROVALS.includes(row.label)) return "approval";
  if (DECISIONS.includes(row.label)) return "decision";
  if (row.group === "steer" || row.group === "check") return "proposal";
  return row.group === "problem" ? "problem" : "other";
}

// The record a row stands for, from its key (api/todo.py builds the keys).
export function recordOf(row) {
  const [kind, ...rest] = String(row.key || "").split(":");
  const id = rest.join(":");
  if (kind === "approve" && row.label === "Approve email") return { type: "outbound", id };
  if (kind === "proposal") return { type: "direction", id };
  if (kind === "lesson") return { type: "lesson", id };
  if (row.ref) return row.ref;
  return null;
}

// What was settled in this browser since it opened, newest first (shown under Done).
const settled = [];
const ui = { open: null, error: null };

export async function load(ctx, params) {
  const me = enc(ctx.me);
  const [todo, items] = await Promise.all([ctx.api.get("/todo"), ctx.api.get(`/inbox?owner=${me}&status=all`).catch(() => [])]);
  const rows = todo.groups.flatMap((g) => g.items);
  const tab = TABS.some(([t]) => t === params.tab) ? params.tab : "all";
  const list = tab === "all" ? rows : rows.filter((r) => typeOf(r) === tab);
  const sel = list.some((r) => r.key === params.sel) ? params.sel : list[0] && list[0].key;
  if (params.tab !== tab || params.sel !== sel) ctx.replace("inbox", { tab, sel });
  const done = items.filter((i) => i.status !== "new" && i.decidedBy === ctx.me);
  const data = { todo, rows, list, tab, sel, items, done };
  const cur = list.find((r) => r.key === sel);
  if (cur) Object.assign(data, await extra(ctx, cur));
  return data;
}

// What the selected row needs beyond the to-do line: the email waiting for a second approval, the lesson or the
// direction change it is about.
async function extra(ctx, row) {
  const rec = recordOf(row);
  try {
    if (rec && rec.type === "outbound" && row.opportunity) {
      const o = await ctx.api.get(`/opportunities/${enc(row.opportunity.id)}`);
      const w = o.outreach && o.outreach.waiting;
      return { email: w && String(w.id) === rec.id ? w : null };
    }
    if (row.label === "Accept or reject" && row.opportunity) return { brief: await ctx.api.get(`/opportunities/${enc(row.opportunity.id)}`) };
    if (rec && rec.type === "lesson") return { lesson: (await ctx.api.get("/lessons?status=proposed")).find((l) => String(l.id) === rec.id) || null };
    if (rec && rec.type === "direction") return { proposal: ((await ctx.api.get("/direction")).proposals || []).find((p) => String(p.id) === rec.id) || null };
  } catch { /* the row still shows; the detail says less */ }
  return {};
}

let keysOn = false;
export function after(root) {
  if (!keysOn) { root.addEventListener("keydown", inboxKey); keysOn = true; }
}

// J/K move through the list, E runs the selected row's main quick action (when it needs no form).
function inboxKey(e) {
  if (!/^#\/inbox/.test(location.hash)) return;
  if ((e.target && e.target.matches && e.target.matches("input, textarea, select")) || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = (e.key || "").toLowerCase();
  if (k === "e") {
    // E opens the place to decide, or runs an action that offers Undo. An action that cannot be undone (a direction
    // change, a lesson) only gets the focus: Enter then confirms it, so one stray key never decides.
    const b = document.querySelector(".inbox-detail [data-main]");
    if (b) { e.preventDefault(); if (b.matches("a, [data-undo]")) b.click(); else b.focus(); }
    return;
  }
  if (k !== "j" && k !== "k") return;
  const all = [...document.querySelectorAll(".inbox-list .pick")];
  const i = all.findIndex((a) => a.getAttribute("aria-current") === "true");
  const n = all[Math.max(0, Math.min(all.length - 1, i + (k === "j" ? 1 : -1)))];
  if (n && n !== all[i]) { e.preventDefault(); location.hash = n.getAttribute("href"); }
}

// ---------- view ----------
export function view(d, ctx) {
  const count = (t) => (t === "all" ? d.rows.length : t === "done" ? d.done.length + settled.length : d.rows.filter((r) => typeOf(r) === t).length);
  return html`
  <div class="page-head">
    <div>
      <h1>Inbox</h1>
      <p>Every decision that needs a person, from agents and teammates.</p>
    </div>
    <div class="head-side"><span class="meta-3"><span class="keys">J</span> <span class="keys">K</span> to move <span class="keys" style="margin-left:8px">E</span> for the main action</span></div>
  </div>
  ${d.todo.health.length ? html`<div class="notice warn" role="status" style="margin-bottom:20px">${icon("alert")}<span>
    ${d.todo.health.map((h) => html`<span style="display:block">${h.text} <a href="${href(h.page)}">Open it</a></span>`)}</span></div>` : ""}
  <div class="tabs" role="tablist" aria-label="Inbox" data-act="tabkeys" data-on="keydown">
    ${TABS.map(([id, label], i) => html`
      ${i === 4 ? html`<span class="gap" aria-hidden="true"></span>` : ""}
      <button type="button" role="tab" id="tab-${id}" aria-selected="${d.tab === id ? "true" : "false"}" aria-controls="panel"
        tabindex="${d.tab === id ? "0" : "-1"}" data-act="tab" data-tab="${id}">${label}<span class="count ${id === "all" && count(id) ? "you-count" : ""}">${count(id)}</span></button>`)}
  </div>
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${d.tab === "done" ? doneView(d, ctx) : !d.list.length ? html`<section class="card">${emptyState(d.tab === "all" ? "Nothing needs you right now" : "Nothing left in this view",
      "New items land here when an agent or a teammate needs a decision.", { href: href("overview"), label: "Back to Overview" })}</section>` : html`
    <div class="review inbox">
      <nav class="card queue inbox-list" aria-label="Items">
        ${d.list.map((r) => html`<a class="pick" href="${href("inbox", { tab: d.tab, sel: r.key })}" ${r.key === d.sel ? html`aria-current="true"` : ""}>
          ${r.opportunity ? html`<span class="avatar" aria-hidden="true">${initials(r.opportunity.company)}</span>` : html`<span class="avatar agent" aria-hidden="true">${icon("sparkle", 14)}</span>`}
          <span class="pick-text"><b>${shown(r.title)}</b><span class="meta-3 ellipsis">${r.sub}</span></span>
          <span class="tag ${TONE[r.group] || ""}">${labelOf(r)}</span></a>`)}
      </nav>
      <section class="card inbox-detail" aria-labelledby="detail-h">${detail(d, ctx)}</section>
    </div>`}
  </div>`;
}

// The to-do line as the backend words it, without the old step numbers ("Step 4: Acme" reads "Acme").
const shown = (title) => String(title).replace(/^Step \d+: /, "");

function detail(d, ctx) {
  const r = d.list.find((x) => x.key === d.sel);
  if (!r) return "";
  const t = typeOf(r);
  const rec = recordOf(r);
  const o = r.opportunity;
  const item = rec && rec.type === "inbox" ? d.items.find((i) => i.id === rec.id) : null;
  const group = d.todo.groups.find((g) => g.id === r.group);
  const facts = [];
  if (o) facts.push(["Company", o.company], ["Stage", STAGE_LABELS[o.stage] || o.stage]);
  if (d.email) facts.push(["To", d.email.to], ["Written by", d.email.by]);
  if (d.lesson) facts.push(["Agent", d.lesson.agent], ["Proposed by", `${d.lesson.proposedBy}, ${day(d.lesson.proposedOn, ctx.today)}`]);
  if (item) facts.push(["From", `${item.agent}, ${day(item.on, ctx.today)}`]);
  if (d.brief && d.brief.brief) {
    const b = d.brief.brief, c = d.brief.contact || {};
    const fl = b.facts || [];
    if (b.hypothesis) facts.push(["Why now", b.hypothesis]);
    facts.push(["Contact", c.name ? `${c.name}${c.title ? `, ${c.title}` : ""}` : "None named yet"]);
    facts.push(["Sources", `${plural(fl.length, "fact")} from ${plural(new Set(fl.map((f) => f.src)).size, "source")}`]);
    const open = [(b.flags || []).length && plural(b.flags.length, "flag"), (b.unknowns || []).length && plural(b.unknowns.length, "unknown")].filter(Boolean);
    if (open.length) facts.push(["Open questions", open.join(", ")]);
  }
  return html`
  <div class="block">
    <span class="tag ${TONE[r.group] || ""}">${KIND[t]}: ${labelOf(r)}</span>
    <h2 id="detail-h" style="margin-top:8px">${shown(r.title)}</h2>
    <p class="meta">${r.sub}</p>
  </div>
  <div class="block stack">
    ${body(r, d, item)}
    ${facts.length ? html`<dl class="dl">${facts.map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>` : ""}
    <p class="meta-3">${group ? group.why : ""}</p>
  </div>
  <div class="block">${buttons(r, d, ctx)}${inlineError(ui.error && ui.error.key === r.key ? ui.error.message : "")}</div>`;
}

function body(r, d, item) {
  // A company decision: the Research agent's suggestion in the decision screen's own words. The choice itself is made
  // there (Accept needs an owner, a rating and a next step).
  if (d.brief && d.brief.brief) {
    const s = suggestion(d.brief.brief.recommendation, d.brief);
    return html`<div class="band"><div class="band-text"><span class="band-label">${s.label}</span><b class="band-title">${s.title}</b>
      ${s.body ? html`<p class="meta">${s.body}</p>` : ""}${s.note ? html`<p class="meta-3">${s.note}</p>` : ""}</div></div>`;
  }
  if (d.email) {
    return html`<div class="email-plain"><b>${d.email.subject}</b>\n\n${d.email.body || ""}</div>
      <p class="meta">It goes out as ${d.email.by}, from their mailbox, ${UNDO_SECONDS} seconds after you approve it. Nothing is sent before.</p>`;
  }
  if (d.lesson) return html`<p>${d.lesson.text}</p>${d.lesson.why ? html`<p class="meta">Why: ${d.lesson.why}</p>` : ""}`;
  if (d.proposal) return html`<p>${d.proposal.rationale || ""}</p>`;
  if (item && item.body) return html`<p class="email-plain">${item.body}</p>`;
  return "";
}

// The row's buttons: a quick action where the API allows one, otherwise the link that opens the place to decide.
function buttons(r, d, ctx) {
  const rec = recordOf(r);
  const open = r.open ? href(r.open.page, r.open.params) : r.opportunity ? linkFor(r.opportunity) : href("overview");
  const openLabel = { "Approve or return": "Open the pack", "Accept or reject": "Read the full brief", "Pick candidates": "Pick companies",
    "New candidates": "Pick companies", "Decide the contact plan": "Open the plan", "Approve email": "Open the email" }[r.label] || "Open it";
  if (rec && rec.type === "outbound" && d.email && d.email.status === "approval") {
    return html`
    ${ui.open === `return:${r.key}` ? html`<form class="panel-form" data-act="returnEmail" data-on="submit input" data-id="${rec.id}" data-key="${r.key}">
      <label class="field">What ${d.email.by} should change<textarea name="note" rows="2" required></textarea></label>
      <div class="actions"><button type="submit" class="btn primary" disabled>Return to ${d.email.by}</button>
        <button type="button" class="btn ghost" data-act="openForm" data-open="">Cancel</button><span class="missing" data-missing>Say what to change.</span></div>
    </form>` : html`<div class="actions">
      <button type="button" class="btn primary" data-main data-undo data-act="approveEmail" data-id="${rec.id}" data-key="${r.key}">Approve and send</button>
      <button type="button" class="btn" data-act="openForm" data-open="return:${r.key}">Return to ${d.email.by}…</button>
      <span class="spacer"></span><a class="btn ghost" href="${open}">${openLabel}</a></div>`}`;
  }
  if (rec && rec.type === "direction") {
    return html`<div class="actions">
      <button type="button" class="btn primary" data-main data-act="direction" data-verdict="approve" data-id="${rec.id}" data-key="${r.key}">Apply</button>
      <button type="button" class="btn" data-act="direction" data-verdict="reject" data-id="${rec.id}" data-key="${r.key}">Dismiss</button>
      <span class="spacer"></span><a class="btn ghost" href="${href("agents")}">Open Agent hub</a></div>`;
  }
  if (rec && rec.type === "lesson") {
    if (!ctx.admin) return html`<p class="meta">Only an admin approves or rejects lessons.</p><div class="actions"><a class="btn" href="${href("agents")}">Open Agent hub</a></div>`;
    return html`
    ${ui.open === `reject:${r.key}` ? html`<form class="panel-form" data-act="rejectLesson" data-on="submit input" data-id="${rec.id}" data-key="${r.key}">
      <label class="field">Why reject it <span class="hint">The manager agent reads this before it proposes again.</span><textarea name="reason" rows="2" required></textarea></label>
      <div class="actions"><button type="submit" class="btn primary" disabled>Reject the lesson</button>
        <button type="button" class="btn ghost" data-act="openForm" data-open="">Cancel</button><span class="missing" data-missing>Say why.</span></div>
    </form>` : html`<div class="actions">
      <button type="button" class="btn primary" data-main data-act="approveLesson" data-id="${rec.id}" data-key="${r.key}">Approve lesson</button>
      <button type="button" class="btn" data-act="openForm" data-open="reject:${r.key}">Reject…</button>
      <span class="spacer"></span><a class="btn ghost" href="${href("agents")}">Open Agent hub</a></div>`}`;
  }
  const acts = r.act || [];
  return html`<div class="actions">
    ${acts.length ? "" : html`<a class="btn primary" data-main href="${open}">${openLabel}</a>`}
    ${acts.map((a, i) => html`<button type="button" class="btn ${i === 0 ? "primary" : ""}" ${i === 0 ? html`data-main` : ""} data-act="rowAct" data-do="${a}"
      data-type="${rec ? rec.type : ""}" data-id="${rec ? rec.id : ""}" data-hash="${(rec && rec.hash) || ""}" data-key="${r.key}" data-title="${r.title}">${BUTTONS[a] || a}</button>`)}
    ${acts.length ? html`<span class="spacer"></span><a class="btn ghost" href="${open}">${openLabel}</a>` : ""}
  </div>`;
}

function doneView(d, ctx) {
  const rows = [...settled.map((x) => ({ title: x.title, outcome: x.outcome, when: "Just now" })),
    ...d.done.map((i) => ({ title: i.title, outcome: `${i.status === "dismissed" ? "Not useful" : "Done"}, ${i.agent}`, when: day(i.on, ctx.today) }))];
  if (!rows.length) return html`<section class="card">${emptyState("Nothing settled yet", "Agent items you mark done or not useful are listed here.")}</section>`;
  return html`<section class="card" style="max-width:820px">
    <ul class="rows">${rows.map((x) => html`<li><span class="what">${icon("check", 16)}<b>${x.title}</b><span class="meta">${x.outcome}</span></span><span class="meta-3">${x.when}</span></li>`)}</ul>
    <p class="meta-3" style="padding:12px 20px">Agent items you settled, and what you settled in this browser. Approvals and decisions are recorded on each company's page.</p>
  </section>`;
}

// ---------- actions ----------
// After a quick action: the row leaves the list and the next one is selected.
function settle(ctx, key, title, outcome, toast) {
  settled.unshift({ title, outcome });
  ui.open = null; ui.error = null;
  const keys = [...document.querySelectorAll(".inbox-list .pick")].map((a) => new URLSearchParams(a.getAttribute("href").split("?")[1]).get("sel"));
  const i = keys.indexOf(key);
  const next = keys[i + 1] || keys[i - 1];
  if (toast) ctx.toast(toast);
  if (ctx.refreshCounts) ctx.refreshCounts();
  ctx.go("inbox", { tab: ctx.route.params.tab, sel: next });
}

async function run(t, key, ctx, fn) {
  t.disabled = true;
  try { return await fn(); } catch (e) { ui.error = { key, message: e.message }; ctx.refresh(); return undefined; } finally { if (t.isConnected) t.disabled = false; }
}

function missing(form, text) {
  form.querySelectorAll("[type=submit]").forEach((b) => { b.disabled = !!text; });
  const line = form.querySelector("[data-missing]");
  if (line) line.textContent = text || "";
}

export const actions = {
  tab(t, e, ctx) { ui.open = null; ctx.go("inbox", { tab: t.dataset.tab }); },
  tabkeys(t, e, ctx) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    const tabs = [...t.querySelectorAll("[role=tab]")];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const n = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : e.key === "Home" ? 0 : tabs.length - 1;
    ctx.focusAfter = `#${tabs[n].id}`;
    ctx.go("inbox", { tab: tabs[n].dataset.tab });
  },
  openForm(t, e, ctx) {
    ui.open = t.dataset.open || null;
    ui.error = null;
    ctx.focusAfter = ui.open ? ".inbox-detail textarea" : ".inbox-detail [data-main]";
    ctx.refresh();
  },
  // An agent's item done or not useful, or a failed / maybe-sent email checked. Nothing is sent.
  async rowAct(t, e, ctx) {
    const { type, id, hash, key } = t.dataset;
    const what = t.dataset.do;
    const ok = await run(t, key, ctx, () => (type === "email" ? ctx.api.post(`/outbound/${enc(id)}/checked`)
      : ctx.api.post(`/inbox/${enc(id)}`, { action: what, ...(hash ? { payload_hash: hash } : {}) })));
    if (ok === undefined) return;
    const toast = { checked: "Marked checked. Approve the email again if it should still go.", dismiss: "Marked not useful. Nothing changed.", done: "Done." }[what] || "Done.";
    settle(ctx, key, t.dataset.title, BUTTONS[what] || what, toast);
  },
  // The second approval: the email goes out after the undo window, from the writer's mailbox.
  async approveEmail(t, e, ctx) {
    const key = t.dataset.key;
    const res = await run(t, key, ctx, () => ctx.api.post(`/outbound/${enc(t.dataset.id)}/second-approval`, { decision: "approve", note: "", delay: UNDO_SECONDS }));
    if (!res) return;
    settle(ctx, key, `Email to ${res.to}`, "Approved and sent");
    offerUndo(ctx, `Approved. The email goes to ${res.to} in ${res.seconds} seconds.`, () => undoEmail(ctx, res.id), { seconds: res.seconds, key: `email:${res.id}` });
  },
  async returnEmail(form, e, ctx) {
    const note = form.elements.note.value.trim();
    if (e.type !== "submit") return missing(form, note.length < 3 ? "Say what to change." : "");
    if (note.length < 3) return;
    const ok = await run(form.querySelector("[type=submit]"), form.dataset.key, ctx,
      () => ctx.api.post(`/outbound/${enc(form.dataset.id)}/second-approval`, { decision: "return", note, delay: 0 }));
    if (ok) settle(ctx, form.dataset.key, "Email returned", "Returned with your note", "Returned with your note. Nothing was sent.");
  },
  async direction(t, e, ctx) {
    const approve = t.dataset.verdict === "approve";
    const ok = await run(t, t.dataset.key, ctx, () => ctx.api.post(`/direction/proposals/${enc(t.dataset.id)}/${approve ? "approve" : "reject"}`));
    if (ok) settle(ctx, t.dataset.key, "Direction change", approve ? "Applied" : "Dismissed", approve ? "Applied. The agents search with the new direction from now on." : "Dismissed. Nothing changed.");
  },
  async approveLesson(t, e, ctx) {
    const ok = await run(t, t.dataset.key, ctx, () => ctx.api.post(`/lessons/${enc(t.dataset.id)}/decision`, { decision: "approve" }));
    if (ok) settle(ctx, t.dataset.key, `Lesson ${ok.label || ""}`.trim(), "Approved", "Lesson approved. It applies to the agent's new work.");
  },
  async rejectLesson(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason.length < 3 ? "Say why." : "");
    if (reason.length < 3) return;
    const ok = await run(form.querySelector("[type=submit]"), form.dataset.key, ctx,
      () => ctx.api.post(`/lessons/${enc(form.dataset.id)}/decision`, { decision: "reject", reason }));
    if (ok) settle(ctx, form.dataset.key, `Lesson ${ok.label || ""}`.trim(), "Rejected", "Lesson rejected. The manager agent reads your reason.");
  },
};
