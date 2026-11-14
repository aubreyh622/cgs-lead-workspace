// Connectors (design/redesign/Connectors.dc.html): the systems the agents can reach, and what each agent may do with
// them. Every outside system goes through one gate (api/toolkit) with a usage log and daily limits. Nothing is sent or
// written without a person's click.
//
// The design's graded levels ("Search only", "Read and quote in drafts") are on/off in the backend; the table shows
// the states the backend has: On, Off, Never (set in the code), Not built, Connector off.
import { html, icon, day, plural } from "../lib.js";
import { enc } from "../api.js";
import { emptyState } from "../shell.js";
import { pageState, missing, tabBar, tabKeys, cancelButton, openButton } from "../forms.js";

export const title = "Connectors";

const TABS = [["systems", "Connected systems"], ["permissions", "Agent permissions"]];
const NAMES = {
  web: ["Ws", "Web search", "Public news and company websites"],
  library: ["Lb", "CGS library", "Services, cases and documents the agents may quote"],
  mail: ["Gm", "Email", "Client threads; each person's own mailbox or the shared one"],
  drive: ["Dr", "Google Drive", "Folders the CGS library is copied from"],
  crm: ["In", "CRM", "The record of every lead and opportunity"],
};
const DATA = { public: "Public data", cgs: "CGS documents", client: "Client data" };
const STATE_LABEL = { on: "On", off: "Off", forbidden: "Never (code)", "not built": "Not built", "connector off": "Connector off" };
const bool = (v) => (v === "true" ? true : v === "false" ? false : null);

const st = pageState();

export async function load(ctx, params) {
  const tab = TABS.some(([t]) => t === params.tab) ? params.tab : "systems";
  const opt = (p, f) => p.catch((e) => f || { error: e.message });
  const [perms, usage, history, crm, toolkit, people, agents] = await Promise.all([
    ctx.api.get("/toolkit/permissions"), ctx.api.get("/toolkit/usage"), opt(ctx.api.get("/toolkit/permissions/history")),
    opt(ctx.api.get("/crm/state"), null), opt(ctx.api.get("/toolkit"), {}), opt(ctx.api.get("/people"), { people: [] }),
    opt(ctx.api.get("/agents"), {}),
  ]);
  if (params.tab !== tab) ctx.replace("connectors", { tab });
  return { tab, perms, usage, history, crm, toolkit, people, agents };
}

export function view(d, ctx) {
  return html`
  <div class="page-head">
    <div>
      <h1>Connectors</h1>
      <p>The systems agents can reach, and what each agent may do with them. Nothing is sent or written without a person's click.</p>
    </div>
  </div>
  ${st.notices()}
  ${tabBar("Connectors", TABS, d.tab, { systems: d.perms.connectors.length, permissions: d.perms.agents.length })}
  <div id="panel" role="tabpanel" aria-labelledby="tab-${d.tab}">
    ${d.tab === "systems" ? systemsView(d, ctx) : permissionsView(d, ctx)}
  </div>`;
}

// ----- connected systems -----
function lastUsed(usage, name, ctx) {
  const rows = usage.rows.filter((r) => r.connector === name && r.ok + r.failed + r.denied > 0).sort((a, b) => b.on.localeCompare(a.on));
  if (!rows.length) return `Not in the last ${usage.days} days`;
  const r = rows[0];
  return `${day(r.on, ctx.today)}, ${r.agent}: ${plural(r.ok, "call")}${r.failed ? `, ${r.failed} failed` : ""}${r.denied ? `, ${r.denied} refused` : ""}`;
}

