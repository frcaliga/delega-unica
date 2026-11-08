"""Letture dei documenti d'identita' in background.

La lettura impegna la CPU per decine di secondi, troppo per una richiesta HTTP dietro il
bilanciatore. La pagina avvia la lettura e ne chiede l'esito ogni pochi secondi. Le letture girano
una alla volta per container, cosi' il sito resta reattivo. L'esito (dati letti e anteprima) e'
cifrato nel database, perche' la richiesta successiva puo' arrivare a un altro container. Anche la
copia caricata resta, cifrata, per salvarla con il cliente senza ricaricarla ne' rileggerla. La
riga si cancella al salvataggio o dopo un'ora.
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
    busta = portachiavi.cifra(copia.dati, lettura.contesto_copia())
    lettura.copia_cifratura = busta.schema
    lettura.copia_chiave = busta.chiave_cifrata
    lettura.copia_cifrata = busta.dati
    lettura.copia_tipo = copia.tipo
    lettura.copia_nome = copia.nome
    lettura.copia_impronta = copia.impronta
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
    if lettura.stato in ("consegnata", "chiusa"):
        return Esito("assente")
    if lettura.stato == "in_corso":
        if secondi > PERSA_DOPO.total_seconds():
            db.delete(lettura)
            db.commit()
            return Esito("errore", secondi=secondi)
        return Esito("in_corso", secondi=secondi)
    dati = json.loads(portachiavi.decifra(lettura.busta, lettura.contesto()) or b"{}")
    riuscita = lettura.stato == "pronta" and bool(dati.get("proposta"))
    # L'esito si consegna una volta; copia ed esito restano per il salvataggio
    lettura.stato = "consegnata" if riuscita else "chiusa"
    db.commit()
    if not riuscita:
        return Esito("errore", secondi=secondi)
    return Esito(
        "pronta",
        proposta=PropostaOut.model_validate(dati["proposta"]),
        anteprima=dati.get("anteprima"),
        secondi=secondi,
    )


def _mia(db: Session, user: User, lettura_id: str | None) -> LetturaDocumento | None:
    try:
        chiave = uuid.UUID(str(lettura_id))
    except ValueError:
        return None
    return db.scalars(
        select(LetturaDocumento).where(
            LetturaDocumento.id == chiave,
            LetturaDocumento.tenant_id == user.tenant_id,
            LetturaDocumento.user_id == user.id,
            LetturaDocumento.created_at >= _ora() - CONSERVATA_PER,
        )
    ).first()


@dataclass
class Letta:
    copia: servizio.Copia
    proposta: PropostaOut | None
    impronta: str | None


def letta(
    db: Session, portachiavi: Portachiavi, user: User, lettura_id: str | None
) -> Letta | None:
    """La copia caricata con la lettura e i dati letti, per salvarla senza ricaricarla e senza
    rileggerla."""
    lettura = _mia(db, user, lettura_id)
    if lettura is None or not lettura.copia_cifrata:
        return None
    dati = portachiavi.decifra(lettura.busta_copia, lettura.contesto_copia())
    copia = servizio.Copia(dati, lettura.copia_tipo or "", lettura.copia_nome or "documento")
    proposta = None
    if lettura.stato == "consegnata" and lettura.esito_cifrato:
        esito = json.loads(portachiavi.decifra(lettura.busta, lettura.contesto()) or b"{}")
        if esito.get("proposta"):
            proposta = PropostaOut.model_validate(esito["proposta"])
    return Letta(copia, proposta, lettura.copia_impronta)


def letta_valida(db: Session, user: User, lettura_id: str | None) -> bool:
    """La copia della lettura c'e' ancora: il modulo ripresentato puo' continuare a usarla."""
    lettura = _mia(db, user, lettura_id)
    return lettura is not None and bool(lettura.copia_cifrata)


def scarta(db: Session, user: User, lettura_id: str | None) -> None:
    """Dopo il salvataggio la lettura (e la sua copia) non serve piu'."""
    if (lettura := _mia(db, user, lettura_id)) is not None:
        db.delete(lettura)
