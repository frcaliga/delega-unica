"""FEA allo sportello: il cliente viene identificato di persona e firma con un OTP via SMS.

1. L'operatore registra il documento esibito e il cellulare: POST /clienti/{id}/adesioni-fea
2. Il cliente conferma la dichiarazione di accettazione con l'OTP: POST /adesioni-fea/{id}/conferma
3. Per ogni delega: POST /deleghe/{id}/fea/otp e POST /deleghe/{id}/fea/firma
4. Lo studio controfirma la busta (GET /deleghe/{id}/fea/busta-cliente) con la propria firma
   digitale e la carica con POST /deleghe/{id}/firmato, che la verifica e la annota nel registro.
"""

import re
import uuid
from datetime import date

from cryptography.hazmat.primitives.serialization import Encoding
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, clock
from app.db import get_db
from app.deps import get_current_user
from app.domain.regole import ModalitaFirma, TipoDocumento
from app.models import (
    AdesioneFea,
    Delega,
    EvidenzaFirma,
    ScopoOtp,
    SessioneOtp,
    StatoAdesione,
    StatoDelega,
    Tenant,
    User,
)
from app.routers.clienti import get_cliente
from app.routers.deleghe import _errori, _get_delega, _richiedi_stato
from app.schemas.fea import AdesioneOut, CodiceIn, FirmaFeaOut, OtpInviatoOut
from app.services import ca_fea, fea, otp, sms

router = APIRouter(tags=["fea"])

MAX_COPIA_DOCUMENTO = 10 * 1024 * 1024
TIPI_COPIA = {"application/pdf", "image/jpeg", "image/png"}
CELLULARE = re.compile(r"^\+?\d{8,15}$")


def _get_adesione(db: Session, user: User, adesione_id: uuid.UUID) -> AdesioneFea:
    adesione = db.get(AdesioneFea, adesione_id)
    if adesione is None or adesione.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Adesione non trovata")
    return adesione


def _invia_otp(
    db: Session,
    invio: sms.InvioSms,
    *,
    tenant: Tenant,
    scopo: ScopoOtp,
    riferimento_id: uuid.UUID,
    cellulare: str,
    sha256_documento: str,
    oggetto: str,
) -> OtpInviatoOut:
    sessione, codice = otp.crea(
        db,
        tenant_id=tenant.id,
        scopo=scopo,
        riferimento_id=riferimento_id,
        cellulare=cellulare,
        sha256_documento=sha256_documento,
    )
    db.commit()
    invio.invia(cellulare, f"{tenant.name}: codice {codice} per {oggetto}. Non comunicarlo.")
    return OtpInviatoOut(cellulare=sms.maschera(cellulare), scade_il=sessione.scade_il)


def _verifica_otp(db: Session, **kw) -> SessioneOtp:
    try:
        return otp.verifica(db, **kw)
    except otp.OtpNonValido as exc:
        # Il tentativo errato resta contato
        db.commit()
        raise _errori([str(exc)]) from exc


def adesione_attiva(db: Session, tenant_id: uuid.UUID, cf: str) -> AdesioneFea | None:
    return db.scalar(
        select(AdesioneFea).where(
            AdesioneFea.tenant_id == tenant_id,
            AdesioneFea.codice_fiscale == cf,
            AdesioneFea.stato == StatoAdesione.ATTIVA,
        )
    )


