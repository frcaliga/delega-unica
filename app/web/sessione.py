"""Sessione dell'interfaccia web: cookie con il token di accesso e protezione CSRF.

Il cookie di sessione contiene lo stesso token delle API (scopo "access"), quindi valgono le
stesse regole: utente attivo, studio corrispondente e secondo fattore completato. Il token dura
`access_token_minutes` e si rinnova a ogni pagina, cosi' la sessione scade dopo quel tempo di
inattivita'.
"""

import hmac
import secrets
import uuid
from datetime import UTC, datetime

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Role, User
from app.security import create_token, decode_token

COOKIE_SESSIONE = "du_sessione"
# Token "mfa" tra la password e il codice TOTP (o la configurazione del TOTP)
COOKIE_MFA = "du_mfa"
COOKIE_CSRF = "du_csrf"
HEADER_CSRF = "X-CSRF-Token"
CAMPO_CSRF = "csrf"
PREFISSO = "/studio"


class NonAutenticato(Exception):
    """Sessione assente o scaduta: si torna alla pagina di accesso."""


def imposta_cookie(response: Response, nome: str, valore: str, minuti: int) -> None:
    response.set_cookie(
        nome,
        valore,
        max_age=minuti * 60,
        path=PREFISSO,
        httponly=True,
        secure=True,
        samesite="strict",
    )


def cancella_cookie(response: Response, nome: str) -> None:
    response.delete_cookie(nome, path=PREFISSO, httponly=True, secure=True, samesite="strict")


def apri_sessione(response: Response, user: User, mfa: bool) -> None:
    token = create_token(user.id, user.tenant_id, "access", mfa=mfa)
    imposta_cookie(response, COOKIE_SESSIONE, token, get_settings().access_token_minutes)
    cancella_cookie(response, COOKIE_MFA)


def utente_web(request: Request, db: Session = Depends(get_db)) -> User:
    """Operatore autenticato con password e secondo fattore."""
    token = request.cookies.get(COOKIE_SESSIONE)
    if not token:
        raise NonAutenticato
    try:
        payload = decode_token(token, "access")
    except jwt.InvalidTokenError as exc:
        raise NonAutenticato from exc
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.active or str(user.tenant_id) != payload["tid"]:
        raise NonAutenticato
    if get_settings().require_mfa and not payload.get("mfa"):
        raise NonAutenticato
    # Rinnovo della sessione quando e' passato almeno un minuto dall'ultimo
    if datetime.now(UTC).timestamp() - payload["iat"] > 60:
        request.state.rinnova_sessione = create_token(
            user.id, user.tenant_id, "access", mfa=bool(payload.get("mfa"))
        )
    return user


def utente_mfa(request: Request, db: Session = Depends(get_db)) -> User:
    """Utente che ha superato la password e deve inserire o configurare il codice TOTP."""
    token = request.cookies.get(COOKIE_MFA)
    if not token:
        raise NonAutenticato
    try:
        payload = decode_token(token, "mfa")
    except jwt.InvalidTokenError as exc:
        raise NonAutenticato from exc
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.active:
        raise NonAutenticato
    return user


def richiedi_ruolo(user: User, *ruoli: Role) -> None:
    if user.role not in ruoli:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Permessi insufficienti")


def token_csrf(request: Request) -> str:
    """Token del modulo: uguale al cookie CSRF (double submit). Lo crea se manca."""
    token = request.cookies.get(COOKIE_CSRF) or getattr(request.state, "nuovo_csrf", None)
    if not token:
        token = secrets.token_urlsafe(32)
        request.state.nuovo_csrf = token
    return token


async def verifica_csrf(request: Request) -> None:
    """Le richieste che modificano dati devono riportare il token CSRF del cookie."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    atteso = request.cookies.get(COOKIE_CSRF)
    inviato = request.headers.get(HEADER_CSRF)
    if not inviato:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            valore = (await request.form()).get(CAMPO_CSRF)
            inviato = valore if isinstance(valore, str) else None
    if not atteso or not inviato or not hmac.compare_digest(atteso, inviato):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Sessione del modulo scaduta: ricaricare la pagina e riprovare",
        )
