import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
import pyotp
from pwdlib import PasswordHash

from app.config import get_settings

ALGORITHM = "HS256"
TokenPurpose = Literal["access", "mfa"]

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def create_token(
    user_id: uuid.UUID, tenant_id: uuid.UUID, purpose: TokenPurpose, mfa: bool = False
) -> str:
    settings = get_settings()
    minutes = settings.access_token_minutes if purpose == "access" else settings.mfa_token_minutes
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "purpose": purpose,
        "mfa": mfa,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str, purpose: TokenPurpose) -> dict:
    """Solleva jwt.InvalidTokenError se il token non e' valido o ha uno scopo diverso."""
    payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("scopo del token non valido")
    return payload


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=get_settings().totp_issuer)


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)
