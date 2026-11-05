// Home: the queue of things waiting for the person acting, then their next steps and their leads by stage.
import { html, day, daysBetween, href, plural } from "../lib.js";
import { STAGE_LABELS } from "../options.js";
import { emptyState } from "../shell.js";
import { linkFor } from "./links.js";

export const title = "Home";

export async function load(ctx) {
  const enc = encodeURIComponent;
  const [opps, cands, inbox, alerts] = await Promise.all([
    ctx.api.get("/opportunities"),
    ctx.api.get("/candidates?status=new"),
    ctx.api.get(`/inbox?owner=${enc(ctx.me)}`),
    ctx.api.get("/alerts"),
  ]);
  // Research that stopped needs a person; the list view does not carry research status, so ask for those few.
  const researching = opps.filter((o) => o.stage === "researching");
  const details = await Promise.all(researching.map((o) => ctx.api.get(`/opportunities/${enc(o.id)}`).catch(() => null)));
  return { opps, cands, inbox, alerts, stuck: details.filter((d) => d && d.research && ["failed", "paused"].includes(d.research.status)) };
}

function whenLabel(due, today) {
  if (!due) return { text: "No date", cls: "" };
  const d = daysBetween(today, due);
  if (d < 0) return { text: `Overdue ${day(due, today)}`, cls: "overdue" };
  if (d === 0) return { text: "Today", cls: "today" };
  return { text: day(due, today), cls: "" };
}

function queue(data, ctx) {
  const { opps, cands, inbox, alerts, stuck } = data;
  const byId = Object.fromEntries(opps.map((o) => [o.id, o]));
  const items = [];
  for (const a of alerts.filter((x) => x.owner === ctx.me)) {
    const o = byId[a.opportunityId];
    items.push({ rank: a.type === "overdue" ? 0 : 2, tag: a.type === "overdue" ? ["bad", "Overdue"] : ["warn", "Reminder"],
      title: a.message, sub: `${a.company}: ${a.title}`, link: o ? linkFor(o) : href("home") });
  }
  for (const i of inbox) {
    const o = byId[i.opportunityId];
    items.push({ rank: 1, tag: ["info", "Prepared by an agent"], title: i.title, sub: `${i.agent}, ${day(i.on, ctx.today)}${o ? `. ${o.company}` : ""}`,
      link: o && ["draft", "reply"].includes(i.kind) && ["identified", "contacted", "nurture"].includes(o.stage) ? href("outreach", { item: i.id })
        : o ? linkFor(o) : href("agents") });
  }
  for (const d of stuck) {
    const why = d.research.status === "failed" ? (d.research.error || "Research failed.") : d.research.pauseReason === "budget" ? "Paused: it reached its budget." : "Paused.";
    items.push({ rank: 1, tag: ["bad", "Research stopped"], title: `Research on ${d.company.name} needs you`, sub: why, link: href("companies", { tab: "research" }) });
  }
  for (const o of opps.filter((x) => x.stage === "approval" && x.owner !== ctx.me)) {
    items.push({ rank: 2, tag: ["warn", "Approval"], title: `Decide on the qualification pack for ${o.company}`,
      sub: `${o.title}. Sent by ${o.owner}.`, link: href("qualification", { tab: "approval", id: o.id }) });
  }
  for (const o of opps.filter((x) => x.stage === "active" && x.owner === ctx.me && x.assessment && x.assessment.readiness === "Ready")) {
    items.push({ rank: 2, tag: ["good", "Ready"], title: `Send the pack for ${o.company}`,
      sub: "The rules say Ready. Send it for approval.", link: href("qualification", { tab: "active", id: o.id }) });
  }
  const review = opps.filter((o) => o.stage === "review");
  for (const o of review) {
    items.push({ rank: 3, tag: ["warn", "Ready to decide"], title: `Decide on ${o.company}`, sub: `${o.title}. The research brief is ready.`,
      link: href("companies", { tab: "review", id: o.id }) });
  }
  if (cands.length) {
    items.push({ rank: 4, tag: ["", "New candidates"], title: `Pick which of ${plural(cands.length, "new candidate")} get researched`,
      sub: "Found by the discovery agent. Each one you pick gets a research brief.", link: href("companies", { tab: "new" }) });
  }
  return items.sort((a, b) => a.rank - b.rank);
}

export function view(data, ctx) {
  const items = queue(data, ctx);
  const mine = data.opps.filter((o) => o.owner === ctx.me);
  const next = mine.filter((o) => o.next.action && !["rejected", "closed", "qualification"].includes(o.stage))
    .sort((a, b) => (a.next.due || "9999").localeCompare(b.next.due || "9999"));
  const stages = ["identified", "contacted", "active", "approval", "qualification", "nurture"];
  const byStage = stages.map((s) => [s, mine.filter((o) => o.stage === s).length]).filter(([, n]) => n);
  const clock = ctx.config && ctx.config.timeTravel ? " (simulated clock)" : "";

  return html`
  <div class="page-head">
    <div>
      <h1>Waiting for you</h1>
      <p>${items.length ? `${plural(items.length, "item")} for ${ctx.me}.` : `Nothing is waiting for ${ctx.me}.`} Workspace date ${day(ctx.today, "")}${clock}.</p>
    </div>
  </div>
  <div class="two-col">
    <div class="main stack">
      <section class="card" aria-labelledby="queue-h">
        <div class="card-head"><h2 id="queue-h">To do</h2><span class="meta">Overdue first</span></div>
        ${items.length ? html`<ul class="rows">${items.map((it) => html`
          <li>
            <span class="tag ${it.tag[0]}">${it.tag[1]}</span>
            <span class="what"><b>${it.title}</b><span class="meta">${it.sub}</span></span>
            <a class="btn small" href="${it.link}">Open</a>
          </li>`)}</ul>`
        : emptyState("You are up to date", "New candidates arrive after the weekly discovery search, and agent drafts arrive in this list when they are ready.", { href: href("companies"), label: "Open companies" })}
      </section>
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
      <section class="card card-body" aria-labelledby="how-h">
        <h2 id="how-h" class="section-title">How work reaches you</h2>
        <p class="meta">Agents research and prepare. Nothing is sent, scored or written to the CRM until a person clicks.</p>
      </section>
    </aside>
  </div>`;
}
