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
from app.services import documenti_identita as servizio
from app.services import letture
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
        "documento_rilasciato_da": p.rilasciato_da,
        "cognome": p.cognome,
        "nome": p.nome,
        "data_nascita": p.data_nascita.isoformat() if p.data_nascita else None,
        "sesso": p.sesso,
        "luogo_nascita": luogo_nascita(p),
        "codice_fiscale": p.codice_fiscale,
    }


def luogo_nascita(p: PropostaOut) -> str | None:
    if p.luogo_nascita and p.provincia_nascita:
        return f"{p.luogo_nascita} ({p.provincia_nascita})"
    return p.luogo_nascita


def _file(uploads: list[UploadFile] | None) -> list[UploadFile]:
    return [u for u in uploads or [] if u.filename]


async def _avvia_lettura(
    request: Request,
    db: Session,
    portachiavi: Portachiavi,
    user: User,
    uploads: list[UploadFile] | None,
    cf: str | None,
    poll: str,
) -> Response:
    """Avvia la lettura in background e risponde con il frammento che ne chiede l'esito."""
    if not (scelti := _file(uploads)):
        errori = ["Scegliere il file con la copia del documento"]
    else:
        try:
            copia = await api.leggi_upload(scelti)
        except HTTPException as exc:
            errori = errori_da(exc)
        else:
            lettura = letture.crea(db, portachiavi, user, copia, cf)
            return pagina(
                request,
                "_lettura_in_corso.html",
                {"url": f"{poll}?lettura={lettura.id}", "secondi": 0},
                user=user,
            )
    return pagina(request, "_lettura_in_corso.html", {"errori": errori}, user=user)


