"""Parallel lanes for the background agents: different opportunities at once, never two jobs on the same opportunity,
never two of the same company-wide job, and a demo reset still waits for all work to stop."""
import threading
import time

from api import autonomy as AU
from api import jobs as J
from api.jobs import WorkGate
from api.main import create_app
from api.settings import Settings


def test_the_gate_lets_lanes_share_and_the_reset_wait():
    gate = WorkGate()
    with gate:
        with gate:  # two lanes at once
            assert gate.acquire(timeout=0.05) is False  # a reset cannot start while lanes work
    assert gate.acquire(timeout=0.05) is True
    entered = threading.Event()
    t = threading.Thread(target=lambda: (gate.__enter__(), entered.set(), gate.__exit__()))
    t.start()
    assert not entered.wait(0.1)  # a lane waits while the reset runs
    gate.release()
    assert entered.wait(1)
    t.join()


def board(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "lanes.sqlite3"))
    with app.state.db.session() as s:
        today = AU.agent_ctx(app, s).today
        for kind, opp in (("ready_reminder", "meridian"), ("defer_nurture", "meridian"), ("ready_reminder", "atlas"),
                          ("ready_reminder", "northstar"), ("mail_sync", None), ("crm_sync", None)):
            assert J.enqueue(s, today, kind, opp, "person", "test", requested_by="test")
        s.commit()
    return app


def test_the_picker_skips_busy_opportunities_and_running_company_jobs(tmp_path):
    app = board(tmp_path)
    picked = AU.next_job_for_lane(app, {"meridian"}, set())
    assert picked[1] != "meridian"
    first = AU.next_job_for_lane(app, set(), set())
    with app.state.db.session() as s:
        J.claim(s, first[0])  # running somewhere else (a person pressed "run now")
    second = AU.next_job_for_lane(app, set(), set())
    assert second[1] != first[1]
    only_company = AU.next_job_for_lane(app, {"meridian", "atlas", "northstar"}, {"mail_sync"})
    assert only_company[1] is None and only_company[2] == "crm_sync"


def test_lanes_run_different_opportunities_at_once_and_one_opportunity_at_a_time(tmp_path, monkeypatch):
    app = board(tmp_path)
    spans, lock = [], threading.Lock()

    def slow(c, job, o):
        start = time.monotonic()
        time.sleep(0.3)
        with lock:
            spans.append((job.opportunity_id or job.kind, start, time.monotonic()))
        return "done", "ok"

    monkeypatch.setattr(AU, "patrol_due", lambda app: False)
    for kind in ("ready_reminder", "defer_nurture", "mail_sync", "crm_sync"):
        monkeypatch.setitem(AU.HANDLERS, kind, slow)
    w = AU.Worker(app, tick=0.01, pause=0, lanes=3).start()
    deadline = time.monotonic() + 10
    while len(spans) < 6 and time.monotonic() < deadline:
        time.sleep(0.05)
    w.stop.set()
    for t in w.threads:
        t.join(2)
    assert len(spans) == 6

    def overlap(a, b):
        return a[1] < b[2] and b[1] < a[2]
    assert any(overlap(a, b) for i, a in enumerate(spans) for b in spans[i + 1:])  # work ran in parallel
    mer = [x for x in spans if x[0] == "meridian"]
    assert len(mer) == 2 and not overlap(*mer)  # never two jobs on the same opportunity at once
    assert sum(t.name.startswith("cgs-agents-") for t in w.threads) == 3
