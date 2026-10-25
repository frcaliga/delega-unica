from app.models.audit import AuditLog
from app.models.base import Base
from app.models.cliente import Cliente
from app.models.delega import Delega, StatoDelega
from app.models.registro import VoceRegistro
from app.models.tenant import Tenant
from app.models.user import Role, User

__all__ = [
    "AuditLog",
    "Base",
    "Cliente",
    "Delega",
    "Role",
    "StatoDelega",
    "Tenant",
    "User",
    "VoceRegistro",
]
