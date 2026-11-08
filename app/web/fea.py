"""FEA allo sportello: adesione del cliente identificato di persona e firma della delega con OTP."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.regole import TipoDocumento
from app.models import StatoAdesione, User
from app.routers import deleghe as api_deleghe
from app.routers import fea as api
from app.routers.clienti import get_cliente
from app.schemas.fea import CodiceIn
from app.services import ca_fea, fea, sms
from app.web import clienti as web_clienti
from app.web import deleghe as web_deleghe
from app.web.base import errori_da, htmx_target, pagina, vai, vuoto_a_none
from app.web.sessione import utente_web

router = APIRouter()


@router.post("/clienti/{cliente_id}/adesioni", response_class=HTMLResponse)
async def crea_adesione(
    request: Request,
    cliente_id: uuid.UUID,
    copia_documento: UploadFile | None = None,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    invio: sms.InvioSms = Depends(sms.get_sms),
) -> Response:
    """Identificazione di persona: documento esibito, copia e cellulare. Invia l'OTP di conferma."""
    cliente = get_cliente(db, user, cliente_id)
    form = await request.form()
    dati = {
        "documento_tipo": vuoto_a_none(form.get("documento_tipo")),
        "documento_numero": vuoto_a_none(form.get("documento_numero")),
        "documento_rilasciato_da": vuoto_a_none(form.get("documento_rilasciato_da")),
        "documento_scadenza": vuoto_a_none(form.get("documento_scadenza")),
        "cellulare": vuoto_a_none(form.get("cellulare")) or cliente.telefono,
    }
    errori = []
    if dati["documento_tipo"] not in TipoDocumento._value2member_map_:
        errori.append("Indicare il tipo di documento")
    if not dati["documento_numero"] or len(dati["documento_numero"]) > 30:
        errori.append("Indicare il numero del documento (al massimo 30 caratteri)")
    if not dati["documento_rilasciato_da"] or len(dati["documento_rilasciato_da"]) > 100:
        errori.append("Indicare chi ha rilasciato il documento (al massimo 100 caratteri)")
    try:
        scadenza = date.fromisoformat(dati["documento_scadenza"] or "")
    except ValueError:
        errori.append("Indicare la data di scadenza del documento")
    if not dati["cellulare"]:
        errori.append("Indicare il cellulare del cliente")
    if copia_documento is None or not copia_documento.filename:
        errori.append("Allegare la copia del documento esibito")
    if not errori:
        assert copia_documento is not None
        try:
            adesione = await api.crea_adesione(
                cliente.id,
                request=request,
                copia_documento=copia_documento,
                documento_tipo=TipoDocumento(dati["documento_tipo"]),
                documento_numero=dati["documento_numero"],
                documento_rilasciato_da=dati["documento_rilasciato_da"],
                documento_scadenza=scadenza,
                cellulare=dati["cellulare"],
                user=user,
                db=db,
                invio=invio,
            )
        except HTTPException as exc:
            db.rollback()
            errori = errori_da(exc)
        else:
            return vai(f"/studio/adesioni/{adesione.id}")
    contesto = web_clienti.contesto_scheda(db, user, cliente)
    contesto.update(dati_adesione=dati, errori_adesione=errori)
    return pagina(
        request,
        "cliente.html",
        contesto,
        user=user,
        status_code=422,
        url=f"/studio/clienti/{cliente.id}",
    )


def _pagina_adesione(request: Request, user: User, adesione, *, status_code=200, **extra):
    contesto = {
        "adesione": adesione,
        "cliente": adesione.cliente,
        "dichiarazione": adesione.dichiarazione.decode(),
        "cellulare": sms.maschera(adesione.cellulare),
        **extra,
    }
    return pagina(
        request,
        "adesione.html",
        contesto,
        user=user,
        status_code=status_code,
        url=f"/studio/adesioni/{adesione.id}",
    )