function systemsView(d, ctx) {
  const limits = Object.fromEntries(d.usage.limits.map((l) => [l.connector, l]));
  return html`
  <div class="sys-grid">
    ${d.perms.connectors.map((c) => {
      const [mono, name, role] = NAMES[c.name] || [c.name.slice(0, 2), c.name, c.about];
      const l = limits[c.name];
      const on = c.built && c.on;
      const users = d.perms.agents.filter((a) => a.connectors[c.name] && a.connectors[c.name].state === "on").map((a) => a.agent.replace(/ agent$/, ""));
      return html`
      <section class="card sys" aria-labelledby="sys-${c.name}">
        <div class="row-flex" style="flex-wrap:nowrap;align-items:flex-start">
          <span class="avatar" aria-hidden="true">${mono}</span>
          <div style="flex:1;min-width:0"><h2 id="sys-${c.name}" class="ttl">${name}</h2><span class="meta-3">${role}</span></div>
          <span class="tag ${on ? "good" : ""}">${!c.built ? "Not built" : on ? "On for agents" : "Off for agents"}</span>
        </div>
        <dl class="dl">
          <dt>Data</dt><dd>${DATA[c.data] || c.data}</dd>
          <dt>Today</dt><dd>${l ? `${l.usedToday} ${l.perDay ? `of ${l.perDay}` : "calls, no daily limit"}` : "Not metered"}</dd>
          <dt>Last used</dt><dd>${lastUsed(d.usage, c.name, ctx)}</dd>
          <dt>Used by</dt><dd>${users.length ? users.join(", ") : "No agent"}</dd>
          ${extra(c.name, d)}
        </dl>
        <p class="meta-3">${c.about}. Set in the ${c.source}.</p>
        ${c.name === "crm" ? crmActions(d) : ""}
        ${st.ui.open === `conn:${c.name}` ? connectorForm(c, l) : c.built ? html`<div>${openButton(`conn:${c.name}`, "Change…", "btn small", `Change ${name}`)}</div>` : ""}
      </section>`;
    })}
    <section class="card sys" aria-labelledby="sys-ai">
      <div class="row-flex" style="flex-wrap:nowrap"><span class="avatar" aria-hidden="true">AI</span>
        <div style="flex:1"><h2 id="sys-ai" class="ttl">AI model</h2><span class="meta-3">The language model behind every agent</span></div></div>
      <dl class="dl">
        <dt>Today</dt><dd>${d.agents.aiCallsToday !== undefined ? `${d.agents.aiCallsToday} of ${d.agents.aiBudget} requests` : "Not known"}</dd>
        <dt>Client data</dt><dd>${d.toolkit.clientDataToAI ? "May be sent to the model" : "Not sent to the model"}</dd>
      </dl>
      <p class="meta-3">The daily limit is set on the Agent hub.</p>
    </section>
  </div>`;
}

function extra(name, d) {
  if (name === "mail") {
    const mail = d.toolkit.mail || {};
    const own = (d.people.people || []).filter((p) => p.mailbox === "own").length;
    return html`<dt>Mailbox</dt><dd>${mail.provider || "off"}${mail.ready ? ", ready" : ", not ready"}. ${plural(own, "person", "people")} with their own mailbox signed in.</dd>`;
  }
  if (name === "crm" && d.crm) {
    const c = d.crm;
    return html`<dt>Sync</dt><dd>${c.provider}, ${c.mode.replace(/_/g, " ")}. ${c.pending} waiting, ${c.failed} failed, ${c.conflicts} to check.
      ${c.lastSyncedAt ? `Last synced ${new Date(c.lastSyncedAt).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}.` : "Nothing synced yet."}</dd>`;
  }
  if (name === "drive" && d.toolkit.drive) {
    const dr = d.toolkit.drive;
    return html`<dt>Folders</dt><dd>${dr.provider || "off"}${dr.off ? `. ${dr.off}` : ""}</dd>`;
  }
  return "";
}

function crmActions(d) {
  const c = d.crm;
  if (!c) return "";
  return html`<div class="actions">
    ${c.writes && (c.pending || c.failed) ? html`<button type="button" class="btn small" data-act="crmSync">Sync now</button>` : ""}
    ${c.reads ? html`<button type="button" class="btn small" data-act="crmPull">Read back from the CRM now</button>` : ""}
  </div>${st.err("crm")}`;
}

