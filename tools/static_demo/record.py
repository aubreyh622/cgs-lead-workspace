"""Record the workspace for the static demo (tools/static_demo/README.md).

Starts the real v2 backend in this process on a fresh synthetic demo database (APP_MODE=demo, mock mailbox, mock CRM,
no knowledge folder, network blocked by the test offline guard), then drives the workspace page in Chromium through the
demo story. After every step it visits every page as the people in the story and keeps each API answer, so the static
site can answer the same requests later without a server.

    .venv/Scripts/python.exe -B tools/static_demo/record.py --out runtime/static-demo/recording.json

Each change (a person's click, or something the backend did on its own, such as an undo window closing or an agent
run) starts a new "epoch". The output lists the epochs, the answers recorded in each, and the transitions between them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "offline_guard"))

ALEX, SARAH = "Alex Morgan", "Sarah Patel"
CLIENT_EMAIL = "elena.fischer@corvane-demo.example"
CLIENT_REPLY = ("Thanks for reaching out. The board asked us to bring in outside help to roll the AI inspection pilot out "
                "to all twelve plants. Could we talk next Tuesday or Wednesday? I would like our COO to join.")

# The pages the crawl opens in every epoch; links found on them (items, agents) are opened too.
SEED_ROUTES = [
    "home",
    "companies?tab=new", "companies?tab=research", "companies?tab=review", "companies?tab=accepted", "companies?tab=aside",
    "sources?tab=find", "sources?tab=direction", "sources?tab=learned",
    "outreach?tab=approve", "outreach?tab=waiting", "outreach?tab=nurture", "outreach?tab=talking",
    "outreach?tab=approve&team=1", "outreach?tab=waiting&team=1", "outreach?tab=nurture&team=1", "outreach?tab=talking&team=1",
    "qualification?tab=active", "qualification?tab=approval", "qualification?tab=qualification", "qualification?tab=closed",
    "qualification?tab=active&team=1", "qualification?tab=approval&team=1", "qualification?tab=qualification&team=1",
    "qualification?tab=closed&team=1",
    "agents", "agents?tab=controls", "agents?tab=work", "agents?tab=runs", "agents?tab=permissions", "agents?tab=lessons",
    "agents?tab=scorecard",
    "agents?agent=research", "agents?agent=outreach", "agents?agent=evidence", "agents?agent=manager",
    "agents?agent=discovery", "agents?agent=direction",
]
# Later epochs: the pages the story passes through (items of the story are followed from them).
STORY_ROUTES = [
    "home", "companies?tab=new", "companies?tab=research", "companies?tab=review", "companies?tab=accepted",
    "outreach?tab=approve", "outreach?tab=waiting", "outreach?tab=talking",
    "qualification?tab=active", "qualification?tab=approval", "qualification?tab=qualification", "qualification?tab=qualification&team=1",
    "agents", "agents?tab=work", "agents?agent=research", "agents?agent=outreach", "agents?agent=evidence",
]
FOLLOW_PAGES = ("companies", "outreach", "qualification", "agents", "sources")
MAX_VIEWS = 60         # the first epoch
MAX_STORY_VIEWS = 26   # each later epoch: the story's pages and items


def norm_key(path: str) -> str:
    """The same key the static site computes (site/demo-api.js normKey): decoded path and query, /updates?since=*."""
    p, _, q = path.partition("?")
    p = unquote(p)
    parts = [unquote(x.replace("+", " ")) for x in q.split("&") if x] if q else []
    if p == "/updates":
        parts = ["since=*" if x.startswith("since=") else x for x in parts]
    return p + ("?" + "&".join(parts) if parts else "")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Frozen:
    """Stops time for the undo windows (api/undo.py) while an epoch is crawled, so an open window stays open."""

    def __init__(self):
        self.at: datetime | None = None

    def now(self) -> datetime:
        return self.at or datetime.now(timezone.utc)


class Recorder:
    def __init__(self):
        self.blobs: dict[str, object] = {}
        self.epochs: list[dict] = []
        self.transitions: list[dict] = []
        self.epoch = -1
        self.inflight = 0
        self.last_activity = time.monotonic()
        self.req_epoch: dict = {}
        self.crawling = False
        self.warnings: list[str] = []

    # ----- storage -----
    def blob(self, body) -> str:
        text = json.dumps(body, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        self.blobs.setdefault(h, body)
        return h

    def begin(self, label: str) -> int:
        self.epoch += 1
        self.epochs.append({"id": self.epoch, "label": label, "t": int(time.time() * 1000), "users": {}})
        print(f"[epoch {self.epoch}] {label}", flush=True)
        return self.epoch

    def keep(self, epoch: int, user: str, key: str, status: int, body):
        self.epochs[epoch]["users"].setdefault(user or "", {})[key] = [status, self.blob(body)]

    # ----- Playwright events -----
    def on_request(self, req):
        if "/api/" not in req.url:
            return
        self.inflight += 1
        self.last_activity = time.monotonic()
        if req.method != "GET":
            if self.crawling:
                self.warnings.append(f"write during crawl: {req.method} {req.url}")
            path = req.url.split("/api", 1)[1]
            try:
                body = req.post_data_json
            except Exception:
                body = req.post_data
            t = {"from": self.epoch, "to": None, "method": req.method, "key": norm_key(path), "user": req.headers.get("x-user", ""),
                 "body": body, "status": None, "res": None, "kind": "ui", "auto": None, "label": ""}
            self.transitions.append(t)
            self.req_epoch[req] = t
            t["to"] = self.begin(f"{req.method} {norm_key(path)}")
        else:
            self.req_epoch[req] = self.epoch

    def on_done(self, req):
        if "/api/" not in req.url:
            return
        self.inflight = max(0, self.inflight - 1)
        self.last_activity = time.monotonic()
        tag = self.req_epoch.pop(req, None)
        try:
            res = req.response()
        except Exception:
            res = None
        if res is None:
            return
        try:
            body = res.json()
        except Exception:
            body = None
        if isinstance(tag, dict):
            tag["status"], tag["res"] = res.status, self.blob(body)
        elif tag is not None:
            path = req.url.split("/api", 1)[1]
            self.keep(tag, req.headers.get("x-user", ""), norm_key(path), res.status, body)

    def on_failed(self, req):
        if "/api/" in req.url:
            self.inflight = max(0, self.inflight - 1)
            self.req_epoch.pop(req, None)

    def settle(self, page, quiet=0.25, limit=3.0):
        """Wait until no API request is in flight for `quiet` seconds."""
        end = time.monotonic() + limit
        while time.monotonic() < end:
            page.wait_for_timeout(60)
            if self.inflight == 0 and time.monotonic() - self.last_activity > quiet:
                return
        self.warnings.append(f"settle timeout in epoch {self.epoch} at {page.url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "runtime" / "static-demo" / "recording.json"))
    ap.add_argument("--quick", action="store_true", help="crawl only the seed pages (for a fast check of the story)")
    args = ap.parse_args()

    from offline_guard import install
    install()  # no network from the backend; loopback stays open for the browser
    work = Path(tempfile.mkdtemp(prefix="cgs-static-demo-"))
    (work / "no-knowledge").mkdir()
    port = free_port()
    os.environ.update({
        "APP_MODE": "demo", "AGENT_WORKER": "off", "CRM_MODE": "write_approved", "CRM_PROVIDER": "mock", "MAIL_PROVIDER": "mock",
        "CGS_V2_PORT": str(port), "CGS_V2_DB_PATH": str(work / "workspace.sqlite3"), "PYTHON_DOTENV_DISABLED": "1",
        "CGS_USERS": f"{ALEX},{SARAH},Daniel Brooks,Quinn Harper", "KNOWLEDGE_DIR": str(work / "no-knowledge"),
        "KNOWLEDGE_EMBEDDINGS": "off", "AI_PROVIDER": "gemini",
    })

    from api import undo as U
    frozen = Frozen()
    U.utcnow = frozen.now
    import api.main as M
    import uvicorn
    import httpx
    from playwright.sync_api import sync_playwright

    app = M.app
    app.state.clock = U.Clock(app, tick=0.5).start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/api/health", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    http = httpx.Client(base_url=base, timeout=60)

    rec = Recorder()
    rec.begin("start")

    def auto(label: str, ms: int, method: str, path: str, user=ALEX, body=None, working="", agent=""):
        """A change the backend makes on its own; the static site replays it after `ms`."""
        t = {"from": rec.epoch, "to": None, "method": method, "key": norm_key(path), "user": user, "body": body, "status": None,
             "res": None, "kind": "auto", "auto": ms, "label": label, "agent": agent, "working": working}
        rec.transitions.append(t)
        t["to"] = rec.begin(label)
        if method:
            r = http.request(method, f"/api{path}", headers={"X-User": user}, json=body)
            t["status"], t["res"] = r.status_code, rec.blob(r.json() if r.content else None)
            if r.status_code >= 400:
                raise SystemExit(f"{method} {path} failed: {r.status_code} {r.text}")
        return t

    def ui_http(label: str, method: str, path: str, body=None, user=ALEX, auto=None, **extra):
        """A click made through the API (its button may be gone from the page by now); recorded like a click."""
        t = {"from": rec.epoch, "to": None, "method": method, "key": norm_key(path), "user": user, "body": body, "status": None,
             "res": None, "kind": "ui", "auto": auto, "label": label, **extra}
        rec.transitions.append(t)
        t["to"] = rec.begin(label)
        r = http.request(method, f"/api{path}", headers={"X-User": user}, json=body)
        t["status"], t["res"] = r.status_code, rec.blob(r.json() if r.content else None)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} failed: {r.status_code} {r.text}")
        return t

    def held(opp_id: str) -> bool:
        from api import models as mm
        with app.state.db.session() as s:
            return U.held(s.get(mm.Opportunity, opp_id))

    def wait_for(check, what: str, seconds: float = 30):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if check():
                return
            time.sleep(0.25)
        raise SystemExit(f"Timed out waiting for {what}.")

    def last_ui(label: str, auto_ms=None, **extra):
        """Name the last transition a click made; auto_ms: the site also makes it on its own after that long."""
        t = next(x for x in reversed(rec.transitions) if x["kind"] == "ui")
        t["label"], t["auto"] = label, auto_ms
        t.update(extra)
        return t

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base) else r.abort())
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for pg in (page,):
            pg.on("request", rec.on_request)
            pg.on("requestfinished", rec.on_done)
            pg.on("requestfailed", rec.on_failed)

        def go(route: str):
            page.evaluate(f"location.hash = {('#/' + route)!r}")
            page.wait_for_selector("main .page-head h1", timeout=15000)
            rec.settle(page)

        def as_user(name: str):
            page.evaluate(f"localStorage.setItem('cgs.person', {name!r}); location.hash = '#/home'")
            page.reload()  # a goto that changes only the hash would not start the page again
            page.wait_for_selector("main .page-head h1", timeout=15000)
            rec.settle(page)

        def crawl(users=(ALEX,)):
            """Open pages as each person; the answers join this epoch. The first epoch opens every page and every item
            linked from them, so a visitor can look around before starting; later epochs open the pages of the story
            and its items (Corvane, Meridian). The static site answers other requests from an earlier epoch."""
            rec.crawling = True
            kept = frozen.at  # an undo window that is open stays open while the epoch is crawled
            frozen.at = kept or datetime.now(timezone.utc)
            full = rec.epoch == 0  # the first epoch opens every main page, later ones the story's pages
            start = page.evaluate("localStorage.getItem('cgs.person')")
            for user in users:
                as_user(user)
                seen, queue = set(), list(SEED_ROUTES if full else STORY_ROUTES)
                while queue and len(seen) < (MAX_VIEWS if full else MAX_STORY_VIEWS):
                    route = queue.pop(0)
                    if route in seen:
                        continue
                    seen.add(route)
                    try:
                        go(route)
                    except Exception as e:
                        rec.warnings.append(f"epoch {rec.epoch} {user} {route}: {e}")
                        continue
                    if args.quick:
                        continue
                    for h in page.eval_on_selector_all("main a[href^='#/']", "els => els.map(a => a.getAttribute('href'))"):
                        r = h[2:]
                        if r.split("?")[0] in FOLLOW_PAGES and "?" in r and r not in seen and any(i in r for i in story_ids):
                            queue.append(r)
                if full:  # the agent dock's panel
                    page.click("[data-shell=dock]") if page.query_selector("[data-shell=dock]") else None
                    rec.settle(page)
            as_user(start or ALEX)
            frozen.at = kept
            rec.crawling = False
            print(f"    crawled {len(seen)} views; {sum(len(v) for v in rec.epochs[rec.epoch]['users'].values())} answers", flush=True)

        story_ids = {"meridian", "corvane"}

        # ---------- the story ----------
        page.goto(f"{base}/")
        page.click(f"[data-shell=choose][data-name='{ALEX}']")
        page.wait_for_selector("main .page-head h1")
        rec.settle(page)
        crawl()

        # Step 1-3: the discovery agent's candidate Corvane Industrial; the research agent writes the brief
        go("companies?tab=new")
        cand = page.locator(".cand", has_text="Corvane Industrial").first
        cand.locator("[data-act=researchOne]").click()
        page.wait_for_selector(".toast:has-text('Corvane')")
        rec.settle(page)
        pick = last_ui("Research Corvane Industrial", animate="research")
        pick["created"] = (rec.blobs[pick["res"]] or {}).get("opportunities", [])
        crawl()
        corvane = pick["created"][0]
        story_ids.update(pick["created"])

        # Step 4: accept the brief (undo it once, so the site has the undo answer), then accept again
        def accept():
            go(f"companies?tab=review&id={corvane}")
            page.wait_for_selector("#brief-h:has-text('Corvane')")
            page.click("[data-act=mode][data-mode=accept]")
            page.check("form[data-act=acceptForm] input[name=duplicates_checked]")
            page.click("form[data-act=acceptForm] [type=submit]")
            page.wait_for_selector(".toast:has-text('Accepted')")
            rec.settle(page)
        frozen.at = datetime.now(timezone.utc)  # the undo window stays open while this epoch is crawled
        accept()
        last_ui("Accept Corvane Industrial", undoable=True)
        crawl()
        ui_http("Undo the accept", "POST", f"/opportunities/{corvane}/step4/undo", {}, undo=True)
        crawl()
        frozen.at = datetime.now(timezone.utc)
        accept()
        last_ui("Accept Corvane Industrial", undoable=True)
        crawl()
        auto("The undo window ends", 10000, "", "")
        frozen.at = None
        wait_for(lambda: not held(corvane), "the undo window to end")
        crawl()

        # the outreach agent drafts the first email
        auto("The outreach agent drafts the first email", 4500, "POST", "/agents/run?limit=20", agent="Outreach agent",
             working="Writing the first email to Corvane Industrial")
        crawl()

        # Step 5: approve and send it (mock mailbox); it waits 10 seconds, then goes out
        go("outreach?tab=approve")
        page.click(".pick:has-text('Corvane')")
        page.wait_for_selector("#detail-h:has-text('First email')")
        rec.settle(page)
        page.click("[data-act=mode][data-mode=send]")
        rec.settle(page)
        page.fill("form[data-act=sendForm] input[name=to]", CLIENT_EMAIL)
        frozen.at = datetime.now(timezone.utc)
        page.click("form[data-act=sendForm] button[value=send]")
        page.wait_for_selector(f".toast:has-text('goes to {CLIENT_EMAIL}')")
        rec.settle(page)
        send = last_ui("Approve and send the first email", undoable=True)
        crawl()
        from sqlalchemy import select
        from api import models as mm
        with app.state.db.session() as s:
            out_id = s.scalars(select(mm.OutboundEmail.id).where(mm.OutboundEmail.status == "queued")).one()
        ui_http("The email goes out", "POST", f"/outbound/{out_id}/send-now", {}, auto=10000)
        frozen.at = None
        crawl()

        # the client answers; the mail rules find the reply in the thread
        box = app.state.mail
        thread = next(t for t, msgs in box.threads.items() if any(x.get("to") == CLIENT_EMAIL for x in msgs))
        box.deliver_reply(thread, CLIENT_EMAIL, CLIENT_REPLY)
        auto("Elena Fischer replies", 9000, "POST", "/mail/sync", working="Checking the mailbox")
        crawl()

        # the person records the reply; the evidence agent reads it
        go("outreach?tab=waiting")
        page.click(".pick:has-text('Corvane')")
        page.wait_for_selector("[data-act=inboxAct][data-do=apply]")
        page.click("[data-act=inboxAct][data-do=apply]")
        page.wait_for_selector(".toast")
        rec.settle(page)
        last_ui("Record the reply")
        crawl()
        auto("The evidence agent reads the reply", 5000, "POST", "/agents/run?limit=20", agent="Evidence agent",
             working="Reading Elena Fischer's reply for qualification evidence")
        page.reload()
        page.wait_for_selector("main .page-head h1")
        rec.settle(page)
        crawl()

        # Step 6-7: close Meridian's two gaps, then send the pack for approval
        go("qualification?tab=active&id=meridian")
        page.wait_for_selector(".cond-list")
        page.click("summary[data-fold=scores]")
        page.click("[data-open='score:A']")
        page.select_option("form[data-act=reviewForm] select[name=value]", "4")
        page.fill("form[data-act=reviewForm] textarea[name=reason]", "The CFO approves spend and joins the next call.")
        page.click("form[data-act=reviewForm] [type=submit]")
        page.wait_for_selector(".notice:has-text('Saved')")
        rec.settle(page)
        last_ui("Confirm Meridian's authority score")
        crawl()
        go("qualification?tab=active&id=meridian")
        page.wait_for_selector(".cond-list")
        if not page.is_visible("[data-open='cond:commercial']"):
            page.click("summary[data-fold=scores]")
        page.click("[data-open='cond:commercial']")
        page.select_option("form[data-act=reviewForm] select[name=value]", "true")
        page.fill("form[data-act=reviewForm] textarea[name=reason]", "Budget approved and the CFO decides.")
        page.click("form[data-act=reviewForm] [type=submit]")
        page.wait_for_selector(".tag.good:has-text('Ready')")
        rec.settle(page)
        last_ui("Confirm Meridian's commercial condition")
        crawl()
        go("qualification?tab=active&id=meridian")
        page.wait_for_selector(".cond-list")
        page.click("[data-open=pack]")
        page.click("form[data-act=packForm] [type=submit]")
        page.wait_for_selector(".toast:has-text('sent for approval')")
        rec.settle(page)
        last_ui("Send Meridian's pack for approval")
        crawl((ALEX, SARAH))

        # Step 8: Sarah approves
        as_user(SARAH)
        go("qualification?tab=approval&id=meridian")
        try:
            page.wait_for_selector(".approval-card", timeout=8000)
        except Exception:
            page.screenshot(path=str(Path(args.out).with_suffix(".fail.png")), full_page=True)
            Path(args.out).with_suffix(".fail.txt").write_text(page.inner_text("main") + "\n" + page.url, encoding="utf-8")
            raise
        page.click("form[data-act=quickDecide] button[value=approve]")
        page.wait_for_selector(".toast:has-text('Qualified')")
        rec.settle(page)
        last_ui("Sarah approves: Meridian is qualified")
        crawl((SARAH, ALEX))

        browser.close()
        if errors:
            rec.warnings.extend(f"page error: {e}" for e in errors)

    server.should_exit = True
    app.state.clock.stop.set()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"recordedAt": datetime.now(timezone.utc).isoformat(), "start": rec.epochs[0]["t"], "users": [ALEX, SARAH],
            "epochs": rec.epochs, "transitions": rec.transitions, "blobs": rec.blobs, "warnings": rec.warnings}
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB): {len(rec.epochs)} epochs, {len(rec.transitions)} transitions, "
          f"{len(rec.blobs)} distinct answers.")
    for w in rec.warnings:
        print("warning:", w)


if __name__ == "__main__":
    main()
