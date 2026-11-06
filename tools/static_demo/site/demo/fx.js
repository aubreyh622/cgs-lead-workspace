// Static demo: motion that shows the agents at work. New research steps slide in one by one, an agent's first
// draft types itself out the first time it is shown, changed counts pop, and a card shows which agent is working.
// The workspace redraws whole pages, so every effect keys on content it has not shown before.
import { onChange } from "./runtime.js";

const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const seen = { steps: new Set(), drafts: new Set(), counts: new Map(), ready: new Set() };
const main = document.getElementById("main");

function stepsIn(root) {
  let delay = 0;
  root.querySelectorAll(".steps li").forEach((li) => {
    const key = li.textContent.trim();
    if (seen.steps.has(key)) return;
    seen.steps.add(key);
    if (reduce || li.classList.contains("now")) return;
    li.classList.add("fx-in");
    li.style.animationDelay = `${delay}ms`;
    delay += 180;
  });
}

// The draft of an agent's first email: typed out once per draft.
function typeDrafts(root) {
  root.querySelectorAll(".email-body, .email-plain").forEach((box) => {
    const key = box.textContent.trim().slice(0, 160);
    if (!key || seen.drafts.has(key)) return;
    seen.drafts.add(key);
    if (reduce || !box.closest(".detail")) return;
    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push([walker.currentNode, walker.currentNode.textContent]);
    const total = nodes.reduce((n, [, t]) => n + t.length, 0);
    if (total < 40) return;
    nodes.forEach(([n]) => { n.textContent = ""; });
    box.classList.add("fx-typing");
    const per = Math.max(2, Math.ceil(total / 110)); // about two seconds whatever the length
    let i = 0, done = 0;
    const tick = () => {
      if (!box.isConnected) return;
      let budget = per;
      while (budget > 0 && i < nodes.length) {
        const [n, t] = nodes[i];
        const take = Math.min(budget, t.length - n.textContent.length);
        n.textContent = t.slice(0, n.textContent.length + take);
        budget -= take;
        done += take;
        if (n.textContent.length >= t.length) i += 1;
      }
      if (i < nodes.length) requestAnimationFrame(tick);
      else box.classList.remove("fx-typing");
    };
    requestAnimationFrame(tick);
  });
}

function pop(el) {
  if (reduce) return;
  el.classList.remove("fx-pop");
  void el.offsetWidth;
  el.classList.add("fx-pop");
}

function counts() {
  document.querySelectorAll(".nav a").forEach((a) => {
    const c = a.querySelector(".count");
    const key = a.getAttribute("href");
    const now = c ? c.textContent : "";
    const was = seen.counts.get(key);
    seen.counts.set(key, now);
    if (c && was !== undefined && was !== now) pop(c);
  });
}

function readiness(root) {
  root.querySelectorAll(".tag.good").forEach((t) => {
    const key = `${location.hash.split("&")[0]}|${t.textContent.trim()}|${t.closest("[id]")?.id || ""}`;
    if (seen.ready.has(key)) return;
    const first = seen.ready.size === 0;
    seen.ready.add(key);
    if (!first) pop(t);
  });
}

let queued = false;
function scan() {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => {
    queued = false;
    stepsIn(main);
    typeDrafts(main);
    readiness(main);
    counts();
  });
}
new MutationObserver(scan).observe(main, { childList: true, subtree: true });
new MutationObserver(counts).observe(document.getElementById("top"), { childList: true, subtree: true });

// ---------- the "agent at work" card ----------
const card = document.createElement("div");
card.className = "fx-work";
card.setAttribute("role", "status");
card.setAttribute("aria-live", "polite");
document.body.appendChild(card);
let hideTimer = null;

function showWork({ agent, label, ms }) {
  clearTimeout(hideTimer);
  card.innerHTML = `<span class="fx-orbit" aria-hidden="true"><i></i></span>
    <div class="fx-text"><b class="fx-agent"></b><span class="fx-label"></span><div class="fx-bar"><i></i></div></div>`;
  card.querySelector(".fx-agent").textContent = agent;
  card.querySelector(".fx-label").textContent = label;
  const bar = card.querySelector(".fx-bar i");
  bar.style.transitionDuration = `${Math.max(300, ms)}ms`;
  card.classList.add("on");
  requestAnimationFrame(() => requestAnimationFrame(() => { bar.style.width = "100%"; }));
}

function doneWork() {
  if (!card.classList.contains("on")) return;
  card.classList.add("done");
  const span = card.querySelector(".fx-label");
  if (span) span.textContent = "Done";
  hideTimer = setTimeout(() => card.classList.remove("on", "done"), 1400);
}

onChange((e) => {
  if (e.type === "working") showWork(e);
  if (e.type === "done") doneWork();
});
