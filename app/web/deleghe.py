"""Pagine delle deleghe: cruscotto, elenco, nuova delega, scheda con le azioni per ogni stato."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock
from app.db import get_db
from app.domain import regole
from app.domain.regole import ModalitaFirma, TipoRichiesta
from app.models import (
    AdesioneFea,
    Delega,
    DocumentoFirmato,
    EvidenzaFirma,
    StatoDelega,
    User,
    VoceRegistro,
)
from app.routers import deleghe as api
from app.routers import documenti_identita as api_documenti
from app.routers import forniture as api_forniture
from app.routers.clienti import get_cliente
from app.routers.fea import adesione_attiva
from app.schemas.deleghe import DelegaIn, EsitoIn, FornituraIn
from app.web.base import errori_da, pagina, vai, vuoto_a_none
from app.web.sessione import utente_web

router = APIRouter()

IN_LAVORAZIONE = (StatoDelega.BOZZA, StatoDelega.IN_FIRMA, StatoDelega.FIRMATA, StatoDelega.INVIATA)


@router.get("", response_class=HTMLResponse)
def cruscotto(
    request: Request, user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    conteggi = dict(
        db.execute(
            select(Delega.stato, func.count())
            .where(Delega.tenant_id == user.tenant_id)
            .group_by(Delega.stato)
        ).all()
    )
    da_lavorare = list(
        db.scalars(
            select(Delega)
            .where(
                Delega.tenant_id == user.tenant_id,
                Delega.stato.in_(IN_LAVORAZIONE),
            )
            .order_by(Delega.updated_at.desc())
            .limit(50)
        )
    )
    in_scadenza = api.deleghe_in_scadenza(giorni=regole.GIORNI_FINESTRA_RINNOVO, user=user, db=db)
    return pagina(
        request,
        "cruscotto.html",
        {"conteggi": conteggi, "da_lavorare": da_lavorare, "in_scadenza": in_scadenza},
        user=user,
    )


@router.get("/deleghe", response_class=HTMLResponse)
def elenco(
    request: Request,
    stato: str = "",
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    return _pagina_elenco(request, user, db, stato)


def _pagina_elenco(request: Request, user: User, db: Session, stato: str, **extra) -> HTMLResponse:
    filtro = StatoDelega(stato) if stato in StatoDelega._value2member_map_ else None
    deleghe = api.list_deleghe(stato=filtro, cliente_id=None, limit=500, offset=0, user=user, db=db)
    contesto = {"deleghe": deleghe, "stato": filtro, **extra}
    return pagina(request, "deleghe.html", contesto, user=user, **_status(extra))


def _status(extra: dict) -> dict:
    return {"status_code": 422} if extra.get("errori") else {}


@router.post("/forniture")
async def fornitura(
    request: Request, user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    """File per l'invio massivo delle deleghe firmate selezionate."""
    form = await request.form()
    try:
        body = FornituraIn(
            delega_ids=form.getlist("delega_ids"),
            email=vuoto_a_none(form.get("email")),
            cellulare=vuoto_a_none(form.get("cellulare")),
        )
        return api_forniture.crea_fornitura(body, request=request, user=user, db=db)
    except (ValidationError, HTTPException) as exc:
        return _pagina_elenco(request, user, db, StatoDelega.FIRMATA.value, errori=errori_da(exc))


def _dati_delega(form, cliente_id: uuid.UUID) -> dict:
    dati: dict = {
        "cliente_id": cliente_id,
        "tipo_richiesta": form.get("tipo_richiesta") or TipoRichiesta.CONFERIMENTO.value,
        "servizi": form.getlist("servizi"),
        "delega_origine_id": vuoto_a_none(form.get("delega_origine_id")),
        "modalita_firma": vuoto_a_none(form.get("modalita_firma")),
        "luogo_firma": vuoto_a_none(form.get("luogo_firma")),
        "data_firma": vuoto_a_none(form.get("data_firma")),
        "sottoscrittore": None,
        "documento": None,
    }
    sott = {
        "codice_fiscale": vuoto_a_none(form.get("sott_codice_fiscale")),
        "cognome": vuoto_a_none(form.get("sott_cognome")),
        "nome": vuoto_a_none(form.get("sott_nome")),
        "qualifica": vuoto_a_none(form.get("sott_qualifica")),
    }
    if sott["codice_fiscale"] or sott["cognome"] or sott["nome"]:
        dati["sottoscrittore"] = sott
    doc = {
        "tipo": vuoto_a_none(form.get("doc_tipo")),
        "rilasciato_da": vuoto_a_none(form.get("doc_rilasciato_da")),
        "numero": vuoto_a_none(form.get("doc_numero")),
        "scadenza": vuoto_a_none(form.get("doc_scadenza")),
        "tipo_altro": vuoto_a_none(form.get("doc_tipo_altro")),
    }
    if doc["numero"] or doc["rilasciato_da"] or doc["scadenza"]:
        dati["documento"] = doc
    return dati


