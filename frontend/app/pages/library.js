// Library (design/redesign/Library.dc.html): what the agents may quote. The CGS library is searched passage by passage
// (GET /api/knowledge, the same search the Outreach agent uses); documents copied from Google Drive are listed with
// the sync (GET /api/drive, POST /api/drive/sync).
//
// The backend has no list of every document with its passages and times quoted, and no per-document switch
// (REDESIGN_PLAN G19): the page shows the totals it has, the search, and the Drive documents.
import { html, icon, day, plural, safeUrl } from "../lib.js";
import { enc } from "../api.js";
import { emptyState } from "../shell.js";
import { pageState } from "../forms.js";

export const title = "Library";

const KIND = { service: "Service", solution: "Solution", case: "Case study", document: "Document", page: "Web page", insight: "Insight" };
const st = pageState();

export async function load(ctx, params) {
  const q = (params.q || "").trim();
  // The search answers with the library's size too; with no words typed, one broad word gets the totals.
  const [found, drive] = await Promise.all([
    ctx.api.get(`/knowledge?q=${enc(q.length >= 2 ? q : "cgs")}&k=${q.length >= 2 ? 20 : 1}`).catch((e) => ({ error: e.message, passages: [] })),
    ctx.api.get("/drive").catch((e) => ({ error: e.message, documents: [] })),
  ]);
  return { q, found, drive, results: q.length >= 2 ? found.passages : null };
}

export function view(d, ctx) {
  const f = d.found;
  const dr = d.drive;
  return html`
  <div class="page-head">
    <div>
      <h1>Library</h1>
      <p>What the agents may quote in briefs and emails: CGS services, cases and documents. Agents can only quote passages that are here.</p>
    </div>
    <div class="head-side">
      <form class="lib-search" role="search" data-act="search" data-on="submit">
        ${icon("search", 16)}<label class="sr-only" for="lib-q">Search the library</label>
        <input id="lib-q" type="search" name="q" value="${d.q}" placeholder="Search what agents can quote" minlength="2">
      </form>
      <button type="button" class="btn" data-act="sync" ${dr.ready ? "" : "disabled"} title="${dr.ready ? "Copy the CGS library documents from Google Drive now" : dr.off || "Google Drive is not set up"}">Sync from Drive</button>
    </div>
  </div>
  ${st.err("sync")}
  ${st.notices()}
  <div class="stat-row">
    <section class="card stat"><span class="meta">Passages agents can quote</span><span class="n2">${f.librarySize !== undefined ? f.librarySize : "?"}</span></section>
    <section class="card stat"><span class="meta">Documents from Drive</span><span class="n2">${(dr.documents || []).length}</span></section>
    <section class="card stat wide"><span class="meta">How it is searched</span>
      <span class="md">${f.search === "bm25" ? "Word match" : f.search || "Not known"}${f.embeddings ? `, with meaning search (${f.embeddable} of ${f.librarySize} passages)` : ""}</span>
      ${f.synthetic ? html`<span class="tag warn">Demo library: invented documents</span>` : ""}</section>
  </div>
  ${f.error ? html`<div class="notice bad" role="alert">${icon("alert")}<span>${f.error}</span></div>` : ""}
  ${d.results ? resultsCard(d) : html`<section class="card">${emptyState("Search the library", "Type a few words, for example a service or an industry, to see the passages an agent would find.")}</section>`}
  ${driveCard(dr, ctx)}
  <p class="meta-3" style="margin-top:16px">Documents from Drive stay inside CGS: they are copied into the workspace and never sent to an outside indexing service.</p>`;
}

function resultsCard(d) {
  return html`<section class="card" aria-labelledby="res-h">
    <div class="card-head"><h2 id="res-h">Passages for “${d.q}” <span class="cnt">${d.results.length}</span></h2><span class="meta-3">The best ${d.results.length} matches, as an agent would find them</span></div>
    ${d.results.length ? html`<ul class="rows">${d.results.map((p) => html`<li style="align-items:flex-start">
      <span class="what"><span class="row-flex" style="gap:8px"><b>${p.title}</b><span class="tag">${KIND[p.type] || p.type}</span>${p.synthetic ? html`<span class="tag warn">Synthetic</span>` : ""}</span>
        <span class="meta" style="display:block;margin-top:4px">${p.text}</span>
        ${p.source ? html`<span class="meta-3">${safeUrl(p.source) ? html`<a href="${safeUrl(p.source)}" target="_blank" rel="noopener noreferrer">${p.source}</a>` : p.source}</span>` : ""}</span>
    </li>`)}</ul>` : emptyState("Nothing found", "Try other words. Agents would not find anything to quote for these either.")}
  </section>`;
}

function driveCard(dr, ctx) {
  return html`<section class="card" aria-labelledby="drive-h" style="margin-top:16px">
    <div class="card-head"><div><h2 id="drive-h">Documents from Google Drive <span class="cnt">${(dr.documents || []).length}</span></h2>
      <p class="meta">${dr.ready ? `Copied from ${plural((dr.folders || []).length, "folder")}${dr.lastSync ? `, last synced ${day(dr.lastSync.on || dr.lastSync, ctx.today)}` : ", not synced yet"}.` : dr.off || dr.error || "Google Drive is not set up."}</p></div></div>
    ${(dr.documents || []).length ? html`<div class="table-wrap"><table class="list">
      <thead><tr><th scope="col">Document</th><th scope="col">Type</th><th scope="col">Folder</th><th scope="col">Changed</th></tr></thead>
      <tbody>${dr.documents.map((x) => html`<tr>
        <td><b>${safeUrl(x.link) ? html`<a href="${safeUrl(x.link)}" target="_blank" rel="noopener noreferrer">${x.name}</a>` : x.name}</b></td>
        <td><span class="tag">${x.kind}</span></td><td class="meta">${x.path}</td><td class="meta">${x.modified ? day(String(x.modified).slice(0, 10), ctx.today) : ""}</td></tr>`)}</tbody>
    </table></div>` : html`<p class="meta" style="padding:0 20px 18px">No documents from Drive yet.</p>`}
  </section>`;
}

export const actions = {
  search(form, e, ctx) {
    const q = form.elements.q.value.trim();
    ctx.go("library", { q: q.length >= 2 ? q : undefined });
  },
  sync(t, e, ctx) {
    st.write("sync", t, ctx, () => ctx.api.post("/drive/sync"), (res) => {
      st.ui.notice = [{ text: `Drive sync: ${plural(res.read, "document")} read, ${res.unchanged} unchanged, ${res.removed} removed. The library has ${plural(res.passages, "passage")}.`
        + (res.errors.length ? ` ${plural(res.errors.length, "problem")}: ${res.errors.join("; ")}` : ""), cls: res.errors.length ? "warn" : "good" }];
    });
  },
};
