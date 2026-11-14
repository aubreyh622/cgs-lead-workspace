"""Frontend phase F1 checks that need no browser: the page and its modules are served, the choices the page
offers match what the backend accepts, and a write is saved before its response arrives."""
import re

import pytest
from fastapi.testclient import TestClient

from api import actions
from api import services as S
from api.main import create_app
from api.settings import ROOT, Settings

APP = ROOT / "frontend" / "app"
USER = {"X-User": "Alex Morgan"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(db_path=tmp_path / "t.sqlite3")))


def js_list(name: str) -> list[str]:
    """Read `export const NAME = [ "a", "b" ];` from frontend/app/options.js."""
    text = (APP / "options.js").read_text(encoding="utf-8")
    m = re.search(rf"export const {name} = \[(.*?)\];", text, re.S)
    assert m, f"{name} not found in options.js"
    return re.findall(r'"([^"]*)"', m.group(1))


def test_page_and_modules_are_served_without_caching(client):
    page = client.get("/")
    assert page.status_code == 200 and "/app/main.js" in page.text and "CGS Lead Workspace" in page.text
    assert page.headers["cache-control"] == "no-cache"
    for path in ("/app/main.js", "/app/styles.css", "/app/pages/companies.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache"


def test_every_module_import_exists():
    for js in APP.rglob("*.js"):
        for target in re.findall(r'from "(\.[^"]+)"', js.read_text(encoding="utf-8")):
            assert (js.parent / target).resolve().exists(), f"{js.name} imports missing {target}"


def test_reason_lists_match_the_backend():
    assert js_list("SKIP_REASONS") == list(S.SKIP_REASONS)
    assert js_list("REJECT_REASONS") == list(S.REJECT_REASONS)
    assert js_list("ACCEPT_REASONS") == list(S.ACCEPT_REASONS)
    scopes = re.findall(r'\["(\w+)",', re.search(r"export const SCOPES = \[(.*?)\];", (APP / "options.js").read_text(encoding="utf-8"), re.S).group(1))
    assert scopes and set(scopes) <= set(S.SCOPES)


def test_colours_only_come_from_tokens():
    """DESIGN.md: every colour goes through a token in :root; no colour literal anywhere else."""
    css = (APP / "styles.css").read_text(encoding="utf-8")
    root = re.search(r":root \{.*?\n\}", css, re.S).group(0)
    rest = css.replace(root, "")
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", rest)
    for js in APP.rglob("*.js"):
        assert not re.findall(r"#[0-9a-fA-F]{6}\b", js.read_text(encoding="utf-8")), js.name


def test_every_rail_link_has_a_page_and_old_routes_still_open(tmp_path):
    """The redesign's side rail: each link opens a page main.js knows; #/home and #/sources open their new pages."""
    main = (APP / "main.js").read_text(encoding="utf-8")
    pages = set(re.findall(r"^\s+(\w+)(?::\s*\w+)?,$", re.search(r"const PAGES = \{(.*?)\};", main, re.S).group(1), re.M))
    out = run_module_script(tmp_path, """
globalThis.window = { innerWidth: 1440 };
globalThis.location = { hash: "#/home?x=1", href: "http://127.0.0.1:8800/" };
const { NAV } = await import("./shell.js");
const { readRoute } = await import("./lib.js");
const { STAGE_LABELS } = await import("./options.js");
const r = readRoute();
location.hash = "#/sources";
console.log(JSON.stringify({ nav: NAV.flatMap((g) => g.items.map((i) => i[0])), home: r.page, x: r.params.x, sources: readRoute().page,
  nurture: STAGE_LABELS.nurture, review: STAGE_LABELS.review }));
""")
    import json
    got = json.loads(out)
    assert set(got["nav"]) | {"settings"} <= pages
    assert (got["home"], got["x"], got["sources"]) == ("overview", "1", "find")
    assert (got["nurture"], got["review"]) == ("Parked", "Your decision")


def test_companies_shows_no_value_band_and_keeps_old_tab_links():
    """R2: no revenue tier or amount on the brief review (rule 6); old tab names in links still open a stage."""
    js = (APP / "pages" / "companies.js").read_text(encoding="utf-8")
    assert "Revenue tier" not in js and "p.tier" not in js
    aliases = re.search(r"const TAB_ALIAS = \{(.*?)\};", js).group(1)
    assert all(f"{old}:" in aliases for old in ("new", "review", "accepted"))
    stages = re.findall(r'^\s+\["(\w+)", "[^"]+", "\w+", "[^"]+"\],$', re.search(r"const STAGES = \[(.*?)\];", js, re.S).group(1), re.M)
    assert stages == ["found", "research", "decision", "identified", "aside"]


def test_api_data_is_escaped_before_it_reaches_the_page():
    """Pages build HTML only through the html`` template, which escapes every value."""
    lib = (APP / "lib.js").read_text(encoding="utf-8")
    assert '"<": "&lt;"' in lib and "&quot;" in lib and "&#39;" in lib
    for js in (APP / "pages").glob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert "innerHTML" not in text, f"{js.name} writes innerHTML directly"
        assert "raw(" not in text, f"{js.name} bypasses escaping"


def test_writes_are_committed_before_the_response(client):
    """A read straight after a write must see it (the page reloads its lists right after each action)."""
    assert actions.Actor.scope == "function"
    cand = client.get("/api/candidates?status=new").json()[0]["id"]
    assert client.post(f"/api/candidates/{cand}/skip", json={"reason": "Too small"}, headers=USER).status_code == 200
    assert cand not in {c["id"] for c in client.get("/api/candidates?status=new").json()}


def test_step4_refuses_a_stale_revision(client):
    """The page sends the revision the person read; a change by someone else in between is refused, not overwritten."""
    o = client.get("/api/opportunities/ostrander").json()
    body = {"decision": "return", "questions": "Who leads automation now?", "revision": o["revision"] - 1}
    r = client.post("/api/opportunities/ostrander/step4", json=body, headers=USER)
    assert r.status_code == 409 and r.json()["code"] == "revision_conflict"


def test_modules_parse(tmp_path):
    """Syntax check every module with Node when it is installed. Copied to .mjs: node --check on a .js file
    outside an ES-module package does not reliably report module syntax errors."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    for js in APP.rglob("*.js"):
        copy = tmp_path / (js.relative_to(APP).as_posix().replace("/", "__")[:-3] + ".mjs")
        copy.write_text(js.read_text(encoding="utf-8"), encoding="utf-8")
        r = subprocess.run([node, "--check", str(copy)], capture_output=True, text=True)
        assert r.returncode == 0, f"{js.name}: {r.stderr}"


def run_module_script(tmp_path, script: str) -> str:
    """Run a small ES-module script against a copy of frontend/app (package type module), with Node."""
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    app = tmp_path / "app"
    shutil.copytree(APP, app)
    (app / "package.json").write_text(json.dumps({"type": "module"}), encoding="utf-8")
    (app / "check.js").write_text(script, encoding="utf-8")
    r = subprocess.run([node, str(app / "check.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_outreach_queue_takes_agent_drafts_and_leads_without_one(tmp_path):
    """To approve = new inbox drafts, plus accepted leads with no first-email draft waiting. A client reply waits on its
    lead in Waiting for a reply; the navigation count includes it."""
    out = run_module_script(tmp_path, """
import { approveItems, waitingFor } from "./pages/outreach.js";
const opps = [{ id: "a", stage: "identified" }, { id: "b", stage: "identified" }, { id: "c", stage: "contacted" }];
const inbox = [
  { id: "1", opportunityId: "a", kind: "draft", status: "new", data: { purpose: "first" } },
  { id: "2", opportunityId: "c", kind: "draft", status: "new", data: { purpose: "followup" } },
  { id: "3", opportunityId: "c", kind: "reply", status: "new", data: {} },
  { id: "4", opportunityId: "b", kind: "draft", status: "dismissed", data: { purpose: "first" } },
  { id: "5", opportunityId: "zzz", kind: "draft", status: "new", data: { purpose: "first" } },
  { id: "6", opportunityId: "a", kind: "facts", status: "new", data: {} },
];
console.log(approveItems(opps, inbox).map((x) => x.key).join(","), waitingFor(opps, inbox));
""")
    assert out == "item-1,item-2,opp-b 4"


def test_outreach_puts_each_lead_where_the_next_move_is(tmp_path):
    """R3: one item per lead. A reply comes first, then a second approval (the approver's move), then a draft; a plan
    step due later is the agent's; a sent lead with nothing to do waits on the client; a parked one is Parked."""
    out = run_module_script(tmp_path, """
import { leadItems } from "./pages/outreach.js";
const T = "2026-11-13";
const opps = ["r", "s", "d", "p", "w", "n", "f", "c"].map((id) => ({ id, owner: "Ann",
  stage: { w: "contacted", r: "contacted", p: "contacted", c: "contacted", n: "nurture" }[id] || "identified" }));
const inbox = [
  { id: "1", opportunityId: "r", kind: "reply", status: "new", data: {} },
  { id: "2", opportunityId: "r", kind: "draft", status: "new", data: { purpose: "followup" } },
  { id: "3", opportunityId: "d", kind: "draft", status: "new", data: { purpose: "first" } },
];
const step = (due) => ({ status: "approved", running: true, current: 2, plan: { steps: [{ n: 1, day: 0, status: "done" }, { n: 2, day: 7, status: "pending", due }] } });
const details = {
  s: { outreach: { waiting: { status: "approval", by: "Ann", approver: "Bo" } } },
  p: { outreach: { plan: step("2026-11-20") } },
  w: { outreach: { plan: step("2026-11-10") } },
};
console.log(leadItems(opps, inbox, details, T).map((x) => `${x.key}:${x.kind}:${x.who}`).join(","));
""")
    assert out == "r:reply:Ann,s:approve2:Bo,d:draft:Ann,p:agentstep:agent,w:step:Ann,n:parked:Ann,f:first:Ann,c:wait:client"


def test_html_template_escapes_values(tmp_path):
    out = run_module_script(tmp_path, """
import { html, safeUrl } from "./lib.js";
globalThis.location = { href: "http://127.0.0.1:8800/" };
const evil = '<img src=x onerror="alert(1)">';
console.log(String(html`<p title="${evil}">${evil}${[evil]}${html`<b>ok</b>`}</p>`));
console.log(safeUrl("javascript:alert(1)") === "" && safeUrl("https://example.com/a") === "https://example.com/a");
""")
    page, url_ok = out.splitlines()
    assert "<img" not in page and "&lt;img" in page and "&quot;" in page and "<b>ok</b>" in page
    assert url_ok == "true"


def test_step6_to_8_choices_match_the_backend():
    assert js_list("CLOSE_REASONS") == list(S.CLOSE_REASONS)
    assert js_list("APPROVER_ROLES") == list(S.APPROVER_ROLES)
    from api.actions import NoteIn
    assert js_list("NOTE_TYPES") == list(NoteIn.model_fields["type"].annotation.__args__)
    need = re.findall(r'\["(\w+)", "', re.search(r"export const NEED_FIELDS = \[(.*?)\];", (APP / "options.js").read_text(encoding="utf-8"), re.S).group(1))
    assert need == ["team", "process", "problem", "impact", "target", "service"]


def test_qualification_count_is_packs_to_decide_and_own_ready_leads(tmp_path):
    out = run_module_script(tmp_path, """
import { waitingFor } from "./pages/qualification.js";
const a = (readiness) => ({ readiness, gaps: [] });
const opps = [
  { id: "1", stage: "approval", owner: "Sarah", assessment: a("Ready") },
  { id: "2", stage: "approval", owner: "Alex", assessment: a("Ready") },
  { id: "3", stage: "active", owner: "Alex", assessment: a("Ready") },
  { id: "4", stage: "active", owner: "Alex", assessment: a("More evidence needed") },
  { id: "5", stage: "active", owner: "Sarah", assessment: a("Ready") },
];
console.log(waitingFor("Alex", opps).map((o) => o.id).join(","));
""")
    assert out == "1,3"


def test_qualification_says_what_is_missing_in_words(tmp_path):
    """R4, decision D3: what stands between a lead and Ready is said in words, never as a number. At six of six but
    not Ready (only the strength of the evidence is short) one sentence names the weakest dimensions."""
    out = run_module_script(tmp_path, """
import { readiness } from "./pages/qualification.js";
const R = { conditions: { problem: "Specific client problem", fit: "CGS service fit" }, floors: { B: 2, A: 2 },
  labels: { B: "Budget", A: "Authority", N: "Need", T: "Timeline", H: "History", E: "Engagement" },
  weights: { B: 25, A: 20, N: 20, T: 15, H: 5, E: 15 } };
const all = { problem: true, fit: true };
const one = readiness({ conditions: { problem: true, fit: null }, scores: { B: 1 }, assessment: { readiness: "More evidence needed",
  gaps: [{ key: "B", label: "Budget: below minimum" }, { key: "A", label: "Authority: pending confirmation" }, { key: "condition:fit", label: "x" }] } }, R);
const weak = readiness({ conditions: all, scores: { B: 3, A: 3, N: 2, T: 4, H: 5, E: 4 }, assessment: { readiness: "More evidence needed", gaps: [{ key: "Q", label: "Confirmed Q is below 70" }] } }, R);
const ready = readiness({ conditions: all, scores: {}, assessment: { readiness: "Ready", gaps: [] } }, R);
console.log(JSON.stringify([one.head, one.dims.map((x) => x.text), one.excLabel, weak.head, weak.weakText, weak.excLabel, ready.head, ready.count]));
""")
    import json
    one_head, dims, exc, weak_head, weak_text, weak_exc, ready_head, ready_count = json.loads(out)
    assert one_head == "1 of 2 conditions met. Missing: cgs service fit."
    assert dims == ["Budget: not strong enough yet", "Authority: not confirmed yet"]
    assert exc == "Ask for approval with 3 things missing"
    assert weak_head == "2 of 2 conditions met. The evidence is not strong enough yet for approval."
    assert weak_text == "The evidence is not strong enough yet for approval. Confirm more about need, budget or authority."
    assert weak_exc == "Ask for approval with 1 thing missing"
    assert (ready_head, ready_count) == ("2 of 2 conditions met. Ready for approval.", 0)
    assert not re.search(r"\d", weak_text + "".join(dims))


def test_inbox_tabs_and_quick_actions_follow_the_todo_rows(tmp_path):
    """R5: the Inbox tabs filter GET /api/todo's rows (All is every row, so the rail badge equals it), and a quick
    action finds its record from the row key that api/todo.py builds."""
    todo = (ROOT / "api" / "todo.py").read_text(encoding="utf-8")
    for key in ('key=f"approve:{e.id}"', 'key=f"proposal:{p.id}"', 'key=f"lesson:{l.id}"'):
        assert key in todo, f"api/todo.py no longer builds {key}; the Inbox reads record ids from it"
    out = run_module_script(tmp_path, """
import { typeOf, recordOf } from "./pages/inbox.js";
const rows = [
  { key: "approve:41", label: "Approve email", group: "waiting" },
  { key: "approve:acme", label: "Approve or return", group: "waiting" },
  { key: "review:acme", label: "Accept or reject", group: "decide" },
  { key: "proposal:p2", label: "Direction change", group: "steer" },
  { key: "lesson:7", label: "Lesson to review", group: "steer" },
  { key: "inbox:i9", label: "New facts", group: "check", ref: { type: "inbox", id: "i9", hash: "h" } },
  { key: "email:3", label: "Email not sent", group: "problem", ref: { type: "email", id: 3 } },
  { key: "overdue:acme", label: "Overdue", group: "waiting" },
];
console.log(rows.map((r) => `${typeOf(r)}/${(recordOf(r) || {}).type || "-"}`).join(","));
""")
    assert out == ("approval/outbound,approval/-,decision/-,proposal/direction,proposal/lesson,proposal/inbox,problem/email,other/-")


def test_overview_pipeline_counts_the_lists_it_links_to(tmp_path):
    """R5: each pipeline row counts the stage list its link opens, for the whole team."""
    out = run_module_script(tmp_path, """
import { pipeline } from "./pages/home.js";
const opps = ["researching", "review", "identified", "contacted", "active", "active", "approval", "qualification", "nurture", "rejected"].map((stage, i) => ({ id: String(i), stage }));
console.log(JSON.stringify(pipeline(opps, [{ id: "c" }]).map(([label, n, , link]) => [label, n, link])));
""")
    import json
    rows = json.loads(out)
    assert [r[1] for r in rows] == [1, 1, 1, 2, 2, 1, 1, 1]
    assert rows[3][2] == "#/companies?tab=identified" and rows[4][2] == "#/qualification?tab=active&team=1"


def test_agent_hub_stage_metric_counts_what_the_scorecard_says(tmp_path):
    """R6: each stage card counts the scorecard's results people used (or, for the Evidence agent, the proposals they
    confirmed); the old Agents tabs that moved to Connectors still open there."""
    out = run_module_script(tmp_path, """
import { stageMetric } from "./pages/team.js";
const sc = { work: [{ agent: "Discovery agent", used: 2, judged: 5 }, { agent: "Direction agent", used: 1, judged: 1 }, { agent: "Research agent", used: 3, judged: 4 }],
  evidence: { totals: { decided: 6, accepted: 4 } } };
console.log(JSON.stringify([stageMetric(["discovery", "direction"], sc), stageMetric(["evidence"], sc), stageMetric(["research"], { error: "x" })]));
""")
    import json
    find, evidence, broken = json.loads(out)
    assert (find["n"], find["of"]) == (3, 6) and (evidence["n"], evidence["of"]) == (4, 6) and broken is None
    agents = (APP / "pages" / "agents.js").read_text(encoding="utf-8")
    assert 'params.tab === "permissions"' in agents and 'ctx.go("connectors"' in agents


def test_qualification_never_shows_a_score_out_of_100_or_a_revenue_amount():
    """R4, decisions D3 and D5: no 0-100 qualification score and no revenue amount on the page. The revenue form
    starts empty, so a saved amount is not shown again."""
    js = (APP / "pages" / "qualification.js").read_text(encoding="utf-8")
    assert "of 100" not in js and "toLocaleString" not in js and "revenueText" not in js
    assert "o.revenue.low" not in js and "o.revenue.high" not in js
    form = re.search(r'name="low"[^>]*>', js).group(0)
    assert "value=" not in form


def test_company_record_decides_nothing_and_shows_no_amount(tmp_path):
    """R7, decision D4: the record reads one opportunity and makes no decisions (no writes, only tab actions); it never
    shows the revenue amount; it opens on the tab for the stage; search opens it for companies already in the workflow."""
    js = (APP / "pages" / "record.js").read_text(encoding="utf-8")
    assert not re.search(r"api\.(post|put|patch|delete)\(", js)
    assert set(re.findall(r"^  (\w+)\(t, e, ctx\)", js, re.M)) == {"tab", "tabkeys"}
    assert 'href("record", { id: o.id })' in (APP / "search.js").read_text(encoding="utf-8")
    out = run_module_script(tmp_path, """
globalThis.window = { innerWidth: 1440 };
globalThis.location = { hash: "#/record?id=x", href: "http://127.0.0.1:8800/" };
const record = await import("./pages/record.js");
const o = { id: "x", stage: "approval", owner: "Sam Reyes", rating: 3, title: "T", company: { name: "Acme", industry: "", hq: "", domain: "" },
  contact: { name: "", title: "" }, next: { action: "", due: null }, decisions: [], activity: [{ actor: "h", on: "2026-10-01", text: "Revenue set to $500,000 to $700,000" }],
  notes: [], evidence: [], packs: [], proposals: [], brief: null, outreach: null, revenue: { low: 500000, high: 700000 }, crm: null };
const tabs = {};
const replace = (page, p) => { tabs.went = p.tab; };
for (const stage of ["review", "identified", "active", "nurture"]) {
  await record.load({ api: { get: async (u) => (u === "/rules" ? null : { ...o, stage }) }, replace }, { id: "x" });
  tabs[stage] = tabs.went;
}
const page = String(record.view({ o, tab: "activity" }, { me: "Sam Reyes", today: "2026-11-14" }));
console.log(JSON.stringify({ tabs, amount: /500,000|700,000|500000/.test(page), masked: page.includes("[amount]") }));
""")
    import json
    got = json.loads(out)
    assert got["tabs"]["review"] == "brief" and got["tabs"]["identified"] == "outreach"
    assert got["tabs"]["active"] == "qual" and got["tabs"]["nurture"] == "activity"
    assert not got["amount"] and got["masked"]


def test_problem_banners_show_on_the_pages_whose_work_stops(tmp_path):
    """R8: the AI limit, Gmail signed out and stopped research each show on the pages they stop, from GET /api/todo;
    a paused research is not a failure; only the AI-limit banner can be put off, for the day it was put off."""
    out = run_module_script(tmp_path, """
globalThis.window = { innerWidth: 1440 };
const { problems } = await import("./shell.js");
const health = [{ key: "budget" }, { key: "gmail" }];
const rows = [
  { label: "Research stopped", sub: "The website would not load.", opportunity: { id: "a1", company: "Acme" } },
  { label: "Research stopped", sub: "Paused: it reached its budget.", opportunity: { id: "b2", company: "Bolt" } },
];
const heads = (page, extra = {}) => [...String(problems({ page, health, rows, ai: { used: 60, budget: 60 }, today: "2026-11-14", ...extra }))
  .matchAll(/<h2 id="problem-([a-z]+)"[^>]*>([^<]*)</g)].map((m) => `${m[1]}: ${m[2].replace(/&#39;/g, "'")}`);
console.log(JSON.stringify({
  companies: heads("companies"), outreach: heads("outreach"), overview: heads("overview"),
  later: heads("companies", { dismissed: { budget: "2026-11-14" } }), yesterday: heads("companies", { dismissed: { budget: "2026-11-13" } }),
  healthy: String(problems({ page: "companies" })),
  gmailLater: String(problems({ page: "outreach", health, rows })).includes('data-key="gmail"'),
}));
""")
    import json
    got = json.loads(out)
    assert got["companies"] == ["budget: Today's AI requests are used up: 60 of 60", "research: Research stopped on 1 company"]
    assert got["outreach"] == ["budget: Today's AI requests are used up: 60 of 60", "gmail: Gmail is disconnected"]
    assert got["overview"] == [] and got["healthy"] == "" and not got["gmailLater"]
    assert got["later"] == ["research: Research stopped on 1 company"] and len(got["yesterday"]) == 2


def test_gmail_off_flags_outreach_rows_and_amounts_stay_hidden(tmp_path):
    """R8: with Gmail signed out a row whose next move is an email says it cannot go out, a sent one that replies are
    not read; activity lines never show a revenue amount in either form the backend writes (D5)."""
    js = (APP / "pages" / "outreach.js").read_text(encoding="utf-8")
    assert "Can't send, Gmail off" in js and "Replies not read" in js and "statusTag(x, ctx, d.mail)" in js
    out = run_module_script(tmp_path, """
const { moneyless } = await import("./pages/qualification.js");
console.log(JSON.stringify([
  moneyless("CGS revenue reviewed by Alex Morgan: $600K → $500K–$700K"),
  moneyless("CGS revenue {'low': 500000, 'high': 700000} → {'low': 550000, 'high': 800000}"),
  moneyless("CGS revenue to estimate → {'low': 500000, 'high': 700000}"),
]));
""")
    import json
    for line in json.loads(out):
        assert not re.search(r"\d{3}", line), line


def test_stage_pages_share_the_stage_cards_and_show_no_dimension_numbers():
    """Companies, Outreach and Qualification all open with the design's stage cards (Companies.dc.html isFunnel), and a
    dimension's value reads as the rules' words, never as a number (redesign rule 4, G26)."""
    for name in ("companies", "outreach", "qualification"):
        js = (APP / "pages" / f"{name}.js").read_text(encoding="utf-8")
        assert '<div class="stages" role="tablist"' in js and 'class="stg"' in js, name
    q = (APP / "pages" / "qualification.js").read_text(encoding="utf-8")
    assert "${v}: ${rules.anchors" not in q and "Dimensions (BANTHE)" not in q and "first-time neutral" not in q


def test_agents_page_shows_inbox_items_no_other_page_handles(tmp_path):
    """Drafts and replies belong to Outreach; the Agents page takes the rest, new ones only."""
    out = run_module_script(tmp_path, """
import { otherInbox } from "./pages/agents.js";
const items = [
  { id: "1", kind: "draft", status: "new" }, { id: "2", kind: "reply", status: "new" },
  { id: "3", kind: "facts", status: "new" }, { id: "4", kind: "message", status: "new" },
  { id: "5", kind: "contact", status: "applied" },
];
console.log(otherInbox(items).map((i) => i.id).join(","));
""")
    assert out == "3,4"


def test_one_paused_agent_shows_as_paused_and_crm_links_are_http_only(tmp_path):
    """B1: a person can pause one agent; the page shows Insightly links but never a test CRM's mock:// address."""
    out = run_module_script(tmp_path, """
import { AGENTS, agentState } from "./pages/team.js";
import { crmLinks } from "./shell.js";
globalThis.location = { href: "http://127.0.0.1:8800/" };  // safeUrl resolves against the page address, as in a browser
const d = { jobs: [{ agent: "Outreach agent", status: "queued", reason: "Follow up" }], inbox: [],
            direction: { proposals: [] }, lessons: [], overview: { paused: false, pausedAgents: ["Outreach agent"] } };
const out = AGENTS.find((a) => a.slug === "outreach"), res = AGENTS.find((a) => a.slug === "research");
console.log(agentState(out, d, "Alex Morgan").label + "|" + agentState(out, d, "Alex Morgan").now + "|" + agentState(res, d, "Alex Morgan").label);
const real = String(crmLinks({ provider: "insightly", leadUrl: "https://crm.na1.insightly.com/details/Lead/1", opportunityUrl: null }));
const mock = String(crmLinks({ provider: "mock", leadUrl: "mock://crm/Lead/1", opportunityUrl: "javascript:alert(1)" }));
console.log([real.includes('href="https://crm.na1.insightly.com/details/Lead/1"'), real.includes("Lead in Insightly"),
             real.includes("Opportunity"), mock.includes("href")].join(","));
""")
    states, links = out.splitlines()
    assert states == "Paused|1 job waiting until a person resumes it|Idle"
    assert links == "true,true,false,false"


def test_try_it_shows_the_brief_with_safe_links(tmp_path):
    """B2: a finished trial shows the brief; a source that is not http(s) is shown as text, never as a link."""
    out = run_module_script(tmp_path, """
import { trialRow } from "./pages/team.js";
globalThis.location = { href: "http://127.0.0.1:8800/" };
const ctx = { today: "2026-11-05" };
const done = String(trialRow({ company: "Ardent Castings", domain: "ardentcastings.example", on: "2026-11-05", status: "completed", person: null,
  brief: { hypothesis: "Integration after an acquisition", flags: [], recommendation: { decision: "accept", reason: "Ready." },
           facts: [{ text: "Ardent buys Hollow Pine", src: "https://news.example/a", date: "2026-10-10" },
                   { text: "Evil", src: "javascript:alert(1)", date: "2026-10-11" }] } }, ctx));
const running = String(trialRow({ company: "X", domain: "x.example", on: "2026-11-05", status: "running", brief: null }, ctx));
console.log([done.includes('href="https://news.example/a"'), done.includes('href="javascript'), done.includes("Brief ready"),
             done.includes("Integration after an acquisition"), running.includes("Running"), running.includes("checks again")].join(","));
""")
    assert out == "true,false,true,true,true,true"