def _contesto_nuova(db: Session, user: User, cliente, dati: dict) -> dict:
    tipo = TipoRichiesta(dati.get("tipo_richiesta") or TipoRichiesta.CONFERIMENTO.value)
    origine = None
    if dati.get("delega_origine_id"):
        try:
            origine = api._get_delega(db, user, uuid.UUID(str(dati["delega_origine_id"])))
        except (ValueError, HTTPException):
            origine = None
    fea_ammessa = cliente.persona_fisica and not cliente.partita_iva_attiva
    return {
        "cliente": cliente,
        "tipo": tipo,
        "origine": origine,
        "dati": dati,
        "modalita": [
            m
            for m in ModalitaFirma
            if m != ModalitaFirma.CONVENZIONE_ADE
            and (fea_ammessa or m in regole.FIRME_SOGGETTI_IVA)
        ],
        "adesione": adesione_attiva(db, user.tenant_id, cliente.codice_fiscale),
    }


@router.get("/clienti/{cliente_id}/deleghe/nuova", response_class=HTMLResponse)
def nuova(
    request: Request,
    cliente_id: uuid.UUID,
    tipo: str = TipoRichiesta.CONFERIMENTO.value,
    origine: str = "",
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    cliente = get_cliente(db, user, cliente_id)
    if tipo not in TipoRichiesta._value2member_map_:
        tipo = TipoRichiesta.CONFERIMENTO.value
    dati: dict = {
        "tipo_richiesta": tipo,
        "delega_origine_id": origine or None,
        "data_firma": clock.oggi().isoformat(),
        "servizi": [],
    }
    contesto = _contesto_nuova(db, user, cliente, dati)
    # Documento gia' verificato all'adesione FEA: lo propone per la delega
    if adesione := contesto["adesione"]:
        dati["documento"] = {
            "tipo": adesione.documento_tipo.value,
            "numero": adesione.documento_numero,
            "rilasciato_da": adesione.documento_rilasciato_da,
            "scadenza": adesione.documento_scadenza.isoformat(),
        }
    # Altrimenti l'ultimo documento d'identita' archiviato e ancora valido
    elif (doc := api_documenti.documento_valido(db, cliente)) and doc.tipo:
        dati["documento"] = {
            "tipo": doc.tipo.value,
            "numero": doc.numero,
            "rilasciato_da": doc.rilasciato_da,
            "scadenza": doc.scadenza.isoformat(),
        }
    return pagina(request, "delega_nuova.html", contesto, user=user)


@router.post("/clienti/{cliente_id}/deleghe/nuova", response_class=HTMLResponse)
async def crea(
    request: Request,
    cliente_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    cliente = get_cliente(db, user, cliente_id)
    dati = _dati_delega(await request.form(), cliente.id)
    try:
        delega = api.create_delega(DelegaIn(**dati), request=request, user=user, db=db)
    except (ValidationError, HTTPException) as exc:
        contesto = _contesto_nuova(db, user, cliente, dati)
        contesto["errori"] = errori_da(exc)
        return pagina(request, "delega_nuova.html", contesto, user=user, status_code=422)
    return vai(f"/studio/deleghe/{delega.id}", ok="delega")


def contesto_scheda(db: Session, user: User, delega: Delega) -> dict:
    documento = db.scalar(select(DocumentoFirmato).where(DocumentoFirmato.delega_id == delega.id))
    voce = db.scalar(select(VoceRegistro).where(VoceRegistro.delega_id == delega.id))
    evidenza = db.scalar(select(EvidenzaFirma).where(EvidenzaFirma.delega_id == delega.id))
    adesione: AdesioneFea | None = None
    if delega.modalita_firma == ModalitaFirma.FEA_NON_QUALIFICATA:
        adesione = adesione_attiva(db, delega.tenant_id, delega.sottoscrittore_cf)
    oggi = clock.oggi()
    rinnovabile = (
        delega.stato == StatoDelega.ATTIVA
        and delega.data_scadenza is not None
        and regole.rinnovo_consentito(delega.data_scadenza, oggi)
    )
    collegate = list(
        db.scalars(
            select(Delega)
            .where(Delega.delega_origine_id == delega.id)
            .order_by(Delega.created_at.desc())
        )
    )
    return {
        "delega": delega,
        "cliente": delega.cliente,
        "documento": documento,
        "voce": voce,
        "evidenza": evidenza,
        "adesione": adesione,
        "xml": delega.xml_generato.decode() if delega.xml_generato else None,
        "rinnovabile": rinnovabile,
        "inizio_rinnovo": (
            regole.inizio_finestra_rinnovo(delega.data_scadenza) if delega.data_scadenza else None
        ),
        "collegate": collegate,
        "oggi": oggi,
        "errori": [],
    }


def pagina_scheda(
    request: Request, user: User, db: Session, delega: Delega, *, status_code=200, **extra
) -> HTMLResponse:
    contesto = contesto_scheda(db, user, delega)
    contesto.update(extra)
    return pagina(
        request,
        "delega.html",
        contesto,
        user=user,
        status_code=status_code,
        url=f"/studio/deleghe/{delega.id}",
    )


@router.get("/deleghe/{delega_id}", response_class=HTMLResponse)
def scheda(
    request: Request,
    delega_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    return pagina_scheda(request, user, db, api._get_delega(db, user, delega_id))


@router.post("/deleghe/{delega_id}/xml", response_class=HTMLResponse)
def genera_xml(
    request: Request,
    delega_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    delega = api._get_delega(db, user, delega_id)
    if delega.busta_cliente is not None:
        return pagina_scheda(
            request,
            user,
            db,
            delega,
            status_code=409,
            errori=["Il cliente ha gia' firmato questo file XML: non si puo' rigenerare"],
        )
    try:
        api.download_xml(delega.id, request=request, user=user, db=db)
    except HTTPException as exc:
        db.rollback()
        return pagina_scheda(request, user, db, delega, status_code=422, errori=errori_da(exc))
    return vai(f"/studio/deleghe/{delega.id}", ok="xml")


@router.get("/deleghe/{delega_id}/xml")
def scarica_xml(
    delega_id: uuid.UUID, user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    delega = api._get_delega(db, user, delega_id)
    if delega.xml_generato is None:
        raise HTTPException(404, detail="File XML non ancora generato")
    nome = f"DEL24_{delega.cliente.codice_fiscale}_{str(delega.id)[:8]}.xml"
    return Response(
        delega.xml_generato,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.post("/deleghe/{delega_id}/firmato", response_class=HTMLResponse)
async def carica_firmato(
    request: Request,
    delega_id: uuid.UUID,
    file: UploadFile | None = None,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    delega = api._get_delega(db, user, delega_id)
    if file is None or not file.filename:
        return pagina_scheda(
            request, user, db, delega, status_code=422, errori=["Scegliere il file .p7m firmato"]
        )
    try:
        esito = await api.carica_firmato(delega.id, request=request, file=file, user=user, db=db)
    except HTTPException as exc:
        db.rollback()
        return pagina_scheda(request, user, db, delega, status_code=422, errori=errori_da(exc))
    db.refresh(delega)
    return pagina_scheda(request, user, db, delega, esito=esito)


@router.get("/deleghe/{delega_id}/firmato")
def scarica_firmato(
    delega_id: uuid.UUID, user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    return api.scarica_firmato(delega_id, user=user, db=db)


@router.post("/deleghe/{delega_id}/invio", response_class=HTMLResponse)
def segna_inviata(
    request: Request,
    delega_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    delega = api._get_delega(db, user, delega_id)
    try:
        api.segna_inviata(delega.id, request=request, user=user, db=db)
    except HTTPException as exc:
        return pagina_scheda(request, user, db, delega, status_code=409, errori=errori_da(exc))
    return vai(f"/studio/deleghe/{delega.id}", ok="inviata")


@router.post("/deleghe/{delega_id}/esito", response_class=HTMLResponse)
async def registra_esito(
    request: Request,
    delega_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    delega = api._get_delega(db, user, delega_id)
    form = await request.form()
    try:
        body = EsitoIn(
            esito=form.get("esito"),
            data=vuoto_a_none(form.get("data")),
            motivo=vuoto_a_none(form.get("motivo")),
        )
        api.registra_esito(delega.id, body, request=request, user=user, db=db)
    except (ValidationError, HTTPException) as exc:
        db.rollback()
        return pagina_scheda(request, user, db, delega, status_code=422, errori=errori_da(exc))
    return vai(f"/studio/deleghe/{delega.id}", ok="esito")
