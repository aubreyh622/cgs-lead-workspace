// Shared form and tab behaviour for the Sources and Agents pages.
import { html, icon } from "./lib.js";
import { ApiError } from "./api.js";

// A button shows a spinner and cannot be pressed twice while its request runs.
export async function busy(button, fn) {
  if (button) { button.classList.add("busy"); button.disabled = true; }
  try {
    return await fn();
  } finally {
    if (button && button.isConnected) { button.classList.remove("busy"); button.disabled = false; }
  }
}

// Disable the submit buttons and say what is missing (DESIGN.md: a disabled submit has a line saying why).
export function missing(form, text) {
  form.querySelectorAll("[type=submit]").forEach((b) => { b.disabled = !!text; });
  const line = form.querySelector("[data-missing]");
  if (line) line.textContent = text || "";
}

export function errorText(err) {
  return err instanceof ApiError && err.code === "revision_conflict"
    ? "Someone changed this while you were looking. The page now shows the latest version; check it and try again."
    : err.message;
}

// Page-local state for open forms, errors and notices, with a helper that runs one write and draws again.
export function pageState() {
  const ui = { open: null, error: null, notice: null };
  return {
    ui,
    reset() { ui.open = null; ui.error = null; ui.notice = null; },
    err(where) {
      return ui.error && ui.error.where === where ? html`<div class="notice bad" role="alert">${icon("alert")}<span>${ui.error.message}</span></div>` : "";
    },
    notices() {
      if (!ui.notice) return "";
      return html`<div class="stack" style="margin-bottom:20px">${ui.notice.map((n) => html`<div class="notice ${n.cls || ""}">${icon(n.cls === "good" ? "check" : "alert")}<span>${n.text}</span></div>`)}</div>`;
    },
    async write(where, button, ctx, fn, after) {
      try {
        const res = await busy(button, fn);
        ui.error = null;
        ui.open = null;
        ui.notice = null; // a notice describes the last action only
        if (after) after(res);
      } catch (e) {
        ui.error = { where, message: errorText(e) };
      }
      ctx.refresh();
    },
  };
}

// Arrow keys, Home and End move between tabs (DESIGN.md). go(tabId) navigates.
export function tabKeys(t, e, ctx, go) {
  if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
  const tabs = [...t.querySelectorAll("[role=tab]")];
  const i = tabs.indexOf(document.activeElement);
  if (i < 0) return;
  e.preventDefault();
  const n = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : e.key === "Home" ? 0 : tabs.length - 1;
  ctx.focusAfter = `#${tabs[n].id}`;
  go(tabs[n].dataset.tab);
}

export function tabBar(label, tabs, current, counts = {}, { act = "tab", keys = "tabkeys" } = {}) {
  return html`
  <div class="tabs" role="tablist" aria-label="${label}" data-act="${keys}" data-on="keydown">
    ${tabs.map(([id, text]) => html`
      <button type="button" role="tab" id="tab-${id}" aria-selected="${current === id ? "true" : "false"}" aria-controls="panel"
        tabindex="${current === id ? "0" : "-1"}" data-act="${act}" data-tab="${id}">${text}${counts[id] !== undefined ? html`<span class="count">${counts[id]}</span>` : ""}</button>`)}
  </div>`;
}

export const cancelButton = html`<button type="button" class="btn ghost" data-act="open" data-open="">Cancel</button>`;

export function openButton(name, label, cls = "btn small", aria = "") {
  return html`<button type="button" class="${cls}" data-act="open" data-open="${name}" ${aria ? html`aria-label="${aria}"` : ""}>${label}</button>`;
}
