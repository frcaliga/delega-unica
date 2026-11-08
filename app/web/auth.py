"""Accesso all'interfaccia web: password, poi codice TOTP (o sua configurazione)."""

import io

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.models.base import utcnow
from app.routers.auth import _DUMMY_HASH
from app.security import (
    create_token,
    new_totp_secret,
    totp_uri,
    verify_password,
    verify_totp,
)
from app.web.base import pagina, vai
from app.web.sessione import (
    COOKIE_MFA,
    COOKIE_SESSIONE,
    apri_sessione,
    cancella_cookie,
    imposta_cookie,
    utente_mfa,
)

router = APIRouter()


@router.get("/accesso", response_class=HTMLResponse)
def pagina_accesso(request: Request) -> Response:
    return pagina(request, "accesso.html")


@router.post("/accesso", response_class=HTMLResponse)
def accesso(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    email = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email)) if email else None
    valida = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not valida or not user.active:
        audit.record(db, "login_failed", user=user, request=request, details={"email": email})
        db.commit()
        return pagina(
            request,
            "accesso.html",
            {"errori": ["Email o password non validi"], "email": email},
            status_code=401,
        )

    settings = get_settings()
    if user.totp_enabled or settings.require_mfa:
        if not user.totp_enabled and not user.totp_secret:
            user.totp_secret = new_totp_secret()
            audit.record(db, "totp_setup_started", user=user, request=request)
            db.commit()
        response = vai("/studio/codice" if user.totp_enabled else "/studio/configura-codice")
        token = create_token(user.id, user.tenant_id, "mfa")
        imposta_cookie(response, COOKIE_MFA, token, settings.mfa_token_minutes)
        return response

    # Secondo fattore non richiesto dalla configurazione e non attivato dall'utente
    user.last_login_at = utcnow()
    audit.record(db, "login", user=user, request=request, details={"mfa": False, "web": True})
    db.commit()
    response = vai("/studio")
    apri_sessione(response, user, mfa=False)
    return response


@router.get("/codice", response_class=HTMLResponse)
def pagina_codice(request: Request, user: User = Depends(utente_mfa)) -> Response:
    if not user.totp_enabled:
        return vai("/studio/configura-codice")
    return pagina(request, "codice.html", {"utente_mfa": user})


@router.post("/codice", response_class=HTMLResponse)
def codice(
    request: Request,
    code: str = Form(""),
    user: User = Depends(utente_mfa),
    db: Session = Depends(get_db),
) -> Response:
    if not user.totp_enabled or not user.totp_secret:
        return vai("/studio/configura-codice")
    if not verify_totp(user.totp_secret, code.strip()):
        audit.record(db, "login_mfa_failed", user=user, request=request)
        db.commit()
        return pagina(
            request,
            "codice.html",
            {"utente_mfa": user, "errori": ["Codice non valido"]},
            status_code=401,
        )
    user.last_login_at = utcnow()
    audit.record(db, "login", user=user, request=request, details={"mfa": True, "web": True})
    db.commit()
    response = vai("/studio")
    apri_sessione(response, user, mfa=True)
    return response


def _qr_svg(testo: str) -> str:
    immagine = qrcode.make(testo, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    buffer = io.BytesIO()
    immagine.save(buffer)
    svg = buffer.getvalue().decode()
    # Rimuove la dichiarazione XML per incorporare l'SVG nella pagina
    return svg[svg.index("<svg") :]


def _pagina_configura(request: Request, user: User, **extra) -> Response:
    assert user.totp_secret is not None
    uri = totp_uri(user.totp_secret, user.email)
    contesto = {"utente_mfa": user, "segreto": user.totp_secret, "qr": _qr_svg(uri), **extra}
    return pagina(request, "configura_codice.html", contesto)


@router.get("/configura-codice", response_class=HTMLResponse)
def pagina_configura_codice(request: Request, user: User = Depends(utente_mfa)) -> Response:
    if user.totp_enabled:
        return vai("/studio/codice")
    if not user.totp_secret:
        return vai("/studio/accesso")
    return _pagina_configura(request, user)


@router.post("/configura-codice", response_class=HTMLResponse)
def configura_codice(
    request: Request,
    code: str = Form(""),
    user: User = Depends(utente_mfa),
    db: Session = Depends(get_db),
) -> Response:
    if user.totp_enabled:
        return vai("/studio/codice")
    if not user.totp_secret or not verify_totp(user.totp_secret, code.strip()):
        response = _pagina_configura(request, user, errori=["Codice non valido"])
        response.status_code = 422
        return response
    user.totp_enabled = True
    user.last_login_at = utcnow()
    audit.record(db, "totp_enabled", user=user, request=request)
    audit.record(db, "login", user=user, request=request, details={"mfa": True, "web": True})
    db.commit()
    response = vai("/studio")
    apri_sessione(response, user, mfa=True)
    return response


@router.post("/esci")
def esci(request: Request) -> Response:
    response = vai("/studio/accesso")
    cancella_cookie(response, COOKIE_SESSIONE)
    cancella_cookie(response, COOKIE_MFA)
    return response
