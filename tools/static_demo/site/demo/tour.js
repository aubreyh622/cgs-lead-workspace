// Static demo: the guide. A card at the bottom left says what the next step of the story is, takes the visitor to the
// right page, rings the button to press, and can fill in the form. Visitors may also explore on their own; the guide
// follows the recorded state (demo/runtime.js), so it always shows the step that comes next.
import { nextStep, state, onChange, restart } from "./runtime.js";

const CORVANE = "Corvane";
const ME = () => localStorage.getItem("cgs.person") || "";

// One entry per recorded click (its label in the recording) or automatic change. targets: tried in order, the
// first one on screen gets the ring (so a form's submit button wins over the button that opens the form).
const STEPS = {
  "Research Corvane Industrial": {
    title: "Pick a company to research",
    text: "The discovery agent found these companies for CGS's research direction. Corvane Industrial just announced an AI inspection pilot. Click Research and the research agent writes a brief.",
    route: "companies?tab=new",
    targets: [{ sel: "[data-act=researchOne]", inside: ".cand", text: CORVANE }],
  },
  researching: {
    title: "The research agent is at work",
    text: "It reads public pages, checks them against each other and writes a brief with a source for every fact. While researching Corvane it also spots a second opportunity at the same company.",
    route: "companies?tab=research",
    targets: [{ sel: ".steps" }],
  },
  "Accept Corvane Industrial": {
    title: "Decide on the brief",
    text: "Read what the agent found: the facts and their sources, who to contact, and its suggestion. Then click Accept, tick the duplicate check and press Accept. Nothing reaches the CRM or the client without a person's click.",
    route: "companies?tab=review&id=corvane",
    targets: [{ sel: "form[data-act=acceptForm] [type=submit]" }, { sel: "[data-act=mode][data-mode=accept]" }],
    fill: () => { const c = document.querySelector("form[data-act=acceptForm] input[name=duplicates_checked]"); if (c && !c.checked) c.click(); },
  },
  "The undo window ends": {
    title: "Accepted, with 10 seconds to undo",
    text: "Changed your mind? The notice offers Undo for 10 seconds. After that the lead goes to the CRM and the outreach agent starts on a first email.",
    targets: [{ sel: ".toast .toast-action" }],
  },
  "The outreach agent drafts the first email": {
    title: "The outreach agent writes the first email",
    text: "It uses the brief, the contact's role and CGS's own material. The draft waits for a person: agents never send email on their own.",
  },
  "Approve and send the first email": {
    title: "Approve the agent's email",
    text: "Open Corvane's first email in To approve. Edit it if you like, then choose Approve and send. The address is filled in for you.",
    route: "outreach?tab=approve",
    targets: [{ sel: "form[data-act=sendForm] button[value=send]" }, { sel: "[data-act=mode][data-mode=send]" }, { sel: ".pick", text: CORVANE }],
    fill: () => { const f = document.querySelector("form[data-act=sendForm] input[name=to]"); if (f && !f.value) setValue(f, "elena.fischer@corvane-demo.example"); },
  },
  "The email goes out": {
    title: "It waits 10 seconds before it goes",
    text: "An approved email can still be undone for 10 seconds. Click Send now, or let the time run out.",
    targets: [{ sel: "[data-act=sendNow]" }],
  },
  "Elena Fischer replies": {
    title: "Waiting for the client",
    text: "In this demo Elena Fischer answers within seconds. The workspace checks the email thread and finds her reply; the bell at the bottom right lists it.",
  },
  "Record the reply": {
    title: "Elena replied",
    text: "Corvane comes first in Waiting for a reply. Read the reply and click Record the reply: the lead becomes Active and the evidence agent reads it for qualification evidence.",
    route: "outreach?tab=waiting",
    targets: [{ sel: "[data-act=inboxAct][data-do=apply]" }, { sel: ".pick", text: CORVANE }],
  },
  "The evidence agent reads the reply": {
    title: "The evidence agent reads the reply",
    text: "It labels the reply and proposes evidence for the qualification scores. A person reviews every proposal; the rules, not the AI, calculate the scores.",
  },
  "Confirm Meridian's authority score": {
    title: "Close a qualification gap",
    text: "Meridian Industries is further along. Under To reach Ready the rules list what is missing. Click Confirm Authority, choose 4, give a reason and save.",
    route: "qualification?tab=active&id=meridian",
    targets: [{ sel: "form[data-act=reviewForm] [type=submit]" }, { sel: "[data-open='ready:score:A']" }, { sel: "[data-open='score:A']" }],
    fill: () => fillReview("4", "The CFO approves spend and joins the next call."),
  },
  "Confirm Meridian's commercial condition": {
    title: "One gap left",
    text: "One condition is still open: Funding and decision-access path. Click Confirm by hand, choose Met, give a reason and save. Watch the readiness change to Ready.",
    route: "qualification?tab=active&id=meridian",
    targets: [{ sel: "form[data-act=reviewForm] [type=submit]" }, { sel: "[data-open='ready:cond:commercial']" }, { sel: "[data-open='cond:commercial']" }],
    fill: () => fillReview("true", "Budget approved and the CFO decides."),
  },
  "Send Meridian's pack for approval": {
    title: "Send the pack for approval",
    text: "The rules say Ready. The pack freezes today's scores, conditions and need for the approver. Open it and send it.",
    route: "qualification?tab=active&id=meridian",
    targets: [{ sel: "form[data-act=packForm] [type=submit]" }, { sel: "[data-open=pack]" }],
  },
  "Sarah approves: Meridian is qualified": {
    title: "A colleague approves",
    text: "The owner cannot approve their own pack. Choose Sarah Patel under Acting as (top right), open Awaiting approval and approve Meridian.",
    route: "qualification?tab=approval&id=meridian",
    user: "Sarah Patel",
    targets: [{ sel: "form[data-act=quickDecide] button[value=approve]" }],
  },
  end: {
    title: "That's the whole flow",
    text: "From a company the agents found to a qualified opportunity: agents research, write and read; people decide at every step. Look around freely, or start again.",
    final: true,
  },
};
const ORDER = Object.keys(STEPS).filter((k) => k !== "researching");

