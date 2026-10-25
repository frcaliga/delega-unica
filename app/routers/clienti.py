import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import get_current_user
from app.models import Cliente, User
from app.schemas.deleghe import ClienteIn, ClienteOut, ClienteUpdate

router = APIRouter(prefix="/clienti", tags=["clienti"])


def get_cliente(db: Session, user: User, cliente_id: uuid.UUID) -> Cliente:
    cliente = db.get(Cliente, cliente_id)
    if cliente is None or cliente.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cliente non trovato")
    return cliente


@router.get("", response_model=list[ClienteOut])
def list_clienti(
    q: str | None = Query(None, description="Ricerca per codice fiscale, nome o denominazione"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Cliente]:
    stmt = select(Cliente).where(Cliente.tenant_id == user.tenant_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Cliente.codice_fiscale.ilike(like),
                Cliente.cognome.ilike(like),
                Cliente.nome.ilike(like),
                Cliente.denominazione.ilike(like),
            )
        )
    stmt = stmt.order_by(Cliente.cognome, Cliente.denominazione).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.post("", response_model=ClienteOut, status_code=status.HTTP_201_CREATED)
def create_cliente(
    body: ClienteIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Cliente:
    exists = db.scalar(
        select(Cliente.id).where(
            Cliente.tenant_id == user.tenant_id, Cliente.codice_fiscale == body.codice_fiscale
        )
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Cliente gia' presente")
    cliente = Cliente(tenant_id=user.tenant_id, **body.model_dump())
    db.add(cliente)
    db.flush()
    audit.record(
        db,
        "cliente_created",
        user=user,
        entity_type="cliente",
        entity_id=cliente.id,
        request=request,
    )
    db.commit()
    return cliente


@router.get("/{cliente_id}", response_model=ClienteOut)
def read_cliente(
    cliente_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Cliente:
    return get_cliente(db, user, cliente_id)


@router.patch("/{cliente_id}", response_model=ClienteOut)
def update_cliente(
    cliente_id: uuid.UUID,
    body: ClienteUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Cliente:
    cliente = get_cliente(db, user, cliente_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(cliente, field, value)
    audit.record(
        db,
        "cliente_updated",
        user=user,
        entity_type="cliente",
        entity_id=cliente.id,
        request=request,
        details={"campi": sorted(changes)},
    )
    db.commit()
    return cliente
