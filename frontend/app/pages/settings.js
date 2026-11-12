// People: who is an admin, which address each person sends from and whether their own Gmail is connected, and the
// scoring criteria and the email standard an admin may change (api/people.py, api/scoring.py, api/email_rubric.py).
// Everyone can read this page; admins change it.
import { html, icon, day, href } from "../lib.js";
import { enc, person, actAs } from "../api.js";
import { pageState, missing, tabBar, tabKeys } from "../forms.js";

export const title = "People";

const TABS = [["people", "People and roles"], ["scoring", "Scoring criteria"], ["email", "Email standard"], ["playbook", "Outreach playbook"]];
const LIMITS = [["max_steps", "Steps in a plan, at most", 2, 8], ["min_gap_days", "Days between steps, at least", 1, 14],
  ["max_span_days", "Days a plan may span, at most", 14, 120], ["max_people", "People in a plan, at most", 1, 4]];
const DIMS = ["B", "A", "N", "T", "H", "E"];
const MAILBOX = {
  own: ["good", "Own Gmail connected"],
  shared: ["", "Shared workspace mailbox"],
  off: ["", "Email is off"],
};

const st = pageState();

export async function load(ctx, params) {
  const tab = TABS.some(([t]) => t === params.tab) ? params.tab : "people";
  if (tab === "scoring") return { tab, scoring: await ctx.api.get("/scoring") };
  if (tab === "email") return { tab, rubric: await ctx.api.get("/email-rubric") };
  if (tab === "playbook") return { tab, playbook: await ctx.api.get("/outreach-playbook") };
  return { tab, people: await ctx.api.get("/people") };
}

export function view(d, ctx) {
  return html`
  <div class="page-head">
    <div>
      <h1>People</h1>
      <p>Who may do what, which address each person's email comes from, and how opportunities are scored. Everyone can read
        this page; admins change it.</p>
    </div>
  </div>
  ${whoView(d, ctx)}
  ${st.notices()}
  ${tabBar("People", TABS, d.tab)}
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${d.tab === "scoring" ? scoringView(d.scoring, ctx) : d.tab === "email" ? rubricView(d.rubric, ctx) : d.tab === "playbook" ? playbookView(d.playbook, ctx) : peopleView(d.people, ctx)}
  </div>`;
}

// Who is signed in, whom they act as, and (on one computer, without Google sign-in) signing out.
function whoView(d, ctx) {
  const google = ctx.signedIn;
  const other = ctx.me !== ctx.self;
  return html`<div class="notice ${other ? "warn" : ""}" role="status" style="margin-bottom:20px">${icon(other ? "alert" : "people")}
    <span style="flex:1">${google ? `Signed in with Google as ${google} (${ctx.self})` : `Signed in as ${ctx.self}`}${ctx.admin ? ", an admin" : ""}.
      ${other ? html`You are acting as <b>${ctx.me}</b>: changes record "${ctx.self} as ${ctx.me}" and email goes out as ${ctx.me}.` : ""}</span>
    ${other ? html`<button type="button" class="btn small" data-act="beMe">Act as myself</button>` : ""}
    ${google ? "" : html`<button type="button" class="btn ghost small" data-act="signOut">Sign out</button>`}
  </div>`;
}

