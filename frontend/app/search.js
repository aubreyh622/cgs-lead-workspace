// Search from the side rail (Ctrl K): companies and people from the workspace's own records, and the pages.
// Everything comes from the API; nothing is searched outside the workspace. A native <dialog> keeps focus inside and
// closes on Escape.
import { html, href, icon, initials } from "./lib.js";
import { STAGE_LABELS } from "./options.js";
import { NAV } from "./shell.js";

const box = () => document.getElementById("palette");
let records = null;   // [{ name, sub, stage, href, tone }]
let loadedAt = 0;
let api = null;
let opener = null;    // the element that had focus, to give it back
let went = false;     // closed by following a result: the new page takes focus

// Who has the next move at each stage: orange waits on you, purple is agent work, green is done.
const TONE = { review: "warn", approval: "warn", researching: "info", qualification: "good" };

const PAGES = [...NAV.flatMap((g) => g.items.map(([id, label]) => [id, label])), ["settings", "Settings"]];

async function load() {
  if (records && Date.now() - loadedAt < 60000) return;
  const [opps, cands] = await Promise.all([api.get("/opportunities"), api.get("/candidates?status=new")]);
  records = [
    ...opps.map((o) => ({ name: o.company, sub: o.contact && o.contact.name ? `${o.contact.name}, ${o.contact.title || ""}`.replace(/, $/, "") : o.title,
      stage: STAGE_LABELS[o.stage] || o.stage, tone: TONE[o.stage] || "", href: href("record", { id: o.id }) })), // D4: the full record
    ...cands.map((c) => ({ name: c.company, sub: c.person ? `${c.person}${c.title ? `, ${c.title}` : ""}` : c.signal || "",
      stage: "Found", tone: "warn", href: href("companies", { tab: "found", id: c.id }) })),
  ];
  loadedAt = Date.now();
}

function results(q) {
  const t = q.trim().toLowerCase();
  const hit = (x) => !t || `${x.name} ${x.sub}`.toLowerCase().includes(t);
  const found = (records || []).filter(hit).slice(0, t ? 8 : 5);
  const pages = PAGES.filter(([, label]) => !t || label.toLowerCase().includes(t)).slice(0, t ? 7 : 3);
  return html`
    ${found.length ? html`<div class="palette-group" id="pal-companies">${t ? "Companies and people" : "Recent companies"}</div>
      <div role="group" aria-labelledby="pal-companies">${found.map((x) => html`<a href="${x.href}" data-pal="go">
        <span class="avatar" aria-hidden="true">${initials(x.name)}</span>
        <span class="what"><b>${x.name}</b><span class="meta-3">${x.sub}</span></span>
        <span class="tag ${x.tone}">${x.stage}</span></a>`)}</div>` : ""}
    ${pages.length ? html`<div class="palette-group" id="pal-pages">Pages</div>
      <div role="group" aria-labelledby="pal-pages">${pages.map(([id, label]) => html`<a href="${href(id)}" data-pal="go">
        <span class="avatar" aria-hidden="true">${label.slice(0, 1)}</span><span class="what"><b>${label}</b><span class="meta-3">Page</span></span></a>`)}</div>` : ""}
    ${!found.length && !pages.length ? html`<p class="palette-empty">Nothing matches. Try a company, a person or a page name.</p>` : ""}
    ${records === null ? html`<p class="palette-empty">Loading companies…</p>` : ""}`;
}

function drawList() {
  const d = box();
  const input = d.querySelector("input");
  d.querySelector(".palette-list").innerHTML = String(results(input ? input.value : ""));
}

export async function openSearch(client) {
  api = client;
  const d = box();
  if (!d || d.open) return;
  opener = document.activeElement;
  d.innerHTML = String(html`
    <div class="palette-in">${icon("search", 16)}
      <input type="search" placeholder="Search companies, people and pages" aria-label="Search companies, people and pages" autocomplete="off"></div>
    <div class="palette-list"></div>
    <div class="palette-foot"><span class="keys">Esc</span>to close<span class="keys" style="margin-left:8px">↓</span><span class="keys">↑</span>to move</div>`);
  drawList();
  d.showModal();
  d.querySelector("input").focus();
  try { await load(); } catch { records = []; }
  if (d.open) drawList();
}

export function initSearch(client) {
  api = client;
  const d = box();
  if (!d) return;
  d.addEventListener("input", (e) => { if (e.target.matches("input")) drawList(); });
  d.addEventListener("click", (e) => {
    if (e.target === d) d.close(); // a click on the backdrop
    if (e.target.closest("[data-pal=go]")) { went = true; d.close(); }
  });
  d.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const links = [...d.querySelectorAll("[data-pal=go]")];
    if (!links.length) return;
    e.preventDefault();
    const at = links.indexOf(document.activeElement);
    const next = e.key === "ArrowDown" ? (at + 1) % links.length : at <= 0 ? links.length - 1 : at - 1;
    links[next].focus();
  });
  d.addEventListener("close", () => {
    d.innerHTML = "";
    if (!went && opener && document.contains(opener)) opener.focus();
    opener = null;
    went = false;
  });
  // Another page opened (Back, a notification link): the dialog belongs to the page it was opened on.
  window.addEventListener("hashchange", () => { if (d.open) { went = true; d.close(); } });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openSearch(api);
    }
  });
}

// After a change the person made, the next search reads the records again.
export function forgetSearch() { records = null; }
