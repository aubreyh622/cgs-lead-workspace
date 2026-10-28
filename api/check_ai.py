"""AI connection check: run on your own computer after adding your key to .env.

Double-click "Check AI.cmd", or run:  python -m api.check_ai
It sends two small requests with synthetic demo text (no client data), shows what the AI proposed,
and what the code checks kept or threw away. The result is also saved to runtime/ai-check-result.txt.
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path

import warnings

import httpx

warnings.filterwarnings("ignore", message=".*httpx.*")

from api.demo_agents import STAR
from api.live_agents import extract_evidence, label_reply
from api.llm import KEY_NAMES, LLMError, make_provider
from api.settings import ROOT, load_settings

LINES: list[str] = []


def say(text: str = "") -> None:
    print(text)
    LINES.append(text)


def gemini_models(key: str) -> list[str]:
    try:
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": key}, timeout=20)
        r.raise_for_status()
        return [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception:
        return []


def main() -> int:
    settings = load_settings()
    say(f"CGS Workspace AI check  {datetime.now():%Y-%m-%d %H:%M}")
    say(f"AI_PROVIDER = {settings.ai_provider}   AI_MODEL = {settings.ai_model or '(default)'}")
    say("Only synthetic demo text is sent. No client data.\n")
    ok = True
    try:
        provider = make_provider(settings.ai_provider, settings.ai_model)
        say(f"[1/3] Key found. Using {provider.label}.  OK")
    except LLMError as e:
        say(f"[1/3] Setup problem: {e}")
        say(f"      Open the .env file in this folder and fill in {KEY_NAMES.get(settings.ai_provider, 'the key')}=...")
        return finish(False)

    try:
        label, usage = label_reply(provider, STAR["reply"])
        say(f"[2/3] Reply label: '{label}' (expected 'meet').  {'OK' if label == 'meet' else 'CHECK: unexpected label'}")
    except LLMError as e:
        say(f"[2/3] FAILED ({e.kind}): {e}")
        if settings.ai_provider == "gemini" and e.kind == "config":
            models = gemini_models(provider.key)
            if models:
                say("      Models your key can use (put one in AI_MODEL in .env):")
                for m in [m for m in models if "flash" in m][:12] or models[:12]:
                    say(f"        {m}")
        return finish(False)

    try:
        (kept, dropped), usage = extract_evidence(provider, STAR["note"], {"company": "Corvane Industrial (synthetic)", "opportunity": "Scaling AI inspection"})
    except LLMError as e:
        say(f"[3/3] FAILED ({e.kind}): {e}")
        return finish(False)
    say(f"[3/3] Evidence agent: {len(kept)} proposals kept, dropped by the code checks: {dropped or 'none'}")
    say(f"      Tokens used: {usage.tokens_in} in, {usage.tokens_out} out")
    for p in kept:
        say(f"      - {p['kind']:<6} {p['key']:<11} = {str(p['value']):<6} [{p['type']}]  \"{p['quote'][:70]}\"")
    if not kept:
        ok = False
        say("      CHECK: nothing usable came back. Try a stronger model in AI_MODEL.")
    return finish(ok and workflow(settings))


BLUEGATE_NOTE = ("Call with Daniel Okafor (CIO), 3 Nov 2026. Daniel said the ERP consolidation is late because the three sites run "
                 "different order-to-cash processes. He said: \"We need someone to help us design one process before the cutover in June.\" "
                 "Daniel owns the IT budget for the program; the CFO approves anything above $300K. "
                 "We agreed to a working session on 12 November with his process leads.")


def workflow(settings) -> bool:
    """[4/4] The whole chain through the backend, on a throwaway copy of the demo data, with the real AI."""
    import tempfile
    from fastapi.testclient import TestClient
    from api.main import create_app
    from api.settings import Settings

    say("[4/4] Full workflow through the backend (throwaway copy of the demo data):")
    db = Path(tempfile.mkdtemp()) / "check.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    api = TestClient(create_app(Settings(app_mode="live", db_path=db, ai_provider=settings.ai_provider, ai_model=settings.ai_model)))
    me = {"X-User": "Alex Morgan"}
    steps = [
        ("A2 labels a reply (Bluegate)", lambda: api.post("/api/opportunities/bluegate/outreach/reply", json={"text": "Thanks Alex, happy to talk. Does Tuesday work?"}, headers=me),
         lambda j: f"label '{j.get('replyLabel')}'"),
        ("A2+A3 read call notes", lambda: api.post("/api/opportunities/bluegate/notes", json={"text": BLUEGATE_NOTE}, headers=me),
         lambda j: f"{j.get('proposals')} proposals, {j.get('droppedForInvalidQuote')} dropped for bad quotes"),
    ]
    ok = True
    for name, call, show in steps:
        r = call()
        good = r.status_code == 200 and r.json().get("replyLabel") != "pending"
        say(f"      {name:<34} {'OK  ' + show(r.json()) if good else 'FAILED ' + r.text[:200]}")
        ok &= good
    if ok:
        o = api.get("/api/opportunities/bluegate").json()
        for prop in [x for x in o["proposals"] if x["status"] == "pending"]:
            api.post(f"/api/proposals/{prop['id']}", json={"action": "accept", "reason": "check"}, headers=me)
        a = api.get("/api/opportunities/bluegate").json()["assessment"]
        say(f"      {'Person accepts all; rules engine':<34} Q {a['ql']:g}-{a['qu']:g}, coverage {a['coverage']}%, {a['readiness']}")
    for name, call, show in [
        ("A4 suggests next actions", lambda: api.post("/api/opportunities/bluegate/suggestions", headers=me), lambda j: f"{j.get('suggestions')} suggestions"),
        ("A1 drafts outreach (Halvorsen)", lambda: api.post("/api/opportunities/halvorsen/outreach/generate", json={"tone": "brief"}, headers=me),
         lambda j: f"subject '{j.get('subject', '')[:50]}'"),
        ("Feedback agent reviews feedback", lambda: api.post("/api/direction/analyze", headers=me), lambda j: (j.get("summary") or "")[:70]),
    ]:
        r = call()
        good = r.status_code == 200
        say(f"      {name:<34} {'OK  ' + show(r.json()) if good else 'FAILED ' + r.text[:200]}")
        ok &= good
    runs = [x for x in api.get("/api/agent-runs", params={"limit": 50}).json() if not x["model"].startswith("demo")]
    say(f"      Real AI runs logged: {sum(1 for x in runs if x['status'] == 'completed')} completed, {sum(1 for x in runs if x['status'] == 'failed')} failed")
    return ok


def finish(ok: bool) -> int:
    say("")
    say("RESULT: AI connection works." if ok else "RESULT: not working yet. Send a screenshot of this window.")
    out = ROOT / "runtime" / "ai-check-result.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nSaved to {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
