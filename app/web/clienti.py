"""Pagine dei clienti (deleganti): elenco con ricerca, scheda e anagrafica."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.db import get_db
from app.domain import regole
from app.models import AdesioneFea, Cliente, Delega, StatoDelega, User
from app.routers import clienti as api
from app.routers import documenti_identita as api_documenti
from app.schemas.deleghe import ClienteUpdate
from app.services.cifratura import Portachiavi, get_portachiavi
from app.web.base import errori_da, htmx_target, pagina, vai, vuoto_a_none
from app.web.sessione import utente_web

router = APIRouter()

CAMPI = (
    "codice_fiscale",
    "cognome",
    "nome",
    "denominazione",
    "email",
    "telefono",
    "data_nascita",
    "sesso",
)
CAMPI_PERSONA_FISICA = ("cognome", "nome", "data_nascita", "sesso")


def dati_modulo(form) -> dict:
    dati = {c: vuoto_a_none(form.get(c)) for c in CAMPI}
    dati["partita_iva_attiva"] = form.get("partita_iva_attiva") == "on"
    return dati


@router.get("/clienti", response_class=HTMLResponse)
def elenco(
    request: Request,
    q: str = "",
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    clienti = api.list_clienti(q=q.strip() or None, limit=200, offset=0, user=user, db=db)
    contesto = {"clienti": clienti, "q": q}
    if htmx_target(request) == "elenco-clienti":
        return pagina(request, "_clienti_righe.html", contesto, user=user)
    return pagina(request, "clienti.html", contesto, user=user)


@router.get("/clienti/nuovo", response_class=HTMLResponse)
def nuovo(request: Request, user: User = Depends(utente_web)) -> Response:
    return pagina(request, "cliente_nuovo.html", {"dati": {}}, user=user)


@router.post("/clienti/nuovo", response_class=HTMLResponse)
async def crea(
    request: Request,
    copia: UploadFile | None = None,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> Response:
    from app.web.documenti import crea_con_documento

    form = await request.form()
    cliente, dati, errori = await crea_con_documento(request, db, user, form, copia, portachiavi)
    if cliente is None:
        return pagina(
            request,
            "cliente_nuovo.html",
            {"dati": dati, "errori": errori},
            user=user,
            status_code=422,
        )
    return vai(f"/studio/clienti/{cliente.id}", ok="cliente")


def contesto_scheda(db: Session, user: User, cliente: Cliente) -> dict:
    deleghe = list(
        db.scalars(
            select(Delega).where(Delega.cliente_id == cliente.id).order_by(Delega.created_at.desc())
        )
    )
    adesioni = list(
        db.scalars(
            select(AdesioneFea)
            .where(AdesioneFea.cliente_id == cliente.id)
            .order_by(AdesioneFea.created_at.desc())
        )
    )
    oggi = clock.oggi()
    attive = [d for d in deleghe if d.stato == StatoDelega.ATTIVA]
    rinnovabili = {
        d.id for d in attive if d.data_scadenza and regole.rinnovo_consentito(d.data_scadenza, oggi)
    }
    archiviato = api_documenti.documento_valido(db, cliente)
    dati_adesione = {}
    if archiviato is not None:
        dati_adesione = {
            "documento_tipo": archiviato.tipo.value,
            "documento_numero": archiviato.numero,
            "documento_rilasciato_da": archiviato.rilasciato_da,
            "documento_scadenza": archiviato.scadenza.isoformat() if archiviato.scadenza else None,
            "documento_identita_id": str(archiviato.id),
        }
    return {
        "cliente": cliente,
        "deleghe": deleghe,
        "documenti": api_documenti.documenti_cliente(db, cliente),
        "archiviato": archiviato,
        "oggi": oggi,
        "attive": attive,
        "rinnovabili": rinnovabili,
        "adesioni": adesioni,
        "fea_ammessa": cliente.persona_fisica and not cliente.partita_iva_attiva,
        "dati": {},
        "dati_adesione": dati_adesione,
        "dati_documento": {},
    }


@router.get("/clienti/{cliente_id}", response_class=HTMLResponse)
def scheda(
    request: Request,
    cliente_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    cliente = api.get_cliente(db, user, cliente_id)
    return pagina(request, "cliente.html", contesto_scheda(db, user, cliente), user=user)


@router.post("/clienti/{cliente_id}", response_class=HTMLResponse)
async def aggiorna(
    request: Request,
    cliente_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    cliente = api.get_cliente(db, user, cliente_id)
    dati = dati_modulo(await request.form())
    dati.pop("codice_fiscale")
    if cliente.persona_fisica:
        dati.pop("denominazione")
    else:
        for campo in CAMPI_PERSONA_FISICA:
            dati.pop(campo)
    try:
        if cliente.persona_fisica and not (dati["cognome"] and dati["nome"]):
            raise HTTPException(422, detail="Per una persona fisica servono cognome e nome")
        if not cliente.persona_fisica and not dati["denominazione"]:
            raise HTTPException(422, detail="Indicare la denominazione")
        api.update_cliente(cliente.id, ClienteUpdate(**dati), request=request, user=user, db=db)
    except (ValidationError, HTTPException) as exc:
        db.rollback()
        contesto = contesto_scheda(db, user, cliente)
        contesto.update(dati=dati, errori_anagrafica=errori_da(exc))
        return pagina(
            request,
            "cliente.html",
            contesto,
            user=user,
            status_code=422,
            url=f"/studio/clienti/{cliente.id}",
        )
    return vai(f"/studio/clienti/{cliente.id}", ok="cliente")
