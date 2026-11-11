// Sources: where companies come from. Find candidates now, add a company by hand, the research direction the agents
// follow, changes proposed to it (a person approves each one), and what earlier searches taught.
import { html, icon, day, href, plural } from "../lib.js";
import { enc } from "../api.js";
import { emptyState } from "../shell.js";
import { pageState, missing, tabBar, tabKeys, cancelButton, openButton } from "../forms.js";

const SEARCH_NAMES = { mock: "the synthetic internet", tavily: "the web (Tavily)" };

// Who approved the direction in use. The first live direction comes from the project brief; its note was written for the
// prototype, so it is shown as what it is: not yet approved by CGS.
function approval(cur) {
  if (!cur || !cur.approvedBy) return "";
  if (/not (yet )?(approved by )?CGS|not CGS approval/i.test(cur.approvedBy)) return ", the starting direction, not yet approved by CGS";
  return `, approved by ${cur.approvedBy}`;
}

export const title = "Sources";

const TABS = [["find", "Find companies"], ["direction", "Research direction"], ["learned", "What searches taught"]];
const FIELDS = [
  ["problem", "Problem CGS looks for"],
  ["signals", "Signals"],
  ["buyer_roles", "Buyer roles"],
  ["exclusions", "Exclusions"],
  ["excluded_domains", "Excluded websites"],
  ["company_size", "Company size"],
];
const SIZE = [["min_employees", "Fewest employees"], ["max_employees", "Most employees"], ["min_revenue_musd", "Lowest revenue (USD millions)"], ["max_revenue_musd", "Highest revenue (USD millions)"]];
const SOURCES = ["Conference", "Referral", "Event attendee list", "Inbound enquiry", "Other"];
const FIELD_LABEL = Object.fromEntries([...FIELDS, ["problems", "Problems to watch for"]]);
// Automatic searches (api/discovery_searches.py): how often, on which weekday, and how many companies per run.
const FREQS = [["day", "Every day"], ["3days", "Every 3 days"], ["week", "Every week"], ["2weeks", "Every 2 weeks"], ["4weeks", "Every 4 weeks"]];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
const COUNTS = [3, 5, 10];

const st = pageState();
let field = "signals"; // the field chosen in the "propose a change" form

export async function load(ctx, params) {
  const tab = TABS.some(([t]) => t === params.tab) ? params.tab : "find";
  const [direction, search] = await Promise.all([ctx.api.get("/direction"), ctx.api.get("/search/settings").catch(() => null)]);
  const data = { tab, direction, search };
  if (tab === "find") data.searches = await ctx.api.get("/discovery/searches").catch((e) => ({ error: e.message }));
  if (tab === "learned") {
    [data.memory, data.feedback] = await Promise.all([ctx.api.get("/discovery/memory"), ctx.api.get("/feedback")]);
  }
  return data;
}

export function view(d, ctx) {
  const pending = d.direction.proposals.filter((p) => p.status === "pending").length;
  return html`
  <a class="back" href="${href("companies")}">${icon("back", 16)}Companies</a>
  <div class="page-head" style="margin-top:12px">
    <div>
      <h1>Where companies come from</h1>
      <p>Ask the discovery agent for companies now, add one yourself, and set what the agents look for. Anyone may propose a change to the direction; an admin approves changes to the team's direction.</p>
    </div>
  </div>
  ${st.notices()}
  ${tabBar("Sources", TABS, d.tab, { direction: pending || undefined })}
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${d.tab === "find" ? findView(d, ctx) : d.tab === "direction" ? directionView(d, ctx) : learnedView(d, ctx)}
  </div>`;
}

