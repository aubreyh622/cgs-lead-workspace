// Home: what waits for the person acting, grouped by what they must do (GET /api/todo, api/todo.py), then their next
// steps and their leads by stage. Workspace problems show only when there is one; a quiet workspace takes no space.
import { html, icon, day, daysBetween, href, plural } from "../lib.js";
import { enc } from "../api.js";
import { STAGE_LABELS } from "../options.js";
import { emptyState } from "../shell.js";
import { pageState } from "../forms.js";
import { linkFor } from "./links.js";

export const title = "Home";

const st = pageState();
const TAGS = { problem: "bad", waiting: "warn", decide: "info", check: "", steer: "" };
const BUTTONS = { done: "Done", dismiss: "Not useful", checked: "Checked" };
const TOASTS = { checked: "Marked checked. Approve the email again if it should still go.", dismiss: "Dismissed. Nothing changed.", done: "Done." };

export async function load(ctx) {
  const [todo, opps] = await Promise.all([ctx.api.get("/todo"), ctx.api.get(`/opportunities?owner=${enc(ctx.me)}`)]);
  return { todo, opps };
}

function whenLabel(due, today) {
  if (!due) return { text: "No date", cls: "" };
  const d = daysBetween(today, due);
  if (d < 0) return { text: `Overdue ${day(due, today)}`, cls: "overdue" };
  if (d === 0) return { text: "Today", cls: "today" };
  return { text: day(due, today), cls: "" };
}

function openLink(it) {
  if (it.open) return href(it.open.page, it.open.params);
  return it.opportunity ? linkFor(it.opportunity) : href("home");
}

function row(it, gid) {
  return html`<li>
    <span class="what"><span class="tag ${TAGS[gid]}">${it.label}</span><b>${it.title}</b><span class="meta">${it.sub}</span></span>
    <span class="acts">
      ${it.act.map((a) => html`<button type="button" class="btn small ghost" data-act="todo" data-do="${a}" data-group="${gid}"
        data-type="${it.ref.type}" data-id="${it.ref.id}" data-hash="${it.ref.hash || ""}">${BUTTONS[a] || a}</button>`)}
      <a class="btn small" href="${openLink(it)}">Open</a>
    </span>
  </li>`;
}

function group(g) {
  return html`<section class="card" aria-labelledby="todo-${g.id}">
    <div class="card-head"><h2 id="todo-${g.id}">${g.title} <span class="count" aria-label="${g.count} items">${g.count}</span></h2>
      <span class="meta">${g.why}</span></div>
    ${st.err(`todo:${g.id}`)}
    <ul class="rows">${g.items.map((it) => row(it, g.id))}</ul>
  </section>`;
}

export function view(data, ctx) {
  const { todo, opps } = data;
  const next = opps.filter((o) => o.next.action && !["rejected", "closed", "qualification"].includes(o.stage))
    .sort((a, b) => (a.next.due || "9999").localeCompare(b.next.due || "9999"));
  const stages = ["identified", "contacted", "active", "approval", "qualification", "nurture"];
  const byStage = stages.map((s) => [s, opps.filter((o) => o.stage === s).length]).filter(([, n]) => n);
  const clock = ctx.config && ctx.config.timeTravel ? " (simulated clock)" : "";

  return html`
  <div class="page-head">
    <div>
      <h1>Waiting for you</h1>
      <p>${todo.count ? `${plural(todo.count, "item")} for ${ctx.me}.` : `Nothing is waiting for ${ctx.me}.`} Workspace date ${day(ctx.today, "")}${clock}.</p>
    </div>
  </div>
  ${todo.health.length ? html`<div class="notice warn" role="status" style="margin-bottom:20px">${icon("alert")}<span>
    ${todo.health.map((h) => html`<span style="display:block">${h.text} <a href="${href(h.page)}">Open ${h.page}</a></span>`)}</span></div>` : ""}
  <div class="two-col">
    <div class="main stack">
      ${todo.groups.length ? todo.groups.map(group)
        : html`<section class="card">${emptyState("You are up to date", "New candidates arrive after the discovery search, and agent drafts appear here when they are ready.",
          { href: href("companies"), label: "Open companies" })}</section>`}
      <section class="card" aria-labelledby="next-h">
        <div class="card-head"><h2 id="next-h">Next steps on your leads</h2><span class="meta">The step each owner agreed</span></div>
        ${next.length ? html`<ul class="rows">${next.map((o) => {
          const w = whenLabel(o.next.due, ctx.today);
          return html`<li>
            <span class="when ${w.cls}">${w.text}</span>
            <span class="what"><b>${o.next.action}</b><span class="meta">${o.company}${o.contact.name ? `, ${o.contact.name}` : ""}</span></span>
            <a class="btn small" href="${linkFor(o)}">Open</a>
          </li>`;
        })}</ul>`
        : emptyState("No next steps", "Accept a company at Step 4 and name its next step; it appears here.")}
      </section>
    </div>
    <aside class="side">
      <section class="card card-body" aria-labelledby="stage-h">
        <h2 id="stage-h" class="section-title">Your pipeline</h2>
        ${byStage.length ? html`<ul class="pipeline">${byStage.map(([s, n]) => html`<li><span>${STAGE_LABELS[s]}</span>
          <span class="pipe-bar" aria-hidden="true"><span class="pipe-${s}" style="width:${Math.round((n / Math.max(...byStage.map((x) => x[1]))) * 100)}%"></span></span>
          <span class="num">${n}</span></li>`)}</ul>`
        : html`<p class="meta">You do not own any leads yet.</p>`}
      </section>
    </aside>
  </div>`;
}

export const actions = {
  // A row's own button: an agent's item done or not useful, or a failed / maybe-sent email checked. Nothing is sent.
  todo(t, e, ctx) {
    const { type, id, hash, group } = t.dataset;
    const what = t.dataset.do;
    st.write(`todo:${group}`, t, ctx,
      () => (type === "email" ? ctx.api.post(`/outbound/${enc(id)}/checked`)
        : ctx.api.post(`/inbox/${enc(id)}`, { action: what, ...(hash ? { payload_hash: hash } : {}) })),
      () => { ctx.toast(TOASTS[what] || "Done."); if (ctx.refreshCounts) ctx.refreshCounts(); });
  },
};