// ----- people and roles -----
function peopleView(p, ctx) {
  const can = p.canChange;
  return html`
  <div class="two-col">
    <div class="main stack">
      ${p.everyoneIsAdmin ? html`<div class="notice warn" role="status">${icon("alert")}<span>Nobody is an admin yet, so everyone
        can do admin work and act as anyone. Make one or two people admins to limit that.</span></div>` : ""}
      <section class="card" aria-labelledby="people-h">
        <div class="card-head"><div><h2 id="people-h">People and roles</h2>
          <p class="meta">${can ? "You are an admin: you can change roles and sending addresses." : `Only an admin (${p.admins.join(", ")}) can change these.`}</p></div></div>
        <div class="table-wrap"><table class="list">
          <thead><tr><th scope="col">Person</th><th scope="col">Role</th><th scope="col">Sends from</th><th scope="col">Second approval</th>${can ? html`<th scope="col"><span class="sr-only">Change</span></th>` : ""}</tr></thead>
          <tbody>${p.people.map((x) => personRow(x, p, ctx))}</tbody>
        </table></div>
        ${st.err("people")}
      </section>
    </div>
    <aside class="side stack">
      <section class="card card-body">
        <h2 class="section-title">What an admin can do</h2>
        <ul class="bullets" style="margin-top:8px">
          <li>Act as someone else, for example send a follow-up as the consultant who leads that conversation. The record says who did it: "Alex as Sarah".</li>
          <li>Make other people admins.</li>
          <li>Ask for a second approval of someone's emails: they approve as usual, then the person named approves too before
            anything goes out. Only the people marked need it.</li>
          <li>Change the <a href="${href("settings", { tab: "scoring" })}">scoring criteria</a> and the
            <a href="${href("settings", { tab: "email" })}">email standard</a>.</li>
          <li>Approve or reject changes to the team's <a href="${href("sources", { tab: "direction" })}">research direction</a>. Anyone may propose one.</li>
          <li>Change agent permissions, lessons and team instructions.</li>
        </ul>
        <p class="meta" style="margin-top:8px">Everyone else acts only as themselves.</p>
      </section>
      <section class="card card-body">
        <h2 class="section-title">Each person's own Gmail</h2>
        <p class="meta" style="margin-top:8px">Once a person connects their Gmail, the emails they approve go out from it and replies
          are read there. Until then, email goes out from the shared workspace mailbox. To connect, the workspace admin runs this on the
          server computer and that person signs in with their own Google account, then the workspace is restarted:</p>
        <pre class="code">python -m api.toolkit.gmail_auth --person "Name"</pre>
        <p class="meta" style="margin-top:8px">A follow-up stays in the mailbox that holds the conversation, so it comes from the person
          who sent the first email (or an admin acting as them).</p>
      </section>
    </aside>
  </div>`;
}

function personRow(x, p, ctx) {
  const [cls, label] = MAILBOX[x.mailbox] || MAILBOX.shared;
  const can = p.canChange;
  const editing = st.ui.open === `email:${x.name}`;
  return html`<tr>
    <td><b>${x.name}</b>${x.name === ctx.self ? html`<span class="meta-3">You</span>` : ""}</td>
    <td>${x.admin ? html`<span class="tag info">Admin</span>` : p.everyoneIsAdmin ? html`<span class="meta-3">Everyone (no admins yet)</span>` : html`<span class="meta-3">Consultant</span>`}</td>
    <td>
      <span class="tag ${cls}">${label}</span>
      ${editing ? html`<form class="inline-form" data-act="emailForm" data-on="submit input" data-name="${x.name}" style="margin-top:8px">
          <label class="field">Sending address <span class="hint">Shown as From. Gmail keeps it only for that person's own Gmail or a "Send mail as" alias.</span>
            <input type="email" name="email" value="${x.email || ""}" autocomplete="off"></label>
          <div class="actions"><button type="submit" class="btn primary small">Save</button>
            <button type="button" class="btn ghost small" data-act="open" data-open="">Cancel</button><span class="missing" data-missing></span></div>
        </form>`
        : html`<div class="meta-3" style="margin-top:4px">${x.email || "No address set"}</div>`}
    </td>
    <td>${can ? html`<label><span class="sr-only">Who approves ${x.name}'s emails a second time</span>
        <select data-act="approver" data-on="change" data-name="${x.name}">
          <option value="" ${x.secondApprover ? "" : "selected"}>Not needed</option>
          ${p.people.filter((y) => y.name !== x.name).map((y) => html`<option value="${y.name}" ${x.secondApprover === y.name ? "selected" : ""}>By ${y.name}</option>`)}
        </select></label>`
      : x.secondApprover ? html`<span class="tag warn">By ${x.secondApprover}</span>` : html`<span class="meta-3">Not needed</span>`}</td>
    ${can ? html`<td class="acts">
      ${editing ? "" : html`<button type="button" class="btn ghost small" data-act="open" data-open="email:${x.name}">Change address</button>`}
      <button type="button" class="btn small" data-act="admin" data-name="${x.name}" data-admin="${x.admin ? "0" : "1"}">${x.admin ? "Remove admin" : "Make admin"}</button>
    </td>` : ""}
  </tr>`;
}

