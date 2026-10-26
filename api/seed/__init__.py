"""Load the synthetic demo records (the same ones the prototype shows) into an empty database.

demo_seed.json is exported from the prototype's data.js, so the backend and the UI start from
identical records. All companies and people are fictional.
"""
from __future__ import annotations

import copy
from datetime import date
import json
from pathlib import Path

from sqlalchemy import select

from api import models as m
from backend.rules import RULE_VERSION

SEED = Path(__file__).with_name("demo_seed.json")
_SYNC = {"pending": "sync_pending", "synced": "synced", "failed": "sync_failed"}


def d(value) -> date | None:
    return date.fromisoformat(value) if value else None


def _direction_history(current: dict) -> list[dict]:
    """Rebuild v1 and v2 from the change notes so every version has full content."""
    v3 = copy.deepcopy(current)
    v2 = copy.deepcopy(v3)
    v2["signals"] = [s for s in v2["signals"] if s != "Product or plant expansion"]
    v1 = copy.deepcopy(v2)
    v1["exclusions"] = [e for e in v1["exclusions"] if e != "Consulting firms and resellers"]
    return [
        dict(v1, version="v1", approvedBy="Quinn Harper", approvedOn="2026-10-01", change="First research direction"),
        dict(v2, version="v2", approvedBy="Quinn Harper", approvedOn="2026-10-08", change="Excluded consulting firms and resellers"),
        dict(v3, change="Added “Product or plant expansion” as a signal"),
    ]


def load_demo(db) -> bool:
    """Seed an empty database. Returns False and changes nothing if records already exist."""
    data = json.loads(SEED.read_text(encoding="utf-8"))
    with db.session() as s:
        if s.scalar(select(m.Opportunity.id).limit(1)):
            return False
        s.add_all([m.Setting(key="demo_today", value=data["today"]), m.Setting(key="current_user", value=data["me"]),
                   m.Setting(key="owners", value=data["owners"]), m.Setting(key="round", value=3)])
        for v in _direction_history(data["direction"]):
            s.add(m.DirectionVersion(version=v["version"], scope="shared", problem=v["problem"], signals=v["signals"],
                                     buyer_roles=v["buyer_roles"], exclusions=v["exclusions"], excluded_domains=v["excluded_domains"],
                                     approved_by=v["approvedBy"], approved_on=d(v["approvedOn"]), basis="mock", change=v["change"]))
        companies = set()
        for L in data["leads"]:
            _load_lead(s, L, companies)
        s.flush()
        _northstar_stale_pack(s)
        for c in data["candidates"]:
            s.add(m.Candidate(id=c["id"], round=3, company=c["company"], domain=c["domain"], person=c["person"], title=c["title"],
                              why_person=c["whyPerson"], why=c["why"], signal=c["signal"], signal_date=c["signalDate"],
                              flags=c.get("flags", []), status=c["status"], direction_version="v3", demo_star=bool(c.get("star"))))
        for f in data["feedback"]:
            s.add(m.Feedback(id=f["id"], subject=f["subject"], step=f["step"], outcome=f["outcome"], reason=f["reason"],
                             comment=f.get("comment", ""), scope=f["scope"], by=f["by"], on=d(f["on"]), minutes=f["minutes"],
                             direction_version=f["version"]))
        for p in data["proposals"]:
            s.add(m.DirectionProposal(id=p["id"], type=p["type"], field=p.get("field"), target=p.get("target"), domain=p.get("domain"),
                                      scope=p["scope"], origin=p["origin"], title=p.get("title", ""), rationale=p["rationale"],
                                      before=p.get("before"), after=p.get("after"), feedback_ids=p.get("feedbackIds", []),
                                      base_version=p.get("baseVersion"), status=p["status"], decided_by=p.get("decidedBy"),
                                      created_on=d(p["on"]), decided_on=d(p["on"]) if p["status"] != "pending" else None))
        for r in data["runs"]:
            s.add(m.AgentRun(id=r["id"], agent=r["agent"], subject=r["lead"], model=r["model"], prompt_version=r["prompt"],
                             status=r["status"], calls=r["calls"], on=d(r["on"])))
    return True


def _load_lead(s, L: dict, companies: set) -> None:
    co = L["company"]
    if L["companyId"] not in companies:
        s.add(m.Company(id=L["companyId"], name=co["name"], domain=co.get("domain", ""), industry=co.get("industry", ""), hq=co.get("hq", "")))
        companies.add(L["companyId"])
    person = L["person"]
    contact_id = "ct-" + L["id"]
    s.add(m.Contact(id=contact_id, company_id=L["companyId"], name=person["name"] or None, title=person["title"]))
    rev = L.get("revenue")
    s.add(m.Opportunity(
        id=L["id"], company_id=L["companyId"], contact_id=contact_id, title=L["oppTitle"], team=L.get("team", ""),
        stage=L["stage"], owner=L.get("owner"), rating=L.get("rating", 3), source_type=L["source"]["type"], source_note=L["source"]["note"],
        direction_version=L.get("version", ""), next_action=(L.get("next") or {}).get("action"), next_due=d((L.get("next") or {}).get("due")),
        last_progress=d(L.get("lastProgress")), reply_label=L.get("replyLabel"), scores=L["scores"], conditions=L["conditions"],
        blocker=L.get("blocker"), revenue_low=rev["low"] if rev else None, revenue_high=rev["high"] if rev else None,
        need=L["problem"], crm_sync=_SYNC.get(L.get("sync"), "local_saved")))
    s.flush()  # parent rows first, so foreign keys always point at something that exists
    r = L["research"]
    s.add(m.ResearchBrief(opportunity_id=L["id"], facts=r["facts"], hypothesis=r["hypothesis"], relevance=r["relevance"],
                          alternative=r["alternative"], unknowns=r["unknowns"], flags=r["flags"], priority=r["priority"],
                          recommendation=r["rec"], suggested=r["suggest"], usage=r["usage"],
                          frozen_on=d(L.get("lastProgress")) if L["stage"] not in ("researching", "review") else None))
    if L["stage"] == "researching":
        s.add(m.ResearchTask(id="rt-" + L["id"], opportunity_id=L["id"], progress=L.get("prog", 0), log=L.get("log", [])))
    o = L.get("outreach") or {}
    if o.get("status", "none") != "none":
        s.add(m.Outreach(opportunity_id=L["id"], status=o["status"], subject=o.get("subject"), sent_on=d(o.get("sentOn")), sent_via=o.get("via")))
    for n in L.get("notes", []):
        s.add(m.Note(id=n["id"], opportunity_id=L["id"], type=n["type"], on=d(n["on"]), text=n["text"], added_by=L.get("owner") or ""))
    s.flush()
    for p in L.get("proposals", []):
        s.add(m.Proposal(id=p["id"], opportunity_id=L["id"], note_id=p.get("src"), kind=p["kind"], key=p["key"], value=p["value"],
                         quote=p["quote"], why=p["why"], evidence_type=p["type"], status=p["status"]))
    for e in L.get("evidence", []):
        s.add(m.Evidence(opportunity_id=L["id"], kind=e["kind"], key=e["key"], value=e["value"], quote=e["quote"],
                         evidence_type=e["type"], how=e["how"], by=L.get("owner") or "", on=d(e["on"])))
    for t in L.get("tasks", []):
        s.add(m.Task(id=f'{L["id"]}-{t["id"]}', opportunity_id=L["id"], title=t["title"], side=t["side"], assignee=t["assignee"], due=d(t["due"]), status=t["status"]))
    for dec in L.get("decisions", []):
        s.add(m.Decision(opportunity_id=L["id"], step=8, decision=dec["decision"], by=dec["by"], role=dec["role"], note=dec.get("note", ""), on=d(dec["on"]),
                         handover_from="Alex Morgan" if "Handed over" in dec.get("note", "") else None,
                         handover_to=L.get("owner") if "Handed over" in dec.get("note", "") else None))
    for a in L.get("activity", []):
        s.add(m.Activity(opportunity_id=L["id"], on=d(a["on"]), actor_type=a["a"], text=a["t"]))


def _northstar_stale_pack(s) -> None:
    """Northstar's pack was submitted on 23 Oct; the revenue estimate changed on 25 Oct, so the pack is out of date."""
    o = s.get(m.Opportunity, "northstar")
    if not o:
        return
    snap = {"scores": o.scores, "conditions": o.conditions, "blocker": o.blocker, "revenue": o.revenue, "need": o.need}
    s.add(m.Pack(id="pk-northstar-1", opportunity_id=o.id, submitted_by="Alex Morgan", submitted_on=date(2026, 10, 23),
                 snapshot=snap, rule_version=RULE_VERSION, stale_reason="Revenue estimate changed on 25 Oct"))
    o.revenue_low, o.revenue_high = 500000, 700000
    s.add(m.Evidence(opportunity_id=o.id, kind="revenue", key="revenue", value={"low": 500000, "high": 700000},
                     quote="Scope narrowed to three regions in the first phase.", how="manual review", by="Alex Morgan", on=date(2026, 10, 25)))
    s.add(m.Activity(opportunity_id=o.id, on=date(2026, 10, 25), actor_type="h", text="CGS revenue reviewed by Alex Morgan: $600K → $500K–$700K"))
    s.add(m.Activity(opportunity_id=o.id, on=date(2026, 10, 25), actor_type="s", text="Qualification pack marked out of date: revenue estimate changed"))
