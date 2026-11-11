// People: who is an admin, which address each person sends from and whether their own Gmail is connected, and the
// scoring criteria an admin may change (api/people.py, api/scoring.py). Everyone can read this page; admins change it.
import { html, icon, day, href } from "../lib.js";
import { enc, person, actAs } from "../api.js";
import { pageState, missing, tabBar, tabKeys } from "../forms.js";

export const title = "People";

const TABS = [["people", "People and roles"], ["scoring", "Scoring criteria"]];
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
    ${d.tab === "scoring" ? scoringView(d.scoring, ctx) : peopleView(d.people, ctx)}
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
          <thead><tr><th scope="col">Person</th><th scope="col">Role</th><th scope="col">Sends from</th>${can ? html`<th scope="col"><span class="sr-only">Change</span></th>` : ""}</tr></thead>
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
          <li>Change the <a href="${href("settings", { tab: "scoring" })}">scoring criteria</a>.</li>
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
  tab(t, e, ctx) { st.reset(); ctx.go("settings", { tab: t.dataset.tab }); },
  tabkeys(t, e, ctx) { tabKeys(t, e, ctx, (tab) => { st.reset(); ctx.go("settings", { tab }); }); },
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
};
