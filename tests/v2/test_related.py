"""Memory across opportunities: other CGS work with the same company or person (api/related.py)."""
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api import models as m
from api import related as REL
from api.main import create_app
from api.settings import Settings

TODAY = date(2026, 10, 28)


def db():
    engine = create_engine("sqlite://")
    m.Base.metadata.create_all(engine)
    return Session(engine)


def opp(s, oid, company, person, *, email=None, owner="Alex Morgan", sent_days_ago=None, stage="identified"):
    if not s.get(m.Company, company):
        s.add(m.Company(id=company, name=company.title()))
    ct = m.Contact(id="ct-" + oid, company_id=company, name=person, title="CIO", email=email)
    s.add(ct)
    o = m.Opportunity(id=oid, company_id=company, contact_id=ct.id, title=f"{oid} work", stage=stage, owner=owner)
    s.add(o)
    if sent_days_ago is not None:
        s.add(m.Outreach(opportunity_id=oid, status="sent", subject="Hello", sent_on=TODAY - timedelta(days=sent_days_ago)))
    s.flush()
    return o


def test_same_company_and_same_person_across_companies():
    s = db()
    o = opp(s, "a1", "ardent", "Maria Lopez", email="maria@ardent.example")
    opp(s, "a2", "ardent", "Tom Reed", owner="Sam Lee")                                        # same company
    opp(s, "k1", "kestrel", "maria  lopez", owner="Sam Lee", sent_days_ago=10, stage="contacted")  # moved company, same name
    opp(s, "x1", "other", "Maria", sent_days_ago=5)                                           # first name only: not her
    opp(s, "y1", "yarrow", "M. Lopez", email="MARIA@ardent.example", sent_days_ago=90)       # same email, long ago
    rel = REL.related(s, o, TODAY)
    why = {x["id"]: x["why"] for x in rel["other_opportunities"]}
    assert why == {"a2": ["same company"], "k1": ["same person (matched by name)"], "y1": ["same person (matched by email)"]}
    assert [x["id"] for x in rel["same_person_emailed_recently"]] == ["k1"]  # y1 was 90 days ago
    w = REL.warning(rel)
    assert "(same name; check it is the same person) was already emailed 10 days ago by Sam Lee" in w and "stage contacted" in w
    assert all("id" not in x for x in REL.for_agent(s, o, TODAY))


def test_nothing_related_means_no_warning():
    s = db()
    o = opp(s, "a1", "ardent", None)
    assert REL.related(s, o, TODAY) == {"other_opportunities": [], "same_person_emailed_recently": []}
    assert REL.warning(REL.related(s, o, TODAY)) == ""


def test_endpoint_and_the_outreach_brief(tmp_path):
    app = create_app(Settings(app_mode="demo", db_path=tmp_path / "w.sqlite3"))
    api = TestClient(app)
    me = {"X-User": "Alex Morgan"}
    from sqlalchemy import select
    with app.state.db.session() as s:
        o = next(x for x in s.scalars(select(m.Opportunity)) if x.contact and x.contact.name and len(x.contact.name.split()) >= 2)
        oid, name, company_id = o.id, o.contact.name, o.company_id
        s.add(m.Company(id="elsewhere", name="Elsewhere Ltd"))
        s.flush()
        s.add(m.Contact(id="ct-else", company_id="elsewhere", name=name, title="COO"))
        s.flush()
        s.add(m.Opportunity(id="else-1", company_id="elsewhere", contact_id="ct-else", title="Other work", stage="contacted", owner="Sam Lee"))
        s.commit()
    body = api.get(f"/api/opportunities/{oid}/related", headers=me).json()
    assert "else-1" in [x["id"] for x in body["other_opportunities"]] and body["warning"] is None  # never emailed: no warning
    from api import outreach_agent as OA
    from api.autonomy import agent_ctx
    with app.state.db.session() as s:
        c = agent_ctx(app, s)
        spec, _ = OA.outreach_agent(c, s.get(m.Opportunity, oid), purpose="first", angle=None, tone="standard", sender="Alex Morgan")
        brief = next(t for t in spec.tools if t.name == "brief").run({})
    assert any(x["title"] == "Other work" for x in brief["other_cgs_opportunities"])
    assert api.get("/api/opportunities/nope/related", headers=me).status_code == 404