function setValue(f, v) {
  f.value = v;
  f.dispatchEvent(new Event("input", { bubbles: true }));
}
function fillReview(value, reason) {
  const form = document.querySelector("form[data-act=reviewForm]");
  if (!form) return;
  const sel = form.querySelector("select[name=value]");
  if (sel) { sel.value = value; sel.dispatchEvent(new Event("change", { bubbles: true })); sel.dispatchEvent(new Event("input", { bubbles: true })); }
  const t = form.querySelector("textarea[name=reason]");
  if (t && !t.value) setValue(t, reason);
}

function visible(el) {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && !el.closest("[inert]");
}
function find(step) {
  if (step.user && ME() !== step.user) return document.querySelector("[data-shell=person]");
  for (const t of step.targets || []) {
    const els = [...document.querySelectorAll(t.sel)].filter(visible);
    const el = t.text ? els.find((e) => (t.inside ? e.closest(t.inside) : e)?.textContent.includes(t.text)) : els[0];
    if (el) return el;
  }
  return null;
}

function current() {
  const st = state();
  if (st.researching) return "researching";
  const { ui, auto } = nextStep();
  if (auto && auto.kind === "auto") return auto.label;
  if (ui) return ui.label;
  if (auto) return auto.label;
  return "end";
}

// ---------- the card ----------
const box = document.createElement("aside");
box.className = "tour";
box.setAttribute("aria-label", "Demo guide");
document.body.appendChild(box);
const ring = document.createElement("div");
ring.className = "tour-ring";
ring.setAttribute("aria-hidden", "true");
document.body.appendChild(ring);

let hidden = sessionStorage.getItem("demo.tourHidden") === "1";
let welcomed = sessionStorage.getItem("demo.welcomed") === "1";
let shownKey = "";