@router.get("/adesioni/{adesione_id}", response_class=HTMLResponse)
def scheda_adesione(
    request: Request,
    adesione_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    return _pagina_adesione(request, user, api._get_adesione(db, user, adesione_id))


@router.post("/adesioni/{adesione_id}/conferma", response_class=HTMLResponse)
async def conferma_adesione(
    request: Request,
    adesione_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    adesione = api._get_adesione(db, user, adesione_id)
    form = await request.form()
    try:
        body = CodiceIn(codice=str(form.get("codice") or ""))
        api.conferma_adesione(adesione.id, body, request=request, user=user, db=db)
    except ValidationError:
        return _pagina_adesione(
            request, user, adesione, status_code=422, errori=["Il codice ha sei cifre"]
        )
    except HTTPException as exc:
        db.refresh(adesione)
        return _pagina_adesione(request, user, adesione, status_code=422, errori=errori_da(exc))
    return vai(f"/studio/clienti/{adesione.cliente_id}", ok="adesione")


@router.post("/adesioni/{adesione_id}/otp", response_class=HTMLResponse)
def reinvia_otp_adesione(
    request: Request,
    adesione_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    invio: sms.InvioSms = Depends(sms.get_sms),
) -> Response:
    adesione = api._get_adesione(db, user, adesione_id)
    try:
        inviato = api.reinvia_otp_adesione(adesione.id, user=user, db=db, invio=invio)
    except HTTPException as exc:
        return _pagina_adesione(request, user, adesione, status_code=409, errori=errori_da(exc))
    return _pagina_adesione(request, user, adesione, otp=inviato)


@router.post("/adesioni/{adesione_id}/revoca", response_class=HTMLResponse)
def revoca_adesione(
    request: Request,
    adesione_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    adesione = api._get_adesione(db, user, adesione_id)
    if adesione.stato == StatoAdesione.REVOCATA:
        return _pagina_adesione(
            request, user, adesione, status_code=409, errori=["Adesione gia' revocata"]
        )
    api.revoca_adesione(adesione.id, request=request, user=user, db=db)
    return vai(f"/studio/clienti/{adesione.cliente_id}", ok="revocata")


def _pannello_firma(request: Request, user: User, db: Session, delega, *, status_code=200, **kw):
    """Pannello della firma FEA: solo il frammento se richiesto da HTMX, se no la scheda intera."""
    if htmx_target(request) == "fea-firma":
        contesto = web_deleghe.contesto_scheda(db, user, delega)
        contesto.update(kw)
        return pagina(request, "_fea_firma.html", contesto, user=user, status_code=status_code)
    return web_deleghe.pagina_scheda(request, user, db, delega, status_code=status_code, **kw)


@router.post("/deleghe/{delega_id}/fea/otp", response_class=HTMLResponse)
def invia_otp_firma(
    request: Request,
    delega_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    invio: sms.InvioSms = Depends(sms.get_sms),
) -> Response:
    delega = api_deleghe._get_delega(db, user, delega_id)
    try:
        inviato = api.invia_otp_firma(delega.id, user=user, db=db, invio=invio)
    except HTTPException as exc:
        return _pannello_firma(request, user, db, delega, status_code=422, errori=errori_da(exc))
    return _pannello_firma(request, user, db, delega, otp=inviato)


@router.post("/deleghe/{delega_id}/fea/firma", response_class=HTMLResponse)
async def firma_con_otp(
    request: Request,
    delega_id: uuid.UUID,
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
    autorita: ca_fea.Autorita = Depends(fea.get_autorita),
) -> Response:
    delega = api_deleghe._get_delega(db, user, delega_id)
    form = await request.form()
    try:
        body = CodiceIn(codice=str(form.get("codice") or ""))
        api.firma_con_otp(delega.id, body, request=request, user=user, db=db, autorita=autorita)
    except ValidationError:
        return _pannello_firma(
            request, user, db, delega, status_code=422, errori=["Il codice ha sei cifre"]
        )
    except HTTPException as exc:
        return _pannello_firma(request, user, db, delega, status_code=422, errori=errori_da(exc))
    response = vai(f"/studio/deleghe/{delega.id}", ok="fea")
    if htmx_target(request):
        # Dopo la firma cambia tutta la scheda: HTMX ricarica la pagina intera
        response = Response(status_code=204, headers={"HX-Location": response.headers["location"]})
    return response


@router.get("/deleghe/{delega_id}/fea/busta-cliente")
def scarica_busta_cliente(
    delega_id: uuid.UUID, user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    return api.scarica_busta_cliente(delega_id, user=user, db=db)