// ----- find -----
function findView(d, ctx) {
  const s = d.search;
  return html`
  <div class="two-col">
    <div class="main stack">
      <section class="card" aria-labelledby="rec-h">
        <div class="card-head"><div><h2 id="rec-h">Ask the discovery agent</h2>
          <p class="meta">It searches recent news for companies that match the research direction and checks every result before you see it.</p></div></div>
        <form class="card-body panel-form" data-act="recommendForm" data-on="submit">
          <label class="field">What to look for, in your own words <span class="hint">Optional. Empty means the research direction alone.</span>
            <textarea name="intent" rows="3" placeholder="For example: mid-size food manufacturers opening a new plant this year"></textarea></label>
          ${countPicker("count", 10, "How many companies, at most")}
          <div class="actions"><button type="submit" class="btn primary">Find candidates</button>
            ${s ? html`<span class="meta-3">Searches ${SEARCH_NAMES[s.provider] || s.provider}, news from the last ${s.recency_days} days.</span>` : ""}</div>
          ${st.err("recommend")}
        </form>
      </section>
      ${searchesCard(d, ctx)}
      <section class="card" aria-labelledby="add-h">
        <div class="card-head"><div><h2 id="add-h">Add a company yourself</h2>
          <p class="meta">For a referral, a conference contact or an inbound enquiry. Research starts at once, and the company then waits for a decision like any other.</p></div></div>
        <form class="card-body panel-form" data-act="addForm" data-on="submit input">
          <div class="form-grid">
            <label class="field">Company<input name="company" required></label>
            <label class="field">Website <span class="hint">For example company.com</span><input name="domain"></label>
          </div>
          <div class="form-grid">
            <label class="field">Contact name <span class="hint">Optional</span><input name="name"></label>
            <label class="field">Contact role <span class="hint">Optional</span><input name="title"></label>
            <label class="field">Where it came from<select name="source">${SOURCES.map((x) => html`<option>${x}</option>`)}</select></label>
          </div>
          <label class="field">Notes <span class="hint">What you know: why now, who introduced you.</span><textarea name="notes" rows="3"></textarea></label>
          <div class="actions"><button type="submit" class="btn primary" disabled>Add and research</button><span class="missing" data-missing>Give the company name.</span></div>
          ${st.err("add")}
        </form>
      </section>
    </div>
    <aside class="side">
      <section class="card card-body">
        <h2 class="section-title">The direction in use</h2>
        <p class="meta">Version ${d.direction.current ? d.direction.current.version : "none"}${approval(d.direction.current)}.</p>
        ${d.direction.current ? html`<p style="margin-top:8px">${d.direction.current.problem}</p>` : ""}
        <p style="margin-top:12px"><a href="${href("sources", { tab: "direction" })}">See or change the direction</a></p>
      </section>
    </aside>
  </div>`;
}

function countPicker(name, current, legend) {
  const counts = COUNTS.includes(current) ? COUNTS : [...COUNTS, current].sort((a, b) => a - b);
  return html`<fieldset class="rating"><legend>${legend}</legend>
    ${counts.map((n) => html`<label><input type="radio" name="${name}" value="${n}" ${n === current ? "checked" : ""}><span>${n}</span></label>`)}</fieldset>`;
}

function searchesCard(d, ctx) {
  const list = d.searches;
  return html`
  <section class="card" aria-labelledby="auto-h">
    <div class="card-head"><div><h2 id="auto-h">Automatic searches</h2>
      <p class="meta">The discovery agent runs these on its own, at the daily check. Results wait in Companies, New candidates, for you to pick or skip.</p></div>
      ${st.ui.open === "search:new" ? "" : openButton("search:new", "New automatic search", "btn small")}</div>
    ${st.ui.open === "search:new" ? html`<div class="card-body">${searchForm(null)}</div>` : ""}
    ${list.error ? html`<div class="card-body"><div class="notice bad">${icon("alert")}<span>${list.error}</span></div></div>`
      : html`<ul class="rows">${list.map((x) => searchRow(x, ctx))}</ul>`}
    ${st.err("searches")}
  </section>`;
}

function searchRow(x, ctx) {
  const editing = st.ui.open === `search:${x.id}`;
  const when = `${x.freqLabel}${x.weekdayLabel ? `, ${x.weekdayLabel}` : ""}`;
  const last = x.lastRound ? `Last run ${day(x.lastRound.on, ctx.today)}: ${plural(x.lastRound.found, "company", "companies")}` : x.lastRunOn ? `Last run ${day(x.lastRunOn, ctx.today)}` : "Not run yet";
  return html`<li class="search-row ${x.on ? "" : "off"}">
    <div class="search-line">
      <span class="what"><b>${x.name}</b><span class="meta">${x.main ? "Uses the research direction alone." : x.query}</span>
        ${x.main ? "" : html`<span class="meta-3">Added by ${x.createdBy}; uses their direction.</span>`}</span>
      <span class="search-when"><b>${when}</b><span class="meta">${plural(x.perRun, "company", "companies")} per run</span>
        <span class="meta">${x.on ? `Next: ${day(x.nextRunOn, ctx.today)}` : "Off"}</span></span>
      <span class="search-result meta">${last}${x.found ? html`<br>${x.found} found, ${x.picked} picked, ${x.skipped} skipped` : ""}
        ${x.lastError ? html`<br><span class="bad-text">Last attempt failed: ${x.lastError}</span>` : ""}</span>
      ${editing ? html`<span></span>` : html`<button type="button" class="btn small" data-act="open" data-open="search:${x.id}" aria-label="Edit ${x.name}">Edit</button>`}
      <button type="button" class="switch" role="switch" aria-checked="${x.on ? "true" : "false"}" aria-label="${x.name}: run automatically"
        data-act="toggleSearch" data-id="${x.id}" data-turn-on="${x.on ? "" : "1"}"><span></span></button>
    </div>
    ${editing ? searchForm(x) : ""}
  </li>`;
}

function searchForm(x) {
  const isNew = !x;
  const main = x && x.main;
  return html`
  <form class="panel-form search-form" data-act="searchForm" data-on="submit input" data-id="${x ? x.id : ""}" data-main="${main ? "1" : ""}">
    ${isNew ? html`<h3 class="sub-title" style="margin:0">New automatic search</h3>` : ""}
    ${main ? html`<p class="meta">The main search uses the research direction alone. Change the direction itself under Research direction.</p>` : html`
    <label class="field">Name<input name="name" value="${x ? x.name : ""}" placeholder="For example: New plant announcements" required></label>
    <label class="field">What to look for <span class="hint">Describe the companies and the signal, as you would to a researcher. Your own research direction applies too.</span>
      <textarea name="query" rows="2" required>${x ? x.query : ""}</textarea></label>`}
    <div class="form-grid">
      <label class="field">How often<select name="freq">${FREQS.map(([v, l]) => html`<option value="${v}" ${(x ? x.freq : "week") === v ? "selected" : ""}>${l}</option>`)}</select></label>
      <label class="field">On <span class="hint">Weekly or less often only</span><select name="weekday">
        <option value="">Any day</option>${WEEKDAYS.map((w, i) => html`<option value="${i}" ${x && x.weekday === i ? "selected" : ""}>${w}</option>`)}</select></label>
    </div>
    ${countPicker("per_run", x ? x.perRun : 5, "Companies per run")}
    <p class="meta">Each run is one discovery round: a few AI requests and up to 6 web searches, counted in the agents' daily AI limit.</p>
    <div class="actions">
      <button type="submit" class="btn primary" ${main ? "" : "disabled"}>${isNew ? "Save search" : "Save changes"}</button>${cancelButton}
      ${x && !main ? html`<button type="button" class="btn ghost" data-act="deleteSearch" data-id="${x.id}">Delete search</button>` : ""}
      <span class="missing" data-missing>${main ? "" : "Add a name and what to look for."}</span>
    </div>
    ${st.err(isNew ? "search:new" : `search:${x.id}`)}
  </form>`;
}

// ----- direction -----
function listOrText(v) {
  if (Array.isArray(v)) return v.length ? html`<ul class="bullets">${v.map((x) => html`<li>${typeof x === "object" ? x.problem || JSON.stringify(x) : x}</li>`)}</ul>` : html`<span class="meta">None</span>`;
  if (v && typeof v === "object") {
    const parts = SIZE.filter(([k]) => v[k] !== undefined && v[k] !== null).map(([k, l]) => `${l}: ${v[k]}`);
    return parts.length ? html`<ul class="bullets">${parts.map((p) => html`<li>${p}</li>`)}</ul>` : html`<span class="meta">Any size</span>`;
  }
  return v ? html`<span>${v}</span>` : html`<span class="meta">Not set</span>`;
}

function directionView(d, ctx) {
  const cur = d.direction.current;
  const pending = d.direction.proposals.filter((p) => p.status === "pending");
  const decided = d.direction.proposals.filter((p) => p.status !== "pending");
  return html`
  <div class="stack">
    ${pending.length ? html`
    <section class="card" aria-labelledby="prop-h">
      <div class="card-head"><h2 id="prop-h">Proposed changes</h2><span class="meta">Approving makes a new direction version; future searches use it.</span></div>
      <div class="card-body stack">${pending.map((p) => proposalCard(p, ctx))}</div>
    </section>` : ""}
    <section class="card" aria-labelledby="dir-h">
      <div class="card-head">
        <div><h2 id="dir-h">Research direction ${cur ? cur.version : ""}</h2>
          ${cur ? html`<p class="meta">${cur.approvedBy ? `Approved by ${cur.approvedBy}, ${day(cur.approvedOn, ctx.today)}.` : ""} ${cur.change || ""}</p>` : ""}</div>
        <div class="actions">
          <button type="button" class="btn" data-act="analyze">Ask the direction agent to review feedback</button>
          <button type="button" class="btn" data-act="problems">Propose problems from the CGS library</button>
        </div>
      </div>
      ${st.err("agent")}
      ${cur ? html`<dl class="need card-body">${[...FIELDS, ["problems", "Problems to watch for"]].map(([k, label]) => html`<dt>${label}</dt><dd>${listOrText(cur[k])}</dd>`)}</dl>`
        : emptyState("No direction yet", "Propose one below; it applies once a person approves it.")}
      <div class="card-body">${st.ui.open === "propose" ? proposeForm(cur) : openButton("propose", "Propose a change…", "btn")}</div>
    </section>
    <section class="card" aria-labelledby="ver-h">
      <div class="card-head"><h2 id="ver-h">Versions and decisions</h2></div>
      <div class="card-body stack">
        <ul class="history">${d.direction.versions.map((v) => html`<li><b>${v.version}${v.scope !== "shared" ? ` (${v.owner}'s own)` : ""}</b>
          <span class="meta-3">${v.approvedBy ? `Approved by ${v.approvedBy}, ${day(v.approvedOn, ctx.today)}. ` : ""}${v.change || ""}</span></li>`)}</ul>
        ${decided.length ? html`<details><summary class="sub-title">Decided proposals (${decided.length})</summary>
          <ul class="history">${decided.map((p) => html`<li><b>${proposalTitle(p)}</b><span class="meta-3">${p.status}. ${p.rationale}</span></li>`)}</ul></details>` : ""}
      </div>
    </section>
  </div>`;
}

function proposalTitle(p) {
  if (p.type === "exclude") return `Leave ${p.target}${p.domain ? ` (${p.domain})` : ""} out of future searches`;
  return p.title || `Change ${FIELD_LABEL[p.field] || p.field}`;
}

function diff(p) {
  if (p.type === "exclude") return "";
  if (Array.isArray(p.before) || Array.isArray(p.after)) {
    const name = (x) => (typeof x === "object" ? x.problem || JSON.stringify(x) : x);
    const before = (p.before || []).map(name), after = (p.after || []).map(name);
    const added = after.filter((x) => !before.includes(x)), removed = before.filter((x) => !after.includes(x));
    return html`<ul class="bullets">${added.map((x) => html`<li>Add: ${x}</li>`)}${removed.map((x) => html`<li>Remove: ${x}</li>`)}</ul>`;
  }
  return html`<div class="form-grid"><div><p class="meta-3">Now</p>${listOrText(p.before)}</div><div><p class="meta-3">Proposed</p>${listOrText(p.after)}</div></div>`;
}

// Who may decide (api/services.py decide_direction): an admin for the team's direction; the proposer or an admin for
// a change to one person's own searches.
export function mayDecide(p, ctx) {
  return ctx.admin || (p.scope === "consultant" && (p.proposedBy || ctx.me) === ctx.me);
}

