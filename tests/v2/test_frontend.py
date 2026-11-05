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
    """To approve = new inbox drafts and replies, plus accepted leads with no first-email draft waiting."""
    out = run_module_script(tmp_path, """
import { approveItems } from "./pages/outreach.js";
const opps = [{ id: "a", stage: "identified" }, { id: "b", stage: "identified" }, { id: "c", stage: "contacted" }];
const inbox = [
  { id: "1", opportunityId: "a", kind: "draft", status: "new", data: { purpose: "first" } },
  { id: "2", opportunityId: "c", kind: "draft", status: "new", data: { purpose: "followup" } },
  { id: "3", opportunityId: "c", kind: "reply", status: "new", data: {} },
  { id: "4", opportunityId: "b", kind: "draft", status: "dismissed", data: { purpose: "first" } },
  { id: "5", opportunityId: "zzz", kind: "draft", status: "new", data: { purpose: "first" } },
  { id: "6", opportunityId: "a", kind: "facts", status: "new", data: {} },
];
console.log(approveItems(opps, inbox).map((x) => x.key).join(","));
""")
    assert out == "item-1,item-2,item-3,opp-b"


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
