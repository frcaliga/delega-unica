"""Documenti d'identita' dei clienti persone fisiche: lettura dei dati e archivio cifrato.

- POST /documenti-identita/lettura: legge i dati da una copia senza salvarla
- POST /clienti/{id}/documenti-identita: salva la copia cifrata con gli estremi confermati
- GET /clienti/{id}/documenti-identita, GET /documenti-identita/{id}/copia
- DELETE /documenti-identita/{id}: cancella la copia (le evidenze FEA restano nell'adesione)
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, clock
from app.db import get_db
from app.deps import get_current_user
from app.domain.regole import TipoDocumento
from app.models import Cliente, DocumentoIdentita, User
from app.routers.clienti import get_cliente
from app.routers.deleghe import _errori
from app.schemas.documenti_identita import DocumentoCaricatoOut, DocumentoIdentitaOut, PropostaOut
from app.services import documenti_identita as servizio
from app.services.cifratura import ErroreCifratura, Portachiavi, get_portachiavi

router = APIRouter(tags=["documenti d'identita'"])


def get_documento(db: Session, user: User, documento_id: uuid.UUID) -> DocumentoIdentita:
    doc = db.get(DocumentoIdentita, documento_id)
    if doc is None or doc.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Documento non trovato")
    return doc


async def leggi_upload(upload: UploadFile) -> servizio.Copia:
    dati = await upload.read(servizio.MAX_COPIA + 1)
    copia, errore = servizio.controlla_copia(dati, upload.content_type, upload.filename)
    if copia is None:
        raise _errori([errore])
    return copia


def solo_persone_fisiche(cliente: Cliente) -> None:
    if not cliente.persona_fisica:
        raise _errori(["I documenti d'identita' si archiviano solo per le persone fisiche"])


async def leggi_proposta(copia: servizio.Copia, cf: str | None) -> PropostaOut:
    # Tesseract impegna la CPU per qualche secondo: fuori dal ciclo degli eventi
    return await run_in_threadpool(servizio.proposta, copia, clock.oggi(), cf)


@router.post("/documenti-identita/lettura", response_model=PropostaOut)
async def leggi(
    copia: UploadFile,
    codice_fiscale: str | None = Form(None),
    user: User = Depends(get_current_user),
) -> PropostaOut:
    """Legge i dati dalla copia senza salvarla, per precompilare l'anagrafica."""
    return await leggi_proposta(await leggi_upload(copia), (codice_fiscale or "").upper() or None)


def salva(
    db: Session,
    request: Request,
    user: User,
    cliente: Cliente,
    copia: servizio.Copia,
    proposta: PropostaOut,
    portachiavi: Portachiavi,
    *,
    tipo: TipoDocumento,
    numero: str | None,
    rilasciato_da: str | None,
    scadenza: date | None,
) -> DocumentoIdentita:
    doc = servizio.nuovo(
        portachiavi,
        cliente=cliente,
        user=user,
        copia=copia,
        tipo=tipo,
        numero=numero,
        rilasciato_da=rilasciato_da,
        scadenza=scadenza,
        lettura=servizio.riepilogo(proposta),
    )
    db.add(doc)
    audit.record(
        db,
        "documento_identita_caricato",
        user=user,
        entity_type="documento_identita",
        entity_id=doc.id,
        request=request,
        details={"cliente_id": str(cliente.id), "letto": proposta.letta},
    )
    return doc


@router.post(
    "/clienti/{cliente_id}/documenti-identita",
    response_model=DocumentoCaricatoOut,
    status_code=status.HTTP_201_CREATED,
)
async def carica(
    cliente_id: uuid.UUID,
    request: Request,
    copia: UploadFile,
    tipo: TipoDocumento | None = Form(None),
    numero: str | None = Form(None, max_length=30),
    rilasciato_da: str | None = Form(None, max_length=100),
    scadenza: date | None = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> dict:
    """Salva la copia cifrata. Gli estremi non indicati si prendono dalla lettura, se riuscita."""
    cliente = get_cliente(db, user, cliente_id)
    solo_persone_fisiche(cliente)
    file = await leggi_upload(copia)
    proposta = await leggi_proposta(file, cliente.codice_fiscale)
    doc = salva(
        db,
        request,
        user,
        cliente,
        file,
        proposta,
        portachiavi,
        tipo=tipo or proposta.tipo or TipoDocumento.CARTA_IDENTITA,
        numero=numero or proposta.numero,
        rilasciato_da=rilasciato_da,
        scadenza=scadenza or proposta.scadenza,
    )
    db.commit()
    return {**DocumentoIdentitaOut.model_validate(doc).model_dump(), "proposta": proposta}


@router.get("/clienti/{cliente_id}/documenti-identita", response_model=list[DocumentoIdentitaOut])
def elenco(
    cliente_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DocumentoIdentita]:
    cliente = get_cliente(db, user, cliente_id)
    return documenti_cliente(db, cliente)


def documenti_cliente(db: Session, cliente: Cliente) -> list[DocumentoIdentita]:
    stmt = (
        select(DocumentoIdentita)
        .where(DocumentoIdentita.cliente_id == cliente.id)
        .order_by(DocumentoIdentita.created_at.desc())
    )
    return list(db.scalars(stmt))


def documento_valido(db: Session, cliente: Cliente) -> DocumentoIdentita | None:
    """Ultimo documento archiviato non scaduto, per proporne gli estremi."""
    oggi = clock.oggi()
    for doc in db.scalars(
        select(DocumentoIdentita)
        .where(DocumentoIdentita.cliente_id == cliente.id)
        .order_by(DocumentoIdentita.created_at.desc())
    ):
        if doc.numero and doc.scadenza and doc.scadenza >= oggi:
            return doc
    return None


def copia_in_chiaro(
    db: Session,
    request: Request,
    user: User,
    doc: DocumentoIdentita,
    portachiavi: Portachiavi,
    motivo: str,
) -> bytes:
    try:
        dati = servizio.apri(portachiavi, doc)
    except (ErroreCifratura, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit.record(
        db,
        "documento_identita_aperto",
        user=user,
        entity_type="documento_identita",
        entity_id=doc.id,
        request=request,
        details={"motivo": motivo},
    )
    db.commit()
    return dati


@router.get("/documenti-identita/{documento_id}/copia", response_class=Response)
def scarica(
    documento_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    portachiavi: Portachiavi = Depends(get_portachiavi),
) -> Response:
    doc = get_documento(db, user, documento_id)
    dati = copia_in_chiaro(db, request, user, doc, portachiavi, "download")
    nome = doc.copia_nome.replace('"', "")
    return Response(
        dati,
        media_type=doc.copia_tipo,
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/documenti-identita/{documento_id}", status_code=status.HTTP_204_NO_CONTENT)
def elimina(
    documento_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    doc = get_documento(db, user, documento_id)
    audit.record(
        db,
        "documento_identita_eliminato",
        user=user,
        entity_type="documento_identita",
        entity_id=doc.id,
        request=request,
        details={"cliente_id": str(doc.cliente_id), "copia_sha256": doc.copia_sha256},
    )
    db.delete(doc)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
