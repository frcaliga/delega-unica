import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models import AuditLog, Role

NEW_USER = {
    "email": "nuovo@studio.it",
    "full_name": "Nuovo Operatore",
    "password": "una-password-lunga",
    "role": "operatore",
}


def test_admin_creates_user_and_it_is_audited(client, make_user, login, db):
    admin = make_user("admin@studio.it", totp=True)
    res = client.post("/users", headers=login(admin), json=NEW_USER)
    assert res.status_code == 201
    assert res.json()["tenant_id"] == str(admin.tenant_id)
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "user_created"))
    assert entry.user_id == admin.id
    assert entry.details["email"] == NEW_USER["email"]


def test_duplicate_email_is_rejected(client, make_user, login):
    admin = make_user("admin@studio.it", totp=True)
    headers = login(admin)
    assert client.post("/users", headers=headers, json=NEW_USER).status_code == 201
    dup = {**NEW_USER, "email": NEW_USER["email"].upper()}
    assert client.post("/users", headers=headers, json=dup).status_code == 409


def test_operator_cannot_create_users_or_read_audit(client, make_user, login):
    op = make_user("op@studio.it", role=Role.OPERATORE, totp=True)
    headers = login(op)
    assert client.post("/users", headers=headers, json=NEW_USER).status_code == 403
    assert client.get("/audit", headers=headers).status_code == 403


def test_tenants_are_isolated(client, make_user, login):
    admin_a = make_user("a@studio-a.it", tenant_cf="11111111111", totp=True)
    make_user("collega@studio-a.it", role=Role.OPERATORE, tenant_cf="11111111111")
    admin_b = make_user("b@studio-b.it", tenant_cf="22222222222", totp=True)

    headers_a, headers_b = login(admin_a), login(admin_b)
    emails_a = {u["email"] for u in client.get("/users", headers=headers_a).json()}
    emails_b = {u["email"] for u in client.get("/users", headers=headers_b).json()}
    assert emails_a == {"a@studio-a.it", "collega@studio-a.it"}
    assert emails_b == {"b@studio-b.it"}

    audit_b = client.get("/audit", headers=headers_b).json()
    assert audit_b and all(e["user_id"] == str(admin_b.id) for e in audit_b)
    assert client.get("/tenant", headers=headers_b).json()["codice_fiscale"] == "22222222222"


def test_audit_log_is_append_only(make_user, login, db):
    login(make_user("admin@studio.it", totp=True))
    with pytest.raises(DBAPIError, match="sola aggiunta"):
        db.execute(text("UPDATE audit_log SET action = 'manomesso'"))
    db.rollback()
