"""P1: one bounded company-research task, using the existing Worker."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import threading
import time
from uuid import uuid4
from typing import Literal
from pydantic import Field, model_validator

from .contracts import Contract, Id, ResearchTask
from lead_agent.models import Brief, Limits, Rubric, Seed, now
from lead_agent.providers import LiveProviders, ProviderError
from lead_agent.storage import Store
from lead_agent.worker import Worker, new_payload


class ResearchServiceError(Exception):
    status = 409


class ResearchNotFound(ResearchServiceError):
    status = 404


class ResearchUnavailable(ResearchServiceError):
    status = 503


class StartResearch(Contract):
    request_id: Id
    consultant_id: Id
    company: Seed | None = None
    mode: Literal["company", "discovery"] = "company"
    research_version: Literal["legacy", "v2"] = "legacy"
    target: int = Field(default=2, ge=1, le=2)
    theme_id: Id | None = None
    theme_version: Id | None = None

    @model_validator(mode="after")
    def valid_mode(self):
        if (self.mode == "company") != (self.company is not None):
            raise ValueError("Supply a company only in company mode; discovery uses the approved direction.")
        return self


class ResearchJobStore(Store):
    def __init__(self, path):
        super().__init__(path)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_requests(
                    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    run_id TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS research_tasks(
                    run_id TEXT PRIMARY KEY, contract TEXT NOT NULL);
            """)

    def request(self, request_id, fingerprint):
        with self.db() as db:
            row = db.execute("SELECT * FROM research_requests WHERE request_id=?", (request_id,)).fetchone()
        if row and row["fingerprint"] != fingerprint:
            raise ResearchServiceError("This request_id was already used for different input.")
        return row["run_id"] if row else None

    def task(self, run_id):
        with self.db() as db:
            row = db.execute("SELECT contract FROM research_tasks WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ResearchNotFound("Research task not found.")
        return ResearchTask.model_validate_json(row[0])

    def jobs(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("""
                SELECT runs.* FROM runs JOIN research_tasks ON runs.id=research_tasks.run_id
                ORDER BY runs.rowid DESC
            """)]

    def assert_idle(self, db, except_run=None):
        row = db.execute("""
            SELECT runs.id FROM runs JOIN research_tasks ON runs.id=research_tasks.run_id
            WHERE runs.status='running' AND runs.lease>? AND runs.id!=?
        """, (time.time(), except_run or "")).fetchone()
        if row:
            raise ResearchServiceError("A research task is already running. Stop it or wait before starting another.")

    def create_job(self, request, fingerprint, payload, knowledge_version, validate_theme=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM research_requests WHERE request_id=?",
                               (request.request_id,)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ResearchServiceError("This request_id was already used for different input.")
                return prior["run_id"], None, False
            if validate_theme:
                validate_theme(db)
            self.assert_idle(db)
            run_id = self._create_run(db, payload)
            task = ResearchTask(run_id=run_id, request_id=request.request_id,
                consultant_id=request.consultant_id, knowledge_version=knowledge_version,
                brief=payload["brief"], rubric=payload["rubric"],
                companies=[request.company] if request.company else [], mode=request.mode,
                limits=payload["limits"], created_at=now())
            db.execute("INSERT INTO research_tasks VALUES (?,?)", (run_id, task.model_dump_json()))
            db.execute("INSERT INTO research_requests VALUES (?,?,?)",
                       (request.request_id, fingerprint, run_id))
            token = str(uuid4())
            db.execute("UPDATE runs SET status='running',token=?,lease=? WHERE id=?",
                       (token, time.time() + 120, run_id))
        return run_id, token, True

    def claim_job(self, run_id):
        self.task(run_id)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.assert_idle(db, run_id)
            token = str(uuid4())
            changed = db.execute("""
                UPDATE runs SET token=?,lease=?,status='running',stop=0,error=''
                WHERE id=? AND lease<? AND status IN ('paused','failed')
            """, (token, time.time() + 120, run_id, time.time())).rowcount
            if not changed:
                raise ResearchServiceError("This task is active or terminal and cannot be resumed.")
        return token

    def reconcile_expired(self, protected=()):
        protected = set(protected)
        with self.db() as db:
            rows = db.execute("""
                SELECT runs.id FROM runs JOIN research_tasks ON runs.id=research_tasks.run_id
                WHERE runs.status IN ('pending','running') AND runs.lease<?
            """, (time.time(),)).fetchall()
            for row in rows:
                if row["id"] not in protected:
                    db.execute("""
                        UPDATE runs SET status='paused',lease=0,token=NULL,error=?
                        WHERE id=? AND lease<?
                    """, ("Worker interrupted; saved progress can be resumed explicitly.",
                          row["id"], time.time()))


class ResearchService:
    def __init__(self, root: Path, store: ResearchJobStore, model: str,
                 provider_factory=None):
        self.root, self.store, self.model = root, store, model
        self.provider_factory = provider_factory or self._provider
        self._lock = threading.RLock()
        self._threads = {}
        self._closed = False
        self.theme_store = None
        self.demo_companies = None
        self.store.reconcile_expired()

    @staticmethod
    def _provider(model):
        return LiveProviders(os.getenv("GEMINI_API_KEY", ""), os.getenv("TAVILY_API_KEY", ""), model)

    def _checked_provider(self, model):
        provider = self.provider_factory(model)
        try:
            provider.check()  # Local validation only; this does not make an API call.
        except ProviderError:
            raise ResearchUnavailable("Research is not configured. Set server-side GEMINI_API_KEY, "
                                      "TAVILY_API_KEY and a supported GEMINI_MODEL, then restart.") from None
        return provider

    def theme(self, consultant_id=None):
        try:
            brief = Brief.model_validate_json((self.root / "config/problem_brief.json").read_text(encoding="utf-8"))
            rubric = Rubric.model_validate_json((self.root / "config/rubric.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ResearchUnavailable("Research theme configuration is unavailable or invalid.") from None
        return self.theme_store.current(consultant_id) if self.theme_store else brief, rubric

    @staticmethod
    def limits():
        return Limits(searches=8, extractions=10, model_calls=6, tavily_credits=18,
                      active_seconds=300, model_requests_per_minute=4)

    @staticmethod
    def discovery_limits():
        return Limits(searches=10, extractions=12, model_calls=18, tavily_credits=22,
                      active_seconds=480, model_requests_per_minute=4)

    def start(self, body):
        request = StartResearch.model_validate(body)
        fingerprint = hashlib.sha256(request.model_dump_json(exclude={"request_id"}).encode()).hexdigest()
        with self._lock:
            self._ensure_open()
            existing = self.store.request(request.request_id, fingerprint)
            if existing:
                return self.status(existing), False
            brief, rubric = self.theme(request.consultant_id)
            if ((request.theme_id and request.theme_id != brief.brief_id) or
                    (request.theme_version and request.theme_version != brief.version)):
                raise ResearchServiceError("The theme changed. Read the current theme before starting.")
            if request.company and request.company.domain in brief.excluded_domains:
                raise ResearchServiceError("This company domain is excluded by the approved research direction.")
            if self.demo_companies is not None and (not request.company or self.demo_companies.get(request.company.domain) != request.company.company):
                raise ValueError("The offline demo supports only its listed synthetic company names and domains.")
            provider = self._checked_provider(self.model)
            if self.demo_companies is None and (request.mode == "discovery" or request.research_version == "v2"):
                from lead_agent.research_v2 import new_research_payload
                payload = new_research_payload(brief, rubric, company=request.company, target=request.target,
                                               limits=self.discovery_limits(), model=self.model)
            else:
                payload = new_payload(brief, rubric, [request.company.model_dump()], strategy="seeds",
                                      limits=self.limits(), model=self.model, prefetch_sources=True)
            if self.demo_companies is not None:
                payload["mode"] = "synthetic"
            run_id, token, created = self.store.create_job(
                request, fingerprint, payload, "brief-only:" + brief.brief_id + ":" + brief.version,
                (lambda db: self.theme_store.assert_current(db, request.consultant_id, brief.version)) if self.theme_store else None)
            if created:
                self._dispatch(run_id, token, provider)
            return self.status(run_id), created

    def _dispatch(self, run_id, token, provider):
        def work():
            try:
                Worker(self.store, provider).execute(run_id, token)
            except Exception:
                # The worker normally stores its own errors. Never expose an unexpected exception body.
                self.store.finish(run_id, "failed", "Worker failed; saved progress is retained.", token)
            finally:
                with self._lock:
                    if self._threads.get(run_id) is threading.current_thread():
                        self._threads.pop(run_id, None)

        thread = threading.Thread(target=work, name="research-" + run_id[:8], daemon=True)
        self._threads[run_id] = thread
        try:
            thread.start()
        except Exception:
            self._threads.pop(run_id, None)
            self.store.finish(run_id, "failed", "Could not start the research worker. Retry explicitly.", token)
            raise ResearchUnavailable("Could not start the research worker.") from None

    def _ensure_open(self):
        if self._closed:
            raise ResearchUnavailable("Research service is shutting down.")

    def _reconcile(self):
        self.store.reconcile_expired(k for k, t in self._threads.items() if t.is_alive())

    def status(self, run_id):
        with self._lock:
            self._reconcile()
            task = self.store.task(run_id)
            row = self.store.run(run_id)
            p = row["payload"]
            status = "stopping" if row["status"] == "running" and row["stop"] else row["status"]
            return {
                "run_id": run_id, "request_id": task.request_id, "status": status,
                "consultant_id": task.consultant_id,
                "company": task.companies[0].model_dump() if task.companies else {"company": "Direction-led discovery", "domain": ""},
                "research_mode": task.mode, "workflow": p.get("workflow", "legacy"),
                "stage": p.get("discovery", {}).get("stage"),
                "candidate_queue": [{k: c.get(k) for k in ("company", "domain", "status", "stage", "disposition", "quality_gaps")}
                                    for c in p.get("discovery", {}).get("candidates", [])],
                "briefs_ready": sum(l.review.decision == "Ready for review" for l in self.store.leads(run_id)),
                "theme_id": task.brief.brief_id, "theme_version": task.brief.version,
                "rubric_version": task.rubric.version, "approval_basis": task.brief.approval_basis,
                "knowledge_version": task.knowledge_version, "model": p["model"],
                "mode": p.get("mode", "live"),
                "created_at": p["created_at"], "research_date": p["research_date"],
                "counters": p["counters"], "limits": p["limits"], "usage": p["usage"],
                "active_seconds": p["active_seconds"], "reserved_credits": p["reserved_credits"],
                "reported_credits": p["reported_credits"], "events": p["events"][-20:],
                "error": row["error"], "resume_available": row["status"] in ("paused", "failed"),
                "lease_expires_at": row["lease"] if row["status"] == "running" else None,
                "company_cards_saved": len(self.store.leads(run_id)),
            }

    def list_runs(self):
        with self._lock:
            self._reconcile()
            return [self.status(row["id"]) for row in self.store.jobs()]

    def results(self, run_id):
        status = self.status(run_id)
        payload = self.store.run(run_id)['payload']
        sources = {s['source_id']: s for s in payload.get('sources', [])}
        notes = []
        for candidate in payload.get('discovery', {}).get('candidates', []):
            if candidate['status'] == 'saved':
                continue
            facts = candidate.get('verified_facts', [])
            notes.append({'company': candidate['company'], 'status': candidate['status'],
                          'disposition': candidate.get('disposition'), 'evidence': facts,
                          'sources': [sources[key] for key in dict.fromkeys(f['source_id'] for f in facts) if key in sources]})
        return {"run": status, "candidate_notes": notes,
                "company_cards": [lead.model_dump(mode="json") for lead in self.store.leads(run_id)]}

    def stop(self, run_id):
        with self._lock:
            self._ensure_open()
            current = self.status(run_id)
            if current["status"] in ("running", "stopping"):
                self.store.stop(run_id)
            return self.status(run_id)

    def resume(self, run_id):
        with self._lock:
            self._ensure_open()
            current = self.status(run_id)
            if current["status"] not in ("paused", "failed"):
                raise ResearchServiceError("Only a paused or failed task can be resumed.")
            provider = self._checked_provider(current["model"])
            token = self.store.claim_job(run_id)
            self._dispatch(run_id, token, provider)
            return self.status(run_id)

    def shutdown(self, timeout=2):
        with self._lock:
            self._closed = True
            threads = list(self._threads.items())
            for run_id, thread in threads:
                if thread.is_alive():
                    self.store.stop(run_id)
        deadline = time.monotonic() + timeout
        for _, thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