// ----- scoring criteria -----
function scoringView(s, ctx) {
  const cur = s.current;
  const can = s.canChange;
  const L = cur.labels;
  const sum = DIMS.reduce((n, k) => n + (Number(cur.weights[k]) || 0), 0);
  const changed = s.changed;
  return html`
  <div class="two-col">
    <div class="main stack">
      <section class="card" aria-labelledby="sc-h">
        <div class="card-head"><div><h2 id="sc-h">Scoring criteria ${cur.version}</h2>
          <p class="meta">${changed ? `Changed by ${changed.by}, ${day(changed.on, ctx.today)}: ${changed.reason}` : "The built-in criteria. No admin has changed them."}
            The rules engine scores every opportunity with these; no AI model calculates a score.</p></div></div>
        <form class="card-body panel-form" data-act="scoringForm" data-on="submit input">
          <fieldset ${can ? "" : "disabled"}>
            <legend class="sub-title">Weight of each dimension <span class="hint" data-sum>Total ${sum} of 100</span></legend>
            <div class="form-grid">${DIMS.map((k) => html`<label class="field">${L[k]} (${k})
              <input type="number" name="w_${k}" min="0" max="100" step="1" value="${cur.weights[k]}" required></label>`)}</div>
          </fieldset>
          <fieldset ${can ? "" : "disabled"}>
            <legend class="sub-title">Lowest score each dimension needs for Ready <span class="hint">0 to 5; empty = no minimum</span></legend>
            <div class="form-grid">${DIMS.map((k) => html`<label class="field">${L[k]}
              <input type="number" name="f_${k}" min="0" max="5" step="1" value="${cur.floors[k] === undefined ? "" : cur.floors[k]}"></label>`)}</div>
          </fieldset>
          <fieldset ${can ? "" : "disabled"}>
            <legend class="sub-title">Ready also needs</legend>
            <div class="form-grid">
              <label class="field">Confirmed weight, at least <span class="hint">Coverage, out of 100</span>
                <input type="number" name="coverage" min="0" max="100" step="1" value="${cur.ready.coverage}" required></label>
              <label class="field">Confirmed Q, at least <span class="hint">Out of 100</span>
                <input type="number" name="q" min="0" max="100" step="1" value="${cur.ready.q}" required></label>
            </div>
          </fieldset>
          <fieldset ${can ? "" : "disabled"}>
            <legend class="sub-title">Priority bands on P</legend>
            <div class="form-grid">
              <label class="field">High from<input type="number" name="high" min="1" max="100" step="1" value="${cur.bands.High}" required></label>
              <label class="field">Medium from<input type="number" name="medium" min="0" max="99" step="1" value="${cur.bands.Medium}" required></label>
            </div>
          </fieldset>
          ${can ? html`
          <label class="field">Why this change <span class="hint">The team sees it beside the criteria.</span><textarea name="reason" rows="2"></textarea></label>
          <div class="actions">
            <button type="submit" class="btn primary">Save new criteria</button>
            <button type="button" class="btn ghost" data-act="builtIn">Back to the built-in criteria</button>
            <span class="missing" data-missing></span>
          </div>` : html`<p class="meta">Only an admin (${s.admins.join(", ") || "anyone, while none is named"}) can change these.</p>`}
          ${st.err("scoring")}
        </form>
      </section>
    </div>
    <aside class="side stack">
      <section class="card card-body">
        <h2 class="section-title">How a score is built</h2>
        <p class="meta" style="margin-top:8px">Q adds weight × score ÷ 5 over the dimensions a person confirmed. P = 0.6 Q + 0.4 R, where R
          comes from the revenue estimate. Ready needs every minimum, every condition, and the coverage and Q above.</p>
        <p class="meta" style="margin-top:8px">The dimensions and conditions themselves stay fixed: the agents and the evidence checklist are built on them.</p>
      </section>
      <section class="card card-body">
        <h2 class="section-title">Earlier versions</h2>
        ${s.history.length ? html`<ul class="history" style="margin-top:8px">${s.history.map((h) => html`<li><b>${h.version}</b>
          <span class="meta-3">${h.by}${h.on ? `, ${day(h.on, ctx.today)}` : ""}. ${h.reason || ""}</span></li>`)}</ul>`
          : html`<p class="meta" style="margin-top:8px">None yet.</p>`}
      </section>
    </aside>
  </div>`;
}

