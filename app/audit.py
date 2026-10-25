import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record(
    db: Session,
    action: str,
    *,
    user: User | None = None,
    tenant_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    request: Request | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Aggiunge una voce di audit alla sessione; il commit resta al chiamante."""
    entry = AuditLog(
        tenant_id=tenant_id or (user.tenant_id if user else None),
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        ip_address=request.client.host if request and request.client else None,
        details=details or {},
    )
    db.add(entry)
    return entry
