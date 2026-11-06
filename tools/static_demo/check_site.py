"""Click through the built static demo in Chromium, the way a visitor following the guide would.

    .venv/Scripts/python.exe -B tools/static_demo/check_site.py ../cgs-workspace-demo
    .venv/Scripts/python.exe -B tools/static_demo/check_site.py https://aubreyh622.github.io/cgs-workspace-demo/

Serves the folder on a local port, follows every step of the story, and fails on a script error, a request that
leaves the page's own folder (except the web font), or a step that does not move the story on. Screenshots go to
runtime/static-demo/shots/.
"""
from __future__ import annotations

import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "runtime" / "static-demo" / "shots"


def serve(folder: Path) -> str:
    """Serve the folder under /demo/, as GitHub Pages serves a project site under /<repository>/."""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            return super().translate_path(path[len("/demo"):] if path.startswith("/demo/") else "/__nothing__")

        def log_message(self, *a):
            pass
    handler = functools.partial(Handler, directory=str(folder))
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/demo/"


def main():
    arg = sys.argv[1]
    base = arg.rstrip("/") + "/" if arg.startswith("http") else serve(Path(arg).resolve())  # a published URL, or a folder
    SHOTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    errors, outside, misses = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        # a missing file of the site shows as a failed request; "Failed to load resource" also covers the blocked font
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("requestfailed", lambda r: errors.append(f"failed: {r.url}") if r.url.startswith(base) else None)
        page.on("response", lambda r: errors.append(f"{r.status}: {r.url}") if r.status >= 400 and r.url.startswith(base) else None)

        def route(r):
            u = r.request.url
            if u.startswith(base):
                return r.continue_()
            if "fonts.g" not in u:
                outside.append(u)
            return r.abort()
        page.route("**/*", route)
        page.goto(base)
        page.wait_for_selector("main .page-head h1")
        page.wait_for_selector(".tour [data-tour=start]")
        page.screenshot(path=str(SHOTS / "00-welcome.png"))
        page.click(".tour [data-tour=start]")

        def step_title():
            return page.inner_text(".tour h2")

        def ring_click(n: int, name: str, fill=False, wait_change=True, timeout=30000):
            """Press Show me, optionally Fill it in, then click what the ring is on until the step changes."""
            title = step_title()
            if page.query_selector(".tour [data-tour=go]"):
                page.click(".tour [data-tour=go]")
                page.wait_for_timeout(900)
            for _ in range(6):
                if step_title() != title:
                    break
                if fill and page.query_selector(".tour [data-tour=fill]"):
                    page.click(".tour [data-tour=fill]")
                    page.wait_for_timeout(200)
                target = page.evaluate("""() => {
                    const el = window.demoTourTarget && window.demoTourTarget(); if (!el) return null;
                    el.scrollIntoView({ block: "center" });
                    const b = el.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2];
                }""")
                if not target:
                    page.wait_for_timeout(700)
                    continue
                page.mouse.click(*target)
                page.wait_for_timeout(1200)
            page.screenshot(path=str(SHOTS / f"{n:02d}-{name}.png"))
            if wait_change:
                page.wait_for_function(f"document.querySelector('.tour h2') && document.querySelector('.tour h2').textContent !== {title!r}",
                                       timeout=timeout)
            print(f"{n:02d} {title} -> {step_title()}", flush=True)

        def wait_auto(n: int, name: str, timeout=40000):
            title = step_title()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SHOTS / f"{n:02d}-{name}.png"))
            page.wait_for_function(f"document.querySelector('.tour h2').textContent !== {title!r}", timeout=timeout)
            print(f"{n:02d} {title} -> {step_title()} (by itself)", flush=True)

        ring_click(1, "research")
        page.goto(base + "#/companies?tab=research")
        wait_auto(2, "researching")
        ring_click(3, "accept", fill=True)
        wait_auto(4, "undo-window")
        wait_auto(5, "outreach-agent")
        ring_click(6, "approve-send", fill=True)
        ring_click(7, "send-now")
        wait_auto(8, "reply-arrives")
        ring_click(9, "record-reply")
        wait_auto(10, "evidence-agent")
        ring_click(11, "authority", fill=True)
        ring_click(12, "commercial", fill=True)
        ring_click(13, "pack")
        # Step 8 as Sarah: the ring is on the person picker first
        page.select_option("[data-shell=person]", "Sarah Patel")
        page.wait_for_timeout(1200)
        ring_click(14, "approve")
        page.screenshot(path=str(SHOTS / "15-end.png"))
        for h in ["#/home", "#/companies?tab=accepted", "#/outreach?tab=talking", "#/qualification?tab=qualification&team=1", "#/agents"]:
            page.goto(base + h)
            page.wait_for_selector("main .page-head h1")
            page.wait_for_timeout(600)
            if page.query_selector("main .notice.bad, main .error"):
                misses.append(h)
        browser.close()

    ok = not errors and not outside and not misses
    for e in errors:
        print("error:", e)
    for u in outside:
        print("outside request:", u)
    for m in misses:
        print("error card on:", m)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
