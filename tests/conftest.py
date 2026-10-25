import os

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://delega:delega@localhost:5432/delega_test"
    ),
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789-0123456789")
os.environ.setdefault("REQUIRE_MFA", "true")

from collections.abc import Callable, Iterator  # noqa: E402

import pyotp  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, Tenant, User  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "password-di-prova-123"


@pytest.fixture(scope="session", autouse=True)
def _migrated_db() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def _clean_db() -> Iterator[None]:
    yield
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE registro_deleghe, deleghe, clienti, audit_log, users, tenants CASCADE")
        )


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    tenants: dict[str, Tenant] = {}

    def _make(
        email: str,
        role: Role = Role.ADMIN,
        tenant_cf: str = "01234567890",
        totp: bool = False,
    ) -> User:
        tenant = tenants.get(tenant_cf)
        if tenant is None:
            tenant = Tenant(name=f"Studio {tenant_cf}", codice_fiscale=tenant_cf)
            db.add(tenant)
            db.flush()
            tenants[tenant_cf] = tenant
        user = User(
            tenant_id=tenant.id,
            email=email,
            full_name=email.split("@")[0],
            password_hash=hash_password(PASSWORD),
            role=role,
            totp_secret=pyotp.random_base32() if totp else None,
            totp_enabled=totp,
        )
        db.add(user)
        db.commit()
        return user

    return _make


@pytest.fixture
def login(client: TestClient) -> Callable[[User], dict[str, str]]:
    """Login completo (password + TOTP se attivo); restituisce gli header di autorizzazione."""

    def _login(user: User) -> dict[str, str]:
        res = client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
        assert res.status_code == 200, res.text
        body = res.json()
        if body["mfa_required"]:
            code = pyotp.TOTP(user.totp_secret).now()
            res = client.post(
                "/auth/login/mfa", json={"mfa_token": body["mfa_token"], "code": code}
            )
            assert res.status_code == 200, res.text
            body = res.json()
        return {"Authorization": f"Bearer {body['access_token']}"}

    return _login