@router.post(
    "/clienti/{cliente_id}/adesioni-fea",
    response_model=AdesioneOut,
    status_code=status.HTTP_201_CREATED,
)
async def crea_adesione(
    cliente_id: uuid.UUID,
    request: Request,
    copia_documento: UploadFile,
    documento_tipo: TipoDocumento = Form(),
    documento_numero: str = Form(min_length=1, max_length=30),
    documento_rilasciato_da: str = Form(min_length=1, max_length=100),
    documento_scadenza: date = Form(),
    cellulare: str = Form(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    invio: sms.InvioSms = Depends(sms.get_sms),
) -> AdesioneFea:
    """Identificazione di persona: registra il documento esibito e invia l'OTP di conferma."""
    cliente = get_cliente(db, user, cliente_id)
    errori = []
    if not cliente.persona_fisica or cliente.partita_iva_attiva:
        errori.append(
            "La FEA con certificato non qualificato e' ammessa solo per persone fisiche "
            "senza partita IVA"
        )
    if documento_scadenza < clock.oggi():
        errori.append("Il documento d'identita' e' scaduto")
    cellulare = re.sub(r"[\s.-]", "", cellulare)
    if not CELLULARE.match(cellulare):
        errori.append("Numero di cellulare non valido")
    if copia_documento.content_type not in TIPI_COPIA:
        errori.append("La copia del documento deve essere PDF, JPEG o PNG")
    copia = await copia_documento.read(MAX_COPIA_DOCUMENTO + 1)
    if not copia or len(copia) > MAX_COPIA_DOCUMENTO:
        errori.append("La copia del documento deve essere tra 1 byte e 10 MB")
    if errori:
        raise _errori(errori)

    tenant = db.get_one(Tenant, user.tenant_id)
    adesione = AdesioneFea(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        cliente_id=cliente.id,
        codice_fiscale=cliente.codice_fiscale,
        cognome=cliente.cognome,
        nome=cliente.nome,
        documento_tipo=documento_tipo,
        documento_numero=documento_numero.strip().upper(),
        documento_rilasciato_da=documento_rilasciato_da.strip(),
        documento_scadenza=documento_scadenza,
        copia_documento=copia,
        copia_documento_tipo=copia_documento.content_type,
        copia_documento_sha256=fea.sha256(copia),
        cellulare=cellulare,
        versione_condizioni=fea.VERSIONE_CONDIZIONI,
        user_id=user.id,
    )
    adesione.dichiarazione = fea.dichiarazione(adesione, tenant, user.full_name, clock.adesso())
    adesione.dichiarazione_sha256 = fea.sha256(adesione.dichiarazione)
    db.add(adesione)
    audit.record(
        db,
        "fea_adesione_creata",
        user=user,
        entity_type="adesione_fea",
        entity_id=adesione.id,
        request=request,
        details={"cliente_id": str(cliente.id), "cellulare": sms.maschera(cellulare)},
    )
    _invia_otp(
        db,
        invio,
        tenant=tenant,
        scopo=ScopoOtp.ADESIONE,
        riferimento_id=adesione.id,
        cellulare=cellulare,
        sha256_documento=adesione.dichiarazione_sha256,
        oggetto="aderire alla firma elettronica avanzata",
    )
    return adesione


@router.get("/clienti/{cliente_id}/adesioni-fea", response_model=list[AdesioneOut])
def list_adesioni(
    cliente_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AdesioneFea]:
    cliente = get_cliente(db, user, cliente_id)
    stmt = (
        select(AdesioneFea)
        .where(AdesioneFea.cliente_id == cliente.id)
        .order_by(AdesioneFea.created_at.desc())
    )
    return list(db.scalars(stmt))


@router.get("/adesioni-fea/{adesione_id}/dichiarazione", response_class=Response)
def scarica_dichiarazione(
    adesione_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Testo della dichiarazione, da mostrare al cliente prima della conferma."""
    adesione = _get_adesione(db, user, adesione_id)
    return Response(adesione.dichiarazione, media_type="text/plain; charset=utf-8")


@router.post("/adesioni-fea/{adesione_id}/otp", response_model=OtpInviatoOut)
def reinvia_otp_adesione(
    adesione_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    invio: sms.InvioSms = Depends(sms.get_sms),
) -> OtpInviatoOut:
    adesione = _get_adesione(db, user, adesione_id)
    if adesione.stato != StatoAdesione.DA_CONFERMARE:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Adesione gia' confermata o revocata")
    return _invia_otp(
        db,
        invio,
        tenant=db.get_one(Tenant, user.tenant_id),
        scopo=ScopoOtp.ADESIONE,
        riferimento_id=adesione.id,
        cellulare=adesione.cellulare,
        sha256_documento=adesione.dichiarazione_sha256,
        oggetto="aderire alla firma elettronica avanzata",
    )


@router.post("/adesioni-fea/{adesione_id}/conferma", response_model=AdesioneOut)
def conferma_adesione(
    adesione_id: uuid.UUID,
    body: CodiceIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdesioneFea:
    adesione = _get_adesione(db, user, adesione_id)
    if adesione.stato != StatoAdesione.DA_CONFERMARE:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Adesione gia' confermata o revocata")
    sessione = _verifica_otp(
        db,
        scopo=ScopoOtp.ADESIONE,
        riferimento_id=adesione.id,
        codice=body.codice,
        sha256_documento=adesione.dichiarazione_sha256,
    )
    adesso = clock.adesso()
    # Una nuova adesione sostituisce la precedente (ad esempio per un documento rinnovato)
    precedente = adesione_attiva(db, adesione.tenant_id, adesione.codice_fiscale)
    if precedente is not None:
        precedente.stato = StatoAdesione.REVOCATA
        precedente.revocata_il = adesso
    adesione.stato = StatoAdesione.ATTIVA
    adesione.confermata_il = adesso
    audit.record(
        db,
        "fea_adesione_confermata",
        user=user,
        entity_type="adesione_fea",
        entity_id=adesione.id,
        request=request,
        details={
            "sessione_otp": str(sessione.id),
            "dichiarazione_sha256": adesione.dichiarazione_sha256,
            "sostituisce": str(precedente.id) if precedente else None,
        },
    )
    db.commit()
    return adesione


@router.post("/adesioni-fea/{adesione_id}/revoca", response_model=AdesioneOut)
def revoca_adesione(
    adesione_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdesioneFea:
    adesione = _get_adesione(db, user, adesione_id)
    if adesione.stato == StatoAdesione.REVOCATA:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Adesione gia' revocata")
    adesione.stato = StatoAdesione.REVOCATA
    adesione.revocata_il = clock.adesso()
    audit.record(
        db,
        "fea_adesione_revocata",
        user=user,
        entity_type="adesione_fea",
        entity_id=adesione.id,
        request=request,
    )
    db.commit()
    return adesione


def _pronta_per_fea(db: Session, delega: Delega) -> AdesioneFea:
    _richiedi_stato(delega, StatoDelega.IN_FIRMA)
    errori = []
    if delega.modalita_firma != ModalitaFirma.FEA_NON_QUALIFICATA:
        errori.append("La delega non prevede la FEA con certificato non qualificato")
    if delega.xml_generato is None:
        errori.append("Generare prima il file XML della delega")
    if delega.busta_cliente is not None:
        errori.append("Il cliente ha gia' firmato questa delega")
    adesione = adesione_attiva(db, delega.tenant_id, delega.sottoscrittore_cf)
    if adesione is None:
        errori.append("Il sottoscrittore non ha un'adesione attiva al servizio FEA")
    elif (
        adesione.documento_tipo != delega.documento_tipo
        or adesione.documento_numero != (delega.documento_numero or "").strip().upper()
    ):
        errori.append("Il documento indicato nella delega non e' quello registrato all'adesione")
    elif adesione.documento_scadenza < clock.oggi():
        errori.append("Il documento d'identita' dell'adesione e' scaduto: rinnovare l'adesione")
    if errori:
        raise _errori(errori)
    assert adesione is not None
    return adesione


@router.post("/deleghe/{delega_id}/fea/otp", response_model=OtpInviatoOut)
def invia_otp_firma(
    delega_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    invio: sms.InvioSms = Depends(sms.get_sms),
) -> OtpInviatoOut:
    """Invia al cliente il codice per firmare l'XML della delega appena mostrato."""
    delega = _get_delega(db, user, delega_id)
    adesione = _pronta_per_fea(db, delega)
    assert delega.xml_generato is not None
    return _invia_otp(
        db,
        invio,
        tenant=db.get_one(Tenant, user.tenant_id),
        scopo=ScopoOtp.FIRMA,
        riferimento_id=delega.id,
        cellulare=adesione.cellulare,
        sha256_documento=fea.sha256(delega.xml_generato),
        oggetto="firmare la delega unica all'Agenzia delle Entrate",
    )


@router.post("/deleghe/{delega_id}/fea/firma", response_model=FirmaFeaOut)
def firma_con_otp(
    delega_id: uuid.UUID,
    body: CodiceIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    autorita: ca_fea.Autorita = Depends(fea.get_autorita),
) -> EvidenzaFirma:
    delega = _get_delega(db, user, delega_id)
    adesione = _pronta_per_fea(db, delega)
    assert delega.xml_generato is not None
    sha_xml = fea.sha256(delega.xml_generato)
    sessione = _verifica_otp(
        db,
        scopo=ScopoOtp.FIRMA,
        riferimento_id=delega.id,
        codice=body.codice,
        sha256_documento=sha_xml,
    )
    ora = clock.adesso()
    busta, certificato = fea.firma_delega(autorita, delega, ora)
    delega.busta_cliente = busta
    evidenza = EvidenzaFirma(
        tenant_id=delega.tenant_id,
        delega_id=delega.id,
        adesione_id=adesione.id,
        sessione_otp_id=sessione.id,
        sha256_xml=sha_xml,
        sha256_busta=fea.sha256(busta),
        certificato=certificato.public_bytes(Encoding.DER),
        seriale_certificato=format(certificato.serial_number, "x"),
        ora_firma=ora,
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        user_id=user.id,
    )
    db.add(evidenza)
    db.flush()
    audit.record(
        db,
        "fea_delega_firmata_cliente",
        user=user,
        entity_type="delega",
        entity_id=delega.id,
        request=request,
        details={"evidenza_id": str(evidenza.id), "seriale": evidenza.seriale_certificato},
    )
    db.commit()
    return evidenza


@router.get("/deleghe/{delega_id}/fea/busta-cliente", response_class=Response)
def scarica_busta_cliente(
    delega_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Busta firmata dal cliente, da controfirmare con la firma digitale dello studio."""
    delega = _get_delega(db, user, delega_id)
    if delega.busta_cliente is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Il cliente non ha ancora firmato")
    nome = f"DEL24_{delega.cliente.codice_fiscale}_{str(delega.id)[:8]}.xml.p7m"
    return Response(
        delega.busta_cliente,
        media_type="application/pkcs7-mime",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )
