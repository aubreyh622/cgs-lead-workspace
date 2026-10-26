"""P5 reproducible two-round functional rehearsal in an isolated offline workspace."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import threading
import time

from .demo_providers import COMPANIES, DIRECTION_SIGNAL, SyntheticResearchProvider, SyntheticFeedbackProvider
from .research_service import ResearchJobStore
from .workspace_config import WorkspaceConfig
from .workspace_runtime import WorkspaceStores, make_workspace_server
from backend.agents import Agents
from lead_agent.models import now
from server import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MARKER = "offline-demo.json"


class OfflineQualificationStore(Store):
    def __init__(self, path):
        # Even an inherited credential in the process cannot enable a live agent here.
        super().__init__(path, agent_factory=lambda mode: Agents("demo"))

    def bootstrap(self):
        data = super().bootstrap()
        data["config"].update(liveConfigured=False, model="")
        return data

    def config(self, body):
        if body.get("mode", "demo") != "demo":
            raise ValueError("Offline demo qualification cannot switch to live providers.")
        return super().config(body)


def demo_server(root, port=8767, trace=None):
    trace = trace if trace is not None else []
    # Explicit mapping prevents .env loading and ignores ambient credential configuration.
    config = WorkspaceConfig.load(Path(root), {"CGS_PORT": "8767"})
    providers = {
        "research": {"configured": True, "simulated": True, "model": "offline-synthetic-v1"},
        "qualification": {"configured": False, "simulated": True, "model": ""},
        "orchestrator": {"implemented": True, "configured": True, "simulated": True, "model": "offline-synthetic-v1"},
    }
    config = replace(config, providers=providers)
    stores = WorkspaceStores(ResearchJobStore(config.research_db), OfflineQualificationStore(config.qualification_db))
    try:
        server = make_workspace_server(replace(config, port=port), stores,
            research_provider_factory=lambda model: SyntheticResearchProvider(model, trace),
            manager_provider_factory=lambda model: SyntheticFeedbackProvider(), demo_companies=COMPANIES)
    except Exception:
        stores.close()
        raise
    return server, stores


def close_demo(server, stores):
    server.orchestrator_service.shutdown()
    server.research_service.shutdown()
    server.server_close()
    stores.close()


@contextmanager
def running_demo(root, trace):
    server, stores = demo_server(root, port=0, trace=trace)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield PilotClient(server.server_port)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        close_demo(server, stores)


class PilotClient:
    def __init__(self, port):
        self.port, self.csrf = port, None
        self.csrf = self.request("GET", "/api/bootstrap")["csrf"]

    def request(self, method, path, body=None, *, expected=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            value = json.loads(response.read())
            if expected is not None:
                assert response.status == expected, (path, response.status, value)
            else:
                assert 200 <= response.status < 300, (path, response.status, value)
            return value.get("result", value)
        finally:
            connection.close()

    def wait(self, path):
        deadline = time.monotonic()+12
        while time.monotonic() < deadline:
            item = self.request("GET", path)
            if item["status"] not in ("pending", "running", "stopping"):
                assert item["status"] == "completed", item
                return item
            time.sleep(.02)
        raise AssertionError("Offline pilot task did not finish.")


def run_two_round_pilot(root):
    checks, rounds, trace, cases, receipts = [], [], [], [], []
    def check(key, condition, evidence):
        checks.append({"id": key, "passed": bool(condition), "evidence": evidence})
        assert condition, key

    def company_task(client, round_number, number, version):
        host = f"synthetic-factory-{number}.example"
        body = {"request_id": f"pilot-r{round_number}-c{number}", "consultant_id": "Demo consultant",
                "company": {"company": COMPANIES[host], "domain": host}, "theme_version": version}
        run = client.request("POST", "/api/research/runs", body)
        client.wait("/api/research/runs/"+run["run_id"])
        result = client.request("GET", "/api/research/runs/"+run["run_id"]+"/results")
        card = result["company_cards"][0]
        check(f"r{round_number}-c{number}-sources", card["mode"] == "synthetic" and not card["validation_issues"] and bool(card["sources"]),
              {"run_id":run["run_id"], "evidence_count":len(card["research"]["evidence"]), "source_urls":[s["url"] for s in card["sources"]]})
        replay = client.request("POST", "/api/research/runs", body)
        check(f"r{round_number}-c{number}-task-replay", replay["run_id"] == run["run_id"], {"run_id":run["run_id"]})
        item = client.request("POST", "/api/research/opportunities", {
            "request_id":f"pilot-op-r{round_number}-c{number}", "run_id":run["run_id"], "lead_id":card["lead_id"],
            "title":f"Round {round_number}: "+("Digital pilot coordination" if number == 1 else "Acquisition integration planning"),
            "team":"Synthetic strategy team" if round_number == 1 else "Synthetic operations integration team",
            "problem_hypothesis":card["research"]["problem_hypothesis"],
            "cgs_relevance_hypothesis":card["research"]["cgs_relevance"],
            "alternative_explanation":card["research"]["alternative_explanation"],
            "evidence_ids":[e["evidence_id"] for e in card["research"]["evidence"] if e["category"] in ("signal","problem")]})
        return {"company":COMPANIES[host], "run_id":run["run_id"], "theme_version":run["theme_version"],
                "opportunity_id":item["opportunity"]["opportunity_id"], "card":card, "record":item}

    def review(client, task, outcome, key, *, minutes=None):
        current = client.request("GET", "/api/research/opportunities/"+task["opportunity_id"])
        record = client.request("POST", "/api/research/opportunities/"+task["opportunity_id"]+"/feedback", {
            "request_id":key, "revision":current["revision"], "consultant_id":"Demo consultant", "scope":"theme",
            "outcome":outcome, "reason_code":"relevant_signal" if outcome == "worth_exploring" else "timing" if outcome == "defer" else "evidence_gap",
            "comment":"Scripted synthetic feedback: prioritize acquisition integration planning; external demand remains unknown.",
            "review_minutes":minutes, "source_support_checked":outcome == "worth_exploring"})
        task.update(record=record, outcome=outcome)
        return record

    def submit(client, task, key):
        return client.request("POST", "/api/research/opportunities/"+task["opportunity_id"]+"/handoff",
            {"request_id":key,"revision":task["record"]["revision"]})

    def accept(client, pending, key, relationship):
        receipt = pending["receipt"]
        body = {"request_id":key,"opportunity_revision":receipt["opportunity_revision"],"status":"accepted",
            "reviewer":"Demo receiving reviewer","owner_id":"Demo owner", "next_action":"Identify and verify an appropriate contact",
            "due_date":(date.today()+timedelta(days=3)).isoformat(), "relationship_checked":True,"relationship_note":relationship}
        result = client.request("POST", "/api/research/handoffs/"+receipt["handoff_id"]+"/decision",body)
        replay = client.request("POST", "/api/research/handoffs/"+receipt["handoff_id"]+"/decision",body)
        check(key+"-replay", result["receipt"]["case_id"] == replay["receipt"]["case_id"], result["receipt"])
        receipts.append(result["receipt"])
        return result["receipt"]["case_id"]

    with running_demo(root, trace) as client:
        bootstrap = client.request("GET","/api/bootstrap")
        check("explicit-offline-mode", bootstrap.get("offlineDemo") is True, {"offlineDemo":True})
        client.request("POST","/api/config",{"mode":"live"},expected=400)
        client.request("POST","/api/research/runs",{"request_id":"real-domain-blocked","consultant_id":"Demo consultant",
            "company":{"company":"Real company","domain":"example.com"}},expected=400)
        check("no-live-mode-or-real-domain",True,{"live_mode_status":400,"real_domain_status":400})
        base = client.request("GET","/api/research/theme")["brief"]["version"]
        first = [company_task(client,1,n,base) for n in (1,2)]
        review(client,first[0],"research_further","pilot-feedback-r1-c1",minutes=8)
        review(client,first[1],"worth_exploring","pilot-feedback-r1-c2")
        pending = submit(client,first[1],"pilot-handoff-r1")
        case_id = accept(client,pending,"pilot-accept-r1","Scripted check: no existing synthetic opportunity for this team.")
        case = next(c for c in client.request("GET","/api/bootstrap")["cases"] if c["id"] == case_id)
        check("unknown-contact-accepted", case["stage"] == "Not contacted" and case["contact"] is None, {"case_id":case_id,"stage":case["stage"]})
        case = client.request("POST",f"/api/cases/{case_id}/contact",{"revision":case["revision"],"contact":"Synthetic Person",
            "role":"Synthetic operations lead","source_rationale":"Scripted synthetic contact confirmation for demonstration only."})
        client.request("POST",f"/api/cases/{case_id}/outreach",{"revision":case["revision"]})
        case = next(c for c in client.request("GET","/api/bootstrap")["cases"] if c["id"] == case_id)
        check("draft-does-not-contact", case["stage"] == "Not contacted", {"case_id":case_id,"stage":case["stage"]})
        case = client.request("POST",f"/api/cases/{case_id}/contacted",{"revision":case["revision"],"contacted_on":date.today().isoformat(),
            "note":"Scripted record of fictional outreach; no message was sent."})
        client.request("POST",f"/api/cases/{case_id}/analyze",{"revision":case["revision"],"requestId":"pilot-courtesy-reply",
            "text":"Thank you for reaching out.","source":"Fictional courtesy reply","kind":"Client reply","date":date.today().isoformat()})
        case = next(c for c in client.request("GET","/api/bootstrap")["cases"] if c["id"] == case_id)
        check("reply-active-not-qualified",case["stage"] == "Active" and all(v is None for v in case["scores"].values()),
              {"case_id":case_id,"stage":case["stage"],"qualification_coverage":case["result"]["coverage"]})
        cases.append(case_id)
        feedback_ids = [t["record"]["latest_feedback"]["feedback"]["feedback_id"] for t in first]
        analyzed = client.request("POST","/api/research/orchestrator/reviews",{"request_id":"pilot-ai-review",
            "consultant_id":"Demo consultant","scope":"theme","feedback_ids":feedback_ids,"base_theme_version":base})
        analysis = client.wait("/api/research/orchestrator/reviews/"+analyzed["id"])
        proposal_id = analysis["result"]["proposal_id"]
        check("proposal-does-not-auto-apply",client.request("GET","/api/research/theme")["brief"]["version"] == base,
              {"review_id":analysis["id"],"proposal_id":proposal_id,"model_calls":analysis["calls"]})
        approved = client.request("POST",f"/api/research/orchestrator/proposals/{proposal_id}/decision",{
            "request_id":"pilot-human-direction-approval","status":"approved","reviewer":"Demo direction reviewer",
            "reason":"Scripted human approval of a restricted synthetic integration-signal change."})
        version = approved["proposal"]["resulting_theme_version"]
        check("human-approval-new-version",version != base,{"before":base,"after":version})
        rounds.append({"round":1,"theme_version":base,"tasks":first})

    # Reopen both databases and all services; no in-memory head may carry round two.
    with running_demo(root, trace) as client:
        check("restart-keeps-approved-direction",client.request("GET","/api/research/theme")["brief"]["version"] == version,{"approved_version":version})
        second = [company_task(client,2,n,version) for n in (1,2)]
        review(client,second[0],"defer","pilot-feedback-r2-c1",minutes=4)
        client.request("POST","/api/research/opportunities/"+second[0]["opportunity_id"]+"/handoff",
            {"request_id":"pilot-ineligible-handoff","revision":second[0]["record"]["revision"]},expected=409)
        review(client,second[1],"worth_exploring","pilot-feedback-r2-c2",minutes=7)
        pending = submit(client,second[1],"pilot-handoff-r2-first")
        check("same-company-duplicate-candidate",bool(pending["duplicate_candidates"]),{"matching_cases":len(pending["duplicate_candidates"])})
        returned = client.request("POST","/api/research/handoffs/"+pending["receipt"]["handoff_id"]+"/decision",{
            "request_id":"pilot-return-r2","opportunity_revision":pending["receipt"]["opportunity_revision"],"status":"needs_research",
            "reviewer":"Demo receiving reviewer","reason":"Clarify why this synthetic operations-team opportunity differs from the earlier team."})
        check("return-creates-no-case",returned["receipt"]["case_id"] is None and len(client.request("GET","/api/bootstrap")["cases"]) == 8,
              {"handoff_id":returned["receipt"]["handoff_id"],"status":"needs_research"})
        receipts.append(returned["receipt"])
        review(client,second[1],"worth_exploring","pilot-feedback-r2-c2-revised",minutes=5)
        pending = submit(client,second[1],"pilot-handoff-r2-revised")
        second_case = accept(client,pending,"pilot-accept-r2","Scripted check: same company, explicitly distinct synthetic operations integration team.")
        cases.append(second_case)
        check("distinct-opportunities-not-merged",second_case != case_id,{"case_ids":cases})
        dashboard = client.request("GET","/api/research/orchestrator?consultant_id=Demo%20consultant&scope=theme")
        final_cases = [c for c in client.request("GET","/api/bootstrap")["cases"] if c["id"] in cases]
        check("public-research-not-qualification-evidence",all(not c["evidence"] and all(v is None for v in c["scores"].values()) for c in final_cases),
              {"confirmed_evidence_count":sum(len(c["evidence"]) for c in final_cases),"qualification_coverage":[c["result"]["coverage"] for c in final_cases]})
        check("new-direction-reaches-assessment",all(t["theme_version"] == version for t in second) and
              len([x for x in trace if x["kind"] == "assessment" and x["theme_version"] == version and DIRECTION_SIGNAL in x["signals"]]) == 2,
              {"round_two_version":version,"signal":DIRECTION_SIGNAL})
        check("new-direction-reaches-search",sum(x["kind"] == "search" and DIRECTION_SIGNAL in x["query"] for x in trace) == 2,{"round_two_queries":2})
        old = client.request("GET","/api/research/runs/"+first[0]["run_id"])
        check("old-snapshot-preserved",old["theme_version"] == base,{"old_run_id":old["run_id"],"old_version":old["theme_version"]})
        check("all-output-is-synthetic",all(t["card"]["mode"] == "synthetic" for t in first+second),{"synthetic_tasks":4})
        kpis = dashboard["kpis"]
        check("kpi-denominators-and-unknowns",kpis["opportunities"] == 4 and kpis["reviewed_opportunities"] == 4 and
              kpis["worth_exploring_rate"] == .5 and kpis["review_minutes_samples"] == 3 and kpis["review_minutes_median"] == 5,
              kpis)
        rounds.append({"round":2,"theme_version":version,"tasks":second})
        snapshot = {"opportunities":client.request("GET","/api/research/opportunities"),"handoffs":client.request("GET","/api/research/handoffs"),
                    "received_cases":final_cases,"manager":dashboard}

    return {"kind":"offline_synthetic_functional_rehearsal","created_at":now(),"passed":all(c["passed"] for c in checks),
        "external_api_calls":0,"real_messages_sent":0,"scripted_human_actions":True,
        "business_value_verified":False,"cgs_client_acceptance":False,"checks":checks,"rounds":rounds,
        "kpis":kpis,"case_ids":cases,"receipts":receipts,"trace":trace,"snapshot":snapshot}


def prepare_demo(root):
    root = Path(root).resolve()
    marker = root / MARKER
    if root.exists() and any(root.iterdir()):
        if not marker.is_file():
            raise ValueError("Choose an empty demo directory; existing non-demo files will not be modified.")
        state = json.loads(marker.read_text(encoding="utf-8"))
        if state.get("kind") != "cgs-offline-demo" or state.get("status") != "complete":
            raise ValueError("This demo preparation was incomplete. Choose a new empty --workspace-dir.")
        return json.loads((root / "pilot-evidence.json").read_text(encoding="utf-8"))
    root.mkdir(parents=True, exist_ok=True)
    state = {"kind":"cgs-offline-demo","status":"preparing","created_at":now()}
    marker.write_text(json.dumps(state,indent=2)+"\n",encoding="utf-8")
    (root/"config").mkdir()
    for name in ("problem_brief.json","rubric.json"):
        shutil.copyfile(PROJECT_ROOT/"config"/name,root/"config"/name)
    report = run_two_round_pilot(root)
    (root/"pilot-evidence.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    state.update(status="complete",completed_at=now())
    marker.write_text(json.dumps(state,indent=2)+"\n",encoding="utf-8")
    return report