def _attesa_o_esito(
    request: Request, user: User, esito: letture.Esito, url: str
) -> Response | None:
    """Frammento di attesa, o di errore; None quando l'esito e' pronto."""
    if esito.stato == "in_corso":
        return pagina(
            request, "_lettura_in_corso.html", {"url": url, "secondi": esito.secondi}, user=user
        )
    if esito.stato != "pronta":
        errore = (
            "La lettura non e' riuscita: compilare i campi a mano o riprovare con un'altra foto"
            if esito.stato == "errore"
            else "Lettura non trovata: avviarla di nuovo"
        )
        return pagina(request, "_lettura_in_corso.html", {"errori": [errore]}, user=user)
    return None


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
    copia: servizio.Copia,
    proposta: PropostaOut | None,
    estremi: dict,
    portachiavi: Portachiavi,
) -> DocumentoIdentita:
    # Gli estremi li conferma l'operatore: al salvataggio non si rilegge il documento, si usa
    # l'esito della lettura fatta prima (se c'e'), che resta solo come riepilogo
    return api.salva(
        db,
        request,
        user,
        cliente,
        copia,
        proposta or PropostaOut(letta=False),
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


async def _da_salvare(
    db: Session, portachiavi: Portachiavi, user: User, form, scelti: list[UploadFile]
) -> tuple[servizio.Copia | None, PropostaOut | None]:
    """La copia da archiviare con l'esito della sua lettura: quella caricata ora, oppure quella
    gia' letta, che non va ricaricata."""
    letta = letture.letta(db, portachiavi, user, form.get("lettura_copia"))
    if scelti:
        file = await api.leggi_upload(scelti)
        if letta is not None and letta.impronta == file.impronta:
            return file, letta.proposta
        return file, None
    if letta is not None:
        return letta.copia, letta.proposta
    return None, None


# Scheda del cliente


@router.post("/clienti/{cliente_id}/documenti/leggi", response_class=HTMLResponse)
async def leggi(
    request: Request,
    cliente_id: uuid.UUID,
    copia: list[UploadFile] | None = None,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> Response:
    """Frammento HTMX: avvia la lettura del documento caricato nella scheda."""
    cliente = api_clienti.get_cliente(db, user, cliente_id)
    poll = f"/studio/clienti/{cliente.id}/documenti/lettura"
    return await _avvia_lettura(request, db, portachiavi, user, copia, cliente.codice_fiscale, poll)


@router.post("/clienti/{cliente_id}/documenti/lettura", response_class=HTMLResponse)
def lettura_scheda(
    request: Request,
    cliente_id: uuid.UUID,
    lettura: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> Response:
    """Esito della lettura: a lettura conclusa sostituisce estremi e anagrafica da confermare."""
    cliente = api_clienti.get_cliente(db, user, cliente_id)
    esito = letture.esito(db, portachiavi, user, lettura)
    url = f"/studio/clienti/{cliente.id}/documenti/lettura?lettura={lettura}"
    if (risposta := _attesa_o_esito(request, user, esito, url)) is not None:
        return risposta
    return pagina(
        request,
        "_documento_campi.html",
        {
            "cliente": cliente,
            "dati_documento": dati_da_proposta(esito.proposta),
            "proposta": esito.proposta,
            "errori": None,
            "anteprima": esito.anteprima,
            "lettura_copia": str(lettura),
            "oob": True,
        },
        user=user,
    )


@router.post("/clienti/{cliente_id}/documenti", response_class=HTMLResponse)
async def archivia(
    request: Request,
    cliente_id: uuid.UUID,
    copia: list[UploadFile] | None = None,
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
        "luogo_nascita": vuoto_a_none(form.get("luogo_nascita")),
    }
    scelti = _file(copia)
    lettura_copia = form.get("lettura_copia") or None
    try:
        file, proposta = await _da_salvare(db, portachiavi, user, form, scelti)
    except HTTPException as exc:
        file, proposta = None, None
        errori += errori_da(exc)
    else:
        if file is None:
            errori.append("Scegliere il file con la copia del documento")
    if not errori and file is not None:
        try:
            api.solo_persone_fisiche(cliente)
            modifiche = {k: v for k, v in anagrafica.items() if v} if aggiorna else {}
            update = ClienteUpdate(**modifiche) if modifiche else None
            await _archivia(request, db, user, cliente, file, proposta, estremi, portachiavi)
            if update is not None:
                api_clienti.update_cliente(cliente.id, update, request=request, user=user, db=db)
            letture.scarta(db, user, lettura_copia)
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
        lettura_copia=lettura_copia if letture.letta_valida(db, user, lettura_copia) else None,
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


@router.get("/documenti/{documento_id}/anteprima.jpg")
def anteprima(
    request: Request,
    documento_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> Response:
    """Fronte e retro della copia archiviata, ridotti, da vedere nella scheda."""
    doc = api.get_documento(db, user, documento_id)
    dati = api.copia_in_chiaro(db, request, user, doc, portachiavi, "anteprima")
    try:
        jpeg = servizio.anteprima(dati, doc.copia_tipo)
    except Exception as exc:  # copia non visualizzabile: resta scaricabile
        raise HTTPException(422, detail="Anteprima non disponibile") from exc
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
    copia: list[UploadFile] | None = None,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> Response:
    form = await request.form()
    dati, _ = _senza_letture_precedenti(form)
    cf = (dati.get("codice_fiscale") or "").upper() or None
    poll = "/studio/clienti/nuovo/lettura"
    return await _avvia_lettura(request, db, portachiavi, user, copia, cf, poll)


def _senza_letture_precedenti(form) -> tuple[dict, set[str]]:
    """Campi del modulo senza quelli compilati dalla lettura precedente: con un altro documento
    non devono restare i dati della persona di prima. Restano quelli scritti a mano."""
    dati = {k: vuoto_a_none(form.get(k)) for k in web_clienti.CAMPI}
    precedenti = set(str(form.get("campi_letti") or "").split(",")) & set(web_clienti.CAMPI)
    for k in precedenti:
        dati[k] = None
    return dati, precedenti


@router.post("/clienti/nuovo/lettura", response_class=HTMLResponse)
async def lettura_nuovo(
    request: Request,
    lettura: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> Response:
    """Esito della lettura: a lettura conclusa compila anagrafica ed estremi del nuovo cliente."""
    esito = letture.esito(db, portachiavi, user, lettura)
    url = f"/studio/clienti/nuovo/lettura?lettura={lettura}"
    if (risposta := _attesa_o_esito(request, user, esito, url)) is not None:
        return risposta
    form = await request.form()
    dati, _ = _senza_letture_precedenti(form)
    cf = (dati.get("codice_fiscale") or "").upper() or None
    letti: list[str] = []
    for k, v in dati_da_proposta(esito.proposta).items():
        if v and (k != "codice_fiscale" or not cf):
            dati[k] = v
            letti.append(k)
    return pagina(
        request,
        "_nuovo_cliente_campi.html",
        {
            "dati": dati,
            "proposta": esito.proposta,
            "errori": None,
            "campi_letti": ",".join(letti),
            "anteprima": esito.anteprima,
            "lettura_copia": str(lettura),
            "oob": True,
        },
        user=user,
    )


async def crea_con_documento(
    request: Request,
    db: Session,
    user: User,
    form,
    copia: list[UploadFile] | None,
    portachiavi: Portachiavi,
) -> tuple[Cliente | None, dict, list[str]]:
    """Crea il cliente e, se e' stata scelta una copia, ne archivia il documento."""
    dati = web_clienti.dati_modulo(form)
    estremi, errori = _estremi(form)
    scelti = _file(copia)
    lettura_copia = form.get("lettura_copia") or None
    # Se il modulo torna indietro, la copia gia' letta resta e i campi letti si riconoscono
    rimanda = {**dati, **estremi, "campi_letti": form.get("campi_letti") or ""}
    if letture.letta_valida(db, user, lettura_copia):
        rimanda["lettura_copia"] = lettura_copia
    if errori and (scelti or "lettura_copia" in rimanda):
        return None, rimanda, errori
    try:
        body = ClienteIn(**dati)
        # Il file e il tipo di soggetto si controllano prima di creare il cliente
        file, proposta = await _da_salvare(db, portachiavi, user, form, scelti)
        if file is not None:
            api.solo_persone_fisiche(Cliente(codice_fiscale=body.codice_fiscale))
        cliente = api_clienti.create_cliente(body, request=request, user=user, db=db)
        if file is not None:
            await _archivia(request, db, user, cliente, file, proposta, estremi, portachiavi)
            letture.scarta(db, user, lettura_copia)
            db.commit()
    except (ValidationError, HTTPException) as exc:
        db.rollback()
        return None, rimanda, errori_da(exc)
    return cliente, dati, []
