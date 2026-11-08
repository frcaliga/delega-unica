"""Letture dei documenti d'identita' in background.

La lettura impegna la CPU per decine di secondi, troppo per una richiesta HTTP dietro il
bilanciatore. La pagina avvia la lettura e ne chiede l'esito ogni pochi secondi. Le letture girano
una alla volta per container, cosi' il sito resta reattivo. L'esito (dati letti e anteprima) e'
cifrato nel database, perche' la richiesta successiva puo' arrivare a un altro container; la riga
si cancella appena l'esito e' consegnato, o dopo un'ora.
"""

import json
import logging
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import clock
from app.db import SessionLocal
from app.models import LetturaDocumento, User
from app.schemas.documenti_identita import PropostaOut
from app.services import documenti_identita as servizio
from app.services.cifratura import Portachiavi

log = logging.getLogger(__name__)

# Oltre questo tempo il motore si ferma e restituisce i dati gia' trovati
TEMPO_MAX = 120.0
# Una lettura ancora "in corso" dopo questo tempo si considera persa (container fermato)
PERSA_DOPO = timedelta(minutes=5)
CONSERVATA_PER = timedelta(hours=1)

_ESECUTORE = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lettura-documento")


def avvia(lavoro: Callable[[], None]) -> None:
    """Mette in coda la lettura. I test lo sostituiscono con un'esecuzione immediata."""
    _ESECUTORE.submit(lavoro)


@dataclass
class Esito:
    stato: str  # in_corso, pronta, errore, assente
    proposta: PropostaOut | None = None
    anteprima: str | None = None
    secondi: int = 0


def _ora() -> datetime:
    return datetime.now(UTC)


def crea(
    db: Session,
    portachiavi: Portachiavi,
    user: User,
    copia: servizio.Copia,
    cf: str | None,
) -> LetturaDocumento:
    db.execute(
        delete(LetturaDocumento).where(LetturaDocumento.created_at < _ora() - CONSERVATA_PER)
    )
    lettura = LetturaDocumento(
        id=uuid.uuid4(), tenant_id=user.tenant_id, user_id=user.id, stato="in_corso"
    )
    db.add(lettura)
    db.commit()
    contesto = lettura.contesto()
    avvia(lambda: _esegui(lettura.id, contesto, portachiavi, copia, cf))
    return lettura


def _esegui(
    lettura_id: uuid.UUID,
    contesto: dict[str, str],
    portachiavi: Portachiavi,
    copia: servizio.Copia,
    cf: str | None,
) -> None:
    try:
        proposta = servizio.proposta(copia, clock.oggi(), cf, tempo_max=TEMPO_MAX)
        esito = {
            "proposta": proposta.model_dump(mode="json"),
            "anteprima": servizio.anteprima_inline(copia),
        }
        stato = "pronta"
    except Exception:
        log.exception("Lettura del documento non riuscita")
        esito, stato = {}, "errore"
    busta = portachiavi.cifra(json.dumps(esito).encode(), contesto)
    with SessionLocal() as db:
        lettura = db.get(LetturaDocumento, lettura_id)
        if lettura is None:
            return
        lettura.stato = stato
        lettura.cifratura = busta.schema
        lettura.esito_chiave = busta.chiave_cifrata
        lettura.esito_cifrato = busta.dati
        db.commit()


def esito(db: Session, portachiavi: Portachiavi, user: User, lettura_id: uuid.UUID) -> Esito:
    """Stato della lettura; se conclusa, l'esito viene consegnato una volta sola e cancellato."""
    lettura = db.scalars(
        select(LetturaDocumento).where(
            LetturaDocumento.id == lettura_id,
            LetturaDocumento.tenant_id == user.tenant_id,
            LetturaDocumento.user_id == user.id,
        )
    ).first()
    if lettura is None:
        return Esito("assente")
    secondi = int((_ora() - lettura.created_at).total_seconds())
    if lettura.stato == "in_corso":
        if secondi > PERSA_DOPO.total_seconds():
            db.delete(lettura)
            db.commit()
            return Esito("errore", secondi=secondi)
        return Esito("in_corso", secondi=secondi)
    dati = json.loads(portachiavi.decifra(lettura.busta, lettura.contesto()) or b"{}")
    db.delete(lettura)
    db.commit()
    if lettura.stato != "pronta" or not dati.get("proposta"):
        return Esito("errore", secondi=secondi)
    return Esito(
        "pronta",
        proposta=PropostaOut.model_validate(dati["proposta"]),
        anteprima=dati.get("anteprima"),
        secondi=secondi,
    )
