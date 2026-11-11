"""Google sign-in for a hosted workspace (api/login.py). The tests sign their own notes with a key made here, the way
Google's Identity-Aware Proxy signs them, so the real checking code runs: signature, audience, expiry, issuer, email."""
import time

import pytest

pytest.importorskip("google.auth", reason="hosted sign-in needs requirements-cloud.txt")
pytest.importorskip("cryptography", reason="hosted sign-in needs requirements-cloud.txt")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from google.auth import crypt, jwt

from api.login import HEADER, ISSUER, GoogleLogin
from api.main import create_app
from api.settings import Settings, load_settings, people

AUD = "/projects/123/locations/us-east1/services/cgs-workspace"
ALEX, SARAH = "alex@example.com", "sarah@example.com"
LOGINS = ((ALEX, "Alex Morgan"), (SARAH, "Sarah Patel"))


def make_key():
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public.decode()


PRIVATE, PUBLIC = make_key()
OTHER_PRIVATE, _ = make_key()


def note(email=ALEX, aud=AUD, iss=ISSUER, age=0, private=PRIVATE, kid="k1"):
    now = int(time.time()) - age
    signer = crypt.ES256Signer.from_string(private, kid)
    return jwt.encode(signer, {"iss": iss, "aud": aud, "email": email, "iat": now, "exp": now + 600}).decode()


@pytest.fixture
def api(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "login.sqlite3", login="google", iap_audience=AUD, logins=LOGINS))
    app.state.login.fetch = lambda: {"k1": PUBLIC}
    return TestClient(app)


def signed(**kw):
    return {HEADER: note(**kw)}


def test_every_request_needs_a_sign_in_except_the_health_check(api):
    for path in ("/", "/api/config", "/api/users", "/api/opportunities", "/app/main.js"):
        r = api.get(path)
        assert r.status_code == 401 and r.json()["code"] == "sign_in_required", path
    assert api.get("/api/health").status_code == 200
    assert api.get("/api/config", headers=signed()).status_code == 200
    assert api.get("/", headers=signed()).status_code == 200


def test_the_signed_in_person_is_named(api):
    assert api.get("/api/me", headers=signed()).json() == {"login": "google", "name": "Alex Morgan", "email": ALEX}
    assert api.get("/api/me", headers=signed(email="Sarah@Example.com")).json()["name"] == "Sarah Patel"


def test_nobody_can_act_under_another_name(api):
    """The page's X-User is ignored: the change is recorded under the signed-in person."""
    r = api.post("/api/opportunities", json={"company": "Login Test Co"}, headers={**signed(), "X-User": "Sarah Patel"})
    assert r.status_code == 200, r.text
    oid = r.json()["id"]
    detail = api.get(f"/api/opportunities/{oid}", headers=signed()).json()
    assert "Alex Morgan" in str(detail) and "Sarah Patel" not in str(detail)


def test_a_write_without_a_sign_in_is_refused_even_with_a_name(api):
    r = api.post("/api/opportunities", json={"company": "Nobody Co"}, headers={"X-User": "Alex Morgan"})
    assert r.status_code == 401 and r.json()["code"] == "sign_in_required"


@pytest.mark.parametrize("kw, status, code", [
    ({"email": "stranger@example.com"}, 403, "not_a_user"),
    ({"aud": "/projects/999/locations/us-east1/services/another"}, 401, "bad_sign_in"),  # meant for another service
    ({"age": 3600}, 401, "bad_sign_in"),                                                   # expired
    ({"private": OTHER_PRIVATE}, 401, "bad_sign_in"),                                      # not signed by Google's key
    ({"iss": "https://accounts.example.com"}, 401, "bad_sign_in"),
    ({"kid": "unknown"}, 401, "bad_sign_in"),
])
def test_bad_sign_ins_are_refused(api, kw, status, code):
    r = api.get("/api/config", headers=signed(**kw))
    assert (r.status_code, r.json()["code"]) == (status, code), r.text


def test_a_note_that_is_not_a_note_is_refused(api):
    r = api.get("/api/config", headers={HEADER: "not-a-token"})
    assert r.status_code == 401 and r.json()["code"] == "bad_sign_in"


def test_keys_are_fetched_again_when_google_changes_them():
    calls = []
    new_private, new_public = make_key()

    def fetch():
        calls.append(1)
        return {"k1": PUBLIC} if len(calls) == 1 else {"k2": new_public}

    login = GoogleLogin(AUD, dict(LOGINS), fetch)
    assert login.who({HEADER: note()})[0] == "Alex Morgan"
    assert login.who({HEADER: note()})[0] == "Alex Morgan" and len(calls) == 1  # kept, not fetched per request
    assert login.who({HEADER: note(private=new_private, kid="k2")})[0] == "Alex Morgan" and len(calls) == 2


def test_no_request_passes_while_the_keys_cannot_be_fetched(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "down.sqlite3", login="google", iap_audience=AUD, logins=LOGINS))

    def down():
        raise OSError("no network")
    app.state.login.fetch = down
    r = TestClient(app).get("/api/config", headers=signed())
    assert r.status_code == 503 and r.json()["code"] == "sign_in_unavailable"


def test_people_who_can_sign_in_join_an_existing_workspace(tmp_path):
    db = tmp_path / "old.sqlite3"
    create_app(Settings(db_path=db)).state.db.engine.dispose()  # made before sign-in, with the default four people
    app = create_app(Settings(db_path=db, login="google", iap_audience=AUD, logins=LOGINS + (("new@example.com", "New Person"),)))
    app.state.login.fetch = lambda: {"k1": PUBLIC}
    users = TestClient(app).get("/api/users", headers=signed()).json()
    assert "New Person" in users and users.count("Alex Morgan") == 1


def test_without_sign_in_the_workspace_works_as_before(tmp_path):
    api = TestClient(create_app(Settings(db_path=tmp_path / "off.sqlite3")))
    assert api.get("/api/me").json() == {"login": "off", "name": "", "email": ""}
    assert api.get("/api/config").status_code == 200


def test_settings():
    assert people("Alex Morgan <Alex@Example.com>, Sarah Patel ,") == (("Alex Morgan", "Sarah Patel"), (("alex@example.com", "Alex Morgan"),))
    with pytest.raises(ValueError, match="no name"):
        people("<alex@example.com>")
    with pytest.raises(ValueError, match="CGS_IAP_AUDIENCE"):
        Settings(login="google", logins=LOGINS)
    with pytest.raises(ValueError, match="Google email"):
        Settings(login="google", iap_audience=AUD)
    with pytest.raises(ValueError, match="same email"):
        Settings(login="google", iap_audience=AUD, logins=((ALEX, "A"), (ALEX, "B")))
    with pytest.raises(ValueError, match="CGS_LOGIN"):
        Settings(login="password")


def test_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("CGS_LOGIN", "google")
    monkeypatch.setenv("CGS_IAP_AUDIENCE", AUD)
    monkeypatch.setenv("CGS_USERS", "Alex Morgan <alex@example.com>, Sarah Patel <sarah@example.com>, Quinn Harper")
    s = load_settings(env_file=None)
    assert s.login == "google" and s.logins == LOGINS and s.users == ("Alex Morgan", "Sarah Patel", "Quinn Harper")
