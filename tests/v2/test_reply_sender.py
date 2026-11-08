"""Who wrote a client's reply (api/senders.py). Found in the real Gmail check of 8 Nov 2026: a reply from someone other than
the contact was shown and noted as the contact's. Now the reply names its real writer, says when it is not the contact,
keeps the writer in the notes the agents read, and lets a person add the writer as a contact at the company."""
from api import models as m
from api import senders as SND
from tests.v2.test_crm import ME, get, post
from tests.v2.test_undo import TO, approve, ws  # noqa: F401  (ws is a fixture)

AUBREY = '"Reyes, Sam" <sam.reyes@example.edu>'


def test_the_from_line_is_read_the_way_people_write_it():
    assert SND.parse(AUBREY) == {"name": "Sam Reyes", "email": "sam.reyes@example.edu"}
    assert SND.parse("Priya Raman <PRaman@Halvorsen.example>") == {"name": "Priya Raman", "email": "praman@halvorsen.example"}
    assert SND.parse("praman@halvorsen.example") == {"name": "", "email": "praman@halvorsen.example"}
    assert SND.same_name("Raman,  Priya".replace(",", ""), "raman priya") and not SND.same_name("Priya", "Priya")


def sent_thread(app, api, oid):
    approve(api, oid, delay=0)
    with app.state.db.session() as s:
        return s.get(m.Outreach, oid).mail_thread_id


def reply_from(app, api, oid, sender, text="Thanks. Our Ohio plant has no shared production data; the COO decides by March."):
    thread = sent_thread(app, api, oid)
    app.state.mail.deliver_reply(thread, sender, text)
    assert post(api, "/api/mail/sync")["replies"] == 1
    return next(i for i in api.get("/api/inbox?owner=Alex%20Morgan", headers=ME).json() if i["opportunityId"] == oid and i["kind"] == "reply")


def contact(app, oid):
    with app.state.db.session() as s:
        o = s.get(m.Opportunity, oid)
        return o.contact.name, o.contact.email


def test_a_reply_from_the_contact_is_the_contacts(ws):
    app, api, oid = ws
    name, _ = contact(app, oid)
    item = reply_from(app, api, oid, f"{name} <{TO.upper()}>")  # the address the email went to, now on file
    assert contact(app, oid)[1] == TO
    assert item["data"]["sender"]["isContact"] and item["title"] == f"Email reply from {name} (Halvorsen Precision Castings)"
    post(api, f"/api/inbox/{item['id']}", {"action": "apply", "payload_hash": item["payloadHash"]})
    note = next(n for n in get(api, oid)["notes"] if n["type"] == "Email reply")
    assert not note["text"].startswith("[From")  # the client's words alone


def test_a_reply_from_someone_else_says_so_everywhere(ws):
    app, api, oid = ws
    name, _ = contact(app, oid)
    item = reply_from(app, api, oid, AUBREY)
    s = item["data"]["sender"]
    assert s == {"name": "Sam Reyes", "email": "sam.reyes@example.edu", "contact": name, "isContact": False, "knownAs": None, "sameAddress": False}
    assert item["title"] == f"Email reply from Sam Reyes, not {name} (Halvorsen Precision Castings)"
    todo = [it["title"] for g in api.get("/api/todo", headers=ME).json()["groups"] for it in g["items"]]
    assert item["title"] in todo  # the home page names the real writer too
    res = post(api, f"/api/inbox/{item['id']}", {"action": "apply", "payload_hash": item["payloadHash"]})
    assert res["note"].endswith(f"It came from Sam Reyes, not {name}")
    o = get(api, oid)
    note = next(n for n in o["notes"] if n["type"] == "Email reply")
    assert note["text"].startswith(f"[From Sam Reyes <sam.reyes@example.edu>, not {name} (the contact on file).]\n")
    assert note["text"].endswith("the COO decides by March.")  # the client's words follow, unchanged
    assert o["stage"] == "active" and o["contact"]["name"] == name  # the lead's contact stays as it was
    assert any(f"The reply came from Sam Reyes <sam.reyes@example.edu>, not from {name}." in a["text"] for a in o["activity"])


