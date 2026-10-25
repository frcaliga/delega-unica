"""Codici OTP monouso inviati via SMS, legati a un documento tramite la sua impronta SHA-256.

Il codice non viene salvato in chiaro: si conserva solo un HMAC con la chiave dell'applicazione.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import clock
from app.config import get_settings
from app.models.fea import ScopoOtp, SessioneOtp


class OtpNonValido(Exception):
    pass


def _impronta(sessione_id: uuid.UUID, codice: str) -> str:
    chiave = get_settings().secret_key.encode()
    return hmac.new(chiave, f"{sessione_id}:{codice}".encode(), hashlib.sha256).hexdigest()


def crea(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    scopo: ScopoOtp,
    riferimento_id: uuid.UUID,
    cellulare: str,
    sha256_documento: str,
) -> tuple[SessioneOtp, str]:
    """Nuova sessione OTP; annulla quelle ancora aperte per lo stesso riferimento."""
    db.execute(
        update(SessioneOtp)
        .where(
            SessioneOtp.riferimento_id == riferimento_id,
            SessioneOtp.scopo == scopo,
            SessioneOtp.usata_il.is_(None),
            SessioneOtp.annullata.is_(False),
        )
        .values(annullata=True)
    )
    codice = f"{secrets.randbelow(10**6):06d}"
    sessione = SessioneOtp(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        scopo=scopo,
        riferimento_id=riferimento_id,
        cellulare=cellulare,
        sha256_documento=sha256_documento,
        scade_il=clock.adesso() + timedelta(minutes=get_settings().otp_minuti),
    )
    sessione.codice_hmac = _impronta(sessione.id, codice)
    db.add(sessione)
    return sessione, codice


def verifica(
    db: Session, *, scopo: ScopoOtp, riferimento_id: uuid.UUID, codice: str, sha256_documento: str
) -> SessioneOtp:
    """Consuma la sessione se il codice e' giusto. Un tentativo errato viene contato: il
    chiamante deve fare commit anche quando riceve OtpNonValido."""
    sessione = db.scalar(
        select(SessioneOtp)
        .where(
            SessioneOtp.riferimento_id == riferimento_id,
            SessioneOtp.scopo == scopo,
            SessioneOtp.usata_il.is_(None),
            SessioneOtp.annullata.is_(False),
        )
        .order_by(SessioneOtp.created_at.desc())
        .with_for_update()
    )
    if sessione is None:
        raise OtpNonValido("Nessun codice in attesa: richiederne uno nuovo")
    if clock.adesso() > sessione.scade_il:
        raise OtpNonValido("Il codice e' scaduto: richiederne uno nuovo")
    if sessione.tentativi >= get_settings().otp_tentativi:
        raise OtpNonValido("Troppi tentativi errati: richiedere un nuovo codice")
    if sessione.sha256_documento != sha256_documento:
        sessione.annullata = True
        raise OtpNonValido("Il documento e' cambiato dopo l'invio del codice: richiederlo")
    if not hmac.compare_digest(sessione.codice_hmac, _impronta(sessione.id, codice.strip())):
        sessione.tentativi += 1
        raise OtpNonValido("Codice errato")
    sessione.usata_il = clock.adesso()
    return sessione