function draw() {
  const key = !welcomed ? "welcome" : current();
  if (hidden) {
    box.className = "tour min";
    box.innerHTML = `<button type="button" class="tour-open" data-tour="open">Guide</button>`;
    shownKey = "";
    return;
  }
  if (key === shownKey) return;
  shownKey = key;
  box.className = box.classList.contains("alt") ? "tour alt" : "tour";
  if (key === "welcome") {
    box.innerHTML = `
      <p class="tour-kicker">Interactive demo</p>
      <h2>CGS Lead Workspace</h2>
      <p>You are Alex Morgan, a CGS consultant. Six AI agents find companies, research them, write emails and read replies; you decide at every step.</p>
      <p class="tour-note">Everything here runs in your browser on invented companies. Nothing is sent anywhere, and no AI is called: the agents' work was recorded from the real workspace.</p>
      <div class="tour-actions"><button type="button" class="btn primary" data-tour="start">Start the guided tour</button>
      <button type="button" class="btn ghost" data-tour="hide">Explore on my own</button></div>`;
    return;
  }
  const step = STEPS[key] || { title: key, text: "" };
  const n = ORDER.indexOf(key);
  const auto = !step.route && !step.final && !(step.targets || []).length;
  box.innerHTML = `
    <div class="tour-top"><p class="tour-kicker">${n >= 0 ? `Step ${n + 1} of ${ORDER.length}` : "Step 2 of " + ORDER.length}</p>
      <button type="button" class="btn ghost small" data-tour="hide" aria-label="Hide the guide">Hide</button></div>
    <h2></h2><p class="tour-text"></p>
    ${auto || key === "researching" ? `<p class="tour-wait"><span class="fx-orbit small" aria-hidden="true"><i></i></span>Happening by itself…</p>` : ""}
    <div class="tour-actions">
      ${step.route ? `<button type="button" class="btn primary" data-tour="go">Show me</button>` : ""}
      ${step.fill ? `<button type="button" class="btn" data-tour="fill">Fill it in for me</button>` : ""}
      ${step.final ? `<button type="button" class="btn primary" data-tour="restart">Start again</button>` : ""}
    </div>
    <button type="button" class="tour-restart" data-tour="restart">Restart the demo</button>`;
  box.querySelector("h2").textContent = step.title;
  box.querySelector(".tour-text").textContent = step.text;
  box.classList.add("fx-swap");
}

function place() {
  const key = !welcomed || hidden ? "" : current();
  const step = STEPS[key];
  const el = step ? find(step) : null;
  if (!el) { ring.classList.remove("on"); box.classList.remove("alt"); return; }
  const r = el.getBoundingClientRect();
  Object.assign(ring.style, { left: `${r.left - 6}px`, top: `${r.top - 6}px`, width: `${r.width + 12}px`, height: `${r.height + 12}px` });
  ring.classList.add("on");
  // the card moves to the other corner rather than cover what it points at
  const home = { left: 24, right: 24 + 360, bottom: innerHeight - 24, top: innerHeight - 24 - box.offsetHeight };
  const covers = !(r.right < home.left || r.left > home.right || r.bottom < home.top || r.top > home.bottom);
  if (covers !== box.classList.contains("alt")) box.classList.toggle("alt", covers);
}

function routeOf(step) {
  if (step.user && ME() !== step.user) return null;
  return step.route;
}

box.addEventListener("click", (e) => {
  const b = e.target.closest("[data-tour]");
  if (!b) return;
  const what = b.dataset.tour;
  if (what === "start") { welcomed = true; sessionStorage.setItem("demo.welcomed", "1"); }
  if (what === "hide") { hidden = true; welcomed = true; sessionStorage.setItem("demo.welcomed", "1"); sessionStorage.setItem("demo.tourHidden", "1"); }
  if (what === "open") { hidden = false; sessionStorage.removeItem("demo.tourHidden"); }
  if (what === "restart") return restart();
  const step = STEPS[current()];
  if (what === "go" && step) {
    const r = routeOf(step);
    if (r && location.hash !== `#/${r}`) location.hash = `#/${r}`;
    setTimeout(() => { const el = find(step); if (el) el.scrollIntoView({ block: "center", behavior: "smooth" }); }, 500);
  }
  if (what === "fill" && step && step.fill) step.fill();
  if (what === "start") {
    const s = STEPS[current()];
    if (s && s.route) location.hash = `#/${s.route}`;
  }
  draw();
});

onChange(() => setTimeout(draw, 50));
// for tools/static_demo/check_site.py: what the ring is on
window.demoTourTarget = () => { const s = STEPS[current()]; return s ? find(s) : null; };
setInterval(() => { draw(); place(); }, 300);
draw();