def test_the_writer_can_be_added_as_a_contact_once(ws):
    app, api, oid = ws
    item = reply_from(app, api, oid, AUBREY)
    added = post(api, f"/api/inbox/{item['id']}/add-sender")
    assert added["created"] and added["name"] == "Sam Reyes" and added["email"] == "sam.reyes@example.edu"
    assert post(api, f"/api/inbox/{item['id']}/add-sender")["created"] is False  # never twice
    with app.state.db.session() as s:
        ct = s.get(m.Contact, added["contactId"])
        assert ct.company_id == "halvorsen" and ct.title == "Contact to confirm" and ct.confirmed_by == "Alex Morgan"
        assert "From line" in ct.confirmed_note
    item = next(i for i in api.get("/api/inbox?owner=Alex%20Morgan", headers=ME).json() if i["id"] == item["id"])
    assert item["data"]["sender"]["knownAs"] == "Sam Reyes"
    post(api, f"/api/inbox/{item['id']}", {"action": "apply", "payload_hash": item["payloadHash"]})  # still the version they saw
    note = next(n for n in get(api, oid)["notes"] if n["type"] == "Email reply")
    assert "on file as a contact at this company" in note["text"].split("\n")[0]


def test_a_later_reply_from_a_colleague_on_file_is_named_as_them(ws):
    app, api, oid = ws
    first = reply_from(app, api, oid, AUBREY)
    post(api, f"/api/inbox/{first['id']}/add-sender")
    post(api, f"/api/inbox/{first['id']}", {"action": "apply", "payload_hash": first["payloadHash"]})  # now Active: a reply is evidence
    with app.state.db.session() as s:
        thread = s.get(m.Outreach, oid).mail_thread_id
    app.state.mail.deliver_reply(thread, AUBREY, "We can share the capacity data next week.")
    post(api, "/api/mail/sync")
    later = next(i for i in api.get("/api/inbox?owner=Alex%20Morgan", headers=ME).json() if i["kind"] == "reply" and i["status"] == "new")
    assert later["data"]["sender"]["knownAs"] == "Sam Reyes" and not later["data"]["sender"]["isContact"]


def test_same_name_from_another_address_is_not_taken_for_the_contact(ws):
    app, api, oid = ws
    name, _ = contact(app, oid)
    item = reply_from(app, api, oid, f"{name} <someone.else@gmail.example>")  # the contact's address is on file: it decides
    assert item["data"]["sender"]["isContact"] is False


def test_with_no_address_on_file_the_names_decide():
    o = m.Opportunity(company_id="x", contact=m.Contact(name="Priya Raman", email=None))
    assert SND.identify(None, o, '"Raman, Priya" <p@x.example>')["isContact"]
    assert not SND.identify(_NoSession(), o, "Priya <p@x.example>")["isContact"]  # a first name alone is not enough


class _NoSession:
    def scalars(self, *_):
        return self

    def first(self):
        return None


def test_signed_by_someone_else_from_the_contacts_own_address(ws):
    """The real Gmail check: the email went to an address now on file for the contact, and the reply from that address
    was signed by someone else. Not the contact's words, and no second contact with the same address."""
    app, api, oid = ws
    name, _ = contact(app, oid)
    item = reply_from(app, api, oid, f'"Reyes, Sam" <{TO}>')
    s = item["data"]["sender"]
    assert s["sameAddress"] and not s["isContact"] and item["title"] == f"Email reply from Sam Reyes, not {name} (Halvorsen Precision Castings)"
    r = api.post(f"/api/inbox/{item['id']}/add-sender", headers=ME)
    assert r.status_code == 409 and r.json()["code"] == "same_address"
    res = post(api, f"/api/inbox/{item['id']}", {"action": "apply", "payload_hash": item["payloadHash"]})
    assert "check that address" in res["note"]
    note = next(n for n in get(api, oid)["notes"] if n["type"] == "Email reply")
    assert note["text"].startswith(f"[From Sam Reyes <{TO}>: the address on file for {name}, but signed Sam Reyes.]")


def test_a_short_or_missing_name_on_the_contacts_address_is_the_contact(ws):
    app, api, oid = ws
    item = reply_from(app, api, oid, f"Dana <{TO}>")  # a first name alone proves nothing
    assert item["data"]["sender"]["isContact"]


def test_the_contact_on_file_cannot_be_added_again(ws):
    app, api, oid = ws
    name, _ = contact(app, oid)
    item = reply_from(app, api, oid, f"{name} <{TO}>")
    r = api.post(f"/api/inbox/{item['id']}/add-sender", headers=ME)
    assert r.status_code == 409 and r.json()["code"] == "is_contact"
