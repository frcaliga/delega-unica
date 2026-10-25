import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, clock
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.domain import regole
from app.domain.regole import ModalitaFirma, Qualifica, TipoRichiesta
from app.models import Delega, StatoDelega, Tenant, User
from app.routers.clienti import get_cliente
from app.schemas.deleghe import DelegaIn, DelegaOut, EsitoIn, VoceRegistroOut
from app.services import registro
from app.services.xml_delega import XmlNonValido, costruisci_delega

router = APIRouter(prefix="/deleghe", tags=["deleghe"])


def _errori(errori: list[str]) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"errori": errori})


def _get_delega(db: Session, user: User, delega_id: uuid.UUID) -> Delega:
    delega = db.get(Delega, delega_id)
    if delega is None or delega.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Delega non trovata")
    return delega


def _richiedi_stato(delega: Delega, *ammessi: StatoDelega) -> None:
    if delega.stato not in ammessi:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Operazione non consentita con la delega in stato {delega.stato.value}",
        )


@router.get("", response_model=list[DelegaOut])
def list_deleghe(
    stato: StatoDelega | None = None,
    cliente_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Delega]:
    stmt = select(Delega).where(Delega.tenant_id == user.tenant_id)
    if stato:
        stmt = stmt.where(Delega.stato == stato)
    if cliente_id:
        stmt = stmt.where(Delega.cliente_id == cliente_id)
    stmt = stmt.order_by(Delega.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.get("/in-scadenza", response_model=list[DelegaOut])
def deleghe_in_scadenza(
    giorni: int = Query(regole.GIORNI_FINESTRA_RINNOVO, ge=1, le=366),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Delega]:
    """Deleghe attive che scadono entro il numero di giorni indicato (di norma rinnovabili)."""
    oggi = clock.oggi()
    stmt = (
        select(Delega)
        .where(
            Delega.tenant_id == user.tenant_id,
            Delega.stato == StatoDelega.ATTIVA,
            Delega.data_scadenza >= oggi,
            Delega.data_scadenza <= oggi + timedelta(days=giorni),
        )
        .order_by(Delega.data_scadenza)
    )
    return list(db.scalars(stmt))


@router.post("", response_model=DelegaOut, status_code=status.HTTP_201_CREATED)
def create_delega(
    body: DelegaIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Delega:
    cliente = get_cliente(db, user, body.cliente_id)
    oggi = clock.oggi()
    errori: list[str] = []

    if body.modalita_firma == ModalitaFirma.CONVENZIONE_ADE:
        errori.append("Il conferimento tramite servizio in convenzione non e' ancora disponibile")

    servizi = [s.value for s in body.servizi]
    errori += regole.errori_servizi(body.tipo_richiesta, servizi)

    origine = None
    if body.tipo_richiesta == TipoRichiesta.CONFERIMENTO:
        if body.delega_origine_id:
            errori.append("Un nuovo conferimento non si riferisce a una delega precedente")
    elif body.delega_origine_id is None:
        errori.append("Indicare la delega attiva da revocare o rinnovare")
    else:
        origine = _get_delega(db, user, body.delega_origine_id)
        if origine.cliente_id != cliente.id:
            errori.append("La delega da revocare o rinnovare e' di un altro cliente")
        elif origine.stato != StatoDelega.ATTIVA:
            errori.append("La delega da revocare o rinnovare non e' attiva")
        elif body.tipo_richiesta == TipoRichiesta.RINNOVO:
            if not regole.rinnovo_consentito(origine.data_scadenza, oggi):
                inizio = regole.inizio_finestra_rinnovo(origine.data_scadenza)
                errori.append(f"Il rinnovo e' possibile dal {inizio:%d/%m/%Y} alla scadenza")
            # Il rinnovo non ammette variazioni: conserva i servizi della delega rinnovata
            servizi = list(origine.servizi)

    sott = body.sottoscrittore
    if sott is None and not cliente.persona_fisica:
        errori.append("Per un soggetto diverso da persona fisica indicare il rappresentante")
    sottoscrittore_cf = sott.codice_fiscale if sott else cliente.codice_fiscale
    qualifica = sott.qualifica if sott else Qualifica.DELEGANTE
    delegante = regole.DatiDelegante(
        cliente.codice_fiscale, cliente.persona_fisica, cliente.partita_iva_attiva
    )
    documento = (
        regole.DatiDocumento(
            body.documento.tipo,
            body.documento.rilasciato_da,
            body.documento.numero,
            body.documento.scadenza,
            body.documento.tipo_altro,
        )
        if body.documento
        else None
    )
    errori += regole.errori_sottoscrittore(
        delegante, regole.DatiSottoscrittore(sottoscrittore_cf, qualifica)
    )
    errori += regole.errori_firma(delegante, body.modalita_firma, documento)
    errori += regole.errori_data_firma(body.data_firma, oggi)
    if errori:
        raise _errori(errori)

    doc = body.documento
    delega = Delega(
        tenant_id=user.tenant_id,
        cliente_id=cliente.id,
        tipo_richiesta=body.tipo_richiesta,
        servizi=servizi if body.tipo_richiesta != TipoRichiesta.REVOCA else [],
        delega_origine_id=origine.id if origine else None,
        sottoscrittore_cf=sottoscrittore_cf,
        sottoscrittore_cognome=sott.cognome if sott else cliente.cognome,
        sottoscrittore_nome=sott.nome if sott else cliente.nome,
        qualifica=qualifica,
        documento_tipo=doc.tipo if doc else None,
        documento_rilasciato_da=doc.rilasciato_da if doc else None,
        documento_numero=doc.numero if doc else None,
        documento_scadenza=doc.scadenza if doc else None,
        documento_tipo_altro=doc.tipo_altro if doc else None,
        modalita_firma=body.modalita_firma,
        luogo_firma=body.luogo_firma,
        data_firma=body.data_firma,
        stato=StatoDelega.BOZZA,
        created_by=user.id,
    )
    db.add(delega)
    db.flush()
    audit.record(
        db,
        "delega_created",
        user=user,
        entity_type="delega",
        entity_id=delega.id,
        request=request,
        details={"tipo_richiesta": delega.tipo_richiesta.value, "cliente_id": str(cliente.id)},
    )
    db.commit()
    return delega


@router.get("/{delega_id}", response_model=DelegaOut)
def read_delega(
    delega_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Delega:
    return _get_delega(db, user, delega_id)


@router.get("/{delega_id}/xml", response_class=Response)
def download_xml(
    delega_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """File XML della delega da far firmare al cliente in formato CAdES (.p7m)."""
    delega = _get_delega(db, user, delega_id)
    _richiedi_stato(delega, StatoDelega.BOZZA, StatoDelega.IN_FIRMA)
    tenant = db.get_one(Tenant, user.tenant_id)
    errori = regole.errori_data_firma(delega.data_firma, clock.oggi())
    if len(tenant.codice_fiscale) == 16 and not (tenant.cognome and tenant.nome):
        errori.append("Completare cognome e nome dell'intermediario nei dati dello studio")
    if errori:
        raise _errori(errori)
    try:
        contenuto = costruisci_delega(delega, tenant, get_settings().identificativo_prod_software)
    except XmlNonValido as exc:
        raise _errori(exc.errori) from exc

    if delega.stato == StatoDelega.BOZZA:
        delega.stato = StatoDelega.IN_FIRMA
    audit.record(
        db,
        "delega_xml_generated",
        user=user,
        entity_type="delega",
        entity_id=delega.id,
        request=request,
    )
    db.commit()
    nome = f"DEL24_{delega.cliente.codice_fiscale}_{str(delega.id)[:8]}.xml"
    return Response(
        contenuto,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.post("/{delega_id}/registra", response_model=VoceRegistroOut)
def registra_delega(
    delega_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Annota nel registro cronologico la delega ricevuta firmata dal cliente."""
    delega = _get_delega(db, user, delega_id)
    _richiedi_stato(delega, StatoDelega.BOZZA, StatoDelega.IN_FIRMA)
    if not delega.ha_documento:
        raise _errori(
            ["Il registro richiede gli estremi del documento d'identita' del sottoscrittore"]
        )
    voce = registro.annota(db, delega, user, clock.oggi())
    delega.stato = StatoDelega.FIRMATA
    audit.record(
        db,
        "delega_registrata",
        user=user,
        entity_type="delega",
        entity_id=delega.id,
        request=request,
        details={"numero_registro": voce.numero},
    )
    db.commit()
    return voce


@router.post("/{delega_id}/invio", response_model=DelegaOut)
def segna_inviata(
    delega_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Delega:
    """Segna la delega come trasmessa all'Agenzia delle Entrate."""
    delega = _get_delega(db, user, delega_id)
    _richiedi_stato(delega, StatoDelega.FIRMATA)
    delega.stato = StatoDelega.INVIATA
    audit.record(
        db,
        "delega_inviata",
        user=user,
        entity_type="delega",
        entity_id=delega.id,
        request=request,
    )
    db.commit()
    return delega


@router.post("/{delega_id}/esito", response_model=DelegaOut)
def registra_esito(
    delega_id: uuid.UUID,
    body: EsitoIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Delega:
    """Riporta l'esito della ricevuta AdE e aggiorna le deleghe collegate."""
    delega = _get_delega(db, user, delega_id)
    _richiedi_stato(delega, StatoDelega.INVIATA)

    if body.esito == "scartata":
        delega.stato = StatoDelega.SCARTATA
        delega.esito_motivo = body.motivo
    elif delega.tipo_richiesta == TipoRichiesta.REVOCA:
        delega.stato = StatoDelega.ACCETTATA
        delega.delega_origine.stato = StatoDelega.REVOCATA
    else:
        if delega.tipo_richiesta == TipoRichiesta.RINNOVO:
            delega.data_scadenza = regole.scadenza_dopo_rinnovo(delega.delega_origine.data_scadenza)
        else:
            delega.data_scadenza = regole.scadenza(body.data)
        delega.data_conferimento = body.data
        # Una nuova comunicazione revoca e sostituisce le deleghe precedenti allo stesso studio
        precedenti = db.scalars(
            select(Delega).where(
                Delega.tenant_id == delega.tenant_id,
                Delega.cliente_id == delega.cliente_id,
                Delega.stato == StatoDelega.ATTIVA,
                Delega.id != delega.id,
            )
        )
        for precedente in precedenti:
            precedente.stato = StatoDelega.SOSTITUITA
        delega.stato = StatoDelega.ATTIVA

    audit.record(
        db,
        "delega_esito",
        user=user,
        entity_type="delega",
        entity_id=delega.id,
        request=request,
        details={"esito": body.esito, "data": body.data.isoformat()},
    )
    db.commit()
    return delega