function connectorForm(c, l) {
  return html`
  <div class="stack">
    <form class="panel-form" data-act="connForm" data-on="submit input" data-connector="${c.name}">
      <h3 class="sub-title">Switch it for every agent</h3>
      <div class="form-grid">${stateSelect()}<label class="field">Reason<input name="reason" required></label></div>
      <div class="actions"><button type="submit" class="btn primary" disabled>Save</button>${cancelButton}<span class="missing" data-missing>Give a reason.</span></div>
    </form>
    ${l ? html`<form class="panel-form" data-act="limitForm" data-on="submit input" data-connector="${c.name}">
      <h3 class="sub-title">Daily limit</h3>
      <div class="form-grid">
        <label class="field">Calls per day <span class="hint">0 means no limit; empty means the config file's default.</span><input type="number" name="perDay" min="0" step="1" value="${l.perDay === null ? "" : l.perDay}"></label>
        <label class="field">Reason<input name="reason" required></label>
      </div>
      <div class="actions"><button type="submit" class="btn primary" disabled>Save limit</button>${cancelButton}<span class="missing" data-missing>Give a reason.</span></div>
    </form>` : ""}
    ${st.err("conn")}
  </div>`;
}

function stateSelect() {
  return html`<label class="field">Set to<select name="enabled"><option value="true">On</option><option value="false">Off</option><option value="">Back to the default</option></select></label>`;
}

// ----- which agent may use which connector -----
function permissionsView(d, ctx) {
  const p = d.perms;
  const last = Array.isArray(d.history) && d.history[0];
  return html`
  <div class="stack">
    <section class="card" aria-labelledby="perm-h">
      <div class="card-head"><div><h2 id="perm-h">What each agent may do</h2>
        <p class="meta">Give each agent the least it needs. Changes apply from the agent's next run and are logged with the reason. "Never" is set in the code.</p></div>
        ${last ? html`<span class="meta-3">Last changed by ${last.by}, ${day(last.on, ctx.today)}</span>` : ""}</div>
      <div class="table-wrap"><table class="list perm-table">
        <thead><tr><th scope="col">Agent</th>${p.connectors.map((c) => html`<th scope="col">${(NAMES[c.name] || [0, c.name])[1]}</th>`)}<th scope="col">Send or write</th></tr></thead>
        <tbody>${p.agents.map((a) => html`<tr>
          <td><b>${a.agent}</b></td>
          ${p.connectors.map((c) => {
            const cell = a.connectors[c.name];
            const label = STATE_LABEL[cell.state] || cell.state;
            return html`<td>${cell.editable ? html`<button type="button" class="perm ${cell.state === "on" ? "on" : ""}" data-act="open" data-open="perm:${a.agent}:${c.name}" aria-label="${a.agent}, ${(NAMES[c.name] || [0, c.name])[1]}: ${label}. Change">${label}</button>`
              : html`<span class="perm locked">${label}</span>`}</td>`;
          })}
          <td><span class="perm locked" title="Only a person can send an email or write to the CRM">${icon("check", 12)}People only</span></td>
        </tr>
        ${p.connectors.some((c) => st.ui.open === `perm:${a.agent}:${c.name}`) ? html`<tr><td colspan="${p.connectors.length + 2}">${permForm(a, p.connectors.find((c) => st.ui.open === `perm:${a.agent}:${c.name}`))}</td></tr>` : ""}`)}</tbody>
      </table></div>
      <p class="meta-3" style="padding:12px 20px">No agent can send an email or write to the CRM. Those happen only when a person approves.</p>
      ${st.err("perm")}
    </section>
    <section class="card" aria-labelledby="hist-h">
      <div class="card-head"><h2 id="hist-h">Changes</h2></div>
      ${d.history.error ? html`<div class="card-body"><div class="notice bad">${icon("alert")}<span>${d.history.error}</span></div></div>`
        : d.history.length ? html`<ul class="rows">${d.history.map((h) => html`<li>
          <span class="what"><b>${h.kind === "limit" ? `${h.connector} limit set to ${h.set}` : `${h.agent || "All agents"}, ${h.connector}: ${h.set}`}</b>
            <span class="meta">${h.by}, ${day(h.on, ctx.today)}. ${h.reason}</span></span>
          ${h.undoable ? (st.ui.open === `undo:${h.id}` ? html`<form class="row-flex" data-act="undoForm" data-on="submit" data-id="${h.id}">
            <label class="sr-only" for="undo-${h.id}">Why undo</label><input class="input" id="undo-${h.id}" name="reason" placeholder="Why undo" required style="width:220px">
            <button type="submit" class="btn small">Undo</button>${cancelButton}</form>` : openButton(`undo:${h.id}`, "Undo…", "btn ghost small")) : ""}
        </li>`)}</ul>` : emptyState("No changes yet", "Changes to the table and the limits appear here.")}
      ${st.err("undo")}
    </section>
  </div>`;
}

