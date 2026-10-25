import pyotp
from sqlalchemy import select

from app.models import AuditLog
from tests.conftest import PASSWORD


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_wrong_password_is_rejected_and_audited(client, make_user, db):
    user = make_user("anna@studio.it")
    res = client.post("/auth/login", json={"email": user.email, "password": "sbagliata"})
    assert res.status_code == 401
    actions = db.scalars(select(AuditLog.action)).all()
    assert actions == ["login_failed"]


def test_unknown_email_is_rejected(client):
    res = client.post("/auth/login", json={"email": "nessuno@studio.it", "password": PASSWORD})
    assert res.status_code == 401


def test_without_totp_only_enrollment_is_allowed(client, make_user, login):
    user = make_user("anna@studio.it")
    headers = login(user)
    assert client.get("/auth/me", headers=headers).status_code == 200
    res = client.get("/users", headers=headers)
    assert res.status_code == 403
    assert "Secondo fattore" in res.json()["detail"]


def test_totp_enrollment_unlocks_the_api(client, make_user):
    user = make_user("anna@studio.it")
    res = client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    assert res.json()["mfa_setup_required"] is True
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    secret = client.post("/auth/totp/setup", headers=headers).json()["secret"]
    bad = client.post("/auth/totp/confirm", headers=headers, json={"code": "000000"})
    assert bad.status_code == 400
    res = client.post(
        "/auth/totp/confirm", headers=headers, json={"code": pyotp.TOTP(secret).now()}
    )
    assert res.status_code == 200
    full = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert client.get("/users", headers=full).status_code == 200

    # Al login successivo serve il codice TOTP
    res = client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    assert res.json()["mfa_required"] is True
    assert res.json()["access_token"] is None


def test_mfa_login_rejects_wrong_code(client, make_user):
    user = make_user("anna@studio.it", totp=True)
    res = client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    mfa_token = res.json()["mfa_token"]
    res = client.post("/auth/login/mfa", json={"mfa_token": mfa_token, "code": "000000"})
    assert res.status_code == 401


def test_mfa_token_cannot_be_used_as_access_token(client, make_user):
    user = make_user("anna@studio.it", totp=True)
    res = client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    headers = {"Authorization": f"Bearer {res.json()['mfa_token']}"}
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_tampered_token_is_rejected(client, make_user, login):
    headers = login(make_user("anna@studio.it", totp=True))
    headers["Authorization"] += "x"
    assert client.get("/auth/me", headers=headers).status_code == 401
