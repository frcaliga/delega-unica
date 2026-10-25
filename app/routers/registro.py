import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_roles
from app.models import Role, User, VoceRegistro
from app.schemas.deleghe import VerificaRegistroOut, VoceRegistroOut
from app.services import registro

router = APIRouter(prefix="/registro", tags=["registro"])

TIPI = {"1": "Conferimento", "2": "Revoca", "3": "Rinnovo"}


def _voci(db: Session, user: User, dal: date | None, al: date | None):
    stmt = select(VoceRegistro).where(VoceRegistro.tenant_id == user.tenant_id)
    if dal:
        stmt = stmt.where(VoceRegistro.data >= dal)
    if al:
        stmt = stmt.where(VoceRegistro.data <= al)
    return db.scalars(stmt.order_by(VoceRegistro.numero))


@router.get("", response_model=list[VoceRegistroOut])
def list_registro(
    dal: date | None = None,
    al: date | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[VoceRegistro]:
    return list(_voci(db, user, dal, al))


@router.get("/export.csv", response_class=Response)
def export_registro(
    dal: date | None = Query(None),
    al: date | None = Query(None),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESPONSABILE_DELEGHE)),
    db: Session = Depends(get_db),
) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Numero",
            "Data",
            "Operazione",
            "Codice fiscale delegante",
            "Delegante",
            "Codice fiscale sottoscrittore",
            "Sottoscrittore",
            "Documento d'identita'",
            "Servizi",
            "Hash",
        ]
    )
    for voce in _voci(db, user, dal, al):
        writer.writerow(
            [
                voce.numero,
                voce.data.strftime("%d/%m/%Y"),
                TIPI[voce.tipo_richiesta.value],
                voce.delegante_cf,
                voce.delegante_nominativo,
                voce.sottoscrittore_cf,
                voce.sottoscrittore_nominativo,
                voce.documento_estremi or "",
                registro.descrizione_servizi(voce.servizi),
                voce.hash,
            ]
        )
    return Response(
        buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="registro_deleghe.csv"'},
    )


@router.get("/verifica", response_model=VerificaRegistroOut)
def verifica_registro(
    user: User = Depends(require_roles(Role.ADMIN, Role.RESPONSABILE_DELEGHE)),
    db: Session = Depends(get_db),
) -> registro.EsitoVerifica:
    return registro.verifica(db, user.tenant_id)