// ----- email standard -----
// The draft lives in st.ui.crit while an admin edits, so adding or removing a criterion keeps what they typed.
function rubricView(r, ctx) {
  const cur = r.current;
  const can = r.canChange;
  const crit = st.ui.crit || cur.criteria.map((c) => ({ ...c }));
  const sum = crit.reduce((n, c) => n + (Number(c.weight) || 0), 0);
  const changed = r.changed;
  return html`
  <div class="two-col">
    <div class="main stack">
      <section class="card" aria-labelledby="em-h">
        <div class="card-head"><div><h2 id="em-h">Email standard ${cur.version}</h2>
          <p class="meta">${changed ? `Changed by ${changed.by}, ${day(changed.on, ctx.today)}: ${changed.reason}` : "The built-in standard, written by the project team. No admin has changed it."}
            The outreach agent writes to it, its reviewer checks every first draft against it before anyone sees it, and the
            quality check scores drafts with it.</p></div></div>
        <form class="card-body panel-form" data-act="rubricForm" data-on="submit input">
          ${crit.map((c, i) => html`<fieldset ${can ? "" : "disabled"}>
            <legend class="sub-title">${c.label || `Criterion ${i + 1}`}
              ${can && crit.length > 1 ? html`<button type="button" class="btn ghost small" data-act="dropCrit" data-i="${i}">Remove</button>` : ""}</legend>
            <input type="hidden" name="key_${i}" value="${c.key || ""}">
            <div class="form-grid">
              <label class="field">Name<input name="label_${i}" value="${c.label || ""}" maxlength="40" required></label>
              <label class="field">Weight <span class="hint">Out of 100</span>
                <input type="number" name="weight_${i}" min="0" max="100" step="1" value="${c.weight ?? 0}" required></label>
            </div>
            <label class="field">What the reader asks <span class="hint">20 to 400 characters</span>
              <textarea name="question_${i}" rows="2" maxlength="400">${c.question || ""}</textarea></label>
            <div class="form-grid">
              <label class="field">Looks like a 5<textarea name="good_${i}" rows="2" maxlength="300">${c.good || ""}</textarea></label>
              <label class="field">Looks like a 2<textarea name="poor_${i}" rows="2" maxlength="300">${c.poor || ""}</textarea></label>
            </div>
          </fieldset>`)}
          <div>${can && crit.length < 8 ? html`<button type="button" class="btn small" data-act="addCrit">Add a criterion</button> ` : ""}
            <span class="hint" data-sum>Weights total ${sum} of 100</span></div>
          <fieldset ${can ? "" : "disabled"}>
            <legend class="sub-title">Good enough</legend>
            <div class="form-grid">
              <label class="field">Weighted mean, at least <span class="hint">1 to 5</span>
                <input type="number" name="mean" min="1" max="5" step="0.1" value="${cur.bar.mean}" required></label>
              <label class="field">Every line, at least <span class="hint">1 to 5</span>
                <input type="number" name="each" min="1" max="5" step="1" value="${cur.bar.each}" required></label>
              <label class="field">Review rounds <span class="hint">How often the reviewer may send a draft back</span>
                <select name="rounds">${[0, 1, 2].map((n) => html`<option value="${n}" ${cur.review.rounds === n ? "selected" : ""}>${n === 0 ? "No review" : String(n)}</option>`)}</select></label>
            </div>
          </fieldset>
          ${can ? html`
          <label class="field">Why this change <span class="hint">The team sees it beside the standard.</span><textarea name="reason" rows="2"></textarea></label>
          <div class="actions">
            <button type="submit" class="btn primary">Save new standard</button>
            <button type="button" class="btn ghost" data-act="rubricBuiltIn">Back to the built-in standard</button>
            <span class="missing" data-missing></span>
          </div>` : html`<p class="meta">Only an admin (${r.admins.join(", ") || "anyone, while none is named"}) can change this.</p>`}
          ${st.err("rubric")}
        </form>
      </section>
    </div>
    <aside class="side stack">
      <section class="card card-body">
        <h2 class="section-title">How it is used</h2>
        <p class="meta" style="margin-top:8px">Each line is scored 1 to 5. A first draft meets the standard when the weighted mean and
          every line reach the bar. If it does not, the reviewer sends it back to the writer with fixes, up to the review rounds set
          here; the person approving still sees the scores and decides.</p>
        <p class="meta" style="margin-top:8px">Hard rules stay in code whatever the standard says: no invented numbers, prices or
          promises, citations must be real, and a person approves every email.</p>
      </section>
      <section class="card card-body">
        <h2 class="section-title">Earlier versions</h2>
        ${r.history.length ? html`<ul class="history" style="margin-top:8px">${r.history.map((h) => html`<li><b>${h.version}</b>
          <span class="meta-3">${h.by}${h.on ? `, ${day(h.on, ctx.today)}` : ""}. ${h.reason || ""}</span></li>`)}</ul>`
          : html`<p class="meta" style="margin-top:8px">None yet.</p>`}
      </section>
    </aside>
  </div>`;
}

function rubricCrit(f) {
  const out = [];
  for (let i = 0; f[`label_${i}`]; i++) {
    out.push({
      key: f[`key_${i}`].value, label: f[`label_${i}`].value.trim(), weight: Number(f[`weight_${i}`].value),
      question: f[`question_${i}`].value.trim(), good: f[`good_${i}`].value.trim(), poor: f[`poor_${i}`].value.trim(),
    });
  }
  return out;
}

function rubricWhy(body) {
  const sum = body.criteria.reduce((n, c) => n + (Number.isFinite(c.weight) ? c.weight : 0), 0);
  const short = body.criteria.find((c) => c.label.length < 2 || c.question.length < 20);
  return sum !== 100 ? `The weights add up to ${sum}; they must add up to 100.`
    : short ? `${short.label || "A criterion"} needs a name and a question of at least 20 characters.`
    : !(body.bar.mean >= 1 && body.bar.mean <= 5) ? "The weighted mean must be from 1 to 5."
    : body.reason.length < 5 ? "Say why, in a few words." : "";
}

// ----- outreach playbook (api/outreach_plan.py) -----
function playbookView(v, ctx) {
  const cur = v.current;
  const can = v.canChange;
  const ch = cur.changed;
  return html`
  <div class="two-col">
    <div class="main stack">
      <section class="card" aria-labelledby="pb-h">
        <div class="card-head"><div><h2 id="pb-h">Outreach playbook ${cur.version}</h2>
          <p class="meta">${ch ? `Changed by ${ch.by}, ${day(ch.on, ctx.today)}: ${ch.reason}` : "The built-in playbook, written by the project team from general practice and CGS's public website. No admin has changed it."}
            The outreach agent makes every contact plan with it: whom to approach first, which ways in, and how many steps.</p></div></div>
        <form class="card-body panel-form" data-act="playbookForm" data-on="submit input">
          <fieldset ${can ? "" : "disabled"}>
            <legend class="sub-title">Principles <span class="hint">One per line. The agent plans by them; code checks still apply.</span></legend>
            <textarea name="principles" rows="9">${cur.principles.join("\n")}</textarea>
          </fieldset>
          <fieldset ${can ? "" : "disabled"}>
            <legend class="sub-title">Ways in <span class="hint">Switch one off and the agent never plans it.</span></legend>
            ${Object.entries(cur.channels).map(([k, c]) => html`<div class="field">
              <label class="check"><input type="checkbox" name="on_${k}" ${c.enabled ? "checked" : ""}> <b>${c.label}</b></label>
              <textarea name="use_${k}" rows="2" aria-label="How to use ${c.label}">${c.use}</textarea></div>`)}
          </fieldset>
          <fieldset ${can ? "" : "disabled"}>
            <legend class="sub-title">Limits</legend>
            <div class="form-grid">${LIMITS.map(([k, label, lo, hi]) => html`<label class="field">${label}
              <input type="number" name="lim_${k}" min="${lo}" max="${hi}" step="1" value="${cur.limits[k]}" required></label>`)}</div>
          </fieldset>
          ${can ? html`
          <label class="field">Why this change <span class="hint">The team sees it beside the playbook.</span><textarea name="reason" rows="2"></textarea></label>
          <div class="actions">
            <button type="submit" class="btn primary">Save new playbook</button>
            <button type="button" class="btn ghost" data-act="playbookBuiltIn">Back to the built-in playbook</button>
            <span class="missing" data-missing></span>
          </div>` : html`<p class="meta">Only an admin (${v.admins.join(", ") || "anyone, while none is named"}) can change this.</p>`}
          ${st.err("playbook")}
        </form>
      </section>
    </div>
    <aside class="side stack">
      <section class="card card-body">
        <h2 class="section-title">What stays fixed</h2>
        <p class="meta" style="margin-top:8px">Whatever the playbook says, code checks every plan: only people the sources name,
          only real CGS library items, an introduction only through a colleague who works with the company, and a person
          approves the plan and does every step. Plans already made keep the playbook version they name.</p>
        <p class="meta" style="margin-top:8px">The built-in playbook holds nothing from CGS's internal guides. Replace it with CGS's own
          once CGS agrees to share that with the AI.</p>
      </section>
      <section class="card card-body">
        <h2 class="section-title">Earlier versions</h2>
        ${v.history.length ? html`<ul class="history" style="margin-top:8px">${v.history.map((h) => html`<li><b>${h.version}</b>
          <span class="meta-3">${h.by}${h.on ? `, ${day(h.on, ctx.today)}` : ""}. ${h.reason || ""}</span></li>`)}</ul>`
          : html`<p class="meta" style="margin-top:8px">None yet.</p>`}
      </section>
    </aside>
  </div>`;
}

