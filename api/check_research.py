"""Research check: run the live Step 1 discovery and Steps 2-3 research once, on a throwaway copy of the demo data.

Double-click "Check Research.cmd", or run:  python -m api.check_research
Cost: about 4 Tavily credits for discovery, then up to 22 credits and 18 Gemini calls for one company's research.
Research can take up to about 8 minutes because Gemini's free tier allows only a few requests per minute.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import time
import warnings

warnings.filterwarnings("ignore", message=".*httpx.*")

from api.settings import ROOT, Settings, load_settings  # noqa: E402

LINES: list[str] = []


def say(text: str = "") -> None:
    print(text, flush=True)
    LINES.append(text)


def main() -> int:
    from fastapi.testclient import TestClient
    from api.main import create_app

    s = load_settings()
    say(f"CGS Workspace research check  {datetime.now():%Y-%m-%d %H:%M}")
    from api.search import search_config
    prov = search_config()["provider"]
    say(f"Search provider: {prov} ({'synthetic internet, invented companies' if prov == 'mock' else 'real web'}). No client data is sent.\n")
    db = Path(tempfile.mkdtemp()) / "research-check.sqlite3"
    create_app(Settings(app_mode="demo", db_path=db)).state.db.engine.dispose()
    api = TestClient(create_app(Settings(app_mode="live", db_path=db, ai_provider=s.ai_provider, ai_model=s.ai_model)))
    me = {"X-User": "Alex Morgan"}

    say("[1/2] Discovery agent (Step 1): search the web and recommend up to 10 companies...")
    r = api.post("/api/candidates/recommend", json={"intent": "US manufacturers that announced an acquisition or new digital leader in the last six months"}, headers=me)
    if r.status_code != 200:
        say(f"      FAILED: {r.json().get('error', r.text)[:300]}")
        return finish(False)
    res = r.json()
    say(f"      OK  {res['candidates']} candidates kept. Details: {res['checks']}")
    say(f"      What was searched and seen is saved in runtime\\discovery-last-round.json")
    new = [c for c in api.get("/api/candidates", params={"status": "new"}).json() if c["round"] == res["round"]]
    for c in new:
        say(f"      - {c['company'][:38]:<38} {c['domain'][:24]:<24} {c['person'] or '(contact to confirm)'}  [{c['signal']}, {c['signalDate']}]")
    target = next((c for c in new if c["domain"]), None) or (new[0] if new else None)
    if not target:
        say("      No candidates this round, so the research step is skipped. Send a screenshot.")
        return finish(False)

    say(f"\n[2/2] Research agent (Steps 2-3) on {target['company']} ({target['domain']}). This can take up to 8 minutes...")
    oid = api.post(f"/api/candidates/{target['id']}/pick", headers=me).json()["opportunities"][0]
    o = api.get(f"/api/opportunities/{oid}").json()
    if o["research"]["status"] == "failed":
        say(f"      FAILED to start: {o['research']['error']}")
        return finish(False)
    start, last = time.monotonic(), None
    while time.monotonic() - start < 11 * 60:
        upd = api.post("/api/research/sync", headers=me).json()["updates"]
        u = next((x for x in upd if x["opportunityId"] == oid), None)
        if u and u["status"] != "running":
            break
        stage = u.get("stage") if u else None
        if stage != last:
            say(f"      ... {int(time.monotonic() - start)} s  stage: {stage}")
            last = stage
        time.sleep(15)
    o = api.get(f"/api/opportunities/{oid}").json()
    b = o["brief"]
    if o["stage"] != "review":
        say(f"      Research did not produce a reviewable brief: {o['research'].get('error') or 'still running after 11 minutes'}")
        say(f"      Usage: {b['usage']}")
        return finish(False)
    say(f"      OK  Brief ready for Step 4 review. Usage: {b['usage']}")
    say(f"      Contact: {o['contact']['name'] or '(to confirm)'}  {o['contact']['title']}")
    for f in b["facts"][:5]:
        say(f"      - [{f['date']}] {f['text'][:110]}")
        say(f"        source: {f['src'][:100]}")
    for fl in b["flags"]:
        say(f"      flag: {fl['type']}: {fl['text'][:110]}")
    say(f"      AI suggests: {b['recommendation']['decision']} ({b['recommendation']['reason'][:100]})")
    return finish(True)


def finish(ok: bool) -> int:
    say("")
    say("RESULT: research works." if ok else "RESULT: not working yet. Send a screenshot of this window.")
    out = ROOT / "runtime" / "research-check-result.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nSaved to {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
