"""After a restart: what the stopped workspace left half done. Jobs left as running go back on the board (and stop
blocking their opportunity); an email left as sending is never sent again on its own: it becomes unknown and the person
checks Gmail first. A second server on the same port refuses to start instead of taking the first one's work."""
from datetime import timedelta
import socket

import pytest

from api import autonomy as AU
from api import jobs as J
from api import main as MAIN
from api import models as m
from api import undo as U
from api import updates as UP
from tests.v2.test_crm import get
from tests.v2.test_undo import TO, approve, texts, ws  # noqa: F401  (ws is a fixture)


def running_job(app, kind="ready_reminder", opp="meridian", attempts=1):
    with app.state.db.session() as s:
        j = J.enqueue(s, AU.agent_ctx(app, s).today, kind, opp, "person", "test", requested_by="test")
        s.flush()
        assert J.claim(s, j.id)  # the process that took it then stopped
        if attempts > 1:
            s.get(m.AgentJob, j.id).attempts = attempts
        return j.id


def test_a_job_left_running_goes_back_on_the_board_and_frees_its_opportunity(ws):
    app, api, oid = ws
    jid = running_job(app)
    picked = AU.next_job_for_lane(app, set(), set())
    assert picked is None or picked[1] != "meridian"  # stuck as running, it blocks its opportunity
    back = AU.recover_after_restart(app)
    assert back == {"requeued": 1, "failed": 0, "emails": 0}
    with app.state.db.session() as s:
        j = s.get(m.AgentJob, jid)
        assert j.status == "queued" and j.retry_at is None and "stopped while this ran" in j.result
    assert AU.run_job(app, jid) in ("done", "skipped")  # it runs again from the start
    assert AU.recover_after_restart(app) == {"requeued": 0, "failed": 0, "emails": 0}  # nothing left to recover


def test_a_job_that_keeps_stopping_the_workspace_fails_instead(ws):
    app, api, oid = ws
    jid = running_job(app, attempts=J.MAX_TRIES)
    assert AU.recover_after_restart(app)["failed"] == 1
    with app.state.db.session() as s:
        j = s.get(m.AgentJob, jid)
        assert j.status == "failed" and "3 times" in j.error and j.finished_at is not None


def test_an_email_left_sending_is_not_sent_again_and_the_person_is_told(ws):
    app, api, oid = ws
    r = approve(api, oid)
    with app.state.db.session() as s:  # the clock took it, then the workspace stopped before Gmail answered
        assert U._take(AU.agent_ctx(app, s), r["id"], "sending")
    assert approve(api, oid, ok=409)["code"] == "queued"  # while it is "sending", the opportunity's next email is held
    assert AU.recover_after_restart(app)["emails"] == 1
    assert U.send_due(app) == 0 and app.state.mail.outbox == []  # never sent again on its own
    o = get(api, oid)
    w = o["outreach"]["waiting"]
    assert o["stage"] == "identified" and w["status"] == "unknown" and "Sent folder in Gmail" in w["error"]
    assert any("may or may not have gone out" in t for t in texts(api, oid))
    with app.state.db.session() as s:
        row = s.get(m.OutboundEmail, r["id"])
        feed = UP.feed(s, row.by, U.aware(row.created_at) - timedelta(seconds=1))
    assert any(i["status"] == "unknown" and "may not have gone out" in i["text"] for i in feed)
    assert approve(api, oid)["mode"] == "queued"  # after checking Gmail, the person can approve it again


def test_a_second_server_on_the_same_port_refuses_to_start(monkeypatch, tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert MAIN.port_in_use(port)
    assert not MAIN.port_in_use(port)
    called = []
    monkeypatch.setattr(MAIN, "port_in_use", lambda p: True)
    monkeypatch.setattr(AU, "recover_after_restart", lambda app: called.append(app))
    with pytest.raises(SystemExit):
        MAIN.main()
    assert called == []  # nothing of the running server's work was touched
