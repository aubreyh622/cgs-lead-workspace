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

// The approved email of an opportunity (outreach.waiting): waiting for a second approval, about to go out, or not
// sent and why. me: the person acting; the second approver sees the email and decides here.
export function waitingNotice(w, me = "") {
  if (!w) return "";
  if (w.status === "approval") {
    const mine = me && me === w.approver;
    return html`<div class="block"><div class="notice ${mine ? "warn" : ""}" role="status">${icon("clock")}<div style="flex:1;min-width:0">
      <b>${mine ? `${w.by} asks you to approve this email to ${w.to}.` : `Approved by ${w.by}. It waits for ${w.approver}'s approval before it goes to ${w.to}.`}</b>
      <p class="meta">${mine ? `It goes out as ${w.by}, from ${w.by}'s mailbox, once you approve it. Nothing is sent before.`
        : `${w.approver} sees it on their Home page. You can withdraw it until then: nothing is sent and the draft stays as it is.`}</p>
      ${mine ? html`
      <div class="email-plain" style="margin-top:10px"><b>${w.subject}</b>\n\n${w.body || ""}</div>
      <form class="panel-form" data-act="secondForm" data-on="submit" data-out="${w.id}" style="margin-top:10px">
        <label class="field">Note for ${w.by} <span class="hint">Needed to return it: what to change.</span><textarea name="note" rows="2"></textarea></label>
        <div class="actions">
          <button type="submit" class="btn primary small" name="decision" value="approve">Approve and send</button>
          <button type="submit" class="btn small" name="decision" value="return">Return to ${w.by}</button>
        </div>
      </form>` : html`<div class="actions" style="margin-top:10px">
        <button type="button" class="btn small" data-act="undoEmail" data-out="${w.id}">Withdraw</button></div>`}
    </div></div></div>`;
  }
  if (w.status === "returned") {
    return html`<div class="block"><div class="notice bad" role="alert">${icon("alert")}<span><b>The email to ${w.to} was returned; nothing was sent.</b>
      ${w.error || ""} Change the draft, then approve it again.</span></div></div>`;
  }
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
  return !!w && (w.status === "queued" || w.status === "sending" || w.status === "approval");
}

// actions.secondForm = secondFormAct: the second approver approves (it goes out after the undo window) or returns it.
export async function secondFormAct(form, e, ctx) {
  const decision = (e.submitter && e.submitter.value) || "approve";
  const note = form.elements.note.value.trim();
  if (decision === "return" && note.length < 3) { ctx.toast("Say what to change, so the writer knows."); return; }
  form.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  try {
    const res = await ctx.api.post(`/outbound/${enc(form.dataset.out)}/second-approval`, { decision, note, delay: decision === "approve" ? UNDO_SECONDS : 0 });
    if (res.status === "returned") ctx.toast("Returned with your note. Nothing was sent.");
    else offerUndo(ctx, `Approved. The email goes to ${res.to} in ${res.seconds} seconds.`, () => undoEmail(ctx, res.id), { seconds: res.seconds, key: `email:${res.id}` });
  } catch (err) {
    ctx.toast(err.message);
  }
  ctx.refresh();
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
