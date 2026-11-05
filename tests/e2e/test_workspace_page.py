"""End-to-end check of the workspace page in a real browser (frontend phase F5).

Starts the v2 server on a fresh demo database (agents run only when asked, mock mail, no CRM), then clicks through
the whole workflow the way a consultant would: pick a candidate, accept it (undo that once, then accept again), let the outreach agent draft, approve
and send, record the reply, close the qualification gaps, send the pack, and approve it as someone else.

Skipped when Playwright or its Chromium is not installed (they are not in requirements.lock).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sync_api = pytest.importorskip("playwright.sync_api")
PAGE_ERRORS: list[str] = []  # uncaught script errors in the page


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = free_port()
    db = tmp_path_factory.mktemp("e2e") / "workspace.sqlite3"
    env = {**os.environ, "APP_MODE": "demo", "AGENT_WORKER": "off", "CRM_MODE": "disabled", "CRM_PROVIDER": "mock",
           "MAIL_PROVIDER": "mock", "CGS_V2_PORT": str(port), "CGS_V2_DB_PATH": str(db), "PYTHON_DOTENV_DISABLED": "1",
           "CGS_USERS": "Alex Morgan,Sarah Patel,Daniel Brooks,Quinn Harper"}
    proc = subprocess.Popen([sys.executable, "-B", "-m", "api.main"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{base}/api/health", timeout=1)
            break
        except OSError:
            if proc.poll() is not None:
                pytest.fail(f"The server stopped: {proc.stderr.read().decode()[-2000:]}")
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("The server did not start.")
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def page(server):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # Chromium not downloaded
            pytest.skip(f"Chromium is not installed for Playwright: {e}")
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        pg.route("**/fonts.googleapis.com/**", lambda r: r.abort())  # tests stay offline; the page falls back to system fonts
        pg.route("**/fonts.gstatic.com/**", lambda r: r.abort())
        pg.on("pageerror", lambda e: PAGE_ERRORS.append(str(e)))
        yield pg
        browser.close()


def go(page, base, route):
    page.goto(f"{base}/#/{route}")
    page.wait_for_selector("main .page-head h1")


def act_as(page, base, name):
    page.evaluate(f"localStorage.setItem('cgs.person', {name!r})")
    page.goto(f"{base}/?as={name.split()[0]}#/home")
    page.wait_for_selector("main .page-head h1")
    assert page.input_value("[data-shell=person]") == name


def test_whole_workflow_in_the_browser(server, page):
    base = server

    # who is acting
    page.goto(f"{base}/")
    page.click("[data-shell=choose][data-name='Alex Morgan']")
    page.wait_for_selector("main .page-head h1:has-text('Waiting for you')")

    # Step 1-3: research a candidate (demo research finishes at once)
    go(page, base, "companies?tab=new")
    first = page.locator(".cand").first
    company = first.locator("b").first.inner_text()
    first.locator("[data-act=researchOne]").click()
    page.wait_for_selector(f".toast:has-text('{company}')")

    # Step 4: accept it, undo that from the toast, then accept again and let the undo window pass
    def accept_it():
        go(page, base, "companies?tab=review")
        page.click(f".pick:has-text('{company}')")
        page.wait_for_selector(f"#brief-h:has-text('{company}')")
        page.click("[data-act=mode][data-mode=accept]")
        page.check("form[data-act=acceptForm] input[name=duplicates_checked]")
        page.click("form[data-act=acceptForm] [type=submit]")
        page.wait_for_selector(".toast:has-text('Accepted')")
    accept_it()
    page.click(".toast:has-text('Accepted') .toast-action")
    page.wait_for_selector(".toast:has-text('Undone')")
    accept_it()
    page.wait_for_timeout(11500)  # the clock ends the window; then the outreach agent may draft
    go(page, base, "companies?tab=accepted")
    page.wait_for_selector(f"table.list b:has-text('{company}')")

    # the outreach agent drafts the first email when the queue runs
    go(page, base, "agents?tab=team")
    page.click("[data-act=runQueue]")
    page.wait_for_selector(".notice:has-text('Ran')")

    # Step 5: approve and send the agent's draft (mock mailbox)
    go(page, base, "outreach?tab=approve")
    page.click(f".pick:has-text('{company}')")
    page.wait_for_selector("#detail-h:has-text('First email')")
    page.click("[data-act=mode][data-mode=send]")
    page.fill("form[data-act=sendForm] input[name=to]", "contact@e2e-test.example")
    page.click("form[data-act=sendForm] button[value=send]")
    page.wait_for_selector(".toast:has-text('goes to contact@e2e-test.example')")  # it waits so it can be undone
    page.click(f".pick:has-text('{company}')")
    page.click("[data-act=sendNow]")
    page.wait_for_selector(".toast:has-text('Sent to contact@e2e-test.example')")

    # the reply makes the lead Active
    go(page, base, "outreach?tab=waiting")
    page.click(f".pick:has-text('{company}')")
    page.click("[data-act=mode][data-mode=reply]")
    page.fill("form[data-act=replyForm] textarea[name=text]", "Thanks. Our board wants outside help with this. Can we talk next week?")
    page.click("form[data-act=replyForm] [type=submit]")
    page.wait_for_selector(".toast:has-text('Reply recorded')")
    go(page, base, "outreach?tab=talking")
    page.wait_for_selector(f"table.list:has-text('{company}')")

    # Step 6-7: close Meridian's two gaps by hand, then send the pack
    go(page, base, "qualification?tab=active&id=meridian")
    page.click("[data-open='score:A']")
    page.select_option("form[data-act=reviewForm] select[name=value]", "4")
    page.fill("form[data-act=reviewForm] textarea[name=reason]", "The CFO approves spend and joins the next call.")
    page.click("form[data-act=reviewForm] [type=submit]")
    page.wait_for_selector(".notice:has-text('Saved')")
    page.click("[data-open='cond:commercial']")
    page.select_option("form[data-act=reviewForm] select[name=value]", "true")
    page.fill("form[data-act=reviewForm] textarea[name=reason]", "Budget approved and the CFO decides.")
    page.click("form[data-act=reviewForm] [type=submit]")
    page.wait_for_selector(".tag.good:has-text('Ready')")
    page.click("[data-open=pack]")
    page.click("form[data-act=packForm] [type=submit]")
    page.wait_for_selector(".toast:has-text('sent for approval')")

    # Step 8: another person approves
    act_as(page, base, "Sarah Patel")
    go(page, base, "qualification?tab=approval&id=meridian")
    page.wait_for_selector(".approval-card:has-text('Alex Morgan asks for your approval')")
    page.click("form[data-act=quickDecide] button[value=approve]")
    page.wait_for_selector(".toast:has-text('Qualified')")
    go(page, base, "qualification?tab=qualification&team=1")  # the owner is Alex: show the whole team
    page.wait_for_selector(".lead-row:has-text('Meridian Industries')")

    assert PAGE_ERRORS == [], PAGE_ERRORS