function permForm(a, c) {
  return html`
  <form class="panel-form" data-act="permForm" data-on="submit input" data-agent="${a.agent}" data-connector="${c.name}">
    <h3 class="sub-title">${a.agent} and ${(NAMES[c.name] || [0, c.name])[1]}</h3>
    <div class="form-grid">${stateSelect()}<label class="field">Reason<input name="reason" required></label></div>
    <div class="actions"><button type="submit" class="btn primary" disabled>Save</button>${cancelButton}<span class="missing" data-missing>Give a reason.</span></div>
  </form>`;
}

// ---------- actions ----------
export const actions = {
  tab(t, e, ctx) { st.reset(); ctx.go("connectors", { tab: t.dataset.tab }); },
  tabkeys(t, e, ctx) { tabKeys(t, e, ctx, (tab) => { st.reset(); ctx.go("connectors", { tab }); }); },
  open(t, e, ctx) { st.ui.open = t.dataset.open || null; st.ui.error = null; ctx.focusAfter = st.ui.open ? ".panel-form select" : null; ctx.refresh(); },
  permForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give a reason.");
    if (!reason) return;
    st.write("perm", form.querySelector("[type=submit]"), ctx, () => ctx.api.put(`/toolkit/permissions/${enc(form.dataset.agent)}/${enc(form.dataset.connector)}`,
      { enabled: bool(form.elements.enabled.value), reason }), () => { st.ui.notice = [{ text: "Saved and logged. The agent uses it from its next run.", cls: "good" }]; });
  },
  connForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give a reason.");
    if (!reason) return;
    st.write("conn", form.querySelector("[type=submit]"), ctx, () => ctx.api.put(`/toolkit/connectors/${enc(form.dataset.connector)}`,
      { enabled: bool(form.elements.enabled.value), reason }), () => { st.ui.notice = [{ text: "Saved and logged.", cls: "good" }]; });
  },
  limitForm(form, e, ctx) {
    const reason = form.elements.reason.value.trim();
    if (e.type !== "submit") return missing(form, reason ? "" : "Give a reason.");
    if (!reason) return;
    const v = form.elements.perDay.value;
    st.write("conn", form.querySelector("[type=submit]"), ctx, () => ctx.api.put(`/toolkit/limits/${enc(form.dataset.connector)}`,
      { perDay: v === "" ? null : Number(v), reason }), () => { st.ui.notice = [{ text: "Daily limit saved and logged.", cls: "good" }]; });
  },
  undoForm(form, e, ctx) {
    st.write("undo", form.querySelector("[type=submit]"), ctx, () => ctx.api.post(`/toolkit/permissions/history/${enc(form.dataset.id)}/undo`,
      { reason: form.elements.reason.value.trim() }));
  },
  crmPull(t, e, ctx) {
    st.write("crm", t, ctx, () => ctx.api.post("/crm/pull"), (res) => {
      st.ui.notice = [{ text: `Read back ${plural(res.read, "opportunity", "opportunities")} from the CRM: ${plural(res.changes, "new change")} there.`
        + (res.failed ? ` ${res.failed} could not be read: ${res.errors.join("; ")}` : ""), cls: res.failed ? "warn" : "good" }];
    });
  },
  crmSync(t, e, ctx) {
    st.write("crm", t, ctx, () => ctx.api.post("/crm/sync"), () => { st.ui.notice = [{ text: "CRM sync ran. The card shows what is still waiting.", cls: "good" }]; });
  },
};
