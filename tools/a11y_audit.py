"""Accessibility audit of the workspace page (development tool; needs Playwright and Chromium).

Opens every page and tab of a running workspace, plus the first form each one offers, and checks:
form controls and buttons have a name, ids are unique, heading levels do not skip, there is one h1,
and text meets WCAG AA contrast against its background.

    python tools/a11y_audit.py http://127.0.0.1:8810 "Alex Morgan"

Exit code 1 when anything is found. It only reads pages and opens forms; it submits nothing.
"""
from __future__ import annotations

import json
import sys

ROUTES = [
    "home",
    *[f"companies?tab={t}" for t in ("new", "research", "review", "accepted", "aside")],
    *[f"outreach?tab={t}" for t in ("approve", "waiting", "nurture", "talking")],
    *[f"qualification?tab={t}" for t in ("active", "approval", "qualification", "closed")],
    *[f"sources?tab={t}" for t in ("find", "direction", "learned")],
    "agents",  # the six agent cards
    *[f"agents?tab={t}" for t in ("controls", "work", "runs", "permissions", "lessons", "scorecard")],
    *[f"agents?agent=research&dtab={t}" for t in ("overview", "work", "runs", "permissions", "lessons")],
]

CHECK = r"""
() => {
  const out = [];
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== "hidden";
  const name = (el) => {
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label").trim();
    const by = el.getAttribute("aria-labelledby");
    if (by) return by.split(/\s+/).map((id) => (document.getElementById(id) || {}).textContent || "").join(" ").trim();
    if (el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) return l.textContent.trim(); }
    const wrap = el.closest("label");
    if (wrap) return wrap.textContent.trim();
    if (["BUTTON", "A"].includes(el.tagName)) return el.textContent.trim();
    return "";
  };
  const where = (el) => `${el.tagName.toLowerCase()}${el.name ? `[name=${el.name}]` : ""} "${(el.textContent || el.value || "").trim().slice(0, 40)}"`;
  for (const el of document.querySelectorAll("input:not([type=hidden]), select, textarea, button, a[href]")) {
    if (!visible(el)) continue;
    if (!name(el)) out.push(`no accessible name: ${where(el)}`);
  }
  const ids = {};
  for (const el of document.querySelectorAll("[id]")) ids[el.id] = (ids[el.id] || 0) + 1;
  for (const [id, n] of Object.entries(ids)) if (n > 1) out.push(`duplicate id: ${id} (${n})`);
  const hs = [...document.querySelectorAll("main h1, main h2, main h3, main h4")].filter(visible);
  if (hs.filter((h) => h.tagName === "H1").length !== 1) out.push(`h1 count: ${hs.filter((h) => h.tagName === "H1").length}`);
  let last = 1;
  for (const h of hs) {
    const lvl = Number(h.tagName[1]);
    if (lvl > last + 1) out.push(`heading jumps from h${last} to h${lvl}: "${h.textContent.trim().slice(0, 40)}"`);
    last = lvl;
  }
  // contrast
  const rgb = (s) => (s.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => { const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor); if (c.length >= 3 && (c.length < 4 || c[3] > 0.5)) return c; } return [255, 255, 255]; };
  const seen = new Set();
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el) || ![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    const cs = getComputedStyle(el);
    if (el.closest("[disabled], .btn:disabled, [aria-hidden=true]")) continue;
    const fg = rgb(cs.color), bg = bgOf(el);
    const a = lum(fg), b = lum(bg);
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    const size = parseFloat(cs.fontSize), bold = Number(cs.fontWeight) >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    if (ratio < need) {
      const key = `${cs.color}|${bg}|${size}`;
      if (!seen.has(key)) { seen.add(key); out.push(`contrast ${ratio.toFixed(2)} < ${need}: ${where(el)} (${cs.color} on rgb(${bg.join(",")}), ${size}px)`); }
    }
  }
  return out;
}
"""


def main(base: str, person: str) -> int:
    from playwright.sync_api import sync_playwright

    problems: dict[str, list[str]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{base}/")
        page.evaluate(f"localStorage.setItem('cgs.person', {json.dumps(person)})")
        for route in ROUTES:
            page.goto(f"{base}/#/{route}")
            page.wait_for_selector(".page-head h1", timeout=15000)
            page.wait_for_timeout(300)
            found = page.evaluate(CHECK)
            # open the first form the page offers (an "open" or "mode" button) and check again
            opener = page.query_selector("main [data-act=open][data-open]:not([disabled]), main [data-act=mode][data-mode]:not([data-mode='']):not([disabled])")
            if opener:
                opener.click()
                page.wait_for_timeout(600)
                found += [f"(form open) {x}" for x in page.evaluate(CHECK)]
            if found:
                problems[route] = sorted(set(found))
        # keyboard: the page puts focus on the main region; the skip button shows when focused;
        # Tab moves to a control with a visible focus ring
        page.goto(f"{base}/#/home")
        page.wait_for_selector(".page-head h1")
        start = page.evaluate("() => document.activeElement.id")
        page.focus(".skip")
        skip_top = page.evaluate("() => document.querySelector('.skip').getBoundingClientRect().top")
        page.focus("main")
        page.keyboard.press("Tab")
        ring = page.evaluate("() => { const cs = getComputedStyle(document.activeElement); return [document.activeElement.tagName, cs.outlineStyle, cs.outlineWidth]; }")
        if start != "main":
            problems.setdefault("keyboard", []).append(f"focus after load is on #{start}, not main")
        if skip_top < 0:
            problems.setdefault("keyboard", []).append("the skip button stays off screen when focused")
        if ring[1] == "none" or ring[2] == "0px":
            problems.setdefault("keyboard", []).append(f"no visible focus ring on {ring[0]}")
        browser.close()
    for route, items in problems.items():
        print(f"## {route}")
        for x in items:
            print(f"  - {x}")
    print(f"{sum(len(v) for v in problems.values())} problem(s) on {len(problems)} of {len(ROUTES) + 1} checks.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8800", sys.argv[2] if len(sys.argv) > 2 else "Alex Morgan"))
