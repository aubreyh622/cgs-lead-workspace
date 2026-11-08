// Undo (api/undo.py): an approved email waits UNDO_SECONDS before it goes out, a Step 4 accept or reject can be taken
// back for as long, and a skipped candidate can be put back. Pages offer it in a toast and on the item itself.
import { html, icon } from "./lib.js";
import { enc } from "./api.js";
import { dropToast } from "./shell.js";

export const UNDO_SECONDS = 10;

const clock = (iso) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

// A toast with an Undo button for `seconds`, then the page is drawn again so it shows what happened.
// undo() makes the request; its result message is shown, or the error.
// key: what is being undone (for example "email:<id>"); an undo made on the page closes this toast.
export function offerUndo(ctx, message, undo, { seconds = UNDO_SECONDS, link, key } = {}) {
  // The page, not the full address: the caller usually moves on to the next item right after this.
  const page = pageOf();
  const here = () => pageOf() === page;
  ctx.toast(message, {
    link, key, timeout: seconds * 1000,
    action: {
      label: "Undo",
      run: async () => {
        try {
          ctx.toast(await undo());
        } catch (err) {
          ctx.toast(err.message || "That could not be undone.");
        }
        if (here()) ctx.refresh();
      },
    },
  });
  setTimeout(() => { if (here() && !isTyping()) ctx.refresh(); }, seconds * 1000 + 1500);
}

const pageOf = () => location.hash.split("?")[0];

function isTyping() {
  const a = document.activeElement;
  return !!a && a.matches("input, textarea, select");
}

export const undoEmail = (ctx, id) => ctx.api.post(`/outbound/${enc(id)}/undo`).then(() => "Undone. Nothing was sent; the draft is unchanged.");

// The approved email of an opportunity (outreach.waiting): about to go out, or not sent and why.
export function waitingNotice(w) {
  if (!w) return "";
  if (w.status === "queued" || w.status === "sending") {
    return html`<div class="block"><div class="notice" role="status">${icon("clock")}<div style="flex:1;min-width:0">
      <b>${w.status === "sending" ? `Sending to ${w.to}…` : `Approved by ${w.by}. It goes to ${w.to} at ${clock(w.sendAt)}.`}</b>
      <p class="meta">Until then you can undo it: nothing is sent and the draft stays as it is.</p>
      ${w.status === "queued" ? html`<div class="actions" style="margin-top:10px">
        <button type="button" class="btn small" data-act="undoEmail" data-out="${w.id}">Undo</button>
        <button type="button" class="btn small ghost" data-act="sendNow" data-out="${w.id}">Send now</button></div>` : ""}
    </div></div></div>`;
  }
  if (w.status === "unknown") {
    return html`<div class="block"><div class="notice bad" role="alert">${icon("alert")}<span><b>The email to ${w.to} may or may not have gone out.</b> ${w.error || "The workspace stopped while it was being sent."}</span></div></div>`;
  }
  const why = w.status === "expired" ? "The workspace was stopped before it went out." : w.error || "It could not be sent.";
  return html`<div class="block"><div class="notice bad" role="alert">${icon("alert")}<span><b>The email to ${w.to} was not sent.</b> ${why} Approve it again to send it.</span></div></div>`;
}

export function emailWaits(w) {
  return !!w && (w.status === "queued" || w.status === "sending");
}

// Page actions: actions.undoEmail = undoEmailAct, actions.sendNow = sendNowAct.
export async function undoEmailAct(t, e, ctx) {
  t.disabled = true;
  dropToast(`email:${t.dataset.out}`);
  try {
    ctx.toast(await undoEmail(ctx, t.dataset.out));
  } catch (err) {
    ctx.toast(err.message);
  }
  ctx.refresh();
}

export async function sendNowAct(t, e, ctx) {
  t.disabled = true;
  dropToast(`email:${t.dataset.out}`);
  try {
    const res = await ctx.api.post(`/outbound/${enc(t.dataset.out)}/send-now`);
    ctx.toast(`Sent to ${res.to}.`);
  } catch (err) {
    ctx.toast(err.message);
  }
  ctx.refresh();
}
