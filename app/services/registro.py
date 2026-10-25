"""Registro cronologico delle deleghe: numerazione progressiva per studio e catena di hash."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.regole import DESCRIZIONE_SERVIZI, Servizio
from app.models import Delega, Tenant, User, VoceRegistro

TIPI_DOCUMENTO = {"1": "Carta d'identita'", "2": "Passaporto", "3": "Patente", "4": "Altro"}


def estremi_documento(delega: Delega) -> str | None:
    if not delega.ha_documento:
        return None
    tipo = delega.documento_tipo_altro or TIPI_DOCUMENTO[delega.documento_tipo.value]
    scadenza = delega.documento_scadenza.strftime("%d/%m/%Y") if delega.documento_scadenza else ""
    return (
        f"{tipo} n. {delega.documento_numero}, rilasciato da {delega.documento_rilasciato_da}, "
        f"scadenza {scadenza}"
    )


def calcola_hash(voce: VoceRegistro) -> str:
    contenuto = {
        "tenant_id": str(voce.tenant_id),
        "numero": voce.numero,
        "data": voce.data.isoformat(),
        "delega_id": str(voce.delega_id),
        "tipo_richiesta": voce.tipo_richiesta.value,
        "delegante_cf": voce.delegante_cf,
        "delegante_nominativo": voce.delegante_nominativo,
        "sottoscrittore_cf": voce.sottoscrittore_cf,
        "sottoscrittore_nominativo": voce.sottoscrittore_nominativo,
        "documento_estremi": voce.documento_estremi,
        "servizi": voce.servizi,
        "user_id": str(voce.user_id),
        "hash_precedente": voce.hash_precedente,
    }
    canonico = json.dumps(contenuto, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode()).hexdigest()


def annota(db: Session, delega: Delega, utente: User, data: date) -> VoceRegistro:
    """Aggiunge la delega al registro. Il commit resta al chiamante."""
    # Blocca la riga dello studio: due annotazioni contemporanee non prendono lo stesso numero
    db.execute(select(Tenant.id).where(Tenant.id == delega.tenant_id).with_for_update())
    ultima = db.scalar(
        select(VoceRegistro)
        .where(VoceRegistro.tenant_id == delega.tenant_id)
        .order_by(VoceRegistro.numero.desc())
        .limit(1)
    )
    voce = VoceRegistro(
        tenant_id=delega.tenant_id,
        numero=(ultima.numero + 1) if ultima else 1,
        data=data,
        delega_id=delega.id,
        tipo_richiesta=delega.tipo_richiesta,
        delegante_cf=delega.cliente.codice_fiscale,
        delegante_nominativo=delega.cliente.nominativo,
        sottoscrittore_cf=delega.sottoscrittore_cf,
        sottoscrittore_nominativo=f"{delega.sottoscrittore_cognome} {delega.sottoscrittore_nome}",
        documento_estremi=estremi_documento(delega),
        servizi=sorted(delega.servizi),
        user_id=utente.id,
        hash_precedente=ultima.hash if ultima else None,
    )
    voce.hash = calcola_hash(voce)
    db.add(voce)
    return voce


@dataclass
class EsitoVerifica:
    integro: bool
    voci_controllate: int
    primo_errore: str | None = None


def verifica(db: Session, tenant_id) -> EsitoVerifica:
    """Ricalcola la catena di hash e controlla che la numerazione non abbia buchi."""
    voci = db.scalars(
        select(VoceRegistro)
        .where(VoceRegistro.tenant_id == tenant_id)
        .order_by(VoceRegistro.numero)
    ).all()
    precedente = None
    for atteso, voce in enumerate(voci, start=1):
        if voce.numero != atteso:
            return EsitoVerifica(False, atteso - 1, f"Manca la voce numero {atteso}")
        if voce.hash_precedente != precedente or calcola_hash(voce) != voce.hash:
            return EsitoVerifica(False, atteso - 1, f"La voce numero {atteso} e' stata alterata")
        precedente = voce.hash
    return EsitoVerifica(True, len(voci))


def descrizione_servizi(codici: list[str]) -> str:
    return "; ".join(f"{c} {DESCRIZIONE_SERVIZI[Servizio(c)]}" for c in sorted(codici))
