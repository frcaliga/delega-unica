"""Documenti d'identita' dalle pagine: lettura dei dati, archivio e precompilazione."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.regole import TipoDocumento
from app.models import Cliente, DocumentoIdentita, User
from app.routers import clienti as api_clienti
from app.routers import documenti_identita as api
from app.schemas.deleghe import ClienteIn, ClienteUpdate
from app.schemas.documenti_identita import PropostaOut
from app.services.cifratura import Portachiavi, get_portachiavi
from app.web import clienti as web_clienti
from app.web.base import errori_da, pagina, vai, vuoto_a_none
from app.web.sessione import utente_web

router = APIRouter()


def dati_da_proposta(p: PropostaOut) -> dict:
    return {
        "documento_tipo": p.tipo.value if p.tipo else TipoDocumento.CARTA_IDENTITA.value,
        "documento_numero": p.numero,
        "documento_scadenza": p.scadenza.isoformat() if p.scadenza else None,
        "cognome": p.cognome,
        "nome": p.nome,
        "data_nascita": p.data_nascita.isoformat() if p.data_nascita else None,
        "sesso": p.sesso,
        "codice_fiscale": p.codice_fiscale,
    }


def _file(upload: UploadFile | None) -> UploadFile | None:
    return upload if upload is not None and upload.filename else None


async def _proposta(upload: UploadFile | None, cf: str | None) -> tuple[PropostaOut | None, list]:
    if (upload := _file(upload)) is None:
        return None, ["Scegliere il file con la copia del documento"]
    try:
        copia = await api.leggi_upload(upload)
    except HTTPException as exc:
        return None, errori_da(exc)
    return await api.leggi_proposta(copia, cf), []


def _estremi(form) -> tuple[dict, list[str]]:
    dati = {
        "documento_tipo": vuoto_a_none(form.get("documento_tipo")) or "1",
        "documento_numero": vuoto_a_none(form.get("documento_numero")),
        "documento_rilasciato_da": vuoto_a_none(form.get("documento_rilasciato_da")),
        "documento_scadenza": vuoto_a_none(form.get("documento_scadenza")),
    }
    errori = []
    if dati["documento_tipo"] not in TipoDocumento._value2member_map_:
        errori.append("Indicare il tipo di documento")
    if dati["documento_numero"] and len(dati["documento_numero"]) > 30:
        errori.append("Numero del documento: al massimo 30 caratteri")
    if dati["documento_rilasciato_da"] and len(dati["documento_rilasciato_da"]) > 100:
        errori.append("Rilasciato da: al massimo 100 caratteri")
    if dati["documento_scadenza"]:
        try:
            date.fromisoformat(dati["documento_scadenza"])
        except ValueError:
            errori.append("Data di scadenza del documento non valida")
    return dati, errori


async def _archivia(
    request: Request,
    db: Session,
    user: User,
    cliente: Cliente,
    upload: UploadFile,
    estremi: dict,
    portachiavi: Portachiavi,
) -> DocumentoIdentita:
    copia = await api.leggi_upload(upload)
    proposta = await api.leggi_proposta(copia, cliente.codice_fiscale)
    return api.salva(
        db,
        request,
        user,
        cliente,
        copia,
        proposta,
        portachiavi,
        tipo=TipoDocumento(estremi["documento_tipo"]),
        numero=estremi["documento_numero"],
        rilasciato_da=estremi["documento_rilasciato_da"],
        scadenza=(
            date.fromisoformat(estremi["documento_scadenza"])
            if estremi["documento_scadenza"]
            else None
        ),
    )


# Scheda del cliente


@router.post("/clienti/{cliente_id}/documenti/leggi", response_class=HTMLResponse)
async def leggi(
    request: Request,
    cliente_id: uuid.UUID,
    copia: UploadFile | None = None,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    """Frammento HTMX: estremi e anagrafica letti dal documento, da confermare."""
    cliente = api_clienti.get_cliente(db, user, cliente_id)
    proposta, errori = await _proposta(copia, cliente.codice_fiscale)
    dati = dati_da_proposta(proposta) if proposta else {}
    return pagina(
        request,
        "_documento_campi.html",
        {"cliente": cliente, "dati_documento": dati, "proposta": proposta, "errori": errori},
        user=user,
    )


@router.post("/clienti/{cliente_id}/documenti", response_class=HTMLResponse)
async def archivia(
    request: Request,
    cliente_id: uuid.UUID,
    copia: UploadFile | None = None,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> Response:
    cliente = api_clienti.get_cliente(db, user, cliente_id)
    form = await request.form()
    estremi, errori = _estremi(form)
    aggiorna = form.get("aggiorna_anagrafica") == "on"
    anagrafica = {
        "cognome": vuoto_a_none(form.get("cognome")),
        "nome": vuoto_a_none(form.get("nome")),
        "data_nascita": vuoto_a_none(form.get("data_nascita")),
        "sesso": vuoto_a_none(form.get("sesso")),
    }
    if (upload := _file(copia)) is None:
        errori.append("Scegliere il file con la copia del documento")
    if not errori:
        try:
            api.solo_persone_fisiche(cliente)
            modifiche = {k: v for k, v in anagrafica.items() if v} if aggiorna else {}
            update = ClienteUpdate(**modifiche) if modifiche else None
            await _archivia(request, db, user, cliente, upload, estremi, portachiavi)
            if update is not None:
                api_clienti.update_cliente(cliente.id, update, request=request, user=user, db=db)
            db.commit()
        except (ValidationError, HTTPException) as exc:
            db.rollback()
            errori = errori_da(exc)
        else:
            return vai(f"/studio/clienti/{cliente.id}", ok="documento")
    contesto = web_clienti.contesto_scheda(db, user, cliente)
    contesto.update(
        dati_documento={**estremi, **anagrafica, "aggiorna_anagrafica": aggiorna},
        errori_documento=errori,
    )
    return pagina(
        request,
        "cliente.html",
        contesto,
        user=user,
        status_code=422,
        url=f"/studio/clienti/{cliente.id}",
    )


@router.get("/documenti/{documento_id}/copia")
def scarica(
    request: Request,
    documento_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> Response:
    return api.scarica(documento_id, request=request, user=user, db=db, portachiavi=portachiavi)


@router.post("/documenti/{documento_id}/elimina")
def elimina(
    request: Request,
    documento_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    doc = api.get_documento(db, user, documento_id)
    cliente_id = doc.cliente_id
    api.elimina(documento_id, request=request, user=user, db=db)
    return vai(f"/studio/clienti/{cliente_id}", ok="documento_eliminato")


# Nuovo cliente a partire dal documento


@router.post("/clienti/nuovo/leggi", response_class=HTMLResponse)
async def leggi_nuovo(
    request: Request,
    copia: UploadFile | None = None,
    user: User = Depends(utente_web),
) -> Response:
    form = await request.form()
    cf = (vuoto_a_none(form.get("codice_fiscale")) or "").upper() or None
    proposta, errori = await _proposta(copia, cf)
    dati = {k: vuoto_a_none(form.get(k)) for k in web_clienti.CAMPI}
    if proposta:
        letti = dati_da_proposta(proposta)
        dati.update({k: v for k, v in letti.items() if v and (k != "codice_fiscale" or not cf)})
    return pagina(
        request,
        "_nuovo_cliente_campi.html",
        {"dati": dati, "proposta": proposta, "errori": errori},
        user=user,
    )


async def crea_con_documento(
    request: Request,
    db: Session,
    user: User,
    form,
    copia: UploadFile | None,
    portachiavi: Portachiavi,
) -> tuple[Cliente | None, dict, list[str]]:
    """Crea il cliente e, se e' stata scelta una copia, ne archivia il documento."""
    dati = web_clienti.dati_modulo(form)
    estremi, errori = _estremi(form)
    upload = _file(copia)
    if errori and upload:
        return None, {**dati, **estremi}, errori
    try:
        body = ClienteIn(**dati)
        if upload:
            # Il file e il tipo di soggetto si controllano prima di creare il cliente
            await api.leggi_upload(upload)
            await upload.seek(0)
            api.solo_persone_fisiche(Cliente(codice_fiscale=body.codice_fiscale))
        cliente = api_clienti.create_cliente(body, request=request, user=user, db=db)
        if upload:
            await _archivia(request, db, user, cliente, upload, estremi, portachiavi)
            db.commit()
    except (ValidationError, HTTPException) as exc:
        db.rollback()
        return None, {**dati, **estremi}, errori_da(exc)
    return cliente, dati, []
