from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import get_current_user, require_roles
from app.models import AuditLog, Role, Tenant, User
from app.schemas import AuditLogOut, TenantOut
from app.schemas.deleghe import TenantUpdate

router = APIRouter(tags=["tenant"])


@router.get("/tenant", response_model=TenantOut)
def current_tenant(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Tenant:
    return db.get_one(Tenant, user.tenant_id)


@router.patch("/tenant", response_model=TenantOut)
def update_tenant(
    body: TenantUpdate,
    request: Request,
    user: User = Depends(require_roles(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> Tenant:
    tenant = db.get_one(Tenant, user.tenant_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(tenant, field, value)
    audit.record(
        db,
        "tenant_updated",
        user=user,
        entity_type="tenant",
        entity_id=tenant.id,
        request=request,
        details={"campi": sorted(changes)},
    )
    db.commit()
    return tenant


@router.get("/audit", response_model=list[AuditLogOut])
def list_audit(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_roles(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> list[AuditLog]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.tenant_id == user.tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt))
