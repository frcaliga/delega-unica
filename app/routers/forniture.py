from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, clock
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Delega, DocumentoFirmato, StatoDelega, Tenant, User
from app.schemas.deleghe import FornituraIn
from app.services.xml_delega import AllegatoFirmato, XmlNonValido, costruisci_fornitura

router = APIRouter(prefix="/forniture", tags=["forniture"])


@router.post("", response_class=Response)
def crea_fornitura(
    body: FornituraIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """File per l'invio massivo (fino a 300 deleghe firmate), da controllare e trasmettere con
    Desktop Telematico o Entratel Multifile."""
    ids = list(dict.fromkeys(body.delega_ids))
    righe = db.execute(
        select(Delega, DocumentoFirmato)
        .join(DocumentoFirmato, DocumentoFirmato.delega_id == Delega.id)
        .where(Delega.tenant_id == user.tenant_id, Delega.id.in_(ids))
    ).all()
    trovate = {delega.id: (delega, doc) for delega, doc in righe}
    errori = [f"Delega {i} non trovata o senza file firmato" for i in ids if i not in trovate]
    errori += [
        f"Delega {d.id} in stato {d.stato.value}: serve lo stato firmata"
        for d, _ in trovate.values()
        if d.stato != StatoDelega.FIRMATA
    ]
    if errori:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"errori": errori})

    tenant = db.get_one(Tenant, user.tenant_id)
    allegati = [
        AllegatoFirmato(delega.cliente.codice_fiscale, doc.nome_file, doc.contenuto)
        for delega, doc in (trovate[i] for i in ids)
    ]
    try:
        contenuto = costruisci_fornitura(
            tenant,
            allegati,
            identificativo_software=get_settings().identificativo_prod_software,
            email=body.email,
            cellulare=body.cellulare,
        )
    except XmlNonValido as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"errori": exc.errori}
        ) from exc
    audit.record(
        db,
        "fornitura_generata",
        user=user,
        request=request,
        details={"deleghe": [str(i) for i in ids]},
    )
    db.commit()
    nome = f"DEL24_fornitura_{clock.oggi():%Y%m%d}_{len(ids)}.xml"
    return Response(
        contenuto,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )
