"""The CRM connector (Insightly), and an in-memory MockCRM with the same methods.

The workspace hands over plain, provider-neutral records and gets plain records back:
  lead in:  first, last, title, email, company, website, industry, rating, status (a workspace stage name, mapped with
            config/crm.json), source (ai | manual | import), owner (a workspace user), description, workspace_id,
            target_problem, evidence_url, priority_tier, lead_type
  lead out: id, name, company, email, status (the CRM's status name), converted, mailbox, url, workspace_id
InsightlyCRM maps them onto Insightly's fields with config/crm.json, so another Insightly account only needs that file.

Reads (matches, find_lead, get_lead) serve the duplicate check and the agents (through the toolkit gate). Writes
(save_lead, add_note, convert) run only after a person's action, from api/crm_sync.py; they are never agent tools.

Credentials: %USERPROFILE%\\.cgs-workspace\\insightly.env (INSIGHTLY_API_KEY, INSIGHTLY_API_URL), outside the synced
project folder. INSIGHTLY_CREDENTIALS_FILE can point elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
import html
import itertools
import json
import os
from pathlib import Path
import re

import httpx

CONFIG = Path(__file__).resolve().parents[2] / "config" / "crm.json"
CREDENTIALS = Path(os.environ.get("INSIGHTLY_CREDENTIALS_FILE") or Path.home() / ".cgs-workspace" / "insightly.env")
PROVIDERS = ("off", "mock", "insightly")
CUSTOM = {"workspace_id": "workspaceId", "target_problem": "targetProblem", "evidence_url": "evidenceUrl",
          "priority_tier": "priorityTier", "lead_type": "leadType"}


class CrmError(Exception):
    pass


def _clean(d):
    """config/crm.json without its "_about" notes."""
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if not k.startswith("_")}
    return d


def crm_config() -> dict:
    return _clean(json.loads(CONFIG.read_text(encoding="utf-8")))


def load_credentials() -> None:
    from api.settings import _load_dotenv
    _load_dotenv(CREDENTIALS)


def split_name(name: str | None) -> tuple[str, str]:
    """First and last name; a lead without a named person is 'Contact to confirm' (Insightly requires a last name)."""
    parts = (name or "").split()
    if not parts:
        return "", "Contact to confirm"
    return (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", parts[0])


def tag_name(tag: str) -> str:
    """Insightly refuses spaces in tags."""
    return re.sub(r"\s+", "-", (tag or "").strip())


def note_html(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


# ---------- Insightly ----------
class InsightlyCRM:
    name = "insightly"

    def __init__(self, api_key: str, api_url: str, cfg: dict | None = None, http: httpx.Client | None = None):
        self.key, self.base = api_key.strip(), api_url.strip().rstrip("/")
        self.cfg = cfg or crm_config()
        self.http = http or httpx.Client(timeout=30)
        self.web = re.sub(r"//api\.", "//crm.", self.base).split("/v3")[0]
        self._cache: dict = {}

    @classmethod
    def from_env(cls, http: httpx.Client | None = None) -> "InsightlyCRM":
        load_credentials()
        return cls(os.environ.get("INSIGHTLY_API_KEY", ""), os.environ.get("INSIGHTLY_API_URL", ""), http=http)

    def ready(self) -> bool:
        return bool(self.key and self.base)

    def url(self, kind: str, rid) -> str:
        return f"{self.web}/details/{kind}/{rid}"

    @staticmethod
    def _reason(r: httpx.Response) -> str:
        try:
            data = r.json()
        except ValueError:
            return r.text[:200]
        if isinstance(data, list):
            return "; ".join(str(x.get("MESSAGE") or x) for x in data)[:300]
        if isinstance(data, dict):
            return str(data.get("Message") or data)[:300]
        return str(data)[:300]

    def _call(self, method: str, path: str, body=None, params=None):
        if not self.ready():
            raise CrmError(f"Insightly is not set up. Put INSIGHTLY_API_KEY and INSIGHTLY_API_URL in {CREDENTIALS} "
                           "(Insightly: User Settings > API), then restart the workspace.")
        try:
            r = self.http.request(method, f"{self.base}/{path}", json=body, params=params, auth=(self.key, ""))
        except httpx.HTTPError as e:
            raise CrmError(f"Could not reach Insightly ({type(e).__name__}).") from None
        if r.status_code == 404:
            return None
        if r.status_code == 401:
            raise CrmError("Insightly refused the API key. Check INSIGHTLY_API_KEY (Insightly: User Settings > API).")
        if r.status_code == 429:
            raise CrmError("Insightly's API limit for today is used up. Try again tomorrow.")
        if r.status_code >= 400:
            raise CrmError(f"Insightly answered {r.status_code}: {self._reason(r)}")
        return r.json() if r.content else {}

    def _search(self, obj: str, field: str, value) -> list[dict]:
        if value in (None, ""):
            return []
        return self._call("GET", f"{obj}/Search", params={"field_name": field, "field_value": str(value), "top": 25}) or []

    # names -> IDs, looked up once per connector
    def _table(self, path: str, name_key: str, id_key: str) -> dict:
        if path not in self._cache:
            self._cache[path] = {x[name_key]: x[id_key] for x in self._call("GET", path) or []}
        return self._cache[path]

    def _status_id(self, stage: str) -> int:
        name = self.cfg["statuses"].get(stage)
        if not name:
            raise CrmError(f"config/crm.json maps no lead status for the workspace stage {stage!r}.")
        ids = self._table("LeadStatuses", "LEAD_STATUS", "LEAD_STATUS_ID")
        if name not in ids:
            raise CrmError(f"Insightly has no lead status named {name!r}. Add it (System Settings > Lead Statuses) "
                           "or change config/crm.json.")
        return ids[name]

    def _status_name(self, sid) -> str:
        return {v: k for k, v in self._table("LeadStatuses", "LEAD_STATUS", "LEAD_STATUS_ID").items()}.get(sid, "")

    def _source_id(self, source: str | None):
        name = self.cfg.get("leadSources", {}).get(source or "")
        return self._table("LeadSources", "LEAD_SOURCE", "LEAD_SOURCE_ID").get(name) if name else None

    def _stage(self) -> tuple[int, int]:
        if "stage" not in self._cache:
            pipes = {p["PIPELINE_NAME"]: p["PIPELINE_ID"] for p in self._call("GET", "Pipelines") or []}
            pid = pipes.get(self.cfg.get("pipeline", ""))
            stages = {s["STAGE_NAME"]: s["STAGE_ID"] for s in self._call("GET", "PipelineStages") or [] if s["PIPELINE_ID"] == pid}
            sid = stages.get(self.cfg.get("qualificationStage", ""))
            if not pid or not sid:
                raise CrmError(f"Insightly has no pipeline {self.cfg.get('pipeline')!r} with a stage "
                               f"{self.cfg.get('qualificationStage')!r} (config/crm.json).")
            self._cache["stage"] = (pid, sid)
        return self._cache["stage"]

    def _user(self, person: str | None):
        uid = self.cfg.get("users", {}).get(person or "")
        return int(uid) if uid else None

    def _field(self, key: str) -> str:
        return (self.cfg.get("fields") or {}).get(key) or ""

    def account(self) -> str:
        me = self._call("GET", "Users/Me") or {}
        return me.get("EMAIL_ADDRESS", "")

    def check_setup(self) -> list[str]:
        """What config/crm.json names that this Insightly account lacks (empty = ready). Reads only."""
        problems = []
        statuses = self._table("LeadStatuses", "LEAD_STATUS", "LEAD_STATUS_ID")
        problems += [f"lead status {n!r} (System Settings > Lead Statuses)" for n in sorted(set(self.cfg["statuses"].values())) if n not in statuses]
        sources = self._table("LeadSources", "LEAD_SOURCE", "LEAD_SOURCE_ID")
        problems += [f"lead source {n!r} (System Settings > Lead Sources)" for n in sorted(set(self.cfg.get("leadSources", {}).values())) if n not in sources]
        try:
            self._stage()
        except CrmError as e:
            problems.append(str(e))
        have = {c["FIELD_NAME"] for c in (self._call("GET", "CustomFields/Leads") or []) + (self._call("GET", "CustomFields/Opportunities") or [])}
        problems += [f"custom field {n!r} (System Settings > Objects and Fields)" for n in (self.cfg.get("fields") or {}).values() if n and n not in have]
        return problems

    # reads
    def _lead_out(self, x: dict) -> dict:
        wf = self._field("workspaceId")
        ws = next((c.get("FIELD_VALUE") for c in x.get("CUSTOMFIELDS") or [] if c.get("FIELD_NAME") == wf), None) if wf else None
        return {"id": str(x["LEAD_ID"]), "name": " ".join(p for p in (x.get("FIRST_NAME"), x.get("LAST_NAME")) if p),
                "company": x.get("ORGANISATION_NAME") or "", "email": x.get("EMAIL") or "",
                "status": self._status_name(x.get("LEAD_STATUS_ID")), "converted": bool(x.get("CONVERTED")),
                "mailbox": x.get("LINKED_EMAIL_ADDRESS") or "", "url": self.url("Lead", x["LEAD_ID"]), "workspace_id": ws}

    def get_lead(self, lead_id: str) -> dict | None:
        x = self._call("GET", f"Leads/{lead_id}")
        return self._lead_out(x) if x else None

    def find_lead(self, workspace_id: str = "", email: str = "") -> dict | None:
        """The lead for this workspace record (by the Workspace ID field), else the lead with this email address."""
        if self._field("workspaceId"):
            hits = self._search("Leads", self._field("workspaceId"), workspace_id)
            if hits:
                return self._lead_out(hits[0])
        hits = self._search("Leads", "EMAIL", email)
        return self._lead_out(hits[0]) if hits else None

    def matches(self, company: str = "", email: str = "") -> dict:
        """What the CRM already holds for a company or a person: leads, contacts, organizations, opportunities."""
        out = {"leads": {}, "contacts": {}, "organizations": {}, "opportunities": {}}
        orgs = self._search("Organisations", "ORGANISATION_NAME", company)
        for x in self._search("Leads", "ORGANISATION_NAME", company) + self._search("Leads", "EMAIL", email):
            out["leads"][x["LEAD_ID"]] = self._lead_out(x)
        for x in self._search("Contacts", "EMAIL_ADDRESS", email):
            out["contacts"][x["CONTACT_ID"]] = {"id": str(x["CONTACT_ID"]), "name": f"{x.get('FIRST_NAME') or ''} {x.get('LAST_NAME') or ''}".strip(),
                                                "title": x.get("TITLE") or "", "email": x.get("EMAIL_ADDRESS") or "", "url": self.url("Contact", x["CONTACT_ID"])}
        for x in orgs:
            oid = x["ORGANISATION_ID"]
            out["organizations"][oid] = {"id": str(oid), "name": x.get("ORGANISATION_NAME") or "", "url": self.url("Organisation", oid)}
            try:
                deals = self._search("Opportunities", "ORGANISATION_ID", oid)
            except CrmError:
                deals = []
            for d in deals:
                out["opportunities"][d["OPPORTUNITY_ID"]] = {"id": str(d["OPPORTUNITY_ID"]), "name": d.get("OPPORTUNITY_NAME") or "",
                                                             "state": d.get("OPPORTUNITY_STATE") or "", "url": self.url("Opportunity", d["OPPORTUNITY_ID"])}
        return {k: list(v.values()) for k, v in out.items()}

    # writes (after a person's action only)
    def _lead_body(self, lead: dict, create: bool) -> dict:
        body = {"FIRST_NAME": lead.get("first"), "LAST_NAME": lead.get("last") or "Contact to confirm", "TITLE": lead.get("title"),
                "EMAIL": lead.get("email"), "ORGANISATION_NAME": lead.get("company"), "WEBSITE": lead.get("website"),
                "INDUSTRY": lead.get("industry"), "LEAD_RATING": lead.get("rating"), "LEAD_DESCRIPTION": lead.get("description")}
        body = {k: v for k, v in body.items() if v not in (None, "")}
        if lead.get("status"):
            body["LEAD_STATUS_ID"] = self._status_id(lead["status"])
        src = self._source_id(lead.get("source"))
        if src:
            body["LEAD_SOURCE_ID"] = src
        uid = self._user(lead.get("owner"))
        if uid:
            body["OWNER_USER_ID"] = body["RESPONSIBLE_USER_ID"] = uid
        custom = [{"FIELD_NAME": self._field(cfg_key), "FIELD_VALUE": lead[key]}
                  for key, cfg_key in CUSTOM.items() if self._field(cfg_key) and lead.get(key) not in (None, "")]
        if custom:
            body["CUSTOMFIELDS"] = custom
        if create and self.cfg.get("tag"):
            body["TAGS"] = [{"TAG_NAME": tag_name(self.cfg["tag"])}]
        return body

    def save_lead(self, lead: dict, lead_id: str | None = None) -> dict:
        """Create a lead, or update the given one. A converted lead cannot be changed (Insightly refuses)."""
        body = self._lead_body(lead, create=not lead_id)
        if lead_id:
            body["LEAD_ID"] = int(lead_id)
            x = self._call("PUT", "Leads", body)
            if x is None:
                raise CrmError(f"Insightly lead {lead_id} no longer exists.")
        else:
            x = self._call("POST", "Leads", body)
        return self._lead_out(x)

    def add_note(self, record: str, rid: str, title: str, text: str) -> str:
        kind = {"lead": "Leads", "opportunity": "Opportunities", "contact": "Contacts"}[record]
        x = self._call("POST", f"{kind}/{rid}/Notes", {"TITLE": title[:250], "BODY": note_html(text)}) or {}
        return str(x.get("NOTE_ID", ""))

    def convert(self, lead_id: str, deal: dict) -> dict:
        """Qualification: organization, contact and opportunity (at the qualification stage), linked; then the lead is set
        to the 'converted' status and marked converted, in that order (Insightly refuses a status change afterwards).
        Safe to run again after a partial failure: each part is looked up before it is created."""
        tag = [{"TAG_NAME": tag_name(self.cfg["tag"])}] if self.cfg.get("tag") else []
        uid = self._user(deal.get("owner"))
        orgs = self._search("Organisations", "ORGANISATION_NAME", deal["company"])
        org = orgs[0] if orgs else self._call("POST", "Organisations", {"ORGANISATION_NAME": deal["company"], "TAGS": tag,
                                                                       **({"WEBSITE": deal["website"]} if deal.get("website") else {})})
        contacts = self._search("Contacts", "EMAIL_ADDRESS", deal.get("email")) or [
            x for x in self._search("Contacts", "LAST_NAME", deal.get("last") if not deal.get("email") else "")
            if x.get("ORGANISATION_ID") == org["ORGANISATION_ID"] and (x.get("FIRST_NAME") or "") == (deal.get("first") or "")]
        contact = contacts[0] if contacts else self._call("POST", "Contacts", {k: v for k, v in {
            "FIRST_NAME": deal.get("first"), "LAST_NAME": deal.get("last") or "Contact to confirm", "TITLE": deal.get("title"),
            "EMAIL_ADDRESS": deal.get("email"), "ORGANISATION_ID": org["ORGANISATION_ID"], "TAGS": tag}.items() if v not in (None, "")})
        f = self._field("opportunityWorkspaceId")
        found = self._search("Opportunities", f, deal["workspace_id"]) if f else []
        if found:
            opp = found[0]
        else:
            body = {"OPPORTUNITY_NAME": deal["name"][:250], "OPPORTUNITY_STATE": "OPEN", "OPPORTUNITY_DETAILS": deal.get("details") or "",
                    "ORGANISATION_ID": org["ORGANISATION_ID"], "TAGS": tag,
                    **({"CUSTOMFIELDS": [{"FIELD_NAME": f, "FIELD_VALUE": deal["workspace_id"]}]} if f else {}),
                    **({"OWNER_USER_ID": uid, "RESPONSIBLE_USER_ID": uid} if uid else {})}
            opp = self._call("POST", "Opportunities", body)
            pid, sid = self._stage()
            self._call("PUT", f"Opportunities/{opp['OPPORTUNITY_ID']}/Pipeline", {"PIPELINE_ID": pid, "PIPELINE_STAGE_CHANGE": {"STAGE_ID": sid}})
        oid = opp["OPPORTUNITY_ID"]
        links = self._call("GET", f"Opportunities/{oid}/Links") or []
        if not any(lk.get("LINK_OBJECT_NAME") == "Contact" and lk.get("LINK_OBJECT_ID") == contact["CONTACT_ID"] for lk in links):
            self._call("POST", f"Opportunities/{oid}/Links", {"LINK_OBJECT_NAME": "Contact", "LINK_OBJECT_ID": contact["CONTACT_ID"]})
        lead = self._call("GET", f"Leads/{lead_id}")
        if lead is None:
            raise CrmError(f"Insightly lead {lead_id} no longer exists.")
        if not lead.get("CONVERTED"):
            conv = self._status_id("converted")
            if lead.get("LEAD_STATUS_ID") != conv:
                self._call("PUT", "Leads", {"LEAD_ID": int(lead_id), "LEAD_STATUS_ID": conv})
            self._call("PUT", "Leads", {"LEAD_ID": int(lead_id), "CONVERTED": True, "CONVERTED_CONTACT_ID": contact["CONTACT_ID"],
                                        "CONVERTED_ORGANISATION_ID": org["ORGANISATION_ID"], "CONVERTED_OPPORTUNITY_ID": oid,
                                        "CONVERTED_DATE_UTC": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        return {"organisation": str(org["ORGANISATION_ID"]), "contact": str(contact["CONTACT_ID"]), "opportunity": str(oid),
                "url": self.url("Opportunity", oid)}


# ---------- mock ----------
class MockCRM:
    """An in-memory CRM with the same methods, for the demo and the tests. fail_next makes the next write fail."""
    name = "mock"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or crm_config()
        self.leads: dict[str, dict] = {}
        self.contacts: dict[str, dict] = {}
        self.orgs: dict[str, dict] = {}
        self.deals: dict[str, dict] = {}
        self.notes: list[dict] = []
        self.fail_next = ""
        self._ids = itertools.count(1001)

    def _id(self) -> str:
        return str(next(self._ids))

    def _maybe_fail(self):
        if self.fail_next:
            msg, self.fail_next = self.fail_next, ""
            raise CrmError(msg)

    def ready(self) -> bool:
        return True

    def account(self) -> str:
        return "mock@crm.example"

    def url(self, kind: str, rid) -> str:
        return f"mock://crm/{kind}/{rid}"

    def _out(self, x: dict) -> dict:
        return {k: x.get(k) for k in ("id", "company", "email", "status", "converted", "mailbox", "url", "workspace_id")} | {
            "name": f"{x.get('first') or ''} {x.get('last') or ''}".strip()}

    def get_lead(self, lead_id: str) -> dict | None:
        x = self.leads.get(str(lead_id))
        return self._out(x) if x else None

    def find_lead(self, workspace_id: str = "", email: str = "") -> dict | None:
        for x in self.leads.values():
            if workspace_id and x.get("workspace_id") == workspace_id:
                return self._out(x)
        for x in self.leads.values():
            if email and (x.get("email") or "").lower() == email.lower():
                return self._out(x)
        return None

    def matches(self, company: str = "", email: str = "") -> dict:
        same = lambda a, b: bool(a and b) and a.strip().lower() == b.strip().lower()
        orgs = [o for o in self.orgs.values() if same(o["name"], company)]
        return {"leads": [self._out(x) for x in self.leads.values() if same(x.get("company"), company) or same(x.get("email"), email)],
                "contacts": [dict(x) for x in self.contacts.values() if same(x.get("email"), email)],
                "organizations": [dict(o) for o in orgs],
                "opportunities": [dict(d) for d in self.deals.values() if d["organisation"] in {o["id"] for o in orgs}]}

    def save_lead(self, lead: dict, lead_id: str | None = None) -> dict:
        self._maybe_fail()
        status = self.cfg["statuses"].get(lead["status"]) if lead.get("status") else None
        if lead.get("status") and not status:  # as strict as Insightly
            raise CrmError(f"config/crm.json maps no lead status for the workspace stage {lead['status']!r}.")
        if lead_id:
            x = self.leads.get(str(lead_id))
            if x is None:
                raise CrmError(f"Insightly lead {lead_id} no longer exists.")
            if x["converted"]:
                raise CrmError("Cannot change converted Leads")
            x.update({k: v for k, v in lead.items() if v not in (None, "") and k != "status"})
        else:
            lid = self._id()
            x = {**{k: v for k, v in lead.items() if k != "status"}, "id": lid, "converted": False,
                 "mailbox": f"lead-{lid}@mailbox.crm.example", "url": self.url("Lead", lid)}
            self.leads[lid] = x
        if status:
            x["status"] = status
        return self._out(x)

    def add_note(self, record: str, rid: str, title: str, text: str) -> str:
        self._maybe_fail()
        nid = self._id()
        self.notes.append({"id": nid, "record": record, "rid": str(rid), "title": title, "text": text})
        return nid

    def convert(self, lead_id: str, deal: dict) -> dict:
        self._maybe_fail()
        org = next((o for o in self.orgs.values() if o["name"] == deal["company"]), None)
        if org is None:
            oid = self._id()
            org = self.orgs[oid] = {"id": oid, "name": deal["company"], "url": self.url("Organisation", oid)}
        contact = next((c for c in self.contacts.values() if deal.get("email") and c["email"] == deal["email"]), None)
        if contact is None:
            cid = self._id()
            contact = self.contacts[cid] = {"id": cid, "name": f"{deal.get('first') or ''} {deal.get('last') or ''}".strip(),
                                            "title": deal.get("title") or "", "email": deal.get("email") or "", "url": self.url("Contact", cid)}
        d = next((x for x in self.deals.values() if x["workspace_id"] == deal["workspace_id"]), None)
        if d is None:
            did = self._id()
            d = self.deals[did] = {"id": did, "name": deal["name"], "state": "OPEN", "stage": self.cfg.get("qualificationStage"),
                                   "organisation": org["id"], "contact": contact["id"], "workspace_id": deal["workspace_id"],
                                   "details": deal.get("details") or "", "url": self.url("Opportunity", did)}
        lead = self.leads[str(lead_id)]
        if not lead["converted"]:
            lead["status"], lead["converted"] = self.cfg["statuses"]["converted"], True
        return {"organisation": org["id"], "contact": contact["id"], "opportunity": d["id"], "url": d["url"]}


def make_crm(provider: str, app_mode: str, crm_mode: str):
    """The CRM for these settings, or None when it is off. Empty provider: mock in demo mode, off in live mode."""
    if crm_mode == "disabled":
        return None
    p = provider or ("mock" if app_mode == "demo" else "off")
    if p == "off":
        return None
    if p == "mock":
        return MockCRM()
    return InsightlyCRM.from_env()