function playbookBody(form) {
  const f = form.elements;
  const keys = [...form.querySelectorAll("[name^=on_]")].map((x) => x.name.slice(3));
  return {
    principles: f.principles.value.split("\n").map((x) => x.trim()).filter(Boolean),
    channels: Object.fromEntries(keys.map((k) => [k, { enabled: f[`on_${k}`].checked, use: f[`use_${k}`].value.trim() }])),
    limits: Object.fromEntries(LIMITS.map(([k]) => [k, Number(f[`lim_${k}`].value)])),
    reason: f.reason ? f.reason.value.trim() : "",
  };
}

function scoringBody(f) {
  const num = (name) => Number(f[name].value);
  const floors = {};
  DIMS.forEach((k) => { if (f[`f_${k}`].value !== "") floors[k] = num(`f_${k}`); });
  return {
    weights: Object.fromEntries(DIMS.map((k) => [k, num(`w_${k}`)])), floors,
    ready: { coverage: num("coverage"), q: num("q") }, bands: { High: num("high"), Medium: num("medium") },
    reason: f.reason.value.trim(),
  };
}

export const actions = {
  tab(t, e, ctx) { st.reset(); st.ui.crit = null; ctx.go("settings", { tab: t.dataset.tab }); },
  tabkeys(t, e, ctx) { tabKeys(t, e, ctx, (tab) => { st.reset(); st.ui.crit = null; ctx.go("settings", { tab }); }); },
  open(t, e, ctx) { st.ui.open = t.dataset.open || null; st.ui.error = null; ctx.refresh(); },
  beMe() { actAs.clear(); location.reload(); },
  signOut() { person.clear(); actAs.clear(); location.hash = "#/home"; location.reload(); },

  admin(t, e, ctx) {
    const on = t.dataset.admin === "1";
    st.write("people", t, ctx, () => ctx.api.put(`/people/${enc(t.dataset.name)}`, { admin: on }), (res) => {
      st.ui.notice = [{ text: res.changed.length ? `${res.changed.join("; ")}.`.replace(/^./, (c) => c.toUpperCase()) : "Nothing changed.", cls: "good" }];
      if (t.dataset.name === ctx.self && !on) ctx.toast("You are no longer an admin. Reload the page to see what you can do now.");
    });
  },
  approver(t, e, ctx) {
    st.write("people", t, ctx, () => ctx.api.put(`/people/${enc(t.dataset.name)}`, { secondApprover: t.value }), (res) => {
      st.ui.notice = [{ text: res.changed.length ? `${res.changed.join("; ")}.` : "Nothing changed.", cls: "good" }];
    });
  },
  emailForm(form, e, ctx) {
    const v = form.elements.email.value.trim();
    const why = v && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v) ? "That is not an email address." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    st.write("people", form.querySelector("[type=submit]"), ctx, () => ctx.api.put(`/people/${enc(form.dataset.name)}`, { email: v }), (res) => {
      st.ui.notice = [{ text: res.changed.length ? `${res.changed.join("; ")}.`.replace(/^./, (c) => c.toUpperCase()) : "Nothing changed.", cls: "good" }];
    });
  },

  scoringForm(form, e, ctx) {
    const body = scoringBody(form.elements);
    const sum = Object.values(body.weights).reduce((n, w) => n + (Number.isFinite(w) ? w : 0), 0);
    const line = form.querySelector("[data-sum]");
    if (line) line.textContent = `Total ${sum} of 100`;
    const why = sum !== 100 ? `The weights add up to ${sum}; they must add up to 100.`
      : body.bands.Medium >= body.bands.High ? "High must start above Medium."
      : body.reason.length < 5 ? "Say why, in a few words." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    st.write("scoring", form.querySelector("[type=submit]"), ctx, () => ctx.api.put("/scoring", body), (res) => {
      st.ui.notice = [{ text: `Saved as ${res.current.version}. Every opportunity is scored with these criteria from now on.`, cls: "good" }];
    });
  },
  builtIn(t, e, ctx) {
    const form = t.closest("form");
    const reason = form.elements.reason.value.trim() || "Back to the built-in criteria";
    st.write("scoring", t, ctx, () => ctx.api.put("/scoring", { builtIn: true, reason }), (res) => {
      st.ui.notice = [{ text: `Back to the built-in criteria (${res.current.version}).`, cls: "good" }];
    });
  },

  rubricForm(form, e, ctx) {
    const f = form.elements;
    const criteria = rubricCrit(f);
    st.ui.crit = criteria;
    const body = { criteria, bar: { mean: Number(f.mean.value), each: Number(f.each.value) }, review: { rounds: Number(f.rounds.value) },
      reason: f.reason ? f.reason.value.trim() : "" };
    const line = form.querySelector("[data-sum]");
    if (line) line.textContent = `Weights total ${criteria.reduce((n, c) => n + (Number(c.weight) || 0), 0)} of 100`;
    const why = rubricWhy(body);
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    st.write("rubric", form.querySelector("[type=submit]"), ctx, () => ctx.api.put("/email-rubric", body), (res) => {
      st.ui.crit = null;
      st.ui.notice = [{ text: `Saved as ${res.current.version}. New first drafts are written and reviewed to this standard.`, cls: "good" }];
    });
  },
  addCrit(t, e, ctx) {
    st.ui.crit = [...rubricCrit(t.closest("form").elements), { key: "", label: "", weight: 0, question: "", good: "", poor: "" }];
    ctx.refresh();
  },
  dropCrit(t, e, ctx) {
    const crit = rubricCrit(t.closest("form").elements);
    crit.splice(Number(t.dataset.i), 1);
    st.ui.crit = crit;
    ctx.refresh();
  },
  rubricBuiltIn(t, e, ctx) {
    const form = t.closest("form");
    const reason = form.elements.reason.value.trim() || "Back to the built-in standard";
    st.write("rubric", t, ctx, () => ctx.api.put("/email-rubric", { builtIn: true, reason }), (res) => {
      st.ui.crit = null;
      st.ui.notice = [{ text: `Back to the built-in standard (${res.current.version}).`, cls: "good" }];
    });
  },

  playbookForm(form, e, ctx) {
    const body = playbookBody(form);
    const why = !body.principles.length ? "Give at least one principle."
      : body.principles.some((x) => x.length < 15 || x.length > 300) ? "Each principle is 15 to 300 characters."
      : !Object.values(body.channels).some((c) => c.enabled) ? "Keep at least one way in."
      : body.reason.length < 5 ? "Say why, in a few words." : "";
    if (e.type !== "submit") return missing(form, why);
    if (why) return;
    st.write("playbook", form.querySelector("[type=submit]"), ctx, () => ctx.api.put("/outreach-playbook", body), (res) => {
      st.ui.notice = [{ text: `Saved as ${res.current.version}. New contact plans are made with it.`, cls: "good" }];
    });
  },
  playbookBuiltIn(t, e, ctx) {
    const reason = t.closest("form").elements.reason.value.trim() || "Back to the built-in playbook";
    st.write("playbook", t, ctx, () => ctx.api.put("/outreach-playbook", { builtIn: true, reason }), (res) => {
      st.ui.notice = [{ text: `Back to the built-in playbook (${res.current.version}).`, cls: "good" }];
    });
  },
};