function proposalCard(p, ctx) {
  const who = p.origin === "manual" ? `Proposed by ${p.proposedBy}` : p.origin === "ai" ? "Proposed by the direction agent" : "Proposed from feedback";
  return html`
  <div class="suggestion">${icon("bulb", 20)}<div style="flex:1;min-width:0">
    <b>${proposalTitle(p)}</b>
    <p class="meta-3">${who}${p.feedbackIds && p.feedbackIds.length ? `, from ${plural(p.feedbackIds.length, "feedback record")}` : ""}. Applies to ${p.scope === "consultant" ? "the proposer's own searches" : "the team's direction"}.</p>
    ${p.rationale ? html`<p class="meta" style="margin-top:6px">${p.rationale}</p>` : ""}
    <div style="margin-top:8px">${diff(p)}</div>
    ${mayDecide(p, ctx) ? html`<div class="actions" style="margin-top:10px">
      <button type="button" class="btn primary small" data-act="decideProposal" data-id="${p.id}" data-verdict="approve">Approve</button>
      <button type="button" class="btn ghost small" data-act="decideProposal" data-id="${p.id}" data-verdict="reject">Reject</button>
    </div>` : html`<p class="meta-3" style="margin-top:10px">An admin approves or rejects changes to the team's direction.</p>`}
    ${st.err(`prop:${p.id}`)}
  </div></div>`;
}

function proposeForm(cur) {
  const v = cur ? cur[field] : null;
  let input;
  if (field === "problem") input = html`<label class="field">New text<textarea name="value" rows="3" required>${v || ""}</textarea></label>`;
  else if (field === "company_size") input = html`<div class="form-grid">${SIZE.map(([k, l]) => html`<label class="field">${l}<input type="number" min="0" step="any" name="${k}" value="${v && v[k] !== undefined && v[k] !== null ? v[k] : ""}"></label>`)}</div>`;
  else input = html`<label class="field">One per line <span class="hint">Edit the current list: add, remove or reword lines.</span><textarea name="value" rows="7" required>${(v || []).join("\n")}</textarea></label>`;
  return html`
  <form class="panel-form" data-act="proposeForm" data-on="submit input change">
    <h3 class="sub-title">Propose a change</h3>
    <div class="form-grid">
      <label class="field">What to change<select name="field" data-act="pickField" data-on="change">${FIELDS.map(([k, l]) => html`<option value="${k}" ${k === field ? "selected" : ""}>${l}</option>`)}</select></label>
      <label class="field">For whom<select name="scope"><option value="theme">The team's direction</option><option value="consultant">Only my own searches</option></select></label>
    </div>
    ${input}
    <label class="field">Why <span class="hint">The approver reads this.</span><textarea name="rationale" rows="2" required></textarea></label>
    <div class="actions"><button type="submit" class="btn primary" disabled>Send for approval</button>${cancelButton}<span class="missing" data-missing>Say why.</span></div>
    ${st.err("propose")}
  </form>`;
}

// ----- learned -----
function learnedView(d, ctx) {
  const m = d.memory;
  return html`
  <div class="stack">
    <section class="card" aria-labelledby="sig-h">
      <div class="card-head"><div><h2 id="sig-h">Signals that led to leads</h2>
        <p class="meta">Counted from ${plural(m.decided, "decided candidate")}. The discovery agent sees these numbers as hints, not rules.</p></div></div>
      ${m.signals.length ? html`<div class="table-wrap"><table class="list">
        <thead><tr><th scope="col">Signal</th><th scope="col">Found</th><th scope="col">Picked</th><th scope="col">Accepted</th><th scope="col">Rejected at Step 4</th><th scope="col">Skipped</th></tr></thead>
        <tbody>${m.signals.map((x) => html`<tr><td><b>${x.signal}</b></td><td class="num">${x.nominated}</td><td class="num">${x.picked}</td><td class="num">${x.accepted}</td><td class="num">${x.rejected_at_step_4}</td><td class="num">${x.skipped}</td></tr>`)}</tbody>
      </table></div>` : emptyState("Nothing counted yet", "Signals appear once people pick or skip candidates.")}
    </section>
    <div class="form-grid">
      <section class="card card-body">
        <h2 class="section-title">Searches that led to accepted leads</h2>
        ${queryList(m.queries_that_led_to_accepted_leads)}
      </section>
      <section class="card card-body">
        <h2 class="section-title">Searches that led to nothing</h2>
        ${queryList(m.queries_that_led_to_nothing)}
      </section>
      <section class="card card-body">
        <h2 class="section-title">Common skip reasons</h2>
        ${m.common_skip_reasons.length ? html`<ul class="kv">${m.common_skip_reasons.map((r) => html`<li><span>${r.reason}</span><span>${r.times}</span></li>`)}</ul>` : html`<p class="meta">None yet.</p>`}
      </section>
    </div>
    <section class="card" aria-labelledby="fb-h">
      <div class="card-head"><h2 id="fb-h">Feedback from decisions</h2><span class="meta">What the direction agent reviews</span></div>
      ${d.feedback.length ? html`<div class="table-wrap"><table class="list">
        <thead><tr><th scope="col">Company</th><th scope="col">Step</th><th scope="col">Outcome</th><th scope="col">Reason</th><th scope="col">Teaches</th><th scope="col">By</th></tr></thead>
        <tbody>${d.feedback.slice(0, 50).map((f) => html`<tr><td><b>${f.subject}</b></td><td class="num">${f.step}</td><td>${f.outcome}</td><td>${f.reason}${f.comment ? html`<div class="meta-3">${f.comment}</div>` : ""}</td><td>${f.scope}</td><td>${f.by}<div class="meta-3">${day(f.on, ctx.today)}</div></td></tr>`)}</tbody>
      </table></div>` : emptyState("No feedback yet", "Skipping, accepting or rejecting a company records feedback.")}
    </section>
  </div>`;
}

function queryList(list) {
  return list && list.length ? html`<ul class="bullets">${list.map((q) => html`<li>${typeof q === "object" ? q.query || JSON.stringify(q) : q}</li>`)}</ul>` : html`<p class="meta">None yet.</p>`;
}

// ---------- actions ----------
export const actions = {
  tab(t, e, ctx) { st.reset(); ctx.go("sources", { tab: t.dataset.tab }); },
  tabkeys(t, e, ctx) { tabKeys(t, e, ctx, (tab) => { st.reset(); ctx.go("sources", { tab }); }); },
  open(t, e, ctx) { st.ui.open = t.dataset.open || null; st.ui.error = null; ctx.refresh(); },
  pickField(t, e, ctx) { field = t.value; ctx.refresh(); },

  recommendForm(form, e, ctx) {
    const count = Number(form.elements.count.value) || 10;
    st.write("recommend", form.querySelector("[type=submit]"), ctx, () => ctx.api.post("/candidates/recommend", { intent: form.elements.intent.value.trim(), count }), (res) => {
      st.ui.notice = [{ text: `Search round ${res.round}: ${plural(res.candidates, "new candidate")}.`, cls: res.candidates ? "good" : "" }];
      if (res.candidates) ctx.toast(`${plural(res.candidates, "new candidate")} to pick from.`, { link: { href: href("companies", { tab: "new" }), label: "Open them" } });
    });
  },
  searchForm(form, e, ctx) {
    const f = form.elements;
    const main = form.dataset.main === "1";
    const why = main || (f.name.value.trim().length >= 3 && f.query.value.trim().length >= 10) ? "" : "Add a name and what to look for.";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    const freq = f.freq.value;
    const weekday = f.weekday.value === "" || ["day", "3days"].includes(freq) ? null : Number(f.weekday.value);
    const body = { freq, per_run: Number(f.per_run.value) || 5, ...(main ? {} : { name: f.name.value.trim(), query: f.query.value.trim() }) };
    const id = form.dataset.id;
    st.write(id ? `search:${id}` : "search:new", form.querySelector("[type=submit]"), ctx,
      () => (id ? ctx.api.patch(`/discovery/searches/${enc(id)}`, { ...body, weekday: weekday === null ? -1 : weekday })
        : ctx.api.post("/discovery/searches", { ...body, weekday })),
      (res) => { st.ui.notice = [{ text: `${res.name}: saved. Next run ${day(res.nextRunOn, ctx.today)}.`, cls: "good" }]; });
  },
  toggleSearch(t, e, ctx) {
    const on = t.dataset.turnOn === "1";
    st.write("searches", t, ctx, () => ctx.api.patch(`/discovery/searches/${enc(t.dataset.id)}`, { on }),
      (res) => { st.ui.notice = [{ text: on ? `${res.name} is on. Next run ${day(res.nextRunOn, ctx.today)}.` : `${res.name} is off. It does not run until you switch it on.`, cls: "good" }]; });
  },
  deleteSearch(t, e, ctx) {
    st.write(`search:${t.dataset.id}`, t, ctx, () => ctx.api.del(`/discovery/searches/${enc(t.dataset.id)}`),
      () => { st.ui.notice = [{ text: "Search deleted. Companies it found stay in Companies.", cls: "good" }]; });
  },
  addForm(form, e, ctx) {
    const f = form.elements;
    if (e.type !== "submit") return missing(form, f.company.value.trim() ? "" : "Give the company name.");
    if (!f.company.value.trim()) return;
    st.write("add", form.querySelector("[type=submit]"), ctx, () => ctx.api.post("/opportunities", {
      company: f.company.value.trim(), domain: f.domain.value.trim(), name: f.name.value.trim(), title: f.title.value.trim(),
      source: f.source.value, notes: f.notes.value.trim(),
    }), (res) => {
      ctx.toast(`${f.company.value.trim()} added. Research has started.`, { link: { href: href("companies", { tab: "research" }), label: "Watch it" } });
      form.reset();
      void res;
    });
  },

  analyze(t, e, ctx) {
    st.write("agent", t, ctx, () => ctx.api.post("/direction/analyze"), (res) => {
      st.ui.notice = [{ text: res.proposalId ? `The direction agent reviewed ${plural(res.eligible, "feedback record")} and proposed a change. ${res.summary || ""}`
        : `The direction agent reviewed ${plural(res.eligible, "feedback record")} and proposed nothing. ${res.summary || ""}`, cls: res.proposalId ? "good" : "" }];
    });
  },
  problems(t, e, ctx) {
    st.write("agent", t, ctx, () => ctx.api.post("/direction/problems", { scope: "theme" }), (res) => {
      st.ui.notice = [{ text: `The direction agent proposed ${plural((res.problems || []).length, "problem")} from the CGS library. ${res.summary || ""}`, cls: "good" }];
    });
  },
  decideProposal(t, e, ctx) {
    const approve = t.dataset.verdict === "approve";
    st.write(`prop:${t.dataset.id}`, t, ctx, () => ctx.api.post(`/direction/proposals/${enc(t.dataset.id)}/${t.dataset.verdict}`), (res) => {
      st.ui.notice = [{ text: approve ? `Approved. ${res.version ? `Direction ${res.version} is now in use.` : "The change is in use."}` : "Rejected. The direction is unchanged.", cls: approve ? "good" : "" }];
    });
  },
  proposeForm(form, e, ctx) {
    const f = form.elements;
    const why = f.rationale.value.trim() ? "" : "Say why.";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    let value;
    if (field === "company_size") {
      value = Object.fromEntries(SIZE.filter(([k]) => f[k].value !== "").map(([k]) => [k, Number(f[k].value)]));
    } else if (field === "problem") {
      value = f.value.value.trim();
    } else {
      value = f.value.value.split("\n").map((x) => x.trim()).filter(Boolean);
    }
    st.write("propose", form.querySelector("[type=submit]"), ctx, () => ctx.api.post("/direction/proposals",
      { field, scope: f.scope.value, value, rationale: f.rationale.value.trim() }),
      () => { st.ui.notice = [{ text: "Proposal saved. It applies once a person approves it.", cls: "good" }]; });
  },
};
