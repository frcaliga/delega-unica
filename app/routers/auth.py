import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_db
from app.deps import AuthContext, get_auth
from app.models import User
from app.models.base import utcnow
from app.schemas import (
    LoginRequest,
    MfaLoginRequest,
    TokenResponse,
    TotpConfirmRequest,
    TotpSetupResponse,
    UserOut,
)
from app.security import (
    create_token,
    decode_token,
    hash_password,
    new_totp_secret,
    totp_uri,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash fittizio per rendere uniforme il tempo di risposta quando l'email non esiste
_DUMMY_HASH = hash_password("password-fittizia-per-timing")


def _access_token(user: User, mfa: bool) -> TokenResponse:
    return TokenResponse(
        access_token=create_token(user.id, user.tenant_id, "access", mfa=mfa),
        mfa_setup_required=get_settings().require_mfa and not user.totp_enabled,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    email = body.email.lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    valid = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not valid or not user.active:
        audit.record(
            db,
            "login_failed",
            user=user,
            request=request,
            details={"email": email},
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Credenziali non valide")

    if user.totp_enabled:
        return TokenResponse(
            mfa_required=True, mfa_token=create_token(user.id, user.tenant_id, "mfa")
        )

    user.last_login_at = utcnow()
    audit.record(db, "login", user=user, request=request, details={"mfa": False})
    db.commit()
    return _access_token(user, mfa=False)


@router.post("/login/mfa", response_model=TokenResponse)
def login_mfa(
    body: MfaLoginRequest, request: Request, db: Session = Depends(get_db)
) -> TokenResponse:
    try:
        payload = decode_token(body.mfa_token, "mfa")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sessione scaduta") from exc
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.active or not user.totp_enabled or not user.totp_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Credenziali non valide")
    if not verify_totp(user.totp_secret, body.code):
        audit.record(db, "login_mfa_failed", user=user, request=request)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Codice non valido")

    user.last_login_at = utcnow()
    audit.record(db, "login", user=user, request=request, details={"mfa": True})
    db.commit()
    return _access_token(user, mfa=True)


@router.post("/totp/setup", response_model=TotpSetupResponse)
def totp_setup(
    request: Request, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)
) -> TotpSetupResponse:
    user = auth.user
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Secondo fattore gia' attivo")
    user.totp_secret = new_totp_secret()
    audit.record(db, "totp_setup_started", user=user, request=request)
    db.commit()
    return TotpSetupResponse(
        secret=user.totp_secret, otpauth_uri=totp_uri(user.totp_secret, user.email)
    )


@router.post("/totp/confirm", response_model=TokenResponse)
def totp_confirm(
    body: TotpConfirmRequest,
    request: Request,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
) -> TokenResponse:
    user = auth.user
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Secondo fattore gia' attivo")
    if not user.totp_secret or not verify_totp(user.totp_secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Codice non valido")
    user.totp_enabled = True
    audit.record(db, "totp_enabled", user=user, request=request)
    db.commit()
    return _access_token(user, mfa=True)


@router.get("/me", response_model=UserOut)
def me(auth: AuthContext = Depends(get_auth)) -> User:
    return auth.user
